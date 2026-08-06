from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from integrations.common import decode_jwt_payload, first_non_empty


DEFAULT_MODEL_MAPPING = {
    "gpt-5.1": "gpt-5.1",
    "gpt-5.1-codex": "gpt-5.1-codex",
    "gpt-5.1-codex-max": "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini": "gpt-5.1-codex-mini",
    "gpt-5.2": "gpt-5.2",
    "gpt-5.2-codex": "gpt-5.2-codex",
    "gpt-5.3": "gpt-5.3",
    "gpt-5.3-codex": "gpt-5.3-codex",
    "gpt-5.4": "gpt-5.4",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    dt = dt or _now()
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_list(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("accounts", "items", "data", "tokens"):
            if isinstance(payload.get(key), list):
                return [x for x in payload[key] if isinstance(x, dict)]
        return [payload]
    return []


def detect_openai_kind(doc: dict[str, Any]) -> str:
    if not isinstance(doc, dict):
        return "unknown"
    if "accessToken" in doc or "sessionToken" in doc:
        return "session"
    if doc.get("type") in {"codex", "openai"} and ("access_token" in doc or "session_token" in doc):
        return "cpa"
    if isinstance(doc.get("tokens"), dict) and (
        "access_token" in doc["tokens"] or "refresh_token" in doc["tokens"]
    ):
        if "meta" in doc:
            return "codexmanager"
        return "codex"
    if "providerSpecificData" in doc:
        return "9router"
    if "account_note" in doc:
        return "cockpit"
    if isinstance(doc.get("credentials"), dict) and (
        doc.get("platform") == "openai"
        or any(
            key in doc["credentials"]
            for key in ("access_token", "refresh_token", "id_token", "chatgpt_account_id")
        )
    ):
        return "sub2api"
    if isinstance(doc.get("accounts"), list):
        return "sub2api"
    return "generic"


def normalize_account(doc: dict[str, Any], *, name_prefix: str = "", plan_type: str = "") -> dict[str, Any]:
    kind = detect_openai_kind(doc)
    email = ""
    access = ""
    refresh = ""
    session = ""
    id_token = ""
    account_id = ""
    plan = plan_type
    plan_known = bool(plan_type)
    expired = ""

    if kind == "session":
        user = doc.get("user") if isinstance(doc.get("user"), dict) else {}
        account = doc.get("account") if isinstance(doc.get("account"), dict) else {}
        email = first_non_empty(user.get("email"), doc.get("email"), "") or ""
        access = first_non_empty(doc.get("accessToken"), doc.get("access_token"), "") or ""
        session = first_non_empty(doc.get("sessionToken"), doc.get("session_token"), "") or ""
        refresh = first_non_empty(doc.get("refreshToken"), doc.get("refresh_token"), "") or ""
        id_token = first_non_empty(doc.get("idToken"), doc.get("id_token"), "") or ""
        account_id = first_non_empty(account.get("id"), doc.get("account_id"), "") or ""
        plan = plan or first_non_empty(account.get("planType"), doc.get("plan_type"), "") or ""
        expired = first_non_empty(doc.get("expires"), doc.get("expired"), "") or ""
    elif kind == "codex":
        tokens = doc.get("tokens") if isinstance(doc.get("tokens"), dict) else {}
        access = tokens.get("access_token") or ""
        refresh = tokens.get("refresh_token") or ""
        id_token = tokens.get("id_token") or ""
        account_id = tokens.get("account_id") or doc.get("account_id") or ""
        email = doc.get("email") or ""
        expired = doc.get("last_refresh") or ""
    elif kind == "codexmanager":
        tokens = doc.get("tokens") if isinstance(doc.get("tokens"), dict) else {}
        meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
        access = tokens.get("access_token") or ""
        refresh = tokens.get("refresh_token") or ""
        id_token = tokens.get("id_token") or ""
        account_id = meta.get("chatgpt_account_id") or meta.get("workspace_id") or ""
        email = meta.get("label") or meta.get("note") or ""
    elif kind == "cpa":
        access = doc.get("access_token") or ""
        refresh = doc.get("refresh_token") or ""
        session = doc.get("session_token") or ""
        id_token = doc.get("id_token") or ""
        email = doc.get("email") or ""
        account_id = doc.get("account_id") or ""
        plan = plan or doc.get("plan_type") or ""
        expired = doc.get("expired") or ""
    elif kind == "9router":
        specific = doc.get("providerSpecificData") if isinstance(doc.get("providerSpecificData"), dict) else {}
        access = doc.get("access_token") or doc.get("token") or ""
        refresh = doc.get("refresh_token") or ""
        email = doc.get("email") or doc.get("name") or ""
        account_id = specific.get("chatgptAccountId") or ""
        plan = plan or specific.get("chatgptPlanType") or ""
    elif kind == "sub2api":
        # Supports both the current Sub2API data bundle (nested credentials)
        # and the older flat account object emitted by previous AutoMyAI builds.
        credentials = doc.get("credentials") if isinstance(doc.get("credentials"), dict) else {}
        extra = doc.get("extra") if isinstance(doc.get("extra"), dict) else {}
        access = first_non_empty(
            doc.get("access_token"), doc.get("token"), credentials.get("access_token"), ""
        ) or ""
        refresh = first_non_empty(
            doc.get("refresh_token"), credentials.get("refresh_token"), ""
        ) or ""
        session = first_non_empty(
            doc.get("session_token"), credentials.get("session_token"), ""
        ) or ""
        id_token = first_non_empty(doc.get("id_token"), credentials.get("id_token"), "") or ""
        email = first_non_empty(
            doc.get("email"), credentials.get("email"), extra.get("email"), doc.get("name"), ""
        ) or ""
        account_id = first_non_empty(
            doc.get("chatgpt_account_id"),
            credentials.get("chatgpt_account_id"),
            doc.get("organization_id"),
            credentials.get("organization_id"),
            doc.get("account_id"),
            "",
        ) or ""
        plan_value = first_non_empty(doc.get("plan_type"), credentials.get("plan_type"), "") or ""
        if plan_value:
            plan = plan or plan_value
            plan_known = True
        expired = first_non_empty(
            doc.get("expired"), doc.get("expires_at"), credentials.get("expires_at"), ""
        ) or ""
    else:
        access = first_non_empty(doc.get("access_token"), doc.get("accessToken"), doc.get("token"), "") or ""
        refresh = first_non_empty(doc.get("refresh_token"), doc.get("refreshToken"), "") or ""
        session = first_non_empty(doc.get("session_token"), doc.get("sessionToken"), "") or ""
        id_token = first_non_empty(doc.get("id_token"), doc.get("idToken"), "") or ""
        email = first_non_empty(doc.get("email"), doc.get("name"), "") or ""
        account_id = first_non_empty(doc.get("account_id"), doc.get("organization_id"), "") or ""
        plan = plan or first_non_empty(doc.get("plan_type"), doc.get("planType"), "") or ""
        expired = first_non_empty(doc.get("expired"), doc.get("expires"), "") or ""

    payload = decode_jwt_payload(access)
    if not email:
        email = str(payload.get("email") or "")
    if not account_id:
        account_id = str(payload.get("https://api.openai.com/auth") or payload.get("auth") or payload.get("sub") or "")
        if isinstance(account_id, dict):
            account_id = str(account_id.get("chatgpt_account_id") or account_id.get("user_id") or "")
    if not plan:
        payload_plan = str(payload.get("chatgpt_plan_type") or payload.get("plan_type") or "")
        if payload_plan:
            plan = payload_plan
            plan_known = True
    if not plan:
        plan = "plus"
    elif not plan_known:
        plan_known = True
    generated_id_token = False
    if not id_token:
        # synthetic placeholder for gateways that require a 3-part JWT-like field
        header = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
        body_obj = {
            "email": email,
            "sub": account_id or email or "unknown",
            "chatgpt_account_id": account_id,
            "chatgpt_plan_type": plan or "plus",
            "iat": int(time.time()),
            "exp": int(time.time()) + 86400 * 30,
        }
        import base64

        body = base64.urlsafe_b64encode(json.dumps(body_obj, separators=(",", ":")).encode()).decode().rstrip("=")
        id_token = f"{header}.{body}."
        generated_id_token = True

    name = email or account_id or ("OpenAI RT" if refresh and not access else "account")
    if name_prefix:
        name = f"{name_prefix}{name}"

    return {
        "kind": kind,
        "email": email,
        "name": name,
        "access_token": access,
        "refresh_token": refresh,
        "session_token": session,
        "id_token": id_token,
        "account_id": str(account_id or ""),
        "plan_type": plan or "plus",
        "plan_type_known": plan_known,
        "expired": expired or "",
        "id_token_generated": generated_id_token,
        "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
    }


def parse_openai_input(text: str) -> list[dict[str, Any]]:
    text = str(text or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except Exception as error:
        # Sub2API's admin UI accepts one raw RT per line. Mirror that behavior
        # here, while still allowing mixed JSON-lines and token-lines input.
        docs: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip().strip(",")
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                item = None
            if isinstance(item, dict):
                docs.append(item)
                continue
            if isinstance(item, list):
                docs.extend(x for x in item if isinstance(x, dict))
                continue
            token = re.sub(r"^(?:Bearer\s+|(?:rt|refresh[_ -]?token)\s*[:=]\s*)", "", line, flags=re.I).strip()
            if token:
                key = "access_token" if token.startswith("eyJ") else "refresh_token"
                docs.append({key: token})
        if docs:
            return docs
        raise ValueError(f"JSON 解析失败: {error}") from error

    docs = _as_list(payload)
    if docs:
        return docs
    if isinstance(payload, str) and payload.strip():
        token = payload.strip()
        key = "access_token" if token.startswith("eyJ") else "refresh_token"
        return [{key: token}]
    return []


def convert_openai(docs: list[dict[str, Any]], target: str, *, name_prefix: str = "", plan_type: str = "") -> Any:
    converted = [normalize_account(doc, name_prefix=name_prefix, plan_type=plan_type) for doc in docs]
    target = (target or "sub2api").lower()
    now = _iso()

    if target == "sub2api":
        accounts = []
        for index, item in enumerate(converted, start=1):
            name = item["name"]
            if len(converted) > 1 and name in {"OpenAI RT", "account"}:
                name = f"{name} #{index}"
            credentials: dict[str, Any] = {}
            for key in ("access_token", "refresh_token", "session_token"):
                if item.get(key):
                    credentials[key] = item[key]
            if item.get("id_token") and not item.get("id_token_generated"):
                credentials["id_token"] = item["id_token"]
            if item.get("email"):
                credentials["email"] = item["email"]
            if item.get("account_id"):
                credentials["chatgpt_account_id"] = item["account_id"]
            if item.get("plan_type_known"):
                credentials["plan_type"] = item["plan_type"]
            if item.get("expired"):
                credentials["expires_at"] = item["expired"]
            if item.get("client_id"):
                credentials["client_id"] = item["client_id"]
            credentials["model_mapping"] = dict(DEFAULT_MODEL_MAPPING)

            extra: dict[str, Any] = {"source": "automyai_converter"}
            if item.get("email"):
                extra["email"] = item["email"]
            extra["name"] = name
            accounts.append(
                {
                    "name": name,
                    "platform": "openai",
                    "type": "oauth",
                    "credentials": credentials,
                    "extra": extra,
                    "concurrency": 10,
                    "priority": 1,
                }
            )
        return {
            "type": "sub2api-data",
            "version": 1,
            "exported_at": now,
            "proxies": [],
            "accounts": accounts,
        }

    if target == "codex":
        # single or list
        out = []
        for item in converted:
            out.append(
                {
                    "auth_mode": "chatgpt",
                    "last_refresh": now,
                    "tokens": {
                        "id_token": item["id_token"],
                        "access_token": item["access_token"],
                        "refresh_token": item["refresh_token"],
                        "account_id": item["account_id"],
                    },
                }
            )
        return out[0] if len(out) == 1 else out

    if target == "cpa":
        out = []
        for item in converted:
            out.append(
                {
                    "type": "codex",
                    "email": item["email"],
                    "account_id": item["account_id"],
                    "plan_type": item["plan_type"],
                    "id_token": item["id_token"],
                    "access_token": item["access_token"],
                    "refresh_token": item["refresh_token"],
                    "session_token": item["session_token"],
                    "expired": item["expired"] or now,
                }
            )
        return out[0] if len(out) == 1 else out

    if target == "cockpit":
        out = []
        for item in converted:
            out.append(
                {
                    "email": item["email"],
                    "access_token": item["access_token"],
                    "refresh_token": item["refresh_token"],
                    "account_note": item["name"],
                    "plan_type": item["plan_type"],
                }
            )
        return out[0] if len(out) == 1 else out

    if target == "9router":
        out = []
        for item in converted:
            out.append(
                {
                    "email": item["email"],
                    "access_token": item["access_token"],
                    "refresh_token": item["refresh_token"],
                    "isActive": True,
                    "testStatus": "unknown",
                    "createdAt": now,
                    "updatedAt": now,
                    "providerSpecificData": {
                        "chatgptAccountId": item["account_id"],
                        "chatgptPlanType": item["plan_type"],
                    },
                }
            )
        return out[0] if len(out) == 1 else out

    if target == "axonhub":
        out = []
        for item in converted:
            out.append(
                {
                    "auth_mode": "chatgpt",
                    "last_refresh": now,
                    "tokens": {
                        "access_token": item["access_token"],
                        "refresh_token": item["refresh_token"] or "__missing_refresh_token__",
                        "id_token": item["id_token"],
                    },
                }
            )
        return out[0] if len(out) == 1 else out

    if target in {"codexmanager", "codex-manager"}:
        out = []
        for item in converted:
            out.append(
                {
                    "tokens": {
                        "access_token": item["access_token"],
                        "refresh_token": item["refresh_token"],
                        "id_token": item["id_token"],
                    },
                    "meta": {
                        "label": item["email"] or item["name"],
                        "workspace_id": item["account_id"],
                        "chatgpt_account_id": item["account_id"],
                        "note": item["name"],
                    },
                }
            )
        return out[0] if len(out) == 1 else out

    raise ValueError(f"不支持的 OpenAI 目标格式: {target}")
