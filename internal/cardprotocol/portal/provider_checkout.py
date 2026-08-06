"""Recovered provider helpers used by the sanitized card portal snapshot."""
from __future__ import annotations

import random
import uuid
from typing import Any, Callable

from ph_short_extractor import DEFAULT_USER_AGENT


def default_billing(country: str, email: str, real_random: bool = True) -> dict[str, Any]:
    suffix = random.randint(100, 999) if real_random else 100
    code = str(country or "PH").upper()
    return {
        "name": "Portal User",
        "email": str(email or "user@example.com"),
        "phone": "",
        "address": {
            "line1": f"{suffix} Example Street",
            "line2": "",
            "city": "Manila",
            "state": "Metro Manila",
            "postal_code": "1000",
            "country": code,
        },
    }


def _runtime_meta(context: dict[str, Any], checkout_session_id: str) -> tuple[dict[str, str], dict[str, str]]:
    common = {
        "guid": str(context.get("guid") or uuid.uuid4()),
        "muid": str(context.get("muid") or uuid.uuid4()),
        "sid": str(context.get("sid") or uuid.uuid4()),
    }
    attr = {
        "client_session_id": str(context.get("client_session_id") or uuid.uuid4()),
        "checkout_session_id": str(checkout_session_id or context.get("checkout_session_id") or ""),
        "checkout_config_id": str(context.get("checkout_config_id") or context.get("config_id") or ""),
        "elements_session_id": str(context.get("elements_session_id") or ""),
        "elements_session_config_id": str(context.get("elements_session_config_id") or ""),
    }
    return common, attr


def stripe_to_provider(
    http: Any,
    checkout_session_id: str,
    provider: str,
    *,
    billing: dict[str, Any],
    country: str,
    chatgpt_http: Any,
    access_token: str,
    stage1: dict[str, Any],
    require_zero_due: bool = True,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Create a provider handoff through the official checkout API."""
    processor = str(stage1.get("processor_entity") or "openai_ie")
    payload = {
        "checkout_session_id": checkout_session_id,
        "processor_entity": processor,
        "payment_method_type": str(provider or "paypal"),
        "billing_details": billing,
        "country": str(country or "PH").upper(),
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://chatgpt.com",
        "Referer": f"https://chatgpt.com/checkout/{processor}/{checkout_session_id}",
        "User-Agent": DEFAULT_USER_AGENT,
    }
    if log:
        log("Submitting provider handoff to the official Checkout API")
    response = chatgpt_http.post(
        "https://chatgpt.com/backend-api/payments/checkout/confirm",
        json=payload,
        headers=headers,
        timeout=60,
    )
    try:
        result = response.json() or {}
    except Exception as exc:
        raise RuntimeError("Provider handoff returned a non-JSON response") from exc
    if int(getattr(response, "status_code", 0) or 0) not in {200, 201}:
        raise RuntimeError(f"Provider handoff HTTP {getattr(response, 'status_code', '?')}: {str(result)[:300]}")
    redirect = str(
        result.get("provider_redirect_url")
        or result.get("redirect_url")
        or result.get("url")
        or ""
    )
    if not redirect:
        raise RuntimeError("Provider handoff response did not include a redirect URL")
    amount = result.get("checkout_amount")
    if require_zero_due and amount not in (None, 0, "0", "0.00"):
        raise RuntimeError(f"Checkout amount is not zero: {amount}")
    return {
        **result,
        "provider_redirect_url": redirect,
        "processor_entity": str(result.get("processor_entity") or processor),
        "checkout_currency": str(result.get("checkout_currency") or "PHP").upper(),
    }
