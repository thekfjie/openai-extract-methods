"""Sub2API admin client: account import, group binding and compliance reads."""
from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from integrations.common import first_non_empty


class Sub2ApiError(Exception):
    pass

def sub2api_account_group_ids(account: dict[str, Any]) -> set[int]:
    group_ids: set[int] = set()
    raw_group_ids = account.get("group_ids")
    if isinstance(raw_group_ids, list):
        for item in raw_group_ids:
            try:
                group_ids.add(int(item))
            except (TypeError, ValueError):
                pass
    raw_groups = account.get("groups")
    if isinstance(raw_groups, list):
        for group in raw_groups:
            if not isinstance(group, dict):
                continue
            try:
                group_ids.add(int(group.get("id")))
            except (TypeError, ValueError):
                pass
    try:
        group_ids.add(int(account.get("group_id")))
    except (TypeError, ValueError):
        pass
    return group_ids

class Sub2ApiClient:
    def __init__(
        self,
        api_url: str,
        admin_email: str,
        admin_password: str,
        admin_token: str,
        timeout_ms: int,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.admin_email = admin_email
        self.admin_password = admin_password
        self.admin_token = admin_token
        self.timeout_seconds = timeout_ms / 1000

    @property
    def configured(self) -> bool:
        return bool(self.api_url and (self.admin_token or (self.admin_email and self.admin_password)))

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        token: str = "",
    ) -> Any:
        if not self.api_url:
            raise Sub2ApiError("未配置 SUB2API_API_URL")
        url = f"{self.api_url}{path}"
        if query:
            query_string = urlencode({key: value for key, value in query.items() if value not in (None, "")})
            if query_string:
                url = f"{url}?{query_string}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
            "User-Agent": "help-oai-sub2api-client/1.0",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(url, data=data, method=method, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                text = response.read().decode("utf-8", errors="replace").strip()
        except HTTPError as error:
            body_text = error.read().decode("utf-8", errors="replace").strip()
            detail = body_text
            if body_text.startswith("{"):
                try:
                    payload = json.loads(body_text)
                    metadata = payload.get("metadata") if isinstance(payload, dict) else None
                    detail = str(
                        first_non_empty(
                            payload.get("detail") if isinstance(payload, dict) else "",
                            payload.get("message") if isinstance(payload, dict) else "",
                            payload.get("error") if isinstance(payload, dict) else "",
                            payload.get("code") if isinstance(payload, dict) else "",
                            body_text,
                        )
                    )
                    if isinstance(metadata, dict) and metadata.get("version"):
                        detail = f"{detail} ({metadata.get('version')})"
                except Exception:
                    detail = body_text
            raise Sub2ApiError(f"Sub2API 请求失败: HTTP {error.code} {detail}".strip())
        except URLError as error:
            raise Sub2ApiError(f"Sub2API 连接失败: {error.reason}")
        if not text:
            return {}
        if text.startswith("{") or text.startswith("["):
            return json.loads(text)
        return text

    @staticmethod
    def _payload_data(payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        code = payload.get("code")
        if code not in (None, 0, "0"):
            raise Sub2ApiError(str(first_non_empty(payload.get("message"), payload.get("detail"), payload.get("error"), code)))
        if "data" in payload:
            return payload.get("data")
        return payload

    def _access_token(self) -> str:
        if self.admin_token:
            return self.admin_token
        if not self.admin_email or not self.admin_password:
            raise Sub2ApiError("未配置 SUB2API_ADMIN_TOKEN 或 SUB2API_ADMIN_EMAIL/SUB2API_ADMIN_PASSWORD")

        payload = self._request(
            "POST",
            "/auth/login",
            body={"email": self.admin_email, "password": self.admin_password},
        )
        if not isinstance(payload, dict):
            raise Sub2ApiError("Sub2API 登录返回格式异常")
        if payload.get("requires_2fa"):
            raise Sub2ApiError("Sub2API 管理账号启用了 2FA，请改用 SUB2API_ADMIN_TOKEN")
        token = first_non_empty(
            payload.get("access_token"),
            payload.get("token"),
            (payload.get("data") or {}).get("access_token") if isinstance(payload.get("data"), dict) else "",
        )
        if not token:
            raise Sub2ApiError(str(payload.get("message") or payload.get("error") or "Sub2API 登录未返回 access_token"))
        return str(token)

    def import_accounts_document(self, document: dict[str, Any]) -> dict[str, Any]:
        accounts = document.get("accounts") if isinstance(document, dict) else None
        if not isinstance(accounts, list) or not accounts:
            raise Sub2ApiError("没有可导入 Sub2API 的账号")
        payload = self._request(
            "POST",
            "/admin/accounts/data",
            body={"data": document, "skip_default_group_bind": True},
            token=self._access_token(),
        )
        return payload if isinstance(payload, dict) else {"result": payload}

    @staticmethod
    def _items_from_list_payload(payload: Any, keys: tuple[str, ...]) -> tuple[list[dict[str, Any]], int | None]:
        data = Sub2ApiClient._payload_data(payload)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)], None
        if not isinstance(data, dict):
            return [], None
        items: Any = None
        for key in keys:
            if isinstance(data.get(key), list):
                items = data.get(key)
                break
        if items is None and isinstance(data.get("data"), list):
            items = data.get("data")
        if items is None and isinstance(data.get("results"), list):
            items = data.get("results")
        total = first_non_empty(data.get("total"), data.get("count"), data.get("total_count"))
        try:
            total_int = int(total) if total is not None else None
        except (TypeError, ValueError):
            total_int = None
        return [item for item in (items or []) if isinstance(item, dict)], total_int

    def list_groups(self, page_size: int = 200) -> list[dict[str, Any]]:
        token = self._access_token()
        groups: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self._request("GET", "/admin/groups", query={"page": page, "page_size": page_size}, token=token)
            items, total = self._items_from_list_payload(payload, ("groups", "items"))
            groups.extend(items)
            if not items or (total is not None and len(groups) >= total) or len(items) < page_size:
                break
            page += 1
            if page > 50:
                break
        return groups

    def list_accounts(self, page_size: int = 200) -> list[dict[str, Any]]:
        token = self._access_token()
        accounts: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self._request("GET", "/admin/accounts", query={"page": page, "page_size": page_size}, token=token)
            items, total = self._items_from_list_payload(payload, ("accounts", "items"))
            accounts.extend(items)
            if not items or (total is not None and len(accounts) >= total) or len(items) < page_size:
                break
            page += 1
            if page > 50:
                break
        return accounts

    def recent_first_token_latency(self, sample_limit: int = 10) -> dict[str, Any]:
        """Average the newest valid Sub2API first-token latency samples."""
        limit = max(1, min(int(sample_limit or 10), 100))
        token = self._access_token()
        samples: list[float] = []
        latest_sample_at = ""
        # A page can contain non-stream requests without first_token_ms. Scan a
        # bounded number of recent rows until the requested valid sample window
        # is full, while preserving newest-first order from Sub2API.
        page_size = max(50, min(200, limit * 5))
        for page in range(1, 6):
            payload = self._request(
                "GET",
                "/admin/usage",
                query={
                    "page": page,
                    "page_size": page_size,
                    "sort_by": "created_at",
                    "sort_order": "desc",
                },
                token=token,
            )
            items, total = self._items_from_list_payload(payload, ("items", "usage", "records"))
            for item in items:
                try:
                    first_token_ms = float(item.get("first_token_ms") or 0)
                except (TypeError, ValueError):
                    first_token_ms = 0
                if first_token_ms <= 0:
                    continue
                if not latest_sample_at:
                    latest_sample_at = str(item.get("created_at") or "")
                samples.append(first_token_ms)
                if len(samples) >= limit:
                    break
            if len(samples) >= limit or not items:
                break
            if total is not None and page * page_size >= total:
                break

        average_ms = round(sum(samples) / len(samples), 2) if samples else None
        return {
            "ok": bool(samples),
            "averageMs": average_ms,
            "averageSeconds": round(average_ms / 1000, 3) if average_ms is not None else None,
            "sampleCount": len(samples),
            "windowSize": limit,
            "latestSampleAt": latest_sample_at,
        }

    def find_group_by_name(self, group_name: str, platform: str = "") -> dict[str, Any]:
        target_name = str(group_name or "").strip()
        if not target_name:
            raise Sub2ApiError("Sub2API 目标分组为空")
        matches = [
            group
            for group in self.list_groups()
            if isinstance(group, dict)
            and str(group.get("name") or "").strip().lower() == target_name.lower()
            and not group.get("deleted_at")
        ]
        if platform:
            platform_lower = platform.lower()
            platform_match = next((group for group in matches if str(group.get("platform") or "").lower() == platform_lower), None)
            if platform_match:
                return platform_match
            if matches:
                raise Sub2ApiError(f"Sub2API 分组平台不匹配: {target_name}")
        if matches:
            return matches[0]
        raise Sub2ApiError(f"Sub2API 分组不存在: {target_name}")

    def bind_accounts_to_group(self, account_ids: list[int], group_name: str, platform: str = "openai") -> dict[str, Any]:
        ids: list[int] = []
        for account_id in account_ids:
            try:
                numeric_id = int(account_id)
            except (TypeError, ValueError):
                continue
            if numeric_id > 0 and numeric_id not in ids:
                ids.append(numeric_id)
        if not ids:
            return {"success": True, "updated": 0, "skipped": 0, "results": []}

        target_group = self.find_group_by_name(group_name, platform=platform)
        try:
            target_group_id = int(target_group.get("id"))
        except (TypeError, ValueError):
            raise Sub2ApiError(f"Sub2API 分组 ID 异常: {group_name}")

        accounts = self.list_accounts()
        accounts_by_id: dict[int, dict[str, Any]] = {}
        for account in accounts:
            try:
                accounts_by_id[int(account.get("id"))] = account
            except (TypeError, ValueError):
                continue

        token = self._access_token()
        results: list[dict[str, Any]] = []
        updated = 0
        skipped = 0
        failed = 0
        for account_id in ids:
            account = accounts_by_id.get(account_id)
            if not account:
                failed += 1
                results.append({"accountId": account_id, "success": False, "error": "account_not_found"})
                continue

            group_ids = sub2api_account_group_ids(account)
            if target_group_id in group_ids:
                skipped += 1
                results.append({"accountId": account_id, "success": True, "skipped": True, "groupIds": sorted(group_ids)})
                continue

            next_group_ids = sorted(group_ids | {target_group_id})
            payload = self._request(
                "POST",
                "/admin/accounts/bulk-update",
                body={
                    "account_ids": [account_id],
                    "group_ids": next_group_ids,
                    "confirm_mixed_channel_risk": True,
                },
                token=token,
            )
            data = self._payload_data(payload)
            failed_count = 0
            if isinstance(data, dict):
                try:
                    failed_count = int(data.get("failed") or 0)
                except (TypeError, ValueError):
                    failed_count = 0
            success = failed_count == 0
            if success:
                updated += 1
            else:
                failed += 1
            results.append(
                {
                    "accountId": account_id,
                    "success": success,
                    "groupIds": next_group_ids,
                    "response": data,
                }
            )

        return {
            "success": failed == 0,
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
            "targetGroup": {
                "id": target_group.get("id"),
                "name": target_group.get("name"),
                "platform": target_group.get("platform"),
            },
            "results": results,
        }

    def admin_compliance_status(self) -> dict[str, Any]:
        payload = self._request("GET", "/admin/compliance", token=self._access_token())
        data = self._payload_data(payload)
        return data if isinstance(data, dict) else {"result": data}

    def openai_generate_auth_url(self, redirect_uri: str = "http://localhost:1455/auth/callback", proxy_id: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {}
        if redirect_uri:
            body["redirect_uri"] = redirect_uri
        if proxy_id:
            body["proxy_id"] = proxy_id
        payload = self._request("POST", "/admin/openai/generate-auth-url", body=body, token=self._access_token())
        data = self._payload_data(payload)
        if not isinstance(data, dict):
            raise Sub2ApiError("Sub2API OpenAI OAuth 链接返回格式异常")
        auth_url = str(first_non_empty(data.get("auth_url"), data.get("url")) or "")
        if not auth_url:
            raise Sub2ApiError("Sub2API OpenAI OAuth 未返回 auth_url")
        if not data.get("state"):
            try:
                data["state"] = (parse_qs(urlparse(auth_url).query).get("state") or [""])[-1]
            except Exception:
                data["state"] = ""
        data["auth_url"] = auth_url
        data.setdefault("url", auth_url)
        return data

    def openai_exchange_code(self, *, session_id: str, code: str, state: str, proxy_id: str = "") -> dict[str, Any]:
        if not session_id:
            raise Sub2ApiError("Sub2API OpenAI OAuth 缺少 session_id")
        if not code or not state:
            raise Sub2ApiError("Sub2API OpenAI OAuth 回调缺少 code/state")
        body: dict[str, Any] = {
            "session_id": session_id,
            "code": code,
            "state": state,
        }
        if proxy_id:
            body["proxy_id"] = proxy_id
        payload = self._request("POST", "/admin/openai/exchange-code", body=body, token=self._access_token())
        data = self._payload_data(payload)
        if not isinstance(data, dict):
            raise Sub2ApiError("Sub2API OpenAI OAuth 换码返回格式异常")
        return data
