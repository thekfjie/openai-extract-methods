"""Opus Mail Admin account import using the existing Apple Mail project config."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from integrations.common import first_non_empty, http_json, load_json_file


class OpusMailError(RuntimeError):
    pass


OPENAI_SIGNUP_POOL_NOTE = "automyai OpenAI signup pool pending"
OPENAI_REGISTERED_PENDING_NOTE = "automyai UC signup registered; OAuth pending"
OPENAI_WEB_SESSION_PENDING_NOTE = "automyai ChatGPT web session saved; OAuth RT pending"


def _bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _token_material(payload: Mapping[str, Any]) -> dict[str, str]:
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), Mapping) else {}
    credentials = payload.get("credentials") if isinstance(payload.get("credentials"), Mapping) else {}
    return {
        "accessToken": str(first_non_empty(payload.get("accessToken"), payload.get("access_token"), tokens.get("access_token"), credentials.get("access_token")) or ""),
        "refreshToken": str(first_non_empty(payload.get("refreshToken"), payload.get("refresh_token"), tokens.get("refresh_token"), credentials.get("refresh_token")) or ""),
        "idToken": str(first_non_empty(payload.get("idToken"), payload.get("id_token"), tokens.get("id_token"), credentials.get("id_token")) or ""),
        "sessionToken": str(first_non_empty(payload.get("sessionToken"), payload.get("session_token"), tokens.get("session_token"), credentials.get("session_token")) or ""),
    }


def build_opus_openai_payload(oauth_payload: Mapping[str, Any], *, email: str, source_email: str = "") -> dict[str, Any]:
    tokens = _token_material(oauth_payload)
    credential_kind = str(oauth_payload.get("credentialKind") or oauth_payload.get("credential_kind") or "").strip()
    web_session_only = credential_kind == "chatgpt_web_session"
    account_email = str(first_non_empty(email, oauth_payload.get("email"), oauth_payload.get("email_address")) or "").strip()
    if not account_email:
        raise OpusMailError("Opus Mail 导入缺少邮箱")
    if not tokens["accessToken"]:
        raise OpusMailError("Opus Mail 导入缺少 access_token")

    payload: dict[str, Any] = {
        "email": account_email,
        "toEmail": account_email,
        # Keep iCloud alias delivery on the existing Mail Opus mapping when
        # OAuth material is added to a pending/registered account.
        "outlookManagerEmail": "",
        "note": OPENAI_WEB_SESSION_PENDING_NOTE if web_session_only else "automyai UC signup OAuth",
        # A successful OAuth write supersedes any earlier phone/OAuth-pending
        # failure banner on the same Mail Admin mapping.
        "statusMessage": "",
        "billingChannelOverride": "",
        "manualPlus": False,
        "sold": False,
        "autoFlag": True,
        # Preserve the immutable first-registration time when this is an
        # AT/RT follow-up upsert.  The Mail Admin server keeps this field
        # unchanged on every later token/status update.
        "accessToken": tokens["accessToken"],
        "oauthTokens": {
            "access_token": tokens["accessToken"],
            "refresh_token": tokens["refreshToken"],
            "id_token": tokens["idToken"],
            "session_token": tokens["sessionToken"],
        },
    }
    registration_created_at = str(
        first_non_empty(
            oauth_payload.get("registrationCreatedAt"),
            oauth_payload.get("registration_created_at"),
        ) or ""
    ).strip()
    if registration_created_at:
        payload["registrationCreatedAt"] = registration_created_at
    if source_email:
        payload["sourceEmail"] = source_email
    if tokens["refreshToken"]:
        payload["refreshToken"] = tokens["refreshToken"]
    if tokens["idToken"]:
        payload["idToken"] = tokens["idToken"]
    if tokens["sessionToken"]:
        payload["sessionToken"] = tokens["sessionToken"]
        payload["credential"] = f"{account_email}---{tokens['sessionToken']}"
    else:
        payload["credential"] = f"{account_email}---{tokens['accessToken']}"
    status_message = str(oauth_payload.get("statusMessage") or oauth_payload.get("status_message") or "").strip()
    if status_message:
        payload["statusMessage"] = status_message[:500]
    return payload


def build_opus_pending_payload(*, email: str) -> dict[str, Any]:
    account_email = str(email or "").strip()
    if not account_email:
        raise OpusMailError("Opus Mail 待处理入库缺少邮箱")
    return {
        "email": account_email,
        "toEmail": account_email,
        # iCloud aliases are delivered through Mail Opus' shared mailbox.  An
        # Outlook manager address here would route reads to the wrong service.
        "outlookManagerEmail": "",
        "note": OPENAI_SIGNUP_POOL_NOTE,
        "billingChannelOverride": "",
        "manualPlus": False,
        "sold": False,
        "autoFlag": True,
    }


def build_opus_registered_payload(
    *,
    email: str,
    password: str = "",
    reason: str = "",
    source_email: str = "",
    registration_created_at: str = "",
) -> dict[str, Any]:
    """Promote a tokenless but registered account into the main Mail Admin view.

    The mapping already owns the alias/forwarding rules created by the pending
    pool import.  Updating the note moves it out of the pending-only view while
    preserving those rules and any OAuth material written by an earlier partial
    callback.
    """
    account_email = str(email or "").strip()
    if not account_email:
        raise OpusMailError("Opus Mail 已注册待授权入库缺少邮箱")
    payload: dict[str, Any] = {
        "email": account_email,
        "toEmail": account_email,
        "outlookManagerEmail": "",
        "note": OPENAI_REGISTERED_PENDING_NOTE,
        "statusMessage": str(reason or "OAuth / RT 尚未完成，可重新授权").strip()[:500],
        "billingChannelOverride": "",
        "manualPlus": False,
        "sold": False,
        "autoFlag": True,
    }
    if str(registration_created_at or "").strip():
        payload["registrationCreatedAt"] = str(registration_created_at).strip()
    if source_email:
        payload["sourceEmail"] = str(source_email).strip()
    if password:
        payload["password"] = str(password)
    return payload


class OpusMailClient:
    def __init__(self, base_url: str, api_key: str, *, proxy_url: str = "", enabled: bool = True, timeout: float = 30) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.proxy_url = str(proxy_url or "").strip()
        self.enabled = bool(enabled)
        self.timeout = float(timeout)

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.base_url and self.api_key)

    @classmethod
    def from_project(cls, root: Path, *, proxy_url: str = "") -> "OpusMailClient":
        root = Path(root)
        config = load_json_file(root / "data" / "apple_mail" / "config.json", {}) or {}
        secrets = load_json_file(root / "data" / "apple_mail" / "secrets.json", {}) or {}
        base_url = os.getenv("OPUS_MAIL_IMPORT_BASE", "").strip() or str(config.get("importBase") or "https://cloud.opus.sryze.cc")
        api_key = os.getenv("OPUS_MAIL_IMPORT_API_KEY", "").strip() or str(secrets.get("importApiKey") or "")
        configured_proxy = proxy_url or os.getenv("OPUS_MAIL_IMPORT_PROXY", "").strip() or str(config.get("proxyUrl") or "")
        enabled = _bool(os.getenv("OPUS_MAIL_OAUTH_IMPORT_ENABLED", config.get("oauthImportEnabled", config.get("enabled", True))), True)
        return cls(base_url, api_key, proxy_url=configured_proxy, enabled=enabled)

    def _import_account(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        status, response, raw = http_json(
            "POST",
            f"{self.base_url}/api/v1/accounts",
            headers={
                "Content-Type": "application/json",
                "X-API-Key": self.api_key,
                "Authorization": f"Bearer {self.api_key}",
            },
            body=payload,
            timeout=self.timeout,
            proxy_url=self.proxy_url,
        )
        if status < 200 or status >= 300:
            detail = ""
            if isinstance(response, Mapping):
                detail = str(first_non_empty(response.get("message"), response.get("error"), response.get("detail")) or "")
            raise OpusMailError(f"Opus Mail 导入失败: HTTP {status} {detail or raw[:200]}".strip())
        response_data = response if isinstance(response, Mapping) else {}
        response_item = response_data.get("item") if isinstance(response_data.get("item"), Mapping) else {}
        return {
            "configured": True,
            "imported": True,
            "status": status,
            "hasAccessToken": bool(payload.get("accessToken")),
            "hasRefreshToken": bool(payload.get("refreshToken")),
            "hasIdToken": bool(payload.get("idToken")),
            "accountId": first_non_empty(response_data.get("id"), response_data.get("accountId"), response_item.get("id"), (response_data.get("data") or {}).get("id") if isinstance(response_data.get("data"), Mapping) else ""),
        }

    def import_pending_email(self, *, email: str) -> dict[str, Any]:
        if not self.configured:
            return {"configured": False, "imported": False, "reason": "not_configured"}
        result = self._import_account(build_opus_pending_payload(email=email))
        return {**result, "pending": True}

    def import_registered_email(
        self,
        *,
        email: str,
        password: str = "",
        reason: str = "",
        source_email: str = "",
        registration_created_at: str = "",
    ) -> dict[str, Any]:
        if not self.configured:
            return {"configured": False, "imported": False, "reason": "not_configured"}
        result = self._import_account(build_opus_registered_payload(
            email=email,
            password=password,
            reason=reason,
            source_email=source_email,
            registration_created_at=registration_created_at,
        ))
        return {**result, "registered": True, "oauthPending": True}

    def import_openai_oauth(self, oauth_payload: Mapping[str, Any], *, email: str, source_email: str = "") -> dict[str, Any]:
        if not self.configured:
            return {"configured": False, "imported": False, "reason": "not_configured"}
        payload = build_opus_openai_payload(oauth_payload, email=email, source_email=source_email)
        return self._import_account(payload)
