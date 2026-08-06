package fingerprintmodel

import (
	"testing"
)

func loadSample(t *testing.T) map[string]any {
	t.Helper()
	profile := map[string]any{
		"schemaVersion": 2,
		"purpose":       "local-browser-compatibility-testing",
		"id":            "0123456789abcdef",
		"seed":          "model-test",
		"preset":        "windows-11-chrome",
		"generator": map[string]any{
			"algorithm": "roxybrowser-3.9.2-compatible", "featureMode": "random",
			"deterministic": true, "baseDataSource": "local-template",
		},
		"engine":    map[string]any{"family": "Chrome", "version": "145.0.0.0", "userAgent": "Mozilla/5.0 test"},
		"os":        map[string]any{"name": "Windows", "version": "11", "architecture": "x86_64", "model": ""},
		"machine":   map[string]any{"computerName": "DESKTOP-TEST", "macAddress": "02:00:00:00:00:01"},
		"locale":    map[string]any{"appLocale": "en-US", "acceptLanguage": "en-US,en;q=0.9", "timezone": "UTC"},
		"navigator": map[string]any{"platform": "Win32", "hardwareConcurrency": 8, "deviceMemory": 8, "maxTouchPoints": 0, "mobile": false},
		"screen":    map[string]any{"width": 1920, "height": 1080, "availWidth": 1920, "availHeight": 1040, "devicePixelRatio": 1},
	}
	for _, family := range requiredFamilies {
		profile[family] = map[string]any{}
	}
	return map[string]any{"profile": profile}
}

func TestValidateOriginalSample(t *testing.T) {
	if err := ValidateBundle(loadSample(t)); err != nil {
		t.Fatal(err)
	}
}

func TestRejectsMissingObservableFamily(t *testing.T) {
	value := loadSample(t)
	profile := value["profile"].(map[string]any)
	delete(profile, "canvas")
	if err := ValidateBundle(value); err == nil {
		t.Fatal("expected missing canvas error")
	}
}

func TestRejectsInvalidMachineIdentity(t *testing.T) {
	value := loadSample(t)
	profile := value["profile"].(map[string]any)
	profile["machine"].(map[string]any)["macAddress"] = "invalid"
	if err := ValidateBundle(value); err == nil {
		t.Fatal("expected invalid MAC error")
	}
}
