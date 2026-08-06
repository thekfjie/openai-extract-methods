#!/usr/bin/env python3
"""Audit a PP HAR against checked-in split references without exporting secrets."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qs, urlparse


SENSITIVE_HEADERS = {
    "authorization", "cookie", "paypal-client-context", "paypal-client-metadata-id",
    "x-paypal-internal-euat", "set-cookie",
}


def request_json(entry: dict[str, Any]) -> Any:
    text = ((entry.get("request") or {}).get("postData") or {}).get("text") or ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def response_text(entry: dict[str, Any]) -> str:
    content = (entry.get("response") or {}).get("content") or {}
    text = str(content.get("text") or "")
    if content.get("encoding") == "base64":
        try:
            return base64.b64decode(text).decode("utf-8", "replace")
        except (ValueError, UnicodeError):
            return ""
    return text


def request_headers(entry: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("name") or "").lower(): str(item.get("value") or "")
        for item in (entry.get("request") or {}).get("headers") or []
    }


def operations(payload: Any) -> list[dict[str, Any]]:
    values = payload if isinstance(payload, list) else [payload]
    return [item for item in values if isinstance(item, dict) and item.get("operationName")]


def normalize_graphql(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def decode_reason(value: str) -> str:
    try:
        return base64.b64decode(value).decode("utf-8", "replace") if value else ""
    except (ValueError, UnicodeError):
        return ""


def response_roots(value: Any) -> tuple[list[str], int]:
    values = value if isinstance(value, list) else [value]
    roots: set[str] = set()
    errors = 0
    for item in values:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("data"), dict):
            roots.update(str(key) for key in item["data"])
        if isinstance(item.get("errors"), list):
            errors += len(item["errors"])
    return sorted(roots), errors


def load_current_queries(protocol_root: Path) -> dict[str, str]:
    namespace: dict[str, Any] = {}
    source = (protocol_root / "paypal" / "graphql.py").read_text(encoding="utf-8", errors="replace")
    exec(compile(source, str(protocol_root / "paypal" / "graphql.py"), "exec"), namespace)
    return {
        "CheckoutSessionDataQuery": namespace.get("CHECKOUT_SESSION_DATA_QUERY", ""),
        "GriffinMetadataQuery": namespace.get("GRIFFIN_METADATA_QUERY", ""),
        "DeferredFeature": namespace.get("DEFERRED_FEATURE_QUERY", ""),
        "CookieBannerQuery": namespace.get("COOKIE_BANNER_QUERY", ""),
        "InitialDataQuery": namespace.get("INITIAL_DATA_QUERY", ""),
        "InitiateRiskBasedTwoFactorPhoneConfirmationMutation": namespace.get("INITIATE_2FA_PHONE_MUTATION", ""),
        "ConfirmRiskBasedTwoFactorPhoneConfirmationMutation": namespace.get("CONFIRM_2FA_PHONE_MUTATION", ""),
        "SignUpNewMemberMutation": namespace.get("SIGNUP_NEW_MEMBER_MUTATION", ""),
        "authorize": namespace.get("AUTHORIZE_BILLING_MUTATION", ""),
    }


def audit(har_path: Path, reference_root: Path, protocol_root: Path) -> dict[str, Any]:
    raw = har_path.read_bytes()
    payload = json.loads(raw.decode("utf-8", "replace"))
    log = payload.get("log") or {}
    entries = log.get("entries") or []
    sequence = []
    operation_requests: dict[str, list[dict[str, Any]]] = {}
    authorize_success = False
    successful_return = False
    post_success_generic_errors = 0
    return_seen = False
    checkout_drop_before_hermes = False
    signup_index = None
    hermes_index = None
    authorize_index = None
    hermes_query_flags: dict[str, Any] = {}
    hermes_cookie_names: set[str] = set()
    signup_cookie_names: set[str] = set()
    authorize_cookie_names: set[str] = set()
    authorize_billing_id = ""
    prior_ec_tokens: set[str] = set()

    for index, entry in enumerate(entries):
        request = entry.get("request") or {}
        response = entry.get("response") or {}
        parsed = urlparse(str(request.get("url") or ""))
        body = request_json(entry)
        body_operations = operations(body)
        if body_operations:
            try:
                response_json = json.loads(response_text(entry))
            except json.JSONDecodeError:
                response_json = None
            roots, error_count = response_roots(response_json)
            for operation in body_operations:
                name = str(operation.get("operationName") or "")
                operation_requests.setdefault(name, []).append(operation)
                sequence.append({
                    "entry": index,
                    "at": entry.get("startedDateTime"),
                    "host": parsed.hostname,
                    "path": parsed.path,
                    "operation": name,
                    "variableNames": sorted(str(key) for key in (operation.get("variables") or {})),
                    "status": response.get("status"),
                    "responseRoots": roots,
                    "responseErrorCount": error_count,
                    "responseFormat": "json" if response_json is not None else "html_or_text",
                })
                if name == "authorize" and isinstance(response_json, list):
                    authorize_index = index
                    authorize_billing_id = str((operation.get("variables") or {}).get("billingAgreementId") or "")
                    authorize_success = any(
                        isinstance(item, dict)
                        and isinstance(item.get("data"), dict)
                        and isinstance(item["data"].get("billing"), dict)
                        and bool(item["data"]["billing"].get("authorize"))
                        for item in response_json
                    )
                if name == "SignUpNewMemberMutation":
                    signup_index = index
                    token = str((operation.get("variables") or {}).get("token") or "")
                    if token:
                        prior_ec_tokens.add(token)

        headers = {str(item.get("name") or "").lower(): str(item.get("value") or "") for item in response.get("headers") or []}
        request_header_map = request_headers(entry)
        cookie_names = {part.split("=", 1)[0].strip() for part in request_header_map.get("cookie", "").split(";") if "=" in part}
        if index == signup_index:
            signup_cookie_names = cookie_names
        if parsed.hostname == "www.paypal.com" and parsed.path == "/webapps/hermes" and hermes_index is None:
            hermes_index = index
            hermes_cookie_names = cookie_names
            query = parse_qs(parsed.query)
            hermes_query_flags = {
                "guestUserReason": (query.get("modxo_redirect_reason") or [""])[0] == "guest_user",
                "fromSignupLite": (query.get("fromSignupLite") or [""])[0] == "true",
                "addFIContingencyNoRetry": (query.get("addFIContingency") or [""])[0] == "noretry",
                "redirectToHermes": (query.get("redirectToHermes") or [""])[0] == "true",
                "fallbackOne": (query.get("fallback") or [""])[0] == "1",
                "reason": (query.get("reason") or [""])[0],
            }
        if parsed.hostname == "www.paypal.com" and parsed.path == "/checkoutweb/drop" and hermes_index is None:
            checkout_drop_before_hermes = True
        if index == authorize_index:
            authorize_cookie_names = cookie_names
        location = urlparse(headers.get("location", ""))
        if parsed.hostname == "pm-redirects.stripe.com" and response.get("status") in {301, 302, 303, 307, 308}:
            return_seen = True
            query = parse_qs(location.query)
            successful_return = (
                location.hostname == "pay.openai.com"
                and location.path.startswith("/c/pay/cs_live_")
                and str((query.get("redirect_status") or [""])[0]).lower() in {"success", "succeeded"}
            )
        if return_seen and parsed.hostname == "www.paypal.com" and parsed.path == "/checkoutweb/genericError":
            post_success_generic_errors += 1

    reference_matches = []
    stems = sorted({path.name.rsplit(".", 2)[0] for path in reference_root.glob("*.req.json")})
    for stem in stems:
        ref_request = json.loads((reference_root / f"{stem}.req.json").read_text(encoding="utf-8"))
        ref_headers = json.loads((reference_root / f"{stem}.headers.json").read_text(encoding="utf-8"))
        ref_response_text = (reference_root / f"{stem}.resp.json").read_text(encoding="utf-8", errors="replace")
        candidates = [(index, entry) for index, entry in enumerate(entries) if request_json(entry) == ref_request]
        exact_response_entries = [index for index, entry in candidates if response_text(entry) == ref_response_text]
        exact_header_entries = []
        for index, entry in candidates:
            actual = request_headers(entry)
            if all(actual.get(str(key).lower()) == str(value) for key, value in ref_headers.items()):
                exact_header_entries.append(index)
        reference_matches.append({
            "group": stem,
            "requestMatchEntries": [index for index, _ in candidates],
            "exactResponseEntries": exact_response_entries,
            "exactHeaderSubsetEntries": exact_header_entries,
            "referenceHeaderNames": sorted(str(key).lower() for key in ref_headers if str(key).lower() not in SENSITIVE_HEADERS),
        })

    current_queries = load_current_queries(protocol_root)
    query_conformance = {}
    for name in sorted(operation_requests):
        captured = operation_requests[name][0]
        current = current_queries.get(name, "")
        query_conformance[name] = {
            "currentQueryPresent": bool(current),
            "queryMatchesCapture": bool(current) and normalize_graphql(current) == normalize_graphql(captured.get("query") or ""),
            "capturedVariableNames": sorted(str(key) for key in (captured.get("variables") or {})),
        }

    started = [str(entry.get("startedDateTime") or "") for entry in entries if entry.get("startedDateTime")]
    flow_source = (protocol_root / "paypal" / "flow.py").read_text(encoding="utf-8", errors="replace")
    return {
        "source": {
            "path": str(har_path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "creator": log.get("creator"),
            "entryCount": len(entries),
            "startedAt": min(started) if started else None,
            "endedAt": max(started) if started else None,
            "methodCounts": dict(sorted(Counter(str((entry.get("request") or {}).get("method") or "") for entry in entries).items())),
            "statusCounts": dict(sorted(Counter(str((entry.get("response") or {}).get("status") or "") for entry in entries).items())),
        },
        "referenceValidation": {
            "groupCount": len(reference_matches),
            "fullyMatchedGroups": sum(
                bool(item["requestMatchEntries"] and item["exactResponseEntries"] and item["exactHeaderSubsetEntries"])
                for item in reference_matches
            ),
            "groups": reference_matches,
        },
        "graphql": {
            "requestCount": len(sequence),
            "operationCounts": dict(sorted(Counter(item["operation"] for item in sequence).items())),
            "sequence": sequence,
            "queryConformance": query_conformance,
        },
        "milestones": {
            "authorizeResponseHasBillingAuthorize": authorize_success,
            "providerReturnRedirectsToSuccessfulCSLive": successful_return,
            "postSuccessGenericErrorPageCount": post_success_generic_errors,
        },
        "identityUplift": {
            "signupEntry": signup_index,
            "hermesEntry": hermes_index,
            "authorizeEntry": authorize_index,
            "hermesQueryFlags": hermes_query_flags,
            "hermesBodyContainsBuyer": "buyer" in response_text(entries[hermes_index]).lower() if hermes_index is not None else False,
            "hermesBodyContainsUserId": "userid" in response_text(entries[hermes_index]).lower() if hermes_index is not None else False,
            "hermesBodyContainsEuatMarker": "euat" in response_text(entries[hermes_index]).lower() if hermes_index is not None else False,
            "signupToHermesCookieOverlap": len(signup_cookie_names & hermes_cookie_names),
            "hermesToAuthorizeCookieOverlap": len(hermes_cookie_names & authorize_cookie_names),
            "signupRequestHasCookie": bool(signup_cookie_names),
            "hermesRequestHasCookie": bool(hermes_cookie_names),
            "authorizeRequestHasCookie": bool(authorize_cookie_names),
            "authorizeBillingAgreementIdMatchesSignupEc": bool(authorize_billing_id and authorize_billing_id in prior_ec_tokens),
            "authorizeUsesEuatHeader": bool(authorize_index is not None and request_headers(entries[authorize_index]).get("x-paypal-internal-euat")),
            "authorizeHasClientContextHeader": bool(authorize_index is not None and request_headers(entries[authorize_index]).get("paypal-client-context")),
            "authorizeHasSeparateClientMetadataHeader": bool(authorize_index is not None and request_headers(entries[authorize_index]).get("paypal-client-metadata-id")),
            "currentFlowHasIdentityGate": "_assert_identity_uplift" in flow_source,
            "currentFlowUsesHermes": "webapps/hermes" in flow_source,
            "currentFlowHardcodesBillingLite": "billingLite=1" in flow_source,
            "currentFlowReasonValue": (
                "Ul9FUlJPUg=="
                if "HERMES_GUEST_REASON = \"Ul9FUlJPUg==\"" in flow_source
                else ("CARD_GENERIC_ERROR" if "CARD_GENERIC_ERROR" in flow_source else "")
            ),
            "harReasonValue": hermes_query_flags.get("reason", ""),
            "harReasonDecoded": decode_reason(str(hermes_query_flags.get("reason", ""))),
            "harHasCheckoutDropBeforeHermes": checkout_drop_before_hermes,
            "currentFlowUsesCheckoutDrop": "checkoutweb/drop" in flow_source,
            "currentFlowContainsTwoHermesUrls": flow_source.count("webapps/hermes?") >= 2,
        },
        "privacy": {
            "capturedValuesExported": False,
            "individualTokensExported": False,
            "individualAccountsExported": False,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    source = report["source"]
    refs = report["referenceValidation"]
    milestones = report["milestones"]
    uplift = report["identityUplift"]
    lines = [
        "# 英国 PP HAR 对照报告",
        "",
        f"- SHA-256：`{source['sha256']}`",
        f"- 时间：{source['startedAt']} — {source['endedAt']}",
        f"- HAR 条目：{source['entryCount']}；GraphQL 操作：{report['graphql']['requestCount']}",
        f"- 拆分参考：{refs['fullyMatchedGroups']}/{refs['groupCount']} 组与原 HAR 的请求、响应及选定头部完全对应",
        f"- `billing.authorize` 成功对象：{milestones['authorizeResponseHasBillingAuthorize']}",
        f"- Provider return 跳回成功 `cs_live_`：{milestones['providerReturnRedirectsToSuccessfulCSLive']}",
        f"- 成功返回之后出现 genericError 页面：{milestones['postSuccessGenericErrorPageCount']} 次（属于后续重复导航，不能反推前一次 authorize 失败）",
        "",
        "## 当前查询与 HAR",
        "",
        "| 操作 | 当前实现 | 查询完全一致 | HAR 变量 |",
        "|---|---:|---:|---|",
    ]
    for name, item in report["graphql"]["queryConformance"].items():
        lines.append(f"| {name} | {item['currentQueryPresent']} | {item['queryMatchesCapture']} | {', '.join(item['capturedVariableNames'])} |")
    lines.extend(["", "## 操作顺序", "", "| # | 时间 | 操作 | 端点 | HTTP | 响应根 | 格式 |", "|---:|---|---|---|---:|---|---|"])
    for item in report["graphql"]["sequence"]:
        lines.append(
            f"| {item['entry']} | {item['at']} | {item['operation']} | {item['path']} | {item['status']} | "
            f"{', '.join(item['responseRoots'])} | {item['responseFormat']} |"
        )
    lines.extend([
        "",
        "## Guest → Member 身份提升",
        "",
        f"- HAR 顺序：SignUp entry {uplift['signupEntry']} → Hermes entry {uplift['hermesEntry']} → authorize entry {uplift['authorizeEntry']}",
        f"- Hermes flags：`{json.dumps(uplift['hermesQueryFlags'], ensure_ascii=False, sort_keys=True)}`",
        f"- Hermes 页面包含 buyer/userId/EUAT 标记：{uplift['hermesBodyContainsBuyer']} / {uplift['hermesBodyContainsUserId']} / {uplift['hermesBodyContainsEuatMarker']}",
        f"- 三个关键请求均带 Cookie 头：注册={uplift['signupRequestHasCookie']}，Hermes={uplift['hermesRequestHasCookie']}，authorize={uplift['authorizeRequestHasCookie']}（Cookie 名会因作用域变化，未用名称交集判定会话一致）",
        f"- authorize 的 billingAgreementId 复用注册 EC：{uplift['authorizeBillingAgreementIdMatchesSignupEc']}",
        f"- authorize 头：EUAT={uplift['authorizeUsesEuatHeader']}，Client-Context={uplift['authorizeHasClientContextHeader']}，独立 CMID={uplift['authorizeHasSeparateClientMetadataHeader']}",
        f"- 当前代码：身份门禁={uplift['currentFlowHasIdentityGate']}，Hermes={uplift['currentFlowUsesHermes']}，显式 checkout/drop={uplift['currentFlowUsesCheckoutDrop']}（HAR={uplift['harHasCheckoutDropBeforeHermes']}），Hermes URL 两套={uplift['currentFlowContainsTwoHermesUrls']}，硬编码 billingLite={uplift['currentFlowHardcodesBillingLite']}，HAR reason=`{uplift['harReasonValue']}`（解码 `{uplift['harReasonDecoded']}`），当前 reason 常量=`{uplift['currentFlowReasonValue']}`",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--har", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--protocol-root", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.har, args.reference_root, args.protocol_root)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    print(args.json_output.resolve())
    print(args.markdown_output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
