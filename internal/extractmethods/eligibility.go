package extractmethods

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
)

var paidPlanNames = map[string]bool{
	"plus": true, "pro": true, "team": true, "business": true,
	"enterprise": true, "edu": true, "education": true, "k12": true,
}

type PaymentVerification struct {
	Status string
	Plan   string
	Email  string
	Detail string
}

// VerifyPaymentStatus performs a read-only account entitlement check. It does
// not call checkout approval, Stripe confirm, or any payment mutation endpoint.
func (e *Engine) VerifyPaymentStatus(ctx context.Context, credential Credential, options Options) (PaymentVerification, error) {
	options = normalizeOptions(MethodPH, options)
	proxies, err := ResolveStageProxies(e.Config, options, MethodPH)
	if err != nil {
		return PaymentVerification{Status: "verification_unknown", Detail: err.Error()}, err
	}
	profile := profileForCountry(options.Country)
	fingerprint := resolveRequestFingerprint(options.ClientFingerprint)
	f := &flow{
		ctx: ctx, engine: e, credential: credential, options: options, proxies: proxies,
		profile: profile, fingerprint: fingerprint, deviceID: newUUID(), sessionID: newUUID(),
	}
	client, err := NewBrowserClient(proxies.Checkout, options.Timeout(), options.ClientFingerprint)
	if err != nil {
		return PaymentVerification{Status: "verification_unknown", Detail: err.Error()}, err
	}
	defer client.Close()
	client.SetDefaultHeaders(map[string]string{"User-Agent": fingerprint.UserAgent, "Accept-Language": profile.AcceptLanguage})

	get := func(path string) (map[string]any, int, error) {
		response, requestErr := client.Do(ctx, http.MethodGet, e.Endpoints.ChatGPT+path, f.openAIHeaders(path, e.Endpoints.ChatGPT+"/"), nil, true)
		if requestErr != nil {
			return nil, 0, requestErr
		}
		if response.Status != http.StatusOK {
			return nil, response.Status, upstreamError("payment.verify", response)
		}
		var payload map[string]any
		if response.JSON(&payload) != nil {
			return nil, response.Status, fmt.Errorf("%s 返回的不是 JSON", path)
		}
		return payload, response.Status, nil
	}

	me, status, err := get("/backend-api/me")
	if err != nil {
		if status == http.StatusUnauthorized {
			return PaymentVerification{Status: "token_invalid", Detail: "上游明确返回 401，Token 已失效"}, nil
		}
		if status == http.StatusForbidden {
			return PaymentVerification{Status: "verification_unknown", Detail: "支付状态复核遇到 HTTP 403/CF 拦截"}, nil
		}
		return PaymentVerification{Status: "verification_unknown", Detail: err.Error()}, err
	}
	models, status, err := get("/backend-api/models")
	if err != nil {
		if status == http.StatusUnauthorized {
			return PaymentVerification{Status: "token_invalid", Detail: "上游明确返回 401，Token 已失效"}, nil
		}
		if status == http.StatusForbidden {
			return PaymentVerification{Status: "verification_unknown", Detail: "支付状态复核遇到 HTTP 403/CF 拦截"}, nil
		}
		return PaymentVerification{Status: "verification_unknown", Detail: err.Error()}, err
	}

	email, plan := evaluateAccountStatus(credential.AccessToken, me, models)
	if paidPlanNames[plan] {
		return PaymentVerification{Status: "paid_success", Plan: plan, Email: email, Detail: "官方账号权限已显示为付费套餐 " + plan}, nil
	}
	if plan == "free" {
		return PaymentVerification{Status: "awaiting_payment", Plan: plan, Email: email, Detail: "官方账号当前仍为 Free，尚未确认支付成功"}, nil
	}
	return PaymentVerification{Status: "verification_unknown", Plan: plan, Email: email, Detail: "官方账号套餐状态暂无法确认"}, nil
}

// probeAccountEligibility is a hard gate shared by every extraction channel.
// It verifies the token against ChatGPT, requires an email-bound account, and
// refuses any account for which the current plan cannot be established as Free.
func (f *flow) probeAccountEligibility() error {
	const stage = "account.eligibility"
	mainPool := ParseProxyPool(f.options.Proxy)
	if len(mainPool) == 0 {
		mainPool = []string{f.proxies.Checkout}
	}
	eligibilityAttempts := len(mainPool)
	if eligibilityAttempts < 3 {
		// A single dynamic proxy endpoint can still return a different exit after
		// reconnecting. Give CF/network failures two fresh connections before
		// concluding that the upstream is unavailable.
		eligibilityAttempts = 3
	}
	f.addStep(stage, "running", fmt.Sprintf("检查 Token、Free 套餐与绑定邮箱；主代理候选 %d 条；最多 %d 次", len(mainPool), eligibilityAttempts), 0)

	var lastErr error
	for attempt := 1; attempt <= eligibilityAttempts; attempt++ {
		selected := SelectAttemptOptions(f.options, attempt)
		proxies, err := ResolveStageProxies(f.engine.Config, selected, f.method)
		if err != nil {
			return fmt.Errorf("账号资格检查代理: %w", err)
		}
		client, err := NewBrowserClient(proxies.Checkout, selected.Timeout(), selected.ClientFingerprint)
		if err != nil {
			return fmt.Errorf("账号资格检查客户端: %w", err)
		}
		client.SetDefaultHeaders(map[string]string{
			"User-Agent": f.fingerprint.UserAgent, "Accept-Language": f.profile.AcceptLanguage,
		})

		get := func(path string) (map[string]any, error) {
			response, requestErr := client.Do(
				f.ctx, http.MethodGet, f.engine.Endpoints.ChatGPT+path,
				f.openAIHeaders(path, f.engine.Endpoints.ChatGPT+"/"), nil, true,
			)
			if requestErr != nil {
				return nil, &eligibilityUpstreamError{Path: path, Cause: requestErr, Retryable: true}
			}
			if response.Status != http.StatusOK {
				return nil, classifyEligibilityResponse(path, response)
			}
			var payload map[string]any
			if response.JSON(&payload) != nil {
				return nil, &eligibilityUpstreamError{Path: path, Status: response.Status, Preview: response.Preview(500), Retryable: true, Reason: "上游未返回 JSON"}
			}
			return payload, nil
		}

		me, requestErr := get("/backend-api/me")
		if requestErr == nil {
			var models map[string]any
			models, requestErr = get("/backend-api/models")
			if requestErr == nil {
				client.Close()
				email, _, eligibilityErr := evaluateAccountEligibility(f.credential.AccessToken, me, models)
				if eligibilityErr != nil {
					if strings.Contains(eligibilityErr.Error(), "绑定邮箱") {
						f.addStep(stage, "failed", "账号未检测到已绑定邮箱", 0)
					} else {
						f.addStep(stage, "failed", "当前账号套餐不是 Free", 0)
					}
					return eligibilityErr
				}
				f.credential.Email = email
				f.proxies = proxies
				f.options.Proxy = strings.Join(rotateProxyPool(mainPool, attempt), "\n")
				f.addStep(stage, "success", "Token 有效；Free；邮箱已绑定 "+MaskEmail(email), 0)
				return nil
			}
		}
		client.Close()
		lastErr = requestErr
		if !isRetryableEligibilityError(requestErr) || attempt == eligibilityAttempts {
			break
		}
		f.addStep(stage, "retrying", fmt.Sprintf("上游 CF/网络拦截，切换主代理或重连重试 %d/%d：%s", attempt+1, eligibilityAttempts, eligibilityErrorReason(requestErr)), 0)
	}

	var upstream *eligibilityUpstreamError
	if errors.As(lastErr, &upstream) && upstream.AuthInvalid {
		f.addStep(stage, "failed", "Token 已被上游明确判定失效", 0)
		return fmt.Errorf("Token 验证失败: %w", lastErr)
	}
	f.addStep(stage, "failed", "账号资格检查被 CF/网络拦截；已完成全部代理轮换/重连", 0)
	return fmt.Errorf("账号资格检查上游不可用: %w", lastErr)
}

type eligibilityUpstreamError struct {
	Path        string
	Status      int
	Preview     string
	Reason      string
	Cause       error
	Retryable   bool
	AuthInvalid bool
}

func (e *eligibilityUpstreamError) Error() string {
	if e.Cause != nil {
		return fmt.Sprintf("%s 请求失败: %v", e.Path, e.Cause)
	}
	reason := strings.TrimSpace(e.Reason)
	if reason == "" {
		reason = strings.TrimSpace(e.Preview)
	}
	if reason == "" {
		reason = http.StatusText(e.Status)
	}
	return fmt.Sprintf("%s HTTP %d: %s", e.Path, e.Status, reason)
}

func (e *eligibilityUpstreamError) Unwrap() error { return e.Cause }

func classifyEligibilityResponse(path string, response HTTPResponse) error {
	preview := response.Preview(500)
	lower := strings.ToLower(preview)
	authInvalid := response.Status == http.StatusUnauthorized
	cfChallenge := response.Status == http.StatusForbidden && (strings.Contains(lower, "enable javascript and cookies") ||
		strings.Contains(lower, "challenge-platform") ||
		strings.Contains(lower, "cf_chl") ||
		strings.Contains(lower, "cloudflare"))
	retryable := cfChallenge || response.Status == http.StatusForbidden || response.Status == http.StatusTooManyRequests || response.Status >= http.StatusInternalServerError
	reason := ""
	switch {
	case authInvalid:
		reason = "上游明确返回 Token 过期或已吊销"
	case cfChallenge:
		reason = "Cloudflare challenge"
	case response.Status == http.StatusForbidden:
		reason = "HTTP 403 上游访问拦截"
	case response.Status == http.StatusTooManyRequests:
		reason = "HTTP 429 上游限流"
	}
	return &eligibilityUpstreamError{Path: path, Status: response.Status, Preview: preview, Reason: reason, Retryable: retryable, AuthInvalid: authInvalid}
}

func isRetryableEligibilityError(err error) bool {
	var upstream *eligibilityUpstreamError
	return errors.As(err, &upstream) && upstream.Retryable && !upstream.AuthInvalid
}

func eligibilityErrorReason(err error) string {
	var upstream *eligibilityUpstreamError
	if errors.As(err, &upstream) && strings.TrimSpace(upstream.Reason) != "" {
		return upstream.Reason
	}
	return trimDetail(fmt.Sprint(err), 180)
}

func rotateProxyPool(pool []string, successfulAttempt int) []string {
	if len(pool) < 2 {
		return append([]string(nil), pool...)
	}
	index := (successfulAttempt - 1) % len(pool)
	if index < 0 {
		index = 0
	}
	rotated := make([]string, 0, len(pool))
	rotated = append(rotated, pool[index:]...)
	rotated = append(rotated, pool[:index]...)
	return rotated
}

func evaluateAccountEligibility(accessToken string, me, models map[string]any) (string, string, error) {
	// A caller-supplied email is only display metadata. The binding gate must be
	// established by the authenticated upstream account response itself.
	email := strings.TrimSpace(findEmailDeep(me))
	if email == "" || !emailPattern.MatchString(email) {
		return "", "", fmt.Errorf("账号资格不符合：必须绑定邮箱")
	}

	// Explicit plan fields from the authenticated account response or signed JWT
	// are authoritative. The model catalog is only a fallback when both omit it.
	_, plan := evaluateAccountStatus(accessToken, me, models)
	if plan != "free" {
		return "", plan, fmt.Errorf("账号资格不符合：当前套餐为 %s，仅允许 Free 账号", plan)
	}
	return email, plan, nil
}

func evaluateAccountStatus(accessToken string, me, models map[string]any) (string, string) {
	email := strings.TrimSpace(findEmailDeep(me))
	plan := normalizePlanName(findPlanDeep(me))
	if plan == "" {
		plan = planFromJWT(accessToken)
	}
	if plan == "" && modelsIndicatePaidPlan(models) {
		// Models are only a fallback when neither the authenticated /me payload
		// nor the signed access-token claim identifies a plan. Free accounts can
		// receive broad model catalogs and must not be promoted by heuristics.
		plan = "plus"
	} else if plan == "" {
		// A successful models response without paid-only evidence is the
		// established Free signal used by the existing account inventory.
		plan = "free"
	}
	return email, plan
}

func findEmailDeep(value any) string {
	return findStringByKeys(value, map[string]bool{
		"email": true, "account_email": true, "email_address": true, "preferred_username": true,
	}, func(candidate string) bool { return emailPattern.MatchString(strings.TrimSpace(candidate)) })
}

func findPlanDeep(value any) string {
	return findStringByKeys(value, map[string]bool{
		"plan_type": true, "account_type": true, "current_plan": true,
		"subscription_plan": true, "subscription_tier": true,
	}, func(candidate string) bool { return normalizePlanName(candidate) != "" })
}

func findStringByKeys(value any, keys map[string]bool, accept func(string) bool) string {
	switch item := value.(type) {
	case map[string]any:
		for key, child := range item {
			if keys[strings.ToLower(strings.TrimSpace(key))] {
				candidate := stringValue(child)
				if accept(candidate) {
					return candidate
				}
			}
		}
		for _, child := range item {
			if candidate := findStringByKeys(child, keys, accept); candidate != "" {
				return candidate
			}
		}
	case []any:
		for _, child := range item {
			if candidate := findStringByKeys(child, keys, accept); candidate != "" {
				return candidate
			}
		}
	}
	return ""
}

func normalizePlanName(value string) string {
	plan := strings.ToLower(strings.TrimSpace(value))
	plan = strings.NewReplacer("chatgpt_", "", "-", "_", " ", "_").Replace(plan)
	for paid := range paidPlanNames {
		if plan == paid || strings.Contains(plan, paid) {
			return paid
		}
	}
	if plan == "free" || strings.Contains(plan, "free") {
		return "free"
	}
	return ""
}

func modelsIndicatePaidPlan(value any) bool {
	paidMarkers := []string{"o3", "o4", "gpt-4", "gpt-5-6", "gpt-5.6", "-instant", "-thinking", "-wm", ".5-wm", ".6-", "cca-wm", "sol-wm", "terra-wm", "luna-wm"}
	modelSlugs := map[string]bool{}
	explicitCount := 0
	var walk func(any) bool
	walk = func(node any) bool {
		switch item := node.(type) {
		case map[string]any:
			for key, child := range item {
				lowerKey := strings.ToLower(key)
				if lowerKey == "available_model_count" {
					if count, err := strconv.Atoi(stringValue(child)); err == nil && count > explicitCount {
						explicitCount = count
					}
				}
				if lowerKey == "slug" || lowerKey == "model" {
					text := strings.ToLower(stringValue(child))
					if text != "" {
						modelSlugs[text] = true
						for _, marker := range paidMarkers {
							if strings.Contains(text, marker) {
								return true
							}
						}
					}
				}
				if lowerKey == "title" {
					title := strings.ToLower(stringValue(child))
					if strings.Contains(title, "plus") || strings.Contains(title, "pro") {
						return true
					}
				}
				if walk(child) {
					return true
				}
			}
		case []any:
			for _, child := range item {
				if walk(child) {
					return true
				}
			}
		}
		return false
	}
	return walk(value) || explicitCount >= 12 || len(modelSlugs) >= 12
}
