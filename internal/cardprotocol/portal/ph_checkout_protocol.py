#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import random
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any


PAY153_ROOT = Path("/opt/payment-core")
if str(PAY153_ROOT) not in sys.path:
    sys.path.insert(0, str(PAY153_ROOT))

import stripe_checkout as sc  # noqa: E402
from app import sentinel_headers  # noqa: E402
from provider_checkout import _runtime_meta, default_billing  # noqa: E402
from ph_short_extractor import (  # noqa: E402
    DEFAULT_CLIENT_BUILD,
    DEFAULT_CLIENT_VERSION,
    DEFAULT_USER_AGENT,
)


SHORT_RE = re.compile(
    r"^https://chatgpt\.com/checkout/(?P<processor>openai_ie|openai_llc)/(?P<session>(?:oaics_|cs_)[A-Za-z0-9_-]{12,})"
)


def event(kind: str, **payload: Any) -> None:
    print(json.dumps({"type": kind, **payload}, ensure_ascii=False), flush=True)


def log(message: str, level: str = "info") -> None:
    event("log", level=level, message=str(message or ""))


def normalize_proxy(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if "://" in value:
        return value
    parts = value.split(":")
    if len(parts) == 4:
        host, port, username, password = parts
        return f"http://{username}:{password}@{host}:{port}"
    if len(parts) == 2:
        return f"http://{value}"
    return value


def build_identity_http(proxy: str, user_agent: str) -> Any:
    from curl_cffi.requests import Session as CffiSession

    ua = str(user_agent or DEFAULT_USER_AGENT)
    impersonate = "firefox144" if "Firefox/144" in ua else "chrome136"
    http = CffiSession(impersonate=impersonate)
    try:
        http.trust_env = False
    except Exception:
        pass
    if proxy:
        http.proxies = {"http": proxy, "https": proxy}
    return http


def install_cookies(http: Any, cookies: dict[str, Any], did: str) -> None:
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
    try:
        http.cookies.set("oai-did", did, domain="chatgpt.com")
    except Exception:
        pass


def card_parts(raw: dict[str, Any]) -> dict[str, str]:
    number = re.sub(r"\D", "", str(raw.get("number") or ""))
    month = re.sub(r"\D", "", str(raw.get("exp_month") or raw.get("month") or ""))
    year = re.sub(r"\D", "", str(raw.get("exp_year") or raw.get("year") or ""))
    cvc = re.sub(r"\D", "", str(raw.get("cvc") or raw.get("cvv") or ""))
    if len(year) == 2:
        year = "20" + year
    if not (12 <= len(number) <= 19 and 1 <= int(month or 0) <= 12 and len(year) == 4 and 3 <= len(cvc) <= 4):
        raise ValueError("card fields are incomplete")
    return {"number": number, "exp_month": str(int(month)), "exp_year": year, "cvc": cvc}


def chatgpt_headers(
    payload: dict[str, Any],
    device_id: str,
    processor: str,
    session_id: str,
    path: str,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {str(payload.get('access_token') or '').strip()}",
        "Accept": "*/*",
        "Origin": "https://chatgpt.com",
        "Referer": f"https://chatgpt.com/checkout/{processor}/{session_id}",
        "OAI-Device-Id": device_id,
        "OAI-Language": "en-US",
        "User-Agent": str(payload.get("user_agent") or DEFAULT_USER_AGENT),
        "Accept-Language": "en-US,en;q=0.9",
        "OAI-Client-Version": DEFAULT_CLIENT_VERSION,
        "OAI-Client-Build-Number": DEFAULT_CLIENT_BUILD,
        "x-openai-target-path": path,
        "x-openai-target-route": path,
    }
    checkout_trace_id = str(payload.get("checkout_chatgpt_session_id") or "").strip()
    if checkout_trace_id:
        headers["OAI-Session-Id"] = checkout_trace_id
    account_id = str(payload.get("chatgpt_account_id") or "").strip()
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    if extra:
        headers.update(extra)
    return headers


def response_json(response: Any, label: str) -> dict[str, Any]:
    text = str(getattr(response, "text", "") or "")
    try:
        data = response.json() or {}
    except Exception as exc:
        raise RuntimeError(f"{label} returned non-JSON HTTP {getattr(response, 'status_code', '?')}: {text[:400]}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{label} returned an invalid response")
    return data


def api_error(data: dict[str, Any]) -> str:
    error = data.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or error.get("type") or "")
    return str(error or data.get("detail") or data.get("message") or "")


def stripe_error_detail(data: dict[str, Any]) -> str:
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    intent = error.get("payment_intent") or error.get("setup_intent") or {}
    last_error = intent.get("last_payment_error") or intent.get("last_setup_error") or {}
    parts: list[str] = []
    for label, value in (
        ("message", error.get("message") or last_error.get("message")),
        ("code", error.get("code") or last_error.get("code")),
        ("decline_code", error.get("decline_code") or last_error.get("decline_code")),
        ("network_decline_code", error.get("network_decline_code") or last_error.get("network_decline_code")),
        ("advice_code", error.get("advice_code") or last_error.get("advice_code")),
    ):
        text = str(value or "").strip()
        if text and f"{label}={text}" not in parts:
            parts.append(f"{label}={text}")
    return "; ".join(parts) or api_error(data)


def resolve_oaics_checkout(
    http: Any,
    payload: dict[str, Any],
    processor: str,
    session_id: str,
    device_id: str,
) -> dict[str, Any]:
    path = f"/backend-api/payments/checkout/{processor}/{session_id}"
    response = http.get(
        f"https://chatgpt.com{path}",
        headers=chatgpt_headers(payload, device_id, processor, session_id, path),
        timeout=45,
    )
    data = response_json(response, "OAICS Checkout")
    if getattr(response, "status_code", 0) != 200:
        raise RuntimeError(f"OAICS Checkout HTTP {getattr(response, 'status_code', '?')}: {api_error(data) or str(data)[:300]}")
    resolved_id = str(data.get("checkout_session_id") or "")
    pk = str(data.get("publishable_key") or "")
    customer_secret = str(data.get("customer_session_client_secret") or "")
    if resolved_id != session_id or not pk.startswith("pk_") or not customer_secret.startswith("cuss_secret_"):
        raise RuntimeError("OAICS Checkout response is incomplete")
    methods = [str(item).lower() for item in (data.get("payment_method_types") or [])]
    if "card" not in methods:
        raise RuntimeError(f"Checkout does not expose card; methods={methods}")
    return data


def oaics_total(state: dict[str, Any]) -> tuple[int, str]:
    checkout_state = state.get("checkout_state") if isinstance(state.get("checkout_state"), dict) else {}
    total = checkout_state.get("total") if isinstance(checkout_state.get("total"), dict) else {}
    final_total = total.get("total") if isinstance(total.get("total"), dict) else {}
    amount = final_total.get("minorUnitsAmount")
    if amount is None:
        amount = 0
    return int(amount), str(checkout_state.get("currency") or "php").lower()


def fetch_deferred_elements(
    http: Any,
    state: dict[str, Any],
    session_id: str,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict[str, Any]:
    pk = str(state.get("publishable_key") or "")
    amount, currency = oaics_total(state)
    methods = [str(item).lower() for item in (state.get("payment_method_types") or [])]
    stripe_js_id = str(uuid.uuid4())
    params: dict[str, Any] = {
        "customer_session_client_secret": state.get("customer_session_client_secret"),
        "client_betas[0]": "custom_checkout_server_updates_1",
        "client_betas[1]": "custom_checkout_manual_approval_1",
        "deferred_intent[mode]": "subscription",
        "deferred_intent[amount]": str(amount),
        "deferred_intent[currency]": currency,
        "currency": currency,
        "key": pk,
        "elements_init_source": "stripe.elements",
        "referrer_host": "chatgpt.com",
        "stripe_js_id": stripe_js_id,
        "locale": "en-PH",
        "type": "deferred_intent",
    }
    # Official custom Checkout keeps mode=subscription even when amount=0.
    # The zero-due branch later becomes a SetupIntent, but Elements is still
    # initialized as a subscription with an explicit zero amount.
    params["deferred_intent[setup_future_usage]"] = "off_session"
    for index, method in enumerate(methods):
        params[f"deferred_intent[payment_method_types][{index}]"] = method
    response = http.get(
        f"{sc.STRIPE_API}/v1/elements/sessions",
        params=params,
        headers={
            "User-Agent": str(user_agent or DEFAULT_USER_AGENT),
            "Accept": "application/json",
            "Authorization": f"Bearer {pk}",
            "Origin": "https://js.stripe.com",
            "Referer": "https://js.stripe.com/",
        },
        timeout=45,
    )
    data = response_json(response, "Stripe Elements session")
    if getattr(response, "status_code", 0) != 200:
        raise RuntimeError(f"Stripe Elements session HTTP {getattr(response, 'status_code', '?')}: {api_error(data) or str(data)[:300]}")
    elements_session_id = str(data.get("session_id") or "")
    config_id = str(data.get("config_id") or "")
    if not elements_session_id:
        raise RuntimeError("Stripe Elements session did not return session_id")
    def find_customer_id(value: Any) -> str:
        if isinstance(value, str) and value.startswith("cus_"):
            return value
        if isinstance(value, dict):
            for child in value.values():
                found = find_customer_id(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find_customer_id(child)
                if found:
                    return found
        return ""
    customer_id = find_customer_id(data.get("customer")) or find_customer_id(data)
    customer_payload = data.get("customer") if isinstance(data.get("customer"), dict) else {}
    customer_session = customer_payload.get("customer_session") if isinstance(customer_payload.get("customer_session"), dict) else {}
    customer_session_api_key = str(customer_session.get("api_key") or "")
    customer_payment_method_ids = []
    for item in customer_payload.get("payment_methods") or []:
        if isinstance(item, dict):
            payment_method_id = str(item.get("id") or "")
            if payment_method_id.startswith("pm_") and payment_method_id not in customer_payment_method_ids:
                customer_payment_method_ids.append(payment_method_id)
    return {
        "elements_session_id": elements_session_id,
        "elements_session_config_id": config_id,
        "config_id": config_id,
        "stripe_js_id": stripe_js_id,
        "currency": currency,
        "checkout_amount": amount,
        "payment_method_types": methods,
        "runtime_version": sc.DEFAULT_STRIPE_RUNTIME_VERSION,
        "customer_session_client_secret": str(state.get("customer_session_client_secret") or ""),
        "customer_id": customer_id,
        "customer_session_api_key": customer_session_api_key,
        "customer_payment_method_ids": customer_payment_method_ids,
    }


def submit_checkout_taxes(
    http: Any,
    payload: dict[str, Any],
    processor: str,
    session_id: str,
    device_id: str,
    billing: dict[str, Any],
    currency: str,
) -> dict[str, Any]:
    address = dict(billing.get("address") or {})
    clean_address = {
        "country": str(address.get("country") or "PH").upper(),
        "line1": str(address.get("line1") or ""),
        "line2": str(address.get("line2") or ""),
        "city": str(address.get("city") or ""),
        "state": str(address.get("state") or ""),
        "postal_code": str(address.get("postal_code") or ""),
    }
    path = "/backend-api/payments/checkout/taxes"
    response = http.post(
        f"https://chatgpt.com{path}",
        json={
            "checkout_session_id": session_id,
            "checkout_email": str(billing.get("email") or ""),
            "billing_country": clean_address["country"],
            "billing_name": str(billing.get("name") or ""),
            "currency": str(currency or "PHP").upper(),
            "tax_id": None,
            "processor_entity": processor,
            "billing_address": clean_address,
        },
        headers=chatgpt_headers(
            payload, device_id, processor, session_id, path,
            {"Content-Type": "application/json"},
        ),
        timeout=50,
    )
    result = response_json(response, "OpenAI Checkout taxes")
    if getattr(response, "status_code", 0) != 200:
        raise RuntimeError(
            f"OpenAI Checkout taxes HTTP {getattr(response, 'status_code', '?')}: "
            f"{api_error(result) or str(result)[:350]}"
        )
    checkout = result.get("checkout_session")
    return checkout if isinstance(checkout, dict) else {}


def snapshot_billing(
    http: Any,
    payload: dict[str, Any],
    processor: str,
    session_id: str,
    device_id: str,
    billing: dict[str, Any],
) -> None:
    address = billing.get("address") or {}
    body = {
        "snapshot": {
            "billing_address": {
                "name": billing.get("name", ""),
                "address": {
                    "line1": address.get("line1", ""),
                    "line2": address.get("line2", ""),
                    "city": address.get("city", ""),
                    "country": address.get("country", "PH"),
                    "postal_code": address.get("postal_code", ""),
                    "state": address.get("state", ""),
                },
            }
        }
    }
    path = "/backend-api/payments/checkout/snapshot"
    try:
        response = http.post(
            f"https://chatgpt.com{path}",
            json=body,
            headers=chatgpt_headers(
                payload, device_id, processor, session_id, path, {"Content-Type": "application/json"}
            ),
            timeout=30,
        )
        log(f"OAICS billing snapshot HTTP {getattr(response, 'status_code', '?')}")
    except Exception as exc:
        log(f"OAICS billing snapshot skipped: {type(exc).__name__}", "warn")


def create_card_payment_method(
    http: Any,
    pk: str,
    session_id: str,
    ctx: dict[str, Any],
    billing: dict[str, Any],
    card: dict[str, str],
) -> str:
    common, attr = _runtime_meta(ctx, session_id)
    address = billing.get("address") or {}
    data = {
        "type": "card",
        "card[number]": card["number"],
        "card[cvc]": card["cvc"],
        "card[exp_month]": card["exp_month"],
        "card[exp_year]": card["exp_year"],
        "billing_details[name]": billing.get("name", ""),
        "billing_details[email]": billing.get("email", ""),
        "billing_details[address][country]": address.get("country", "PH"),
        "billing_details[address][line1]": address.get("line1", ""),
        "billing_details[address][line2]": address.get("line2", ""),
        "billing_details[address][city]": address.get("city", ""),
        "billing_details[address][postal_code]": address.get("postal_code", ""),
        "billing_details[address][state]": address.get("state", ""),
        "payment_user_agent": (
            f"stripe.js/{common['runtime_version']}; stripe-js-v3/{common['runtime_version']}; "
            "payment-element; deferred-intent"
        ),
        "referrer": "https://chatgpt.com",
        "time_on_page": str(random.randint(18000, 52000)),
        "guid": common["guid"],
        "muid": common["muid"],
        "sid": common["sid"],
        "key": pk,
        "_stripe_version": sc.STRIPE_VERSION_FULL,
        "client_attribution_metadata[client_session_id]": attr["client_session_id"],
        "client_attribution_metadata[checkout_session_id]": attr["checkout_session_id"],
        "client_attribution_metadata[checkout_config_id]": attr["checkout_config_id"],
        "client_attribution_metadata[elements_session_id]": attr["elements_session_id"],
        "client_attribution_metadata[elements_session_config_id]": attr["elements_session_config_id"],
        "client_attribution_metadata[merchant_integration_source]": "elements",
        "client_attribution_metadata[merchant_integration_subtype]": "payment-element",
        "client_attribution_metadata[merchant_integration_version]": "2021",
        "client_attribution_metadata[payment_intent_creation_flow]": "deferred",
        "client_attribution_metadata[payment_method_selection_flow]": "automatic",
    }
    data = {key: value for key, value in data.items() if value not in (None, "")}
    response = http.post(
        f"{sc.STRIPE_API}/v1/payment_methods",
        data=data,
        headers=sc._stripe_headers(),
        timeout=40,
    )
    result = response_json(response, "Card PaymentMethod")
    if getattr(response, "status_code", 0) != 200:
        raise RuntimeError(f"Card PaymentMethod HTTP {getattr(response, 'status_code', '?')}: {api_error(result) or str(result)[:300]}")
    payment_method_id = str(result.get("id") or "")
    if not payment_method_id.startswith("pm_"):
        raise RuntimeError("Card PaymentMethod did not return pm_id")
    return payment_method_id


def create_confirmation_token(
    http: Any,
    pk: str,
    payment_method_id: str,
    return_url: str,
    *,
    setup_only: bool = False,
    customer_session_client_secret: str = "",
    elements_context: dict[str, Any] | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
) -> str:
    data = {
        "payment_method": payment_method_id,
        "key": pk,
    }
    # Both the OpenAI-created PaymentIntent and the ConfirmationToken must
    # declare identical future usage. For paid Checkout this only saves the
    # method after a successful charge; it does not pre-attach the card.
    data["setup_future_usage"] = "off_session"
    if return_url:
        data["return_url"] = return_url
    context = dict(elements_context or {})
    currency = str(context.get("currency") or "php").lower()
    methods = [str(item).lower() for item in (context.get("payment_method_types") or ["card"])]
    data["client_context[currency]"] = currency
    data["client_context[mode]"] = "subscription"
    for index, method in enumerate(methods):
        data[f"client_context[payment_method_types][{index}]"] = method
    customer_id = str(context.get("customer_id") or "")
    if customer_id.startswith("cus_"):
        data["client_context[customer]"] = customer_id
    data.update({
        "client_attribution_metadata[client_session_id]": str(context.get("stripe_js_id") or uuid.uuid4()),
        "client_attribution_metadata[merchant_integration_source]": "elements",
        "client_attribution_metadata[merchant_integration_subtype]": "payment-element",
        "client_attribution_metadata[merchant_integration_version]": "2021",
        "client_attribution_metadata[payment_intent_creation_flow]": "deferred",
        "client_attribution_metadata[payment_method_selection_flow]": "merchant_specified",
        "client_attribution_metadata[elements_session_id]": str(context.get("elements_session_id") or ""),
        "client_attribution_metadata[elements_session_config_id]": str(context.get("elements_session_config_id") or ""),
        "client_attribution_metadata[merchant_integration_additional_elements][0]": "payment",
        "set_as_default_payment_method": "false",
    })
    data = {key: value for key, value in data.items() if value not in (None, "")}
    customer_api_key = str(context.get("customer_session_api_key") or "")
    authorization_key = customer_api_key if customer_api_key.startswith("ek_") else pk
    response = http.post(
        f"{sc.STRIPE_API}/v1/confirmation_tokens",
        data=data,
        headers={
            "User-Agent": str(user_agent or DEFAULT_USER_AGENT),
            "Accept": "application/json",
            "Authorization": f"Bearer {authorization_key}",
            "Origin": "https://js.stripe.com",
            "Referer": "https://js.stripe.com/",
            "Stripe-Version": "2020-08-27",
        },
        timeout=40,
    )
    result = response_json(response, "Stripe ConfirmationToken")
    if getattr(response, "status_code", 0) != 200:
        raise RuntimeError(f"Stripe ConfirmationToken HTTP {getattr(response, 'status_code', '?')}: {api_error(result) or str(result)[:300]}")
    token_id = str(result.get("id") or "")
    if not token_id.startswith("ctoken_"):
        raise RuntimeError("Stripe ConfirmationToken did not return ctoken_id")
    return token_id


def confirm_openai_checkout(
    http: Any,
    payload: dict[str, Any],
    processor: str,
    session_id: str,
    device_id: str,
    did: str,
    proxy: str,
    confirmation_token: str,
) -> dict[str, Any]:
    path = "/backend-api/payments/checkout/confirm"
    sentinel = asyncio.run(sentinel_headers(proxy, "checkout_session_approval", device_id, did))
    response = http.post(
        f"https://chatgpt.com{path}",
        json={
            "checkout_session_id": session_id,
            "confirm_token": confirmation_token,
            "selected_payment_method_type": "card",
        },
        headers=chatgpt_headers(
            payload,
            device_id,
            processor,
            session_id,
            path,
            {"Content-Type": "application/json", **sentinel},
        ),
        timeout=60,
    )
    result = response_json(response, "OpenAI Checkout confirm")
    if getattr(response, "status_code", 0) != 200:
        raise RuntimeError(f"OpenAI Checkout confirm HTTP {getattr(response, 'status_code', '?')}: {api_error(result) or str(result)[:350]}")
    if api_error(result):
        raise RuntimeError(f"OpenAI Checkout confirm: {api_error(result)}")
    intent_type = str(result.get("type") or "")
    client_secret = str(result.get("client_secret") or "")
    if intent_type not in {"setup_intent", "payment_intent"} or "_secret_" not in client_secret:
        status = str(result.get("status") or "unknown").lower()
        log(f"OpenAI Checkout confirm returned status={status} without client_secret", "warn")
    return result


def approve_oaics_checkout(
    http: Any,
    payload: dict[str, Any],
    processor: str,
    session_id: str,
    device_id: str,
    did: str,
    proxy: str,
) -> dict[str, Any]:
    path = "/backend-api/payments/checkout/approve"
    sentinel = asyncio.run(sentinel_headers(proxy, "checkout_session_approval", device_id, did))
    response = http.post(
        f"https://chatgpt.com{path}",
        json={"checkout_session_id": session_id, "processor_entity": processor},
        headers=chatgpt_headers(
            payload,
            device_id,
            processor,
            session_id,
            path,
            {"Content-Type": "application/json", **sentinel},
        ),
        timeout=50,
    )
    result = response_json(response, "OpenAI Checkout approval")
    if getattr(response, "status_code", 0) != 200:
        raise RuntimeError(f"OpenAI Checkout approval HTTP {getattr(response, 'status_code', '?')}: {api_error(result) or str(result)[:300]}")
    approval = str(result.get("result") or "").lower()
    if approval and approval != "approved":
        raise RuntimeError(f"OpenAI Checkout approval result={approval}")
    return result


def intent_parts(intent_type: str, client_secret: str) -> tuple[str, str]:
    expected = "seti_" if intent_type == "setup_intent" else "pi_"
    intent_id = client_secret.split("_secret_", 1)[0]
    if not intent_id.startswith(expected):
        raise RuntimeError("Stripe intent client secret does not match the returned type")
    plural = "setup_intents" if intent_type == "setup_intent" else "payment_intents"
    return intent_id, plural


def confirm_stripe_intent(
    http: Any,
    pk: str,
    intent_type: str,
    client_secret: str,
    confirmation_token: str,
    return_url: str,
) -> dict[str, Any]:
    intent_id, plural = intent_parts(intent_type, client_secret)
    data = {
        "client_secret": client_secret,
        "confirmation_token": confirmation_token,
        "use_stripe_sdk": "true",
        "key": pk,
        "_stripe_version": sc.STRIPE_VERSION_FULL,
    }
    # PH paid short links use payment-first semantics. Stripe only attaches the
    # successful PaymentMethod to the Checkout customer after the charge has
    # succeeded. A zero-due SetupIntent remains a bind-only operation.
    if intent_type == "payment_intent":
        data["setup_future_usage"] = "off_session"
    if return_url:
        data["return_url"] = return_url
    response = http.post(
        f"{sc.STRIPE_API}/v1/{plural}/{intent_id}/confirm",
        data=data,
        headers=sc._stripe_headers(),
        timeout=60,
    )
    result = response_json(response, "Stripe intent confirm")
    if getattr(response, "status_code", 0) != 200:
        raise RuntimeError(
            f"Stripe intent confirm HTTP {getattr(response, 'status_code', '?')}: "
            f"{stripe_error_detail(result) or str(result)[:350]}"
        )
    return result


def retrieve_stripe_intent(http: Any, pk: str, intent_type: str, client_secret: str) -> dict[str, Any]:
    intent_id, plural = intent_parts(intent_type, client_secret)
    response = http.get(
        f"{sc.STRIPE_API}/v1/{plural}/{intent_id}",
        params={"client_secret": client_secret, "key": pk, "_stripe_version": sc.STRIPE_VERSION_FULL},
        headers=sc._stripe_headers(),
        timeout=40,
    )
    result = response_json(response, "Stripe intent retrieve")
    if getattr(response, "status_code", 0) != 200:
        raise RuntimeError(f"Stripe intent retrieve HTTP {getattr(response, 'status_code', '?')}: {api_error(result) or str(result)[:300]}")
    return result


def post_payment_binding_state(intent_type: str, intent: dict[str, Any]) -> dict[str, Any]:
    status = str(intent.get("status") or "").lower()
    payment_method = intent.get("payment_method")
    if isinstance(payment_method, dict):
        payment_method = payment_method.get("id")
    customer = intent.get("customer")
    if isinstance(customer, dict):
        customer = customer.get("id")
    if intent_type != "payment_intent":
        return {
            "payment_first": False,
            "card_binding_mode": "setup_only",
            "card_binding_status": "completed" if status == "succeeded" else status or "pending",
        }
    confirmed = bool(status == "succeeded" and str(payment_method or "").startswith("pm_") and customer)
    return {
        "payment_first": True,
        "card_binding_mode": "after_payment",
        "card_binding_status": "confirmed" if confirmed else (
            "requested_after_payment" if status == "succeeded" else "waiting_for_payment"
        ),
    }


def next_action_payload(data: dict[str, Any]) -> dict[str, Any]:
    action = data.get("next_action") if isinstance(data.get("next_action"), dict) else sc._find_next_action(data)
    if not action:
        return {}
    redirect = action.get("redirect_to_url") if isinstance(action.get("redirect_to_url"), dict) else {}
    return {
        "type": str(action.get("type") or ""),
        "redirect_url": str(redirect.get("url") or ""),
        "use_stripe_sdk": action.get("use_stripe_sdk") or {},
    }


def finalize_return(
    http: Any,
    payload: dict[str, Any],
    processor: str,
    session_id: str,
    device_id: str,
    return_url: str,
) -> int | None:
    if not return_url:
        return None
    try:
        response = http.get(
            return_url,
            headers=chatgpt_headers(payload, device_id, processor, session_id, "/checkout/verify"),
            allow_redirects=True,
            timeout=45,
        )
        return getattr(response, "status_code", None)
    except Exception as exc:
        log(f"Checkout return URL follow-up skipped: {type(exc).__name__}", "warn")
        return None


def verification_result(
    attempt: int,
    session_id: str,
    processor: str,
    pk: str,
    intent_type: str,
    client_secret: str,
    action: dict[str, Any],
    amount: int,
    currency: str,
) -> dict[str, Any]:
    return {
        "status": "verification_required",
        "attempt": attempt,
        "checkout_session_id": session_id,
        "processor_entity": processor,
        "stripe_publishable_key": pk,
        "intent_type": intent_type,
        "intent_client_secret": client_secret,
        "next_action": action,
        "checkout_amount": amount,
        "checkout_currency": currency.upper(),
    }


def attempt_payment(payload: dict[str, Any], raw_card: dict[str, Any], attempt: int) -> dict[str, Any]:
    short_url = str(payload.get("short_url") or "").strip()
    match = SHORT_RE.match(short_url)
    if not match:
        raise ValueError("PH Checkout URL format is invalid")
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise ValueError("ChatGPT access token is missing")
    payment_proxy = normalize_proxy(str(payload.get("proxy") or ""))
    processor = str(payload.get("processor_entity") or match.group("processor"))
    session_id = str(payload.get("checkout_session_id") or match.group("session"))
    email = str(payload.get("email") or "").strip()
    provided_confirmation_token = str(payload.get("confirmation_token") or "").strip()
    if provided_confirmation_token and not re.fullmatch(r"ctoken_[A-Za-z0-9]+", provided_confirmation_token):
        raise ValueError("ConfirmationToken ID is invalid")
    saved_payment_method_id = str(
        payload.get("saved_payment_method_id")
        or raw_card.get("saved_payment_method_id")
        or ""
    ).strip()
    if saved_payment_method_id and not re.fullmatch(r"pm_[A-Za-z0-9]+", saved_payment_method_id):
        raise ValueError("saved PaymentMethod ID is invalid")
    card = {} if (provided_confirmation_token or saved_payment_method_id) else card_parts(raw_card)
    session_cookies = dict(payload.get("session_cookies") or {})
    cookie_device_id = str(session_cookies.get("oai-did") or "").strip()
    device_id = str(payload.get("checkout_device_id") or cookie_device_id or "").strip()
    if bool(payload.get("preserve_checkout_identity")) and not device_id:
        raise RuntimeError("CHECKOUT_IDENTITY_MISSING: checkout_device_id")
    did = device_id
    payload = dict(payload)
    payload["checkout_device_id"] = device_id
    payload["checkout_chatgpt_session_id"] = str(payload.get("checkout_chatgpt_session_id") or "").strip()
    if bool(payload.get("preserve_checkout_identity")) and not payload["checkout_chatgpt_session_id"]:
        raise RuntimeError("CHECKOUT_IDENTITY_MISSING: checkout_chatgpt_session_id")
    payload["user_agent"] = str(payload.get("user_agent") or DEFAULT_USER_AGENT)

    chatgpt_http = build_identity_http(payment_proxy, payload["user_agent"])
    stripe_http = build_identity_http(payment_proxy, payload["user_agent"])
    install_cookies(chatgpt_http, session_cookies, device_id)
    log("Checkout identity restored: original OAI device/cookie and matching HTTP fingerprint")
    prepared_state = payload.get("prepared_checkout_state") if isinstance(payload.get("prepared_checkout_state"), dict) else {}
    if provided_confirmation_token and prepared_state:
        state = dict(prepared_state)
        pk = str(state.get("publishable_key") or "")
        amount = int(payload.get("prepared_amount") if payload.get("prepared_amount") is not None else oaics_total(state)[0])
        currency = str(payload.get("prepared_currency") or oaics_total(state)[1]).lower()
        log(f"Attempt {attempt}: reuse immutable prepared OAICS context")
    else:
        log(f"Attempt {attempt}: resolve OAICS Checkout")
        state = resolve_oaics_checkout(chatgpt_http, payload, processor, session_id, device_id)
        pk = str(state.get("publishable_key") or "")
        amount, currency = oaics_total(state)
    log(f"OAICS session resolved: amount={amount} currency={currency.upper()}")

    billing = payload.get("billing_details") if isinstance(payload.get("billing_details"), dict) else None
    if not billing:
        billing = default_billing("PH", email, real_random=True)
    else:
        billing = dict(billing)
        billing["email"] = str(billing.get("email") or email)
        billing["address"] = dict(billing.get("address") or {})
        billing["address"]["postal_code"] = str(billing["address"].get("postal_code") or billing["address"].get("postalCode") or "")
    if saved_payment_method_id or provided_confirmation_token:
        log("Prepared/saved PaymentMethod flow: preserve the original Checkout billing and CustomerSession")
    else:
        tax_state = submit_checkout_taxes(
            chatgpt_http, payload, processor, session_id, device_id, billing, currency
        )
        if tax_state:
            state = {**state, **tax_state}
            pk = str(state.get("publishable_key") or pk)
            amount, currency = oaics_total(state)
        log(f"PH billing taxes submitted: amount={amount} currency={currency.upper()}")
        snapshot_billing(chatgpt_http, payload, processor, session_id, device_id, billing)
        refreshed_state = resolve_oaics_checkout(
            chatgpt_http, payload, processor, session_id, device_id
        )
        if refreshed_state:
            state = {**state, **refreshed_state}
            pk = str(state.get("publishable_key") or pk)
            amount, currency = oaics_total(state)
        log("OAICS Checkout refreshed after billing snapshot")

    prepared_ctx = payload.get("prepared_elements_context") if isinstance(payload.get("prepared_elements_context"), dict) else {}
    if provided_confirmation_token and prepared_ctx:
        ctx = dict(prepared_ctx)
        log("Reusing the exact Elements/CustomerSession context that minted the ConfirmationToken")
    else:
        ctx = fetch_deferred_elements(stripe_http, state, session_id, payload["user_agent"])
        log("Stripe deferred Elements session initialized")
    ctx["billing"] = billing

    return_url = str(state.get("confirm_return_url") or "")
    setup_only = amount == 0
    if provided_confirmation_token:
        confirmation_token = provided_confirmation_token
        log(f"Attempt {attempt}: using prepared ConfirmationToken from the locked Elements context")
    else:
        if saved_payment_method_id:
            payment_method_id = saved_payment_method_id
            log(f"Attempt {attempt}: using the account saved PaymentMethod")
        else:
            payment_method_id = create_card_payment_method(stripe_http, pk, session_id, ctx, billing, card)
            log(f"Attempt {attempt}: card PaymentMethod created (unattached)")
        confirmation_token = create_confirmation_token(
            stripe_http, pk, payment_method_id, return_url, setup_only=setup_only,
            customer_session_client_secret=str(ctx.get("customer_session_client_secret") or ""),
            elements_context=ctx,
            user_agent=payload["user_agent"],
        )
        if setup_only:
            log("Zero-due Checkout: SetupIntent binding ConfirmationToken created")
        else:
            log("Paid Checkout: post-payment-save ConfirmationToken created")

    preconfirmed_checkout = payload.get("preconfirmed_checkout") or {}
    if isinstance(preconfirmed_checkout, dict) and preconfirmed_checkout:
        checkout_confirm = dict(preconfirmed_checkout)
        log("Using Checkout confirm result produced by the official headless page")
    else:
        checkout_confirm = confirm_openai_checkout(
            chatgpt_http, payload, processor, session_id, device_id, did, payment_proxy, confirmation_token
        )
    intent_type = str(checkout_confirm.get("type") or "")
    client_secret = str(checkout_confirm.get("client_secret") or "")
    return_url = str(checkout_confirm.get("confirm_return_url") or return_url)
    confirm_status = str(checkout_confirm.get("status") or "").lower()
    if not client_secret:
        if confirm_status in {"blocked", "error", "expired", "failed"}:
            mode = "ZERO_DUE_SETUP" if amount == 0 else "PAID_CHECKOUT"
            if confirm_status == "blocked":
                raise RuntimeError(
                    f"OPENAI_CONFIRM_BLOCKED: mode={mode}; "
                    "Checkout confirm was blocked before Stripe intent creation"
                )
            raise RuntimeError(
                f"OPENAI_CONFIRM_{confirm_status.upper()}: mode={mode}; "
                "request was blocked before Stripe intent creation; this is not an issuer card decline"
            )
        if confirm_status in {"requires_approval", "pending", "requires_confirmation", "open", "success", "succeeded"}:
            log(f"OpenAI Checkout status={confirm_status}; submit approval and retry confirm")
            approve_oaics_checkout(chatgpt_http, payload, processor, session_id, device_id, did, payment_proxy)
            checkout_confirm = confirm_openai_checkout(
                chatgpt_http, payload, processor, session_id, device_id, did, payment_proxy, confirmation_token
            )
            intent_type = str(checkout_confirm.get("type") or "")
            client_secret = str(checkout_confirm.get("client_secret") or "")
            return_url = str(checkout_confirm.get("confirm_return_url") or return_url)
            confirm_status = str(checkout_confirm.get("status") or "").lower()
        if not client_secret:
            refreshed = resolve_oaics_checkout(chatgpt_http, payload, processor, session_id, device_id)
            if str(refreshed.get("payment_status") or "").lower() == "paid":
                return_status = finalize_return(chatgpt_http, payload, processor, session_id, device_id, return_url)
                return {
                    "status": "success",
                    "attempt": attempt,
                    "checkout_session_id": session_id,
                    "processor_entity": processor,
                    "checkout_amount": amount,
                    "checkout_currency": currency.upper(),
                    "intent_type": "server_confirmed",
                    "intent_status": str(refreshed.get("payment_status") or "paid"),
                    "return_http_status": return_status,
                }
            raise RuntimeError(
                f"OpenAI Checkout confirm status={confirm_status or 'unknown'} without payment client secret"
            )
    log(f"OpenAI Checkout confirm returned {intent_type}")

    intent = confirm_stripe_intent(
        stripe_http, pk, intent_type, client_secret, confirmation_token, return_url
    )
    status = str(intent.get("status") or "")
    action = next_action_payload(intent)
    log(f"Stripe {intent_type} status={status or '-'}")
    if action:
        return verification_result(
            attempt, session_id, processor, pk, intent_type, client_secret, action, amount, currency
        )

    if bool(state.get("requires_manual_approval")) and status not in {"succeeded", "processing"}:
        log(f"Attempt {attempt}: submit ChatGPT Checkout approval")
        approve_oaics_checkout(chatgpt_http, payload, processor, session_id, device_id, did, payment_proxy)
        try:
            intent = confirm_stripe_intent(
                stripe_http, pk, intent_type, client_secret, confirmation_token, return_url
            )
        except Exception as exc:
            log(f"Second Stripe confirm returned {type(exc).__name__}; retrieving intent", "warn")
            intent = retrieve_stripe_intent(stripe_http, pk, intent_type, client_secret)
        status = str(intent.get("status") or "")
        action = next_action_payload(intent)
        log(f"Stripe {intent_type} after approval status={status or '-'}")
        if action:
            return verification_result(
                attempt, session_id, processor, pk, intent_type, client_secret, action, amount, currency
            )

    if status not in {"succeeded", "processing"}:
        decline = intent.get("last_setup_error") or intent.get("last_payment_error") or {}
        detail = api_error(intent) or str(decline.get("message") or decline.get("code") or status or "unknown")
        raise RuntimeError(f"Stripe {intent_type} did not complete: {detail}")

    if intent_type == "payment_intent" and status == "succeeded":
        log("Payment succeeded; checking post-payment card attachment")
        try:
            intent = retrieve_stripe_intent(stripe_http, pk, intent_type, client_secret)
            status = str(intent.get("status") or status)
        except Exception as exc:
            log(f"Post-payment attachment check skipped: {type(exc).__name__}", "warn")
    binding = post_payment_binding_state(intent_type, intent)
    return_status = finalize_return(chatgpt_http, payload, processor, session_id, device_id, return_url)
    if binding.get("payment_first"):
        log(f"Post-payment card binding status={binding.get('card_binding_status')}")
    return {
        "status": "success",
        "attempt": attempt,
        "checkout_session_id": session_id,
        "processor_entity": processor,
        "checkout_amount": amount,
        "checkout_currency": currency.upper(),
        "intent_type": intent_type,
        "intent_status": status,
        "return_http_status": return_status,
        **binding,
    }


def run(payload: dict[str, Any]) -> dict[str, Any]:
    if str(payload.get("mode") or "").lower() == "resume":
        session_id = str(payload.get("checkout_session_id") or "").strip()
        pk = str(payload.get("stripe_publishable_key") or "").strip()
        intent_type = str(payload.get("intent_type") or "").strip()
        client_secret = str(payload.get("intent_client_secret") or "").strip()
        if not session_id or not pk or intent_type not in {"setup_intent", "payment_intent"} or not client_secret:
            raise ValueError("resume context is incomplete")
        proxy = normalize_proxy(str(payload.get("proxy") or ""))
        http = sc.build_http(proxy or None)
        resume_cookies = dict(payload.get("session_cookies") or {})
        resume_device_id = str(payload.get("checkout_device_id") or resume_cookies.get("oai-did") or uuid.uuid4())
        install_cookies(http, resume_cookies, resume_device_id)
        log("Verification completed; retrieving the original Stripe intent")
        intent = retrieve_stripe_intent(http, pk, intent_type, client_secret)
        status = str(intent.get("status") or "")
        action = next_action_payload(intent)
        if action or status in {"requires_action", "requires_payment_method", "requires_confirmation"}:
            return {
                "status": "verification_required",
                "checkout_session_id": session_id,
                "stripe_publishable_key": pk,
                "intent_type": intent_type,
                "intent_client_secret": client_secret,
                "next_action": action,
            }
        if status not in {"succeeded", "processing"}:
            raise RuntimeError(f"Stripe intent status={status or 'unknown'}")
        binding = post_payment_binding_state(intent_type, intent)
        if binding.get("payment_first"):
            log(f"Post-payment card binding status={binding.get('card_binding_status')}")
        return {
            "status": "success",
            "checkout_session_id": session_id,
            "intent_type": intent_type,
            "intent_status": status,
            **binding,
        }

    provided_confirmation_token = str(payload.get("confirmation_token") or "").strip()
    if provided_confirmation_token and not re.fullmatch(r"ctoken_[A-Za-z0-9]+", provided_confirmation_token):
        raise ValueError("ConfirmationToken ID is invalid")
    saved_payment_method_id = str(payload.get("saved_payment_method_id") or "").strip()
    if saved_payment_method_id and not re.fullmatch(r"pm_[A-Za-z0-9]+", saved_payment_method_id):
        raise ValueError("saved PaymentMethod ID is invalid")
    cards = payload.get("cards") if isinstance(payload.get("cards"), list) else []
    if provided_confirmation_token and not cards:
        cards = [{}]
    elif saved_payment_method_id and not cards:
        cards = [{"saved_payment_method_id": saved_payment_method_id}]
    if not cards:
        raise ValueError("at least one card, saved PaymentMethod, or ConfirmationToken is required")
    raw_proxies = payload.get("proxies") if isinstance(payload.get("proxies"), list) else []
    proxies: list[str] = []
    for value in [*raw_proxies, payload.get("proxy")]:
        normalized = normalize_proxy(str(value or ""))
        if normalized and normalized not in proxies:
            proxies.append(normalized)
    if not proxies:
        raise ValueError("at least one payment proxy is required")
    try:
        card_retry_count = int(payload.get("card_retry_count", 2))
    except (TypeError, ValueError):
        card_retry_count = 2
    card_retry_count = max(0, min(10, card_retry_count))
    try:
        card_retry_delay = float(payload.get("card_retry_delay", 1))
    except (TypeError, ValueError):
        card_retry_delay = 1.0
    card_retry_delay = max(1.0, min(10.0, card_retry_delay))
    last_error = ""
    protocol_attempt = 0
    for proxy_index, proxy in enumerate(proxies, 1):
        log(f"Payment route {proxy_index}/{len(proxies)} selected")
        proxy_payload = dict(payload)
        proxy_payload["proxy"] = proxy
        switch_proxy = False
        for card_index, raw_card in enumerate(cards, 1):
            for retry_index in range(card_retry_count + 1):
                protocol_attempt += 1
                log(
                    f"Card {card_index}/{len(cards)} submission "
                    f"{retry_index + 1}/{card_retry_count + 1}"
                )
                try:
                    result = attempt_payment(proxy_payload, dict(raw_card or {}), protocol_attempt)
                    if result.get("status") in {"success", "verification_required"}:
                        result["card_index"] = card_index
                        result["card_submission"] = retry_index + 1
                        result["proxy_attempt"] = proxy_index
                        return result
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    log(
                        f"Card {card_index} submission {retry_index + 1} failed: {last_error}",
                        "warn",
                    )
                    if "OPENAI_CONFIRM_BLOCKED" in last_error:
                        # A Checkout-level block is not a proxy or issuer decline.
                        # Do not rotate routes or resubmit the same confirmation.
                        raise RuntimeError(last_error)
                    if retry_index < card_retry_count:
                        log(f"Retrying the same card in {card_retry_delay:g} second(s)")
                        time.sleep(card_retry_delay)
            if switch_proxy:
                break
        if switch_proxy:
            continue
    if last_error.startswith("RuntimeError: "):
        last_error = last_error[len("RuntimeError: "):]
    raise RuntimeError(last_error or "all card/proxy attempts failed")


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        result = run(payload)
        event("result", result=result)
        return 0
    except Exception as exc:
        event("error", error=f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
