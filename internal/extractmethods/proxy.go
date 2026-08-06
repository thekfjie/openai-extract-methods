package extractmethods

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"
)

type RuntimeConfig struct {
	path    string
	mu      sync.Mutex
	loaded  time.Time
	modTime time.Time
	values  map[string]string
}

func NewRuntimeConfig(path string) *RuntimeConfig {
	return &RuntimeConfig{path: path, values: map[string]string{}}
}

func (c *RuntimeConfig) Values() map[string]string {
	c.mu.Lock()
	defer c.mu.Unlock()
	stat, err := os.Stat(c.path)
	if err == nil && (c.loaded.IsZero() || stat.ModTime().After(c.modTime)) {
		encoded, readErr := os.ReadFile(c.path)
		if readErr == nil {
			var raw map[string]any
			if json.Unmarshal(encoded, &raw) == nil {
				values := make(map[string]string, len(raw))
				for key, value := range raw {
					values[key] = stringValue(value)
				}
				c.values = values
				c.loaded = time.Now()
				c.modTime = stat.ModTime()
			}
		}
	}
	result := make(map[string]string, len(c.values))
	for key, value := range c.values {
		result[key] = value
	}
	return result
}

type StageProxies struct {
	Checkout  string
	Promotion string
	Provider  string
	Approve   string
	Labels    map[string]string
}

func validateExplicitProxies(options Options) error {
	mode := strings.ToLower(strings.TrimSpace(options.ProxyMode))
	if mode == "" {
		mode = "custom"
	}
	switch mode {
	case "custom", "manual", "cliproxy", "dynamic", "residential":
	case "regional", "mihomo", "local", "system", "default":
		return errors.New("提炼任务没有默认代理：请手动填写主流程代理")
	default:
		return fmt.Errorf("不支持的代理模式: %s", mode)
	}
	mainPool := ParseProxyPool(options.Proxy)
	if len(mainPool) == 0 {
		return errors.New("请手动填写主流程代理")
	}
	for index, proxy := range mainPool {
		if _, err := normalizeProxy(proxy); err != nil {
			return fmt.Errorf("主流程代理第 %d 条无效: %w", index+1, err)
		}
	}
	for index, proxy := range ParseProxyPool(options.PromotionProxy) {
		if _, err := normalizeProxy(proxy); err != nil {
			return fmt.Errorf("Promotion 代理第 %d 条无效: %w", index+1, err)
		}
	}
	return nil
}

// ParseProxyPool accepts one proxy per line, or comma / semicolon separated
// values in a single field. Blank lines and # comments are ignored so the UI
// can keep multi-select pools in the existing proxy string fields.
func ParseProxyPool(raw string) []string {
	text := strings.ReplaceAll(raw, "\r\n", "\n")
	text = strings.ReplaceAll(text, "\r", "\n")
	text = strings.ReplaceAll(text, ";", "\n")
	text = strings.ReplaceAll(text, ",", "\n")
	seen := map[string]bool{}
	pool := make([]string, 0)
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if seen[line] {
			continue
		}
		seen[line] = true
		pool = append(pool, line)
	}
	return pool
}

func ParseRegionPool(raw string) []string {
	text := strings.ReplaceAll(raw, "\r\n", "\n")
	text = strings.ReplaceAll(text, "\r", "\n")
	text = strings.ReplaceAll(text, ";", "\n")
	text = strings.ReplaceAll(text, ",", "\n")
	text = strings.ReplaceAll(text, "|", "\n")
	text = strings.ReplaceAll(text, " ", "\n")
	seen := map[string]bool{}
	pool := make([]string, 0)
	for _, part := range strings.Split(text, "\n") {
		region := normalizeRegion(part)
		if region == "" || seen[region] {
			continue
		}
		seen[region] = true
		pool = append(pool, region)
	}
	return pool
}

func PickPoolValue(pool []string, attempt int) string {
	if len(pool) == 0 {
		return ""
	}
	if attempt < 1 {
		attempt = 1
	}
	return pool[(attempt-1)%len(pool)]
}

func SelectAttemptOptions(options Options, attempt int) Options {
	selected := options
	mainPool := ParseProxyPool(options.Proxy)
	if len(mainPool) > 0 {
		selected.Proxy = PickPoolValue(mainPool, attempt)
	}
	promotionPool := ParseProxyPool(options.PromotionProxy)
	if len(promotionPool) > 0 {
		selected.PromotionProxy = PickPoolValue(promotionPool, attempt)
		selected.PromotionProxyRegion = firstNonEmpty(proxyConfiguredRegion(selected.PromotionProxy), selected.PromotionProxyRegion)
	} else {
		regionPool := ParseRegionPool(options.PromotionProxyRegion)
		if len(regionPool) > 0 {
			selected.PromotionProxyRegion = PickPoolValue(regionPool, attempt)
		}
	}
	return selected
}

func ResolveAttemptStageProxies(config *RuntimeConfig, options Options, method string, attempt int) (StageProxies, error) {
	return ResolveStageProxies(config, SelectAttemptOptions(options, attempt), method)
}

func ResolveStageProxies(config *RuntimeConfig, options Options, method string) (StageProxies, error) {
	_ = config
	mode := strings.ToLower(strings.TrimSpace(options.ProxyMode))
	if mode == "" {
		mode = "custom"
	}
	mainPool := ParseProxyPool(options.Proxy)
	baseProxy := ""
	if len(mainPool) > 0 {
		baseProxy = mainPool[0]
	}
	switch mode {
	case "custom", "manual", "cliproxy", "dynamic", "residential":
		// Extraction proxies are request-scoped. Never pull a saved system or
		// regional default into a batch that did not explicitly provide one.
	case "regional", "mihomo", "local", "system", "default":
		return StageProxies{}, errors.New("提炼任务没有默认代理：请手动填写主流程代理")
	default:
		return StageProxies{}, fmt.Errorf("不支持的代理模式: %s", mode)
	}
	if strings.TrimSpace(baseProxy) == "" {
		return StageProxies{}, errors.New("提炼任务拒绝直连：请手动填写主流程代理")
	}
	// Keep ResolveStageProxies single-value for the first pool entry. Full-chain
	// retries should call ResolveAttemptStageProxies / SelectAttemptOptions so
	// later attempts can rotate through the remaining main or promotion proxies.
	if len(mainPool) > 1 {
		options.Proxy = baseProxy
	}
	promotionPool := ParseProxyPool(options.PromotionProxy)
	if len(promotionPool) > 0 {
		options.PromotionProxy = promotionPool[0]
	}
	regionPool := ParseRegionPool(options.PromotionProxyRegion)
	if len(regionPool) > 0 && strings.TrimSpace(options.PromotionProxy) == "" {
		options.PromotionProxyRegion = regionPool[0]
	}
	if NormalizeMethod(method) == MethodKakao && NormalizeKakaoMode(options.KakaoMode) == KakaoModeProviderLink {
		return resolveKakaoProviderLinkProxies(baseProxy, options)
	}
	regions := defaultStageRegions(method, options)
	resolve := func(explicit, region, stage string) (string, string, error) {
		candidate := firstNonEmpty(explicit, baseProxy)
		// Region selectors live in the proxy username/password. Raw proxy values
		// such as host:port:user:pass are not URL-parsable yet, so rewriting them
		// before normalization silently leaves the original exit country in
		// place while the UI label claims the requested region. Normalize first,
		// then derive the regional and request-scoped identity.
		normalizedCandidate, err := normalizeProxy(candidate)
		if err != nil {
			return "", "", fmt.Errorf("%s 代理无效: %w", stage, err)
		}
		candidate = rewriteProxyRegion(normalizedCandidate, region)
		candidate = freshProxyIdentity(candidate)
		normalized, err := normalizeProxy(candidate)
		if err != nil {
			return "", "", fmt.Errorf("%s 代理无效: %w", stage, err)
		}
		return normalized, proxyLabel(normalized, region), nil
	}
	checkout, checkoutLabel, err := resolve("", regions.checkout, "Checkout")
	if err != nil {
		return StageProxies{}, err
	}
	promotion, promotionLabel := "", ""
	reuseKakaoSeed := NormalizeMethod(method) == MethodKakao && strings.TrimSpace(options.PromotionProxy) == ""
	if NormalizeMethod(method) == MethodKakao && strings.TrimSpace(options.PromotionProxy) != "" {
		normalizedBase, baseErr := normalizeProxy(baseProxy)
		normalizedPromotion, promotionErr := normalizeProxy(options.PromotionProxy)
		reuseKakaoSeed = baseErr == nil && promotionErr == nil && normalizedBase == normalizedPromotion
	}
	if reuseKakaoSeed {
		// The Kakao promotion hop must preserve the checkout sticky Seed and
		// only switch its country selector. Generating another sid here makes
		// checkout/update unrelated to the KR checkout even when the caller
		// supplied the same proxy seed for both fields.
		promotion = rewriteProxyRegion(checkout, regions.promotion)
		promotion, err = normalizeProxy(promotion)
		if err != nil {
			return StageProxies{}, fmt.Errorf("Promotion 代理无效: %w", err)
		}
		promotionLabel = proxyLabel(promotion, regions.promotion) + "（复用 Checkout sticky Seed）"
	} else {
		promotion, promotionLabel, err = resolve(options.PromotionProxy, regions.promotion, "Promotion")
		if err != nil {
			return StageProxies{}, err
		}
	}
	provider, providerLabel := "", ""
	approve, approveLabel := "", ""
	if NormalizeMethod(method) == MethodPayPalBA && options.PayPalSameStickyIP {
		provider, approve = checkout, checkout
		providerLabel = checkoutLabel + "（复用 Checkout sticky IP）"
		approveLabel = checkoutLabel + "（复用 Checkout sticky IP）"
	} else if NormalizeMethod(method) == MethodKakao {
		// Kakao checkout eligibility and Stripe/NicePay state are tied to the
		// exact sticky proxy identity. Reuse the checkout proxy instead of
		// generating separate KR identities for provider/approve.
		provider, approve = checkout, checkout
		providerLabel = checkoutLabel + "（复用 Checkout）"
		approveLabel = checkoutLabel + "（复用 Checkout）"
	} else {
		provider, providerLabel, err = resolve("", regions.provider, "Provider")
		if err != nil {
			return StageProxies{}, err
		}
		approve, approveLabel, err = resolve("", regions.approve, "Approve")
		if err != nil {
			return StageProxies{}, err
		}
	}
	return StageProxies{
		Checkout: checkout, Promotion: promotion, Provider: provider, Approve: approve,
		Labels: map[string]string{"checkout": checkoutLabel, "promotion": promotionLabel, "provider": providerLabel, "approve": approveLabel},
	}, nil
}

func resolveKakaoProviderLinkProxies(baseProxy string, options Options) (StageProxies, error) {
	checkout, err := normalizeProxy(baseProxy)
	if err != nil {
		return StageProxies{}, fmt.Errorf("Checkout 代理无效: %w", err)
	}
	checkoutRegion := proxyConfiguredRegion(checkout)
	if checkoutRegion != "" && checkoutRegion != "KR" {
		return StageProxies{}, fmt.Errorf("Kakao 主流程代理出口选择器必须为 KR，当前为 %s", checkoutRegion)
	}

	promotion := ""
	promotionRegion := ""
	promotionLabelSuffix := "（从主代理保持 sticky Seed 派生）"
	if strings.TrimSpace(options.PromotionProxy) != "" {
		// Explicit means exact: normalize syntax only. Never rewrite its region or
		// replace a caller-provided sticky SID.
		promotion, err = normalizeProxy(options.PromotionProxy)
		if err != nil {
			return StageProxies{}, fmt.Errorf("Promotion 代理无效: %w", err)
		}
		promotionRegion = proxyConfiguredRegion(promotion)
		promotionLabelSuffix = "（显式代理原样使用）"
	} else {
		promotionRegion = firstNonEmpty(PickPoolValue(ParseRegionPool(options.PromotionProxyRegion), 1), normalizeRegion(options.PromotionProxyRegion))
		if promotionRegion == "" {
			return StageProxies{}, errors.New("Kakao 支付链未配置优惠代理或优惠地区：请勾选 JP/VN/TR 或填写优惠代理")
		}
		promotion = rewriteProxyRegion(checkout, promotionRegion)
		promotion, err = normalizeProxy(promotion)
		if err != nil {
			return StageProxies{}, fmt.Errorf("Promotion 代理无效: %w", err)
		}
	}

	checkoutLabelRegion := firstNonEmpty(checkoutRegion, "KR")
	promotionLabel := proxyLabel(promotion, promotionRegion)
	if promotionRegion == "" {
		parsed, parseErr := url.Parse(promotion)
		if parseErr == nil {
			promotionLabel = fmt.Sprintf("CUSTOM / %s://%s:%s", parsed.Scheme, parsed.Hostname(), parsed.Port())
		}
	}
	return StageProxies{
		Checkout:  checkout,
		Promotion: promotion,
		Provider:  checkout,
		Approve:   checkout,
		Labels: map[string]string{
			"checkout":  proxyLabel(checkout, checkoutLabelRegion) + "（显式主代理）",
			"promotion": promotionLabel + promotionLabelSuffix,
			"provider":  proxyLabel(checkout, checkoutLabelRegion) + "（复用 Checkout）",
			"approve":   proxyLabel(checkout, checkoutLabelRegion) + "（复用 Checkout）",
		},
	}, nil
}

type stageRegions struct{ checkout, promotion, provider, approve string }

func defaultStageRegions(method string, options Options) stageRegions {
	method = NormalizeMethod(method)
	defaultRegion := normalizeRegion(firstNonEmpty(options.Country, "US"))
	checkout, promotion, provider, approve := defaultRegion, defaultRegion, defaultRegion, defaultRegion
	switch method {
	case MethodPayPalBA:
		// PayPal billing country is independent from the proxy exit. If the
		// supplied proxy embeds a region selector, preserve that selector instead
		// of rewriting it from the billing-country dropdown.
		proxyRegion := proxyConfiguredRegion(options.Proxy)
		checkout = firstNonEmpty(proxyRegion, normalizeRegion(firstNonEmpty(options.Country, "US")))
		provider, approve = checkout, checkout
		promotion = firstNonEmpty(proxyConfiguredRegion(options.PromotionProxy), checkout)
	case MethodDirect:
		checkout = normalizeRegion(firstNonEmpty(options.Country, "PH"))
		promotion, provider, approve = checkout, checkout, checkout
	case MethodPH:
		// The verified PH/PHP short-link route uses a US checkout exit and a
		// separate TR promotion exit. Billing remains PH/PHP. Preserve explicit
		// country selectors from caller-supplied pools instead of silently
		// rewriting US/TR proxies to PH/PH.
		checkout = firstNonEmpty(proxyConfiguredRegion(options.Proxy), "US")
		promotion = firstNonEmpty(proxyConfiguredRegion(options.PromotionProxy), "TR")
		provider, approve = checkout, checkout
	case MethodMoMo:
		checkout, promotion, provider, approve = "VN", "VN", "VN", "VN"
	case MethodKakao:
		checkout, promotion, provider, approve = "KR", "TR", "KR", "KR"
	case MethodUPI:
		checkout, promotion, provider, approve = "IN", "VN", "IN", "IN"
	case MethodIDEAL:
		checkout, promotion, provider, approve = "NL", "NL", "NL", "NL"
	case MethodGoPay:
		checkout, promotion, provider, approve = "ID", "ID", "ID", "ID"
	case MethodPIX:
		checkout, promotion, provider, approve = "BR", "VN", "BR", "BR"
	case MethodBLIK:
		checkout, promotion, provider, approve = "PL", "PL", "PL", "PL"
	case MethodTWINT:
		checkout, promotion, provider, approve = "CH", "VN", "CH", "CH"
	}
	// Billing country and proxy-stage country are separate axes. This is
	// especially important for PayPal promotion combinations such as TH+TR:
	// the checkout remains TH/THB while only checkout/update uses a TR exit.
	checkout = firstNonEmpty(normalizeRegion(options.CheckoutProxyRegion), checkout)
	promotion = firstNonEmpty(PickPoolValue(ParseRegionPool(options.PromotionProxyRegion), 1), normalizeRegion(options.PromotionProxyRegion), promotion)
	provider = firstNonEmpty(normalizeRegion(options.ProviderProxyRegion), provider)
	approve = firstNonEmpty(normalizeRegion(options.ApproveProxyRegion), approve)
	return stageRegions{checkout: checkout, promotion: promotion, provider: provider, approve: approve}
}

func normalizeRegion(value string) string {
	region := strings.ToUpper(strings.TrimSpace(value))
	switch region {
	case "UK":
		return "GB"
	case "USA":
		return "US"
	case "JAPAN":
		return "JP"
	case "KOREA":
		return "KR"
	}
	if len(region) == 2 {
		return region
	}
	return ""
}

func normalizeProxy(raw string) (string, error) {
	value := strings.Trim(strings.TrimSpace(raw), "\"'")
	if value == "" {
		return "", errors.New("代理为空")
	}
	if !strings.Contains(value, "://") {
		parts := strings.Split(value, ":")
		if len(parts) >= 4 {
			host, port, username := parts[0], parts[1], parts[2]
			password := strings.Join(parts[3:], ":")
			value = "http://" + url.QueryEscape(username) + ":" + url.QueryEscape(password) + "@" + host + ":" + port
		} else {
			value = "http://" + value
		}
	}
	parsed, err := url.Parse(value)
	if err != nil || parsed.Hostname() == "" || parsed.Port() == "" {
		return "", errors.New("格式应为 http://user:pass@host:port 或 host:port")
	}
	scheme := strings.ToLower(parsed.Scheme)
	if scheme != "http" && scheme != "https" && scheme != "socks5" && scheme != "socks5h" {
		return "", fmt.Errorf("不支持协议 %s", parsed.Scheme)
	}
	return parsed.String(), nil
}

var proxyRegionPattern = regexp.MustCompile(`(?i)(^|[-_])((?:region|country|cc|res)-)([a-z]{2})([-_]|$)`)

func proxyConfiguredRegion(raw string) string {
	normalized, err := normalizeProxy(raw)
	if err != nil {
		return ""
	}
	parsed, err := url.Parse(normalized)
	if err != nil || parsed.User == nil {
		return ""
	}
	values := []string{parsed.User.Username()}
	if password, ok := parsed.User.Password(); ok {
		values = append(values, password)
	}
	for _, value := range values {
		match := proxyRegionPattern.FindStringSubmatch(value)
		if len(match) >= 4 {
			return normalizeRegion(match[3])
		}
	}
	return ""
}

func rewriteProxyRegion(raw, region string) string {
	region = normalizeRegion(region)
	if region == "" {
		return raw
	}
	parsed, err := url.Parse(raw)
	if err != nil || parsed.User == nil {
		return raw
	}
	username := parsed.User.Username()
	password, _ := parsed.User.Password()
	updated := proxyRegionPattern.ReplaceAllStringFunc(username, func(match string) string {
		parts := proxyRegionPattern.FindStringSubmatch(match)
		if len(parts) < 5 {
			return match
		}
		return parts[1] + parts[2] + region + parts[4]
	})
	if updated == username {
		return raw
	}
	if password != "" {
		parsed.User = url.UserPassword(updated, password)
	} else {
		parsed.User = url.User(updated)
	}
	return parsed.String()
}

var sidPattern = regexp.MustCompile(`(?i)sid-[^-:@]*-t-`)

func freshProxyIdentity(raw string) string {
	random := randomHex(6)
	result := strings.NewReplacer(
		"{{sid}}", random, "{sid}", random,
		"{{uuid}}", randomHex(16), "{uuid}", randomHex(16),
		"{{ts}}", stringValue(time.Now().Unix()), "{ts}", stringValue(time.Now().Unix()),
	).Replace(raw)
	if sidPattern.MatchString(result) {
		result = sidPattern.ReplaceAllString(result, "sid-"+random+"-t-")
	}
	return result
}

func randomHex(bytesCount int) string {
	if bytesCount < 1 {
		bytesCount = 1
	}
	buffer := make([]byte, bytesCount)
	if _, err := rand.Read(buffer); err != nil {
		return fmt.Sprintf("%x", time.Now().UnixNano())
	}
	return hex.EncodeToString(buffer)
}

func proxyLabel(raw, requestedRegion string) string {
	parsed, err := url.Parse(raw)
	if err != nil {
		return normalizeRegion(requestedRegion) + " / custom"
	}
	host := parsed.Hostname()
	port := parsed.Port()
	return fmt.Sprintf("%s / %s://%s:%s", normalizeRegion(requestedRegion), parsed.Scheme, host, port)
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}
