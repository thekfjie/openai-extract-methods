from __future__ import annotations
import re, uuid
from typing import Any
import requests
from .common import amount_is_zero, normalize_proxy, normalize_token, rewrite_proxy_country

CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"
UPDATE_URL = "https://chatgpt.com/backend-api/payments/checkout/update"

def _session(token: str, proxy: str, locale: str = "en-US") -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    device_id = str(uuid.uuid4())
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
        "oai-device-id": device_id,
        "oai-language": locale,
        "Cookie": f"oai-did={device_id}",
    })
    proxy = normalize_proxy(proxy)
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s

def _extract_cs(payload: dict[str, Any]) -> tuple[str, str]:
    text = str(payload)
    oai = re.search(r"oaics_[A-Za-z0-9]+", text)
    cs = re.search(r"cs_(?:live|test)_[A-Za-z0-9]+", text)
    entity = str(payload.get("processor_entity") or payload.get("processorEntity") or "").strip()
    if not entity:
        for key in ("checkout_session", "session", "checkout", "data"):
            node = payload.get(key)
            if isinstance(node, dict) and node.get("processor_entity"):
                entity = str(node.get("processor_entity")).strip()
                break
    return (oai.group(0) if oai else (cs.group(0) if cs else "")), entity

def run_paper_card_extract(access_token: str, *, billing_country: str = "PH", currency: str = "PHP", checkout_proxy: str = "", promotion_proxy: str = "", checkout_proxy_country: str = "US", promotion_proxy_country: str = "TR", promo_campaign_id: str = "plus-1-month-free", timeout: int = 45) -> dict[str, Any]:
    token = normalize_token(access_token)
    if not token:
        raise ValueError("access token required")
    billing_country = str(billing_country or "PH").upper()
    currency = str(currency or "PHP").upper()
    checkout_proxy = normalize_proxy(checkout_proxy)
    promotion_proxy = normalize_proxy(promotion_proxy) or checkout_proxy
    if checkout_proxy and checkout_proxy_country:
        checkout_proxy = rewrite_proxy_country(checkout_proxy, checkout_proxy_country) or checkout_proxy
    if promotion_proxy and promotion_proxy_country:
        promotion_proxy = rewrite_proxy_country(promotion_proxy, promotion_proxy_country) or promotion_proxy
    s1 = _session(token, checkout_proxy)
    body = {
        "entry_point": "all_plans_pricing_modal",
        "plan_name": "chatgptplusplan",
        "billing_details": {"country": billing_country, "currency": currency},
        "checkout_ui_mode": "custom",
    }
    r1 = s1.post(CHECKOUT_URL, json=body, timeout=timeout)
    if r1.status_code >= 400:
        raise RuntimeError(f"paper-card checkout failed: HTTP {r1.status_code} {r1.text[:300]}")
    payload = r1.json() if r1.content else {}
    cs_id, entity = _extract_cs(payload if isinstance(payload, dict) else {})
    if not cs_id:
        raise RuntimeError("paper-card checkout missing oaicss/cs id")
    entity = entity or ("openai_llc" if billing_country == "US" else "openai_ie")
    s2 = _session(token, promotion_proxy)
    update_body = {
        "checkout_session_id": cs_id,
        "processor_entity": entity,
        "plan_name": "chatgptplusplan",
        "price_interval": "month",
        "seat_quantity": 1,
        "promo_campaign": {"promo_campaign_id": promo_campaign_id, "is_coupon_from_query_param": False},
    }
    r2 = s2.post(UPDATE_URL, json=update_body, headers={"Referer": f"https://chatgpt.com/checkout/{entity}/{cs_id}", "x-openai-target-path": "/backend-api/payments/checkout/update", "x-openai-target-route": "/backend-api/payments/checkout/update"}, timeout=timeout)
    if r2.status_code >= 400:
        raise RuntimeError(f"paper-card promo update failed: HTTP {r2.status_code} {r2.text[:300]}")
    update_payload = r2.json() if r2.content else {}
    amount = None
    amount_currency = currency
    if isinstance(update_payload, dict):
        total = update_payload.get("total_summary")
        if isinstance(total, dict) and total.get("due") is not None:
            amount = total.get("due")
        amount_currency = str(update_payload.get("currency") or amount_currency).upper()
    status = "verified_zero" if amount_is_zero(amount) else ("pending" if amount is None else "nonzero")
    if status == "nonzero":
        raise RuntimeError(f"paper-card amount not zero: {amount} {amount_currency}")
    return {
        "ok": True,
        "method": "paper_card",
        "cs_id": cs_id,
        "processor_entity": entity,
        "billing_country": billing_country,
        "currency": currency,
        "checkout_amount": str(amount if amount is not None else ""),
        "amount_status": status,
        "long_url": f"https://chatgpt.com/checkout/{entity}/{cs_id}",
        "checkout_proxy_country": checkout_proxy_country,
        "promotion_proxy_country": promotion_proxy_country,
    }
