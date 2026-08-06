package extractmethods

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math/rand/v2"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	defaultStripePK      = "pk_live_51HOrSwC6h1nxGoI3lTAgRjYVrz4dU3fVOabyCcKR3pbEJguCVAlqCxdxCUvoRh1XWwRacViovU3kLKvpkjh7IqkW00iXQsjo3n"
	stripeVersion        = "2025-03-31.basil; checkout_server_update_beta=v1; checkout_manual_approval_preview=v1"
	paypalStripeVersion  = "2020-08-27;custom_checkout_beta=v1; checkout_server_update_beta=v1; checkout_manual_approval_preview=v1"
	stripeRuntimeVersion = "c00af4ce81"
	paypalRuntimeVersion = "81274c9437"
	chromeUserAgent      = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
	firefoxUserAgent     = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0"
	defaultUserAgent     = chromeUserAgent
)

var (
	stripeCheckoutIDPattern = regexp.MustCompile(`cs_(?:live|test)_[A-Za-z0-9]+`)
	checkoutIDPattern       = regexp.MustCompile(`(?:cs_(?:live|test)_[A-Za-z0-9]+|oaics_[A-Za-z0-9]+)`)
	stripePKPattern         = regexp.MustCompile(`pk_live_[A-Za-z0-9]+`)
)

var countryCurrency = map[string]string{
	"AE": "AED", "AU": "AUD", "BA": "BAM", "BH": "BHD", "BR": "BRL",
	"CA": "CAD", "CH": "CHF", "DE": "EUR", "FR": "EUR", "GB": "GBP", "ID": "IDR",
	"IN": "INR", "JP": "JPY", "KR": "KRW", "MX": "MXN", "NL": "EUR",
	"PH": "PHP", "PL": "PLN", "TH": "THB", "TR": "USD", "US": "USD", "VN": "VND",
}

type localeProfile struct {
	Browser        string
	Elements       string
	AcceptLanguage string
	Timezone       string
}

var localeProfiles = map[string]localeProfile{
	"AE": {"ar-AE", "en", "ar-AE,ar;q=0.9,en;q=0.8", "Asia/Dubai"},
	"AU": {"en-AU", "en", "en-AU,en;q=0.9", "Australia/Sydney"},
	"BA": {"bs-BA", "en", "bs-BA,bs;q=0.9,en;q=0.8", "Europe/Sarajevo"},
	"BH": {"ar-BH", "en", "ar-BH,ar;q=0.9,en;q=0.8", "Asia/Bahrain"},
	"BR": {"pt-BR", "pt-BR", "pt-BR,pt;q=0.9,en;q=0.8", "America/Sao_Paulo"},
	"CA": {"en-CA", "en", "en-CA,en;q=0.9", "America/Toronto"},
	"CH": {"de-CH", "de", "de-CH,de;q=0.9,en;q=0.8", "Europe/Zurich"},
	"DE": {"de-DE", "de", "de-DE,de;q=0.9,en;q=0.8", "Europe/Berlin"},
	"FR": {"fr-FR", "fr", "fr-FR,fr;q=0.9,en;q=0.8", "Europe/Paris"},
	"GB": {"en-GB", "en", "en-GB,en;q=0.9", "Europe/London"},
	"ID": {"id-ID", "id", "id-ID,id;q=0.9,en;q=0.8", "Asia/Jakarta"},
	"IN": {"hi-IN", "en", "hi-IN,hi;q=0.9,en-US;q=0.8,en;q=0.7", "Asia/Kolkata"},
	"JP": {"ja-JP", "ja", "ja-JP,ja;q=0.9,en;q=0.8", "Asia/Tokyo"},
	"KR": {"ko-KR", "ko", "ko-KR,ko;q=0.9,en-US;q=0.8", "Asia/Seoul"},
	"MX": {"es-MX", "es", "es-MX,es;q=0.9,en;q=0.8", "America/Mexico_City"},
	"NL": {"nl-NL", "nl", "nl-NL,nl;q=0.9,en;q=0.8", "Europe/Amsterdam"},
	"PH": {"en-PH", "en", "en-PH,en;q=0.9", "Asia/Manila"},
	"PL": {"pl-PL", "pl", "pl-PL,pl;q=0.9,en;q=0.8", "Europe/Warsaw"},
	"TH": {"th-TH", "en", "th-TH,th;q=0.9,en;q=0.8", "Asia/Bangkok"},
	"TR": {"tr-TR", "en", "tr-TR,tr;q=0.9,en;q=0.8", "Europe/Istanbul"},
	"US": {"en-US", "en", "en-US,en;q=0.9", "America/New_York"},
	"VN": {"vi-VN", "en", "vi-VN,vi;q=0.9,en;q=0.8", "Asia/Ho_Chi_Minh"},
}

type Endpoints struct {
	ChatGPT string
	Stripe  string
	IDEAL   string
}

func DefaultEndpoints() Endpoints {
	return Endpoints{ChatGPT: "https://chatgpt.com", Stripe: "https://api.stripe.com", IDEAL: "https://pay.ideal.nl"}
}

type Engine struct {
	Config           *RuntimeConfig
	Endpoints        Endpoints
	KakaoHelperPath  string
	PythonExecutable string
	eligibilityProbe func(*flow) error
}

func NewEngine(configPath string) *Engine {
	return &Engine{
		Config: NewRuntimeConfig(configPath), Endpoints: DefaultEndpoints(),
		KakaoHelperPath: defaultKakaoHelperPath(), PythonExecutable: defaultPythonExecutable(),
	}
}

type flow struct {
	ctx         context.Context
	engine      *Engine
	method      string
	credential  Credential
	options     Options
	proxies     StageProxies
	profile     localeProfile
	fingerprint requestFingerprint
	deviceID    string
	sessionID   string
	steps       []Step
	progress    ProgressFunc
	checkout    *BrowserClient
	promotion   *BrowserClient
	provider    *BrowserClient
	approve     *BrowserClient
}

type requestFingerprint struct {
	Name      string
	UserAgent string
	Firefox   bool
}

func resolveRequestFingerprint(value string) requestFingerprint {
	normalized := strings.ToLower(strings.TrimSpace(value))
	if strings.Contains(normalized, "firefox") {
		return requestFingerprint{Name: "firefox147", UserAgent: firefoxUserAgent, Firefox: true}
	}
	if strings.Contains(normalized, "chrome136") || strings.Contains(normalized, "chrome133") {
		// The known-good curl_cffi reference advertises Chrome 147 while using
		// the Chrome 136 TLS profile. Keep that combination available for live
		// protocol comparisons instead of collapsing every Chromium request to
		// the newest profile.
		return requestFingerprint{Name: "chrome133-reference", UserAgent: chromeUserAgent}
	}
	return requestFingerprint{Name: "chrome146", UserAgent: chromeUserAgent}
}

func (e *Engine) Run(ctx context.Context, method string, credential Credential, options Options, progress ProgressFunc) (Result, error) {
	method = NormalizeMethod(method)
	if _, ok := LookupMethod(method); !ok {
		return Result{}, fmt.Errorf("未知提炼渠道: %s", method)
	}
	if method == MethodKakao {
		if err := validateKakaoMode(options.KakaoMode); err != nil {
			return Result{}, err
		}
	}
	options = normalizeOptions(method, options)
	proxies, err := ResolveStageProxies(e.Config, options, method)
	if err != nil {
		return Result{}, err
	}
	profile := profileForCountry(options.Country)
	fingerprint := resolveRequestFingerprint(options.ClientFingerprint)
	f := &flow{
		ctx: ctx, engine: e, method: method, credential: credential, options: options,
		proxies: proxies, profile: profile, fingerprint: fingerprint, deviceID: newUUID(), sessionID: newUUID(), progress: progress,
	}
	f.addStep("fingerprint", "running", fingerprint.Name, 0)
	if method == MethodPayPalBA && options.CountryFallback {
		f.addStep("paypal.country_fallback", "warning", fmt.Sprintf("所选国家 %s 暂未适配；本次按 US / USD / en-US / 美国账单资料执行", options.RequestedCountry), 0)
	}
	poolSummary := fmt.Sprintf("主代理池 %d 条；优惠代理池 %d 条；优惠地区 %s", len(ParseProxyPool(options.Proxy)), len(ParseProxyPool(options.PromotionProxy)), firstNonEmpty(options.PromotionProxyRegion, "—"))
	f.addStep("proxy", "running", fmt.Sprintf("%s；Checkout=%s；Promotion=%s；Provider=%s；Approve=%s", poolSummary, proxies.Labels["checkout"], proxies.Labels["promotion"], proxies.Labels["provider"], proxies.Labels["approve"]), 0)
	eligibilityProbe := e.eligibilityProbe
	if eligibilityProbe == nil {
		eligibilityProbe = func(active *flow) error { return active.probeAccountEligibility() }
	}
	if err := eligibilityProbe(f); err != nil {
		decision := "account_ineligible"
		var upstream *eligibilityUpstreamError
		if errors.As(err, &upstream) {
			if upstream.AuthInvalid {
				decision = "token_invalid"
			} else {
				decision = "upstream_blocked"
			}
		}
		return f.finishResult(Result{Decision: decision}, err)
	}
	if method == MethodKakao {
		result, helperErr, handled := f.runKakaoCurlTransport()
		if handled {
			return f.finishResult(result, helperErr)
		}
		return f.finishResult(result, fmt.Errorf("Kakao %s 的已验证 curl_cffi helper 不可用，已拒绝回退到旧流程: %w", options.KakaoMode, helperErr))
	}
	if err := f.openClients(); err != nil {
		return f.finishResult(Result{}, err)
	}
	defer f.closeClients()

	var result Result
	switch method {
	case MethodDirect:
		result, err = f.runDirect(false)
	case MethodPH:
		result, err = f.runPhilippinesLink()
	case MethodMoMo:
		result, err = f.runMoMo()
	case MethodPayPalBA:
		result, err = f.runPayPal()
	case MethodIDEAL, MethodGoPay, MethodPIX, MethodBLIK, MethodTWINT:
		result, err = f.runRedirectProvider(method)
	case MethodKakao:
		result, err = f.runKakao()
	case MethodUPI:
		result, err = f.runUPI()
	}
	return f.finishResult(result, err)
}

func (f *flow) finishResult(result Result, err error) (Result, error) {
	result.Method = f.method
	// Persist the exact Checkout identity used during extraction so the later
	// payment handoff can continue the same browser/session tuple.  Generating
	// a new device or ChatGPT session at payment time breaks Checkout
	// continuity and is surfaced upstream as OPENAI_CONFIRM_BLOCKED.
	if result.Metadata == nil {
		result.Metadata = map[string]any{}
	}
	result.Metadata["checkout_user_agent"] = f.fingerprint.UserAgent
	result.Metadata["checkout_device_id"] = f.deviceID
	result.Metadata["checkout_chatgpt_session_id"] = f.sessionID
	result.Metadata["checkout_proxy"] = f.proxies.Checkout
	result.Metadata["checkout_identity_version"] = 1
	if f.method == MethodPayPalBA {
		result.Metadata["requestedCountry"] = firstNonEmpty(f.options.RequestedCountry, f.options.Country)
		result.Metadata["effectiveCountry"] = f.options.Country
		result.Metadata["countryFallback"] = f.options.CountryFallback
		if f.options.CountryFallback {
			result.Metadata["countryFallbackDetail"] = "未适配国家按 US / USD / en-US / 美国账单资料执行"
		}
	}
	result.Steps = append([]Step(nil), f.steps...)
	if err != nil {
		if result.ExtractionStatus == "" {
			result.ExtractionStatus = "failed"
		}
		if result.PaymentStatus == "" {
			result.PaymentStatus = "not_started"
		}
		return result, err
	}
	if result.ExtractionStatus == "" {
		result.ExtractionStatus = "link_ready"
	}
	if result.PaymentStatus == "" {
		result.PaymentStatus = "awaiting_payment"
	}
	result.OK = true
	return result, nil
}

func normalizeOptions(method string, options Options) Options {
	method = NormalizeMethod(method)
	if options.ProxyMode == "" {
		options.ProxyMode = "custom"
	}
	if options.TimeoutSeconds == 0 {
		options.TimeoutSeconds = 45
	}
	if options.MaxAttempts == 0 {
		if method == MethodKakao && NormalizeKakaoMode(options.KakaoMode) == KakaoModeProviderLink {
			// The original Kakao runner defaulted to 5 Seeds x 5 retry rounds.
			// Keep that full-chain retry budget when provider-link callers omit it.
			options.MaxAttempts = 10
		} else if method == MethodPH {
			options.MaxAttempts = 10
		} else {
			options.MaxAttempts = 3
		}
	}
	if options.PromoCampaignID == "" {
		options.PromoCampaignID = "plus-1-month-free"
	}
	if options.ClientFingerprint == "" {
		options.ClientFingerprint = "chrome"
	}
	if options.TrialDays == 0 {
		options.TrialDays = 30
	}
	if options.ApproveAttempts == 0 {
		options.ApproveAttempts = 3
	}
	// Preserve the legacy maxAmountMinor request shape, but normalize it into
	// the explicit amount-gate contract used by checkout-link methods.
	amountGate := options.amountGateConfig()
	options.AmountGate = amountGate.Mode
	options.AmountThresholdMinor = int(amountGate.Threshold)
	if amountGate.Mode == AmountGateAtMost {
		options.MaxAmountMinor = int(amountGate.Threshold)
	} else {
		options.MaxAmountMinor = 0
	}
	switch method {
	case MethodPayPalBA:
		requested := normalizeRegion(firstNonEmpty(options.RequestedCountry, options.Country, "US"))
		if requested == "" {
			requested = "US"
		}
		options.RequestedCountry = requested
		if payPalCountryAdapted(requested) {
			options.Country = requested
			options.Currency = currencyForCountry(requested)
			options.CountryFallback = false
		} else {
			options.Country = "US"
			options.Currency = "USD"
			options.CountryFallback = true
		}
	case MethodDirect:
		options.Country = normalizeCountry(firstNonEmpty(options.Country, "PH"), "PH")
	case MethodPH:
		options.Country = "PH"
	case MethodMoMo:
		options.Country = "VN"
	case MethodKakao:
		options.Country = "KR"
		options.Currency = "KRW"
		mode := NormalizeKakaoMode(options.KakaoMode)
		if mode != KakaoModeProviderLink {
			mode = KakaoModeEligibility
		}
		options.KakaoMode = mode
		options.KakaoEligibilityOnly = mode == KakaoModeEligibility
		regionPool := ParseRegionPool(options.PromotionProxyRegion)
		explicitPromotionProxy := mode == KakaoModeProviderLink && len(ParseProxyPool(options.PromotionProxy)) > 0
		if explicitPromotionProxy {
			// Explicit promotion proxy pools are authoritative. Derive display
			// regions from the supplied proxies so a stale UI selector cannot
			// rewrite TR into JP, while still preserving multi-select order.
			derived := make([]string, 0)
			seen := map[string]bool{}
			for _, proxy := range ParseProxyPool(options.PromotionProxy) {
				region := proxyConfiguredRegion(proxy)
				if region == "" || seen[region] {
					continue
				}
				seen[region] = true
				derived = append(derived, region)
			}
			if len(derived) > 0 {
				regionPool = derived
			}
		}
		// Do not invent promotion regions. Empty means the caller left promotion
		// unset; explicit proxy pools remain authoritative via derived regions.
		if len(regionPool) == 1 {
			options.PromotionProxyRegion = regionPool[0]
		} else if len(regionPool) > 1 {
			options.PromotionProxyRegion = strings.Join(regionPool, ",")
		} else if !explicitPromotionProxy {
			options.PromotionProxyRegion = ""
		}
		if options.KakaoEligibilityOnly {
			usePromo := false
			options.UsePromo = &usePromo
			options.PaymentStatusAutoRefresh = false
		}
	case MethodUPI:
		options.Country = "IN"
	case MethodIDEAL:
		options.Country = "NL"
	case MethodGoPay:
		options.Country = "ID"
	case MethodPIX:
		options.Country, options.Currency = "BR", "BRL"
	case MethodBLIK:
		options.Country, options.Currency = "PL", "PLN"
	case MethodTWINT:
		options.Country, options.Currency = "CH", "CHF"
	}
	if options.Currency == "" {
		options.Currency = currencyForCountry(options.Country)
	} else {
		options.Currency = strings.ToUpper(strings.TrimSpace(options.Currency))
	}
	return options
}

func normalizeCountry(value, fallback string) string {
	country := normalizeRegion(value)
	if _, ok := countryCurrency[country]; ok {
		return country
	}
	return fallback
}

func payPalCountryAdapted(country string) bool {
	country = normalizeRegion(country)
	method, ok := LookupMethod(MethodPayPalBA)
	if !ok {
		return false
	}
	for _, adapted := range method.AdaptedCountries {
		if country == adapted {
			return true
		}
	}
	return false
}

func currencyForCountry(country string) string {
	if currency := countryCurrency[normalizeRegion(country)]; currency != "" {
		return currency
	}
	return "USD"
}

func profileForCountry(country string) localeProfile {
	if profile, ok := localeProfiles[normalizeRegion(country)]; ok {
		return profile
	}
	return localeProfiles["US"]
}

func (f *flow) openClients() error {
	var err error
	f.checkout, err = NewBrowserClient(f.proxies.Checkout, f.options.Timeout(), f.options.ClientFingerprint)
	if err != nil {
		return fmt.Errorf("Checkout 客户端: %w", err)
	}
	f.promotion, err = NewBrowserClient(f.proxies.Promotion, f.options.Timeout(), f.options.ClientFingerprint)
	if err != nil {
		return fmt.Errorf("Promotion 客户端: %w", err)
	}
	f.provider, err = NewBrowserClient(f.proxies.Provider, f.options.Timeout(), f.options.ClientFingerprint)
	if err != nil {
		return fmt.Errorf("Provider 客户端: %w", err)
	}
	f.approve, err = NewBrowserClient(f.proxies.Approve, f.options.Timeout(), f.options.ClientFingerprint)
	if err != nil {
		return fmt.Errorf("Approve 客户端: %w", err)
	}
	for _, client := range []*BrowserClient{f.checkout, f.promotion, f.provider, f.approve} {
		client.SetDefaultHeaders(map[string]string{
			"User-Agent":      f.fingerprint.UserAgent,
			"Accept-Language": f.profile.AcceptLanguage,
		})
	}
	return nil
}

func (f *flow) closeClients() {
	for _, client := range []*BrowserClient{f.checkout, f.promotion, f.provider, f.approve} {
		if client != nil {
			client.Close()
		}
	}
}

func (f *flow) addStep(stage, status, detail string, elapsed time.Duration) {
	step := NewStep(stage, status, detail, elapsed)
	f.steps = append(f.steps, step)
	if f.progress != nil {
		f.progress(step)
	}
}

func (f *flow) openAIHeaders(targetPath, referer string) map[string]string {
	headers := map[string]string{
		"Accept":         "*/*",
		"Authorization":  "Bearer " + f.credential.AccessToken,
		"Content-Type":   "application/json",
		"Origin":         f.engine.Endpoints.ChatGPT,
		"Referer":        firstNonEmpty(referer, f.engine.Endpoints.ChatGPT+"/"),
		"oai-device-id":  f.deviceID,
		"oai-session-id": f.sessionID,
		"oai-language":   f.profile.Browser,
		"User-Agent":     f.fingerprint.UserAgent,
		"sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "sec-fetch-site": "same-origin",
		"Cookie": f.openAICookie(),
	}
	if !f.fingerprint.Firefox {
		headers["sec-ch-ua"] = `"Google Chrome";v="147", "Chromium";v="147", "Not_A Brand";v="24"`
		headers["sec-ch-ua-mobile"] = "?0"
		headers["sec-ch-ua-platform"] = `"Windows"`
	}
	if targetPath != "" {
		headers["x-openai-target-path"] = targetPath
		headers["x-openai-target-route"] = targetPath
	}
	return headers
}

func (f *flow) kakaoCheckoutHeaders() map[string]string {
	// Match the small header surface used by the independently verified Kakao
	// reference. In particular, do not attach ChatGPT browser cookies, device
	// IDs, session IDs, Origin, Chromium hints, or x-openai routing headers to
	// checkout creation. An empty Accept-Language explicitly removes the
	// BrowserClient default for this request.
	return map[string]string{
		"Accept-Language": "",
		"Authorization":   "Bearer " + f.credential.AccessToken,
		"Content-Type":    "application/json",
		"oai-language":    f.profile.Browser,
		"User-Agent":      f.fingerprint.UserAgent,
	}
}

func (f *flow) kakaoCheckoutAPIHeaders(referer, targetPath string) map[string]string {
	headers := f.kakaoCheckoutHeaders()
	headers["Referer"] = referer
	if targetPath != "" {
		headers["x-openai-target-path"] = targetPath
		headers["x-openai-target-route"] = targetPath
	}
	return headers
}

func (f *flow) openAICookie() string {
	cookie := "oai-did=" + f.deviceID
	if strings.TrimSpace(f.credential.SessionToken) != "" {
		cookie += "; __Secure-next-auth.session-token=" + strings.TrimSpace(f.credential.SessionToken)
	}
	return cookie
}

type checkoutData struct {
	ID              string
	StripeID        string
	OpenAIID        string
	ProcessorEntity string
	PublishableKey  string
	Country         string
	Currency        string
	PaymentPageURL  string
	UserAgent       string
	Raw             map[string]any
}

func (f *flow) createCheckout(client *BrowserClient, country, currency, uiMode string, includePromo bool, trialDays int) (checkoutData, error) {
	usePricingEntryPoint := f.method != MethodKakao
	return f.createCheckoutVariant(client, country, currency, uiMode, includePromo, trialDays, usePricingEntryPoint)
}

func (f *flow) createCheckoutVariant(client *BrowserClient, country, currency, uiMode string, includePromo bool, trialDays int, usePricingEntryPoint bool) (checkoutData, error) {
	stage := "chatgpt.checkout"
	f.addStep(stage, "running", fmt.Sprintf("创建 %s / %s checkout", country, currency), 0)
	payload := map[string]any{
		"plan_name":        "chatgptplusplan",
		"billing_details":  map[string]string{"country": country, "currency": currency},
		"checkout_ui_mode": firstNonEmpty(uiMode, "custom"),
	}
	if usePricingEntryPoint {
		payload["entry_point"] = "all_plans_pricing_modal"
	} else if f.method == MethodKakao {
		// Match the Kakao checkout contract: the pricing-modal entry point can
		// cause brand-new signup accounts to receive an immediate generic
		// zero-amount checkout that omits regional payment methods.
		payload["cancel_url"] = f.engine.Endpoints.ChatGPT + "/#pricing"
	}
	if includePromo && f.options.PromoEnabled() {
		payload["promo_campaign"] = map[string]any{
			"promo_campaign_id":          f.options.PromoCampaignID,
			"is_coupon_from_query_param": false,
		}
	}
	if trialDays > 0 {
		payload["subscription_data"] = map[string]int{"trial_period_days": trialDays}
	}
	started := time.Now()
	headers := f.openAIHeaders("/backend-api/payments/checkout", f.engine.Endpoints.ChatGPT+"/")
	if f.method == MethodKakao {
		headers = f.kakaoCheckoutHeaders()
	}
	response, err := client.JSON(f.ctx, http.MethodPost, f.engine.Endpoints.ChatGPT+"/backend-api/payments/checkout", headers, payload)
	if err != nil {
		f.addStep(stage, "failed", err.Error(), time.Since(started))
		return checkoutData{}, err
	}
	if response.Status >= 400 {
		err = upstreamError(stage, response)
		// Account already on a paid plan is terminal: never continue attempts.
		if isAlreadyPaidError(err) {
			f.addStep(stage, "failed", "账号已是付费状态，停止后续流程："+err.Error(), time.Since(started))
			return checkoutData{}, fmt.Errorf("already_paid: %w", err)
		}
		f.addStep(stage, "failed", err.Error(), time.Since(started))
		return checkoutData{}, err
	}
	var raw map[string]any
	if response.JSON(&raw) != nil {
		return checkoutData{}, errors.New("checkout 返回的不是 JSON")
	}
	stripeID := findStripeCheckoutID(raw)
	openAIID := findOpenAICheckoutID(raw)
	id := firstNonEmpty(stripeID, openAIID)
	if f.method == MethodPH {
		// PH short links are the OpenAI custom-checkout handoff. A nested Stripe
		// payment-page id may coexist in the same response, but it is not the
		// final chatgpt.com/checkout identifier.
		id = firstNonEmpty(openAIID, stripeID)
	}
	if id == "" {
		return checkoutData{}, fmt.Errorf("checkout 响应缺少 cs/oaics id；keys=%s", strings.Join(mapKeys(raw), ","))
	}
	processor := findStringDeep(raw, "processor_entity", "processorEntity")
	if processor == "" {
		if country == "US" || f.method == MethodKakao {
			processor = "openai_llc"
		} else {
			processor = "openai_ie"
		}
	}
	pk := findStringDeep(raw, "publishable_key", "stripe_publishable_key", "publishableKey", "stripePublishableKey")
	if match := stripePKPattern.FindString(pk); match != "" {
		pk = match
	}
	if pk == "" {
		pk = firstNonEmpty(f.options.StripePublishableKey, defaultStripePK)
	}
	f.addStep(stage, "success", fmt.Sprintf("checkout=%s；type=%s；processor=%s；observed=%s", id, checkoutIDType(id), processor, checkoutObservation(stripeID, openAIID)), time.Since(started))
	return checkoutData{ID: id, StripeID: stripeID, OpenAIID: openAIID, ProcessorEntity: processor, PublishableKey: pk, Country: country, Currency: currency, UserAgent: f.fingerprint.UserAgent, Raw: raw}, nil
}

func (f *flow) updatePromotion(client *BrowserClient, checkout checkoutData) (map[string]any, error) {
	stage := "chatgpt.checkout_update"
	if !f.options.PromoEnabled() {
		f.addStep(stage, "skipped", "已关闭优惠更新", 0)
		return nil, nil
	}
	payload := map[string]any{
		"checkout_session_id": checkout.ID,
		"processor_entity":    checkout.ProcessorEntity,
		"plan_name":           "chatgptplusplan",
		"price_interval":      "month",
		"seat_quantity":       1,
		"promo_campaign": map[string]any{
			"promo_campaign_id":          f.options.PromoCampaignID,
			"is_coupon_from_query_param": false,
		},
	}
	referer := fmt.Sprintf("%s/checkout/%s/%s", f.engine.Endpoints.ChatGPT, checkout.ProcessorEntity, checkout.ID)
	started := time.Now()
	headers := f.openAIHeaders("/backend-api/payments/checkout/update", referer)
	if f.method == MethodKakao {
		headers = f.kakaoCheckoutAPIHeaders(referer, "/backend-api/payments/checkout/update")
	}
	response, err := client.JSON(f.ctx, http.MethodPost, f.engine.Endpoints.ChatGPT+"/backend-api/payments/checkout/update", headers, payload)
	if err != nil {
		f.addStep(stage, "failed", err.Error(), time.Since(started))
		return nil, err
	}
	if response.Status >= 400 {
		err = upstreamError(stage, response)
		f.addStep(stage, "failed", err.Error(), time.Since(started))
		return nil, err
	}
	var raw map[string]any
	if len(response.Body) > 0 && response.JSON(&raw) != nil {
		return nil, errors.New("checkout/update 返回的不是 JSON")
	}
	if success, ok := raw["success"].(bool); ok && !success {
		return raw, errors.New("checkout/update 返回 success=false")
	}
	f.addStep(stage, "success", "优惠参数已更新", time.Since(started))
	return raw, nil
}

type stripeInit struct {
	Raw               map[string]any
	HostedURL         string
	Amount            string
	Currency          string
	Methods           []string
	ConfigID          string
	InitChecksum      string
	ElementsSessionID string
	StripeJSID        string
}

func (f *flow) stripeInitForm(checkout checkoutData, elements bool) (url.Values, string) {
	form := url.Values{
		"key":              {checkout.PublishableKey},
		"browser_locale":   {f.profile.Browser},
		"browser_timezone": {f.profile.Timezone},
	}
	stripeJSID := ""
	if elements {
		stripeJSID = newUUID()
		form.Set("_stripe_version", stripeVersion)
		form.Set("elements_session_client[client_betas][0]", "custom_checkout_server_updates_1")
		form.Set("elements_session_client[client_betas][1]", "custom_checkout_manual_approval_1")
		form.Set("elements_session_client[elements_init_source]", "custom_checkout")
		form.Set("elements_session_client[referrer_host]", "chatgpt.com")
		form.Set("elements_session_client[stripe_js_id]", stripeJSID)
		form.Set("elements_session_client[locale]", f.profile.Elements)
		form.Set("elements_session_client[is_aggregation_expected]", "false")
		form.Set("elements_options_client[saved_payment_method][enable_save]", "auto")
		form.Set("elements_options_client[saved_payment_method][enable_redisplay]", "auto")
		if f.method == MethodKakao {
			form.Set("eid", "NA")
			form.Set("redirect_type", "url")
		}
	} else {
		form.Set("eid", "NA")
		form.Set("redirect_type", "url")
	}
	return form, stripeJSID
}

func (f *flow) stripeInit(client *BrowserClient, checkout checkoutData, elements bool) (stripeInit, error) {
	stage := "stripe.init"
	form, stripeJSID := f.stripeInitForm(checkout, elements)
	started := time.Now()
	response, err := client.Form(f.ctx, http.MethodPost, f.engine.Endpoints.Stripe+"/v1/payment_pages/"+url.PathEscape(checkout.ID)+"/init", stripeHeaders(checkout, f.profile), form)
	if err != nil {
		f.addStep(stage, "failed", err.Error(), time.Since(started))
		return stripeInit{}, err
	}
	if response.Status >= 400 {
		err = upstreamError(stage, response)
		f.addStep(stage, "failed", err.Error(), time.Since(started))
		return stripeInit{}, err
	}
	var raw map[string]any
	if response.JSON(&raw) != nil {
		return stripeInit{}, errors.New("Stripe init 返回的不是 JSON")
	}
	init := stripeInit{
		Raw:               raw,
		HostedURL:         findStringDeep(raw, "stripe_hosted_url", "hosted_url", "url"),
		Amount:            expectedAmount(raw),
		Currency:          strings.ToUpper(firstNonEmpty(findStringDeep(raw, "currency"), checkout.Currency)),
		Methods:           paymentMethodTypes(raw),
		ConfigID:          findStringDeep(raw, "config_id"),
		InitChecksum:      findStringDeep(raw, "init_checksum"),
		ElementsSessionID: findStringDeep(raw, "elements_session_id", "session_id"),
		StripeJSID:        stripeJSID,
	}
	f.addStep(stage, "success", fmt.Sprintf("amount=%s %s；methods=%s", init.Amount, init.Currency, strings.Join(init.Methods, ",")), time.Since(started))
	return init, nil
}

func (f *flow) activateStripeCheckout(client *BrowserClient, checkout checkoutData) (checkoutData, error) {
	stage := "stripe.activate"
	started := time.Now()
	pageURL := "https://checkout.stripe.com/c/pay/" + url.PathEscape(checkout.ID)
	for _, target := range []string{
		"https://pay.openai.com/c/pay/" + url.PathEscape(checkout.ID),
		pageURL,
	} {
		response, err := client.Do(f.ctx, http.MethodGet, target, map[string]string{
			"Accept":          "text/html,*/*",
			"Accept-Language": f.profile.AcceptLanguage,
			"Referer":         f.engine.Endpoints.ChatGPT + "/",
			"User-Agent":      f.fingerprint.UserAgent,
		}, nil, true)
		if err != nil {
			f.addStep(stage, "failed", err.Error(), time.Since(started))
			return checkoutData{}, err
		}
		if response.Status >= 400 {
			err = upstreamError(stage, response)
			f.addStep(stage, "failed", err.Error(), time.Since(started))
			return checkoutData{}, err
		}
	}
	checkout.PaymentPageURL = pageURL
	f.addStep(stage, "success", "同一 Checkout 代理已加载 pay.openai 与 checkout.stripe 支付页", time.Since(started))
	return checkout, nil
}

func stripeHeaders(checkout checkoutData, profile localeProfile) map[string]string {
	referer := firstNonEmpty(checkout.PaymentPageURL, "https://pay.openai.com/c/pay/"+checkout.ID)
	origin := "https://pay.openai.com"
	if parsed, err := url.Parse(referer); err == nil && parsed.Scheme != "" && parsed.Host != "" {
		origin = parsed.Scheme + "://" + parsed.Host
	}
	secFetchSite := "cross-site"
	if strings.HasSuffix(strings.ToLower(origin), ".stripe.com") {
		secFetchSite = "same-site"
	}
	return map[string]string{
		"Accept":          "application/json",
		"Authorization":   "Bearer " + checkout.PublishableKey,
		"Origin":          origin,
		"Referer":         referer,
		"Accept-Language": profile.AcceptLanguage,
		"User-Agent":      firstNonEmpty(checkout.UserAgent, defaultUserAgent),
		"Sec-Fetch-Site":  secFetchSite,
		"Sec-Fetch-Mode":  "cors",
		"Sec-Fetch-Dest":  "empty",
	}
}

func (f *flow) approveCheckout(checkout checkoutData, attempts int) error {
	return f.approveCheckoutWith(f.approve, checkout, attempts, true)
}

func (f *flow) approveCheckoutWith(client *BrowserClient, checkout checkoutData, attempts int, sendSentinel bool) error {
	if attempts < 1 {
		attempts = 1
	}
	stage := "chatgpt.approve"
	referer := fmt.Sprintf("%s/checkout/%s/%s", f.engine.Endpoints.ChatGPT, checkout.ProcessorEntity, checkout.ID)
	payload := map[string]any{"checkout_session_id": checkout.ID, "processor_entity": checkout.ProcessorEntity}
	var last error
	for attempt := 1; attempt <= attempts; attempt++ {
		active := client
		// Kakao-like approve retries: optionally rebuild approve jar/fingerprint.
		if f.method == MethodPayPalBA || f.method == MethodKakao {
			policy := f.options.StageFingerprintPolicy()["approve"]
			if policy == "fresh" || attempt > 1 {
				fps := []string{"chrome146", "chrome136", "chrome"}
				fp := fps[(attempt-1)%len(fps)]
				if rebuilt, err := NewBrowserClient(f.proxies.Approve, f.options.Timeout(), fp); err == nil {
					if f.approve != nil && f.approve != client {
						// keep original caller client untouched; close only previous rebuilt ones carefully later
					}
					active = rebuilt
					f.fingerprint = resolveRequestFingerprint(fp)
					f.addStep(stage, "running", fmt.Sprintf("approve %d/%d；fingerprint=%s", attempt, attempts, f.fingerprint.Name), 0)
				}
			}
		}
		if sendSentinel {
			_, _ = active.JSON(f.ctx, http.MethodPost, f.engine.Endpoints.ChatGPT+"/backend-api/sentinel/ping", f.openAIHeaders("/backend-api/sentinel/ping", f.engine.Endpoints.ChatGPT+"/"), map[string]any{})
		}
		started := time.Now()
		headers := f.openAIHeaders("/backend-api/payments/checkout/approve", referer)
		if f.method == MethodKakao && !sendSentinel {
			headers = f.kakaoCheckoutAPIHeaders(referer, "")
		}
		response, err := active.JSON(f.ctx, http.MethodPost, f.engine.Endpoints.ChatGPT+"/backend-api/payments/checkout/approve", headers, payload)
		if err == nil && response.Status < 400 {
			var body map[string]any
			_ = response.JSON(&body)
			result := strings.ToLower(findStringDeep(body, "result", "status"))
			if result == "approved" {
				f.addStep(stage, "success", fmt.Sprintf("第 %d 次 approve 成功", attempt), time.Since(started))
				return nil
			}
			last = fmt.Errorf("approve result=%s", result)
		} else if err != nil {
			last = err
		} else {
			last = upstreamError(stage, response)
		}
		f.addStep(stage, "retrying", fmt.Sprintf("第 %d/%d 次失败：%v", attempt, attempts, last), time.Since(started))
		if attempt < attempts {
			select {
			case <-f.ctx.Done():
				return f.ctx.Err()
			case <-time.After(time.Duration(350+attempt*150) * time.Millisecond):
			}
		}
	}
	return last
}

func (f *flow) runDirect(forcePH bool) (Result, error) {
	country := f.options.Country
	currency := f.options.Currency
	if forcePH {
		country, currency = "PH", "PHP"
	}
	checkout, err := f.createCheckout(f.checkout, country, currency, "custom", false, 0)
	if err != nil {
		return Result{Country: country, Currency: currency}, err
	}
	update, err := f.updatePromotion(f.promotion, checkout)
	if err != nil {
		return Result{Country: country, Currency: currency, CheckoutID: checkout.ID, ProcessorEntity: checkout.ProcessorEntity}, err
	}
	amount := expectedAmount(update)
	if amount == "" {
		init, initErr := f.stripeInit(f.provider, checkout, true)
		if initErr != nil {
			return Result{Country: country, Currency: currency, CheckoutID: checkout.ID, ProcessorEntity: checkout.ProcessorEntity}, initErr
		}
		amount = init.Amount
	}
	gate := f.options.amountGateConfig()
	status := amountGateStatus(amount, currency, gate)
	if err := requireAmountGate("直卡 checkout", amount, currency, gate); err != nil {
		return Result{Country: country, Currency: currency, CheckoutID: checkout.ID, ProcessorEntity: checkout.ProcessorEntity, Amount: amount, AmountStatus: status}, err
	}
	link := fmt.Sprintf("%s/checkout/%s/%s", f.engine.Endpoints.ChatGPT, checkout.ProcessorEntity, checkout.ID)
	f.addStep("direct.link", "success", link, 0)
	return Result{
		Country: country, Currency: currency, CheckoutID: checkout.ID, ProcessorEntity: checkout.ProcessorEntity,
		LongURL: link, Amount: amount, AmountDisplay: displayAmount(amount, currency), AmountStatus: status,
		ExtractionStatus: "link_ready", PaymentStatus: "awaiting_card_payment",
	}, nil
}

// runPhilippinesLink only returns a PH/PHP link after its configured amount
// gate is satisfied. It never confirms a payment method or attempts a charge.
func (f *flow) runPhilippinesLink() (Result, error) {
	const country, currency = "PH", "PHP"
	gate := f.options.amountGateConfig()
	var last error
	var partial Result
	for attempt := 1; attempt <= f.options.Attempts(); attempt++ {
		selected := SelectAttemptOptions(f.options, attempt)
		fingerprints := []string{"chrome146", "chrome136", "chrome"}
		selected.ClientFingerprint = fingerprints[(attempt-1)%len(fingerprints)]
		stage, stageErr := ResolveStageProxies(f.engine.Config, selected, MethodPH)
		if stageErr != nil {
			last = stageErr
			f.addStep("ph.attempt", "retrying", fmt.Sprintf("第 %d/%d 次代理准备失败：%v", attempt, f.options.Attempts(), stageErr), 0)
			continue
		}
		if err := f.rebuildClients(selected, stage); err != nil {
			last = err
			f.addStep("ph.attempt", "retrying", fmt.Sprintf("第 %d/%d 次客户端准备失败：%v", attempt, f.options.Attempts(), err), 0)
			continue
		}
		f.addStep("ph.attempt", "running", fmt.Sprintf("第 %d/%d 次金额门禁提链（%s）；Checkout=%s；fingerprint=%s", attempt, f.options.Attempts(), amountGateLabel(gate, currency), stage.Labels["checkout"], f.fingerprint.Name), 0)

		checkout, err := f.createCheckout(f.checkout, country, currency, "custom", false, 0)
		if err != nil {
			last = err
			if isAlreadyPaidError(err) {
				return alreadyPaidResult(country, currency, err.Error()), err
			}
			f.addStep("ph.attempt", "retrying", fmt.Sprintf("第 %d/%d 次创建 checkout 失败：%v", attempt, f.options.Attempts(), err), 0)
			continue
		}
		partial = Result{Country: country, Currency: currency, CheckoutID: checkout.ID, CheckoutType: checkoutIDType(checkout.ID), ProcessorEntity: checkout.ProcessorEntity, ExtractionStatus: "failed", PaymentStatus: "not_started", Metadata: checkoutObservationMetadata(checkout, amountGateMetadata(gate, "none"))}
		if err := f.promotion.CopyCookiesFrom(f.checkout, f.engine.Endpoints.ChatGPT); err != nil {
			last = fmt.Errorf("同步 Checkout Cookie 到 Promotion: %w", err)
			f.addStep("ph.attempt", "retrying", fmt.Sprintf("第 %d/%d 次会话同步失败：%v", attempt, f.options.Attempts(), last), 0)
			continue
		}
		update, err := f.updatePromotion(f.promotion, checkout)
		if err != nil {
			last = err
			f.addStep("ph.attempt", "retrying", fmt.Sprintf("第 %d/%d 次优惠更新失败：%v", attempt, f.options.Attempts(), err), 0)
			continue
		}
		if updatedOpenAIID := findOpenAICheckoutID(update); updatedOpenAIID != "" {
			checkout.OpenAIID = updatedOpenAIID
			checkout.ID = updatedOpenAIID
		}
		if updatedStripeID := findStripeCheckoutID(update); updatedStripeID != "" {
			checkout.StripeID = updatedStripeID
		}
		if !strings.HasPrefix(strings.ToLower(strings.TrimSpace(checkout.ID)), "oaics_") {
			last = fmt.Errorf("提链类型校验失败：返回 %s，期望 oaics_", trimDetail(checkout.ID, 48))
			f.addStep("ph.checkout_type", "retrying", fmt.Sprintf("第 %d/%d 次：%v；该 cs_live_ 仅作为 Stripe 内部支付页，不作为 PH 最终链", attempt, f.options.Attempts(), last), 0)
			continue
		}
		partial.CheckoutID = checkout.ID
		partial.CheckoutType = checkoutIDType(checkout.ID)
		partial.Metadata = checkoutObservationMetadata(checkout, partial.Metadata)

		amount := expectedAmount(update)
		if amount == "" && checkout.StripeID != "" {
			_ = f.provider.CopyCookiesFrom(f.checkout, f.engine.Endpoints.ChatGPT)
			_ = f.provider.CopyCookiesFrom(f.promotion, f.engine.Endpoints.ChatGPT)
			stripeCheckout := checkout
			stripeCheckout.ID = checkout.StripeID
			amount = f.probePhilippinesAmount(stripeCheckout)
		}
		partial.Amount = amount
		partial.AmountDisplay = checkoutAmountDisplay(amount, currency)
		partial.AmountStatus = amountGateStatus(amount, currency, gate)
		if err := requireAmountGate("菲律宾 PHP checkout", amount, currency, gate); err != nil {
			last = err
			f.addStep("ph.amount_gate", "retrying", fmt.Sprintf("第 %d/%d 次未满足金额门禁（%s）：%v；继续轮换", attempt, f.options.Attempts(), amountGateLabel(gate, currency), err), 0)
			continue
		}
		result := philippinesLinkResult(f.engine.Endpoints.ChatGPT, checkout, amount, gate)
		f.addStep("ph.link", "success", fmt.Sprintf("第 %d 次满足金额门禁（%s）；官方 PH / PHP 链已生成；未确认或发起付款", attempt, amountGateLabel(gate, currency)), 0)
		return result, nil
	}
	if last == nil {
		last = fmt.Errorf("菲律宾 PHP 在重试上限内未获得满足金额门禁（%s）的 checkout", amountGateLabel(gate, currency))
	}
	f.addStep("ph.attempt", "failed", fmt.Sprintf("已完成 %d 次尝试，未满足金额门禁（%s）：%v", f.options.Attempts(), amountGateLabel(gate, currency), last), 0)
	return partial, last
}

func (f *flow) rebuildClients(options Options, proxies StageProxies) error {
	type preparedClient struct {
		target **BrowserClient
		proxy  string
	}
	prepared := []preparedClient{
		{target: &f.checkout, proxy: proxies.Checkout},
		{target: &f.promotion, proxy: proxies.Promotion},
		{target: &f.provider, proxy: proxies.Provider},
		{target: &f.approve, proxy: proxies.Approve},
	}
	created := make([]*BrowserClient, 0, len(prepared))
	for _, item := range prepared {
		client, err := NewBrowserClient(item.proxy, options.Timeout(), options.ClientFingerprint)
		if err != nil {
			for _, pending := range created {
				pending.Close()
			}
			return err
		}
		client.SetDefaultHeaders(map[string]string{
			"User-Agent":      resolveRequestFingerprint(options.ClientFingerprint).UserAgent,
			"Accept-Language": f.profile.AcceptLanguage,
		})
		created = append(created, client)
	}
	for index, item := range prepared {
		if *item.target != nil {
			(*item.target).Close()
		}
		*item.target = created[index]
	}
	f.proxies = proxies
	f.fingerprint = resolveRequestFingerprint(options.ClientFingerprint)
	return nil
}

func (f *flow) probePhilippinesAmount(checkout checkoutData) string {
	const stage = "ph.amount_probe"
	started := time.Now()
	var lastDetail string
	for attempt := 1; attempt <= 3; attempt++ {
		form, _ := f.stripeInitForm(checkout, true)
		response, err := f.provider.Form(f.ctx, http.MethodPost, f.engine.Endpoints.Stripe+"/v1/payment_pages/"+url.PathEscape(checkout.ID)+"/init", stripeHeaders(checkout, f.profile), form)
		if err != nil {
			lastDetail = err.Error()
		} else if response.Status >= 400 {
			lastDetail = fmt.Sprintf("HTTP %d", response.Status)
		} else {
			var raw map[string]any
			if response.JSON(&raw) != nil {
				lastDetail = "未返回 JSON"
			} else if amount := expectedAmount(raw); amount != "" {
				f.addStep(stage, "success", fmt.Sprintf("第 %d 次只读探测金额=%s", attempt, checkoutAmountDisplay(amount, "PHP")), time.Since(started))
				return amount
			} else {
				lastDetail = "未找到金额"
			}
		}
		if attempt < 3 {
			select {
			case <-f.ctx.Done():
				lastDetail = f.ctx.Err().Error()
				attempt = 3
			case <-time.After(time.Duration(attempt) * 500 * time.Millisecond):
			}
		}
	}
	f.addStep(stage, "warning", "3 次只读金额探测失败；金额门禁将按未知金额策略处理："+lastDetail, time.Since(started))
	return ""
}

func philippinesLinkResult(chatGPTURL string, checkout checkoutData, amount string, gate amountGate) Result {
	const country, currency = "PH", "PHP"
	return Result{
		Country: country, Currency: currency, CheckoutID: checkout.ID, CheckoutType: checkoutIDType(checkout.ID), ProcessorEntity: checkout.ProcessorEntity,
		LongURL: fmt.Sprintf("%s/checkout/%s/%s", chatGPTURL, checkout.ProcessorEntity, checkout.ID),
		Amount:  amount, AmountDisplay: checkoutAmountDisplay(amount, currency), AmountStatus: amountGateStatus(amount, currency, gate),
		ExtractionStatus: "link_ready", PaymentStatus: "awaiting_user_payment",
		Metadata: checkoutObservationMetadata(checkout, amountGateMetadata(gate, "none")),
	}
}

func (f *flow) runMoMo() (Result, error) {
	// MoMo eligibility must be observed from a plain checkout. Do not add a
	// promotion or trial-days override to manufacture a payment method.
	checkout, err := f.createCheckout(f.checkout, "VN", "VND", "custom", false, 0)
	if err != nil {
		decision := "checkout_failed"
		if strings.Contains(strings.ToLower(err.Error()), "already paid") {
			decision = "already_paid"
		}
		return Result{Country: "VN", Currency: "VND", Decision: decision, ExtractionStatus: "failed", PaymentStatus: "not_available"}, err
	}
	init, err := f.stripeInit(f.provider, checkout, true)
	if err != nil {
		return Result{Country: "VN", Currency: "VND", CheckoutID: checkout.ID, ProcessorEntity: checkout.ProcessorEntity}, err
	}
	if err := requireMoMoMethod(init.Methods); err != nil {
		f.addStep("momo.check", "failed", err.Error(), 0)
		return Result{
			Country: "VN", Currency: "VND", CheckoutID: checkout.ID, ProcessorEntity: checkout.ProcessorEntity,
			AvailableMethods: init.Methods, Decision: "momo_not_enabled", ExtractionStatus: "failed", PaymentStatus: "not_available",
		}, err
	}
	if err := requireZeroAmount("MoMo checkout", init.Amount); err != nil {
		f.addStep("momo.amount", "failed", err.Error(), 0)
		return Result{
			Country: "VN", Currency: "VND", CheckoutID: checkout.ID, ProcessorEntity: checkout.ProcessorEntity,
			Amount: init.Amount, AmountStatus: amountStatus(init.Amount, 0), AvailableMethods: init.Methods,
			Decision: "nonzero_checkout", ExtractionStatus: "failed", PaymentStatus: "not_available",
		}, err
	}
	f.addStep("momo.check", "success", "checkout 已返回 MoMo", 0)
	oneClick, _ := boolDeep(checkout.Raw, "one_click_trial_eligible")
	actualTrial := hasTrialMarker(checkout.Raw) || hasTrialMarker(init.Raw)
	mode := findStringDeep(init.Raw, "mode")
	hasMoMo := containsFold(init.Methods, "momo")
	decision := "ready"
	switch {
	case !actualTrial && !oneClick:
		decision = "account_trial_ineligible"
	case !actualTrial:
		decision = "trial_not_applied"
	case mode != "" && !strings.EqualFold(mode, "subscription"):
		decision = "unexpected_mode"
	}
	status := "eligible"
	if decision != "ready" {
		status = "ineligible"
	}
	link := fmt.Sprintf("%s/checkout/%s/%s", f.engine.Endpoints.ChatGPT, checkout.ProcessorEntity, checkout.ID)
	return Result{
		Country: "VN", Currency: "VND", CheckoutID: checkout.ID, ProcessorEntity: checkout.ProcessorEntity,
		LongURL: link, Amount: init.Amount, AmountDisplay: displayAmount(init.Amount, "VND"),
		AvailableMethods: init.Methods, Decision: decision, ExtractionStatus: "probe_complete", PaymentStatus: status,
		Metadata: map[string]any{"oneClickTrialEligible": oneClick, "actualTrial": actualTrial, "stripeMode": mode, "hasMoMo": hasMoMo},
	}, nil
}

func requireMoMoMethod(methods []string) error {
	if len(methods) == 0 {
		return errors.New("checkout 未返回支付方式，无法确认 MoMo")
	}
	if !containsFold(methods, "momo") {
		return fmt.Errorf("checkout 不支持 MoMo；methods=%s", strings.Join(methods, ","))
	}
	return nil
}

func isAlreadyPaidError(err error) bool {
	if err == nil {
		return false
	}
	lower := strings.ToLower(err.Error())
	return strings.Contains(lower, "already paid") || strings.Contains(lower, "user is already paid") || strings.Contains(lower, `"detail":"user is already paid"`)
}

func alreadyPaidResult(country, currency, detail string) Result {
	return Result{
		Country: country, Currency: currency,
		Decision:         "already_paid",
		ExtractionStatus: "failed",
		PaymentStatus:    "already_paid",
		Metadata:         map[string]any{"stopReason": "already_paid", "detail": detail},
	}
}

func upstreamError(stage string, response HTTPResponse) error {
	preview := response.Preview(500)
	if preview == "" {
		preview = http.StatusText(response.Status)
	}
	return fmt.Errorf("%s HTTP %d: %s", stage, response.Status, preview)
}

func findCheckoutID(value any) string {
	// Newer checkout responses can expose an outer oaics_* identifier while
	// retaining the actual Stripe cs_* payment-page id deeper in the payload.
	// Stripe /v1/payment_pages endpoints only accept the latter.
	encoded, _ := json.Marshal(value)
	if stripeID := stripeCheckoutIDPattern.FindString(string(encoded)); stripeID != "" {
		return stripeID
	}
	if direct := findStringDeep(value, "checkout_session_id", "session_id", "id", "checkout_id"); checkoutIDPattern.MatchString(direct) {
		return checkoutIDPattern.FindString(direct)
	}
	return checkoutIDPattern.FindString(string(encoded))
}

func findStripeCheckoutID(value any) string {
	encoded, _ := json.Marshal(value)
	return stripeCheckoutIDPattern.FindString(string(encoded))
}

func findOpenAICheckoutID(value any) string {
	encoded, _ := json.Marshal(value)
	return regexp.MustCompile(`oaics_[A-Za-z0-9]+`).FindString(string(encoded))
}

func checkoutIDType(id string) string {
	normalized := strings.ToLower(strings.TrimSpace(id))
	switch {
	case strings.HasPrefix(normalized, "oaics_"):
		return "oaics"
	case strings.HasPrefix(normalized, "cs_live_"):
		return "stripe_live"
	case strings.HasPrefix(normalized, "cs_test_"):
		return "stripe_test"
	default:
		return "unknown"
	}
}

func checkoutObservation(stripeID, openAIID string) string {
	observed := make([]string, 0, 2)
	if openAIID != "" {
		observed = append(observed, "oaics")
	}
	if stripeID != "" {
		observed = append(observed, "stripe")
	}
	if len(observed) == 0 {
		return "unknown"
	}
	return strings.Join(observed, "+")
}

func checkoutObservationMetadata(checkout checkoutData, metadata map[string]any) map[string]any {
	if metadata == nil {
		metadata = map[string]any{}
	}
	metadata["checkoutType"] = checkoutIDType(checkout.ID)
	metadata["processorEntity"] = checkout.ProcessorEntity
	metadata["observedCheckoutTypes"] = checkoutObservation(checkout.StripeID, checkout.OpenAIID)
	metadata["hasStripeCheckout"] = checkout.StripeID != ""
	metadata["hasOpenAICheckout"] = checkout.OpenAIID != ""
	return metadata
}

func findStringDeep(value any, keys ...string) string {
	wanted := make(map[string]bool, len(keys))
	for _, key := range keys {
		wanted[strings.ToLower(key)] = true
	}
	var walk func(any) string
	walk = func(node any) string {
		switch item := node.(type) {
		case map[string]any:
			for key, value := range item {
				if wanted[strings.ToLower(key)] {
					if candidate := stringValue(value); candidate != "" && candidate != "<nil>" {
						return candidate
					}
				}
			}
			for _, value := range item {
				if candidate := walk(value); candidate != "" {
					return candidate
				}
			}
		case []any:
			for _, value := range item {
				if candidate := walk(value); candidate != "" {
					return candidate
				}
			}
		}
		return ""
	}
	return walk(value)
}

func mapKeys(value map[string]any) []string {
	keys := make([]string, 0, len(value))
	for key := range value {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func expectedAmount(value any) string {
	if value == nil {
		return ""
	}
	if mapValue, ok := value.(map[string]any); ok {
		if options, ok := mapValue["elements_options"].(map[string]any); ok {
			if amount := stringValue(options["amount"]); amount != "" {
				return amount
			}
		}
		if summary, ok := mapValue["total_summary"].(map[string]any); ok {
			if due := stringValue(summary["due"]); due != "" {
				return due
			}
		}
		if invoice, ok := mapValue["invoice"].(map[string]any); ok {
			for _, key := range []string{"amount_due", "total"} {
				if amount := stringValue(invoice[key]); amount != "" {
					return amount
				}
			}
		}
		for _, key := range []string{"amount_total", "amount", "due"} {
			if amount := stringValue(mapValue[key]); amount != "" {
				return amount
			}
		}
		for _, child := range mapValue {
			if amount := expectedAmount(child); amount != "" {
				return amount
			}
		}
	}
	if array, ok := value.([]any); ok {
		for _, child := range array {
			if amount := expectedAmount(child); amount != "" {
				return amount
			}
		}
	}
	return ""
}

func currencyMinorExponent(currency string) int {
	switch strings.ToUpper(strings.TrimSpace(currency)) {
	case "JPY", "KRW", "IDR", "VND":
		return 0
	case "BHD", "JOD", "KWD", "OMR", "TND":
		return 3
	default:
		return 2
	}
}

func minorScale(exponent int) int64 {
	scale := int64(1)
	for index := 0; index < exponent; index++ {
		scale *= 10
	}
	return scale
}

// parseCheckoutAmountMinor accepts both Stripe's raw minor-unit values and
// localized values returned by checkout/update. A decorated value such as
// "₱982.14" is major currency units; a bare integer such as "98214" is
// Stripe's minor-unit value.
func parseCheckoutAmountMinor(amount, currency string) (int64, error) {
	text := strings.TrimSpace(amount)
	if text == "" {
		return 0, errors.New("empty amount")
	}
	upper := strings.ToUpper(text)
	decorated := false
	tokens := []string{"R$", strings.ToUpper(strings.TrimSpace(currency)), "PHP", "USD", "EUR", "GBP", "JPY", "KRW", "INR", "VND", "IDR", "BRL", "CAD", "AUD", "THB", "TRY", "AED", "BHD", "MXN", "₱", "$", "€", "£", "¥", "₹", "₩", "₫"}
	for _, token := range tokens {
		if token == "" || !strings.Contains(upper, token) {
			continue
		}
		decorated = true
		upper = strings.ReplaceAll(upper, token, "")
	}
	upper = strings.ReplaceAll(upper, ",", "")
	upper = strings.ReplaceAll(upper, " ", "")
	if upper == "" || strings.Count(upper, ".") > 1 || strings.ContainsAny(upper, "-") {
		return 0, errors.New("invalid amount")
	}
	upper = strings.TrimPrefix(upper, "+")
	if upper == "" {
		return 0, errors.New("invalid amount")
	}
	for _, character := range upper {
		if (character < '0' || character > '9') && character != '.' {
			return 0, errors.New("invalid amount")
		}
	}
	if !strings.Contains(upper, ".") && !decorated {
		value, err := strconv.ParseInt(upper, 10, 64)
		if err != nil || value < 0 {
			return 0, errors.New("invalid amount")
		}
		return value, nil
	}

	parts := strings.SplitN(upper, ".", 2)
	wholeText := parts[0]
	if wholeText == "" {
		wholeText = "0"
	}
	whole, err := strconv.ParseInt(wholeText, 10, 64)
	if err != nil || whole < 0 {
		return 0, errors.New("invalid amount")
	}
	exponent := currencyMinorExponent(currency)
	fraction := ""
	if len(parts) == 2 {
		fraction = parts[1]
	}
	if len(fraction) > exponent {
		for _, character := range fraction[exponent:] {
			if character != '0' {
				return 0, errors.New("amount has too many decimal places")
			}
		}
		fraction = fraction[:exponent]
	}
	for len(fraction) < exponent {
		fraction += "0"
	}
	fractionValue := int64(0)
	if fraction != "" {
		fractionValue, err = strconv.ParseInt(fraction, 10, 64)
		if err != nil {
			return 0, errors.New("invalid amount")
		}
	}
	scale := minorScale(exponent)
	const maxInt64 = int64(^uint64(0) >> 1)
	if whole > (maxInt64-fractionValue)/scale {
		return 0, errors.New("amount exceeds supported range")
	}
	return whole*scale + fractionValue, nil
}

func formatMinorAmount(amount int64, currency string) string {
	if amount < 0 {
		amount = 0
	}
	exponent := currencyMinorExponent(currency)
	if exponent == 0 {
		return fmt.Sprintf("%d %s", amount, strings.ToUpper(strings.TrimSpace(currency)))
	}
	scale := minorScale(exponent)
	return fmt.Sprintf("%d.%0*d %s", amount/scale, exponent, amount%scale, strings.ToUpper(strings.TrimSpace(currency)))
}

func amountGateLabel(gate amountGate, currency string) string {
	switch gate.Mode {
	case AmountGateAtMost:
		return "不高于 " + formatMinorAmount(gate.Threshold, currency)
	case AmountGateAtLeast:
		return "不低于 " + formatMinorAmount(gate.Threshold, currency)
	case AmountGateAnyKnown:
		return "任意已识别金额"
	default:
		return "严格等于 0"
	}
}

func amountGateMetadata(gate amountGate, paymentAction string) map[string]any {
	return map[string]any{
		"amountGate":           gate.Mode,
		"amountThresholdMinor": gate.Threshold,
		"allowUnknownAmount":   gate.AllowUnknown,
		"paymentAction":        paymentAction,
	}
}

func amountGateStatus(amount, currency string, gate amountGate) string {
	value, err := parseCheckoutAmountMinor(amount, currency)
	if err != nil {
		if gate.AllowUnknown {
			return "unknown_allowed"
		}
		return "unknown"
	}
	if value == 0 {
		return "zero"
	}
	switch gate.Mode {
	case AmountGateAtMost:
		if value <= gate.Threshold {
			return "within_limit"
		}
		return "over_limit"
	case AmountGateAtLeast:
		if value >= gate.Threshold {
			return "meets_minimum"
		}
		return "below_minimum"
	case AmountGateAnyKnown:
		return "known_amount"
	default:
		return "over_limit"
	}
}

func requireAmountGate(stage, amount, currency string, gate amountGate) error {
	status := amountGateStatus(amount, currency, gate)
	switch status {
	case "zero", "within_limit", "meets_minimum", "known_amount", "unknown_allowed":
		return nil
	case "unknown":
		return fmt.Errorf("%s 未返回可识别金额，当前金额门禁拒绝出链", stage)
	case "below_minimum":
		return fmt.Errorf("%s 金额 %s 低于设定下限 %s", stage, checkoutAmountDisplay(amount, currency), formatMinorAmount(gate.Threshold, currency))
	default:
		if gate.Mode == AmountGateStrictZero {
			return fmt.Errorf("%s 金额必须严格等于 0，当前为 %s", stage, checkoutAmountDisplay(amount, currency))
		}
		return fmt.Errorf("%s 金额 %s 超过设定上限 %s", stage, checkoutAmountDisplay(amount, currency), formatMinorAmount(gate.Threshold, currency))
	}
}

func amountStatus(amount string, limit int64) string {
	value, err := parseCheckoutAmount(amount)
	if err != nil {
		return "unknown"
	}
	if value == 0 {
		return "zero"
	}
	if value <= float64(limit) {
		return "within_limit"
	}
	return "over_limit"
}

func parseCheckoutAmount(amount string) (float64, error) {
	text := strings.TrimSpace(amount)
	if text == "" {
		return 0, errors.New("empty amount")
	}
	// OpenAI checkout responses are inconsistent here: Stripe usually returns
	// minor units such as "98214", while some promotion responses return a
	// localized display value such as "₱982.14" or "PHP 0.00". Accept only a
	// single well-formed numeric value surrounded by known currency decoration;
	// arbitrary text must still fail closed as unknown.
	upper := strings.ToUpper(text)
	for _, token := range []string{"PHP", "₱"} {
		upper = strings.ReplaceAll(upper, token, "")
	}
	upper = strings.TrimSpace(upper)
	if upper == "" || strings.Count(upper, ".") > 1 {
		return 0, errors.New("invalid amount")
	}
	for _, r := range upper {
		if (r < '0' || r > '9') && r != '.' && r != ',' && r != '-' && r != '+' && r != ' ' {
			return 0, errors.New("invalid amount")
		}
	}
	upper = strings.ReplaceAll(upper, ",", "")
	upper = strings.ReplaceAll(upper, " ", "")
	if upper == "" || upper == "+" || upper == "-" || upper == "." {
		return 0, errors.New("invalid amount")
	}
	return strconv.ParseFloat(upper, 64)
}

func requireZeroAmount(stage, amount string) error {
	status := amountStatus(amount, 0)
	if status == "zero" {
		return nil
	}
	if status == "unknown" {
		return fmt.Errorf("%s 金额未知，严格零元门禁拒绝继续", stage)
	}
	return fmt.Errorf("%s 金额必须严格等于 0，当前为 %s", stage, strings.TrimSpace(amount))
}

func displayAmount(amount, currency string) string {
	if strings.TrimSpace(amount) == "" {
		return ""
	}
	value, err := strconv.ParseInt(strings.Split(amount, ".")[0], 10, 64)
	if err != nil {
		return amount + " " + currency
	}
	zeroDecimal := map[string]bool{"JPY": true, "KRW": true, "IDR": true, "VND": true}
	if zeroDecimal[strings.ToUpper(currency)] {
		return fmt.Sprintf("%d %s", value, strings.ToUpper(currency))
	}
	return fmt.Sprintf("%.2f %s", float64(value)/100, strings.ToUpper(currency))
}

func checkoutAmountDisplay(amount, currency string) string {
	amount = strings.TrimSpace(amount)
	if amount == "" {
		return ""
	}
	if _, err := strconv.ParseFloat(amount, 64); err == nil {
		return displayAmount(amount, currency)
	}
	return amount
}

func paymentMethodTypes(value any) []string {
	set := map[string]bool{}
	var walk func(any)
	walk = func(node any) {
		switch item := node.(type) {
		case map[string]any:
			for key, value := range item {
				lower := strings.ToLower(key)
				if lower == "payment_method_types" || lower == "ordered_payment_method_types" {
					if array, ok := value.([]any); ok {
						for _, method := range array {
							name := strings.ToLower(stringValue(method))
							if name != "" {
								set[name] = true
							}
						}
					}
				}
				walk(value)
			}
		case []any:
			for _, value := range item {
				walk(value)
			}
		}
	}
	walk(value)
	methods := make([]string, 0, len(set))
	for method := range set {
		methods = append(methods, method)
	}
	sort.Strings(methods)
	return methods
}

func containsFold(values []string, wanted string) bool {
	for _, value := range values {
		if strings.EqualFold(value, wanted) {
			return true
		}
	}
	return false
}

func boolDeep(value any, key string) (bool, bool) {
	switch item := value.(type) {
	case map[string]any:
		for current, value := range item {
			if strings.EqualFold(current, key) {
				boolean, ok := value.(bool)
				return boolean, ok
			}
		}
		for _, child := range item {
			if boolean, ok := boolDeep(child, key); ok {
				return boolean, true
			}
		}
	case []any:
		for _, child := range item {
			if boolean, ok := boolDeep(child, key); ok {
				return boolean, true
			}
		}
	}
	return false, false
}

func hasTrialMarker(value any) bool {
	switch item := value.(type) {
	case map[string]any:
		for key, value := range item {
			lower := strings.ToLower(key)
			if lower == "trial_period_days" {
				if number, err := strconv.Atoi(strings.Split(stringValue(value), ".")[0]); err == nil && number > 0 {
					return true
				}
			}
			if lower == "trial_end" && stringValue(value) != "" && stringValue(value) != "0" {
				return true
			}
			if hasTrialMarker(value) {
				return true
			}
		}
	case []any:
		for _, child := range item {
			if hasTrialMarker(child) {
				return true
			}
		}
	}
	return false
}

type billingAddress struct {
	Name       string
	Email      string
	Country    string
	Line1      string
	Line2      string
	City       string
	State      string
	PostalCode string
}

func billingForCountry(country, email string) billingAddress {
	country = normalizeCountry(country, "US")
	if !emailPattern.MatchString(email) {
		email = fmt.Sprintf("buyer.%s@example.com", randomHex(5))
	}
	addresses := map[string][]billingAddress{
		"US": {{Name: "James Smith", Line1: "500 Main Street", City: "Austin", State: "TX", PostalCode: "78701"}},
		"JP": {{Name: "Taro Yamada", Line1: "1-2-3 Shibuya", City: "Shibuya-ku", State: "Tokyo", PostalCode: "150-0002"}},
		"NL": {{Name: "Jan de Vries", Line1: "Prinsengracht 263", City: "Amsterdam", PostalCode: "1016 GV"}},
		"KR": {{Name: "Kim Minjun", Line1: "12 Teheran-ro", City: "Gangnam-gu", State: "Seoul", PostalCode: "06236"}},
		"IN": {{Name: "Arjun Sharma", Line1: "12 MG Road", City: "Bengaluru", State: "Karnataka", PostalCode: "560001"}},
		"ID": {{Name: "Budi Santoso", Line1: "Jl. Jend. Sudirman No. 1", City: "Jakarta", State: "DKI Jakarta", PostalCode: "10210"}},
		"VN": {{Name: "Nguyen Minh", Line1: "12 Nguyen Hue", City: "Ho Chi Minh City", State: "Ho Chi Minh", PostalCode: "700000"}},
		"PH": {{Name: "Juan Santos", Line1: "12 Ayala Avenue", City: "Makati", State: "Metro Manila", PostalCode: "1226"}},
		"GB": {{Name: "James Wilson", Line1: "10 Downing Street", City: "London", PostalCode: "SW1A 2AA"}},
		"DE": {{Name: "Lukas Mueller", Line1: "Unter den Linden 10", City: "Berlin", State: "Berlin", PostalCode: "10117"}},
		"FR": {{Name: "Louis Martin", Line1: "10 Rue de Rivoli", City: "Paris", PostalCode: "75001"}},
		"CA": {{Name: "Liam Smith", Line1: "100 King Street W", City: "Toronto", State: "ON", PostalCode: "M5X 1A9"}},
		"AU": {{Name: "Oliver Brown", Line1: "100 George Street", City: "Sydney", State: "NSW", PostalCode: "2000"}},
		"BR": {{Name: "Lucas Silva", Line1: "Avenida Paulista 1000", City: "Sao Paulo", State: "SP", PostalCode: "01310-100"}},
		"TR": {{Name: "Mehmet Yilmaz", Line1: "Istiklal Caddesi 10", City: "Istanbul", State: "Istanbul", PostalCode: "34433"}},
		"TH": {{Name: "Somchai Saelim", Line1: "10 Sukhumvit Road", City: "Bangkok", State: "Bangkok", PostalCode: "10110"}},
		"AE": {{Name: "Omar Hassan", Line1: "10 Sheikh Zayed Road", City: "Dubai", State: "Dubai", PostalCode: "00000"}},
		"BA": {{Name: "Amar Hadzic", Line1: "Ferhadija 10", City: "Sarajevo", State: "Sarajevo", PostalCode: "71000"}},
		"BH": {{Name: "Ali Hassan", Line1: "10 Government Avenue", City: "Manama", State: "Capital", PostalCode: "317"}},
		"MX": {{Name: "Carlos Hernandez", Line1: "Paseo de la Reforma 100", City: "Ciudad de Mexico", State: "CDMX", PostalCode: "06600"}},
	}
	choices := addresses[country]
	if len(choices) == 0 {
		choices = addresses["US"]
	}
	address := choices[rand.IntN(len(choices))]
	address.Email = email
	address.Country = country
	return address
}

func newUUID() string {
	value := randomHex(16)
	if len(value) < 32 {
		return value
	}
	return value[:8] + "-" + value[8:12] + "-4" + value[13:16] + "-a" + value[17:20] + "-" + value[20:32]
}
