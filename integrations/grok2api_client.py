from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

from .common import http_json, sanitize_sso


class Grok2ApiError(Exception):
    pass


class Grok2ApiClient:
    """Import Grok Web SSO accounts through the grok2api v3 admin API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        admin_key: str = "",
        pool: str = "basic",
        *,
        admin_username: str = "",
        admin_password: str = "",
        credentials_file: str = "",
    ) -> None:
        self.base_url = self._normalize_base_url(base_url)
        self.admin_key = str(admin_key or "").strip()  # Legacy fallback only.
        self.pool = str(pool or "basic").strip() or "basic"
        self.admin_username = str(admin_username or "").strip()
        self.admin_password = str(admin_password or "").strip()
        self.credentials_file = str(
            credentials_file
            or os.environ.get("GROK2API_ADMIN_CREDENTIALS_FILE")
            or "/run/secrets/grok2api_admin_credentials"
        ).strip()

    @staticmethod
    def _normalize_base_url(value: str) -> str:
        base = str(value or "http://127.0.0.1:8000").rstrip("/")
        for suffix in ("/api/admin/v1", "/admin/api"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        return base.rstrip("/")

    def _credentials(self) -> tuple[str, str]:
        username = self.admin_username
        password = self.admin_password
        path = Path(self.credentials_file) if self.credentials_file else None
        if path and path.is_file():
            try:
                values: dict[str, str] = {}
                for line in path.read_text(encoding="utf-8").splitlines():
                    key, separator, value = line.partition("=")
                    if separator:
                        values[key.strip().lower()] = value.strip()
                username = username or values.get("username", "")
                password = password or values.get("password", "")
            except OSError as error:
                raise Grok2ApiError(f"无法读取 grok2api 管理员凭据文件: {error}") from error
        # Keep an explicit compatibility path for installations that still use
        # the default v3 username and stored their password in the old key field.
        if not password and self.admin_key:
            username = username or "admin"
            password = self.admin_key
        if not username or not password:
            raise Grok2ApiError("grok2api v3 管理员用户名或密码未配置")
        return username, password

    def _login(self) -> str:
        username, password = self._credentials()
        url = f"{self.base_url}/api/admin/v1/auth/login"
        status, payload, raw = http_json(
            "POST",
            url,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            body={"username": username, "password": password},
            timeout=15,
        )
        try:
            token = str(payload["data"]["tokens"]["accessToken"] or "").strip()
        except (KeyError, TypeError):
            token = ""
        if not (200 <= status < 300) or not token:
            detail = self._error_detail(payload, raw)
            raise Grok2ApiError(f"grok2api v3 管理员登录失败 ({status}): {detail}")
        return token

    @staticmethod
    def _error_detail(payload: Any, raw: str) -> str:
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error.get("code") or "请求失败")[:240]
            return str(payload.get("message") or payload.get("code") or "请求失败")[:240]
        return str(raw or payload or "请求失败")[:240]

    @staticmethod
    def _multipart_document(document: dict[str, Any]) -> tuple[bytes, str]:
        boundary = f"----automyai-{secrets.token_hex(12)}"
        content = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        body = b"".join(
            (
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="files"; filename="automyai-grok-web.json"\r\n',
                b"Content-Type: application/json\r\n\r\n",
                content,
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            )
        )
        return body, boundary

    @staticmethod
    def _complete_event(raw: str) -> dict[str, Any]:
        complete: dict[str, Any] | None = None
        for block in str(raw or "").replace("\r\n", "\n").split("\n\n"):
            event = ""
            data_lines: list[str] = []
            for line in block.splitlines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
            if not event or not data_lines:
                continue
            try:
                value = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                value = {"message": "无法解析 grok2api 导入结果"}
            if event == "error":
                raise Grok2ApiError(
                    str(value.get("message") or value.get("code") or "grok2api 导入失败")
                    if isinstance(value, dict)
                    else "grok2api 导入失败"
                )
            if event == "complete" and isinstance(value, dict):
                complete = value
        if complete is None:
            raise Grok2ApiError("grok2api 导入响应缺少 complete 事件")
        return complete

    def health(self) -> dict[str, Any]:
        try:
            token = self._login()
            url = f"{self.base_url}/api/admin/v1/me"
            status, payload, raw = http_json(
                "GET",
                url,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=8,
            )
            return {
                "baseUrl": self.base_url,
                "url": url,
                "status": status,
                "ok": 200 <= status < 300,
                "authenticated": 200 <= status < 300,
                "error": None if 200 <= status < 300 else self._error_detail(payload, raw),
            }
        except Grok2ApiError as error:
            return {
                "baseUrl": self.base_url,
                "url": f"{self.base_url}/api/admin/v1/me",
                "status": 0,
                "ok": False,
                "authenticated": False,
                "error": str(error)[:240],
            }

    def import_sso_tokens(
        self,
        tokens: list[str],
        *,
        pool: str = "",
        tags: list[str] | None = None,
        auto_nsfw: bool = False,
    ) -> dict[str, Any]:
        clean: list[str] = []
        seen: set[str] = set()
        for item in tokens:
            token = sanitize_sso(item)
            if token and token not in seen:
                clean.append(token)
                seen.add(token)
        if not clean:
            raise Grok2ApiError("没有有效的 SSO token")

        raw_pool = (pool or self.pool or "auto").strip() or "auto"
        tier_map = {
            "ssoBasic": "basic",
            "ssoSuper": "super",
            "basic": "basic",
            "super": "super",
            "heavy": "heavy",
            "auto": "auto",
        }
        tier = tier_map.get(raw_pool, "auto")
        accounts = [
            {
                "name": f"AutomyAI {hashlib.sha256(token.encode()).hexdigest()[:8]}",
                "sso_token": token,
                "tier": tier,
            }
            for token in clean
        ]
        body, boundary = self._multipart_document({"provider": "grok_web", "accounts": accounts})
        access_token = self._login()
        url = f"{self.base_url}/api/admin/v1/accounts/web/import"
        status, payload, raw = http_json(
            "POST",
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "text/event-stream",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            body=body,
            timeout=120,
        )
        if not (200 <= status < 300):
            raise Grok2ApiError(f"导入 grok2api 失败 ({status}): {self._error_detail(payload, raw)}")
        result = self._complete_event(raw)
        return {
            "ok": True,
            "mode": "import-additive",
            "status": status,
            "payload": result,
            "count": len(clean),
            "pool": tier,
            "url": url,
        }
