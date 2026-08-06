package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func writeRuntimeConfig(t *testing.T, values map[string]any) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.json")
	payload, err := json.Marshal(values)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, payload, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestRuntimeSettingsCloudSwitch(t *testing.T) {
	path := writeRuntimeConfig(t, map[string]any{
		"OAI_FINGERPRINT_CLOUD_ENABLED":      "true",
		"OAI_FINGERPRINT_CLOUD_API_BASE_URL": "https://fingerprint.example.test/api",
		"OAI_FINGERPRINT_CLOUD_HEADERS_FILE": "/run/secrets/cloud-headers.json",
		"OAI_FINGERPRINT_CLOUD_INCLUDE_MAC":  "false",
	})
	settings, err := loadRuntimeSettings(path)
	if err != nil {
		t.Fatal(err)
	}
	if !settings.CloudEnabled || !settings.CloudOmitMAC || settings.CloudBaseURL == "" || settings.CloudHeadersFile == "" {
		t.Fatalf("unexpected cloud settings: %#v", settings)
	}
}

func TestRuntimeSettingsRoxyOpenAPIIsIndependent(t *testing.T) {
	path := writeRuntimeConfig(t, map[string]any{
		"OAI_FINGERPRINT_CLOUD_ENABLED": "false",
		"ROXY_OPENAPI_ENABLED":          "true",
		"ROXY_OPENAPI_URL":              "http://127.0.0.1:50000",
		"ROXY_OPENAPI_KEY_FILE":         "/run/secrets/roxy-openapi.key",
	})
	settings, err := loadRuntimeSettings(path)
	if err != nil {
		t.Fatal(err)
	}
	if settings.CloudEnabled || !settings.RoxyEnabled || settings.RoxyBaseURL != "http://127.0.0.1:50000" {
		t.Fatalf("unexpected settings: %#v", settings)
	}
}
