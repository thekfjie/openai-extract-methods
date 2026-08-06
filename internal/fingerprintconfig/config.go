package fingerprintconfig

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type Settings struct {
	CloudEnabled     bool
	CloudBaseURL     string
	CloudHeadersFile string
	CloudOmitMAC     bool
	RoxyEnabled      bool
	RoxyBaseURL      string
	RoxyKeyFile      string
	RoxyTimeout      time.Duration
}

func settingString(values map[string]any, name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	if value, ok := values[name]; ok {
		if text := strings.TrimSpace(fmt.Sprint(value)); text != "" {
			return text
		}
	}
	return fallback
}

func settingBool(values map[string]any, name string, fallback bool) bool {
	value := settingString(values, name, "")
	if value == "" {
		return fallback
	}
	switch strings.ToLower(value) {
	case "1", "true", "yes", "on":
		return true
	case "0", "false", "no", "off":
		return false
	default:
		return fallback
	}
}

func resolveFile(configPath, value string) string {
	value = strings.TrimSpace(value)
	if value == "" || filepath.IsAbs(value) {
		return value
	}
	base := "."
	if strings.TrimSpace(configPath) != "" {
		base = filepath.Dir(configPath)
	}
	resolved, err := filepath.Abs(filepath.Join(base, value))
	if err != nil {
		return filepath.Join(base, value)
	}
	return resolved
}

func Load(path string) (Settings, error) {
	values := map[string]any{}
	if strings.TrimSpace(path) != "" {
		payload, err := os.ReadFile(path)
		if err != nil {
			return Settings{}, errors.New("fingerprint runtime config could not be read")
		}
		if err := json.Unmarshal(payload, &values); err != nil {
			return Settings{}, errors.New("fingerprint runtime config is invalid")
		}
	}
	cloudBaseURL := settingString(values, "OAI_FINGERPRINT_CLOUD_API_BASE_URL", "")
	if cloudBaseURL == "" {
		cloudBaseURL = settingString(values, "OAI_FINGERPRINT_AUTHORIZED_API_BASE_URL", "")
	}
	cloudHeadersFile := settingString(values, "OAI_FINGERPRINT_CLOUD_HEADERS_FILE", "")
	if cloudHeadersFile == "" {
		cloudHeadersFile = settingString(values, "OAI_FINGERPRINT_AUTHORIZED_HEADERS_FILE", "")
	}
	timeoutSeconds := 10
	if value, err := strconv.Atoi(settingString(values, "ROXY_OPENAPI_TIMEOUT_SECONDS", "10")); err == nil && value >= 1 && value <= 120 {
		timeoutSeconds = value
	}
	return Settings{
		CloudEnabled:     settingBool(values, "OAI_FINGERPRINT_CLOUD_ENABLED", false),
		CloudBaseURL:     cloudBaseURL,
		CloudHeadersFile: resolveFile(path, cloudHeadersFile),
		CloudOmitMAC:     !settingBool(values, "OAI_FINGERPRINT_CLOUD_INCLUDE_MAC", true),
		RoxyEnabled:      settingBool(values, "ROXY_OPENAPI_ENABLED", false),
		RoxyBaseURL:      settingString(values, "ROXY_OPENAPI_URL", "http://127.0.0.1:50000"),
		RoxyKeyFile:      resolveFile(path, settingString(values, "ROXY_OPENAPI_KEY_FILE", "./data/roxy-openapi/api.key")),
		RoxyTimeout:      time.Duration(timeoutSeconds) * time.Second,
	}, nil
}
