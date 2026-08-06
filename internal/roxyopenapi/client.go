package roxyopenapi

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

const maxResponseBytes = 4 * 1024 * 1024

type Client struct {
	BaseURL string
	KeyFile string
	Timeout time.Duration
	HTTP    *http.Client
}

type Response struct {
	Code int             `json:"code"`
	Msg  string          `json:"msg"`
	Data json.RawMessage `json:"data"`
}

type RandomEnvRequest struct {
	DirIDs                   []string `json:"dirIds"`
	OSType                   string   `json:"osType,omitempty"`
	IsRandomUA               bool     `json:"isRandomUa"`
	IsRandomWebGL            bool     `json:"isRandomWebgl"`
	IsRandomWebGLInfo        bool     `json:"isRandomWebglInfo"`
	IsRandomResolution       bool     `json:"isRandomResolution"`
	IsRandomAudioContext     bool     `json:"isRandomAudioContext"`
	IsRandomMediaDevice      bool     `json:"isRandomMediaDevice"`
	IsRandomCanvas           bool     `json:"isRandomCanvas"`
	IsRandomHardware         bool     `json:"isRandomHardware"`
	IsRandomClientRects      bool     `json:"isRandomClientRects"`
	IsRandomSpeechVoices     bool     `json:"isRandomSpeechVoices"`
	IsRandomFont             bool     `json:"isRandomFont"`
	IsRandomDeviceName       bool     `json:"isRandomDeviceName"`
	IsRandomDeviceMACAddress bool     `json:"isRandomDeviceMacAddress"`
}

func ReadPrivateKey(path string) (string, error) {
	info, err := os.Stat(path)
	if err != nil || info.IsDir() {
		return "", errors.New("Roxy OpenAPI key is not configured")
	}
	if info.Mode().Perm()&0o077 != 0 {
		return "", errors.New("Roxy OpenAPI key file permissions must be private")
	}
	value, err := os.ReadFile(path)
	if err != nil {
		return "", errors.New("Roxy OpenAPI key could not be read")
	}
	key := strings.TrimSpace(string(value))
	if key == "" {
		return "", errors.New("Roxy OpenAPI key is empty")
	}
	return key, nil
}

func (c Client) normalizedBaseURL() (string, error) {
	parsed, err := url.Parse(strings.TrimSpace(c.BaseURL))
	if err != nil || parsed.Scheme == "" || parsed.Hostname() == "" {
		return "", errors.New("Roxy OpenAPI URL is invalid")
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return "", errors.New("Roxy OpenAPI URL must use HTTP or HTTPS")
	}
	host := parsed.Hostname()
	if host != "localhost" {
		ip := net.ParseIP(host)
		if ip == nil || !ip.IsLoopback() {
			return "", errors.New("Roxy OpenAPI URL must use a loopback host")
		}
	}
	parsed.RawQuery = ""
	parsed.Fragment = ""
	return strings.TrimRight(parsed.String(), "/"), nil
}

func (c Client) httpClient() *http.Client {
	if c.HTTP != nil {
		return c.HTTP
	}
	timeout := c.Timeout
	if timeout <= 0 {
		timeout = 10 * time.Second
	}
	return &http.Client{Timeout: timeout}
}

func (c Client) request(ctx context.Context, method, path string, query url.Values, payload any) (any, error) {
	baseURL, err := c.normalizedBaseURL()
	if err != nil {
		return nil, err
	}
	key, err := ReadPrivateKey(c.KeyFile)
	if err != nil {
		return nil, err
	}
	target, err := url.Parse(baseURL + path)
	if err != nil {
		return nil, errors.New("Roxy OpenAPI request URL is invalid")
	}
	target.RawQuery = query.Encode()
	var body io.Reader
	if payload != nil {
		encoded, encodeErr := json.Marshal(payload)
		if encodeErr != nil {
			return nil, fmt.Errorf("encode Roxy OpenAPI request: %w", encodeErr)
		}
		body = bytes.NewReader(encoded)
	}
	request, err := http.NewRequestWithContext(ctx, method, target.String(), body)
	if err != nil {
		return nil, errors.New("build Roxy OpenAPI request failed")
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("token", key)
	if payload != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	response, err := c.httpClient().Do(request)
	if err != nil {
		return nil, errors.New("Roxy OpenAPI is unavailable")
	}
	defer response.Body.Close()
	limited := io.LimitReader(response.Body, maxResponseBytes)
	var envelope Response
	if err := json.NewDecoder(limited).Decode(&envelope); err != nil {
		return nil, errors.New("Roxy OpenAPI returned invalid JSON")
	}
	if response.StatusCode == http.StatusUnauthorized || response.StatusCode == http.StatusForbidden {
		return nil, errors.New("Roxy OpenAPI key was rejected")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 || envelope.Code != 0 {
		message := strings.TrimSpace(envelope.Msg)
		if message == "" {
			message = fmt.Sprintf("HTTP %d", response.StatusCode)
		}
		return nil, fmt.Errorf("Roxy OpenAPI request failed: %s", message)
	}
	if len(envelope.Data) == 0 || string(envelope.Data) == "null" {
		return map[string]any{}, nil
	}
	var data any
	if err := json.Unmarshal(envelope.Data, &data); err != nil {
		return nil, errors.New("Roxy OpenAPI returned invalid data")
	}
	return data, nil
}

func (c Client) Workspace(ctx context.Context) (any, error) {
	return c.request(ctx, http.MethodGet, "/browser/workspace", nil, nil)
}

func (c Client) BrowserDetail(ctx context.Context, dirID string) (any, error) {
	dirID = strings.TrimSpace(dirID)
	if dirID == "" {
		return nil, errors.New("dirId is required")
	}
	return c.request(ctx, http.MethodGet, "/browser/detail", url.Values{"dirId": {dirID}}, nil)
}

func (c Client) RandomEnv(ctx context.Context, payload RandomEnvRequest) (any, error) {
	if len(payload.DirIDs) == 0 {
		return nil, errors.New("dirIds is required")
	}
	if len(payload.DirIDs) > 1000 {
		return nil, errors.New("dirIds exceeds the Roxy OpenAPI limit")
	}
	for _, dirID := range payload.DirIDs {
		if strings.TrimSpace(dirID) == "" {
			return nil, errors.New("dirIds contains an empty value")
		}
	}
	return c.request(ctx, http.MethodPost, "/browser/random_env", nil, payload)
}
