package controlapi

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"automyai/internal/fingerprintpolicy"
)

type Config struct {
	DefaultPreset        string   `json:"defaultPreset"`
	FingerprintSource    string   `json:"fingerprintSource"`
	BrowserVersion       string   `json:"browserVersion,omitempty"`
	CompatibilityPresets []string `json:"compatibilityPresets"`
	SamplesPerPreset     int      `json:"samplesPerPreset"`
	LogLevel             string   `json:"logLevel"`
}

func DefaultConfig() Config {
	return Config{
		DefaultPreset:        fingerprintpolicy.OpenAI3Preset,
		FingerprintSource:    "local",
		BrowserVersion:       fingerprintpolicy.ChromeBrowserVersion,
		CompatibilityPresets: fingerprintpolicy.OpenAI3Presets(),
		SamplesPerPreset:     1,
		LogLevel:             "info",
	}
}

func enforceOpenAI3Policy(config Config) Config {
	config.DefaultPreset = fingerprintpolicy.OpenAI3Preset
	config.BrowserVersion = fingerprintpolicy.ChromeBrowserVersion
	config.CompatibilityPresets = fingerprintpolicy.OpenAI3Presets()
	return config
}

func (c Config) Validate(available []string) error {
	if c.FingerprintSource != "local" && c.FingerprintSource != "cloud" {
		return errors.New("fingerprintSource must be local or cloud")
	}
	allowed := make(map[string]bool, len(available))
	for _, preset := range available {
		allowed[preset] = true
	}
	if err := fingerprintpolicy.ValidateAvailable(available); err != nil {
		return err
	}
	if c.DefaultPreset != fingerprintpolicy.OpenAI3Preset {
		return fmt.Errorf("defaultPreset must be %s", fingerprintpolicy.OpenAI3Preset)
	}
	if c.BrowserVersion != fingerprintpolicy.ChromeBrowserVersion {
		return fmt.Errorf("browserVersion must be %s", fingerprintpolicy.ChromeBrowserVersion)
	}
	if len(c.CompatibilityPresets) < 1 || len(c.CompatibilityPresets) > 12 {
		return errors.New("compatibilityPresets must contain 1 to 12 presets")
	}
	seen := map[string]bool{}
	for _, preset := range c.CompatibilityPresets {
		if !allowed[preset] {
			return fmt.Errorf("unknown compatibility preset: %s", preset)
		}
		if seen[preset] {
			return fmt.Errorf("duplicate compatibility preset: %s", preset)
		}
		seen[preset] = true
	}
	for _, preset := range fingerprintpolicy.OpenAI3Presets() {
		if !seen[preset] {
			return fmt.Errorf("compatibilityPresets must include %s", preset)
		}
	}
	if c.SamplesPerPreset < 1 || c.SamplesPerPreset > 5 {
		return errors.New("samplesPerPreset must be between 1 and 5")
	}
	if c.LogLevel != "debug" && c.LogLevel != "info" && c.LogLevel != "warn" && c.LogLevel != "error" {
		return errors.New("logLevel must be debug, info, warn, or error")
	}
	return nil
}

type ConfigStore struct {
	mu   sync.RWMutex
	path string
	data Config
}

func OpenConfig(path string, available []string) (*ConfigStore, error) {
	store := &ConfigStore{path: path, data: DefaultConfig()}
	encoded, err := os.ReadFile(path)
	if err == nil {
		if err := json.Unmarshal(encoded, &store.data); err != nil {
			return nil, fmt.Errorf("decode control config: %w", err)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, fmt.Errorf("read control config: %w", err)
	}
	store.data = enforceOpenAI3Policy(store.data)
	if err := store.data.Validate(available); err != nil {
		return nil, fmt.Errorf("validate control config: %w", err)
	}
	if err := store.saveLocked(); err != nil {
		return nil, err
	}
	return store, nil
}

func (s *ConfigStore) Get() Config {
	s.mu.RLock()
	defer s.mu.RUnlock()
	result := s.data
	result.CompatibilityPresets = append([]string(nil), s.data.CompatibilityPresets...)
	return result
}

func (s *ConfigStore) Set(value Config, available []string) (Config, error) {
	value = enforceOpenAI3Policy(value)
	if err := value.Validate(available); err != nil {
		return Config{}, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	previous := s.data
	s.data = value
	if err := s.saveLocked(); err != nil {
		s.data = previous
		return Config{}, err
	}
	return s.GetUnlocked(), nil
}

func (s *ConfigStore) GetUnlocked() Config {
	result := s.data
	result.CompatibilityPresets = append([]string(nil), s.data.CompatibilityPresets...)
	return result
}

func (s *ConfigStore) saveLocked() error {
	directory := filepath.Dir(s.path)
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return fmt.Errorf("create control config directory: %w", err)
	}
	if err := os.Chmod(directory, 0o700); err != nil {
		return fmt.Errorf("protect control config directory: %w", err)
	}
	encoded, err := json.MarshalIndent(s.data, "", "  ")
	if err != nil {
		return fmt.Errorf("encode control config: %w", err)
	}
	temporary := s.path + ".tmp"
	if err := os.WriteFile(temporary, append(encoded, '\n'), 0o600); err != nil {
		return fmt.Errorf("write control config: %w", err)
	}
	if err := os.Chmod(temporary, 0o600); err != nil {
		return fmt.Errorf("protect control config: %w", err)
	}
	if err := os.Rename(temporary, s.path); err != nil {
		return fmt.Errorf("replace control config: %w", err)
	}
	return nil
}

func RedactMap(value map[string]any) map[string]any {
	result := make(map[string]any, len(value))
	for key, item := range value {
		lower := strings.ToLower(key)
		switch nested := item.(type) {
		case map[string]any:
			result[key] = RedactMap(nested)
		default:
			if strings.Contains(lower, "password") || strings.Contains(lower, "pass") ||
				strings.Contains(lower, "token") || strings.Contains(lower, "secret") || strings.Contains(lower, "key") {
				if fmt.Sprint(item) == "" {
					result[key] = ""
				} else {
					result[key] = "***"
				}
			} else if strings.Contains(lower, "proxy") {
				result[key] = RedactProxy(fmt.Sprint(item))
			} else {
				result[key] = item
			}
		}
	}
	return result
}

func RedactProxy(value string) string {
	text := strings.TrimSpace(value)
	if text == "" {
		return ""
	}
	if at := strings.LastIndex(text, "@"); at >= 0 {
		prefix, host := text[:at], text[at+1:]
		scheme, credentials := "", prefix
		if marker := strings.Index(prefix, "://"); marker >= 0 {
			scheme, credentials = prefix[:marker+3], prefix[marker+3:]
		}
		username := strings.SplitN(credentials, ":", 2)[0]
		return scheme + username + ":***@" + host
	}
	parts := strings.Split(text, ":")
	if len(parts) >= 4 {
		return strings.Join([]string{parts[0], parts[1], "***", "***"}, ":")
	}
	if len(parts) >= 3 {
		return strings.Join([]string{parts[0], "***", parts[len(parts)-1]}, ":")
	}
	return "***"
}
