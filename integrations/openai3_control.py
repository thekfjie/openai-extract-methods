"""Pure helpers for the OpenAI 3 control plane.

The FastAPI service keeps process and HTTP concerns in ``tools/openai3``;
format validation lives here so it can be tested by the main project suite.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, unquote, urlparse


def normalize_proxy_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        legacy = raw.split(":", 3)
        if len(legacy) == 4 and legacy[1].isdigit():
            host, port, username, password = legacy
            raw = f"http://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"
        else:
            raw = "http://" + raw
    parsed = urlparse(raw)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("代理端口格式不正确") from error
    if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname or not port:
        raise ValueError("代理格式应为 URL 或 host:port:user:pass")
    auth = ""
    if parsed.username is not None or parsed.password is not None:
        auth = (
            f"{quote(unquote(parsed.username or ''), safe='')}:"
            f"{quote(unquote(parsed.password or ''), safe='')}@"
        )
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"{parsed.scheme}://{auth}{host}:{port}"


def proxy_http_connect_fallback(value: Any, error: Exception) -> str:
    """Return an HTTP CONNECT form for mislabeled HTTPS proxy endpoints.

    Some providers call a proxy "HTTPS" because it can tunnel HTTPS targets,
    while the client-facing proxy endpoint itself still speaks plaintext HTTP
    CONNECT. Only retry that interpretation for TLS/protocol handshake errors;
    authentication, timeout and ordinary connection failures must remain hard
    failures.
    """
    normalized = normalize_proxy_url(value)
    parsed = urlparse(normalized)
    if parsed.scheme != "https":
        return ""
    message = str(error or "").lower()
    if not any(token in message for token in (
        "wrong_version_number",
        "wrong version number",
        "tls connect error",
        "ssl connect error",
        "connection reset by peer",
        "send failure",
    )):
        return ""
    return "http://" + normalized.removeprefix("https://")


def normalize_group_name(value: Any, label: str) -> str:
    name = str(value or "").strip()
    if not name:
        raise ValueError(f"{label}不能为空")
    if len(name) > 100 or any(ord(char) < 32 for char in name):
        raise ValueError(f"{label}格式不正确")
    return name


def normalize_mail_groups(source: Any, pending: Any, success: Any, bad: Any) -> dict[str, str]:
    groups = {
        "sourceGroup": normalize_group_name(source, "来源账号池"),
        "pendingGroup": normalize_group_name(pending, "执行中分组"),
        "successGroup": normalize_group_name(success, "成功分组"),
        "badGroup": normalize_group_name(bad, "坏邮箱分组"),
    }
    if len(set(groups.values())) != len(groups):
        raise ValueError("来源、执行中、成功和坏邮箱分组必须互不相同")
    return groups


def mail_failure_is_definitive(error: Exception) -> bool:
    text = str(error or "").lower()
    if any(token in text for token in ("连接失败", "timed out", "timeout", "connection refused", "temporary")):
        return False
    return any(token in text for token in (
        "invalid_grant", "http 401", "http 403", "unauthorized", "authentication failed",
        "认证失败", "refresh token", "token expired", "token has expired", "imap authentication",
    ))


def classify_openai_signup_transition(payload: Any) -> dict[str, str]:
    """Normalize the next OpenAI Auth signup stage from an API response."""
    data = payload if isinstance(payload, dict) else {}
    page = data.get("page")
    if isinstance(page, dict):
        page_type = str(page.get("type") or "").strip().lower()
        page_payload = page.get("payload") if isinstance(page.get("payload"), dict) else {}
    else:
        page_type = str(page or "").strip().lower()
        page_payload = {}
    session = data.get("oai-client-auth-session")
    session = session if isinstance(session, dict) else {}
    continue_url = str(data.get("continue_url") or "").strip()
    email_mode = str(
        page_payload.get("email_verification_mode")
        or session.get("email_verification_mode")
        or session.get("signup_mode")
        or ""
    ).strip().lower()
    location = continue_url.lower()

    if "code=" in location and "state=" in location:
        stage = "callback"
    elif "about-you" in location or page_type in {"about_you", "about-you"}:
        stage = "about_you"
    elif "create-account/password" in location or page_type == "create_account_password":
        stage = "password"
    elif "email-verification" in location or page_type == "email_otp_verification":
        stage = "email_otp"
    elif page_type == "login_password" or "/log-in/password" in location:
        stage = "login_password"
    elif page_type in {"sign_in_with_chatgpt_codex_consent", "workspace"} or "/workspace" in location:
        stage = "workspace"
    else:
        stage = "unknown"
    return {
        "stage": stage,
        "pageType": page_type,
        "continueUrl": continue_url,
        "emailVerificationMode": email_mode,
    }


def select_latest_verification_code(
    mails: list[dict[str, Any]],
    keyword: str = "openai",
    not_before: float = 0.0,
    excluded_codes: set[str] | None = None,
) -> dict[str, Any]:
    """Select the newest matching verification email after ``not_before``.

    An already-submitted code is deliberately not replaced by an older message:
    callers wait for a genuinely newer replacement instead of reviving a stale
    OTP from the current mailbox window.
    """
    kw = str(keyword or "openai").lower()
    code_re = re.compile(r"\b(\d{6})\b")
    excluded = {str(code) for code in (excluded_codes or set()) if str(code)}

    def timestamp(mail: dict[str, Any]) -> float:
        raw = str(
            mail.get("date")
            or mail.get("received_at")
            or mail.get("receivedDateTime")
            or mail.get("created_at")
            or ""
        ).strip()
        if not raw:
            return 0.0
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            return 0.0

    threshold = max(0.0, float(not_before or 0.0))
    for mail in sorted((item for item in mails if isinstance(item, dict)), key=timestamp, reverse=True):
        received_at = timestamp(mail)
        # A missing/unparseable date cannot safely satisfy a time-bounded OTP
        # request because it may be an old code from a previous challenge.
        if threshold and (not received_at or received_at < threshold):
            continue
        subject = str(mail.get("subject") or "")
        body = str(mail.get("body") or mail.get("body_preview") or mail.get("content") or mail.get("html") or "")
        blob = f"{subject}\n{body}"
        low = blob.lower()
        if kw and kw not in low and "openai" not in low and "chatgpt" not in low and "验证" not in subject:
            if "verify" not in low and "code" not in low and "验证" not in blob:
                continue
        match = code_re.search(blob)
        if not match:
            continue
        if match.group(1) in excluded:
            return {"success": False}
        date = str(
            mail.get("date")
            or mail.get("received_at")
            or mail.get("receivedDateTime")
            or mail.get("created_at")
            or ""
        )
        return {"success": True, "code": match.group(1), "mail": {"date": date}}
    return {"success": False}
