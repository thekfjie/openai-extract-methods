package fingerprintconfig

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func writeConfig(t *testing.T, values map[string]any) string {
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

func TestLoadResolvesSharedRelativeSecretFiles(t *testing.T) {
	path := writeConfig(t, map[string]any{
		"OAI_FINGERPRINT_CLOUD_ENABLED":      "true",
		"OAI_FINGERPRINT_CLOUD_API_BASE_URL": "https://fingerprint.example.test/api",
		"OAI_FINGERPRINT_CLOUD_HEADERS_FILE": "./data/cloud/headers.json",
		"ROXY_OPENAPI_KEY_FILE":              "./data/roxy/api.key",
	})
	settings, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	root := filepath.Dir(path)
	if settings.CloudHeadersFile != filepath.Join(root, "data/cloud/headers.json") {
		t.Fatalf("cloud path=%q", settings.CloudHeadersFile)
	}
	if settings.RoxyKeyFile != filepath.Join(root, "data/roxy/api.key") {
		t.Fatalf("roxy path=%q", settings.RoxyKeyFile)
	}
}
