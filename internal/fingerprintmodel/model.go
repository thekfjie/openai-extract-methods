package fingerprintmodel

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
)

var (
	profileIDPattern = regexp.MustCompile(`^[0-9a-f]{16}$`)
	macPattern       = regexp.MustCompile(`^([0-9A-F]{2}:){5}[0-9A-F]{2}$`)
)

type Bundle struct {
	Profile Profile `json:"profile"`
}

type Profile struct {
	SchemaVersion    int                        `json:"schemaVersion"`
	Purpose          string                     `json:"purpose"`
	ID               string                     `json:"id"`
	Seed             string                     `json:"seed"`
	Preset           string                     `json:"preset"`
	Generator        Generator                  `json:"generator"`
	Engine           Engine                     `json:"engine"`
	OS               OS                         `json:"os"`
	Machine          Machine                    `json:"machine"`
	Locale           Locale                     `json:"locale"`
	Navigator        Navigator                  `json:"navigator"`
	Screen           Screen                     `json:"screen"`
	RequiredFamilies map[string]json.RawMessage `json:"-"`
}

type Generator struct {
	Algorithm      string  `json:"algorithm"`
	FeatureMode    string  `json:"featureMode"`
	Deterministic  bool    `json:"deterministic"`
	BaseDataSource string  `json:"baseDataSource"`
	Provider       *string `json:"provider"`
}

type Engine struct {
	Family    string `json:"family"`
	Version   string `json:"version"`
	UserAgent string `json:"userAgent"`
}

type OS struct {
	Name         string `json:"name"`
	Version      string `json:"version"`
	Architecture string `json:"architecture"`
	Model        string `json:"model"`
}

type Machine struct {
	ComputerName string `json:"computerName"`
	MACAddress   string `json:"macAddress"`
}

type Locale struct {
	AppLocale      string `json:"appLocale"`
	AcceptLanguage string `json:"acceptLanguage"`
	Timezone       string `json:"timezone"`
}

type Navigator struct {
	Platform            string  `json:"platform"`
	HardwareConcurrency int     `json:"hardwareConcurrency"`
	DeviceMemory        float64 `json:"deviceMemory"`
	MaxTouchPoints      int     `json:"maxTouchPoints"`
	Mobile              bool    `json:"mobile"`
}

type Screen struct {
	Width            int     `json:"width"`
	Height           int     `json:"height"`
	AvailWidth       int     `json:"availWidth"`
	AvailHeight      int     `json:"availHeight"`
	DevicePixelRatio float64 `json:"devicePixelRatio"`
}

var requiredFamilies = []string{
	"graphics", "canvas", "audioContext", "clientRects", "fonts", "speechSynthesis",
	"mediaDevices", "webrtc", "geolocation", "content", "security", "runtime",
	"battery", "network", "bluetooth",
}

func DecodeBundle(value any) (Bundle, error) {
	encoded, err := json.Marshal(value)
	if err != nil {
		return Bundle{}, fmt.Errorf("encode fingerprint bundle: %w", err)
	}
	var envelope map[string]json.RawMessage
	if err := json.Unmarshal(encoded, &envelope); err != nil {
		return Bundle{}, errors.New("fingerprint bundle must be a JSON object")
	}
	profileJSON := envelope["profile"]
	if len(profileJSON) == 0 {
		profileJSON = encoded
	}
	var profileMap map[string]json.RawMessage
	if err := json.Unmarshal(profileJSON, &profileMap); err != nil {
		return Bundle{}, errors.New("fingerprint profile must be a JSON object")
	}
	var profile Profile
	if err := json.Unmarshal(profileJSON, &profile); err != nil {
		return Bundle{}, fmt.Errorf("decode fingerprint profile: %w", err)
	}
	profile.RequiredFamilies = make(map[string]json.RawMessage, len(requiredFamilies))
	for _, name := range requiredFamilies {
		profile.RequiredFamilies[name] = profileMap[name]
	}
	return Bundle{Profile: profile}, nil
}

func ValidateBundle(value any) error {
	bundle, err := DecodeBundle(value)
	if err != nil {
		return err
	}
	return bundle.Profile.Validate()
}

func (p Profile) Validate() error {
	var problems []string
	if p.SchemaVersion != 2 {
		problems = append(problems, "schemaVersion must be 2")
	}
	if p.Purpose != "local-browser-compatibility-testing" {
		problems = append(problems, "purpose is invalid")
	}
	if !profileIDPattern.MatchString(p.ID) {
		problems = append(problems, "id must be 16 lowercase hex characters")
	}
	if p.Seed == "" || p.Preset == "" {
		problems = append(problems, "seed and preset are required")
	}
	if p.Generator.Algorithm != "roxybrowser-3.9.2-compatible" {
		problems = append(problems, "generator algorithm is invalid")
	}
	if p.Generator.BaseDataSource != "local-template" && p.Generator.BaseDataSource != "authorized-provider" {
		problems = append(problems, "generator baseDataSource is invalid")
	}
	if p.Engine.Family != "Chrome" && p.Engine.Family != "Firefox" {
		problems = append(problems, "engine family is invalid")
	}
	if p.Engine.Version == "" || p.Engine.UserAgent == "" {
		problems = append(problems, "engine version and userAgent are required")
	}
	if !macPattern.MatchString(p.Machine.MACAddress) {
		problems = append(problems, "machine macAddress is invalid")
	}
	if p.Locale.AppLocale == "" || p.Locale.AcceptLanguage == "" || p.Locale.Timezone == "" {
		problems = append(problems, "locale fields are required")
	}
	if p.Navigator.HardwareConcurrency < 1 || p.Navigator.DeviceMemory < 0 || p.Navigator.MaxTouchPoints < 0 {
		problems = append(problems, "navigator hardware values are invalid")
	}
	if p.Screen.Width < 1 || p.Screen.Height < 1 || p.Screen.DevicePixelRatio <= 0 {
		problems = append(problems, "screen dimensions are invalid")
	}
	for _, name := range requiredFamilies {
		raw := p.RequiredFamilies[name]
		if len(raw) == 0 || string(raw) == "null" {
			problems = append(problems, name+" is required")
		}
	}
	if len(problems) > 0 {
		return errors.New(strings.Join(problems, "; "))
	}
	return nil
}
