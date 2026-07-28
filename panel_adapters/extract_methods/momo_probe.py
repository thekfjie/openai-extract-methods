from __future__ import annotations
import re, uuid
from typing import Any
import requests
from .common import normalize_proxy, normalize_token, rewrite_proxy_country
CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"

def run_momo_eligibility(access_token: str, *, proxy: str = "", trial_days: int = 30, timeout: int = 20, stripe_pk: str = "") -> dict[str, Any]:
    token = normalize_token(access_token)
    if not token:
        raise ValueError("access token required")
    proxy = rewrite_proxy_country(normalize_proxy(proxy), "VN") or normalize_proxy(proxy)
    s = requests.Session(); s.trust_env = False
    device_id = str(uuid.uuid4())
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
        "oai-device-id": device_id,
        "oai-language": "vi-VN",
        "Cookie": f"oai-did={device_id}",
    })
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    body = {
        "entry_point": "all_plans_pricing_modal",
        "plan_name": "chatgptplusplan",
        "billing_details": {"country": "VN", "currency": "VND"},
        "checkout_ui_mode": "hosted",
        "trial_days": trial_days,
    }
    resp = s.post(CHECKOUT_URL, json=body, timeout=timeout)
    if resp.status_code >= 400:
        text = resp.text[:300]
        decision = "already_paid" if "already paid" in text.lower() else "checkout_failed"
        return {"ok": False, "method": "momo", "decision": decision, "detail": text}
    payload = resp.json() if resp.content else {}
    text = str(payload)
    cs = re.search(r"cs_(?:live|test)_[A-Za-z0-9]+", text)
    cs_id = cs.group(0) if cs else ""
    methods = []
    has_momo = None
    stripe_mode = None
    amount = None
    if cs_id and stripe_pk:
        stripe = requests.Session(); stripe.trust_env = False
        if proxy:
            stripe.proxies = {"http": proxy, "https": proxy}
        init = stripe.get(f"https://api.stripe.com/v1/payment_pages/{cs_id}/init", params={"key": stripe_pk}, timeout=timeout)
        if init.status_code < 400:
            init_payload = init.json() if init.content else {}
            raw_methods = init_payload.get("payment_method_types") or init_payload.get("ordered_payment_method_types") or []
            if isinstance(raw_methods, list):
                methods = [str(x).lower() for x in raw_methods]
            has_momo = "momo" in methods
            stripe_mode = init_payload.get("mode")
            total = init_payload.get("total_summary")
            if isinstance(total, dict):
                amount = total.get("due")
    decision = "ready" if has_momo else ("momo_not_enabled" if has_momo is False else "payment_methods_unknown")
    return {"ok": True, "method": "momo", "decision": decision, "billing_country": "VN", "currency": "VND", "has_momo": has_momo, "methods": methods, "stripe_mode": stripe_mode, "checkout_amount": str(amount if amount is not None else ""), "cs_present": bool(cs_id)}
