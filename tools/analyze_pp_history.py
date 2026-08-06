#!/usr/bin/env python3
"""Produce a de-identified PP extraction/payment history report."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


CARD_METHODS = {"direct_card", "ph_link"}


def parse_time(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def checkout_family(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if "oaics_" in normalized:
        return "OAICS"
    if "cs_live_" in normalized:
        return "CS_LIVE"
    if "cs_test_" in normalized:
        return "CS_TEST"
    return "NONE"


def extraction_succeeded(method: str, item: dict[str, Any]) -> bool:
    if method == "paypal_ba":
        return item.get("status") == "succeeded" and item.get("extractionStatus") == "ba_ready"
    return item.get("status") == "succeeded"


def failure_category(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "empty"
    rules = [
        ("token_or_eligibility", ("token", "401", "资格", "account.eligibility")),
        ("invalid_promotion", ("invalid_promotion",)),
        ("blocked", ("blocked", "result=block", "openai_confirm_blocked")),
        ("provider_redirect_timeout", ("provider redirect", "轮询超时")),
        ("stripe_init", ("stripe.init",)),
        ("amount_gate", ("金额必须", "金额 0", "amount")),
        ("paypal_unavailable", ("不支持 paypal",)),
        ("credential_lost_after_restart", ("凭证已清除",)),
        ("already_paid", ("当前套餐为 plus", "当前套餐为 pro", "already paid")),
        ("network_or_tls", ("sslerror", "tls", "connection reset", "bad hostname", "eof")),
        ("verification_required", ("verification_required",)),
        ("cancelled", ("取消", "cancel")),
    ]
    for name, markers in rules:
        if any(marker in text for marker in markers):
            return name
    return "other"


def flatten_jobs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for job in payload.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        method = str(job.get("method") or "unknown")
        options = job.get("options") if isinstance(job.get("options"), dict) else {}
        for item in job.get("items") or []:
            if not isinstance(item, dict):
                continue
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            token_hash = str(item.get("tokenHash") or "").strip()
            email = str(item.get("email") or "").strip().lower()
            checkout_id = item.get("checkoutId") or result.get("checkoutId") or ""
            rows.append({
                "method": method,
                "country": str(item.get("country") or options.get("country") or "").upper(),
                "status": str(item.get("status") or ""),
                "extractionStatus": str(item.get("extractionStatus") or ""),
                "paymentStatus": str(item.get("paymentStatus") or ""),
                "family": checkout_family(checkout_id),
                "succeeded": extraction_succeeded(method, item),
                "failureCategory": "" if extraction_succeeded(method, item) else failure_category(item.get("error") or item.get("detail")),
                "identity": token_hash or email,
                "at": parse_time(item.get("startedAt") or job.get("startedAt") or job.get("createdAt")),
            })
    return rows


def rate(successes: int, total: int) -> float | None:
    return round(100 * successes / total, 1) if total else None


def outcome(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    successes = sum(bool(row.get("succeeded")) for row in materialized)
    pending = sum(str(row.get("status") or "").lower() in {"queued", "running"} for row in materialized)
    settled = len(materialized) - pending
    return {
        "total": len(materialized), "settled": settled, "pending": pending,
        "succeeded": successes, "successRatePct": rate(successes, settled),
    }


def grouped_outcomes(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = sorted({str(row.get(field) or "") for row in rows})
    return {value or "(empty)": outcome(row for row in rows if str(row.get(field) or "") == value) for value in values}


def prior_cohort(rows: list[dict[str, Any]], *, family: str, country: str = "GB", methods: set[str] | None = None) -> dict[str, Any]:
    by_identity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["identity"]:
            by_identity[row["identity"]].append(row)
    for history in by_identity.values():
        history.sort(key=lambda row: row["at"])

    selected = []
    matched_identities: set[str] = set()
    for current in rows:
        if current["method"] != "paypal_ba" or current["country"] != country or not current["identity"]:
            continue
        previous = [
            row for row in by_identity[current["identity"]]
            if row["at"] < current["at"]
            and row["method"] != "paypal_ba"
            and row["family"] == family
            and (methods is None or row["method"] in methods)
        ]
        if previous:
            selected.append(current)
            matched_identities.add(current["identity"])
    result = outcome(selected)
    result["distinctIdentities"] = len(matched_identities)
    return result


def read_json_lines(path: Path) -> list[dict[str, Any]]:
    items = []
    if not path.is_file():
        return items
    with path.open(encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                items.append(value)
    return items


def analyze(jobs_payload: dict[str, Any], audit_events: list[dict[str, Any]], contexts: list[dict[str, Any]], raw_jobs_text: str = "") -> dict[str, Any]:
    rows = flatten_jobs(jobs_payload)
    paypal = [row for row in rows if row["method"] == "paypal_ba"]
    paypal_gb = [row for row in paypal if row["country"] == "GB"]

    methods = {}
    for method in sorted({row["method"] for row in rows}):
        method_rows = [row for row in rows if row["method"] == method]
        methods[method] = {**outcome(method_rows), "families": grouped_outcomes(method_rows, "family")}

    protocol_events = [event for event in audit_events if event.get("event") == "protocol-pay"]
    card_link_events = [
        event for event in audit_events
        if event.get("event") == "client-card-flow"
        and event.get("stage") == "生成 Checkout 提链"
        and event.get("status") == "succeeded"
    ]
    latest_by_task: dict[str, dict[str, Any]] = {}
    for event in protocol_events:
        task_id = str(event.get("task_id") or "")
        if task_id:
            latest_by_task[task_id] = event
    terminal = Counter()
    for event in latest_by_task.values():
        status = str(event.get("status") or "unknown")
        payment_status = str(event.get("payment_status") or "")
        if payment_status == "verification_required":
            status = "verification_required"
        terminal[status] += 1
    protocol_failure_categories = Counter(
        failure_category(event.get("message"))
        for event in latest_by_task.values()
        if str(event.get("status") or "") == "failed"
    )

    exact_oailive = raw_jobs_text.lower().count("oailive")
    context_families = Counter(checkout_family(item.get("checkout_session_id")) for item in contexts)
    minimum = datetime.min.replace(tzinfo=timezone.utc)
    dated = [row["at"] for row in rows if row["at"] != minimum]
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "inputs": {
            "jobCount": len(jobs_payload.get("jobs") or []),
            "itemCount": len(rows),
            "identityCount": len({row["identity"] for row in rows if row["identity"]}),
            "auditEventCount": len(audit_events),
            "checkoutContextCount": len(contexts),
            "range": {
                "start": min(dated).isoformat().replace("+00:00", "Z") if dated else None,
                "end": max(dated).isoformat().replace("+00:00", "Z") if dated else None,
            },
        },
        "methods": methods,
        "paypal": {
            "all": outcome(paypal),
            "gb": outcome(paypal_gb),
            "allFamilies": grouped_outcomes(paypal, "family"),
            "gbFamilies": grouped_outcomes(paypal_gb, "family"),
            "extractionStatuses": dict(sorted(Counter(row["extractionStatus"] or "(empty)" for row in paypal).items())),
            "failureCategories": dict(sorted(Counter(row["failureCategory"] for row in paypal if row["failureCategory"]).items())),
        },
        "priorHistoryHypotheses": {
            "directCardOAICSToGBPayPal": prior_cohort(rows, family="OAICS", methods={"direct_card"}),
            "cardMethodsOAICSToGBPayPal": prior_cohort(rows, family="OAICS", methods=CARD_METHODS),
            "anyNonPayPalOAICSToGBPayPal": prior_cohort(rows, family="OAICS"),
            "directCardCSLiveToGBPayPal": prior_cohort(rows, family="CS_LIVE", methods={"direct_card"}),
            "cardMethodsCSLiveToGBPayPal": prior_cohort(rows, family="CS_LIVE", methods=CARD_METHODS),
            "anyNonPayPalCSLiveToGBPayPal": prior_cohort(rows, family="CS_LIVE"),
        },
        "protocolPaymentAudit": {
            "eventCount": len(protocol_events),
            "distinctTasks": len(latest_by_task),
            "latestTaskOutcomes": dict(sorted(terminal.items())),
            "latestFailureCategories": dict(sorted(protocol_failure_categories.items())),
        },
        "cardLinkAudit": {
            "successfulLinkEvents": len(card_link_events),
            "linkFamilyFieldPresent": any(
                any(key in event for key in ("checkout_session_id", "checkoutId", "checkout_id"))
                for event in card_link_events
            ),
            "note": "card_audit does not carry a checkout family per event, so exact card-link → PP joins are not asserted.",
        },
        "linkVocabulary": {
            "exactOAILIVEOccurrences": exact_oailive,
            "note": "OAILIVE is kept separate from Stripe cs_live_; no alias is assumed.",
            "checkoutContextFamilies": dict(sorted(context_families.items())),
        },
        "interpretation": [
            "顺序队列要求相同的 token hash 或规范化邮箱，并且前一条记录时间更早。",
            "队列为 0 表示当前历史没有该模式样本。",
            "这些是描述性比例，不构成因果结论。",
            "verification_required 与已完成支付成功分开统计。",
        ],
    }


def markdown(report: dict[str, Any]) -> str:
    pp = report["paypal"]
    hypotheses = report["priorHistoryHypotheses"]
    audit = report["protocolPaymentAudit"]
    lines = [
        "# PP 提炼与协议支付历史核查",
        "",
        f"- 任务：{report['inputs']['jobCount']}；条目：{report['inputs']['itemCount']}；可关联身份：{report['inputs']['identityCount']}",
        f"- PP 总体：{pp['all']['succeeded']}/{pp['all']['total']}（{pp['all']['successRatePct']}%）",
        f"- 英国 PP：{pp['gb']['succeeded']}/{pp['gb']['total']}（{pp['gb']['successRatePct']}%）",
        "",
        "## PP 当前链型",
        "",
        "| 范围 | 链型 | 成功/总数 | 成功率 |",
        "|---|---:|---:|---:|",
    ]
    for scope, values in (("全部", pp["allFamilies"]), ("英国", pp["gbFamilies"])):
        for family, item in values.items():
            lines.append(f"| {scope} | {family} | {item['succeeded']}/{item['total']} | {item['successRatePct']}% |")
    lines.extend(["", "## 先前链型 → 英国 PP", "", "| 假设 | 成功/总数 | 成功率 | 身份数 |", "|---|---:|---:|---:|"])
    for name, item in hypotheses.items():
        pct = "—" if item["successRatePct"] is None else f"{item['successRatePct']}%"
        lines.append(f"| {name} | {item['succeeded']}/{item['total']} | {pct} | {item['distinctIdentities']} |")
    lines.extend([
        "",
        "## 协议支付审计",
        "",
        f"- 事件：{audit['eventCount']}；独立任务：{audit['distinctTasks']}",
        f"- 最新任务结果：`{json.dumps(audit['latestTaskOutcomes'], ensure_ascii=False, sort_keys=True)}`",
        f"- 精确字符串 OAILIVE 出现次数：{report['linkVocabulary']['exactOAILIVEOccurrences']}（未与 `cs_live_` 合并）",
        f"- 直卡审计中成功‘生成 Checkout 提链’事件：{report['cardLinkAudit']['successfulLinkEvents']}；事件未记录链型，未强行与 PP 任务关联",
        "",
        "## 结论口径",
        "",
    ])
    lines.extend(f"- {item}" for item in report["interpretation"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--contexts", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()

    raw_jobs = args.jobs.read_text(encoding="utf-8", errors="ignore")
    jobs_payload = json.loads(raw_jobs)
    audit_raw = args.audit.read_bytes() if args.audit.is_file() else b""
    contexts_raw = args.contexts.read_bytes() if args.contexts.is_file() else b""
    report = analyze(jobs_payload, read_json_lines(args.audit), read_json_lines(args.contexts), raw_jobs)
    report["inputFiles"] = {
        "jobs": {"path": str(args.jobs), "bytes": len(raw_jobs.encode("utf-8")), "sha256": hashlib.sha256(raw_jobs.encode("utf-8")).hexdigest()},
        "audit": {"path": str(args.audit), "bytes": len(audit_raw), "sha256": hashlib.sha256(audit_raw).hexdigest()},
        "contexts": {"path": str(args.contexts), "bytes": len(contexts_raw), "sha256": hashlib.sha256(contexts_raw).hexdigest()},
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown_output.write_text(markdown(report), encoding="utf-8")
    print(args.json_output.resolve())
    print(args.markdown_output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
