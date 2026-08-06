package extractmethods

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"strings"
	"sync"
	"time"

	fhttp "github.com/bogdanfinn/fhttp"
	tlsclient "github.com/bogdanfinn/tls-client"
	"github.com/bogdanfinn/tls-client/profiles"
)

const maxUpstreamResponseBytes = 4 * 1024 * 1024

type HTTPResponse struct {
	Status  int
	Headers map[string]string
	Body    []byte
	URL     string
}

func (r HTTPResponse) JSON(destination any) error {
	if len(r.Body) == 0 {
		return errors.New("empty response")
	}
	decoder := json.NewDecoder(bytes.NewReader(r.Body))
	decoder.UseNumber()
	return decoder.Decode(destination)
}

func (r HTTPResponse) Preview(limit int) string {
	return trimDetail(string(r.Body), limit)
}

type BrowserClient struct {
	client  tlsclient.HttpClient
	headers map[string]string
	mu      sync.Mutex
}

func NewBrowserClient(proxy string, timeout time.Duration, fingerprint string) (*BrowserClient, error) {
	if strings.TrimSpace(proxy) == "" {
		return nil, errors.New("拒绝直连：BrowserClient 必须配置代理")
	}
	seconds := int(timeout.Seconds())
	if seconds < 5 {
		seconds = 45
	}
	profile := profiles.Chrome_146
	normalizedFingerprint := strings.ToLower(strings.TrimSpace(fingerprint))
	if strings.Contains(normalizedFingerprint, "firefox") {
		profile = profiles.Firefox_147
	} else if strings.Contains(normalizedFingerprint, "chrome136") {
		// tls-client v1.15.1 does not export Chrome 136. Chrome 133 is the
		// nearest available pre-144 profile for reproducing the older curl_cffi
		// transport family while retaining the reference Chrome 147 UA.
		profile = profiles.Chrome_133
	}
	client, err := tlsclient.NewHttpClient(
		tlsclient.NewNoopLogger(),
		tlsclient.WithTimeoutSeconds(seconds),
		tlsclient.WithClientProfile(profile),
		tlsclient.WithRandomTLSExtensionOrder(),
		tlsclient.WithCookieJar(tlsclient.NewCookieJar()),
		tlsclient.WithProxyUrl(proxy),
	)
	if err != nil {
		return nil, fmt.Errorf("创建 TLS 客户端: %w", err)
	}
	return &BrowserClient{client: client, headers: map[string]string{}}, nil
}

func (c *BrowserClient) Close() {
	c.client.CloseIdleConnections()
}

func (c *BrowserClient) CopyCookiesFrom(source *BrowserClient, rawURL string) error {
	if c == nil || source == nil || c == source {
		return nil
	}
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return err
	}
	source.mu.Lock()
	cookies := source.client.GetCookies(parsed)
	source.mu.Unlock()
	if len(cookies) == 0 {
		return nil
	}
	c.mu.Lock()
	c.client.SetCookies(parsed, cookies)
	c.mu.Unlock()
	return nil
}

func (c *BrowserClient) SetDefaultHeaders(headers map[string]string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	for key, value := range headers {
		c.headers[key] = value
	}
}

func (c *BrowserClient) Do(ctx context.Context, method, rawURL string, headers map[string]string, body []byte, followRedirects bool) (HTTPResponse, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	request, err := fhttp.NewRequestWithContext(ctx, method, rawURL, bytes.NewReader(body))
	if err != nil {
		return HTTPResponse{}, err
	}
	for key, value := range c.headers {
		request.Header.Set(key, value)
	}
	for key, value := range headers {
		if value == "" {
			request.Header.Del(key)
		} else {
			request.Header.Set(key, value)
		}
	}
	c.client.SetFollowRedirect(followRedirects)
	response, err := c.client.Do(request)
	if err != nil {
		return HTTPResponse{}, err
	}
	defer response.Body.Close()
	content, err := io.ReadAll(io.LimitReader(response.Body, maxUpstreamResponseBytes+1))
	if err != nil {
		return HTTPResponse{}, err
	}
	if len(content) > maxUpstreamResponseBytes {
		return HTTPResponse{}, errors.New("上游响应超过 4 MiB")
	}
	responseHeaders := map[string]string{}
	for key, values := range response.Header {
		if len(values) > 0 {
			responseHeaders[key] = values[0]
		}
	}
	responseURL := rawURL
	if response.Request != nil && response.Request.URL != nil {
		responseURL = response.Request.URL.String()
	}
	return HTTPResponse{Status: response.StatusCode, Headers: responseHeaders, Body: content, URL: responseURL}, nil
}

func (c *BrowserClient) JSON(ctx context.Context, method, rawURL string, headers map[string]string, payload any) (HTTPResponse, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return HTTPResponse{}, err
	}
	merged := cloneHeaders(headers)
	merged["Content-Type"] = "application/json"
	return c.Do(ctx, method, rawURL, merged, body, true)
}

func (c *BrowserClient) Form(ctx context.Context, method, rawURL string, headers map[string]string, form url.Values) (HTTPResponse, error) {
	merged := cloneHeaders(headers)
	merged["Content-Type"] = "application/x-www-form-urlencoded"
	return c.Do(ctx, method, rawURL, merged, []byte(form.Encode()), true)
}

func cloneHeaders(headers map[string]string) map[string]string {
	result := make(map[string]string, len(headers)+1)
	for key, value := range headers {
		result[key] = value
	}
	return result
}
