package extractmethods

import (
	"encoding/json"
	"errors"
	"fmt"
	"math/rand/v2"
	"net/http"
	"net/url"
	"strings"
	"time"
)

func (f *flow) runKakao() (Result, error) {
	checkout, bootstrap, err := f.createKakaoCheckout()
	if err != nil {
		if isAlreadyPaidError(err) {
			return alreadyPaidResult("KR", "KRW", err.Error()), err
		}
		return Result{Country: "KR", Currency: "KRW"}, err
	}
	partial := Result{Country: "KR", Currency: "KRW", CheckoutID: checkout.ID, ProcessorEntity: checkout.ProcessorEntity}
	partial.Amount = bootstrap.Amount
	partial.AmountDisplay = displayAmount(bootstrap.Amount, "KRW")
	partial.AvailableMethods = bootstrap.Methods
	if _, err := f.updatePromotion(f.promotion, checkout); err != nil {
		return partial, err
	}
	// Keep the exact sticky KR proxy identity while separating the Checkout
	// cookie jar from the post-promotion Stripe/NicePay session. This mirrors
	// the verified curl_cffi flow: same exit identity, independent sessions.
	paymentClient := f.provider
	init, err := f.stripeInit(paymentClient, checkout, true)
	if err != nil {
		return partial, err
	}
	partial.Amount = init.Amount
	partial.AmountDisplay = displayAmount(init.Amount, "KRW")
	partial.AmountStatus = amountStatus(init.Amount, 0)
	partial.AvailableMethods = init.Methods
	if err := requireZeroAmount("Kakao checkout", init.Amount); err != nil {
		return partial, err
	}
	if !containsFold(init.Methods, "kakao_pay") {
		return partial, fmt.Errorf("Promotion 后 checkout 不支持 kakao_pay；methods=%s", strings.Join(init.Methods, ","))
	}
	billing := kakaoBilling()
	if err := f.updateCheckoutTaxes(paymentClient, checkout, billing, "KRW"); err != nil {
		return partial, err
	}
	ctx := newStripeContext(init, false)
	if err := f.updateKakaoStripeTaxRegion(paymentClient, checkout, billing, ctx); err != nil {
		return partial, err
	}
	// Stripe mutates the payment-page state after taxes/tax_region. Kakao must
	// confirm with the refreshed checksum and Elements context, matching the
	// provider browser session that performed the tax update.
	init, err = f.stripeInit(paymentClient, checkout, true)
	if err != nil {
		return partial, err
	}
	partial.Amount = init.Amount
	partial.AmountDisplay = displayAmount(init.Amount, "KRW")
	partial.AmountStatus = amountStatus(init.Amount, 0)
	partial.AvailableMethods = init.Methods
	if err := requireZeroAmount("Kakao 税务同步后 checkout", init.Amount); err != nil {
		return partial, err
	}
	if !containsFold(init.Methods, "kakao_pay") {
		return partial, fmt.Errorf("税务同步后 checkout 不支持 kakao_pay；methods=%s", strings.Join(init.Methods, ","))
	}
	ctx = newStripeContext(init, false)
	preConfirm := url.Values{
		"eid": {newUUID()}, "payment_method_type": {"kakao_pay"},
		"key": {checkout.PublishableKey}, "_stripe_version": {stripeVersion},
	}
	response, err := paymentClient.Form(f.ctx, http.MethodPost, f.engine.Endpoints.Stripe+"/v1/payment_pages/"+url.PathEscape(checkout.ID)+"/pre_confirm", stripeHeaders(checkout, f.profile), preConfirm)
	if err != nil {
		return partial, err
	}
	if response.Status >= 400 {
		return partial, upstreamError("stripe.pre_confirm", response)
	}
	f.addStep("stripe.pre_confirm", "success", "kakao_pay", 0)
	pmID, err := f.createProviderPaymentMethod(paymentClient, checkout, billing, "kakao_pay", ctx)
	if err != nil {
		return partial, err
	}
	partial.PaymentMethodID = pmID
	confirm, err := f.confirmProvider(paymentClient, checkout, init, pmID, "kakao_pay", ctx)
	if err != nil {
		return partial, err
	}
	redirect, err := f.resolveProviderAfterConfirmWith(paymentClient, paymentClient, false, checkout, init, confirm, pmID, "kakao", ctx, 35*time.Second)
	if err != nil {
		return partial, err
	}
	providerURL := f.followProviderRedirect(paymentClient, redirect, []string{"nicepay.co.kr", "kakaopay.com", "kakao.com"})
	partial.OK = true
	partial.StripeRedirectURL = redirect
	partial.ProviderRedirectURL = providerURL
	partial.LongURL = firstNonEmpty(providerURL, redirect)
	partial.ExtractionStatus = "provider_link_ready"
	partial.PaymentStatus = "awaiting_kakao_payment"
	// NicePay/KakaoPay QR channel is typically valid for about 10 minutes.
	generatedAt := time.Now().UTC()
	expiresAt := generatedAt.Add(10 * time.Minute)
	partial.LinkGeneratedAt = generatedAt.Format(time.RFC3339)
	partial.ExpiresAt = expiresAt.Format(time.RFC3339)
	partial.LinkTTLSeconds = 600
	if partial.Metadata == nil {
		partial.Metadata = map[string]any{}
	}
	partial.Metadata["linkChannel"] = "kakao_nicepay"
	partial.Metadata["linkTtlSeconds"] = 600
	partial.Metadata["linkGeneratedAt"] = partial.LinkGeneratedAt
	partial.Metadata["expiresAt"] = partial.ExpiresAt
	partial.Metadata["providerLinkExpiresAt"] = partial.ExpiresAt
	f.addStep("kakao.link_expiry", "success", fmt.Sprintf("NicePay/Kakao 二维码/长链有效期 10 分钟；expiresAt=%s", partial.ExpiresAt), 0)
	return partial, nil
}

func (f *flow) createKakaoCheckout() (checkoutData, stripeInit, error) {
	attempts := f.options.Attempts()
	variants := []struct {
		label                string
		includePromo         bool
		usePricingEntryPoint bool
	}{
		{"Kakao 原始入口 / 创建时带优惠（参考流程）", true, false},
		{"Kakao 原始入口 / 创建时不带优惠", false, false},
		{"pricing 入口 / 创建时不带优惠", false, true},
	}
	var last error
	for attempt := 1; attempt <= attempts; attempt++ {
		variant := variants[(attempt-1)%len(variants)]
		f.addStep("kakao.checkout_attempt", "running", fmt.Sprintf("第 %d/%d 次：%s；同一 KR sticky 创建并激活 Stripe 支付页", attempt, attempts, variant.label), 0)
		checkout, err := f.createCheckoutVariant(f.checkout, "KR", "KRW", "custom", variant.includePromo, 0, variant.usePricingEntryPoint)
		if err != nil {
			last = err
			if isAlreadyPaidError(err) {
				f.addStep("kakao.checkout_attempt", "failed", "账号已付费，停止后续流程", 0)
				return checkoutData{}, stripeInit{}, err
			}
		} else {
			checkout, err = f.activateStripeCheckout(f.checkout, checkout)
			if err != nil {
				last = err
				goto retry
			}
			init, initErr := f.stripeInit(f.checkout, checkout, true)
			if initErr == nil {
				initErr = requireZeroAmount("Kakao bootstrap checkout", init.Amount)
			}
			if initErr == nil && containsFold(init.Methods, "kakao_pay") {
				f.addStep("kakao.checkout_ready", "success", fmt.Sprintf("第 %d 次 checkout 已展示 kakao_pay", attempt), 0)
				return checkout, init, nil
			}
			if initErr != nil {
				last = initErr
			} else {
				last = fmt.Errorf("checkout 未展示 kakao_pay；methods=%s", strings.Join(init.Methods, ","))
			}
		}
	retry:
		status := "retrying"
		if attempt == attempts {
			status = "failed"
		}
		f.addStep("kakao.checkout_retry", status, fmt.Sprintf("第 %d/%d 次未命中：%v", attempt, attempts, last), 0)
	}
	if last == nil {
		last = errors.New("Kakao checkout 重建后仍未展示 kakao_pay")
	}
	return checkoutData{}, stripeInit{}, last
}

func (f *flow) updateCheckoutTaxes(client *BrowserClient, checkout checkoutData, billing billingAddress, currency string) error {
	stage := "chatgpt.checkout_taxes"
	payload := map[string]any{
		"checkout_session_id": checkout.ID,
		"checkout_email":      billing.Email,
		"billing_country":     billing.Country,
		"billing_name":        billing.Name,
		"currency":            currency,
		"tax_id":              nil,
		"processor_entity":    checkout.ProcessorEntity,
		"billing_address": map[string]string{
			"line1": billing.Line1, "city": billing.City,
			"country": billing.Country, "postal_code": billing.PostalCode, "state": billing.State,
		},
	}
	referer := fmt.Sprintf("%s/checkout/%s/%s", f.engine.Endpoints.ChatGPT, checkout.ProcessorEntity, checkout.ID)
	started := time.Now()
	response, err := client.JSON(f.ctx, http.MethodPost, f.engine.Endpoints.ChatGPT+"/backend-api/payments/checkout/taxes", f.kakaoCheckoutAPIHeaders(referer, "/backend-api/payments/checkout/taxes"), payload)
	if err != nil {
		return err
	}
	if response.Status >= 400 {
		return upstreamError(stage, response)
	}
	f.addStep(stage, "success", billing.Country+" / "+billing.PostalCode, time.Since(started))
	return nil
}

func kakaoBilling() billingAddress {
	names := []string{"김민준", "이서준", "박지훈", "최도윤", "정하준"}
	addresses := []billingAddress{
		{Line1: "테헤란로 12", City: "서울특별시", State: "강남구", PostalCode: "06236"},
		{Line1: "세종대로 110", City: "서울특별시", State: "중구", PostalCode: "04524"},
		{Line1: "송파대로 201", City: "서울특별시", State: "송파구", PostalCode: "05552"},
	}
	domains := []string{"gmail.com", "naver.com", "daum.net", "kakao.com"}
	billing := addresses[rand.IntN(len(addresses))]
	billing.Name = names[rand.IntN(len(names))]
	billing.Email = "buyer." + randomHex(5) + "@" + domains[rand.IntN(len(domains))]
	billing.Country = "KR"
	return billing
}

func (f *flow) updateKakaoStripeTaxRegion(client *BrowserClient, checkout checkoutData, billing billingAddress, ctx stripeContext) error {
	stage := "stripe.tax_region"
	form := f.elementsSessionParams(ctx, true)
	form.Set("key", checkout.PublishableKey)
	form.Set("_stripe_version", ctx.StripeVersion)
	form.Set("tax_region[country]", billing.Country)
	form.Set("tax_region[postal_code]", billing.PostalCode)
	form.Set("tax_region[line1]", billing.Line1)
	form.Set("tax_region[city]", billing.City)
	form.Set("tax_region[state]", billing.State)
	started := time.Now()
	response, err := client.Form(f.ctx, http.MethodPost, f.engine.Endpoints.Stripe+"/v1/payment_pages/"+url.PathEscape(checkout.ID), stripeHeaders(checkout, f.profile), form)
	if err != nil {
		return err
	}
	if response.Status >= 400 {
		return upstreamError(stage, response)
	}
	f.addStep(stage, "success", billing.Country+" / "+billing.PostalCode, time.Since(started))
	return nil
}

type upiMaterial struct {
	Payload     string
	Instruction string
	PNG         string
	SVG         string
	Redirect    string
}

func (m upiMaterial) URL() string {
	return firstNonEmpty(m.Instruction, m.Redirect, m.PNG, m.SVG, m.Payload)
}

func (m upiMaterial) Ready() bool {
	return m.URL() != ""
}

func (f *flow) runUPI() (Result, error) {
	checkout, err := f.createCheckout(f.checkout, "IN", "INR", "custom", true, 0)
	if err != nil {
		if isAlreadyPaidError(err) {
			return alreadyPaidResult("IN", "INR", err.Error()), err
		}
		return Result{Country: "IN", Currency: "INR"}, err
	}
	partial := Result{Country: "IN", Currency: "INR", CheckoutID: checkout.ID, ProcessorEntity: checkout.ProcessorEntity}
	bootstrap, err := f.stripeInit(f.checkout, checkout, true)
	if err != nil {
		return partial, err
	}
	if err := requireZeroAmount("UPI bootstrap checkout", bootstrap.Amount); err != nil {
		return partial, err
	}
	if _, err := f.updatePromotion(f.promotion, checkout); err != nil {
		return partial, err
	}
	init, err := f.stripeInit(f.provider, checkout, true)
	if err != nil {
		return partial, err
	}
	partial.Amount = init.Amount
	partial.AmountDisplay = displayAmount(init.Amount, "INR")
	partial.AmountStatus = amountStatus(init.Amount, 0)
	partial.AvailableMethods = init.Methods
	if err := requireZeroAmount("UPI checkout", init.Amount); err != nil {
		return partial, err
	}
	if len(init.Methods) > 0 && !containsFold(init.Methods, "upi") {
		return partial, fmt.Errorf("checkout 不支持 UPI；methods=%s", strings.Join(init.Methods, ","))
	}
	billing := billingForCountry("IN", f.credential.Email)
	ctx := newStripeContext(init, false)
	if err := f.createElementsSession(checkout, init, &ctx); err != nil {
		return partial, err
	}
	if err := f.updateUPITaxRegion(checkout, billing, ctx); err != nil {
		return partial, err
	}
	if err := f.snapshotCheckout(checkout, billing); err != nil {
		return partial, err
	}
	confirm, err := f.confirmUPI(checkout, init, billing, ctx)
	if err != nil {
		return partial, err
	}
	material := extractUPIMaterial(confirm)
	if !material.Ready() && requiresApproval(confirm) {
		if err := f.approveCheckout(checkout, f.options.ApproveRetries()); err != nil {
			return partial, err
		}
	}
	if !material.Ready() {
		material, err = f.pollUPIMaterial(checkout, init, ctx, 30*time.Second)
		if err != nil {
			return partial, err
		}
	}
	partial.OK = true
	partial.LongURL = material.URL()
	partial.ProviderRedirectURL = firstNonEmpty(material.Instruction, material.Redirect)
	partial.UPIPayload = material.Payload
	partial.UPIInstructionURL = material.Instruction
	partial.QRPNGURL = material.PNG
	partial.QRSVGURL = material.SVG
	partial.ExtractionStatus = "upi_ready"
	partial.PaymentStatus = "awaiting_upi_payment"
	partial.Metadata = map[string]any{
		"upiPayload":     material.Payload,
		"instructionUrl": material.Instruction,
		"qrPngUrl":       material.PNG,
		"qrSvgUrl":       material.SVG,
	}
	f.addStep("upi.material", "success", partial.LongURL, 0)
	return partial, nil
}

func (f *flow) createElementsSession(checkout checkoutData, init stripeInit, ctx *stripeContext) error {
	stage := "stripe.elements_session"
	params := url.Values{
		"client_betas[0]":                          {"custom_checkout_server_updates_1"},
		"client_betas[1]":                          {"custom_checkout_manual_approval_1"},
		"deferred_intent[mode]":                    {"subscription"},
		"deferred_intent[amount]":                  {firstNonEmpty(init.Amount, "0")},
		"deferred_intent[currency]":                {"inr"},
		"deferred_intent[setup_future_usage]":      {"off_session"},
		"deferred_intent[payment_method_types][0]": {"card"},
		"deferred_intent[payment_method_types][1]": {"upi"},
		"currency": {"inr"}, "key": {checkout.PublishableKey},
		"_stripe_version":      {stripeVersion},
		"elements_init_source": {"custom_checkout"}, "referrer_host": {"chatgpt.com"},
		"stripe_js_id": {ctx.StripeJSID}, "locale": {f.profile.Elements},
		"type": {"deferred_intent"}, "checkout_session_id": {checkout.ID},
	}
	started := time.Now()
	response, err := f.provider.Do(f.ctx, http.MethodGet, f.engine.Endpoints.Stripe+"/v1/elements/sessions?"+params.Encode(), stripeHeaders(checkout, f.profile), nil, true)
	if err != nil {
		return err
	}
	if response.Status >= 400 {
		return upstreamError(stage, response)
	}
	body := decodeLooseJSON(response.Body)
	ctx.ElementsSessionID = firstNonEmpty(findStringDeep(body, "session_id", "id"), ctx.ElementsSessionID)
	ctx.ElementsSessionConfigID = firstNonEmpty(findStringDeep(body, "config_id"), ctx.ElementsSessionConfigID)
	if ctx.ElementsSessionID == "" {
		return errors.New("elements session 缺少 session_id")
	}
	f.addStep(stage, "success", ctx.ElementsSessionID, time.Since(started))
	return nil
}

func (f *flow) updateUPITaxRegion(checkout checkoutData, billing billingAddress, ctx stripeContext) error {
	stage := "stripe.upi_tax_region"
	base := f.elementsBaseForm(checkout, ctx, true)
	forms := []url.Values{
		cloneValues(base), cloneValues(base), cloneValues(base), cloneValues(base),
	}
	forms[0].Set("tax_region[country]", "IN")
	forms[1].Set("tax_region[country]", "IN")
	forms[2].Set("tax_region[country]", "IN")
	forms[2].Set("tax_region[line1]", billing.Line1)
	forms[3].Set("tax_region[country]", "IN")
	forms[3].Set("tax_region[line1]", billing.Line1)
	forms[3].Set("tax_region[city]", billing.City)
	forms[3].Set("tax_region[postal_code]", billing.PostalCode)
	forms[3].Set("tax_region[state]", billing.State)
	for index, form := range forms {
		form.Set("key", checkout.PublishableKey)
		response, err := f.provider.Form(f.ctx, http.MethodPost, f.engine.Endpoints.Stripe+"/v1/payment_pages/"+url.PathEscape(checkout.ID), stripeHeaders(checkout, f.profile), form)
		if err != nil {
			return err
		}
		if response.Status >= 400 {
			return upstreamError(stage, response)
		}
		f.addStep(stage, "success", fmt.Sprintf("第 %d/4 次", index+1), 0)
	}
	return nil
}

func (f *flow) snapshotCheckout(checkout checkoutData, billing billingAddress) error {
	stage := "chatgpt.checkout_snapshot"
	payload := map[string]any{
		"snapshot": map[string]any{
			"billing_address": map[string]any{
				"name": billing.Name,
				"address": map[string]string{
					"line1": billing.Line1, "line2": billing.Line2, "city": billing.City,
					"country": billing.Country, "postal_code": billing.PostalCode, "state": billing.State,
				},
			},
		},
	}
	referer := fmt.Sprintf("%s/checkout/%s/%s", f.engine.Endpoints.ChatGPT, checkout.ProcessorEntity, checkout.ID)
	response, err := f.approve.JSON(f.ctx, http.MethodPost, f.engine.Endpoints.ChatGPT+"/backend-api/payments/checkout/snapshot", f.openAIHeaders("/backend-api/payments/checkout/snapshot", referer), payload)
	if err != nil {
		return err
	}
	if response.Status != http.StatusNoContent && response.Status >= 400 {
		return upstreamError(stage, response)
	}
	f.addStep(stage, "success", "billing snapshot 已同步", 0)
	return nil
}

func (f *flow) confirmUPI(checkout checkoutData, init stripeInit, billing billingAddress, ctx stripeContext) (map[string]any, error) {
	stage := "stripe.upi_confirm"
	form := f.elementsBaseForm(checkout, ctx, true)
	form.Set("guid", ctx.GUID)
	form.Set("muid", ctx.MUID)
	form.Set("sid", ctx.SID)
	form.Set("expected_amount", firstNonEmpty(init.Amount, "0"))
	form.Set("expected_payment_method_type", "upi")
	form.Set("return_url", f.providerReturnURL(checkout, "upi"))
	form.Set("version", ctx.RuntimeVersion)
	form.Set("init_checksum", ctx.InitChecksum)
	form.Set("key", checkout.PublishableKey)
	form.Set("payment_method_data[type]", "upi")
	form.Set("payment_method_data[billing_details][name]", billing.Name)
	form.Set("payment_method_data[billing_details][email]", billing.Email)
	form.Set("payment_method_data[billing_details][address][country]", billing.Country)
	form.Set("payment_method_data[billing_details][address][line1]", billing.Line1)
	form.Set("payment_method_data[billing_details][address][city]", billing.City)
	form.Set("payment_method_data[billing_details][address][postal_code]", billing.PostalCode)
	form.Set("payment_method_data[billing_details][address][state]", billing.State)
	form.Set("payment_method_data[payment_user_agent]", fmt.Sprintf("stripe.js/%s; stripe-js-v3/%s; payment-element; deferred-intent", ctx.RuntimeVersion, ctx.RuntimeVersion))
	form.Set("payment_method_data[referrer]", "https://chatgpt.com")
	form.Set("payment_method_data[time_on_page]", "360000")
	response, err := f.provider.Form(f.ctx, http.MethodPost, f.engine.Endpoints.Stripe+"/v1/payment_pages/"+url.PathEscape(checkout.ID)+"/confirm", stripeHeaders(checkout, f.profile), form)
	if err != nil {
		return nil, err
	}
	if response.Status >= 400 {
		return nil, upstreamError(stage, response)
	}
	body := decodeLooseJSON(response.Body)
	if failure := findFailureMessage(body); failure != "" {
		return body, errors.New(failure)
	}
	f.addStep(stage, "success", "UPI confirm 完成", 0)
	return body, nil
}

func (f *flow) pollUPIMaterial(checkout checkoutData, init stripeInit, ctx stripeContext, timeout time.Duration) (upiMaterial, error) {
	stage := "stripe.upi_poll"
	params := f.elementsBaseForm(checkout, ctx, true)
	params.Set("key", checkout.PublishableKey)
	deadline := time.Now().Add(timeout)
	last := ""
	for time.Now().Before(deadline) {
		response, err := f.provider.Do(f.ctx, http.MethodGet, f.engine.Endpoints.Stripe+"/v1/payment_pages/"+url.PathEscape(checkout.ID)+"?"+params.Encode(), stripeHeaders(checkout, f.profile), nil, true)
		if err == nil && response.Status < 400 {
			body := decodeLooseJSON(response.Body)
			if failure := findFailureMessage(body); failure != "" {
				return upiMaterial{}, errors.New(failure)
			}
			if material := extractUPIMaterial(body); material.Ready() {
				f.addStep(stage, "success", material.URL(), 0)
				return material, nil
			}
			last = "keys=" + strings.Join(mapKeys(body), ",")
		} else if err != nil {
			last = err.Error()
		} else {
			last = fmt.Sprintf("HTTP %d", response.Status)
		}
		if err := sleepContext(f.ctx, 700*time.Millisecond); err != nil {
			return upiMaterial{}, err
		}
	}
	return upiMaterial{}, fmt.Errorf("UPI QR/指令轮询超时: %s", last)
}

func extractUPIMaterial(value any) upiMaterial {
	material := upiMaterial{}
	var walk func(any, string, string)
	walk = func(node any, key, path string) {
		switch item := node.(type) {
		case map[string]any:
			for childKey, child := range item {
				childPath := childKey
				if path != "" {
					childPath = path + "." + childKey
				}
				walk(child, childKey, childPath)
			}
		case []any:
			for _, child := range item {
				walk(child, key, path)
			}
		case string:
			text := strings.TrimSpace(item)
			lowerKey := strings.ToLower(key)
			lower := strings.ToLower(text)
			upiContext := isUPIMaterialPath(path)
			switch {
			case material.Payload == "" && strings.HasPrefix(lower, "upi://pay"):
				material.Payload = text
			case material.Instruction == "" && (strings.Contains(lowerKey, "instruction") || (upiContext && strings.Contains(lowerKey, "hosted"))) && validUPIInstructionURL(text):
				material.Instruction = text
			case material.PNG == "" && validUPIQRImageURL(lowerKey, text, "png"):
				material.PNG = text
			case material.SVG == "" && validUPIQRImageURL(lowerKey, text, "svg"):
				material.SVG = text
			case material.Redirect == "" && upiContext && validUPIRedirectURL(lowerKey, text):
				material.Redirect = text
			}
		}
	}
	walk(value, "", "")
	return material
}

func isUPIMaterialPath(path string) bool {
	lower := strings.ToLower(path)
	for _, marker := range []string{"upi", "display_qr_code", "qr_code", "pix_display"} {
		if strings.Contains(lower, marker) {
			return true
		}
	}
	return false
}

func validUPIQRImageURL(key, raw, format string) bool {
	if !strings.Contains(key, format) || strings.Contains(key, "icon") || strings.Contains(key, "logo") {
		return false
	}
	return validUPIMaterialURL(raw)
}

func validUPIRedirectURL(key, raw string) bool {
	if !strings.Contains(key, "url") && !strings.Contains(key, "redirect") {
		return false
	}
	// An image URL rejected by the QR branch must not come back as a generic
	// redirect. Static payment-method assets frequently use image_url_png.
	for _, marker := range []string{"png", "svg", "image", "icon", "logo"} {
		if strings.Contains(key, marker) {
			return false
		}
	}
	return validUPIInstructionURL(raw)
}

func validUPIMaterialURL(raw string) bool {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Hostname() == "" {
		return false
	}
	return !isStaticPaymentMethodAssetURL(parsed)
}

func isStaticPaymentMethodAssetURL(parsed *url.URL) bool {
	host := strings.ToLower(parsed.Hostname())
	if host == "js.stripe.com" {
		return true
	}
	path := strings.ToLower(parsed.EscapedPath())
	for _, marker := range []string{
		"/fingerprinted/img/payment-methods/",
		"/img/payment-methods/",
		"/payment-methods/icon-pm-",
		"icon-pm-upi",
		"icon-pm-pix",
	} {
		if strings.Contains(path, marker) {
			return true
		}
	}
	return false
}

func validUPIInstructionURL(raw string) bool {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Hostname() == "" || isStaticPaymentMethodAssetURL(parsed) {
		return false
	}
	host := strings.ToLower(parsed.Hostname())
	switch host {
	case "js.stripe.com", "m.stripe.network", "api.stripe.com", "r.stripe.com", "q.stripe.com",
		"checkout.stripe.com", "pay.openai.com", "chatgpt.com", "www.chatgpt.com":
		return false
	}
	pathAndQuery := strings.ToLower(parsed.EscapedPath() + "?" + parsed.RawQuery)
	if host == "pm-redirects.stripe.com" && strings.Contains(pathAndQuery, "/redirect/complete") {
		return true
	}
	stripeHost := host == "stripe.com" || strings.HasSuffix(host, ".stripe.com") || host == "stripe.network" || strings.HasSuffix(host, ".stripe.network")
	if !stripeHost {
		return true
	}
	if host == "qr.stripe.com" && parsed.EscapedPath() != "" && parsed.EscapedPath() != "/" {
		return true
	}
	for _, marker := range []string{"/qr/", "qr_code", "instruction", "upi", "pix"} {
		if strings.Contains(pathAndQuery, marker) {
			return true
		}
	}
	return false
}

func validUPILegacyLongURL(raw string) bool {
	if !validUPIInstructionURL(raw) {
		return false
	}
	parsed, _ := url.Parse(strings.TrimSpace(raw))
	host := strings.ToLower(parsed.Hostname())
	stripeHost := host == "stripe.com" || strings.HasSuffix(host, ".stripe.com") || host == "stripe.network" || strings.HasSuffix(host, ".stripe.network")
	if stripeHost {
		return true
	}
	pathAndQuery := strings.ToLower(parsed.EscapedPath() + "?" + parsed.RawQuery)
	for _, marker := range []string{"upi", "/qr", "qr_", "instruction", "/pay"} {
		if strings.Contains(pathAndQuery, marker) {
			return true
		}
	}
	return false
}

func findFailureMessage(value any) string {
	switch item := value.(type) {
	case map[string]any:
		for key, child := range item {
			lower := strings.ToLower(key)
			if lower == "last_setup_error" || lower == "last_payment_error" {
				encoded, _ := json.Marshal(child)
				return trimDetail(string(encoded), 700)
			}
			if lower == "status" && strings.EqualFold(stringValue(child), "failed") {
				return "Stripe submission failed"
			}
			if failure := findFailureMessage(child); failure != "" {
				return failure
			}
		}
	case []any:
		for _, child := range item {
			if failure := findFailureMessage(child); failure != "" {
				return failure
			}
		}
	}
	return ""
}

func cloneValues(input url.Values) url.Values {
	result := make(url.Values, len(input))
	for key, values := range input {
		result[key] = append([]string(nil), values...)
	}
	return result
}
