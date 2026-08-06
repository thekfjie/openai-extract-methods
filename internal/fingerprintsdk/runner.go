package fingerprintsdk

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"

	"automyai/internal/fingerprintmodel"
)

const MaxCount = 20

type Request struct {
	Preset            string `json:"preset"`
	Seed              string `json:"seed"`
	Count             int    `json:"count"`
	BrowserVersion    string `json:"browserVersion"`
	BrowserVersionAlt string `json:"browser_version"`
	Source            string `json:"source,omitempty"`
}

type Runner struct {
	Node             string
	SDKDir           string
	CloudEnabled     bool
	CloudBaseURL     string
	CloudHeadersFile string
	CloudOmitMAC     bool
}

func (r Runner) Available() bool {
	info, err := os.Stat(filepath.Join(r.SDKDir, "cli.mjs"))
	return err == nil && !info.IsDir()
}

func (r Runner) run(ctx context.Context, args ...string) ([]byte, error) {
	command := exec.CommandContext(ctx, r.Node, append([]string{filepath.Join(r.SDKDir, "cli.mjs")}, args...)...)
	command.Dir = r.SDKDir
	command.Env = []string{"NO_COLOR=1"}
	for _, name := range []string{"PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR"} {
		if value, ok := os.LookupEnv(name); ok {
			command.Env = append(command.Env, name+"="+value)
		}
	}
	output, err := command.CombinedOutput()
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return nil, errors.New("fingerprint SDK timed out")
	}
	if errors.Is(ctx.Err(), context.Canceled) {
		return nil, errors.New("fingerprint SDK canceled")
	}
	if err != nil {
		message := strings.TrimSpace(string(output))
		if message == "" {
			message = "fingerprint SDK failed"
		}
		return nil, errors.New(message)
	}
	return output, nil
}

func (r Runner) Presets(ctx context.Context) ([]string, error) {
	output, err := r.run(ctx, "presets")
	if err != nil {
		return nil, err
	}
	var presets []string
	for _, line := range strings.Split(string(output), "\n") {
		if value := strings.TrimSpace(line); value != "" {
			presets = append(presets, value)
		}
	}
	if len(presets) == 0 {
		return nil, errors.New("fingerprint SDK returned no presets")
	}
	return presets, nil
}

func (r Runner) Mode() string {
	if r.CloudEnabled {
		return "authorized-cloud"
	}
	return "local-template"
}

func (r Runner) cloudArguments(source string) ([]string, error) {
	cloudEnabled := r.CloudEnabled
	switch strings.ToLower(strings.TrimSpace(source)) {
	case "", "configured":
	case "local":
		cloudEnabled = false
	case "cloud":
		cloudEnabled = true
	default:
		return nil, errors.New("fingerprint source must be local or cloud")
	}
	if !cloudEnabled {
		return []string{"generate"}, nil
	}
	baseURL := strings.TrimSpace(r.CloudBaseURL)
	parsed, err := url.Parse(baseURL)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") {
		return nil, errors.New("cloud fingerprint API base URL is invalid")
	}
	headersFile := strings.TrimSpace(r.CloudHeadersFile)
	info, err := os.Stat(headersFile)
	if err != nil || info.IsDir() {
		return nil, errors.New("cloud fingerprint headers file is not configured")
	}
	if info.Mode().Perm()&0o077 != 0 {
		return nil, errors.New("cloud fingerprint headers file permissions must be private")
	}
	arguments := []string{"generate-cloud", "--base-url", baseURL, "--headers-file", headersFile}
	if r.CloudOmitMAC {
		arguments = append(arguments, "--no-cloud-mac")
	}
	return arguments, nil
}

func (r Runner) Generate(ctx context.Context, request Request) (any, error) {
	if request.Preset == "" {
		request.Preset = "windows-11-chrome"
	}
	if request.Seed == "" {
		seed := make([]byte, 16)
		if _, err := rand.Read(seed); err != nil {
			return nil, fmt.Errorf("generate random seed: %w", err)
		}
		request.Seed = hex.EncodeToString(seed)
	}
	if request.Count == 0 {
		request.Count = 1
	}
	if request.Count < 1 || request.Count > MaxCount {
		return nil, fmt.Errorf("count must be between 1 and %d", MaxCount)
	}
	version := request.BrowserVersion
	if version == "" {
		version = request.BrowserVersionAlt
	}
	args, err := r.cloudArguments(request.Source)
	if err != nil {
		return nil, err
	}
	args = append(args,
		"--preset", request.Preset,
		"--seed", request.Seed,
		"--count", strconv.Itoa(request.Count),
		"--format", "bundle",
	)
	if version != "" {
		args = append(args, "--browser-version", version)
	}
	output, err := r.run(ctx, args...)
	if err != nil {
		return nil, err
	}
	var result any
	if err := json.Unmarshal(output, &result); err != nil {
		return nil, fmt.Errorf("fingerprint SDK returned invalid JSON: %w", err)
	}
	if err := validateResult(result); err != nil {
		return nil, fmt.Errorf("fingerprint SDK bundle validation failed: %w", err)
	}
	return result, nil
}

func validateResult(result any) error {
	if bundles, ok := result.([]any); ok {
		if len(bundles) == 0 {
			return errors.New("empty fingerprint bundle list")
		}
		for index, bundle := range bundles {
			if err := fingerprintmodel.ValidateBundle(bundle); err != nil {
				return fmt.Errorf("bundle %d: %w", index+1, err)
			}
		}
		return nil
	}
	return fingerprintmodel.ValidateBundle(result)
}

func FindSDKDir(configured string) (string, error) {
	candidates := []string{}
	if value := strings.TrimSpace(configured); value != "" {
		candidates = append(candidates, value)
	}
	candidates = append(candidates, "/app/fingerprint/sdk", "/opt/automyai/fingerprint/sdk", "fingerprint/sdk")
	for _, candidate := range candidates {
		path, err := filepath.Abs(candidate)
		if err != nil {
			continue
		}
		if info, err := os.Stat(filepath.Join(path, "cli.mjs")); err == nil && !info.IsDir() {
			return path, nil
		}
	}
	return "", errors.New("fingerprint SDK was not found")
}
