package extractmethods

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	JobQueued      = "queued"
	JobRunning     = "running"
	JobCompleted   = "completed"
	JobFailed      = "failed"
	JobCancelled   = "cancelled"
	JobInterrupted = "interrupted"

	ItemQueued              = "queued"
	ItemRunning             = "running"
	ItemSucceeded           = "succeeded"
	ItemFailed              = "failed"
	ItemCancelled           = "cancelled"
	ItemEligibilityObserved = "eligibility_observed"
)

var (
	ErrJobNotFound             = errors.New("任务不存在")
	ErrJobActive               = errors.New("运行中的任务请先停止，再删除该批次")
	ErrContinuationUnsupported = errors.New("只有已结束的 Kakao 资格观察批次可以请求整批转支付链")
)

type RetryOptions struct {
	FailedOnly          bool     `json:"failedOnly"`
	ExcludeSucceeded    bool     `json:"excludeSucceeded"`
	ExcludeInvalidToken bool     `json:"excludeInvalidToken"`
	ExcludePaidPlan     bool     `json:"excludePaidPlan"`
	ExcludeLegacyOAICS  bool     `json:"excludeLegacyOAICS"`
	ItemIDs             []string `json:"itemIds,omitempty"`
}

type PaymentVerifier interface {
	VerifyPaymentStatus(context.Context, Credential, Options) (PaymentVerification, error)
}

type JobOptions struct {
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
	CheckoutProxyRegion      string            `json:"checkoutProxyRegion,omitempty"`
	PromotionProxyRegion     string            `json:"promotionProxyRegion,omitempty"`
	ProviderProxyRegion      string            `json:"providerProxyRegion,omitempty"`
	ApproveProxyRegion       string            `json:"approveProxyRegion,omitempty"`
	CountryProxies           map[string]string `json:"countryProxies,omitempty"`
	CountryPromotionProxies  map[string]string `json:"countryPromotionProxies,omitempty"`
	UsePromo                 bool              `json:"usePromo"`
	TrialDays                int               `json:"trialDays"`
	TimeoutSeconds           int               `json:"timeoutSeconds"`
	MaxAttempts              int               `json:"maxAttempts"`
	ApproveAttempts          int               `json:"approveAttempts,omitempty"`
	AmountGate               string            `json:"amountGate,omitempty"`
	AmountThresholdMinor     int               `json:"amountThresholdMinor,omitempty"`
	AllowUnknownAmount       bool              `json:"allowUnknownAmount,omitempty"`
	MaxAmountMinor           int               `json:"maxAmountMinor"`
	FingerprintPolicy        map[string]string `json:"fingerprintPolicy,omitempty"`
	FingerprintWeightMode    bool              `json:"fingerprintWeightMode,omitempty"`
	PaymentStatusAutoRefresh bool              `json:"paymentStatusAutoRefresh"`
	PayPalSameStickyIP       bool              `json:"paypalSameStickyIp,omitempty"`
	KakaoMode                string            `json:"kakaoMode,omitempty"`
	KakaoEligibilityOnly     bool              `json:"kakaoEligibilityOnly,omitempty"`
	BlikCode                 string            `json:"blikCode,omitempty"`
}

type JobItem struct {
	ID                    string  `json:"id"`
	Index                 int     `json:"index"`
	Label                 string  `json:"label"`
	Email                 string  `json:"email,omitempty"`
	TokenHash             string  `json:"tokenHash"`
	Country               string  `json:"country,omitempty"`
	Currency              string  `json:"currency,omitempty"`
	Status                string  `json:"status"`
	Stage                 string  `json:"stage,omitempty"`
	Detail                string  `json:"detail,omitempty"`
	ExtractionStatus      string  `json:"extractionStatus,omitempty"`
	PaymentStatus         string  `json:"paymentStatus,omitempty"`
	LongURL               string  `json:"longUrl,omitempty"`
	ProviderRedirectURL   string  `json:"providerRedirectUrl,omitempty"`
	LinkGeneratedAt       string  `json:"linkGeneratedAt,omitempty"`
	ExpiresAt             string  `json:"expiresAt,omitempty"`
	LinkTTLSeconds        int     `json:"linkTtlSeconds,omitempty"`
	StripeRedirectURL     string  `json:"stripeRedirectUrl,omitempty"`
	UPIPayload            string  `json:"upiPayload,omitempty"`
	UPIInstructionURL     string  `json:"upiInstructionUrl,omitempty"`
	PaymentPayload        string  `json:"paymentPayload,omitempty"`
	PaymentInstructionURL string  `json:"paymentInstructionUrl,omitempty"`
	QRPNGURL              string  `json:"qrPngUrl,omitempty"`
	QRSVGURL              string  `json:"qrSvgUrl,omitempty"`
	CheckoutID            string  `json:"checkoutId,omitempty"`
	CheckoutType          string  `json:"checkoutType,omitempty"`
	PaymentMethodID       string  `json:"paymentMethodId,omitempty"`
	AmountDisplay         string  `json:"amountDisplay,omitempty"`
	Decision              string  `json:"decision,omitempty"`
	Error                 string  `json:"error,omitempty"`
	StartedAt             string  `json:"startedAt,omitempty"`
	FinishedAt            string  `json:"finishedAt,omitempty"`
	DurationMs            int64   `json:"durationMs,omitempty"`
	Steps                 []Step  `json:"steps,omitempty"`
	Result                *Result `json:"result,omitempty"`
}

type Job struct {
	ID           string           `json:"id"`
	Method       string           `json:"method"`
	MethodLabel  string           `json:"methodLabel"`
	Status       string           `json:"status"`
	Concurrency  int              `json:"concurrency"`
	Total        int              `json:"total"`
	Queued       int              `json:"queued"`
	Running      int              `json:"running"`
	Succeeded    int              `json:"succeeded"`
	Failed       int              `json:"failed"`
	Cancelled    int              `json:"cancelled"`
	Options      JobOptions       `json:"options"`
	Items        []JobItem        `json:"items"`
	CreatedAt    string           `json:"createdAt"`
	StartedAt    string           `json:"startedAt,omitempty"`
	FinishedAt   string           `json:"finishedAt,omitempty"`
	DurationMs   int64            `json:"durationMs,omitempty"`
	Continuation *JobContinuation `json:"continuation,omitempty"`
	InputStored  bool             `json:"inputStored,omitempty"`
}

type JobContinuation struct {
	Mode           string `json:"mode"`
	Status         string `json:"status"`
	MaxAttempts    int    `json:"maxAttempts"`
	RequestedAt    string `json:"requestedAt"`
	SubmittedAt    string `json:"submittedAt,omitempty"`
	SubmittedJobID string `json:"submittedJobId,omitempty"`
}

type JobSummary struct {
	ID               string           `json:"id"`
	Method           string           `json:"method"`
	MethodLabel      string           `json:"methodLabel"`
	Status           string           `json:"status"`
	Concurrency      int              `json:"concurrency"`
	Total            int              `json:"total"`
	Queued           int              `json:"queued"`
	Running          int              `json:"running"`
	Succeeded        int              `json:"succeeded"`
	Failed           int              `json:"failed"`
	Cancelled        int              `json:"cancelled"`
	Options          JobOptions       `json:"options"`
	CreatedAt        string           `json:"createdAt"`
	StartedAt        string           `json:"startedAt,omitempty"`
	FinishedAt       string           `json:"finishedAt,omitempty"`
	DurationMs       int64            `json:"durationMs,omitempty"`
	Eligible         int              `json:"eligible,omitempty"`
	Ineligible       int              `json:"ineligible,omitempty"`
	EligibleAccounts []string         `json:"eligibleAccounts,omitempty"`
	Continuation     *JobContinuation `json:"continuation,omitempty"`
	InputStored      bool             `json:"inputStored,omitempty"`
}

// PortalSourceTask is the compatibility view consumed by the recovered card
// portal. It deliberately carries only public Checkout metadata.
type PortalSourceTask struct {
	TaskID     string `json:"task_id"`
	SourceJob  string `json:"source_job_id"`
	Email      string `json:"email,omitempty"`
	ShortURL   string `json:"short_url"`
	Currency   string `json:"currency"`
	Amount     int    `json:"amount"`
	Status     string `json:"status"`
	CreatedAt  string `json:"created_at,omitempty"`
	HasContext bool   `json:"has_context"`
}

// PortalSourceSession adds locally encrypted source credentials only for the
// loopback bridge between the extract API and card portal.
type PortalSourceSession struct {
	PortalSourceTask
	AccessToken     string            `json:"access_token"`
	SessionToken    string            `json:"session_token,omitempty"`
	SessionCookies  map[string]string `json:"session_cookies,omitempty"`
	AccountID       string            `json:"chatgpt_account_id,omitempty"`
	CheckoutProxy   string            `json:"checkout_proxy,omitempty"`
	UserAgent       string            `json:"user_agent,omitempty"`
	CheckoutDevice  string            `json:"checkout_device_id,omitempty"`
	CheckoutSession string            `json:"checkout_chatgpt_session_id,omitempty"`
}

type jobRuntime struct {
	credentials  []Credential
	options      []Options
	request      BatchRequest
	ctx          context.Context
	cancel       context.CancelFunc
	accountLocks []*accountRunLock
}

type Runner interface {
	Run(context.Context, string, Credential, Options, ProgressFunc) (Result, error)
}

type JobManager struct {
	mu             sync.RWMutex
	engine         Runner
	dataPath       string
	jobs           map[string]*Job
	order          []string
	runtimes       map[string]*jobRuntime
	requests       map[string]BatchRequest
	globalSem      chan struct{}
	accountLockDir string
}

func NewJobManager(engine Runner, dataPath string, globalConcurrency int) (*JobManager, error) {
	if engine == nil {
		return nil, errors.New("提炼引擎不能为空")
	}
	if globalConcurrency < 1 {
		globalConcurrency = 32
	}
	lockDir := strings.TrimSpace(os.Getenv("AUTOMYAI_ACCOUNT_RUN_LOCK_DIR"))
	guardEnabled := !strings.EqualFold(strings.TrimSpace(os.Getenv("AUTOMYAI_ACCOUNT_RUN_GUARD")), "0") &&
		!strings.EqualFold(strings.TrimSpace(os.Getenv("AUTOMYAI_ACCOUNT_RUN_GUARD")), "false")
	if !guardEnabled {
		lockDir = ""
	}
	if lockDir == "" && strings.TrimSpace(dataPath) != "" {
		if guardEnabled {
			lockDir = filepath.Join(filepath.Dir(dataPath), "account-run-locks")
		}
	}
	manager := &JobManager{
		engine: engine, dataPath: dataPath, jobs: map[string]*Job{}, runtimes: map[string]*jobRuntime{}, requests: map[string]BatchRequest{},
		globalSem: make(chan struct{}, globalConcurrency), accountLockDir: lockDir,
	}
	if err := manager.loadStoredRequests(); err != nil {
		return nil, err
	}
	if err := manager.load(); err != nil {
		return nil, err
	}
	for jobID := range manager.runtimes {
		go manager.run(jobID)
	}
	return manager, nil
}

func (m *JobManager) Create(request BatchRequest) (*Job, error) {
	method := NormalizeMethod(request.Method)
	methodInfo, ok := LookupMethod(method)
	if !ok {
		return nil, fmt.Errorf("未知提炼渠道: %s", request.Method)
	}
	if method == MethodKakao {
		if err := validateKakaoMode(request.Options.KakaoMode); err != nil {
			return nil, err
		}
	}
	credentials, err := ParseBatchCredentials(request.Input, request.Items)
	if err != nil {
		return nil, err
	}
	concurrency := request.Concurrency
	if concurrency < 1 {
		concurrency = 3
	}
	if concurrency > 32 {
		concurrency = 32
	}
	if concurrency > len(credentials) {
		concurrency = len(credentials)
	}
	normalizedOptions, itemOptions, err := prepareJobCountryOptions(method, request.Options, len(credentials))
	if err != nil {
		return nil, err
	}
	request.Options = normalizedOptions
	if err := validateExplicitProxies(request.Options); err != nil {
		return nil, err
	}
	now := time.Now().UTC()
	jobID := newJobID()
	accountLocks, err := acquireAccountRunLocks(m.accountLockDir, credentials, accountRunOwner{
		Service: "提炼中心", JobID: jobID, Method: methodInfo.Label,
	})
	if err != nil {
		return nil, err
	}
	job := &Job{
		ID: jobID, Method: method, MethodLabel: methodInfo.Label, Status: JobQueued,
		Concurrency: concurrency, Total: len(credentials), Queued: len(credentials),
		Options: safeJobOptions(request.Options), CreatedAt: now.Format(time.RFC3339), InputStored: true,
		Items: make([]JobItem, len(credentials)),
	}
	for index, credential := range credentials {
		label := strings.TrimSpace(credential.Label)
		if label == "" {
			label = fmt.Sprintf("账号 %d", index+1)
		}
		job.Items[index] = JobItem{
			ID: fmt.Sprintf("%s-%03d", jobID, index+1), Index: index + 1,
			Label: label, Email: credential.Email, TokenHash: credential.Hash, Status: ItemQueued,
			Country: itemOptions[index].Country, Currency: itemOptions[index].Currency,
			ExtractionStatus: "queued", PaymentStatus: "not_started",
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	m.mu.Lock()
	m.jobs[jobID] = job
	m.order = append([]string{jobID}, m.order...)
	m.requests[jobID] = cloneBatchRequest(request)
	m.runtimes[jobID] = &jobRuntime{credentials: credentials, options: itemOptions, request: cloneBatchRequest(request), ctx: ctx, cancel: cancel, accountLocks: accountLocks}
	m.mu.Unlock()
	if err := m.persistStoredRequests(); err != nil {
		m.mu.Lock()
		delete(m.jobs, jobID)
		delete(m.requests, jobID)
		delete(m.runtimes, jobID)
		m.order = removeString(m.order, jobID)
		m.mu.Unlock()
		cancel()
		releaseAccountRunLocks(accountLocks)
		return nil, err
	}
	if err := m.persist(); err != nil {
		m.mu.Lock()
		delete(m.jobs, jobID)
		delete(m.requests, jobID)
		delete(m.runtimes, jobID)
		m.order = removeString(m.order, jobID)
		m.mu.Unlock()
		cancel()
		releaseAccountRunLocks(accountLocks)
		_ = m.persistStoredRequests()
		return nil, err
	}
	go m.run(jobID)
	return m.Get(jobID)
}

func (m *JobManager) Get(jobID string) (*Job, error) {
	m.mu.Lock()
	job := m.jobs[jobID]
	if job == nil {
		m.mu.Unlock()
		return nil, ErrJobNotFound
	}
	changed := demoteKakaoEligibilitySuccess(job)
	if changed {
		m.recountLocked(job)
	}
	result := cloneJob(job)
	m.mu.Unlock()
	if changed {
		_ = m.persist()
	}
	return result, nil
}

func portalTaskID(jobID, itemID string) string {
	sum := sha256.Sum256([]byte(jobID + "\x00" + itemID))
	return hex.EncodeToString(sum[:6])
}

// stablePortalUUID supplies a compatibility identity for links extracted
// before the exact Checkout identity was persisted. The value remains stable
// for the source item instead of changing on every payment attempt.
func stablePortalUUID(jobID, itemID, purpose string) string {
	sum := sha256.Sum256([]byte(jobID + "\x00" + itemID + "\x00" + purpose))
	value := append([]byte(nil), sum[:16]...)
	value[6] = (value[6] & 0x0f) | 0x50
	value[8] = (value[8] & 0x3f) | 0x80
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x",
		value[0:4], value[4:6], value[6:8], value[8:10], value[10:16])
}

func portalTaskFrom(job *Job, item JobItem) (PortalSourceTask, bool) {
	if job == nil || NormalizeMethod(job.Method) != MethodPH || item.Status != ItemSucceeded {
		return PortalSourceTask{}, false
	}
	shortURL := strings.TrimSpace(item.LongURL)
	if shortURL == "" && item.Result != nil {
		shortURL = strings.TrimSpace(item.Result.LongURL)
	}
	if shortURL == "" {
		return PortalSourceTask{}, false
	}
	return PortalSourceTask{
		TaskID: portalTaskID(job.ID, item.ID), SourceJob: job.ID, Email: item.Email,
		ShortURL: shortURL, Currency: "PHP", Amount: 0, Status: "completed", CreatedAt: job.CreatedAt,
	}, true
}

// ListPortalSourceTasks returns completed PH links in the compact shape used by
// the recovered portal, newest first.
func (m *JobManager) ListPortalSourceTasks(limit int) []PortalSourceTask {
	m.mu.RLock()
	defer m.mu.RUnlock()
	if limit < 1 || limit > 500 {
		limit = 100
	}
	result := make([]PortalSourceTask, 0, limit)
	for _, jobID := range m.order {
		job := m.jobs[jobID]
		for _, item := range job.Items {
			if task, ok := portalTaskFrom(job, item); ok {
				_, task.HasContext = m.requests[job.ID]
				result = append(result, task)
				if len(result) >= limit {
					return result
				}
			}
		}
	}
	return result
}

// GetPortalSourceSession resolves a compact task alias back to the locally
// encrypted credential captured when that extract job was submitted.
func (m *JobManager) GetPortalSourceSession(taskID string) (PortalSourceSession, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	for _, jobID := range m.order {
		job := m.jobs[jobID]
		for index, item := range job.Items {
			task, ok := portalTaskFrom(job, item)
			if !ok || task.TaskID != taskID {
				continue
			}
			request, stored := m.requests[job.ID]
			if !stored {
				return PortalSourceSession{}, errors.New("来源任务的本地凭证已清理")
			}
			credentials, _, err := restoreJobRuntime(job.Method, request, len(job.Items))
			if err != nil || index >= len(credentials) {
				return PortalSourceSession{}, errors.New("来源任务的本地凭证读取失败")
			}
			credential := credentials[index]
			cookies := map[string]string{}
			if credential.SessionToken != "" {
				cookies["__Secure-next-auth.session-token"] = credential.SessionToken
			}
			var metadata map[string]any
			if item.Result != nil {
				metadata = item.Result.Metadata
			}
			userAgent := metadataString(metadata, "checkout_user_agent", "user_agent")
			if userAgent == "" {
				userAgent = resolveRequestFingerprint(request.Options.ClientFingerprint).UserAgent
			}
			checkoutDevice := metadataString(metadata, "checkout_device_id")
			if checkoutDevice == "" {
				checkoutDevice = stablePortalUUID(job.ID, item.ID, "device")
			}
			checkoutSession := metadataString(metadata, "checkout_chatgpt_session_id")
			if checkoutSession == "" {
				checkoutSession = stablePortalUUID(job.ID, item.ID, "session")
			}
			return PortalSourceSession{
				PortalSourceTask: task, AccessToken: credential.AccessToken, SessionToken: credential.SessionToken,
				SessionCookies: cookies, AccountID: credential.AccountID,
				CheckoutProxy:   firstNonEmpty(metadataString(metadata, "checkout_proxy"), request.Options.CheckoutProxy, request.Options.Proxy),
				UserAgent:       userAgent,
				CheckoutDevice:  checkoutDevice,
				CheckoutSession: checkoutSession,
			}, nil
		}
	}
	return PortalSourceSession{}, ErrJobNotFound
}

func (m *JobManager) Delete(jobID string) error {
	m.mu.Lock()
	job := m.jobs[jobID]
	if job == nil {
		m.mu.Unlock()
		return ErrJobNotFound
	}
	if job.Status == JobQueued || job.Status == JobRunning {
		m.mu.Unlock()
		return ErrJobActive
	}
	delete(m.jobs, jobID)
	delete(m.requests, jobID)
	delete(m.runtimes, jobID)
	m.order = removeString(m.order, jobID)
	m.mu.Unlock()
	if err := m.persistStoredRequests(); err != nil {
		return err
	}
	return m.persist()
}

func (m *JobManager) Retry(jobID string, retryOptions ...RetryOptions) (*Job, error) {
	m.mu.RLock()
	job := m.jobs[jobID]
	request, ok := m.requests[jobID]
	terminal := job != nil && (job.Status == JobCompleted || job.Status == JobFailed || job.Status == JobCancelled || job.Status == JobInterrupted)
	m.mu.RUnlock()
	if job == nil {
		return nil, ErrJobNotFound
	}
	if !terminal {
		return nil, ErrJobActive
	}
	if !ok {
		return nil, errors.New("该历史批次创建时未启用本机凭证落盘，无法直接重跑")
	}
	if len(retryOptions) == 0 {
		return m.Create(cloneBatchRequest(request))
	}
	filtered, err := retryBatchRequest(job, request, retryOptions[0])
	if err != nil {
		return nil, err
	}
	return m.Create(filtered)
}

func retryBatchRequest(job *Job, request BatchRequest, options RetryOptions) (BatchRequest, error) {
	credentials, err := ParseBatchCredentials(request.Input, request.Items)
	if err != nil {
		return BatchRequest{}, err
	}
	byHash := make(map[string]Credential, len(credentials))
	for _, credential := range credentials {
		byHash[credential.Hash] = credential
	}
	selected := make([]Credential, 0, len(credentials))
	selectedItemIDs := make(map[string]bool, len(options.ItemIDs))
	for _, itemID := range options.ItemIDs {
		if itemID = strings.TrimSpace(itemID); itemID != "" {
			selectedItemIDs[itemID] = true
		}
	}
	for _, item := range job.Items {
		credential, exists := byHash[item.TokenHash]
		if !exists || (len(selectedItemIDs) > 0 && !selectedItemIDs[item.ID]) || !retryItemSelected(item, options) {
			continue
		}
		selected = append(selected, credential)
	}
	if len(selected) == 0 {
		return BatchRequest{}, errors.New("当前筛选条件下没有可重跑账号")
	}
	items := make([]map[string]string, 0, len(selected))
	for _, credential := range selected {
		items = append(items, map[string]string{
			"accessToken": credential.AccessToken, "sessionToken": credential.SessionToken,
			"email": credential.Email, "accountId": credential.AccountID, "label": credential.Label,
		})
	}
	encoded, err := json.Marshal(items)
	if err != nil {
		return BatchRequest{}, err
	}
	filtered := cloneBatchRequest(request)
	filtered.Input = ""
	filtered.Items = encoded
	if filtered.Concurrency > len(selected) {
		filtered.Concurrency = len(selected)
	}
	return filtered, nil
}

func retryItemSelected(item JobItem, options RetryOptions) bool {
	if options.FailedOnly && item.Status != ItemFailed && item.Status != ItemCancelled {
		return false
	}
	if options.ExcludeSucceeded && item.Status == ItemSucceeded {
		return false
	}
	combined := strings.ToLower(strings.Join([]string{item.Decision, item.PaymentStatus, item.ExtractionStatus, item.Stage, item.Error, item.Detail}, " "))
	if options.ExcludeInvalidToken && (strings.Contains(combined, "token_invalidated") || strings.Contains(combined, "token_invalid") || strings.Contains(combined, "credential_invalid") || strings.Contains(combined, "token_expired") || strings.Contains(combined, "http 401")) {
		return false
	}
	if options.ExcludePaidPlan && (strings.EqualFold(item.Decision, "already_paid") || strings.EqualFold(item.PaymentStatus, "already_paid") || strings.EqualFold(item.PaymentStatus, "paid_success") || strings.Contains(combined, "already paid") || strings.Contains(combined, "当前套餐为 plus") || strings.Contains(combined, "当前套餐为 pro")) {
		return false
	}
	if options.ExcludeLegacyOAICS && (strings.HasPrefix(strings.ToLower(strings.TrimSpace(item.CheckoutID)), "oaics_") || strings.Contains(combined, "no such payment_page")) {
		return false
	}
	return true
}

func (m *JobManager) VerifyPayment(jobID string, onlySucceeded bool) (*Job, error) {
	verifier, ok := m.engine.(PaymentVerifier)
	if !ok {
		return nil, errors.New("当前提炼引擎不支持支付状态复核")
	}
	m.mu.RLock()
	job := m.jobs[jobID]
	request, stored := m.requests[jobID]
	m.mu.RUnlock()
	if job == nil {
		return nil, ErrJobNotFound
	}
	if !stored {
		return nil, errors.New("该历史批次没有本机加密凭证，无法复核支付状态")
	}
	credentials, itemOptions, err := restoreJobRuntime(job.Method, request, len(job.Items))
	if err != nil {
		return nil, err
	}
	type verifyTarget struct {
		index      int
		credential Credential
		options    Options
	}
	targets := make([]verifyTarget, 0, len(job.Items))
	for index, item := range job.Items {
		if onlySucceeded && item.Status != ItemSucceeded {
			continue
		}
		targets = append(targets, verifyTarget{index: index, credential: credentials[index], options: itemOptions[index]})
	}
	if len(targets) == 0 {
		return nil, errors.New("当前批次没有符合条件的账号可复核")
	}
	lockCredentials := make([]Credential, 0, len(targets))
	for _, target := range targets {
		lockCredentials = append(lockCredentials, target.credential)
	}
	accountLocks, err := acquireAccountRunLocks(m.accountLockDir, lockCredentials, accountRunOwner{
		Service: "提炼中心", JobID: jobID, Method: "支付状态复核",
	})
	if err != nil {
		return nil, err
	}
	defer releaseAccountRunLocks(accountLocks)

	limit := job.Concurrency
	if limit < 1 {
		limit = 1
	}
	if limit > 8 {
		limit = 8
	}
	sem := make(chan struct{}, limit)
	var workers sync.WaitGroup
	for _, target := range targets {
		target := target
		workers.Add(1)
		go func() {
			defer workers.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			started := time.Now().UTC()
			m.mu.Lock()
			if current := m.jobs[jobID]; current != nil && target.index < len(current.Items) {
				item := &current.Items[target.index]
				item.PaymentStatus = "verifying_payment"
				item.Steps = append(item.Steps, NewStep("payment.verify", "running", "只读查询官方账号套餐状态", 0))
			}
			m.mu.Unlock()

			verification, verifyErr := verifier.VerifyPaymentStatus(context.Background(), target.credential, target.options)
			m.mu.Lock()
			if current := m.jobs[jobID]; current != nil && target.index < len(current.Items) {
				item := &current.Items[target.index]
				status := verification.Status
				if status == "" {
					status = "verification_unknown"
				}
				item.PaymentStatus = status
				detail := verification.Detail
				if verifyErr != nil && detail == "" {
					detail = verifyErr.Error()
				}
				detail = sanitizeJobText(detail, target.credential)
				stepStatus := "success"
				if status == "verification_unknown" || status == "token_invalid" {
					stepStatus = "failed"
				}
				item.Steps = append(item.Steps, NewStep("payment.verify", stepStatus, detail, time.Since(started)))
				if verification.Email != "" {
					item.Email = verification.Email
				}
			}
			m.mu.Unlock()
		}()
	}
	workers.Wait()
	if err := m.persist(); err != nil {
		return nil, err
	}
	return m.Get(jobID)
}

func (m *JobManager) List(limit int) []JobSummary {
	m.mu.Lock()
	changed := false
	for _, jobID := range m.order {
		if job := m.jobs[jobID]; job != nil {
			if demoteKakaoEligibilitySuccess(job) {
				m.recountLocked(job)
				changed = true
			}
		}
	}
	count := len(m.order)
	if limit > 0 && limit < count {
		count = limit
	}
	result := make([]JobSummary, 0, count)
	for _, jobID := range m.order[:count] {
		if job := m.jobs[jobID]; job != nil {
			result = append(result, summaryForJob(job))
		}
	}
	m.mu.Unlock()
	if changed {
		_ = m.persist()
	}
	return result
}

func (m *JobManager) Cancel(jobID string) (*Job, error) {
	m.mu.Lock()
	job := m.jobs[jobID]
	if job == nil {
		m.mu.Unlock()
		return nil, ErrJobNotFound
	}
	if job.Status == JobCompleted || job.Status == JobFailed || job.Status == JobCancelled || job.Status == JobInterrupted {
		result := cloneJob(job)
		m.mu.Unlock()
		return result, nil
	}
	if runtime := m.runtimes[jobID]; runtime != nil {
		runtime.cancel()
	}
	for index := range job.Items {
		if job.Items[index].Status == ItemQueued {
			job.Items[index].Status = ItemCancelled
			job.Items[index].ExtractionStatus = "cancelled"
			job.Items[index].PaymentStatus = "not_started"
			job.Items[index].FinishedAt = time.Now().UTC().Format(time.RFC3339)
		}
	}
	m.recountLocked(job)
	result := cloneJob(job)
	m.mu.Unlock()
	_ = m.persist()
	return result, nil
}

func (m *JobManager) RequestKakaoProviderContinuation(jobID string, maxAttempts int) (*Job, error) {
	if maxAttempts < 1 {
		maxAttempts = 10
	}
	if maxAttempts > 100 {
		maxAttempts = 100
	}
	m.mu.Lock()
	job := m.jobs[jobID]
	if job == nil {
		m.mu.Unlock()
		return nil, ErrJobNotFound
	}
	terminal := job.Status == JobCompleted || job.Status == JobFailed || job.Status == JobCancelled || job.Status == JobInterrupted
	eligibleCount := 0
	for _, item := range job.Items {
		if strings.EqualFold(strings.TrimSpace(item.Decision), "eligible") || item.Status == ItemEligibilityObserved {
			eligibleCount++
		}
	}
	if NormalizeMethod(job.Method) != MethodKakao || NormalizeKakaoMode(job.Options.KakaoMode) == KakaoModeProviderLink || !terminal || eligibleCount < 1 {
		m.mu.Unlock()
		return nil, ErrContinuationUnsupported
	}
	if job.Continuation == nil || job.Continuation.Status != "submitted" {
		job.Continuation = &JobContinuation{
			Mode: KakaoModeProviderLink, Status: "requested", MaxAttempts: maxAttempts,
			RequestedAt: time.Now().UTC().Format(time.RFC3339),
		}
	}
	result := cloneJob(job)
	m.mu.Unlock()
	if err := m.persist(); err != nil {
		return nil, err
	}
	return result, nil
}

func (m *JobManager) MarkKakaoProviderContinuationSubmitted(jobID, submittedJobID string) (*Job, error) {
	m.mu.Lock()
	job := m.jobs[jobID]
	child := m.jobs[submittedJobID]
	if job == nil || child == nil {
		m.mu.Unlock()
		return nil, ErrJobNotFound
	}
	if job.Continuation == nil || job.Continuation.Mode != KakaoModeProviderLink || NormalizeMethod(child.Method) != MethodKakao || NormalizeKakaoMode(child.Options.KakaoMode) != KakaoModeProviderLink {
		m.mu.Unlock()
		return nil, ErrContinuationUnsupported
	}
	job.Continuation.Status = "submitted"
	job.Continuation.SubmittedAt = time.Now().UTC().Format(time.RFC3339)
	job.Continuation.SubmittedJobID = submittedJobID
	result := cloneJob(job)
	m.mu.Unlock()
	if err := m.persist(); err != nil {
		return nil, err
	}
	return result, nil
}

func (m *JobManager) run(jobID string) {
	m.mu.Lock()
	job := m.jobs[jobID]
	runtime := m.runtimes[jobID]
	if job == nil || runtime == nil {
		m.mu.Unlock()
		return
	}
	defer releaseAccountRunLocks(runtime.accountLocks)
	job.Status = JobRunning
	job.StartedAt = time.Now().UTC().Format(time.RFC3339)
	m.recountLocked(job)
	m.mu.Unlock()
	_ = m.persist()

	indices := make(chan int)
	var workers sync.WaitGroup
	for worker := 0; worker < job.Concurrency; worker++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for index := range indices {
				if runtime.ctx.Err() != nil {
					m.cancelQueuedItem(jobID, index)
					continue
				}
				m.runItem(jobID, index, runtime)
			}
		}()
	}
	for index := range runtime.credentials {
		select {
		case <-runtime.ctx.Done():
			m.cancelQueuedItem(jobID, index)
		case indices <- index:
		}
	}
	close(indices)
	workers.Wait()

	m.mu.Lock()
	job = m.jobs[jobID]
	if job != nil {
		m.recountLocked(job)
		eligibilityObserved := 0
		for _, item := range job.Items {
			if item.Status == ItemEligibilityObserved {
				eligibilityObserved++
			}
		}
		switch {
		case runtime.ctx.Err() != nil:
			job.Status = JobCancelled
		case job.Succeeded == job.Total:
			job.Status = JobCompleted
		case job.Succeeded > 0:
			job.Status = JobCompleted
		case eligibilityObserved > 0 && job.Failed == 0 && job.Cancelled == 0:
			// Pure diagnostic batch finished without extraction success.
			job.Status = JobCompleted
		default:
			job.Status = JobFailed
		}
		finished := time.Now().UTC()
		job.FinishedAt = finished.Format(time.RFC3339)
		job.DurationMs = durationSince(job.StartedAt, finished)
	}
	delete(m.runtimes, jobID)
	_ = m.persistLocked()
	m.mu.Unlock()
}

func (m *JobManager) runItem(jobID string, index int, runtime *jobRuntime) {
	select {
	case m.globalSem <- struct{}{}:
		defer func() { <-m.globalSem }()
	case <-runtime.ctx.Done():
		m.cancelQueuedItem(jobID, index)
		return
	}

	started := time.Now().UTC()
	m.mu.Lock()
	job := m.jobs[jobID]
	if job == nil || index >= len(job.Items) || job.Items[index].Status != ItemQueued {
		m.mu.Unlock()
		return
	}
	job.Items[index].Status = ItemRunning
	job.Items[index].Stage = "starting"
	job.Items[index].Detail = "正在准备代理与 TLS 会话"
	job.Items[index].StartedAt = started.Format(time.RFC3339)
	job.Items[index].ExtractionStatus = "running"
	job.Items[index].PaymentStatus = "not_started"
	m.recountLocked(job)
	m.mu.Unlock()

	credential := runtime.credentials[index]
	options := runtime.options[index]
	progress := func(step Step) {
		m.mu.Lock()
		if current := m.jobs[jobID]; current != nil && index < len(current.Items) {
			item := &current.Items[index]
			live := step
			live.Stage = trimDetail(step.Stage, 120)
			live.Detail = sanitizeJobText(step.Detail, credential)
			item.Stage = live.Stage
			item.Detail = live.Detail
			item.Steps = append(item.Steps, live)
			if step.Status == "success" && strings.Contains(strings.ToLower(step.Stage), "approve") {
				item.PaymentStatus = "approved"
			}
		}
		m.mu.Unlock()
	}
	result, err := m.engine.Run(runtime.ctx, job.Method, credential, options, progress)
	finished := time.Now().UTC()

	m.mu.Lock()
	job = m.jobs[jobID]
	if job != nil && index < len(job.Items) {
		item := &job.Items[index]
		resultCopy := result
		item.Result = &resultCopy
		if len(result.Steps) > 0 {
			item.Steps = append([]Step(nil), result.Steps...)
		} else if len(item.Steps) == 0 {
			item.Steps = nil
		}
		item.ExtractionStatus = result.ExtractionStatus
		item.PaymentStatus = result.PaymentStatus
		item.LongURL = result.LongURL
		item.ProviderRedirectURL = result.ProviderRedirectURL
		item.StripeRedirectURL = result.StripeRedirectURL
		item.LinkGeneratedAt = result.LinkGeneratedAt
		item.ExpiresAt = result.ExpiresAt
		item.LinkTTLSeconds = result.LinkTTLSeconds
		item.UPIPayload = result.UPIPayload
		item.UPIInstructionURL = result.UPIInstructionURL
		item.PaymentPayload = result.PaymentPayload
		item.PaymentInstructionURL = result.PaymentInstructionURL
		item.QRPNGURL = result.QRPNGURL
		item.QRSVGURL = result.QRSVGURL
		item.CheckoutID = result.CheckoutID
		item.CheckoutType = result.CheckoutType
		item.PaymentMethodID = result.PaymentMethodID
		item.AmountDisplay = result.AmountDisplay
		item.Decision = result.Decision
		item.FinishedAt = finished.Format(time.RFC3339)
		item.DurationMs = finished.Sub(started).Milliseconds()
		if err == nil {
			if job.Method == MethodKakao && options.KakaoMode == KakaoModeEligibility {
				// Eligibility is diagnostic only. Never count it as extraction success.
				item.Status = ItemEligibilityObserved
				item.Stage = "eligibility_observed"
				item.Detail = "Kakao 上游资格观察已完成（非提炼成功）"
				if item.ExtractionStatus == "" || item.ExtractionStatus == "link_ready" {
					item.ExtractionStatus = "probe_complete"
				}
			} else {
				item.Status = ItemSucceeded
				item.Stage = "completed"
				if job.Method == MethodKakao {
					item.Detail = "Kakao 支付链已生成，等待用户自行支付"
				} else {
					item.Detail = "提炼流程已完成"
				}
			}
		} else if errors.Is(err, context.Canceled) || runtime.ctx.Err() != nil {
			item.Status = ItemCancelled
			item.Stage = "cancelled"
			item.Detail = "任务已取消"
			item.Error = "任务已取消"
			if item.ExtractionStatus == "" {
				item.ExtractionStatus = "cancelled"
			}
		} else {
			item.Status = ItemFailed
			item.Stage = "failed"
			item.Error = sanitizeJobText(err.Error(), credential)
			item.Detail = item.Error
			if item.ExtractionStatus == "" {
				item.ExtractionStatus = "failed"
			}
			if isAlreadyPaidError(err) || strings.Contains(strings.ToLower(item.Error), "already paid") {
				item.Decision = "already_paid"
				if item.PaymentStatus == "" || item.PaymentStatus == "not_started" {
					item.PaymentStatus = "already_paid"
				}
				item.Detail = "账号已是付费状态，已停止后续流程"
			}
		}
		if item.PaymentStatus == "" {
			item.PaymentStatus = "not_started"
		}
		if job.Method == MethodUPI {
			sanitizeUPIJobItem(item)
		}
		m.recountLocked(job)
	}
	m.mu.Unlock()
	_ = m.persist()
}

func (m *JobManager) cancelQueuedItem(jobID string, index int) {
	m.mu.Lock()
	defer m.mu.Unlock()
	job := m.jobs[jobID]
	if job == nil || index >= len(job.Items) || job.Items[index].Status != ItemQueued {
		return
	}
	job.Items[index].Status = ItemCancelled
	job.Items[index].Stage = "cancelled"
	job.Items[index].Detail = "任务在执行前已取消"
	job.Items[index].ExtractionStatus = "cancelled"
	job.Items[index].PaymentStatus = "not_started"
	job.Items[index].FinishedAt = time.Now().UTC().Format(time.RFC3339)
	m.recountLocked(job)
}

func (m *JobManager) recountLocked(job *Job) {
	job.Queued, job.Running, job.Succeeded, job.Failed, job.Cancelled = 0, 0, 0, 0, 0
	for _, item := range job.Items {
		switch item.Status {
		case ItemQueued:
			job.Queued++
		case ItemRunning:
			job.Running++
		case ItemSucceeded:
			job.Succeeded++
		case ItemFailed:
			job.Failed++
		case ItemCancelled:
			job.Cancelled++
		case ItemEligibilityObserved:
			// Diagnostic completion only; intentionally excluded from Succeeded.
		}
	}
}

func (m *JobManager) persist() error {
	if strings.TrimSpace(m.dataPath) == "" {
		return nil
	}
	m.mu.RLock()
	jobs := make([]*Job, 0, len(m.order))
	for _, jobID := range m.order {
		if job := m.jobs[jobID]; job != nil {
			jobs = append(jobs, cloneJob(job))
		}
	}
	m.mu.RUnlock()
	return m.writePersistedJobs(jobs)
}

func (m *JobManager) persistLocked() error {
	if strings.TrimSpace(m.dataPath) == "" {
		return nil
	}
	jobs := make([]*Job, 0, len(m.order))
	for _, jobID := range m.order {
		if job := m.jobs[jobID]; job != nil {
			jobs = append(jobs, cloneJob(job))
		}
	}
	return m.writePersistedJobs(jobs)
}

func (m *JobManager) writePersistedJobs(jobs []*Job) error {
	encoded, err := json.MarshalIndent(map[string]any{"version": 1, "jobs": jobs}, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(m.dataPath), 0o700); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(m.dataPath), ".jobs-*.json")
	if err != nil {
		return err
	}
	temporaryName := temporary.Name()
	defer os.Remove(temporaryName)
	if err := temporary.Chmod(0o600); err != nil {
		_ = temporary.Close()
		return err
	}
	if _, err := temporary.Write(encoded); err != nil {
		_ = temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryName, m.dataPath)
}

func cloneBatchRequest(request BatchRequest) BatchRequest {
	encoded, _ := json.Marshal(request)
	var cloned BatchRequest
	_ = json.Unmarshal(encoded, &cloned)
	return cloned
}

func restoreJobRuntime(method string, request BatchRequest, expectedItems int) ([]Credential, []Options, error) {
	credentials, err := ParseBatchCredentials(request.Input, request.Items)
	if err != nil {
		return nil, nil, err
	}
	if expectedItems > 0 && len(credentials) != expectedItems {
		return nil, nil, fmt.Errorf("本机凭证数量 %d 与任务账号数量 %d 不一致", len(credentials), expectedItems)
	}
	_, itemOptions, err := prepareJobCountryOptions(method, request.Options, len(credentials))
	if err != nil {
		return nil, nil, err
	}
	return credentials, itemOptions, nil
}

func (m *JobManager) credentialStorePath() string {
	if strings.TrimSpace(m.dataPath) == "" {
		return ""
	}
	return m.dataPath + ".credentials.enc"
}

func (m *JobManager) credentialKeyPath() string {
	if strings.TrimSpace(m.dataPath) == "" {
		return ""
	}
	return m.dataPath + ".credentials.key"
}

func (m *JobManager) credentialKey(create bool) ([]byte, error) {
	path := m.credentialKeyPath()
	if path == "" {
		return nil, errors.New("提炼任务未配置本机数据目录")
	}
	encoded, err := os.ReadFile(path)
	if err == nil {
		key, decodeErr := base64.RawStdEncoding.DecodeString(strings.TrimSpace(string(encoded)))
		if decodeErr != nil || len(key) != 32 {
			return nil, errors.New("本机提炼凭证密钥格式无效")
		}
		return key, nil
	}
	if !errors.Is(err, os.ErrNotExist) || !create {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		return nil, err
	}
	if err := os.WriteFile(path, []byte(base64.RawStdEncoding.EncodeToString(key)), 0o600); err != nil {
		return nil, err
	}
	return key, nil
}

func sealJobRequests(key []byte, requests map[string]BatchRequest) ([]byte, error) {
	plain, err := json.Marshal(map[string]any{"version": 1, "requests": requests})
	if err != nil {
		return nil, err
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, aead.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	sealed := aead.Seal(nil, nonce, plain, []byte("automyai-extract-job-credentials-v1"))
	return []byte(base64.RawStdEncoding.EncodeToString(append(nonce, sealed...))), nil
}

func openJobRequests(key, encoded []byte) (map[string]BatchRequest, error) {
	packed, err := base64.RawStdEncoding.DecodeString(strings.TrimSpace(string(encoded)))
	if err != nil {
		return nil, err
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	if len(packed) <= aead.NonceSize() {
		return nil, errors.New("本机提炼凭证库内容不完整")
	}
	plain, err := aead.Open(nil, packed[:aead.NonceSize()], packed[aead.NonceSize():], []byte("automyai-extract-job-credentials-v1"))
	if err != nil {
		return nil, err
	}
	var payload struct {
		Requests map[string]BatchRequest `json:"requests"`
	}
	if err := json.Unmarshal(plain, &payload); err != nil {
		return nil, err
	}
	if payload.Requests == nil {
		payload.Requests = map[string]BatchRequest{}
	}
	return payload.Requests, nil
}

func (m *JobManager) persistStoredRequests() error {
	path := m.credentialStorePath()
	if path == "" {
		return nil
	}
	m.mu.RLock()
	requests := make(map[string]BatchRequest, len(m.requests))
	for jobID, request := range m.requests {
		requests[jobID] = cloneBatchRequest(request)
	}
	m.mu.RUnlock()
	key, err := m.credentialKey(true)
	if err != nil {
		return err
	}
	encoded, err := sealJobRequests(key, requests)
	if err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), ".credentials-*.enc")
	if err != nil {
		return err
	}
	name := temporary.Name()
	defer os.Remove(name)
	if err := temporary.Chmod(0o600); err != nil {
		_ = temporary.Close()
		return err
	}
	if _, err := temporary.Write(encoded); err != nil {
		_ = temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(name, path)
}

func (m *JobManager) loadStoredRequests() error {
	path := m.credentialStorePath()
	if path == "" {
		return nil
	}
	encoded, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	key, err := m.credentialKey(false)
	if err != nil {
		return fmt.Errorf("读取本机提炼凭证密钥失败: %w", err)
	}
	requests, err := openJobRequests(key, encoded)
	if err != nil {
		return fmt.Errorf("解密本机提炼凭证库失败: %w", err)
	}
	m.requests = requests
	return nil
}

func (m *JobManager) load() error {
	if strings.TrimSpace(m.dataPath) == "" {
		return nil
	}
	encoded, err := os.ReadFile(m.dataPath)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	var payload struct {
		Jobs []*Job `json:"jobs"`
	}
	if err := json.Unmarshal(encoded, &payload); err != nil {
		return fmt.Errorf("读取提炼任务历史失败: %w", err)
	}
	migrated := false
	for _, job := range payload.Jobs {
		if job == nil || job.ID == "" {
			continue
		}
		interrupted := job.Status == JobQueued || job.Status == JobRunning
		storedRequest, hasStoredRequest := m.requests[job.ID]
		job.InputStored = hasStoredRequest
		for index := range job.Items {
			if job.Items[index].Status == ItemQueued || job.Items[index].Status == ItemRunning {
				if hasStoredRequest {
					job.Items[index].Status = ItemQueued
					job.Items[index].Stage = JobQueued
					job.Items[index].Detail = "服务重启，已从本机加密凭证库恢复并继续排队"
					job.Items[index].Error = ""
					job.Items[index].ExtractionStatus = JobQueued
					job.Items[index].PaymentStatus = "not_started"
					job.Items[index].StartedAt = ""
					job.Items[index].FinishedAt = ""
				} else {
					job.Items[index].Status = ItemFailed
					job.Items[index].Stage = JobInterrupted
					job.Items[index].Detail = "服务重启，旧任务未保存凭证；请重新提交该账号"
					job.Items[index].Error = job.Items[index].Detail
					job.Items[index].ExtractionStatus = JobInterrupted
					job.Items[index].PaymentStatus = "unknown"
				}
				interrupted = true
			}
		}
		if interrupted {
			if hasStoredRequest {
				credentials, options, restoreErr := restoreJobRuntime(job.Method, storedRequest, len(job.Items))
				if restoreErr == nil {
					accountLocks, lockErr := acquireAccountRunLocks(m.accountLockDir, credentials, accountRunOwner{
						Service: "提炼中心", JobID: job.ID, Method: job.MethodLabel,
					})
					if lockErr == nil {
						ctx, cancel := context.WithCancel(context.Background())
						m.runtimes[job.ID] = &jobRuntime{credentials: credentials, options: options, request: cloneBatchRequest(storedRequest), ctx: ctx, cancel: cancel, accountLocks: accountLocks}
						job.Status = JobQueued
						job.FinishedAt = ""
						job.DurationMs = 0
					} else {
						restoreErr = lockErr
					}
				}
				if restoreErr != nil {
					job.Status = JobInterrupted
					job.FinishedAt = time.Now().UTC().Format(time.RFC3339)
					for index := range job.Items {
						if job.Items[index].Status == ItemQueued {
							job.Items[index].Status = ItemFailed
							job.Items[index].Stage = JobInterrupted
							job.Items[index].Detail = sanitizeJobText(restoreErr.Error(), credentials[index])
							job.Items[index].Error = job.Items[index].Detail
						}
					}
				} else {
					// runtime and account locks restored above
				}
			} else {
				job.Status = JobInterrupted
				if job.FinishedAt == "" {
					job.FinishedAt = time.Now().UTC().Format(time.RFC3339)
				}
			}
		}
		if sanitizeLoadedJob(job) {
			migrated = true
		}
		if demoteKakaoEligibilitySuccess(job) {
			migrated = true
		}
		m.recountLocked(job)
		if job.Status == JobCompleted && job.Succeeded == 0 && job.Failed > 0 {
			job.Status = JobFailed
			migrated = true
		}
		m.jobs[job.ID] = job
		m.order = append(m.order, job.ID)
	}
	sort.SliceStable(m.order, func(i, j int) bool {
		left, right := m.jobs[m.order[i]], m.jobs[m.order[j]]
		return left != nil && right != nil && left.CreatedAt > right.CreatedAt
	})
	if migrated {
		return m.persist()
	}
	return nil
}

func safeJobOptions(options Options) JobOptions {
	return JobOptions{
		Country: options.Country, RequestedCountry: options.RequestedCountry, CountryFallback: options.CountryFallback,
		Currency: options.Currency, CountryMode: options.CountryMode, CountryPool: append([]string(nil), options.CountryPool...),
		AssignmentStrategy: options.AssignmentStrategy, AssignmentSeed: options.AssignmentSeed,
		ProxyMode: options.ProxyMode, ProxyRegion: options.ProxyRegion,
		CheckoutProxyRegion: options.CheckoutProxyRegion, PromotionProxyRegion: options.PromotionProxyRegion,
		ProviderProxyRegion: options.ProviderProxyRegion, ApproveProxyRegion: options.ApproveProxyRegion,
		CountryProxies: copyStringMap(options.CountryProxies), CountryPromotionProxies: copyStringMap(options.CountryPromotionProxies),
		UsePromo: options.PromoEnabled(), TrialDays: options.TrialDays, TimeoutSeconds: options.TimeoutSeconds,
		MaxAttempts: options.MaxAttempts, ApproveAttempts: options.ApproveAttempts,
		AmountGate: options.AmountGate, AmountThresholdMinor: options.AmountThresholdMinor, AllowUnknownAmount: options.AllowUnknownAmount,
		MaxAmountMinor:           options.MaxAmountMinor,
		FingerprintPolicy:        options.StageFingerprintPolicy(),
		FingerprintWeightMode:    options.FingerprintWeightMode != nil && *options.FingerprintWeightMode,
		PaymentStatusAutoRefresh: options.PaymentStatusAutoRefresh,
		PayPalSameStickyIP:       options.PayPalSameStickyIP,
		KakaoMode:                options.KakaoMode,
		KakaoEligibilityOnly:     options.KakaoEligibilityOnly,
		BlikCode:                 options.BlikCode,
	}
}

func copyStringMap(source map[string]string) map[string]string {
	if len(source) == 0 {
		return nil
	}
	result := make(map[string]string, len(source))
	for key, value := range source {
		result[key] = value
	}
	return result
}

func demoteKakaoEligibilitySuccess(job *Job) bool {
	if job == nil || NormalizeMethod(job.Method) != MethodKakao {
		return false
	}
	mode := NormalizeKakaoMode(job.Options.KakaoMode)
	// Provider-link batches can still contain a mislabeled diagnostic item; demote
	// item-by-item. Whole-job mode only short-circuits when it is clearly provider_link
	// and no item looks diagnostic.
	changed := false
	for index := range job.Items {
		item := &job.Items[index]
		hasProviderMaterial := strings.TrimSpace(item.LongURL) != "" || strings.TrimSpace(item.ProviderRedirectURL) != ""
		if hasProviderMaterial {
			continue
		}
		isEligibilityResult := item.Status == ItemEligibilityObserved ||
			item.ExtractionStatus == "probe_complete" ||
			item.ExtractionStatus == "eligibility_complete" ||
			item.Stage == "eligibility_observed" ||
			strings.Contains(item.Detail, "上游资格观察") ||
			strings.Contains(item.Detail, "资格观察") ||
			((strings.EqualFold(strings.TrimSpace(item.Decision), "eligible") || strings.EqualFold(strings.TrimSpace(item.Decision), "ineligible")) &&
				(mode == KakaoModeEligibility || item.ExtractionStatus == "probe_complete" || strings.Contains(item.Detail, "资格"))) ||
			(mode == KakaoModeEligibility && item.Status == ItemSucceeded && !hasProviderMaterial)
		if !isEligibilityResult {
			continue
		}
		if item.Status == ItemSucceeded || item.Status == ItemEligibilityObserved {
			if item.Status != ItemEligibilityObserved {
				item.Status = ItemEligibilityObserved
				changed = true
			}
			if item.Stage != "eligibility_observed" {
				item.Stage = "eligibility_observed"
				changed = true
			}
			if strings.TrimSpace(item.Detail) == "" || item.Detail == "提炼流程已完成" || item.Detail == "Kakao 上游资格观察已完成" {
				item.Detail = "Kakao 上游资格观察已完成（非提炼成功）"
				changed = true
			}
			if item.ExtractionStatus == "" || item.ExtractionStatus == "link_ready" || item.ExtractionStatus == "provider_link_ready" {
				item.ExtractionStatus = "probe_complete"
				changed = true
			}
		}
	}
	return changed
}

func summaryForJob(job *Job) JobSummary {
	eligibleAccounts := make([]string, 0)
	eligible, ineligible := 0, 0
	seen := map[string]bool{}
	var continuation *JobContinuation
	if job.Continuation != nil {
		copy := *job.Continuation
		continuation = &copy
	}
	for _, item := range job.Items {
		switch strings.ToLower(strings.TrimSpace(item.Decision)) {
		case "eligible":
			eligible++
			account := strings.TrimSpace(firstNonEmpty(item.Email, item.Label))
			key := strings.ToLower(account)
			if account != "" && !seen[key] {
				seen[key] = true
				eligibleAccounts = append(eligibleAccounts, account)
			}
		case "ineligible":
			ineligible++
		}
	}
	return JobSummary{
		ID: job.ID, Method: job.Method, MethodLabel: job.MethodLabel, Status: job.Status,
		Concurrency: job.Concurrency, Total: job.Total, Queued: job.Queued, Running: job.Running,
		Succeeded: job.Succeeded, Failed: job.Failed, Cancelled: job.Cancelled,
		Options:   job.Options,
		CreatedAt: job.CreatedAt, StartedAt: job.StartedAt, FinishedAt: job.FinishedAt, DurationMs: job.DurationMs,
		Eligible: eligible, Ineligible: ineligible, EligibleAccounts: eligibleAccounts,
		Continuation: continuation, InputStored: job.InputStored,
	}
}

func cloneJob(job *Job) *Job {
	if job == nil {
		return nil
	}
	encoded, _ := json.Marshal(job)
	var result Job
	_ = json.Unmarshal(encoded, &result)
	return &result
}

func durationSince(raw string, finished time.Time) int64 {
	started, err := time.Parse(time.RFC3339, raw)
	if err != nil {
		return 0
	}
	return finished.Sub(started).Milliseconds()
}

func sanitizeJobText(value string, credential Credential) string {
	text := value
	for _, secret := range []string{credential.AccessToken, credential.SessionToken} {
		if strings.TrimSpace(secret) != "" {
			text = strings.ReplaceAll(text, secret, "[redacted]")
		}
	}
	return trimDetail(text, 900)
}

func newJobID() string {
	buffer := make([]byte, 6)
	if _, err := rand.Read(buffer); err != nil {
		return fmt.Sprintf("ext-%d", time.Now().UnixNano())
	}
	return fmt.Sprintf("ext-%d-%s", time.Now().UTC().Unix(), hex.EncodeToString(buffer))
}

func removeString(values []string, wanted string) []string {
	result := values[:0]
	for _, value := range values {
		if value != wanted {
			result = append(result, value)
		}
	}
	return result
}
