from __future__ import annotations

import json
import sys
import uuid

import ph_checkout_protocol as protocol


def main() -> int:
    snapshot = json.loads(sys.stdin.read() or "{}")
    short_url = str(snapshot.get("short_url") or "")
    parts = short_url.split("/")
    if len(parts) < 6:
        raise ValueError("Checkout URL is invalid")

    payload = {
        "short_url": short_url,
        "processor_entity": parts[4],
        "checkout_session_id": str(snapshot.get("checkout_session_id") or parts[5]),
        "access_token": snapshot.get("access_token"),
        "chatgpt_account_id": snapshot.get("chatgpt_account_id"),
        "session_cookies": snapshot.get("session_cookies") or {},
        "user_agent": snapshot.get("user_agent"),
        "checkout_device_id": snapshot.get("checkout_device_id"),
        "checkout_chatgpt_session_id": snapshot.get("checkout_chatgpt_session_id"),
        "email": snapshot.get("email"),
        "proxy": snapshot.get("checkout_proxy") or snapshot.get("proxy"),
    }
    proxy = protocol.normalize_proxy(str(payload.get("proxy") or ""))
    if not proxy:
        raise ValueError("Checkout proxy is missing")
    cookies = dict(payload.get("session_cookies") or {})
    device_id = str(payload.get("checkout_device_id") or cookies.get("oai-did") or uuid.uuid4())
    http = protocol.build_identity_http(proxy, str(payload.get("user_agent") or ""))
    protocol.install_cookies(http, cookies, device_id)
    state = protocol.resolve_oaics_checkout(
        http,
        payload,
        str(payload["processor_entity"]),
        str(payload["checkout_session_id"]),
        device_id,
    )
    amount, currency = protocol.oaics_total(state)
    methods = [str(item).lower() for item in (state.get("payment_method_types") or [])]
    customer_session = str(state.get("customer_session_client_secret") or "")
    publishable_key = str(state.get("publishable_key") or "")
    if not publishable_key.startswith("pk_") or not customer_session.startswith("cuss_secret_"):
        raise RuntimeError("Checkout customer context is incomplete")
    result = {
        "ok": True,
        "task_id": str(snapshot.get("task_id") or ""),
        "publishable_key": publishable_key,
        "customer_session_client_secret": customer_session,
        "mode": "subscription",
        "amount": int(amount),
        "currency": str(currency).lower(),
        "payment_method_types": methods or ["card"],
        "setup_future_usage": "off_session",
        "return_url": str(state.get("confirm_return_url") or short_url),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
