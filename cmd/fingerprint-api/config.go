package main

import (
	"context"

	"automyai/internal/fingerprintconfig"
	"automyai/internal/fingerprintsdk"
	"automyai/internal/roxyopenapi"
)

type runtimeSettings = fingerprintconfig.Settings

func loadRuntimeSettings(path string) (runtimeSettings, error) { return fingerprintconfig.Load(path) }

type configuredRunner struct {
	base       fingerprintsdk.Runner
	configFile string
}

func (r configuredRunner) Available() bool {
	return r.base.Available()
}

func (r configuredRunner) Presets(ctx context.Context) ([]string, error) {
	return r.base.Presets(ctx)
}

func (r configuredRunner) Generate(ctx context.Context, request generateRequest) (any, error) {
	settings, err := loadRuntimeSettings(r.configFile)
	if err != nil {
		return nil, err
	}
	runner := r.base
	runner.CloudEnabled = settings.CloudEnabled
	runner.CloudBaseURL = settings.CloudBaseURL
	runner.CloudHeadersFile = settings.CloudHeadersFile
	runner.CloudOmitMAC = settings.CloudOmitMAC
	return runner.Generate(ctx, request)
}

func (r configuredRunner) SourceStatus() map[string]any {
	settings, err := loadRuntimeSettings(r.configFile)
	if err != nil {
		return map[string]any{"mode": "unknown", "error": err.Error()}
	}
	mode := "local-template"
	if settings.CloudEnabled {
		mode = "authorized-cloud"
	}
	return map[string]any{
		"mode":            mode,
		"cloudEnabled":    settings.CloudEnabled,
		"cloudConfigured": settings.CloudBaseURL != "" && settings.CloudHeadersFile != "",
	}
}

func roxyClient(settings runtimeSettings) roxyopenapi.Client {
	return roxyopenapi.Client{
		BaseURL: settings.RoxyBaseURL,
		KeyFile: settings.RoxyKeyFile,
		Timeout: settings.RoxyTimeout,
	}
}
