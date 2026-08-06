package roxyopenapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func testKeyFile(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "api.key")
	if err := os.WriteFile(path, []byte("roxy-test-key\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestWorkspaceUsesTokenHeader(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/browser/workspace" || request.Header.Get("token") != "roxy-test-key" {
			t.Fatalf("unexpected request: %s token=%q", request.URL.Path, request.Header.Get("token"))
		}
		_ = json.NewEncoder(writer).Encode(map[string]any{"code": 0, "msg": "success", "data": map[string]any{"rows": []any{}}})
	}))
	defer server.Close()
	client := Client{BaseURL: server.URL, KeyFile: testKeyFile(t), Timeout: time.Second}
	if _, err := client.Workspace(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func TestRandomEnvDoesNotStartBrowser(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/browser/random_env" || request.Method != http.MethodPost {
			t.Fatalf("unexpected request: %s %s", request.Method, request.URL.Path)
		}
		var payload RandomEnvRequest
		if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		if len(payload.DirIDs) != 1 || !payload.IsRandomUA || !payload.IsRandomWebGL {
			t.Fatalf("unexpected payload: %#v", payload)
		}
		_ = json.NewEncoder(writer).Encode(map[string]any{"code": 0, "msg": "success", "data": map[string]any{"success": true}})
	}))
	defer server.Close()
	client := Client{BaseURL: server.URL, KeyFile: testKeyFile(t), Timeout: time.Second}
	_, err := client.RandomEnv(context.Background(), RandomEnvRequest{
		DirIDs: []string{"window-1"}, IsRandomUA: true, IsRandomWebGL: true,
	})
	if err != nil {
		t.Fatal(err)
	}
}

func TestPrivateKeyPermissions(t *testing.T) {
	path := filepath.Join(t.TempDir(), "api.key")
	if err := os.WriteFile(path, []byte("key"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadPrivateKey(path); err == nil {
		t.Fatal("expected permissions error")
	}
}
