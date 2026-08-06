"""OutlookEmail service clients: mailbox reads and the admin group/account API."""
from __future__ import annotations

import json
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from integrations.text_utils import html_to_text


class OutlookEmailError(Exception):
    pass

class OutlookEmailClient:
    def __init__(self, base_url: str, api_key: str, timeout_ms: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_ms / 1000

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _request(self, path: str, query: dict[str, Any] | None = None) -> Any:
        if not self.base_url:
            raise OutlookEmailError("未配置 OUTLOOK_EMAIL_API_URL")
        if not self.api_key:
            raise OutlookEmailError("未配置 OUTLOOK_EMAIL_API_KEY")
        query_string = urlencode({key: value for key, value in (query or {}).items() if value not in (None, "")})
        url = f"{self.base_url}{path}"
        if query_string:
            url = f"{url}?{query_string}"
        request = Request(
            url,
            method="GET",
            headers={
                "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
                "User-Agent": "help-oai-outlook-email-client/1.0",
                "X-API-Key": self.api_key,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                text = response.read().decode("utf-8", errors="replace").strip()
        except HTTPError as error:
            body_text = error.read().decode("utf-8", errors="replace").strip()
            raise OutlookEmailError(f"OutlookEmail 请求失败: HTTP {error.code} {body_text}".strip())
        except URLError as error:
            raise OutlookEmailError(f"OutlookEmail 连接失败: {error.reason}")
        if not text:
            return {}
        if text.startswith("{") or text.startswith("["):
            return json.loads(text)
        return text

    def list_accounts(self, limit: int = 10000, offset: int = 0) -> dict[str, Any]:
        payload = self._request(
            "/api/external/accounts",
            {"limit": int(limit), "offset": int(offset), "sort_by": "created_at", "sort_order": "desc"},
        )
        if not isinstance(payload, dict):
            raise OutlookEmailError("OutlookEmail 账号列表返回格式异常")
        return payload

    def list_mails(self, address: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        payload = self._request(
            "/api/external/emails",
            {
                "email": address,
                "folder": "all",
                "top": max(1, min(int(limit), 50)),
                "skip": max(0, int(offset)),
            },
        )
        if not isinstance(payload, dict):
            raise OutlookEmailError("OutlookEmail 邮件列表返回格式异常")
        if not payload.get("success"):
            raise OutlookEmailError(str(payload.get("error") or "OutlookEmail 邮件列表请求失败"))
        emails = payload.get("emails") or []
        if not isinstance(emails, list):
            emails = []
        return {
            "success": True,
            "count": len(emails),
            "results": [self._normalize_mail_item(address, item, payload) for item in emails],
            "source": "outlookEmail",
            "method": payload.get("method"),
            "requestedEmail": payload.get("requested_email") or address,
            "resolvedEmail": payload.get("resolved_email") or "",
            "raw": payload,
        }

    def latest_mail(self, address: str) -> dict[str, Any] | None:
        payload = self.list_mails(address, limit=5, offset=0)
        results = payload.get("results") or []
        return results[0] if results else None

    @staticmethod
    def _normalize_mail_item(address: str, item: Any, payload: dict[str, Any]) -> dict[str, Any]:
        source = item if isinstance(item, dict) else {"raw": item}
        subject = str(source.get("subject") or "").strip()
        body_preview = str(source.get("body_preview") or source.get("bodyPreview") or "").strip()
        body = str(source.get("body") or source.get("content") or "").strip()
        html = str(source.get("html") or source.get("html_content") or "").strip()
        raw_lines = [
            f"Subject: {subject}",
            "",
            html_to_text(html) if html else "",
            body,
            body_preview,
        ]
        raw = "\n".join(line for line in raw_lines if line)
        return {
            "id": source.get("id") or source.get("message_id") or source.get("provider_message_id") or "",
            "address": address,
            "from": source.get("from") or source.get("sender") or "",
            "subject": subject,
            "date": source.get("date") or source.get("received_at") or "",
            "folder": source.get("folder") or "",
            "isRead": bool(source.get("is_read")),
            "hasAttachments": bool(source.get("has_attachments")),
            "body_preview": body_preview,
            "body": body,
            "html": html,
            "raw": raw,
            "source": "outlookEmail",
            "resolvedEmail": payload.get("resolved_email") or "",
            "rawItem": source,
        }

class OutlookEmailAdminClient:
    def __init__(self, base_url: str, admin_password: str, timeout_ms: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.admin_password = admin_password
        self.timeout_seconds = timeout_ms / 1000

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.admin_password)

    def _decode_response(self, response: Any) -> Any:
        text = response.read().decode("utf-8", errors="replace").strip()
        if not text:
            return {}
        if text.startswith("{") or text.startswith("["):
            return json.loads(text)
        return text

    def _session(self) -> tuple[Any, str]:
        if not self.base_url:
            raise OutlookEmailError("未配置 OUTLOOK_EMAIL_API_URL")
        if not self.admin_password:
            raise OutlookEmailError("未配置 OUTLOOK_EMAIL_ADMIN_PASSWORD")

        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        login_request = Request(
            f"{self.base_url}/login",
            data=json.dumps({"password": self.admin_password}).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
                "User-Agent": "help-oai-outlook-email-admin/1.0",
            },
        )
        try:
            with opener.open(login_request, timeout=self.timeout_seconds) as response:
                payload = self._decode_response(response)
        except HTTPError as error:
            body_text = error.read().decode("utf-8", errors="replace").strip()
            raise OutlookEmailError(f"OutlookEmail 管理登录失败: HTTP {error.code} {body_text}".strip())
        except URLError as error:
            raise OutlookEmailError(f"OutlookEmail 管理接口连接失败: {error.reason}")

        if isinstance(payload, dict) and not payload.get("success"):
            raise OutlookEmailError(str(payload.get("error") or "OutlookEmail 管理登录失败"))

        csrf_token = ""
        csrf_request = Request(
            f"{self.base_url}/api/csrf-token",
            method="GET",
            headers={
                "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
                "User-Agent": "help-oai-outlook-email-admin/1.0",
            },
        )
        try:
            with opener.open(csrf_request, timeout=self.timeout_seconds) as response:
                csrf_payload = self._decode_response(response)
        except HTTPError as error:
            body_text = error.read().decode("utf-8", errors="replace").strip()
            raise OutlookEmailError(f"OutlookEmail CSRF 获取失败: HTTP {error.code} {body_text}".strip())
        except URLError as error:
            raise OutlookEmailError(f"OutlookEmail 管理接口连接失败: {error.reason}")
        if isinstance(csrf_payload, dict):
            csrf_token = str(csrf_payload.get("csrf_token") or "")
        return opener, csrf_token

    def _request(
        self,
        opener: Any,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        csrf_token: str = "",
    ) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
            "User-Agent": "help-oai-outlook-email-admin/1.0",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        if csrf_token:
            headers["X-CSRFToken"] = csrf_token
            headers["X-CSRF-Token"] = csrf_token
        request = Request(f"{self.base_url}{path}", data=data, method=method, headers=headers)
        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                return self._decode_response(response)
        except HTTPError as error:
            body_text = error.read().decode("utf-8", errors="replace").strip()
            raise OutlookEmailError(f"OutlookEmail 管理请求失败: HTTP {error.code} {body_text}".strip())
        except URLError as error:
            raise OutlookEmailError(f"OutlookEmail 管理接口连接失败: {error.reason}")

    def list_groups(self) -> list[dict[str, Any]]:
        opener, csrf_token = self._session()
        payload = self._request(opener, "GET", "/api/groups", csrf_token=csrf_token)
        if not isinstance(payload, dict) or not isinstance(payload.get("groups"), list):
            raise OutlookEmailError("OutlookEmail 分组列表返回格式异常")
        return [group for group in payload["groups"] if isinstance(group, dict)]

    def ensure_groups(self, names: list[str]) -> dict[str, Any]:
        # Local import: server owns the live settings object and imports this module.
        from server import CONFIG

        opener, csrf_token = self._session()
        payload = self._request(opener, "GET", "/api/groups", csrf_token=csrf_token)
        groups = payload.get("groups") if isinstance(payload, dict) else None
        if not isinstance(groups, list):
            raise OutlookEmailError("OutlookEmail 分组列表返回格式异常")

        by_name = {str(group.get("name") or ""): group for group in groups if isinstance(group, dict)}
        created: list[dict[str, Any]] = []
        color_by_name = {
            CONFIG.mail_pending_group_name: "#605e5c",
            CONFIG.mail_success_group_name: "#107c10",
            CONFIG.mail_bad_group_name: "#a4262c",
            CONFIG.mail_source_group_name: "#666666",
        }
        for raw_name in names:
            name = str(raw_name or "").strip()
            if not name or name in by_name:
                continue
            create_payload = self._request(
                opener,
                "POST",
                "/api/groups",
                body={
                    "name": name,
                    "description": "automyai 自动分组",
                    "color": color_by_name.get(name, "#1a1a1a"),
                },
                csrf_token=csrf_token,
            )
            if not isinstance(create_payload, dict) or not create_payload.get("success"):
                error_text = str((create_payload or {}).get("error") if isinstance(create_payload, dict) else "")
                raise OutlookEmailError(error_text or f"创建分组失败: {name}")
            group = {"id": create_payload.get("group_id"), "name": name, "created": True}
            by_name[name] = group
            created.append(group)

        payload = self._request(opener, "GET", "/api/groups", csrf_token=csrf_token)
        refreshed_groups = payload.get("groups") if isinstance(payload, dict) else []
        if not isinstance(refreshed_groups, list):
            refreshed_groups = list(by_name.values())
        return {
            "success": True,
            "created": created,
            "groups": [group for group in refreshed_groups if isinstance(group, dict)],
        }

    def move_accounts(self, account_ids: list[int], target_group_name: str) -> dict[str, Any]:
        ids = [int(account_id) for account_id in account_ids if str(account_id).strip()]
        if not ids:
            raise OutlookEmailError("没有可移动的账号 ID")

        ensured = self.ensure_groups([target_group_name])
        groups = ensured.get("groups") if isinstance(ensured, dict) else []
        target_group = next((group for group in groups if isinstance(group, dict) and str(group.get("name") or "") == target_group_name), None)
        if not target_group or not target_group.get("id"):
            raise OutlookEmailError(f"目标分组不存在: {target_group_name}")

        opener, csrf_token = self._session()
        payload = self._request(
            opener,
            "POST",
            "/api/accounts/batch-update-group",
            body={"account_ids": ids, "group_id": int(target_group["id"])},
            csrf_token=csrf_token,
        )
        if isinstance(payload, dict) and payload.get("success"):
            return {
                **payload,
                "targetGroup": target_group,
                "accountIds": ids,
                "movedCount": len(ids),
            }
        raise OutlookEmailError(str((payload or {}).get("error") if isinstance(payload, dict) else payload) or "移动账号分组失败")

    @staticmethod
    def _project_key_path(project_key: str) -> str:
        key = str(project_key or "").strip().lower()
        if not key:
            raise OutlookEmailError("OutlookEmail 项目标识不能为空")
        return quote(key, safe="")

    def get_project(self, project_key: str) -> dict[str, Any] | None:
        """读取项目；不存在时返回 None。不会修改 OutlookEmail 状态。"""
        opener, csrf_token = self._session()
        path = f"/api/projects/{self._project_key_path(project_key)}"
        try:
            payload = self._request(opener, "GET", path, csrf_token=csrf_token)
        except OutlookEmailError as error:
            if "HTTP 404" in str(error):
                return None
            raise
        if not isinstance(payload, dict) or not payload.get("success"):
            return None
        data = payload.get("data")
        return data.get("project") if isinstance(data, dict) and isinstance(data.get("project"), dict) else None

    def start_project(
        self,
        project_key: str,
        *,
        name: str = "",
        description: str = "",
        group_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        opener, csrf_token = self._session()
        body: dict[str, Any] = {"project_key": str(project_key or "").strip().lower()}
        if str(name or "").strip():
            body["name"] = str(name).strip()
        if str(description or "").strip():
            body["description"] = str(description).strip()
        if group_ids is not None:
            body["group_ids"] = [int(value) for value in group_ids if str(value).strip()]
            body["use_alias_email"] = False
        payload = self._request(opener, "POST", "/api/projects/start", body=body, csrf_token=csrf_token)
        if not isinstance(payload, dict) or not payload.get("success"):
            raise OutlookEmailError(str((payload or {}).get("error") if isinstance(payload, dict) else payload) or "启动 OutlookEmail 项目失败")
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def list_project_accounts(self, project_key: str, *, status: str = "") -> dict[str, Any]:
        opener, csrf_token = self._session()
        path = f"/api/projects/{self._project_key_path(project_key)}/accounts"
        if status:
            path += "?status=" + quote(str(status), safe="")
        payload = self._request(opener, "GET", path, csrf_token=csrf_token)
        if not isinstance(payload, dict) or not payload.get("success"):
            raise OutlookEmailError(str((payload or {}).get("error") if isinstance(payload, dict) else payload) or "读取 OutlookEmail 项目失败")
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def claim_project_account(
        self,
        project_key: str,
        *,
        caller_id: str,
        task_id: str,
        lease_seconds: int = 900,
    ) -> dict[str, Any] | None:
        opener, csrf_token = self._session()
        path = f"/api/projects/{self._project_key_path(project_key)}/claim-random"
        payload = self._request(
            opener,
            "POST",
            path,
            body={"caller_id": caller_id, "task_id": task_id, "lease_seconds": lease_seconds},
            csrf_token=csrf_token,
        )
        if isinstance(payload, dict) and payload.get("success"):
            data = payload.get("data")
            return data if isinstance(data, dict) else None
        if isinstance(payload, dict) and "没有可领取" in str(payload.get("error") or ""):
            return None
        raise OutlookEmailError(str((payload or {}).get("error") if isinstance(payload, dict) else payload) or "领取 OutlookEmail 项目邮箱失败")

    def _finish_project_account(self, project_key: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
        opener, csrf_token = self._session()
        path = f"/api/projects/{self._project_key_path(project_key)}/{action}"
        payload = self._request(opener, "POST", path, body=body, csrf_token=csrf_token)
        if not isinstance(payload, dict) or not payload.get("success"):
            raise OutlookEmailError(str((payload or {}).get("error") if isinstance(payload, dict) else payload) or f"OutlookEmail 项目结果回写失败: {action}")
        return payload

    def complete_project_success(self, project_key: str, *, account_id: int, claim_token: str, caller_id: str = "", task_id: str = "", detail: str = "") -> dict[str, Any]:
        return self._finish_project_account(project_key, "complete-success", {"account_id": int(account_id), "claim_token": claim_token, "caller_id": caller_id, "task_id": task_id, "detail": detail})

    def complete_project_failed(self, project_key: str, *, account_id: int, claim_token: str, caller_id: str = "", task_id: str = "", detail: str = "") -> dict[str, Any]:
        return self._finish_project_account(project_key, "complete-failed", {"account_id": int(account_id), "claim_token": claim_token, "caller_id": caller_id, "task_id": task_id, "detail": detail})

    def release_project_account(self, project_key: str, *, account_id: int, claim_token: str, caller_id: str = "", task_id: str = "", detail: str = "") -> dict[str, Any]:
        return self._finish_project_account(project_key, "release", {"account_id": int(account_id), "claim_token": claim_token, "caller_id": caller_id, "task_id": task_id, "detail": detail})

    def import_accounts(
        self,
        account_string: str,
        *,
        group_name: str = "",
        group_id: int | None = None,
        provider: str = "outlook",
        account_format: str = "client_id_refresh_token",
        status: str = "active",
        remark: str = "",
    ) -> dict[str, Any]:
        """Import four-segment Outlook lines into a target group via admin API."""
        lines = [
            line.strip()
            for line in str(account_string or "").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if not lines:
            raise OutlookEmailError("没有可导入的账号行")

        target_group_name = str(group_name or "").strip()
        target_group_id = int(group_id) if group_id not in (None, "") else None
        if target_group_id is None:
            if not target_group_name:
                from server import CONFIG
                target_group_name = str(CONFIG.mail_source_group_name or "默认分组").strip() or "默认分组"
            ensured = self.ensure_groups([target_group_name])
            groups = ensured.get("groups") if isinstance(ensured, dict) else []
            target = next(
                (
                    group
                    for group in groups
                    if isinstance(group, dict) and str(group.get("name") or "") == target_group_name
                ),
                None,
            )
            if not target or not target.get("id"):
                raise OutlookEmailError(f"目标分组不存在: {target_group_name}")
            target_group_id = int(target["id"])
            target_group_name = str(target.get("name") or target_group_name)
        else:
            target_group_name = target_group_name or ""

        opener, csrf_token = self._session()
        payload = self._request(
            opener,
            "POST",
            "/api/accounts",
            body={
                "account_string": "\n".join(lines),
                "group_id": int(target_group_id),
                "provider": str(provider or "outlook").strip() or "outlook",
                "account_format": str(account_format or "client_id_refresh_token").strip() or "client_id_refresh_token",
                "status": str(status or "active").strip() or "active",
                "remark": str(remark or ""),
            },
            csrf_token=csrf_token,
        )
        if isinstance(payload, dict) and payload.get("success"):
            return {
                **payload,
                "groupId": int(target_group_id),
                "groupName": target_group_name,
                "lineCount": len(lines),
            }
        raise OutlookEmailError(
            str((payload or {}).get("error") if isinstance(payload, dict) else payload) or "导入 OutlookEmail 账号失败"
        )
