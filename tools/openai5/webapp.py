#!/usr/bin/env python3
"""OpenAI5 API-only desktop environment supervisor.

This service performs read-only environment diagnostics.  It deliberately does
not submit signup forms, rotate accounts, solve challenges, or fall back to a
locally generated fingerprint profile.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

import sys

if "/opt/automyai" not in sys.path:
    sys.path.insert(0, "/opt/automyai")
from integrations.proxy_config import parse_proxy_url, proxy_url_from_parsed


ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("OPENAI5_DATA_DIR", "/opt/automyai/data/openai5"))
CONFIG_FILE = DATA / "config.json"
STATE_FILE = DATA / "state.json"
LOG_DIR = DATA / "logs"
API_KEY_FILE = Path(os.environ.get("FINGERPRINT_API_KEY_FILE", "/opt/automyai/data/fingerprint-api/api.key"))
DEFAULT_API_URL = os.environ.get("OPENAI5_FINGERPRINT_API_URL", "http://127.0.0.1:50001").rstrip("/")
DEFAULT_TARGETS = ("https://auth.openai.com/", "https://chatgpt.com/")
DESKTOP_PRESETS = {"windows-10-chrome", "windows-11-chrome", "macos-intel-chrome", "macos-apple-chrome"}

for path in (DATA, LOG_DIR):
    path.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="OpenAI5 Environment Supervisor", docs_url=None, redoc_url=None)
_lock = threading.RLock()
_stop = threading.Event()
_worker: threading.Thread | None = None
_logs: list[dict[str, str]] = []
_state: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "run_id": "",
    "current_node": "",
    "node_statuses": {},
    "started_at": "",
    "finished_at": "",
    "updated_at": "",
    "error": "",
    "summary": {},
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _append(message: str, level: str = "info") -> None:
    entry = {"time": _now(), "level": level, "message": str(message)}
    with _lock:
        _logs.append(entry)
        del _logs[:-1000]
        run_id = str(_state.get("run_id") or "")
    if run_id:
        try:
            with (LOG_DIR / f"{run_id}.log").open("a", encoding="utf-8") as handle:
                handle.write(f"[{entry['time']}] {level.upper()} {message}\n")
        except OSError:
            pass


def _save_state() -> None:
    try:
        STATE_FILE.write_text(json.dumps(_state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _public_proxy(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if not parsed.hostname:
        return ""
    scheme = parsed.scheme or "http"
    port = f":{parsed.port}" if parsed.port else ""
    auth = "***:***@" if parsed.username or parsed.password else ""
    return f"{scheme}://{auth}{parsed.hostname}{port}"


def _default_config() -> dict[str, Any]:
    return {
        "fingerprint_api_url": DEFAULT_API_URL,
        "require_authorized_cloud": True,
        "desktop_only": True,
        # Empty means inherit the project CLI/Cliproxy gateway.  Credentials
        # are read server-side and never returned by the API.
        "proxy_url": "",
        "targets": list(DEFAULT_TARGETS),
        "attempts": 3,
        "timeout_seconds": 12,
    }


def _load_config() -> dict[str, Any]:
    result = _default_config()
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            result.update({key: value for key, value in raw.items() if key in result})
    except (OSError, ValueError, TypeError):
        pass
    result["fingerprint_api_url"] = DEFAULT_API_URL
    result["require_authorized_cloud"] = True
    result["desktop_only"] = True
    return result


def _project_config() -> dict[str, Any]:
    candidates = [
        DATA.parent / "openai4" / "config.json",
        Path(os.environ.get("AUTOMYAI_CONFIG", "/opt/automyai/config.json")),
        Path("/opt/automyai/config.json"),
    ]
    for path in candidates:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, ValueError, TypeError):
            continue
    return {}


def _effective_proxy(config: dict[str, Any] | None = None) -> tuple[str, str]:
    configured = str((config or {}).get("proxy_url") or "").strip()
    if configured and "***" not in configured:
        return configured, "openai5-config"
    project = _project_config()
    # OpenAI5 should compare against OpenAI4 using the exact same saved proxy
    # first.  This prevents a global JP Cliproxy value from silently replacing
    # OpenAI4's per-service VN gateway.
    try:
        openai4 = json.loads((DATA.parent / "openai4" / "config.json").read_text(encoding="utf-8"))
        exact = str(openai4.get("custom_proxy_url") or "").strip()
        parsed = parse_proxy_url(exact)
        if parsed:
            return proxy_url_from_parsed(parsed), "openai4-config"
    except (OSError, ValueError, TypeError):
        pass
    for key, label in (
        ("CLIPROXY_PROXY_URL", "project-cliproxy"),
        ("SIGNUP_PROXY_CUSTOM_URL", "project-signup-proxy"),
        ("UC_SIGNUP_PROXY", "project-uc-proxy"),
        ("BROWSER_PROXY", "project-browser-proxy"),
    ):
        value = str(project.get(key) or os.environ.get(key) or "").strip()
        if value and "***" not in value and urlparse(value if "://" in value else "http://" + value).hostname:
            return value, label
    return "", "direct"


def _public_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    result = dict(config or _load_config())
    raw_proxy, proxy_source = _effective_proxy(result)
    result["proxy_url"] = _public_proxy(raw_proxy) if raw_proxy else ""
    result["resolved_proxy"] = _public_proxy(raw_proxy)
    result["proxy_source"] = proxy_source
    result["mode"] = "api-only"
    result["fallback"] = "disabled"
    return result


class ConfigReq(BaseModel):
    proxy_url: str = ""
    targets: list[str] = Field(default_factory=lambda: list(DEFAULT_TARGETS), min_length=1, max_length=4)
    attempts: int = Field(3, ge=1, le=3)
    timeout_seconds: int = Field(12, ge=3, le=30)

    @field_validator("targets")
    @classmethod
    def validate_targets(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        allowed = {"auth.openai.com", "chatgpt.com", "www.chatgpt.com", "openai.com", "www.openai.com"}
        for value in values:
            parsed = urlparse(str(value or "").strip())
            if parsed.scheme != "https" or (parsed.hostname or "").lower() not in allowed:
                raise ValueError("targets only allow official OpenAI HTTPS hosts")
            normalized.append(parsed.geturl())
        return list(dict.fromkeys(normalized))


def _save_config(payload: dict[str, Any]) -> dict[str, Any]:
    config = _load_config()
    next_payload = dict(payload)
    if "***" in str(next_payload.get("proxy_url") or ""):
        next_payload.pop("proxy_url", None)
    config.update(next_payload)
    config["fingerprint_api_url"] = DEFAULT_API_URL
    config["require_authorized_cloud"] = True
    config["desktop_only"] = True
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(CONFIG_FILE, 0o600)
    return config


def _api_key() -> str:
    try:
        if API_KEY_FILE.stat().st_mode & 0o077:
            raise RuntimeError("指纹 API key 文件权限必须为 0600")
        value = API_KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeError(f"读取指纹 API key 失败: {error}") from error
    if not value:
        raise RuntimeError("指纹 API key 为空")
    return value


def _json_request(url: str, *, token: str = "", timeout: int = 10) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "automyai-openai5-supervisor/1.0"}
    if token:
        headers["token"] = token
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    except HTTPError as error:
        raise RuntimeError(f"HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError(f"连接失败: {error.reason}") from error
    except (ValueError, UnicodeDecodeError) as error:
        raise RuntimeError("返回不是有效 JSON") from error
    if not isinstance(payload, dict):
        raise RuntimeError("返回结构不是 JSON object")
    if payload.get("code") not in (None, 0, 200):
        raise RuntimeError(str(payload.get("msg") or "API 返回失败"))
    return payload


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data", payload)
    return value if isinstance(value, dict) else {}


def _generate_api_profile(base: str, token: str, timeout: int) -> dict[str, Any]:
    request = Request(
        base + "/oai/fingerprint/generate",
        data=json.dumps({
            "entry": "uc_signup",
            "preset": "windows-11-chrome",
            "seed": "openai5-diagnostic",
            "source": "local",
        }).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json", "token": token},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    except HTTPError as error:
        raise RuntimeError(f"HTTP {error.code}") from error
    except (URLError, ValueError, UnicodeDecodeError) as error:
        raise RuntimeError(f"指纹 API 生成失败: {error}") from error
    profile = _data(payload)
    provenance = profile.get("provenance") if isinstance(profile.get("provenance"), dict) else {}
    if profile.get("source") != "automyai-fingerprint-api" or provenance.get("provider") not in {"local-api", "authorized-cloud"}:
        raise RuntimeError("实际生成结果不是 API 指纹来源")
    if profile.get("mobile") is True:
        raise RuntimeError("API 返回了移动端配置，OpenAI5 仅接受桌面配置")
    required = ("user_agent", "profile_id", "platform", "screen_width", "screen_height")
    missing = [name for name in required if not profile.get(name)]
    if missing:
        raise RuntimeError(f"API 指纹字段不完整: {', '.join(missing)}")
    return {
        "source": profile.get("source"),
        "provenance": provenance,
        "profile_id": profile.get("profile_id"),
        "preset": profile.get("preset"),
        "user_agent": profile.get("user_agent"),
        "mobile": profile.get("mobile"),
        "platform": profile.get("platform"),
        "screen": f"{profile.get('screen_width')}x{profile.get('screen_height')}",
    }


def _classify(error: Exception) -> str:
    text = str(error).lower()
    if "407" in text or "proxy authentication" in text:
        return "proxy_auth"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "certificate" in text or "ssl" in text or "tls" in text:
        return "tls"
    if "name or service" in text or "nodename" in text or "dns" in text:
        return "dns"
    if "401" in text or "403" in text:
        return "api_auth"
    if "connection" in text or "connect" in text:
        return "connection"
    return "unknown"


def _retry(label: str, attempts: int, action):
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        if _stop.is_set():
            raise RuntimeError("用户停止")
        try:
            return action()
        except Exception as error:  # diagnostics must preserve the last reason
            last = error
            kind = _classify(error)
            _append(f"{label} 第 {attempt}/{attempts} 次失败 [{kind}]: {error}", "warn")
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 4))
    assert last is not None
    raise last


def _target_probe(target: str, proxy: str, timeout: int) -> dict[str, Any]:
    handler = ProxyHandler({"http": proxy, "https": proxy}) if proxy else ProxyHandler({})
    request = Request(target, method="GET", headers={"User-Agent": "automyai-openai5-connectivity/1.0"})
    try:
        with build_opener(handler).open(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0) or 0)
            final_url = str(getattr(response, "url", target) or target)
    except HTTPError as error:
        status = int(error.code or 0)
        final_url = str(getattr(error, "url", target) or target)
        if status >= 500:
            raise RuntimeError(f"HTTP {status}") from error
    return {"target": target, "status": status, "final_host": urlparse(final_url).hostname or ""}


NODES = (
    "fingerprint_api_health",
    "fingerprint_api_auth",
    "authorized_cloud_source",
    "desktop_presets",
    "target_connectivity",
)


def _set_node(node: str, status: str) -> None:
    with _lock:
        _state["current_node"] = node
        statuses = dict(_state.get("node_statuses") or {})
        statuses[node] = status
        _state["node_statuses"] = statuses
        _state["updated_at"] = _now()
    _save_state()


def _run(run_id: str, config: dict[str, Any]) -> None:
    summary: dict[str, Any] = {"mode": "api-only", "desktop_only": True, "fallback": "disabled"}
    try:
        attempts = int(config["attempts"])
        timeout = int(config["timeout_seconds"])
        base = str(config["fingerprint_api_url"]).rstrip("/")

        _set_node("fingerprint_api_health", "running")
        health = _retry("指纹 API 健康检查", attempts, lambda: _data(_json_request(base + "/health", timeout=timeout)))
        if health.get("service") != "automyai-fingerprint-api" or not health.get("sdkAvailable"):
            raise RuntimeError("指纹 API 服务标识异常或 SDK 不可用")
        _set_node("fingerprint_api_health", "completed")

        token = _api_key()
        _set_node("fingerprint_api_auth", "running")
        workspace = _retry("指纹 API 鉴权", attempts, lambda: _data(_json_request(base + "/browser/workspace", token=token, timeout=timeout)))
        if not isinstance(workspace.get("rows"), list):
            raise RuntimeError("指纹 API 鉴权返回结构异常")
        _set_node("fingerprint_api_auth", "completed")

        _set_node("authorized_cloud_source", "running")
        profile = _retry("实际指纹 API 生成", attempts, lambda: _generate_api_profile(base, token, timeout))
        summary["fingerprint_source"] = profile
        _set_node("authorized_cloud_source", "completed")

        _set_node("desktop_presets", "running")
        preset_payload = _retry("桌面预设检查", attempts, lambda: _json_request(base + "/fingerprint/presets", token=token, timeout=timeout))
        preset_data = preset_payload.get("data")
        presets = [str(item) for item in preset_data] if isinstance(preset_data, list) else []
        desktop = sorted(DESKTOP_PRESETS.intersection(presets))
        if not desktop:
            raise RuntimeError("API 未提供 Windows/macOS Chrome 桌面预设")
        summary["desktop_presets"] = desktop
        _set_node("desktop_presets", "completed")

        _set_node("target_connectivity", "running")
        proxy, proxy_source = _effective_proxy(config)
        targets = []
        for target in config.get("targets") or DEFAULT_TARGETS:
            targets.append(_retry(f"目标连通 {urlparse(target).hostname}", attempts, lambda target=target: _target_probe(target, proxy, timeout)))
        summary["proxy"] = _public_proxy(proxy) if proxy else "direct"
        summary["proxy_source"] = proxy_source
        summary["targets"] = targets
        _set_node("target_connectivity", "completed")

        with _lock:
            _state.update({"running": False, "phase": "completed", "finished_at": _now(), "error": "", "summary": summary})
        _append("OpenAI5 环境诊断完成：API-only / desktop / 实际 API 指纹生成检查通过")
    except Exception as error:
        current = str(_state.get("current_node") or "")
        if current:
            _set_node(current, "stopped" if _stop.is_set() else "failed")
        with _lock:
            _state.update({
                "running": False,
                "phase": "stopped" if _stop.is_set() else "failed",
                "finished_at": _now(),
                "error": str(error),
                "summary": {**summary, "error_kind": _classify(error)},
            })
        _append(f"诊断结束: {error}", "warn" if _stop.is_set() else "error")
    finally:
        _save_state()


@app.get("/api/health")
def health():
    return {"ok": True, "service": "openai5", "mode": "api-only", "state": dict(_state)}


@app.get("/api/status")
def status():
    with _lock:
        state = dict(_state)
    return {"ok": True, "state": state, "config": _public_config()}


@app.get("/api/logs")
def logs(tail: int = 200):
    count = max(1, min(int(tail or 200), 1000))
    with _lock:
        items = list(_logs[-count:])
    return {"ok": True, "logs": items}


@app.get("/api/config")
def get_config():
    return {"ok": True, "config": _public_config()}


@app.post("/api/config")
def post_config(request: ConfigReq):
    config = _save_config(request.model_dump())
    return {"ok": True, "config": _public_config(config)}


@app.post("/api/preflight")
def preflight(request: ConfigReq):
    config = _save_config(request.model_dump())
    base = str(config["fingerprint_api_url"]).rstrip("/")
    try:
        health_data = _data(_json_request(base + "/health", timeout=int(config["timeout_seconds"])))
        token = _api_key()
        workspace = _data(_json_request(base + "/browser/workspace", token=token, timeout=int(config["timeout_seconds"])))
        profile = _generate_api_profile(base, token, int(config["timeout_seconds"]))
    except Exception as error:
        raise HTTPException(400, f"API-only 预检失败: {error}") from error
    ok = bool(
        health_data.get("service") == "automyai-fingerprint-api"
        and health_data.get("sdkAvailable")
        and isinstance(workspace.get("rows"), list)
        and profile.get("source") == "automyai-fingerprint-api"
    )
    result = {
        "ok": ok,
        "mode": "api-only",
        "fallback": "disabled",
        "desktop_only": True,
        "source": profile,
        "authenticated": isinstance(workspace.get("rows"), list),
        "proxy": _public_proxy(_effective_proxy(config)[0]),
        "proxy_source": _effective_proxy(config)[1],
    }
    if not ok:
        result["reason"] = "实际指纹生成结果未通过 API-only 桌面校验；本地 SDK 回退不会被使用"
    return result


@app.post("/api/start")
def start(request: ConfigReq):
    global _worker
    with _lock:
        if _state.get("running"):
            raise HTTPException(409, "诊断任务已在运行")
    config = _save_config(request.model_dump())
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    _stop.clear()
    with _lock:
        _state.update({
            "running": True,
            "phase": "running",
            "run_id": run_id,
            "current_node": NODES[0],
            "node_statuses": {node: "pending" for node in NODES},
            "started_at": _now(),
            "finished_at": "",
            "updated_at": _now(),
            "error": "",
            "summary": {},
        })
    _append("开始 OpenAI5 API-only 桌面环境诊断")
    _worker = threading.Thread(target=_run, args=(run_id, config), daemon=True)
    _worker.start()
    return {"ok": True, "state": dict(_state)}


@app.post("/api/stop")
def stop():
    _stop.set()
    _append("已请求停止诊断任务", "warn")
    return {"ok": True, "state": dict(_state)}


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse("<!doctype html><meta charset='utf-8'><title>OpenAI5</title><p>OpenAI5 API-only environment supervisor</p>")


def main() -> None:
    import uvicorn

    port = str(os.environ.get("OPENAI5_PORT") or "").strip()
    if not port.isdigit():
        raise SystemExit("OPENAI5_PORT must come from config/ports.env")
    uvicorn.run(app, host=os.environ.get("OPENAI5_HOST", "127.0.0.1"), port=int(port), log_level="info")


if __name__ == "__main__":
    main()
