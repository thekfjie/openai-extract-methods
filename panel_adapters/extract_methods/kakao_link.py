from __future__ import annotations
import re, uuid
from typing import Any
import requests
from .common import amount_is_zero, normalize_proxy, normalize_token, rewrite_proxy_country
CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"
UPDATE_URL = "https://chatgpt.com/backend-api/payments/checkout/update"

def run_kakao_extract(access_token: str, *, checkout_proxy: str = "", promotion_proxy: str = "", provider_proxy: str = "", checkout_country: str = "KR", promotion_country: str = "VN", provider_country: str = "KR", stripe_pk: str = "", timeout: int = 45) -> dict[str, Any]:
    token = normalize_token(access_token)
    if not token:
        raise ValueError("access token required")
    checkout_proxy = rewrite_proxy_country(normalize_proxy(checkout_proxy), checkout_country) or normalize_proxy(checkout_proxy)
    promotion_proxy = rewrite_proxy_country(normalize_proxy(promotion_proxy) or checkout_proxy, promotion_country) or checkout_proxy
    provider_proxy = rewrite_proxy_country(normalize_proxy(provider_proxy) or checkout_proxy, provider_country) or checkout_proxy
    def build(proxy: str) -> requests.Session:
        s = requests.Session(); s.trust_env = False
        device_id = str(uuid.uuid4())
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Origin": "https://chatgpt.com",
            "Referer": "https://chatgpt.com/",
            "oai-device-id": device_id,
            "oai-language": "ko-KR",
            "Cookie": f"oai-did={device_id}",
        })
        if proxy:
            s.proxies = {"http": proxy, "https": proxy}
        return s
    s1 = build(checkout_proxy)
    body = {"entry_point": "all_plans_pricing_modal", "plan_name": "chatgptplusplan", "billing_details": {"country": checkout_country, "currency": "KRW"}, "checkout_ui_mode": "custom", "promo_campaign": {"promo_campaign_id": "plus-1-month-free", "is_coupon_from_query_param": False}}
    r1 = s1.post(CHECKOUT_URL, json=body, timeout=timeout)
    if r1.status_code >= 400:
        raise RuntimeError(f"kakao checkout failed: HTTP {r1.status_code} {r1.text[:300]}")
    payload = r1.json() if r1.content else {}
    text = str(payload)
    cs_match = re.search(r"cs_(?:live|test)_[A-Za-z0-9]+|oaics_[A-Za-z0-9]+", text)
    cs_id = cs_match.group(0) if cs_match else ""
    if not cs_id:
        raise RuntimeError("kakao checkout missing cs id")
    entity = str(payload.get("processor_entity") or "openai_ie")
    s2 = build(promotion_proxy)
    update_body = {"checkout_session_id": cs_id, "processor_entity": entity, "plan_name": "chatgptplusplan", "price_interval": "month", "seat_quantity": 1, "promo_campaign": {"promo_campaign_id": "plus-1-month-free", "is_coupon_from_query_param": False}}
    r2 = s2.post(UPDATE_URL, json=update_body, headers={"Referer": f"https://chatgpt.com/checkout/{entity}/{cs_id}", "x-openai-target-path": "/backend-api/payments/checkout/update", "x-openai-target-route": "/backend-api/payments/checkout/update"}, timeout=timeout)
    if r2.status_code >= 400:
        raise RuntimeError(f"kakao promo update failed: HTTP {r2.status_code} {r2.text[:300]}")
    methods = []
    amount = ""
    redirect = ""
    if stripe_pk and cs_id.startswith("cs_"):
        stripe = requests.Session(); stripe.trust_env = False
        if provider_proxy:
            stripe.proxies = {"http": provider_proxy, "https": provider_proxy}
        init = stripe.get(f"https://api.stripe.com/v1/payment_pages/{cs_id}/init", params={"key": stripe_pk}, timeout=timeout)
        if init.status_code < 400:
            init_payload = init.json() if init.content else {}
            raw_methods = init_payload.get("payment_method_types") or init_payload.get("ordered_payment_method_types") or []
            if isinstance(raw_methods, list):
                methods = [str(x).lower() for x in raw_methods]
            total = init_payload.get("total_summary")
            if isinstance(total, dict) and total.get("due") is not None:
                amount = str(total.get("due"))
            redirect = str(init_payload.get("stripe_hosted_url") or "")
    has_kakao = any(("kakao" in m) for m in methods)
    if methods and not has_kakao:
        raise RuntimeError(f"kakao method missing; available={','.join(methods)}")
    if amount and not amount_is_zero(amount):
        raise RuntimeError(f"kakao amount not zero: {amount} KRW")
    return {"ok": True, "method": "kakao", "cs_id": cs_id, "processor_entity": entity, "billing_country": checkout_country, "currency": "KRW", "checkout_amount": amount, "methods": methods, "has_kakao": has_kakao if methods else None, "long_url": redirect or f"https://chatgpt.com/checkout/{entity}/{cs_id}", "provider_redirect_url": redirect, "note": "full Nicepay redirect confirm uses methods/kakao/kakao_extract.py"}
