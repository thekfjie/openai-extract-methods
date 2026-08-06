package controlapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"automyai/internal/fingerprintsdk"
	"automyai/internal/taskqueue"
)

type fakeSDK struct{}

func (fakeSDK) Available() bool { return true }
func (fakeSDK) Presets(context.Context) ([]string, error) {
	return []string{"windows-11-chrome"}, nil
}
func (fakeSDK) Generate(_ context.Context, request fingerprintsdk.Request) (any, error) {
	return validBundle(request.Preset, request.Seed, request.Source), nil
}

type localOnlySDK struct{ fakeSDK }

func (localOnlySDK) Generate(_ context.Context, request fingerprintsdk.Request) (any, error) {
	return validBundle(request.Preset, request.Seed, "local"), nil
}

func validBundle(preset, seed, source string) map[string]any {
	userAgent := "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
	deviceName := "DESKTOP-TEST150"
	webglVendor := "Google Inc. (Intel)"
	webglRenderer := "ANGLE (Intel Test)"
	baseDataSource := "local-template"
	generator := map[string]any{
		"algorithm": "roxybrowser-3.9.2-compatible", "featureMode": "random",
		"deterministic": true, "baseDataSource": baseDataSource,
	}
	if source == "cloud" {
		generator["baseDataSource"] = "authorized-provider"
		generator["provider"] = "roxy-authorized-api"
	}
	return map[string]any{
		"profile": map[string]any{
			"schemaVersion": 2,
			"purpose":       "local-browser-compatibility-testing",
			"id":            "0123456789abcdef",
			"seed":          seed,
			"preset":        preset,
			"generator":     generator,
			"engine":        map[string]any{"family": "Chrome", "version": "150.0.0.0", "userAgent": userAgent},
			"os":            map[string]any{"name": "Windows", "version": "11", "architecture": "x86", "model": ""},
			"machine":       map[string]any{"computerName": deviceName, "macAddress": "02:00:00:00:00:01"},
			"locale":        map[string]any{"appLocale": "en-US", "acceptLanguage": "en-US,en;q=0.9", "timezone": "UTC"},
			"navigator":     map[string]any{"platform": "Win32", "hardwareConcurrency": 8, "deviceMemory": 8, "maxTouchPoints": 0, "mobile": false},
			"screen":        map[string]any{"width": 1920, "height": 1080, "availWidth": 1920, "availHeight": 1040, "devicePixelRatio": 1},
			"graphics":      map[string]any{"webglVendor": webglVendor, "webglRenderer": webglRenderer},
			"canvas":        map[string]any{}, "audioContext": map[string]any{}, "clientRects": map[string]any{},
			"fonts": map[string]any{}, "speechSynthesis": map[string]any{}, "mediaDevices": map[string]any{},
			"webrtc": map[string]any{}, "geolocation": map[string]any{}, "content": map[string]any{},
			"security": map[string]any{}, "runtime": map[string]any{}, "battery": map[string]any{},
			"network": map[string]any{}, "bluetooth": map[string]any{},
		},
		"roxyConfig":    map[string]any{"userAgent": userAgent},
		"runtimeConfig": map[string]any{"engine": "Chrome", "launchArgs": []any{"--disable-background-mode"}},
	}
}

func testApplication(t *testing.T) (*Application, *taskqueue.Queue) {
	t.Helper()
	directory := t.TempDir()
	presets, _ := fakeSDK{}.Presets(context.Background())
	config, err := OpenConfig(filepath.Join(directory, "config.json"), presets)
	if err != nil {
		t.Fatal(err)
	}
	queue, err := taskqueue.Open(filepath.Join(directory, "tasks.json"), 4)
	if err != nil {
		t.Fatal(err)
	}
	app, err := NewApplication("openai3", fakeSDK{}, config, queue, NewLogRing(20), filepath.Join(directory, "profiles"))
	if err != nil {
		queue.Close()
		t.Fatal(err)
	}
	return app, queue
}

func TestRegistrationRouteIsGone(t *testing.T) {
	app, queue := testApplication(t)
	defer queue.Close()
	recorder := httptest.NewRecorder()
	app.ServeHTTP(recorder, httptest.NewRequest(http.MethodPost, "/api/start", strings.NewReader(`{}`)))
	if recorder.Code != http.StatusGone {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestCompatibilityTaskCompletes(t *testing.T) {
	app, queue := testApplication(t)
	defer queue.Close()
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/tasks", strings.NewReader(`{"type":"fingerprint-compatibility-check","input":{"presets":["windows-11-chrome"],"samplesPerPreset":1}}`))
	app.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusAccepted {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var response struct {
		Task taskqueue.Task `json:"task"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		task, ok := queue.Get(response.Task.ID)
		if ok && task.Status == taskqueue.Completed {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("compatibility task did not complete")
}

func TestProfileGenerationTaskWritesPrivateArtifact(t *testing.T) {
	app, queue := testApplication(t)
	defer queue.Close()
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/tasks", strings.NewReader(`{"type":"fingerprint-profile-generate","input":{"preset":"windows-11-chrome","seed":"fixed","count":1,"accountId":42,"accountEmail":"selected@example.test","accountGroup":"gpt_old_account"}}`))
	app.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusAccepted {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var response struct {
		Task taskqueue.Task `json:"task"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		task, ok := queue.Get(response.Task.ID)
		if ok && task.Status == taskqueue.Completed {
			result := task.Result.(ProfileGenerateResult)
			if result.Source != "local" || len(result.Profiles) != 1 || result.Profiles[0].Source != "local" || result.Profiles[0].Cloud {
				t.Fatalf("unexpected profile source: %#v", result)
			}
			if result.Account == nil || result.Account.ID != 42 || result.Account.Email != "selected@example.test" || result.Account.Group != "gpt_old_account" {
				t.Fatalf("unexpected account context: %#v", result.Account)
			}
			path := result.File
			info, err := os.Stat(path)
			if err != nil {
				t.Fatal(err)
			}
			if info.Mode().Perm() != 0o600 {
				t.Fatalf("artifact mode=%o", info.Mode().Perm())
			}
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("profile generation task did not complete")
}

func TestProfileGenerationRejectsAccountContextWithoutID(t *testing.T) {
	_, err := RunProfileGeneration(context.Background(), fakeSDK{}, DefaultConfig(), map[string]any{
		"preset": "windows-11-chrome", "seed": "fixed", "count": 1,
		"accountEmail": "selected@example.test",
	}, t.TempDir())
	if err == nil || !strings.Contains(err.Error(), "accountId is required") {
		t.Fatalf("expected account context validation error, got %v", err)
	}
}

func TestCloudProfileGenerationIsMarkedAndStrict(t *testing.T) {
	defaults := DefaultConfig()
	result, err := RunProfileGeneration(context.Background(), fakeSDK{}, defaults, map[string]any{
		"preset": "windows-11-chrome", "source": "cloud", "seed": "cloud-seed", "count": 1,
	}, t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if result.Source != "cloud" || len(result.Profiles) != 1 || !result.Profiles[0].Cloud || result.Profiles[0].Source != "cloud" {
		t.Fatalf("unexpected cloud result: %#v", result)
	}

	_, err = RunProfileGeneration(context.Background(), localOnlySDK{}, defaults, map[string]any{
		"preset": "windows-11-chrome", "source": "cloud", "seed": "mismatch", "count": 1,
	}, t.TempDir())
	if err == nil || !strings.Contains(err.Error(), "source mismatch") {
		t.Fatalf("expected strict source mismatch, got %v", err)
	}
}

func TestConfigRejectsUnknownFingerprintSource(t *testing.T) {
	config := DefaultConfig()
	config.FingerprintSource = "automatic"
	if err := config.Validate([]string{"windows-11-chrome"}); err == nil {
		t.Fatal("expected unsupported fingerprint source error")
	}
}

func TestConfigRejectsUnsupportedRegistrationFields(t *testing.T) {
	app, queue := testApplication(t)
	defer queue.Close()
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/config", strings.NewReader(`{"proxy":"http://user:pass@example:80"}`))
	app.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestRedactionCoversCommonProxyAndSecretShapes(t *testing.T) {
	redacted := RedactMap(map[string]any{
		"apiKey": "secret", "password": "secret",
		"proxyUrl":   "http://user:pass@example.test:8080",
		"proxyColon": "example.test:8080:user:pass",
	})
	if redacted["apiKey"] != "***" || redacted["password"] != "***" {
		t.Fatalf("secrets not redacted: %#v", redacted)
	}
	if redacted["proxyUrl"] != "http://user:***@example.test:8080" {
		t.Fatalf("URL proxy not redacted: %#v", redacted["proxyUrl"])
	}
	if redacted["proxyColon"] != "example.test:8080:***:***" {
		t.Fatalf("colon proxy not redacted: %#v", redacted["proxyColon"])
	}
}
