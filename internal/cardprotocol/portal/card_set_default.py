from __future__ import annotations
import json
import re
import sys
import time
import base64
import hashlib
import uuid
import os
from pathlib import Path
from curl_cffi.requests import Session

ACCOUNT_FILE = "/opt/account-service/success_accounts.jsonl"
ACCOUNT_API_BASE = os.getenv("CARD_ACCOUNT_API_BASE", "").strip().rstrip("/")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"


def load_account(email: str):
    match = None
    account_path = Path(ACCOUNT_FILE)
    if not account_path.is_file():
        return None
    with account_path.open(encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except Exception:
                continue
            if str(item.get("email") or "").strip().lower() == email:
                match = item
    return match


def list_payment_method_ids(http: Session, headers: dict, account_id: str) -> set[str]:
    response = http.get(
        ACCOUNT_API_BASE + "/backend-api/payments/payment_methods",
        params={"account_id": account_id},
        headers=headers,
        timeout=45,
    )
    if response.status_code != 200:
        return set()
    try:
        payload = response.json()
    except Exception:
        return set()
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("data") or payload.get("payment_methods") or []
    else:
        items = []
    return {str(item.get("id") or "") for item in items if isinstance(item, dict)}


def main() -> int:
    if not ACCOUNT_API_BASE:
        print(json.dumps({"ok": False, "error": "ACCOUNT_API_BASE_MISSING"}))
        return 11
    email = str(sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    payment_method_id = str(sys.argv[2] if len(sys.argv) > 2 else "").strip()
    if not re.fullmatch(r"pm_[A-Za-z0-9]+", payment_method_id):
        print(json.dumps({"ok": False, "error": "INVALID_PAYMENT_METHOD"}))
        return 2

    access_token_override = str(sys.argv[4] if len(sys.argv) > 4 else "").strip()
    account = None if access_token_override else load_account(email)
    if access_token_override:
        try:
            segment = access_token_override.split(".")[1]
            segment += "=" * (-len(segment) % 4)
            claims = json.loads(base64.urlsafe_b64decode(segment.encode()).decode())
            auth = claims.get("https://api.openai.com/auth") or {}
            profile = claims.get("https://api.openai.com/profile") or {}
            account_id_value = str(auth.get("chatgpt_account_id") or claims.get("account_id") or "").strip()
            token_hash = hashlib.sha256(access_token_override.encode()).hexdigest()
            decoded_email = str(profile.get("email") or claims.get("email") or email or f"external-{token_hash[:16]}@example.com").strip().lower()
            device_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "reg153-external-at:" + account_id_value))
            account = {"email": decoded_email, "account_id": account_id_value, "access_token": access_token_override, "session_cookies": {"oai-did": device_id}, "proxy": "DIRECT"}
        except Exception:
            account = None
    if not account:
        print(json.dumps({"ok": False, "error": "ACCOUNT_NOT_FOUND"}))
        return 3
    if access_token_override:
        account = dict(account)
        account["access_token"] = access_token_override

    account_id = str(account.get("account_id") or "")
    access_token = str(account.get("access_token") or "")
    if not account_id or not access_token:
        print(json.dumps({"ok": False, "error": "ACCOUNT_SESSION_INCOMPLETE"}))
        return 4

    http = Session(impersonate="firefox144")
    http.trust_env = False
    proxy = str(sys.argv[3] if len(sys.argv) > 3 else account.get("proxy") or "").strip()
    if proxy and proxy.upper() != "DIRECT":
        http.proxies = {"http": proxy, "https": proxy}

    cookies = account.get("session_cookies") or {}
    if isinstance(cookies, str):
        try:
            cookies = json.loads(cookies)
        except Exception:
            cookies = {}
    for name, value in dict(cookies).items():
        try:
            http.cookies.set(str(name), str(value), domain="chatgpt.com")
        except Exception:
            pass

    headers = {
        "Authorization": "Bearer " + access_token,
        "ChatGPT-Account-Id": account_id,
        "OAI-Device-Id": str(cookies.get("oai-did") or ""),
        "User-Agent": UA,
        "Accept": "application/json",
        "Origin": ACCOUNT_API_BASE,
        "Referer": ACCOUNT_API_BASE + "/",
        "Content-Type": "application/json",
    }

    try:
        response = http.post(
            ACCOUNT_API_BASE + "/backend-api/payments/payment_method/default",
            json={"account_id": account_id, "payment_method_id": payment_method_id},
            headers=headers,
            timeout=45,
        )
        if response.status_code not in (200, 204):
            print(json.dumps({"ok": False, "error": "SET_DEFAULT_FAILED", "status": response.status_code}))
            return 5

        # The default endpoint also attaches the newly confirmed SetupIntent PM
        # to the account's billing customer. Wait for that attachment to become
        # visible before creating Hosted Checkout, otherwise the new checkout can
        # race ahead and omit the saved card.
        for attempt in range(1, 13):
            if payment_method_id in list_payment_method_ids(http, headers, account_id):
                print(json.dumps({"ok": True, "attached": True, "attempt": attempt}))
                return 0
            time.sleep(1.0)

        print(json.dumps({"ok": False, "error": "PAYMENT_METHOD_NOT_ATTACHED"}))
        return 6
    finally:
        http.close()


if __name__ == "__main__":
    raise SystemExit(main())
