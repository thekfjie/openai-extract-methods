from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from integrations.common import sanitize_sso
from integrations.grok_oauth import (
    auth_file_to_token,
    parse_sso_lines,
    sso_to_token,
    token_to_auth_entry,
    token_to_cliproxy_entry,
)


def parse_grok_input(text: str) -> list[dict[str, Any]]:
    text = str(text or "").strip()
    if not text:
        return []
    # try json first
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return [payload]
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict) or isinstance(x, str)]
    except Exception:
        pass
    # sso lines
    rows = parse_sso_lines(text)
    return [{"sso": sso, "email": email} for sso, email in rows]


def convert_grok(
    items: list[Any],
    target: str,
    *,
    proxy: str = "",
    do_device_flow: bool = True,
    log=print,
) -> list[dict[str, Any]]:
    target = (target or "cpa").lower()
    results: list[dict[str, Any]] = []
    for item in items:
        email = ""
        token = None
        sso = ""
        if isinstance(item, str):
            sso = sanitize_sso(item)
        elif isinstance(item, dict):
            if item.get("sso") or item.get("token"):
                sso = sanitize_sso(item.get("sso") or item.get("token"))
                email = str(item.get("email") or "")
            else:
                parsed = auth_file_to_token(item)
                if parsed:
                    token, email2 = parsed
                    email = email or email2
        if token is None and sso and do_device_flow:
            token = sso_to_token(sso, proxy=proxy, log=log)
        if token is None and sso and target == "sso":
            results.append({"sso": sso, "email": email})
            continue
        if token is None:
            results.append({"error": "无法解析/换取 token", "email": email, "hasSso": bool(sso)})
            continue
        if target in {"cpa", "xai", "cliproxy"}:
            filename, entry = token_to_cliproxy_entry(token, email=email)
            results.append({"filename": filename, "entry": entry})
        elif target in {"auth", "auth.json", "nested", "grok"}:
            key, entry = token_to_auth_entry(token, email=email)
            results.append({"key": key, "entry": entry, "document": {key: entry}})
        elif target == "token":
            results.append({"token": token, "email": email or token.get("email") or token.get("_email") or ""})
        elif target == "sso":
            results.append({"sso": sso, "email": email})
        else:
            raise ValueError(f"不支持的 Grok 目标格式: {target}")
    return results
