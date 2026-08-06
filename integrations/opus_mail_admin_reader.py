"""Read Free / unactivated ChatGPT accounts from local Mail Opus Admin.

Mail Admin's public import API is write-only. This helper authenticates with the
admin password already stored for Apple Mail imports and lists account sessions
for the extraction workbench.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from integrations.common import first_non_empty, http_json, load_json_file


class OpusMailAdminReaderError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = int(status_code or 400)


_COLOR_LABELS = {
    "yellow": "黄",
    "orange": "橙",
    "green": "绿",
    "blue": "蓝",
    "purple": "紫",
    "gray": "灰",
    "red": "红",
}

_MARK_COLORS = set(_COLOR_LABELS)
_PENDING_SIGNUP_NOTE = "automyai OpenAI signup pool pending"


def _bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _cookie_value(set_cookie: str, name: str) -> str:
    raw = _text(set_cookie)
    if not raw:
        return ""
    # Only the first Set-Cookie line is expected from http_json; keep it robust.
    for part in re.split(r",(?=\s*[^;=]+=)", raw):
        chunk = part.split(";", 1)[0].strip()
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        if key.strip() == name:
            return value.strip()
    if raw.startswith(f"{name}="):
        return raw.split(";", 1)[0].split("=", 1)[1].strip()
    return ""


def _parse_session_blob(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        data = dict(raw)
    else:
        text = _text(raw)
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        data = dict(parsed) if isinstance(parsed, Mapping) else {}

    access = _text(first_non_empty(data.get("accessToken"), data.get("access_token")))
    refresh = _text(first_non_empty(data.get("refreshToken"), data.get("refresh_token")))
    session = _text(first_non_empty(data.get("sessionToken"), data.get("session_token")))
    user = data.get("user") if isinstance(data.get("user"), Mapping) else {}
    account = data.get("account") if isinstance(data.get("account"), Mapping) else {}
    email = _text(first_non_empty(data.get("email"), user.get("email"), account.get("email")))
    return {
        "accessToken": access,
        "refreshToken": refresh,
        "sessionToken": session,
        "email": email,
        "expires": _text(data.get("expires")),
        "authProvider": _text(data.get("authProvider") or data.get("auth_provider")),
        "raw": data,
    }


def _color_label(color: str) -> str:
    key = _text(color).lower()
    return _COLOR_LABELS.get(key, key or "无")


def _account_status(item: Mapping[str, Any]) -> dict[str, Any]:
    has_plus = bool(item.get("manualPlus") or item.get("hasPlus") or item.get("hasPlusOverride"))
    has_deactivation = bool(item.get("hasDeactivation"))
    if has_plus and has_deactivation:
        free_state = "plus_deactivated"
        free_label = "Plus 已停用"
    elif has_plus:
        free_state = "plus"
        free_label = "Plus"
    elif has_deactivation:
        free_state = "free_deactivated"
        free_label = "Free 已停用"
    else:
        free_state = "free_unactivated"
        free_label = "Free 未开通"
    return {
        "hasPlus": has_plus,
        "hasDeactivation": has_deactivation,
        "freeState": free_state,
        "freeLabel": free_label,
        "isFreeUnactivated": free_state == "free_unactivated",
    }


def public_mail_admin_account(item: Mapping[str, Any], *, include_secret_hints: bool = True) -> dict[str, Any]:
    status = _account_status(item)
    oauth = item.get("openaiOAuth") if isinstance(item.get("openaiOAuth"), Mapping) else {}
    session = _parse_session_blob(item.get("note"))
    mark_color = _text(item.get("markColor")).lower()
    if mark_color not in _MARK_COLORS:
        mark_color = ""
    has_access = bool(session.get("accessToken") or oauth.get("hasAccessToken"))
    has_session = bool(session.get("sessionToken") or oauth.get("hasSessionToken"))
    payload = {
        "id": _text(item.get("id")),
        "email": _text(item.get("email") or item.get("login") or session.get("email")),
        "sold": bool(item.get("sold")),
        "markColor": mark_color,
        "markColorLabel": _color_label(mark_color) if mark_color else "",
        "billingChannel": _text(item.get("billingChannel") or item.get("billingChannelOverride")),
        "createdAt": _text(item.get("createdAt")),
        "updatedAt": _text(item.get("updatedAt")),
        "hasAccessToken": has_access,
        "hasSessionToken": has_session,
        "hasCredential": bool(has_access or has_session),
        "selectable": bool(status["isFreeUnactivated"] and (has_access or has_session or True)),
        **status,
    }
    # selectable remains true for free-unactivated even without credential so UI can still show them.
    payload["selectable"] = bool(status["isFreeUnactivated"])
    if include_secret_hints:
        payload["credentialHint"] = (
            "accessToken"
            if has_access
            else ("sessionToken" if has_session else ("note" if _text(item.get("note")) else "missing"))
        )
    return payload


def build_extraction_credential(item: Mapping[str, Any], oauth: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build one extraction-ready credential object from Mail Admin account data."""
    session = _parse_session_blob(item.get("note"))
    oauth = oauth if isinstance(oauth, Mapping) else {}
    access = _text(first_non_empty(oauth.get("accessToken"), oauth.get("access_token"), session.get("accessToken")))
    refresh = _text(first_non_empty(oauth.get("refreshToken"), oauth.get("refresh_token"), session.get("refreshToken")))
    session_token = _text(first_non_empty(oauth.get("sessionToken"), oauth.get("session_token"), session.get("sessionToken")))
    email = _text(first_non_empty(item.get("email"), item.get("login"), oauth.get("email"), session.get("email")))
    if not email:
        raise OpusMailAdminReaderError("账号缺少邮箱")
    if not access and not session_token:
        raise OpusMailAdminReaderError(f"{email} 没有可用的 accessToken / sessionToken")

    credential: dict[str, Any] = {"email": email}
    if access:
        credential["accessToken"] = access
    if session_token:
        credential["sessionToken"] = session_token
    if refresh:
        credential["refreshToken"] = refresh
    # Keep a compact OpenAI session shape when the stored note already is one.
    raw = session.get("raw") if isinstance(session.get("raw"), Mapping) else {}
    if raw.get("account") and isinstance(raw.get("account"), Mapping):
        credential["account"] = {
            key: raw["account"].get(key)
            for key in ("id", "planType", "structure", "isDelinquent")
            if key in raw["account"]
        }
    if raw.get("user") and isinstance(raw.get("user"), Mapping):
        credential["user"] = {
            key: raw["user"].get(key)
            for key in ("id", "email", "name", "idp")
            if key in raw["user"]
        }
    if session.get("expires"):
        credential["expires"] = session["expires"]
    credential["source"] = "mail_admin"
    credential["mailAdminId"] = _text(item.get("id"))
    return credential


class OpusMailAdminReader:
    """Small authenticated client for Mail Opus Admin free-account browsing."""

    def __init__(
        self,
        base_url: str,
        admin_password: str,
        *,
        proxy_url: str = "",
        enabled: bool = True,
        timeout: float = 30,
        origin: str = "",
    ) -> None:
        self.base_url = _text(base_url).rstrip("/")
        self.admin_password = _text(admin_password)
        self.proxy_url = _text(proxy_url)
        self.enabled = bool(enabled)
        self.timeout = float(timeout)
        self.origin = _text(origin) or self.base_url
        self._cookie = ""

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.base_url and self.admin_password)

    @classmethod
    def from_project(cls, root: Path, *, proxy_url: str = "") -> "OpusMailAdminReader":
        root = Path(root)
        config = load_json_file(root / "data" / "apple_mail" / "config.json", {}) or {}
        secrets = load_json_file(root / "data" / "apple_mail" / "secrets.json", {}) or {}
        base_url = (
            os.getenv("OPUS_MAIL_ADMIN_BASE", "").strip()
            or os.getenv("OPUS_MAIL_IMPORT_BASE", "").strip()
            or _text(config.get("adminBase") or config.get("importBase") or "https://cloud.opus.sryze.cc")
        )
        # Prefer loopback when the service is co-located; falls back to configured public base.
        loopback = os.getenv("OPUS_MAIL_ADMIN_LOOPBACK", "http://127.0.0.1:8789").strip()
        if _bool(os.getenv("OPUS_MAIL_ADMIN_USE_LOOPBACK", "1"), True) and loopback:
            base_url = loopback
        admin_password = (
            os.getenv("OPUS_MAIL_ADMIN_PASSWORD", "").strip()
            or os.getenv("VIEWER_ADMIN_PASSWORD", "").strip()
            or _text(secrets.get("adminAuth") or secrets.get("adminPassword") or secrets.get("viewerAdminPassword"))
        )
        configured_proxy = (
            proxy_url
            or os.getenv("OPUS_MAIL_ADMIN_PROXY", "").strip()
            or os.getenv("OPUS_MAIL_IMPORT_PROXY", "").strip()
            or _text(config.get("proxyUrl") or "")
        )
        # Loopback admin calls must not go through an egress proxy.
        if base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost"):
            configured_proxy = ""
        enabled = _bool(os.getenv("OPUS_MAIL_ADMIN_READER_ENABLED", config.get("adminReaderEnabled", True)), True)
        origin = os.getenv("OPUS_MAIL_ADMIN_ORIGIN", "").strip() or base_url
        return cls(base_url, admin_password, proxy_url=configured_proxy, enabled=enabled, origin=origin)

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        cookie: str = "",
        allow_retry_login: bool = True,
    ) -> tuple[int, Any, str, str]:
        if not self.configured:
            raise OpusMailAdminReaderError("Mail Admin 读取未配置", status_code=503)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": self.origin,
        }
        active_cookie = cookie or self._cookie
        if active_cookie:
            headers["Cookie"] = active_cookie if active_cookie.startswith("mail_opus_admin=") else f"mail_opus_admin={active_cookie}"
        status, response, raw = http_json(
            method,
            f"{self.base_url}{path}",
            headers=headers,
            body=body,
            timeout=self.timeout,
            proxy_url=self.proxy_url,
        )
        # http_json does not expose response headers; login path handles cookie separately.
        if status in {401, 403} and allow_retry_login and path != "/api/admin/login":
            self.login()
            return self._request(method, path, body=body, cookie=self._cookie, allow_retry_login=False)
        return status, response, raw, active_cookie

    def login(self) -> str:
        if not self.configured:
            raise OpusMailAdminReaderError("Mail Admin 读取未配置", status_code=503)
        # Need Set-Cookie; use a direct opener here.
        from urllib.error import HTTPError, URLError
        from urllib.request import ProxyHandler, Request, build_opener

        handlers = []
        if self.proxy_url:
            handlers.append(ProxyHandler({"http": self.proxy_url, "https": self.proxy_url}))
        opener = build_opener(*handlers)
        payload = json.dumps({"password": self.admin_password}, ensure_ascii=False).encode("utf-8")
        req = Request(
            f"{self.base_url}/api/admin/login",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Origin": self.origin,
                "User-Agent": "help-oai/1.0",
            },
            method="POST",
        )
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                set_cookie = ""
                try:
                    set_cookie = resp.headers.get("Set-Cookie") or ""
                except Exception:
                    set_cookie = ""
                cookie = _cookie_value(set_cookie, "mail_opus_admin")
                if not cookie:
                    # Some stacks return multiple set-cookie via get_all
                    try:
                        values = resp.headers.get_all("Set-Cookie") or []
                    except Exception:
                        values = []
                    for value in values:
                        cookie = _cookie_value(value, "mail_opus_admin")
                        if cookie:
                            break
                if not cookie:
                    raise OpusMailAdminReaderError("Mail Admin 登录成功但未返回会话 Cookie", status_code=502)
                self._cookie = f"mail_opus_admin={cookie}"
                try:
                    parsed = json.loads(raw) if raw else {}
                except Exception:
                    parsed = {}
                if isinstance(parsed, Mapping) and parsed.get("ok") is False:
                    raise OpusMailAdminReaderError(_text(parsed.get("error") or "Mail Admin 登录失败"), status_code=401)
                return self._cookie
        except HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace") if hasattr(error, "read") else str(error)
            detail = raw
            try:
                parsed = json.loads(raw)
                detail = first_non_empty(parsed.get("error"), parsed.get("message"), raw)
            except Exception:
                pass
            raise OpusMailAdminReaderError(f"Mail Admin 登录失败: {detail}", status_code=int(error.code or 401)) from error
        except URLError as error:
            raise OpusMailAdminReaderError(f"Mail Admin 无法连接: {error.reason if hasattr(error, 'reason') else error}", status_code=502) from error

    def _ensure_cookie(self) -> str:
        if not self._cookie:
            self.login()
        return self._cookie

    def list_mappings(self) -> list[dict[str, Any]]:
        self._ensure_cookie()
        status, response, raw, _ = self._request("GET", "/api/admin/mappings")
        if status < 200 or status >= 300:
            detail = ""
            if isinstance(response, Mapping):
                detail = _text(first_non_empty(response.get("error"), response.get("message")))
            raise OpusMailAdminReaderError(f"读取 Mail Admin 账号失败: HTTP {status} {detail or raw[:200]}".strip(), status_code=502)
        if isinstance(response, Mapping):
            items = response.get("results") or response.get("items") or response.get("mappings") or []
        elif isinstance(response, list):
            items = response
        else:
            items = []
        return [item for item in items if isinstance(item, Mapping)]

    def find_mapping_by_email(self, email: str) -> dict[str, Any] | None:
        wanted = _text(email).lower()
        if not wanted:
            return None
        for item in self.list_mappings():
            current = _text(item.get("email") or item.get("login")).lower()
            if current == wanted:
                return dict(item)
        return None

    @staticmethod
    def _is_pending_signup(item: Mapping[str, Any]) -> bool:
        oauth = item.get("openaiOAuth") if isinstance(item.get("openaiOAuth"), Mapping) else {}
        lifecycle = _text(item.get("lifecycleStatus") or "active").lower()
        return bool(
            _text(item.get("note")) == _PENDING_SIGNUP_NOTE
            and not bool(item.get("sold"))
            and lifecycle == "active"
            and not bool(oauth.get("hasAccessToken"))
            and not bool(oauth.get("hasRefreshToken"))
            and bool(item.get("autoFlag"))
        )

    def list_pending_signup_accounts(self, *, limit: int = 500) -> dict[str, Any]:
        accounts = []
        for item in self.list_mappings():
            if not self._is_pending_signup(item):
                continue
            accounts.append({
                "id": _text(item.get("id")),
                "email": _text(item.get("email") or item.get("login")),
                "createdAt": _text(item.get("createdAt")),
                "updatedAt": _text(item.get("updatedAt")),
                "mailReadable": True,
                "hasAccessToken": False,
                "hasRefreshToken": False,
                "status": "pending",
            })
        # Newest pool entries first, matching Mail Admin's main/auto views.
        # Use createdAt for pending aliases because registrationCreatedAt is
        # intentionally absent until signup completes.
        accounts.sort(key=lambda item: (item.get("createdAt") or "", item.get("email") or ""), reverse=True)
        bounded = max(1, min(int(limit or 500), 1000))
        return {
            "success": True,
            "configured": True,
            "source": "opus_mail",
            "total": len(accounts),
            "accounts": accounts[:bounded],
        }

    def latest_verification_code(self, email: str) -> dict[str, Any] | None:
        mapping = self.find_mapping_by_email(email)
        if not mapping:
            return None
        account_id = _text(mapping.get("id"))
        self._ensure_cookie()
        status, response, raw, _ = self._request(
            "GET",
            f"/api/admin/mapping-verification-code?id={quote(account_id, safe='')}",
        )
        if status < 200 or status >= 300 or not isinstance(response, Mapping):
            detail = _text(first_non_empty(
                response.get("error") if isinstance(response, Mapping) else "",
                response.get("message") if isinstance(response, Mapping) else "",
                raw[:200],
            ))
            raise OpusMailAdminReaderError(
                f"读取 Mail Opus 验证码失败: HTTP {status} {detail}".strip(),
                status_code=502,
            )
        item = response.get("item")
        if not isinstance(item, Mapping) or not _text(item.get("verificationCode")):
            return None
        return {
            "id": _text(item.get("id")),
            "verificationCode": _text(item.get("verificationCode")),
            "date": _text(item.get("created_at")),
            "subject": _text(item.get("subject")),
        }

    def probe_mail_access(self, email: str) -> dict[str, Any]:
        mapping = self.find_mapping_by_email(email)
        if not mapping:
            raise OpusMailAdminReaderError("Mail Opus 邮箱映射不存在", status_code=404)
        return self.probe_mapping_mail_access(mapping)

    def probe_mapping_mail_access(self, mapping: Mapping[str, Any]) -> dict[str, Any]:
        """Probe one already-resolved mapping without re-listing all mappings.

        The OpenAI4 preflight can check several pending aliases at once.  The
        old email-oriented helper called ``list_mappings`` for every alias,
        turning a quick health check into N sequential admin round trips.
        Callers that already have the mapping inventory should use this method.
        """
        if not isinstance(mapping, Mapping):
            raise OpusMailAdminReaderError("Mail Opus 邮箱映射不存在", status_code=404)
        account_id = _text(mapping.get("id"))
        if not account_id:
            raise OpusMailAdminReaderError("Mail Opus 邮箱映射缺少 id", status_code=502)
        self._ensure_cookie()
        status, response, raw, _ = self._request(
            "GET",
            f"/api/admin/mapping-mails?id={quote(account_id, safe='')}&limit=1&offset=0",
        )
        if status < 200 or status >= 300 or not isinstance(response, Mapping):
            detail = _text(first_non_empty(
                response.get("error") if isinstance(response, Mapping) else "",
                response.get("message") if isinstance(response, Mapping) else "",
                raw[:200],
            ))
            raise OpusMailAdminReaderError(
                f"Mail Opus 拉信探测失败: HTTP {status} {detail}".strip(),
                status_code=502,
            )
        results = response.get("results") if isinstance(response.get("results"), list) else []
        return {"reachable": True, "mailCount": len(results), "mappingId": account_id}

    def get_oauth(self, account_id: str) -> dict[str, Any]:
        account_id = _text(account_id)
        if not account_id:
            raise OpusMailAdminReaderError("缺少账号 id")
        self._ensure_cookie()
        status, response, raw, _ = self._request("GET", f"/api/admin/mappings/{quote(account_id, safe='')}/oauth")
        if status < 200 or status >= 300 or not isinstance(response, Mapping):
            detail = ""
            if isinstance(response, Mapping):
                detail = _text(first_non_empty(response.get("error"), response.get("message")))
            raise OpusMailAdminReaderError(f"读取账号 session 失败: HTTP {status} {detail or raw[:200]}".strip(), status_code=502)
        return dict(response)

    def list_free_unactivated(
        self,
        *,
        marked_only: bool = False,
        mark_color: str = "",
        include_sold: bool = True,
        query: str = "",
        limit: int = 300,
    ) -> dict[str, Any]:
        items = self.list_mappings()
        color_filter = _text(mark_color).lower()
        needle = _text(query).lower()
        accounts: list[dict[str, Any]] = []
        color_counts: dict[str, int] = {}
        with_credential = 0
        without_credential = 0
        sold_count = 0

        for item in items:
            public = public_mail_admin_account(item)
            if not public.get("isFreeUnactivated"):
                continue
            if not include_sold and public.get("sold"):
                continue
            if marked_only and not public.get("markColor"):
                continue
            if color_filter and public.get("markColor") != color_filter:
                continue
            if needle:
                hay = " ".join(
                    [
                        _text(public.get("email")),
                        _text(public.get("markColor")),
                        _text(public.get("markColorLabel")),
                        _text(public.get("billingChannel")),
                        _text(public.get("id")),
                    ]
                ).lower()
                if needle not in hay:
                    continue
            accounts.append(public)
            color_key = public.get("markColor") or "none"
            color_counts[color_key] = color_counts.get(color_key, 0) + 1
            if public.get("hasCredential"):
                with_credential += 1
            else:
                without_credential += 1
            if public.get("sold"):
                sold_count += 1

        # Prefer marked (c选/颜色) accounts, then those with credentials, then newest-looking email.
        accounts.sort(
            key=lambda item: (
                0 if item.get("markColor") else 1,
                0 if item.get("hasCredential") else 1,
                1 if item.get("sold") else 0,
                _text(item.get("email")).lower(),
            )
        )
        limited = max(1, min(int(limit or 300), 500))
        trimmed = accounts[:limited]
        return {
            "success": True,
            "configured": True,
            "source": "mail_admin",
            "total": len(accounts),
            "returned": len(trimmed),
            "withCredential": with_credential,
            "withoutCredential": without_credential,
            "sold": sold_count,
            "marked": sum(1 for item in accounts if item.get("markColor")),
            "colorCounts": color_counts,
            "accounts": trimmed,
            "filters": {
                "markedOnly": bool(marked_only),
                "markColor": color_filter,
                "includeSold": bool(include_sold),
                "query": _text(query),
                "limit": limited,
            },
        }

    def materialize_credentials(self, account_ids: list[str]) -> dict[str, Any]:
        wanted = [_text(value) for value in (account_ids or []) if _text(value)]
        if not wanted:
            raise OpusMailAdminReaderError("请至少选择一个账号")
        if len(wanted) > 50:
            raise OpusMailAdminReaderError("单次最多导入 50 个账号")

        mappings = { _text(item.get("id")): item for item in self.list_mappings() }
        credentials: list[dict[str, Any]] = []
        missing: list[dict[str, str]] = []
        for account_id in wanted:
            item = mappings.get(account_id)
            if not item:
                missing.append({"id": account_id, "error": "账号不存在"})
                continue
            public = public_mail_admin_account(item)
            try:
                # Prefer note session first; fall back to dedicated oauth endpoint.
                try:
                    credential = build_extraction_credential(item)
                except OpusMailAdminReaderError:
                    oauth = self.get_oauth(account_id)
                    credential = build_extraction_credential(item, oauth)
                credentials.append(credential)
            except OpusMailAdminReaderError as error:
                missing.append({
                    "id": account_id,
                    "email": _text(public.get("email")),
                    "error": str(error),
                    "markColor": _text(public.get("markColor")),
                })

        lines = [json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in credentials]
        return {
            "success": True,
            "count": len(credentials),
            "missingCount": len(missing),
            "credentials": credentials,
            "missing": missing,
            "inputText": "\n".join(lines),
            "accounts": [public_mail_admin_account(mappings[account_id]) for account_id in wanted if account_id in mappings],
        }
