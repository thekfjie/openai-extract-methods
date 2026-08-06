"""Safe Checkout handoff validation adapted from the sanitized protocol snapshot."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit


CHECKOUT_RE = re.compile(
    r"^https://chatgpt\.com/checkout/(?P<processor>openai_ie|openai_llc)/"
    r"(?P<session>(?:oaics_|cs_)[A-Za-z0-9_-]{12,})$"
)


def validate_checkout_result(
    result: dict[str, Any],
    *,
    expected_country: str = "",
    expected_currency: str = "",
) -> dict[str, Any]:
    """Return a value-limited public Checkout context without payment secrets."""
    raw_url = str(result.get("url") or result.get("link") or "").strip().rstrip("/")
    match = CHECKOUT_RE.fullmatch(raw_url)
    if not match:
        raise ValueError("上游未返回有效的 ChatGPT Checkout 链接")
    parsed = urlsplit(raw_url)
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("Checkout 链接包含不允许的附加信息")

    checkout_id = str(result.get("checkout_id") or "").strip()
    processor = str(result.get("processor_entity") or "").strip()
    if checkout_id != match.group("session") or processor != match.group("processor"):
        raise ValueError("Checkout 链接与上游会话标识不一致")

    country = str(result.get("country") or "").upper()
    currency = str(result.get("currency") or "").upper()
    wanted_country = str(expected_country or "").upper()
    wanted_currency = str(expected_currency or "").upper()
    if not re.fullmatch(r"[A-Z]{2}", country) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError(f"Checkout 未返回有效地区/币种：{country or '?'} / {currency or '?'}")
    if wanted_country and country != wanted_country:
        raise ValueError(f"Checkout 地区不匹配：{country}，期望 {wanted_country}")
    if wanted_currency and currency != wanted_currency:
        raise ValueError(f"Checkout 币种不匹配：{currency}，期望 {wanted_currency}")
    if result.get("context_verified") is not True:
        raise ValueError("Checkout 尚未通过官方上下文复核")

    return {
        "checkoutId": checkout_id,
        "processorEntity": processor,
        "url": raw_url,
        "country": country,
        "currency": currency,
        "amount": str(result.get("amount") or "unknown"),
        "amountSource": str(result.get("amount_source") or ""),
        "promoCampaign": str(result.get("promo_campaign") or "none"),
        "stage1Proxy": str(result.get("stage1_proxy") or ""),
        "stage2Proxy": str(result.get("stage2_proxy") or ""),
        # Keep the runtime Checkout identity attached to the validated result.
        # The portal persists these fields for the later card/payment handoff.
        "checkout_device_id": str(result.get("checkout_device_id") or ""),
        "checkout_chatgpt_session_id": str(result.get("checkout_chatgpt_session_id") or ""),
        "checkout_user_agent": str(result.get("checkout_user_agent") or ""),
        "contextVerified": True,
    }
