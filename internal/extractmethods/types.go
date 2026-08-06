package extractmethods

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"time"
)

const (
	MethodPayPalBA  = "paypal_ba"
	MethodDirect    = "direct_card"
	MethodPH        = "ph_link"
	MethodMoMo      = "momo"
	MethodKakao     = "kakao"
	MethodUPI       = "upi"
	MethodIDEAL     = "ideal"
	MethodGoPay     = "gopay"
	MethodPIX       = "pix"
	MethodBLIK      = "blik"
	MethodTWINT     = "twint"
	DefaultMethod   = MethodPayPalBA
	DefaultMaxItems = 500

	KakaoModeEligibility  = "eligibility"
	KakaoModeProviderLink = "provider_link"

	// AmountGateStrictZero remains the default for checkout-link extraction.
	// The other gates are opt-in and only used by link-only methods.
	AmountGateStrictZero = "strict_zero"
	AmountGateAtMost     = "at_most"
	AmountGateAtLeast    = "at_least"
	AmountGateAnyKnown   = "any_known"

	CountryModeSingle                = "single"
	CountryModeRandom                = "random"
	AssignmentStrategyRandomBalanced = "random_balanced"
)

type Method struct {
	ID                    string   `json:"id"`
	Name                  string   `json:"name"`
	Label                 string   `json:"label"`
	Description           string   `json:"description"`
	Kind                  string   `json:"kind"`
	Countries             []string `json:"countries"`
	AdaptedCountries      []string `json:"adaptedCountries,omitempty"`
	Primary               bool     `json:"primary,omitempty"`
	SupportsConcurrency   bool     `json:"supportsConcurrency"`
	SupportsPaymentStatus bool     `json:"supportsPaymentStatus"`
	Implementation        string   `json:"implementation"`
	Modes                 []string `json:"modes,omitempty"`
}

var catalog = []Method{
	{
		ID: MethodPayPalBA, Name: "PayPal BA", Label: "PP 提炼", Kind: "provider_link", Primary: true,
		Description:         "创建 PayPal Billing Agreement approve 链；包含 requires_approval、approve、reconfirm 与 Stripe 轮询。",
		Countries:           []string{"US", "GB", "DE", "FR", "NL", "CA", "AU", "IN", "PH", "TH", "BA", "AE", "BR", "TR", "VN", "JP", "BH", "MX"},
		AdaptedCountries:    []string{"US", "GB", "DE", "FR", "NL", "CA", "AU", "IN", "PH", "TH", "BA", "AE", "BR", "TR", "VN", "JP", "BH", "MX"},
		SupportsConcurrency: true, SupportsPaymentStatus: true, Implementation: "go",
	},
	{
		ID: MethodDirect, Name: "Direct Card", Label: "直卡", Kind: "checkout_link",
		Description:         "直卡 checkout 提炼；先创建 checkout，再按配置更新优惠并校验金额。",
		Countries:           []string{"US", "GB", "DE", "FR", "NL", "CA", "AU", "IN", "PH", "TH", "BA", "AE", "BR", "TR", "VN", "JP", "BH", "MX"},
		SupportsConcurrency: true, SupportsPaymentStatus: true, Implementation: "go",
	},
	{
		ID: MethodPH, Name: "Philippines PHP", Label: "菲律宾 PHP", Kind: "checkout_link",
		Description: "固定 PH / PHP 的 checkout 链接提炼。",
		Countries:   []string{"PH"}, SupportsConcurrency: true, SupportsPaymentStatus: true, Implementation: "go",
	},
	{
		ID: MethodMoMo, Name: "Vietnam MoMo", Label: "MoMo 资格", Kind: "eligibility",
		Description: "检测 VN trial checkout、实际 trial 标记及 MoMo payment method 可用性。",
		Countries:   []string{"VN"}, SupportsConcurrency: true, SupportsPaymentStatus: true, Implementation: "go",
	},
	{
		ID: MethodKakao, Name: "Korea Kakao Pay", Label: "Kakao Pay", Kind: "eligibility_or_provider_link",
		Description: "可配置批量与并发：观察 KR checkout 的上游 kakao_pay 资格，或在上游真实展示后提炼 NicePay/Kakao 待支付长链。",
		Countries:   []string{"KR"}, SupportsConcurrency: true, SupportsPaymentStatus: true, Implementation: "go",
		Modes: []string{KakaoModeEligibility, KakaoModeProviderLink},
	},
	{
		ID: MethodUPI, Name: "India UPI", Label: "UPI", Kind: "qr_link",
		Description: "执行 IN checkout、Elements、tax region、snapshot、approve 与 UPI QR/指令链接提炼。",
		Countries:   []string{"IN"}, SupportsConcurrency: true, SupportsPaymentStatus: true, Implementation: "go",
	},
	{
		ID: MethodIDEAL, Name: "Netherlands iDEAL", Label: "iDEAL", Kind: "provider_link",
		Description: "执行 NL checkout、iDEAL payment method、approve、redirect 与支付状态检测。",
		Countries:   []string{"NL"}, SupportsConcurrency: true, SupportsPaymentStatus: true, Implementation: "go",
	},
	{
		ID: MethodGoPay, Name: "Indonesia GoPay", Label: "GoPay", Kind: "provider_link",
		Description: "执行 ID checkout、GoPay payment method、approve 与 provider redirect 提炼。",
		Countries:   []string{"ID"}, SupportsConcurrency: true, SupportsPaymentStatus: true, Implementation: "go",
	},
	{
		ID: MethodPIX, Name: "Brazil PIX", Label: "PIX", Kind: "qr_link",
		Description: "执行 BR / BRL checkout，确认 PIX 后提取真实 QR payload、图片或 hosted instructions。",
		Countries:   []string{"BR"}, SupportsConcurrency: true, SupportsPaymentStatus: true, Implementation: "go",
	},
	{
		ID: MethodBLIK, Name: "Poland BLIK", Label: "BLIK", Kind: "provider_link",
		Description: "执行 PL / PLN checkout，使用 BLIK payment method 与可选 6 位验证码提取渠道跳转。",
		Countries:   []string{"PL"}, SupportsConcurrency: true, SupportsPaymentStatus: true, Implementation: "go",
	},
	{
		ID: MethodTWINT, Name: "Swiss TWINT", Label: "TWINT", Kind: "provider_link",
		Description: "执行 CH / CHF checkout，确认 TWINT 后提取 twint.ch 或 Stripe provider 跳转。",
		Countries:   []string{"CH"}, SupportsConcurrency: true, SupportsPaymentStatus: true, Implementation: "go",
	},
}

func Catalog() []Method {
	result := make([]Method, len(catalog))
	for index, method := range catalog {
		result[index] = method
		result[index].Countries = append([]string(nil), method.Countries...)
		result[index].AdaptedCountries = append([]string(nil), method.AdaptedCountries...)
		result[index].Modes = append([]string(nil), method.Modes...)
	}
	return result
}

func NormalizeKakaoMode(value string) string {
	normalized := strings.ToLower(strings.TrimSpace(value))
	switch normalized {
	case "", "eligibility", "eligible", "probe", "diagnostic":
		return KakaoModeEligibility
	case "provider_link", "provider-link", "link", "extract", "extraction":
		return KakaoModeProviderLink
	default:
		return normalized
	}
}

func validateKakaoMode(value string) error {
	mode := NormalizeKakaoMode(value)
	if mode != KakaoModeEligibility && mode != KakaoModeProviderLink {
		return fmt.Errorf("不支持的 Kakao 模式: %s；可用值为 eligibility 或 provider_link", strings.TrimSpace(value))
	}
	return nil
}

func NormalizeMethod(value string) string {
	normalized := strings.ToLower(strings.TrimSpace(value))
	switch normalized {
	case "", "pp", "paypal", "paypal-ba", "paypal_ba":
		return MethodPayPalBA
	case "paper_card", "paper-card", "card", "direct", "direct-card", "direct_card":
		return MethodDirect
	case "ph", "philippines", "philippines_php", "ph-link", "ph_link":
		return MethodPH
	case "momo", "vn_momo":
		return MethodMoMo
	case "kakao", "kakao_pay", "kakao-pay":
		return MethodKakao
	case "upi", "india_upi":
		return MethodUPI
	case "ideal", "ideal_nl", "ideal-nl":
		return MethodIDEAL
	case "gopay", "go-pay", "go_pay":
		return MethodGoPay
	case "pix", "br_pix", "pix_br":
		return MethodPIX
	case "blik", "pl_blik", "blik_pl":
		return MethodBLIK
	case "twint", "ch_twint", "twint_ch":
		return MethodTWINT
	default:
		return normalized
	}
}

func LookupMethod(id string) (Method, bool) {
	normalized := NormalizeMethod(id)
	for _, method := range catalog {
		if method.ID == normalized {
			return method, true
		}
	}
	return Method{}, false
}

type BatchRequest struct {
	Method      string          `json:"method"`
	Input       string          `json:"input"`
	Items       json.RawMessage `json:"items,omitempty"`
	Concurrency int             `json:"concurrency"`
	Options     Options         `json:"options"`
}

type Options struct {
	Country                  string            `json:"country"`
	RequestedCountry         string            `json:"requestedCountry,omitempty"`
	CountryFallback          bool              `json:"countryFallback,omitempty"`
	Currency                 string            `json:"currency"`
	CountryMode              string            `json:"countryMode,omitempty"`
	CountryPool              []string          `json:"countryPool,omitempty"`
	AssignmentStrategy       string            `json:"assignmentStrategy,omitempty"`
	AssignmentSeed           string            `json:"assignmentSeed,omitempty"`
	ProxyMode                string            `json:"proxyMode"`
	ProxyRegion              string            `json:"proxyRegion"`
	Proxy                    string            `json:"proxy"`
	CheckoutProxy            string            `json:"checkoutProxy"`
	PromotionProxy           string            `json:"promotionProxy"`
	ProviderProxy            string            `json:"providerProxy"`
	ApproveProxy             string            `json:"approveProxy"`
	CheckoutProxyRegion      string            `json:"checkoutProxyRegion"`
	PromotionProxyRegion     string            `json:"promotionProxyRegion"`
	ProviderProxyRegion      string            `json:"providerProxyRegion"`
	ApproveProxyRegion       string            `json:"approveProxyRegion"`
	CountryProxies           map[string]string `json:"countryProxies,omitempty"`
	CountryPromotionProxies  map[string]string `json:"countryPromotionProxies,omitempty"`
	UsePromo                 *bool             `json:"usePromo,omitempty"`
	PromoCampaignID          string            `json:"promoCampaignId"`
	TrialDays                int               `json:"trialDays"`
	TimeoutSeconds           int               `json:"timeoutSeconds"`
	MaxAttempts              int               `json:"maxAttempts"`
	ApproveAttempts          int               `json:"approveAttempts"`
	AmountGate               string            `json:"amountGate,omitempty"`
	AmountThresholdMinor     int               `json:"amountThresholdMinor,omitempty"`
	AllowUnknownAmount       bool              `json:"allowUnknownAmount,omitempty"`
	MaxAmountMinor           int               `json:"maxAmountMinor"`
	StripePublishableKey     string            `json:"stripePublishableKey"`
	ClientFingerprint        string            `json:"clientFingerprint"`
	FingerprintPolicy        map[string]string `json:"fingerprintPolicy,omitempty"`
	FingerprintWeightMode    *bool             `json:"fingerprintWeightMode,omitempty"`
	PaymentStatusAutoRefresh bool              `json:"paymentStatusAutoRefresh"`
	PayPalSameStickyIP       bool              `json:"paypalSameStickyIp"`
	KakaoMode                string            `json:"kakaoMode"`
	KakaoEligibilityOnly     bool              `json:"kakaoEligibilityOnly"`
	BlikCode                 string            `json:"blikCode,omitempty"`
}

func (o Options) PromoEnabled() bool {
	return o.UsePromo == nil || *o.UsePromo
}

func (o Options) Timeout() time.Duration {
	seconds := o.TimeoutSeconds
	if seconds < 5 {
		seconds = 45
	}
	if seconds > 180 {
		seconds = 180
	}
	return time.Duration(seconds) * time.Second
}

func (o Options) Attempts() int {
	attempts := o.MaxAttempts
	if attempts < 1 {
		attempts = 3
	}
	if attempts > 100 {
		attempts = 100
	}
	return attempts
}

func (o Options) ApproveRetries() int {
	attempts := o.ApproveAttempts
	if attempts < 1 {
		attempts = 3
	}
	if attempts > 10 {
		attempts = 10
	}
	return attempts
}

func normalizeFingerprintMode(value, defaultMode string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "fresh", "new", "rotate", "independent", "random":
		return "fresh"
	case "follow", "main", "shared", "reuse", "same", "inherit":
		return "follow"
	default:
		if defaultMode == "" {
			return "follow"
		}
		return defaultMode
	}
}

func (o Options) StageFingerprintPolicy() map[string]string {
	policy := map[string]string{
		"checkout":  "main",
		"promotion": "follow",
		"provider":  "follow",
		"approve":   "follow",
	}
	for stage, mode := range o.FingerprintPolicy {
		stage = strings.ToLower(strings.TrimSpace(stage))
		if stage == "" || stage == "checkout" {
			continue
		}
		if _, ok := policy[stage]; !ok {
			continue
		}
		policy[stage] = normalizeFingerprintMode(mode, policy[stage])
	}
	return policy
}

type amountGate struct {
	Mode         string
	Threshold    int64
	AllowUnknown bool
}

func normalizeAmountGate(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "", "zero", "strict", "strict_zero", "exact_zero", "exactly_zero":
		return AmountGateStrictZero
	case "at_most", "max", "maximum", "under", "up_to", "within_limit":
		return AmountGateAtMost
	case "at_least", "min", "minimum", "over", "above", "from":
		return AmountGateAtLeast
	case "any", "any_known", "known", "allow_any":
		return AmountGateAnyKnown
	default:
		return AmountGateStrictZero
	}
}

func (o Options) amountGateConfig() amountGate {
	rawMode := strings.ToLower(strings.TrimSpace(o.AmountGate))
	threshold := int64(o.AmountThresholdMinor)
	if threshold < 0 {
		threshold = 0
	}
	// Older clients only send maxAmountMinor. A positive legacy value meant an
	// upper bound; zero in current clients remains the strict-zero default.
	if rawMode == "" && o.MaxAmountMinor > 0 {
		return amountGate{Mode: AmountGateAtMost, Threshold: int64(o.MaxAmountMinor), AllowUnknown: o.AllowUnknownAmount}
	}
	if threshold == 0 && o.MaxAmountMinor > 0 {
		threshold = int64(o.MaxAmountMinor)
	}
	return amountGate{Mode: normalizeAmountGate(rawMode), Threshold: threshold, AllowUnknown: o.AllowUnknownAmount}
}

func (o Options) AmountLimit() int64 {
	gate := o.amountGateConfig()
	if gate.Mode == AmountGateAtMost {
		return gate.Threshold
	}
	if gate.Mode == AmountGateStrictZero {
		return 0
	}
	return -1
}

type Step struct {
	At        string `json:"at"`
	Stage     string `json:"stage"`
	Status    string `json:"status"`
	Detail    string `json:"detail,omitempty"`
	ElapsedMs int64  `json:"elapsedMs,omitempty"`
}

type ProgressFunc func(step Step)

type Result struct {
	OK                    bool           `json:"ok"`
	Method                string         `json:"method"`
	Country               string         `json:"country,omitempty"`
	Currency              string         `json:"currency,omitempty"`
	CheckoutID            string         `json:"checkoutId,omitempty"`
	CheckoutType          string         `json:"checkoutType,omitempty"`
	ProcessorEntity       string         `json:"processorEntity,omitempty"`
	PaymentMethodID       string         `json:"paymentMethodId,omitempty"`
	StripeRedirectURL     string         `json:"stripeRedirectUrl,omitempty"`
	ProviderRedirectURL   string         `json:"providerRedirectUrl,omitempty"`
	LongURL               string         `json:"longUrl,omitempty"`
	LinkGeneratedAt       string         `json:"linkGeneratedAt,omitempty"`
	ExpiresAt             string         `json:"expiresAt,omitempty"`
	LinkTTLSeconds        int            `json:"linkTtlSeconds,omitempty"`
	UPIPayload            string         `json:"upiPayload,omitempty"`
	UPIInstructionURL     string         `json:"upiInstructionUrl,omitempty"`
	PaymentPayload        string         `json:"paymentPayload,omitempty"`
	PaymentInstructionURL string         `json:"paymentInstructionUrl,omitempty"`
	QRPNGURL              string         `json:"qrPngUrl,omitempty"`
	QRSVGURL              string         `json:"qrSvgUrl,omitempty"`
	Amount                string         `json:"amount,omitempty"`
	AmountDisplay         string         `json:"amountDisplay,omitempty"`
	AmountStatus          string         `json:"amountStatus,omitempty"`
	ExtractionStatus      string         `json:"extractionStatus"`
	PaymentStatus         string         `json:"paymentStatus"`
	Decision              string         `json:"decision,omitempty"`
	AvailableMethods      []string       `json:"availableMethods,omitempty"`
	Steps                 []Step         `json:"steps,omitempty"`
	Metadata              map[string]any `json:"metadata,omitempty"`
}

func NewStep(stage, status, detail string, elapsed time.Duration) Step {
	step := Step{At: time.Now().UTC().Format(time.RFC3339), Stage: stage, Status: status, Detail: trimDetail(detail, 900)}
	if elapsed > 0 {
		step.ElapsedMs = elapsed.Milliseconds()
	}
	return step
}

func trimDetail(value string, limit int) string {
	text := strings.TrimSpace(strings.ReplaceAll(strings.ReplaceAll(value, "\r", " "), "\n", " "))
	if limit > 0 && len(text) > limit {
		return text[:limit] + "..."
	}
	return text
}

func stringValue(value any) string {
	switch item := value.(type) {
	case string:
		return strings.TrimSpace(item)
	case json.Number:
		return item.String()
	case float64:
		return strconv.FormatFloat(item, 'f', -1, 64)
	case float32:
		return strconv.FormatFloat(float64(item), 'f', -1, 32)
	case int:
		return strconv.Itoa(item)
	case int64:
		return strconv.FormatInt(item, 10)
	case bool:
		return strconv.FormatBool(item)
	default:
		if value == nil {
			return ""
		}
		return strings.TrimSpace(fmt.Sprint(value))
	}
}
