from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from integrations.common import first_non_empty, now_iso, sanitize_sso
from integrations.cpa import CpaClient, CpaError
from integrations.grok2api_client import Grok2ApiClient, Grok2ApiError
from integrations.grok_oauth import parse_sso_lines, sso_to_token, token_to_cliproxy_entry, write_cliproxy_file

# Optional scheme-A traffic meter (default off). Paste upstream proxy unchanged.
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
from integrations.mail_policy import (
    DEFAULT_GROUP_PLAN,
    LEGACY_GROUP_MAP,
    generate_domain_emails,
    pick_email_source_order,
)
from converters.openai_formats import convert_openai, parse_openai_input
from converters.grok_formats import convert_grok, parse_grok_input

ROOT = Path(__file__).resolve().parent

# Promo checker lives under tools/
try:
    _tools_dir = str(ROOT / "tools")
    if _tools_dir not in sys.path:
        sys.path.insert(0, _tools_dir)
except Exception:
    pass
try:
    from chatgpt_promo_check import check_promo as chatgpt_check_promo
except Exception:  # pragma: no cover
    chatgpt_check_promo = None  # type: ignore

GROK_RESULTS_PATH = ROOT / "data" / "grok_results.json"
GROK_LOG_MAX = 300
GROK_TTK_DIR = ROOT / "tools" / "grok_ttk"
GROK_TOKEN_MAX_COUNT = 2000
GROK_TOKEN_MAX_LENGTH = 8192

EMAIL_PLATFORM_ALIASES = {
    "oai": "oai",
    "openai": "oai",
    "chatgpt": "oai",
    "gpt": "oai",
    "grok": "grok",
    "xai": "grok",
    "x.ai": "grok",
}


def normalize_email_platform(value: Any) -> str:
    platform = EMAIL_PLATFORM_ALIASES.get(str(value or "oai").strip().lower(), "")
    if not platform:
        raise ValueError("邮箱平台只支持 oai 或 grok")
    return platform


def request_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def email_project_key(platform: Any) -> str:
    return normalize_email_platform(platform)


def _cfg(config: Any, key: str, default: str = "") -> str:
    # support both object attrs and dict-like via app_config_value fallbacks in caller
    if hasattr(config, key.lower()):
        val = getattr(config, key.lower(), None)
        if val is not None and str(val) != "":
            return str(val)
    # camel / upper from settings map
    return str(default)


def parse_grok_export_tokens(text: str) -> list[str]:
    """Parse and bound one-token-per-line exports from the Grok TTK tool."""
    tokens: list[str] = []
    seen: set[str] = set()
    for raw_line in str(text or "").splitlines():
        token = sanitize_sso(raw_line)
        if not token or token in seen:
            continue
        # TTK SSO exports are typically JWT-like (eyJ...) or long opaque cookies.
        if len(token) < 40:
            continue
        if len(token) > GROK_TOKEN_MAX_LENGTH:
            raise ValueError("Grok token 长度异常，已拒绝导入")
        seen.add(token)
        tokens.append(token)
        if len(tokens) > GROK_TOKEN_MAX_COUNT:
            raise ValueError(f"单次最多导入 {GROK_TOKEN_MAX_COUNT} 个 Grok token")
    if not tokens:
        raise ValueError("文件中没有可导入的 Grok token")
    return tokens


GROK_TTK_STATE_DIR = ROOT / "data" / "grok_ttk"
GROK_TTK_CONFIG_PATH = GROK_TTK_DIR / "config.json"
GROK_TTK_RUNTIME_CONFIG = GROK_TTK_STATE_DIR / "config.runtime.json"
GROK_TTK_HEADLESS = GROK_TTK_DIR / "run_ttk_headless.py"
GROK_TTK_STOP_FLAG = GROK_TTK_STATE_DIR / "stop.flag"
GROK_TTK_STATUS_FILE = GROK_TTK_STATE_DIR / "status.json"
GROK_TTK_RESULTS_FILE = GROK_TTK_STATE_DIR / "results.json"
GROK_TTK_LOG_RETENTION_SECONDS = 30 * 60
GROK_TTK_PUBLIC_LOG_MAX_LINES = 2000
GROK_TTK_LOG_FILE = GROK_TTK_STATE_DIR / "run.log"
GROK_TTK_EXPORT_DIR = GROK_TTK_STATE_DIR / "exports"
GROK_TTK_ACCOUNT_DIR = GROK_TTK_STATE_DIR / "account"

# Config keys exposed to web UI (secrets are returned for operator page; still behind login).
GROK_TTK_PUBLIC_KEYS = [
    "email_provider",
    "register_count",
    "register_threads",
    "thread_start_interval",
    "proxy",
    "traffic_meter",
    "enable_nsfw",
    "duckmail_api_key",
    "yyds_api_key",
    "yyds_jwt",
    "cloudflare_api_base",
    "cloudflare_api_key",
    "cloudflare_auth_mode",
    "cloudflare_custom_auth",
    "cloudflare_path_domains",
    "cloudflare_path_accounts",
    "cloudflare_path_token",
    "cloudflare_path_messages",
    "defaultDomains",
    "user_agent",
    "grok2api_auto_add_local",
    "grok2api_local_token_file",
    "grok2api_pool_name",
    "grok2api_auto_add_remote",
    "grok2api_remote_base",
    "grok2api_remote_app_key",
    "cpa_auto_add",
    "cpa_auth_dir",
    "cpa_remote_url",
    "cpa_management_key",
]


def _ensure_grok_ttk_dirs() -> None:
    for p in (GROK_TTK_STATE_DIR, GROK_TTK_EXPORT_DIR, GROK_TTK_ACCOUNT_DIR):
        p.mkdir(parents=True, exist_ok=True)


def _default_ttk_config() -> dict[str, Any]:
    return {
        "email_provider": "cloudflare",
        "register_count": 1,
        "register_threads": 1,
        "thread_start_interval": 0.8,
        "proxy": "",
        "traffic_meter": False,
        "enable_nsfw": True,
        "duckmail_api_key": "",
        "yyds_api_key": "",
        "yyds_jwt": "",
        "cloudflare_api_base": "",
        "cloudflare_api_key": "",
        "cloudflare_auth_mode": "none",
        "cloudflare_custom_auth": "",
        "cloudflare_path_domains": "/api/domains",
        "cloudflare_path_accounts": "/api/new_address",
        "cloudflare_path_token": "/api/token",
        "cloudflare_path_messages": "/api/mails",
        "defaultDomains": "ai.kfjie.me,sub.kfjie.me,x.kfjie.me,grok.kfjie.me",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "grok2api_auto_add_local": False,
        "grok2api_local_token_file": "",
        "grok2api_pool_name": "ssoBasic",
        "grok2api_auto_add_remote": False,
        "grok2api_remote_base": "",
        "grok2api_remote_app_key": "",
        "cpa_auto_add": False,
        "cpa_auth_dir": "",
        "cpa_remote_url": "",
        "cpa_management_key": "",
        "show_tutorial_on_start": False,
    }


def load_ttk_config() -> dict[str, Any]:
    cfg = _default_ttk_config()
    for candidate in (GROK_TTK_RUNTIME_CONFIG, GROK_TTK_CONFIG_PATH):
        try:
            if candidate.is_file():
                loaded = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    cfg.update(loaded)
                    break
        except Exception:
            continue
    # Fill empty integration defaults from app config when available.
    try:
        from server import app_config_value

        if not cfg.get("grok2api_remote_base"):
            cfg["grok2api_remote_base"] = str(app_config_value("GROK2API_BASE_URL", "") or "")
        if not cfg.get("grok2api_remote_app_key"):
            cfg["grok2api_remote_app_key"] = str(app_config_value("GROK2API_ADMIN_KEY", "") or "")
        if not cfg.get("cpa_auth_dir"):
            cfg["cpa_auth_dir"] = str(app_config_value("CPA_AUTH_DIR", "") or "")
        if not cfg.get("cpa_remote_url"):
            cfg["cpa_remote_url"] = str(app_config_value("CPA_REMOTE_URL", "") or "")
        if not cfg.get("cpa_management_key"):
            cfg["cpa_management_key"] = str(app_config_value("CPA_MANAGEMENT_KEY", "") or "")
        if not cfg.get("proxy"):
            cfg["proxy"] = str(
                app_config_value("GROK_SIGNUP_PROXY", "")
                or app_config_value("UC_SIGNUP_PROXY", "")
                or app_config_value("BROWSER_PROXY", "")
                or ""
            )
    except Exception:
        pass
    return cfg


def save_ttk_config(updates: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_grok_ttk_dirs()
    cfg = load_ttk_config()
    if updates:
        # Accept camelCase from web and snake_case from tools.
        alias = {
            "emailProvider": "email_provider",
            "registerCount": "register_count",
            "registerThreads": "register_threads",
            "trafficMeter": "traffic_meter",
            "threadStartInterval": "thread_start_interval",
            "enableNsfw": "enable_nsfw",
            "duckmailApiKey": "duckmail_api_key",
            "yydsApiKey": "yyds_api_key",
            "yydsJwt": "yyds_jwt",
            "cloudflareApiBase": "cloudflare_api_base",
            "cloudflareApiKey": "cloudflare_api_key",
            "cloudflareAuthMode": "cloudflare_auth_mode",
            "cloudflareCustomAuth": "cloudflare_custom_auth",
            "cloudflarePathDomains": "cloudflare_path_domains",
            "cloudflarePathAccounts": "cloudflare_path_accounts",
            "cloudflarePathToken": "cloudflare_path_token",
            "cloudflarePathMessages": "cloudflare_path_messages",
            "userAgent": "user_agent",
            "grok2apiAutoAddLocal": "grok2api_auto_add_local",
            "grok2apiLocalTokenFile": "grok2api_local_token_file",
            "grok2apiPoolName": "grok2api_pool_name",
            "grok2apiAutoAddRemote": "grok2api_auto_add_remote",
            "grok2apiRemoteBase": "grok2api_remote_base",
            "grok2apiRemoteAppKey": "grok2api_remote_app_key",
            "cpaAutoAdd": "cpa_auto_add",
            "cpaAuthDir": "cpa_auth_dir",
            "cpaRemoteUrl": "cpa_remote_url",
            "cpaManagementKey": "cpa_management_key",
            "cloudflarePaths": "cloudflarePaths",
        }
        normalized: dict[str, Any] = {}
        for k, v in updates.items():
            key = alias.get(k, k)
            normalized[key] = v
        if "cloudflarePaths" in normalized and normalized["cloudflarePaths"]:
            raw_paths = [x.strip() for x in str(normalized.pop("cloudflarePaths")).split(",") if x.strip()]
            if len(raw_paths) >= 4:
                for i, name in enumerate(
                    (
                        "cloudflare_path_domains",
                        "cloudflare_path_accounts",
                        "cloudflare_path_token",
                        "cloudflare_path_messages",
                    )
                ):
                    p = raw_paths[i]
                    normalized[name] = p if p.startswith("/") else f"/{p}"
        for k, v in normalized.items():
            if k in GROK_TTK_PUBLIC_KEYS or k in cfg:
                cfg[k] = v
    # clamps
    try:
        cfg["register_count"] = max(1, min(100, int(cfg.get("register_count") or 1)))
    except Exception:
        cfg["register_count"] = 1
    try:
        cfg["register_threads"] = max(1, min(10, int(cfg.get("register_threads") or 1)))
    except Exception:
        cfg["register_threads"] = 1
    try:
        cfg["thread_start_interval"] = max(0.0, float(cfg.get("thread_start_interval") or 0.8))
    except Exception:
        cfg["thread_start_interval"] = 0.8
    cfg["show_tutorial_on_start"] = False
    # Normalize cliproxy-style host:port:user:pass into URL
    proxy = str(cfg.get("proxy") or "").strip()
    if proxy and "://" not in proxy and proxy.count(":") >= 3:
        host, port, user, password = proxy.split(":", 3)
        cfg["proxy"] = f"http://{user}:{password}@{host}:{port}"
    if "traffic_meter" not in cfg or cfg.get("traffic_meter") is None:
        try:
            from server import app_config_value, parse_bool_flag
            cfg["traffic_meter"] = parse_bool_flag(app_config_value("TRAFFIC_METER_ENABLED", "false"), default=False)
        except Exception:
            cfg["traffic_meter"] = False
    tm = cfg.get("traffic_meter")
    if isinstance(tm, str):
        cfg["traffic_meter"] = tm.strip().lower() in {"1", "true", "yes", "on"}
    else:
        cfg["traffic_meter"] = bool(tm)
    # Prefer writing runtime config (data volume is rw). Also try original path.
    GROK_TTK_RUNTIME_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=4), encoding="utf-8")
    try:
        GROK_TTK_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=4), encoding="utf-8")
    except Exception:
        pass
    return cfg


def public_ttk_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    src = cfg or load_ttk_config()
    out = {k: src.get(k) for k in GROK_TTK_PUBLIC_KEYS}
    out["cloudflarePaths"] = ",".join(
        [
            str(src.get("cloudflare_path_domains") or "/api/domains"),
            str(src.get("cloudflare_path_accounts") or "/api/new_address"),
            str(src.get("cloudflare_path_token") or "/api/token"),
            str(src.get("cloudflare_path_messages") or "/api/mails"),
        ]
    )
    return out


def _read_json_file(path: Path, default: Any) -> Any:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def list_ttk_exports(limit: int = 40) -> list[dict[str, Any]]:
    _ensure_grok_ttk_dirs()
    files: list[Path] = []
    for base in (GROK_TTK_EXPORT_DIR, GROK_TTK_ACCOUNT_DIR, GROK_TTK_DIR / "account"):
        if not base.exists():
            continue
        files.extend([p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in {".txt", ".json"}])
    files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[: max(1, min(limit, 200))]
    rows = []
    for p in files:
        try:
            st = p.stat()
            rows.append(
                {
                    "name": p.name,
                    "path": str(p),
                    "rel": str(p.relative_to(ROOT)) if str(p).startswith(str(ROOT)) else p.name,
                    "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"),
                    "kind": "tokens" if "token" in p.name.lower() or p.name.startswith("tokens_") else "accounts",
                }
            )
        except Exception:
            continue
    return rows


def grok_ttk_status() -> dict[str, Any]:
    """Local TTK tool + live task status for authenticated UI."""
    _ensure_grok_ttk_dirs()
    script = GROK_TTK_DIR / "grok_register_ttk.py"
    headless = GROK_TTK_HEADLESS
    config = GROK_TTK_CONFIG_PATH
    credentials = GROK_TTK_DIR / "mail_credentials.txt"
    task = GROK_TTK_MANAGER.get_state()
    file_status = _read_json_file(GROK_TTK_STATUS_FILE, {})
    return {
        "available": script.is_file() and headless.is_file(),
        "scriptPath": str(script),
        "headlessPath": str(headless),
        "configured": config.is_file() or GROK_TTK_RUNTIME_CONFIG.is_file(),
        "mailCredentialsPresent": credentials.is_file(),
        "executionEnabled": True,
        "stateDir": str(GROK_TTK_STATE_DIR),
        "exports": list_ttk_exports(8),
        "task": task,
        "fileStatus": file_status if isinstance(file_status, dict) else {},
        "message": "网页可启动/停止容器内 TTK 注册任务，并导出 SSO token。",
    }


@dataclass
class GrokTtkState:
    running: bool = False
    stop_requested: bool = False
    phase: str = "idle"
    total: int = 0
    completed: int = 0
    success: int = 0
    failed: int = 0
    pid: int = 0
    started_at: str = ""
    updated_at: str = ""
    finished_at: str = ""
    accounts_file: str = ""
    tokens_file: str = ""
    error: str = ""
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    traffic_meter: bool = False
    traffic: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GrokTtkManager:
    """Start/stop the headless TTK registrar process and stream logs/status."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._state = GrokTtkState()
        self._logs: list[dict[str, str]] = []
        self._meter_session = None

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            if self._meter_session is not None and public_session is not None:
                try:
                    self._state.traffic = public_session(self._meter_session)
                    self._state.traffic_meter = True
                except Exception:
                    pass
            state = self._state.to_dict()
        # Merge live file status written by headless runner.
        file_status = _read_json_file(GROK_TTK_STATUS_FILE, {})
        if isinstance(file_status, dict) and file_status:
            for src, dst in (
                ("running", "running"),
                ("phase", "phase"),
                ("total", "total"),
                ("completed", "completed"),
                ("success", "success"),
                ("failed", "failed"),
                ("pid", "pid"),
                ("accountsFile", "accounts_file"),
                ("tokensFile", "tokens_file"),
                ("error", "error"),
                ("startedAt", "started_at"),
                ("updatedAt", "updated_at"),
                ("finishedAt", "finished_at"),
            ):
                if src in file_status and file_status[src] not in (None, ""):
                    state[dst] = file_status[src]
            if file_status.get("configSnapshot"):
                state["config_snapshot"] = file_status.get("configSnapshot")
        # process liveness
        proc = self._process
        if proc is not None and proc.poll() is None:
            state["running"] = True
            if state.get("phase") in {"", "idle", "completed", "error", "stopped"}:
                state["phase"] = "running"
        else:
            # Status file can remain running=true after crash/restart.
            if proc is None:
                phase = str(state.get("phase") or "")
                finished = bool(state.get("finished_at"))
                pid = state.get("pid")
                alive = False
                if pid:
                    try:
                        os.kill(int(pid), 0)
                        alive = True
                    except Exception:
                        alive = False
                if not alive:
                    if finished or phase in {"completed", "error", "stopped", "running", "starting"}:
                        # If we have no live process under this manager, trust finished/stale as not running.
                        # Only keep running when pid still exists.
                        state["running"] = False
                        if phase in {"running", "starting", ""}:
                            state["phase"] = "stopped" if not finished else phase or "stopped"
            elif proc.poll() is not None:
                state["running"] = False
                if str(state.get("phase") or "") in {"", "running", "starting"}:
                    state["phase"] = "stopped"
        return state

    def _cleanup_old_logs(self) -> None:
        """Keep only last 30 minutes of run.log / memory logs."""
        cutoff = time.time() - float(GROK_TTK_LOG_RETENTION_SECONDS)
        # memory
        with self._lock:
            kept = []
            for item in self._logs:
                ts = None
                raw_t = str(item.get("time") or "")
                if raw_t:
                    try:
                        ts = datetime.fromisoformat(raw_t.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        ts = None
                if ts is None or ts >= cutoff:
                    kept.append(item)
            self._logs = kept[-GROK_LOG_MAX:]
        # file: rewrite only recent lines when possible
        try:
            if not GROK_TTK_LOG_FILE.is_file():
                return
            raw = GROK_TTK_LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            if len(raw) <= 50:
                return
            # Lines look like: [12:11:07] ... (no date). Use mtime window + keep last N as fallback.
            mtime = GROK_TTK_LOG_FILE.stat().st_mtime
            # If file is older than retention entirely, truncate hard to last 200 lines.
            if mtime < cutoff:
                recent = raw[-200:]
            else:
                # Keep a generous tail; precise HH:MM:SS without date is ambiguous across midnight.
                # Retention target is ~30min of active registration logs.
                recent = raw[-1200:]
            text = "\n".join(recent) + ("\n" if recent else "")
            GROK_TTK_LOG_FILE.write_text(text, encoding="utf-8")
        except Exception:
            pass

    def get_logs(self, tail: int = 300) -> list[dict[str, str]]:
        self._cleanup_old_logs()
        with self._lock:
            mem = list(self._logs)
        file_lines: list[dict[str, str]] = []
        try:
            if GROK_TTK_LOG_FILE.is_file():
                raw = GROK_TTK_LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in raw[-(max(tail or 300, 1)) :]:
                    file_lines.append({"time": "", "message": line, "level": "info"})
        except Exception:
            pass
        if file_lines:
            return file_lines
        return mem[-(tail or 300) :]

    def append_log(self, message: str, level: str = "info") -> None:
        entry = {
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "message": str(message),
            "level": level,
        }
        with self._lock:
            self._logs.append(entry)
            while len(self._logs) > GROK_LOG_MAX:
                self._logs.pop(0)
            self._state.updated_at = now_iso()

    def start(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        _ensure_grok_ttk_dirs()
        if not GROK_TTK_HEADLESS.is_file():
            return {"error": "未找到 run_ttk_headless.py", "tool": grok_ttk_status()}
        if not (GROK_TTK_DIR / "grok_register_ttk.py").is_file():
            return {"error": "未找到 grok_register_ttk.py", "tool": grok_ttk_status()}
        options = options or {}
        with self._lock:
            if self._state.running and self._process and self._process.poll() is None:
                return {"error": "TTK 注册任务已在运行中", "tool": grok_ttk_status(), "ttkState": self._state.to_dict()}
            # persist config first
            cfg = save_ttk_config(options)
            self._logs = []
            try:
                if GROK_TTK_STOP_FLAG.exists():
                    GROK_TTK_STOP_FLAG.unlink()
            except Exception:
                pass
            self._state = GrokTtkState(
                running=True,
                phase="starting",
                total=int(cfg.get("register_count") or 1),
                started_at=now_iso(),
                updated_at=now_iso(),
                config_snapshot={
                    "email_provider": cfg.get("email_provider"),
                    "register_count": cfg.get("register_count"),
                    "register_threads": cfg.get("register_threads"),
                    "proxy": cfg.get("proxy"),
                    "traffic_meter": bool(cfg.get("traffic_meter")),
                    "enable_nsfw": cfg.get("enable_nsfw"),
                    "cloudflare_api_base": cfg.get("cloudflare_api_base"),
                    "cloudflare_auth_mode": cfg.get("cloudflare_auth_mode"),
                    "defaultDomains": cfg.get("defaultDomains"),
                    "grok2api_auto_add_remote": cfg.get("grok2api_auto_add_remote"),
                    "cpa_auto_add": cfg.get("cpa_auto_add"),
                },
                traffic_meter=bool(cfg.get("traffic_meter")),
                traffic=None,
            )
        self._thread = threading.Thread(target=self._run, args=(cfg,), daemon=True)
        self._thread.start()
        return {"tool": grok_ttk_status(), "ttkState": self.get_state(), "config": public_ttk_config(cfg)}

    def stop(self) -> dict[str, Any]:
        process = None
        with self._lock:
            self._state.stop_requested = True
            self._state.phase = "stopping"
            self._state.updated_at = now_iso()
            process = self._process
        try:
            GROK_TTK_STOP_FLAG.write_text(now_iso(), encoding="utf-8")
        except Exception:
            pass
        if process and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
            # hard kill after short wait
            def _kill_later(p: subprocess.Popen) -> None:
                time.sleep(8)
                if p.poll() is None:
                    try:
                        p.kill()
                    except Exception:
                        pass

            threading.Thread(target=_kill_later, args=(process,), daemon=True).start()
        self.append_log("[!] 已请求停止 TTK 任务")
        return {"tool": grok_ttk_status(), "ttkState": self.get_state()}

    def _run(self, cfg: dict[str, Any]) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["GROK_TTK_STATE_DIR"] = str(GROK_TTK_STATE_DIR)
        env["GROK_CF_CLEARANCE_ENABLED"] = "true"
        env["GROK_CF_CLEARANCE_API_URL"] = "http://127.0.0.1:18191/v1"
        env["GROK_CF_CLEARANCE_TARGET_URL"] = "https://accounts.x.ai/sign-up"
        # Prefer runtime config (writable).
        env["GROK_TTK_CONFIG"] = str(
            GROK_TTK_RUNTIME_CONFIG if GROK_TTK_RUNTIME_CONFIG.is_file() else GROK_TTK_CONFIG_PATH
        )
        display = env.get("BROWSER_DISPLAY") or env.get("DISPLAY") or ":1"
        env["DISPLAY"] = display if str(display).startswith(":") else f":{display}"
        env["BROWSER_DISPLAY"] = env["DISPLAY"]

        run_cfg = dict(cfg)
        upstream_proxy = str(run_cfg.get("proxy") or "").strip()
        self._meter_session = None
        if bool(run_cfg.get("traffic_meter")):
            if not upstream_proxy:
                self.append_log("[!] 已开启流量统计但未填写代理，已取消启动", "error")
                with self._lock:
                    self._state.running = False
                    self._state.phase = "error"
                    self._state.error = "traffic_meter requires proxy"
                    self._state.finished_at = now_iso()
                    self._state.updated_at = now_iso()
                return
            if start_meter_for_proxy is None:
                self.append_log("[!] 流量统计模块不可用", "error")
                with self._lock:
                    self._state.running = False
                    self._state.phase = "error"
                    self._state.error = "traffic_meter module missing"
                    self._state.finished_at = now_iso()
                    self._state.updated_at = now_iso()
                return
            try:
                local_url, self._meter_session = start_meter_for_proxy(
                    upstream_proxy,
                    service="grok_ttk",
                    run_id=str(self._state.started_at or now_iso()),
                    meta={
                        "register_count": run_cfg.get("register_count"),
                        "register_threads": run_cfg.get("register_threads"),
                    },
                )
                run_cfg["proxy"] = local_url
                run_cfg["upstream_proxy"] = upstream_proxy
                with self._lock:
                    self._state.traffic_meter = True
                    self._state.traffic = public_session(self._meter_session) if public_session else None
                self.append_log(f"[*] 流量统计已启用 → 本地 {local_url}（上游仍是你粘贴的代理）")
            except Exception as error:
                self.append_log(f"[!] 流量统计启动失败: {error}", "error")
                with self._lock:
                    self._state.running = False
                    self._state.phase = "error"
                    self._state.error = f"traffic_meter: {error}"
                    self._state.finished_at = now_iso()
                    self._state.updated_at = now_iso()
                return

        cmd = [
            sys.executable,
            str(GROK_TTK_HEADLESS),
            "--config-json",
            json.dumps(run_cfg, ensure_ascii=False),
        ]
        self.append_log(f"[*] 启动 TTK: {' '.join(cmd[:3])} ...")
        try:
            process = subprocess.Popen(
                cmd,
                cwd=str(GROK_TTK_DIR),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as error:
            self.append_log(f"启动失败: {error}", "error")
            if stop_meter is not None and self._meter_session is not None:
                try:
                    stop_meter(self._meter_session, status="error")
                except Exception:
                    pass
                self._meter_session = None
            with self._lock:
                self._state.running = False
                self._state.phase = "error"
                self._state.error = str(error)
                self._state.finished_at = now_iso()
                self._state.updated_at = now_iso()
            return
        with self._lock:
            self._process = process
            self._state.pid = int(process.pid or 0)
            self._state.phase = "running"
            self._state.updated_at = now_iso()
        assert process.stdout is not None
        for line in process.stdout:
            self.append_log(line.rstrip())
        code = process.wait()
        traffic = None
        if stop_meter is not None and self._meter_session is not None:
            try:
                status = "stopped" if self._state.stop_requested else ("completed" if code == 0 else "error")
                traffic = stop_meter(self._meter_session, status=status)
            except Exception as me:
                self.append_log(f"[!] 流量统计结束异常: {me}", "error")
            self._meter_session = None
        with self._lock:
            self._state.running = False
            if self._state.stop_requested:
                self._state.phase = "stopped"
            elif code == 0:
                self._state.phase = "completed"
            else:
                self._state.phase = "error"
                self._state.error = f"exit={code}"
            # merge final file status
            file_status = _read_json_file(GROK_TTK_STATUS_FILE, {})
            if isinstance(file_status, dict):
                self._state.success = int(file_status.get("success") or self._state.success or 0)
                self._state.failed = int(file_status.get("failed") or self._state.failed or 0)
                self._state.completed = int(file_status.get("completed") or (self._state.success + self._state.failed))
                self._state.accounts_file = str(file_status.get("accountsFile") or self._state.accounts_file or "")
                self._state.tokens_file = str(file_status.get("tokensFile") or self._state.tokens_file or "")
            if traffic is not None:
                self._state.traffic = traffic
                self._state.traffic_meter = True
            self._state.finished_at = now_iso()
            self._state.updated_at = now_iso()
            self._process = None
        if traffic is not None:
            self.append_log(
                f"[*] 流量统计: 上行 {traffic.get('bytes_sent', 0)}B / 下行 {traffic.get('bytes_recv', 0)}B / 合计 {traffic.get('bytes_total_h') or traffic.get('bytes_total')}"
            )
        self.append_log(f"[*] TTK 进程结束 code={code}")


GROK_TTK_MANAGER = GrokTtkManager()

def public_ttk_logs(tail: int = 300) -> dict[str, Any]:
    try:
        tail_i = max(1, min(int(tail or 300), GROK_TTK_PUBLIC_LOG_MAX_LINES))
    except Exception:
        tail_i = 300
    try:
        GROK_TTK_MANAGER._cleanup_old_logs()
    except Exception:
        pass
    logs = GROK_TTK_MANAGER.get_logs(tail=tail_i)
    state = GROK_TTK_MANAGER.get_state()
    return {
        "ok": True,
        "retentionSeconds": GROK_TTK_LOG_RETENTION_SECONDS,
        "tail": tail_i,
        "count": len(logs),
        "ttkState": {
            "running": state.get("running"),
            "phase": state.get("phase"),
            "success": state.get("success"),
            "failed": state.get("failed"),
            "completed": state.get("completed"),
            "total": state.get("total"),
            "updatedAt": state.get("updated_at") or state.get("updatedAt"),
        },
        "logs": logs,
        "updatedAt": now_iso(),
    }



class CpaMonitor:
    """Poll CPA xAI auth pool health and auto-start Grok TTK when usable accounts are low.

    Adaptive interval:
    - base (default 60s): normal healthy polling / public recommended interval
    - min (default 10s): when usable accounts are below threshold
    - max (default 300s): after staying healthy long enough without refill
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_trigger_ts = 0.0
        self._last_public_wake_ts = 0.0
        self._public_poll_seconds = 60
        self._current_interval = 60
        self._healthy_since_ts = 0.0
        self._low_since_ts = 0.0
        self._state: dict[str, Any] = {
            "running": False,
            "lastCheckAt": "",
            "lastError": "",
            "lastTriggerAt": "",
            "lastTriggerResult": None,
            "health": None,
            "runtime": None,
            "adaptive": {
                "mode": "base",
                "currentIntervalSeconds": 60,
                "recommendedPollSeconds": 60,
            },
        }

    def _settings(self) -> dict[str, Any]:
        from server import app_config_value, parse_bool_flag, parse_positive_int

        base = parse_positive_int(app_config_value("CPA_MONITOR_INTERVAL_SECONDS", "60"), default=60)
        min_iv = parse_positive_int(app_config_value("CPA_MONITOR_MIN_INTERVAL_SECONDS", "10"), default=10)
        max_iv = parse_positive_int(app_config_value("CPA_MONITOR_MAX_INTERVAL_SECONDS", "300"), default=300)
        slow_after = parse_positive_int(
            app_config_value("CPA_MONITOR_HEALTHY_SLOWDOWN_AFTER_SECONDS", "1800"), default=1800
        )
        # Clamp: min <= base <= max, and min cannot go below 10 by policy for public consumers.
        min_iv = max(10, min_iv)
        base = max(min_iv, base)
        max_iv = max(base, max_iv)
        return {
            "enabled": parse_bool_flag(app_config_value("CPA_MONITOR_ENABLED", "true"), default=True),
            "minOkAccounts": parse_positive_int(app_config_value("CPA_MONITOR_MIN_OK_ACCOUNTS", "5"), default=5),
            "intervalSeconds": base,
            "minIntervalSeconds": min_iv,
            "maxIntervalSeconds": max_iv,
            "healthySlowdownAfterSeconds": slow_after,
            "triggerCooldownSeconds": parse_positive_int(
                app_config_value("CPA_MONITOR_TRIGGER_COOLDOWN_SECONDS", "0"), default=0
            ),
            "publicWakeMinIntervalSeconds": parse_positive_int(
                app_config_value("CPA_PUBLIC_WAKE_MIN_INTERVAL_SECONDS", "60"), default=60
            ),
            "registerCount": parse_positive_int(app_config_value("CPA_MONITOR_REGISTER_COUNT", "2"), default=2),
            "registerThreads": parse_positive_int(app_config_value("CPA_MONITOR_REGISTER_THREADS", "2"), default=2),
            "proxy": str(
                app_config_value("CPA_MONITOR_PROXY", "")
                or app_config_value("GROK_SIGNUP_PROXY", "")
                or ""
            ).strip(),
            "remoteUrl": str(app_config_value("CPA_REMOTE_URL", "http://172.19.0.1:8317")).rstrip("/"),
            "managementKey": str(app_config_value("CPA_MANAGEMENT_KEY", "")).strip(),
            "logDir": str(app_config_value("CPA_LOG_DIR", "/opt/cliproxyapi/logs")).strip(),
            "logWindowSeconds": parse_positive_int(
                app_config_value("CPA_LOG_WINDOW_SECONDS", "86400"), default=86400
            ),
        }

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, name="cpa-monitor", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)
            adaptive = dict(state.get("adaptive") or {})
            current_interval = int(self._current_interval or 60)
        settings = self._settings()
        state["settings"] = settings
        adaptive.setdefault("mode", "base")
        adaptive["currentIntervalSeconds"] = current_interval
        # Public consumers: 60s by default; when pool is low recommend min interval (down to 10s).
        health = state.get("health") if isinstance(state.get("health"), dict) else {}
        enough = health.get("enough")
        if enough is False:
            adaptive["recommendedPollSeconds"] = int(settings.get("minIntervalSeconds") or 10)
        else:
            adaptive["recommendedPollSeconds"] = int(settings.get("intervalSeconds") or 60)
        adaptive["baseIntervalSeconds"] = int(settings.get("intervalSeconds") or 60)
        adaptive["minIntervalSeconds"] = int(settings.get("minIntervalSeconds") or 10)
        adaptive["maxIntervalSeconds"] = int(settings.get("maxIntervalSeconds") or 300)
        state["adaptive"] = adaptive
        try:
            state["ttkRunning"] = bool(GROK_TTK_MANAGER.get_state().get("running"))
        except Exception:
            state["ttkRunning"] = False
        return state

    def _compute_next_interval(self, health: dict[str, Any], settings: dict[str, Any]) -> tuple[int, str]:
        """Return (seconds, mode) for the next sleep based on pool health."""
        base = int(settings.get("intervalSeconds") or 60)
        min_iv = int(settings.get("minIntervalSeconds") or 10)
        max_iv = int(settings.get("maxIntervalSeconds") or 300)
        slow_after = int(settings.get("healthySlowdownAfterSeconds") or 1800)
        now_ts = time.time()
        enough = bool(health.get("enough"))
        ok = bool(health.get("ok"))
        management_ok = bool(health.get("managementOk", ok))

        if not enough or not management_ok:
            if self._low_since_ts <= 0:
                self._low_since_ts = now_ts
            self._healthy_since_ts = 0.0
            return min_iv, "low"

        # An upstream quota/cooldown is not a reason to hammer the management
        # endpoint every 10s. Keep the normal interval while retaining the
        # degraded request status in the UI.
        if health.get("requestAvailable") is False:
            self._low_since_ts = 0.0
            if self._healthy_since_ts <= 0:
                self._healthy_since_ts = now_ts
            return base, "request_degraded"

        # healthy path
        self._low_since_ts = 0.0
        if self._healthy_since_ts <= 0:
            self._healthy_since_ts = now_ts
        healthy_for = now_ts - self._healthy_since_ts

        # If TTK just triggered, keep scanning a bit faster until pool recovers solidly.
        recent_trigger = bool(self._last_trigger_ts and (now_ts - self._last_trigger_ts) < 600)
        if recent_trigger and healthy_for < 180:
            return max(min_iv, min(base, 30)), "recovering"

        if healthy_for >= slow_after:
            return max_iv, "idle"
        return base, "base"

    def _set_state(self, **kwargs: Any) -> None:
        with self._lock:
            self._state.update(kwargs)

    @staticmethod
    def _parse_maybe_ts(value: Any) -> float | None:
        if value is None or value is False:
            return None
        if isinstance(value, (int, float)):
            ts = float(value)
            # ms epoch
            if ts > 1e12:
                ts = ts / 1000.0
            return ts if ts > 0 else None
        text = str(value).strip()
        if not text:
            return None
        if text.isdigit():
            ts = float(text)
            if ts > 1e12:
                ts = ts / 1000.0
            return ts
        # ISO-ish
        try:
            from datetime import datetime

            cleaned = text.replace("Z", "+00:00")
            return datetime.fromisoformat(cleaned).timestamp()
        except Exception:
            return None

    @classmethod
    def _cooldown_active(cls, item: dict[str, Any], *, now_ts: float | None = None) -> tuple[bool, float | None]:
        """Return (active, until_ts). Expired cooldown => active False (count as normal)."""
        now = time.time() if now_ts is None else now_ts
        until_keys = (
            "cooldown_until",
            "cooled_until",
            "cooling_until",
            "unavailable_until",
            "rate_limit_reset_at",
            "rateLimitResetAt",
            "next_retry_at",
            "retry_after",
            "retryAfter",
        )
        until = None
        for key in until_keys:
            if key in item and item.get(key) not in (None, "", 0, "0"):
                until = cls._parse_maybe_ts(item.get(key))
                if until is not None:
                    break
        # nested common shapes
        for nest_key in ("cooldown", "cooling", "rate_limit", "quota"):
            nest = item.get(nest_key)
            if isinstance(nest, dict):
                for key in ("until", "reset_at", "resetAt", "end", "expires_at", "expiresAt"):
                    if nest.get(key) not in (None, "", 0, "0"):
                        until = cls._parse_maybe_ts(nest.get(key))
                        if until is not None:
                            break
            if until is not None:
                break
        if until is not None:
            return until > now, until

        # Boolean/string flags without until: treat as currently cooling only if explicitly true
        for key in ("cooldown", "cooling", "in_cooldown", "is_cooling"):
            val = item.get(key)
            if isinstance(val, bool):
                return val, None
            if isinstance(val, str) and val.strip().lower() in {"1", "true", "yes", "on", "cooling", "cooldown"}:
                return True, None
        return False, None

    @classmethod
    def _limit_markers_in_text(cls, text: str) -> bool:
        msg = (text or "").lower()
        return any(
            marker in msg
            for marker in (
                "spending-limit",
                "personal-team-blocked",
                "rate limit",
                "rate_limit",
                "quota",
                "resource_exhausted",
                "too many requests",
                "429",
                "cooling",
                "cooldown",
            )
        )

    @classmethod
    def _runtime_event_from_error_file(cls, path: Path) -> dict[str, Any] | None:
        """Classify one CPA request error without reading/exposing its body."""
        try:
            size = path.stat().st_size
            with path.open("rb") as fh:
                fh.seek(max(0, size - 32768))
                text = fh.read().decode("utf-8", errors="replace")
        except Exception:
            return None
        status_match = re.search(r"Status:\s*(\d{3})", text)
        if not status_match:
            return None
        status = int(status_match.group(1))
        lowered = text.lower()
        if "auth_unavailable" in lowered or "no auth available" in lowered:
            state, message = "pool_unavailable", "CPA 暂无可调度认证（上游限流后池级熔断）"
        elif any(x in lowered for x in ("free-usage-exhausted", "usage exhausted", "spending-limit", "tokens (actual/limit)")):
            state, message = "quota_exhausted", "上游额度耗尽（滚动窗口内不可用）"
        elif status == 429 or cls._limit_markers_in_text(lowered):
            state, message = "rate_limited", "上游限流（HTTP 429）"
        else:
            state, message = "error", f"上游请求失败（HTTP {status}）"
        ts_matches = re.findall(r"Timestamp:\s*([^\s]+)", text)
        event_ts = cls._parse_maybe_ts(ts_matches[-1] if ts_matches else None) or path.stat().st_mtime
        return {
            "status": state,
            "message": message,
            "statusCode": status,
            "at": datetime.fromtimestamp(event_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "timestamp": event_ts,
            "source": path.name,
        }

    @classmethod
    def _read_runtime_events(cls, log_dir: str, window_seconds: int) -> dict[str, Any]:
        """Summarize real CPA request outcomes; never probes an account."""
        now_ts = time.time()
        root = Path(log_dir).expanduser()
        if not root.is_dir():
            return {"status": "unknown", "available": None, "message": "CPA 请求日志未挂载", "logAvailable": False}
        failures: list[dict[str, Any]] = []
        try:
            for path in root.glob("error-v1-responses-*.log"):
                try:
                    if now_ts - path.stat().st_mtime > max(300, window_seconds):
                        continue
                except OSError:
                    continue
                event = cls._runtime_event_from_error_file(path)
                if event:
                    failures.append(event)
        except Exception:
            pass

        request_events: list[tuple[float, int]] = []
        main = root / "main.log"
        try:
            with main.open("rb") as fh:
                fh.seek(max(0, main.stat().st_size - 2 * 1024 * 1024))
                text = fh.read().decode("utf-8", errors="replace")
            line_re = re.compile(r"^\[([^\]]+)\].*?\b(\d{3})\s+\|.*?POST\s+\"/v1/responses\"", re.M)
            for match in line_re.finditer(text):
                try:
                    ts = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
                    request_events.append((ts, int(match.group(2))))
                except Exception:
                    continue
        except Exception:
            pass

        latest_failure = max(failures, key=lambda x: float(x.get("timestamp") or 0), default=None)
        historical_failure = latest_failure
        latest_rate_limit = max(
            (x for x in failures if x.get("statusCode") == 429 or x.get("status") in {"quota_exhausted", "rate_limited"}),
            key=lambda x: float(x.get("timestamp") or 0),
            default=None,
        )
        latest_pool_unavailable = max(
            (x for x in failures if x.get("status") == "pool_unavailable"),
            key=lambda x: float(x.get("timestamp") or 0),
            default=None,
        )
        latest_request = max(request_events, key=lambda x: x[0], default=None)
        latest_success = max((x for x in request_events if x[1] < 400), key=lambda x: x[0], default=None)
        if latest_failure and latest_success and latest_success[0] > float(latest_failure.get("timestamp") or 0):
            latest_failure = None
        if latest_failure:
            age = max(0, int(now_ts - float(latest_failure.get("timestamp") or now_ts)))
            recent = age <= max(300, window_seconds)
            return {
                "status": latest_failure.get("status") if recent else "stale",
                "available": False if recent else None,
                "message": latest_failure.get("message") if recent else "最近一次 CPA 上游失败已过期",
                "statusCode": latest_failure.get("statusCode"),
                "lastFailureAt": latest_failure.get("at"),
                "lastFailureAgeSeconds": age,
                "lastRequestAt": datetime.fromtimestamp(latest_request[0], tz=timezone.utc).isoformat().replace("+00:00", "Z") if latest_request else "",
                "lastRequestStatusCode": latest_request[1] if latest_request else None,
                "recent429": bool(latest_rate_limit and now_ts - float(latest_rate_limit.get("timestamp") or 0) <= max(300, window_seconds)),
                "lastRateLimitAt": latest_rate_limit.get("at") if latest_rate_limit else "",
                "recentPoolUnavailable": bool(latest_pool_unavailable and now_ts - float(latest_pool_unavailable.get("timestamp") or 0) <= max(300, window_seconds)),
                "lastPoolUnavailableAt": latest_pool_unavailable.get("at") if latest_pool_unavailable else "",
                "logAvailable": True,
            }
        historical_age = max(0, int(now_ts - float(historical_failure.get("timestamp") or now_ts))) if historical_failure else None
        return {
            "status": "ok" if latest_success else "unknown",
            "available": True if latest_success else None,
            "message": "最近真实请求成功" if latest_success else "暂无可判定的真实请求",
            "lastFailureAt": historical_failure.get("at") if historical_failure else "",
            "lastFailureAgeSeconds": historical_age,
            "lastRequestAt": datetime.fromtimestamp(latest_request[0], tz=timezone.utc).isoformat().replace("+00:00", "Z") if latest_request else "",
            "lastRequestStatusCode": latest_request[1] if latest_request else None,
            "recent429": bool(latest_rate_limit and now_ts - float(latest_rate_limit.get("timestamp") or 0) <= max(300, window_seconds)),
            "lastRateLimitAt": latest_rate_limit.get("at") if latest_rate_limit else "",
            "recentPoolUnavailable": bool(latest_pool_unavailable and now_ts - float(latest_pool_unavailable.get("timestamp") or 0) <= max(300, window_seconds)),
            "lastPoolUnavailableAt": latest_pool_unavailable.get("at") if latest_pool_unavailable else "",
            "logAvailable": bool(request_events or failures),
        }

    @classmethod
    def _is_usable_auth(cls, item: dict[str, Any], *, now_ts: float | None = None) -> bool:
        if not isinstance(item, dict):
            return False
        if item.get("disabled"):
            return False
        # If marked unavailable only because of cooldown that already expired, treat as usable.
        cooling, until = cls._cooldown_active(item, now_ts=now_ts)
        if item.get("unavailable") and cooling:
            return False
        if item.get("unavailable") and until is not None and not cooling:
            # cooldown over -> normal account
            pass
        elif item.get("unavailable"):
            # unavailable for other reasons
            msg = str(item.get("status_message") or item.get("error") or "").lower()
            if cls._limit_markers_in_text(msg) and until is None:
                # still limited without clear end; keep unusable
                return False
            if not msg:
                return False

        status = str(item.get("status") or "").strip().lower()
        # cooled / cooling statuses are usable only after cooldown ends
        if status in {"cooling", "cooldown", "rate_limited", "limited"}:
            if cooling:
                return False
            # cooled down already
        elif status and status not in {"active", "ok", "success", "normal", "ready", "idle", ""}:
            # hard bad states
            if status in {"error", "invalid", "expired", "revoked", "banned", "disabled"}:
                return False

        provider = str(item.get("provider") or item.get("type") or item.get("account_type") or "").lower()
        name = str(item.get("name") or item.get("path") or item.get("account") or "").lower()
        if provider and provider not in {"xai", "grok", "oauth", "cli", ""}:
            if "xai" not in provider and "grok" not in provider:
                return False
        if name and not any(x in name for x in ("xai", "grok", "@")) and provider not in {"xai", "grok", "oauth", ""}:
            return False

        msg = str(item.get("status_message") or item.get("error") or "").lower()
        hard_bad = (
            "invalid",
            "expired",
            "revoked",
            "unauthorized",
            "forbidden",
            "blocked",
            "personal-team-blocked",
        )
        if any(m in msg for m in hard_bad):
            return False
        # temporary limit markers: only unusable while cooldown active
        if cls._limit_markers_in_text(msg):
            if cooling:
                return False
            if until is None:
                # no until timestamp; if status is still active and not unavailable, count usable
                if item.get("unavailable"):
                    return False
                if status in {"cooling", "cooldown", "rate_limited", "limited"}:
                    return False
        return True

    def fetch_health(self) -> dict[str, Any]:
        settings = self._settings()
        from integrations.common import http_json

        checked_at = now_iso()
        if not settings["remoteUrl"] or not settings["managementKey"]:
            return {
                "ok": False,
                "status": "misconfigured",
                "message": "CPA_REMOTE_URL / CPA_MANAGEMENT_KEY 未配置",
                "totalAccounts": 0,
                "okAccounts": 0,
                "limitedAccounts": 0,
                "disabledAccounts": 0,
                "minOkAccounts": settings["minOkAccounts"],
                "proxyUrl": "",
                "checkedAt": checked_at,
            }

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {settings['managementKey']}",
            "X-Management-Key": settings["managementKey"],
        }
        status, payload, raw = http_json(
            "GET",
            f"{settings['remoteUrl']}/v0/management/auth-files",
            headers=headers,
            timeout=12,
        )
        if status != 200 or not isinstance(payload, dict):
            return {
                "ok": False,
                "status": "error",
                "message": f"CPA auth-files HTTP {status}: {(raw or '')[:180]}",
                "totalAccounts": 0,
                "okAccounts": 0,
                "limitedAccounts": 0,
                "disabledAccounts": 0,
                "minOkAccounts": settings["minOkAccounts"],
                "proxyUrl": "",
                "checkedAt": checked_at,
            }

        files = payload.get("files") if isinstance(payload.get("files"), list) else []
        ok_accounts = 0
        limited = 0
        cooled_active = 0
        recovered_from_cooldown = 0
        disabled = 0
        recent_failure_accounts = 0
        recent_success_accounts = 0
        recent_failed_requests = 0
        recent_success_requests = 0
        cumulative_failed_requests = 0
        cumulative_success_requests = 0
        sample: list[str] = []
        now_ts = time.time()
        for item in files:
            if not isinstance(item, dict):
                continue
            if item.get("disabled"):
                disabled += 1
            try:
                cumulative_failed_requests += max(0, int(item.get("failed") or 0))
                cumulative_success_requests += max(0, int(item.get("success") or 0))
            except (TypeError, ValueError):
                pass
            recent = item.get("recent_requests")
            if isinstance(recent, list):
                # Keep a short 30-minute view; CPA's buckets are normally 10m.
                buckets = [x for x in recent[-3:] if isinstance(x, dict)]
                try:
                    failed_now = sum(max(0, int(x.get("failed") or 0)) for x in buckets)
                    success_now = sum(max(0, int(x.get("success") or 0)) for x in buckets)
                except (TypeError, ValueError):
                    failed_now = success_now = 0
                recent_failed_requests += failed_now
                recent_success_requests += success_now
                recent_failure_accounts += int(failed_now > 0)
                recent_success_accounts += int(success_now > 0)
            cooling, until = self._cooldown_active(item, now_ts=now_ts)
            msg = str(item.get("status_message") or item.get("error") or "")
            looks_limited = self._limit_markers_in_text(msg) or cooling
            status = str(item.get("status") or "").strip().lower()
            if status in {"cooling", "cooldown", "rate_limited", "limited"}:
                looks_limited = True
            if cooling:
                cooled_active += 1
                limited += 1
            elif looks_limited and until is not None and until <= now_ts:
                # cooldown over -> normal again
                recovered_from_cooldown += 1
            elif looks_limited and item.get("unavailable"):
                limited += 1
            elif looks_limited and status in {"cooling", "cooldown", "rate_limited", "limited"}:
                limited += 1
            # permanent spend-block still counts limited if message says so and not clearly expired
            if ("spending-limit" in msg.lower() or "personal-team-blocked" in msg.lower()) and not (
                until is not None and until <= now_ts
            ):
                if not cooling:
                    limited += 1

            if self._is_usable_auth(item, now_ts=now_ts):
                ok_accounts += 1
                email = str(item.get("email") or item.get("account") or item.get("name") or "")
                if email and len(sample) < 8:
                    sample.append(email)

        # Also surface live proxy-url from management config (best effort).
        proxy_url = ""
        try:
            c_status, c_payload, _ = http_json(
                "GET",
                f"{settings['remoteUrl']}/v0/management/config",
                headers=headers,
                timeout=8,
            )
            if c_status == 200 and isinstance(c_payload, dict):
                proxy_url = str(c_payload.get("proxy-url") or c_payload.get("proxy_url") or "")
        except Exception:
            pass

        enough = ok_accounts >= int(settings["minOkAccounts"])
        runtime = self._read_runtime_events(settings.get("logDir") or "/opt/cliproxyapi/logs", int(settings.get("logWindowSeconds") or 86400))
        management_status = "healthy" if enough else "low"
        request_unavailable = runtime.get("available") is False
        # de-dup limited counter if permanent block double-counted
        limited = min(int(limited), len(files))
        message = (
            f"可用账号 {ok_accounts}/{len(files)}，阈值 {settings['minOkAccounts']}"
            + ("，充足" if enough else "，不足将自动注册")
        )
        if cooled_active:
            message += f"，冷却中 {cooled_active}"
        if recovered_from_cooldown:
            message += f"，冷却已恢复 {recovered_from_cooldown}"
        if limited and not cooled_active:
            message += f"，限流/额度 {limited}"
        if runtime.get("status") in {"quota_exhausted", "rate_limited", "pool_unavailable"}:
            message += f"；真实请求：{runtime.get('message')}"
        return {
            "ok": not request_unavailable,
            "status": runtime.get("status") if request_unavailable else management_status,
            "managementStatus": management_status,
            "message": message,
            "totalAccounts": len(files),
            "okAccounts": ok_accounts,
            "limitedAccounts": limited,
            "coolingAccounts": cooled_active,
            "recoveredFromCooldown": recovered_from_cooldown,
            "disabledAccounts": disabled,
            "recentFailureAccounts": recent_failure_accounts,
            "recentSuccessAccounts": recent_success_accounts,
            "recentFailedRequests": recent_failed_requests,
            "recentSuccessRequests": recent_success_requests,
            "cumulativeFailedRequests": cumulative_failed_requests,
            "cumulativeSuccessRequests": cumulative_success_requests,
            "minOkAccounts": settings["minOkAccounts"],
            "enough": enough,
            "sampleEmails": sample,
            "proxyUrl": proxy_url,
            "managementOk": True,
            "requestStatus": runtime.get("status", "unknown"),
            "requestAvailable": runtime.get("available"),
            "requestMessage": runtime.get("message", ""),
            "lastFailureAt": runtime.get("lastFailureAt", ""),
            "lastFailureAgeSeconds": runtime.get("lastFailureAgeSeconds"),
            "recent429": runtime.get("recent429", False),
            "lastRateLimitAt": runtime.get("lastRateLimitAt", ""),
            "recentPoolUnavailable": runtime.get("recentPoolUnavailable", False),
            "lastPoolUnavailableAt": runtime.get("lastPoolUnavailableAt", ""),
            "lastRequestStatusCode": runtime.get("lastRequestStatusCode"),
            "runtimeLogAvailable": runtime.get("logAvailable", False),
            "checkedAt": checked_at,
        }

    def check_once(self, *, trigger: bool = False, force: bool = False) -> dict[str, Any]:
        """Poll CPA health.

        trigger=True: allow auto-start when usable accounts are below threshold.
        force=True: start even when currently enough (manual补号).
        """
        settings = self._settings()
        if not settings["enabled"] and not trigger and not force:
            state = {
                "status": "disabled",
                "ok": False,
                "message": "CPA 监控未启用",
                "checkedAt": now_iso(),
            }
            self._set_state(lastCheckAt=state["checkedAt"], lastError="", health=state)
            return self.status()

        try:
            health = self.fetch_health()
            self._set_state(lastCheckAt=health.get("checkedAt") or now_iso(), lastError="", health=health)
        except Exception as error:
            health = {
                "ok": False,
                "status": "error",
                "message": str(error),
                "totalAccounts": 0,
                "okAccounts": 0,
                "minOkAccounts": settings["minOkAccounts"],
                "checkedAt": now_iso(),
            }
            self._set_state(lastCheckAt=health["checkedAt"], lastError=str(error), health=health)
            return self.status()

        if force or trigger:
            result = self._maybe_trigger(health, settings, force=force)
            if result is not None:
                self._set_state(lastTriggerResult=result)
        return self.status()

    def _maybe_trigger(self, health: dict[str, Any], settings: dict[str, Any], *, force: bool = False) -> dict[str, Any] | None:
        now_ts = time.time()
        if not force and health.get("enough"):
            return {"triggered": False, "reason": "enough_accounts", "checkedAt": now_iso()}

        if not force and not health.get("ok"):
            return {
                "triggered": False,
                "reason": "health_not_ok",
                "message": health.get("message"),
                "checkedAt": now_iso(),
            }

        cooldown = int(settings.get("triggerCooldownSeconds") or 0)
        if cooldown > 0 and (not force) and self._last_trigger_ts and (now_ts - self._last_trigger_ts) < cooldown:
            return {
                "triggered": False,
                "reason": "cooldown",
                "cooldownSeconds": cooldown,
                "remainingSeconds": int(cooldown - (now_ts - self._last_trigger_ts)),
                "checkedAt": now_iso(),
            }

        ttk_state = GROK_TTK_MANAGER.get_state()
        if ttk_state.get("running"):
            return {
                "triggered": False,
                "reason": "ttk_already_running",
                "ttkState": ttk_state,
                "checkedAt": now_iso(),
            }

        ok_accounts = int(health.get("okAccounts") or 0)
        min_ok = int(settings["minOkAccounts"] or 1)
        need = max(0, min_ok - ok_accounts)
        if not force and need <= 0:
            return {"triggered": False, "reason": "enough_accounts", "checkedAt": now_iso()}

        count = max(1, min(10, int(settings.get("registerCount") or (need or 1))))
        threads = max(1, min(5, int(settings.get("registerThreads") or 1)))
        proxy = settings.get("proxy") or ""

        start_opts = {
            "register_count": count,
            "register_threads": threads,
            "proxy": proxy,
            "email_provider": "cloudflare",
            "cpa_auto_add": True,
            "grok2api_auto_add_remote": True,
            "cloudflare_api_base": "https://apimail.kfjie.me",
            "cloudflare_auth_mode": "none",
            "defaultDomains": "ai.kfjie.me,sub.kfjie.me,x.kfjie.me,grok.kfjie.me",
        }
        result = GROK_TTK_MANAGER.start(start_opts)
        if result.get("error"):
            return {
                "triggered": False,
                "reason": "ttk_start_failed",
                "error": result.get("error"),
                "needAccounts": need,
                "checkedAt": now_iso(),
            }

        self._last_trigger_ts = now_ts
        triggered = {
            "triggered": True,
            "reason": "manual" if force else "below_threshold",
            "needAccounts": need if need > 0 else count,
            "registerCount": count,
            "proxy": proxy,
            "checkedAt": now_iso(),
            "ttkState": result.get("ttkState") or GROK_TTK_MANAGER.get_state(),
        }
        self._set_state(lastTriggerAt=triggered["checkedAt"])
        return triggered

    def public_wake(self, *, force: bool = False) -> dict[str, Any]:
        """Public interactive wake: recheck pool and maybe refill.

        Rate-limited by CPA_PUBLIC_WAKE_MIN_INTERVAL_SECONDS (default 60s).
        When pool is low, public recommended poll halves step-by-step down to 10s.
        """
        settings = self._settings()
        now_ts = time.time()
        min_gap = max(10, int(settings.get("publicWakeMinIntervalSeconds") or 60))
        with self._lock:
            last = float(self._last_public_wake_ts or 0.0)
            elapsed = now_ts - last if last else 10**9
            if (not force) and last and elapsed < min_gap:
                state = dict(self._state)
                health = state.get("health") if isinstance(state.get("health"), dict) else {}
                return {
                    "ok": True,
                    "accepted": False,
                    "reason": "too_frequent",
                    "retryAfterSeconds": int(max(1, min_gap - elapsed)),
                    "minIntervalSeconds": min_gap,
                    "pollIntervalSeconds": int(self._public_poll_seconds or settings.get("intervalSeconds") or 60),
                    "health": health,
                    "checkedAt": now_iso(),
                }
            self._last_public_wake_ts = now_ts

        # Always allow trigger attempt on public wake (no register cooldown by default).
        result = self.check_once(trigger=True, force=False)
        health = result.get("health") if isinstance(result.get("health"), dict) else {}
        if not health:
            with self._lock:
                health = dict(self._state.get("health") or {})

        base = int(settings.get("intervalSeconds") or 60)
        min_iv = int(settings.get("minIntervalSeconds") or 10)
        enough = bool(health.get("enough"))
        with self._lock:
            current_public = int(self._public_poll_seconds or base)
            if not enough or health.get("managementOk") is False:
                # halve until min 10s
                current_public = max(min_iv, int(current_public / 2) if current_public > min_iv else min_iv)
                if current_public > min_iv and current_public > base:
                    current_public = base
                # ensure stepwise from base: 60 -> 30 -> 15 -> 10
                if current_public > min_iv:
                    current_public = max(min_iv, current_public)
            else:
                current_public = base
            self._public_poll_seconds = current_public
            adaptive = dict(self._state.get("adaptive") or {})
            adaptive.update(
                {
                    "recommendedPollSeconds": current_public if not enough else base,
                    "publicPollSeconds": current_public,
                    "mode": "low" if not enough else adaptive.get("mode") or "base",
                    "updatedAt": now_iso(),
                }
            )
            self._state["adaptive"] = adaptive

        return {
            "ok": True,
            "accepted": True,
            "reason": "checked",
            "minIntervalSeconds": min_gap,
            "pollIntervalSeconds": int(self._public_poll_seconds or base),
            "recommendedPollSeconds": int(self._public_poll_seconds or base),
            "health": health,
            "triggered": bool((result.get("lastTriggerResult") or {}).get("triggered"))
            if isinstance(result.get("lastTriggerResult"), dict)
            else bool((self.status().get("lastTriggerResult") or {}).get("triggered")),
            "lastTriggerResult": result.get("lastTriggerResult") or self.status().get("lastTriggerResult"),
            "ttkRunning": bool(self.status().get("ttkRunning")),
            "checkedAt": now_iso(),
        }

    def _run(self) -> None:
        self._set_state(running=True)
        while not self._stop_event.is_set():
            settings = self._settings()
            mode = "base"
            wait_seconds = int(settings.get("intervalSeconds") or 60)
            if settings.get("enabled"):
                try:
                    result = self.check_once(trigger=True)
                    health = {}
                    if isinstance(result, dict):
                        health = result.get("health") if isinstance(result.get("health"), dict) else {}
                    if not health:
                        with self._lock:
                            health = dict(self._state.get("health") or {})
                    wait_seconds, mode = self._compute_next_interval(health, settings)
                except Exception as error:
                    self._set_state(lastError=str(error), lastCheckAt=now_iso())
                    wait_seconds = int(settings.get("minIntervalSeconds") or 10)
                    mode = "error"
            else:
                wait_seconds = int(settings.get("intervalSeconds") or 60)
                mode = "disabled"

            wait_seconds = max(int(settings.get("minIntervalSeconds") or 10), int(wait_seconds))
            self._current_interval = wait_seconds
            self._set_state(
                adaptive={
                    "mode": mode,
                    "currentIntervalSeconds": wait_seconds,
                    "recommendedPollSeconds": (
                        int(settings.get("minIntervalSeconds") or 10)
                        if mode in {"low", "error", "recovering"}
                        else int(settings.get("intervalSeconds") or 60)
                    ),
                    "baseIntervalSeconds": int(settings.get("intervalSeconds") or 60),
                    "minIntervalSeconds": int(settings.get("minIntervalSeconds") or 10),
                    "maxIntervalSeconds": int(settings.get("maxIntervalSeconds") or 300),
                    "updatedAt": now_iso(),
                }
            )
            if self._stop_event.wait(wait_seconds):
                break
        self._set_state(running=False)


CPA_MONITOR = CpaMonitor()




def grok_ttk_status_legacy_placeholder() -> dict[str, Any]:
    return grok_ttk_status()


@dataclass
class GrokSignupState:
    running: bool = False
    stop_requested: bool = False
    phase: str = "idle"
    total: int = 0
    completed: int = 0
    failed: int = 0
    current_email: str = ""
    started_at: str = ""
    updated_at: str = ""
    errors: list[dict[str, str]] = field(default_factory=list)
    log_lines: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GrokSignupManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._state = GrokSignupState()
        self._logs: list[dict[str, str]] = []

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            return self._state.to_dict()

    def get_logs(self) -> list[dict[str, str]]:
        with self._lock:
            return list(self._logs)

    def append_log(self, message: str, level: str = "info") -> None:
        entry = {
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "message": str(message),
            "level": level,
        }
        with self._lock:
            self._logs.append(entry)
            while len(self._logs) > GROK_LOG_MAX:
                self._logs.pop(0)
            self._state.log_lines = list(self._logs)
            self._state.updated_at = now_iso()

    def start(self, emails: list[str] | None = None, count: int = 1, **options: Any) -> dict[str, Any]:
        script = ROOT / "grok_signup.py"
        if not script.exists():
            return {"error": "未找到 grok_signup.py", "grokSignupState": self.get_state()}
        with self._lock:
            if self._state.running:
                return {"error": "Grok 注册任务已在运行中", "grokSignupState": self._state.to_dict()}
            self._stop_event.clear()
            self._process = None
            self._logs = []
            total = len(emails or []) or max(1, int(count or 1))
            self._state = GrokSignupState(
                running=True,
                total=total,
                phase="running",
                started_at=now_iso(),
                updated_at=now_iso(),
            )
        self._thread = threading.Thread(target=self._run, args=(emails or [], count, options), daemon=True)
        self._thread.start()
        return {"grokSignupState": self.get_state()}

    def stop(self) -> dict[str, Any]:
        process = None
        with self._lock:
            if not self._state.running:
                return {"grokSignupState": self._state.to_dict(), "message": "没有运行中的 Grok 注册任务"}
            self._state.stop_requested = True
            self._state.phase = "stopping"
            process = self._process
        self._stop_event.set()
        if process and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
        return {"grokSignupState": self.get_state()}

    def _run(self, emails: list[str], count: int, options: dict[str, Any]) -> None:
        try:
            targets = [e for e in emails if str(e).strip()]
            if not targets:
                # count mode; script allocates
                cmd = [sys.executable, str(ROOT / "grok_signup.py"), "--count", str(max(1, int(count or 1)))]
                self._exec(cmd, options)
            else:
                for email in targets:
                    if self._stop_event.is_set():
                        break
                    with self._lock:
                        self._state.current_email = email
                        self._state.updated_at = now_iso()
                    cmd = [sys.executable, str(ROOT / "grok_signup.py"), "--email", email]
                    code = self._exec(cmd, options)
                    with self._lock:
                        if code == 0:
                            self._state.completed += 1
                        else:
                            self._state.failed += 1
        finally:
            with self._lock:
                self._state.running = False
                self._state.phase = "stopped" if self._state.stop_requested else "idle"
                self._state.updated_at = now_iso()
                self._process = None

    def _exec(self, command: list[str], options: dict[str, Any]) -> int:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        for key in (
            "PORT",
            "GROK_SIGNUP_PROXY",
            "UC_SIGNUP_PROXY",
            "BROWSER_PROXY",
            "BROWSER_DISPLAY",
            "GROK2API_BASE_URL",
            "GROK2API_ADMIN_KEY",
            "GROK2API_POOL",
            "CPA_ENABLED",
            "CPA_AUTH_DIR",
            "CPA_REMOTE_URL",
            "CPA_MANAGEMENT_KEY",
            "GROK_CF_CLEARANCE_ENABLED",
            "UC_SIGNUP_CF_CLEARANCE_ENABLED",
            "UC_SIGNUP_CF_CLEARANCE_API_URL",
            "GROK_CF_CLEARANCE_API_URL",
            "GROK_CF_CLEARANCE_TARGET_URL",
            "DOMAIN_MAIL_ROOT",
            "GROK_DOMAIN_ROOT",
        ):
            # leave to process config.json primarily
            pass
        try:
            process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as error:
            self.append_log(f"启动失败: {error}", "error")
            return 1
        with self._lock:
            self._process = process
        assert process.stdout is not None
        for line in process.stdout:
            self.append_log(line.rstrip())
            if self._stop_event.is_set():
                process.terminate()
                break
        return process.wait()


GROK_SIGNUP_MANAGER = GrokSignupManager()


def load_grok_results() -> list[dict[str, Any]]:
    try:
        data = json.loads(GROK_RESULTS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_grok_result(item: dict[str, Any]) -> list[dict[str, Any]]:
    rows = load_grok_results()
    rows.insert(0, {**item, "savedAt": now_iso()})
    rows = rows[:200]
    GROK_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    GROK_RESULTS_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows


def build_clients(config: Any) -> dict[str, Any]:
    def g(key: str, default: str = "") -> str:
        # config fields are lower-snake in Config object; settings via app_config_value-like attributes
        candidates = [
            key,
            key.lower(),
            key.upper(),
        ]
        for c in candidates:
            if hasattr(config, c):
                val = getattr(config, c)
                if val is not None and str(val) != "":
                    return str(val)
        # dynamic attributes maybe set by reload
        raw = getattr(config, "raw", None)
        if isinstance(raw, dict) and key in raw:
            return str(raw.get(key) or default)
        return default

    # Prefer reading from environment / app settings through config attributes when present.
    from server import app_config_value  # local import to use live settings

    cpa = CpaClient(
        enabled=str(app_config_value("CPA_ENABLED", "true")).lower() in {"1", "true", "yes", "on"},
        auth_dir=str(app_config_value("CPA_AUTH_DIR", "/opt/cliproxyapi/auths")),
        remote_url=str(app_config_value("CPA_REMOTE_URL", "http://127.0.0.1:8317")),
        management_key=str(app_config_value("CPA_MANAGEMENT_KEY", "")),
        api_key=str(app_config_value("CPA_API_KEY", "")),
    )
    g2 = Grok2ApiClient(
        base_url=str(app_config_value("GROK2API_BASE_URL", "http://127.0.0.1:8000")),
        admin_key=str(app_config_value("GROK2API_ADMIN_KEY", "")),
        pool=str(app_config_value("GROK2API_POOL", "basic")),
    )
    return {"cpa": cpa, "grok2api": g2}


def finish_outlook_email_allocation(payload: dict[str, Any]) -> dict[str, Any]:
    """Write a claimed email result back to OutlookEmail's per-platform project."""
    from server import OUTLOOK_EMAIL_ADMIN

    allocation = payload.get("allocation")
    if not isinstance(allocation, dict) or str(allocation.get("source") or "outlook_project") != "outlook_project":
        return {"ok": True, "skipped": True, "reason": "没有 OutlookEmail 项目领取记录"}

    platform = normalize_email_platform(payload.get("platform") or allocation.get("platform") or allocation.get("projectKey"))
    project_key = email_project_key(platform)
    supplied_project_key = str(allocation.get("projectKey") or project_key).strip().lower()
    if supplied_project_key != project_key:
        raise ValueError("allocation 的项目与 platform 不匹配")

    account_id = allocation.get("accountId") or allocation.get("account_id")
    claim_token = str(allocation.get("claimToken") or allocation.get("claim_token") or "").strip()
    if not account_id or not claim_token:
        raise ValueError("allocation 缺少 accountId 或 claimToken")

    raw_outcome = payload.get("outcome") or payload.get("status")
    if not raw_outcome and isinstance(payload.get("result"), str):
        raw_outcome = payload.get("result")
    if not raw_outcome and "ok" in payload:
        raw_outcome = "success" if request_bool(payload.get("ok")) else "failed"
    outcome = str(raw_outcome or "").strip().lower()
    outcome = {"ok": "success", "done": "success", "error": "failed", "failure": "failed", "cancel": "release", "cancelled": "release"}.get(outcome, outcome)
    if outcome not in {"success", "failed", "release"}:
        raise ValueError("结果只支持 success、failed 或 release")

    caller_id = str(allocation.get("callerId") or allocation.get("caller_id") or payload.get("callerId") or "").strip()
    task_id = str(allocation.get("taskId") or allocation.get("task_id") or payload.get("taskId") or "").strip()
    detail = str(payload.get("detail") or payload.get("error") or "")[:500]
    kwargs = {
        "account_id": int(account_id),
        "claim_token": claim_token,
        "caller_id": caller_id,
        "task_id": task_id,
        "detail": detail,
    }
    if outcome == "success":
        result = OUTLOOK_EMAIL_ADMIN.complete_project_success(project_key, **kwargs)
    elif outcome == "failed":
        result = OUTLOOK_EMAIL_ADMIN.complete_project_failed(project_key, **kwargs)
    else:
        result = OUTLOOK_EMAIL_ADMIN.release_project_account(project_key, **kwargs)
    return {"ok": True, "platform": platform, "projectKey": project_key, "outcome": outcome, "result": result}


def handle_extension_api(handler: Any, method: str, path: str, query: dict[str, str], body: dict[str, Any] | None = None) -> bool:
    """Return True if handled."""
    body = body if isinstance(body, dict) else {}

    # health extras
    if method == "GET" and path == "/api/extensions/status":
        clients = build_clients(handler)
        try:
            from server import CONFIG, app_config_value
            cpa = clients["cpa"].health()
            g2 = clients["grok2api"].health()
            handler.send_json(
                200,
                {
                    "ok": True,
                    "grokSignup": GROK_SIGNUP_MANAGER.get_state(),
                    "grokTtk": grok_ttk_status(),
                    "grokTtkConfig": public_ttk_config(),
                    "cpa": cpa,
                    "grok2api": g2,
                    "mailGroups": {
                        "source": app_config_value("MAIL_SOURCE_GROUP_NAME", "mail_pool"),
                        "domain": app_config_value("DOMAIN_MAIL_GROUP_NAME", "domain_pool"),
                        "oaiPending": app_config_value("MAIL_PENDING_GROUP_NAME", "oai_pending"),
                        "oaiSuccess": app_config_value("MAIL_SUCCESS_GROUP_NAME", "oai_success"),
                        "grokPending": app_config_value("GROK_MAIL_PENDING_GROUP_NAME", "grok_pending"),
                        "grokSuccess": app_config_value("GROK_MAIL_SUCCESS_GROUP_NAME", "grok_success"),
                        "bad": app_config_value("MAIL_BAD_GROUP_NAME", "badmail"),
                    },
                    "domain": {
                        "root": app_config_value("DOMAIN_MAIL_ROOT", ""),
                        "subdomains": app_config_value("DOMAIN_MAIL_SUBDOMAINS", "sub,x,grok"),
                        "nameStyle": app_config_value("DOMAIN_MAIL_NAME_STYLE", "outlook"),
                        "nameDigits": int(app_config_value("DOMAIN_MAIL_NAME_DIGITS", "4") or 4),
                        "preferSubdomain": str(app_config_value("DOMAIN_MAIL_PREFER_SUBDOMAIN", "true")).lower()
                        in {"1", "true", "yes", "on"},
                        "preferInventory": str(app_config_value("MAIL_PREFER_INVENTORY", "true")).lower()
                        in {"1", "true", "yes", "on"},
                    },
                },
            )
        except Exception as error:
            handler.send_json(500, {"error": str(error)})
        return True

    if method == "GET" and path == "/api/grok/signup/status":
        handler.send_json(200, {"grokSignupState": GROK_SIGNUP_MANAGER.get_state()})
        return True

    if method == "GET" and path == "/api/grok/signup/logs":
        handler.send_json(200, {"logs": GROK_SIGNUP_MANAGER.get_logs()})
        return True

    if method == "GET" and path == "/api/grok/ttk/traffic":
        try:
            tail = int(query.get("tail") or 30)
        except Exception:
            tail = 30
        items = []
        if load_sessions is not None:
            try:
                items = load_sessions(service="grok_ttk", tail=tail)
            except Exception as error:
                handler.send_json(500, {"error": str(error)})
                return True
        current = GROK_TTK_MANAGER.get_state().get("traffic")
        handler.send_json(200, {"ok": True, "current": current, "items": items, "ttkState": GROK_TTK_MANAGER.get_state()})
        return True

    if method == "GET" and path == "/api/grok/ttk/status":
        handler.send_json(200, {"tool": grok_ttk_status(), "config": public_ttk_config(), "ttkState": GROK_TTK_MANAGER.get_state()})
        return True

    if method == "GET" and path == "/api/grok/ttk/config":
        handler.send_json(200, {"config": public_ttk_config()})
        return True

    if method == "POST" and path == "/api/grok/ttk/config":
        try:
            cfg = save_ttk_config(body or {})
            handler.send_json(200, {"ok": True, "config": public_ttk_config(cfg)})
        except Exception as error:
            handler.send_json(400, {"error": str(error)})
        return True

    if method == "POST" and path == "/api/grok/ttk/start":
        result = GROK_TTK_MANAGER.start(body or {})
        code = 409 if result.get("error") else 200
        handler.send_json(code, result)
        return True

    if method == "POST" and path == "/api/grok/ttk/stop":
        handler.send_json(200, GROK_TTK_MANAGER.stop())
        return True

    if method == "GET" and path == "/api/grok/ttk/logs":
        try:
            tail = int(query.get("tail") or 300)
        except Exception:
            tail = 300
        handler.send_json(200, {"logs": GROK_TTK_MANAGER.get_logs(tail=tail), "ttkState": GROK_TTK_MANAGER.get_state()})
        return True

    if method == "GET" and path == "/api/grok/ttk/results":
        rows = _read_json_file(GROK_TTK_RESULTS_FILE, [])
        if not isinstance(rows, list):
            rows = []
        handler.send_json(200, {"results": rows[:200], "exports": list_ttk_exports(40), "ttkState": GROK_TTK_MANAGER.get_state()})
        return True

    if method == "GET" and path == "/api/grok/ttk/exports":
        handler.send_json(200, {"exports": list_ttk_exports(80)})
        return True

    if method == "GET" and path == "/api/grok/ttk/export":
        # Download/export content by name or path under data/grok_ttk or tools account.
        name = str(query.get("name") or query.get("file") or "").strip()
        rel = str(query.get("path") or "").strip()
        target: Path | None = None
        if rel:
            candidate = (ROOT / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
            allowed_roots = [GROK_TTK_STATE_DIR.resolve(), (GROK_TTK_DIR / "account").resolve(), GROK_TTK_DIR.resolve()]
            if any(str(candidate).startswith(str(r)) for r in allowed_roots) and candidate.is_file():
                target = candidate
        if target is None and name:
            for row in list_ttk_exports(200):
                if row.get("name") == name or Path(str(row.get("path"))).name == name:
                    p = Path(str(row.get("path")))
                    if p.is_file():
                        target = p
                        break
        if target is None or not target.is_file():
            handler.send_json(404, {"error": "导出文件不存在"})
            return True
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except Exception as error:
            handler.send_json(500, {"error": f"读取失败: {error}"})
            return True
        handler.send_json(
            200,
            {
                "name": target.name,
                "path": str(target),
                "content": content,
                "size": target.stat().st_size,
            },
        )
        return True

    if method == "POST" and path == "/api/grok/ttk/sync":
        # Import latest exported tokens into Grok2API and/or CPA.
        try:
            from server import app_config_value

            targets = body.get("targets") or ["grok2api"]
            if isinstance(targets, str):
                targets = [targets]
            targets = [str(t).lower() for t in targets]
            pool = str(body.get("pool") or app_config_value("GROK2API_POOL", "basic") or "basic")
            auto_nsfw = bool(body.get("autoNsfw") or False)
            text = str(body.get("text") or "")
            name = str(body.get("name") or body.get("file") or "").strip()
            if not text and name:
                for row in list_ttk_exports(200):
                    if row.get("name") == name:
                        text = Path(str(row["path"])).read_text(encoding="utf-8", errors="replace")
                        break
            if not text:
                # default latest tokens file
                for row in list_ttk_exports(40):
                    if row.get("kind") == "tokens":
                        text = Path(str(row["path"])).read_text(encoding="utf-8", errors="replace")
                        name = row.get("name") or name
                        break
            tokens = parse_grok_export_tokens(text)
            out: dict[str, Any] = {"count": len(tokens), "name": name, "grok2api": None, "cpa": None}
            from server import CONFIG
            clients = build_clients(CONFIG)
            if "grok2api" in targets:
                out["grok2api"] = clients["grok2api"].import_sso_tokens(
                    tokens,
                    pool=pool,
                    tags=["grok-ttk-sync"],
                    auto_nsfw=auto_nsfw,
                )
            if "cpa" in targets:
                proxy = str(app_config_value("GROK_SIGNUP_PROXY", "") or app_config_value("BROWSER_PROXY", "") or "")
                cpa_rows = []
                for sso in tokens:
                    item: dict[str, Any] = {"ssoPrefix": sso[:10], "cpa": None, "error": None}
                    try:
                        token = sso_to_token(sso, proxy=proxy, log=lambda m: None)
                        if not token:
                            item["error"] = "device-flow 失败"
                        else:
                            item["cpa"] = clients["cpa"].import_token(token, email="")
                    except Exception as error:
                        item["error"] = str(error)
                    cpa_rows.append(item)
                out["cpa"] = {"items": cpa_rows, "ok": sum(1 for x in cpa_rows if x.get("cpa")), "failed": sum(1 for x in cpa_rows if x.get("error"))}
            handler.send_json(200, out)
        except Exception as error:
            handler.send_json(400, {"error": str(error)})
        return True

    if method == "POST" and path == "/api/grok/import/grok2api":
        try:
            tokens = parse_grok_export_tokens(str(body.get("text") or ""))
            pool = str(body.get("pool") or "").strip()
            auto_nsfw = bool(body.get("autoNsfw", False))
            clients = build_clients(handler)
            result = clients["grok2api"].import_sso_tokens(
                tokens,
                pool=pool,
                tags=["grok-ttk-import"],
                auto_nsfw=auto_nsfw,
            )
            save_grok_result(
                {
                    "type": "grok2api_import",
                    "count": len(tokens),
                    "pool": result.get("pool"),
                    "mode": result.get("mode"),
                }
            )
            handler.send_json(200, result)
        except (ValueError, Grok2ApiError) as error:
            handler.send_json(400, {"error": str(error)})
        return True

    if method == "POST" and path == "/api/grok/signup/start":
        emails = body.get("emails") or []
        if isinstance(emails, str):
            emails = [x.strip() for x in emails.splitlines() if x.strip()]
        count = int(body.get("count") or 1)
        result = GROK_SIGNUP_MANAGER.start(emails=list(emails), count=count)
        code = 409 if result.get("error") else 200
        handler.send_json(code, result)
        return True

    if method == "POST" and path == "/api/grok/signup/stop":
        handler.send_json(200, GROK_SIGNUP_MANAGER.stop())
        return True

    if method == "POST" and path == "/api/grok/signup/result":
        save_grok_result(body)
        allocation_result = {"ok": True, "skipped": True}
        if isinstance(body.get("allocation"), dict):
            try:
                allocation_result = finish_outlook_email_allocation(body)
            except Exception as error:
                handler.send_json(400, {"ok": False, "error": f"邮箱项目结果回写失败: {error}"})
                return True
        handler.send_json(200, {"ok": True, "allocation": allocation_result})
        return True

    if method == "GET" and path == "/api/grok/results":
        handler.send_json(200, {"items": load_grok_results()})
        return True

    if method == "POST" and path == "/api/grok/import/sso":
        # CPA-only import for existing user-authorized SSO sessions.
        text = str(body.get("text") or body.get("sso") or "")
        rows = parse_sso_lines(text) if text else []
        if body.get("sso") and not rows:
            rows = [(sanitize_sso(body.get("sso")), str(body.get("email") or ""))]
        targets = body.get("targets") or ["cpa"]
        if isinstance(targets, str):
            targets = [x.strip() for x in targets.split(",") if x.strip()]
        if set(targets) - {"cpa"}:
            handler.send_json(400, {"error": "Grok 凭证只支持导入 CPA；请勿写入 grok2api 或 Sub2API"})
            return True
        proxy = str(body.get("proxy") or "")
        clients = build_clients(handler)
        results = []
        for sso, email in rows:
            item: dict[str, Any] = {"email": email, "ssoPrefix": sso[:10], "cpa": None}
            if "cpa" in targets:
                try:
                    token = sso_to_token(sso, proxy=proxy, log=lambda m: None)
                    if not token:
                        raise RuntimeError("device-flow 失败")
                    item["cpa"] = clients["cpa"].import_token(token, email=email)
                except Exception as error:
                    item["cpa"] = {"ok": False, "error": str(error)}
            results.append(item)
            save_grok_result({"type": "import", **item})
        handler.send_json(200, {"count": len(results), "results": results})
        return True


    if method == "POST" and path == "/api/tools/chatgpt-promo-check":
        try:
            from server import app_config_value
            import importlib
            promo_fn = chatgpt_check_promo
            promo_err = None
            if promo_fn is None:
                try:
                    tools_dir = str(ROOT / "tools")
                    if tools_dir not in sys.path:
                        sys.path.insert(0, tools_dir)
                    # also allow project venv packages for curl_cffi
                    for site in (ROOT / ".venv" / "lib").glob("python*/site-packages"):
                        sp = str(site)
                        if sp not in sys.path:
                            sys.path.insert(0, sp)
                    mod = importlib.import_module("chatgpt_promo_check")
                    importlib.reload(mod)
                    promo_fn = getattr(mod, "check_promo", None)
                except Exception as imp_err:
                    promo_err = str(imp_err)
            if promo_fn is None:
                raise RuntimeError(f"chatgpt_promo_check 模块不可用: {promo_err or 'import returned None'}")
            proxy = str(body.get("proxy") or "").strip()
            if not proxy and str(body.get("direct") or "").lower() not in {"1", "true", "yes", "on"}:
                proxy = str(
                    app_config_value("BROWSER_PROXY", "")
                    or app_config_value("UC_SIGNUP_PROXY", "")
                    or ""
                ).strip()
            if str(body.get("direct") or "").lower() in {"1", "true", "yes", "on"}:
                proxy = ""
            result = promo_fn(
                access_token=str(body.get("accessToken") or body.get("access_token") or body.get("token") or ""),
                account_id=str(body.get("accountId") or body.get("account_id") or body.get("chatgptAccountId") or ""),
                device_id=str(body.get("deviceId") or body.get("device_id") or body.get("oaiDid") or ""),
                email=str(body.get("email") or ""),
                proxy=proxy,
                raw_input=body.get("input") if body.get("input") not in (None, "") else body.get("raw"),
            )
            handler.send_json(200 if result.get("ok") else 502, result)
        except Exception as error:
            handler.send_json(400, {"ok": False, "error": str(error)})
        return True

    if method == "POST" and path == "/api/convert/openai":
        try:
            docs = parse_openai_input(str(body.get("input") or body.get("text") or ""))
            target = str(body.get("target") or "sub2api")
            out = convert_openai(
                docs,
                target,
                name_prefix=str(body.get("namePrefix") or ""),
                plan_type=str(body.get("planType") or ""),
            )
            handler.send_json(200, {"target": target, "count": len(docs), "output": out})
        except Exception as error:
            handler.send_json(400, {"error": str(error)})
        return True

    if method == "POST" and path == "/api/convert/grok":
        try:
            items = parse_grok_input(str(body.get("input") or body.get("text") or ""))
            target = str(body.get("target") or "cpa")
            do_flow = bool(body.get("deviceFlow", True))
            out = convert_grok(
                items,
                target,
                proxy=str(body.get("proxy") or ""),
                do_device_flow=do_flow,
                log=lambda m: None,
            )
            handler.send_json(200, {"target": target, "count": len(items), "output": out})
        except Exception as error:
            handler.send_json(400, {"error": str(error)})
        return True

    if method == "POST" and path == "/api/convert/openai/import-sub2api":
        try:
            from server import Sub2ApiClient, bind_sub2api_import_to_target_groups, CONFIG

            docs = parse_openai_input(str(body.get("input") or body.get("text") or ""))
            document = convert_openai(
                docs,
                "sub2api",
                name_prefix=str(body.get("namePrefix") or ""),
                plan_type=str(body.get("planType") or ""),
            )
            client = Sub2ApiClient()
            imported = client.import_accounts_document(document)
            groups = body.get("groups") or CONFIG.sub2api_import_group_names
            bind = bind_sub2api_import_to_target_groups(document, groups)
            handler.send_json(200, {"imported": imported, "bind": bind, "documentSummary": {"accounts": len(document.get("accounts") or [])}})
        except Exception as error:
            handler.send_json(400, {"error": str(error)})
        return True

    if method == "POST" and path == "/api/convert/grok/import":
        try:
            items = parse_grok_input(str(body.get("input") or body.get("text") or ""))
            targets = body.get("targets") or ["cpa"]
            if isinstance(targets, str):
                targets = [x.strip() for x in targets.split(",") if x.strip()]
            if set(targets) - {"cpa"}:
                raise ValueError("Grok 凭证只支持导入 CPA；请勿写入 grok2api 或 Sub2API")
            proxy = str(body.get("proxy") or "")
            clients = build_clients(handler)
            results = []
            for item in items:
                # normalize to sso or token path via convert_grok
                if isinstance(item, dict) and item.get("sso"):
                    sso = sanitize_sso(item.get("sso"))
                    email = str(item.get("email") or "")
                    row: dict[str, Any] = {"email": email}
                    if "cpa" in targets:
                        try:
                            token = sso_to_token(sso, proxy=proxy, log=lambda m: None)
                            if not token:
                                raise RuntimeError("device-flow 失败")
                            row["cpa"] = clients["cpa"].import_token(token, email=email)
                        except Exception as error:
                            row["cpa"] = {"ok": False, "error": str(error)}
                    results.append(row)
                else:
                    # auth json -> cpa
                    out = convert_grok([item], "cpa", proxy=proxy, do_device_flow=False, log=lambda m: None)
                    row = {"converted": out}
                    if out and "cpa" in targets and out[0].get("entry"):
                        entry = out[0]["entry"]
                        # reconstruct token-like for import_token
                        token = {
                            "access_token": entry.get("access_token"),
                            "refresh_token": entry.get("refresh_token"),
                            "expires_in": entry.get("expires_in"),
                            "id_token": entry.get("id_token"),
                            "email": entry.get("email"),
                        }
                        row["cpa"] = clients["cpa"].import_token(token, email=entry.get("email") or "")
                    results.append(row)
            handler.send_json(200, {"results": results})
        except Exception as error:
            handler.send_json(400, {"error": str(error)})
        return True

    if method == "POST" and path == "/api/email-queue/allocate":
        try:
            from server import (
                app_config_value,
                load_email_queue,
                save_email_queue,
                OUTLOOK_EMAIL_ADMIN,
            )

            platform = normalize_email_platform(body.get("platform") or "oai")
            prefer_inventory = request_bool(body.get("preferInventory"), True)
            allow_queue_fallback = request_bool(body.get("allowQueueFallback"), False)
            allow_domain_fallback = request_bool(body.get("allowDomainFallback"), False)
            if str(app_config_value("MAIL_PREFER_INVENTORY", "true")).lower() not in {"1", "true", "yes", "on"}:
                prefer_inventory = False if body.get("preferInventory") is None else prefer_inventory

            email = ""
            source = ""
            allocation: dict[str, Any] | None = None
            project_key = email_project_key(platform)

            # OutlookEmail 项目是主池：项目按平台隔离，OAI 用过不会把 Grok 项目标成 done。
            if prefer_inventory and OUTLOOK_EMAIL_ADMIN.configured:
                existing_project = OUTLOOK_EMAIL_ADMIN.get_project(project_key)
                if existing_project is None:
                    groups = OUTLOOK_EMAIL_ADMIN.list_groups()
                    source_group = str(app_config_value("MAIL_SOURCE_GROUP_NAME", "mail_pool") or "").strip().lower()
                    bad_group = str(app_config_value("MAIL_BAD_GROUP_NAME", "badmail") or "").strip().lower()
                    exact = next((g for g in groups if str(g.get("name") or "").strip().lower() == source_group and g.get("id")), None)
                    if exact:
                        group_ids = [int(exact["id"])]
                    else:
                        # 旧配置里的 mail_pool 可能已经不存在；保留所有活跃分组，但排除 badmail。
                        group_ids = [int(g["id"]) for g in groups if g.get("id") and str(g.get("name") or "").strip().lower() != bad_group]
                    project = OUTLOOK_EMAIL_ADMIN.start_project(
                        project_key,
                        name=f"AutomyAI {platform} 邮箱",
                        description=f"AutomyAI {platform} 注册邮箱池；与其他平台独立记录",
                        group_ids=group_ids or None,
                    )
                else:
                    # 不带 group_ids，避免重启时意外覆盖已有项目范围和历史状态。
                    project = OUTLOOK_EMAIL_ADMIN.start_project(project_key)

                caller_id = str(body.get("callerId") or body.get("caller_id") or f"automyai-{platform}").strip()
                task_id = str(body.get("taskId") or body.get("task_id") or f"{platform}-{uuid.uuid4().hex}").strip()
                try:
                    lease_seconds = max(60, min(int(body.get("leaseSeconds") or body.get("lease_seconds") or 900), 3600))
                except (TypeError, ValueError):
                    lease_seconds = 900
                claim = OUTLOOK_EMAIL_ADMIN.claim_project_account(
                    project_key,
                    caller_id=caller_id,
                    task_id=task_id,
                    lease_seconds=lease_seconds,
                )
                if isinstance(claim, dict) and str(claim.get("email") or "").strip():
                    email = str(claim.get("email") or "").strip()
                    source = "outlook_project"
                    allocation = {
                        "source": source,
                        "platform": platform,
                        "projectKey": project_key,
                        "projectAccountId": claim.get("project_account_id"),
                        "accountId": claim.get("account_id"),
                        "claimToken": claim.get("claim_token") or "",
                        "callerId": caller_id,
                        "taskId": task_id,
                        "claimedAt": claim.get("claimed_at") or "",
                        "leaseExpiresAt": claim.get("lease_expires_at") or "",
                        "remark": claim.get("remark") or "",
                    }

            # 本地队列只作为显式兼容回退，不再覆盖 OutlookEmail 的项目状态。
            if not email and prefer_inventory and allow_queue_fallback:
                queue = load_email_queue()
                emails = [str(x).strip() for x in (queue.get("emails") or []) if str(x).strip()]
                if emails:
                    email = emails.pop(0)
                    source = "queue"
                    save_email_queue({**queue, "emails": emails, "activeEmail": email})

            # 自有域名默认关闭；只有调用方显式 allowDomainFallback 才允许生成。
            if not email and allow_domain_fallback:
                root = app_config_value("DOMAIN_MAIL_ROOT", "") or app_config_value("GROK_DOMAIN_ROOT", "")
                prefer_sub = str(app_config_value("DOMAIN_MAIL_PREFER_SUBDOMAIN", "true")).lower() in {"1", "true", "yes", "on"}
                if not root:
                    handler.send_json(400, {"error": "库存邮箱为空且未配置 DOMAIN_MAIL_ROOT"})
                    return True
                email = generate_domain_emails(
                    root_domain=root,
                    count=1,
                    prefer_subdomain=prefer_sub,
                    subdomains=app_config_value("DOMAIN_MAIL_SUBDOMAINS", "sub,x,grok"),
                    name_style=app_config_value("DOMAIN_MAIL_NAME_STYLE", "outlook"),
                    name_digits=int(app_config_value("DOMAIN_MAIL_NAME_DIGITS", "4") or 4),
                )[0]
                source = "domain_sub" if prefer_sub else "domain_root"
            if not email:
                handler.send_json(
                    409,
                    {
                        "error": "OutlookEmail 项目池暂无可领取邮箱",
                        "platform": platform,
                        "projectKey": project_key,
                        "hint": "请先补充 OutlookEmail 账号，或显式传 allowQueueFallback/allowDomainFallback",
                    },
                )
                return True
            result = {"email": email, "source": source, "platform": platform}
            if allocation:
                result["allocation"] = allocation
            handler.send_json(200, result)
        except Exception as error:
            handler.send_json(500, {"error": str(error)})
        return True

    if method == "POST" and path == "/api/email-queue/allocation/result":
        try:
            result = finish_outlook_email_allocation(body)
            handler.send_json(200, result)
        except Exception as error:
            handler.send_json(400, {"error": str(error)})
        return True

    if method == "GET" and path == "/api/email-queue/platform-usage":
        try:
            from server import OUTLOOK_EMAIL_ADMIN

            platform = normalize_email_platform(query.get("platform") or "oai")
            project_key = email_project_key(platform)
            project = OUTLOOK_EMAIL_ADMIN.get_project(project_key)
            if not project:
                handler.send_json(200, {"platform": platform, "projectKey": project_key, "project": None, "accounts": []})
                return True
            status = str(query.get("status") or "").strip()
            data = OUTLOOK_EMAIL_ADMIN.list_project_accounts(project_key, status=status)
            handler.send_json(200, {"platform": platform, "projectKey": project_key, **data})
        except Exception as error:
            handler.send_json(400, {"error": str(error)})
        return True

    if method == "POST" and path == "/api/email-queue/generate-domain":
        try:
            from server import app_config_value, load_email_queue, save_email_queue

            root = str(body.get("domain") or app_config_value("DOMAIN_MAIL_ROOT", "")).strip()
            count = int(body.get("count") or 1)
            prefer_sub = bool(body.get("preferSubdomain", True))
            prefix = str(body.get("prefix") or "")
            emails = generate_domain_emails(
                root_domain=root,
                count=count,
                prefer_subdomain=prefer_sub,
                local_prefix=prefix,
                subdomains=app_config_value("DOMAIN_MAIL_SUBDOMAINS", "sub,x,grok"),
                name_style=app_config_value("DOMAIN_MAIL_NAME_STYLE", "outlook"),
                name_digits=int(app_config_value("DOMAIN_MAIL_NAME_DIGITS", "4") or 4),
            )
            queue = load_email_queue()
            merged = list(dict.fromkeys([*(queue.get("emails") or []), *emails]))
            queue = save_email_queue({**queue, "emails": merged})
            handler.send_json(200, {"generated": emails, "emailQueue": queue})
        except Exception as error:
            handler.send_json(400, {"error": str(error)})
        return True

    if method == "POST" and path == "/api/outlook-email/groups/replan":
        try:
            from server import OUTLOOK_EMAIL, app_config_value

            plan = dict(DEFAULT_GROUP_PLAN)
            # allow overrides
            plan.update({k: v for k, v in (body.get("plan") or {}).items() if v})
            names = list(plan.values())
            if hasattr(OUTLOOK_EMAIL, "ensure_groups"):
                ensured = OUTLOOK_EMAIL.ensure_groups(names)
            else:
                ensured = {"requested": names}
            handler.send_json(200, {"plan": plan, "legacyMap": LEGACY_GROUP_MAP, "ensured": ensured})
        except Exception as error:
            handler.send_json(500, {"error": str(error)})
        return True


    if method == "GET" and path == "/api/cpa/monitor/status":
        handler.send_json(200, CPA_MONITOR.status())
        return True

    if method == "POST" and path == "/api/cpa/monitor/check":
        trigger = False
        force = False
        if isinstance(body, dict):
            raw = body.get("trigger")
            trigger = str(raw).lower() in {"1", "true", "yes", "on"} if raw is not None else False
            raw_force = body.get("force")
            force = str(raw_force).lower() in {"1", "true", "yes", "on"} if raw_force is not None else False
            if str(body.get("manual")).lower() in {"1", "true", "yes", "on"}:
                force = True
        handler.send_json(200, CPA_MONITOR.check_once(trigger=True if force else trigger, force=force))
        return True

    return False
