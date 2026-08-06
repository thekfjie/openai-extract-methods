package extractapi

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"automyai/internal/extractmethods"
)

type apiRunner struct{}

func (apiRunner) Run(_ context.Context, method string, credential extractmethods.Credential, _ extractmethods.Options, progress extractmethods.ProgressFunc) (extractmethods.Result, error) {
	progress(extractmethods.NewStep("done", "success", "fixture", 0))
	return extractmethods.Result{OK: true, Method: method, LongURL: "https://example.test/" + credential.Hash, ExtractionStatus: "link_ready", PaymentStatus: "awaiting_payment"}, nil
}

func TestCatalogAndCompatibilityRoutes(t *testing.T) {
	manager, err := extractmethods.NewJobManager(apiRunner{}, "", 4)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(NewServer(manager).Handler())
	defer server.Close()

	response, err := http.Get(server.URL + "/api/extract/catalog")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	var catalog map[string]any
	if err := json.NewDecoder(response.Body).Decode(&catalog); err != nil {
		t.Fatal(err)
	}
	if catalog["defaultMethod"] != extractmethods.MethodPayPalBA {
		t.Fatalf("PayPal must be default: %#v", catalog)
	}
	var kakao map[string]any
	for _, raw := range catalog["methods"].([]any) {
		method, _ := raw.(map[string]any)
		if method["id"] == extractmethods.MethodKakao {
			kakao = method
			break
		}
	}
	if kakao == nil || kakao["supportsConcurrency"] != true || kakao["supportsPaymentStatus"] != true {
		t.Fatalf("catalog must expose the Kakao configurable batch dual-mode capability: %#v", kakao)
	}
	modes, _ := kakao["modes"].([]any)
	if len(modes) != 2 || modes[0] != extractmethods.KakaoModeEligibility || modes[1] != extractmethods.KakaoModeProviderLink {
		t.Fatalf("unexpected Kakao catalog modes: %#v", modes)
	}

	token := strings.Repeat("x", 90)
	for path, wantedMethod := range map[string]string{
		"/api/extract-pp":      extractmethods.MethodPayPalBA,
		"/api/paper-card-task": extractmethods.MethodDirect,
		"/api/long-link-task":  extractmethods.MethodPayPalBA,
	} {
		body, _ := json.Marshal(map[string]any{"accessToken": token, "concurrency": 1, "link_type": "paypal", "proxy": "http://main.test:8080"})
		result, err := http.Post(server.URL+path, "application/json", bytes.NewReader(body))
		if err != nil {
			t.Fatal(err)
		}
		var payload struct {
			Job struct {
				Method string `json:"method"`
			} `json:"job"`
		}
		if err := json.NewDecoder(result.Body).Decode(&payload); err != nil {
			_ = result.Body.Close()
			t.Fatal(err)
		}
		_ = result.Body.Close()
		if result.StatusCode != http.StatusAccepted || payload.Job.Method != wantedMethod {
			t.Fatalf("%s returned %d method=%s, want %s", path, result.StatusCode, payload.Job.Method, wantedMethod)
		}
	}
}

func TestKakaoJobAPIValidatesModeAndKeepsProviderLinkParameters(t *testing.T) {
	manager, err := extractmethods.NewJobManager(apiRunner{}, "", 1)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(NewServer(manager).Handler())
	defer server.Close()

	post := func(mode string, input string) (*http.Response, map[string]any) {
		t.Helper()
		body, _ := json.Marshal(map[string]any{
			"method": extractmethods.MethodKakao, "input": input, "concurrency": 8,
			"options": map[string]any{
				"proxy": "http://main.test:8080", "kakaoMode": mode,
				"promotionProxyRegion": "JP", "maxAttempts": 4, "usePromo": true,
			},
		})
		response, requestErr := http.Post(server.URL+"/api/extract/jobs", "application/json", bytes.NewReader(body))
		if requestErr != nil {
			t.Fatal(requestErr)
		}
		var payload map[string]any
		if decodeErr := json.NewDecoder(response.Body).Decode(&payload); decodeErr != nil {
			_ = response.Body.Close()
			t.Fatal(decodeErr)
		}
		_ = response.Body.Close()
		return response, payload
	}

	invalid, payload := post("unknown", strings.Repeat("k", 90))
	errorText, _ := payload["error"].(string)
	if invalid.StatusCode != http.StatusBadRequest || !strings.Contains(strings.TrimSpace(errorText), "不支持的 Kakao 模式") {
		t.Fatalf("unknown Kakao mode should return 400: status=%d payload=%#v", invalid.StatusCode, payload)
	}

	accepted, payload := post(extractmethods.KakaoModeProviderLink, strings.Join([]string{strings.Repeat("p", 90), strings.Repeat("q", 90), strings.Repeat("r", 90)}, "\n"))
	if accepted.StatusCode != http.StatusAccepted {
		t.Fatalf("provider-link request returned %d: %#v", accepted.StatusCode, payload)
	}
	job := payload["job"].(map[string]any)
	options := job["options"].(map[string]any)
	if job["concurrency"] != float64(3) || job["total"] != float64(3) || options["kakaoMode"] != extractmethods.KakaoModeProviderLink || options["promotionProxyRegion"] != "JP" || options["maxAttempts"] != float64(4) {
		t.Fatalf("provider-link parameters were not preserved safely: %#v", job)
	}
}

func TestKakaoContinuationRouteQueuesBrowserSideProviderResume(t *testing.T) {
	manager, err := extractmethods.NewJobManager(apiRunner{}, "", 1)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(NewServer(manager).Handler())
	defer server.Close()

	body, _ := json.Marshal(map[string]any{
		"method": extractmethods.MethodKakao,
		"input": strings.Repeat("c", 90),
		"options": map[string]any{"proxy": "http://main.test:8080", "kakaoMode": extractmethods.KakaoModeEligibility},
	})
	response, err := http.Post(server.URL+"/api/extract/jobs", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	var created struct {
		Job extractmethods.Job `json:"job"`
	}
	if err := json.NewDecoder(response.Body).Decode(&created); err != nil {
		_ = response.Body.Close()
		t.Fatal(err)
	}
	_ = response.Body.Close()
	deadline := time.Now().Add(time.Second)
	for !terminalJobStatus(created.Job.Status) && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
		loaded, _ := manager.Get(created.Job.ID)
		if loaded != nil {
			created.Job = *loaded
		}
	}

	continuationBody := bytes.NewBufferString(`{"maxAttempts":25}`)
	continuationResponse, err := http.Post(server.URL+"/api/extract/jobs/"+created.Job.ID+"/continue-provider", "application/json", continuationBody)
	if err != nil {
		t.Fatal(err)
	}
	defer continuationResponse.Body.Close()
	var payload struct {
		Job extractmethods.Job `json:"job"`
	}
	if err := json.NewDecoder(continuationResponse.Body).Decode(&payload); err != nil {
		t.Fatal(err)
	}
	if continuationResponse.StatusCode != http.StatusOK || payload.Job.Continuation == nil || payload.Job.Continuation.Status != "requested" || payload.Job.Continuation.MaxAttempts != 25 {
		t.Fatalf("unexpected continuation response: status=%d job=%#v", continuationResponse.StatusCode, payload.Job)
	}
}

func terminalJobStatus(status string) bool {
	return status == extractmethods.JobCompleted || status == extractmethods.JobFailed || status == extractmethods.JobCancelled || status == extractmethods.JobInterrupted
}

func TestDeleteTerminalJobRoute(t *testing.T) {
	manager, err := extractmethods.NewJobManager(apiRunner{}, "", 1)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(NewServer(manager).Handler())
	defer server.Close()

	body, _ := json.Marshal(map[string]any{
		"method":      extractmethods.MethodDirect,
		"input":       strings.Repeat("y", 90),
		"concurrency": 1,
		"options":     map[string]any{"proxy": "http://main.test:8080"},
	})
	response, err := http.Post(server.URL+"/api/extract/jobs", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	var created struct {
		Job extractmethods.Job `json:"job"`
	}
	if err := json.NewDecoder(response.Body).Decode(&created); err != nil {
		_ = response.Body.Close()
		t.Fatal(err)
	}
	_ = response.Body.Close()
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		job, getErr := manager.Get(created.Job.ID)
		if getErr == nil && (job.Status == extractmethods.JobCompleted || job.Status == extractmethods.JobFailed) {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	request, _ := http.NewRequest(http.MethodDelete, server.URL+"/api/extract/jobs/"+created.Job.ID, nil)
	deleted, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer deleted.Body.Close()
	if deleted.StatusCode != http.StatusOK {
		t.Fatalf("DELETE returned %d", deleted.StatusCode)
	}
}
