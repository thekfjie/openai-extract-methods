package extractmethods

import (
	"context"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

type fakeRunner struct {
	active atomic.Int32
	max    atomic.Int32
	delay  time.Duration
}

type countryEchoRunner struct{}

type verifyingRunner struct {
	fakeRunner
}

func testJWTWithAccount(t *testing.T, accountID, email, signature string) string {
	t.Helper()
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"none","typ":"JWT"}`))
	payload, err := json.Marshal(map[string]any{
		"https://api.openai.com/auth":    map[string]any{"chatgpt_account_id": accountID},
		"https://api.openai.com/profile": map[string]any{"email": email},
	})
	if err != nil {
		t.Fatal(err)
	}
	return header + "." + base64.RawURLEncoding.EncodeToString(payload) + "." + strings.Repeat(signature, 24)
}

func (runner *verifyingRunner) VerifyPaymentStatus(_ context.Context, credential Credential, _ Options) (PaymentVerification, error) {
	if strings.HasPrefix(credential.AccessToken, "p") {
		return PaymentVerification{Status: "paid_success", Plan: "plus", Email: "paid@example.com", Detail: "paid"}, nil
	}
	return PaymentVerification{Status: "awaiting_payment", Plan: "free", Email: "free@example.com", Detail: "free"}, nil
}

func (runner *countryEchoRunner) Run(ctx context.Context, method string, credential Credential, options Options, progress ProgressFunc) (Result, error) {
	for attempt := 1; attempt <= options.Attempts(); attempt++ {
		selected := SelectAttemptOptions(options, attempt)
		if selected.Country != options.Country || selected.Currency != options.Currency || selected.RequestedCountry != options.RequestedCountry {
			return Result{}, fmt.Errorf("attempt %d changed assigned country from %s/%s to %s/%s", attempt, options.Country, options.Currency, selected.Country, selected.Currency)
		}
	}
	return Result{
		OK: true, Method: method, Country: options.Country, Currency: options.Currency,
		LongURL:          "https://example.test/" + credential.Hash,
		ExtractionStatus: "link_ready", PaymentStatus: "awaiting_payment",
	}, nil
}

func (runner *fakeRunner) Run(ctx context.Context, method string, credential Credential, options Options, progress ProgressFunc) (Result, error) {
	current := runner.active.Add(1)
	defer runner.active.Add(-1)
	for {
		maximum := runner.max.Load()
		if current <= maximum || runner.max.CompareAndSwap(maximum, current) {
			break
		}
	}
	progress(NewStep("checkout.create", "running", "creating checkout", 0))
	select {
	case <-ctx.Done():
		return Result{ExtractionStatus: "cancelled", PaymentStatus: "not_started"}, ctx.Err()
	case <-time.After(runner.delay):
	}
	return Result{
		OK: true, Method: method, LongURL: "https://example.test/" + credential.Hash,
		ExtractionStatus: "link_ready", PaymentStatus: "awaiting_payment",
	}, nil
}

func TestJobManagerRunsConcurrentAndPersistsWithoutSecrets(t *testing.T) {
	runner := &fakeRunner{delay: 25 * time.Millisecond}
	path := filepath.Join(t.TempDir(), "jobs.json")
	manager, err := NewJobManager(runner, path, 8)
	if err != nil {
		t.Fatal(err)
	}
	tokens := []string{strings.Repeat("a", 90), strings.Repeat("b", 90), strings.Repeat("c", 90), strings.Repeat("d", 90)}
	job, err := manager.Create(BatchRequest{Method: MethodPayPalBA, Input: strings.Join(tokens, "\n"), Concurrency: 2, Options: Options{Proxy: "http://main.test:8080"}})
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(3 * time.Second)
	for !terminalJob(job.Status) && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
		job, err = manager.Get(job.ID)
		if err != nil {
			t.Fatal(err)
		}
	}
	if job.Status != JobCompleted || job.Succeeded != 4 {
		t.Fatalf("unexpected completed job: %#v", job)
	}
	if got := runner.max.Load(); got != 2 {
		t.Fatalf("observed concurrency %d, want 2", got)
	}
	encoded, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	for _, token := range tokens {
		if strings.Contains(string(encoded), token) {
			t.Fatal("persisted job history contains an access token")
		}
	}
	if strings.Contains(string(encoded), "proxyPassword") {
		t.Fatal("persisted job history contains a proxy password")
	}
	reloaded, err := NewJobManager(runner, path, 8)
	if err != nil {
		t.Fatal(err)
	}
	loaded, err := reloaded.Get(job.ID)
	if err != nil || loaded.Status != JobCompleted || loaded.Succeeded != 4 {
		t.Fatalf("completed job did not reload: job=%#v err=%v", loaded, err)
	}
	credentialStore, err := os.ReadFile(path + ".credentials.enc")
	if err != nil {
		t.Fatal(err)
	}
	for _, token := range tokens {
		if strings.Contains(string(credentialStore), token) {
			t.Fatal("encrypted credential store exposed a plaintext token")
		}
	}
	if info, err := os.Stat(path + ".credentials.key"); err != nil || info.Mode().Perm() != 0o600 {
		t.Fatalf("credential key permissions = %v err=%v, want 0600", info.Mode().Perm(), err)
	}
}

func TestStoredJobCanRetryWithoutReturningPlaintext(t *testing.T) {
	runner := &fakeRunner{delay: 5 * time.Millisecond}
	path := filepath.Join(t.TempDir(), "jobs.json")
	manager, err := NewJobManager(runner, path, 4)
	if err != nil {
		t.Fatal(err)
	}
	token := strings.Repeat("z", 90)
	proxy := "http://stored-user:stored-pass@proxy.test:8080"
	first, err := manager.Create(BatchRequest{Method: MethodIDEAL, Input: token, Concurrency: 1, Options: Options{Proxy: proxy}})
	if err != nil {
		t.Fatal(err)
	}
	first = waitForTerminalJob(t, manager, first.ID)
	if !first.InputStored {
		t.Fatal("new job did not advertise locally stored encrypted input")
	}
	retried, err := manager.Retry(first.ID)
	if err != nil {
		t.Fatal(err)
	}
	if retried.ID == first.ID || !retried.InputStored {
		t.Fatalf("retry did not create an independent stored-input job: %#v", retried)
	}
	retried = waitForTerminalJob(t, manager, retried.ID)
	if retried.Status != JobCompleted {
		t.Fatalf("retried job status = %s", retried.Status)
	}
	for _, file := range []string{path, path + ".credentials.enc"} {
		encoded, readErr := os.ReadFile(file)
		if readErr != nil {
			t.Fatal(readErr)
		}
		if strings.Contains(string(encoded), token) || strings.Contains(string(encoded), proxy) {
			t.Fatalf("%s exposed plaintext stored input", file)
		}
	}
}

func TestCreateRejectsSameAccountWhileAnotherMethodIsRunning(t *testing.T) {
	runner := &fakeRunner{delay: 200 * time.Millisecond}
	manager, err := NewJobManager(runner, filepath.Join(t.TempDir(), "jobs.json"), 2)
	if err != nil {
		t.Fatal(err)
	}
	firstToken := testJWTWithAccount(t, "account-shared", "first@example.com", "first")
	secondToken := testJWTWithAccount(t, "account-shared", "second@example.com", "second")
	first, err := manager.Create(BatchRequest{Method: MethodIDEAL, Input: firstToken, Concurrency: 1, Options: Options{Proxy: "http://main.test:8080"}})
	if err != nil {
		t.Fatal(err)
	}
	_, err = manager.Create(BatchRequest{Method: MethodPayPalBA, Input: secondToken, Concurrency: 1, Options: Options{Proxy: "http://main.test:8080"}})
	if !errors.Is(err, ErrAccountActive) {
		t.Fatalf("second method error = %v, want ErrAccountActive", err)
	}
	_ = waitForTerminalJob(t, manager, first.ID)
	third, err := manager.Create(BatchRequest{Method: MethodPayPalBA, Input: secondToken, Concurrency: 1, Options: Options{Proxy: "http://main.test:8080"}})
	if err != nil {
		t.Fatalf("account lock was not released: %v", err)
	}
	_ = waitForTerminalJob(t, manager, third.ID)
}

func TestRetryCanSelectOnlyRetryableFailures(t *testing.T) {
	runner := &fakeRunner{delay: time.Millisecond}
	manager, err := NewJobManager(runner, filepath.Join(t.TempDir(), "jobs.json"), 4)
	if err != nil {
		t.Fatal(err)
	}
	tokens := []string{
		strings.Repeat("s", 90), strings.Repeat("i", 90), strings.Repeat("p", 90),
		strings.Repeat("o", 90), strings.Repeat("r", 90),
	}
	job, err := manager.Create(BatchRequest{Method: MethodIDEAL, Input: strings.Join(tokens, "\n"), Concurrency: 3, Options: Options{Proxy: "http://main.test:8080"}})
	if err != nil {
		t.Fatal(err)
	}
	job = waitForTerminalJob(t, manager, job.ID)
	manager.mu.Lock()
	items := manager.jobs[job.ID].Items
	items[0].Status = ItemSucceeded
	items[1].Status, items[1].Error = ItemFailed, "Token 验证失败: account.eligibility HTTP 401"
	items[2].Status, items[2].Decision, items[2].PaymentStatus = ItemFailed, "already_paid", "already_paid"
	items[3].Status, items[3].CheckoutID, items[3].Error = ItemFailed, "oaics_legacy", "No such payment_page"
	items[4].Status, items[4].Error = ItemFailed, "账号资格检查上游不可用: /backend-api/me HTTP 403: Cloudflare challenge"
	manager.jobs[job.ID].Items = items
	manager.recountLocked(manager.jobs[job.ID])
	manager.mu.Unlock()

	retried, err := manager.Retry(job.ID, RetryOptions{
		FailedOnly: true, ExcludeSucceeded: true, ExcludeInvalidToken: true,
		ExcludePaidPlan: true, ExcludeLegacyOAICS: true,
	})
	if err != nil {
		t.Fatal(err)
	}
	if retried.Total != 1 || len(retried.Items) != 1 || retried.Items[0].TokenHash != tokenHash(tokens[4]) {
		t.Fatalf("CF-blocked account must remain retryable while explicit 401 is excluded: %#v", retried.Items)
	}
	if !retried.InputStored {
		t.Fatal("filtered retry did not retain encrypted local input")
	}
	_ = waitForTerminalJob(t, manager, retried.ID)
}

func TestRetryCanSelectExplicitAccountsAcrossStatuses(t *testing.T) {
	runner := &fakeRunner{delay: time.Millisecond}
	manager, err := NewJobManager(runner, filepath.Join(t.TempDir(), "jobs.json"), 4)
	if err != nil {
		t.Fatal(err)
	}
	tokens := []string{strings.Repeat("a", 90), strings.Repeat("b", 90), strings.Repeat("c", 90)}
	job, err := manager.Create(BatchRequest{Method: MethodPayPalBA, Input: strings.Join(tokens, "\n"), Concurrency: 3, Options: Options{Proxy: "http://main.test:8080"}})
	if err != nil {
		t.Fatal(err)
	}
	job = waitForTerminalJob(t, manager, job.ID)
	manager.mu.Lock()
	manager.jobs[job.ID].Items[1].Status = ItemFailed
	manager.jobs[job.ID].Items[1].Error = "temporary failure"
	manager.recountLocked(manager.jobs[job.ID])
	manager.mu.Unlock()

	retried, err := manager.Retry(job.ID, RetryOptions{ItemIDs: []string{job.Items[0].ID, job.Items[1].ID}})
	if err != nil {
		t.Fatal(err)
	}
	if retried.Total != 2 || retried.Items[0].TokenHash != tokenHash(tokens[0]) || retried.Items[1].TokenHash != tokenHash(tokens[1]) {
		t.Fatalf("explicit retry selected unexpected accounts: %#v", retried.Items)
	}
	_ = waitForTerminalJob(t, manager, retried.ID)
}

func TestVerifyPaymentUpdatesSucceededItemsWithoutChangingExtractionResult(t *testing.T) {
	runner := &verifyingRunner{fakeRunner: fakeRunner{delay: time.Millisecond}}
	manager, err := NewJobManager(runner, filepath.Join(t.TempDir(), "jobs.json"), 4)
	if err != nil {
		t.Fatal(err)
	}
	job, err := manager.Create(BatchRequest{
		Method:      MethodIDEAL,
		Input:       strings.Join([]string{strings.Repeat("p", 90), strings.Repeat("f", 90)}, "\n"),
		Concurrency: 2, Options: Options{Proxy: "http://main.test:8080"},
	})
	if err != nil {
		t.Fatal(err)
	}
	job = waitForTerminalJob(t, manager, job.ID)
	verified, err := manager.VerifyPayment(job.ID, true)
	if err != nil {
		t.Fatal(err)
	}
	if verified.Items[0].PaymentStatus != "paid_success" || verified.Items[1].PaymentStatus != "awaiting_payment" {
		t.Fatalf("unexpected payment verification statuses: %#v", verified.Items)
	}
	for _, item := range verified.Items {
		if item.Status != ItemSucceeded || item.LongURL == "" {
			t.Fatalf("payment verification changed extraction result: %#v", item)
		}
	}
}

func TestPayPalRandomBalancedAssignmentIsBalancedStableAndUsedAtRuntime(t *testing.T) {
	manager, err := NewJobManager(&countryEchoRunner{}, "", 16)
	if err != nil {
		t.Fatal(err)
	}
	input := testBatchTokens(11)
	request := BatchRequest{
		Method: MethodPayPalBA, Input: input, Concurrency: 5,
		Options: Options{
			Proxy: "http://main.test:8080", MaxAttempts: 10,
			CountryMode: CountryModeRandom, CountryPool: []string{"us", "UK", "jp", "uk"},
			AssignmentStrategy: AssignmentStrategyRandomBalanced, AssignmentSeed: "repeatable-country-seed",
		},
	}
	first, err := manager.Create(request)
	if err != nil {
		t.Fatal(err)
	}
	secondRequest := request
	secondRequest.Concurrency = 1
	second, err := manager.Create(secondRequest)
	if err != nil {
		t.Fatal(err)
	}
	first = waitForTerminalJob(t, manager, first.ID)
	second = waitForTerminalJob(t, manager, second.ID)

	if first.Options.CountryMode != CountryModeRandom || first.Options.AssignmentStrategy != AssignmentStrategyRandomBalanced || first.Options.AssignmentSeed != "repeatable-country-seed" {
		t.Fatalf("random assignment snapshot was not preserved: %#v", first.Options)
	}
	wantPool := []string{"US", "GB", "JP"}
	if strings.Join(first.Options.CountryPool, ",") != strings.Join(wantPool, ",") {
		t.Fatalf("normalized country pool = %#v, want %#v", first.Options.CountryPool, wantPool)
	}

	counts := map[string]int{}
	firstSequence := make([]string, len(first.Items))
	secondSequence := make([]string, len(second.Items))
	for index, item := range first.Items {
		counts[item.Country]++
		firstSequence[index] = item.Country
		if item.Currency != currencyForCountry(item.Country) {
			t.Fatalf("item %d has country/currency %s/%s", index, item.Country, item.Currency)
		}
		if item.Result == nil || item.Result.Country != item.Country || item.Result.Currency != item.Currency {
			t.Fatalf("runtime did not receive item %d assignment: item=%#v result=%#v", index, item, item.Result)
		}
	}
	for index, item := range second.Items {
		secondSequence[index] = item.Country
	}
	if strings.Join(firstSequence, ",") != strings.Join(secondSequence, ",") {
		t.Fatalf("same seed produced different assignments: first=%v second=%v", firstSequence, secondSequence)
	}
	minimum, maximum := len(first.Items), 0
	for _, country := range wantPool {
		count := counts[country]
		if count < minimum {
			minimum = count
		}
		if count > maximum {
			maximum = count
		}
	}
	if maximum-minimum > 1 {
		t.Fatalf("assignment is not balanced: %#v", counts)
	}
}

func TestPayPalRandomAssignmentGeneratesSafeSeedAndPersistsWithoutRequestSecrets(t *testing.T) {
	path := filepath.Join(t.TempDir(), "jobs.json")
	manager, err := NewJobManager(&countryEchoRunner{}, path, 4)
	if err != nil {
		t.Fatal(err)
	}
	token := strings.Repeat("r", 90)
	proxy := "http://proxy-user:proxy-password@main.test:8080"
	job, err := manager.Create(BatchRequest{
		Method: MethodPayPalBA, Input: token, Concurrency: 1,
		Options: Options{
			Proxy: proxy, CountryMode: CountryModeRandom,
			CountryPool: []string{"BR", "JP"}, AssignmentStrategy: AssignmentStrategyRandomBalanced,
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	job = waitForTerminalJob(t, manager, job.ID)
	seedBytes, err := hex.DecodeString(job.Options.AssignmentSeed)
	if err != nil || len(seedBytes) != 16 {
		t.Fatalf("generated seed %q is not 128-bit hex: bytes=%d err=%v", job.Options.AssignmentSeed, len(seedBytes), err)
	}
	encoded, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	history := string(encoded)
	for _, secret := range []string{token, proxy, "proxy-user", "proxy-password"} {
		if strings.Contains(history, secret) {
			t.Fatalf("safe history contains request secret %q", secret)
		}
	}
	for _, expected := range []string{`"countryMode": "random"`, `"assignmentStrategy": "random_balanced"`, job.Options.AssignmentSeed, `"BR"`, `"JP"`} {
		if !strings.Contains(history, expected) {
			t.Fatalf("safe history is missing %q: %s", expected, history)
		}
	}
	reloaded, err := NewJobManager(&countryEchoRunner{}, path, 4)
	if err != nil {
		t.Fatal(err)
	}
	loaded, err := reloaded.Get(job.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(loaded.Items) != 1 || loaded.Items[0].Country == "" || loaded.Items[0].Currency == "" {
		t.Fatalf("persisted history lost item country/currency: %#v", loaded.Items)
	}
}

func TestRandomBalancedCountryValidation(t *testing.T) {
	validProxy := "http://main.test:8080"
	tests := []struct {
		name    string
		method  string
		options Options
		want    string
	}{
		{
			name: "paypal needs two countries", method: MethodPayPalBA,
			options: Options{Proxy: validProxy, CountryMode: CountryModeRandom, CountryPool: []string{"US"}, AssignmentStrategy: AssignmentStrategyRandomBalanced},
			want:    "至少需要选择 2 个",
		},
		{
			name: "paypal rejects promotion-only country as main", method: MethodPayPalBA,
			options: Options{Proxy: validProxy, CountryMode: CountryModeRandom, CountryPool: []string{"US", "TR"}, AssignmentStrategy: AssignmentStrategyRandomBalanced},
			want:    "TR 是 PP 优惠地区",
		},
		{
			name: "paypal rejects unadapted country", method: MethodPayPalBA,
			options: Options{Proxy: validProxy, CountryMode: CountryModeRandom, CountryPool: []string{"US", "SG"}, AssignmentStrategy: AssignmentStrategyRandomBalanced},
			want:    "未适配国家: SG",
		},
		{
			name: "paypal rejects another strategy", method: MethodPayPalBA,
			options: Options{Proxy: validProxy, CountryMode: CountryModeRandom, CountryPool: []string{"US", "GB"}, AssignmentStrategy: "round_robin"},
			want:    "random_balanced",
		},
		{
			name: "other methods reject random country", method: MethodDirect,
			options: Options{Proxy: validProxy, CountryMode: CountryModeRandom, CountryPool: []string{"US", "GB"}, AssignmentStrategy: AssignmentStrategyRandomBalanced},
			want:    "仅支持 PP 提炼",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			manager, err := NewJobManager(&fakeRunner{}, "", 1)
			if err != nil {
				t.Fatal(err)
			}
			_, err = manager.Create(BatchRequest{Method: test.method, Input: strings.Repeat("v", 90), Concurrency: 1, Options: test.options})
			if err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("error = %v, want text %q", err, test.want)
			}
		})
	}
}

func TestSingleCountryModeKeepsExistingBehavior(t *testing.T) {
	manager, err := NewJobManager(&countryEchoRunner{}, "", 2)
	if err != nil {
		t.Fatal(err)
	}
	job, err := manager.Create(BatchRequest{
		Method: MethodPayPalBA, Input: strings.Repeat("w", 90), Concurrency: 1,
		Options: Options{
			Proxy: "http://main.test:8080", Country: "GB", CountryMode: CountryModeSingle,
			CountryPool: []string{"US", "JP"}, AssignmentStrategy: AssignmentStrategyRandomBalanced, AssignmentSeed: "stale-seed",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	job = waitForTerminalJob(t, manager, job.ID)
	if job.Options.CountryMode != CountryModeSingle || job.Options.Country != "GB" || job.Options.Currency != "GBP" {
		t.Fatalf("single-country options regressed: %#v", job.Options)
	}
	if len(job.Options.CountryPool) != 0 || job.Options.AssignmentStrategy != "" || job.Options.AssignmentSeed != "" {
		t.Fatalf("single-country mode retained stale random settings: %#v", job.Options)
	}
	if len(job.Items) != 1 || job.Items[0].Country != "GB" || job.Items[0].Currency != "GBP" || job.Items[0].Result == nil || job.Items[0].Result.Country != "GB" {
		t.Fatalf("single-country item regressed: %#v", job.Items)
	}
}

func TestJobManagerCancel(t *testing.T) {
	runner := &fakeRunner{delay: 2 * time.Second}
	manager, err := NewJobManager(runner, "", 2)
	if err != nil {
		t.Fatal(err)
	}
	job, err := manager.Create(BatchRequest{Method: MethodDirect, Input: strings.Repeat("z", 90), Concurrency: 1, Options: Options{Proxy: "http://main.test:8080"}})
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(20 * time.Millisecond)
	if _, err := manager.Cancel(job.ID); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		job, _ = manager.Get(job.ID)
		if job.Status == JobCancelled {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("job was not cancelled: %#v", job)
}

func TestJobManagerStreamsStepsDuringRun(t *testing.T) {
	runner := &fakeRunner{delay: 120 * time.Millisecond}
	manager, err := NewJobManager(runner, "", 1)
	if err != nil {
		t.Fatal(err)
	}
	job, err := manager.Create(BatchRequest{Method: MethodDirect, Input: strings.Repeat("s", 90), Concurrency: 1, Options: Options{Proxy: "http://main.test:8080"}})
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(time.Second)
	var sawLiveStep bool
	for time.Now().Before(deadline) {
		current, err := manager.Get(job.ID)
		if err != nil {
			t.Fatal(err)
		}
		if len(current.Items) > 0 && len(current.Items[0].Steps) > 0 {
			sawLiveStep = true
			if current.Items[0].Stage != "checkout.create" {
				t.Fatalf("live stage = %q, want checkout.create", current.Items[0].Stage)
			}
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if !sawLiveStep {
		t.Fatal("expected live steps while the account was still running")
	}
	deadline = time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		current, err := manager.Get(job.ID)
		if err != nil {
			t.Fatal(err)
		}
		if terminalJob(current.Status) {
			if len(current.Items[0].Steps) == 0 {
				t.Fatal("completed item lost streamed steps")
			}
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("job did not complete after streaming steps")
}

func TestKakaoBatchPreservesConcurrencyAndAttempts(t *testing.T) {
	runner := &fakeRunner{delay: time.Millisecond}
	manager, err := NewJobManager(runner, "", 8)
	if err != nil {
		t.Fatal(err)
	}
	first := strings.Repeat("k", 90)
	second := strings.Repeat("l", 90)
	job, err := manager.Create(BatchRequest{
		Method:      MethodKakao,
		Input:       strings.Join([]string{first, second}, "\n"),
		Concurrency: 2,
		Options:     Options{Proxy: "http://main.test:8080", KakaoMode: KakaoModeEligibility, MaxAttempts: 5},
	})
	if err != nil {
		t.Fatal(err)
	}
	if job.Total != 2 || job.Concurrency != 2 || job.Options.KakaoMode != KakaoModeEligibility || !job.Options.KakaoEligibilityOnly || job.Options.MaxAttempts != 5 || job.Options.UsePromo {
		t.Fatalf("Kakao eligibility batch settings were not preserved: %#v", job)
	}
	third := strings.Repeat("m", 90)
	providerJob, err := manager.Create(BatchRequest{
		Method: MethodKakao, Input: strings.Join([]string{first, second, third}, "\n"), Concurrency: 9,
		Options: Options{Proxy: "http://main.test:8080", KakaoMode: KakaoModeProviderLink, PromotionProxyRegion: "JP", MaxAttempts: 4},
	})
	if err != nil {
		t.Fatal(err)
	}
	if providerJob.Total != 3 || providerJob.Concurrency != 3 || providerJob.Options.KakaoMode != KakaoModeProviderLink || providerJob.Options.KakaoEligibilityOnly || providerJob.Options.MaxAttempts != 4 || !providerJob.Options.UsePromo || providerJob.Options.PromotionProxyRegion != "JP" {
		t.Fatalf("Kakao provider-link batch settings were not preserved: %#v", providerJob)
	}
	if _, err := manager.Create(BatchRequest{
		Method: MethodKakao, Input: strings.Repeat("n", 90), Options: Options{Proxy: "http://main.test:8080", KakaoMode: "unknown"},
	}); err == nil || !strings.Contains(err.Error(), "不支持的 Kakao 模式") {
		t.Fatalf("unknown Kakao mode must be rejected, got %v", err)
	}
}

func TestKakaoEligibilityContinuationPersistsAndLinksProviderJob(t *testing.T) {
	runner := &fakeRunner{delay: time.Millisecond}
	path := filepath.Join(t.TempDir(), "jobs.json")
	manager, err := NewJobManager(runner, path, 4)
	if err != nil {
		t.Fatal(err)
	}
	token := strings.Repeat("q", 90)
	eligibility, err := manager.Create(BatchRequest{
		Method: MethodKakao, Input: token, Concurrency: 1,
		Options: Options{Proxy: "http://main.test:8080", KakaoMode: KakaoModeEligibility},
	})
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(time.Second)
	for !terminalJob(eligibility.Status) && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
		eligibility, _ = manager.Get(eligibility.ID)
	}
	requested, err := manager.RequestKakaoProviderContinuation(eligibility.ID, 10)
	if err != nil {
		t.Fatal(err)
	}
	if requested.Continuation == nil || requested.Continuation.Status != "requested" || requested.Continuation.MaxAttempts != 10 {
		t.Fatalf("unexpected continuation request: %#v", requested.Continuation)
	}
	provider, err := manager.Create(BatchRequest{
		Method: MethodKakao, Input: token, Concurrency: 1,
		Options: Options{Proxy: "http://main.test:8080", KakaoMode: KakaoModeProviderLink, MaxAttempts: 10},
	})
	if err != nil {
		t.Fatal(err)
	}
	submitted, err := manager.MarkKakaoProviderContinuationSubmitted(eligibility.ID, provider.ID)
	if err != nil {
		t.Fatal(err)
	}
	if submitted.Continuation == nil || submitted.Continuation.Status != "submitted" || submitted.Continuation.SubmittedJobID != provider.ID {
		t.Fatalf("unexpected submitted continuation: %#v", submitted.Continuation)
	}
	reloaded, err := NewJobManager(runner, path, 4)
	if err != nil {
		t.Fatal(err)
	}
	loaded, err := reloaded.Get(eligibility.ID)
	if err != nil || loaded.Continuation == nil || loaded.Continuation.SubmittedJobID != provider.ID {
		t.Fatalf("continuation did not persist: job=%#v err=%v", loaded, err)
	}
	// Create starts the provider job asynchronously. Wait for it to finish so
	// its final persist cannot race t.TempDir cleanup after this test returns.
	deadline = time.Now().Add(time.Second)
	for !terminalJob(provider.Status) && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
		provider, _ = manager.Get(provider.ID)
	}
	if !terminalJob(provider.Status) {
		t.Fatalf("provider job did not finish before test cleanup: %#v", provider)
	}
}

func TestKakaoEligibilitySummaryKeepsSafeDistinctAccountEvidence(t *testing.T) {
	job := &Job{
		ID: "kakao-summary", Method: MethodKakao, Status: JobCompleted,
		Items: []JobItem{
			{Email: "Eligible@example.com", Decision: "eligible"},
			{Email: "eligible@example.com", Decision: "eligible"},
			{Email: "other@example.com", Decision: "ineligible"},
			{Email: "pending@example.com", Decision: ""},
		},
	}
	summary := summaryForJob(job)
	if summary.Eligible != 2 || summary.Ineligible != 1 {
		t.Fatalf("unexpected Kakao decision counts: %#v", summary)
	}
	if len(summary.EligibleAccounts) != 1 || !strings.EqualFold(summary.EligibleAccounts[0], "eligible@example.com") {
		t.Fatalf("eligible accounts were not deduplicated safely: %#v", summary.EligibleAccounts)
	}
}

func TestJobManagerListReturnsCompleteHistoryUnlessLimited(t *testing.T) {
	manager, err := NewJobManager(&fakeRunner{}, "", 1)
	if err != nil {
		t.Fatal(err)
	}
	manager.mu.Lock()
	for index := 0; index < 135; index++ {
		id := fmt.Sprintf("history-%03d", index)
		manager.order = append(manager.order, id)
		manager.jobs[id] = &Job{
			ID: id, Method: MethodKakao, MethodLabel: "Kakao Pay", Status: JobCompleted,
			Total: 1, Succeeded: 1, Options: JobOptions{Country: "KR", Currency: "KRW"},
		}
	}
	manager.mu.Unlock()

	if summaries := manager.List(0); len(summaries) != 135 {
		t.Fatalf("unlimited history returned %d jobs, want 135", len(summaries))
	} else if summaries[0].Options.Country != "KR" || summaries[0].Options.Currency != "KRW" {
		t.Fatalf("history summary lost safe job options: %#v", summaries[0].Options)
	}
	if summaries := manager.List(7); len(summaries) != 7 {
		t.Fatalf("limited history returned %d jobs, want 7", len(summaries))
	}
}

func TestJobManagerDeletesTerminalHistory(t *testing.T) {
	runner := &fakeRunner{delay: 5 * time.Millisecond}
	path := filepath.Join(t.TempDir(), "jobs.json")
	manager, err := NewJobManager(runner, path, 2)
	if err != nil {
		t.Fatal(err)
	}
	job, err := manager.Create(BatchRequest{Method: MethodDirect, Input: strings.Repeat("d", 90), Concurrency: 1, Options: Options{Proxy: "http://main.test:8080"}})
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(time.Second)
	for !terminalJob(job.Status) && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
		job, err = manager.Get(job.ID)
		if err != nil {
			t.Fatal(err)
		}
	}
	if err := manager.Delete(job.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Get(job.ID); !errors.Is(err, ErrJobNotFound) {
		t.Fatalf("deleted job is still readable: %v", err)
	}
	reloaded, err := NewJobManager(runner, path, 2)
	if err != nil {
		t.Fatal(err)
	}
	if summaries := reloaded.List(10); len(summaries) != 0 {
		t.Fatalf("deleted job returned after reload: %#v", summaries)
	}
}

func TestJobManagerRejectsDeletingActiveJob(t *testing.T) {
	manager, err := NewJobManager(&fakeRunner{delay: time.Second}, "", 1)
	if err != nil {
		t.Fatal(err)
	}
	job, err := manager.Create(BatchRequest{Method: MethodDirect, Input: strings.Repeat("e", 90), Concurrency: 1, Options: Options{Proxy: "http://main.test:8080"}})
	if err != nil {
		t.Fatal(err)
	}
	if err := manager.Delete(job.ID); !errors.Is(err, ErrJobActive) {
		t.Fatalf("active delete error = %v, want ErrJobActive", err)
	}
	_, _ = manager.Cancel(job.ID)
}

func TestLoadedUPIHistoryDropsIconsAndFalseSuccess(t *testing.T) {
	path := filepath.Join(t.TempDir(), "jobs.json")
	job := &Job{
		ID: "legacy-upi", Method: MethodUPI, MethodLabel: "UPI", Status: JobCompleted,
		Total: 1, Succeeded: 1, CreatedAt: time.Now().UTC().Format(time.RFC3339),
		Items: []JobItem{{
			ID: "legacy-upi-001", Index: 1,
			Label: "map[planType:free]", Email: "person@example.test", Status: ItemSucceeded,
			ExtractionStatus: "upi_ready", PaymentStatus: "awaiting_upi_payment",
			LongURL:           "https://checkout.stripe.com/c/pay/cs_live_example",
			UPIInstructionURL: "https://checkout.stripe.com/c/pay/cs_live_example",
			QRPNGURL:          "https://js.stripe.com/v3/fingerprinted/img/payment-methods/icon-pm-upi@example.png",
			QRSVGURL:          "https://js.stripe.com/v3/fingerprinted/img/payment-methods/icon-pm-upi-example.svg",
			Result: &Result{OK: true, ExtractionStatus: "upi_ready", PaymentStatus: "awaiting_upi_payment", Metadata: map[string]any{
				"instructionUrl": "https://checkout.stripe.com/c/pay/cs_live_example",
				"qrPngUrl":       "https://js.stripe.com/v3/fingerprinted/img/payment-methods/icon-pm-upi@example.png",
			}},
		}},
	}
	encoded, err := json.Marshal(map[string]any{"version": 1, "jobs": []*Job{job}})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, encoded, 0o600); err != nil {
		t.Fatal(err)
	}
	manager, err := NewJobManager(&fakeRunner{}, path, 1)
	if err != nil {
		t.Fatal(err)
	}
	loaded, err := manager.Get(job.ID)
	if err != nil {
		t.Fatal(err)
	}
	item := loaded.Items[0]
	if loaded.Status != JobFailed || item.Status != ItemFailed || item.Label != item.Email {
		t.Fatalf("legacy false success was not corrected: %#v", loaded)
	}
	if item.LongURL != "" || item.UPIInstructionURL != "" || item.QRPNGURL != "" || item.QRSVGURL != "" {
		t.Fatalf("legacy fake UPI material was not removed: %#v", item)
	}
}

func terminalJob(status string) bool {
	return status == JobCompleted || status == JobFailed || status == JobCancelled || status == JobInterrupted
}

func testBatchTokens(count int) string {
	tokens := make([]string, count)
	for index := range tokens {
		tokens[index] = strings.Repeat(string(rune('a'+index)), 90)
	}
	return strings.Join(tokens, "\n")
}

func waitForTerminalJob(t *testing.T, manager *JobManager, jobID string) *Job {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		job, err := manager.Get(jobID)
		if err != nil {
			t.Fatal(err)
		}
		if terminalJob(job.Status) {
			return job
		}
		time.Sleep(5 * time.Millisecond)
	}
	job, err := manager.Get(jobID)
	if err != nil {
		t.Fatal(err)
	}
	t.Fatalf("job %s did not reach a terminal state: %#v", jobID, job)
	return nil
}
