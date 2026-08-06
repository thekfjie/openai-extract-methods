package main

import (
	"context"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type fakeRunner struct{}

func (fakeRunner) Available() bool { return true }
func (fakeRunner) Presets(context.Context) ([]string, error) {
	return []string{"windows-11-chrome"}, nil
}
func (fakeRunner) Generate(_ context.Context, request generateRequest) (any, error) {
	userAgent := "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
	deviceName := "DESKTOP-TEST150"
	return map[string]any{
		"profile": map[string]any{
			"id": "profile-1", "preset": request.Preset, "seed": request.Seed,
			"engine": map[string]any{
				"family": "Chrome", "version": request.BrowserVersion,
				"userAgent": userAgent,
			},
			"os":        map[string]any{"name": "Windows", "version": "11", "architecture": "x86"},
			"locale":    map[string]any{"appLocale": "en-US", "acceptLanguage": "en-US,en;q=0.9", "timezone": "UTC"},
			"navigator": map[string]any{"platform": "Win32", "hardwareConcurrency": 8, "deviceMemory": 8, "maxTouchPoints": 0},
			"screen":    map[string]any{"width": 1920, "height": 1080, "devicePixelRatio": 1},
			"machine":   map[string]any{"computerName": deviceName},
			"graphics":  map[string]any{"webglVendor": "Apple", "webglRenderer": "Apple M2"},
			"generator": map[string]any{"baseDataSource": "local-template"},
		},
		"runtimeConfig": map[string]any{"launchArgs": []any{"--disable-background-mode", "--remote-debugging-port=0"}},
	}, nil
}

func testApplication(t *testing.T) application {
	t.Helper()
	directory := t.TempDir()
	keyFile := filepath.Join(directory, "api.key")
	if err := os.WriteFile(keyFile, []byte("test-key\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return application{keyFile: keyFile, runner: fakeRunner{}, logger: log.New(io.Discard, "", 0)}
}

func decodeResponse(t *testing.T, recorder *httptest.ResponseRecorder) apiResponse {
	t.Helper()
	var response apiResponse
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	return response
}

func TestHealthIsPublicAndIdentifiesGoService(t *testing.T) {
	recorder := httptest.NewRecorder()
	testApplication(t).ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/health", nil))
	if recorder.Code != http.StatusOK {
		t.Fatalf("status=%d", recorder.Code)
	}
	data := decodeResponse(t, recorder).Data.(map[string]any)
	if data["service"] != "automyai-fingerprint-api" || data["implementation"] != "go" {
		t.Fatalf("unexpected health: %#v", data)
	}
}

func TestWorkspaceRequiresPrivateKey(t *testing.T) {
	app := testApplication(t)
	wrong := httptest.NewRequest(http.MethodGet, "/browser/workspace", nil)
	wrong.Header.Set("token", "wrong")
	recorder := httptest.NewRecorder()
	app.ServeHTTP(recorder, wrong)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("wrong key status=%d", recorder.Code)
	}

	valid := httptest.NewRequest(http.MethodGet, "/browser/workspace", nil)
	valid.Header.Set("Authorization", "Bearer test-key")
	recorder = httptest.NewRecorder()
	app.ServeHTTP(recorder, valid)
	if recorder.Code != http.StatusOK {
		t.Fatalf("valid key status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestGenerateReturnsBundle(t *testing.T) {
	request := httptest.NewRequest(http.MethodPost, "/fingerprint/generate", strings.NewReader(`{"preset":"linux-firefox","seed":"fixed"}`))
	request.Header.Set("token", "test-key")
	recorder := httptest.NewRecorder()
	testApplication(t).ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	data := decodeResponse(t, recorder).Data.(map[string]any)
	profile := data["profile"].(map[string]any)
	if profile["preset"] != "linux-firefox" || profile["seed"] != "fixed" {
		t.Fatalf("unexpected profile: %#v", profile)
	}
}

func TestOAIGenerateReturnsGoNormalizedExecutionPlan(t *testing.T) {
	request := httptest.NewRequest(http.MethodPost, "/oai/fingerprint/generate", strings.NewReader(`{"entry":"openai3","seed":"fixed"}`))
	request.Header.Set("token", "test-key")
	recorder := httptest.NewRecorder()
	testApplication(t).ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	data := decodeResponse(t, recorder).Data.(map[string]any)
	if data["entry"] != "openai3" || data["source"] != "automyai-fingerprint-api" {
		t.Fatalf("unexpected identity: %#v", data)
	}
	if data["impersonate"] != "chrome" || data["device_id"] != "3d6c55e8-5ec9-5bce-87a6-7f4c1963395c" {
		t.Fatalf("unexpected runtime identity: %#v", data)
	}
	if data["preset"] != "windows-11-chrome" || data["platform"] != "Win32" {
		t.Fatalf("managed profile is not Windows Chrome: %#v", data)
	}
	headers := data["http_headers"].(map[string]any)
	if headers["User-Agent"] != data["user_agent"] || headers["Accept-Language"] != "en-US,en;q=0.9" {
		t.Fatalf("unexpected headers: %#v", headers)
	}
	commands := data["chromium_cdp_commands"].([]any)
	if len(commands) < 5 {
		t.Fatalf("missing CDP plan: %#v", commands)
	}
	provenance := data["provenance"].(map[string]any)
	if provenance["endpoint"] != "/oai/fingerprint/generate" {
		t.Fatalf("unexpected provenance: %#v", provenance)
	}
}

func TestOAIGenerateRejectsManagedLinuxOverride(t *testing.T) {
	request := httptest.NewRequest(http.MethodPost, "/oai/fingerprint/generate", strings.NewReader(`{"entry":"openai3","preset":"linux-firefox","browserVersion":"150.0.0.0"}`))
	request.Header.Set("token", "test-key")
	recorder := httptest.NewRecorder()
	testApplication(t).ServeHTTP(recorder, request)
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestOAIGenerateRejectsUnknownEntry(t *testing.T) {
	request := httptest.NewRequest(http.MethodPost, "/oai/fingerprint/generate", strings.NewReader(`{"entry":"unknown"}`))
	request.Header.Set("token", "test-key")
	recorder := httptest.NewRecorder()
	testApplication(t).ServeHTTP(recorder, request)
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestKeyPermissionsMustBePrivate(t *testing.T) {
	directory := t.TempDir()
	path := filepath.Join(directory, "api.key")
	if err := os.WriteFile(path, []byte("key"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := readPrivateKey(path); err == nil {
		t.Fatal("expected permissions error")
	}
}

func TestRoxyOpenAPIStatusUsesIndependentVendorKey(t *testing.T) {
	vendor := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/browser/workspace" || request.Header.Get("token") != "vendor-key" {
			t.Fatalf("unexpected vendor request: %s token=%q", request.URL.Path, request.Header.Get("token"))
		}
		_ = json.NewEncoder(writer).Encode(map[string]any{
			"code": 0, "msg": "success", "data": map[string]any{"rows": []any{}, "total": 0},
		})
	}))
	defer vendor.Close()
	directory := t.TempDir()
	vendorKey := filepath.Join(directory, "roxy.key")
	if err := os.WriteFile(vendorKey, []byte("vendor-key\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	configFile := filepath.Join(directory, "config.json")
	config := map[string]any{
		"ROXY_OPENAPI_ENABLED":  "true",
		"ROXY_OPENAPI_URL":      vendor.URL,
		"ROXY_OPENAPI_KEY_FILE": vendorKey,
	}
	payload, _ := json.Marshal(config)
	if err := os.WriteFile(configFile, payload, 0o600); err != nil {
		t.Fatal(err)
	}
	app := testApplication(t)
	app.configFile = configFile
	request := httptest.NewRequest(http.MethodGet, "/roxy/openapi/status", nil)
	request.Header.Set("token", "test-key")
	recorder := httptest.NewRecorder()
	app.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	data := decodeResponse(t, recorder).Data.(map[string]any)
	if data["authenticated"] != true || data["reachable"] != true {
		t.Fatalf("unexpected status: %#v", data)
	}
}
