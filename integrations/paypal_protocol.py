"""Read-only inventory for the standalone PayPal protocol module."""
from __future__ import annotations

import json
import os
from pathlib import Path
import random
import re
import sys
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
import uuid


PROTOCOL_ROOT = Path(__file__).resolve().parent.parent / "internal" / "paypalprotocol" / "protocol"
REFERENCE_ROOT = PROTOCOL_ROOT / "_uk_refs"
COUNTRY_CATALOG_PATH = Path(__file__).with_name("paypal_countries.json")
RESULTS_PATH = Path(os.getenv(
    "PAYPAL_PROTOCOL_RESULTS_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "paypal-protocol" / "results.jsonl"),
))
PAYPAL_TASKS: dict[str, dict[str, Any]] = {}
PAYPAL_TASKS_LOCK = threading.RLock()
MAX_PAYPAL_TASKS = 100

UK_CAPTURE_PROFILE = {
    "id": "uk_har_v1",
    "label": "英国 HAR",
    "country": "GB",
    "locale": "en_GB",
    "currency": "GBP",
    "dialingCode": "+44",
    "addressFields": ["line1", "city", "postalCode", "country"],
    "optionalAddressFields": ["line2", "state"],
    "source": "har_split_json",
}

COUNTRY_LOCALES = {
    "AE": "ar_AE", "AR": "es_AR", "AT": "de_AT", "AU": "en_AU", "BE": "nl_BE",
    "BH": "ar_BH", "BR": "pt_BR", "CA": "en_CA", "CH": "de_CH", "CL": "es_CL",
    "CO": "es_CO", "CZ": "cs_CZ", "DE": "de_DE", "DK": "da_DK", "ES": "es_ES",
    "FI": "fi_FI", "FR": "fr_FR", "GB": "en_GB", "GR": "el_GR", "HK": "en_HK",
    "ID": "id_ID", "IE": "en_IE", "IL": "he_IL", "IN": "en_IN", "IT": "it_IT",
    "JP": "ja_JP", "KR": "ko_KR", "MX": "es_MX", "MY": "en_MY", "NL": "nl_NL",
    "NO": "no_NO", "NZ": "en_NZ", "PE": "es_PE", "PH": "en_PH", "PL": "pl_PL",
    "PT": "pt_PT", "SA": "ar_SA", "SE": "sv_SE", "SG": "en_SG", "TH": "th_TH",
    "TW": "zh_TW", "US": "en_US", "ZA": "en_ZA",
}

COUNTRY_CURRENCIES = {
    "AE": "AED", "AR": "ARS", "AT": "EUR", "AU": "AUD", "BE": "EUR",
    "BH": "BHD", "BR": "BRL", "CA": "CAD", "CH": "CHF", "CL": "CLP",
    "CO": "COP", "CZ": "CZK", "DE": "EUR", "DK": "DKK", "ES": "EUR",
    "FI": "EUR", "FR": "EUR", "GB": "GBP", "GR": "EUR", "HK": "HKD",
    "ID": "IDR", "IE": "EUR", "IL": "ILS", "IN": "INR", "IT": "EUR",
    "JP": "JPY", "KR": "KRW", "MX": "MXN", "MY": "MYR", "NL": "EUR",
    "NO": "NOK", "NZ": "NZD", "PE": "PEN", "PH": "PHP", "PL": "PLN",
    "PT": "EUR", "SA": "SAR", "SE": "SEK", "SG": "SGD", "TH": "THB",
    "TW": "TWD", "US": "USD", "ZA": "ZAR",
}

PROTOCOL_ADAPTED_COUNTRIES = {"BR", "GB"}

COUNTRY_PROTOCOL_PROFILES = {
    "TH": {
        "id": "th_manual_handoff_v1",
        "label": "泰国 BA 人工授权交接",
        "language": "th",
        "acceptLanguage": "th-TH,th;q=0.9,en;q=0.7",
        "timezoneOffsetMinutes": 420,
        "phoneCallingCode": "+66",
        "identityDocument": None,
        "addressRequirements": ["line1", "city", "postalCode", "country"],
        "authorizationPhases": [
            "打开 PayPal BA approve 页面",
            "完成 PayPal 风控或验证码",
            "使用 +66 泰国手机号完成验证",
            "确认 Billing Agreement 授权",
            "返回商户并核对 SetupIntent 状态",
        ],
        "execution": "manual_or_sandbox_only",
    },
}

OPERATION_PHASES = {
    "CookieBannerQuery": "load",
    "GriffinMetadataQuery": "load",
    "CheckoutSessionDataQuery": "load",
    "InitialDataQuery": "load",
    "DeferredFeature": "risk",
    "getOtpChallengeOperation": "verification",
    "InitiateRiskBasedTwoFactorPhoneConfirmationMutation": "verification",
    "ConfirmRiskBasedTwoFactorPhoneConfirmationMutation": "verification",
    "SignUpNewMemberMutation": "account",
    "authorize": "authorize",
}

PHASES = [
    {"id": "input", "label": "校验参数", "detail": "解析 BA Token、国家、Locale 与国际手机号"},
    {"id": "proxy", "label": "检查代理", "detail": "验证最多 500 条运行代理格式"},
    {"id": "handoff", "label": "生成授权页", "detail": "构建 PayPal 官方协议授权地址"},
    {"id": "verification", "label": "协议验证", "detail": "进入协议验证阶段"},
    {"id": "result", "label": "记录结果", "detail": "保存最近协议准备记录"},
]


def _relative_files(directory: Path, suffix: str = "") -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(
        str(path.relative_to(PROTOCOL_ROOT))
        for path in directory.rglob("*")
        if path.is_file() and (not suffix or path.suffix == suffix)
    )


def _read_capture_json(path: Path) -> tuple[Any | None, str]:
    """Read a captured artifact without ever returning captured field values."""
    try:
        return json.loads(path.read_text(encoding="utf-8")), "json"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        try:
            prefix = path.read_text(encoding="utf-8", errors="replace").lstrip()[:32].lower()
        except OSError:
            return None, "missing"
        return None, "html" if prefix.startswith("<!doctype html") or prefix.startswith("<html") else "invalid"


def _response_roots(payload: Any) -> tuple[list[str], int]:
    items = payload if isinstance(payload, list) else [payload]
    roots: set[str] = set()
    errors = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if isinstance(data, dict):
            roots.update(str(key) for key in data)
        item_errors = item.get("errors")
        if isinstance(item_errors, list):
            errors += len(item_errors)
    return sorted(roots), errors


def _capture_groups() -> list[dict[str, Any]]:
    """Build a value-free manifest from the checked-in UK HAR split files."""
    if not REFERENCE_ROOT.is_dir():
        return []

    stems = sorted({path.name.rsplit(".", 2)[0] for path in REFERENCE_ROOT.glob("*.json")})
    groups: list[dict[str, Any]] = []
    for stem in stems:
        request_path = REFERENCE_ROOT / f"{stem}.req.json"
        response_path = REFERENCE_ROOT / f"{stem}.resp.json"
        headers_path = REFERENCE_ROOT / f"{stem}.headers.json"
        request, request_format = _read_capture_json(request_path)
        response, response_format = _read_capture_json(response_path)
        headers, headers_format = _read_capture_json(headers_path)

        request_items = request if isinstance(request, list) else [request]
        operations = []
        for item in request_items:
            if not isinstance(item, dict):
                continue
            operation_name = str(item.get("operationName") or stem)
            variables = item.get("variables")
            operations.append({
                "name": operation_name,
                "phase": OPERATION_PHASES.get(operation_name, "load"),
                "variableNames": sorted(str(key) for key in variables) if isinstance(variables, dict) else [],
                "envelopeFields": sorted(str(key) for key in item if key not in {"query", "variables"}),
            })

        response_roots, response_errors = _response_roots(response)
        groups.append({
            "id": stem,
            "operations": operations,
            "operationCount": len(operations),
            "requestFormat": request_format,
            "responseFormat": response_format,
            "headerFormat": headers_format,
            "headerNames": sorted(str(key).lower() for key in headers) if isinstance(headers, dict) else [],
            "responseRoots": response_roots,
            "responseErrorCount": response_errors,
            "artifactsComplete": all(path.is_file() for path in (request_path, response_path, headers_path)),
        })
    return groups


def _capture_summary() -> dict[str, Any]:
    groups = _capture_groups()
    operations = [operation for group in groups for operation in group["operations"]]
    formats: dict[str, int] = {}
    for group in groups:
        response_format = group["responseFormat"]
        formats[response_format] = formats.get(response_format, 0) + 1
    summary = {
        "profile": UK_CAPTURE_PROFILE,
        "groupCount": len(groups),
        "operationCount": len(operations),
        "artifactCount": len(_relative_files(REFERENCE_ROOT, ".json")),
        "completeGroupCount": sum(1 for group in groups if group["artifactsComplete"]),
        "responseFormats": formats,
        "groups": groups,
    }
    summary["implementationCoverage"] = _implementation_coverage(summary)
    return summary


def _implementation_coverage(capture: dict[str, Any]) -> dict[str, Any]:
    """Compare value-free HAR operation names with the checked-in protocol source."""
    operation_names = sorted({
        str(operation.get("name") or "")
        for group in capture.get("groups") or []
        for operation in group.get("operations") or []
        if str(operation.get("name") or "")
    })
    source = ""
    for path in (PROTOCOL_ROOT / "paypal").glob("*.py"):
        try:
            source += "\n" + path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    direct = sorted(name for name in operation_names if name in source)
    equivalent_handlers = {}
    if "InitialDataQuery" in operation_names and "_extract_window_initial_data" in source:
        equivalent_handlers["InitialDataQuery"] = "window.__INITIAL_DATA__ HTML parser"
    covered = set(direct) | set(equivalent_handlers)
    return {
        "uniqueOperationCount": len(operation_names),
        "directOperationCount": len(direct),
        "equivalentHandlerCount": len(equivalent_handlers),
        "coveredOperationCount": len(covered),
        "directOperations": direct,
        "equivalentHandlers": equivalent_handlers,
        "referenceOnlyOperations": sorted(set(operation_names) - covered),
    }


def paypal_protocol_countries() -> dict[str, Any]:
    """Return the checked-in country catalog used by the interactive workbench."""
    try:
        catalog = json.loads(COUNTRY_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        catalog = {"source": "local", "countries": []}
    countries = catalog.get("countries") if isinstance(catalog, dict) else []
    if not isinstance(countries, list):
        countries = []
    normalized = []
    for item in countries:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").upper()
        if item.get("verified"):
            support_level = "real_ok"
            support_label = "真实 OK"
            support_detail = "参考站已将该国家标记为协议验证通过"
        elif item.get("schema_cached"):
            support_level = "theoretical_ok"
            support_label = "理论 OK"
            support_detail = "已有地区字段模板，但尚未标记实跑验证通过"
        else:
            support_level = "unsupported"
            support_label = "未适配"
            support_detail = "只有国家名称和国际区号，暂无地区协议模板"
        if code == "BR":
            internal_logic = "BR 专项分支（pt_BR / BRL / CPF）"
        elif code == "GB":
            internal_logic = "GB 专项分支（en_GB / GBP / CRS）"
        elif code == "TH":
            internal_logic = "TH 专项交接（th_TH / THB / +66 / 无 CPF）"
        elif item.get("schema_cached"):
            internal_logic = "通用地区模板"
        else:
            internal_logic = "无地区模板"
        normalized.append({
            **item,
            "locale": COUNTRY_LOCALES.get(code, ""),
            "currency": COUNTRY_CURRENCIES.get(code, ""),
            "support_level": support_level,
            "support_label": support_label,
            "support_detail": support_detail,
            "internal_logic": internal_logic,
            "protocol_profile": COUNTRY_PROTOCOL_PROFILES.get(code),
        })
    return {
        "source": catalog.get("source", "local"),
        "count": len(normalized),
        "realOkCount": sum(1 for item in normalized if item["support_level"] == "real_ok"),
        "theoreticalOkCount": sum(1 for item in normalized if item["support_level"] == "theoretical_ok"),
        "unsupportedCount": sum(1 for item in normalized if item["support_level"] == "unsupported"),
        "countries": normalized,
    }


def _extract_ba_token(raw: str) -> str:
    value = str(raw or "").strip()
    match = re.search(r"\bBA-[A-Za-z0-9]{8,80}\b", value, re.I)
    return match.group(0).upper() if match else ""


def _normalize_phone_for_country(raw: str, calling_code: str) -> str:
    value = re.sub(r"[\s().-]+", "", str(raw or "").strip())
    if not value:
        raise ValueError("请输入手机号")
    if value.startswith("00"):
        value = "+" + value[2:]
    if not value.startswith("+") and calling_code:
        digits = value.lstrip("0")
        dialing_digits = calling_code.lstrip("+")
        value = "+" + digits if digits.startswith(dialing_digits) else calling_code + digits
    if not re.fullmatch(r"\+[0-9]{7,15}", value):
        raise ValueError("手机号必须使用国际格式，例如 +447512345678")
    if calling_code and not value.startswith(calling_code):
        raise ValueError(f"手机号需与所选国家匹配，并以 {calling_code} 开头")
    return value


def _validate_proxy_line(value: str) -> str:
    line = str(value or "").strip()
    if not line or line.startswith("#"):
        return ""
    if "://" in line:
        parsed = urlparse(line)
        if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname or not parsed.port:
            raise ValueError("代理格式无效")
        return line
    parts = line.split(":")
    if len(parts) not in {2, 4} or not parts[0] or not parts[1].isdigit():
        raise ValueError("代理支持 host:port、host:port:user:pass 或带协议 URL")
    port = int(parts[1])
    if port < 1 or port > 65535:
        raise ValueError("代理端口超出范围")
    return line


def prepare_paypal_protocol(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a manual PayPal authorization handoff without executing it."""
    catalog = paypal_protocol_countries()["countries"]
    by_code = {str(item.get("code") or "").upper(): item for item in catalog if isinstance(item, dict)}
    country = str(payload.get("country") or "").strip().upper()
    country_item = by_code.get(country)
    if not country_item:
        raise ValueError("请选择有效的 PayPal 国家/地区")

    token = _extract_ba_token(payload.get("paypalUrl") or payload.get("baToken") or "")
    if not token:
        raise ValueError("请输入包含 BA- Token 的 PayPal 链接或直接填写 BA Token")
    calling_code = str(country_item.get("calling_code") or "")
    phone = _normalize_phone_for_country(payload.get("phone") or "", calling_code)

    raw_proxies = payload.get("proxies")
    if isinstance(raw_proxies, str):
        raw_proxies = raw_proxies.splitlines()
    if not isinstance(raw_proxies, list):
        raw_proxies = []
    proxies = []
    for index, raw_proxy in enumerate(raw_proxies[:501], start=1):
        try:
            proxy = _validate_proxy_line(raw_proxy)
        except ValueError as error:
            raise ValueError(f"第 {index} 条代理：{error}") from error
        if proxy:
            proxies.append(proxy)
    if len(proxies) > 500:
        raise ValueError("代理最多 500 条")

    locale = str(country_item.get("locale") or "")
    query_values = {"ba_token": token, "country.x": country}
    if locale:
        query_values["locale.x"] = locale
    query = urlencode(query_values)
    approval_url = f"https://www.paypal.com/agreements/approve?{query}"
    protocol_profile = COUNTRY_PROTOCOL_PROFILES.get(country)
    return {
        "ok": True,
        "mode": "manual_handoff",
        "message": "协议参数已验证",
        "approvalUrl": approval_url,
        "baToken": token,
        "country": country,
        "countryName": country_item.get("name_zh") or country_item.get("name_en") or country,
        "locale": locale,
        "currency": country_item.get("currency") or "",
        "supportLevel": country_item.get("support_level") or "unsupported",
        "supportLabel": country_item.get("support_label") or "未适配",
        "supportDetail": country_item.get("support_detail") or "暂无地区协议模板",
        "internalLogic": country_item.get("internal_logic") or "无地区模板",
        "protocolProfile": protocol_profile,
        "authorizationPlan": list((protocol_profile or {}).get("authorizationPhases") or []),
        "executionBoundary": (protocol_profile or {}).get("execution") or "manual_only",
        "phone": phone,
        "proxyCount": len(proxies),
        "proxyConfigured": bool(proxies),
        "executorStarted": False,
        "sensitiveValuesStored": False,
    }


def _mask_protocol_token(value: Any) -> str:
    token = str(value or "")
    if len(token) <= 10:
        return "<redacted>"
    return f"{token[:4]}…{token[-4:]}"


def _safe_merchant_url(value: Any) -> str:
    """Keep route and success state while removing tokens and client secrets."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    query = parse_qs(parsed.query)
    visible = []
    for key in ("status", "redirect_status", "returned_from_redirect", "ui_mode"):
        value_list = query.get(key) or []
        if value_list:
            visible.append((key, str(value_list[0])[:64]))
    safe_segments = []
    for segment in parsed.path.split("/"):
        safe_segments.append("REDACTED" if re.match(r"^(?:cs_(?:live|test)|sa_nonce_|seti_)", segment, re.I) else segment)
    return urlunparse((parsed.scheme, parsed.netloc, "/".join(safe_segments), "", urlencode(visible), ""))


def public_paypal_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return the durable/UI form of a protocol result without URL secrets."""
    identity = result.get("identity_elevation")
    if not isinstance(identity, dict):
        identity = {}
    final_url = _safe_merchant_url(result.get("final_redirect_url"))
    settlement_status = str(result.get("settlement_status") or "authorized").lower()
    return {
        "status": str(result.get("status") or "error"),
        "ba_token": _mask_protocol_token(result.get("ba_token")),
        "ec_token": _mask_protocol_token(result.get("ec_token")),
        "user_id": str(result.get("user_id") or ""),
        "return_url": "<redacted>" if result.get("return_url") else "",
        "final_redirect_url": final_url,
        "verification_url": "",
        # Keep a safe merchant URL for the intermediate state so an operator
        # can continue checkout verification.  The raw session/setup tokens
        # are removed by _safe_merchant_url().
        "pending_url": final_url if settlement_status != "confirmed" else "",
        "redirect_status": str(result.get("redirect_status") or "").lower(),
        "settlement_status": settlement_status,
        "payment_action": str(result.get("payment_action") or ""),
        "buyer_mode": str(result.get("buyer_mode") or "original"),
        "identity_elevation": {
            "buyer_ready": bool(identity.get("buyer_ready")),
            "user_id": str(identity.get("user_id") or result.get("user_id") or ""),
            "auth_refreshed": bool(identity.get("auth_refreshed")),
            "funding_selected": bool(identity.get("funding_selected")),
            "funding_available": bool(identity.get("funding_available")),
            "funding_available_count": int(identity.get("funding_available_count") or 0),
            "funding_errors": [str(item)[:80] for item in identity.get("funding_errors") or []],
            "funding_checkpoints": [str(item)[:80] for item in identity.get("funding_checkpoints") or []],
            "fatal_contingency": str(identity.get("fatal_contingency") or "")[:160],
        },
    }


def _safe_task_error(error: Any) -> str:
    value = str(error or "")
    value = re.sub(r"(?i)(access[_-]?token|client_secret|euat|authorization)[=: ]+[^\s,;]+", r"\1=<redacted>", value)
    value = re.sub(r"\b(?:BA|EC)-[A-Za-z0-9]{6,80}\b", "<redacted-token>", value)
    return value[:1000]


def _public_paypal_task(task: dict[str, Any]) -> dict[str, Any]:
    hidden = {"payload", "_otp_condition", "_otp_values"}
    return {key: value for key, value in task.items() if key not in hidden}


def _trim_paypal_tasks() -> None:
    terminal = sorted(
        (task for task in PAYPAL_TASKS.values() if task.get("status") in {"completed", "authorized", "failed"}),
        key=lambda task: float(task.get("finishedAt") or 0),
    )
    while len(PAYPAL_TASKS) >= MAX_PAYPAL_TASKS and terminal:
        PAYPAL_TASKS.pop(str(terminal.pop(0).get("id") or ""), None)


def _persist_paypal_task(task: dict[str, Any]) -> None:
    record = _public_paypal_task(task)
    try:
        RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with RESULTS_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def _load_persisted_paypal_tasks() -> None:
    if PAYPAL_TASKS or not RESULTS_PATH.is_file():
        return
    try:
        lines = RESULTS_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-MAX_PAYPAL_TASKS:]
    except OSError:
        return
    for line in lines:
        try:
            task = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(task, dict) and task.get("id") and task.get("status") in {"completed", "authorized", "failed"}:
            PAYPAL_TASKS[str(task["id"])] = task


def _protocol_imports():
    protocol_path = str(PROTOCOL_ROOT)
    if protocol_path not in sys.path:
        sys.path.insert(0, protocol_path)
    from paypal.flow import PayPalFlow
    from paypal.models import generate_address, generate_card, generate_user, normalize_locale
    from paypal.proxy import ProxyConfig, ProxyEntry

    return PayPalFlow, generate_address, generate_card, generate_user, normalize_locale, ProxyConfig, ProxyEntry


def _proxy_values(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("proxies") or []
    if isinstance(raw, str):
        raw = raw.splitlines()
    return [str(item).strip() for item in raw if str(item).strip() and not str(item).lstrip().startswith("#")]


def start_paypal_protocol_task(payload: dict[str, Any]) -> dict[str, Any]:
    prepared = prepare_paypal_protocol(payload)
    if prepared["country"] not in PROTOCOL_ADAPTED_COUNTRIES:
        raise ValueError("当前全链路执行已适配 GB 和 BR；其他地区保留参数准备模式")
    task_id = f"pp-{int(time.time())}-{uuid.uuid4().hex[:12]}"
    otp_condition = threading.Condition()
    with PAYPAL_TASKS_LOCK:
        _load_persisted_paypal_tasks()
        _trim_paypal_tasks()
        PAYPAL_TASKS[task_id] = {
            "id": task_id,
            "status": "queued",
            "phase": "input",
            "stage": "协议参数已校验，等待执行",
            "country": prepared["country"],
            "locale": prepared["locale"],
            "phone": re.sub(r".(?=.{4})", "*", prepared["phone"]),
            "proxyCount": prepared["proxyCount"],
            "createdAt": time.time(),
            "logs": ["协议参数已校验"],
            "payload": dict(payload),
            "_otp_condition": otp_condition,
            "_otp_values": [],
            "otpRequired": False,
        }

    def update_event(event: dict[str, Any]) -> None:
        with PAYPAL_TASKS_LOCK:
            task = PAYPAL_TASKS.get(task_id)
            if not task:
                return
            phase = str(event.get("phase") or task.get("phase") or "running")
            message = _safe_task_error(event.get("message") or "协议执行中")
            task.update(phase=phase, stage=message)
            task["logs"] = [*(task.get("logs") or []), message][-80:]
            if "ecTokenPresent" in event:
                task["ecTokenPresent"] = bool(event.get("ecTokenPresent"))
            if "contextTokenType" in event:
                task["contextTokenType"] = str(event.get("contextTokenType") or "missing")[:20]
            challenge = event.get("challenge")
            if isinstance(challenge, dict):
                task["challenge"] = {
                    "kind": str(challenge.get("challengeKind") or "authentication_or_risk")[:80],
                    "pageFamily": str(challenge.get("pageFamily") or "authchallengenodeweb")[:120],
                    "httpStatus": int(challenge.get("httpStatus") or 0),
                    "paypalDebugId": str(challenge.get("paypalDebugId") or "")[:120],
                    "manualActionRequired": bool(challenge.get("manualActionRequired")),
                    "smsCreated": bool(challenge.get("smsCreated", event.get("smsCreated", True))),
                    "form": {
                        "formAction": str((challenge.get("form") or {}).get("formAction") or "/auth/validatecaptcha")[:200],
                        "sessionIdPresent": bool((challenge.get("form") or {}).get("sessionId")),
                        "csrfPresent": bool((challenge.get("form") or {}).get("csrfPresent")),
                        "requestIdPresent": bool((challenge.get("form") or {}).get("requestIdPresent")),
                        "hashPresent": bool((challenge.get("form") or {}).get("hashPresent")),
                        "recaptchaSiteKey": str((challenge.get("form") or {}).get("recaptchaSiteKey") or "")[:160],
                        "captchaIframePresent": bool((challenge.get("form") or {}).get("captchaIframePresent")),
                    },
                }
            if phase == "waiting_otp":
                task.update(status="waiting_otp", otpRequired=True, otpPrompt=message)

    def read_otp(context: dict[str, Any]) -> str:
        update_event({"phase": "waiting_otp", "message": context.get("prompt") or "等待短信验证码"})
        deadline = time.monotonic() + 600
        with otp_condition:
            while True:
                with PAYPAL_TASKS_LOCK:
                    task = PAYPAL_TASKS.get(task_id)
                    values = task.get("_otp_values") if task else None
                    if values:
                        value = str(values.pop(0))
                        task.update(status="running", otpRequired=False, stage="正在校验验证码")
                        return value
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("等待短信验证码超时")
                otp_condition.wait(timeout=min(remaining, 2))

    def run() -> None:
        with PAYPAL_TASKS_LOCK:
            task = PAYPAL_TASKS.get(task_id)
            if task:
                task.update(status="running", phase="initial_load", stage="正在启动英国 PP 全链路", startedAt=time.time())
        try:
            PayPalFlow, generate_address, generate_card, generate_user, normalize_locale, ProxyConfig, ProxyEntry = _protocol_imports()
            country, locale, _lang = normalize_locale(prepared["country"], prepared["locale"] or None)
            proxy_values = _proxy_values(payload)
            proxy_config = ProxyConfig(enabled=False)
            if proxy_values:
                proxy_config = ProxyConfig(enabled=True, entry=ProxyEntry.parse(random.choice(proxy_values)))
            user = generate_user(prepared["phone"], country=country)
            address = generate_address(country=country)
            card = generate_card(proxy_url=proxy_config.url)
            flow = PayPalFlow(
                ba_token=prepared["baToken"],
                user=user,
                card=card,
                address=address,
                max_card_attempts=max(1, min(5, int(payload.get("maxCardAttempts") or 5))),
                proxy_config=proxy_config,
                country=country,
                locale=locale,
                prefer_skip_addfi=True,
                otp_provider=read_otp,
                event_callback=update_event,
            )
            result = public_paypal_result(flow.run())
            if result.get("status") != "success":
                raise RuntimeError(result.get("error") or "协议授权未成功")
        except Exception as error:
            with PAYPAL_TASKS_LOCK:
                task = PAYPAL_TASKS.get(task_id)
                if task:
                    failed_phase = str(task.get("phase") or "running")
                    challenge = getattr(error, "page_family", "")
                    if challenge and "challenge" not in task:
                        task["challenge"] = {
                            "kind": str(getattr(error, "challenge_kind", "authentication_or_risk"))[:80],
                            "pageFamily": str(challenge)[:120],
                            "httpStatus": int(getattr(error, "status", 0) or 0),
                            "paypalDebugId": str(getattr(error, "paypal_debug_id", ""))[:120],
                            "manualActionRequired": True,
                            "smsCreated": False,
                            "form": {
                                "formAction": str(getattr(error, "challenge_form", {}).get("formAction") or "/auth/validatecaptcha")[:200],
                                "sessionIdPresent": bool(getattr(error, "challenge_form", {}).get("sessionId")),
                                "csrfPresent": bool(getattr(error, "challenge_form", {}).get("csrfPresent")),
                                "requestIdPresent": bool(getattr(error, "challenge_form", {}).get("requestIdPresent")),
                                "hashPresent": bool(getattr(error, "challenge_form", {}).get("hashPresent")),
                                "recaptchaSiteKey": str(getattr(error, "challenge_form", {}).get("recaptchaSiteKey") or "")[:160],
                                "captchaIframePresent": bool(getattr(error, "challenge_form", {}).get("captchaIframePresent")),
                            },
                        }
                    task.update(
                        status="failed", phase="failed",
                        stage=(
                            "PayPal 身份/风控检查阻止了短信发送"
                            if task.get("challenge") and task.get("challenge", {}).get("smsCreated") is False
                            else "PayPal 返回身份/风控挑战"
                            if task.get("challenge") else "协议任务失败"
                        ),
                        failedPhase=failed_phase, otpRequired=False,
                        error=_safe_task_error(error), finishedAt=time.time(),
                    )
                    task.pop("payload", None)
                    _persist_paypal_task(task)
        else:
            confirmed = result.get("settlement_status") == "confirmed"
            pending_verification = result.get("settlement_status") == "pending_verification"
            with PAYPAL_TASKS_LOCK:
                task = PAYPAL_TASKS.get(task_id)
                if task:
                    task.update(
                        status="completed" if confirmed else "authorized",
                        phase="completed" if confirmed else "authorized",
                        stage=(
                            "支付已确认"
                            if confirmed
                            else "PayPal 协议已授权，等待商户/Stripe checkout verification"
                            if pending_verification
                            else "协议授权成功，等待商户确认"
                        ),
                        otpRequired=False, result=result, finishedAt=time.time(),
                    )
                    task.pop("payload", None)
                    _persist_paypal_task(task)

    threading.Thread(target=run, name=f"paypal-protocol-{task_id[-6:]}", daemon=True).start()
    with PAYPAL_TASKS_LOCK:
        return _public_paypal_task(dict(PAYPAL_TASKS[task_id]))


def submit_paypal_protocol_otp(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    value = str(payload.get("code") or payload.get("phone") or "").strip()
    if not (re.fullmatch(r"[0-9]{6}", value) or re.fullmatch(r"(?:phone:)?\+?[0-9 ()-]{8,24}", value, re.I)):
        raise ValueError("请输入6位验证码或新的国际手机号")
    with PAYPAL_TASKS_LOCK:
        task = PAYPAL_TASKS.get(str(task_id or ""))
        if not task:
            raise KeyError("PP 协议任务不存在")
        if task.get("status") != "waiting_otp":
            raise ValueError("当前任务不在等待验证码")
        condition = task["_otp_condition"]
        task["_otp_values"].append(value)
        task.update(status="running", otpRequired=False, stage="已提交验证码，正在校验")
    with condition:
        condition.notify_all()
    return {"ok": True, "id": task_id, "status": "running"}


def get_paypal_protocol_task(task_id: str) -> dict[str, Any] | None:
    with PAYPAL_TASKS_LOCK:
        _load_persisted_paypal_tasks()
        task = PAYPAL_TASKS.get(str(task_id or ""))
        return _public_paypal_task(dict(task)) if task else None


def list_paypal_protocol_tasks() -> list[dict[str, Any]]:
    with PAYPAL_TASKS_LOCK:
        _load_persisted_paypal_tasks()
        return [
            _public_paypal_task(dict(task))
            for task in sorted(PAYPAL_TASKS.values(), key=lambda item: float(item.get("createdAt") or 0), reverse=True)
        ][:100]


def delete_paypal_protocol_task(task_id: str) -> bool:
    with PAYPAL_TASKS_LOCK:
        _load_persisted_paypal_tasks()
        task = PAYPAL_TASKS.get(str(task_id or ""))
        if not task or task.get("status") not in {"completed", "authorized", "failed"}:
            return False
        PAYPAL_TASKS.pop(str(task_id), None)
        return True


def paypal_protocol_status() -> dict[str, Any]:
    required = ["main.py", "config.py", "paypal/flow.py", "paypal/graphql.py", "paypal/models.py"]
    missing = [name for name in required if not (PROTOCOL_ROOT / name).is_file()]
    country_catalog = paypal_protocol_countries()
    return {
        "ok": not missing,
        "module": "paypal_protocol",
        "mode": "interactive",
        "executorAvailable": not missing,
        "preparationAvailable": True,
        "executionMode": "full_chain",
        "supportedScope": "general",
        "defaultCountry": "GB",
        "defaultLocale": "en_GB",
        "executorCountries": sorted(PROTOCOL_ADAPTED_COUNTRIES),
        "presetCountries": [item["code"] for item in country_catalog["countries"]],
        "countryCount": country_catalog["count"],
        "realOkCountryCount": country_catalog["realOkCount"],
        "theoreticalOkCountryCount": country_catalog["theoreticalOkCount"],
        "unsupportedCountryCount": country_catalog["unsupportedCount"],
        "phases": PHASES,
        "missing": missing,
    }


def paypal_protocol_materials() -> dict[str, Any]:
    capture = _capture_summary()
    return {
        "sourcePath": "internal/paypalprotocol/protocol",
        "sourceFiles": _relative_files(PROTOCOL_ROOT / "paypal", ".py"),
        "referenceFiles": _relative_files(REFERENCE_ROOT, ".json"),
        "capture": capture,
    }
