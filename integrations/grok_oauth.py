from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Any, Callable

from .common import (
    decode_jwt_payload,
    first_non_empty,
    rfc3339_ns,
    rfc3339_sec,
    sanitize_sso,
    write_json_atomic,
)

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
OIDC_ISSUER = "https://auth.x.ai"
AUTH_KEY = f"{OIDC_ISSUER}::{CLIENT_ID}"
SCOPES = (
    "openid profile email offline_access grok-cli:access "
    "api:access conversations:read conversations:write"
)
CLIPROXY_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
CLIPROXY_TOKEN_ENDPOINT = f"{OIDC_ISSUER}/oauth2/token"
CLIPROXY_REDIRECT_URI = "http://127.0.0.1:56121/callback"
CLIPROXY_HEADERS = {
    "x-grok-client-version": "0.2.93",
    "x-xai-token-auth": "xai-grok-cli",
    "x-authenticateresponse": "authenticate-response",
    "x-grok-client-identifier": "grok-shell",
    "User-Agent": "grok-shell/0.2.93 (linux; x86_64)",
}


class RateLimitedError(Exception):
    pass


class GrokOAuthError(Exception):
    pass


def is_rate_limited(url: str = "", body: str = "") -> bool:
    blob = f"{url}\n{body}".lower()
    return any(
        marker in blob
        for marker in ("rate_limited", "rate-limited", "too_many_requests", "ratelimit")
    )


def backoff_sec(base: float, attempt: int, cap: float = 180.0) -> float:
    return min(cap, float(base) * (1.6 ** max(0, attempt - 1))) + secrets.randbelow(1000) / 1000.0


def _session(proxy: str = ""):
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as error:
        raise GrokOAuthError(
            "缺少依赖 curl_cffi，请安装: pip install curl_cffi"
        ) from error
    session = curl_requests.Session()
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return session


def request_device_code(session, log: Callable[[str], None] = print) -> dict | None:
    try:
        resp = session.post(
            f"{OIDC_ISSUER}/oauth2/device/code",
            data={
                "client_id": CLIENT_ID,
                "scope": SCOPES,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            impersonate="chrome",
            timeout=20,
        )
        if resp.status_code >= 400:
            log(f"device/code HTTP {resp.status_code}: {(resp.text or '')[:200]}")
            return None
        data = resp.json()
        if not data.get("device_code"):
            log(f"device/code 响应异常: {data}")
            return None
        return data
    except Exception as error:
        log(f"device/code 异常: {error}")
        return None


def poll_token(
    session,
    device_code: str,
    interval: int = 5,
    expires_in: int = 600,
    timeout: int = 90,
    log: Callable[[str], None] = print,
) -> dict | None:
    deadline = time.time() + min(int(expires_in or 600), int(timeout or 90))
    sleep_for = max(1, int(interval or 5))
    while time.time() < deadline:
        try:
            resp = session.post(
                CLIPROXY_TOKEN_ENDPOINT,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": CLIENT_ID,
                    "device_code": device_code,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                impersonate="chrome",
                timeout=20,
            )
            payload = {}
            try:
                payload = resp.json()
            except Exception:
                payload = {"raw": (resp.text or "")[:300]}
            if resp.status_code < 400 and payload.get("access_token"):
                return payload
            err = str(payload.get("error") or "")
            if err in {"authorization_pending", "slow_down"}:
                if err == "slow_down":
                    sleep_for = min(30, sleep_for + 2)
                time.sleep(sleep_for)
                continue
            if is_rate_limited(resp.url, resp.text or ""):
                raise RateLimitedError("token poll rate limited")
            log(f"poll_token 失败: HTTP {resp.status_code} {payload}")
            return None
        except RateLimitedError:
            raise
        except Exception as error:
            log(f"poll_token 异常: {error}")
            time.sleep(sleep_for)
    log("poll_token 超时")
    return None


def fetch_userinfo(session, access_token: str) -> dict:
    try:
        resp = session.get(
            f"{OIDC_ISSUER}/oauth2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            impersonate="chrome",
            timeout=15,
        )
        if resp.status_code < 400:
            data = resp.json()
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def enrich_token_with_userinfo(session, token: dict) -> dict:
    access = token.get("access_token") or ""
    if not access:
        return token
    info = fetch_userinfo(session, access)
    email = first_non_empty(info.get("email"), token.get("email"), token.get("_email"))
    if email:
        token["_email"] = email
        token["email"] = email
    return token


def sso_to_token(
    sso_cookie: str,
    *,
    proxy: str = "",
    max_retries: int = 8,
    base_delay: float = 15.0,
    log: Callable[[str], None] = print,
) -> dict | None:
    sso_cookie = sanitize_sso(sso_cookie)
    if not sso_cookie:
        raise GrokOAuthError("SSO 为空")

    session = _session(proxy=proxy)
    session.cookies.set("sso", sso_cookie, domain=".x.ai")

    try:
        probe = session.get("https://accounts.x.ai/", impersonate="chrome", timeout=15)
    except Exception as error:
        raise GrokOAuthError(f"网络错误: {error}") from error
    if "sign-in" in str(probe.url) or "sign-up" in str(probe.url):
        raise GrokOAuthError("SSO 无效或已失效")

    dc: dict | None = None
    rate_hits = 0

    def fresh_device() -> bool:
        nonlocal dc
        log("Device Flow...")
        dc = request_device_code(session, log=log)
        if not dc:
            return False
        log(f"user_code: {dc.get('user_code')}")
        try:
            session.get(dc["verification_uri_complete"], impersonate="chrome", timeout=15)
        except Exception as error:
            log(f"verification_uri 异常: {error}")
            return False
        return True

    if not fresh_device() or not dc:
        return None

    verify_ok = False
    approve_ok = False
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.post(
                f"{OIDC_ISSUER}/oauth2/device/verify",
                data={"user_code": dc["user_code"]},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                impersonate="chrome",
                timeout=15,
                allow_redirects=True,
            )
            body_snip = (resp.text or "")[:300]
            if is_rate_limited(str(resp.url), body_snip):
                rate_hits += 1
                delay = backoff_sec(base_delay, attempt)
                log(f"verify 限流, 第 {attempt}/{max_retries} 次, 等待 {delay:.0f}s")
                time.sleep(delay)
                if not fresh_device():
                    return None
                continue
            if "consent" not in str(resp.url):
                log(f"verify 失败: {resp.url}")
                return None
            verify_ok = True
        except Exception as error:
            delay = backoff_sec(base_delay, attempt, 120)
            log(f"verify 异常 ({error}), 等待 {delay:.0f}s")
            time.sleep(delay)
            continue

        try:
            resp = session.post(
                f"{OIDC_ISSUER}/oauth2/device/approve",
                data={
                    "user_code": dc["user_code"],
                    "action": "allow",
                    "principal_type": "User",
                    "principal_id": "",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                impersonate="chrome",
                timeout=15,
                allow_redirects=True,
            )
            body_snip = (resp.text or "")[:300]
            if is_rate_limited(str(resp.url), body_snip):
                rate_hits += 1
                delay = backoff_sec(base_delay, attempt)
                log(f"approve 限流, 第 {attempt}/{max_retries} 次, 等待 {delay:.0f}s")
                time.sleep(delay)
                verify_ok = False
                if not fresh_device():
                    return None
                continue
            if "done" not in str(resp.url):
                log(f"approve 失败: {resp.url}")
                return None
            approve_ok = True
            break
        except Exception as error:
            delay = backoff_sec(base_delay, attempt, 120)
            log(f"approve 异常 ({error}), 等待 {delay:.0f}s")
            time.sleep(delay)
            continue

    if not verify_ok or not approve_ok or not dc:
        if rate_hits > 0:
            raise RateLimitedError("device flow 限流重试耗尽")
        return None

    token = poll_token(
        session,
        dc["device_code"],
        interval=int(dc.get("interval") or 5),
        expires_in=int(dc.get("expires_in") or 600),
        log=log,
    )
    if not token:
        return None
    return enrich_token_with_userinfo(session, token)


def token_to_auth_entry(token: dict, email: str = "") -> tuple[str, dict]:
    access = token.get("access_token") or token.get("key") or ""
    refresh = token.get("refresh_token") or ""
    payload = decode_jwt_payload(access)
    user_id = payload.get("sub") or payload.get("principal_id") or ""
    principal_id = payload.get("principal_id") or user_id
    principal_type = payload.get("principal_type") or "User"
    expires_in = int(token.get("expires_in") or 21600)
    if "exp" in payload:
        expires_at = rfc3339_ns(float(payload["exp"]))
    else:
        expires_at = rfc3339_ns(time.time() + expires_in)
    iat = payload.get("iat")
    create_time = rfc3339_ns(float(iat) if iat else time.time())
    entry = {
        "key": access,
        "auth_mode": "oidc",
        "create_time": create_time,
        "user_id": user_id,
        "email": email or token.get("_email") or token.get("email") or "",
        "principal_type": principal_type,
        "principal_id": principal_id,
        "refresh_token": refresh,
        "expires_at": expires_at,
        "oidc_issuer": OIDC_ISSUER,
        "oidc_client_id": CLIENT_ID,
    }
    return AUTH_KEY, entry


def cliproxy_filename(email: str = "", sub: str = "") -> str:
    email = (email or "").strip()
    sub = (sub or "").strip()
    if email:
        return f"xai-{email}.json"
    if sub:
        return f"xai-{sub}.json"
    return f"xai-anon_{secrets.token_hex(4)}.json"


def token_to_cliproxy_entry(token: dict, email: str = "") -> tuple[str, dict]:
    access = token.get("access_token") or token.get("key") or ""
    refresh = token.get("refresh_token") or ""
    id_token = token.get("id_token") or ""
    token_type = token.get("token_type") or "Bearer"
    expires_in = int(token.get("expires_in") or 21600)
    access_payload = decode_jwt_payload(access)
    id_payload = decode_jwt_payload(id_token) if id_token else {}
    sub = (
        access_payload.get("sub")
        or access_payload.get("principal_id")
        or id_payload.get("sub")
        or ""
    )
    resolved_email = (
        email
        or token.get("_email")
        or token.get("email")
        or id_payload.get("email")
        or access_payload.get("email")
        or ""
    )
    if "exp" in access_payload:
        expired = rfc3339_sec(float(access_payload["exp"]))
    else:
        expired = rfc3339_sec(time.time() + expires_in)
    if "iat" in access_payload:
        last_refresh = rfc3339_sec(float(access_payload["iat"]))
    else:
        last_refresh = rfc3339_sec()
    entry = {
        "type": "xai",
        "auth_kind": "oauth",
        "access_token": access,
        "refresh_token": refresh,
        "token_type": token_type,
        "expires_in": expires_in,
        "expired": expired,
        "last_refresh": last_refresh,
        "email": resolved_email,
        "sub": sub,
        "base_url": CLIPROXY_BASE_URL,
        "token_endpoint": CLIPROXY_TOKEN_ENDPOINT,
        "redirect_uri": CLIPROXY_REDIRECT_URI,
        "disabled": False,
        # Prefer api.x.ai for CPA. The cli-chat-proxy build route may return
        # 403 for otherwise usable Grok accounts (bot_flag_source=1).
        "using_api": True,
        "headers": dict(CLIPROXY_HEADERS),
        "id_token": id_token,
    }
    return cliproxy_filename(resolved_email, sub), entry


def auth_file_to_token(data: dict) -> tuple[dict, str] | None:
    if not isinstance(data, dict) or not data:
        return None
    if data.get("type") == "xai" or data.get("auth_kind") == "oauth":
        access = data.get("access_token") or data.get("key") or ""
        if not access:
            return None
        token = {
            "access_token": access,
            "refresh_token": data.get("refresh_token") or "",
            "token_type": data.get("token_type") or "Bearer",
            "expires_in": int(data.get("expires_in") or 21600),
            "id_token": data.get("id_token") or "",
        }
        return token, (data.get("email") or "")
    if "key" in data and ("refresh_token" in data or "auth_mode" in data):
        access = data.get("key") or ""
        if not access:
            return None
        exp_in = 21600
        payload = decode_jwt_payload(access)
        if "exp" in payload and "iat" in payload:
            exp_in = max(1, int(payload["exp"]) - int(payload["iat"]))
        token = {
            "access_token": access,
            "refresh_token": data.get("refresh_token") or "",
            "token_type": "Bearer",
            "expires_in": exp_in,
            "id_token": data.get("id_token") or "",
        }
        return token, (data.get("email") or "")
    for key, value in data.items():
        if key == "disabled" or not isinstance(value, dict):
            continue
        access = value.get("access_token") or value.get("key") or ""
        if not access:
            continue
        exp_in = 21600
        payload = decode_jwt_payload(access)
        if "exp" in payload and "iat" in payload:
            exp_in = max(1, int(payload["exp"]) - int(payload["iat"]))
        token = {
            "access_token": access,
            "refresh_token": value.get("refresh_token") or "",
            "token_type": value.get("token_type") or "Bearer",
            "expires_in": int(value.get("expires_in") or exp_in),
            "id_token": value.get("id_token") or "",
        }
        return token, (value.get("email") or data.get("email") or "")
    return None


def write_cliproxy_file(auth_dir: Path, token: dict, email: str = "") -> Path:
    filename, entry = token_to_cliproxy_entry(token, email=email)
    path = Path(auth_dir) / filename
    out = write_json_atomic(path, entry, compact=True, mode=0o600)
    # CPA (cli-proxy-api) usually runs as a non-root user (e.g. ubuntu).
    # If we write as root from the automyai container, fix ownership best-effort.
    try:
        import os
        import pwd
        import grp
        owner = os.environ.get("CPA_AUTH_OWNER") or "ubuntu"
        try:
            pw = pwd.getpwnam(owner)
            uid, gid = pw.pw_uid, pw.pw_gid
        except KeyError:
            # fall back to auth_dir owner
            st = Path(auth_dir).stat()
            uid, gid = st.st_uid, st.st_gid
        os.chown(out, uid, gid)
        os.chmod(out, 0o600)
    except Exception:
        pass
    return out


def write_nested_auth_file(path: Path, token: dict, email: str = "") -> Path:
    auth_key, entry = token_to_auth_entry(token, email=email)
    return write_json_atomic(path, {auth_key: entry}, compact=False, mode=0o600)


def parse_sso_lines(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        email = ""
        sso = line
        if "----" in line:
            parts = [p.strip() for p in line.split("----") if p.strip()]
            if len(parts) >= 2:
                sso = parts[-1]
                if "@" in parts[0]:
                    email = parts[0]
        sso = sanitize_sso(sso)
        if sso:
            out.append((sso, email))
    return out
