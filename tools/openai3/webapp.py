#!/usr/bin/env python3
"""OpenAI 注册 3 — ChatGPT Register CLI 的独立 Web 控制面。

与 OpenAI 1 (UC) / OpenAI 2 (gpt-outlook2) 完全隔离：
- 代码: /opt/automyai/tools/openai3
- 数据: /opt/automyai/data/openai3
- 端口: 127.0.0.1:8791
"""
from __future__ import annotations

import asyncio
import json
import re
import os
import secrets
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

for _parent in Path(__file__).resolve().parents:
    if (_parent / "integrations" / "openai3_control.py").is_file():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

from integrations.openai3_control import (
    mail_failure_is_definitive,
    normalize_mail_groups,
    normalize_proxy_url,
    proxy_http_connect_fallback,
)
from integrations.openai3_sub2api import extract_multipart_json_file, import_auth_to_sub2api

# Optional traffic metering (scheme A: paste upstream as-is; wrap only when enabled)
import sys as _sys
_TM_CANDIDATES = [
    Path('/opt/automyai/tools'),
    Path('/app/tools'),
    Path(__file__).resolve().parent / 'tools',
    Path(__file__).resolve().parent.parent,  # openai3/webapp -> tools/
]
for _TM_ROOT in _TM_CANDIDATES:
    try:
        if (_TM_ROOT / 'traffic_meter').is_dir() and str(_TM_ROOT) not in _sys.path:
            _sys.path.insert(0, str(_TM_ROOT))
            break
        if _TM_ROOT.name == 'traffic_meter' and _TM_ROOT.is_dir() and str(_TM_ROOT.parent) not in _sys.path:
            _sys.path.insert(0, str(_TM_ROOT.parent))
            break
    except Exception:
        pass
try:
    from traffic_meter import load_sessions, public_session, start_meter_for_proxy, stop_meter
except Exception:  # pragma: no cover
    load_sessions = public_session = start_meter_for_proxy = stop_meter = None  # type: ignore

def _automyai_traffic_meter_default() -> bool:
    """Prefer global AutoMyAI setting; env OPENAI3_TRAFFIC_METER overrides when set."""
    try:
        import json
        from pathlib import Path
        for candidate in (
            Path("/opt/automyai/config.json"),
            Path(__file__).resolve().parents[2] / "config.json",
        ):
            if candidate.is_file():
                data = json.loads(candidate.read_text(encoding="utf-8"))
                v = str(data.get("TRAFFIC_METER_ENABLED") or "").strip().lower()
                return v in {"1", "true", "yes", "on"}
    except Exception:
        pass
    return False


ROOT = Path(__file__).resolve().parent
_INTEGRATED_ENGINE = ROOT.parent / "chatgpt_register"
ENGINE = Path(os.environ.get(
    "OPENAI3_ENGINE_DIR",
    str(_INTEGRATED_ENGINE if (_INTEGRATED_ENGINE / "chatgpt_register.py").is_file() else ROOT / "engine"),
)).resolve()
DATA = Path(os.environ.get("OPENAI3_DATA_DIR", "/opt/automyai/data/openai3"))
LOG_DIR = Path(os.environ.get("OPENAI3_LOG_DIR", str(DATA / "logs")))
ACCOUNTS_FILE = Path(os.environ.get("OPENAI3_ACCOUNTS_FILE", str(DATA / "accounts" / "accounts.txt")))
RUNS_DIR = Path(os.environ.get("OPENAI3_RUNS_DIR", str(DATA / "runs")))
STATE_FILE = DATA / "state.json"

for p in (DATA, LOG_DIR, ACCOUNTS_FILE.parent, RUNS_DIR):
    p.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="OpenAI 注册3", docs_url=None, redoc_url=None)

_lock = threading.Lock()
_state: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "run_id": "",
    "pid": 0,
    "concurrency": 1,
    "total": 1,
    "completed": 0,
    "failed": 0,
    "started_at": "",
    "finished_at": "",
    "error": "",
    "log_path": "",
    "traffic_meter": False,
    "traffic": None,
}
_process: Optional[subprocess.Popen] = None
_meter_session = None
_sub2api_import_tokens: dict[str, str] = {}
_logs: list[dict[str, str]] = []
_LOG_MAX = 500
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
NOVNC_PATH = "/novnc/vnc.html?autoconnect=1&resize=scale&path=novnc/websockify"
NOVNC_URL = os.environ.get(
    "VNC_WEB_URL",
    f"https://automyai.kfjie.me{NOVNC_PATH}",
).strip() or f"https://automyai.kfjie.me{NOVNC_PATH}"


def _now() -> str:
    return datetime.now(BEIJING_TZ).isoformat(timespec="seconds")


def _append_log(message: str, level: str = "info") -> None:
    entry = {"time": _now(), "message": str(message), "level": level}
    with _lock:
        _logs.append(entry)
        while len(_logs) > _LOG_MAX:
            _logs.pop(0)
        # also write run log file if active
        log_path = _state.get("log_path") or ""
        if log_path:
            try:
                Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"[{entry['time']}] {message}\n")
            except Exception:
                pass


def _save_state() -> None:
    with _lock:
        payload = dict(_state)
    try:
        STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
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


def _public_state() -> dict[str, Any]:
    with _lock:
        _refresh_traffic_locked()
        st = dict(_state)
        proc = _process
    if proc is not None and proc.poll() is not None and st.get("running"):
        # reaper
        code = proc.returncode
        with _lock:
            _state["running"] = False
            if _state.get("phase") in {"running", "starting"}:
                _state["phase"] = "completed" if code == 0 else "error"
                if code != 0 and not _state.get("error"):
                    _state["error"] = f"exit={code}"
            _state["finished_at"] = _state.get("finished_at") or _now()
            _state["pid"] = 0
            st = dict(_state)
        _save_state()
    return st


def _env_for_job() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["OPENAI3_ACCOUNTS_FILE"] = str(ACCOUNTS_FILE)
    env["OPENAI3_DATA_DIR"] = str(DATA)
    env["AUTOMYAI_CONFIG"] = os.environ.get("AUTOMYAI_CONFIG", "/opt/automyai/config.json")
    # pass-through operator config
    for key in (
        "CPA_BASE", "CPA_KEY", "MAIL_BASE", "MAIL_PASS",
        "CHATGPT_REGISTER_PROXY", "OPENAI3_PROXY",
    ):
        if key in os.environ and os.environ[key]:
            env[key] = os.environ[key]
    # alias
    if env.get("OPENAI3_PROXY") and not env.get("CHATGPT_REGISTER_PROXY"):
        env["CHATGPT_REGISTER_PROXY"] = env["OPENAI3_PROXY"]
    return env


class StartReq(BaseModel):
    concurrency: int = Field(1, ge=1, le=20)
    total: int = Field(1, ge=1, le=200)
    proxy: str = ""
    traffic_meter: bool = False
    mail_pass: str = ""
    selected_account_id: int = Field(0, ge=0)
    selected_account_email: str = ""
    selected_account_group: str = ""
    fingerprint_enabled: bool = True
    fingerprint_source: str = Field("local", pattern="^(local|cloud)$")
    fingerprint_seed: str = ""
    fingerprint_strict: bool = True
    mail_source_group: str = "默认分组"
    mail_pending_group: str = "oai_pending"
    mail_success_group: str = "oai_success"
    mail_bad_group: str = "badmail"
    sub2api_group: str = "auto"


class ConfigReq(BaseModel):
    proxy: str = ""
    traffic_meter: bool = False
    mail_pass: str = ""
    fingerprint_enabled: bool = True
    fingerprint_source: str = Field("local", pattern="^(local|cloud)$")
    fingerprint_seed: str = ""
    fingerprint_strict: bool = True
    mail_source_group: Optional[str] = None
    mail_pending_group: Optional[str] = None
    mail_success_group: Optional[str] = None
    mail_bad_group: Optional[str] = None
    sub2api_group: Optional[str] = None


def _config_path() -> Path:
    return DATA / "config.json"


def load_config() -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "proxy": os.environ.get("OPENAI3_PROXY") or os.environ.get("CHATGPT_REGISTER_PROXY") or "",
        "traffic_meter": (
            os.environ.get("OPENAI3_TRAFFIC_METER", "").strip().lower() in {"1", "true", "yes", "on"}
            if os.environ.get("OPENAI3_TRAFFIC_METER", "").strip() != ""
            else _automyai_traffic_meter_default()
        ),
        "mail_pass": os.environ.get("MAIL_PASS") or "",
        "fingerprint_enabled": True,
        "fingerprint_source": "local",
        "fingerprint_seed": "",
        "fingerprint_strict": True,
        "mail_source_group": "默认分组",
        "mail_pending_group": "oai_pending",
        "mail_success_group": "oai_success",
        "mail_bad_group": "badmail",
        "sub2api_group": "auto",
    }
    p = _config_path()
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update({str(k): v if v is not None else "" for k, v in data.items()})
        except Exception:
            pass
    defaults.pop("cpa_base", None)
    defaults.pop("cpa_key", None)
    defaults.pop("mail_base", None)
    return defaults


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    cur = load_config()
    for k in (
        "proxy", "mail_pass", "traffic_meter",
        "fingerprint_enabled", "fingerprint_source", "fingerprint_seed", "fingerprint_strict",
        "mail_source_group", "mail_pending_group", "mail_success_group", "mail_bad_group", "sub2api_group",
    ):
        if k in cfg and cfg[k] is not None:
            # keep secret if placeholder
            if k in {"proxy", "mail_pass"} and cfg[k] == "***":
                continue
            if k in {"traffic_meter", "fingerprint_enabled", "fingerprint_strict"}:
                cur[k] = bool(cfg[k]) if not isinstance(cfg[k], str) else cfg[k] in {"1", "true", "True", "yes"}
            else:
                cur[k] = str(cfg[k])
    _config_path().write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return cur


def public_config(cfg: Optional[dict] = None) -> dict:
    c = dict(cfg or load_config())
    # Never expose secrets or third-party credentials in API responses.
    secret_keys = {
        "mail_pass", "outlook_api_key", "outlook_admin_password",
        "password", "token", "secret",
    }
    out: dict = {}
    for k, v in c.items():
        kl = str(k).lower()
        if k in {"traffic_meter", "fingerprint_enabled", "fingerprint_strict"}:
            out[k] = bool(v) if not isinstance(v, str) else v in {"1", "true", "True", "yes"}
        elif k in secret_keys or any(s in kl for s in ("pass", "key", "secret", "token")):
            out[k] = "***" if str(v or "").strip() else ""
        elif "proxy" in kl and str(v or "").strip():
            out[k] = "***"
        else:
            out[k] = str(v if v is not None else "")
    out["proxy_configured"] = bool(str(c.get("proxy") or "").strip())
    return out


def _normalize_proxy_url(value: Any) -> str:
    return normalize_proxy_url(value)


def _proxy_request(proxy: str):
    from curl_cffi import requests as curl_requests

    return curl_requests.get(
        "https://auth.openai.com/",
        proxy=proxy,
        timeout=15,
        impersonate="firefox144",
        allow_redirects=False,
    )


def _proxy_preflight(proxy: str) -> tuple[dict[str, Any], str]:
    normalized = _normalize_proxy_url(proxy)
    if not normalized:
        return ({"configured": False, "reachable": True, "mode": "direct"}, "")
    effective_proxy = normalized
    scheme_adjusted = False
    try:
        response = _proxy_request(effective_proxy)
    except Exception as error:
        fallback = proxy_http_connect_fallback(effective_proxy, error)
        if not fallback:
            raise HTTPException(400, f"代理启动前检查失败: {type(error).__name__}: {str(error)[:180]}") from error
        try:
            response = _proxy_request(fallback)
            effective_proxy = fallback
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
    return ({
        "configured": True,
        "reachable": True,
        "status": status,
        "effectiveScheme": "http" if effective_proxy.startswith("http://") else effective_proxy.split(":", 1)[0],
        "schemeAdjusted": scheme_adjusted,
    }, effective_proxy)


def _outlook_clients(cfg: dict[str, Any]):
    project_root = ROOT.parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from integrations.outlook_email_client import OutlookEmailAdminClient, OutlookEmailClient

    base = str(cfg.get("outlook_api_url") or "http://127.0.0.1:5010").strip()
    api_key = str(cfg.get("outlook_api_key") or "").strip()
    admin_password = str(cfg.get("outlook_admin_password") or "").strip()
    return (
        OutlookEmailClient(base, api_key, 20000),
        OutlookEmailAdminClient(base, admin_password, 20000),
    )


def _project_config() -> dict[str, Any]:
    path = ROOT.parents[1] / "config.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _sub2api_client():
    from integrations.sub2api_client import Sub2ApiClient

    cfg = _project_config()
    def setting(name: str, default: str = "") -> str:
        return str(os.environ.get(name) or cfg.get(name) or default).strip()
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
    return client


def _sub2api_preflight(group_name: str) -> dict[str, Any]:
    group = _sub2api_client().find_group_by_name(group_name, platform="openai")
    return {
        "configured": True,
        "groupId": group.get("id"),
        "groupName": group.get("name"),
        "platform": group.get("platform"),
        "status": group.get("status"),
    }


def _mail_bridge_url() -> str:
    port = str(os.environ.get("OPENAI3_MAIL_PORT") or "").strip()
    if not port.isdigit():
        raise RuntimeError("OPENAI3_MAIL_PORT 未从 config/ports.env 注入")
    return f"http://127.0.0.1:{port}"


def _mail_bridge_preflight(cfg: dict[str, Any]) -> dict[str, Any]:
    base = _mail_bridge_url()
    request = UrlRequest(
        f"{base}/api/login",
        data=json.dumps({"password": str(cfg.get("mail_pass") or "")}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:160]
        raise RuntimeError(f"Mail Bridge HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Mail Bridge 连接失败: {error.reason}") from error
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise RuntimeError("Mail Bridge 登录返回异常")
    return {
        "configured": True,
        "reachable": True,
        "host": "127.0.0.1",
        "port": int(base.rsplit(":", 1)[1]),
    }


def _outlook_accounts(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    client, _ = _outlook_clients(cfg)
    try:
        payload = client.list_accounts(limit=10000, offset=0)
    except Exception as error:
        raise HTTPException(502, f"读取 OutlookEmail 账号失败: {error}") from error
    accounts = payload.get("accounts") if isinstance(payload, dict) else []
    if not isinstance(accounts, list):
        return []
    return [account for account in accounts if isinstance(account, dict)]


def _account_summary(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(account.get("id") or 0),
        "email": str(account.get("email") or "").strip(),
        "group": str(account.get("group_name") or "").strip(),
    }


def _account_usable(account: dict[str, Any], bad_group: str) -> bool:
    status = str(account.get("status") or "").strip().lower()
    refresh = str(account.get("last_refresh_status") or "").strip().lower()
    group = str(account.get("group_name") or "").strip()
    return status in {"", "active"} and refresh not in {"failed", "error"} and group != bad_group


def _mail_groups(req: StartReq, cfg: dict[str, Any]) -> dict[str, str]:
    try:
        return normalize_mail_groups(
            req.mail_source_group or cfg.get("mail_source_group"),
            req.mail_pending_group or cfg.get("mail_pending_group"),
            req.mail_success_group or cfg.get("mail_success_group"),
            req.mail_bad_group or cfg.get("mail_bad_group"),
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


def _prepare_accounts(req: StartReq, cfg: dict[str, Any]) -> dict[str, Any]:
    accounts = _outlook_accounts(cfg)
    groups = _mail_groups(req, cfg)
    source_group = groups["sourceGroup"]
    bad_group = groups["badGroup"]
    group_names = {str(account.get("group_name") or "").strip() for account in accounts}
    if source_group not in group_names:
        available = "、".join(sorted(name for name in group_names if name and name != bad_group)) or "无"
        raise HTTPException(400, f"来源账号池不存在: {source_group}；当前可选分组: {available}")

    candidates = [
        account for account in accounts
        if str(account.get("group_name") or "").strip() == source_group and _account_usable(account, bad_group)
    ]
    if req.selected_account_id:
        if req.total != 1:
            raise HTTPException(400, "手动选择单个账号时，总数必须为 1")
        selected = [account for account in candidates if int(account.get("id") or 0) == req.selected_account_id]
        if not selected:
            raise HTTPException(400, f"选择的账号不在来源池 {source_group}，或邮箱状态不可用")
        candidates = selected

    client, _ = _outlook_clients(cfg)
    healthy: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for account in candidates:
        if len(healthy) >= req.total:
            break
        email = str(account.get("email") or "").strip()
        if not email:
            continue
        try:
            client.list_mails(email, limit=1, offset=0)
            healthy.append(account)
        except Exception as error:
            failures.append({
                "id": int(account.get("id") or 0),
                "email": email,
                "error": str(error)[:160],
                "definitive": mail_failure_is_definitive(error),
            })
    return {
        **groups,
        "accounts": [_account_summary(account) for account in healthy],
        "mailChecks": len(healthy),
        "mailFailures": len(failures),
        "failures": failures,
        "sufficient": len(healthy) >= req.total,
    }


def _require_account_capacity(prepared: dict[str, Any], total: int) -> None:
    if prepared.get("sufficient"):
        return
    raise HTTPException(
        400,
        f"来源池 {prepared.get('sourceGroup')} 通过邮箱检查的账号不足: "
        f"需要 {total}，可用 {prepared.get('mailChecks', 0)}，检查失败 {prepared.get('mailFailures', 0)}",
    )


def _move_account_ids(cfg: dict[str, Any], account_ids: list[int], target_group: str, label: str) -> None:
    ids = [int(account_id) for account_id in account_ids if int(account_id or 0) > 0]
    if not ids:
        return
    try:
        _, admin = _outlook_clients(cfg)
        admin.move_accounts(ids, target_group)
    except Exception as error:
        raise HTTPException(502, f"{label} {target_group} 失败: {error}") from error


def _move_result_account(email: str, target_group: str) -> bool:
    if not email or not target_group:
        return False
    try:
        cfg = load_config()
        accounts = _outlook_accounts(cfg)
        account = next((item for item in accounts if str(item.get("email") or "").strip().lower() == email.lower()), None)
        if not account:
            _append_log("[!] 分组切换跳过: OutlookEmail 中找不到结果邮箱", "warn")
            return False
        _, admin = _outlook_clients(cfg)
        admin.move_accounts([int(account["id"])], target_group)
        _append_log(f"[*] 邮箱结果已移动到分组: {target_group}")
        return True
    except Exception as error:
        _append_log(f"[!] 邮箱移动到 {target_group} 失败: {error}", "error")
        return False


def _return_unreported_accounts(reported_emails: set[str]) -> None:
    with _lock:
        active_accounts = list(_state.get("activeAccounts") or [])
        source_group = str(_state.get("sourceGroup") or "")
    for account in active_accounts:
        email = str((account or {}).get("email") or "").strip()
        if email and email.lower() not in reported_emails:
            _move_result_account(email, source_group)


def _reader_thread(proc: subprocess.Popen, run_id: str, import_token: str) -> None:
    global _meter_session
    assert proc.stdout is not None
    success = 0
    failed = 0
    soft_success = 0
    soft_failed = 0
    structured_results = 0
    reported_emails: set[str] = set()
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            if line.startswith("__AUTOMYAI_OPENAI3_RESULT__"):
                try:
                    result = json.loads(line.removeprefix("__AUTOMYAI_OPENAI3_RESULT__"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    result = {}
                email = str(result.get("email") or "").strip()
                outcome = str(result.get("status") or "failed").strip().lower()
                failure_reason = str(result.get("failure_reason") or "").strip().lower()
                structured_results += 1
                if email:
                    reported_emails.add(email.lower())
                    with _lock:
                        if outcome == "success":
                            target = str(_state.get("successGroup"))
                        elif failure_reason == "otp_not_received_after_resend":
                            target = str(_state.get("badGroup"))
                        else:
                            target = str(_state.get("sourceGroup"))
                    _move_result_account(email, target)
                if outcome == "success":
                    success += 1
                elif outcome == "challenge_required":
                    with _lock:
                        _state["phase"] = "challenge_required"
                        _state["challenge"] = {
                            "type": "cloudflare",
                            "reason": failure_reason or "cloudflare_challenge",
                            "email": email,
                            "novnc_path": NOVNC_PATH,
                            "novnc_url": NOVNC_URL,
                            "message": "站点挑战已使本次纯协议任务停止；邮箱已保留，未计为坏号",
                        }
                    _append_log(
                        f"[!] 站点要求真实浏览器挑战，本次任务已停止（未进入人工托管）；"
                        f"邮箱保留在来源池。真实 noVNC: {NOVNC_URL}",
                        "warn",
                    )
                else:
                    failed += 1
                result_label = {
                    "success": "成功",
                    "challenge_required": "需要浏览器挑战",
                }.get(outcome, "失败")
                _append_log(f"[*] 单项任务结果: {result_label}")
                with _lock:
                    _state["completed"] = success
                    _state["failed"] = failed
                continue
            _append_log(line)
            low = line.lower()
            if structured_results == 0 and ("已保存到 accounts" in line or "注册成功" in line or "access_token" in low):
                # soft counters from log keywords
                if "已保存到 accounts" in line:
                    soft_success += 1
                    with _lock:
                        _state["completed"] = soft_success
            if structured_results == 0 and ("注册异常" in line or "仍失败" in line or "没有可用邮箱" in line):
                soft_failed += 1
                with _lock:
                    _state["failed"] = soft_failed
        code = proc.wait()
        structured_terminal_error = (
            structured_results > 0
            and success == 0
            and failed > 0
        )
        traffic = None
        with _lock:
            terminal_phase = str(_state.get("phase") or "")
        if stop_meter is not None and _meter_session is not None:
            try:
                meter_status = "challenge_required" if terminal_phase == "challenge_required" else (
                    "completed" if code == 0 and not structured_terminal_error else "error"
                )
                traffic = stop_meter(_meter_session, status=meter_status)
            except Exception as me:
                _append_log(f"[!] 流量统计结束异常: {me}", "error")
            _meter_session = None
        with _lock:
            _state["running"] = False
            _state["pid"] = 0
            if traffic is not None:
                _state["traffic"] = traffic
            if _state.get("phase") not in {"stopped", "challenge_required"}:
                terminal_ok = code == 0 and not structured_terminal_error
                _state["phase"] = "completed" if terminal_ok else "error"
                if not terminal_ok:
                    _state["error"] = _state.get("error") or (
                        f"0/{structured_results} succeeded"
                        if structured_terminal_error
                        else f"exit={code}"
                    )
            _state["finished_at"] = _now()
        if traffic is not None:
            _append_log(
                f"[*] 流量统计: 上行 {traffic.get('bytes_sent', 0)}B / 下行 {traffic.get('bytes_recv', 0)}B / 合计 {traffic.get('bytes_total_h') or traffic.get('bytes_total')}"
            )
        _append_log(f"[*] 任务结束 code={code}")
    except Exception as e:
        traffic = None
        if stop_meter is not None and _meter_session is not None:
            try:
                traffic = stop_meter(_meter_session, status="error")
            except Exception:
                pass
            _meter_session = None
        with _lock:
            _state["running"] = False
            _state["phase"] = "error"
            _state["error"] = str(e)
            _state["finished_at"] = _now()
            _state["pid"] = 0
            if traffic is not None:
                _state["traffic"] = traffic
        _append_log(f"[!] reader error: {e}", "error")
    finally:
        with _lock:
            _sub2api_import_tokens.pop(import_token, None)
        _return_unreported_accounts(reported_emails)
        _save_state()


@app.get("/api/health")
def health():
    cfg = load_config()
    accounts = 0
    if ACCOUNTS_FILE.is_file():
        try:
            accounts = sum(1 for line in ACCOUNTS_FILE.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
        except Exception:
            accounts = 0
    return {
        "ok": True,
        "service": "openai3",
        "engine": str(ENGINE / "chatgpt_register.py"),
        "accounts_file": str(ACCOUNTS_FILE),
        "accounts_count": accounts,
        "state": _public_state(),
        "config": public_config(cfg),
    }


@app.get("/api/status")
def status():
    return {"ok": True, "state": _public_state(), "config": public_config()}


@app.get("/api/logs")
def logs(tail: int = 200):
    try:
        n = max(1, min(int(tail or 200), 1000))
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
    if payload.get("proxy") and payload.get("proxy") != "***":
        try:
            payload["proxy"] = _normalize_proxy_url(payload["proxy"])
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
    cfg = save_config(payload)
    return {"ok": True, "config": public_config(cfg)}


@app.post("/v0/management/auth-files")
async def direct_sub2api_auth_import(request: Request):
    authorization = str(request.headers.get("authorization") or "")
    token = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    with _lock:
        group_name = _sub2api_import_tokens.get(token)
    if not group_name:
        raise HTTPException(401, "OpenAI3 本地导入令牌无效")
    try:
        auth_document = extract_multipart_json_file(
            str(request.headers.get("content-type") or ""),
            await request.body(),
        )
        result = await asyncio.to_thread(
            import_auth_to_sub2api,
            _sub2api_client(),
            auth_document,
            group_name,
        )
    except Exception as error:
        _append_log(f"[!] 直接导入 Sub2API/{group_name} 失败: {error}", "error")
        raise HTTPException(502, f"直接导入 Sub2API 失败: {error}") from error
    _append_log(f"[*] 已直接导入 Sub2API 分组: {group_name}")
    return result


@app.post("/api/preflight")
def preflight(req: StartReq):
    cfg = load_config()
    raw_proxy = ("" if req.proxy == "***" else req.proxy) or cfg.get("proxy") or ""
    try:
        normalized_proxy = _normalize_proxy_url(raw_proxy)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    proxy_result, _ = _proxy_preflight(normalized_proxy)
    account_result = _prepare_accounts(req, cfg)
    _require_account_capacity(account_result, req.total)
    try:
        mail_bridge_result = _mail_bridge_preflight(cfg)
    except Exception as error:
        raise HTTPException(400, f"Mail Bridge 启动前检查失败: {error}") from error
    try:
        sub2api_result = _sub2api_preflight(req.sub2api_group or cfg.get("sub2api_group") or "auto")
    except Exception as error:
        raise HTTPException(400, f"Sub2API 启动前检查失败: {error}") from error
    return {
        "ok": True,
        "proxy": proxy_result,
        "mail": {
            "sourceGroup": account_result["sourceGroup"],
            "pendingGroup": account_result["pendingGroup"],
            "successGroup": account_result["successGroup"],
            "badGroup": account_result["badGroup"],
            "checked": account_result["mailChecks"],
            "failed": account_result["mailFailures"],
            "definitiveFailures": sum(1 for item in account_result["failures"] if item.get("definitive")),
            "accounts": account_result["accounts"],
        },
        "mailBridge": mail_bridge_result,
        "sub2api": sub2api_result,
    }


@app.post("/api/start")
def start(req: StartReq):
    global _process, _meter_session
    script = ENGINE / "chatgpt_register.py"
    if not script.is_file():
        raise HTTPException(500, "未找到 chatgpt_register.py")
    with _lock:
        if _state.get("running") and _process and _process.poll() is None:
            raise HTTPException(409, "OpenAI3 任务已在运行中")

    # merge request overrides into env via config temp
    cfg = load_config()
    try:
        requested_proxy = "" if req.proxy == "***" else req.proxy
        cfg["proxy"] = _normalize_proxy_url(requested_proxy or cfg.get("proxy") or "")
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    cfg["traffic_meter"] = bool(req.traffic_meter)
    if req.mail_pass and req.mail_pass != "***":
        cfg["mail_pass"] = req.mail_pass
    cfg["fingerprint_enabled"] = bool(req.fingerprint_enabled)
    cfg["fingerprint_source"] = req.fingerprint_source
    cfg["fingerprint_seed"] = req.fingerprint_seed.strip()
    cfg["fingerprint_strict"] = bool(req.fingerprint_strict)
    cfg["mail_source_group"] = req.mail_source_group.strip()
    cfg["mail_pending_group"] = req.mail_pending_group.strip()
    cfg["mail_success_group"] = req.mail_success_group.strip()
    cfg["mail_bad_group"] = req.mail_bad_group.strip()
    cfg["sub2api_group"] = (req.sub2api_group or "auto").strip()

    _, effective_proxy = _proxy_preflight(str(cfg.get("proxy") or ""))
    cfg["proxy"] = effective_proxy
    prepared_accounts = _prepare_accounts(req, cfg)
    _require_account_capacity(prepared_accounts, req.total)
    try:
        _sub2api_preflight(cfg["sub2api_group"])
    except Exception as error:
        raise HTTPException(400, f"Sub2API 启动前检查失败: {error}") from error
    try:
        _mail_bridge_preflight(cfg)
    except Exception as error:
        raise HTTPException(400, f"Mail Bridge 启动前检查失败: {error}") from error
    cfg["mail_source_group"] = prepared_accounts["sourceGroup"]
    cfg["mail_pending_group"] = prepared_accounts["pendingGroup"]
    cfg["mail_success_group"] = prepared_accounts["successGroup"]
    cfg["mail_bad_group"] = prepared_accounts["badGroup"]
    save_config(cfg)
    selected_accounts = list(prepared_accounts["accounts"])
    selected_email = str((selected_accounts[0] if selected_accounts else {}).get("email") or "")
    service_port = str(os.environ.get("OPENAI3_PORT") or "").strip()
    if not service_port.isdigit():
        raise HTTPException(500, "OPENAI3_PORT 未从 config/ports.env 注入")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    log_path = LOG_DIR / f"{run_id}.log"
    env = _env_for_job()
    upstream_proxy = str(cfg.get("proxy") or "").strip()
    effective_proxy = upstream_proxy
    _meter_session = None
    traffic_snap = None
    if bool(cfg.get("traffic_meter")):
        if not upstream_proxy:
            raise HTTPException(400, "已开启流量统计，请先填写上游代理（可直接粘贴 cliproxy 链接）")
        if start_meter_for_proxy is None:
            raise HTTPException(500, "流量统计模块不可用")
        try:
            effective_proxy, _meter_session = start_meter_for_proxy(
                upstream_proxy,
                service="openai3",
                run_id=run_id,
                meta={"concurrency": req.concurrency, "total": req.total},
            )
            traffic_snap = public_session(_meter_session) if public_session else None
            _append_log(f"[*] 流量统计已启用 → 本地 {effective_proxy} → 上游(已脱敏)")
        except Exception as e:
            raise HTTPException(500, f"流量统计启动失败: {e}")

    selected_account_ids = [int(account.get("id") or 0) for account in selected_accounts]
    bad_account_ids = [
        int(item.get("id") or 0)
        for item in prepared_accounts.get("failures") or []
        if item.get("definitive") and int(item.get("id") or 0) > 0
    ]
    try:
        _move_account_ids(
            cfg,
            bad_account_ids,
            prepared_accounts["badGroup"],
            "移动确认不可用邮箱到分组",
        )
        _move_account_ids(
            cfg,
            selected_account_ids,
            prepared_accounts["pendingGroup"],
            "移动账号到执行中分组",
        )
    except HTTPException:
        if stop_meter is not None and _meter_session is not None:
            try:
                stop_meter(_meter_session, status="error")
            except Exception:
                pass
            _meter_session = None
        raise
    env["CHATGPT_REGISTER_PROXY"] = effective_proxy
    env["OPENAI3_PROXY"] = effective_proxy
    env["OPENAI3_UPSTREAM_PROXY"] = upstream_proxy
    env["MAIL_BASE"] = _mail_bridge_url()
    env["MAIL_PASS"] = cfg.get("mail_pass") or ""
    import_token = secrets.token_urlsafe(32)
    with _lock:
        _sub2api_import_tokens[import_token] = cfg["sub2api_group"]
    env["CPA_BASE"] = f"http://127.0.0.1:{service_port}"
    env["CPA_KEY"] = import_token
    env["OPENAI3_ACCOUNTS_FILE"] = str(ACCOUNTS_FILE)
    env["OAI_FINGERPRINT_ENTRY"] = "openai3"
    env["OPENAI3_FINGERPRINT_ENABLED"] = "true" if bool(cfg.get("fingerprint_enabled")) else "false"
    env["OPENAI3_FINGERPRINT_PROVIDER"] = "local-api"
    env["OPENAI3_FINGERPRINT_SOURCE"] = str(cfg.get("fingerprint_source") or "local")
    # One run seed is generated once and then derived per email in the engine.
    # This prevents profile drift between auth steps without reusing one device
    # identity for every account in a concurrent run.
    fingerprint_run_seed = str(cfg.get("fingerprint_seed") or "").strip() or secrets.token_urlsafe(24)
    env["OPENAI3_FINGERPRINT_SEED"] = fingerprint_run_seed
    env["OPENAI3_FINGERPRINT_RUN_SEED"] = fingerprint_run_seed
    env["OPENAI3_FINGERPRINT_STRICT"] = "true" if bool(cfg.get("fingerprint_strict")) else "false"
    env["OPENAI3_FINGERPRINT_PRESET"] = "windows-11-chrome"
    env["OPENAI3_FINGERPRINT_BROWSER_VERSION"] = "150.0.0.0"
    env["OPENAI3_SELECTED_ACCOUNT_ID"] = str(req.selected_account_id or "")
    env["OPENAI3_SELECTED_ACCOUNT_EMAIL"] = selected_email
    env["OPENAI3_SELECTED_ACCOUNT_EMAILS"] = json.dumps(
        [str(account.get("email") or "") for account in selected_accounts],
        ensure_ascii=False,
    )
    env["OPENAI3_SELECTED_ACCOUNT_GROUP"] = prepared_accounts["sourceGroup"]

    py = str(ROOT / ".venv" / "bin" / "python")
    if not Path(py).is_file():
        py = sys.executable
    cmd = [py, str(script), "email", str(req.concurrency), str(req.total)]

    with _lock:
        _logs.clear()
        _state.update({
            "running": True,
            "phase": "starting",
            "run_id": run_id,
            "pid": 0,
            "concurrency": req.concurrency,
            "total": req.total,
            "completed": 0,
            "failed": 0,
            "started_at": _now(),
            "finished_at": "",
            "error": "",
            "log_path": str(log_path),
            "traffic_meter": bool(cfg.get("traffic_meter")),
            "traffic": traffic_snap,
            "challenge": None,
            "sourceGroup": prepared_accounts["sourceGroup"],
            "pendingGroup": prepared_accounts["pendingGroup"],
            "successGroup": prepared_accounts["successGroup"],
            "badGroup": prepared_accounts["badGroup"],
            "activeAccounts": selected_accounts,
        })
    _save_state()
    fingerprint_mode = "关闭" if not cfg.get("fingerprint_enabled") else f"Go/{cfg.get('fingerprint_source') or 'local'}"
    account_label = f" account_id={req.selected_account_id}" if req.selected_account_id else " account=自动"
    _append_log(
        f"[*] 启动 OpenAI3: concurrency={req.concurrency} total={req.total} "
        f"fingerprint={fingerprint_mode}{account_label}"
    )
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ENGINE),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as e:
        with _lock:
            _sub2api_import_tokens.pop(import_token, None)
        for account in selected_accounts:
            _move_result_account(str(account.get("email") or ""), prepared_accounts["sourceGroup"])
        if stop_meter is not None and _meter_session is not None:
            try:
                stop_meter(_meter_session, status="error")
            except Exception:
                pass
            _meter_session = None
        with _lock:
            _state["running"] = False
            _state["phase"] = "error"
            _state["error"] = str(e)
            _state["finished_at"] = _now()
            _state["traffic_meter"] = bool(cfg.get("traffic_meter"))
        _save_state()
        raise HTTPException(500, f"启动失败: {e}")

    with _lock:
        _process = proc
        _state["pid"] = int(proc.pid or 0)
        _state["phase"] = "running"
    _save_state()
    threading.Thread(target=_reader_thread, args=(proc, run_id, import_token), daemon=True).start()
    return {"ok": True, "state": _public_state()}


@app.post("/api/stop")
def stop():
    global _process, _meter_session
    with _lock:
        proc = _process
        running = bool(_state.get("running"))
    if not proc or proc.poll() is not None:
        with _lock:
            _state["running"] = False
            if _state.get("phase") in {"running", "starting"}:
                _state["phase"] = "stopped"
            _state["finished_at"] = _state.get("finished_at") or _now()
        _save_state()
        return {"ok": True, "state": _public_state(), "message": "当前无运行任务"}
    _append_log("[*] 请求停止任务…")
    try:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    except Exception as e:
        _append_log(f"[!] 停止异常: {e}", "error")
    traffic = None
    if stop_meter is not None and _meter_session is not None:
        try:
            traffic = stop_meter(_meter_session, status="stopped")
        except Exception as me:
            _append_log(f"[!] 流量统计停止异常: {me}", "error")
        _meter_session = None
    with _lock:
        _state["running"] = False
        _state["phase"] = "stopped"
        _state["finished_at"] = _now()
        _state["pid"] = 0
        _process = None
        if traffic is not None:
            _state["traffic"] = traffic
    if traffic is not None:
        _append_log(
            f"[*] 流量统计: 上行 {traffic.get('bytes_sent', 0)}B / 下行 {traffic.get('bytes_recv', 0)}B / 合计 {traffic.get('bytes_total_h') or traffic.get('bytes_total')}"
        )
    _save_state()
    return {"ok": True, "state": _public_state()}


@app.get("/api/accounts")
def accounts(tail: int = 50):
    if not ACCOUNTS_FILE.is_file():
        return {"ok": True, "items": [], "count": 0, "path": str(ACCOUNTS_FILE)}
    lines = ACCOUNTS_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    items = [ln for ln in lines if ln.strip()]
    try:
        n = max(1, min(int(tail or 50), 500))
    except Exception:
        n = 50
    return {"ok": True, "items": items[-n:], "count": len(items), "path": str(ACCOUNTS_FILE)}


@app.get("/api/traffic")
def traffic(tail: int = 30):
    global _meter_session
    rows = []
    if load_sessions is not None:
        try:
            rows = load_sessions(service="openai3", tail=tail)
        except Exception as e:
            raise HTTPException(500, str(e))
    with _lock:
        current = _state.get("traffic")
        if _meter_session is not None and public_session is not None:
            try:
                current = public_session(_meter_session)
                _state["traffic"] = current
            except Exception:
                pass
    return {"ok": True, "current": current, "items": rows}


@app.get("/", response_class=HTMLResponse)
def index():
    # Minimal self page; primary UI is AutoMyAI shell page.
    return HTMLResponse(
        """<!doctype html><html><head><meta charset=utf-8><title>OpenAI3</title></head>
<body style="font-family:sans-serif;padding:24px">
<h2>OpenAI 注册 3 API</h2>
<p>请从 AutoMyAI 控制台进入：<a href="/ui/pages/openai3.html">/ui/pages/openai3.html</a></p>
<ul>
<li><a href="/api/health">/api/health</a></li>
<li><a href="/api/status">/api/status</a></li>
<li><a href="/api/logs">/api/logs</a></li>
</ul>
</body></html>"""
    )


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("OPENAI3_HOST", "127.0.0.1")
    port = int(os.environ.get("OPENAI3_PORT", "8791"))
    uvicorn.run(app, host=host, port=port, log_level="info")
