package fingerprintpolicy

import (
	"testing"

	"automyai/internal/fingerprintmodel"
)

func TestResolveOpenAI3PresetIsRepeatableAndWindowsChrome(t *testing.T) {
	available := OpenAI3Presets()
	first, err := ResolveOpenAI3Preset("fixed", 0, available)
	if err != nil {
		t.Fatal(err)
	}
	second, err := ResolveOpenAI3Preset("fixed", 0, available)
	if err != nil {
		t.Fatal(err)
	}
	if first != second || first != "windows-11-chrome" || !IsOpenAI3ConcretePreset(first) {
		t.Fatalf("unexpected resolved preset: %q / %q", first, second)
	}
}

func TestValidateOpenAI3BundleRejectsLinux(t *testing.T) {
	bundle := fingerprintmodel.Bundle{Profile: fingerprintmodel.Profile{
		Preset: "linux-firefox",
		Engine: fingerprintmodel.Engine{Family: "Chrome", Version: ChromeBrowserVersion,
			UserAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/150.0.0.0 Safari/537.36"},
		OS:        fingerprintmodel.OS{Name: "Linux"},
		Navigator: fingerprintmodel.Navigator{Platform: "Linux x86_64"},
	}}
	if err := ValidateOpenAI3Bundle(bundle); err == nil {
		t.Fatal("expected Linux policy rejection")
	}
}
