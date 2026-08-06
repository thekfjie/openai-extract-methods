package extractmethods

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"math/rand/v2"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

func generateBrazilCPF() string {
	digits := make([]int, 9)
	for i := range digits {
		digits[i] = int(rand.IntN(9))
	}
	calc := func(values []int) int {
		weight := len(values) + 1
		total := 0
		for _, digit := range values {
			total += digit * weight
			weight--
		}
		remainder := total % 11
		if remainder < 2 {
			return 0
		}
		return 11 - remainder
	}
	digits = append(digits, calc(digits))
	digits = append(digits, calc(digits))
	return fmt.Sprintf("%d%d%d.%d%d%d.%d%d%d-%d%d", digits[0], digits[1], digits[2], digits[3], digits[4], digits[5], digits[6], digits[7], digits[8], digits[9], digits[10])
}

var (
	paypalBAStrictToken = regexp.MustCompile(`(?i)^BA-[A-Za-z0-9_-]{8,80}$`)
	httpURLPattern      = regexp.MustCompile(`https?://[^\s"'<>\\]+`)
)

const (
	paypalTerminalSetupErrorPolls = 3
	paypalRedirectPollTimeout     = 8 * time.Second
)

type stripeContext struct {
	GUID                    string
	MUID                    string
	SID                     string
	ClientSessionID         string
	StripeJSID              string
	ElementsSessionID       string
	ElementsSessionConfigID string
	CheckoutConfigID        string
	InitChecksum            string
	RuntimeVersion          string
	StripeVersion           string
	PollPhase               string
}

func newStripeContext(init stripeInit, paypal bool) stripeContext {
	runtimeVersion := stripeRuntimeVersion
	version := stripeVersion
	if paypal {
		runtimeVersion = paypalRuntimeVersion
		version = paypalStripeVersion
	}
	configID := firstNonEmpty(init.ConfigID, newUUID())
	elementsSuffix := randomHex(6)
	if len(elementsSuffix) > 11 {
		elementsSuffix = elementsSuffix[:11]
	}
	return stripeContext{
		GUID: stripeBrowserID(), MUID: stripeBrowserID(), SID: stripeBrowserID(),
		ClientSessionID: newUUID(), StripeJSID: firstNonEmpty(init.StripeJSID, newUUID()), ElementsSessionID: "elements_session_" + elementsSuffix,
		ElementsSessionConfigID: configID, CheckoutConfigID: init.ConfigID,
		InitChecksum: init.InitChecksum, RuntimeVersion: runtimeVersion, StripeVersion: version,
	}
}

func stripeBrowserID() string {
	return newUUID() + randomHex(3)
}

func (f *flow) runPayPal() (Result, error) {
	var last error
	var partial Result
	gate := amountGate{Mode: AmountGateStrictZero}
	for attempt := 1; attempt <= f.options.Attempts(); attempt++ {
		selected := SelectAttemptOptions(f.options, attempt)
		// Port Kakao-like per-attempt rotation even for single-proxy runs:
		// fresh sticky identity + rotated TLS profile + independent jars.
		fingerprints := []string{"chrome146", "chrome136", "chrome"}
		selected.ClientFingerprint = fingerprints[(attempt-1)%len(fingerprints)]
		stage, stageErr := ResolveStageProxies(f.engine.Config, selected, MethodPayPalBA)
		if stageErr != nil {
			last = stageErr
			f.addStep("paypal.attempt", "retrying", stageErr.Error(), 0)
			continue
		}
		rebuild := func(current **BrowserClient, proxy *string, nextProxy string) {
			if client, clientErr := NewBrowserClient(nextProxy, selected.Timeout(), selected.ClientFingerprint); clientErr == nil {
				if *current != nil {
					(*current).Close()
				}
				*current = client
				*proxy = nextProxy
			}
		}
		rebuild(&f.checkout, &f.proxies.Checkout, stage.Checkout)
		rebuild(&f.promotion, &f.proxies.Promotion, stage.Promotion)
		rebuild(&f.provider, &f.proxies.Provider, stage.Provider)
		rebuild(&f.approve, &f.proxies.Approve, stage.Approve)
		f.fingerprint = resolveRequestFingerprint(selected.ClientFingerprint)
		f.addStep("paypal.attempt", "running", fmt.Sprintf("第 %d/%d 次 BA 提炼；Promotion=%s；fingerprint=%s", attempt, f.options.Attempts(), firstNonEmpty(selected.PromotionProxyRegion, stage.Labels["promotion"]), f.fingerprint.Name), 0)
		// Match the upstream promotion flow: carry the campaign in checkout
		// creation, then refresh it through the independently routed promotion
		// client. The latter is what makes billing+promo combinations observable.
		checkout, err := f.createCheckout(f.checkout, f.options.Country, f.options.Currency, "custom", true, 0)
		if err != nil {
			last = err
			if isAlreadyPaidError(err) {
				partial = alreadyPaidResult(f.options.Country, f.options.Currency, err.Error())
				f.addStep("paypal.attempt", "failed", "账号已付费，停止 BA 提炼", 0)
				return partial, last
			}
			continue
		}
		partial = Result{Country: checkout.Country, Currency: checkout.Currency, CheckoutID: checkout.ID, ProcessorEntity: checkout.ProcessorEntity}
		if _, err = f.updatePromotion(f.promotion, checkout); err != nil {
			last = err
			continue
		}
		init, err := f.stripeInit(f.provider, checkout, false)
		if err != nil {
			last = err
			continue
		}
		partial.Amount = init.Amount
		partial.AmountDisplay = displayAmount(init.Amount, checkout.Currency)
		partial.AvailableMethods = init.Methods
		partial.AmountStatus = amountGateStatus(init.Amount, checkout.Currency, gate)
		if err := requireAmountGate("PayPal checkout", init.Amount, checkout.Currency, gate); err != nil {
			last = err
			f.addStep("paypal.amount_gate", "retrying", fmt.Sprintf("第 %d/%d 次未满足严格零元门禁：%v；丢弃本轮 Checkout 并完整重跑", attempt, f.options.Attempts(), err), 0)
			continue
		}
		if len(init.Methods) > 0 && !containsFold(init.Methods, "paypal") {
			last = fmt.Errorf("当前 checkout 不支持 PayPal；methods=%s", strings.Join(init.Methods, ","))
			f.addStep("paypal.method", "retrying", last.Error(), 0)
			continue
		}
		billing := billingForCountry(f.options.Country, f.credential.Email)
		if err := f.updateStripeTaxRegion(f.provider, checkout, billing, nil); err != nil {
			last = err
			continue
		}
		ctx := newStripeContext(init, true)
		inline := attempt%2 == 0
		var pmID string
		var confirm map[string]any
		if inline {
			f.addStep("paypal.strategy", "running", "inline payment_method_data", 0)
			confirm, err = f.confirmInlineProvider(f.provider, checkout, init, billing, "paypal", ctx)
			pmID = findStringDeep(confirm, "payment_method", "payment_method_id")
		} else {
			f.addStep("paypal.strategy", "running", "payment_method + confirm", 0)
			pmID, err = f.createProviderPaymentMethod(f.provider, checkout, billing, "paypal", ctx)
			if err == nil {
				confirm, err = f.confirmProvider(f.provider, checkout, init, pmID, "paypal", ctx)
			}
		}
		partial.PaymentMethodID = pmID
		if err != nil {
			last = err
			f.addStep("paypal.confirm", "retrying", err.Error(), 0)
			continue
		}
		ctx.PollPhase = "first"
		redirect, err := f.resolveProviderAfterConfirm(checkout, init, confirm, pmID, "paypal", ctx, paypalRedirectPollTimeout)
		if err != nil && shouldReconfirmPayPalProviderError(err) {
			// OpenAI occasionally asks for a second confirm after approval. Re-initialize
			// the same checkout once before discarding it.
			f.addStep("paypal.reconfirm", "running", "首次 confirm/approve 未产生 BA 链，执行 re-init + reconfirm", 0)
			secondInit, initErr := f.stripeInit(f.provider, checkout, false)
			if initErr == nil {
				partial.Amount = secondInit.Amount
				partial.AmountDisplay = displayAmount(secondInit.Amount, secondInit.Currency)
				partial.AmountStatus = amountStatus(secondInit.Amount, 0)
				initErr = requireZeroAmount("PayPal re-init checkout", secondInit.Amount)
			}
			if initErr == nil {
				ctx = newStripeContext(secondInit, true)
				ctx.PollPhase = "reconfirm"
				if inline {
					confirm, initErr = f.confirmInlineProvider(f.provider, checkout, secondInit, billing, "paypal", ctx)
					// Inline confirm creates a fresh Payment Method. Poll setup errors
					// against that ID, not the stale ID from the first confirm.
					pmID = findStringDeep(confirm, "payment_method", "payment_method_id")
					partial.PaymentMethodID = pmID
				} else {
					pmID, initErr = f.createProviderPaymentMethod(f.provider, checkout, billing, "paypal", ctx)
					if initErr == nil {
						partial.PaymentMethodID = pmID
						confirm, initErr = f.confirmProvider(f.provider, checkout, secondInit, pmID, "paypal", ctx)
					}
				}
				if initErr == nil {
					redirect, initErr = f.resolveProviderAfterConfirm(checkout, secondInit, confirm, pmID, "paypal", ctx, paypalRedirectPollTimeout)
				}
			}
			// A successful reconfirm is a complete replacement for the first
			// poll failure. Leaving err untouched here discarded redirects that
			// the reconfirm poll had already obtained.
			err = reconfirmResultError(err, initErr)
		} else if err != nil {
			f.addStep("paypal.reconfirm", "warning", "当前 Checkout 已终止或任务已取消，跳过无意义的 re-init + reconfirm", 0)
		}
		if err != nil {
			last = err
			f.addStep("paypal.redirect", "retrying", err.Error(), 0)
			continue
		}
		providerURL := f.followProviderRedirect(f.provider, redirect, []string{"paypal.com"})
		baURL := extractPayPalBAURL(map[string]any{"redirect": redirect, "provider": providerURL})
		if !isStrictPayPalBAApproveURL(baURL) {
			last = errors.New("Provider redirect 未包含严格有效的 PayPal BA approve URL")
			f.addStep("paypal.ba", "retrying", last.Error(), 0)
			continue
		}
		f.addStep("paypal.ba", "success", baURL, 0)
		partial.OK = true
		partial.StripeRedirectURL = redirect
		partial.ProviderRedirectURL = baURL
		partial.LongURL = baURL
		partial.ExtractionStatus = "ba_ready"
		partial.PaymentStatus = "awaiting_paypal_approval"
		return partial, nil
	}
	if last == nil {
		last = errors.New("PayPal BA 提炼失败")
	}
	return partial, last
}

func (f *flow) runRedirectProvider(method string) (Result, error) {
	country, currency, providerType := "NL", "EUR", "ideal"
	if method == MethodGoPay {
		country, currency, providerType = "ID", "IDR", "gopay"
	}
	if method == MethodPIX {
		country, currency, providerType = "BR", "BRL", "pix"
	}
	if method == MethodBLIK {
		country, currency, providerType = "PL", "PLN", "blik"
	}
	if method == MethodTWINT {
		country, currency, providerType = "CH", "CHF", "twint"
	}
	var last error
	var partial Result
	businessAttempt := 1
	transportRound := 1
	networkRetries := 0
	for businessAttempt <= f.options.Attempts() {
		attempt := businessAttempt
		selected := SelectAttemptOptions(f.options, transportRound)
		fingerprints := []string{"chrome146", "chrome136", "chrome"}
		selected.ClientFingerprint = fingerprints[(transportRound-1)%len(fingerprints)]
		stage, stageErr := ResolveStageProxies(f.engine.Config, selected, method)
		if stageErr != nil {
			last = stageErr
			f.addStep(providerType+".attempt", "retrying", fmt.Sprintf("第 %d/%d 次代理准备失败：%v", attempt, f.options.Attempts(), stageErr), 0)
			businessAttempt++
			transportRound++
			continue
		}
		if err := f.rebuildClients(selected, stage); err != nil {
			last = err
			f.addStep(providerType+".attempt", "retrying", fmt.Sprintf("第 %d/%d 次客户端准备失败：%v", attempt, f.options.Attempts(), err), 0)
			businessAttempt++
			transportRound++
			continue
		}
		f.addStep(providerType+".attempt", "running", fmt.Sprintf("第 %d/%d 次完整提炼；网络重试=%d（不计次）；Checkout=%s；Promotion=%s；fingerprint=%s", attempt, f.options.Attempts(), networkRetries, stage.Labels["checkout"], stage.Labels["promotion"], f.fingerprint.Name), 0)

		checkout, err := f.createCheckout(f.checkout, country, currency, "custom", true, 0)
		if err != nil {
			last = err
			if isAlreadyPaidError(err) {
				partial = alreadyPaidResult(country, currency, err.Error())
				f.addStep(providerType+".attempt", "failed", "账号已付费，停止后续流程", 0)
				return partial, last
			}
			if isProviderNetworkRetry(err) {
				networkRetries++
				transportRound++
				f.addStep(providerType+".network_retry", "retrying", fmt.Sprintf("业务尝试仍为 %d/%d；网络重试 %d：创建 checkout 失败：%v", attempt, f.options.Attempts(), networkRetries, err), 0)
				continue
			}
			f.addStep(providerType+".attempt", "retrying", fmt.Sprintf("第 %d/%d 次创建 checkout 失败：%v", attempt, f.options.Attempts(), err), 0)
			businessAttempt++
			transportRound++
			continue
		}
		partial = Result{Country: country, Currency: currency, CheckoutID: checkout.ID, CheckoutType: checkoutIDType(checkout.ID), ProcessorEntity: checkout.ProcessorEntity, Metadata: checkoutObservationMetadata(checkout, nil)}
		if !stripeCheckoutIDPattern.MatchString(checkout.ID) {
			last = fmt.Errorf("%s 提链类型校验失败：返回 %s，期望 cs_live_/cs_test_；observed=%s", providerType, trimDetail(checkout.ID, 48), checkoutObservation(checkout.StripeID, checkout.OpenAIID))
			f.addStep(providerType+".checkout_type", "retrying", fmt.Sprintf("第 %d/%d 次：%v；OAICS 适合 ChatGPT Checkout 交接，但不能作为 %s provider 支付页", attempt, f.options.Attempts(), last, providerType), 0)
			businessAttempt++
			transportRound++
			continue
		}
		if err := f.promotion.CopyCookiesFrom(f.checkout, f.engine.Endpoints.ChatGPT); err != nil {
			last = fmt.Errorf("同步 Checkout Cookie 到 Promotion: %w", err)
			f.addStep(providerType+".attempt", "retrying", fmt.Sprintf("第 %d/%d 次会话同步失败：%v", attempt, f.options.Attempts(), last), 0)
			businessAttempt++
			transportRound++
			continue
		}
		if _, err := f.updatePromotion(f.promotion, checkout); err != nil {
			last = err
			if isProviderNetworkRetry(err) {
				networkRetries++
				transportRound++
				f.addStep(providerType+".network_retry", "retrying", fmt.Sprintf("业务尝试仍为 %d/%d；网络重试 %d：优惠更新失败：%v", attempt, f.options.Attempts(), networkRetries, err), 0)
				continue
			}
			f.addStep(providerType+".attempt", "retrying", fmt.Sprintf("第 %d/%d 次优惠更新失败：%v", attempt, f.options.Attempts(), err), 0)
			businessAttempt++
			transportRound++
			continue
		}
		_ = f.provider.CopyCookiesFrom(f.checkout, f.engine.Endpoints.ChatGPT)
		_ = f.provider.CopyCookiesFrom(f.promotion, f.engine.Endpoints.ChatGPT)
		_ = f.approve.CopyCookiesFrom(f.checkout, f.engine.Endpoints.ChatGPT)
		_ = f.approve.CopyCookiesFrom(f.promotion, f.engine.Endpoints.ChatGPT)
		init, err := f.stripeInit(f.provider, checkout, true)
		if err != nil {
			last = err
			if isProviderNetworkRetry(err) {
				networkRetries++
				transportRound++
				f.addStep(providerType+".network_retry", "retrying", fmt.Sprintf("业务尝试仍为 %d/%d；网络重试 %d：Stripe 初始化失败：%v", attempt, f.options.Attempts(), networkRetries, err), 0)
				continue
			}
			f.addStep(providerType+".attempt", "retrying", fmt.Sprintf("第 %d/%d 次 Stripe 初始化失败：%v", attempt, f.options.Attempts(), err), 0)
			businessAttempt++
			transportRound++
			continue
		}
		partial.Amount, partial.AmountDisplay = init.Amount, checkoutAmountDisplay(init.Amount, currency)
		zeroGate := amountGate{Mode: AmountGateStrictZero}
		partial.AmountStatus, partial.AvailableMethods = amountGateStatus(init.Amount, currency, zeroGate), init.Methods
		acceptedMethod := containsFold(init.Methods, providerType)
		if providerType == "gopay" {
			acceptedMethod = acceptedMethod || containsFold(init.Methods, "grabpay")
		}
		if len(init.Methods) > 0 && !acceptedMethod {
			last = fmt.Errorf("checkout 不支持 %s；methods=%s", providerType, strings.Join(init.Methods, ","))
			f.addStep(providerType+".method", "retrying", fmt.Sprintf("第 %d/%d 次：%v", attempt, f.options.Attempts(), last), 0)
			businessAttempt++
			transportRound++
			continue
		}
		if err := requireAmountGate(providerType+" checkout", init.Amount, currency, zeroGate); err != nil {
			last = err
			f.addStep(providerType+".amount_gate", "retrying", fmt.Sprintf("第 %d/%d 次金额为 %s，未满足严格零元门禁；丢弃本轮并完整重跑", attempt, f.options.Attempts(), checkoutAmountDisplay(init.Amount, currency)), 0)
			businessAttempt++
			transportRound++
			continue
		}
		billing := billingForCountry(country, f.credential.Email)
		ctx := newStripeContext(init, false)
		pmID, err := f.createProviderPaymentMethod(f.provider, checkout, billing, providerType, ctx)
		if err != nil {
			last = err
			if isProviderNetworkRetry(err) {
				networkRetries++
				transportRound++
				f.addStep(providerType+".network_retry", "retrying", fmt.Sprintf("业务尝试仍为 %d/%d；网络重试 %d：创建支付方式失败：%v", attempt, f.options.Attempts(), networkRetries, err), 0)
				continue
			}
			f.addStep(providerType+".attempt", "retrying", fmt.Sprintf("第 %d/%d 次创建支付方式失败：%v", attempt, f.options.Attempts(), err), 0)
			businessAttempt++
			transportRound++
			continue
		}
		partial.PaymentMethodID = pmID
		confirm, err := f.confirmProvider(f.provider, checkout, init, pmID, providerType, ctx)
		if err != nil {
			last = err
			if isProviderNetworkRetry(err) {
				networkRetries++
				transportRound++
				f.addStep(providerType+".network_retry", "retrying", fmt.Sprintf("业务尝试仍为 %d/%d；网络重试 %d：Provider confirm 失败：%v", attempt, f.options.Attempts(), networkRetries, err), 0)
				continue
			}
			f.addStep(providerType+".attempt", "retrying", fmt.Sprintf("第 %d/%d 次 Provider confirm 失败：%v", attempt, f.options.Attempts(), err), 0)
			businessAttempt++
			transportRound++
			continue
		}
		paymentMaterial := extractPaymentMaterial(confirm, providerType)
		var redirect string
		if providerType == "pix" && (paymentMaterial.Payload != "" || paymentMaterial.Instruction != "" || paymentMaterial.PNG != "" || paymentMaterial.SVG != "") {
			redirect = paymentMaterial.Instruction
		} else {
			redirect, err = f.resolveProviderAfterConfirm(checkout, init, confirm, pmID, providerType, ctx, 35*time.Second)
		}
		if err != nil {
			last = err
			if isProviderNetworkRetry(err) {
				networkRetries++
				transportRound++
				f.addStep(providerType+".network_retry", "retrying", fmt.Sprintf("业务尝试仍为 %d/%d；网络重试 %d：解析跳转失败：%v", attempt, f.options.Attempts(), networkRetries, err), 0)
				continue
			}
			if isProviderGenericDecline(err) {
				partial.ExtractionStatus = "payment_method_declined"
				partial.PaymentStatus = "declined"
				f.addStep(providerType+".payment_declined", "retrying", fmt.Sprintf("第 %d/%d 次：Stripe 拒绝本轮 Payment Method；不再轮询当前 pm_，丢弃本轮 Checkout/pm_ 并完整重跑：%v", attempt, f.options.Attempts(), err), 0)
				businessAttempt++
				transportRound++
				continue
			}
			if isProviderApprovalBlocked(err) {
				partial.ExtractionStatus = "approval_blocked"
				partial.PaymentStatus = "blocked"
				f.addStep(providerType+".approval_blocked", "retrying", fmt.Sprintf("第 %d/%d 次：本轮 Approve 重试已耗尽；丢弃本轮 Checkout/pm_ 并完整重跑：%v", attempt, f.options.Attempts(), err), 0)
				businessAttempt++
				transportRound++
				continue
			}
			f.addStep(providerType+".attempt", "retrying", fmt.Sprintf("第 %d/%d 次解析跳转失败：%v", attempt, f.options.Attempts(), err), 0)
			businessAttempt++
			transportRound++
			continue
		}
		preferred := []string{}
		if providerType == "ideal" {
			preferred = []string{"pay.ideal.nl", "ideal.nl"}
		} else if providerType == "gopay" {
			preferred = []string{"gopay.co.id", "gojek.com", "midtrans.com"}
		} else if providerType == "twint" {
			preferred = []string{"twint.ch", "stripe.com"}
		} else if providerType == "blik" {
			preferred = []string{"blik.com", "stripe.com"}
		} else {
			preferred = []string{"stripe.com", "payments.stripe.com", "qr.stripe.com"}
		}
		providerURL := f.followProviderRedirect(f.provider, redirect, preferred)
		partial.OK = true
		partial.StripeRedirectURL = redirect
		partial.ProviderRedirectURL = providerURL
		partial.LongURL = firstNonEmpty(providerURL, redirect, paymentMaterial.Instruction, paymentMaterial.Payload)
		if providerType == "pix" {
			partial.PaymentPayload = paymentMaterial.Payload
			partial.PaymentInstructionURL = paymentMaterial.Instruction
			partial.QRPNGURL = paymentMaterial.PNG
			partial.QRSVGURL = paymentMaterial.SVG
			partial.Metadata = map[string]any{"paymentMethod": "pix", "paymentPayload": paymentMaterial.Payload, "paymentInstructionUrl": paymentMaterial.Instruction, "qrPngUrl": paymentMaterial.PNG, "qrSvgUrl": paymentMaterial.SVG}
		}
		if providerType == "blik" {
			partial.Metadata = map[string]any{"paymentMethod": "blik", "blikCodeProvided": strings.TrimSpace(f.options.BlikCode) != ""}
		}
		if providerType == "twint" {
			partial.Metadata = map[string]any{"paymentMethod": "twint"}
		}
		partial.ExtractionStatus = "provider_link_ready"
		partial.PaymentStatus = "awaiting_payment"
		if providerType == "pix" {
			partial.ExtractionStatus = "pix_ready"
			partial.PaymentStatus = "awaiting_pix_payment"
		}
		if providerType == "blik" {
			partial.PaymentStatus = "awaiting_blik_payment"
		}
		if providerType == "twint" {
			partial.PaymentStatus = "awaiting_twint_payment"
		}
		return partial, nil
	}
	if last == nil {
		last = fmt.Errorf("%s provider 提炼失败", providerType)
	}
	f.addStep(providerType+".attempt", "failed", fmt.Sprintf("已完成 %d 次完整链路尝试，仍未获得可用结果；最后结果：%v", f.options.Attempts(), last), 0)
	return partial, last
}

func isProviderNetworkRetry(err error) bool {
	if err == nil || errors.Is(err, context.Canceled) {
		return false
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return true
	}
	detail := strings.ToLower(err.Error())
	for _, marker := range []string{
		"eof", "connection reset", "connection refused", "broken pipe", "unexpected end of file",
		"i/o timeout", "client.timeout", "context deadline exceeded", "tls handshake", "ssl", "proxyconnect", "proxy error",
		"server gave http response to https client", "no such host", "temporary failure", "network is unreachable",
		"http 408", "http 425", "http 429", "http 500", "http 502", "http 503", "http 504",
	} {
		if strings.Contains(detail, marker) {
			return true
		}
	}
	return false
}

func isProviderGenericDecline(err error) bool {
	if err == nil {
		return false
	}
	detail := strings.ToLower(err.Error())
	return strings.Contains(detail, "setup_attempt_failed") && strings.Contains(detail, "generic_decline")
}

func isProviderApprovalBlocked(err error) bool {
	if err == nil {
		return false
	}
	return strings.Contains(strings.ToLower(err.Error()), "approve result=blocked")
}

func (f *flow) updateStripeTaxRegion(client *BrowserClient, checkout checkoutData, billing billingAddress, extra url.Values) error {
	stage := "stripe.tax_region"
	forms := []url.Values{{
		"key":                 {checkout.PublishableKey},
		"tax_region[country]": {billing.Country},
	}}
	if billing.State != "" {
		forms = append(forms, url.Values{
			"key":                     {checkout.PublishableKey},
			"tax_region[country]":     {billing.Country},
			"tax_region[state]":       {billing.State},
			"tax_region[postal_code]": {billing.PostalCode},
			"tax_region[line1]":       {billing.Line1},
			"tax_region[city]":        {billing.City},
		})
	}
	for _, form := range forms {
		for key, values := range extra {
			for _, value := range values {
				form.Add(key, value)
			}
		}
		started := time.Now()
		response, err := client.Form(f.ctx, http.MethodPost, f.engine.Endpoints.Stripe+"/v1/payment_pages/"+url.PathEscape(checkout.ID), stripeHeaders(checkout, f.profile), form)
		if err != nil {
			return err
		}
		if response.Status >= 400 {
			return upstreamError(stage, response)
		}
		f.addStep(stage, "success", "country="+billing.Country, time.Since(started))
	}
	return nil
}

func (f *flow) createProviderPaymentMethod(client *BrowserClient, checkout checkoutData, billing billingAddress, providerType string, ctx stripeContext) (string, error) {
	stage := "stripe.payment_method"
	form := url.Values{
		"type":                                  {providerType},
		"billing_details[name]":                 {billing.Name},
		"billing_details[email]":                {billing.Email},
		"billing_details[address][country]":     {billing.Country},
		"billing_details[address][line1]":       {billing.Line1},
		"billing_details[address][city]":        {billing.City},
		"billing_details[address][postal_code]": {billing.PostalCode},
		"key":                                   {checkout.PublishableKey},
		"client_attribution_metadata[checkout_session_id]": {checkout.ID},
	}
	if billing.Line2 != "" {
		form.Set("billing_details[address][line2]", billing.Line2)
	}
	if billing.State != "" {
		form.Set("billing_details[address][state]", billing.State)
	}
	if providerType == "pix" {
		form.Set("billing_details[address][state]", firstNonEmpty(billing.State, "SP"))
		form.Set("billing_details[tax_id]", generateBrazilCPF())
		form.Set("payment_user_agent", fmt.Sprintf("stripe.js/%s; stripe-js-v3/%s; payment-element; deferred-intent", ctx.RuntimeVersion, ctx.RuntimeVersion))
		form.Set("referrer", "https://chatgpt.com")
		form.Set("time_on_page", "360000")
	}
	if providerType == "blik" && strings.TrimSpace(f.options.BlikCode) != "" {
		form.Set("blik[code]", strings.TrimSpace(f.options.BlikCode))
	}
	if providerType == "paypal" || providerType == "kakao_pay" {
		form.Set("guid", ctx.GUID)
		form.Set("muid", ctx.MUID)
		form.Set("sid", ctx.SID)
		form.Set("_stripe_version", ctx.StripeVersion)
		form.Set("payment_user_agent", fmt.Sprintf("stripe.js/%s; stripe-js-v3/%s; checkout", ctx.RuntimeVersion, ctx.RuntimeVersion))
		form.Set("client_attribution_metadata[client_session_id]", ctx.ClientSessionID)
		form.Set("client_attribution_metadata[merchant_integration_source]", "checkout")
		form.Set("client_attribution_metadata[merchant_integration_version]", "custom_checkout")
		selectionFlow := "automatic"
		if providerType == "kakao_pay" {
			selectionFlow = "merchant_specified"
			form.Set("billing_details[address][line2]", billing.Line2)
		}
		form.Set("client_attribution_metadata[payment_method_selection_flow]", selectionFlow)
		if ctx.CheckoutConfigID != "" {
			form.Set("client_attribution_metadata[checkout_config_id]", ctx.CheckoutConfigID)
		}
	}
	started := time.Now()
	response, err := client.Form(f.ctx, http.MethodPost, f.engine.Endpoints.Stripe+"/v1/payment_methods", stripeHeaders(checkout, f.profile), form)
	if err != nil {
		return "", err
	}
	if response.Status >= 400 {
		return "", upstreamError(stage, response)
	}
	var body map[string]any
	if response.JSON(&body) != nil {
		return "", errors.New("payment_methods 返回的不是 JSON")
	}
	pmID := findStringDeep(body, "id")
	if !strings.HasPrefix(pmID, "pm_") {
		return "", errors.New("payment_methods 响应缺少 pm id")
	}
	f.addStep(stage, "success", pmID, time.Since(started))
	return pmID, nil
}

func (f *flow) confirmProvider(client *BrowserClient, checkout checkoutData, init stripeInit, pmID, providerType string, ctx stripeContext) (map[string]any, error) {
	stage := "stripe.confirm"
	returnURL := f.providerReturnURL(checkout, providerType)
	form := url.Values{
		"eid": {"NA"}, "payment_method": {pmID},
		"expected_amount":              {firstNonEmpty(init.Amount, "0")},
		"expected_payment_method_type": {providerType},
		"return_url":                   {returnURL},
		"_stripe_version":              {ctx.StripeVersion},
		"guid":                         {ctx.GUID}, "muid": {ctx.MUID}, "sid": {ctx.SID},
		"key": {checkout.PublishableKey}, "version": {ctx.RuntimeVersion},
		"init_checksum": {ctx.InitChecksum},
		"client_attribution_metadata[client_session_id]":             {ctx.ClientSessionID},
		"client_attribution_metadata[checkout_session_id]":           {checkout.ID},
		"client_attribution_metadata[merchant_integration_source]":   {"checkout"},
		"client_attribution_metadata[merchant_integration_version]":  {"custom_checkout"},
		"client_attribution_metadata[payment_method_selection_flow]": {"automatic"},
		"client_attribution_metadata[checkout_config_id]":            {ctx.CheckoutConfigID},
		"link_brand": {"link"},
	}
	if providerType == "kakao_pay" {
		form.Set("tax_id_collection[purchasing_as_business]", "false")
		form.Set("client_attribution_metadata[payment_method_selection_flow]", "merchant_specified")
		for key, values := range f.elementsSessionParams(ctx, true) {
			for _, value := range values {
				form.Add(key, value)
			}
		}
	}
	if providerType == "blik" {
		for key, values := range f.elementsSessionParams(ctx, true) {
			for _, value := range values {
				form.Add(key, value)
			}
		}
		form.Set("client_attribution_metadata[merchant_integration_subtype]", "payment-element")
		form.Set("client_attribution_metadata[payment_intent_creation_flow]", "deferred")
		if code := strings.TrimSpace(f.options.BlikCode); code != "" {
			form.Set("blik_code", code)
		}
	}
	if providerType == "pix" || providerType == "twint" {
		for key, values := range f.elementsSessionParams(ctx, true) {
			for _, value := range values {
				form.Add(key, value)
			}
		}
		form.Set("client_attribution_metadata[merchant_integration_subtype]", "payment-element")
		form.Set("client_attribution_metadata[payment_intent_creation_flow]", "deferred")
	}
	if ctx.CheckoutConfigID == "" {
		form.Del("client_attribution_metadata[checkout_config_id]")
	}
	started := time.Now()
	response, err := client.Form(f.ctx, http.MethodPost, f.engine.Endpoints.Stripe+"/v1/payment_pages/"+url.PathEscape(checkout.ID)+"/confirm", stripeHeaders(checkout, f.profile), form)
	if err != nil {
		return nil, err
	}
	body := decodeLooseJSON(response.Body)
	if baURL := extractPayPalBAURL(map[string]any{"body": string(response.Body), "json": body}); baURL != "" {
		body["_ba_approve_url"] = baURL
	}
	if response.Status >= 400 && body["_ba_approve_url"] == nil {
		return body, upstreamError(stage, response)
	}
	f.addStep(stage, "success", fmt.Sprintf("provider=%s", providerType), time.Since(started))
	return body, nil
}

func (f *flow) confirmInlineProvider(client *BrowserClient, checkout checkoutData, init stripeInit, billing billingAddress, providerType string, ctx stripeContext) (map[string]any, error) {
	stage := "stripe.confirm_inline"
	form := f.elementsBaseForm(checkout, ctx, true)
	form.Set("payment_method_data[type]", providerType)
	form.Set("payment_method_data[billing_details][name]", billing.Name)
	form.Set("payment_method_data[billing_details][email]", billing.Email)
	form.Set("payment_method_data[billing_details][address][country]", billing.Country)
	form.Set("payment_method_data[billing_details][address][line1]", billing.Line1)
	form.Set("payment_method_data[billing_details][address][city]", billing.City)
	form.Set("payment_method_data[billing_details][address][postal_code]", billing.PostalCode)
	form.Set("payment_method_data[billing_details][address][state]", billing.State)
	form.Set("expected_amount", firstNonEmpty(init.Amount, "0"))
	form.Set("expected_payment_method_type", providerType)
	form.Set("return_url", f.providerReturnURL(checkout, providerType))
	form.Set("version", ctx.RuntimeVersion)
	form.Set("init_checksum", ctx.InitChecksum)
	form.Set("guid", ctx.GUID)
	form.Set("muid", ctx.MUID)
	form.Set("sid", ctx.SID)
	form.Set("key", checkout.PublishableKey)
	started := time.Now()
	response, err := client.Form(f.ctx, http.MethodPost, f.engine.Endpoints.Stripe+"/v1/payment_pages/"+url.PathEscape(checkout.ID)+"/confirm", stripeHeaders(checkout, f.profile), form)
	if err != nil {
		return nil, err
	}
	body := decodeLooseJSON(response.Body)
	if baURL := extractPayPalBAURL(map[string]any{"body": string(response.Body), "json": body}); baURL != "" {
		body["_ba_approve_url"] = baURL
	}
	if response.Status >= 400 && body["_ba_approve_url"] == nil {
		return body, upstreamError(stage, response)
	}
	f.addStep(stage, "success", providerType, time.Since(started))
	return body, nil
}

func (f *flow) elementsSessionParams(ctx stripeContext, save bool) url.Values {
	enable := "never"
	if save {
		enable = "auto"
	}
	return url.Values{
		"elements_session_client[client_betas][0]":                        {"custom_checkout_server_updates_1"},
		"elements_session_client[client_betas][1]":                        {"custom_checkout_manual_approval_1"},
		"elements_session_client[elements_init_source]":                   {"custom_checkout"},
		"elements_session_client[referrer_host]":                          {"chatgpt.com"},
		"elements_session_client[session_id]":                             {ctx.ElementsSessionID},
		"elements_session_client[stripe_js_id]":                           {ctx.StripeJSID},
		"elements_session_client[locale]":                                 {f.profile.Elements},
		"elements_session_client[is_aggregation_expected]":                {"false"},
		"elements_options_client[saved_payment_method][enable_save]":      {enable},
		"elements_options_client[saved_payment_method][enable_redisplay]": {enable},
	}
}

func (f *flow) elementsBaseForm(checkout checkoutData, ctx stripeContext, save bool) url.Values {
	form := f.elementsSessionParams(ctx, save)
	form.Set("_stripe_version", ctx.StripeVersion)
	for key, value := range map[string]string{
		"client_attribution_metadata[client_session_id]":             ctx.ClientSessionID,
		"client_attribution_metadata[checkout_session_id]":           checkout.ID,
		"client_attribution_metadata[checkout_config_id]":            ctx.CheckoutConfigID,
		"client_attribution_metadata[elements_session_id]":           ctx.ElementsSessionID,
		"client_attribution_metadata[elements_session_config_id]":    ctx.ElementsSessionConfigID,
		"client_attribution_metadata[merchant_integration_source]":   "checkout",
		"client_attribution_metadata[merchant_integration_subtype]":  "payment-element",
		"client_attribution_metadata[merchant_integration_version]":  "custom",
		"client_attribution_metadata[payment_intent_creation_flow]":  "deferred",
		"client_attribution_metadata[payment_method_selection_flow]": "automatic",
	} {
		form.Set(key, value)
	}
	return form
}

func (f *flow) providerReturnURL(checkout checkoutData, providerType string) string {
	if providerType == "kakao_pay" || providerType == "kakao" {
		successURL := fmt.Sprintf("%s/backend-api/payments/checkout/%s/%s/success?billing_country=KR", f.engine.Endpoints.ChatGPT, url.PathEscape(checkout.ProcessorEntity), url.PathEscape(checkout.ID))
		return fmt.Sprintf("https://checkout.stripe.com/c/pay/%s?returned_from_redirect=true&ui_mode=custom&return_url=%s", url.PathEscape(checkout.ID), url.QueryEscape(successURL))
	}
	verify := fmt.Sprintf("%s/checkout/verify?stripe_session_id=%s&processor_entity=%s&plan_type=plus", f.engine.Endpoints.ChatGPT, url.QueryEscape(checkout.ID), url.QueryEscape(checkout.ProcessorEntity))
	result := fmt.Sprintf("https://pay.openai.com/c/pay/%s?returned_from_redirect=true&ui_mode=custom&return_url=%s", url.PathEscape(checkout.ID), url.QueryEscape(verify))
	if providerType == "paypal" {
		result += "&redirect_pm_type=paypal&lid=" + url.QueryEscape(newUUID())
	}
	return result
}

func (f *flow) resolveProviderAfterConfirm(checkout checkoutData, init stripeInit, confirm map[string]any, pmID, providerType string, ctx stripeContext, timeout time.Duration) (string, error) {
	return f.resolveProviderAfterConfirmWith(f.provider, f.approve, true, checkout, init, confirm, pmID, providerType, ctx, timeout)
}

func (f *flow) resolveProviderAfterConfirmWith(providerClient, approveClient *BrowserClient, sendSentinel bool, checkout checkoutData, init stripeInit, confirm map[string]any, pmID, providerType string, ctx stripeContext, timeout time.Duration) (string, error) {
	manualApproval, _ := boolDeep(checkout.Raw, "requires_manual_approval")
	if requiresApproval(confirm) || ((strings.EqualFold(providerType, "kakao") || strings.EqualFold(providerType, "kakao_pay")) && manualApproval) {
		f.addStep(providerType+".approve", "running", "confirm 返回 requires_approval", 0)
		approveAttempts := f.options.ApproveRetries()
		if err := f.approveCheckoutWith(approveClient, checkout, approveAttempts, sendSentinel); err != nil {
			// Kakao-like near-success behavior: blocked approve can still expose a
			// provider redirect shortly after. Probe briefly before giving up.
			if strings.EqualFold(providerType, "paypal") || strings.EqualFold(providerType, "kakao") || strings.EqualFold(providerType, "kakao_pay") {
				f.addStep(providerType+".approve", "warning", trimDetail(fmt.Sprintf("%v；仍尝试短轮询 redirect", err), 220), 0)
				shortTimeout := 12 * time.Second
				if strings.EqualFold(providerType, "paypal") {
					shortTimeout = 25 * time.Second
				}
				if timeout > 0 && timeout < shortTimeout {
					shortTimeout = timeout
				}
				if redirect, pollErr := f.pollProviderRedirect(providerClient, checkout, pmID, providerType, ctx, shortTimeout); pollErr == nil && strings.TrimSpace(redirect) != "" {
					f.addStep(providerType+".approve", "success", "blocked 后短轮询仍拿到 redirect", 0)
					return redirect, nil
				}
			}
			return "", err
		}
		return f.pollProviderRedirect(providerClient, checkout, pmID, providerType, ctx, timeout)
	}
	if redirect := extractRedirectURL(confirm); providerRedirectMatches(redirect, providerType) {
		return redirect, nil
	}
	return f.pollProviderRedirect(providerClient, checkout, pmID, providerType, ctx, timeout)
}

func (f *flow) pollProviderRedirect(client *BrowserClient, checkout checkoutData, pmID, providerType string, ctx stripeContext, timeout time.Duration) (string, error) {
	stage := "stripe.poll"
	if strings.EqualFold(providerType, "kakao") || strings.EqualFold(providerType, "kakao_pay") {
		stage = "stripe.redirect_poll"
	}
	startedAt := time.Now()
	deadline := startedAt.Add(timeout)
	params := f.providerPollParams(checkout, providerType, ctx)
	last := ""
	setupWarnCount := 0
	terminalSetupErrorCount := 0
	var lastSetupWarnAt time.Time
	pollDetail := fmt.Sprintf("最长 %ds", int(timeout.Seconds()))
	if strings.EqualFold(providerType, "paypal") {
		switch strings.ToLower(strings.TrimSpace(ctx.PollPhase)) {
		case "first":
			pollDetail = fmt.Sprintf("阶段=首次 confirm；最长 %ds", int(timeout.Seconds()))
		case "reconfirm":
			pollDetail = fmt.Sprintf("阶段=reconfirm；最长 %ds", int(timeout.Seconds()))
		}
	}
	f.addStep(stage, "running", pollDetail, 0)
	for time.Now().Before(deadline) {
		response, err := client.Do(f.ctx, http.MethodGet, f.engine.Endpoints.Stripe+"/v1/payment_pages/"+url.PathEscape(checkout.ID)+"?"+params.Encode(), stripeHeaders(checkout, f.profile), nil, true)
		if err == nil {
			body := decodeLooseJSON(response.Body)
			if isCheckoutNotActiveProviderText(string(response.Body)) {
				return "", fmt.Errorf("checkout_not_active_session（已等待 %.1fs；当前 Checkout Session 已失效，结束本轮）", time.Since(startedAt).Seconds())
			}
			if baURL := extractPayPalBAURL(map[string]any{"body": string(response.Body), "json": body}); baURL != "" {
				f.addStep(stage, "success", baURL, 0)
				return baURL, nil
			}
			if response.Status >= 400 {
				last = upstreamError(stage, response).Error()
			} else if redirect := extractRedirectURL(body); providerRedirectMatches(redirect, providerType) {
				f.addStep(stage, "success", redirect, 0)
				return redirect, nil
			} else if setupErr := setupIntentError(body, pmID); setupErr != "" {
				// After OpenAI approve, Stripe may briefly expose last_setup_error while
				// the provider redirect is still materializing. Keep polling for BA/Kakao.
				if strings.EqualFold(providerType, "paypal") || strings.EqualFold(providerType, "kakao") || strings.EqualFold(providerType, "kakao_pay") {
					setupWarnCount++
					// Avoid flooding the timeline: first hit + periodic heartbeat only.
					if setupWarnCount == 1 || time.Since(lastSetupWarnAt) >= 8*time.Second {
						f.addStep(stage, "warning", trimDetail(fmt.Sprintf("%s setup error 暂忽略并继续轮询 redirect（x%d）: %s", providerType, setupWarnCount, setupErr), 220), 0)
						lastSetupWarnAt = time.Now()
					}
					last = setupErr
					terminalSetupErrorCount = nextPayPalTerminalSetupErrorCount(providerType, setupErr, terminalSetupErrorCount)
					if terminalSetupErrorCount >= paypalTerminalSetupErrorPolls {
						return "", fmt.Errorf("%s（已等待 %.1fs；当前 Payment Method 已观察到 %d 次 setup_attempt_failed，结束本轮轮询）", setupErr, time.Since(startedAt).Seconds(), terminalSetupErrorCount)
					}
				} else {
					return "", errors.New(setupErr)
				}
			} else {
				last = "keys=" + strings.Join(mapKeys(body), ",")
			}
		} else if err != nil {
			last = err.Error()
		} else {
			last = upstreamError(stage, response).Error()
		}
		select {
		case <-f.ctx.Done():
			return "", f.ctx.Err()
		case <-time.After(800 * time.Millisecond):
		}
	}
	return "", fmt.Errorf("Provider redirect 轮询超时（已等待 %.1fs）: %s", time.Since(startedAt).Seconds(), last)
}

func isCheckoutNotActiveProviderText(detail string) bool {
	normalized := strings.ToLower(strings.TrimSpace(detail))
	return strings.Contains(normalized, "checkout_not_active_session") ||
		strings.Contains(normalized, "checkout session is no longer active") ||
		strings.Contains(normalized, "session no longer active")
}

func shouldReconfirmPayPalProviderError(err error) bool {
	if err == nil || errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return false
	}
	return !isCheckoutNotActiveProviderText(err.Error())
}

func reconfirmResultError(_ error, reconfirmErr error) error {
	return reconfirmErr
}

func isTerminalPayPalSetupError(detail string) bool {
	normalized := strings.ToLower(detail)
	return strings.Contains(normalized, `"code":"setup_attempt_failed"`) &&
		strings.Contains(normalized, `"decline_code":"generic_decline"`)
}

func nextPayPalTerminalSetupErrorCount(providerType, detail string, previous int) int {
	if !strings.EqualFold(strings.TrimSpace(providerType), "paypal") || !isTerminalPayPalSetupError(detail) {
		return 0
	}
	if previous < 0 {
		previous = 0
	}
	return previous + 1
}

func providerRedirectMatches(rawURL, providerType string) bool {
	parsed, err := url.Parse(strings.TrimSpace(rawURL))
	if err != nil || parsed.Scheme == "" || parsed.Hostname() == "" {
		return false
	}
	host := strings.ToLower(parsed.Hostname())
	path := strings.ToLower(parsed.EscapedPath())
	switch strings.ToLower(strings.TrimSpace(providerType)) {
	case "paypal":
		if host == "paypal.com" || strings.HasSuffix(host, ".paypal.com") {
			return true
		}
		return host == "pm-redirects.stripe.com" && !strings.Contains(path, "/redirect/complete")
	case "kakao", "kakao_pay":
		return host == "pm-redirects.stripe.com" || host == "nicepay.co.kr" || strings.HasSuffix(host, ".nicepay.co.kr") ||
			host == "kakaopay.com" || strings.HasSuffix(host, ".kakaopay.com") || host == "kakao.com" || strings.HasSuffix(host, ".kakao.com")
	case "ideal":
		return host == "pm-redirects.stripe.com" || host == "ideal.nl" || strings.HasSuffix(host, ".ideal.nl")
	case "gopay", "grabpay":
		return host == "pm-redirects.stripe.com" || host == "gopay.co.id" || strings.HasSuffix(host, ".gopay.co.id") ||
			host == "gojek.com" || strings.HasSuffix(host, ".gojek.com") || host == "midtrans.com" || strings.HasSuffix(host, ".midtrans.com")
	case "pix":
		return host == "pm-redirects.stripe.com" || host == "payments.stripe.com" || host == "qr.stripe.com" || strings.HasSuffix(host, ".stripe.com")
	case "blik":
		return host == "pm-redirects.stripe.com" || host == "blik.com" || strings.HasSuffix(host, ".blik.com") || strings.HasSuffix(host, ".stripe.com")
	case "twint":
		return host == "pm-redirects.stripe.com" || host == "twint.ch" || strings.HasSuffix(host, ".twint.ch") || strings.HasSuffix(host, ".stripe.com")
	default:
		return true
	}
}

type paymentMaterial struct {
	Payload     string
	Instruction string
	PNG         string
	SVG         string
}

func extractPaymentMaterial(value any, providerType string) paymentMaterial {
	material := paymentMaterial{}
	want := strings.ToLower(strings.TrimSpace(providerType))
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
			lower := strings.ToLower(text)
			lowerKey := strings.ToLower(key)
			if want == "pix" && (strings.HasPrefix(lower, "000201") || strings.HasPrefix(lower, "upi://pay")) && material.Payload == "" {
				material.Payload = text
			}
			if (strings.Contains(lowerKey, "instruction") || strings.Contains(lowerKey, "hosted") || strings.Contains(lowerKey, "redirect")) && validProviderMaterialURL(text, want) && material.Instruction == "" {
				material.Instruction = text
			}
			if strings.Contains(lowerKey, "png") && validProviderMaterialURL(text, want) && material.PNG == "" {
				material.PNG = text
			}
			if strings.Contains(lowerKey, "svg") && validProviderMaterialURL(text, want) && material.SVG == "" {
				material.SVG = text
			}
		}
	}
	walk(value, "", "")
	return material
}

func validProviderMaterialURL(raw, providerType string) bool {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Hostname() == "" {
		return false
	}
	host := strings.ToLower(parsed.Hostname())
	path := strings.ToLower(parsed.EscapedPath() + "?" + parsed.RawQuery)
	if host == "js.stripe.com" || strings.Contains(path, "icon-pm-") || strings.Contains(path, "/payment-methods/") {
		return false
	}
	if providerType == "pix" {
		return host == "qr.stripe.com" || host == "payments.stripe.com" || strings.Contains(path, "pix") || strings.Contains(path, "qr")
	}
	if providerType == "twint" {
		return strings.Contains(host, "twint") || strings.Contains(path, "twint") || strings.Contains(path, "redirect")
	}
	if providerType == "blik" {
		return strings.Contains(host, "blik") || strings.Contains(path, "blik") || strings.Contains(path, "redirect")
	}
	return true
}

func (f *flow) providerPollParams(checkout checkoutData, providerType string, ctx stripeContext) url.Values {
	if strings.EqualFold(providerType, "paypal") {
		// PayPal uses the legacy non-Elements init contract. The payment-page
		// GET accepts only the public key/eid pair and rejects the locale,
		// redirect and confirm metadata fields.
		return url.Values{
			"key": {checkout.PublishableKey},
			"eid": {"NA"},
		}
	}
	if strings.EqualFold(providerType, "kakao") || strings.EqualFold(providerType, "kakao_pay") {
		params := f.elementsSessionParams(ctx, true)
		params.Set("key", checkout.PublishableKey)
		return params
	}
	// payment_pages GET has a narrower contract than init/confirm. In
	// particular, Stripe rejects client_attribution_metadata on this endpoint
	// with parameter_unknown, which hid valid redirects after OpenAI approve.
	params := f.elementsSessionParams(ctx, false)
	params.Set("key", checkout.PublishableKey)
	return params
}

func (f *flow) followProviderRedirect(client *BrowserClient, rawURL string, preferredHosts []string) string {
	current := strings.TrimSpace(rawURL)
	for hop := 0; hop < 6 && current != ""; hop++ {
		if baURL := extractPayPalBAURL(current); baURL != "" {
			return baURL
		}
		parsed, err := url.Parse(current)
		if err != nil {
			return current
		}
		host := strings.ToLower(parsed.Hostname())
		for _, preferred := range preferredHosts {
			preferred = strings.ToLower(strings.TrimPrefix(preferred, "."))
			if host == preferred || strings.HasSuffix(host, "."+preferred) {
				return current
			}
		}
		response, err := client.Do(f.ctx, http.MethodGet, current, map[string]string{"Accept": "text/html,application/xhtml+xml,*/*"}, nil, false)
		if err != nil {
			return current
		}
		if baURL := extractPayPalBAURL(map[string]any{"url": current, "location": response.Headers["Location"], "body": string(response.Body)}); baURL != "" {
			return baURL
		}
		if response.Status < 300 || response.Status > 399 {
			return current
		}
		location := firstNonEmpty(response.Headers["Location"], response.Headers["location"])
		if location == "" {
			return current
		}
		next, err := parsed.Parse(location)
		if err != nil {
			return current
		}
		current = next.String()
	}
	return current
}

func extractRedirectURL(value any) string {
	if baURL := extractPayPalBAURL(value); baURL != "" {
		return baURL
	}
	// Payment-page snapshots contain many unrelated URLs (logos, legal pages,
	// management links). A provider redirect nested under setup_intent or
	// payment_intent must win before the generic URL fallback.
	if redirect := extractNextActionRedirectURL(value); redirect != "" {
		return redirect
	}
	switch item := value.(type) {
	case map[string]any:
		for _, key := range []string{"redirect_url", "url", "provider_redirect_url", "hosted_instructions_url"} {
			candidate := stringValue(item[key])
			if strings.HasPrefix(candidate, "http://") || strings.HasPrefix(candidate, "https://") {
				if !strings.Contains(candidate, "docs/error-codes") {
					return candidate
				}
			}
		}
		for _, child := range item {
			if candidate := extractRedirectURL(child); candidate != "" {
				return candidate
			}
		}
	case []any:
		for _, child := range item {
			if candidate := extractRedirectURL(child); candidate != "" {
				return candidate
			}
		}
	case string:
		for _, candidate := range httpURLPattern.FindAllString(html.UnescapeString(strings.ReplaceAll(item, `\/`, `/`)), -1) {
			if !strings.Contains(candidate, "docs/error-codes") {
				return candidate
			}
		}
	}
	return ""
}

func extractNextActionRedirectURL(value any) string {
	switch item := value.(type) {
	case map[string]any:
		for _, key := range []string{"next_action", "setup_intent", "payment_intent"} {
			if child, ok := item[key]; ok {
				if candidate := extractNextActionRedirectURL(child); candidate != "" {
					return candidate
				}
			}
		}
		if redirect, ok := item["redirect_to_url"].(map[string]any); ok {
			if candidate := stringValue(redirect["url"]); strings.HasPrefix(candidate, "http://") || strings.HasPrefix(candidate, "https://") {
				return candidate
			}
		}
		for _, child := range item {
			if candidate := extractNextActionRedirectURL(child); candidate != "" {
				return candidate
			}
		}
	case []any:
		for _, child := range item {
			if candidate := extractNextActionRedirectURL(child); candidate != "" {
				return candidate
			}
		}
	}
	return ""
}

func extractPayPalBAURL(value any) string {
	encoded, _ := json.Marshal(value)
	text := html.UnescapeString(string(encoded))
	text = strings.NewReplacer(`\/`, `/`, `\u0026`, "&", `\u003d`, "=", `\u003D`, "=").Replace(text)
	if decoded, err := url.QueryUnescape(text); err == nil {
		text += " " + decoded
	}
	for _, candidate := range httpURLPattern.FindAllString(text, -1) {
		candidate = strings.TrimRight(candidate, `.,;:)]}`)
		if isStrictPayPalBAApproveURL(candidate) {
			return candidate
		}
	}
	return ""
}

func isStrictPayPalBAApproveURL(value string) bool {
	parsed, err := url.Parse(strings.TrimSpace(value))
	if err != nil || parsed.Scheme != "https" || parsed.User != nil || parsed.Port() != "" || parsed.Fragment != "" {
		return false
	}
	host := strings.ToLower(parsed.Hostname())
	if host != "paypal.com" && !strings.HasSuffix(host, ".paypal.com") {
		return false
	}
	if strings.ToLower(strings.TrimSuffix(parsed.EscapedPath(), "/")) != "/agreements/approve" {
		return false
	}
	token := strings.TrimSpace(parsed.Query().Get("ba_token"))
	return paypalBAStrictToken.MatchString(token)
}

func requiresApproval(value any) bool {
	switch item := value.(type) {
	case map[string]any:
		for key, child := range item {
			if (strings.EqualFold(key, "state") || strings.EqualFold(key, "status")) && strings.EqualFold(stringValue(child), "requires_approval") {
				return true
			}
			if strings.EqualFold(key, "requires_manual_approval") {
				if boolean, ok := child.(bool); ok && boolean {
					return true
				}
			}
			if requiresApproval(child) {
				return true
			}
		}
	case []any:
		for _, child := range item {
			if requiresApproval(child) {
				return true
			}
		}
	}
	return false
}

func setupIntentError(value any, currentPMID string) string {
	switch item := value.(type) {
	case map[string]any:
		if last, ok := item["last_setup_error"]; ok && last != nil {
			encoded, _ := json.Marshal(last)
			text := string(encoded)
			pmID := strings.TrimSpace(currentPMID)
			// Prefer an exact payment_method id match. A bare contains() check can
			// over-match nested ids / unrelated fields and abort BA polling early.
			if pmID == "" {
				return "setup_intent.last_setup_error: " + trimDetail(text, 700)
			}
			if paymentMethodIDFromSetupError(last) == pmID || strings.Contains(text, `"id":"`+pmID+`"`) || strings.Contains(text, `"payment_method":"`+pmID+`"`) {
				return "setup_intent.last_setup_error: " + trimDetail(text, 700)
			}
		}
		for _, child := range item {
			if found := setupIntentError(child, currentPMID); found != "" {
				return found
			}
		}
	case []any:
		for _, child := range item {
			if found := setupIntentError(child, currentPMID); found != "" {
				return found
			}
		}
	}
	return ""
}

func paymentMethodIDFromSetupError(value any) string {
	switch item := value.(type) {
	case map[string]any:
		if direct := strings.TrimSpace(stringValue(item["payment_method"])); strings.HasPrefix(direct, "pm_") {
			return direct
		}
		if nested, ok := item["payment_method"].(map[string]any); ok {
			if id := strings.TrimSpace(stringValue(nested["id"])); strings.HasPrefix(id, "pm_") {
				return id
			}
		}
		for _, key := range []string{"id", "payment_method_id"} {
			if id := strings.TrimSpace(stringValue(item[key])); strings.HasPrefix(id, "pm_") {
				return id
			}
		}
	}
	return ""
}

func decodeLooseJSON(body []byte) map[string]any {
	result := map[string]any{}
	decoder := json.NewDecoder(strings.NewReader(string(body)))
	decoder.UseNumber()
	if decoder.Decode(&result) != nil {
		result["_raw_text"] = string(body)
	}
	return result
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func sleepContext(ctx context.Context, duration time.Duration) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-time.After(duration):
		return nil
	}
}
