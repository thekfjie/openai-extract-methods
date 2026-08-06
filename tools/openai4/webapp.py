#!/usr/bin/env python3
"""OpenAI 注册控制面 — UC 有头浏览器生产线（OpenAI4 内部服务名）。

- 代码: /opt/automyai/tools/openai4
- 数据: /opt/automyai/data/openai4
- 端口: config/ports.env OPENAI4_PORT
- 引擎: 主站 uc_signup（有头 Chromium + Xvfb），本服务只做控制/预检/日志编排
"""
from __future__ import annotations

import json
import hashlib
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

import sys

for _parent in Path(__file__).resolve().parents:
    if (_parent / "integrations" / "openai4_control.py").is_file():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

from integrations.openai4_control import (
    DEFAULT_MAIL_GROUPS,
    build_uc_start_payload,
    default_openai4_config,
    mail_failure_is_definitive,
    map_uc_state_to_openai4,
    normalize_openai4_mail_groups,
    sanitize_openai4_proxy_display,
    normalize_openai4_proxy_input,
    normalize_proxy_url,
    proxy_http_connect_fallback,
    public_proxy_url,
    resolve_openai4_proxy,
    source_group_requires_signup,
)
from integrations.opus_mail_admin_reader import OpusMailAdminReader
from integrations.proxy_config import MIHOMO_SUB2API_PROFILES, normalize_proxy_region

OPUS_PENDING_GROUP = "Mail Opus 待注册"
BEIJING_TZ = ZoneInfo("Asia/Shanghai")

# Optional traffic metering (same scheme as OpenAI3: wrap upstream only when enabled)
try:
    import sys as _sys
    from pathlib import Path as _Path
    _TM_ROOT = _Path(__file__).resolve().parents[1]
    if (_TM_ROOT / "traffic_meter").is_dir() and str(_TM_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_TM_ROOT))
    from traffic_meter import load_sessions, public_session, start_meter_for_proxy, stop_meter
except Exception:
    load_sessions = public_session = start_meter_for_proxy = stop_meter = None  # type: ignore


def _automyai_traffic_meter_default() -> bool:
    try:
        raw = json.loads(Path(os.environ.get("AUTOMYAI_CONFIG") or "/opt/automyai/config.json").read_text(encoding="utf-8"))
        val = str((raw or {}).get("TRAFFIC_METER_ENABLED") or "").strip().lower()
        return val in {"1", "true", "yes", "on"}
    except Exception:
        return False




ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
DATA = Path(os.environ.get("OPENAI4_DATA_DIR", "/opt/automyai/data/openai4"))
LOG_DIR = Path(os.environ.get("OPENAI4_LOG_DIR", str(DATA / "logs")))
STATE_FILE = DATA / "state.json"
CONFIG_FILE = DATA / "config.json"

for path in (DATA, LOG_DIR):
    path.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="OpenAI 注册", docs_url=None, redoc_url=None)

_lock = threading.Lock()
_logs: list[dict[str, str]] = []
_LOG_MAX = 3000
_meter_session = None
_meter_lock = threading.Lock()
_state: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "run_id": "",
    "pid": 0,
    "concurrency": 1,
    "total": 0,
    "completed": 0,
    "success": 0,
    "failed": 0,
    "started_at": "",
    "finished_at": "",
    "updated_at": "",
    "error": "",
    "current_email": "",
    "current_phone": "",
    "current_proxy": "",
    "current_step": "",
    "results": [],
    "engine": "uc_signup",
    "display": ":1",
    "novnc_path": default_openai4_config()["novnc_path"],
    "activeAccounts": [],
    "last_preflight": None,
    "traffic_meter": False,
    "traffic": None,
}
_poll_stop = threading.Event()
_poll_thread: Optional[threading.Thread] = None
_seen_uc_log_keys: set[str] = set()
_seen_uc_log_order: list[str] = []
_preflight_cache: dict[str, Any] = {}
_PREFLIGHT_CACHE_SECONDS = 120
_preflight_lock = threading.Lock()

# Signup stage state is shared with the UC worker.  It is deliberately read
# only by the control plane so a transient mailbox failure can move an alias
# to the back of the queue without changing credentials or provider data.
_STAGE_STATE_FILE = PROJECT_ROOT / "data" / "uc_signup_email_stage.json"


def _load_signup_stage_state() -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(_STAGE_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(key).strip().lower(): value
        for key, value in payload.items()
        if str(key).strip() and isinstance(value, dict)
    }


def _stage_retry_metadata(email: str, stage: dict[str, dict[str, Any]]) -> dict[str, Any]:
    record = stage.get(str(email or "").strip().lower()) or {}
    try:
        count = max(0, int(record.get("retryableCount") or 0))
    except (TypeError, ValueError):
        count = 0
    retry_after = str(record.get("retryAfter") or record.get("retryAfterAt") or "").strip()
    retry_after_ts = 0.0
    if retry_after:
        try:
            text_value = retry_after.replace("Z", "+00:00")
            retry_after_ts = datetime.fromisoformat(text_value).timestamp()
        except (TypeError, ValueError, OverflowError):
            retry_after_ts = 0.0
    return {
        "retryableCount": count,
        "retryAfter": retry_after,
        "retryableHold": retry_after_ts > time.time(),
        "lastRetryableError": str(record.get("lastRetryableError") or "")[:240],
        "registered": bool(record.get("registered")),
    }


def _openai4_mail_probe_timeout() -> float:
    """Bound a single mailbox health probe so a dead alias cannot stall UI."""
    try:
        value = float(os.environ.get("OPENAI4_MAIL_PREFLIGHT_TIMEOUT", "8"))
    except (TypeError, ValueError):
        value = 8.0
    return max(2.0, min(value, 20.0))


def _now() -> str:
    return datetime.now(BEIJING_TZ).isoformat(timespec="seconds")


def _preflight_cache_key(req: Any, cfg: dict[str, Any]) -> str:
    """Hash preflight inputs without retaining proxy or account secrets."""
    material = {
        "merged": _merge_start_config(req, cfg),
        "total": int(getattr(req, "total", 1) or 1),
        "selectedAccountId": getattr(req, "selected_account_id", None),
        "selectedAccountEmail": str(getattr(req, "selected_account_email", "") or ""),
        "forcedPhone": str(getattr(req, "forced_phone", "") or ""),
    }
    try:
        material["stageStateMtimeNs"] = _STAGE_STATE_FILE.stat().st_mtime_ns
    except OSError:
        material["stageStateMtimeNs"] = 0
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _remember_preflight(req: Any, cfg: dict[str, Any], result: dict[str, Any]) -> None:
    global _preflight_cache
    _preflight_cache = {
        "key": _preflight_cache_key(req, cfg),
        "at": time.time(),
        "result": result,
    }


def _recent_preflight(req: Any, cfg: dict[str, Any]) -> dict[str, Any] | None:
    if _preflight_cache.get("key") != _preflight_cache_key(req, cfg):
        return None
    if time.time() - float(_preflight_cache.get("at") or 0) > _PREFLIGHT_CACHE_SECONDS:
        return None
    result = _preflight_cache.get("result")
    return result if isinstance(result, dict) else None


def _reset_current_run_logs() -> None:
    """Clear the live window while keeping every previous on-disk run log."""
    global _logs, _seen_uc_log_keys, _seen_uc_log_order
    with _lock:
        _logs = []
        _seen_uc_log_keys = set()
        _seen_uc_log_order = []



def _hydrate_logs_from_disk(limit: int = 400) -> None:
    """Load recent on-disk run logs into memory so UI keeps history across restarts/new runs."""
    global _logs
    with _lock:
        if _logs:
            return
    if not LOG_DIR.is_dir():
        return
    files = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime)
    if not files:
        return
    rows: list[dict[str, str]] = []
    for path in files[-2:]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        rows.append({"time": _now(), "level": "info", "message": f"---- 历史日志 {path.name} ----"})
        for line in lines[-limit:]:
            message = line
            level = "info"
            if "❌" in line or "💀" in line:
                level = "error"
            elif "⚠️" in line:
                level = "warn"
            ts = ""
            if line.startswith("[") and "]" in line:
                ts = line[1:line.find("]")]
                message = line[line.find("]") + 1 :].lstrip()
            rows.append({"time": ts or _now(), "level": level, "message": message})
    if not rows:
        return
    with _lock:
        if not _logs:
            _logs = rows[-_LOG_MAX:]


def _remember_uc_log_key(key: str) -> bool:
    """Return True if this UC log line is new; False if already mirrored."""
    global _seen_uc_log_keys, _seen_uc_log_order
    if not key:
        return True
    if key in _seen_uc_log_keys:
        return False
    _seen_uc_log_keys.add(key)
    _seen_uc_log_order.append(key)
    while len(_seen_uc_log_order) > max(_LOG_MAX * 2, 800):
        old_key = _seen_uc_log_order.pop(0)
        _seen_uc_log_keys.discard(old_key)
    return True


def _append_log(message: str, level: str = "info", *, event_time: str = "", write_disk: bool = True) -> None:
    entry = {"time": str(event_time or _now()), "message": str(message), "level": level}
    with _lock:
        _logs.append(entry)
        while len(_logs) > _LOG_MAX:
            _logs.pop(0)
        if not write_disk:
            return
        run_id = str(_state.get("run_id") or "").strip()
        if run_id:
            log_path = LOG_DIR / f"{run_id}.log"
            try:
                with open(log_path, "a", encoding="utf-8") as handle:
                    handle.write(f"[{entry['time']}] {entry['message']}\n")
            except Exception:
                pass


def _save_state() -> None:
    try:
        STATE_FILE.write_text(json.dumps(_public_state(), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass



def _refresh_traffic_locked() -> None:
    global _meter_session
    if _meter_session is not None and public_session is not None:
        try:
            _state["traffic"] = public_session(_meter_session)
            _state["traffic_meter"] = True
        except Exception:
            pass


def _stop_traffic(status: str = "done") -> dict[str, Any] | None:
    global _meter_session
    if stop_meter is None or _meter_session is None:
        return None
    try:
        snap = stop_meter(_meter_session, status=status)
    except Exception as error:
        _append_log(f"流量统计停止失败: {error}", "warn")
        snap = None
    _meter_session = None
    if snap is not None:
        with _lock:
            _state["traffic"] = snap
            _state["traffic_meter"] = True
        _append_log(
            f"[*] 流量统计: 上行 {snap.get('bytes_sent', 0)}B / 下行 {snap.get('bytes_recv', 0)}B / 合计 {snap.get('bytes_total_h') or snap.get('bytes_total')}"
        )
    return snap


def _public_state() -> dict[str, Any]:
    _refresh_traffic_locked()
    with _lock:
        return dict(_state)


def _project_config() -> dict[str, Any]:
    path = PROJECT_ROOT / "config.json"
    if not path.is_file():
        path = Path("/opt/automyai/config.json")
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _main_api_base() -> str:
    return str(os.environ.get("OPENAI4_MAIN_API_BASE") or os.environ.get("AUTOMYAI_API_BASE") or "http://127.0.0.1:13030").rstrip("/")


def _admin_password() -> str:
    return str(
        os.environ.get("OPENAI4_ADMIN_PASSWORD")
        or os.environ.get("ADMIN_PASSWORD")
        or ""
    ).strip()


def _main_request(method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 20.0) -> dict[str, Any]:
    url = f"{_main_api_base()}{path if path.startswith('/') else '/' + path}"
    data = None
    headers = {"Accept": "application/json", "User-Agent": "openai4-control/1.0"}
    password = _admin_password()
    if password:
        headers["X-Admin-Password"] = password
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = UrlRequest(url, data=data, method=method.upper(), headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            if not raw:
                return {}
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {"data": payload}
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        try:
            parsed = json.loads(detail)
        except Exception:
            parsed = {"error": detail or f"HTTP {error.code}"}
        raise RuntimeError(str(parsed.get("error") or parsed.get("message") or detail or f"HTTP {error.code}")) from error
    except URLError as error:
        raise RuntimeError(f"主站连接失败: {error.reason}") from error


def load_config() -> dict[str, Any]:
    defaults = default_openai4_config()
    defaults["traffic_meter"] = bool(defaults.get("traffic_meter")) or _automyai_traffic_meter_default()
    project = _project_config()
    # Do NOT seed any default proxy. User must fill custom_proxy_url on this page.
    if project.get("SUB2API_IMPORT_GROUP_NAMES"):
        defaults["sub2api_group"] = str(project.get("SUB2API_IMPORT_GROUP_NAMES") or "auto").split(",")[0].strip() or "auto"
    flag = str(project.get("SUB2API_IMPORT_USE_SIGNUP_PROXY") or "false").strip().lower()
    defaults["sub2api_import_use_signup_proxy"] = flag in {"1", "true", "yes", "on"}
    if project.get("UC_SIGNUP_FINGERPRINT_ENABLED"):
        defaults["fingerprint_enabled"] = str(project.get("UC_SIGNUP_FINGERPRINT_ENABLED")).lower() in {"1", "true", "yes", "on"}

    if CONFIG_FILE.is_file():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # Drop removed legacy keys if present.
                data.pop("proxy_mode", None)
                data.pop("proxy_region", None)
                data.pop("cliproxy_proxy_url", None)
                data.pop("proxy", None)
                for k, v in data.items():
                    key = str(k)
                    if key not in defaults and key not in {"custom_proxy_url"}:
                        continue
                    if v is None:
                        defaults[key] = defaults.get(key, "")
                        continue
                    if key in {
                        "fingerprint_enabled",
                        "fingerprint_strict",
                        "sub2api_import_use_signup_proxy",
                        "get_refresh_token",
                        "keep_browser_on_failure",
                        "auth_only",
                        "manual_mode",
                        "traffic_meter",
                    }:
                        if isinstance(v, str):
                            defaults[key] = v.strip().lower() in {"1", "true", "yes", "on"}
                        else:
                            defaults[key] = bool(v)
                    else:
                        defaults[key] = v
        except Exception:
            pass

    # Compatibility guard for password-before-OTP signup pages.  The Mail
    # Opus "待注册" source is a new-registration pool; do not let a stale
    # auth_only flag route it into existing-account /log-in/password flow.
    if source_group_requires_signup(defaults.get("mail_source_group")):
        defaults["auth_only"] = False

    # OpenAI4 is always unattended. Old saved forms may still contain these
    # switches from an earlier UI; never expose or execute them here.
    defaults["manual_mode"] = False
    defaults["keep_browser_on_failure"] = False

    # Always refresh Outlook credentials from project config.
    # noVNC is deployment-owned, not a user setting. Older saved forms may
    # contain an empty novnc_path and must not override the working route.
    defaults["novnc_path"] = default_openai4_config()["novnc_path"]
    defaults["outlook_api_url"] = str(project.get("OUTLOOK_EMAIL_API_URL") or "http://127.0.0.1:5010")
    defaults["outlook_api_key"] = str(project.get("OUTLOOK_EMAIL_API_KEY") or "")
    defaults["outlook_admin_password"] = str(project.get("OUTLOOK_EMAIL_ADMIN_PASSWORD") or "")
    return defaults


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    cur = load_config()
    bool_keys = {
        "fingerprint_enabled",
        "fingerprint_strict",
        "sub2api_import_use_signup_proxy",
        "get_refresh_token",
        "keep_browser_on_failure",
        "auth_only",
        "manual_mode",
        "traffic_meter",
    }
    secret_keys = {"custom_proxy_url"}
    allowed = set(default_openai4_config().keys()) | {"custom_proxy_url"}
    for key, value in cfg.items():
        if key == "novnc_path":
            continue
        if key not in allowed and key not in bool_keys:
            continue
        if key in secret_keys and value == "***":
            continue
        if key in bool_keys:
            if isinstance(value, str):
                cur[key] = value.strip().lower() in {"1", "true", "yes", "on"}
            else:
                cur[key] = bool(value)
        elif value is None:
            continue
        else:
            cur[key] = str(value)
    # Persist without outlook secrets duplicated unnecessarily? keep for offline preflight.
    persist = {k: v for k, v in cur.items() if k not in {"outlook_api_key", "outlook_admin_password"}}
    persist["manual_mode"] = False
    persist["keep_browser_on_failure"] = False
    # Actually keep outlook url only; keys come from project each load.
    persist.pop("outlook_api_url", None)
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(persist, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(CONFIG_FILE, 0o600)
    return load_config()


def public_config(cfg: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    c = dict(cfg or load_config())
    out: dict[str, Any] = {}
    secret_keys = {"outlook_api_key", "outlook_admin_password"}
    for key, value in c.items():
        kl = str(key).lower()
        if key == "custom_proxy_url":
            # Always return full proxy text for the panel input. Never mask.
            out[key] = str(value or "")
            continue
        if key in {"fingerprint_enabled", "fingerprint_strict", "sub2api_import_use_signup_proxy", "get_refresh_token", "keep_browser_on_failure", "auth_only", "traffic_meter"}:
            out[key] = bool(value) if not isinstance(value, str) else value in {"1", "true", "True", "yes", "on"}
        elif key in secret_keys or any(s in kl for s in ("pass", "key", "secret", "token")):
            out[key] = "***" if str(value or "").strip() else ""
        else:
            out[key] = value if isinstance(value, (bool, int, float)) else str(value if value is not None else "")
    try:
        resolved = resolve_openai4_proxy(c)
        out["resolved_proxy"] = public_proxy_url(resolved["proxyUrl"])
        out["resolved_proxy_mode"] = resolved["mode"]
        out["resolved_proxy_name"] = resolved.get("proxyName") or ""
        out["proxy_configured"] = bool(resolved.get("proxyUrl"))
    except Exception as error:
        out["resolved_proxy"] = ""
        out["resolved_proxy_mode"] = "custom"
        out["resolved_proxy_name"] = ""
        out["proxy_configured"] = False
        out["proxy_resolve_error"] = str(error)[:160]
    out["engine"] = "uc_signup"
    out["concurrency_fixed"] = 1
    return out


class ConfigReq(BaseModel):
    custom_proxy_url: str = ""
    fingerprint_enabled: bool = True
    fingerprint_source: str = Field("local", pattern="^(local|cloud)$")
    fingerprint_seed: str = ""
    fingerprint_strict: bool = True
    mail_source_group: str = "默认分组"
    mail_pending_group: str = "oai_pending"
    mail_success_group: str = "oai_success"
    mail_bad_group: str = "badmail"
    sub2api_group: str = "auto"
    sub2api_import_use_signup_proxy: bool = False
    get_refresh_token: bool = True
    keep_browser_on_failure: bool = False
    auth_only: bool = False
    manual_mode: bool = False
    traffic_meter: bool = False
    novnc_path: str = ""


class StartReq(BaseModel):
    traffic_meter: bool | None = None
    total: int = Field(1, ge=1, le=50)
    custom_proxy_url: str = ""
    selected_account_id: int = Field(0, ge=0)
    selected_account_email: str = ""
    selected_account_group: str = ""
    fingerprint_enabled: bool | None = None
    fingerprint_source: str = ""
    fingerprint_seed: str = ""
    fingerprint_strict: bool | None = None
    mail_source_group: str = ""
    mail_pending_group: str = ""
    mail_success_group: str = ""
    mail_bad_group: str = ""
    sub2api_group: str = ""
    sub2api_import_use_signup_proxy: bool | None = None
    get_refresh_token: bool | None = None
    keep_browser_on_failure: bool | None = None
    auth_only: bool | None = None
    manual_mode: bool | None = None
    forced_phone: str = ""
    emails: list[str] = Field(default_factory=list)

    @field_validator("selected_account_id", mode="before")
    @classmethod
    def _coerce_selected_account_id(cls, value):
        if value is None or value == "":
            return 0
        return value


def _proxy_request(proxy: str):
    from curl_cffi import requests as curl_requests

    return curl_requests.get(
        "https://auth.openai.com/",
        proxy=proxy,
        timeout=15,
        impersonate="firefox144",
        allow_redirects=False,
    )


def _proxy_preflight(proxy: str) -> dict[str, Any]:
    normalized = normalize_proxy_url(proxy)
    effective = normalized
    scheme_adjusted = False
    try:
        response = _proxy_request(effective)
    except Exception as error:
        fallback = proxy_http_connect_fallback(effective, error)
        if not fallback:
            raise HTTPException(400, f"代理启动前检查失败: {type(error).__name__}: {str(error)[:180]}") from error
        try:
            response = _proxy_request(fallback)
            effective = fallback
            scheme_adjusted = True
        except Exception as fallback_error:
            raise HTTPException(
                400,
                f"代理启动前检查失败: HTTPS 端点握手失败，按 HTTP CONNECT 重试仍失败: "
                f"{type(fallback_error).__name__}: {str(fallback_error)[:140]}",
            ) from fallback_error
    status = int(response.status_code or 0)
    if status == 407:
        raise HTTPException(400, "代理认证失败: HTTP 407")
    if status <= 0 or status >= 500:
        raise HTTPException(400, f"代理出口返回异常状态: HTTP {response.status_code}")
    return {
        "configured": True,
        "reachable": True,
        "status": status,
        "effectiveScheme": "http" if effective.startswith("http://") else effective.split(":", 1)[0],
        "schemeAdjusted": scheme_adjusted,
        "proxy": public_proxy_url(effective),
    }


def _outlook_clients(cfg: dict[str, Any]):
    from integrations.outlook_email_client import OutlookEmailAdminClient, OutlookEmailClient

    base = str(cfg.get("outlook_api_url") or "http://127.0.0.1:5010").strip()
    api_key = str(cfg.get("outlook_api_key") or "").strip()
    admin_password = str(cfg.get("outlook_admin_password") or "").strip()
    return (
        OutlookEmailClient(base, api_key, 20000),
        OutlookEmailAdminClient(base, admin_password, 20000),
    )


def _sub2api_preflight(group_name: str) -> dict[str, Any]:
    from integrations.sub2api_client import Sub2ApiClient

    project = _project_config()

    def setting(name: str, default: str = "") -> str:
        return str(os.environ.get(name) or project.get(name) or default).strip()

    try:
        timeout_ms = max(1000, int(setting("TIMEOUT_MS", "20000")))
    except ValueError:
        timeout_ms = 20000
    client = Sub2ApiClient(
        setting("SUB2API_API_URL"),
        setting("SUB2API_ADMIN_EMAIL"),
        setting("SUB2API_ADMIN_PASSWORD"),
        setting("SUB2API_ADMIN_TOKEN"),
        timeout_ms,
    )
    if not client.configured:
        raise RuntimeError("Sub2API 管理接口未配置")
    groups = client.list_groups()
    target = str(group_name or "auto").strip() or "auto"
    matched = None
    for item in groups if isinstance(groups, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("groupName") or "").strip()
        if name == target:
            matched = item
            break
    if matched is None:
        # Some deployments accept create-on-import; still require connectivity.
        return {"configured": True, "groupName": target, "status": "missing-on-list", "reachable": True}
    return {
        "configured": True,
        "groupId": matched.get("id") or matched.get("groupId"),
        "groupName": str(matched.get("name") or matched.get("groupName") or target),
        "platform": matched.get("platform") or "openai",
        "status": matched.get("status") or "active",
        "reachable": True,
    }


def _sub2api_groups() -> list[dict[str, Any]]:
    payload = _main_request("GET", "/api/sub2api/groups", timeout=20)
    groups = payload.get("groups") if isinstance(payload, dict) else []
    return [item for item in groups if isinstance(item, dict)] if isinstance(groups, list) else []


def _sub2api_import_proxy_preflight(merged: dict[str, Any], signup_proxy_url: str) -> dict[str, Any]:
    follow = bool(merged.get("sub2api_import_use_signup_proxy"))
    if follow:
        proxy_url = str(signup_proxy_url or "").strip()
        mode = "follow-signup"
        name = "跟随注册代理"
    else:
        project = _project_config()
        region = normalize_proxy_region(project.get("SUB2API_PROXY_REGION")) or "JP"
        profile_name, profile_url = MIHOMO_SUB2API_PROFILES.get(region, MIHOMO_SUB2API_PROFILES["JP"])
        proxy_url = str(project.get("SUB2API_PROXY_URL") or profile_url).strip()
        name = str(project.get("SUB2API_PROXY_NAME") or profile_name).strip()
        mode = "dedicated-fallback"
    if not proxy_url:
        raise RuntimeError("Sub2API/OAuth 导入代理未配置")
    checked = _proxy_preflight(proxy_url)
    return {
        **checked,
        "mode": mode,
        "name": name,
        "followSignupProxy": follow,
        "proxy": public_proxy_url(proxy_url),
    }


def _fingerprint_preflight(cfg: dict[str, Any]) -> dict[str, Any]:
    if not bool(cfg.get("fingerprint_enabled", True)):
        return {"enabled": False, "reachable": True, "skipped": True}
    port = str(os.environ.get("FINGERPRINT_API_PORT") or "50001").strip() or "50001"
    url = str(os.environ.get("OPENAI4_FINGERPRINT_API_URL") or f"http://127.0.0.1:{port}").rstrip("/")
    try:
        request = UrlRequest(url + "/", method="GET", headers={"Accept": "application/json"})
        with urlopen(request, timeout=5) as response:
            code = int(getattr(response, "status", 0) or 0)
    except HTTPError as error:
        code = int(error.code or 0)
        # 401 means service up but auth required — acceptable.
        if code not in {200, 401, 404}:
            raise RuntimeError(f"指纹 API HTTP {code}") from error
    except URLError as error:
        if bool(cfg.get("fingerprint_strict", True)):
            raise RuntimeError(f"指纹 API 不可达: {error.reason}") from error
        return {"enabled": True, "reachable": False, "strict": False, "error": str(error.reason)}
    return {"enabled": True, "reachable": True, "status": code, "url": url}


def _main_uc_preflight() -> dict[str, Any]:
    payload = _main_request("GET", "/api/uc-signup/status", timeout=8)
    state = payload.get("ucSignupState") if isinstance(payload, dict) else None
    if not isinstance(state, dict):
        raise RuntimeError("主站 UC 状态返回异常")
    return {
        "reachable": True,
        "running": bool(state.get("running")),
        "phase": state.get("phase") or "idle",
        "apiBase": _main_api_base(),
    }


def _display_preflight() -> dict[str, Any]:
    novnc_port = str(os.environ.get("NOVNC_PORT") or "16080").strip()
    vnc_port = str(os.environ.get("VNC_PORT") or "15901").strip()
    display = str(os.environ.get("BROWSER_DISPLAY") or ":1")
    return {
        "display": display,
        "vncPort": int(vnc_port) if vnc_port.isdigit() else vnc_port,
        "novncPort": int(novnc_port) if novnc_port.isdigit() else novnc_port,
        "novncPath": default_openai4_config()["novnc_path"],
        "headed": True,
        "note": "有头 Chromium 运行在 automyai 容器 Xvfb 上，可通过 noVNC 观察",
    }


def _phone_preflight(forced_phone: str = "") -> dict[str, Any]:
    health = _main_request("GET", "/api/health", timeout=8)
    hero_configured = bool(health.get("configured")) if isinstance(health, dict) else False
    teleauto_configured = bool(health.get("teleAutoConfigured")) if isinstance(health, dict) else False
    if not hero_configured and not teleauto_configured:
        raise RuntimeError("手机接码未配置：HeroSMS / TeleAuto 均不可用")

    forced = str(forced_phone or "").strip()
    pool = _main_request("GET", "/api/phones/pool?limit=200", timeout=8)
    items = pool.get("items") if isinstance(pool, dict) else []
    items = items if isinstance(items, list) else []
    forced_match = None
    if forced:
        normalized = "".join(ch for ch in forced if ch.isdigit())
        for item in items:
            if not isinstance(item, dict):
                continue
            candidate = str(item.get("phoneNumber") or item.get("phone") or item.get("phoneKey") or "")
            if "".join(ch for ch in candidate if ch.isdigit()) == normalized:
                forced_match = item
                break
        if forced_match is None:
            raise RuntimeError("指定手机号不在当前号码池中，无法在启动前确认可用性")
    return {
        "reachable": True,
        "heroSmsConfigured": hero_configured,
        "teleAutoConfigured": teleauto_configured,
        "poolCount": len(items),
        "mode": "forced" if forced else "automatic",
        "forcedPhone": forced,
        "forcedPhoneFound": bool(forced_match) if forced else None,
    }


def _account_summary(account: dict[str, Any], stage: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    mail_probe = account.get("mail_probe") if isinstance(account.get("mail_probe"), dict) else {}
    retry = _stage_retry_metadata(str(account.get("email") or ""), stage or {})
    return {
        "id": int(account.get("id") or account.get("ID") or 0),
        "email": str(account.get("email") or "").strip(),
        "group": str(account.get("group_name") or account.get("groupName") or "").strip(),
        "provider": str(account.get("provider") or "outlook").strip(),
        "mailReadable": bool(mail_probe.get("reachable")) if mail_probe else None,
        "retryableCount": retry["retryableCount"],
        "retryAfter": retry["retryAfter"],
        "retryableHold": retry["retryableHold"],
        "lastRetryableError": retry["lastRetryableError"],
    }


def _account_usable(account: dict[str, Any], bad_group: str) -> bool:
    status = str(account.get("status") or "").strip().lower()
    refresh = str(account.get("last_refresh_status") or account.get("lastRefreshStatus") or "").strip().lower()
    group = str(account.get("group_name") or account.get("groupName") or "").strip()
    return status in {"", "active"} and refresh not in {"failed", "error"} and group != bad_group


def _outlook_accounts(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    # Prefer main API inventory (already authenticated path) when possible.
    try:
        payload = _main_request("GET", "/api/outlook-email/accounts", timeout=20)
        accounts = payload.get("accounts") if isinstance(payload, dict) else []
        if isinstance(accounts, list):
            normalized = []
            for account in accounts:
                if not isinstance(account, dict):
                    continue
                normalized.append({
                    "id": account.get("id"),
                    "email": account.get("email"),
                    "group_name": account.get("groupName") or account.get("group_name"),
                    "status": account.get("status"),
                    "last_refresh_status": account.get("lastRefreshStatus") or account.get("last_refresh_status"),
                    "provider": account.get("provider") or account.get("mailProvider") or "outlook",
                })
            if normalized:
                return _merge_opus_pending_accounts(normalized)
    except Exception:
        pass
    client, _ = _outlook_clients(cfg)
    payload = client.list_accounts(limit=10000, offset=0)
    accounts = payload.get("accounts") if isinstance(payload, dict) else []
    if not isinstance(accounts, list):
        return []
    return _merge_opus_pending_accounts([account for account in accounts if isinstance(account, dict)])


def _opus_account_id(email: str) -> int:
    digest = hashlib.sha256(str(email or "").strip().lower().encode("utf-8")).digest()
    return 1_500_000_000 + (int.from_bytes(digest[:4], "big") % 500_000_000)


def _merge_opus_pending_accounts(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = list(accounts)
    by_email = {
        str(item.get("email") or "").strip().lower(): index
        for index, item in enumerate(rows)
        if str(item.get("email") or "").strip()
    }
    try:
        reader = OpusMailAdminReader.from_project(PROJECT_ROOT)
        reader.timeout = _openai4_mail_probe_timeout()
        if not reader.configured:
            return rows
        payload = reader.list_pending_signup_accounts(limit=1000)
        for item in payload.get("accounts") or []:
            email = str(item.get("email") or "").strip()
            if not email:
                continue
            pending_row = {
                "id": _opus_account_id(email),
                "email": email,
                "group_name": OPUS_PENDING_GROUP,
                "status": "active",
                "last_refresh_status": "",
                "provider": "opusMail",
                "opus_id": str(item.get("id") or ""),
            }
            existing_index = by_email.get(email.lower())
            if existing_index is None:
                by_email[email.lower()] = len(rows)
                rows.append(pending_row)
            else:
                rows[existing_index] = {**rows[existing_index], **pending_row}
    except Exception:
        pass
    return rows


def _prepare_accounts(req: StartReq, cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        groups = normalize_openai4_mail_groups(
            req.mail_source_group or cfg.get("mail_source_group"),
            req.mail_pending_group or cfg.get("mail_pending_group"),
            req.mail_success_group or cfg.get("mail_success_group"),
            req.mail_bad_group or cfg.get("mail_bad_group"),
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error

    accounts = _outlook_accounts(cfg)
    source_group = groups["sourceGroup"]
    bad_group = groups["badGroup"]
    group_names = {str(account.get("group_name") or "").strip() for account in accounts}
    if source_group not in group_names:
        available = "、".join(sorted(name for name in group_names if name and name != bad_group)) or "无"
        raise HTTPException(400, f"来源账号池不存在: {source_group}；当前可选分组: {available}")

    # Mail Opus is a virtual source group, not an Outlook group to create/move.
    if source_group != OPUS_PENDING_GROUP:
        try:
            _, admin = _outlook_clients(cfg)
            admin.ensure_groups([groups["pendingGroup"], groups["successGroup"], groups["badGroup"]])
        except Exception:
            pass

    candidates = [
        account for account in accounts
        if str(account.get("group_name") or "").strip() == source_group and _account_usable(account, bad_group)
    ]
    stage_state = _load_signup_stage_state()
    explicit_emails = [str(item).strip() for item in (req.emails or []) if str(item).strip()]
    explicit_selection = bool(req.selected_account_id or req.selected_account_email or explicit_emails)
    if req.selected_account_id or req.selected_account_email:
        if req.total != 1 and not explicit_emails:
            raise HTTPException(400, "手动选择单个账号时，总数必须为 1")
        selected = []
        # Explicit pick may come from source or pending (resume unfinished accounts).
        searchable = [
            account for account in accounts
            if _account_usable(account, bad_group)
            and str(account.get("group_name") or "").strip() in {source_group, groups["pendingGroup"]}
        ]
        for account in searchable:
            email = str(account.get("email") or "").strip()
            account_id = int(account.get("id") or 0)
            if req.selected_account_id and account_id == req.selected_account_id:
                selected.append(account)
            elif req.selected_account_email and email.lower() == req.selected_account_email.lower():
                selected.append(account)
        if not selected and req.selected_account_email:
            # Allow explicit email even if group metadata lags.
            selected = [{"id": 0, "email": req.selected_account_email, "group_name": source_group, "status": "active"}]
        if not selected:
            raise HTTPException(
                400,
                f"选择的账号不在来源池/待授权池（{source_group} 或 {groups['pendingGroup']}），或邮箱状态不可用",
            )
        candidates = selected
    elif explicit_emails:
        lowered = {email.lower() for email in explicit_emails}
        selected = [account for account in candidates if str(account.get("email") or "").strip().lower() in lowered]
        missing = lowered - {str(account.get("email") or "").strip().lower() for account in selected}
        for email in missing:
            selected.append({"id": 0, "email": email, "group_name": source_group, "status": "active"})
        candidates = selected

    # Automatic batches prefer fresh aliases, then aliases with fewer
    # retryable failures.  Accounts still inside their retry window are left
    # out of this run entirely; an explicit single-account selection remains a
    # deliberate override for diagnostics or forced recovery.
    deferred: list[dict[str, Any]] = []
    if not explicit_selection:
        eligible: list[dict[str, Any]] = []
        for original_index, account in enumerate(candidates):
            retry = _stage_retry_metadata(str(account.get("email") or ""), stage_state)
            enriched = {**account, "_retry": retry, "_original_index": original_index}
            if retry["retryableHold"]:
                deferred.append(enriched)
            else:
                eligible.append(enriched)
        eligible.sort(key=lambda account: (
            int((account.get("_retry") or {}).get("retryableCount") or 0),
            int(account.get("_original_index") or 0),
        ))
        candidates = eligible

    client = None
    try:
        client, _ = _outlook_clients(cfg)
    except Exception:
        client = None

    # Mailbox checks used to construct a new Mail Opus reader for every alias.
    # Each probe then logged in and listed the complete mapping inventory again,
    # sequentially.  Resolve that inventory once and probe a small concurrent
    # window instead; the UI now gets a bounded result even when one worker is
    # offline.
    opus_reader = None
    opus_mappings: dict[str, dict[str, Any]] = {}
    opus_reader_error: Exception | None = None
    opus_candidates = [
        account for account in candidates
        if str(account.get("provider") or "") == "opusMail"
    ]
    if opus_candidates:
        opus_reader = OpusMailAdminReader.from_project(PROJECT_ROOT)
        opus_reader.timeout = _openai4_mail_probe_timeout()
        opus_mappings = {
            str(account.get("email") or "").strip().lower(): {
                "id": str(account.get("opus_id") or "").strip(),
                "email": str(account.get("email") or "").strip(),
            }
            for account in opus_candidates
            if str(account.get("email") or "").strip() and str(account.get("opus_id") or "").strip()
        }
        missing_mapping = any(
            str(account.get("email") or "").strip().lower() not in opus_mappings
            for account in opus_candidates
        )
        if missing_mapping:
            try:
                opus_mappings.update({
                    str(item.get("email") or "").strip().lower(): dict(item)
                    for item in opus_reader.list_mappings()
                    if isinstance(item, dict) and str(item.get("email") or "").strip()
                })
            except Exception:
                # Keep the failure attached to each alias below.  This
                # preserves the existing actionable account diagnostics.
                pass
        # Establish one shared admin cookie before worker threads start.  The
        # request helper is otherwise allowed to race several login calls when
        # the first batch of probes begins at the same instant.
        try:
            if not str(getattr(opus_reader, "_cookie", "") or "").strip():
                login = getattr(opus_reader, "login", None)
                if callable(login):
                    login()
        except Exception as error:
            opus_reader_error = error

    target = max(1, int(req.total or 1))
    healthy_by_index: dict[int, dict[str, Any]] = {}
    failures_by_index: dict[int, dict[str, Any]] = {}

    def _is_transient(error: Exception) -> bool:
        text = str(error).lower()
        return any(token in text for token in (
            "timed out", "timeout", "tempor", "connection reset",
            "connection refused", "502", "503", "504",
        ))

    def _probe(index: int, account: dict[str, Any]) -> tuple[int, dict[str, Any] | None, dict[str, Any] | None]:
        email = str(account.get("email") or "").strip()
        if not email:
            return index, None, None
        try:
            if str(account.get("provider") or "") == "opusMail":
                if opus_reader is None:
                    raise RuntimeError("Mail Opus 读取未配置")
                if opus_reader_error is not None:
                    raise opus_reader_error
                mapping = opus_mappings.get(email.lower())
                if not mapping:
                    raise RuntimeError("Mail Opus 邮箱映射不存在")
                last_error: Exception | None = None
                for attempt in range(2):
                    try:
                        probe = opus_reader.probe_mapping_mail_access(mapping)
                        return index, {**account, "mail_probe": probe}, None
                    except Exception as error:
                        last_error = error
                        if not _is_transient(error) or attempt:
                            break
                        time.sleep(0.2)
                raise last_error or RuntimeError("Mail Opus 拉信探测失败")
            if client is None:
                return index, dict(account), None
            client.list_mails(email, limit=1, offset=0)
            return index, dict(account), None
        except Exception as error:
            return index, None, {
                "id": int(account.get("id") or 0),
                "email": email,
                "error": str(error)[:160],
                "definitive": mail_failure_is_definitive(error),
            }

    remaining = list(enumerate(candidates))
    # Four workers also keeps a single-account preflight quick without
    # creating a large burst against the mailbox service; larger batches use
    # up to eight parallel probes.
    worker_count = min(8, max(4, target * 2), len(remaining) or 1)
    while remaining and len(healthy_by_index) < target:
        batch = remaining[:worker_count]
        remaining = remaining[worker_count:]
        with ThreadPoolExecutor(max_workers=min(worker_count, len(batch))) as executor:
            futures = [executor.submit(_probe, index, account) for index, account in batch]
            for future in as_completed(futures):
                index, healthy_account, failure = future.result()
                if healthy_account is not None:
                    healthy_by_index[index] = healthy_account
                elif failure is not None:
                    failures_by_index[index] = failure

    healthy = [healthy_by_index[index] for index in sorted(healthy_by_index)]
    failures = [failures_by_index[index] for index in sorted(failures_by_index)]
    deferred_summaries = [_account_summary(account, stage_state) for account in deferred]
    return {
        **groups,
        "accounts": [_account_summary(account, stage_state) for account in healthy],
        "checked": len(healthy),
        "failed": len(failures),
        "definitiveFailures": sum(1 for item in failures if item.get("definitive")),
        "failures": failures,
        "deferred": deferred_summaries,
        "deferredCount": len(deferred_summaries),
        "deferredReason": "retryable_cooldown" if deferred_summaries else "",
        "groupNames": sorted(name for name in group_names if name),
    }


def _require_account_capacity(prepared: dict[str, Any], total: int) -> None:
    accounts = prepared.get("accounts") if isinstance(prepared, dict) else []
    if not isinstance(accounts, list) or len(accounts) < total:
        have = len(accounts) if isinstance(accounts, list) else 0
        source = str((prepared or {}).get("sourceGroup") or "")
        failed = prepared.get("failed") if isinstance(prepared, dict) else 0
        failures = prepared.get("failures") if isinstance(prepared, dict) else []
        deferred_count = int((prepared or {}).get("deferredCount") or 0) if isinstance(prepared, dict) else 0
        detail_bits = []
        if isinstance(failures, list) and failures:
            sample = []
            for item in failures[:3]:
                if not isinstance(item, dict):
                    continue
                email = str(item.get("email") or "").strip() or "?"
                err = str(item.get("error") or "").strip()
                sample.append(f"{email}: {err}" if err else email)
            if sample:
                detail_bits.append("读信失败 " + " | ".join(sample))
        pending = str((prepared or {}).get("pendingGroup") or "oai_pending")
        hint = ""
        if source and source != pending:
            hint = f"；若要续跑未完成号，请来源池改选 {pending}"
        extra = ("；" + "；".join(detail_bits)) if detail_bits else ""
        if deferred_count:
            extra += f"；冷却账号 {deferred_count} 个已自动后置"
        raise HTTPException(
            400,
            f"可用邮箱不足：需要 {total}，来源池[{source or '?'}]预检通过 {have}"
            f"（读信失败 {int(failed or 0)}）{extra}{hint}",
        )


def _merge_start_config(req: StartReq, cfg: dict[str, Any]) -> dict[str, Any]:
    merged = dict(cfg)
    # This control plane is unattended automation. Do not let legacy saved
    # values turn an ordinary failure into a long VNC/manual wait.
    merged["manual_mode"] = False
    merged["keep_browser_on_failure"] = False
    mapping = {
        "custom_proxy_url": req.custom_proxy_url,
        "mail_source_group": req.mail_source_group,
        "mail_pending_group": req.mail_pending_group,
        "mail_success_group": req.mail_success_group,
        "mail_bad_group": req.mail_bad_group,
        "sub2api_group": req.sub2api_group,
        "fingerprint_source": req.fingerprint_source,
        "fingerprint_seed": req.fingerprint_seed,
    }
    for key, value in mapping.items():
        if value is not None and str(value).strip() != "":
            merged[key] = value
    if req.fingerprint_enabled is not None:
        merged["fingerprint_enabled"] = bool(req.fingerprint_enabled)
    if req.fingerprint_strict is not None:
        merged["fingerprint_strict"] = bool(req.fingerprint_strict)
    if req.sub2api_import_use_signup_proxy is not None:
        merged["sub2api_import_use_signup_proxy"] = bool(req.sub2api_import_use_signup_proxy)
    if req.get_refresh_token is not None:
        merged["get_refresh_token"] = bool(req.get_refresh_token)
    if req.auth_only is not None:
        merged["auth_only"] = bool(req.auth_only)
    if getattr(req, "traffic_meter", None) is not None:
        merged["traffic_meter"] = bool(req.traffic_meter)
    return merged


def _run_preflight(req: StartReq, cfg: dict[str, Any]) -> dict[str, Any]:
    merged = _merge_start_config(req, cfg)
    if bool(merged.get("auth_only")) and not bool(merged.get("get_refresh_token", True)):
        raise HTTPException(400, "配置冲突：仅重新授权必须开启‘获取 RT 并导入’")
    try:
        resolved = resolve_openai4_proxy(merged, override_proxy=req.custom_proxy_url)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    _append_log("预检 1/7：检查注册代理出口")
    step_started = time.monotonic()
    proxy_result = _proxy_preflight(resolved["proxyUrl"])
    _append_log(f"预检 1/7 完成，耗时 {time.monotonic() - step_started:.1f}s")
    _append_log("预检 2/7：检查邮箱来源与可用数量")
    step_started = time.monotonic()
    account_result = _prepare_accounts(req, merged)
    _require_account_capacity(account_result, req.total)
    _append_log(
        f"预检 2/7 完成，耗时 {time.monotonic() - step_started:.1f}s；"
        f"邮箱可用 {int(account_result.get('checked') or 0)} / 失败 {int(account_result.get('failed') or 0)}"
        + (f" / 冷却后置 {int(account_result.get('deferredCount') or 0)}" if account_result.get("deferredCount") else "")
    )
    if bool(merged.get("get_refresh_token", True)):
        _append_log("预检 3/7：检查 Sub2API 与 OAuth 导入代理")
        step_started = time.monotonic()
        try:
            sub2 = _sub2api_preflight(str(merged.get("sub2api_group") or "auto"))
        except Exception as error:
            raise HTTPException(400, f"Sub2API 启动前检查失败: {error}") from error
        try:
            sub2_proxy = _sub2api_import_proxy_preflight(merged, resolved["proxyUrl"])
        except Exception as error:
            raise HTTPException(400, f"Sub2API/OAuth 导入代理检查失败: {error}") from error
        _append_log(f"预检 3/7 完成，耗时 {time.monotonic() - step_started:.1f}s")
    else:
        _append_log("预检 3/7：已配置跳过 RT / Sub2API")
        sub2 = {"reachable": False, "skipped": True, "status": "skipped", "groupName": ""}
        sub2_proxy = {"reachable": False, "skipped": True, "mode": "skipped", "proxy": ""}
    _append_log("预检 4/7：检查浏览器指纹模块")
    try:
        fingerprint = _fingerprint_preflight(merged)
    except Exception as error:
        raise HTTPException(400, f"指纹模块启动前检查失败: {error}") from error
    _append_log("预检 5/7：检查主站 UC 服务")
    try:
        uc = _main_uc_preflight()
    except Exception as error:
        raise HTTPException(400, f"主站 UC 启动前检查失败: {error}") from error
    if uc.get("running"):
        raise HTTPException(409, "主站 UC 注册任务已在运行中，请先停止再启动")
    _append_log("预检 6/7：检查手机号与接码通道")
    try:
        phone = _phone_preflight(req.forced_phone)
    except Exception as error:
        raise HTTPException(400, f"手机接码启动前检查失败: {error}") from error
    _append_log("预检 7/7：检查真实 noVNC 桌面")
    display = _display_preflight()
    result = {
        "ok": True,
        "proxy": {**proxy_result, "mode": resolved["mode"], "name": resolved.get("proxyName"), "region": resolved.get("region")},
        "mail": {
            "sourceGroup": account_result.get("sourceGroup"),
            "pendingGroup": account_result.get("pendingGroup"),
            "successGroup": account_result.get("successGroup"),
            "badGroup": account_result.get("badGroup"),
            "checked": account_result.get("checked"),
            "failed": account_result.get("failed"),
            "definitiveFailures": account_result.get("definitiveFailures"),
            "accounts": account_result.get("accounts"),
            "deferred": account_result.get("deferred"),
            "deferredCount": account_result.get("deferredCount"),
            "deferredReason": account_result.get("deferredReason"),
        },
        "sub2api": sub2,
        "sub2apiProxy": sub2_proxy,
        "fingerprint": fingerprint,
        "uc": uc,
        "phone": phone,
        "display": display,
        "engine": "uc_signup",
        "getRefreshToken": bool(merged.get("get_refresh_token", True)),
    }
    with _lock:
        _state["last_preflight"] = {"at": _now(), "ok": True, "summary": {
            "proxy": result["proxy"].get("proxy"),
            "mailChecked": account_result.get("checked"),
            "sub2api": sub2.get("groupName"),
        }}
    _remember_preflight(req, cfg, result)
    return result


def _sync_from_uc() -> None:
    try:
        payload = _main_request("GET", "/api/uc-signup/status", timeout=8)
        uc_state = payload.get("ucSignupState") if isinstance(payload, dict) else {}
        cfg = load_config()
        mapped = map_uc_state_to_openai4(uc_state if isinstance(uc_state, dict) else {}, run_id=str(_state.get("run_id") or ""), cfg=cfg)
        with _lock:
            # Preserve local activeAccounts / last_preflight / run_id if UC idle wiped them.
            active = list(_state.get("activeAccounts") or [])
            last_preflight = _state.get("last_preflight")
            run_id = _state.get("run_id") or mapped.get("run_id")
            local_phase = str(_state.get("phase") or "")
            local_step = str(_state.get("current_step") or "")
            preserve_preflight_state = local_phase in {"preflight", "ready", "preflight_failed"} and not bool(mapped.get("running"))
            _state.update(mapped)
            if run_id:
                _state["run_id"] = run_id
            if active and not _state.get("activeAccounts"):
                _state["activeAccounts"] = active
            if last_preflight is not None:
                _state["last_preflight"] = last_preflight
            if preserve_preflight_state:
                _state["phase"] = local_phase
                _state["current_step"] = local_step
        # Pull logs once each; never re-append historical UC lines on every poll.
        try:
            logs_payload = _main_request("GET", "/api/uc-signup/logs", timeout=8)
            lines = logs_payload.get("logs") if isinstance(logs_payload, dict) else []
            if isinstance(lines, list):
                for line in lines[-200:]:
                    if not isinstance(line, dict):
                        continue
                    message = str(line.get("message") or "").strip()
                    if not message:
                        continue
                    event_time = str(line.get("time") or "").strip()
                    key = f"{event_time}|{message}|{line.get('level') or 'info'}"
                    with _lock:
                        is_new = _remember_uc_log_key(key)
                    if not is_new:
                        continue
                    _append_log(
                        message,
                        str(line.get("level") or "info"),
                        event_time=event_time or _now(),
                        write_disk=True,
                    )
        except Exception:
            pass
        _save_state()
    except Exception as error:
        _append_log(f"同步 UC 状态失败: {error}", "warn")


def _poll_loop(run_id: str) -> None:
    _append_log(f"开始跟踪 UC 任务 run_id={run_id}")
    while not _poll_stop.is_set():
        _sync_from_uc()
        with _lock:
            running = bool(_state.get("running"))
            phase = str(_state.get("phase") or "")
            _refresh_traffic_locked()
        if not running and phase in {"done", "stopped", "idle", "error", "completed"}:
            break
        time.sleep(2.0)
    _sync_from_uc()
    with _lock:
        running = bool(_state.get("running"))
        phase = str(_state.get("phase") or "")
        success = int(_state.get("success") or 0)
        failed = int(_state.get("failed") or 0)
        _state["finished_at"] = _state.get("finished_at") or _now()
        if _state.get("running") is False and not _state.get("phase"):
            _state["phase"] = "done"
    if not running:
        status = "completed" if success > 0 and failed == 0 else ("stopped" if phase == "stopped" else "error")
        if phase == "stopped":
            status = "stopped"
        elif success > 0 and failed == 0:
            status = "completed"
        else:
            status = "error" if failed else "done"
        _stop_traffic(status=status)
    _append_log("UC 任务跟踪结束")
    _save_state()


def _start_poller(run_id: str) -> None:
    global _poll_thread, _seen_uc_log_keys, _seen_uc_log_order
    _poll_stop.set()
    if _poll_thread and _poll_thread.is_alive():
        _poll_thread.join(timeout=2)
    with _lock:
        _seen_uc_log_keys = set()
        _seen_uc_log_order = []
    _poll_stop.clear()
    _poll_thread = threading.Thread(target=_poll_loop, args=(run_id,), daemon=True)
    _poll_thread.start()


@app.get("/api/health")
def health():
    cfg = load_config()
    return {
        "ok": True,
        "service": "openai4",
        "title": "OpenAI 注册",
        "engine": "uc_signup",
        "mainApiBase": _main_api_base(),
        "state": _public_state(),
        "config": public_config(cfg),
    }


@app.get("/api/status")
def status():
    # Cheap sync when client polls.
    try:
        _sync_from_uc()
    except Exception:
        pass
    return {"ok": True, "state": _public_state(), "config": public_config()}


@app.get("/api/logs")
def logs(tail: int = 200):
    _hydrate_logs_from_disk()
    try:
        n = max(1, min(int(tail or 200), 2000))
    except Exception:
        n = 200
    with _lock:
        items = list(_logs[-n:])
    return {"ok": True, "logs": items, "state": _public_state()}


@app.get("/api/config")
def get_config():
    return {"ok": True, "config": public_config()}


@app.post("/api/config")
def post_config(req: ConfigReq):
    payload = req.model_dump()
    value = sanitize_openai4_proxy_display(payload.get("custom_proxy_url"))
    if value == "***":
        payload.pop("custom_proxy_url", None)
    else:
        # Persist exactly what the user typed. Runtime normalize happens on resolve/start.
        payload["custom_proxy_url"] = value
        if value:
            try:
                normalize_openai4_proxy_input(value)
            except ValueError as error:
                raise HTTPException(400, str(error)) from error
    cfg = save_config(payload)
    return {"ok": True, "config": public_config(cfg)}


@app.get("/api/accounts")
def accounts():
    cfg = load_config()
    try:
        rows = _outlook_accounts(cfg)
    except Exception as error:
        raise HTTPException(502, f"读取邮箱账号失败: {error}") from error
    groups: dict[str, int] = {}
    summaries = []
    stage_state = _load_signup_stage_state()
    bad = str(cfg.get("mail_bad_group") or "badmail")
    for account in rows:
        summary = _account_summary(account, stage_state)
        group = summary["group"] or "(无分组)"
        groups[group] = groups.get(group, 0) + 1
        if _account_usable(account, bad):
            summaries.append(summary)
    summaries.sort(key=lambda item: (
        bool(item.get("retryableHold")),
        int(item.get("retryableCount") or 0),
        str(item.get("email") or "").lower(),
    ))
    mail_groups: list[dict[str, Any]] = []
    try:
        _, admin = _outlook_clients(cfg)
        for item in admin.list_groups():
            name = str(item.get("name") or "").strip()
            if name:
                mail_groups.append({"id": item.get("id"), "name": name})
    except Exception:
        mail_groups = []
    known_mail_names = {str(item.get("name") or "") for item in mail_groups}
    for name in groups:
        if name and name != OPUS_PENDING_GROUP and name not in known_mail_names:
            mail_groups.append({"name": name})
    source_names = set(groups) | {str(item.get("name") or "") for item in mail_groups}
    return {
        "ok": True,
        "total": len(rows),
        "usable": len(summaries),
        "groups": [{"name": name, "count": groups.get(name, 0)} for name in sorted(source_names) if name],
        "mailGroups": sorted(mail_groups, key=lambda item: str(item.get("name") or "")),
        "accounts": summaries[:500],
        "config": public_config(cfg),
    }


@app.get("/api/sub2api-groups")
def sub2api_groups():
    try:
        groups = _sub2api_groups()
    except Exception as error:
        raise HTTPException(502, f"读取 Sub2API 分组失败: {error}") from error
    return {"ok": True, "groups": groups}


@app.post("/api/preflight")
def preflight(req: StartReq):
    cfg = load_config()
    with _lock:
        if _state.get("running"):
            raise HTTPException(409, "OpenAI 注册任务已在运行中，请先停止再预检")

    # A successful preflight is valid for the same inputs for a short window.
    # Reusing it avoids a second mailbox/proxy sweep when a user presses
    # “预检” and then “开始” moments later.
    cached = _recent_preflight(req, cfg)
    if cached is not None:
        _reset_current_run_logs()
        _append_log("启动前检查结果已复用（输入未变化，缓存仍有效）")
        with _lock:
            _state["phase"] = "ready"
            _state["current_step"] = ""
            _state["error"] = ""
            _state["updated_at"] = _now()
            _state["last_preflight"] = {"at": _now(), "ok": True, "summary": {
                "proxy": cached.get("proxy", {}).get("proxy"),
                "mailChecked": cached.get("mail", {}).get("checked"),
                "sub2api": cached.get("sub2api", {}).get("groupName"),
            }}
        _save_state()
        return {"ok": True, **cached, "cached": True}

    if not _preflight_lock.acquire(blocking=False):
        raise HTTPException(409, "已有预检正在执行，请等待当前检查完成")
    try:
        # Another request may have completed while this request waited for the
        # lock; check once more before touching the visible log window.
        cached = _recent_preflight(req, cfg)
        if cached is not None:
            _reset_current_run_logs()
            _append_log("启动前检查结果已复用（输入未变化，缓存仍有效）")
            with _lock:
                _state["phase"] = "ready"
                _state["current_step"] = ""
                _state["error"] = ""
                _state["updated_at"] = _now()
            _save_state()
            return {"ok": True, **cached, "cached": True}

        return _preflight_uncached(req, cfg)
    finally:
        _preflight_lock.release()


def _preflight_uncached(req: StartReq, cfg: dict[str, Any]) -> dict[str, Any]:
    _reset_current_run_logs()
    started = time.time()
    with _lock:
        _state["run_id"] = ""
        _state["phase"] = "preflight"
        _state["current_step"] = "preflight"
        _state["error"] = ""
        _state["updated_at"] = _now()
    _append_log("启动前检查开始；检查过程会逐项显示")
    try:
        result = _run_preflight(req, cfg)
    except Exception as error:
        detail = str(getattr(error, "detail", "") or error)
        _append_log(f"启动前检查失败：{detail}", "error")
        with _lock:
            _state["phase"] = "preflight_failed"
            _state["current_step"] = ""
            _state["error"] = detail
            _state["updated_at"] = _now()
        _save_state()
        raise
    elapsed = time.time() - started
    _append_log(f"启动前检查通过，耗时 {elapsed:.1f}s；等待最终确认")
    with _lock:
        _state["phase"] = "ready"
        _state["current_step"] = ""
        _state["error"] = ""
        _state["updated_at"] = _now()
    _save_state()
    return {"ok": True, **result}


@app.post("/api/start")
def start(req: StartReq):
    cfg = load_config()
    # Persist non-secret start form choices users expect to stick.
    persist_payload = {
        "mail_source_group": req.mail_source_group or cfg.get("mail_source_group"),
        "mail_pending_group": req.mail_pending_group or cfg.get("mail_pending_group"),
        "mail_success_group": req.mail_success_group or cfg.get("mail_success_group"),
        "mail_bad_group": req.mail_bad_group or cfg.get("mail_bad_group"),
        "sub2api_group": req.sub2api_group or cfg.get("sub2api_group"),
    }
    raw_proxy = sanitize_openai4_proxy_display(req.custom_proxy_url)
    if raw_proxy and raw_proxy != "***":
        try:
            normalize_openai4_proxy_input(raw_proxy)  # validate only
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        persist_payload["custom_proxy_url"] = raw_proxy  # keep user-typed form
    if req.fingerprint_enabled is not None:
        persist_payload["fingerprint_enabled"] = req.fingerprint_enabled
    if req.fingerprint_strict is not None:
        persist_payload["fingerprint_strict"] = req.fingerprint_strict
    if req.sub2api_import_use_signup_proxy is not None:
        persist_payload["sub2api_import_use_signup_proxy"] = req.sub2api_import_use_signup_proxy
    if req.get_refresh_token is not None:
        persist_payload["get_refresh_token"] = bool(req.get_refresh_token)
    if req.traffic_meter is not None:
        persist_payload["traffic_meter"] = bool(req.traffic_meter)
    if req.auth_only is not None:
        persist_payload["auth_only"] = bool(req.auth_only)
    persist_payload["manual_mode"] = False
    persist_payload["keep_browser_on_failure"] = False
    cfg = save_config({**cfg, **persist_payload})

    preflight_result = _recent_preflight(req, cfg)
    reused_preflight = preflight_result is not None
    if preflight_result is None:
        if not _preflight_lock.acquire(blocking=False):
            raise HTTPException(409, "已有预检正在执行，请等待当前检查完成")
        try:
            # A preflight request can finish between the first cache lookup and
            # acquiring this lock; avoid running the expensive mailbox sweep a
            # second time in that case.
            preflight_result = _recent_preflight(req, cfg)
            reused_preflight = preflight_result is not None
            if preflight_result is None:
                _reset_current_run_logs()
                _append_log("启动检查：没有可复用的预检结果，开始逐项检查")
                preflight_result = _run_preflight(req, cfg)
        finally:
            _preflight_lock.release()
    merged = _merge_start_config(req, cfg)
    resolved = resolve_openai4_proxy(merged, override_proxy=req.custom_proxy_url)
    accounts = preflight_result.get("mail", {}).get("accounts") or []
    emails = [str(item.get("email") or "").strip() for item in accounts if str(item.get("email") or "").strip()]
    if not emails:
        raise HTTPException(400, "没有可启动的邮箱")
    emails = emails[: max(1, int(req.total or 1))]

    # Mirror both states. The old one-way update left the main process stuck
    # on the signup proxy after the UI toggle was turned off.
    follow_signup_proxy = bool(merged.get("sub2api_import_use_signup_proxy"))
    project = _project_config()
    settings_payload = {
        "SUB2API_IMPORT_USE_SIGNUP_PROXY": "true" if follow_signup_proxy else "false",
    }
    if not follow_signup_proxy:
        region = normalize_proxy_region(project.get("SUB2API_PROXY_REGION")) or "JP"
        name, url = MIHOMO_SUB2API_PROFILES.get(region, MIHOMO_SUB2API_PROFILES["JP"])
        settings_payload.update({
            "SUB2API_PROXY_REGION": region,
            "SUB2API_PROXY_URL": str(project.get("SUB2API_PROXY_URL") or url),
            "SUB2API_PROXY_NAME": str(project.get("SUB2API_PROXY_NAME") or name),
        })
    try:
        _main_request("POST", "/api/settings", settings_payload, timeout=10)
    except Exception as error:
        raise HTTPException(400, f"设置 Sub2API 导入代理失败: {error}") from error

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    with _lock:
        _state["run_id"] = run_id
        _state["phase"] = "starting"
        _state["started_at"] = _now()
        _state["updated_at"] = _now()
        _state["error"] = ""
    _append_log("======== 新任务 ========")
    _append_log(
        "启动检查：复用刚通过的预检结果" if reused_preflight
        else "启动检查：已重新完成预检"
    )
    global _meter_session
    upstream_proxy = str(resolved["proxyUrl"] or "").strip()
    effective_proxy = upstream_proxy
    traffic_snap = None
    want_meter = bool(merged.get("traffic_meter"))
    if want_meter:
        if not upstream_proxy:
            raise HTTPException(400, "已开启流量统计，请先填写自定义注册代理")
        if start_meter_for_proxy is None:
            raise HTTPException(500, "流量统计模块不可用")
        # stop previous dangling meter if any
        if _meter_session is not None:
            _stop_traffic(status="replaced")
        try:
            effective_proxy, _meter_session = start_meter_for_proxy(
                upstream_proxy,
                service="openai4",
                run_id=run_id,
                meta={"total": len(emails), "engine": "uc_signup"},
            )
            traffic_snap = public_session(_meter_session) if public_session else None
            _append_log(f"[*] 流量统计已启用 → 本地 {effective_proxy} → 上游 {public_proxy_url(upstream_proxy)}")
        except Exception as error:
            raise HTTPException(500, f"流量统计启动失败: {error}") from error

    payload = build_uc_start_payload(
        emails=emails,
        proxy_url=effective_proxy,
        cfg=merged,
        selected_account_email=str(req.selected_account_email or "").strip(),
        forced_phone=str(req.forced_phone or "").strip(),
        mail_provider=str((accounts[0] if accounts and isinstance(accounts[0], dict) else {}).get("provider") or "").strip(),
    )
    try:
        result = _main_request("POST", "/api/uc-signup/start", payload, timeout=30)
    except Exception as error:
        _stop_traffic(status="error")
        raise HTTPException(400, f"启动 UC 注册失败: {error}") from error
    if isinstance(result, dict) and result.get("error"):
        _stop_traffic(status="error")
        raise HTTPException(409, str(result.get("error")))

    uc_state = result.get("ucSignupState") if isinstance(result, dict) else {}
    mapped = map_uc_state_to_openai4(uc_state if isinstance(uc_state, dict) else {}, run_id=run_id, cfg=merged)
    with _lock:
        _state.update(mapped)
        _state["run_id"] = run_id
        _state["running"] = True
        _state["phase"] = mapped.get("phase") or "running"
        _state["started_at"] = mapped.get("started_at") or _now()
        _state["activeAccounts"] = accounts[: len(emails)]
        _state["current_proxy"] = public_proxy_url(upstream_proxy)
        _state["traffic_meter"] = want_meter
        _state["traffic"] = traffic_snap
        _state["error"] = ""
    proxy_label = resolved.get("proxyName") or resolved.get("mode") or "unknown"
    traffic_label = "on" if want_meter else "off"
    fingerprint_label = "on" if merged.get("fingerprint_enabled", True) else "off"
    _append_log(
        f"[*] 启动 OpenAI 注册: engine=uc_signup total={len(emails)} proxy={proxy_label} traffic={traffic_label} fingerprint={fingerprint_label} run_id={run_id}"
    )
    for email in emails:
        _append_log(f"  队列邮箱: {email}")
    _save_state()
    _start_poller(run_id)
    return {"ok": True, "state": _public_state(), "preflight": preflight_result}


@app.post("/api/stop")
def stop():
    try:
        result = _main_request("POST", "/api/uc-signup/stop", {}, timeout=15)
    except Exception as error:
        raise HTTPException(502, f"停止 UC 注册失败: {error}") from error
    _append_log("已请求停止 UC 注册任务")
    _sync_from_uc()
    with _lock:
        _state["phase"] = "stopped"
        _state["running"] = bool((_state.get("running")))
    if not _state.get("running"):
        _stop_traffic(status="stopped")
    _save_state()
    return {"ok": True, "state": _public_state(), "uc": result}



@app.get("/api/traffic")
def traffic(tail: int = 30):
    try:
        n = max(1, min(int(tail or 30), 200))
    except Exception:
        n = 30
    current = None
    if public_session is not None and _meter_session is not None:
        try:
            current = public_session(_meter_session)
        except Exception:
            current = None
    if current is None:
        with _lock:
            current = _state.get("traffic")
    history = []
    if load_sessions is not None:
        try:
            history = load_sessions(service="openai4", tail=n)
        except Exception:
            history = []
    return {
        "ok": True,
        "enabled": bool((_state.get("traffic_meter") if isinstance(_state, dict) else False) or current),
        "current": current,
        "history": history[:n],
    }


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'>"
        "<title>OpenAI 注册</title>"
        "<p>请从 AutoMyAI 控制台进入："
        "<a href='/ui/pages/openai.html'>/ui/pages/openai.html</a></p>"
    )


def main() -> None:
    import uvicorn

    host = str(os.environ.get("OPENAI4_HOST") or "127.0.0.1")
    port = str(os.environ.get("OPENAI4_PORT") or "").strip()
    if not port.isdigit():
        raise SystemExit("OPENAI4_PORT must come from config/ports.env")
    uvicorn.run(app, host=host, port=int(port), log_level="info")


if __name__ == "__main__":
    main()
