"""Direct-card Checkout workflow for the authenticated payment center."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import threading
import time
from typing import Any
import uuid

from integrations.account_run_guard import acquire_account_run, release_account_run
from internal.cardprotocol.protocol.checkout_context import CHECKOUT_RE, validate_checkout_result
from internal.cardprotocol.protocol.ph_shortlink_extractor import (
    Mode8Config,
    Mode8Extractor,
    default_session_factory,
    extract_publishable_key,
    mask_proxy,
    normalize_access_token,
    resolved_checkout_amount,
    resolve_proxy_placeholders,
    split_proxy_pool,
)


AMOUNT_GATES = {"strict_zero", "at_most", "at_least", "any_known"}
PHASES = [
    {"id": "input", "label": "校验参数", "detail": "解析 AT、代理池与金额门禁"},
    {"id": "checkout", "label": "创建 Checkout", "detail": "使用代理池 1 按目标地区/币种创建 Checkout"},
    {"id": "promotion", "label": "应用优惠", "detail": "使用代理池 2 更新同一 Checkout"},
    {"id": "amount", "label": "官方金额复核", "detail": "重读 Checkout 上下文；不符合门禁时完整重建"},
    {"id": "handoff", "label": "官方交接", "detail": "返回并打开官方 Checkout 页面"},
]
TASKS: dict[str, dict[str, Any]] = {}
TASKS_LOCK = threading.RLock()
MAX_TASKS = 120


def card_protocol_status() -> dict[str, Any]:
    return {
        "ok": True,
        "module": "card_protocol",
        "mode": "official_checkout_workflow",
        "country": "configurable",
        "currency": "configurable",
        "checkoutGenerationAvailable": True,
        "officialCheckoutHandoffAvailable": True,
        "existingCheckoutContextAvailable": True,
        "protocolWorkspaceAvailable": True,
        "amountGates": sorted(AMOUNT_GATES),
        "defaultAmountGate": "strict_zero",
        "maxAttempts": 50,
        "maxBatchTokens": 50,
        "maxBatchConcurrency": 10,
        "optionalInputs": [
            "checkoutCountry",
            "checkoutCurrency",
            "accountId",
            "deviceId",
            "sessionTraceId",
            "sessionCookies",
            "userAgent",
            "timeout",
            "diagnoseCoupon",
        ],
        "phases": PHASES,
        "sources": {
            "protocol": "thekfjie/zkky",
            "checkoutContext": "protocol-card-payment-sanitized-20260803-203919",
        },
    }


def _context_string(value: Any, *keys: str) -> str:
    queue: list[Any] = [value]
    wanted = {key.lower() for key in keys}
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            for key, item in current.items():
                if str(key).lower() in wanted and item not in (None, ""):
                    return str(item).strip()
                if isinstance(item, (dict, list)):
                    queue.append(item)
        elif isinstance(current, list):
            queue.extend(item for item in current if isinstance(item, (dict, list)))
    return ""


def _context_list(value: Any, *keys: str) -> list[str]:
    queue: list[Any] = [value]
    wanted = {key.lower() for key in keys}
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            for key, item in current.items():
                if str(key).lower() in wanted and isinstance(item, list):
                    return [str(entry).lower() for entry in item if str(entry).strip()][:12]
                if isinstance(item, (dict, list)):
                    queue.append(item)
        elif isinstance(current, list):
            queue.extend(item for item in current if isinstance(item, (dict, list)))
    return []


def _billing_summary(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {"ready": False, "completed": 0, "required": 6, "missing": ["name", "email", "line1", "city", "postalCode", "country"]}
    if not isinstance(value, dict):
        raise ValueError("支付资料必须是 JSON 对象")
    limits = {
        "name": 160,
        "email": 320,
        "phone": 80,
        "line1": 240,
        "line2": 240,
        "city": 120,
        "state": 120,
        "postalCode": 40,
        "country": 2,
    }
    normalized: dict[str, str] = {}
    for key, limit in limits.items():
        text = str(value.get(key) or "").strip()
        if len(text) > limit:
            raise ValueError(f"支付资料 {key} 长度不能超过 {limit}")
        normalized[key] = text
    if normalized["email"] and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized["email"]):
        raise ValueError("支付资料邮箱格式无效")
    if normalized["country"]:
        normalized["country"] = normalized["country"].upper()
        if not re.fullmatch(r"[A-Z]{2}", normalized["country"]):
            raise ValueError("支付资料国家必须是两位代码")
    required = ("name", "email", "line1", "city", "postalCode", "country")
    missing = [key for key in required if not normalized[key]]
    return {
        "ready": not missing,
        "completed": len(required) - len(missing),
        "required": len(required),
        "missing": missing,
        "country": normalized["country"],
        "hasPhone": bool(normalized["phone"]),
        "hasState": bool(normalized["state"]),
    }


def _protocol_materials(
    payload: dict[str, Any],
    *,
    amount_minor: int | None,
    checkout_url: str,
    context_return_url: str,
    payment_methods: list[str],
    publishable_key_ready: bool,
    customer_session_ready: bool,
    can_confirm: bool,
    billing: dict[str, Any],
) -> dict[str, Any]:
    raw_options = payload.get("protocolOptions") or {}
    if not isinstance(raw_options, dict):
        raise ValueError("协议选项必须是 JSON 对象")
    requested_mode = str(raw_options.get("mode") or "auto").strip().lower()
    if requested_mode not in {"auto", "setup", "subscription"}:
        raise ValueError("协议模式必须是 auto、setup 或 subscription")
    resolved_mode = requested_mode
    if requested_mode == "auto":
        resolved_mode = "setup" if amount_minor == 0 else "subscription"
    payment_method_type = str(raw_options.get("paymentMethodType") or "card").strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", payment_method_type):
        raise ValueError("支付方式格式无效")
    setup_future_usage = str(raw_options.get("setupFutureUsage") or "off_session").strip().lower()
    if setup_future_usage not in {"off_session", "on_session", "none"}:
        raise ValueError("后续使用方式不受支持")
    explicit_return_url = str(raw_options.get("returnUrl") or "").strip()
    if explicit_return_url and not re.fullmatch(r"https://[^\s]+", explicit_return_url):
        raise ValueError("返回地址必须是 HTTPS URL")
    return_url = explicit_return_url or context_return_url or checkout_url
    final_concurrency = max(1, min(10, int(raw_options.get("finalConcurrency") or 3)))
    card_retry_count = max(0, min(10, int(raw_options.get("cardRetryCount") or 2)))
    card_retry_delay = max(0, min(30, int(raw_options.get("cardRetryDelay") or 1)))

    raw_card = payload.get("cardSummary") or {}
    if not isinstance(raw_card, dict):
        raise ValueError("卡片摘要必须是 JSON 对象")
    hosted_elements = bool(raw_card.get("hostedElements"))
    brand = str(raw_card.get("brand") or "CARD").strip().upper()[:24]
    last4 = re.sub(r"\D", "", str(raw_card.get("last4") or ""))[-4:]
    pan_length = int(raw_card.get("panLength") or 0)
    expiry_month = re.sub(r"\D", "", str(raw_card.get("expiryMonth") or ""))[:2]
    expiry_year = re.sub(r"\D", "", str(raw_card.get("expiryYear") or ""))[-2:]
    cvc_length = int(raw_card.get("cvcLength") or 0)
    if hosted_elements:
        card_ready = all(bool(raw_card.get(key)) for key in ("numberComplete", "expiryComplete", "cvcComplete"))
    else:
        card_ready = (
            12 <= pan_length <= 19
            and len(last4) == 4
            and bool(re.fullmatch(r"0[1-9]|1[0-2]", expiry_month))
            and bool(re.fullmatch(r"\d{2}", expiry_year))
            and cvc_length in {3, 4}
        )
    method_supported = not payment_methods or payment_method_type in payment_methods
    missing: list[str] = []
    if not publishable_key_ready:
        missing.append("Stripe publishable key")
    if not customer_session_ready:
        missing.append("CustomerSession")
    if not method_supported:
        missing.append(f"支付方式 {payment_method_type}")
    if not card_ready:
        missing.append("卡片输入")
    if not billing.get("ready"):
        missing.append("账单资料")
    if not return_url:
        missing.append("返回路径")
    return {
        "requestedMode": requested_mode,
        "mode": resolved_mode,
        "paymentMethodType": payment_method_type,
        "paymentMethodSupported": method_supported,
        "setupFutureUsage": "" if setup_future_usage == "none" else setup_future_usage,
        "returnUrl": return_url,
        "returnUrlReady": bool(return_url),
        "canConfirm": can_confirm,
        "materialsReady": not missing,
        "missing": missing,
        "finalConcurrency": final_concurrency,
        "cardRetryCount": card_retry_count,
        "cardRetryDelay": card_retry_delay,
        "card": {
            "brand": brand,
            "last4": last4,
            "expiry": f"{expiry_month}/{expiry_year}" if expiry_month and expiry_year else "",
            "ready": card_ready,
            "hostedElements": hosted_elements,
        },
        "elements": {
            "mode": resolved_mode,
            "currency": "php",
            "amountMinor": amount_minor,
            "paymentMethodTypes": [payment_method_type],
            "setupFutureUsage": "" if setup_future_usage == "none" else setup_future_usage,
        },
    }


def inspect_card_checkout_context(payload: dict[str, Any], *, include_elements: bool = False) -> dict[str, Any]:
    """Resolve an existing official Checkout for the protocol workspace.

    The response intentionally exposes readiness and public Checkout metadata,
    while keeping AT, cookies, proxy credentials and provider client secrets
    inside the authenticated service.
    """
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")
    token = normalize_access_token(str(payload.get("accessToken") or payload.get("access_token") or ""))
    if not token or token.count(".") != 2:
        raise ValueError("请输入有效的 Access Token")
    raw_url = str(payload.get("checkoutUrl") or payload.get("checkout_url") or "").strip().rstrip("/")
    match = CHECKOUT_RE.fullmatch(raw_url)
    if not match:
        raise ValueError("请输入有效的官方 ChatGPT Checkout 链接")
    proxy_protocol = _proxy_protocol(payload.get("proxyProtocol") or payload.get("proxy_protocol") or "http")
    proxy_pool = split_proxy_pool(
        _proxy_text(payload.get("proxy") or payload.get("proxyPool") or payload.get("proxy_pool")),
        default_scheme=proxy_protocol,
        force_scheme=bool(payload.get("proxyProtocol") or payload.get("proxy_protocol")),
    )
    if not proxy_pool:
        raise ValueError("协议上下文代理不能为空")
    timeout = max(20, min(180, int(payload.get("timeout") or 90)))
    account_id = _short_text(payload, "accountId", "chatgptAccountId", limit=160)
    device_id = _short_text(payload, "deviceId", "checkoutDeviceId", limit=160)
    session_trace_id = _short_text(payload, "sessionTraceId", "checkoutChatgptSessionId", limit=160)
    user_agent = _short_text(payload, "userAgent", limit=600)
    session_cookies = _session_cookies(payload.get("sessionCookies"))
    billing = _billing_summary(payload.get("billingDetails"))
    checkout_country, checkout_currency = _checkout_target(payload)
    logs: list[str] = []
    extractor = Mode8Extractor(
        Mode8Config(
            token=token,
            country=checkout_country,
            currency=checkout_currency,
            timeout=timeout,
            account_id=account_id,
            device_id=device_id,
            chatgpt_session_id=session_trace_id,
            user_agent=user_agent,
            session_cookies=session_cookies,
            diagnose_coupon=False,
            reject_nonzero=False,
        ),
        logger=lambda message: logs.append(_safe_log(message)),
    )
    session = extractor._session(proxy_pool[0])
    try:
        context = extractor._resolve_checkout_context(
            session,
            match.group("session"),
            match.group("processor"),
        )
    finally:
        try:
            session.close()
        except Exception:
            pass
    amount, amount_source, currency = resolved_checkout_amount(context)
    currency = str(currency or _context_string(context, "currency") or "").upper()
    country = _context_string(context, "country", "billing_country").upper()
    methods = _context_list(context, "payment_method_types", "paymentMethodTypes")
    customer_session = _context_string(context, "customer_session_client_secret", "customerSessionClientSecret")
    publishable_key = extract_publishable_key(context)
    return_url = _context_string(context, "confirm_return_url", "return_url", "returnUrl")
    if return_url and not return_url.startswith("https://chatgpt.com/"):
        return_url = ""
    amount_minor = int(amount) if amount_source else None
    amount_display = "未知"
    if amount_minor is not None:
        symbol = "₱" if currency == "PHP" else f"{currency} " if currency else ""
        amount_display = f"{symbol}{Decimal(amount_minor) / 100:.2f}"
    setup_future_usage = _context_string(context, "setup_future_usage", "setupFutureUsage") or "off_session"
    checkout_state = context.get("checkout_state") if isinstance(context, dict) else {}
    can_confirm = bool(checkout_state.get("canConfirm")) if isinstance(checkout_state, dict) else False
    publishable_key_ready = publishable_key.startswith(("pk_live_", "pk_test_"))
    customer_session_ready = customer_session.startswith("cuss_secret_")
    protocol = _protocol_materials(
        payload,
        amount_minor=amount_minor,
        checkout_url=raw_url,
        context_return_url=return_url,
        payment_methods=methods or ["card"],
        publishable_key_ready=publishable_key_ready,
        customer_session_ready=customer_session_ready,
        can_confirm=can_confirm,
        billing=billing,
    )
    result = {
        "ok": True,
        "status": "prepared",
        "checkoutId": match.group("session"),
        "processorEntity": match.group("processor"),
        "checkoutUrl": raw_url,
        "country": country or "未识别",
        "currency": currency or "未识别",
        "amountMinor": amount_minor,
        "amountDisplay": amount_display,
        "amountSource": amount_source,
        "paymentMethodTypes": methods or ["card"],
        "cardSupported": not methods or "card" in methods,
        "publishableKeyReady": publishable_key_ready,
        "customerSessionReady": customer_session_ready,
        "setupFutureUsage": setup_future_usage,
        "returnUrl": return_url or raw_url,
        "billing": billing,
        "protocol": protocol,
        "contextVerified": True,
        "proxy": mask_proxy(proxy_pool[0]),
        "logs": ["已读取现有 Checkout", "官方上下文复核完成", *logs[-8:]],
        "tokenHash": hashlib.sha256(token.encode("utf-8")).hexdigest()[:12],
    }
    if include_elements:
        if not publishable_key_ready:
            raise ValueError("当前 Checkout 未返回可用的 Stripe 公钥")
        result["elements"] = {
            "publishableKey": publishable_key,
            "checkoutId": match.group("session"),
            "processorEntity": match.group("processor"),
        }
    return result


def load_card_elements_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the public Stripe key needed to mount hosted card Elements."""
    return inspect_card_checkout_context(payload, include_elements=True)


def _proxy_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(item or "").strip() for item in value if str(item or "").strip())
    return str(value or "").strip()


def _proxy_protocol(value: Any) -> str:
    scheme = str(value or "http").strip().lower()
    if scheme == "socks":
        scheme = "socks5h"
    if scheme not in {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}:
        raise ValueError("代理协议不受支持")
    return scheme


def _threshold(value: Any) -> Decimal:
    try:
        threshold = Decimal(str(value if value not in (None, "") else "0"))
    except InvalidOperation as error:
        raise ValueError("金额阈值必须是有效数字") from error
    if threshold < 0 or threshold > Decimal("1000000"):
        raise ValueError("金额阈值必须在 0 到 1000000 PHP 之间")
    return threshold


def _php_amount(amount: str) -> tuple[Decimal | None, int | None]:
    if amount == "unknown" or not amount:
        return None, None
    try:
        raw = Decimal(amount)
    except InvalidOperation:
        return None, None
    if "." in amount:
        major = raw
        minor = int((major * 100).quantize(Decimal("1")))
    else:
        minor = int(raw)
        major = Decimal(minor) / 100
    return major, minor


def _amount_matches(amount: str, gate: str, threshold: Decimal, allow_unknown: bool) -> tuple[bool, str, Decimal | None, int | None]:
    if amount == "unknown" or not amount:
        return (allow_unknown, "金额未知，已按显式设置放行" if allow_unknown else "金额未知，默认拒绝", None, None)
    current, minor = _php_amount(amount)
    if current is None:
        return (allow_unknown, "金额无法解析，已按显式设置放行" if allow_unknown else "金额无法解析", None, None)
    if gate == "strict_zero":
        accepted = current == 0
        rule = "严格等于 0"
    elif gate == "at_most":
        accepted = current <= threshold
        rule = f"不高于 {threshold.normalize()} PHP"
    elif gate == "at_least":
        accepted = current >= threshold
        rule = f"不低于 {threshold.normalize()} PHP"
    else:
        accepted = True
        rule = "任意已识别金额"
    return accepted, f"金额 ₱{current:.2f}；门禁：{rule}", current, minor


def _safe_log(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._-]+", "Bearer <REDACTED>", text)
    text = re.sub(r"(?i)(https?|socks[45]h?)://[^\s/@]+:[^\s/@]+@", r"\1://***@", text)
    return text[:500]


def _proxy_country(proxy: str, timeout: int) -> str:
    session = default_session_factory("chrome146")
    try:
        runtime_proxy = resolve_proxy_placeholders(proxy)
        session.proxies = {"http": runtime_proxy, "https": runtime_proxy}
        response = session.get(
            "https://www.cloudflare.com/cdn-cgi/trace",
            timeout=timeout,
        )
        if int(getattr(response, "status_code", 0) or 0) != 200:
            return ""
        fields = dict(
            line.split("=", 1)
            for line in str(getattr(response, "text", "") or "").splitlines()
            if "=" in line
        )
        return str(fields.get("loc") or "").upper()
    except Exception:
        return ""
    finally:
        try:
            session.close()
        except Exception:
            pass


def _preflight_pool(raw: str, expected: str, timeout: int, proxy_protocol: str = "http") -> dict[str, Any]:
    proxies = split_proxy_pool(raw, default_scheme=_proxy_protocol(proxy_protocol), force_scheme=True)
    if not proxies:
        raise ValueError(f"{expected} 代理池不能为空")
    results: list[dict[str, Any]] = []
    workers = min(24, len(proxies))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(_proxy_country, proxy, timeout): proxy for proxy in proxies}
        for future in as_completed(futures):
            proxy = futures[future]
            try:
                country = future.result()
            except Exception:
                country = ""
            results.append({
                "proxy": proxy,
                "maskedProxy": mask_proxy(proxy),
                "country": country or "UNKNOWN",
                "reachable": bool(country),
                "regionMatched": country == expected,
                "valid": bool(country),
            })
    results.sort(key=lambda item: (not item["reachable"], not item["regionMatched"], item["maskedProxy"]))
    reachable = [item["proxy"] for item in results if item["reachable"]]
    region_matched = [item["proxy"] for item in results if item["regionMatched"]]
    return {
        "expected": expected,
        "total": len(proxies),
        "reachable": len(reachable),
        "regionMatched": len(region_matched),
        "valid": len(reachable),
        "validProxies": reachable,
        "reachableProxies": reachable,
        "regionMatchedProxies": region_matched,
        "results": results,
    }


def preflight_card_protocol_proxies(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")
    timeout = max(5, min(30, int(payload.get("timeout") or 12)))
    pool1 = _proxy_text(payload.get("proxyPool1") or payload.get("proxy_pool_1"))
    pool2 = _proxy_text(payload.get("proxyPool2") or payload.get("proxy_pool_2"))
    proxy_protocol = _proxy_protocol(payload.get("proxyProtocol") or payload.get("proxy_protocol") or "http")
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(_preflight_pool, pool1, "US", timeout, proxy_protocol)
        second = executor.submit(_preflight_pool, pool2, "TR", timeout, proxy_protocol)
        result1 = first.result()
        result2 = second.result()
    return {
        "ok": bool(result1["reachable"] and result2["reachable"]),
        "regionOk": bool(result1["regionMatched"] and result2["regionMatched"]),
        "pool1": result1,
        "pool2": result2,
        "timeout": timeout,
        "proxyProtocol": proxy_protocol,
    }


def _short_text(payload: dict[str, Any], *keys: str, limit: int = 500) -> str:
    value = ""
    for key in keys:
        if payload.get(key) not in (None, ""):
            value = str(payload.get(key) or "").strip()
            break
    if len(value) > limit:
        raise ValueError(f"{keys[0]} 长度不能超过 {limit}")
    return value


def _checkout_target(payload: dict[str, Any]) -> tuple[str, str]:
    country = str(
        payload.get("checkoutCountry")
        or payload.get("checkout_country")
        or payload.get("country")
        or "PH"
    ).strip().upper()
    currency = str(
        payload.get("checkoutCurrency")
        or payload.get("checkout_currency")
        or payload.get("currency")
        or "PHP"
    ).strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError("Checkout 地区必须是 2 位国家代码")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("Checkout 币种必须是 3 位币种代码")
    return country, currency


def _session_cookies(value: Any) -> dict[str, str]:
    if value in (None, "", {}):
        return {}
    parsed: Any = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except Exception as error:
                raise ValueError("Session Cookies JSON 无法解析") from error
        else:
            parsed = {}
            for item in text.split(";"):
                if "=" not in item:
                    continue
                name, cookie_value = item.split("=", 1)
                if name.strip():
                    parsed[name.strip()] = cookie_value.strip()
    if not isinstance(parsed, dict):
        raise ValueError("Session Cookies 必须是 JSON 对象或 Cookie 字符串")
    cookies: dict[str, str] = {}
    for name, cookie_value in list(parsed.items())[:50]:
        key = str(name or "").strip()
        val = str(cookie_value or "")
        if key and len(key) <= 128 and len(val) <= 8192:
            cookies[key] = val
    return cookies


def prepare_card_protocol(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")
    token = normalize_access_token(str(payload.get("accessToken") or payload.get("access_token") or ""))
    if not token or token.count(".") != 2:
        raise ValueError("请输入有效的 Access Token")
    pool1 = _proxy_text(payload.get("proxyPool1") or payload.get("proxy_pool_1"))
    pool2 = _proxy_text(payload.get("proxyPool2") or payload.get("proxy_pool_2"))
    proxy_protocol = _proxy_protocol(payload.get("proxyProtocol") or payload.get("proxy_protocol") or "http")
    if not split_proxy_pool(pool1, default_scheme=proxy_protocol, force_scheme=True):
        raise ValueError("代理池 1 不能为空")
    if not split_proxy_pool(pool2, default_scheme=proxy_protocol, force_scheme=True):
        raise ValueError("代理池 2 不能为空")
    checkout_country, checkout_currency = _checkout_target(payload)

    gate = str(payload.get("amountGate") or "strict_zero").strip().lower()
    if gate not in AMOUNT_GATES:
        raise ValueError("金额门禁不受支持")
    threshold = _threshold(payload.get("amountThreshold"))
    allow_unknown = bool(payload.get("allowUnknownAmount", False))
    attempts = max(1, min(50, int(payload.get("maxAttempts") or 10)))
    promo = str(payload.get("promoCampaign") or "plus-1-month-free").strip()[:120]
    timeout = max(20, min(180, int(payload.get("timeout") or 90)))
    account_id = _short_text(payload, "accountId", "chatgptAccountId", limit=160)
    device_id = _short_text(payload, "deviceId", "checkoutDeviceId", limit=160)
    session_trace_id = _short_text(payload, "sessionTraceId", "checkoutChatgptSessionId", limit=160)
    user_agent = _short_text(payload, "userAgent", limit=600)
    session_cookies = _session_cookies(payload.get("sessionCookies"))
    logs: list[str] = []
    last_reason = "等待工作流执行"

    for attempt in range(1, attempts + 1):
        logs.append(f"========== 提链尝试 {attempt}/{attempts} ==========")
        attempt_logs: list[str] = []
        try:
            raw = Mode8Extractor(
                Mode8Config(
                    token=token,
                    country=checkout_country,
                    currency=checkout_currency,
                    promo_campaign=promo,
                    require_oaics=True,
                    reject_nonzero=False,
                    timeout=timeout,
                    diagnose_coupon=bool(payload.get("diagnoseCoupon", False)),
                    account_id=account_id,
                    device_id=device_id,
                    chatgpt_session_id=session_trace_id,
                    user_agent=user_agent,
                    session_cookies=session_cookies,
                ),
                logger=lambda message: attempt_logs.append(_safe_log(message)),
            ).run(
                "\n".join(split_proxy_pool(pool1, default_scheme=proxy_protocol, force_scheme=True)),
                "\n".join(split_proxy_pool(pool2, default_scheme=proxy_protocol, force_scheme=True)),
            )
            public = validate_checkout_result(
                raw,
                expected_country=checkout_country,
                expected_currency=checkout_currency,
            )
            accepted, reason, amount_major, amount_minor = _amount_matches(public["amount"], gate, threshold, allow_unknown)
            amount_prefix = "₱" if checkout_currency == "PHP" else f"{checkout_currency} "
            public["amountDisplay"] = f"{amount_prefix}{amount_major:.2f}" if amount_major is not None else "未知"
            public["amountMinor"] = amount_minor
            logs.extend(attempt_logs[-30:])
            logs.append(reason)
            if not accepted:
                last_reason = reason
                logs.append("未满足金额门禁，丢弃本次 Checkout 并完整重跑")
                continue
            return {
                "ok": True,
                "status": "ready",
                "attempt": attempt,
                "maxAttempts": attempts,
                "amountGate": gate,
                "amountThreshold": str(threshold),
                "proxyProtocol": proxy_protocol,
                "allowUnknownAmount": allow_unknown,
                "result": public,
                "logs": logs[-120:],
                "tokenHash": hashlib.sha256(token.encode("utf-8")).hexdigest()[:12],
            }
        except Exception as error:  # upstream failures consume one full-chain attempt
            logs.extend(attempt_logs[-30:])
            last_reason = _safe_log(error)
            logs.append(f"本次完整链路失败：{last_reason}")

    raise ValueError(f"已完成 {attempts} 次完整重试，仍未满足条件：{last_reason}")


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in task.items() if key not in {"payload", "_account_run_lock"}}


def _trim_tasks() -> None:
    terminal = sorted(
        (task for task in TASKS.values() if task.get("status") in {"ready", "failed"}),
        key=lambda task: float(task.get("finishedAt") or 0),
    )
    while len(TASKS) >= MAX_TASKS and terminal:
        TASKS.pop(str(terminal.pop(0).get("id") or ""), None)


def start_card_protocol_task(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")
    task_id = f"card-{int(time.time())}-{uuid.uuid4().hex[:12]}"
    token = normalize_access_token(str(payload.get("accessToken") or payload.get("access_token") or ""))
    if not token or token.count(".") != 2:
        raise ValueError("请输入有效的 Access Token")
    account_run_lock = acquire_account_run(
        token, task_id, "Checkout 提链",
        parent_lease_id=str(payload.get("accountRunLease") or "").strip(),
    )
    with TASKS_LOCK:
        _trim_tasks()
        TASKS[task_id] = {
            "id": task_id,
            "status": "queued",
            "stage": "等待执行 Checkout 协议",
            "createdAt": time.time(),
            "payload": dict(payload),
            "_account_run_lock": account_run_lock,
        }

    def run() -> None:
        with TASKS_LOCK:
            task = TASKS.get(task_id)
            if task:
                task.update(status="running", stage="正在执行完整 Checkout 链路", startedAt=time.time())
        try:
            result = prepare_card_protocol(payload)
        except Exception as error:  # noqa: BLE001
            with TASKS_LOCK:
                task = TASKS.get(task_id)
                if task:
                    task.update(status="failed", stage="协议任务失败", error=_safe_log(error), finishedAt=time.time())
                    task.pop("payload", None)
        else:
            with TASKS_LOCK:
                task = TASKS.get(task_id)
                if task:
                    task.update(status="ready", stage="Checkout 已复核，可前往官方页面", result=result, finishedAt=time.time())
                    task.pop("payload", None)
        finally:
            with TASKS_LOCK:
                task = TASKS.get(task_id)
                handle = task.pop("_account_run_lock", None) if task else account_run_lock
            release_account_run(handle)

    threading.Thread(target=run, name=f"card-protocol-{task_id[-6:]}", daemon=True).start()
    return _public_task(TASKS[task_id])


def get_card_protocol_task(task_id: str) -> dict[str, Any] | None:
    with TASKS_LOCK:
        task = TASKS.get(str(task_id or ""))
        return _public_task(dict(task)) if task else None


def list_card_protocol_tasks() -> list[dict[str, Any]]:
    with TASKS_LOCK:
        return [
            _public_task(dict(task))
            for task in sorted(TASKS.values(), key=lambda item: float(item.get("createdAt") or 0), reverse=True)
        ][:100]


def delete_card_protocol_task(task_id: str) -> bool:
    with TASKS_LOCK:
        task = TASKS.get(str(task_id or ""))
        if not task or task.get("status") not in {"ready", "failed"}:
            return False
        TASKS.pop(str(task_id), None)
        return True
