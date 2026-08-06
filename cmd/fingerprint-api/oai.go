package main

import (
	"crypto/sha1" // UUID v5 requires SHA-1 by specification; this is not used for security.
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"automyai/internal/fingerprintpolicy"
)

type oaiEntrySpec struct {
	Preset         string
	BrowserVersion string
	Managed        bool
}

var oaiEntrySpecs = map[string]oaiEntrySpec{
	"uc_signup":        {Preset: "windows-11-chrome", BrowserVersion: "145.0.0.0"},
	"openai2":          {Preset: "macos-intel-chrome", BrowserVersion: "145.0.0.0"},
	"openai3":          {Preset: fingerprintpolicy.OpenAI3Preset, BrowserVersion: fingerprintpolicy.ChromeBrowserVersion, Managed: true},
	"chatgpt_register": {Preset: fingerprintpolicy.OpenAI3Preset, BrowserVersion: fingerprintpolicy.ChromeBrowserVersion, Managed: true},
}

type oaiGenerateRequest struct {
	Entry             string `json:"entry"`
	Preset            string `json:"preset"`
	Seed              string `json:"seed"`
	Source            string `json:"source"`
	BrowserVersion    string `json:"browserVersion"`
	BrowserVersionAlt string `json:"browser_version"`
}

func normalizeOAIBundle(bundle any, entry string) (map[string]any, error) {
	root, ok := bundle.(map[string]any)
	if !ok {
		return nil, errors.New("fingerprint generator returned invalid bundle")
	}
	profile := object(root["profile"])
	if len(profile) == 0 {
		profile = root
	}
	engine := object(profile["engine"])
	locale := object(profile["locale"])
	navigator := object(profile["navigator"])
	screen := object(profile["screen"])
	machine := object(profile["machine"])
	graphics := object(profile["graphics"])
	generator := object(profile["generator"])
	userAgent := stringValue(engine["userAgent"])
	if userAgent == "" {
		return nil, errors.New("fingerprint profile has no user agent")
	}
	secCHUA, secCHPlatform, secCHMobile := clientHints(object(engine["userAgentMetadata"]))
	width := intValue(screen["width"], 1920)
	height := intValue(screen["height"], 1080)
	lang := defaultString(locale["appLocale"], "en-US")
	acceptLanguage := defaultString(locale["acceptLanguage"], lang)
	profileID := stringValue(profile["id"])
	if profileID == "" {
		digest := sha256.Sum256([]byte(userAgent))
		profileID = hex.EncodeToString(digest[:])[:16]
	}
	impersonate := oaiImpersonate(profile)
	baseDataSource := stringValue(generator["baseDataSource"])
	cloudBaseRecords := baseDataSource == "authorized-provider"
	provenance := map[string]any{
		"provider":  "local-api",
		"verified":  true,
		"status":    "local-key-accepted",
		"authority": "local-api",
		"official":  false,
		"endpoint":  "/oai/fingerprint/generate",
	}
	if cloudBaseRecords {
		provenance = map[string]any{
			"provider":  stringValue(generator["provider"]),
			"verified":  true,
			"status":    "authorized-cloud-records",
			"authority": "authorized-provider",
			"official":  true,
			"endpoint":  "/user_get_ua_webgl_v2,/user_get_device_name_v2,/user_get_mac_addr_v2",
		}
	}
	normalized := map[string]any{
		"entry":                  entry,
		"impersonate":            impersonate,
		"impersonate_candidates": impersonateCandidates(profile, impersonate),
		"user_agent":             userAgent,
		"sec_ch_ua":              secCHUA,
		"sec_ch_ua_platform":     secCHPlatform,
		"sec_ch_ua_mobile":       secCHMobile,
		"screen":                 fmt.Sprintf("%dx%d", width, height),
		"screen_width":           width,
		"screen_height":          height,
		"screen_avail_width":     intValue(screen["availWidth"], width),
		"screen_avail_height":    intValue(screen["availHeight"], height),
		"device_pixel_ratio":     floatValue(screen["devicePixelRatio"], 1),
		"lang":                   lang,
		"lang_full":              acceptLanguage,
		"languages":              languageList(acceptLanguage, lang),
		"timezone":               stringValue(locale["timezone"]),
		"platform":               stringValue(navigator["platform"]),
		"hardware_concurrency":   intValue(navigator["hardwareConcurrency"], 8),
		"device_memory":          floatValue(navigator["deviceMemory"], 8),
		"max_touch_points":       intValue(navigator["maxTouchPoints"], 0),
		"mobile":                 boolValue(navigator["mobile"]),
		"do_not_track":           nil,
		"device_name":            stringValue(machine["computerName"]),
		"webgl_vendor":           stringValue(graphics["webglVendor"]),
		"webgl_renderer":         stringValue(graphics["webglRenderer"]),
		"base_data_source":       baseDataSource,
		"generator_provider":     stringValue(generator["provider"]),
		"cloud_base_records":     cloudBaseRecords,
		"device_id":              uuidV5URL("automyai-fingerprint:" + profileID),
		"profile_id":             profileID,
		"seed":                   stringValue(profile["seed"]),
		"preset":                 stringValue(profile["preset"]),
		"source":                 "automyai-fingerprint-api",
		"profile":                profile,
		"roxy_config":            object(root["roxyConfig"]),
		"runtime_config":         object(root["runtimeConfig"]),
		"provenance":             provenance,
	}
	if boolValue(navigator["doNotTrack"]) {
		normalized["do_not_track"] = "1"
	}
	normalized["http_headers"] = oaiHTTPHeaders(normalized)
	normalized["sentinel_navigator"] = sentinelNavigator(normalized)
	normalized["chromium_base_args"] = chromiumBaseArgs(normalized)
	normalized["chromium_cdp_commands"] = chromiumCDPCommands(normalized)
	return normalized, nil
}

func object(value any) map[string]any {
	result, _ := value.(map[string]any)
	if result == nil {
		return map[string]any{}
	}
	return result
}

func stringValue(value any) string {
	if value == nil {
		return ""
	}
	if text, ok := value.(string); ok {
		return text
	}
	return fmt.Sprint(value)
}

func defaultString(value any, fallback string) string {
	if result := stringValue(value); result != "" {
		return result
	}
	return fallback
}

func intValue(value any, fallback int) int {
	switch number := value.(type) {
	case float64:
		if number != 0 {
			return int(number)
		}
	case int:
		if number != 0 {
			return number
		}
	case json.Number:
		if parsed, err := number.Int64(); err == nil && parsed != 0 {
			return int(parsed)
		}
	case string:
		if parsed, err := strconv.Atoi(number); err == nil && parsed != 0 {
			return parsed
		}
	}
	return fallback
}

func floatValue(value any, fallback float64) float64 {
	switch number := value.(type) {
	case float64:
		if number != 0 {
			return number
		}
	case int:
		if number != 0 {
			return float64(number)
		}
	case json.Number:
		if parsed, err := number.Float64(); err == nil && parsed != 0 {
			return parsed
		}
	case string:
		if parsed, err := strconv.ParseFloat(number, 64); err == nil && parsed != 0 {
			return parsed
		}
	}
	return fallback
}

func boolValue(value any) bool {
	switch item := value.(type) {
	case bool:
		return item
	case string:
		switch strings.ToLower(strings.TrimSpace(item)) {
		case "1", "true", "yes", "on":
			return true
		default:
			return false
		}
	case float64:
		return item != 0
	case int:
		return item != 0
	default:
		return false
	}
}

func clientHints(metadata map[string]any) (string, string, string) {
	brands := make([]string, 0)
	if values, ok := metadata["brands"].([]any); ok {
		for _, value := range values {
			brand := object(value)
			name := strings.ReplaceAll(stringValue(brand["brand"]), `"`, "")
			version := strings.ReplaceAll(stringValue(brand["version"]), `"`, "")
			if name != "" && version != "" {
				brands = append(brands, fmt.Sprintf(`"%s";v="%s"`, name, version))
			}
		}
	}
	platform := strings.ReplaceAll(stringValue(metadata["platform"]), `"`, "")
	quotedPlatform := ""
	if platform != "" {
		quotedPlatform = `"` + platform + `"`
	}
	mobile := "?0"
	if boolValue(metadata["mobile"]) {
		mobile = "?1"
	}
	return strings.Join(brands, ", "), quotedPlatform, mobile
}

func languageList(acceptLanguage, primary string) []string {
	result := make([]string, 0)
	seen := map[string]bool{}
	for _, item := range strings.Split(acceptLanguage, ",") {
		value := strings.TrimSpace(strings.SplitN(item, ";", 2)[0])
		if value != "" && !seen[value] {
			seen[value] = true
			result = append(result, value)
		}
	}
	if primary != "" && !seen[primary] {
		result = append([]string{primary}, result...)
	}
	if len(result) == 0 {
		return []string{"en-US", "en"}
	}
	return result
}

func majorVersion(value any) string {
	return strings.SplitN(stringValue(value), ".", 2)[0]
}

func oaiImpersonate(profile map[string]any) string {
	engine := object(profile["engine"])
	navigator := object(profile["navigator"])
	osInfo := object(profile["os"])
	family := strings.ToLower(stringValue(engine["family"]))
	major := majorVersion(engine["version"])
	if strings.ToLower(stringValue(osInfo["name"])) == "ios" {
		return "safari_ios"
	}
	if family == "firefox" {
		if map[string]bool{"133": true, "135": true, "144": true, "147": true}[major] {
			return "firefox" + major
		}
		return "firefox"
	}
	if boolValue(navigator["mobile"]) {
		if major == "131" {
			return "chrome131_android"
		}
		return "chrome_android"
	}
	known := map[string]bool{"99": true, "100": true, "101": true, "104": true, "107": true, "110": true, "116": true, "119": true, "120": true, "123": true, "124": true, "131": true, "136": true, "142": true, "145": true, "146": true}
	if known[major] {
		return "chrome" + major
	}
	return "chrome"
}

func impersonateCandidates(profile map[string]any, primary string) []string {
	family := strings.ToLower(stringValue(object(profile["engine"])["family"]))
	generic := "chrome"
	if primary == "safari_ios" {
		generic = "safari_ios"
	} else if family == "firefox" {
		generic = "firefox"
	} else if strings.Contains(primary, "android") {
		generic = "chrome_android"
	}
	if generic == primary {
		return []string{primary}
	}
	return []string{primary, generic}
}

func uuidV5URL(name string) string {
	namespace := []byte{0x6b, 0xa7, 0xb8, 0x11, 0x9d, 0xad, 0x11, 0xd1, 0x80, 0xb4, 0x00, 0xc0, 0x4f, 0xd4, 0x30, 0xc8}
	hash := sha1.New() // UUID v5 is defined in terms of SHA-1.
	_, _ = hash.Write(namespace)
	_, _ = hash.Write([]byte(name))
	value := hash.Sum(nil)[:16]
	value[6] = (value[6] & 0x0f) | 0x50
	value[8] = (value[8] & 0x3f) | 0x80
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%s",
		binary.BigEndian.Uint32(value[0:4]),
		binary.BigEndian.Uint16(value[4:6]),
		binary.BigEndian.Uint16(value[6:8]),
		binary.BigEndian.Uint16(value[8:10]),
		hex.EncodeToString(value[10:16]),
	)
}

func oaiHTTPHeaders(fingerprint map[string]any) map[string]string {
	headers := map[string]string{
		"User-Agent":      stringValue(fingerprint["user_agent"]),
		"Accept-Language": stringValue(fingerprint["lang_full"]),
	}
	if secCHUA := stringValue(fingerprint["sec_ch_ua"]); secCHUA != "" {
		headers["sec-ch-ua"] = secCHUA
		headers["sec-ch-ua-mobile"] = defaultString(fingerprint["sec_ch_ua_mobile"], "?0")
		headers["sec-ch-ua-platform"] = stringValue(fingerprint["sec_ch_ua_platform"])
	}
	if dnt := stringValue(fingerprint["do_not_track"]); dnt != "" {
		headers["DNT"] = dnt
	}
	for key, value := range headers {
		if value == "" {
			delete(headers, key)
		}
	}
	return headers
}

func sentinelNavigator(fingerprint map[string]any) map[string]string {
	userAgent := stringValue(fingerprint["user_agent"])
	appVersion := strings.TrimPrefix(userAgent, "Mozilla/")
	chrome := strings.Contains(userAgent, "Chrome/") || strings.Contains(userAgent, "CriOS/")
	pluginsEnabled := true
	navigator := object(object(fingerprint["profile"])["navigator"])
	if value, ok := navigator["pluginsEnabled"]; ok {
		pluginsEnabled = boolValue(value)
	}
	languages := make([]string, 0)
	if values, ok := fingerprint["languages"].([]string); ok {
		languages = values
	} else if values, ok := fingerprint["languages"].([]any); ok {
		for _, value := range values {
			languages = append(languages, stringValue(value))
		}
	}
	result := map[string]string{
		"userAgent":           userAgent,
		"language":            defaultString(fingerprint["lang"], "en-US"),
		"languages":           strings.Join(languages, ","),
		"platform":            defaultString(fingerprint["platform"], "Win32"),
		"vendor":              "",
		"vendorSub":           "",
		"product":             "Gecko",
		"productSub":          "20100101",
		"appName":             "Netscape",
		"appVersion":          appVersion,
		"appCodeName":         "Mozilla",
		"hardwareConcurrency": strconv.Itoa(intValue(fingerprint["hardware_concurrency"], 8)),
		"deviceMemory":        strconv.FormatFloat(floatValue(fingerprint["device_memory"], 8), 'f', -1, 64),
		"maxTouchPoints":      strconv.Itoa(intValue(fingerprint["max_touch_points"], 0)),
		"cookieEnabled":       "true",
		"onLine":              "true",
		"doNotTrack":          defaultString(fingerprint["do_not_track"], "null"),
		"pdfViewerEnabled":    strconv.FormatBool(pluginsEnabled),
	}
	if chrome {
		result["vendor"] = "Google Inc."
		result["productSub"] = "20030107"
	}
	return result
}

func chromiumBaseArgs(fingerprint map[string]any) []string {
	result := []string{"--no-sandbox", "--disable-dev-shm-usage", "--remote-allow-origins=*"}
	runtime := object(fingerprint["runtime_config"])
	if args, ok := runtime["launchArgs"].([]any); ok {
		for _, item := range args {
			value := stringValue(item)
			if strings.HasPrefix(value, "--user-data-dir=") || strings.HasPrefix(value, "--remote-debugging-port=") {
				continue
			}
			result = appendUnique(result, value)
		}
	}
	return result
}

func appendUnique(values []string, value string) []string {
	if value == "" {
		return values
	}
	for _, existing := range values {
		if existing == value {
			return values
		}
	}
	return append(values, value)
}

func chromiumCDPCommands(fingerprint map[string]any) []map[string]any {
	profile := object(fingerprint["profile"])
	engine := object(profile["engine"])
	navigator := object(profile["navigator"])
	screen := object(profile["screen"])
	runtime := object(profile["runtime"])
	commands := []map[string]any{{
		"method": "Page.addScriptToEvaluateOnNewDocument",
		"params": map[string]any{"source": navigatorPreloadScript(fingerprint)},
	}}
	if timezone := stringValue(fingerprint["timezone"]); timezone != "" {
		commands = append(commands, map[string]any{"method": "Emulation.setTimezoneOverride", "params": map[string]any{"timezoneId": timezone}})
	}
	locale := stringValue(fingerprint["lang"])
	if locale != "" {
		commands = append(commands, map[string]any{"method": "Emulation.setLocaleOverride", "params": map[string]any{"locale": locale}})
	}
	if len(screen) > 0 {
		width := intValue(screen["width"], 1440)
		height := intValue(screen["height"], 900)
		commands = append(commands, map[string]any{"method": "Emulation.setDeviceMetricsOverride", "params": map[string]any{
			"width": width, "height": height, "deviceScaleFactor": floatValue(screen["devicePixelRatio"], 1),
			"mobile": boolValue(navigator["mobile"]), "screenWidth": width, "screenHeight": height,
		}})
	}
	commands = append(commands, map[string]any{"method": "Emulation.setHardwareConcurrencyOverride", "params": map[string]any{"hardwareConcurrency": intValue(fingerprint["hardware_concurrency"], 8)}})
	touchPoints := intValue(fingerprint["max_touch_points"], 0)
	touch := map[string]any{"enabled": touchPoints > 0}
	if touchPoints > 0 {
		touch["maxTouchPoints"] = touchPoints
	}
	commands = append(commands, map[string]any{"method": "Emulation.setTouchEmulationEnabled", "params": touch})
	colorScheme := defaultString(runtime["colorScheme"], "system")
	if colorScheme == "light" || colorScheme == "dark" {
		commands = append(commands, map[string]any{"method": "Emulation.setEmulatedMedia", "params": map[string]any{"features": []map[string]string{{"name": "prefers-color-scheme", "value": colorScheme}}}})
	}
	uaParams := map[string]any{
		"userAgent": stringValue(fingerprint["user_agent"]), "acceptLanguage": strings.Join(languageList(stringValue(fingerprint["lang_full"]), locale), ","),
		"platform": stringValue(fingerprint["platform"]),
	}
	if metadata := object(engine["userAgentMetadata"]); len(metadata) > 0 {
		uaParams["userAgentMetadata"] = metadata
	}
	commands = append(commands, map[string]any{"method": "Network.setUserAgentOverride", "params": uaParams})
	return commands
}

func navigatorPreloadScript(fingerprint map[string]any) string {
	profile := object(fingerprint["profile"])
	payload := map[string]any{
		"language": fingerprint["lang"], "languages": fingerprint["languages"], "platform": fingerprint["platform"],
		"hardwareConcurrency": fingerprint["hardware_concurrency"], "deviceMemory": fingerprint["device_memory"],
		"maxTouchPoints": fingerprint["max_touch_points"], "doNotTrack": fingerprint["do_not_track"],
		"screen": object(profile["screen"]), "mobile": boolValue(object(profile["navigator"])["mobile"]),
		"webglVendor": object(profile["graphics"])["webglVendor"], "webglRenderer": object(profile["graphics"])["webglRenderer"],
	}
	encoded, _ := json.Marshal(payload)
	return `(() => {
  const fp = ` + string(encoded) + `;
  const define = (object, name, value) => {
    if (value === undefined || value === null) return;
    try { Object.defineProperty(object, name, { configurable: true, get: () => value }); } catch (_) {}
  };
  const nav = Navigator.prototype;
  for (const name of ['language', 'languages', 'platform', 'hardwareConcurrency', 'deviceMemory', 'maxTouchPoints', 'doNotTrack'])
    define(nav, name, fp[name]);
  const scr = Screen.prototype;
  for (const name of ['width', 'height', 'availWidth', 'availHeight', 'colorDepth', 'pixelDepth'])
    define(scr, name, fp.screen && fp.screen[name]);
  define(window, 'devicePixelRatio', fp.screen && fp.screen.devicePixelRatio);
  const patchWebGL = (ctor) => {
    if (!ctor || !ctor.prototype) return;
    const original = ctor.prototype.getParameter;
    if (typeof original !== 'function') return;
    Object.defineProperty(ctor.prototype, 'getParameter', { configurable: true, value: function(parameter) {
      if (parameter === 37445 && fp.webglVendor) return fp.webglVendor;
      if (parameter === 37446 && fp.webglRenderer) return fp.webglRenderer;
      return original.apply(this, arguments);
    } });
  };
  patchWebGL(globalThis.WebGLRenderingContext);
  patchWebGL(globalThis.WebGL2RenderingContext);
})();`
}
