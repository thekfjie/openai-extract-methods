package main

import (
	"context"
	"crypto/hmac"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"automyai/internal/fingerprintmodel"
	"automyai/internal/fingerprintpolicy"
	"automyai/internal/fingerprintsdk"
	"automyai/internal/roxyopenapi"
)

const (
	maxRequestBytes = 256 * 1024
)

type apiResponse struct {
	Code int    `json:"code"`
	Msg  string `json:"msg"`
	Data any    `json:"data"`
}

type generateRequest = fingerprintsdk.Request

type sdkRunner interface {
	Available() bool
	Presets(context.Context) ([]string, error)
	Generate(context.Context, generateRequest) (any, error)
}

type nodeSDKRunner = fingerprintsdk.Runner

type application struct {
	keyFile    string
	configFile string
	runner     sdkRunner
	logger     *log.Logger
}

func (a application) reply(writer http.ResponseWriter, status int, payload apiResponse) {
	writer.Header().Set("Content-Type", "application/json; charset=utf-8")
	writer.Header().Set("Cache-Control", "no-store")
	writer.WriteHeader(status)
	if err := json.NewEncoder(writer).Encode(payload); err != nil {
		a.logger.Printf("encode response: %v", err)
	}
}

func (a application) success(writer http.ResponseWriter, data any) {
	a.reply(writer, http.StatusOK, apiResponse{Code: 0, Msg: "success", Data: data})
}

func (a application) fail(writer http.ResponseWriter, status int, message string) {
	a.reply(writer, status, apiResponse{Code: status, Msg: message, Data: nil})
}

func readPrivateKey(path string) (string, error) {
	info, err := os.Stat(path)
	if err != nil || info.IsDir() {
		return "", errors.New("API key is not configured")
	}
	if info.Mode().Perm()&0o077 != 0 {
		return "", errors.New("API key file permissions must be private")
	}
	value, err := os.ReadFile(path)
	if err != nil {
		return "", errors.New("API key could not be read")
	}
	key := strings.TrimSpace(string(value))
	if key == "" {
		return "", errors.New("API key is empty")
	}
	return key, nil
}

func (a application) authorized(request *http.Request) error {
	expected, err := readPrivateKey(a.keyFile)
	if err != nil {
		return err
	}
	supplied := strings.TrimSpace(request.Header.Get("token"))
	if supplied == "" {
		authorization := strings.TrimSpace(request.Header.Get("Authorization"))
		if len(authorization) > 7 && strings.EqualFold(authorization[:7], "Bearer ") {
			supplied = strings.TrimSpace(authorization[7:])
		}
	}
	if supplied == "" || !hmac.Equal([]byte(supplied), []byte(expected)) {
		return errors.New("invalid token")
	}
	return nil
}

func (a application) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	a.logger.Printf("%s %s %s", request.RemoteAddr, request.Method, request.URL.Path)
	if request.Method == http.MethodGet && request.URL.Path == "/health" {
		source := map[string]any{"mode": "local-template", "cloudEnabled": false, "cloudConfigured": false}
		if reporter, ok := a.runner.(interface{ SourceStatus() map[string]any }); ok {
			source = reporter.SourceStatus()
		}
		a.success(writer, map[string]any{
			"service":        "automyai-fingerprint-api",
			"implementation": "go",
			"mode":           "fingerprint-control",
			"sdkAvailable":   a.runner.Available(),
			"source":         source,
		})
		return
	}
	if err := a.authorized(request); err != nil {
		if err.Error() == "invalid token" {
			a.fail(writer, http.StatusUnauthorized, err.Error())
		} else {
			a.fail(writer, http.StatusServiceUnavailable, err.Error())
		}
		return
	}
	switch {
	case request.Method == http.MethodGet && request.URL.Path == "/browser/workspace":
		a.success(writer, map[string]any{
			"rows": []map[string]string{{
				"id": "automyai-local-fingerprint", "name": "AutoMyAI Local Fingerprint", "provider": "local-api",
			}},
			"total": 1,
		})
	case request.Method == http.MethodGet && request.URL.Path == "/fingerprint/presets":
		ctx, cancel := context.WithTimeout(request.Context(), 10*time.Second)
		defer cancel()
		presets, err := a.runner.Presets(ctx)
		if err != nil {
			a.fail(writer, http.StatusInternalServerError, "failed to list presets")
			return
		}
		a.success(writer, presets)
	case request.Method == http.MethodGet && request.URL.Path == "/roxy/openapi/status":
		settings, err := loadRuntimeSettings(a.configFile)
		if err != nil {
			a.fail(writer, http.StatusServiceUnavailable, err.Error())
			return
		}
		status := map[string]any{
			"enabled": settings.RoxyEnabled, "configured": settings.RoxyBaseURL != "" && settings.RoxyKeyFile != "",
			"url": settings.RoxyBaseURL, "role": "local-browser-environment-management",
		}
		if !settings.RoxyEnabled {
			status["reachable"] = false
			status["authenticated"] = false
			a.success(writer, status)
			return
		}
		ctx, cancel := context.WithTimeout(request.Context(), settings.RoxyTimeout)
		defer cancel()
		workspace, err := roxyClient(settings).Workspace(ctx)
		if err != nil {
			status["reachable"] = false
			status["authenticated"] = false
			status["error"] = err.Error()
			a.success(writer, status)
			return
		}
		status["reachable"] = true
		status["authenticated"] = true
		status["workspace"] = workspace
		a.success(writer, status)
	case request.Method == http.MethodGet && request.URL.Path == "/roxy/openapi/workspace":
		settings, err := loadRuntimeSettings(a.configFile)
		if err != nil || !settings.RoxyEnabled {
			a.fail(writer, http.StatusServiceUnavailable, "Roxy OpenAPI is disabled")
			return
		}
		ctx, cancel := context.WithTimeout(request.Context(), settings.RoxyTimeout)
		defer cancel()
		result, err := roxyClient(settings).Workspace(ctx)
		if err != nil {
			a.fail(writer, http.StatusBadGateway, err.Error())
			return
		}
		a.success(writer, result)
	case request.Method == http.MethodGet && request.URL.Path == "/roxy/openapi/browser/detail":
		settings, err := loadRuntimeSettings(a.configFile)
		if err != nil || !settings.RoxyEnabled {
			a.fail(writer, http.StatusServiceUnavailable, "Roxy OpenAPI is disabled")
			return
		}
		ctx, cancel := context.WithTimeout(request.Context(), settings.RoxyTimeout)
		defer cancel()
		result, err := roxyClient(settings).BrowserDetail(ctx, request.URL.Query().Get("dirId"))
		if err != nil {
			a.fail(writer, http.StatusBadGateway, err.Error())
			return
		}
		a.success(writer, result)
	case request.Method == http.MethodPost && request.URL.Path == "/roxy/openapi/random-env":
		settings, err := loadRuntimeSettings(a.configFile)
		if err != nil || !settings.RoxyEnabled {
			a.fail(writer, http.StatusServiceUnavailable, "Roxy OpenAPI is disabled")
			return
		}
		body := http.MaxBytesReader(writer, request.Body, maxRequestBytes)
		defer body.Close()
		var payload roxyopenapi.RandomEnvRequest
		if err := json.NewDecoder(body).Decode(&payload); err != nil {
			a.fail(writer, http.StatusBadRequest, "request body must be a JSON object")
			return
		}
		ctx, cancel := context.WithTimeout(request.Context(), settings.RoxyTimeout)
		defer cancel()
		result, err := roxyClient(settings).RandomEnv(ctx, payload)
		if err != nil {
			a.fail(writer, http.StatusBadGateway, err.Error())
			return
		}
		a.success(writer, result)
	case request.Method == http.MethodPost && request.URL.Path == "/fingerprint/generate":
		body := http.MaxBytesReader(writer, request.Body, maxRequestBytes)
		defer body.Close()
		var payload generateRequest
		decoder := json.NewDecoder(body)
		if err := decoder.Decode(&payload); err != nil && !errors.Is(err, io.EOF) {
			a.fail(writer, http.StatusBadRequest, "request body must be a JSON object")
			return
		}
		ctx, cancel := context.WithTimeout(request.Context(), 30*time.Second)
		defer cancel()
		result, err := a.runner.Generate(ctx, payload)
		if err != nil {
			a.fail(writer, http.StatusBadRequest, err.Error())
			return
		}
		a.success(writer, result)
	case request.Method == http.MethodPost && request.URL.Path == "/oai/fingerprint/generate":
		body := http.MaxBytesReader(writer, request.Body, maxRequestBytes)
		defer body.Close()
		var payload oaiGenerateRequest
		decoder := json.NewDecoder(body)
		if err := decoder.Decode(&payload); err != nil {
			a.fail(writer, http.StatusBadRequest, "request body must be a JSON object")
			return
		}
		spec, ok := oaiEntrySpecs[payload.Entry]
		if !ok {
			a.fail(writer, http.StatusBadRequest, "unknown OAI fingerprint entry")
			return
		}
		preset := payload.Preset
		version := payload.BrowserVersion
		if version == "" {
			version = payload.BrowserVersionAlt
		}
		seed := payload.Seed
		source := strings.TrimSpace(payload.Source)
		if source == "" {
			source = "local"
		}
		if source != "local" && source != "cloud" {
			a.fail(writer, http.StatusBadRequest, "fingerprint source must be local or cloud")
			return
		}
		if spec.Managed {
			if preset != "" && preset != fingerprintpolicy.OpenAI3Preset {
				a.fail(writer, http.StatusBadRequest, "managed OpenAI fingerprint preset cannot be overridden")
				return
			}
			if version != "" && version != fingerprintpolicy.ChromeBrowserVersion {
				a.fail(writer, http.StatusBadRequest, "managed OpenAI fingerprint version must be Chrome 150.0.0.0")
				return
			}
			if seed == "" {
				generatedSeed, seedErr := fingerprintpolicy.NewSeed()
				if seedErr != nil {
					a.fail(writer, http.StatusInternalServerError, seedErr.Error())
					return
				}
				seed = generatedSeed
			}
			ctx, cancel := context.WithTimeout(request.Context(), 10*time.Second)
			available, err := a.runner.Presets(ctx)
			cancel()
			if err != nil {
				a.fail(writer, http.StatusServiceUnavailable, "fingerprint presets are unavailable")
				return
			}
			resolvedPreset, resolveErr := fingerprintpolicy.ResolveOpenAI3Preset(seed, 0, available)
			if resolveErr != nil {
				a.fail(writer, http.StatusBadRequest, resolveErr.Error())
				return
			}
			preset = resolvedPreset
			version = fingerprintpolicy.ChromeBrowserVersion
		} else {
			if preset == "" {
				preset = spec.Preset
			}
			if version == "" {
				version = spec.BrowserVersion
			}
		}
		ctx, cancel := context.WithTimeout(request.Context(), 30*time.Second)
		defer cancel()
		bundle, err := a.runner.Generate(ctx, generateRequest{
			Preset: preset, Seed: seed, Count: 1, BrowserVersion: version, Source: source,
		})
		if err != nil {
			a.fail(writer, http.StatusBadRequest, err.Error())
			return
		}
		if spec.Managed {
			decoded, err := fingerprintmodel.DecodeBundle(bundle)
			if err != nil {
				a.fail(writer, http.StatusBadRequest, err.Error())
				return
			}
			if err := fingerprintpolicy.ValidateOpenAI3Bundle(decoded); err != nil {
				a.fail(writer, http.StatusBadRequest, err.Error())
				return
			}
		}
		result, err := normalizeOAIBundle(bundle, payload.Entry)
		if err != nil {
			a.fail(writer, http.StatusBadRequest, err.Error())
			return
		}
		if spec.Managed {
			result["policy"] = map[string]any{
				"browser": "Chrome", "browserVersion": fingerprintpolicy.ChromeBrowserVersion,
				"linuxAllowed": false, "operatingSystems": []string{"Windows"},
			}
		}
		a.success(writer, result)
	default:
		a.fail(writer, http.StatusNotFound, "not found")
	}
}

func findSDKDir(configured string) (string, error) {
	return fingerprintsdk.FindSDKDir(configured)
}

func loopbackHost(host string) bool {
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func main() {
	host := flag.String("host", envOr("FINGERPRINT_API_HOST", "127.0.0.1"), "loopback listen host")
	port := flag.Int("port", envInt("FINGERPRINT_API_PORT", 0), "listen port from config/ports.env")
	keyFile := flag.String("key-file", envOr("FINGERPRINT_API_KEY_FILE", "/app/data/fingerprint-api/api.key"), "private API key file")
	configFile := flag.String("config", envOr("AUTOMYAI_CONFIG", "/app/config.json"), "runtime configuration file")
	sdkFlag := flag.String("sdk-dir", envOr("OAI_FINGERPRINT_SDK_DIR", ""), "fingerprint SDK directory")
	nodeFlag := flag.String("node", envOr("OAI_FINGERPRINT_NODE", "node"), "Node.js executable")
	flag.Parse()
	if !loopbackHost(*host) {
		log.Fatal("host must be loopback")
	}
	if *port < 1 || *port > 65535 {
		log.Fatal("port must be between 1 and 65535")
	}
	sdkDir, err := findSDKDir(*sdkFlag)
	if err != nil {
		log.Fatal(err)
	}
	node, err := exec.LookPath(*nodeFlag)
	if err != nil {
		log.Fatal("Node.js is not available")
	}
	logger := log.New(os.Stdout, "[fingerprint-api] ", log.LstdFlags)
	runner := configuredRunner{
		base: nodeSDKRunner{Node: node, SDKDir: sdkDir}, configFile: *configFile,
	}
	server := &http.Server{
		Addr:              net.JoinHostPort(*host, strconv.Itoa(*port)),
		Handler:           application{keyFile: *keyFile, configFile: *configFile, runner: runner, logger: logger},
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       30 * time.Second,
	}
	go func() {
		logger.Printf("Go service listening on %s", server.Addr)
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Fatalf("listen: %v", err)
		}
	}()
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := server.Shutdown(ctx); err != nil {
		logger.Printf("shutdown: %v", err)
	}
}

func envOr(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func envInt(name string, fallback int) int {
	value, err := strconv.Atoi(strings.TrimSpace(os.Getenv(name)))
	if err == nil && value > 0 {
		return value
	}
	return fallback
}
