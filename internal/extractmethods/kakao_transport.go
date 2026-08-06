package extractmethods

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

const kakaoHelperMaxLineBytes = 8 * 1024 * 1024

type kakaoTransportRequest struct {
	AccessToken           string            `json:"accessToken"`
	CheckoutProxy         string            `json:"checkoutProxy"`
	PromotionProxy        string            `json:"promotionProxy"`
	ProviderProxy         string            `json:"providerProxy"`
	CheckoutProxies       []string          `json:"checkoutProxies,omitempty"`
	PromotionProxies      []string          `json:"promotionProxies,omitempty"`
	PromotionRegions      []string          `json:"promotionRegions,omitempty"`
	PromotionRegion       string            `json:"promotionRegion"`
	PromoEnabled          bool              `json:"promoEnabled"`
	PromoCampaignID       string            `json:"promoCampaignId"`
	TimeoutSeconds        int               `json:"timeoutSeconds"`
	PollTimeoutSeconds    int               `json:"pollTimeoutSeconds"`
	CheckoutAttempts      int               `json:"checkoutAttempts"`
	ApproveAttempts       int               `json:"approveAttempts,omitempty"`
	MaxAmountMinor        int               `json:"maxAmountMinor"`
	FingerprintPolicy     map[string]string `json:"fingerprintPolicy,omitempty"`
	FingerprintWeightMode bool              `json:"fingerprintWeightMode,omitempty"`
	Mode                  string            `json:"mode"`
	EligibilityOnly       bool              `json:"eligibilityOnly"`
}

type kakaoTransportEvent struct {
	Type    string  `json:"type"`
	Stage   string  `json:"stage,omitempty"`
	Status  string  `json:"status,omitempty"`
	Detail  string  `json:"detail,omitempty"`
	Message string  `json:"message,omitempty"`
	Result  *Result `json:"result,omitempty"`
	Partial *Result `json:"partial,omitempty"`
}

type boundedBuffer struct {
	buffer bytes.Buffer
	limit  int
}

func (w *boundedBuffer) Write(content []byte) (int, error) {
	originalLength := len(content)
	remaining := w.limit - w.buffer.Len()
	if remaining > 0 {
		if len(content) > remaining {
			content = content[:remaining]
		}
		_, _ = w.buffer.Write(content)
	}
	return originalLength, nil
}

func (w *boundedBuffer) String() string {
	return strings.TrimSpace(w.buffer.String())
}

func defaultKakaoHelperPath() string {
	if configured := strings.TrimSpace(os.Getenv("AUTOMYAI_KAKAO_HELPER")); configured != "" {
		return configured
	}
	for _, candidate := range []string{
		"/app/integrations/kakao_curl_transport.py",
		"/opt/automyai/integrations/kakao_curl_transport.py",
		"integrations/kakao_curl_transport.py",
	} {
		if info, err := os.Stat(candidate); err == nil && !info.IsDir() {
			return candidate
		}
	}
	return "/app/integrations/kakao_curl_transport.py"
}

func defaultPythonExecutable() string {
	if configured := strings.TrimSpace(os.Getenv("AUTOMYAI_KAKAO_PYTHON")); configured != "" {
		return configured
	}
	return "python3"
}

func helperEnvironment() []string {
	environment := []string{
		"PYTHONDONTWRITEBYTECODE=1",
		"PYTHONUNBUFFERED=1",
	}
	for _, name := range []string{"PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR"} {
		if value, ok := os.LookupEnv(name); ok {
			environment = append(environment, name+"="+value)
		}
	}
	return environment
}

func (f *flow) runKakaoCurlTransport() (Result, error, bool) {
	helperPath := strings.TrimSpace(f.engine.KakaoHelperPath)
	if helperPath == "" {
		helperPath = defaultKakaoHelperPath()
	}
	info, err := os.Stat(helperPath)
	if err != nil || info.IsDir() {
		return Result{}, fmt.Errorf("curl_cffi helper 不可用: %s", helperPath), false
	}
	python := strings.TrimSpace(f.engine.PythonExecutable)
	if python == "" {
		python = defaultPythonExecutable()
	}
	resolvedPython, err := exec.LookPath(python)
	if err != nil {
		return Result{}, fmt.Errorf("curl_cffi helper 的 Python 不可用: %s", python), false
	}

	mode := NormalizeKakaoMode(f.options.KakaoMode)
	if mode != KakaoModeProviderLink {
		mode = KakaoModeEligibility
	}
	mainPool := ParseProxyPool(f.options.Proxy)
	promotionPoolRaw := ParseProxyPool(f.options.PromotionProxy)
	regionPool := ParseRegionPool(f.options.PromotionProxyRegion)
	checkoutProxies := make([]string, 0, maxInt(1, len(mainPool)))
	promotionProxies := make([]string, 0)
	promotionRegions := make([]string, 0)
	// Prefer already-resolved stage proxies from Engine.Run. Only rebuild the
	// multi-select rotation when the caller actually supplied proxy/region pools.
	hasResolved := strings.TrimSpace(f.proxies.Checkout) != "" && strings.TrimSpace(f.proxies.Promotion) != ""
	// Provider-link full chains benefit from a fresh KR sticky identity on every
	// attempt, even when the caller only selected one main proxy. Promotion still
	// rotates across the multi-selected JP/VN/TR pool.
	attempts := f.options.Attempts()
	if hasResolved || len(mainPool) > 0 {
		for attempt := 1; attempt <= maxInt(1, attempts); attempt++ {
			selected := SelectAttemptOptions(f.options, attempt)
			var stage StageProxies
			var stageErr error
			if len(ParseProxyPool(selected.Proxy)) > 0 {
				stage, stageErr = ResolveStageProxies(f.engine.Config, selected, MethodKakao)
			}
			if stageErr != nil || strings.TrimSpace(stage.Checkout) == "" {
				if !hasResolved && stageErr != nil {
					return Result{}, stageErr, true
				}
				stage = f.proxies
				if len(promotionPoolRaw) > 0 {
					stage.Promotion = PickPoolValue(promotionPoolRaw, attempt)
				} else if len(regionPool) > 0 && strings.TrimSpace(stage.Checkout) != "" {
					stage.Promotion = rewriteProxyRegion(stage.Checkout, PickPoolValue(regionPool, attempt))
				}
			}
			checkout := stage.Checkout
			if checkout == "" {
				checkout = f.proxies.Checkout
			}
			// Mint a new sticky SID for this full-chain attempt while keeping KR.
			if checkout != "" {
				fresh := freshProxyIdentity(checkout)
				if normalized, err := normalizeProxy(fresh); err == nil {
					checkout = normalized
				} else {
					checkout = fresh
				}
				checkoutProxies = append(checkoutProxies, checkout)
			}
			promotion := stage.Promotion
			if promotion == "" && len(promotionPoolRaw) > 0 {
				promotion = PickPoolValue(promotionPoolRaw, attempt)
			}
			if promotion == "" && len(regionPool) > 0 && checkout != "" {
				promotion = rewriteProxyRegion(checkout, PickPoolValue(regionPool, attempt))
			}
			if promotion == "" {
				promotion = f.proxies.Promotion
			}
			if promotion != "" {
				// Keep explicit multi-selected promotion proxies exact; only freshen
				// derived same-seed promotions so each attempt is independent.
				if len(promotionPoolRaw) == 0 {
					freshPromo := freshProxyIdentity(promotion)
					if normalized, err := normalizeProxy(freshPromo); err == nil {
						promotion = normalized
					} else {
						promotion = freshPromo
					}
				} else if _, err := normalizeProxy(promotion); err == nil {
					// already normalized explicit proxy
				} else if normalized, err := normalizeProxy(promotion); err == nil {
					promotion = normalized
				}
				promotionProxies = append(promotionProxies, promotion)
			}
			region := firstNonEmpty(proxyConfiguredRegion(promotion), selected.PromotionProxyRegion, PickPoolValue(regionPool, attempt))
			if region != "" {
				promotionRegions = append(promotionRegions, region)
			}
		}
	}
	if len(checkoutProxies) == 0 && strings.TrimSpace(f.proxies.Checkout) != "" {
		checkoutProxies = []string{f.proxies.Checkout}
	}
	if len(promotionProxies) == 0 && strings.TrimSpace(f.proxies.Promotion) != "" {
		promotionProxies = []string{f.proxies.Promotion}
	}
	if len(checkoutProxies) == 0 || len(promotionProxies) == 0 {
		return Result{}, errors.New("Kakao transport 缺少已解析的 Checkout/Promotion 代理"), true
	}
	if len(promotionRegions) == 0 {
		if mode == KakaoModeProviderLink {
			if len(regionPool) > 0 {
				promotionRegions = regionPool
			} else if len(promotionPoolRaw) > 0 {
				for _, proxy := range promotionPoolRaw {
					if region := proxyConfiguredRegion(proxy); region != "" {
						promotionRegions = append(promotionRegions, region)
					}
				}
			}
			if len(promotionRegions) == 0 {
				promotionRegions = []string{firstNonEmpty(proxyConfiguredRegion(promotionProxies[0]), "JP", "VN")}
			}
		} else {
			promotionRegions = []string{firstNonEmpty(PickPoolValue(regionPool, 1), "TR")}
		}
	}
	promotionRegion := promotionRegions[0]
	request := kakaoTransportRequest{
		AccessToken:           f.credential.AccessToken,
		CheckoutProxy:         checkoutProxies[0],
		PromotionProxy:        promotionProxies[0],
		ProviderProxy:         checkoutProxies[0],
		CheckoutProxies:       checkoutProxies,
		PromotionProxies:      promotionProxies,
		PromotionRegions:      promotionRegions,
		PromotionRegion:       promotionRegion,
		PromoEnabled:          f.options.PromoEnabled(),
		PromoCampaignID:       f.options.PromoCampaignID,
		TimeoutSeconds:        f.options.TimeoutSeconds,
		PollTimeoutSeconds:    maxInt(60, f.options.TimeoutSeconds*2),
		CheckoutAttempts:      f.options.Attempts(),
		ApproveAttempts:       f.options.ApproveRetries(),
		MaxAmountMinor:        int(f.options.AmountLimit()),
		FingerprintPolicy:     f.options.StageFingerprintPolicy(),
		FingerprintWeightMode: f.options.FingerprintWeightMode != nil && *f.options.FingerprintWeightMode,
		Mode:                  mode,
		EligibilityOnly:       mode == KakaoModeEligibility,
	}
	encoded, err := json.Marshal(request)
	if err != nil {
		return Result{}, fmt.Errorf("编码 Kakao transport 请求: %w", err), true
	}

	command := exec.CommandContext(f.ctx, resolvedPython, helperPath)
	command.Dir = filepath.Dir(helperPath)
	command.Env = helperEnvironment()
	stdin, err := command.StdinPipe()
	if err != nil {
		return Result{}, fmt.Errorf("创建 Kakao helper stdin: %w", err), true
	}
	stdout, err := command.StdoutPipe()
	if err != nil {
		return Result{}, fmt.Errorf("创建 Kakao helper stdout: %w", err), true
	}
	stderr := &boundedBuffer{limit: 16 * 1024}
	command.Stderr = stderr
	if err := command.Start(); err != nil {
		return Result{}, fmt.Errorf("启动 Kakao curl_cffi helper: %w", err), false
	}
	go func() {
		_, _ = stdin.Write(encoded)
		_ = stdin.Close()
	}()

	result := Result{Country: "KR", Currency: "KRW", ExtractionStatus: "failed", PaymentStatus: "not_started"}
	var helperError error
	unavailable := false
	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 64*1024), kakaoHelperMaxLineBytes)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var event kakaoTransportEvent
		if err := json.Unmarshal([]byte(line), &event); err != nil {
			helperError = fmt.Errorf("Kakao helper 返回了无效事件: %w", err)
			continue
		}
		switch strings.ToLower(strings.TrimSpace(event.Type)) {
		case "step":
			f.addStep(event.Stage, event.Status, event.Detail, 0)
		case "result":
			if event.Result == nil {
				helperError = errors.New("Kakao helper result 事件缺少结果")
				continue
			}
			result = *event.Result
		case "error":
			if event.Partial != nil {
				result = *event.Partial
			}
			message := strings.TrimSpace(event.Message)
			if message == "" {
				message = "Kakao curl_cffi helper 执行失败"
			}
			helperError = errors.New(message)
		case "unavailable":
			message := strings.TrimSpace(event.Message)
			if message == "" {
				message = "curl_cffi transport unavailable"
			}
			helperError = errors.New(message)
			unavailable = true
		default:
			helperError = fmt.Errorf("Kakao helper 返回未知事件类型: %s", event.Type)
		}
	}
	if err := scanner.Err(); err != nil && helperError == nil {
		helperError = fmt.Errorf("读取 Kakao helper 输出: %w", err)
	}
	waitErr := command.Wait()
	if f.ctx.Err() != nil {
		return result, f.ctx.Err(), true
	}
	if unavailable {
		return result, helperError, false
	}
	if helperError != nil {
		return result, helperError, true
	}
	if waitErr != nil {
		detail := stderr.String()
		if detail == "" {
			detail = waitErr.Error()
		}
		return result, fmt.Errorf("Kakao helper 异常退出: %s", trimDetail(detail, 900)), true
	}
	if request.EligibilityOnly {
		decision := strings.ToLower(strings.TrimSpace(result.Decision))
		if !result.OK || result.ExtractionStatus != "probe_complete" || (decision != "eligible" && decision != "ineligible") {
			return result, errors.New("Kakao helper 未返回完整的资格诊断结果"), true
		}
		if strings.TrimSpace(result.PaymentMethodID) != "" || strings.TrimSpace(result.LongURL) != "" || strings.TrimSpace(result.ProviderRedirectURL) != "" || strings.TrimSpace(result.StripeRedirectURL) != "" {
			return result, errors.New("Kakao 资格诊断返回了支付实体或链接，已按安全边界拒绝"), true
		}
		return result, nil, true
	}
	if !result.OK || result.ExtractionStatus != "provider_link_ready" || result.PaymentStatus != "awaiting_kakao_payment" || !validKakaoProviderURL(result.LongURL) {
		return result, errors.New("Kakao helper 未返回可用的 provider 链接"), true
	}
	return result, nil, true
}

func validKakaoProviderURL(value string) bool {
	parsed, err := url.Parse(strings.TrimSpace(value))
	if err != nil || (parsed.Scheme != "https" && parsed.Scheme != "http") {
		return false
	}
	host := strings.ToLower(parsed.Hostname())
	return host != "" && (strings.Contains(host, "nicepay") || strings.Contains(host, "kakao"))
}

func maxInt(left, right int) int {
	if left > right {
		return left
	}
	return right
}

func uniqueStrings(values []string) []string {
	seen := map[string]bool{}
	result := make([]string, 0, len(values))
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" || seen[value] {
			continue
		}
		seen[value] = true
		result = append(result, value)
	}
	return result
}
