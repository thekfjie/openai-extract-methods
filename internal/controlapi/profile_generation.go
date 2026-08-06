package controlapi

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"automyai/internal/fingerprintmodel"
	"automyai/internal/fingerprintpolicy"
	"automyai/internal/fingerprintsdk"
)

const ProfileGenerateTaskType = "fingerprint-profile-generate"

type ProfileGenerateInput struct {
	Preset         string `json:"preset"`
	Source         string `json:"source,omitempty"`
	Seed           string `json:"seed,omitempty"`
	Count          int    `json:"count"`
	BrowserVersion string `json:"browserVersion,omitempty"`
	AccountID      int64  `json:"accountId,omitempty"`
	AccountEmail   string `json:"accountEmail,omitempty"`
	AccountGroup   string `json:"accountGroup,omitempty"`
}

type ProfileAccountContext struct {
	ID    int64  `json:"id"`
	Email string `json:"email,omitempty"`
	Group string `json:"group,omitempty"`
}

type ProfileSummary struct {
	ID             string `json:"id"`
	Preset         string `json:"preset"`
	Source         string `json:"source"`
	Cloud          bool   `json:"cloud"`
	BrowserVersion string `json:"browserVersion"`
	OS             string `json:"os"`
	UserAgent      string `json:"userAgent"`
	DeviceName     string `json:"deviceName"`
	WebGLVendor    string `json:"webglVendor"`
	WebGLRenderer  string `json:"webglRenderer"`
	Timezone       string `json:"timezone"`
	Screen         string `json:"screen"`
}

type ProfileGenerateResult struct {
	File     string           `json:"file"`
	Seed     string           `json:"seed"`
	Source   string           `json:"source"`
	Count    int              `json:"count"`
	Account  *ProfileAccountContext `json:"account,omitempty"`
	Profiles []ProfileSummary `json:"profiles"`
}

func RunProfileGeneration(ctx context.Context, sdk SDK, defaults Config, raw map[string]any, artifactsDir string) (ProfileGenerateResult, error) {
	input := ProfileGenerateInput{Preset: defaults.DefaultPreset, Source: defaults.FingerprintSource, Count: 1, BrowserVersion: defaults.BrowserVersion}
	if raw != nil {
		encoded, err := json.Marshal(raw)
		if err != nil {
			return ProfileGenerateResult{}, errors.New("invalid profile generation input")
		}
		decoder := json.NewDecoder(bytes.NewReader(encoded))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&input); err != nil {
			return ProfileGenerateResult{}, errors.New("invalid profile generation input")
		}
	}
	if input.Preset == "" {
		input.Preset = defaults.DefaultPreset
	}
	if input.Preset != fingerprintpolicy.OpenAI3Preset {
		return ProfileGenerateResult{}, fmt.Errorf("preset must be %s", fingerprintpolicy.OpenAI3Preset)
	}
	if input.Source == "" {
		input.Source = defaults.FingerprintSource
	}
	if input.Source != "local" && input.Source != "cloud" {
		return ProfileGenerateResult{}, errors.New("source must be local or cloud")
	}
	if input.Count == 0 {
		input.Count = 1
	}
	if input.Count < 1 || input.Count > 5 {
		return ProfileGenerateResult{}, errors.New("count must be between 1 and 5")
	}
	if input.BrowserVersion == "" {
		input.BrowserVersion = fingerprintpolicy.ChromeBrowserVersion
	}
	if input.BrowserVersion != fingerprintpolicy.ChromeBrowserVersion {
		return ProfileGenerateResult{}, fmt.Errorf("browserVersion must be %s", fingerprintpolicy.ChromeBrowserVersion)
	}
	account, err := profileAccountContext(input)
	if err != nil {
		return ProfileGenerateResult{}, err
	}
	available, err := sdk.Presets(ctx)
	if err != nil {
		return ProfileGenerateResult{}, err
	}
	if err := fingerprintpolicy.ValidateAvailable(available); err != nil {
		return ProfileGenerateResult{}, err
	}
	if input.Seed == "" {
		input.Seed, err = fingerprintpolicy.NewSeed()
		if err != nil {
			return ProfileGenerateResult{}, err
		}
	}
	bundles := make([]any, 0, input.Count)
	for index := 0; index < input.Count; index++ {
		preset, err := fingerprintpolicy.ResolveOpenAI3Preset(input.Seed, index, available)
		if err != nil {
			return ProfileGenerateResult{}, err
		}
		generated, err := sdk.Generate(ctx, fingerprintsdk.Request{
			Preset: preset, Seed: fingerprintpolicy.ProfileSeed(input.Seed, index), Count: 1,
			BrowserVersion: fingerprintpolicy.ChromeBrowserVersion, Source: input.Source,
		})
		if err != nil {
			return ProfileGenerateResult{}, err
		}
		generatedBundles := bundleList(generated)
		if len(generatedBundles) != 1 {
			return ProfileGenerateResult{}, fmt.Errorf("fingerprint SDK returned %d profiles for item %d, expected 1", len(generatedBundles), index+1)
		}
		bundles = append(bundles, generatedBundles[0])
	}
	result := ProfileGenerateResult{Seed: input.Seed, Source: input.Source, Count: len(bundles), Account: account}
	for _, item := range bundles {
		bundle, err := fingerprintmodel.DecodeBundle(item)
		if err != nil {
			return ProfileGenerateResult{}, err
		}
		if err := fingerprintpolicy.ValidateOpenAI3Bundle(bundle); err != nil {
			return ProfileGenerateResult{}, err
		}
		root, _ := item.(map[string]any)
		profile, _ := root["profile"].(map[string]any)
		graphics, _ := profile["graphics"].(map[string]any)
		actualSource := bundleSource(bundle)
		if actualSource != input.Source {
			return ProfileGenerateResult{}, fmt.Errorf("fingerprint source mismatch: requested %s, received %s", input.Source, actualSource)
		}
		result.Profiles = append(result.Profiles, ProfileSummary{
			ID: bundle.Profile.ID, Preset: bundle.Profile.Preset,
			Source: actualSource, Cloud: actualSource == "cloud",
			BrowserVersion: bundle.Profile.Engine.Version, OS: bundle.Profile.OS.Name,
			UserAgent: bundle.Profile.Engine.UserAgent, DeviceName: bundle.Profile.Machine.ComputerName,
			WebGLVendor: textValue(graphics["webglVendor"]), WebGLRenderer: textValue(graphics["webglRenderer"]),
			Timezone: bundle.Profile.Locale.Timezone,
			Screen:   fmt.Sprintf("%dx%d @ %.2fx", bundle.Profile.Screen.Width, bundle.Profile.Screen.Height, bundle.Profile.Screen.DevicePixelRatio),
		})
	}
	if artifactsDir == "" {
		return ProfileGenerateResult{}, errors.New("profile artifact directory is not configured")
	}
	if err := os.MkdirAll(artifactsDir, 0o700); err != nil {
		return ProfileGenerateResult{}, fmt.Errorf("create profile directory: %w", err)
	}
	if err := os.Chmod(artifactsDir, 0o700); err != nil {
		return ProfileGenerateResult{}, fmt.Errorf("protect profile directory: %w", err)
	}
	suffix, err := secureID(5)
	if err != nil {
		return ProfileGenerateResult{}, err
	}
	filename := fmt.Sprintf("profiles-%s-%s.json", time.Now().UTC().Format("20060102T150405Z"), suffix)
	path := filepath.Join(artifactsDir, filename)
	var artifact any = bundles
	if len(bundles) == 1 {
		artifact = bundles[0]
	}
	encoded, err := json.MarshalIndent(artifact, "", "  ")
	if err != nil {
		return ProfileGenerateResult{}, fmt.Errorf("encode generated profiles: %w", err)
	}
	temporary := path + ".tmp"
	if err := os.WriteFile(temporary, append(encoded, '\n'), 0o600); err != nil {
		return ProfileGenerateResult{}, fmt.Errorf("write generated profiles: %w", err)
	}
	if err := os.Chmod(temporary, 0o600); err != nil {
		return ProfileGenerateResult{}, fmt.Errorf("protect generated profiles: %w", err)
	}
	if err := os.Rename(temporary, path); err != nil {
		return ProfileGenerateResult{}, fmt.Errorf("publish generated profiles: %w", err)
	}
	result.File = path
	return result, nil
}

func profileAccountContext(input ProfileGenerateInput) (*ProfileAccountContext, error) {
	if input.AccountID < 0 {
		return nil, errors.New("accountId must not be negative")
	}
	email := strings.TrimSpace(input.AccountEmail)
	group := strings.TrimSpace(input.AccountGroup)
	if input.AccountID == 0 {
		if email != "" || group != "" {
			return nil, errors.New("accountId is required when account context is provided")
		}
		return nil, nil
	}
	if len(email) > 320 || strings.ContainsAny(email, "\r\n") {
		return nil, errors.New("accountEmail is invalid")
	}
	if len(group) > 128 || strings.ContainsAny(group, "\r\n") {
		return nil, errors.New("accountGroup is invalid")
	}
	return &ProfileAccountContext{ID: input.AccountID, Email: email, Group: group}, nil
}

func bundleSource(bundle fingerprintmodel.Bundle) string {
	if bundle.Profile.Generator.BaseDataSource == "authorized-provider" {
		return "cloud"
	}
	return "local"
}

func bundleList(value any) []any {
	if items, ok := value.([]any); ok {
		return items
	}
	return []any{value}
}

func secureID(size int) (string, error) {
	value := make([]byte, size)
	if _, err := rand.Read(value); err != nil {
		return "", fmt.Errorf("generate secure id: %w", err)
	}
	return hex.EncodeToString(value), nil
}

func textValue(value any) string {
	if value == nil {
		return ""
	}
	if text, ok := value.(string); ok {
		return text
	}
	return fmt.Sprint(value)
}
