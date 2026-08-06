package extractmethods

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func testPlanJWT(t *testing.T, email, plan string) string {
	t.Helper()
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"none","typ":"JWT"}`))
	payload, err := json.Marshal(map[string]any{
		"https://api.openai.com/auth":    map[string]any{"chatgpt_plan_type": plan},
		"https://api.openai.com/profile": map[string]any{"email": email},
	})
	if err != nil {
		t.Fatal(err)
	}
	return header + "." + base64.RawURLEncoding.EncodeToString(payload) + ".signature"
}

func TestEligibilityResponseClassification(t *testing.T) {
	cf := classifyEligibilityResponse("/backend-api/me", HTTPResponse{Status: http.StatusForbidden, Body: []byte("Enable JavaScript and cookies to continue /cdn-cgi/challenge-platform/")})
	if !isRetryableEligibilityError(cf) || eligibilityErrorReason(cf) != "Cloudflare challenge" {
		t.Fatalf("CF response classification = %v", cf)
	}
	invalid := classifyEligibilityResponse("/backend-api/me", HTTPResponse{Status: http.StatusUnauthorized, Body: []byte(`{"error":{"code":"token_invalidated"}}`)})
	var upstream *eligibilityUpstreamError
	if isRetryableEligibilityError(invalid) || !errors.As(invalid, &upstream) || !upstream.AuthInvalid {
		t.Fatalf("401 response classification = %#v", upstream)
	}
	blocked := classifyEligibilityResponse("/backend-api/me", HTTPResponse{Status: http.StatusForbidden, Body: []byte("forbidden")})
	if !isRetryableEligibilityError(blocked) {
		t.Fatalf("generic 403 must rotate proxy: %v", blocked)
	}
}

func TestCatalogIsGoOnlyAndPayPalFirst(t *testing.T) {
	methods := Catalog()
	// The catalog currently exposes all eleven Go implementations. Keep this
	// assertion explicit so an accidental removal/addition is caught during
	// image builds instead of surfacing only at runtime.
	if len(methods) != 11 {
		t.Fatalf("expected 11 methods, got %d", len(methods))
	}
	if methods[0].ID != MethodPayPalBA || !methods[0].Primary {
		t.Fatalf("PayPal BA must be the primary first method: %#v", methods[0])
	}
	for _, method := range methods {
		if method.Implementation != "go" {
			t.Fatalf("method must be implemented by Go: %#v", method)
		}
		if method.ID == MethodKakao {
			if !method.SupportsConcurrency || method.Kind != "eligibility_or_provider_link" || !method.SupportsPaymentStatus {
				t.Fatalf("Kakao must expose the configurable batch dual-mode workflow: %#v", method)
			}
			if strings.Join(method.Modes, ",") != KakaoModeEligibility+","+KakaoModeProviderLink {
				t.Fatalf("unexpected Kakao modes: %#v", method.Modes)
			}
		} else if !method.SupportsConcurrency {
			t.Fatalf("method unexpectedly lost concurrency: %#v", method)
		}
	}
	direct, ok := LookupMethod("paper_card")
	if !ok || direct.ID != MethodDirect || direct.Label != "直卡" {
		t.Fatalf("paper_card compatibility must resolve to 直卡: %#v", direct)
	}
	wantedCountries := "US GB DE FR NL CA AU IN PH TH BA AE BR TR VN JP BH MX"
	if strings.Join(methods[0].Countries, " ") != wantedCountries {
		t.Fatalf("PayPal extraction must only expose countries with real backend profiles: %v", methods[0].Countries)
	}
}

func TestPayPalCountryFallbackIsExplicit(t *testing.T) {
	adapted := normalizeOptions(MethodPayPalBA, Options{Country: "GB", Currency: "USD"})
	if adapted.Country != "GB" || adapted.RequestedCountry != "GB" || adapted.Currency != "GBP" || adapted.CountryFallback {
		t.Fatalf("adapted GB route changed unexpectedly: %#v", adapted)
	}
	fallback := normalizeOptions(MethodPayPalBA, Options{Country: "SG", Currency: "SGD"})
	if fallback.RequestedCountry != "SG" || fallback.Country != "US" || fallback.Currency != "USD" || !fallback.CountryFallback {
		t.Fatalf("unadapted SG must explicitly fall back to US: %#v", fallback)
	}
}

func TestPayPalCountryCurrencyMap(t *testing.T) {
	wanted := map[string]string{
		"US": "USD", "GB": "GBP", "DE": "EUR", "FR": "EUR", "NL": "EUR", "CA": "CAD",
		"AU": "AUD", "IN": "INR", "PH": "PHP", "TH": "THB", "BA": "BAM", "AE": "AED",
		"BR": "BRL", "TR": "USD", "VN": "VND", "JP": "JPY", "BH": "BHD", "MX": "MXN",
	}
	for country, currency := range wanted {
		if got := currencyForCountry(country); got != currency {
			t.Errorf("%s currency = %s, want %s", country, got, currency)
		}
	}
}

func TestDirectCountryCurrencyIsCanonicalized(t *testing.T) {
	options := normalizeOptions(MethodDirect, Options{Country: "JP", Currency: "PHP"})
	if options.Country != "JP" || options.Currency != "JPY" {
		t.Fatalf("direct billing pair = %s/%s, want JP/JPY", options.Country, options.Currency)
	}
}

func TestDirectBillingDoesNotInheritProxyRegion(t *testing.T) {
	options := normalizeOptions(MethodDirect, Options{Country: "PH", Currency: "PHP", ProxyRegion: "JP", CheckoutProxyRegion: "JP"})
	if options.Country != "PH" || options.Currency != "PHP" {
		t.Fatalf("proxy region rewrote billing pair to %s/%s", options.Country, options.Currency)
	}
}

func TestDirectRouteSelectionUsesObservedCheckoutIdentity(t *testing.T) {
	withBoth := checkoutData{ID: "oaics_outer", OpenAIID: "oaics_outer", StripeID: "cs_live_inner"}
	if got, err := selectDirectCheckoutRoute(withBoth, ""); err != nil || got != DirectRouteOAICSDirect {
		t.Fatalf("auto route = %q, err=%v; want OAICS direct", got, err)
	}
	if got, err := selectDirectCheckoutRoute(withBoth, DirectRouteCSPrepared); err != nil || got != DirectRouteCSPrepared {
		t.Fatalf("explicit CS route = %q, err=%v", got, err)
	}
	if got, err := selectDirectCheckoutRoute(checkoutData{StripeID: "cs_live_only"}, ""); err != nil || got != DirectRouteCSPrepared {
		t.Fatalf("CS-only auto route = %q, err=%v", got, err)
	}
	if _, err := selectDirectCheckoutRoute(checkoutData{StripeID: "cs_live_only"}, DirectRouteOAICSDirect); err == nil {
		t.Fatal("explicit OAICS route must fail when no OAICS identity was observed")
	}
}

func TestPaymentMethodParsingAndIntersection(t *testing.T) {
	raw := map[string]any{"data": []any{map[string]any{"id": "pm_b"}, map[string]any{"id": "pm_a"}, map[string]any{"id": "card_ignored"}}}
	got := savedPaymentMethodIDsFromResponse(raw)
	if strings.Join(got, ",") != "pm_a,pm_b" {
		t.Fatalf("saved methods = %#v", got)
	}
	if !hasPaymentMethodIntersection([]string{"pm_a"}, []string{"pm_b", "pm_a"}) {
		t.Fatal("expected saved PaymentMethod intersection")
	}
}

func TestExpectedAmountReadsOAICSMinorUnits(t *testing.T) {
	raw := map[string]any{"checkout_state": map[string]any{"total": map[string]any{"total": map[string]any{"minorUnitsAmount": json.Number("98214")}}}}
	if got := expectedAmount(raw); got != "98214" {
		t.Fatalf("OAICS minor-unit amount = %q", got)
	}
}

func TestDirectPreparationMetadataKeepsSecretsPrivate(t *testing.T) {
	metadata := map[string]any{}
	applyDirectPreparationMetadata(metadata, directPreparation{
		Route: DirectRouteOAICSDirect, LinkReady: true, FormReady: true,
		PreparedAt: "2026-08-06T00:00:00Z", FormContext: map[string]any{"customerSessionClientSecret": "cuss_secret_PRIVATE"},
	})
	if metadata["requiresPrebind"] != false || metadata["prepared"] != true {
		t.Fatalf("OAICS metadata = %#v", metadata)
	}
	stripPrivateCheckoutMetadata(metadata)
	if _, ok := metadata["checkoutFormContext"]; ok {
		t.Fatal("checkout form context leaked through public metadata")
	}
}

func TestDirectPreparationBarrierRequiresBothArtifacts(t *testing.T) {
	metadata := map[string]any{}
	applyDirectPreparationMetadata(metadata, directPreparation{Route: DirectRouteOAICSDirect, LinkReady: true, FormReady: false})
	if metadata["prepared"] != false {
		t.Fatalf("incomplete preparation crossed barrier: %#v", metadata)
	}
}

func TestFindCheckoutIDPrefersStripeSessionOverOuterOAICSID(t *testing.T) {
	value := map[string]any{
		"id":      "oaics_outer123",
		"payment": map[string]any{"checkout_session_id": "cs_live_stripe456"},
	}
	if got := findCheckoutID(value); got != "cs_live_stripe456" {
		t.Fatalf("checkout id = %q, want Stripe payment-page id", got)
	}
}

func TestCheckoutTypeParsersKeepStripeAndOpenAIIDsSeparate(t *testing.T) {
	value := map[string]any{
		"id":      "oaics_outer123",
		"payment": map[string]any{"checkout_session_id": "cs_live_stripe456"},
	}
	if got := findOpenAICheckoutID(value); got != "oaics_outer123" {
		t.Fatalf("OpenAI checkout id = %q", got)
	}
	if got := findStripeCheckoutID(value); got != "cs_live_stripe456" {
		t.Fatalf("Stripe checkout id = %q", got)
	}
}

func TestCheckoutObservationMetadataRecordsBothFamilies(t *testing.T) {
	metadata := checkoutObservationMetadata(checkoutData{
		ID: "oaics_outer123", OpenAIID: "oaics_outer123", StripeID: "cs_live_inner456", ProcessorEntity: "openai_llc",
	}, nil)
	if metadata["checkoutType"] != "oaics" || metadata["observedCheckoutTypes"] != "oaics+stripe" || metadata["processorEntity"] != "openai_llc" {
		t.Fatalf("unexpected checkout observation metadata: %#v", metadata)
	}
}

func TestCheckoutAmountDisplayKeepsFormattedRegionalAmount(t *testing.T) {
	if got := checkoutAmountDisplay("98214", "PHP"); got != "982.14 PHP" {
		t.Fatalf("numeric PHP amount display = %q", got)
	}
	if got := checkoutAmountDisplay("₱1,100.00", "PHP"); got != "₱1,100.00" {
		t.Fatalf("formatted PHP amount display = %q", got)
	}
}

func TestPhilippinesLinkResultPreservesConfiguredAmountGate(t *testing.T) {
	gate := Options{}.amountGateConfig()
	result := philippinesLinkResult("https://chatgpt.com", checkoutData{
		ID: "oaics_phfixture", ProcessorEntity: "openai_llc",
	}, "0", gate)
	if result.ExtractionStatus != "link_ready" || result.PaymentStatus != "awaiting_user_payment" {
		t.Fatalf("unexpected PH result: %#v", result)
	}
	if result.Amount != "0" || result.AmountDisplay != "0.00 PHP" || result.LongURL == "" {
		t.Fatalf("PH amount/link not preserved: %#v", result)
	}
	if result.Metadata["amountGate"] != "strict_zero" || result.Metadata["paymentAction"] != "none" {
		t.Fatalf("PH link must remain link-only: %#v", result.Metadata)
	}
	if err := requireZeroAmount("菲律宾 PHP checkout", "98214"); err == nil {
		t.Fatal("non-zero PH checkout must be rejected")
	}
	if err := requireZeroAmount("菲律宾 PHP checkout", ""); err == nil {
		t.Fatal("unknown PH checkout amount must be rejected")
	}
}

func TestPhilippinesLinkResultRequiresOAICSCheckout(t *testing.T) {
	gate := Options{}.amountGateConfig()
	result := philippinesLinkResult("https://chatgpt.com", checkoutData{ID: "oaics_expected", ProcessorEntity: "openai_llc"}, "0", gate)
	if !strings.Contains(result.LongURL, "/oaics_expected") {
		t.Fatalf("PH link = %q", result.LongURL)
	}
}

func TestCheckoutLinkAmountGatesUseCurrencyMinorUnits(t *testing.T) {
	strict := Options{}.amountGateConfig()
	if err := requireAmountGate("PH", "₱0.00", "PHP", strict); err != nil {
		t.Fatalf("strict zero must accept localized zero: %v", err)
	}
	if err := requireAmountGate("PH", "₱0.01", "PHP", strict); err == nil {
		t.Fatal("strict zero accepted a non-zero amount")
	}

	atMost := Options{AmountGate: AmountGateAtMost, AmountThresholdMinor: 98214}.amountGateConfig()
	if err := requireAmountGate("PH", "₱982.14", "PHP", atMost); err != nil {
		t.Fatalf("localized PHP amount at the limit was rejected: %v", err)
	}
	if err := requireAmountGate("PH", "98215", "PHP", atMost); err == nil {
		t.Fatal("minor-unit PHP amount above the limit was accepted")
	}

	atLeast := Options{AmountGate: AmountGateAtLeast, AmountThresholdMinor: 10000}.amountGateConfig()
	if err := requireAmountGate("PH", "₱99.99", "PHP", atLeast); err == nil {
		t.Fatal("amount below the configured minimum was accepted")
	}
	if err := requireAmountGate("PH", "₱100.00", "PHP", atLeast); err != nil {
		t.Fatalf("amount at the configured minimum was rejected: %v", err)
	}

	known := Options{AmountGate: AmountGateAnyKnown}.amountGateConfig()
	if err := requireAmountGate("PH", "₱982.14", "PHP", known); err != nil {
		t.Fatalf("known amount was rejected by any-known gate: %v", err)
	}
	if err := requireAmountGate("PH", "", "PHP", known); err == nil {
		t.Fatal("unknown amount was accepted without an explicit override")
	}
	known.AllowUnknown = true
	if err := requireAmountGate("PH", "", "PHP", known); err != nil {
		t.Fatalf("explicit unknown-amount override was rejected: %v", err)
	}

	legacy := normalizeOptions(MethodPH, Options{MaxAmountMinor: 100})
	if legacy.AmountGate != AmountGateAtMost || legacy.AmountThresholdMinor != 100 {
		t.Fatalf("legacy maxAmountMinor was not mapped to an upper gate: %#v", legacy)
	}
}

func TestPhilippinesUsesConfiguredRetryBudget(t *testing.T) {
	options := normalizeOptions(MethodPH, Options{MaxAttempts: 7})
	if options.Attempts() != 7 {
		t.Fatalf("PH attempts = %d, want 7", options.Attempts())
	}
	if err := requireZeroAmount("菲律宾 PHP checkout", "98214"); err == nil {
		t.Fatal("a non-zero attempt must be discarded before a later retry")
	}
	if err := requireZeroAmount("菲律宾 PHP checkout", "0"); err != nil {
		t.Fatalf("a zero attempt must be accepted: %v", err)
	}
}

func TestIDEALUsesConfiguredRetryBudgetAndFormatsMinorUnits(t *testing.T) {
	configured := normalizeOptions(MethodIDEAL, Options{MaxAttempts: 8})
	if configured.Attempts() != 8 {
		t.Fatalf("iDEAL attempts = %d, want 8", configured.Attempts())
	}
	defaults := normalizeOptions(MethodIDEAL, Options{})
	if defaults.Attempts() != 3 {
		t.Fatalf("iDEAL default attempts = %d, want 3", defaults.Attempts())
	}
	if got := checkoutAmountDisplay("1901", "EUR"); got != "19.01 EUR" {
		t.Fatalf("iDEAL amount display = %q, want 19.01 EUR", got)
	}
	err := requireAmountGate("ideal checkout", "1901", "EUR", Options{}.amountGateConfig())
	if err == nil || !strings.Contains(err.Error(), "19.01 EUR") {
		t.Fatalf("strict-zero iDEAL error should show formatted EUR amount: %v", err)
	}
}

func TestPhilippinesDefaultsToVerifiedUSCheckoutTRPromotionRoute(t *testing.T) {
	options := normalizeOptions(MethodPH, Options{
		Proxy:          "us.cliproxy.io:3010:user-region-US:pass",
		PromotionProxy: "us.cliproxy.io:3010:user-region-TR-sid-fixed-t-5:pass",
	})
	if options.Attempts() != 10 {
		t.Fatalf("PH default attempts = %d, want 10", options.Attempts())
	}
	regions := defaultStageRegions(MethodPH, options)
	if regions.checkout != "US" || regions.promotion != "TR" || regions.provider != "US" || regions.approve != "US" {
		t.Fatalf("unexpected PH route: %#v", regions)
	}
	proxies, err := ResolveStageProxies(NewRuntimeConfig(""), options, MethodPH)
	if err != nil {
		t.Fatal(err)
	}
	if proxyConfiguredRegion(proxies.Checkout) != "US" || proxyConfiguredRegion(proxies.Promotion) != "TR" {
		t.Fatalf("PH proxies were rewritten away from US/TR: %#v", proxies.Labels)
	}
}

func TestMoMoCheckoutMethodMustBePresent(t *testing.T) {
	if err := requireMoMoMethod(nil); err == nil {
		t.Fatal("empty checkout methods must stop the MoMo flow")
	}
	if err := requireMoMoMethod([]string{"card", "paypal"}); err == nil {
		t.Fatal("checkout without MoMo must stop the MoMo flow")
	}
	if err := requireMoMoMethod([]string{"card", "momo"}); err != nil {
		t.Fatalf("checkout with MoMo should continue: %v", err)
	}
}

func TestParseBatchCredentialsJSONLinesAndDeduplicate(t *testing.T) {
	payload := base64.RawURLEncoding.EncodeToString([]byte(`{"email":"alice@example.com"}`))
	tokenOne := strings.Repeat("a", 24) + "." + payload + "." + strings.Repeat("b", 24)
	tokenTwo := strings.Repeat("c", 90)
	input := `{"accessToken":"` + tokenOne + `"}` + "\n" + tokenTwo + "\n" + tokenTwo
	items, err := ParseBatchCredentials(input, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 2 {
		t.Fatalf("expected 2 deduplicated credentials, got %d", len(items))
	}
	if items[0].Email != "alice@example.com" {
		t.Fatalf("JWT email not decoded: %#v", items[0])
	}
	if items[0].Hash == "" || items[1].Hash == "" {
		t.Fatal("token hashes must be populated")
	}
}

func TestResolveStageProxiesRequiresExplicitMainAndAllowsPromotionOverride(t *testing.T) {
	config := NewRuntimeConfig("")
	if _, err := ResolveStageProxies(config, Options{}, MethodPayPalBA); err == nil {
		t.Fatal("missing request proxy must fail instead of using a default or connecting directly")
	}
	if _, err := ResolveStageProxies(config, Options{ProxyMode: "system", Proxy: "http://main.test:8080"}, MethodPayPalBA); err == nil {
		t.Fatal("system/default proxy mode must not be accepted by extraction")
	}
	proxies, err := ResolveStageProxies(config, Options{
		ProxyMode: "custom", Proxy: "http://main.test:8080", PromotionProxy: "http://promo.test:8081", Country: "KR",
		CheckoutProxy: "http://ignored-checkout.test:8082", ProviderProxy: "http://ignored-provider.test:8083", ApproveProxy: "http://ignored-approve.test:8084",
	}, MethodKakao)
	if err != nil {
		t.Fatal(err)
	}
	for stage, proxy := range map[string]string{"checkout": proxies.Checkout, "provider": proxies.Provider, "approve": proxies.Approve} {
		if proxy != "http://main.test:8080" {
			t.Errorf("%s proxy = %q, want main proxy", stage, proxy)
		}
	}
	if proxies.Promotion != "http://promo.test:8081" {
		t.Fatalf("promotion proxy = %q, want explicit second proxy", proxies.Promotion)
	}
}

func TestPayPalSameStickyIPOption(t *testing.T) {
	base := Options{ProxyMode: "custom", Proxy: "http://user-region-US-sid-fixed-t-5:pass@proxy.test:3010", Country: "US"}
	shared, err := ResolveStageProxies(NewRuntimeConfig(""), func() Options {
		value := base
		value.PayPalSameStickyIP = true
		return value
	}(), MethodPayPalBA)
	if err != nil {
		t.Fatal(err)
	}
	if shared.Checkout != shared.Provider || shared.Checkout != shared.Approve {
		t.Fatalf("sticky option must reuse the exact proxy identity: %#v", shared)
	}
	if shared.Promotion == shared.Checkout {
		t.Fatal("Promotion must remain independent from the shared PayPal identity")
	}

	independent, err := ResolveStageProxies(NewRuntimeConfig(""), base, MethodPayPalBA)
	if err != nil {
		t.Fatal(err)
	}
	if independent.Checkout == independent.Provider || independent.Checkout == independent.Approve || independent.Provider == independent.Approve {
		t.Fatalf("disabled sticky option must retain independent identities: %#v", independent)
	}
}

func TestPayPalStageRegionsKeepBillingAndPromotionIndependent(t *testing.T) {
	regions := defaultStageRegions(MethodPayPalBA, Options{
		Country: "TH", PromotionProxyRegion: "TR",
	})
	if regions.checkout != "TH" || regions.provider != "TH" || regions.approve != "TH" || regions.promotion != "TR" {
		t.Fatalf("unexpected TH+TR stage regions: %#v", regions)
	}

	regions = defaultStageRegions(MethodPayPalBA, Options{
		Country: "PH", PromotionProxyRegion: "JP", ProviderProxyRegion: "TR", ApproveProxyRegion: "JP",
	})
	if regions.checkout != "PH" || regions.promotion != "JP" || regions.provider != "TR" || regions.approve != "JP" {
		t.Fatalf("explicit stage-region overrides were ignored: %#v", regions)
	}
}

func TestPayPalProxyRegionComesFromExplicitProxyNotBillingCountry(t *testing.T) {
	regions := defaultStageRegions(MethodPayPalBA, Options{
		Country:        "GB",
		Proxy:          "http://user-region-CA-sid-fixed-t-5:pass@proxy.test:3010",
		PromotionProxy: "http://user-region-TR-sid-fixed-t-5:pass@proxy.test:3010",
	})
	if regions.checkout != "CA" || regions.provider != "CA" || regions.approve != "CA" || regions.promotion != "TR" {
		t.Fatalf("explicit proxy regions must win over PayPal billing country: %#v", regions)
	}
}

func TestKakaoDefaultsToVerifiedKRTRKRRoute(t *testing.T) {
	options := normalizeOptions(MethodKakao, Options{})
	if options.Country != "KR" || options.Currency != "KRW" || options.PromotionProxyRegion != "" {
		t.Fatalf("unexpected Kakao defaults: %#v", options)
	}
	if options.KakaoMode != KakaoModeEligibility || !options.KakaoEligibilityOnly || options.MaxAttempts != 3 || options.PromoEnabled() || options.PaymentStatusAutoRefresh {
		t.Fatalf("Kakao eligibility must keep the configurable attempt default: %#v", options)
	}
	// Stage defaults still keep the verified KR/TR/KR route when the caller does
	// not select an explicit promotion region.
	regions := defaultStageRegions(MethodKakao, options)
	if regions.checkout != "KR" || regions.promotion != "TR" || regions.provider != "KR" || regions.approve != "KR" {
		t.Fatalf("unexpected Kakao route: %#v", regions)
	}

	provider := normalizeOptions(MethodKakao, Options{KakaoMode: KakaoModeProviderLink, PromotionProxyRegion: "jp"})
	if provider.Country != "KR" || provider.Currency != "KRW" || provider.KakaoEligibilityOnly || provider.MaxAttempts != 10 || !provider.PromoEnabled() || provider.PromotionProxyRegion != "JP" {
		t.Fatalf("unexpected Kakao provider-link defaults: %#v", provider)
	}

	blankProvider := normalizeOptions(MethodKakao, Options{KakaoMode: KakaoModeProviderLink})
	if blankProvider.PromotionProxyRegion != "" {
		t.Fatalf("blank provider-link must not invent promotion regions: %#v", blankProvider)
	}

	explicitPromotion := normalizeOptions(MethodKakao, Options{
		KakaoMode:            KakaoModeProviderLink,
		PromotionProxy:       "proxy.test:3010:user-region-TR-sid-fixed-t-5:pass",
		PromotionProxyRegion: "JP",
	})
	if explicitPromotion.PromotionProxyRegion != "TR" {
		t.Fatalf("explicit Kakao promotion proxy region must override a stale selector: %#v", explicitPromotion)
	}
}

func TestKakaoProviderLinkAllowsOriginalMultiRoundAttemptBudget(t *testing.T) {
	options := normalizeOptions(MethodKakao, Options{KakaoMode: KakaoModeProviderLink, MaxAttempts: 10})
	if options.Attempts() != 10 {
		t.Fatalf("provider-link attempts = %d, want 10", options.Attempts())
	}
	options.MaxAttempts = 250
	if options.Attempts() != 100 {
		t.Fatalf("provider-link attempts must clamp at 100, got %d", options.Attempts())
	}
}

func TestProviderHelpers(t *testing.T) {
	token := "BA-12345678"
	value := map[string]any{
		"status":      "requires_approval",
		"next_action": map[string]any{"redirect_to_url": map[string]any{"url": "https://www.paypal.com/agreements/approve?ba_token=" + token}},
	}
	if !requiresApproval(value) {
		t.Fatal("requires_approval not detected")
	}
	if got := extractPayPalBAURL(value); !strings.Contains(got, "ba_token="+token) {
		t.Fatalf("PayPal BA link not extracted: %q", got)
	}
	encoded, _ := json.Marshal(value)
	if got := extractRedirectURL(decodeLooseJSON(encoded)); got == "" {
		t.Fatal("provider redirect not extracted")
	}
}

func TestProviderRedirectPrefersNestedNextActionOverUnrelatedURLs(t *testing.T) {
	value := map[string]any{
		"account_settings": map[string]any{
			"logo_url": "https://stripe-camo.global.ssl.fastly.net/image",
		},
		"setup_intent": map[string]any{
			"status": "requires_action",
			"next_action": map[string]any{
				"redirect_to_url": map[string]any{
					"url": "https://pm-redirects.stripe.com/authorize/example",
				},
			},
		},
	}
	if got := extractRedirectURL(value); got != "https://pm-redirects.stripe.com/authorize/example" {
		t.Fatalf("redirect = %q, want nested provider next_action", got)
	}
}

func TestPayPalRedirectRejectsUnrelatedPaymentMethodHooks(t *testing.T) {
	if providerRedirectMatches("https://pm-hooks.stripe.com/apple_pay/merchant_token/example", "paypal") {
		t.Fatal("Apple Pay hook must not terminate PayPal redirect polling")
	}
	for _, candidate := range []string{
		"https://pm-redirects.stripe.com/authorize/example",
		"https://www.paypal.com/agreements/approve?ba_token=BA-example123",
	} {
		if !providerRedirectMatches(candidate, "paypal") {
			t.Fatalf("valid PayPal provider redirect was rejected: %s", candidate)
		}
	}
}

func TestStrictPayPalBAApproveURL(t *testing.T) {
	valid := []string{
		"https://www.paypal.com/agreements/approve?ba_token=BA-12345678",
		"https://www.sandbox.paypal.com/agreements/approve/?ba_token=BA-example_123",
	}
	for _, candidate := range valid {
		if !isStrictPayPalBAApproveURL(candidate) {
			t.Errorf("valid PayPal BA URL rejected: %s", candidate)
		}
	}
	invalid := []string{
		"http://www.paypal.com/agreements/approve?ba_token=BA-12345678",
		"https://evil-paypal.com/agreements/approve?ba_token=BA-12345678",
		"https://paypal.com.evil.test/agreements/approve?ba_token=BA-12345678",
		"https://www.paypal.com/signin?ba_token=BA-12345678",
		"https://www.paypal.com/agreements/approve",
		"https://www.paypal.com/agreements/approve?ba_token=BA-123",
		"https://user@www.paypal.com/agreements/approve?ba_token=BA-12345678",
		"https://www.paypal.com:8443/agreements/approve?ba_token=BA-12345678",
		"https://www.paypal.com/agreements/approve?ba_token=BA-12345678#fragment",
		"https://www.paypal.com/webapps/mpp/logo.png?ba_token=BA-12345678",
	}
	for _, candidate := range invalid {
		if isStrictPayPalBAApproveURL(candidate) {
			t.Errorf("invalid PayPal BA URL accepted: %s", candidate)
		}
	}
	if got := extractPayPalBAURL(map[string]any{"paypal": "BA-12345678"}); got != "" {
		t.Fatalf("a standalone BA token must not be reconstructed into an approve URL: %q", got)
	}
}

func TestRequireZeroAmount(t *testing.T) {
	for _, amount := range []string{"0", "0.00", " 0 ", "₱0.00", "PHP 0.00", "0.00 PHP"} {
		if err := requireZeroAmount("test", amount); err != nil {
			t.Errorf("zero amount %q rejected: %v", amount, err)
		}
	}
	for _, amount := range []string{"", "unknown", "0.01", "1", "-1", "₱117.86", "PHP 982.14", "₱1,100.00"} {
		if err := requireZeroAmount("test", amount); err == nil {
			t.Errorf("non-zero/unknown amount %q accepted", amount)
		}
	}
}

func TestRotateProxyPoolStartsWithSuccessfulProxy(t *testing.T) {
	pool := []string{"proxy-a", "proxy-b", "proxy-c"}
	if got := strings.Join(rotateProxyPool(pool, 2), ","); got != "proxy-b,proxy-c,proxy-a" {
		t.Fatalf("rotated pool = %q", got)
	}
	if got := strings.Join(rotateProxyPool(pool, 1), ","); got != "proxy-a,proxy-b,proxy-c" {
		t.Fatalf("first proxy rotation changed order: %q", got)
	}
}

func TestEvaluateAccountEligibility(t *testing.T) {
	freeModels := map[string]any{"models": []any{map[string]any{"slug": "gpt-5-mini"}}}
	email, plan, err := evaluateAccountEligibility("", map[string]any{"email": "free@example.com", "plan_type": "free"}, freeModels)
	if err != nil || email != "free@example.com" || plan != "free" {
		t.Fatalf("email-bound Free account rejected: email=%q plan=%q err=%v", email, plan, err)
	}
	if _, _, err := evaluateAccountEligibility("", map[string]any{"plan_type": "free"}, freeModels); err == nil {
		t.Fatal("account without a bound email must be rejected")
	}
	if _, _, err := evaluateAccountEligibility("", map[string]any{"email": "plus@example.com", "plan_type": "plus"}, freeModels); err == nil {
		t.Fatal("explicit paid account must be rejected")
	}
	paidModels := map[string]any{"models": []any{map[string]any{"slug": "gpt-5-thinking"}}}
	if email, plan, err := evaluateAccountEligibility("", map[string]any{"email": "stale@example.com", "plan_type": "free"}, paidModels); err != nil || email != "stale@example.com" || plan != "free" {
		t.Fatalf("explicit Free /me label must not be overridden by model heuristics: email=%q plan=%q err=%v", email, plan, err)
	}
	freeJWT := testPlanJWT(t, "jwtfree@example.com", "free")
	if email, plan, err := evaluateAccountEligibility(freeJWT, map[string]any{"email": "jwtfree@example.com"}, paidModels); err != nil || email != "jwtfree@example.com" || plan != "free" {
		t.Fatalf("signed Free JWT claim must not be overridden by model heuristics: email=%q plan=%q err=%v", email, plan, err)
	}
	plusJWT := testPlanJWT(t, "jwtplus@example.com", "plus")
	if _, _, err := evaluateAccountEligibility(plusJWT, map[string]any{"email": "jwtplus@example.com"}, freeModels); err == nil {
		t.Fatal("signed paid JWT claim must be rejected when /me omits plan")
	}
}

func TestEveryMethodUsesAccountEligibilityGate(t *testing.T) {
	sentinel := errors.New("eligibility stopped flow")
	for _, method := range Catalog() {
		engine := &Engine{Config: NewRuntimeConfig(""), Endpoints: DefaultEndpoints(), eligibilityProbe: func(*flow) error { return sentinel }}
		options := Options{Proxy: "http://user:pass@proxy.test:3010"}
		if method.ID == MethodKakao {
			options.KakaoMode = KakaoModeEligibility
		}
		_, err := engine.Run(context.Background(), method.ID, Credential{AccessToken: strings.Repeat("t", 90)}, options, nil)
		if !errors.Is(err, sentinel) {
			t.Errorf("method %s did not stop at the shared eligibility gate: %v", method.ID, err)
		}
	}
}

func TestElementsBaseFormUsesProviderStripeContext(t *testing.T) {
	f := &flow{profile: profileForCountry("TR")}
	ctx := stripeContext{StripeVersion: paypalStripeVersion}
	form := f.elementsBaseForm(checkoutData{ID: "cs_live_example"}, ctx, false)
	if got := form.Get("_stripe_version"); got != paypalStripeVersion {
		t.Fatalf("stripe version = %q, want PayPal context version", got)
	}
}

func TestKakaoCheckoutHeadersStayMinimal(t *testing.T) {
	f := &flow{
		credential:  Credential{AccessToken: "token"},
		profile:     profileForCountry("KR"),
		fingerprint: resolveRequestFingerprint("chrome136"),
	}
	createHeaders := f.kakaoCheckoutHeaders()
	for _, forbidden := range []string{"Cookie", "Origin", "Referer", "oai-device-id", "oai-session-id", "sec-ch-ua", "x-openai-target-path"} {
		if _, ok := createHeaders[forbidden]; ok {
			t.Fatalf("Kakao checkout creation must omit %s: %#v", forbidden, createHeaders)
		}
	}
	apiHeaders := f.kakaoCheckoutAPIHeaders("https://chatgpt.com/checkout/openai_llc/cs_live_example", "/backend-api/payments/checkout/update")
	if apiHeaders["Referer"] == "" || apiHeaders["x-openai-target-path"] == "" || apiHeaders["x-openai-target-route"] == "" {
		t.Fatalf("Kakao checkout API routing headers missing: %#v", apiHeaders)
	}
}

func TestKakaoStripeInitKeepsReferenceParametersAndJSID(t *testing.T) {
	f := &flow{method: MethodKakao, profile: profileForCountry("KR")}
	form, stripeJSID := f.stripeInitForm(checkoutData{PublishableKey: "pk_live_example"}, true)
	if form.Get("eid") != "NA" || form.Get("redirect_type") != "url" {
		t.Fatalf("Kakao init reference parameters missing: %v", form)
	}
	if stripeJSID == "" || form.Get("elements_session_client[stripe_js_id]") != stripeJSID {
		t.Fatalf("Kakao Stripe JS ID was not preserved: %q %v", stripeJSID, form)
	}
	ctx := newStripeContext(stripeInit{StripeJSID: stripeJSID}, false)
	if ctx.StripeJSID != stripeJSID {
		t.Fatalf("Stripe context generated a different JS ID: %q != %q", ctx.StripeJSID, stripeJSID)
	}
}

func TestStripeHeadersUseActivatedCheckoutPageContext(t *testing.T) {
	headers := stripeHeaders(checkoutData{
		ID:             "cs_live_example",
		PublishableKey: "pk_live_example",
		PaymentPageURL: "https://checkout.stripe.com/c/pay/cs_live_example",
	}, profileForCountry("KR"))
	if headers["Origin"] != "https://checkout.stripe.com" || headers["Referer"] != "https://checkout.stripe.com/c/pay/cs_live_example" {
		t.Fatalf("unexpected activated Stripe page headers: %#v", headers)
	}
}

func TestRequestFingerprintKeepsTLSAndHeadersInOneBrowserFamily(t *testing.T) {
	firefox := resolveRequestFingerprint("firefox147")
	if !firefox.Firefox || firefox.Name != "firefox147" || !strings.Contains(firefox.UserAgent, "Firefox/147.0") {
		t.Fatalf("unexpected Firefox fingerprint: %#v", firefox)
	}
	chrome := resolveRequestFingerprint("chrome")
	if chrome.Firefox || chrome.Name != "chrome146" || !strings.Contains(chrome.UserAgent, "Chrome/147.0.0.0") {
		t.Fatalf("unexpected Chrome fingerprint: %#v", chrome)
	}
	chrome136 := resolveRequestFingerprint("chrome136")
	if chrome136.Name != "chrome133-reference" || !strings.Contains(chrome136.UserAgent, "Chrome/147.0.0.0") {
		t.Fatalf("unexpected Chrome 136 reference fingerprint: %#v", chrome136)
	}
}

func TestKakaoElementsPollAndReturnURLMatchReferenceFlow(t *testing.T) {
	f := &flow{engine: &Engine{Endpoints: DefaultEndpoints()}, profile: profileForCountry("KR")}
	ctx := stripeContext{StripeJSID: "js-example", ElementsSessionID: "elements_session_example"}
	params := f.providerPollParams(checkoutData{PublishableKey: "pk_live_example"}, "kakao", ctx)
	if params.Get("key") == "" || params.Get("elements_session_client[stripe_js_id]") != ctx.StripeJSID {
		t.Fatalf("Kakao poll lost Elements context: %v", params)
	}
	for _, forbidden := range []string{"_stripe_version", "client_attribution_metadata[checkout_session_id]"} {
		if params.Has(forbidden) {
			t.Fatalf("Kakao poll must omit %s: %v", forbidden, params)
		}
	}
	returnURL := f.providerReturnURL(checkoutData{ID: "cs_live_example", ProcessorEntity: "openai_llc"}, "kakao_pay")
	if !strings.HasPrefix(returnURL, "https://checkout.stripe.com/c/pay/cs_live_example?") || !strings.Contains(returnURL, "billing_country%3DKR") {
		t.Fatalf("unexpected Kakao return URL: %s", returnURL)
	}
}

func TestKakaoStageProxiesReuseExactStickyCheckoutIdentity(t *testing.T) {
	proxies, err := ResolveStageProxies(NewRuntimeConfig(""), Options{
		ProxyMode:            "custom",
		Proxy:                "http://user-country-KR-sid-original-t-10:pass@proxy.test:8080",
		PromotionProxyRegion: "TR",
	}, MethodKakao)
	if err != nil {
		t.Fatal(err)
	}
	if proxies.Provider != proxies.Checkout || proxies.Approve != proxies.Checkout {
		t.Fatalf("Kakao payment stages must reuse checkout sticky identity: %#v", proxies)
	}
	if proxies.Promotion == proxies.Checkout || !strings.Contains(proxies.Promotion, "country-TR") {
		t.Fatalf("promotion must keep its independent TR identity: %#v", proxies)
	}
	checkoutSID := sidPattern.FindString(proxies.Checkout)
	promotionSID := sidPattern.FindString(proxies.Promotion)
	if checkoutSID == "" || promotionSID != checkoutSID {
		t.Fatalf("Kakao promotion must preserve checkout sticky Seed: checkout=%q promotion=%q", checkoutSID, promotionSID)
	}
}

func TestKakaoProviderLinkUsesExplicitProxiesExactly(t *testing.T) {
	mainRaw := "proxy.test:3010:user-region-KR-sid-main-t-5:pass"
	promotionRaw := "proxy.test:3010:user-region-TR-sid-promo-t-5:pass"
	expectedMain, err := normalizeProxy(mainRaw)
	if err != nil {
		t.Fatal(err)
	}
	expectedPromotion, err := normalizeProxy(promotionRaw)
	if err != nil {
		t.Fatal(err)
	}

	proxies, err := ResolveStageProxies(NewRuntimeConfig(""), Options{
		ProxyMode:            "custom",
		Proxy:                mainRaw,
		PromotionProxy:       promotionRaw,
		PromotionProxyRegion: "JP",
		KakaoMode:            KakaoModeProviderLink,
	}, MethodKakao)
	if err != nil {
		t.Fatal(err)
	}
	if proxies.Checkout != expectedMain || proxies.Provider != expectedMain || proxies.Approve != expectedMain {
		t.Fatalf("provider-link main stages must reuse the exact explicit KR proxy: %#v", proxies)
	}
	if proxies.Promotion != expectedPromotion {
		t.Fatalf("explicit promotion proxy was rewritten: got %q want %q", proxies.Promotion, expectedPromotion)
	}
	if !strings.Contains(proxies.Promotion, "region-TR") || !strings.Contains(proxies.Promotion, "sid-promo-t-") || strings.Contains(proxies.Promotion, "region-JP") {
		t.Fatalf("explicit TR region/SID must survive unchanged: %q", proxies.Promotion)
	}
	if !strings.Contains(proxies.Labels["promotion"], "显式代理原样使用") {
		t.Fatalf("promotion label must state exact explicit handling: %#v", proxies.Labels)
	}
}

func TestKakaoProviderLinkBlankPromotionRequiresExplicitRegionOrProxy(t *testing.T) {
	base := Options{
		ProxyMode: "custom",
		Proxy:     "proxy.test:3010:user-region-KR-sid-original-t-5:pass",
		KakaoMode: KakaoModeProviderLink,
	}
	normalized := normalizeOptions(MethodKakao, base)
	if normalized.PromotionProxyRegion != "" {
		t.Fatalf("blank provider-link regions = %q, want empty", normalized.PromotionProxyRegion)
	}
	if _, err := ResolveStageProxies(NewRuntimeConfig(""), normalized, MethodKakao); err == nil || !strings.Contains(err.Error(), "未配置优惠代理或优惠地区") {
		t.Fatalf("blank promotion must fail closed, err=%v", err)
	}

	// Explicit multi-select still rotates JP/VN/TR from the same sticky seed.
	normalized.PromotionProxyRegion = "JP,VN,TR"
	for attempt, want := range []string{"JP", "VN", "TR", "JP"} {
		selected := SelectAttemptOptions(normalized, attempt+1)
		proxies, err := ResolveStageProxies(NewRuntimeConfig(""), selected, MethodKakao)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(proxies.Promotion, "region-"+want) {
			t.Fatalf("attempt %d promotion = %q, want region-%s", attempt+1, proxies.Promotion, want)
		}
		if !strings.Contains(proxies.Promotion, "sid-original-t-") {
			t.Fatalf("selected promotion must preserve the caller sticky seed: %q", proxies.Promotion)
		}
		if !strings.Contains(proxies.Labels["promotion"], "从主代理保持 sticky Seed 派生") {
			t.Fatalf("promotion label must describe derivation: %#v", proxies.Labels)
		}
	}
}

func TestKakaoProviderLinkRejectsNonKRMainSelector(t *testing.T) {
	_, err := ResolveStageProxies(NewRuntimeConfig(""), Options{
		ProxyMode: "custom",
		Proxy:     "proxy.test:3010:user-region-JP-sid-original-t-5:pass",
		KakaoMode: KakaoModeProviderLink,
	}, MethodKakao)
	if err == nil || !strings.Contains(err.Error(), "必须为 KR") {
		t.Fatalf("provider-link main proxy with an explicit non-KR selector must fail clearly, got %v", err)
	}
}

func TestRawProxyIsNormalizedBeforeRegionRewrite(t *testing.T) {
	proxies, err := ResolveStageProxies(NewRuntimeConfig(""), Options{
		ProxyMode:            "custom",
		Proxy:                "proxy.test:8080:user-country-MX-sid-original-t-10:pass",
		PromotionProxyRegion: "TR",
	}, MethodKakao)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(proxies.Checkout, "country-MX") || !strings.Contains(proxies.Checkout, "country-KR") {
		t.Fatalf("raw checkout proxy was labelled KR without being rewritten: %q", proxies.Checkout)
	}
	if !strings.Contains(proxies.Promotion, "country-TR") {
		t.Fatalf("raw promotion proxy was not rewritten to TR: %q", proxies.Promotion)
	}
	checkoutSID := sidPattern.FindString(proxies.Checkout)
	promotionSID := sidPattern.FindString(proxies.Promotion)
	if checkoutSID == "" || promotionSID != checkoutSID {
		t.Fatalf("regional hops must preserve one request-scoped sticky identity: checkout=%q promotion=%q", checkoutSID, promotionSID)
	}
}

func TestKakaoCurlTransportUsesStdinProtocolAndStreamsSteps(t *testing.T) {
	temporary := t.TempDir()
	helper := filepath.Join(temporary, "fake-kakao-helper.sh")
	script := `#!/bin/sh
if [ "$#" -ne 0 ]; then
  printf '%s\n' '{"type":"error","message":"secret was passed through argv"}'
  exit 1
fi
payload=$(cat)
case "$payload" in
  *stdin-secret-token*) ;;
  *) printf '%s\n' '{"type":"error","message":"stdin credential missing"}'; exit 1 ;;
esac
printf '%s\n' '{"type":"step","stage":"kakao.transport","status":"success","detail":"fake curl_cffi"}'
printf '%s\n' '{"type":"result","result":{"ok":true,"method":"kakao","country":"KR","currency":"KRW","longUrl":"https://pay.nicepay.co.kr/v1/checkout/pay/example","providerRedirectUrl":"https://pay.nicepay.co.kr/v1/checkout/pay/example","extractionStatus":"provider_link_ready","paymentStatus":"awaiting_kakao_payment"}}'
`
	if err := os.WriteFile(helper, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	var progress []string
	f := &flow{
		ctx: context.Background(), method: MethodKakao,
		engine:     &Engine{KakaoHelperPath: helper, PythonExecutable: "/bin/sh"},
		credential: Credential{AccessToken: "stdin-secret-token"},
		options:    Options{TimeoutSeconds: 45, MaxAttempts: 1, MaxAmountMinor: 100, KakaoMode: KakaoModeProviderLink},
		proxies: StageProxies{
			Checkout: "http://user:pass@proxy.test:3010", Promotion: "http://user:pass@proxy.test:3010",
			Provider: "http://user:pass@proxy.test:3010",
		},
		progress: func(step Step) { progress = append(progress, step.Stage+":"+step.Status+":"+step.Detail) },
	}
	result, err, handled := f.runKakaoCurlTransport()
	if err != nil || !handled {
		t.Fatalf("fake helper failed: handled=%v err=%v", handled, err)
	}
	if !result.OK || !strings.Contains(result.LongURL, "nicepay.co.kr") {
		t.Fatalf("unexpected helper result: %#v", result)
	}
	if len(progress) != 1 || !strings.Contains(progress[0], "kakao.transport:success") {
		t.Fatalf("helper progress was not streamed: %v", progress)
	}
}

func TestKakaoCurlTransportAcceptsEligibilityResultWithoutProviderLink(t *testing.T) {
	temporary := t.TempDir()
	helper := filepath.Join(temporary, "fake-kakao-eligibility-helper.sh")
	script := `#!/bin/sh
payload=$(cat)
case "$payload" in
  *'"eligibilityOnly":true'*) ;;
  *) printf '%s\n' '{"type":"error","message":"eligibilityOnly missing"}'; exit 1 ;;
esac
printf '%s\n' '{"type":"step","stage":"kakao.diagnostic_stop","status":"success","detail":"stopped before payment"}'
printf '%s\n' '{"type":"result","result":{"ok":true,"method":"kakao","country":"KR","currency":"KRW","availableMethods":["card","kakao_pay"],"extractionStatus":"probe_complete","paymentStatus":"not_started","decision":"eligible","metadata":{"diagnosticOnly":true,"stoppedBeforePayment":true}}}'
`
	if err := os.WriteFile(helper, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	f := &flow{
		ctx: context.Background(), method: MethodKakao,
		engine:     &Engine{KakaoHelperPath: helper, PythonExecutable: "/bin/sh"},
		credential: Credential{AccessToken: "stdin-secret-token"},
		options:    Options{TimeoutSeconds: 45, MaxAttempts: 1, MaxAmountMinor: 100, KakaoMode: KakaoModeEligibility, KakaoEligibilityOnly: true},
		proxies: StageProxies{
			Checkout: "http://user:pass@proxy.test:8080", Promotion: "http://user:pass@proxy.test:8080", Provider: "http://user:pass@proxy.test:8080",
		},
	}
	result, err, handled := f.runKakaoCurlTransport()
	if !handled || err != nil {
		t.Fatalf("eligibility result was rejected: handled=%v err=%v result=%#v", handled, err, result)
	}
	if result.Decision != "eligible" || result.ExtractionStatus != "probe_complete" || result.LongURL != "" || result.PaymentMethodID != "" {
		t.Fatalf("unexpected eligibility result: %#v", result)
	}
}

func TestKakaoCurlTransportDoesNotFallbackAfterUpstreamFailure(t *testing.T) {
	temporary := t.TempDir()
	helper := filepath.Join(temporary, "fake-kakao-helper.sh")
	script := `#!/bin/sh
cat >/dev/null
printf '%s\n' '{"type":"error","message":"upstream checkout did not advertise kakao_pay","partial":{"country":"KR","currency":"KRW","availableMethods":["card","link"],"extractionStatus":"failed","paymentStatus":"not_started"}}'
exit 1
`
	if err := os.WriteFile(helper, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	f := &flow{
		ctx: context.Background(), method: MethodKakao,
		engine:     &Engine{KakaoHelperPath: helper, PythonExecutable: "/bin/sh"},
		credential: Credential{AccessToken: "stdin-secret-token"},
		options:    Options{TimeoutSeconds: 45, MaxAttempts: 1, MaxAmountMinor: 100},
		proxies: StageProxies{
			Checkout: "http://user:pass@proxy.test:3010", Promotion: "http://user:pass@proxy.test:3010",
			Provider: "http://user:pass@proxy.test:3010",
		},
	}
	result, err, handled := f.runKakaoCurlTransport()
	if err == nil || !handled {
		t.Fatalf("upstream failure must be terminal for this checkout: handled=%v err=%v", handled, err)
	}
	if strings.Join(result.AvailableMethods, ",") != "card,link" {
		t.Fatalf("partial upstream methods were lost: %#v", result)
	}
}

func TestKakaoCurlTransportReportsMissingHelperAsUnavailable(t *testing.T) {
	f := &flow{
		ctx: context.Background(), method: MethodKakao,
		engine: &Engine{KakaoHelperPath: filepath.Join(t.TempDir(), "missing.py"), PythonExecutable: "/bin/sh"},
	}
	_, err, handled := f.runKakaoCurlTransport()
	if err == nil || handled {
		t.Fatalf("missing helper should be reported as unavailable: handled=%v err=%v", handled, err)
	}
}

func TestKakaoEngineRejectsMissingVerifiedHelperForBothModes(t *testing.T) {
	for _, mode := range []string{KakaoModeEligibility, KakaoModeProviderLink} {
		t.Run(mode, func(t *testing.T) {
			engine := &Engine{
				Config: NewRuntimeConfig(""), Endpoints: DefaultEndpoints(),
				KakaoHelperPath: filepath.Join(t.TempDir(), "missing.py"), PythonExecutable: "/bin/sh",
				eligibilityProbe: func(*flow) error { return nil },
			}
			_, err := engine.Run(context.Background(), MethodKakao, Credential{AccessToken: strings.Repeat("t", 90)}, Options{
				Proxy: "http://user:pass@proxy.test:3010", KakaoMode: mode, PromotionProxyRegion: "JP",
			}, nil)
			if err == nil || !strings.Contains(err.Error(), "已拒绝回退到旧流程") {
				t.Fatalf("mode %s should fail closed when the verified helper is missing: %v", mode, err)
			}
		})
	}
}

func TestKakaoEngineRejectsUnknownModeBeforeAnyTransport(t *testing.T) {
	engine := &Engine{Config: NewRuntimeConfig(""), Endpoints: DefaultEndpoints()}
	_, err := engine.Run(context.Background(), MethodKakao, Credential{AccessToken: strings.Repeat("t", 90)}, Options{
		Proxy: "http://user:pass@proxy.test:3010", KakaoMode: "surprise",
	}, nil)
	if err == nil || !strings.Contains(err.Error(), "不支持的 Kakao 模式") {
		t.Fatalf("unknown Kakao mode must be rejected: %v", err)
	}
}

func TestPayPalPollContractDoesNotReuseConfirmParameters(t *testing.T) {
	f := &flow{profile: profileForCountry("TR")}
	params := f.providerPollParams(checkoutData{PublishableKey: "pk_live_example"}, "paypal", stripeContext{})
	if len(params) != 2 || params.Get("key") == "" || params.Get("eid") != "NA" {
		t.Fatalf("unexpected PayPal poll parameters: %v", params)
	}
	for _, forbidden := range []string{"version", "init_checksum", "payment_method", "browser_locale", "browser_timezone", "redirect_type"} {
		if params.Has(forbidden) {
			t.Fatalf("PayPal poll must not contain %s", forbidden)
		}
	}
}

func TestIDEALPollContractOmitsConfirmOnlyMetadata(t *testing.T) {
	f := &flow{profile: profileForCountry("NL")}
	ctx := stripeContext{StripeJSID: "js-example", ElementsSessionID: "elements_session_example", ClientSessionID: "client-example"}
	params := f.providerPollParams(checkoutData{PublishableKey: "pk_live_example"}, "ideal", ctx)
	if params.Get("key") == "" || params.Get("elements_session_client[stripe_js_id]") != ctx.StripeJSID {
		t.Fatalf("iDEAL poll lost required Elements context: %v", params)
	}
	for _, forbidden := range []string{"_stripe_version", "client_attribution_metadata[client_session_id]", "client_attribution_metadata[checkout_session_id]"} {
		if params.Has(forbidden) {
			t.Fatalf("iDEAL payment-page GET must omit %s: %v", forbidden, params)
		}
	}
}

func TestExtractUPIMaterialKeepsQRAndInstructionVariants(t *testing.T) {
	material := extractUPIMaterial(map[string]any{
		"next_action": map[string]any{
			"upi_handle_redirect_or_display_qr_code": map[string]any{
				"payload":                 "upi://pay?pa=merchant@example&am=1.00&cu=INR",
				"hosted_instructions_url": "https://payments.example.test/upi/instructions/123",
				"image_url_png":           "https://payments.example.test/upi/123.png",
				"image_url_svg":           "https://payments.example.test/upi/123.svg",
			},
		},
	})
	if material.Payload == "" || material.Instruction == "" || material.PNG == "" || material.SVG == "" {
		t.Fatalf("UPI material was not preserved: %#v", material)
	}
	if got := material.URL(); got != material.Instruction {
		t.Fatalf("instruction URL must remain the compatibility longUrl, got %q", got)
	}
	result := Result{
		LongURL: material.URL(), UPIPayload: material.Payload, UPIInstructionURL: material.Instruction,
		QRPNGURL: material.PNG, QRSVGURL: material.SVG,
	}
	encoded, err := json.Marshal(result)
	if err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"upiPayload", "upiInstructionUrl", "qrPngUrl", "qrSvgUrl"} {
		if !strings.Contains(string(encoded), `"`+field+`"`) {
			t.Fatalf("result JSON is missing %s: %s", field, encoded)
		}
	}
}

func TestExtractUPIMaterialRejectsStripePaymentMethodIcon(t *testing.T) {
	const iconURL = "https://js.stripe.com/v3/fingerprinted/img/payment-methods/icon-pm-upi@3x-aa4e85e1736f6af87f932f18058386b4.png"
	const iconSVG = "https://js.stripe.com/v3/fingerprinted/img/payment-methods/icon-pm-upi-9107c036320866a1dae0be4b59015a31.svg"
	material := extractUPIMaterial(map[string]any{
		"payment_method": map[string]any{
			"image_url_png": iconURL,
			"image_url_svg": iconSVG,
			"icon_url":      iconURL,
		},
	})
	if material.Ready() || material.PNG != "" || material.SVG != "" || material.Redirect != "" {
		t.Fatalf("static Stripe UPI icon was treated as payment material: %#v", material)
	}
}

func TestExtractUPIMaterialRejectsGenericStripeCheckoutURL(t *testing.T) {
	material := extractUPIMaterial(map[string]any{
		"next_action": map[string]any{
			"redirect_to_url": map[string]any{
				"url": "https://checkout.stripe.com/c/pay/cs_live_example#fragment",
			},
		},
	})
	if material.Ready() {
		t.Fatalf("generic Stripe Checkout URL was treated as UPI material: %#v", material)
	}
}

func TestExtractUPIMaterialRejectsUnrelatedGenericURL(t *testing.T) {
	material := extractUPIMaterial(map[string]any{
		"customer": map[string]any{"url": "https://example.test/account/home"},
	})
	if material.Ready() {
		t.Fatalf("unrelated URL was treated as UPI material: %#v", material)
	}
}

func TestExtractUPIMaterialIgnoresIconAndKeepsRealUPIMaterial(t *testing.T) {
	const iconURL = "https://js.stripe.com/v3/fingerprinted/img/payment-methods/icon-pm-upi@3x-aa4e85e1736f6af87f932f18058386b4.png"
	material := extractUPIMaterial(map[string]any{
		"payment_method_icon": map[string]any{"image_url_png": iconURL},
		"next_action": map[string]any{
			"upi_handle_redirect_or_display_qr_code": map[string]any{
				"payload":                 "upi://pay?pa=merchant@example&am=1.00&cu=INR",
				"hosted_instructions_url": "https://payments.example.test/upi/instructions/123",
				"image_url_png":           "https://payments.example.test/upi/123.png",
			},
		},
	})
	if material.Payload == "" || material.Instruction == "" || material.PNG == "" {
		t.Fatalf("real UPI material was not preserved: %#v", material)
	}
	if material.PNG == iconURL || material.Redirect == iconURL {
		t.Fatalf("static Stripe UPI icon leaked into payment material: %#v", material)
	}
}

func TestCredentialLabelPrefersEmailOverStructuredAccount(t *testing.T) {
	token := strings.Repeat("x", 90)
	input, err := json.Marshal(map[string]any{
		"accessToken": token,
		"email":       "person@example.test",
		"account": map[string]any{
			"id":       "workspace-123",
			"planType": "free",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	credentials, err := ParseBatchCredentials(string(input), nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(credentials) != 1 || credentials[0].Label != "person@example.test" {
		t.Fatalf("structured account leaked into label: %#v", credentials)
	}
}

func TestParseProxyPoolAndAttemptSelection(t *testing.T) {
	pool := ParseProxyPool("us.cliproxy.io:3010:user-region-JP:pass\nus.cliproxy.io:3010:user-region-VN:pass\n# comment\nus.cliproxy.io:3010:user-region-TR:pass")
	if len(pool) != 3 {
		t.Fatalf("proxy pool size = %d, want 3", len(pool))
	}
	regions := ParseRegionPool("jp, vn | tr TR")
	if strings.Join(regions, ",") != "JP,VN,TR" {
		t.Fatalf("region pool = %v", regions)
	}
	selected := SelectAttemptOptions(Options{
		Proxy:                "http://main-kr.test:8080",
		PromotionProxy:       strings.Join(pool, "\n"),
		PromotionProxyRegion: "JP",
	}, 2)
	if selected.PromotionProxy != pool[1] {
		t.Fatalf("attempt 2 promotion = %q, want %q", selected.PromotionProxy, pool[1])
	}
	if selected.PromotionProxyRegion != "VN" {
		t.Fatalf("attempt 2 promotion region = %q, want VN from proxy", selected.PromotionProxyRegion)
	}
	// Main stays sticky when only one main proxy is supplied.
	if selected.Proxy != "http://main-kr.test:8080" {
		t.Fatalf("main proxy changed unexpectedly: %q", selected.Proxy)
	}
	derived := SelectAttemptOptions(Options{
		Proxy:                "http://user-region-KR-sid-fixed-t-5:pass@proxy.test:8080",
		PromotionProxyRegion: "JP,VN,TR",
		KakaoMode:            KakaoModeProviderLink,
	}, 3)
	stage, err := ResolveStageProxies(NewRuntimeConfig(""), derived, MethodKakao)
	if err != nil {
		t.Fatal(err)
	}
	if proxyConfiguredRegion(stage.Promotion) != "TR" {
		t.Fatalf("derived attempt 3 promotion region = %q, want TR", proxyConfiguredRegion(stage.Promotion))
	}
	if proxyConfiguredRegion(stage.Checkout) != "KR" {
		t.Fatalf("checkout region = %q, want KR", proxyConfiguredRegion(stage.Checkout))
	}
}

func TestNormalizeKakaoProviderDefaultsMultiPromoRegions(t *testing.T) {
	options := normalizeOptions(MethodKakao, Options{KakaoMode: KakaoModeProviderLink, MaxAttempts: 10})
	if options.PromotionProxyRegion != "" {
		t.Fatalf("blank provider promotion regions = %q, want empty", options.PromotionProxyRegion)
	}
	selected := normalizeOptions(MethodKakao, Options{KakaoMode: KakaoModeProviderLink, PromotionProxyRegion: "JP,VN,TR", MaxAttempts: 10})
	if selected.PromotionProxyRegion != "JP,VN,TR" {
		t.Fatalf("explicit multi-select regions = %q, want JP,VN,TR", selected.PromotionProxyRegion)
	}
}

func TestApproveRetries(t *testing.T) {
	if got := (Options{}).ApproveRetries(); got != 3 {
		t.Fatalf("default approve retries = %d, want 3", got)
	}
	if got := (Options{ApproveAttempts: 7}).ApproveRetries(); got != 7 {
		t.Fatalf("approve retries = %d, want 7", got)
	}
	if got := (Options{ApproveAttempts: 99}).ApproveRetries(); got != 10 {
		t.Fatalf("approve retries must clamp at 10, got %d", got)
	}
}

func TestStageFingerprintPolicy(t *testing.T) {
	policy := (Options{}).StageFingerprintPolicy()
	if policy["checkout"] != "main" || policy["promotion"] != "follow" || policy["provider"] != "follow" || policy["approve"] != "follow" {
		t.Fatalf("unexpected default fingerprint policy: %#v", policy)
	}
	policy = (Options{FingerprintPolicy: map[string]string{"promotion": "fresh", "approve": "new", "checkout": "fresh"}}).StageFingerprintPolicy()
	if policy["checkout"] != "main" || policy["promotion"] != "fresh" || policy["provider"] != "follow" || policy["approve"] != "fresh" {
		t.Fatalf("unexpected custom fingerprint policy: %#v", policy)
	}
}

func TestSetupIntentErrorMatchesCurrentPaymentMethod(t *testing.T) {
	current := "pm_current"
	stale := map[string]any{
		"setup_intent": map[string]any{
			"last_setup_error": map[string]any{
				"code":           "setup_attempt_failed",
				"payment_method": map[string]any{"id": "pm_other"},
			},
		},
	}
	if got := setupIntentError(stale, current); got != "" {
		t.Fatalf("stale setup error should be ignored, got %q", got)
	}
	currentErr := map[string]any{
		"setup_intent": map[string]any{
			"last_setup_error": map[string]any{
				"code":           "setup_attempt_failed",
				"payment_method": map[string]any{"id": current},
			},
		},
	}
	if got := setupIntentError(currentErr, current); got == "" {
		t.Fatal("current setup error should surface")
	}
}

func TestSetupIntentErrorMatchesNestedPaymentMethodAndPayPalCanIgnore(t *testing.T) {
	body := map[string]any{
		"setup_intent": map[string]any{
			"last_setup_error": map[string]any{
				"code":           "setup_attempt_failed",
				"decline_code":   "generic_decline",
				"payment_method": "pm_current",
			},
		},
	}
	if got := setupIntentError(body, "pm_current"); got == "" {
		t.Fatal("expected setup error for current payment method")
	}
	if got := setupIntentError(body, "pm_other"); got != "" {
		t.Fatalf("expected no setup error for other payment method, got %q", got)
	}
}

func TestPayPalTerminalSetupErrorClassifier(t *testing.T) {
	tests := []struct {
		name   string
		detail string
		want   bool
	}{
		{
			name:   "current generic decline",
			detail: `setup_intent.last_setup_error: {"code":"setup_attempt_failed","decline_code":"generic_decline","payment_method":"pm_current"}`,
			want:   true,
		},
		{
			name:   "other decline remains pollable",
			detail: `setup_intent.last_setup_error: {"code":"setup_attempt_failed","decline_code":"do_not_honor","payment_method":"pm_current"}`,
			want:   false,
		},
		{
			name:   "other setup error remains pollable",
			detail: `setup_intent.last_setup_error: {"code":"api_connection_error","decline_code":"generic_decline","payment_method":"pm_current"}`,
			want:   false,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := isTerminalPayPalSetupError(test.detail); got != test.want {
				t.Fatalf("isTerminalPayPalSetupError(%q) = %t, want %t", test.detail, got, test.want)
			}
		})
	}
}

func TestPayPalTerminalSetupErrorPollCounter(t *testing.T) {
	detail := `setup_intent.last_setup_error: {"code":"setup_attempt_failed","decline_code":"generic_decline","payment_method":"pm_current"}`
	count := 0
	for expected := 1; expected <= paypalTerminalSetupErrorPolls; expected++ {
		count = nextPayPalTerminalSetupErrorCount("paypal", detail, count)
		if count != expected {
			t.Fatalf("poll %d count = %d, want %d", expected, count, expected)
		}
	}
	if count < paypalTerminalSetupErrorPolls {
		t.Fatalf("terminal threshold was not reached: %d", count)
	}
	if got := nextPayPalTerminalSetupErrorCount("paypal", `setup_intent.last_setup_error: {"code":"setup_attempt_failed","decline_code":"do_not_honor"}`, count); got != 0 {
		t.Fatalf("non-terminal PayPal error did not reset count: %d", got)
	}
	if got := nextPayPalTerminalSetupErrorCount("kakao", detail, count); got != 0 {
		t.Fatalf("non-PayPal setup error did not reset count: %d", got)
	}
}

func TestCheckoutNotActiveProviderText(t *testing.T) {
	for _, detail := range []string{
		`{"error":{"code":"checkout_not_active_session"}}`,
		"Checkout Session is no longer active",
		"session no longer active",
	} {
		if !isCheckoutNotActiveProviderText(detail) {
			t.Fatalf("expected terminal checkout detection for %q", detail)
		}
	}
	if isCheckoutNotActiveProviderText("payment page is still processing") {
		t.Fatal("ordinary payment page state was classified as terminal")
	}
}

func TestProviderNetworkRetryClassification(t *testing.T) {
	for _, err := range []error{
		context.DeadlineExceeded,
		errors.New(`Post "https://chatgpt.com/backend-api/payments/checkout": EOF`),
		errors.New("read: connection reset by peer"),
		errors.New("http: server gave HTTP response to HTTPS client"),
		errors.New("stripe.init HTTP 503: unavailable"),
	} {
		if !isProviderNetworkRetry(err) {
			t.Fatalf("expected network retry for %v", err)
		}
	}
	for _, err := range []error{
		context.Canceled,
		errors.New("approve result=blocked"),
		errors.New(`setup_intent.last_setup_error: {"code":"setup_attempt_failed","decline_code":"generic_decline"}`),
		errors.New("ideal checkout 金额必须严格等于 0，当前为 19.01 EUR"),
	} {
		if isProviderNetworkRetry(err) {
			t.Fatalf("business failure was classified as network retry: %v", err)
		}
	}
}

func TestProviderTerminalPaymentClassification(t *testing.T) {
	genericDecline := errors.New(`setup_intent.last_setup_error: {"code":"setup_attempt_failed","decline_code":"generic_decline"}`)
	if !isProviderGenericDecline(genericDecline) {
		t.Fatalf("expected terminal generic decline for %v", genericDecline)
	}
	blocked := errors.New("approve result=blocked")
	if !isProviderApprovalBlocked(blocked) {
		t.Fatalf("expected terminal approval block for %v", blocked)
	}
	for _, err := range []error{
		errors.New("ideal checkout 金额必须严格等于 0，当前为 19.01 EUR"),
		errors.New("checkout 不支持 ideal"),
		errors.New("approve result=denied"),
		errors.New(`setup_intent.last_setup_error: {"code":"setup_attempt_failed","decline_code":"do_not_honor"}`),
	} {
		if isProviderGenericDecline(err) || isProviderApprovalBlocked(err) {
			t.Fatalf("ordinary business result was classified as terminal payment block: %v", err)
		}
	}
}

func TestPayPalAmountMismatchUsesConfiguredRetryBudget(t *testing.T) {
	options := normalizeOptions(MethodPayPalBA, Options{MaxAttempts: 7})
	if options.Attempts() != 7 {
		t.Fatalf("PayPal attempts = %d, want 7", options.Attempts())
	}
	gate := amountGate{Mode: AmountGateStrictZero}
	if err := requireAmountGate("PayPal checkout", "9990", "USD", gate); err == nil {
		t.Fatal("a non-zero PayPal checkout must be discarded before the next full attempt")
	}
	if err := requireAmountGate("PayPal checkout", "0", "USD", gate); err != nil {
		t.Fatalf("a zero PayPal checkout must pass the gate: %v", err)
	}
}

func TestShouldReconfirmPayPalProviderError(t *testing.T) {
	for _, err := range []error{
		errors.New("Provider redirect 轮询超时"),
		errors.New(`setup_intent.last_setup_error: {"code":"setup_attempt_failed","decline_code":"generic_decline"}`),
		errors.New("approve result=blocked"),
	} {
		if !shouldReconfirmPayPalProviderError(err) {
			t.Fatalf("expected reconfirm for %v", err)
		}
	}
	for _, err := range []error{
		nil,
		context.Canceled,
		context.DeadlineExceeded,
		errors.New(`stripe.poll HTTP 400: {"error":{"code":"checkout_not_active_session"}}`),
	} {
		if shouldReconfirmPayPalProviderError(err) {
			t.Fatalf("unexpected reconfirm for %v", err)
		}
	}
}

func TestReconfirmResultSupersedesFirstPollFailure(t *testing.T) {
	firstPollErr := errors.New("Provider redirect 轮询超时")
	if got := reconfirmResultError(firstPollErr, nil); got != nil {
		t.Fatalf("successful reconfirm retained first poll error: %v", got)
	}
	reconfirmErr := errors.New("reconfirm failed")
	if got := reconfirmResultError(firstPollErr, reconfirmErr); !errors.Is(got, reconfirmErr) {
		t.Fatalf("reconfirm error = %v, want %v", got, reconfirmErr)
	}
}

func TestIsAlreadyPaidError(t *testing.T) {
	if !isAlreadyPaidError(errors.New(`chatgpt.checkout HTTP 400: {"detail":"User is already paid"}`)) {
		t.Fatal("expected already paid detection")
	}
	if isAlreadyPaidError(errors.New("chatgpt.checkout HTTP 400: nope")) {
		t.Fatal("unexpected already paid detection")
	}
	result := alreadyPaidResult("US", "USD", "User is already paid")
	if result.Decision != "already_paid" || result.PaymentStatus != "already_paid" {
		t.Fatalf("unexpected already paid result: %#v", result)
	}
}

func TestStripeInitCheckoutUsesNestedStripeID(t *testing.T) {
	checkout := checkoutData{ID: "oaics_outer123", OpenAIID: "oaics_outer123", StripeID: "cs_live_inner456", ProcessorEntity: "openai_llc"}
	stripe := stripeInitCheckout(checkout)
	if stripe.ID != "cs_live_inner456" {
		t.Fatalf("stripe init checkout id = %q, want nested Stripe id", stripe.ID)
	}
	if checkout.ID != "oaics_outer123" {
		t.Fatalf("stripe init helper mutated original checkout id to %q", checkout.ID)
	}
}

func TestCheckoutCompatibilityMetadataNormalizesObservedRoute(t *testing.T) {
	metadata := checkoutCompatibilityMetadata(checkoutData{
		ID: "oaics_outer123", OpenAIID: "oaics_outer123", StripeID: "cs_live_inner456",
		Country: "JP", Currency: "JPY",
	}, nil, "jp", "php", "oaics")
	if metadata["checkoutRoute"] != DirectRouteOAICSDirect {
		t.Fatalf("route = %#v", metadata["checkoutRoute"])
	}
	if metadata["requestedCountry"] != "JP" || metadata["requestedCurrency"] != "PHP" {
		t.Fatalf("requested billing metadata = %#v", metadata)
	}
	if metadata["effectiveCountry"] != "JP" || metadata["effectiveCurrency"] != "JPY" {
		t.Fatalf("effective billing metadata = %#v", metadata)
	}
	if metadata["billingConsistency"] != "country_currency_enforced" {
		t.Fatalf("billing consistency = %#v", metadata["billingConsistency"])
	}
}

func TestCloneJobPublicHidesPrivateCheckoutDiagnostics(t *testing.T) {
	job := &Job{Items: []JobItem{{Result: &Result{Metadata: map[string]any{
		"checkoutRoute": "oaics_preferred", "oaicsCheckoutId": "oaics_private",
		"observedCheckoutTypes": "oaics+stripe", "checkoutFormContext": map[string]any{"customerSessionClientSecret": "cuss_secret_PRIVATE"}, "publicField": "keep",
	}}}}}
	private := cloneJob(job)
	public := cloneJobPublic(job)
	if private.Items[0].Result.Metadata["oaicsCheckoutId"] != "oaics_private" {
		t.Fatal("private clone lost backend checkout diagnostic")
	}
	if _, ok := public.Items[0].Result.Metadata["oaicsCheckoutId"]; ok {
		t.Fatal("public clone exposed OAICS checkout identity")
	}
	if _, ok := public.Items[0].Result.Metadata["checkoutFormContext"]; ok {
		t.Fatal("public clone exposed private checkout form context")
	}
	if public.Items[0].Result.Metadata["publicField"] != "keep" {
		t.Fatal("public clone removed unrelated metadata")
	}
}
