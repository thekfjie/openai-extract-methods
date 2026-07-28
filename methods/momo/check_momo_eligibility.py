#!/usr/bin/env python3
"""Safely probe ChatGPT trial eligibility and Stripe MoMo availability.

The probe creates one unconfirmed Checkout Session and, when needed, reads its
Stripe init payload. It never confirms payment, creates a PaymentMethod, or
prints account credentials, email addresses, Checkout Session IDs, or proxies.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import ideal_qr_extract as core


ROOT = Path(__file__).resolve().parent
DEFAULT_PROXY_FILE = ROOT / "upi" / "proxy_seeds_vn_200_http.txt"
CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"

DECISION_TEXT = {
    "ready": "支持真正试用，且当前 Session 支持 MoMo",
    "account_trial_ineligible": "账号没有真正试用资格",
    "trial_not_applied": "30 天 trial 未被 OpenAI 后端采用",
    "momo_not_enabled": "trial 已生效，但当前 Session 未启用 MoMo",
    "already_paid": "账号已订阅，无法用此流程检测新订阅资格",
    "credential_invalid": "凭据无效或已过期",
    "credential_parse_failed": "无法从文件解析凭据",
    "checkout_failed": "Checkout 创建失败，结果不确定",
    "stripe_init_failed": "Checkout 已创建，但 Stripe init 失败",
    "payment_methods_unknown": "Stripe init 未返回明确的支付方式列表",
    "unexpected_mode": "Stripe Session 不是 subscription 模式",
    "credential_ready": "凭据格式有效，尚未联网检测",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="脱敏检测 ChatGPT 账号的真正试用资格与 Stripe MoMo 支持情况。",
    )
    parser.add_argument(
        "token_files",
        nargs="+",
        type=Path,
        metavar="TOKEN_FILE",
        help="包含 accessToken 的文本或 JSON 文件；可一次传入多个。",
    )
    parser.add_argument(
        "--proxy-file",
        type=Path,
        default=DEFAULT_PROXY_FILE,
        help=f"越南代理列表（默认：{DEFAULT_PROXY_FILE}）。",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="不使用代理文件，直接连接。",
    )
    parser.add_argument(
        "--pre-proxy",
        default="auto",
        help=(
            "上游 SOCKS/HTTP 代理；默认 auto 会在本机 127.0.0.1:7897 可用时自动使用。"
            "传 off 可禁用。"
        ),
    )
    parser.add_argument(
        "--trial-days",
        type=int,
        default=30,
        help="请求的试用天数（默认：30）。",
    )
    parser.add_argument(
        "--max-proxies",
        type=int,
        default=1,
        help="仅遇到明确 Cloudflare 拦截时切换代理的上限（默认：1，不重试）。",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="单个网络请求超时秒数（默认：20）。",
    )
    parser.add_argument(
        "--parse-only",
        action="store_true",
        help="只验证凭据格式和有效期，不发送任何网络请求。",
    )
    parser.add_argument(
        "--check-methods-anyway",
        action="store_true",
        help="即使账号试用资格为 false，也继续调用一次 Stripe init 检查支付方式。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="每个账号输出一行 JSON，便于其他程序处理。",
    )
    args = parser.parse_args()
    if args.trial_days < 1:
        parser.error("--trial-days 必须大于 0")
    if args.max_proxies < 1:
        parser.error("--max-proxies 必须大于 0")
    if args.timeout < 1:
        parser.error("--timeout 必须大于 0")
    return args


def account_label(index: int) -> str:
    if 0 <= index < 26:
        return chr(ord("A") + index)
    return f"#{index + 1}"


def jwt_expiry(access_token: str) -> tuple[bool | None, float | None]:
    parts = access_token.split(".")
    if len(parts) != 3:
        return None, None
    try:
        payload_raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        payload = json.loads(payload_raw)
        exp = payload.get("exp")
        if exp is None:
            return None, None
        ttl_minutes = round((float(exp) - time.time()) / 60, 1)
        return ttl_minutes <= 0, ttl_minutes
    except (ValueError, TypeError, json.JSONDecodeError):
        return None, None


def load_credential(path: Path) -> tuple[str, str, dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "", "", {
            "credential_valid": False,
            "decision": "credential_parse_failed",
        }
    stripped = raw.strip()
    candidates = [stripped]
    if stripped.startswith(("{", "[")) and stripped.endswith(","):
        candidates.insert(0, stripped[:-1].rstrip())

    access_token = ""
    session_token = ""
    for candidate in candidates:
        parsed_access, parsed_session = core.normalize_token(candidate)
        # normalize_token returns the entire input when malformed JSON starts
        # with a brace. Never send such a blob as a Bearer token.
        if parsed_access and not parsed_access.lstrip().startswith(("{", "[")):
            access_token, session_token = parsed_access, parsed_session
            break
    if not access_token:
        return "", "", {
            "credential_valid": False,
            "decision": "credential_parse_failed",
        }
    expired, _ttl_minutes = jwt_expiry(access_token)
    return access_token, session_token, {
        "credential_valid": expired is not True,
        "credential_expired": expired,
        "decision": "credential_invalid" if expired is True else "credential_ready",
    }


def local_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def configure_pre_proxy(value: str) -> None:
    normalized = value.strip()
    if normalized.lower() == "auto":
        normalized = "socks5h://127.0.0.1:7897" if local_port_open("127.0.0.1", 7897) else ""
    elif normalized.lower() in {"", "off", "none", "false", "0"}:
        normalized = ""
    if normalized:
        os.environ["IDEAL_PRE_PROXY"] = normalized
        os.environ["PP_PRE_PROXY"] = normalized
    else:
        os.environ.pop("IDEAL_PRE_PROXY", None)
        os.environ.pop("PP_PRE_PROXY", None)


def load_proxies(path: Path, direct: bool) -> list[str]:
    if direct:
        return [""]
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("代理文件无法读取") from exc
    proxies = [
        core.normalize_proxy_url(line.strip())
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not proxies:
        raise RuntimeError("代理文件中没有可用条目")
    return proxies


def proxy_for_vietnam(proxy: str) -> str:
    if not proxy:
        return ""
    if hasattr(core, "proxy_for_country"):
        return core.proxy_for_country(proxy, "VN")
    return proxy


def classify_checkout_error(response: Any) -> str:
    if core.is_user_already_paid_error(response.text):
        return "already_paid"
    if core.is_cloudflare_response(response):
        return "cloudflare"
    if response.status_code == 401:
        return "credential_invalid"
    if response.status_code == 429:
        return "rate_limited"
    return f"http_{response.status_code}"


def checkout_body(trial_days: int) -> dict[str, Any]:
    return {
        "entry_point": "all_plans_pricing_modal",
        "plan_name": "chatgptplusplan",
        "price_interval": "month",
        "seat_quantity": 1,
        "billing_details": {"country": "VN", "currency": "VND"},
        "checkout_ui_mode": "custom",
        "subscription_data": {"trial_period_days": trial_days},
    }


def create_checkout(
    access_token: str,
    session_token: str,
    proxies: list[str],
    start_index: int,
    max_proxies: int,
    trial_days: int,
    timeout: int,
) -> tuple[dict[str, Any] | None, str, str, str, int, int]:
    headers = {
        "Referer": "https://chatgpt.com/",
        "x-openai-target-path": "/backend-api/payments/checkout",
        "x-openai-target-route": "/backend-api/payments/checkout",
    }
    last_error = "no_attempt"
    attempts = 0
    for offset in range(min(max_proxies, len(proxies))):
        proxy_index = (start_index + offset) % len(proxies)
        proxy = proxy_for_vietnam(proxies[proxy_index])
        attempts += 1
        try:
            session = core.build_chatgpt_session(
                access_token,
                str(uuid.uuid4()),
                proxy,
                session_token,
            )
            response = session.post(
                CHECKOUT_URL,
                json=checkout_body(trial_days),
                headers=headers,
                timeout=timeout,
            )
            if response.status_code >= 400:
                last_error = classify_checkout_error(response)
                if last_error in {"already_paid", "credential_invalid"}:
                    return None, "", "", last_error, attempts, proxy_index + 1
                if last_error == "cloudflare" and offset + 1 < min(max_proxies, len(proxies)):
                    continue
                return None, "", "", last_error, attempts, proxy_index + 1
            data = response.json() or {}
            checkout_id = data.get("checkout_session_id") or data.get("session_id") or data.get("id")
            if not checkout_id or not str(checkout_id).startswith("cs_"):
                last_error = "checkout_missing_session"
                continue
            raw_key = (
                data.get("stripe_publishable_key")
                or data.get("publishable_key")
                or data.get("publishableKey")
                or data.get("stripePublishableKey")
                or data.get("key")
                or ""
            )
            key_match = re.search(r"pk_live_[A-Za-z0-9]+", str(raw_key))
            stripe_key = key_match.group(0) if key_match else core.DEFAULT_STRIPE_PK
            return data, str(checkout_id), stripe_key, proxy, attempts, proxy_index + 1
        except Exception as exc:  # A timeout might have created a Session; don't retry it.
            last_error = f"network_{type(exc).__name__}"
            return None, "", "", last_error, attempts, proxy_index + 1
    return None, "", "", last_error, attempts, start_index


def stripe_init(
    checkout_id: str,
    stripe_key: str,
    selected_proxy: str,
) -> tuple[dict[str, Any] | None, str, int]:
    try:
        return core.stripe_init(checkout_id, stripe_key, selected_proxy), "ok", 1
    except Exception as exc:  # Deliberately do not print response bodies.
        return None, f"network_or_init_{type(exc).__name__}", 1


def extract_methods(payload: dict[str, Any]) -> tuple[list[str] | None, str | None]:
    methods = payload.get("payment_method_types")
    source = "top_level"
    if not isinstance(methods, list):
        elements = payload.get("elements_options")
        methods = elements.get("payment_method_types") if isinstance(elements, dict) else None
        source = "elements_options"
    if not isinstance(methods, list):
        return None, None
    return sorted({str(method).lower() for method in methods}), source


def stripe_field(payload: dict[str, Any], key: str) -> Any:
    elements = payload.get("elements_options")
    if isinstance(elements, dict) and key in elements:
        return elements[key]
    return payload.get(key)


def amount_due(payload: dict[str, Any]) -> int | None:
    summary = payload.get("total_summary")
    if isinstance(summary, dict) and summary.get("due") is not None:
        return int(summary.get("due") or 0)
    if payload.get("amount_total") is not None:
        return int(payload.get("amount_total") or 0)
    amount = stripe_field(payload, "amount")
    if amount is not None:
        return int(amount or 0)
    invoice = payload.get("invoice")
    if isinstance(invoice, dict) and invoice.get("amount_due") is not None:
        return int(invoice.get("amount_due") or 0)
    return None


def trial_marker(payload: dict[str, Any], nested_key: str | None = None) -> tuple[bool, Any, bool]:
    candidates = [payload]
    if nested_key and isinstance(payload.get(nested_key), dict):
        candidates.append(payload[nested_key])
    trial_days = None
    trial_end = None
    for candidate in candidates:
        subscription_data = candidate.get("subscription_data")
        if isinstance(subscription_data, dict):
            trial_days = subscription_data.get("trial_period_days")
            trial_end = subscription_data.get("trial_end")
        if trial_days in (None, "", 0, "0", False):
            trial_days = candidate.get("trial_period_days")
        if trial_end in (None, "", 0, "0", False):
            trial_end = candidate.get("trial_end")
        if trial_days not in (None, "", 0, "0", False) or trial_end not in (
            None,
            "",
            0,
            "0",
            False,
        ):
            break
    try:
        has_days = int(trial_days or 0) > 0
    except (TypeError, ValueError):
        has_days = False
    has_end = trial_end not in (None, "", 0, "0", False)
    return has_days or has_end, trial_days, has_end


def has_actual_trial_in_response(payload: dict[str, Any]) -> bool:
    """Detect an applied trial without treating mere eligibility as success."""
    has_trial, _, _ = trial_marker(payload, "checkout_session")
    return has_trial


def choose_decision(
    one_click_eligible: Any,
    actual_trial: bool,
    stripe_mode: Any,
    has_momo: bool | None,
) -> str:
    if not actual_trial and one_click_eligible is False:
        return "account_trial_ineligible"
    if not actual_trial:
        return "trial_not_applied"
    if stripe_mode != "subscription":
        return "unexpected_mode"
    if has_momo is None:
        return "payment_methods_unknown"
    return "ready" if has_momo else "momo_not_enabled"


def probe_account(
    label: str,
    path: Path,
    proxies: list[str],
    start_index: int,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], int]:
    access_token, session_token, credential = load_credential(path)
    result: dict[str, Any] = {"account": label, **credential}
    if not access_token or credential.get("credential_expired") is True:
        result["conclusive"] = True
        result["supported"] = False
        return result, start_index
    if args.parse_only:
        result["conclusive"] = True
        result["supported"] = None
        return result, start_index

    data, checkout_id, stripe_key, proxy, attempts, next_index = create_checkout(
        access_token,
        session_token,
        proxies,
        start_index,
        args.max_proxies,
        args.trial_days,
        args.timeout,
    )
    result["checkout_proxy_attempts"] = attempts
    if data is None:
        failure = proxy
        result["checkout_status"] = failure
        if failure == "already_paid":
            result["credential_valid"] = True
            result["decision"] = "already_paid"
        elif failure == "credential_invalid":
            result["credential_valid"] = False
            result["decision"] = "credential_invalid"
        else:
            result["decision"] = "checkout_failed"
        result["conclusive"] = failure == "credential_invalid"
        result["supported"] = False if result["conclusive"] else None
        return result, next_index

    one_click_eligible = data.get("one_click_trial_eligible")
    is_new_customer = data.get("is_new_stripe_customer")
    result.update(
        {
            "credential_valid": True,
            "checkout_status": "created",
            "one_click_trial_eligible": one_click_eligible,
            "is_new_stripe_customer": is_new_customer,
            "trial_in_openai_response": has_actual_trial_in_response(data),
        }
    )

    if (
        one_click_eligible is False
        and not result["trial_in_openai_response"]
        and not args.check_methods_anyway
    ):
        result.update(
            {
                "stripe_init_status": "skipped_not_trial_eligible",
                "actual_trial": False,
                "decision": "account_trial_ineligible",
                "decision_text": DECISION_TEXT["account_trial_ineligible"],
                "conclusive": True,
                "supported": False,
            }
        )
        return result, next_index

    init_payload, init_status, init_attempts = stripe_init(
        checkout_id,
        stripe_key,
        proxy,
    )
    result["stripe_init_status"] = init_status
    result["stripe_init_attempts"] = init_attempts
    if init_payload is None:
        result["decision"] = "stripe_init_failed"
        result["conclusive"] = False
        result["supported"] = None
        return result, next_index

    methods, methods_source = extract_methods(init_payload)
    init_has_trial, trial_days, has_trial_end = trial_marker(init_payload, "elements_options")
    actual_trial = bool(result["trial_in_openai_response"] or init_has_trial)
    has_momo = None if methods is None else "momo" in methods
    stripe_mode = stripe_field(init_payload, "mode")
    decision = choose_decision(one_click_eligible, actual_trial, stripe_mode, has_momo)
    conclusive = decision != "payment_methods_unknown"
    result.update(
        {
            "stripe_mode": stripe_mode,
            "payment_method_collection": stripe_field(init_payload, "payment_method_collection"),
            "amount_due": amount_due(init_payload),
            "currency": stripe_field(init_payload, "currency"),
            "methods": methods,
            "methods_source": methods_source,
            "has_momo": has_momo,
            "trial_period_days_in_init": trial_days,
            "trial_end_present_in_init": has_trial_end,
            "actual_trial": actual_trial,
            "decision": decision,
            "decision_text": DECISION_TEXT[decision],
            "conclusive": conclusive,
            "supported": decision == "ready",
        }
    )
    return result, next_index


def print_result(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return
    label = result["account"]
    decision = result.get("decision", "checkout_failed")
    decision_text = result.get("decision_text") or DECISION_TEXT.get(decision, decision)
    if "methods" not in result:
        print(f"[{label}] {decision_text}")
        return
    eligible = result.get("one_click_trial_eligible")
    eligible_text = "是" if eligible is True else "否" if eligible is False else "未知"
    trial_text = "是" if result.get("actual_trial") else "否"
    momo_value = result.get("has_momo")
    momo_text = "是" if momo_value is True else "否" if momo_value is False else "未知"
    methods = ",".join(result.get("methods") or []) or "无"
    print(
        f"[{label}] 账号试用资格={eligible_text} | 真trial={trial_text} | "
        f"模式={result.get('stripe_mode') or '未知'} | 方法={methods} | "
        f"MoMo={momo_text} | 结论={decision_text}"
    )


def main() -> int:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    configure_pre_proxy(args.pre_proxy)
    core.COUNTRY_CURRENCY["VN"] = "VND"
    core.CHATGPT_TIMEOUT = args.timeout
    core.DEFAULT_TIMEOUT = args.timeout

    # Prevent the imported extractor from writing request dumps or verbose logs.
    core.dump_http = lambda *unused_args, **unused_kwargs: None
    core.log = lambda *unused_args, **unused_kwargs: None

    try:
        proxies = [""] if args.parse_only else load_proxies(args.proxy_file, args.direct)
    except RuntimeError as exc:
        print(f"检测无法启动：{exc}", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    next_proxy_index = 0
    for index, token_file in enumerate(args.token_files):
        result, next_proxy_index = probe_account(
            account_label(index),
            token_file,
            proxies,
            next_proxy_index,
            args,
        )
        results.append(result)
        print_result(result, args.json)

    return 2 if any(result.get("conclusive") is False for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
