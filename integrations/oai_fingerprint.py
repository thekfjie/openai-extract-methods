"""Single integration surface for fingerprints used by the four OAI entries."""
from __future__ import annotations

import json
import os
import secrets
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from integrations.browser_fingerprint import browser_headers, generate_oai_fingerprint


@dataclass(frozen=True)
class EntryFingerprintSpec:
    scope: str
    preset: str
    browser_version: str


ENTRY_FINGERPRINT_SPECS = {
    "uc_signup": EntryFingerprintSpec("uc_signup", "windows-11-chrome", "145.0.0.0"),
    "openai2": EntryFingerprintSpec("openai2", "macos-intel-chrome", "145.0.0.0"),
    # The two OpenAI3 browser HAR captures use Windows Chrome 150 with Chromium
    # client hints. Keep both HTTP and Sentinel on that single browser family.
    "openai3": EntryFingerprintSpec("openai3", "windows-11-chrome", "150.0.0.0"),
    "chatgpt_register": EntryFingerprintSpec("chatgpt_register", "windows-11-chrome", "150.0.0.0"),
}

UC_CHROMIUM_PRESETS = (
    "windows-10-chrome",
    "windows-11-chrome",
    "macos-intel-chrome",
    "macos-apple-chrome",
)
UC_FINGERPRINT_IDENTITY_FILE = ".automyai-fingerprint.json"

PROXY_REGION_LOCALES: dict[str, tuple[str, list[str], str]] = {
    "US": ("en-US", ["en-US", "en"], "America/Los_Angeles"),
    "HK": ("zh-HK", ["zh-HK", "zh", "en"], "Asia/Hong_Kong"),
    "JP": ("ja-JP", ["ja-JP", "ja", "en"], "Asia/Tokyo"),
    "SG": ("en-SG", ["en-SG", "en"], "Asia/Singapore"),
    "TW": ("zh-TW", ["zh-TW", "zh", "en"], "Asia/Taipei"),
    "UK": ("en-GB", ["en-GB", "en"], "Europe/London"),
    "KR": ("ko-KR", ["ko-KR", "ko", "en"], "Asia/Seoul"),
    "MY": ("en-MY", ["en-MY", "en"], "Asia/Kuala_Lumpur"),
    "VN": ("vi-VN", ["vi-VN", "vi", "en"], "Asia/Ho_Chi_Minh"),
    "NL": ("nl-NL", ["nl-NL", "nl", "en"], "Europe/Amsterdam"),
    "DE": ("de-DE", ["de-DE", "de", "en"], "Europe/Berlin"),
}


def _accept_language(languages: list[str]) -> str:
    parts = []
    for index, language in enumerate(languages):
        parts.append(language if index == 0 else f"{language};q={max(0.5, 1 - index * 0.2):.1f}")
    return ",".join(parts)


def align_fingerprint_locale_to_region(
    fingerprint: Mapping[str, Any] | None,
    region: Any,
) -> dict[str, Any] | None:
    """Keep browser locale and timezone coherent with the reserved proxy region."""
    if fingerprint is None:
        return None
    normalized_region = str(region or "").strip().upper().replace("-", "_")
    locale = PROXY_REGION_LOCALES.get(normalized_region)
    if not locale:
        return dict(fingerprint)
    language, languages, timezone = locale
    accept_language = _accept_language(languages)
    result = deepcopy(dict(fingerprint))
    result.update({"lang": language, "lang_full": accept_language, "languages": languages, "timezone": timezone})
    profile = result.get("profile") if isinstance(result.get("profile"), dict) else {}
    profile["locale"] = {"appLocale": language, "acceptLanguage": accept_language, "timezone": timezone}
    result["profile"] = profile
    headers = result.get("http_headers") if isinstance(result.get("http_headers"), dict) else {}
    headers["Accept-Language"] = accept_language
    result["http_headers"] = headers
    navigator = result.get("sentinel_navigator") if isinstance(result.get("sentinel_navigator"), dict) else {}
    navigator["language"] = language
    navigator["languages"] = ",".join(languages)
    result["sentinel_navigator"] = navigator
    commands = result.get("chromium_cdp_commands") if isinstance(result.get("chromium_cdp_commands"), list) else []
    for command in commands:
        if not isinstance(command, dict) or not isinstance(command.get("params"), dict):
            continue
        if command.get("method") == "Emulation.setTimezoneOverride":
            command["params"]["timezoneId"] = timezone
        elif command.get("method") == "Emulation.setLocaleOverride":
            command["params"]["locale"] = language
        elif command.get("method") == "Network.setUserAgentOverride":
            command["params"]["acceptLanguage"] = accept_language
    result["chromium_cdp_commands"] = commands
    return result


def _valid_uc_identity(value: Any) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    seed = str(value.get("seed") or "").strip()
    preset = str(value.get("preset") or "").strip()
    if len(seed) < 16 or preset not in UC_CHROMIUM_PRESETS:
        return None
    return {"seed": seed, "preset": preset}


def load_or_create_uc_fingerprint_identity(profile_dir: Path) -> dict[str, str]:
    """Return one random non-Linux Chrome identity that is stable per profile."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    path = profile_dir / UC_FINGERPRINT_IDENTITY_FILE
    try:
        existing = _valid_uc_identity(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        existing = None
    if existing:
        return existing

    seed = secrets.token_hex(24)
    identity = {
        "schemaVersion": 1,
        "seed": seed,
        "preset": secrets.choice(UC_CHROMIUM_PRESETS),
        "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    payload = json.dumps(identity, ensure_ascii=False, indent=2) + "\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            winner = _valid_uc_identity(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            winner = None
        if winner:
            return winner
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    else:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
    return {"seed": identity["seed"], "preset": identity["preset"]}


def generate_entry_fingerprint(
    entry: str,
    *,
    seed: str | None = None,
    preset: str | None = None,
    browser_version: str | None = None,
) -> dict[str, Any] | None:
    """Generate and validate one coherent fingerprint for a known entry."""
    try:
        spec = ENTRY_FINGERPRINT_SPECS[entry]
    except KeyError as error:
        raise ValueError(f"unknown fingerprint entry: {entry}") from error
    fingerprint = generate_oai_fingerprint(
        scope=spec.scope,
        default_preset=preset or spec.preset,
        default_browser_version=browser_version or spec.browser_version,
        seed=seed,
    )
    if fingerprint is None:
        return None
    required = {
        "profile_id",
        "user_agent",
        "impersonate",
        "screen_width",
        "screen_height",
        "lang",
        "platform",
        "device_id",
        "profile",
    }
    missing = sorted(name for name in required if fingerprint.get(name) in (None, ""))
    if missing:
        raise ValueError(f"incomplete fingerprint for {entry}: {', '.join(missing)}")
    fingerprint["entry"] = entry
    return fingerprint


def force_fingerprint_screen(
    fingerprint: Mapping[str, Any] | None,
    width: int,
    height: int,
) -> dict[str, Any] | None:
    """Align every browser-visible screen field with the real headed desktop."""
    if fingerprint is None:
        return None
    width = max(800, int(width))
    height = max(600, int(height))
    result = deepcopy(dict(fingerprint))
    result["screen"] = f"{width}x{height}"
    result["screen_width"] = width
    result["screen_height"] = height

    profile = result.get("profile") if isinstance(result.get("profile"), dict) else {}
    screen = profile.get("screen") if isinstance(profile.get("screen"), dict) else {}
    screen.update(
        {
            "width": width,
            "height": height,
            "availWidth": width,
            "availHeight": height,
            "devicePixelRatio": 1,
        }
    )
    profile["screen"] = screen
    result["profile"] = profile

    generated_args = result.get("chromium_base_args")
    if isinstance(generated_args, list):
        result["chromium_base_args"] = [
            item
            for item in generated_args
            if not str(item).startswith("--window-size=")
        ] + [f"--window-size={width},{height}"]

    generated_commands = result.get("chromium_cdp_commands")
    if isinstance(generated_commands, list):
        normalized_commands = []
        for raw_command in generated_commands:
            if not isinstance(raw_command, Mapping):
                normalized_commands.append(raw_command)
                continue
            command = deepcopy(dict(raw_command))
            params = command.get("params") if isinstance(command.get("params"), Mapping) else {}
            injected_source = str(params.get("source") or params.get("expression") or "")
            if "__automyaiFingerprintV2" in injected_source:
                # chromium_cdp_commands() will append a freshly rendered
                # preload from this normalized profile. Keeping the SDK copy
                # would restore its old random screen through the global guard.
                continue
            if command.get("method") == "Emulation.setDeviceMetricsOverride":
                params = dict(params)
                params.update(
                    {
                        "width": width,
                        "height": height,
                        "screenWidth": width,
                        "screenHeight": height,
                        "deviceScaleFactor": 1,
                        "mobile": False,
                    }
                )
                command["params"] = params
            normalized_commands.append(command)
        result["chromium_cdp_commands"] = normalized_commands
    return result


def fingerprint_is_cloud_based(fingerprint: Mapping[str, Any] | None) -> bool:
    if not fingerprint:
        return False
    profile = fingerprint.get("profile")
    generator = profile.get("generator") if isinstance(profile, Mapping) else None
    return isinstance(generator, Mapping) and generator.get("baseDataSource") == "authorized-provider"


def fingerprint_summary(fingerprint: Mapping[str, Any] | None) -> dict[str, Any]:
    if not fingerprint:
        return {"enabled": False, "cloud": False}
    return {
        "enabled": True,
        "entry": fingerprint.get("entry", ""),
        "profile_id": fingerprint.get("profile_id", ""),
        "preset": fingerprint.get("preset", ""),
        "source": fingerprint.get("source", ""),
        "cloud": fingerprint_is_cloud_based(fingerprint),
        "user_agent": fingerprint.get("user_agent", ""),
        "device_name": fingerprint.get("device_name", ""),
        "webgl_vendor": fingerprint.get("webgl_vendor", ""),
        "webgl_renderer": fingerprint.get("webgl_renderer", ""),
    }


def fingerprint_http_headers(fingerprint: Mapping[str, Any] | None) -> dict[str, str]:
    return browser_headers(fingerprint)


def fingerprint_field(fingerprint: Mapping[str, Any] | None, key: str, default: Any) -> Any:
    if isinstance(fingerprint, Mapping):
        value = fingerprint.get(key)
        if value not in (None, ""):
            return value
    return default


def fingerprint_languages(fingerprint: Mapping[str, Any] | None) -> list[str]:
    values = fingerprint_field(fingerprint, "languages", ["en-US", "en"])
    if isinstance(values, (list, tuple)):
        return [str(item) for item in values if str(item)]
    return [item.strip() for item in str(values).split(",") if item.strip()]


def fingerprint_browser_date(fingerprint: Mapping[str, Any] | None) -> str:
    timezone_name = str(fingerprint_field(fingerprint, "timezone", "UTC"))
    try:
        timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        timezone = ZoneInfo("UTC")
    current = datetime.now(timezone)
    offset = current.strftime("%z")
    return current.strftime(f"%a %b %d %Y %H:%M:%S GMT{offset} (%Z)")


def sentinel_navigator_value(
    prop: str,
    fingerprint: Mapping[str, Any] | None,
    *,
    fallback_user_agent: str,
) -> str:
    generated = fingerprint.get("sentinel_navigator") if isinstance(fingerprint, Mapping) else None
    if isinstance(generated, Mapping) and prop in generated:
        return str(generated[prop])
    user_agent = str(fingerprint_field(fingerprint, "user_agent", fallback_user_agent))
    profile = fingerprint.get("profile") if isinstance(fingerprint, Mapping) else None
    navigator = profile.get("navigator") if isinstance(profile, Mapping) and isinstance(profile.get("navigator"), Mapping) else {}
    app_version = user_agent.removeprefix("Mozilla/")
    chrome = "Chrome/" in user_agent or "CriOS/" in user_agent
    values = {
        "userAgent": user_agent,
        "language": str(fingerprint_field(fingerprint, "lang", "en-US")),
        "languages": ",".join(fingerprint_languages(fingerprint)),
        "platform": str(fingerprint_field(fingerprint, "platform", "Win32")),
        "vendor": "Google Inc." if chrome else "",
        "vendorSub": "",
        "product": "Gecko",
        "productSub": "20030107" if chrome else "20100101",
        "appName": "Netscape",
        "appVersion": app_version,
        "appCodeName": "Mozilla",
        "hardwareConcurrency": str(fingerprint_field(fingerprint, "hardware_concurrency", 8)),
        "deviceMemory": str(fingerprint_field(fingerprint, "device_memory", 8)),
        "maxTouchPoints": str(fingerprint_field(fingerprint, "max_touch_points", 0)),
        "cookieEnabled": "true",
        "onLine": "true",
        "doNotTrack": str(fingerprint_field(fingerprint, "do_not_track", "null")),
        "pdfViewerEnabled": "true" if navigator.get("pluginsEnabled", True) else "false",
    }
    return values.get(prop, "undefined")


def curl_cffi_session_kwargs(
    fingerprint: Mapping[str, Any] | None,
    *,
    fallback_impersonate: str,
    proxy: str | None = None,
    timeout: int | float = 60,
) -> dict[str, Any]:
    """Build consistent curl_cffi sync/async Session constructor arguments."""
    kwargs: dict[str, Any] = {
        "impersonate": str((fingerprint or {}).get("impersonate") or fallback_impersonate),
        "timeout": timeout,
    }
    headers = browser_headers(fingerprint)
    if headers:
        kwargs["headers"] = headers
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    return kwargs


def chrome_proxy_server_arg(proxy: str) -> str:
    """Return a Chromium --proxy-server value.

    Chromium rejects credentials embedded in --proxy-server and surfaces
    ERR_NO_SUPPORTED_PROXIES. Authenticated proxies must be rewritten to a
    host:port form; callers should inject Proxy-Authorization elsewhere
    (local meter proxy / auth extension).
    """
    raw = str(proxy or "").strip()
    if not raw:
        return ""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    except Exception:
        return raw
    host = str(parsed.hostname or "").strip()
    if not host:
        return raw
    scheme = str(parsed.scheme or "http").strip().lower() or "http"
    if scheme not in {"http", "https", "socks4", "socks5", "socks5h"}:
        scheme = "http"
    try:
        port = parsed.port
    except Exception:
        port = None
    if port:
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


def chromium_launch_args(
    fingerprint: Mapping[str, Any] | None,
    *,
    user_data_dir: Path,
    proxy: str = "",
    user_agent: str = "",
) -> list[str]:
    """Translate the SDK runtime bundle into a deduplicated Chromium launch plan."""
    generated = (fingerprint or {}).get("chromium_base_args")
    if isinstance(generated, list):
        base = [str(item) for item in generated if str(item)]
    else:
        base = ["--no-sandbox", "--disable-dev-shm-usage", "--remote-allow-origins=*"]
        runtime = (fingerprint or {}).get("runtime_config")
        launch_args = runtime.get("launchArgs") if isinstance(runtime, Mapping) else []
        for item in launch_args if isinstance(launch_args, list) else []:
            value = str(item)
            if value.startswith("--user-data-dir=") or value.startswith("--remote-debugging-port="):
                continue
            if fingerprint and value == "--start-maximized":
                continue
            base.append(value)
    base.append(f"--user-data-dir={user_data_dir}")
    if fingerprint:
        base.extend(
            (
                f"--lang={fingerprint.get('lang') or 'en-US'}",
                f"--window-size={int(fingerprint.get('screen_width') or 1440)},{int(fingerprint.get('screen_height') or 900)}",
            )
        )
    if user_agent:
        base.append(f"--user-agent={user_agent}")
    if proxy:
        proxy_server = chrome_proxy_server_arg(proxy)
        if proxy_server:
            base.append(f"--proxy-server={proxy_server}")
    return list(dict.fromkeys(base))


def _navigator_preload_script(fingerprint: Mapping[str, Any]) -> str:
    profile = fingerprint.get("profile") if isinstance(fingerprint.get("profile"), Mapping) else {}
    navigator = profile.get("navigator") if isinstance(profile.get("navigator"), Mapping) else {}
    screen = profile.get("screen") if isinstance(profile.get("screen"), Mapping) else {}
    graphics = profile.get("graphics") if isinstance(profile.get("graphics"), Mapping) else {}
    engine = profile.get("engine") if isinstance(profile.get("engine"), Mapping) else {}
    payload = {
        "userAgent": fingerprint.get("user_agent"),
        "userAgentMetadata": engine.get("userAgentMetadata"),
        "language": fingerprint.get("lang"),
        "languages": fingerprint.get("languages"),
        "platform": fingerprint.get("platform"),
        "hardwareConcurrency": fingerprint.get("hardware_concurrency"),
        "deviceMemory": fingerprint.get("device_memory"),
        "maxTouchPoints": fingerprint.get("max_touch_points"),
        "doNotTrack": fingerprint.get("do_not_track"),
        "screen": screen,
        "mobile": bool(navigator.get("mobile")),
        "webglVendor": graphics.get("webglVendor"),
        "webglRenderer": graphics.get("webglRenderer"),
        "webglNoise": graphics.get("noise"),
        "canvas": profile.get("canvas"),
        "audioContext": profile.get("audioContext"),
        "fonts": profile.get("fonts"),
        "mediaDevices": profile.get("mediaDevices"),
        "speechSynthesis": profile.get("speechSynthesis"),
    }
    encoded = json.dumps(payload, ensure_ascii=False)
    return rf"""
(() => {{
  if (globalThis.__automyaiFingerprintV2) return;
  Object.defineProperty(globalThis, '__automyaiFingerprintV2', {{ value: true, configurable: false }});
  const fp = {encoded};
  const define = (object, name, value) => {{
    if (value === undefined || value === null) return;
    try {{ Object.defineProperty(object, name, {{ configurable: true, get: () => value }}); }} catch (_) {{}}
  }};
  const nav = Navigator.prototype;
  define(nav, 'webdriver', false);
  define(nav, 'userAgent', fp.userAgent);
  for (const name of ['language', 'languages', 'platform', 'hardwareConcurrency', 'deviceMemory', 'maxTouchPoints', 'doNotTrack'])
    define(nav, name, fp[name]);

  const metadata = fp.userAgentMetadata;
  const uaData = navigator.userAgentData;
  if (uaData && metadata) {{
    for (const name of ['brands', 'mobile', 'platform']) define(Object.getPrototypeOf(uaData), name, metadata[name]);
    const highEntropy = ['architecture', 'bitness', 'formFactors', 'fullVersionList', 'model', 'platformVersion', 'uaFullVersion', 'wow64'];
    try {{
      Object.defineProperty(Object.getPrototypeOf(uaData), 'getHighEntropyValues', {{
        configurable: true,
        value: async function(hints) {{
          const result = {{ brands: metadata.brands, mobile: metadata.mobile, platform: metadata.platform }};
          for (const name of Array.isArray(hints) ? hints : []) {{
            if (name === 'uaFullVersion') result[name] = metadata.fullVersionList?.find(item => item.brand === 'Google Chrome')?.version || '';
            else if (name === 'formFactors') result[name] = metadata.mobile ? ['Mobile'] : ['Desktop'];
            else if (highEntropy.includes(name) && metadata[name] !== undefined) result[name] = metadata[name];
          }}
          return result;
        }}
      }});
      Object.defineProperty(Object.getPrototypeOf(uaData), 'toJSON', {{
        configurable: true,
        value: function() {{ return {{ brands: metadata.brands, mobile: metadata.mobile, platform: metadata.platform }}; }}
      }});
    }} catch (_) {{}}
  }}

  const scr = Screen.prototype;
  for (const name of ['width', 'height', 'availWidth', 'availHeight', 'colorDepth', 'pixelDepth'])
    define(scr, name, fp.screen && fp.screen[name]);
  define(window, 'devicePixelRatio', fp.screen && fp.screen.devicePixelRatio);
  const patchWebGL = (ctor) => {{
    if (!ctor || !ctor.prototype) return;
    const original = ctor.prototype.getParameter;
    if (typeof original !== 'function') return;
    Object.defineProperty(ctor.prototype, 'getParameter', {{ configurable: true, value: function(parameter) {{
      if (parameter === 37445 && fp.webglVendor) return fp.webglVendor;
      if (parameter === 37446 && fp.webglRenderer) return fp.webglRenderer;
      return original.apply(this, arguments);
    }} }});
  }};
  patchWebGL(globalThis.WebGLRenderingContext);
  patchWebGL(globalThis.WebGL2RenderingContext);

  const canvasSeed = Number(fp.canvas && fp.canvas.valueV2) || 0;
  const alterPixels = (data) => {{
    if (!fp.canvas?.enabled || !data?.length) return data;
    const stride = 97 + (canvasSeed % 29);
    const offset = canvasSeed % Math.min(stride, Math.max(1, data.length));
    for (let index = offset; index < data.length; index += stride) {{
      if ((index & 3) !== 3) data[index] = (data[index] + 1 + (canvasSeed % 2)) & 255;
    }}
    return data;
  }};
  const context2d = globalThis.CanvasRenderingContext2D?.prototype;
  const nativeGetImageData = context2d?.getImageData;
  const nativePutImageData = context2d?.putImageData;
  if (nativeGetImageData) {{
    Object.defineProperty(context2d, 'getImageData', {{ configurable: true, value: function() {{
      const image = nativeGetImageData.apply(this, arguments);
      alterPixels(image.data);
      return image;
    }} }});
  }}
  const noisyCanvas = (source) => {{
    if (!fp.canvas?.enabled || !source.width || !source.height) return source;
    const clone = document.createElement('canvas');
    clone.width = source.width; clone.height = source.height;
    const context = clone.getContext('2d');
    context.drawImage(source, 0, 0);
    const image = nativeGetImageData.call(context, 0, 0, clone.width, clone.height);
    alterPixels(image.data);
    nativePutImageData.call(context, image, 0, 0);
    return clone;
  }};
  const canvasProto = globalThis.HTMLCanvasElement?.prototype;
  if (canvasProto && nativeGetImageData && nativePutImageData) {{
    const nativeToDataURL = canvasProto.toDataURL;
    const nativeToBlob = canvasProto.toBlob;
    Object.defineProperty(canvasProto, 'toDataURL', {{ configurable: true, value: function() {{
      return nativeToDataURL.apply(noisyCanvas(this), arguments);
    }} }});
    if (nativeToBlob) Object.defineProperty(canvasProto, 'toBlob', {{ configurable: true, value: function(callback, type, quality) {{
      return nativeToBlob.call(noisyCanvas(this), callback, type, quality);
    }} }});
  }}

  const audioSeed = Number(fp.audioContext?.value) || 0;
  const audioBuffer = globalThis.AudioBuffer?.prototype;
  if (fp.audioContext?.enabled && audioBuffer?.getChannelData) {{
    const nativeGetChannelData = audioBuffer.getChannelData;
    const altered = new WeakSet();
    Object.defineProperty(audioBuffer, 'getChannelData', {{ configurable: true, value: function() {{
      const samples = nativeGetChannelData.apply(this, arguments);
      if (!altered.has(samples)) {{
        altered.add(samples);
        const stride = Math.max(32, Number(fp.audioContext.interval) || 100);
        const delta = (0.5 + audioSeed) * 1e-7;
        for (let index = Math.floor(audioSeed * stride); index < samples.length; index += stride)
          samples[index] += samples[index] < 0 ? -delta : delta;
      }}
      return samples;
    }} }});
  }}
  const analyser = globalThis.AnalyserNode?.prototype;
  if (fp.audioContext?.enabled && analyser?.getFloatFrequencyData) {{
    const nativeFrequencyData = analyser.getFloatFrequencyData;
    Object.defineProperty(analyser, 'getFloatFrequencyData', {{ configurable: true, value: function(array) {{
      const result = nativeFrequencyData.apply(this, arguments);
      const delta = (0.5 + audioSeed) * 1e-7;
      for (let index = 0; index < array.length; index += 100) array[index] += delta;
      return result;
    }} }});
  }}

  const fontSet = globalThis.FontFaceSet?.prototype;
  if (fp.fonts?.enabled && fontSet?.check) {{
    const nativeFontCheck = fontSet.check;
    const available = new Set((fp.fonts.localCatalog || []).filter(name => !(fp.fonts.disabled || []).includes(name)));
    Object.defineProperty(fontSet, 'check', {{ configurable: true, value: function(font, text) {{
      const match = String(font || '').match(/(?:^|\s)(?:['\"])([^'\"]+)(?:['\"])(?:\s|$)/);
      if (match) return available.has(match[1]);
      return nativeFontCheck.apply(this, arguments);
    }} }});
  }}

  if (navigator.mediaDevices?.enumerateDevices && fp.mediaDevices) {{
    const devices = (fp.mediaDevices.devices || []).map(item => Object.freeze({{
      deviceId: item.deviceId || '', groupId: item.groupId || '', kind: item.deviceType || 'audioinput', label: item.label || '',
      toJSON() {{ return {{ deviceId: this.deviceId, groupId: this.groupId, kind: this.kind, label: this.label }}; }}
    }}));
    try {{ Object.defineProperty(Object.getPrototypeOf(navigator.mediaDevices), 'enumerateDevices', {{ configurable: true, value: async () => devices.slice() }}); }} catch (_) {{}}
  }}

  if (globalThis.speechSynthesis && fp.speechSynthesis?.enabled) {{
    const voices = (fp.speechSynthesis.voices || []).map(item => Object.freeze({{
      default: Boolean(item.default), lang: item.lang || '', localService: item.localService !== false,
      name: item.name || '', voiceURI: item.voiceURI || item.name || ''
    }}));
    try {{ Object.defineProperty(Object.getPrototypeOf(globalThis.speechSynthesis), 'getVoices', {{ configurable: true, value: () => voices.slice() }}); }} catch (_) {{}}
  }}
}})();
""".strip()


def chromium_cdp_commands(
    fingerprint: Mapping[str, Any] | None,
    *,
    override_user_agent: bool = True,
) -> list[tuple[str, dict[str, Any]]]:
    """Return native CDP overrides plus an early document preload script."""
    if not fingerprint:
        return []
    generated = fingerprint.get("chromium_cdp_commands")
    if isinstance(generated, list):
        commands: list[tuple[str, dict[str, Any]]] = []
        for item in generated:
            if not isinstance(item, Mapping):
                continue
            method = str(item.get("method") or "")
            params = item.get("params")
            if not method or not isinstance(params, Mapping):
                continue
            if not override_user_agent and method == "Network.setUserAgentOverride":
                continue
            normalized_params = dict(params)
            if method == "Network.setUserAgentOverride":
                normalized_params["acceptLanguage"] = ",".join(fingerprint_languages(fingerprint))
            elif method == "Emulation.setTimezoneOverride" and fingerprint.get("timezone"):
                normalized_params["timezoneId"] = str(fingerprint["timezone"])
            elif method == "Emulation.setLocaleOverride" and fingerprint.get("lang"):
                normalized_params["locale"] = str(fingerprint["lang"])
            commands.append((method, normalized_params))
        if commands:
            preload = _navigator_preload_script(fingerprint)
            commands.append(("Page.addScriptToEvaluateOnNewDocument", {"source": preload}))
            commands.append(("Runtime.evaluate", {"expression": preload}))
            return commands
    profile = fingerprint.get("profile") if isinstance(fingerprint.get("profile"), Mapping) else {}
    engine = profile.get("engine") if isinstance(profile.get("engine"), Mapping) else {}
    navigator = profile.get("navigator") if isinstance(profile.get("navigator"), Mapping) else {}
    screen = profile.get("screen") if isinstance(profile.get("screen"), Mapping) else {}
    runtime = profile.get("runtime") if isinstance(profile.get("runtime"), Mapping) else {}
    commands: list[tuple[str, dict[str, Any]]] = [
        ("Page.addScriptToEvaluateOnNewDocument", {"source": _navigator_preload_script(fingerprint)}),
        ("Runtime.evaluate", {"expression": _navigator_preload_script(fingerprint)}),
    ]
    timezone = str(fingerprint.get("timezone") or "")
    if timezone:
        commands.append(("Emulation.setTimezoneOverride", {"timezoneId": timezone}))
    locale = str(fingerprint.get("lang") or "")
    if locale:
        commands.append(("Emulation.setLocaleOverride", {"locale": locale}))
    if screen:
        commands.append(
            (
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": int(screen.get("width") or 1440),
                    "height": int(screen.get("height") or 900),
                    "deviceScaleFactor": float(screen.get("devicePixelRatio") or 1),
                    "mobile": bool(navigator.get("mobile")),
                    "screenWidth": int(screen.get("width") or 1440),
                    "screenHeight": int(screen.get("height") or 900),
                },
            )
        )
    commands.append(
        (
            "Emulation.setHardwareConcurrencyOverride",
            {"hardwareConcurrency": int(fingerprint.get("hardware_concurrency") or 8)},
        )
    )
    touch_points = int(fingerprint.get("max_touch_points") or 0)
    touch_params: dict[str, Any] = {"enabled": touch_points > 0}
    if touch_points > 0:
        touch_params["maxTouchPoints"] = touch_points
    commands.append(("Emulation.setTouchEmulationEnabled", touch_params))
    color_scheme = str(runtime.get("colorScheme") or "system")
    if color_scheme in {"light", "dark"}:
        commands.append(("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": color_scheme}]}))
    if override_user_agent:
        params: dict[str, Any] = {
            "userAgent": str(fingerprint.get("user_agent") or ""),
            "acceptLanguage": ",".join(fingerprint_languages(fingerprint)),
            "platform": str(fingerprint.get("platform") or ""),
        }
        metadata = engine.get("userAgentMetadata")
        if isinstance(metadata, Mapping):
            params["userAgentMetadata"] = dict(metadata)
        commands.append(("Network.setUserAgentOverride", params))
    return commands


def apply_chromium_fingerprint(
    driver: Any,
    fingerprint: Mapping[str, Any] | None,
    *,
    override_user_agent: bool = True,
) -> list[str]:
    """Apply every supported CDP command independently and report failures."""
    failures: list[str] = []
    for method, params in chromium_cdp_commands(fingerprint, override_user_agent=override_user_agent):
        try:
            driver.execute_cdp_cmd(method, params)
        except Exception as error:  # the browser version decides which CDP methods exist
            failures.append(f"{method}: {error}")
    return failures
