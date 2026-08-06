"""Shared adapter for the extracted browser fingerprint generator.

The adapter is deliberately opt-in.  Existing registration flows keep their
current behaviour unless ``OAI_FINGERPRINT_ENABLED`` (or a scope-specific
equivalent) is enabled.  Secrets are never accepted on the command line: the
optional authorized provider only receives a path to a mode-0600 headers file.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SDK_CANDIDATES = (
    ROOT / "fingerprint" / "sdk",
    Path("/opt/automyai/fingerprint/sdk"),
    Path("/app/fingerprint/sdk"),
)
MANAGED_OPENAI3_PRESET = "windows-11-chrome"
MANAGED_OPENAI3_BROWSER_VERSION = "150.0.0.0"


class FingerprintError(RuntimeError):
    """Raised when strict fingerprint generation cannot be completed."""


def _bool(value: Any, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _scope_prefix(scope: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in scope.upper()).strip("_")


def _load_app_config() -> dict[str, Any]:
    candidates = []
    configured = os.getenv("AUTOMYAI_CONFIG", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend((ROOT / "config.json", Path("/opt/automyai/config.json"), Path("/app/config.json")))
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            data = json.loads(resolved.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError, TypeError):
            continue
    return {}


def _setting(config: Mapping[str, Any], scope: str, name: str, default: Any = "") -> Any:
    prefix = _scope_prefix(scope)
    keys = ([f"{prefix}_FINGERPRINT_{name}"] if prefix else []) + [f"OAI_FINGERPRINT_{name}"]
    for key in keys:
        value = os.getenv(key)
        if value is not None and value != "":
            return value
    for key in keys:
        value = config.get(key)
        if value is not None and value != "":
            return value
    return default


def _find_sdk_dir(config: Mapping[str, Any], scope: str) -> Path:
    configured = str(_setting(config, scope, "SDK_DIR", "")).strip()
    candidates = ([Path(configured).expanduser()] if configured else []) + list(DEFAULT_SDK_CANDIDATES)
    for path in candidates:
        if (path / "cli.mjs").is_file():
            return path.resolve()
    raise FingerprintError("fingerprint SDK was not found")


def _read_api_key(config: Mapping[str, Any], scope: str) -> str:
    """Read the local fingerprint API key without placing it on a command line."""
    prefix = _scope_prefix(scope)
    environment_keys = ([f"{prefix}_FINGERPRINT_API_KEY"] if prefix else []) + [
        "OAI_FINGERPRINT_API_KEY",
        "FINGERPRINT_API_KEY",
    ]
    for name in environment_keys:
        value = os.getenv(name, "").strip()
        if value:
            return value

    configured = str(_setting(config, scope, "API_KEY_FILE", os.getenv("FINGERPRINT_API_KEY_FILE", ""))).strip()
    if not configured:
        raise FingerprintError("fingerprint API key is not configured")
    path = Path(configured).expanduser()
    if not path.is_file():
        raise FingerprintError("fingerprint API key file does not exist")
    if path.stat().st_mode & 0o077:
        raise FingerprintError("fingerprint API key file permissions must be 0600")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise FingerprintError("fingerprint API key file is empty")
    return value


def _api_request_json(url: str, *, timeout: float, token: str = "") -> Any:
    headers = {"Accept": "application/json"}
    if token:
        headers["token"] = token
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(2 * 1024 * 1024)
    except HTTPError as error:
        if error.code in {401, 403}:
            raise FingerprintError("fingerprint API key was rejected") from error
        raise FingerprintError("fingerprint API request failed") from error
    except (URLError, TimeoutError, OSError) as error:
        raise FingerprintError("fingerprint API is unavailable") from error
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FingerprintError("fingerprint API returned invalid JSON") from error


def _local_api_generate_oai(
    config: Mapping[str, Any],
    scope: str,
    *,
    base_url: str,
    timeout: float,
    preset: str,
    seed: str,
    browser_version: str,
    source: str,
) -> Mapping[str, Any]:
    token = _read_api_key(config, scope)
    payload: dict[str, Any] = {
        "entry": scope,
        "preset": preset,
        "seed": seed,
        "source": source,
    }
    if browser_version:
        payload["browserVersion"] = browser_version
    request = Request(
        f"{base_url.rstrip('/')}/oai/fingerprint/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json", "token": token},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    except HTTPError as error:
        if error.code in {401, 403}:
            raise FingerprintError("fingerprint API key was rejected") from error
        raise FingerprintError("fingerprint API generation failed") from error
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FingerprintError("fingerprint API generation failed") from error
    if not isinstance(result, Mapping):
        raise FingerprintError("fingerprint API returned invalid JSON")
    code = result.get("code")
    if code is not None and str(code) not in {"0", "200"}:
        raise FingerprintError("fingerprint API generation failed")
    fingerprint = result.get("data", result)
    if not isinstance(fingerprint, Mapping):
        raise FingerprintError("fingerprint API returned no OAI profile")
    if fingerprint.get("entry") != scope or fingerprint.get("source") != "automyai-fingerprint-api":
        raise FingerprintError("fingerprint API returned an unexpected OAI profile")
    return fingerprint


def _local_api_attestation(config: Mapping[str, Any], scope: str, timeout: float) -> dict[str, Any]:
    base_url = str(_setting(config, scope, "API_URL", "http://127.0.0.1:50001")).strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise FingerprintError("fingerprint API URL must use a loopback host")

    health = _api_request_json(f"{base_url}/health", timeout=timeout)
    health_data = health.get("data") if isinstance(health, Mapping) else None
    if not isinstance(health_data, Mapping):
        health_data = health if isinstance(health, Mapping) else {}
    if health_data.get("service") != "automyai-fingerprint-api":
        raise FingerprintError("unexpected fingerprint API service")
    api_key = _read_api_key(config, scope)
    workspace = _api_request_json(f"{base_url}/browser/workspace", timeout=timeout, token=api_key)
    if isinstance(workspace, Mapping):
        code = workspace.get("code")
        if code is not None and str(code) not in {"0", "200"}:
            raise FingerprintError("fingerprint API key was rejected")
        if workspace.get("success") is False:
            raise FingerprintError("fingerprint API key was rejected")
    return {
        "provider": "local-api",
        "api_url": base_url,
        "verified": True,
        "status": "local-key-accepted",
        "authority": "local-api",
        "official": False,
        "endpoint": "/browser/workspace",
    }


def _seed(scope: str, configured_seed: str, requested_seed: str | None) -> str:
    base = str(requested_seed or configured_seed or "").strip()
    if not base:
        base = secrets.token_hex(16)
    return f"automyai:{scope}:{base}" if scope else f"automyai:{base}"


def _major(version: Any) -> str:
    return str(version or "").split(".", 1)[0]


def _impersonate(profile: Mapping[str, Any]) -> str:
    engine = profile.get("engine") if isinstance(profile.get("engine"), Mapping) else {}
    navigator = profile.get("navigator") if isinstance(profile.get("navigator"), Mapping) else {}
    operating_system = profile.get("os") if isinstance(profile.get("os"), Mapping) else {}
    family = str(engine.get("family") or "").lower()
    major = _major(engine.get("version"))
    mobile = bool(navigator.get("mobile"))
    if str(operating_system.get("name") or "").lower() == "ios":
        return "safari_ios"
    if family == "firefox":
        return f"firefox{major}" if major in {"133", "135", "144", "147"} else "firefox"
    if mobile:
        return "chrome131_android" if major == "131" else "chrome_android"
    if major in {"99", "100", "101", "104", "107", "110", "116", "119", "120", "123", "124", "131", "136", "142", "145", "146"}:
        return f"chrome{major}"
    return "chrome"


def _impersonate_candidates(profile: Mapping[str, Any], primary: str) -> list[str]:
    engine = profile.get("engine") if isinstance(profile.get("engine"), Mapping) else {}
    family = str(engine.get("family") or "").lower()
    if primary == "safari_ios":
        generic = "safari_ios"
    else:
        generic = "firefox" if family == "firefox" else ("chrome_android" if "android" in primary else "chrome")
    return list(dict.fromkeys((primary, generic)))


def _client_hints(metadata: Any) -> tuple[str, str, str]:
    if not isinstance(metadata, Mapping):
        return "", "", ""
    brands = metadata.get("brands")
    values = []
    if isinstance(brands, list):
        for item in brands:
            if isinstance(item, Mapping) and item.get("brand") and item.get("version") is not None:
                brand = str(item["brand"]).replace('"', "")
                version = str(item["version"]).replace('"', "")
                values.append(f'"{brand}";v="{version}"')
    platform = str(metadata.get("platform") or "").replace('"', "")
    return ", ".join(values), (f'"{platform}"' if platform else ""), "?1" if metadata.get("mobile") else "?0"


def _language_list(accept_language: str, primary: str) -> list[str]:
    values: list[str] = []
    for item in str(accept_language or "").split(","):
        value = item.split(";", 1)[0].strip()
        if value and value not in values:
            values.append(value)
    if primary and primary not in values:
        values.insert(0, primary)
    return values or ([primary] if primary else ["en-US", "en"])


def normalize_bundle(bundle: Mapping[str, Any], *, source: str) -> dict[str, Any]:
    """Map a full SDK bundle to legacy OAI fields while retaining the profile."""
    profile = bundle.get("profile") if isinstance(bundle.get("profile"), Mapping) else bundle
    if not isinstance(profile, Mapping):
        raise FingerprintError("fingerprint generator returned no profile")
    engine = profile.get("engine") if isinstance(profile.get("engine"), Mapping) else {}
    locale = profile.get("locale") if isinstance(profile.get("locale"), Mapping) else {}
    navigator = profile.get("navigator") if isinstance(profile.get("navigator"), Mapping) else {}
    screen = profile.get("screen") if isinstance(profile.get("screen"), Mapping) else {}
    machine = profile.get("machine") if isinstance(profile.get("machine"), Mapping) else {}
    graphics = profile.get("graphics") if isinstance(profile.get("graphics"), Mapping) else {}
    generator = profile.get("generator") if isinstance(profile.get("generator"), Mapping) else {}
    user_agent = str(engine.get("userAgent") or "")
    if not user_agent:
        raise FingerprintError("fingerprint profile has no user agent")
    sec_ch_ua, sec_ch_platform, sec_ch_mobile = _client_hints(engine.get("userAgentMetadata"))
    width = int(screen.get("width") or 0)
    height = int(screen.get("height") or 0)
    primary_language = str(locale.get("appLocale") or "en-US")
    accept_language = str(locale.get("acceptLanguage") or primary_language)
    profile_id = str(profile.get("id") or hashlib.sha256(user_agent.encode()).hexdigest()[:16])
    stable_device_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"automyai-fingerprint:{profile_id}"))
    impersonate = _impersonate(profile)
    return {
        "impersonate": impersonate,
        "impersonate_candidates": _impersonate_candidates(profile, impersonate),
        "user_agent": user_agent,
        "sec_ch_ua": sec_ch_ua,
        "sec_ch_ua_platform": sec_ch_platform,
        "sec_ch_ua_mobile": sec_ch_mobile,
        "screen": f"{width}x{height}" if width and height else "1920x1080",
        "screen_width": width or 1920,
        "screen_height": height or 1080,
        "screen_avail_width": int(screen.get("availWidth") or width or 1920),
        "screen_avail_height": int(screen.get("availHeight") or height or 1080),
        "device_pixel_ratio": float(screen.get("devicePixelRatio") or 1),
        "lang": primary_language,
        "lang_full": accept_language,
        "languages": _language_list(accept_language, primary_language),
        "timezone": str(locale.get("timezone") or ""),
        "platform": str(navigator.get("platform") or ""),
        "hardware_concurrency": int(navigator.get("hardwareConcurrency") or 8),
        "device_memory": float(navigator.get("deviceMemory") or 8),
        "max_touch_points": int(navigator.get("maxTouchPoints") or 0),
        "mobile": bool(navigator.get("mobile")),
        "do_not_track": "1" if navigator.get("doNotTrack") else None,
        "device_name": str(machine.get("computerName") or ""),
        "webgl_vendor": str(graphics.get("webglVendor") or ""),
        "webgl_renderer": str(graphics.get("webglRenderer") or ""),
        "base_data_source": str(generator.get("baseDataSource") or ""),
        "generator_provider": str(generator.get("provider") or ""),
        "cloud_base_records": generator.get("baseDataSource") == "authorized-provider",
        "device_id": stable_device_id,
        "profile_id": profile_id,
        "seed": str(profile.get("seed") or ""),
        "preset": str(profile.get("preset") or ""),
        "source": source,
        "profile": dict(profile),
        "roxy_config": bundle.get("roxyConfig") if isinstance(bundle.get("roxyConfig"), Mapping) else {},
        "runtime_config": bundle.get("runtimeConfig") if isinstance(bundle.get("runtimeConfig"), Mapping) else {},
    }


def generate_oai_fingerprint(
    *,
    scope: str,
    default_preset: str,
    default_browser_version: str,
    seed: str | None = None,
) -> dict[str, Any] | None:
    """Generate one coherent profile, or return ``None`` when disabled/fallback.

    Supported providers:

    - ``local``: fully offline recovered algorithm.
    - ``authorized-http``: the three original base-record endpoints, available
      only with an officially issued API prefix and a protected headers file.
    - ``local-api``: use the loopback AutoMyAI fingerprint API.
    """
    config = _load_app_config()
    if not _bool(_setting(config, scope, "ENABLED", "false")):
        return None
    strict = _bool(_setting(config, scope, "STRICT", "false"))
    try:
        preset = str(_setting(config, scope, "PRESET", default_preset)).strip() or default_preset
        browser_version = str(_setting(config, scope, "BROWSER_VERSION", default_browser_version)).strip()
        provider = str(_setting(config, scope, "PROVIDER", "local")).strip().lower()
        requested_source = str(_setting(config, scope, "SOURCE", "local")).strip().lower() or "local"
        if requested_source not in {"local", "cloud"}:
            raise FingerprintError("fingerprint source must be local or cloud")
        selected_seed = _seed(scope, str(_setting(config, scope, "SEED", "")), seed)
        managed_openai3_chrome = scope in {"openai3", "chatgpt_register"}
        if managed_openai3_chrome:
            # Do not rotate browser families or browser versions inside a
            # registration. The captured browser contract is Win/Chrome 150.
            preset = MANAGED_OPENAI3_PRESET
            browser_version = MANAGED_OPENAI3_BROWSER_VERSION
        timeout = max(1.0, min(float(_setting(config, scope, "TIMEOUT_SECONDS", "15")), 120.0))
        if provider == "local-api":
            try:
                provenance = _local_api_attestation(config, scope, min(timeout, 10.0))
                result = dict(_local_api_generate_oai(
                    config,
                    scope,
                    base_url=str(provenance["api_url"]),
                    timeout=timeout,
                    preset=preset,
                    seed=selected_seed,
                    browser_version=browser_version,
                    source=requested_source,
                ))
                result["provenance"] = result.get("provenance") or provenance
                return result
            except FingerprintError as error:
                if strict:
                    raise
                provenance = {
                    "provider": provider,
                    "verified": False,
                    "status": "local-fallback",
                    "reason": str(error),
                }
                source = "automyai-fingerprint-local-fallback"
                provider = "local"
        else:
            provenance = None
            source = "automyai-fingerprint-local"

        sdk_dir = _find_sdk_dir(config, scope)
        node = str(_setting(config, scope, "NODE", "node")).strip() or "node"
        if not (Path(node).is_file() or shutil.which(node)):
            raise FingerprintError("Node.js is not available")
        command = [node, str(sdk_dir / "cli.mjs")]
        if provider == "local":
            command.append("generate")
        elif provider == "authorized-http":
            base_url = str(_setting(config, scope, "AUTHORIZED_API_BASE_URL", "")).strip()
            headers_file = str(_setting(config, scope, "AUTHORIZED_HEADERS_FILE", "")).strip()
            if not base_url or not headers_file:
                raise FingerprintError("authorized-http requires an API base URL and headers file")
            header_path = Path(headers_file).expanduser()
            if not header_path.is_file():
                raise FingerprintError("authorized headers file does not exist")
            if header_path.stat().st_mode & 0o077:
                raise FingerprintError("authorized headers file permissions must be 0600")
            command.extend(("generate-cloud", "--base-url", base_url, "--headers-file", str(header_path)))
            source = "roxybrowser-3.9.2-authorized-http"
        else:
            raise FingerprintError(f"unsupported fingerprint provider: {provider}")
        command.extend(("--preset", preset, "--seed", selected_seed, "--format", "bundle"))
        if browser_version:
            command.extend(("--browser-version", browser_version))
        child_env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR"}
        }
        child_env["NO_COLOR"] = "1"
        completed = subprocess.run(
            command,
            cwd=sdk_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=child_env,
        )
        if completed.returncode != 0:
            raise FingerprintError("fingerprint generator failed")
        bundle = json.loads(completed.stdout)
        if not isinstance(bundle, Mapping):
            raise FingerprintError("fingerprint generator returned invalid JSON")
        normalized = normalize_bundle(bundle, source=source)
        if provenance is not None:
            normalized["provenance"] = provenance
        return normalized
    except (FingerprintError, OSError, ValueError, TypeError, subprocess.SubprocessError, json.JSONDecodeError):
        if strict:
            raise
        return None


def browser_headers(fingerprint: Mapping[str, Any] | None) -> dict[str, str]:
    if not fingerprint:
        return {}
    generated = fingerprint.get("http_headers")
    if isinstance(generated, Mapping):
        return {
            str(key): str(value)
            for key, value in generated.items()
            if str(key) and value not in (None, "")
        }
    headers = {
        "User-Agent": str(fingerprint.get("user_agent") or ""),
        "Accept-Language": str(fingerprint.get("lang_full") or ""),
    }
    if fingerprint.get("sec_ch_ua"):
        headers.update({
            "sec-ch-ua": str(fingerprint["sec_ch_ua"]),
            "sec-ch-ua-mobile": str(fingerprint.get("sec_ch_ua_mobile") or "?0"),
            "sec-ch-ua-platform": str(fingerprint.get("sec_ch_ua_platform") or ""),
        })
    if fingerprint.get("do_not_track"):
        headers["DNT"] = str(fingerprint["do_not_track"])
    return {key: value for key, value in headers.items() if value}
