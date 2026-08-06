package extractapi

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/netip"
	"strconv"
	"strings"
	"time"

	"automyai/internal/extractmethods"
)

const maxRequestBody = 8 << 20

type Server struct {
	manager *extractmethods.JobManager
}

func NewServer(manager *extractmethods.JobManager) *Server {
	return &Server{manager: manager}
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", s.handleHealth)
	mux.HandleFunc("/api/health", s.handleHealth)
	mux.HandleFunc("/api/extract/catalog", s.handleCatalog)
	mux.HandleFunc("/api/extract/jobs", s.handleJobs)
	mux.HandleFunc("/api/extract/jobs/", s.handleJob)
	mux.HandleFunc("/api/extract-methods/catalog", s.handleCatalog)
	mux.HandleFunc("/api/extract-methods/run", s.handleCompatibilityCreate)
	mux.HandleFunc("/api/long-link-task", s.handleCompatibilityCreate)
	mux.HandleFunc("/api/extract-pp", s.handleCompatibilityCreate)
	mux.HandleFunc("/api/paper-card-task", s.handleCompatibilityCreate)
	mux.HandleFunc("/api/ph-link-task", s.handleCompatibilityCreate)
	mux.HandleFunc("/api/momo-eligibility", s.handleCompatibilityCreate)
	mux.HandleFunc("/api/kakao-long-link-task", s.handleCompatibilityCreate)
	mux.HandleFunc("/api/upi-long-link-task", s.handleCompatibilityCreate)
	mux.HandleFunc("/api/ideal-long-link-task", s.handleCompatibilityCreate)
	mux.HandleFunc("/api/gopay-long-link-task", s.handleCompatibilityCreate)
	mux.HandleFunc("/api/pix-long-link-task", s.handleCompatibilityCreate)
	mux.HandleFunc("/api/blik-long-link-task", s.handleCompatibilityCreate)
	mux.HandleFunc("/api/twint-long-link-task", s.handleCompatibilityCreate)
	mux.HandleFunc("/internal/card-portal/source-tasks", s.handleCardPortalSourceTasks)
	mux.HandleFunc("/internal/card-portal/source-session", s.handleCardPortalSourceSession)
	return s.withJSONHeaders(mux)
}

func loopbackRequest(request *http.Request) bool {
	host := request.RemoteAddr
	if addrPort, err := netip.ParseAddrPort(host); err == nil {
		return addrPort.Addr().IsLoopback()
	}
	if addr, err := netip.ParseAddr(host); err == nil {
		return addr.IsLoopback()
	}
	return false
}

func (s *Server) handleCardPortalSourceTasks(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		methodNotAllowed(writer, http.MethodGet)
		return
	}
	if !loopbackRequest(request) {
		writeError(writer, http.StatusForbidden, errors.New("仅允许本机恢复门户访问"))
		return
	}
	limit, _ := strconv.Atoi(request.URL.Query().Get("limit"))
	writeJSON(writer, http.StatusOK, map[string]any{"ok": true, "items": s.manager.ListPortalSourceTasks(limit)})
}

func (s *Server) handleCardPortalSourceSession(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		methodNotAllowed(writer, http.MethodGet)
		return
	}
	if !loopbackRequest(request) {
		writeError(writer, http.StatusForbidden, errors.New("仅允许本机恢复门户访问"))
		return
	}
	taskID := strings.TrimSpace(request.URL.Query().Get("task_id"))
	if len(taskID) != 12 {
		writeError(writer, http.StatusBadRequest, errors.New("来源任务 ID 格式无效"))
		return
	}
	session, err := s.manager.GetPortalSourceSession(taskID)
	if err != nil {
		writeError(writer, http.StatusNotFound, err)
		return
	}
	writeJSON(writer, http.StatusOK, map[string]any{"ok": true, "session": session})
}

func (s *Server) handleHealth(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		methodNotAllowed(writer, http.MethodGet)
		return
	}
	writeJSON(writer, http.StatusOK, map[string]any{
		"ok": true, "service": "automyai-extract-api", "implementation": "go",
		"time": time.Now().UTC().Format(time.RFC3339),
	})
}

func (s *Server) handleCatalog(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		methodNotAllowed(writer, http.MethodGet)
		return
	}
	writeJSON(writer, http.StatusOK, map[string]any{
		"ok": true, "defaultMethod": extractmethods.DefaultMethod, "methods": extractmethods.Catalog(),
		"limits": map[string]any{"maxItems": extractmethods.DefaultMaxItems, "maxConcurrency": 32},
	})
}

func (s *Server) handleJobs(writer http.ResponseWriter, request *http.Request) {
	switch request.Method {
	case http.MethodGet:
		limit, _ := strconv.Atoi(request.URL.Query().Get("limit"))
		writeJSON(writer, http.StatusOK, map[string]any{"ok": true, "jobs": s.manager.List(limit)})
	case http.MethodPost:
		batch, err := decodeBatchRequest(writer, request, "")
		if err != nil {
			writeError(writer, http.StatusBadRequest, err)
			return
		}
		job, err := s.manager.Create(batch)
		if err != nil {
			status := http.StatusBadRequest
			if errors.Is(err, extractmethods.ErrAccountActive) {
				status = http.StatusConflict
			}
			writeError(writer, status, err)
			return
		}
		writeJSON(writer, http.StatusAccepted, map[string]any{"ok": true, "job": job})
	default:
		methodNotAllowed(writer, http.MethodGet, http.MethodPost)
	}
}

func (s *Server) handleJob(writer http.ResponseWriter, request *http.Request) {
	remainder := strings.TrimPrefix(request.URL.Path, "/api/extract/jobs/")
	parts := strings.Split(strings.Trim(remainder, "/"), "/")
	if len(parts) == 0 || parts[0] == "" {
		writeError(writer, http.StatusNotFound, errors.New("任务不存在"))
		return
	}
	jobID := parts[0]
	if len(parts) == 1 {
		switch request.Method {
		case http.MethodGet:
			job, err := s.manager.Get(jobID)
			if err != nil {
				writeError(writer, http.StatusNotFound, err)
				return
			}
			writeJSON(writer, http.StatusOK, map[string]any{"ok": true, "job": job})
			return
		case http.MethodDelete:
			if err := s.manager.Delete(jobID); err != nil {
				status := http.StatusNotFound
				if errors.Is(err, extractmethods.ErrJobActive) {
					status = http.StatusConflict
				}
				writeError(writer, status, err)
				return
			}
			writeJSON(writer, http.StatusOK, map[string]any{"ok": true, "deleted": true, "jobId": jobID})
			return
		}
	}
	if len(parts) == 2 && parts[1] == "cancel" && request.Method == http.MethodPost {
		job, err := s.manager.Cancel(jobID)
		if err != nil {
			writeError(writer, http.StatusNotFound, err)
			return
		}
		writeJSON(writer, http.StatusOK, map[string]any{"ok": true, "job": job})
		return
	}
	if len(parts) == 2 && parts[1] == "retry" && request.Method == http.MethodPost {
		request.Body = http.MaxBytesReader(writer, request.Body, 64<<10)
		var options extractmethods.RetryOptions
		if err := json.NewDecoder(request.Body).Decode(&options); err != nil && !errors.Is(err, io.EOF) {
			writeError(writer, http.StatusBadRequest, errors.New("重跑筛选必须是有效 JSON"))
			return
		}
		job, err := s.manager.Retry(jobID, options)
		if err != nil {
			status := http.StatusConflict
			if errors.Is(err, extractmethods.ErrJobNotFound) {
				status = http.StatusNotFound
			}
			writeError(writer, status, err)
			return
		}
		writeJSON(writer, http.StatusAccepted, map[string]any{"ok": true, "job": job})
		return
	}
	if len(parts) == 2 && parts[1] == "verify-payment" && request.Method == http.MethodPost {
		request.Body = http.MaxBytesReader(writer, request.Body, 64<<10)
		var body struct {
			OnlySucceeded bool `json:"onlySucceeded"`
		}
		if err := json.NewDecoder(request.Body).Decode(&body); err != nil && !errors.Is(err, io.EOF) {
			writeError(writer, http.StatusBadRequest, errors.New("支付状态复核请求必须是有效 JSON"))
			return
		}
		job, err := s.manager.VerifyPayment(jobID, body.OnlySucceeded)
		if err != nil {
			status := http.StatusConflict
			if errors.Is(err, extractmethods.ErrJobNotFound) {
				status = http.StatusNotFound
			} else if errors.Is(err, extractmethods.ErrAccountActive) {
				status = http.StatusConflict
			}
			writeError(writer, status, err)
			return
		}
		writeJSON(writer, http.StatusOK, map[string]any{"ok": true, "job": job})
		return
	}
	if len(parts) == 2 && parts[1] == "continue-provider" && request.Method == http.MethodPost {
		request.Body = http.MaxBytesReader(writer, request.Body, 64<<10)
		var body struct {
			MaxAttempts    int    `json:"maxAttempts"`
			SubmittedJobID string `json:"submittedJobId"`
		}
		if err := json.NewDecoder(request.Body).Decode(&body); err != nil && !errors.Is(err, io.EOF) {
			writeError(writer, http.StatusBadRequest, errors.New("续跑请求必须是有效 JSON"))
			return
		}
		var (
			job *extractmethods.Job
			err error
		)
		if strings.TrimSpace(body.SubmittedJobID) != "" {
			job, err = s.manager.MarkKakaoProviderContinuationSubmitted(jobID, strings.TrimSpace(body.SubmittedJobID))
		} else {
			job, err = s.manager.RequestKakaoProviderContinuation(jobID, body.MaxAttempts)
		}
		if err != nil {
			status := http.StatusConflict
			if errors.Is(err, extractmethods.ErrJobNotFound) {
				status = http.StatusNotFound
			}
			writeError(writer, status, err)
			return
		}
		writeJSON(writer, http.StatusOK, map[string]any{"ok": true, "job": job})
		return
	}
	writeError(writer, http.StatusNotFound, errors.New("接口不存在"))
}

func (s *Server) handleCompatibilityCreate(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		methodNotAllowed(writer, http.MethodPost)
		return
	}
	forcedMethod := compatibilityMethod(request.URL.Path)
	batch, err := decodeBatchRequest(writer, request, forcedMethod)
	if err != nil {
		writeError(writer, http.StatusBadRequest, err)
		return
	}
	job, err := s.manager.Create(batch)
	if err != nil {
		status := http.StatusBadRequest
		if errors.Is(err, extractmethods.ErrAccountActive) {
			status = http.StatusConflict
		}
		writeError(writer, status, err)
		return
	}
	writeJSON(writer, http.StatusAccepted, map[string]any{
		"ok": true, "async": true, "jobId": job.ID, "job": job,
		"message": "已进入 Go 批量提炼队列",
	})
}

func compatibilityMethod(path string) string {
	switch path {
	case "/api/extract-pp":
		return extractmethods.MethodPayPalBA
	case "/api/paper-card-task":
		return extractmethods.MethodDirect
	case "/api/ph-link-task":
		return extractmethods.MethodPH
	case "/api/momo-eligibility":
		return extractmethods.MethodMoMo
	case "/api/kakao-long-link-task":
		return extractmethods.MethodKakao
	case "/api/upi-long-link-task":
		return extractmethods.MethodUPI
	case "/api/ideal-long-link-task":
		return extractmethods.MethodIDEAL
	case "/api/gopay-long-link-task":
		return extractmethods.MethodGoPay
	case "/api/pix-long-link-task":
		return extractmethods.MethodPIX
	case "/api/blik-long-link-task":
		return extractmethods.MethodBLIK
	case "/api/twint-long-link-task":
		return extractmethods.MethodTWINT
	default:
		return ""
	}
}

func decodeBatchRequest(writer http.ResponseWriter, request *http.Request, forcedMethod string) (extractmethods.BatchRequest, error) {
	request.Body = http.MaxBytesReader(writer, request.Body, maxRequestBody)
	decoder := json.NewDecoder(request.Body)
	decoder.UseNumber()
	var raw map[string]any
	if err := decoder.Decode(&raw); err != nil {
		if errors.Is(err, io.EOF) {
			return extractmethods.BatchRequest{}, errors.New("请求体不能为空")
		}
		return extractmethods.BatchRequest{}, errors.New("请求体必须是有效 JSON，且不超过 8 MiB")
	}
	encoded, _ := json.Marshal(raw)
	var batch extractmethods.BatchRequest
	_ = json.Unmarshal(encoded, &batch)
	if batch.Method == "" {
		batch.Method = stringFromMap(raw, "method", "link_type", "linkType", "type")
	}
	if forcedMethod != "" {
		batch.Method = forcedMethod
	} else if request.URL.Path == "/api/long-link-task" {
		batch.Method = methodFromLinkType(batch.Method)
	}
	if batch.Method == "" {
		batch.Method = extractmethods.DefaultMethod
	}
	if batch.Input == "" {
		batch.Input = stringFromMap(raw, "input", "accessToken", "access_token", "token", "authorization", "credentials")
	}
	if len(batch.Items) == 0 {
		for _, key := range []string{"items", "accounts", "data"} {
			if value, ok := lookupMap(raw, key); ok {
				batch.Items, _ = json.Marshal(value)
				break
			}
		}
	}
	if batch.Concurrency == 0 {
		batch.Concurrency = intFromMap(raw, "concurrency", "workers", "parallel")
	}
	mergeLegacyOptions(&batch.Options, raw)
	return batch, nil
}

func methodFromLinkType(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "", "paypal", "pp", "paypal_ba", "paypal-ba":
		return extractmethods.MethodPayPalBA
	case "ideal":
		return extractmethods.MethodIDEAL
	case "gopay":
		return extractmethods.MethodGoPay
	case "kakao", "kakao_pay":
		return extractmethods.MethodKakao
	case "upi":
		return extractmethods.MethodUPI
	case "pix":
		return extractmethods.MethodPIX
	case "blik":
		return extractmethods.MethodBLIK
	case "twint":
		return extractmethods.MethodTWINT
	default:
		return extractmethods.NormalizeMethod(value)
	}
}

func mergeLegacyOptions(options *extractmethods.Options, raw map[string]any) {
	if options.DirectRoute == "" {
		options.DirectRoute = stringFromMap(raw, "directRoute", "direct_route", "checkoutRoute", "checkout_route")
	}
	if options.Country == "" {
		options.Country = stringFromMap(raw, "country", "billingCountry", "billing_country", "checkoutCountry")
	}
	if options.Currency == "" {
		options.Currency = stringFromMap(raw, "currency")
	}
	if options.ProxyMode == "" {
		options.ProxyMode = stringFromMap(raw, "proxyMode", "proxy_mode")
	}
	if options.ProxyRegion == "" {
		options.ProxyRegion = stringFromMap(raw, "proxyRegion", "proxy_region")
	}
	if options.Proxy == "" {
		options.Proxy = stringFromMap(raw, "proxy", "proxyUrl", "proxy_url")
	}
	if options.CheckoutProxy == "" {
		options.CheckoutProxy = stringFromMap(raw, "checkoutProxy", "checkout_proxy")
	}
	if options.PromotionProxy == "" {
		options.PromotionProxy = stringFromMap(raw, "promotionProxy", "promotion_proxy")
	}
	if options.ProviderProxy == "" {
		options.ProviderProxy = stringFromMap(raw, "providerProxy", "provider_proxy")
	}
	if options.ApproveProxy == "" {
		options.ApproveProxy = stringFromMap(raw, "approveProxy", "approve_proxy")
	}
	if len(options.CountryProxies) == 0 {
		options.CountryProxies = stringMapFromMap(raw, "countryProxies", "country_proxies", "proxiesByCountry")
	}
	if len(options.CountryPromotionProxies) == 0 {
		options.CountryPromotionProxies = stringMapFromMap(raw, "countryPromotionProxies", "country_promotion_proxies", "promotionProxiesByCountry")
	}
	if options.CheckoutProxyRegion == "" {
		options.CheckoutProxyRegion = stringFromMap(raw, "checkoutProxyRegion", "checkoutProxyCountry", "checkoutCountry")
	}
	if options.PromotionProxyRegion == "" {
		options.PromotionProxyRegion = stringFromMap(raw, "promotionProxyRegion", "promotionProxyCountry", "promotionCountry")
	}
	if options.ProviderProxyRegion == "" {
		options.ProviderProxyRegion = stringFromMap(raw, "providerProxyRegion", "providerCountry")
	}
	if options.ApproveProxyRegion == "" {
		options.ApproveProxyRegion = stringFromMap(raw, "approveProxyRegion", "approveCountry")
	}
	if options.TimeoutSeconds == 0 {
		options.TimeoutSeconds = intFromMap(raw, "timeoutSeconds", "timeout")
	}
	if options.TrialDays == 0 {
		options.TrialDays = intFromMap(raw, "trialDays", "trial_days")
	}
	if options.MaxAttempts == 0 {
		options.MaxAttempts = intFromMap(raw, "maxAttempts", "max_attempts")
	}
	if options.ApproveAttempts == 0 {
		options.ApproveAttempts = intFromMap(raw, "approveAttempts", "approve_attempts", "approveRetryMax", "approve_retry_max")
		if options.ApproveAttempts == 0 {
			if nested, ok := lookupMap(raw, "options"); ok {
				if nestedMap, ok := nested.(map[string]any); ok {
					options.ApproveAttempts = intFromMap(nestedMap, "approveAttempts", "approve_attempts", "approveRetryMax", "approve_retry_max")
				}
			}
		}
	}
	if len(options.FingerprintPolicy) == 0 {
		options.FingerprintPolicy = stringMapFromMap(raw, "fingerprintPolicy", "fingerprintStages", "stageFingerprints")
		if len(options.FingerprintPolicy) == 0 {
			if nested, ok := lookupMap(raw, "options"); ok {
				if nestedMap, ok := nested.(map[string]any); ok {
					options.FingerprintPolicy = stringMapFromMap(nestedMap, "fingerprintPolicy", "fingerprintStages", "stageFingerprints")
				}
			}
		}
	}
	if options.FingerprintWeightMode == nil {
		if value, ok := boolFromMap(raw, "fingerprintWeightMode", "fingerprint_weight_mode", "weightMode"); ok {
			options.FingerprintWeightMode = &value
		} else if nested, ok := lookupMap(raw, "options"); ok {
			if nestedMap, ok := nested.(map[string]any); ok {
				if value, ok := boolFromMap(nestedMap, "fingerprintWeightMode", "fingerprint_weight_mode", "weightMode"); ok {
					options.FingerprintWeightMode = &value
				}
			}
		}
	}
	if options.MaxAmountMinor == 0 {
		options.MaxAmountMinor = intFromMap(raw, "maxAmountMinor", "max_amount_minor")
	}
	if options.AmountGate == "" {
		options.AmountGate = stringFromMap(raw, "amountGate", "amount_gate")
		if options.AmountGate == "" {
			if nested, ok := lookupMap(raw, "options"); ok {
				if nestedMap, ok := nested.(map[string]any); ok {
					options.AmountGate = stringFromMap(nestedMap, "amountGate", "amount_gate")
				}
			}
		}
	}
	if options.AmountThresholdMinor == 0 {
		options.AmountThresholdMinor = intFromMap(raw, "amountThresholdMinor", "amount_threshold_minor")
		if options.AmountThresholdMinor == 0 {
			if nested, ok := lookupMap(raw, "options"); ok {
				if nestedMap, ok := nested.(map[string]any); ok {
					options.AmountThresholdMinor = intFromMap(nestedMap, "amountThresholdMinor", "amount_threshold_minor")
				}
			}
		}
	}
	if !options.AllowUnknownAmount {
		if value, ok := boolFromMap(raw, "allowUnknownAmount", "allow_unknown_amount"); ok {
			options.AllowUnknownAmount = value
		} else if nested, ok := lookupMap(raw, "options"); ok {
			if nestedMap, ok := nested.(map[string]any); ok {
				if value, ok := boolFromMap(nestedMap, "allowUnknownAmount", "allow_unknown_amount"); ok {
					options.AllowUnknownAmount = value
				}
			}
		}
	}
	if options.StripePublishableKey == "" {
		options.StripePublishableKey = stringFromMap(raw, "stripePublishableKey", "stripePk", "stripe_pk")
	}
	if options.PromoCampaignID == "" {
		options.PromoCampaignID = stringFromMap(raw, "promoCampaignId", "promo_campaign_id")
	}
	if options.BlikCode == "" {
		options.BlikCode = stringFromMap(raw, "blikCode", "blik_code", "IDEAL_BLIK_CODE")
		if options.BlikCode == "" {
			if nested, ok := lookupMap(raw, "options"); ok {
				if nestedMap, ok := nested.(map[string]any); ok {
					options.BlikCode = stringFromMap(nestedMap, "blikCode", "blik_code", "IDEAL_BLIK_CODE")
				}
			}
		}
	}
	if options.UsePromo == nil {
		if value, ok := boolFromMap(raw, "usePromo", "use_promo"); ok {
			options.UsePromo = &value
		}
	}
}

func lookupMap(values map[string]any, wanted string) (any, bool) {
	for key, value := range values {
		if strings.EqualFold(key, wanted) {
			return value, true
		}
	}
	return nil, false
}

func stringFromMap(values map[string]any, keys ...string) string {
	for _, key := range keys {
		if value, ok := lookupMap(values, key); ok {
			switch item := value.(type) {
			case string:
				if strings.TrimSpace(item) != "" {
					return strings.TrimSpace(item)
				}
			}
		}
	}
	return ""
}

func stringMapFromMap(values map[string]any, keys ...string) map[string]string {
	for _, key := range keys {
		value, ok := lookupMap(values, key)
		if !ok || value == nil {
			continue
		}
		switch item := value.(type) {
		case map[string]any:
			result := map[string]string{}
			for stage, mode := range item {
				stage = strings.ToLower(strings.TrimSpace(stage))
				if stage == "" {
					continue
				}
				result[stage] = strings.ToLower(strings.TrimSpace(fmt.Sprint(mode)))
			}
			if len(result) > 0 {
				return result
			}
		case map[string]string:
			result := map[string]string{}
			for stage, mode := range item {
				stage = strings.ToLower(strings.TrimSpace(stage))
				if stage == "" {
					continue
				}
				result[stage] = strings.ToLower(strings.TrimSpace(mode))
			}
			if len(result) > 0 {
				return result
			}
		case string:
			// compact "promotion,approve" => those stages fresh
			result := map[string]string{}
			for _, part := range strings.FieldsFunc(item, func(r rune) bool {
				switch r {
				case ',', ';', '|', ' ', '\n', '\t':
					return true
				default:
					return false
				}
			}) {
				stage := strings.ToLower(strings.TrimSpace(part))
				if stage == "" || stage == "checkout" {
					continue
				}
				result[stage] = "fresh"
			}
			if len(result) > 0 {
				return result
			}
		}
	}
	return nil
}

func intFromMap(values map[string]any, keys ...string) int {
	for _, key := range keys {
		if value, ok := lookupMap(values, key); ok {
			switch item := value.(type) {
			case json.Number:
				parsed, _ := strconv.Atoi(item.String())
				return parsed
			case float64:
				return int(item)
			case string:
				parsed, _ := strconv.Atoi(strings.TrimSpace(item))
				return parsed
			}
		}
	}
	return 0
}

func boolFromMap(values map[string]any, keys ...string) (bool, bool) {
	for _, key := range keys {
		if value, ok := lookupMap(values, key); ok {
			switch item := value.(type) {
			case bool:
				return item, true
			case string:
				parsed, err := strconv.ParseBool(strings.TrimSpace(item))
				return parsed, err == nil
			}
		}
	}
	return false, false
}

func (s *Server) withJSONHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Cache-Control", "no-store")
		writer.Header().Set("X-Content-Type-Options", "nosniff")
		next.ServeHTTP(writer, request)
	})
}

func methodNotAllowed(writer http.ResponseWriter, allowed ...string) {
	writer.Header().Set("Allow", strings.Join(allowed, ", "))
	writeError(writer, http.StatusMethodNotAllowed, errors.New("请求方法不允许"))
}

func writeError(writer http.ResponseWriter, status int, err error) {
	writeJSON(writer, status, map[string]any{"ok": false, "error": err.Error()})
}

func writeJSON(writer http.ResponseWriter, status int, payload any) {
	writer.Header().Set("Content-Type", "application/json; charset=utf-8")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(payload)
}
