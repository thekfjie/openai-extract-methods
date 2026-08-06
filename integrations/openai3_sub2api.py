"""Direct OpenAI3 credential import into Sub2API.

The registration engine still emits its existing CPA-compatible multipart
upload. The OpenAI3 control service receives that upload locally and sends the
credential document straight to Sub2API, without involving CLIProxyAPI.
"""
from __future__ import annotations

import json
from email.parser import BytesParser
from email.policy import default
from typing import Any

from converters.openai_formats import convert_openai
from integrations.common import first_non_empty


MAX_AUTH_UPLOAD_BYTES = 2 * 1024 * 1024


def extract_multipart_json_file(content_type: str, body: bytes) -> dict[str, Any]:
    if not str(content_type or "").lower().startswith("multipart/form-data"):
        raise ValueError("认证文件上传格式不是 multipart/form-data")
    if not body or len(body) > MAX_AUTH_UPLOAD_BYTES:
        raise ValueError("认证文件为空或超过 2 MiB")
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    )
    for part in message.iter_parts():
        if part.get_param("name", header="content-disposition") != "file":
            continue
        payload = part.get_payload(decode=True) or b""
        try:
            document = json.loads(payload.decode("utf-8"))
        except Exception as error:
            raise ValueError(f"认证文件 JSON 解析失败: {error}") from error
        if not isinstance(document, dict):
            raise ValueError("认证文件必须是 JSON 对象")
        return document
    raise ValueError("multipart 请求中没有 file 字段")


def _document_identity(document: dict[str, Any]) -> tuple[str, str]:
    accounts = document.get("accounts") if isinstance(document, dict) else []
    account = accounts[0] if isinstance(accounts, list) and accounts and isinstance(accounts[0], dict) else {}
    return (
        str(first_non_empty(account.get("email"), account.get("name")) or "").strip().lower(),
        str(first_non_empty(account.get("organization_id"), account.get("account_id")) or "").strip(),
    )


def matching_sub2api_account_ids(accounts: list[dict[str, Any]], document: dict[str, Any]) -> list[int]:
    target_email, target_account_id = _document_identity(document)
    matches: list[int] = []
    for account in accounts:
        credentials = account.get("credentials") if isinstance(account.get("credentials"), dict) else {}
        extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
        candidate_email = str(first_non_empty(
            credentials.get("email"),
            extra.get("email"),
            account.get("email"),
            account.get("name"),
        ) or "").strip().lower()
        candidate_account_id = str(first_non_empty(
            credentials.get("chatgpt_account_id"),
            credentials.get("account_id"),
            account.get("organization_id"),
            account.get("account_id"),
        ) or "").strip()
        if not (
            (target_account_id and candidate_account_id == target_account_id)
            or (target_email and candidate_email == target_email)
        ):
            continue
        try:
            account_id = int(account.get("id"))
        except (TypeError, ValueError):
            continue
        if account_id > 0 and account_id not in matches:
            matches.append(account_id)
    return matches


def import_auth_to_sub2api(client: Any, auth_document: dict[str, Any], group_name: str) -> dict[str, Any]:
    target_group = str(group_name or "").strip()
    if not target_group:
        raise ValueError("Sub2API 目标分组不能为空")
    document = convert_openai([auth_document], "sub2api")
    accounts = document.get("accounts") if isinstance(document, dict) else []
    if not isinstance(accounts, list) or not accounts or not accounts[0].get("access_token"):
        raise ValueError("认证结果缺少可导入 Sub2API 的 access_token")
    imported = client.import_accounts_document(document)
    account_ids = matching_sub2api_account_ids(client.list_accounts(), document)
    if not account_ids:
        raise ValueError("Sub2API 导入完成后未找到对应账号")
    bound = client.bind_accounts_to_group(account_ids, target_group, platform="openai")
    if not bound.get("success"):
        raise ValueError(f"绑定 Sub2API 分组 {target_group} 失败")
    return {
        "ok": True,
        "target": "sub2api",
        "group": target_group,
        "imported": bool(imported),
        "updated": int(bound.get("updated") or 0),
        "skipped": int(bound.get("skipped") or 0),
    }
