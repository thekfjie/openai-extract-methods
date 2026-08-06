"""Temp-mail provider client used for throwaway signup mailboxes."""
from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


class TempMailError(Exception):
    pass

class TempMailClient:
    def __init__(self, base_url: str, admin_password: str, timeout_ms: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.admin_password = admin_password
        self.timeout_seconds = timeout_ms / 1000

    def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        if not self.base_url:
            raise TempMailError("未配置 TEMP_MAIL_API_URL")
        url = f"{self.base_url}{path}"
        request_headers = {
            "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
            "User-Agent": "python-tempmail-client/1.0",
        }
        if headers:
            request_headers.update(headers)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(url, data=data, method=method, headers=request_headers)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                text = response.read().decode("utf-8", errors="replace").strip()
        except HTTPError as error:
            body_text = error.read().decode("utf-8", errors="replace").strip()
            raise TempMailError(f"临时邮箱请求失败: HTTP {error.code} {body_text}".strip())
        except URLError as error:
            raise TempMailError(f"临时邮箱连接失败: {error.reason}")
        if not text:
            return {}
        if text.startswith("{") or text.startswith("["):
            return json.loads(text)
        return text

    def _admin_headers(self) -> dict[str, str]:
        if not self.admin_password:
            raise TempMailError("未配置 TEMP_MAIL_ADMIN_PASSWORD")
        return {"x-admin-auth": self.admin_password}

    def _user_headers(self, jwt: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {jwt}"}

    def get_settings(self) -> dict[str, Any]:
        payload = self._request("GET", "/open_api/settings")
        return payload if isinstance(payload, dict) else {}

    def create_address(self, name: str, domain: str, enable_prefix: bool = True) -> dict[str, Any]:
        payload = self._request(
            "POST",
            "/admin/new_address",
            headers=self._admin_headers(),
            body={
                "enablePrefix": enable_prefix,
                "name": name,
                "domain": domain,
            },
        )
        if not isinstance(payload, dict):
            raise TempMailError("创建邮箱返回格式异常")
        return payload

    def show_address_password(self, address: str) -> dict[str, Any]:
        payload = self._request("GET", f"/admin/show_password/{address}", headers=self._admin_headers())
        if not isinstance(payload, dict):
            raise TempMailError("获取邮箱凭证返回格式异常")
        return payload

    def list_mails(self, address: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        payload = self._request(
            "GET",
            f"/admin/mails?limit={int(limit)}&offset={int(offset)}&address={quote_plus(address)}",
            headers=self._admin_headers(),
        )
        if not isinstance(payload, dict):
            raise TempMailError("邮件列表返回格式异常")
        return payload

    def latest_mail(self, address: str) -> dict[str, Any] | None:
        payload = self.list_mails(address, limit=1, offset=0)
        results = payload.get("results") or []
        return results[0] if results else None

    def delete_address(self, address: str) -> dict[str, Any]:
        payload = self._request("DELETE", f"/admin/delete_address/{address}", headers=self._admin_headers())
        if isinstance(payload, dict):
            return payload
        return {"success": str(payload).lower() == "true", "raw": payload}
