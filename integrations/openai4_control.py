"""OpenAI 注册控制面 helpers (UC-backed OpenAI4).

Pure helpers shared by tools/openai4/webapp.py and unit tests.
"""
from __future__ import annotations

from typing import Any

from integrations.openai3_control import (
    mail_failure_is_definitive,
    normalize_group_name,
    normalize_proxy_url,
    proxy_http_connect_fallback,
)
from integrations.proxy_config import parse_proxy_url, proxy_url_from_parsed


def normalize_openai4_mail_groups(source: Any, pending: Any, success: Any, bad: Any) -> dict[str, str]:
    """OpenAI4 mail groups.

    Allow source == pending so users can resume accounts already in oai_pending.
    success/bad must still be distinct from source/pending and each other.
    """
    groups = {
        "sourceGroup": normalize_group_name(source, "来源账号池"),
        "pendingGroup": normalize_group_name(pending, "执行中分组"),
        "successGroup": normalize_group_name(success, "成功分组"),
        "badGroup": normalize_group_name(bad, "坏邮箱分组"),
    }
    source_g = groups["sourceGroup"]
    pending_g = groups["pendingGroup"]
    success_g = groups["successGroup"]
    bad_g = groups["badGroup"]
    if success_g in {source_g, pending_g, bad_g}:
        raise ValueError("成功分组不能与来源/执行中/坏邮箱分组相同")
    if bad_g in {source_g, pending_g, success_g}:
        raise ValueError("坏邮箱分组不能与来源/执行中/成功分组相同")
    return groups


DEFAULT_MAIL_GROUPS = {
    "mail_source_group": "默认分组",
    "mail_pending_group": "oai_pending",
    "mail_success_group": "oai_success",
    "mail_bad_group": "badmail",
}


def source_group_requires_signup(source_group: Any) -> bool:
    """Identify a mailbox group intended for new registrations."""
    value = str(source_group or "").strip().lower()
    return "待注册" in value or "signup" in value or "register" in value


def default_openai4_config() -> dict[str, Any]:
    return {
        # No default proxy. User must fill custom_proxy_url before start/preflight.
        "custom_proxy_url": "",
        "fingerprint_enabled": True,
        "fingerprint_source": "local",
        "fingerprint_seed": "",
        "fingerprint_strict": True,
        "mail_source_group": DEFAULT_MAIL_GROUPS["mail_source_group"],
        "mail_pending_group": DEFAULT_MAIL_GROUPS["mail_pending_group"],
        "mail_success_group": DEFAULT_MAIL_GROUPS["mail_success_group"],
        "mail_bad_group": DEFAULT_MAIL_GROUPS["mail_bad_group"],
        "sub2api_group": "auto",
        "sub2api_import_use_signup_proxy": False,
        "get_refresh_token": True,
        "keep_browser_on_failure": False,
        "auth_only": False,
        "manual_mode": False,
        "traffic_meter": False,
        "novnc_path": "/novnc/vnc.html?autoconnect=1&resize=scale&path=novnc/websockify",
    }


def sanitize_openai4_proxy_display(value: Any) -> str:
    """Keep the exact proxy text the user typed (trim only). Never rewrite format."""
    return str(value or "").strip()


def normalize_openai4_proxy_input(value: Any) -> str:
    """Normalize proxy only for runtime use (connect / UC / traffic).

    Accepts:
    - URL forms (with or without scheme)
    - host:port:user:pass
    Backend adds http:// when scheme is absent.
    Do NOT use this for panel save/display — that must stay verbatim.
    """
    raw = sanitize_openai4_proxy_display(value)
    if not raw or raw == "***":
        return ""
    return normalize_proxy_url(raw)


def resolve_openai4_proxy(cfg: dict[str, Any], override_proxy: str = "") -> dict[str, Any]:
    """Resolve registration proxy. Only custom/explicit proxy is supported."""
    explicit = sanitize_openai4_proxy_display(override_proxy)
    raw = explicit if explicit and explicit != "***" else sanitize_openai4_proxy_display(cfg.get("custom_proxy_url"))
    if not raw or raw == "***":
        raise ValueError("请填写注册代理完整链接（可不写 http://，后台会自动补全）")
    normalized = normalize_openai4_proxy_input(raw)
    if not normalized:
        raise ValueError("请填写注册代理完整链接（可不写 http://，后台会自动补全）")
    return {
        "mode": "custom",
        "region": "",
        "proxyUrl": normalized,
        "proxyName": "自定义注册代理",
        "displayProxy": raw,
    }


def map_uc_state_to_openai4(uc_state: dict[str, Any] | None, *, run_id: str = "", cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    state = uc_state if isinstance(uc_state, dict) else {}
    running = bool(state.get("running"))
    phase = str(state.get("phase") or ("running" if running else "idle"))
    completed = int(state.get("completed") or 0)
    success = int(state.get("success") or 0)
    failed = int(state.get("failed") or 0)
    total = int(state.get("total") or 0)
    errors = state.get("errors") if isinstance(state.get("errors"), list) else []
    last_error = ""
    if errors:
        last = errors[-1]
        if isinstance(last, dict):
            last_error = str(last.get("message") or last.get("error") or "")
        else:
            last_error = str(last)
    results = state.get("results") if isinstance(state.get("results"), list) else []
    return {
        "running": running,
        "phase": phase,
        "run_id": run_id or str(state.get("startedAt") or state.get("started_at") or ""),
        "pid": int(state.get("currentPid") or state.get("current_pid") or 0),
        "concurrency": 1,
        "total": total,
        "completed": completed,
        "success": success,
        "failed": failed,
        "started_at": str(state.get("startedAt") or state.get("started_at") or ""),
        "finished_at": str(state.get("updatedAt") or state.get("updated_at") or "") if not running else "",
        "updated_at": str(state.get("updatedAt") or state.get("updated_at") or ""),
        "error": last_error,
        "current_email": str(state.get("currentEmail") or state.get("current_email") or ""),
        "current_phone": str(state.get("currentPhone") or state.get("current_phone") or ""),
        "current_proxy": str(state.get("currentProxy") or state.get("current_proxy") or ""),
        "current_step": str(state.get("currentStep") or state.get("current_step") or ""),
        "results": results,
        "engine": "uc_signup",
        "display": ":1",
        "novnc_path": str((cfg or {}).get("novnc_path") or default_openai4_config()["novnc_path"]),
        "sourceGroup": str((cfg or {}).get("mail_source_group") or DEFAULT_MAIL_GROUPS["mail_source_group"]),
        "pendingGroup": str((cfg or {}).get("mail_pending_group") or DEFAULT_MAIL_GROUPS["mail_pending_group"]),
        "successGroup": str((cfg or {}).get("mail_success_group") or DEFAULT_MAIL_GROUPS["mail_success_group"]),
        "badGroup": str((cfg or {}).get("mail_bad_group") or DEFAULT_MAIL_GROUPS["mail_bad_group"]),
    }


def build_uc_start_payload(
    *,
    emails: list[str],
    proxy_url: str,
    cfg: dict[str, Any],
    selected_account_email: str = "",
    forced_phone: str = "",
    mail_provider: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "emails": emails,
        "proxy": proxy_url,
        "display": ":1",
        "moveMail": True,
        "authOnly": bool(cfg.get("auth_only")),
        "getRefreshToken": bool(cfg.get("get_refresh_token", True)),
        # OpenAI4 is the unattended batch path. Manual handoff and failure
        # browser retention must never be inherited from stale saved config.
        "manualMode": False,
        "keepBrowserOnFailure": False,
        "mailSourceGroup": str(cfg.get("mail_source_group") or DEFAULT_MAIL_GROUPS["mail_source_group"]),
        "mailPendingGroup": str(cfg.get("mail_pending_group") or DEFAULT_MAIL_GROUPS["mail_pending_group"]),
        "mailSuccessGroup": str(cfg.get("mail_success_group") or DEFAULT_MAIL_GROUPS["mail_success_group"]),
        "mailBadGroup": str(cfg.get("mail_bad_group") or DEFAULT_MAIL_GROUPS["mail_bad_group"]),
    }
    if mail_provider:
        payload["mailProvider"] = str(mail_provider)
    if selected_account_email:
        payload["emails"] = [selected_account_email]
    if forced_phone:
        payload["forcedPhone"] = forced_phone
    return payload


def public_proxy_url(proxy_url: str) -> str:
    text = str(proxy_url or "").strip()
    if not text:
        return ""
    parsed = parse_proxy_url(text)
    if not parsed:
        return "***"
    host = str(parsed.get("host") or "")
    port = str(parsed.get("port") or "")
    protocol = str(parsed.get("protocol") or "http")
    if parsed.get("username") or parsed.get("password"):
        return f"{protocol}://***:***@{host}:{port}"
    return proxy_url_from_parsed(parsed)


__all__ = [
    "DEFAULT_MAIL_GROUPS",
    "build_uc_start_payload",
    "default_openai4_config",
    "mail_failure_is_definitive",
    "map_uc_state_to_openai4",
    "normalize_openai4_mail_groups",
    "sanitize_openai4_proxy_display",
    "normalize_openai4_proxy_input",
    "normalize_proxy_url",
    "proxy_http_connect_fallback",
    "public_proxy_url",
    "resolve_openai4_proxy",
]
