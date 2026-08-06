from __future__ import annotations

import asyncio
import json
import os
import hmac
import hashlib
import sys
import time
import uuid
from typing import Any

import ph_checkout_protocol as protocol
from remote_browser import playwright_proxy


WORKER_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Stripe worker</title>
<script src="https://js.stripe.com/v3/"></script>
<style>html,body{margin:0;padding:16px;font-family:Arial}#payment-element{min-height:180px}</style>
</head><body><div id="payment-element"></div></body></html>"""


def checkout_payload(snapshot: dict[str, Any]) -> tuple[dict[str, Any], str, str, str]:
    short_url = str(snapshot.get("short_url") or "").strip()
    parts = short_url.split("/")
    if len(parts) < 6:
        raise ValueError("Checkout URL is invalid")
    processor = str(parts[4])
    session_id = str(snapshot.get("checkout_session_id") or parts[5])
    payload = {
        "short_url": short_url,
        "processor_entity": processor,
        "checkout_session_id": session_id,
        "access_token": snapshot.get("access_token"),
        "chatgpt_account_id": snapshot.get("chatgpt_account_id"),
        "session_cookies": snapshot.get("session_cookies") or {},
        "user_agent": snapshot.get("user_agent"),
        "checkout_device_id": snapshot.get("checkout_device_id"),
        "checkout_chatgpt_session_id": snapshot.get("checkout_chatgpt_session_id"),
        "email": snapshot.get("email"),
        "proxy": snapshot.get("checkout_proxy") or snapshot.get("proxy"),
        "offer_proxy": (
            snapshot.get("checkout_proxy")
            if str(snapshot.get("proxy") or "").strip().upper() in {"", "DIRECT"}
            else snapshot.get("proxy")
        ) or snapshot.get("checkout_proxy"),
    }
    proxy = protocol.normalize_proxy(str(payload.get("proxy") or ""))
    if not proxy:
        raise ValueError("Checkout proxy is missing")
    return payload, proxy, processor, session_id


def resolve_context(snapshot: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    payload, proxy, processor, session_id = checkout_payload(snapshot)
    cookies = dict(payload.get("session_cookies") or {})
    device_id = str(payload.get("checkout_device_id") or cookies.get("oai-did") or uuid.uuid4())
    http = protocol.build_identity_http(proxy, str(payload.get("user_agent") or ""))
    protocol.install_cookies(http, cookies, device_id)
    state = protocol.resolve_oaics_checkout(http, payload, processor, session_id, device_id)
    initial_amount, initial_currency = protocol.oaics_total(state)
    billing = protocol.default_billing("PH", str(payload.get("email") or ""), real_random=True)
    tax_state = protocol.submit_checkout_taxes(
        http,
        payload,
        processor,
        session_id,
        device_id,
        billing,
        initial_currency,
    )
    if tax_state:
        state = {**state, **tax_state}
    protocol.snapshot_billing(http, payload, processor, session_id, device_id, billing)
    state = protocol.resolve_oaics_checkout(http, payload, processor, session_id, device_id)

    offer_check_ok = False
    offer_eligible = False
    offer_campaign_id = ""
    offer_label = ""
    try:
        offer_headers = {
            "Authorization": f"Bearer {str(payload.get('access_token') or '')}",
            "Accept": "application/json",
            "Origin": "https://chatgpt.com",
            "Referer": "<REPLACE_ME>",
            "User-Agent": str(payload.get("user_agent") or ""),
            "OAI-Device-Id": device_id,
        }
        account_id = str(payload.get("chatgpt_account_id") or "")
        if account_id:
            offer_headers["ChatGPT-Account-ID"] = account_id
        offer_proxy = protocol.normalize_proxy(str(payload.get("offer_proxy") or "")) or proxy
        offer_http = protocol.build_identity_http(offer_proxy, str(payload.get("user_agent") or ""))
        protocol.install_cookies(offer_http, cookies, device_id)
        offer_response = offer_http.get(
            "<REPLACE_ME>backend-api/accounts/check/v4-2023-04-27",
            headers=offer_headers,
            timeout=40,
        )
        if getattr(offer_response, "status_code", 0) == 200:
            offer_check_ok = True
            offer_payload = offer_response.json() or {}
            accounts = offer_payload.get("accounts") or {}
            candidates = []
            if account_id and isinstance(accounts.get(account_id), dict):
                candidates.append(accounts.get(account_id))
            if isinstance(accounts.get("default"), dict):
                candidates.append(accounts.get("default"))
            candidates.extend(value for value in accounts.values() if isinstance(value, dict))
            for account in candidates:
                plus = ((account.get("eligible_promo_campaigns") or {}).get("plus") or {})
                campaign_id = str(plus.get("id") or plus.get("campaign_id") or "").strip()
                if not campaign_id:
                    continue
                offer_eligible = True
                offer_campaign_id = campaign_id
                metadata = plus.get("metadata") or {}
                discount = (metadata.get("discount") or {}).get("percentage")
                duration = metadata.get("duration") or {}
                if discount == 100 and duration.get("num_periods") == 1 and duration.get("period") == "month":
                    offer_label = "Plus first month free"
                else:
                    offer_label = str(metadata.get("promotion_type_label") or metadata.get("title") or campaign_id)
                break
    except Exception:
        pass

    amount, currency = protocol.oaics_total(state)
    methods = [str(item).lower() for item in (state.get("payment_method_types") or [])]
    customer_session = str(state.get("customer_session_client_secret") or "")
    publishable_key = str(state.get("publishable_key") or "")
    if not publishable_key.startswith("pk_") or not customer_session.startswith("cuss_secret_"):
        raise RuntimeError("Checkout customer context is incomplete")
    context = {
        "publishable_key": publishable_key,
        "customer_session_client_secret": customer_session,
        "mode": "setup" if int(amount) == 0 else "subscription",
        "amount": int(amount),
        "currency": str(currency).lower(),
        "payment_method_types": methods or ["card"],
        "setup_future_usage": "off_session",
        "return_url": str(state.get("confirm_return_url") or payload["short_url"]),
        "can_confirm": bool((state.get("checkout_state") or {}).get("canConfirm")),
        "offer_check_ok": offer_check_ok,
        "offer_eligible": offer_eligible,
        "offer_campaign_id": offer_campaign_id,
        "offer_label": offer_label,
        "checkout_trial_flag": state.get("one_click_trial_eligible"),
        "promo_campaign_id": str((state.get("promo_campaign") or {}).get("promo_campaign_id") or ""),
    }
    return context, proxy, str(payload.get("user_agent") or ""), payload


def create_token(
    context: dict[str, Any],
    proxy: str,
    user_agent: str,
    identity: dict[str, Any],
) -> str:
    confirm_headers = asyncio.run(protocol.sentinel_headers(
        proxy,
        "checkout_session_approval",
        str(identity.get("checkout_device_id") or ""),
        str(identity.get("checkout_device_id") or ""),
    ))
    context["confirm_headers"] = confirm_headers
    context["checkout_session_id"] = str(identity.get("checkout_session_id") or "")

    from playwright.sync_api import sync_playwright

    proxy_cfg = playwright_proxy(proxy) or {}
    proxy_cfg["bypass"] = "127.0.0.1,localhost"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            executable_path="<REPLACE_ME>",
            proxy=proxy_cfg,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--window-size=1280,900",
            ],
        )
        try:
            opts: dict[str, Any] = {
                "viewport": {"width": 1280, "height": 900},
                "locale": "en-PH",
                "timezone_id": "Asia/Manila",
            }
            if user_agent:
                opts["user_agent"] = user_agent
            browser_context = browser.new_context(**opts)
            cookie_values = {
                str(name): str(value)
                for name, value in dict(identity.get("session_cookies") or {}).items()
                if str(name).strip() and str(value)
            }
            device_id = str(identity.get("checkout_device_id") or cookie_values.get("oai-did") or "")
            if device_id:
                cookie_values["oai-did"] = device_id
            if cookie_values:
                browser_context.add_cookies([
                    {
                        "name": name,
                        "value": value,
                        "domain": "chatgpt.com",
                        "path": "/",
                        "secure": True,
                        "sameSite": "Lax",
                    }
                    for name, value in cookie_values.items()
                ])
            page = browser_context.new_page()
            access_token = str(identity.get("access_token") or "")
            account_id = str(identity.get("chatgpt_account_id") or "")
            if access_token:
                def inject_auth(route, request):
                    headers = dict(request.headers)
                    headers["authorization"] = f"Bearer {access_token}"
                    if account_id:
                        headers["chatgpt-account-id"] = account_id
                    if device_id:
                        headers["oai-device-id"] = device_id
                    route.continue_(headers=headers)
                page.route("<REPLACE_ME>backend-api/**", inject_auth)
            worker_url = str(identity.get("short_url") or context.get("return_url") or "")
            page.goto(worker_url, wait_until="commit", timeout=45000)
            if "chatgpt.com/checkout/" not in str(page.url):
                # A raw AT may be fully valid for backend APIs while having no
                # ChatGPT web-session cookie. Keep the official checkout origin,
                # but serve the minimal Stripe worker document locally; backend
                # calls below still carry the supplied Bearer AT/account headers.
                page.route(
                    worker_url,
                    lambda route: route.fulfill(
                        status=200,
                        content_type="text/html; charset=utf-8",
                        body=WORKER_HTML,
                    ),
                )
                page.goto(worker_url, wait_until="domcontentloaded", timeout=45000)
            if "chatgpt.com/checkout/" not in str(page.url):
                raise RuntimeError(f"Checkout worker origin unavailable: {page.url}")
            page.wait_for_timeout(800)
            try:
                page.evaluate("window.stop()")
            except Exception:
                pass
            page.add_script_tag(url="https://js.stripe.com/v3/")
            page.wait_for_function("typeof window.Stripe === 'function'", timeout=30000)
            page.evaluate("""() => {
                document.body.innerHTML = '<div id="payment-element" style="min-height:180px;padding:16px"></div>';
            }""")
            result = page.evaluate(
                """async (cfg) => {
                    const stripe = Stripe(cfg.publishable_key);
                    const elementOptions = {
                        mode: cfg.mode || 'subscription',
                        currency: String(cfg.currency || 'php').toLowerCase(),
                        paymentMethodTypes: cfg.payment_method_types || ['card'],
                        customerSessionClientSecret: cfg.customer_session_client_secret,
                        setupFutureUsage: cfg.setup_future_usage || 'off_session',
                        appearance: {theme: 'stripe', variables: {borderRadius: '12px', fontSizeBase: '16px'}}
                    };
                    if (elementOptions.mode !== 'setup') elementOptions.amount = Number(cfg.amount || 0);
                    const elements = stripe.elements(elementOptions);
                    const payment = elements.create('payment', {layout: 'tabs'});
                    await new Promise((resolve, reject) => {
                        let settled = false;
                        const timer = setTimeout(() => {
                            if (!settled) { settled = true; reject(new Error('Payment Element ready timeout')); }
                        }, 30000);
                        payment.on('ready', () => {
                            if (!settled) { settled = true; clearTimeout(timer); resolve(); }
                        });
                        payment.mount('#payment-element');
                    });
                    await new Promise(resolve => setTimeout(resolve, 1800));
                    const submitted = await elements.submit();
                    if (submitted && submitted.error) {
                        return {ok:false,error:submitted.error.message || submitted.error.code || 'elements.submit failed'};
                    }
                    const created = await stripe.createConfirmationToken({
                        elements,
                        params: {return_url: cfg.return_url},
                        allow_redisplay: 'always'
                    });
                    if (created.error) {
                        return {ok:false,error:created.error.message || created.error.code || 'createConfirmationToken failed'};
                    }
                    const token = created.confirmationToken && created.confirmationToken.id;
                    const response = await fetch('/backend-api/payments/checkout/confirm', {
                        method: 'POST',
                        credentials: 'include',
                        headers: {'Content-Type':'application/json', ...(cfg.confirm_headers || {})},
                        body: JSON.stringify({
                            checkout_session_id: cfg.checkout_session_id,
                            confirm_token: token
                        })
                    });
                    const responseText = await response.text();
                    let confirmResult = {};
                    try { confirmResult = JSON.parse(responseText || '{}'); }
                    catch { confirmResult = {raw: responseText.slice(0, 500)}; }
                    return {
                        ok:true,
                        token,
                        confirm_http_status: response.status,
                        checkout_confirm: confirmResult
                    };
                }""",
                context,
            )
            if not isinstance(result, dict) or not result.get("ok"):
                raise RuntimeError(str((result or {}).get("error") or "Stripe.js token generation failed"))
            token = str(result.get("token") or "")
            if not token.startswith("ctoken_"):
                raise RuntimeError("Stripe.js did not return a ConfirmationToken")
            return result
        finally:
            browser.close()


def main() -> int:
    snapshot = json.loads(sys.stdin.read() or "{}")
    context, proxy, user_agent, identity = resolve_context(snapshot)
    if context.get("offer_check_ok") and not context.get("offer_eligible"):
        print(json.dumps({
            "ok": False,
            "error_code": "OFFER_NOT_ELIGIBLE",
            "error": "The account promo catalog does not contain eligible_promo_campaigns.plus",
            "offer_check_ok": True,
            "offer_eligible": False,
            "offer_campaign_id": "",
        }, ensure_ascii=False))
        return 0
    result = create_token(context, proxy, user_agent, identity)
    print(json.dumps({
        "ok": True,
        "confirmation_token": result.get("token"),
        "confirm_http_status": result.get("confirm_http_status"),
        "checkout_confirm": result.get("checkout_confirm") or {},
        "can_confirm": context.get("can_confirm"),
        "offer_check_ok": context.get("offer_check_ok"),
        "offer_eligible": context.get("offer_eligible"),
        "offer_campaign_id": context.get("offer_campaign_id"),
        "offer_label": context.get("offer_label"),
        "checkout_trial_flag": context.get("checkout_trial_flag"),
        "promo_campaign_id": context.get("promo_campaign_id"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        raise
