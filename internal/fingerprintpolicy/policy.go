package fingerprintpolicy

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"strings"

	"automyai/internal/fingerprintmodel"
)

const (
	OpenAI3Preset         = "windows-11-chrome"
	ChromeBrowserVersion  = "150.0.0.0"
)

var openAI3Presets = []string{
	"windows-11-chrome",
}

func OpenAI3Presets() []string {
	return append([]string(nil), openAI3Presets...)
}

func IsOpenAI3Preset(value string) bool {
	return strings.TrimSpace(value) == OpenAI3Preset
}

func IsOpenAI3ConcretePreset(value string) bool {
	for _, preset := range openAI3Presets {
		if value == preset {
			return true
		}
	}
	return false
}

func ValidateAvailable(available []string) error {
	known := make(map[string]bool, len(available))
	for _, preset := range available {
		known[preset] = true
	}
	for _, preset := range openAI3Presets {
		if !known[preset] {
			return fmt.Errorf("required Windows Chrome preset is unavailable: %s", preset)
		}
	}
	return nil
}

func NewSeed() (string, error) {
	value := make([]byte, 16)
	if _, err := rand.Read(value); err != nil {
		return "", fmt.Errorf("generate fingerprint policy seed: %w", err)
	}
	return hex.EncodeToString(value), nil
}

func ResolveOpenAI3Preset(seed string, index int, available []string) (string, error) {
	if strings.TrimSpace(seed) == "" {
		return "", errors.New("fingerprint policy seed is required")
	}
	if index < 0 {
		return "", errors.New("fingerprint policy index must not be negative")
	}
	if err := ValidateAvailable(available); err != nil {
		return "", err
	}
	digest := sha256.Sum256([]byte(fmt.Sprintf("automyai:%s:%s:%d", OpenAI3Preset, seed, index)))
	return openAI3Presets[int(digest[0])%len(openAI3Presets)], nil
}

func ProfileSeed(seed string, index int) string {
	if index == 0 {
		return seed
	}
	return fmt.Sprintf("%s:%d", seed, index+1)
}

func ValidateOpenAI3Bundle(bundle fingerprintmodel.Bundle) error {
	profile := bundle.Profile
	if profile.Engine.Family != "Chrome" {
		return errors.New("OpenAI 3 policy requires Chrome")
	}
	if profile.Engine.Version != ChromeBrowserVersion {
		return fmt.Errorf("OpenAI 3 policy requires Chrome %s", ChromeBrowserVersion)
	}
	if !IsOpenAI3ConcretePreset(profile.Preset) {
		return fmt.Errorf("OpenAI 3 policy rejected preset: %s", profile.Preset)
	}
	userAgent := profile.Engine.UserAgent
	if !strings.Contains(userAgent, "Chrome/"+ChromeBrowserVersion) {
		return errors.New("OpenAI 3 Chrome version and user agent do not match")
	}
	if strings.Contains(strings.ToLower(userAgent), "linux") || strings.Contains(strings.ToLower(profile.Navigator.Platform), "linux") {
		return errors.New("OpenAI 3 policy forbids Linux fingerprints")
	}
	if profile.OS.Name != "Windows" || profile.Preset != "windows-11-chrome" || profile.Navigator.Platform != "Win32" || !strings.Contains(userAgent, "Windows NT") {
		return errors.New("OpenAI 3 Windows Chrome fingerprint fields are inconsistent")
	}
	return nil
}
