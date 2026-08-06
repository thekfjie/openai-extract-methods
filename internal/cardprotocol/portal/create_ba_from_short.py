#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any


PAY153_ROOT = Path("/opt/payment-core")
if str(PAY153_ROOT) not in sys.path:
    sys.path.insert(0, str(PAY153_ROOT))

import stripe_checkout as sc  # noqa: E402
from provider_checkout import default_billing, stripe_to_provider  # noqa: E402


SHORT_RE = re.compile(
    r"^https://chatgpt\.com/checkout/(?P<processor>openai_ie|openai_llc)/(?P<session>(?:oaics_|cs_)[A-Za-z0-9_-]{12,})"
)


def emit(message: str, *, level: str = "info") -> None:
    print(json.dumps({"type": "log", "level": level, "message": message}, ensure_ascii=False), flush=True)


def normalize_proxy(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        return raw
    parts = raw.split(":")
    if len(parts) == 4:
        host, port, username, password = parts
        return f"http://{username}:{password}@{host}:{port}"
    if len(parts) == 2:
        return f"http://{raw}"
    return raw


def install_cookies(http: Any, cookies: dict[str, Any]) -> None:
    for name, value in dict(cookies or {}).items():
        if not str(name).strip() or not str(value):
            continue
        try:
            http.cookies.set(str(name), str(value), domain="chatgpt.com")
        except Exception:
            try:
                http.cookies.set(str(name), str(value))
            except Exception:
                pass


def run(payload: dict[str, Any]) -> dict[str, Any]:
    short_url = str(payload.get("short_url") or "").strip()
    match = SHORT_RE.match(short_url)
    if not match:
        raise ValueError("PH Checkout URL format is invalid")

    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise ValueError("ChatGPT access token is missing")

    proxy = normalize_proxy(str(payload.get("proxy") or ""))
    email = str(payload.get("email") or "").strip()
    session_id = str(payload.get("checkout_session_id") or match.group("session")).strip()
    processor = str(payload.get("processor_entity") or match.group("processor")).strip()
    device_id = str(payload.get("device_id") or uuid.uuid4())
    did = str(payload.get("did") or uuid.uuid4())

    emit("Loading the existing PH Checkout session")
    stripe_http = sc.build_http(proxy or None)
    install_cookies(stripe_http, payload.get("session_cookies") or {})
    try:
        stripe_http.cookies.set("oai-did", did, domain="chatgpt.com")
    except Exception:
        pass

    billing = default_billing("PH", email, real_random=True)
    stage1 = {
        "checkout_session_id": session_id,
        "processor_entity": processor,
    }

    def log(message: str) -> None:
        text = str(message or "").strip()
        if text:
            emit(text)

    emit("Creating the PayPal buyer authorization redirect from the same Checkout")
    result = stripe_to_provider(
        stripe_http,
        session_id,
        "paypal",
        billing=billing,
        country="PH",
        chatgpt_http=stripe_http,
        access_token=access_token,
        stage1=stage1,
        require_zero_due=True,
        log=log,
    )
    redirect = str(result.get("provider_redirect_url") or "").strip()
    if "paypal.com/agreements/approve" not in redirect or "ba_token=BA-" not in redirect:
        raise RuntimeError("PayPal BA redirect was not returned for the PH Checkout")
    emit("PayPal BA redirect created", level="success")
    return {
        "ok": True,
        "paypal_url": redirect,
        "checkout_session_id": session_id,
        "processor_entity": str(result.get("processor_entity") or processor),
        "checkout_amount": result.get("checkout_amount"),
        "checkout_currency": str(result.get("checkout_currency") or "PHP").upper(),
        "payment_method_types": result.get("payment_method_types") or [],
        "device_id": device_id,
        "did": did,
    }


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        result = run(payload)
        print(json.dumps({"type": "result", "result": result}, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"type": "error", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
