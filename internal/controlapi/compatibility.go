package controlapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"automyai/internal/fingerprintmodel"
	"automyai/internal/fingerprintpolicy"
	"automyai/internal/fingerprintsdk"
)

const CompatibilityTaskType = "fingerprint-compatibility-check"

type CompatibilityInput struct {
	Presets          []string `json:"presets"`
	Source           string   `json:"source,omitempty"`
	SamplesPerPreset int      `json:"samplesPerPreset"`
	BrowserVersion   string   `json:"browserVersion,omitempty"`
}

type CompatibilityPresetResult struct {
	Preset              string   `json:"preset"`
	Samples             int      `json:"samples"`
	Valid               bool     `json:"valid"`
	Deterministic       bool     `json:"deterministic"`
	RuntimePlanComplete bool     `json:"runtimePlanComplete"`
	Diverse             bool     `json:"diverse"`
	ProfileIDs          []string `json:"profileIds"`
	DurationMS          int64    `json:"durationMs"`
}

type CompatibilityResult struct {
	Valid       bool                        `json:"valid"`
	CheckedAt   time.Time                   `json:"checkedAt"`
	Source      string                      `json:"source"`
	Environment string                      `json:"environment"`
	Presets     []CompatibilityPresetResult `json:"presets"`
}

type SDK interface {
	Available() bool
	Presets(context.Context) ([]string, error)
	Generate(context.Context, fingerprintsdk.Request) (any, error)
}

func RunCompatibilityCheck(ctx context.Context, sdk SDK, defaults Config, raw map[string]any) (CompatibilityResult, error) {
	input := CompatibilityInput{
		Presets:          append([]string(nil), defaults.CompatibilityPresets...),
		Source:           defaults.FingerprintSource,
		SamplesPerPreset: defaults.SamplesPerPreset,
		BrowserVersion:   defaults.BrowserVersion,
	}
	if raw != nil {
		encoded, err := json.Marshal(raw)
		if err != nil {
			return CompatibilityResult{}, errors.New("invalid compatibility task input")
		}
		decoderInput := CompatibilityInput{}
		decoder := json.NewDecoder(bytes.NewReader(encoded))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&decoderInput); err != nil {
			return CompatibilityResult{}, errors.New("invalid compatibility task input")
		}
		if len(decoderInput.Presets) > 0 {
			input.Presets = decoderInput.Presets
		}
		if decoderInput.SamplesPerPreset > 0 {
			input.SamplesPerPreset = decoderInput.SamplesPerPreset
		}
		if decoderInput.BrowserVersion != "" {
			input.BrowserVersion = decoderInput.BrowserVersion
		}
		if decoderInput.Source != "" {
			input.Source = decoderInput.Source
		}
	}
	if input.Source != "local" && input.Source != "cloud" {
		return CompatibilityResult{}, errors.New("source must be local or cloud")
	}
	requiredPresets := fingerprintpolicy.OpenAI3Presets()
	if !samePresetSet(input.Presets, requiredPresets) {
		return CompatibilityResult{}, errors.New("presets must be the fixed Windows Chrome desktop set")
	}
	if input.BrowserVersion == "" {
		input.BrowserVersion = fingerprintpolicy.ChromeBrowserVersion
	}
	if input.BrowserVersion != fingerprintpolicy.ChromeBrowserVersion {
		return CompatibilityResult{}, fmt.Errorf("browserVersion must be %s", fingerprintpolicy.ChromeBrowserVersion)
	}
	available, err := sdk.Presets(ctx)
	if err != nil {
		return CompatibilityResult{}, err
	}
	if err := fingerprintpolicy.ValidateAvailable(available); err != nil {
		return CompatibilityResult{}, err
	}
	validation := defaults
	validation.DefaultPreset = fingerprintpolicy.OpenAI3Preset
	validation.FingerprintSource = input.Source
	validation.CompatibilityPresets = input.Presets
	validation.SamplesPerPreset = input.SamplesPerPreset
	if err := validation.Validate(available); err != nil {
		return CompatibilityResult{}, err
	}

	result := CompatibilityResult{Valid: true, CheckedAt: time.Now().UTC(), Source: input.Source, Environment: input.Source + "-structural-runtime-plan"}
	for presetIndex, preset := range input.Presets {
		started := time.Now()
		presetResult := CompatibilityPresetResult{Preset: preset, Samples: input.SamplesPerPreset, Valid: true, Deterministic: input.Source == "local", RuntimePlanComplete: true, Diverse: true}
		seenProfileIDs := map[string]bool{}
		for sample := 0; sample < input.SamplesPerPreset; sample++ {
			seed := fmt.Sprintf("compat-%s-%d-%d", preset, presetIndex, sample)
			request := fingerprintsdk.Request{Preset: preset, Seed: seed, Count: 1, BrowserVersion: input.BrowserVersion, Source: input.Source}
			first, err := sdk.Generate(ctx, request)
			if err != nil {
				return CompatibilityResult{}, fmt.Errorf("%s sample %d: %w", preset, sample+1, err)
			}
			second, err := sdk.Generate(ctx, request)
			if err != nil {
				return CompatibilityResult{}, fmt.Errorf("%s repeat sample %d: %w", preset, sample+1, err)
			}
			if err := fingerprintmodel.ValidateBundle(first); err != nil {
				return CompatibilityResult{}, fmt.Errorf("%s sample %d validation: %w", preset, sample+1, err)
			}
			if err := fingerprintmodel.ValidateBundle(second); err != nil {
				return CompatibilityResult{}, fmt.Errorf("%s repeat sample %d validation: %w", preset, sample+1, err)
			}
			firstJSON, _ := json.Marshal(first)
			secondJSON, _ := json.Marshal(second)
			if input.Source == "local" && sha256.Sum256(firstJSON) != sha256.Sum256(secondJSON) {
				presetResult.Deterministic = false
				presetResult.Valid = false
			}
			root, ok := first.(map[string]any)
			if !ok || !runtimePlanComplete(root) {
				presetResult.RuntimePlanComplete = false
				presetResult.Valid = false
			}
			bundle, err := fingerprintmodel.DecodeBundle(first)
			if err != nil {
				return CompatibilityResult{}, err
			}
			repeatBundle, err := fingerprintmodel.DecodeBundle(second)
			if err != nil {
				return CompatibilityResult{}, err
			}
			if actual := bundleSource(bundle); actual != input.Source {
				return CompatibilityResult{}, fmt.Errorf("%s sample %d source mismatch: requested %s, received %s", preset, sample+1, input.Source, actual)
			}
			if actual := bundleSource(repeatBundle); actual != input.Source {
				return CompatibilityResult{}, fmt.Errorf("%s repeat sample %d source mismatch: requested %s, received %s", preset, sample+1, input.Source, actual)
			}
			if err := fingerprintpolicy.ValidateOpenAI3Bundle(bundle); err != nil {
				return CompatibilityResult{}, fmt.Errorf("%s sample %d policy: %w", preset, sample+1, err)
			}
			if err := fingerprintpolicy.ValidateOpenAI3Bundle(repeatBundle); err != nil {
				return CompatibilityResult{}, fmt.Errorf("%s repeat sample %d policy: %w", preset, sample+1, err)
			}
			if seenProfileIDs[bundle.Profile.ID] {
				presetResult.Diverse = false
				presetResult.Valid = false
			}
			seenProfileIDs[bundle.Profile.ID] = true
			presetResult.ProfileIDs = append(presetResult.ProfileIDs, bundle.Profile.ID)
		}
		presetResult.DurationMS = time.Since(started).Milliseconds()
		if !presetResult.Valid {
			result.Valid = false
		}
		result.Presets = append(result.Presets, presetResult)
	}
	return result, nil
}

func samePresetSet(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	want := make(map[string]bool, len(right))
	for _, value := range right {
		want[value] = true
	}
	for _, value := range left {
		if !want[value] {
			return false
		}
	}
	return true
}

func runtimePlanComplete(root map[string]any) bool {
	profile, profileOK := root["profile"].(map[string]any)
	roxy, roxyOK := root["roxyConfig"].(map[string]any)
	runtime, runtimeOK := root["runtimeConfig"].(map[string]any)
	if !profileOK || !roxyOK || !runtimeOK || len(profile) == 0 || len(roxy) == 0 || len(runtime) == 0 {
		return false
	}
	engine, engineOK := profile["engine"].(map[string]any)
	profileUA, profileUAOK := engine["userAgent"].(string)
	roxyUA, roxyUAOK := roxy["userAgent"].(string)
	runtimeEngine, runtimeEngineOK := runtime["engine"].(string)
	profileEngine, profileEngineOK := engine["family"].(string)
	if !engineOK || !profileUAOK || !roxyUAOK || profileUA == "" || profileUA != roxyUA {
		return false
	}
	if !runtimeEngineOK || !profileEngineOK || runtimeEngine != profileEngine {
		return false
	}
	launchArgs, ok := runtime["launchArgs"].([]any)
	if !ok || len(launchArgs) == 0 {
		return false
	}
	return true
}
