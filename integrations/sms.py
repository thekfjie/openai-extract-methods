"""HeroSMS phone verification: purchase settings, catalog, activations, phone pool.

server.py owns the live settings object, the HeroSMS client, the activation store
and the proxy/identity bindings. Functions here import those inside the function
body rather than at module scope: it breaks the import cycle, and it reads the
current object, which matters because `reload_runtime_config` rebinds CLIENT and
STORE.
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from integrations.common import first_non_empty
from integrations.core_utils import (
    load_json_file,
    normalize_fixed_price_value,
    now_iso,
    parse_bool_flag,
    parse_positive_int,
    parse_timestamp,
    save_json_file,
    strip_empty_values,
    timestamp_is_future,
)
from integrations.herosms import HeroSmsError, PurchaseError, TeleAutoError
from integrations.proxy_config import (
    configured_signup_proxy_candidates,
    normalize_proxy_region,
    parse_proxy_url,
    proxy_name_for_url,
    proxy_url_from_parsed,
    sub2api_proxy_key,
)
from integrations.text_utils import collect_string_values, normalize_text

# Same directory as server.ROOT; defined here so this module does not import
# server just to learn where the repository lives.
ROOT = Path(__file__).resolve().parent.parent


PURCHASE_CONFIG_PATH = ROOT / "data/purchase_config.json"


CATALOG_CACHE_PATH = ROOT / "data/catalog_cache.json"


PHONE_CODE_USAGE_PATH = ROOT / "data/phone_code_usage.json"


DEFAULT_SERVICE_NAME = "OpenAI"


DEFAULT_SERVICE_CODE = "dr"


PURCHASE_FILTER_KEYS = (
    "serviceName",
    "serviceCode",
    "countryName",
    "countryCode",
    "operator",
    "maxPrice",
    "exactPrice",
    "fixedPrice",
)


ACTIVE_STATUS_MAP = {
    "1": ("waiting_for_code", "等待验证码", "STATUS_WAIT_CODE"),
    "3": ("waiting_for_retry", "等待重发", "STATUS_WAIT_RETRY"),
    "4": ("number_issued", "号码已下发", "STATUS_WAIT_GET"),
    "6": ("finished", "已完成", "FULL_SMS"),
    "8": ("canceled", "已取消", "STATUS_CANCEL"),
}


COUNTRY_ALIASES_BY_NAME = {
    "bhutan": ["不丹"],
    "france": ["法国"],
    "italy": ["意大利"],
    "reunion": ["留尼汪"],
    "georgia": ["格鲁吉亚"],
    "england": ["英国", "英格兰"],
    "united kingdom": ["英国"],
    "united states": ["美国"],
    "uae": ["阿联酋"],
    "united arab emirates": ["阿联酋"],
    "ivory coast": ["科特迪瓦"],
    "laos": ["老挝"],
    "syria": ["叙利亚"],
    "vietnam": ["越南"],
    "south korea": ["韩国"],
    "north macedonia": ["北马其顿"],
    "bosnia and herzegovina": ["波黑", "波斯尼亚和黑塞哥维那"],
    "democratic republic of the congo": ["刚果金"],
    "republic of the congo": ["刚果布"],
}


def get_phone_proxy_binding(phone_number: Any) -> dict[str, Any] | None:
    from server import IDENTITY_BINDINGS_LOCK, load_identity_bindings

    phone_key = normalize_phone_key(phone_number)
    if not phone_key:
        return None
    with IDENTITY_BINDINGS_LOCK:
        record = load_identity_bindings().get("phones", {}).get(phone_key)
    return dict(record) if isinstance(record, dict) else None


def phone_record_reusable_for_sms(record: dict[str, Any] | None) -> bool:
    if not isinstance(record, dict):
        return False
    status = str(record.get("status") or "").strip().lower()
    if status in {"canceled", "cancelled", "released", "sold", "suspect_failed"}:
        return False
    expires_at = record.get("expiresAt") or record.get("expires_at")
    if expires_at and not timestamp_is_future(expires_at):
        return False
    return bool(record.get("smsUrl") or record.get("publicUrl") or record.get("line") or record.get("telePublicKey"))


def find_bound_phone_for_email(email: Any) -> str:
    from server import IDENTITY_BINDINGS_LOCK, email_identity_key, load_identity_bindings

    target = email_identity_key(email)
    if not target:
        return ""
    with IDENTITY_BINDINGS_LOCK:
        phones = load_identity_bindings().get("phones", {})
    candidates = []
    for record in phones.values():
        if not isinstance(record, dict):
            continue
        if email_identity_key(record.get("email")) != target:
            continue
        phone_number = str(record.get("phoneNumber") or "").strip()
        if not phone_number or not phone_record_reusable_for_sms(record):
            continue
        candidates.append(record)
    candidates.sort(key=lambda item: str(item.get("updatedAt") or item.get("boundAt") or ""), reverse=True)
    return str((candidates[0] if candidates else {}).get("phoneNumber") or "").strip()


def phone_activation_link_payload(record: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    payload = {
        "activationId": str(record.get("id") or record.get("activationId") or ""),
        "phoneNumber": str(record.get("phoneNumber") or record.get("phone") or ""),
        "telePublicKey": str(record.get("telePublicKey") or ""),
        "publicUrl": str(record.get("publicUrl") or ""),
        "smsUrl": str(record.get("smsUrl") or ""),
        "expiresAt": str(record.get("expiresAt") or record.get("expires_at") or ""),
        "rawExpiresAt": str(record.get("rawExpiresAt") or ""),
        "teleSuccessCount": int(record.get("teleSuccessCount") or 0),
        "teleLastUsedAt": str(record.get("teleLastUsedAt") or ""),
        "teleMaxSuccessCount": int(record.get("teleMaxSuccessCount") or 3),
        "line": str(record.get("line") or ""),
        "status": str(record.get("status") or ""),
        "statusLabel": str(record.get("statusLabel") or ""),
        "purchasedAt": str(record.get("purchasedAt") or ""),
        "updatedAt": str(record.get("updatedAt") or ""),
        "lastCode": str(record.get("lastCode") or ""),
        "codes": [str(code) for code in record.get("codes", []) if str(code)] if isinstance(record.get("codes"), list) else [],
    }
    return {key: value for key, value in payload.items() if value not in ("", [], None)}


def bind_phone_proxy(
    phone_number: Any,
    proxy_data: dict[str, Any],
    *,
    email: Any = "",
    activation_id: Any = "",
    stage: str = "submitted",
    activation: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    from server import IDENTITY_BINDINGS_LOCK, identity_proxy_descriptor, load_identity_bindings, save_identity_bindings

    phone_key = normalize_phone_key(phone_number)
    descriptor = identity_proxy_descriptor(proxy_data.get("proxyUrl") or proxy_data.get("url"), proxy_data.get("proxyName") or proxy_data.get("name"))
    if not phone_key or not descriptor:
        return None
    with IDENTITY_BINDINGS_LOCK:
        data = load_identity_bindings()
        phones = data.setdefault("phones", {})
        existing = phones.get(phone_key) if isinstance(phones.get(phone_key), dict) else {}
        now = now_iso()
        link = phone_activation_link_payload(
            {
                **(activation or {}),
                "id": activation_id or (activation or {}).get("id"),
                "phoneNumber": phone_number,
            }
        )
        links = existing.get("links") if isinstance(existing.get("links"), list) else []
        next_links = [item for item in links if isinstance(item, dict)]
        if link:
            link_key = str(link.get("activationId") or link.get("publicUrl") or link.get("smsUrl") or "")
            next_links = [
                item for item in next_links
                if str(item.get("activationId") or item.get("publicUrl") or item.get("smsUrl") or "") != link_key
            ]
            next_links.insert(0, link)
        record = {
            **existing,
            "phoneNumber": str(phone_number or "").strip(),
            "phoneKey": phone_key,
            **descriptor,
            "email": str(email or existing.get("email") or "").strip(),
            "activationId": str(activation_id or existing.get("activationId") or "").strip(),
            "stage": stage,
            "links": next_links[:30],
            "boundAt": existing.get("boundAt") or now,
            "updatedAt": now,
        }
        for key in (
            "telePublicKey",
            "publicUrl",
            "smsUrl",
            "expiresAt",
            "rawExpiresAt",
            "teleSuccessCount",
            "teleLastUsedAt",
            "teleMaxSuccessCount",
            "line",
            "status",
            "statusLabel",
        ):
            if link.get(key):
                record[key] = link[key]
        phones[phone_key] = record
        save_identity_bindings(data)
        return dict(record)


def phone_proxy_compatibility(phone_number: Any, proxy_data: dict[str, Any]) -> dict[str, Any]:
    from server import identity_proxy_descriptor

    binding = get_phone_proxy_binding(phone_number)
    descriptor = identity_proxy_descriptor(proxy_data.get("proxyUrl") or proxy_data.get("url"), proxy_data.get("proxyName") or proxy_data.get("name"))
    # Phone numbers are independent from browser proxy geography. Keep the
    # latest metadata for history/debugging, but never reject a number because
    # it was previously used through another proxy or region.
    return {"allowed": True, "binding": binding, "proxy": descriptor}


def normalize_phone_key(phone_number: Any) -> str:
    return re.sub(r"\D+", "", str(phone_number or ""))


def load_phone_code_usage() -> dict[str, Any]:
    data = load_json_file(PHONE_CODE_USAGE_PATH)
    events = data.get("events") if isinstance(data, dict) else []
    phones = data.get("phones") if isinstance(data, dict) else {}
    if not isinstance(events, list):
        events = []
    if not isinstance(phones, dict):
        phones = {}
    return {
        "events": [event for event in events if isinstance(event, dict)],
        "phones": {str(key): dict(value) for key, value in phones.items() if isinstance(value, dict)},
    }


def save_phone_code_usage(data: dict[str, Any]) -> dict[str, Any]:
    events = data.get("events") if isinstance(data, dict) else []
    phones = data.get("phones") if isinstance(data, dict) else {}
    if not isinstance(events, list):
        events = []
    if not isinstance(phones, dict):
        phones = {}
    payload = {
        "events": events[-5000:],
        "phones": {str(key): dict(value) for key, value in phones.items() if isinstance(value, dict)},
    }
    save_json_file(PHONE_CODE_USAGE_PATH, payload)
    return payload


def phone_lifecycle_status(phone_number: Any, *, data: dict[str, Any] | None = None) -> dict[str, Any]:
    phone_key = normalize_phone_key(phone_number)
    if not phone_key:
        return {"phoneKey": "", "status": "available", "statusLabel": "可用"}
    usage = data if isinstance(data, dict) else load_phone_code_usage()
    phones = usage.get("phones") if isinstance(usage.get("phones"), dict) else {}
    lifecycle = dict(phones.get(phone_key) or {})
    status = str(lifecycle.get("status") or "available").strip().lower()
    cooldown_until_ts = float(lifecycle.get("cooldownUntilTs") or parse_timestamp(lifecycle.get("cooldownUntil")) or 0)
    if status == "cooldown" and cooldown_until_ts and cooldown_until_ts <= time.time():
        status = "available"
        lifecycle.update(
            {
                "status": "available",
                "statusLabel": "冷却结束，可再次接码",
                "reason": "",
                "cooldownUntil": "",
                "cooldownUntilTs": 0,
                "updatedAt": now_iso(),
            }
        )
        if data is None:
            with PHONE_CODE_USAGE_LOCK:
                latest = load_phone_code_usage()
                latest.setdefault("phones", {})[phone_key] = lifecycle
                save_phone_code_usage(latest)
    labels = {"available": "可用", "cooldown": "冷却中", "sold": "已售"}
    lifecycle.update(
        {
            "phoneNumber": str(lifecycle.get("phoneNumber") or phone_number or ""),
            "phoneKey": phone_key,
            "status": status,
            "statusLabel": str(lifecycle.get("statusLabel") or labels.get(status) or status),
        }
    )
    return lifecycle


def mark_phone_cooldown(phone_number: Any, reason: str, seconds: int, *, source: str = "") -> dict[str, Any]:
    phone_key = normalize_phone_key(phone_number)
    if not phone_key:
        return phone_lifecycle_status(phone_number)
    seconds = max(60, int(seconds or 0))
    now = datetime.now().astimezone()
    cooldown_until = now + timedelta(seconds=seconds)
    with PHONE_CODE_USAGE_LOCK:
        data = load_phone_code_usage()
        current = dict(data.setdefault("phones", {}).get(phone_key) or {})
        if str(current.get("status") or "") == "sold":
            return phone_lifecycle_status(phone_number, data=data)
        current.update(
            {
                "phoneNumber": str(phone_number or ""),
                "phoneKey": phone_key,
                "status": "cooldown",
                "statusLabel": "WhatsApp 冷却中" if "whatsapp" in str(reason or "").lower() else "短信冷却中",
                "reason": str(reason or "").strip(),
                "cooldownUntil": cooldown_until.isoformat(),
                "cooldownUntilTs": cooldown_until.timestamp(),
                "source": str(source or current.get("source") or "self-maintained"),
                "updatedAt": now.isoformat(),
            }
        )
        data["phones"][phone_key] = current
        save_phone_code_usage(data)
    return phone_lifecycle_status(phone_number)


def mark_phone_sold(phone_number: Any, reason: str, *, source: str = "") -> dict[str, Any]:
    phone_key = normalize_phone_key(phone_number)
    if not phone_key:
        return phone_lifecycle_status(phone_number)
    with PHONE_CODE_USAGE_LOCK:
        data = load_phone_code_usage()
        current = dict(data.setdefault("phones", {}).get(phone_key) or {})
        current.update(
            {
                "phoneNumber": str(phone_number or ""),
                "phoneKey": phone_key,
                "status": "sold",
                "statusLabel": "已售（累计接码达到上限）",
                "reason": str(reason or "").strip(),
                "cooldownUntil": "",
                "cooldownUntilTs": 0,
                "soldAt": str(current.get("soldAt") or now_iso()),
                "source": str(source or current.get("source") or "self-maintained"),
                "updatedAt": now_iso(),
            }
        )
        data["phones"][phone_key] = current
        save_phone_code_usage(data)
    return phone_lifecycle_status(phone_number)


def phone_code_window_seconds() -> int:
    from server import CONFIG

    return parse_positive_int(CONFIG.phone_code_window_seconds, default=3600)


def phone_code_max_per_window() -> int:
    from server import CONFIG

    return parse_positive_int(CONFIG.phone_code_max_per_window, default=1)


def phone_code_max_total() -> int:
    from server import CONFIG

    return parse_positive_int(CONFIG.phone_code_max_total, default=3)


def tele_phone_usage_baseline(phone_number: Any, activation_id: Any = "") -> dict[str, Any]:
    """Read Tele's historical successful-code count from the local activation mirror."""
    from server import STORE

    phone_key = normalize_phone_key(phone_number)
    activation_text = str(activation_id or "").strip()
    candidates = []
    for record in STORE.read_all():
        if not is_tele_auto_record(record):
            continue
        if phone_key and normalize_phone_key(record.get("phoneNumber")) != phone_key:
            continue
        if activation_text and str(record.get("id") or "") == activation_text:
            candidates.insert(0, record)
        else:
            candidates.append(record)

    best_count = 0
    best_timestamp = 0.0
    best_used_at = ""
    for record in candidates:
        try:
            count = max(0, int(record.get("teleSuccessCount") or 0))
        except (TypeError, ValueError):
            count = 0
        used_at = str(record.get("teleLastUsedAt") or "").strip()
        used_at_ts = float(parse_timestamp(used_at) or 0)
        if count > best_count or (count == best_count and used_at_ts > best_timestamp):
            best_count = count
            best_timestamp = used_at_ts
            best_used_at = used_at
    return {
        "count": best_count,
        "lastUsedAt": best_used_at,
        "lastUsedAtTs": best_timestamp,
    }


def phone_code_usage_counters(
    phone_number: Any,
    events: list[dict[str, Any]],
    activation_id: Any = "",
    *,
    now_ts: float | None = None,
) -> dict[str, Any]:
    phone_key = normalize_phone_key(phone_number)
    current_ts = time.time() if now_ts is None else float(now_ts)
    cutoff = current_ts - phone_code_window_seconds()
    phone_events = [
        event for event in events
        if str(event.get("phoneKey") or "") == phone_key
    ]
    baseline = tele_phone_usage_baseline(phone_number, activation_id)
    baseline_count = int(baseline.get("count") or 0)
    baseline_ts = float(baseline.get("lastUsedAtTs") or 0)
    if baseline_count > 0 and baseline_ts > 0:
        incremental_events = [
            event for event in phone_events
            if float(event.get("receivedAtTs") or 0) > baseline_ts
        ]
    else:
        incremental_events = phone_events
    window_events = [
        event for event in incremental_events
        if float(event.get("receivedAtTs") or 0) >= cutoff
    ]
    baseline_in_window = bool(baseline_count > 0 and baseline_ts >= cutoff)
    return {
        "total": baseline_count + len(incremental_events),
        "windowCount": len(window_events) + (1 if baseline_in_window else 0),
        "events": phone_events,
        "incrementalEvents": incremental_events,
        "windowEvents": window_events,
        "baselineCount": baseline_count,
        "baselineLastUsedAt": baseline.get("lastUsedAt") or "",
        "baselineLastUsedAtTs": baseline_ts,
        "baselineInWindow": baseline_in_window,
    }


def phone_code_quota_status(phone_number: Any, activation_id: Any = "") -> dict[str, Any]:
    phone_key = normalize_phone_key(phone_number)
    if not phone_key:
        return {"allowed": True, "reason": "", "phoneKey": ""}
    window_seconds = phone_code_window_seconds()
    max_per_window = phone_code_max_per_window()
    max_total = phone_code_max_total()
    now_ts = time.time()
    usage = load_phone_code_usage()
    counters = phone_code_usage_counters(
        phone_number,
        usage.get("events", []),
        activation_id,
        now_ts=now_ts,
    )
    events = counters["events"]
    lifecycle = phone_lifecycle_status(phone_number, data=usage)
    total = int(counters["total"])
    window_count = int(counters["windowCount"])
    baseline_fields = {
        "teleBaselineTotal": int(counters["baselineCount"]),
        "teleBaselineLastUsedAt": counters["baselineLastUsedAt"],
        "localAfterBaseline": len(counters["incrementalEvents"]),
    }
    if lifecycle.get("status") == "sold":
        return {
            "allowed": False,
            "reason": "sold",
            "message": f"手机号 {phone_number} 已转入已售，不再接码",
            "phoneKey": phone_key,
            "windowSeconds": window_seconds,
            "maxPerWindow": max_per_window,
            "maxTotal": max_total,
            "total": total,
            "windowCount": window_count,
            "retryAfterSeconds": 0,
            "lifecycle": lifecycle,
            **baseline_fields,
        }
    if activation_id and any(str(event.get("activationId") or "") == str(activation_id) for event in events):
        return {
            "allowed": True,
            "reason": "same_activation",
            "phoneKey": phone_key,
            "windowSeconds": window_seconds,
            "maxPerWindow": max_per_window,
            "maxTotal": max_total,
            "total": total,
            "windowCount": window_count,
            "lifecycle": lifecycle,
            **baseline_fields,
        }
    if lifecycle.get("status") == "cooldown":
        retry_after = max(1, int(float(lifecycle.get("cooldownUntilTs") or 0) - now_ts))
        return {
            "allowed": False,
            "reason": "whatsapp_cooldown" if "whatsapp" in str(lifecycle.get("reason") or "").lower() else "cooldown",
            "message": f"手机号 {phone_number} 正在冷却，约 {retry_after}s 后可再次接码",
            "phoneKey": phone_key,
            "windowSeconds": window_seconds,
            "maxPerWindow": max_per_window,
            "maxTotal": max_total,
            "total": total,
            "windowCount": window_count,
            "retryAfterSeconds": retry_after,
            "lifecycle": lifecycle,
            **baseline_fields,
        }
    if total >= max_total:
        return {
            "allowed": False,
            "reason": "total_limit",
            "message": f"手机号 {phone_number} 已达到累计 {max_total} 个验证码上限",
            "phoneKey": phone_key,
            "windowSeconds": window_seconds,
            "maxPerWindow": max_per_window,
            "maxTotal": max_total,
            "total": total,
            "windowCount": window_count,
            "retryAfterSeconds": 0,
            "lifecycle": lifecycle,
            **baseline_fields,
        }
    if window_count >= max_per_window:
        window_timestamps = [float(event.get("receivedAtTs") or now_ts) for event in counters["windowEvents"]]
        if counters["baselineInWindow"]:
            window_timestamps.append(float(counters["baselineLastUsedAtTs"] or now_ts))
        oldest = min(window_timestamps or [now_ts])
        retry_after = max(1, int(oldest + window_seconds - now_ts))
        return {
            "allowed": False,
            "reason": "window_limit",
            "message": f"手机号 {phone_number} 在 {window_seconds}s 窗口内已接码 {window_count} 次，需要冷却 {retry_after}s",
            "phoneKey": phone_key,
            "windowSeconds": window_seconds,
            "maxPerWindow": max_per_window,
            "maxTotal": max_total,
            "total": total,
            "windowCount": window_count,
            "retryAfterSeconds": retry_after,
            "lifecycle": lifecycle,
            **baseline_fields,
        }
    return {
        "allowed": True,
        "reason": "",
        "phoneKey": phone_key,
        "windowSeconds": window_seconds,
        "maxPerWindow": max_per_window,
        "maxTotal": max_total,
        "total": total,
        "windowCount": window_count,
        "lifecycle": lifecycle,
        **baseline_fields,
    }


def sync_phone_sold(phone_number: Any, reason: str) -> dict[str, Any]:
    from server import STORE, TELE_AUTO

    phone_key = normalize_phone_key(phone_number)
    matching = [
        dict(record) for record in STORE.read_all()
        if normalize_phone_key(record.get("phoneNumber")) == phone_key
    ]
    tele_records = [record for record in matching if is_tele_auto_record(record)]
    upstream: dict[str, Any] = {"raw": None, "result": "not_tele_auto"}
    if tele_records:
        try:
            upstream = TELE_AUTO.sold_account(tele_records[0], reason)
        except TeleAutoError as error:
            upstream = {"raw": None, "result": "sold_sync_failed", "error": str(error)}
    for record in matching:
        STORE.upsert(
            {
                **record,
                "status": "sold",
                "statusLabel": "已售（累计接码达到上限）",
                "lastAction": "sold",
                "soldReason": reason,
                "teleAutoActionResult": upstream if is_tele_auto_record(record) else record.get("teleAutoActionResult"),
                "updatedAt": now_iso(),
            }
        )
    return upstream


def record_phone_code_usage(phone_number: Any, activation_id: Any, code: Any) -> dict[str, Any]:
    phone_key = normalize_phone_key(phone_number)
    code_text = str(code or "").strip()
    activation_text = str(activation_id or "").strip()
    if not phone_key or not code_text:
        return {"allowed": True, "recorded": False}
    event_key = f"{activation_text}:{code_text}" if activation_text else code_text
    with PHONE_CODE_USAGE_LOCK:
        data = load_phone_code_usage()
        events = data.get("events") or []
        for event in events:
            if str(event.get("phoneKey") or "") == phone_key and str(event.get("eventKey") or "") == event_key:
                return {
                    **phone_code_quota_status(phone_number, activation_id),
                    "allowed": True,
                    "reason": "duplicate_code",
                    "recorded": False,
                    "duplicate": True,
                }
        quota = phone_code_quota_status(phone_number, activation_text)
        if not quota.get("allowed"):
            return {**quota, "recorded": False}
        events.append(
            {
                "phoneNumber": str(phone_number or ""),
                "phoneKey": phone_key,
                "activationId": activation_text,
                "code": code_text,
                "eventKey": event_key,
                "receivedAt": now_iso(),
                "receivedAtTs": time.time(),
            }
        )
        data["events"] = events
        counters = phone_code_usage_counters(
            phone_number,
            events,
            activation_text,
            now_ts=time.time(),
        )
        total = int(counters["total"])
        reached_total_limit = total >= phone_code_max_total()
        if reached_total_limit:
            current = dict(data.setdefault("phones", {}).get(phone_key) or {})
            current.update(
                {
                    "phoneNumber": str(phone_number or ""),
                    "phoneKey": phone_key,
                    "status": "sold",
                    "statusLabel": "已售（累计接码达到上限）",
                    "reason": f"累计成功接码达到 {phone_code_max_total()} 次",
                    "cooldownUntil": "",
                    "cooldownUntilTs": 0,
                    "soldAt": str(current.get("soldAt") or now_iso()),
                    "updatedAt": now_iso(),
                }
            )
            data["phones"][phone_key] = current
        save_phone_code_usage(data)
    upstream = None
    if reached_total_limit:
        upstream = sync_phone_sold(phone_number, f"累计成功接码达到 {phone_code_max_total()} 次")
    result = {**phone_code_quota_status(phone_number, activation_id), "recorded": True}
    result["reachedTotalLimit"] = reached_total_limit
    if reached_total_limit:
        result["allowed"] = True
        result["reason"] = "code_recorded_then_sold"
    if upstream is not None:
        result["soldSync"] = upstream
    return result


class ActivationStore:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.ensure_store()

    def ensure_store(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self.file_path.write_text("[]\n", encoding="utf-8")

    def read_all(self) -> list[dict[str, Any]]:
        self.ensure_store()
        raw = self.file_path.read_text(encoding="utf-8").strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []

    def write_all(self, records: list[dict[str, Any]]) -> None:
        self.ensure_store()
        self.file_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def list(self) -> list[dict[str, Any]]:
        return sorted(
            self.read_all(),
            key=lambda item: item.get("updatedAt") or item.get("purchasedAt") or "",
            reverse=True,
        )

    def get(self, activation_id: str) -> dict[str, Any] | None:
        for record in self.read_all():
            if str(record.get("id")) == str(activation_id):
                return record
        return None

    def upsert(self, next_record: dict[str, Any]) -> dict[str, Any]:
        records = self.read_all()
        now = now_iso()
        merged = {
            "purchasedAt": now,
            "updatedAt": now,
            "codes": [],
        }
        index = -1
        for i, record in enumerate(records):
            if str(record.get("id")) == str(next_record.get("id")):
                index = i
                merged.update(record)
                break
        merged.update(next_record)
        merged["updatedAt"] = now
        merged.setdefault("purchasedAt", now)
        merged["codes"] = [str(code) for code in merged.get("codes", []) if str(code)]
        if index == -1:
            records.append(merged)
        else:
            records[index] = merged
        self.write_all(records)
        return merged

    def append_code(self, activation_id: str, code: str | None) -> dict[str, Any] | None:
        if not code:
            return self.get(activation_id)
        record = self.get(activation_id)
        if not record:
            return None
        codes = [str(item) for item in record.get("codes", []) if str(item)]
        code_str = str(code)
        if code_str not in codes:
            codes.insert(0, code_str)
        record["codes"] = codes
        record["lastCode"] = code_str
        return self.upsert(record)


PURCHASE_GROUP_CURSOR_LOCK = threading.Lock()


PURCHASE_GROUP_NEXT_INDEX = 0


PHONE_CODE_USAGE_LOCK = threading.RLock()


def get_purchase_defaults() -> dict[str, Any]:
    return {
        "serviceName": DEFAULT_SERVICE_NAME,
        "serviceCode": DEFAULT_SERVICE_CODE,
        "countryName": "",
        "countryCode": "",
        "operator": "any",
        "maxPrice": "",
        "exactPrice": "",
        "fixedPrice": "true",
    }


def get_purchase_config() -> dict[str, Any]:
    from server import CONFIG

    file_config = load_json_file(CONFIG.purchase_config_file)
    defaults = get_purchase_defaults()
    settings = get_purchase_settings(file_config=file_config, env_defaults=defaults)
    groups = get_enabled_purchase_groups(settings)
    if groups:
        return dict(groups[0])
    fallback = normalize_purchase_group(defaults, defaults)
    return fallback


def get_purchase_group_start_index(group_count: int) -> int:
    if group_count <= 0:
        return 0
    with PURCHASE_GROUP_CURSOR_LOCK:
        return PURCHASE_GROUP_NEXT_INDEX % group_count


def advance_purchase_group_cursor(group_count: int, next_index: int) -> None:
    if group_count <= 0:
        return
    with PURCHASE_GROUP_CURSOR_LOCK:
        global PURCHASE_GROUP_NEXT_INDEX
        PURCHASE_GROUP_NEXT_INDEX = next_index % group_count


def advance_purchase_group_cursor_after_group(group_index: Any) -> None:
    try:
        current_group_index = int(group_index)
    except (TypeError, ValueError):
        return
    groups = get_enabled_purchase_groups(get_purchase_settings())
    if not groups:
        return
    advance_purchase_group_cursor(len(groups), current_group_index)


def get_purchase_settings(
    *, file_config: dict[str, Any] | None = None, env_defaults: dict[str, Any] | None = None
) -> dict[str, Any]:
    from server import CONFIG

    file_config = file_config if isinstance(file_config, dict) else load_json_file(CONFIG.purchase_config_file)
    env_defaults = env_defaults or get_purchase_defaults()
    root_defaults = dict(env_defaults)
    for key in PURCHASE_FILTER_KEYS:
        value = file_config.get(key)
        if value not in (None, ""):
            root_defaults[key] = value

    raw_groups = file_config.get("purchaseGroups")
    if file_config and not isinstance(raw_groups, list):
        raise HeroSmsError("购买配置必须使用新格式，并提供 purchaseGroups 数组")
    groups: list[dict[str, Any]] = []
    if isinstance(raw_groups, list):
        for index, item in enumerate(raw_groups, start=1):
            if not isinstance(item, dict):
                continue
            groups.append(normalize_purchase_group(item, root_defaults, index=index))

    return {
        "serviceName": str(root_defaults.get("serviceName") or DEFAULT_SERVICE_NAME),
        "serviceCode": str(root_defaults.get("serviceCode") or DEFAULT_SERVICE_CODE),
        "purchaseGroups": groups,
    }


def normalize_purchase_group(source: dict[str, Any] | None, defaults: dict[str, Any], *, index: int = 1) -> dict[str, Any]:
    source = source if isinstance(source, dict) else {}
    merged = {**defaults, **source}
    group = {
        "label": str(source.get("label") or "").strip(),
        "enabled": parse_bool_flag(source.get("enabled", True), default=True),
        "serviceName": str(merged.get("serviceName") or DEFAULT_SERVICE_NAME).strip(),
        "serviceCode": str(merged.get("serviceCode") or DEFAULT_SERVICE_CODE).strip(),
        "countryName": str(merged.get("countryName") or "").strip(),
        "countryCode": str(merged.get("countryCode") or "").strip(),
        "operator": str(merged.get("operator") or "any").strip() or "any",
        "maxPrice": str(merged.get("maxPrice") or "").strip(),
        "exactPrice": str(merged.get("exactPrice") or "").strip(),
        "fixedPrice": normalize_fixed_price_value(merged.get("fixedPrice")),
    }
    if not group["label"]:
        group["label"] = build_purchase_group_label(group, index=index)
    return group


def build_purchase_group_label(group: dict[str, Any], *, index: int = 1) -> str:
    country = str(group.get("countryCode") or group.get("countryName") or "").strip()
    operator = str(group.get("operator") or "any").strip() or "any"
    if str(group.get("fixedPrice") or "").lower() == "true" and str(group.get("exactPrice") or "").strip():
        price = f"exact {str(group.get('exactPrice')).strip()}"
    elif str(group.get("maxPrice") or "").strip():
        price = f"max {str(group.get('maxPrice')).strip()}"
    else:
        price = "market"
    parts = [part for part in (country, operator, price) if part]
    return " / ".join(parts) if parts else f"Group {index}"


def is_purchase_group_configured(group: dict[str, Any]) -> bool:
    return bool(str(group.get("countryCode") or "").strip() or str(group.get("countryName") or "").strip())


def get_enabled_purchase_groups(settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    settings = settings or get_purchase_settings()
    groups = settings.get("purchaseGroups") if isinstance(settings, dict) else []
    return [
        dict(group)
        for group in groups
        if isinstance(group, dict) and parse_bool_flag(group.get("enabled", True), default=True) and is_purchase_group_configured(group)
    ]


def serialize_purchase_settings(settings: dict[str, Any]) -> dict[str, Any]:
    groups = []
    for index, group in enumerate(settings.get("purchaseGroups") or [], start=1):
        if not isinstance(group, dict):
            continue
        normalized = normalize_purchase_group(group, settings, index=index)
        groups.append(
            {
                "label": normalized["label"],
                "enabled": parse_bool_flag(normalized.get("enabled", True), default=True),
                "countryName": normalized["countryName"],
                "countryCode": normalized["countryCode"],
                "operator": normalized["operator"],
                "fixedPrice": normalized["fixedPrice"] == "true",
                "exactPrice": normalized["exactPrice"],
                "maxPrice": normalized["maxPrice"],
            }
        )
    return {
        "serviceName": str(settings.get("serviceName") or DEFAULT_SERVICE_NAME).strip() or DEFAULT_SERVICE_NAME,
        "serviceCode": str(settings.get("serviceCode") or DEFAULT_SERVICE_CODE).strip() or DEFAULT_SERVICE_CODE,
        "purchaseGroups": groups,
    }


def update_purchase_settings(payload: dict[str, Any]) -> dict[str, Any]:
    from server import CONFIG

    defaults = get_purchase_defaults()
    settings = get_purchase_settings(file_config=payload, env_defaults=defaults)
    serialized = serialize_purchase_settings(settings)
    save_json_file(CONFIG.purchase_config_file, serialized)
    return get_purchase_settings(file_config=serialized, env_defaults=defaults)


def get_display_name(source: dict[str, Any], *, name_key: str, code_key: str, default_name: str) -> str:
    explicit_name = str(source.get(name_key) or "").strip()
    if explicit_name:
        return explicit_name
    if str(source.get(code_key) or "").strip():
        return ""
    return default_name


def get_filters(source: dict[str, Any] | None = None, defaults: dict[str, Any] | None = None) -> dict[str, str]:
    source = source or {}
    base = {**(defaults or get_purchase_config()), **source}
    return {
        "serviceName": get_display_name(
            base, name_key="serviceName", code_key="serviceCode", default_name=DEFAULT_SERVICE_NAME
        ),
        "serviceCode": str(base.get("serviceCode") or ""),
        "countryName": get_display_name(
            base, name_key="countryName", code_key="countryCode", default_name=""
        ),
        "countryCode": str(base.get("countryCode") or ""),
        "operator": str(base.get("operator") or "any"),
        "maxPrice": str(base.get("maxPrice") or ""),
        "exactPrice": str(base.get("exactPrice") or ""),
        "fixedPrice": normalize_fixed_price_value(base.get("fixedPrice")),
    }


def get_country_search_fields(item: dict[str, Any]) -> list[str]:
    fields = [str(item.get("name") or ""), str(item.get("localName") or ""), str(item.get("code") or "")]
    if isinstance(item.get("searchTerms"), list):
        fields.extend(str(term) for term in item.get("searchTerms") if term)
    fields.extend(COUNTRY_ALIASES_BY_NAME.get(str(item.get("name") or "").lower(), []))
    return fields


def search_countries_by_name(name: str, limit: int = 8) -> list[dict[str, Any]]:
    from server import CLIENT

    query = normalize_text(name)
    if not query:
        return []
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for item in CLIENT.get_countries():
        fields = get_country_search_fields(item)
        score: int | None = None
        for field in fields:
            normalized = normalize_text(field)
            if not normalized:
                continue
            if normalized == query:
                score = 0 if score is None else min(score, 0)
            elif normalized.startswith(query) or query.startswith(normalized):
                score = 1 if score is None else min(score, 1)
            elif query in normalized or normalized in query:
                score = 2 if score is None else min(score, 2)
        if score is not None:
            label = str(item.get("name") or item.get("localName") or item.get("code") or "")
            ranked.append((score, label.lower(), item))
    ranked.sort(key=lambda entry: (entry[0], entry[1]))
    return [item for _, _, item in ranked[:limit]]


def load_catalog_cache() -> dict[str, Any]:
    return load_json_file(CATALOG_CACHE_PATH)


def save_catalog_cache(cache: dict[str, Any]) -> dict[str, Any]:
    save_json_file(CATALOG_CACHE_PATH, cache)
    return cache


def get_cached_countries(*, refresh: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from server import CLIENT

    cache = load_catalog_cache()
    countries = cache.get("countries")
    if refresh or not isinstance(countries, list):
        countries = CLIENT.get_countries(force=True)
        cache["countries"] = countries
        cache["countriesCachedAt"] = now_iso()
        save_catalog_cache(cache)
    return countries, cache


def search_country_items(items: list[dict[str, Any]], query_text: str, limit: int = 20) -> list[dict[str, Any]]:
    query = normalize_text(query_text)
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for item in items:
        fields = get_country_search_fields(item)
        if not query:
            label = str(item.get("name") or item.get("localName") or item.get("code") or "")
            ranked.append((3, label.lower(), item))
            continue
        score: int | None = None
        for field in fields:
            normalized = normalize_text(field)
            if not normalized:
                continue
            if normalized == query:
                score = 0 if score is None else min(score, 0)
            elif normalized.startswith(query) or query.startswith(normalized):
                score = 1 if score is None else min(score, 1)
            elif query in normalized or normalized in query:
                score = 2 if score is None else min(score, 2)
        if score is not None:
            label = str(item.get("name") or item.get("localName") or item.get("code") or "")
            ranked.append((score, label.lower(), item))
    ranked.sort(key=lambda entry: (entry[0], entry[1]))
    return [item for _, _, item in ranked[:limit]]


def get_cached_operators(service_code: str, country_code: str, *, refresh: bool = False) -> tuple[list[str], dict[str, Any]]:
    from server import CLIENT

    cache = load_catalog_cache()
    operators_cache = cache.get("operators")
    if not isinstance(operators_cache, dict):
        operators_cache = {}
        cache["operators"] = operators_cache
    cache_key = f"{service_code}:{country_code}"
    cached_entry = operators_cache.get(cache_key)
    operators = cached_entry.get("items") if isinstance(cached_entry, dict) else None
    if refresh or not isinstance(operators, list):
        operators = CLIENT.get_operators(service_code, country_code, force=True)
        operators_cache[cache_key] = {"items": operators, "cachedAt": now_iso()}
        save_catalog_cache(cache)
    return operators, cache


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    item = dict(record)
    item["isClosed"] = item.get("status") in {"finished", "canceled"}
    return item


def is_tele_auto_record(record: dict[str, Any] | None) -> bool:
    return bool(isinstance(record, dict) and record.get("teleAuto"))


def list_local_tele_activations(*, include_closed: bool = False) -> list[dict[str, Any]]:
    from server import STORE

    items: list[dict[str, Any]] = []
    for record in STORE.list():
        if not is_tele_auto_record(record):
            continue
        item = normalize_record(record)
        # Records written before the registration-pool cutover remain visible
        # for internal history but are never eligible for new registration.
        item.setdefault("poolScope", "historical_internal")
        item.setdefault("inventoryClass", account_inventory_class(item))
        if include_closed or not item.get("isClosed"):
            items.append(item)
    return items


def registration_inventory_summary() -> dict[str, int]:
    """Summarize the current Tele-backed registration pool without counting history."""
    buckets = {"active": 0, "expired": 0, "usedMany": 0, "unused": 0, "usedSome": 0, "history": 0}
    for item in list_local_tele_activations(include_closed=True):
        if str(item.get("poolScope") or "active_registration") != "active_registration":
            buckets["history"] += 1
            continue
        bucket = account_inventory_class(item)
        if bucket == "expired":
            buckets["expired"] += 1
        elif bucket == "used_many":
            buckets["usedMany"] += 1
            buckets["active"] += 1
        elif bucket == "used_some":
            buckets["usedSome"] += 1
            buckets["active"] += 1
        else:
            buckets["unused"] += 1
            buckets["active"] += 1
    return buckets


def merge_activation_items(primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*primary, *secondary]:
        item_id = str(item.get("id") or "")
        if item_id and item_id in seen:
            continue
        if item_id:
            seen.add(item_id)
        result.append(item)
    result.sort(key=lambda record: record.get("purchasedAt") or record.get("updatedAt") or "", reverse=True)
    return result


def find_local_tele_activation_by_phone(phone_number: Any) -> dict[str, Any] | None:
    target = normalize_phone_key(phone_number)
    if not target:
        return None
    for item in list_local_tele_activations(include_closed=True):
        if normalize_phone_key(item.get("phoneNumber")) == target:
            if phone_record_reusable_for_sms(item):
                return item
    return None


def list_local_tele_activations_by_phone(phone_number: Any, *, include_closed: bool = True) -> list[dict[str, Any]]:
    target = normalize_phone_key(phone_number)
    if not target:
        return []
    return [
        item for item in list_local_tele_activations(include_closed=include_closed)
        if normalize_phone_key(item.get("phoneNumber")) == target
    ]


def phone_detail_payload(phone_number: Any) -> dict[str, Any]:
    history = list_local_tele_activations_by_phone(phone_number, include_closed=True)
    binding = get_phone_proxy_binding(phone_number)
    quota = phone_code_quota_status(phone_number)
    return {
        "phoneNumber": str(phone_number or ""),
        "phoneKey": normalize_phone_key(phone_number),
        "item": history[0] if history else None,
        "items": history,
        "binding": binding,
        "quota": quota,
    }


def phone_record_sort_value(record: dict[str, Any]) -> str:
    return str(record.get("updatedAt") or record.get("purchasedAt") or record.get("boundAt") or "")


def phone_pool_payload(limit: int = 200) -> dict[str, Any]:
    from server import IDENTITY_BINDINGS_LOCK, binding_latest_link, load_identity_bindings

    limit = max(1, min(int(limit or 200), 1000))
    buckets: dict[str, dict[str, Any]] = {}
    for item in list_local_tele_activations(include_closed=True):
        phone_number = str(item.get("phoneNumber") or "").strip()
        phone_key = normalize_phone_key(phone_number)
        if not phone_key:
            continue
        bucket = buckets.setdefault(phone_key, {"phoneNumber": phone_number, "items": []})
        if not bucket.get("phoneNumber"):
            bucket["phoneNumber"] = phone_number
        bucket["items"].append(item)

    with IDENTITY_BINDINGS_LOCK:
        raw_bindings = load_identity_bindings().get("phones", {})
    bindings = {
        normalize_phone_key(binding.get("phoneNumber") or phone_key): dict(binding)
        for phone_key, binding in raw_bindings.items()
        if isinstance(binding, dict) and normalize_phone_key(binding.get("phoneNumber") or phone_key)
    }

    for phone_key, binding in bindings.items():
        bucket = buckets.setdefault(
            phone_key,
            {"phoneNumber": str(binding.get("phoneNumber") or phone_key), "items": []},
        )
        if not bucket.get("phoneNumber"):
            bucket["phoneNumber"] = str(binding.get("phoneNumber") or phone_key)
        if not bucket["items"]:
            latest_link = binding_latest_link(binding)
            bucket["items"].append(
                {
                    **latest_link,
                    "phoneNumber": bucket["phoneNumber"],
                    "status": binding.get("status") or latest_link.get("status") or "",
                    "statusLabel": binding.get("statusLabel") or latest_link.get("statusLabel") or "",
                    "updatedAt": binding.get("updatedAt") or latest_link.get("updatedAt") or "",
                    "purchasedAt": latest_link.get("purchasedAt") or "",
                    "bindingOnly": True,
                    "poolScope": "historical_internal",
                    "inventoryClass": "historical_internal",
                }
            )

    items: list[dict[str, Any]] = []
    for phone_key, bucket in buckets.items():
        records = [
            dict(record) for record in bucket.get("items", [])
            if isinstance(record, dict)
        ]
        records.sort(key=phone_record_sort_value, reverse=True)
        latest = records[0] if records else {}
        phone_number = str(bucket.get("phoneNumber") or latest.get("phoneNumber") or phone_key)
        quota = phone_code_quota_status(phone_number)
        lifecycle = quota.get("lifecycle") if isinstance(quota.get("lifecycle"), dict) else phone_lifecycle_status(phone_number)
        binding = bindings.get(phone_key) or get_phone_proxy_binding(phone_number)
        latest_link = binding_latest_link(binding)
        links = binding.get("links") if isinstance(binding, dict) and isinstance(binding.get("links"), list) else []
        codes_count = 0
        for record in records:
            codes = record.get("codes")
            if isinstance(codes, list):
                codes_count += len([code for code in codes if str(code or "").strip()])
            elif record.get("lastCode"):
                codes_count += 1
        items.append(
            strip_empty_values(
                {
                    "phoneNumber": phone_number,
                    "phoneKey": phone_key,
                    "status": first_non_empty(latest.get("status"), binding.get("status") if isinstance(binding, dict) else ""),
                    "statusLabel": first_non_empty(
                        latest.get("statusLabel"),
                        binding.get("statusLabel") if isinstance(binding, dict) else "",
                    ),
                    "lastAction": latest.get("lastAction"),
                    "holdReason": latest.get("holdReason"),
                    "expiresAt": first_non_empty(
                        latest.get("expiresAt"),
                        latest_link.get("expiresAt"),
                        binding.get("expiresAt") if isinstance(binding, dict) else "",
                    ),
                    "updatedAt": first_non_empty(
                        latest.get("updatedAt"),
                        binding.get("updatedAt") if isinstance(binding, dict) else "",
                    ),
                    "purchasedAt": latest.get("purchasedAt"),
                    "activationId": first_non_empty(latest.get("id"), latest.get("activationId"), latest_link.get("activationId")),
                    "teleAuto": latest.get("teleAuto"),
                    "publicUrl": first_non_empty(
                        latest.get("publicUrl"),
                        latest_link.get("publicUrl"),
                        binding.get("publicUrl") if isinstance(binding, dict) else "",
                    ),
                    "smsUrl": first_non_empty(
                        latest.get("smsUrl"),
                        latest_link.get("smsUrl"),
                        binding.get("smsUrl") if isinstance(binding, dict) else "",
                    ),
                    "line": first_non_empty(
                        latest.get("line"),
                        latest_link.get("line"),
                        binding.get("line") if isinstance(binding, dict) else "",
                    ),
                    "lastCode": first_non_empty(latest.get("lastCode"), latest_link.get("lastCode")),
                    "codesCount": codes_count,
                    "recordsCount": len(records),
                    "quota": quota,
                    "lifecycleStatus": lifecycle.get("status"),
                    "lifecycleLabel": lifecycle.get("statusLabel"),
                    "lifecycleReason": lifecycle.get("reason"),
                    "cooldownUntil": lifecycle.get("cooldownUntil"),
                    "soldAt": lifecycle.get("soldAt"),
                    "source": lifecycle.get("source") or ("tele-auto" if latest.get("teleAuto") else "self-maintained"),
                    "successCount": quota.get("total", 0),
                    "inventoryClass": account_inventory_class(latest) if latest else "unused",
                    "poolScope": latest.get("poolScope") or "active_registration",
                    "binding": binding,
                    "links": links[:10],
                    "reusable": bool(quota.get("allowed")) and (
                        any(phone_record_reusable_for_sms(record) for record in records)
                        or phone_record_reusable_for_sms(binding)
                    ),
                }
            )
        )

    items.sort(key=phone_record_sort_value, reverse=True)
    return {"items": items[:limit], "total": len(items), "limit": limit, "updatedAt": now_iso()}


def public_phone_pool_status() -> dict[str, Any]:
    try:
        pool = phone_pool_payload(1000)
    except Exception:
        return {"ok": False, "status": "unavailable", "total": 0}
    items = pool.get("items", [])
    status_counts: dict[str, int] = {}
    reusable = 0
    quota_allowed = 0
    cooling = 0
    total_limited = 0
    codes_recorded = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("statusLabel") or item.get("status") or "unknown")
        status_counts[label] = status_counts.get(label, 0) + 1
        if item.get("reusable"):
            reusable += 1
        quota = item.get("quota") if isinstance(item.get("quota"), dict) else {}
        if quota.get("allowed"):
            quota_allowed += 1
        elif quota.get("reason") == "window_limit":
            cooling += 1
        elif quota.get("reason") == "total_limit":
            total_limited += 1
        try:
            codes_recorded += int(item.get("codesCount") or 0)
        except (TypeError, ValueError):
            pass
    return {
        "ok": True,
        "status": "ok",
        "total": pool.get("total", len(items)),
        "reusable": reusable,
        "quotaAllowed": quota_allowed,
        "cooling": cooling,
        "totalLimited": total_limited,
        "codesRecorded": codes_recorded,
        "statusCounts": status_counts,
        "updatedAt": pool.get("updatedAt") or now_iso(),
    }


def update_tele_record_from_status(record: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    from server import STORE

    update = {
        **record,
        "status": status.get("localStatus") or record.get("status") or "waiting_for_code",
        "statusLabel": status.get("label") or record.get("statusLabel") or "等待验证码",
        "upstreamStatus": status.get("upstreamStatus") or record.get("upstreamStatus") or "STATUS_WAIT_CODE",
        "rawStatus": status.get("raw"),
        "updatedAt": now_iso(),
    }
    if status.get("code"):
        update["lastCode"] = str(status["code"])
    next_record = STORE.upsert(update)
    if status.get("code"):
        next_record = STORE.append_code(str(record["id"]), str(status["code"])) or next_record
    return normalize_record(next_record)


def build_tele_auto_purchase_item(account: dict[str, Any], filters: dict[str, str], purchase_group_index: int | None = None) -> dict[str, Any]:
    item = {
        "id": account["id"],
        "phoneNumber": account["phoneNumber"],
        "activationCost": None,
        "countryCode": filters.get("countryCode", ""),
        "countryName": filters.get("countryName") or "Tele Auto",
        "serviceCode": filters.get("serviceCode") or DEFAULT_SERVICE_CODE,
        "serviceName": filters.get("serviceName") or DEFAULT_SERVICE_NAME,
        "operator": "tele-auto",
        "canGetAnotherSms": False,
        "status": "number_issued",
        "statusLabel": "号码已下发",
        "upstreamStatus": "STATUS_WAIT_GET",
        "purchasedAt": now_iso(),
        "updatedAt": now_iso(),
        "codes": [],
        "teleAuto": True,
        "telePublicKey": account.get("telePublicKey") or "",
        "publicUrl": account.get("publicUrl") or "",
        "smsUrl": account.get("smsUrl") or "",
        "expiresAt": account.get("expiresAt") or "",
        "rawExpiresAt": account.get("rawExpiresAt") or "",
        "teleSuccessCount": int(account.get("teleSuccessCount") or 0),
        "teleLastUsedAt": account.get("teleLastUsedAt") or "",
        "teleMaxSuccessCount": int(account.get("teleMaxSuccessCount") or 3),
        "teleReuseAfterSeconds": int(account.get("teleReuseAfterSeconds") or 0),
        "line": account.get("line") or "",
        "poolScope": "active_registration",
        "inventoryClass": account_inventory_class(account),
    }
    if purchase_group_index is not None:
        item["purchaseGroupIndex"] = int(purchase_group_index)
    if account.get("raw") is not None:
        item["rawPurchase"] = account["raw"]
    return normalize_record(item)


def account_inventory_class(record: dict[str, Any]) -> str:
    """Normalize Tele activation records into the registration inventory buckets."""
    expires = str(record.get("expiresAt") or record.get("expires_at") or "").strip()
    if expires:
        try:
            parsed = parse_timestamp(expires)
            if parsed and parsed <= time.time():
                return "expired"
        except Exception:
            pass
    count = int(record.get("teleSuccessCount") or 0)
    if count >= int(record.get("teleMaxSuccessCount") or 3):
        return "used_many"
    return "used_some" if count > 0 else "unused"


def find_by_code(items: list[dict[str, Any]], code: str) -> dict[str, Any] | None:
    target = str(code or "").strip()
    if not target:
        return None
    for item in items:
        if str(item.get("code")) == target:
            return item
    return None


def resolve_selections(filters: dict[str, str]) -> dict[str, Any]:
    from server import CLIENT, CONFIG

    service_code = str(filters.get("serviceCode") or "").strip()
    country_code = str(filters.get("countryCode") or "").strip()
    if service_code and country_code:
        return {
            "service": {"code": service_code, "name": filters.get("serviceName") or service_code},
            "services": [],
            "country": {
                "code": country_code,
                "name": filters.get("countryName") or country_code,
                "localName": filters.get("countryName") or country_code,
            },
            "countries": [],
        }

    services = CLIENT.get_services()
    countries = CLIENT.get_countries()
    service = find_by_code(services, service_code) or CLIENT._pick_by_name(
        services, filters["serviceName"], CONFIG.default_service_aliases, ("name", "code")
    )
    country = find_by_code(countries, country_code) or CLIENT._pick_by_name(
        countries, filters["countryName"], CONFIG.default_country_aliases, ("name", "localName", "code")
    )
    if not service:
        raise HeroSmsError(f"找不到服务: {filters.get('serviceCode') or filters['serviceName']}")
    if not country:
        raise HeroSmsError(f"找不到国家/地区: {filters.get('countryCode') or filters['countryName']}")
    return {"service": service, "services": services, "country": country, "countries": countries}


def build_service_lookup() -> dict[str, dict[str, Any]]:
    from server import CLIENT

    return {str(item.get("code")): item for item in CLIENT.get_services()}


def build_country_lookup() -> dict[str, dict[str, Any]]:
    from server import CLIENT

    return {str(item.get("code")): item for item in CLIENT.get_countries()}


def import_active_activations() -> list[dict[str, Any]]:
    from server import CLIENT, STORE

    service_lookup = build_service_lookup()
    country_lookup = build_country_lookup()
    imported = []
    for item in CLIENT.get_active_activations():
        activation_id = str(item.get("activationId") or item.get("id") or "")
        if not activation_id:
            continue
        service_code = str(item.get("serviceCode") or item.get("service") or "")
        country_code = str(item.get("countryCode") or item.get("country") or "")
        status_code = str(item.get("activationStatus") or item.get("status") or "4")
        local_status, label, upstream_status = ACTIVE_STATUS_MAP.get(
            status_code, ("number_issued", "号码已下发", "STATUS_WAIT_GET")
        )
        service = service_lookup.get(service_code, {"code": service_code, "name": service_code or "--"})
        country = country_lookup.get(country_code, {"code": country_code, "name": country_code or "--", "localName": ""})

        record = STORE.upsert(
            {
                "id": activation_id,
                "phoneNumber": str(item.get("phoneNumber") or item.get("phone") or ""),
                "activationCost": item.get("activationCost") or item.get("cost"),
                "countryCode": country_code,
                "countryName": country.get("name") or country.get("localName") or country_code,
                "serviceCode": service_code,
                "serviceName": service.get("name") or service_code,
                "operator": str(item.get("operator") or "any"),
                "status": local_status,
                "statusLabel": label,
                "upstreamStatus": upstream_status,
                "lastCode": item.get("smsCode") or None,
                "codes": [str(item.get("smsCode"))] if item.get("smsCode") else [],
                "purchasedAt": item.get("activationTime") or item.get("createDate") or now_iso(),
                "rawImport": item,
            }
        )
        imported.append(normalize_record(record))
    return imported


def fetch_upstream_activations() -> list[dict[str, Any]]:
    from server import CLIENT, CONFIG

    local_items = list_local_tele_activations(include_closed=False)
    items: list[dict[str, Any]] = []
    if CONFIG.api_key:
        try:
            service_lookup = build_service_lookup()
            country_lookup = build_country_lookup()
            for item in CLIENT.get_active_activations():
                activation_id = str(item.get("activationId") or item.get("id") or "")
                if not activation_id:
                    continue
                service_code = str(item.get("serviceCode") or item.get("service") or "")
                country_code = str(item.get("countryCode") or item.get("country") or "")
                status_code = str(item.get("activationStatus") or item.get("status") or "4")
                local_status, label, upstream_status = ACTIVE_STATUS_MAP.get(
                    status_code, ("number_issued", "号码已下发", "STATUS_WAIT_GET")
                )
                service = service_lookup.get(service_code, {"code": service_code, "name": service_code or "--"})
                country = country_lookup.get(country_code, {"code": country_code, "name": country_code or "--", "localName": ""})
                sms_code = item.get("smsCode") or item.get("code")
                record = normalize_record(
                    {
                        "id": activation_id,
                        "phoneNumber": str(item.get("phoneNumber") or item.get("phone") or ""),
                        "activationCost": item.get("activationCost") or item.get("cost"),
                        "countryCode": country_code,
                        "countryName": country.get("name") or country.get("localName") or country_code,
                        "serviceCode": service_code,
                        "serviceName": service.get("name") or service_code,
                        "operator": str(item.get("operator") or "any"),
                        "status": local_status,
                        "statusLabel": label,
                        "upstreamStatus": upstream_status,
                        "lastCode": str(sms_code) if sms_code else None,
                        "codes": [str(sms_code)] if sms_code else [],
                        "purchasedAt": item.get("activationTime") or item.get("createDate") or now_iso(),
                        "updatedAt": now_iso(),
                        "rawUpstream": item,
                    }
                )
                items.append(record)
        except HeroSmsError:
            if not local_items:
                raise
    return merge_activation_items(local_items, items)


def filter_activations(
    items: list[dict[str, Any]],
    *,
    service_code: str = "",
    country_code: str = "",
    operator: str = "",
    price: str = "",
) -> list[dict[str, Any]]:
    result = items
    if service_code:
        result = [item for item in result if str(item.get("serviceCode")) == str(service_code)]
    if country_code:
        result = [item for item in result if str(item.get("countryCode")) == str(country_code)]
    if operator:
        known_operator_items = [item for item in result if str(item.get("operator", "")).lower() not in {"", "any"}]
        if known_operator_items:
            result = [item for item in result if str(item.get("operator", "")).lower() == str(operator).lower()]
    if price:
        try:
            target = round(float(price), 4)
            result = [
                item
                for item in result
                if item.get("activationCost") is not None and round(float(item.get("activationCost")), 4) == target
            ]
        except ValueError:
            pass
    return result


def get_current_filtered_activations(filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
    items = fetch_upstream_activations()
    if filters:
        price = filters.get("exactPrice") or filters.get("price") or ""
        return filter_activations(
            items,
            service_code=filters.get("serviceCode", ""),
            country_code=filters.get("countryCode", ""),
            operator=filters.get("operator", ""),
            price=price,
        )

    settings = get_purchase_settings()
    matched: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for group in get_enabled_purchase_groups(settings):
        group_filters = get_filters(group, defaults=group)
        price = group_filters.get("exactPrice") or group_filters.get("price") or ""
        for item in filter_activations(
            items,
            service_code=group_filters.get("serviceCode", ""),
            country_code=group_filters.get("countryCode", ""),
            operator=group_filters.get("operator", ""),
            price=price,
        ):
            item_id = str(item.get("id") or "")
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            matched.append(item)
    matched.sort(key=lambda record: record.get("purchasedAt") or "", reverse=True)
    return matched


def build_purchase_attempts(source: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    source = source if isinstance(source, dict) else {}
    if any(key in source for key in PURCHASE_FILTER_KEYS):
        filters = get_filters(source)
        return [
            {
                "label": str(source.get("label") or build_purchase_group_label(filters, index=1)),
                "filters": filters,
            }
        ]

    settings = get_purchase_settings()
    groups = get_enabled_purchase_groups(settings)
    if not groups:
        raise HeroSmsError("未配置可用的 purchaseGroups")
    attempts = []
    group_count = len(groups)
    start_index = get_purchase_group_start_index(group_count)
    ordered_groups = groups[start_index:] + groups[:start_index]
    for offset, group in enumerate(ordered_groups, start=1):
        group_index = (start_index + offset - 1) % group_count
        attempts.append(
            {
                "label": str(group.get("label") or build_purchase_group_label(group, index=group_index + 1)),
                "filters": get_filters(group, defaults=group),
                "groupIndex": group_index + 1,
            }
        )
    return attempts


def execute_purchase(filters: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    from server import CLIENT

    resolved = resolve_selections(filters)
    if filters["fixedPrice"] == "true" and filters["exactPrice"]:
        purchase = CLIENT.buy_activation_fixed_price(
            service_code=resolved["service"]["code"],
            country_code=resolved["country"]["code"],
            operator=filters["operator"],
            exact_price=filters["exactPrice"],
        )
    else:
        purchase = CLIENT.buy_activation(
            service_code=resolved["service"]["code"],
            country_code=resolved["country"]["code"],
            operator=filters["operator"],
            max_price=filters["maxPrice"],
        )
    return purchase, resolved


def build_purchase_item(
    purchase: dict[str, Any],
    resolved: dict[str, Any],
    *,
    include_raw: bool = False,
    purchase_group_index: int | None = None,
) -> dict[str, Any]:
    item = {
        "id": purchase["id"],
        "phoneNumber": purchase["phoneNumber"],
        "activationCost": purchase["activationCost"],
        "countryCode": resolved["country"]["code"],
        "countryName": resolved["country"]["name"] or resolved["country"]["localName"],
        "serviceCode": resolved["service"]["code"],
        "serviceName": resolved["service"]["name"],
        "operator": purchase["operator"],
        "canGetAnotherSms": purchase["canGetAnotherSms"],
        "status": "number_issued",
        "statusLabel": "号码已下发",
        "upstreamStatus": "STATUS_WAIT_GET",
        "purchasedAt": now_iso(),
        "updatedAt": now_iso(),
        "codes": [],
    }
    if purchase_group_index is not None:
        item["purchaseGroupIndex"] = int(purchase_group_index)
    if include_raw:
        item["rawPurchase"] = purchase["raw"]
    return normalize_record(item)


def purchase_context_proxy(source: dict[str, Any]) -> dict[str, str]:
    from server import get_email_proxy_binding, identity_proxy_descriptor

    proxy_url = first_non_empty(source.get("proxy"), source.get("proxyUrl"), source.get("ucSignupProxy"))
    proxy_name = first_non_empty(source.get("proxyName"), source.get("proxy_name"))
    descriptor = identity_proxy_descriptor(proxy_url, proxy_name)
    if descriptor:
        return descriptor
    email = first_non_empty(source.get("email"), source.get("accountEmail"), source.get("account_email"))
    binding = get_email_proxy_binding(email)
    if binding:
        return identity_proxy_descriptor(binding.get("proxyUrl"), binding.get("proxyName"))
    return {}


def purchase_with_fallback(source: dict[str, Any] | None = None) -> dict[str, Any]:
    from server import CONFIG, STORE, TELE_AUTO

    source = source if isinstance(source, dict) else {}
    attempts_summary = []
    last_error: HeroSmsError | None = None
    requested_provider = str(source.get("provider") or source.get("smsProvider") or "").strip().lower()
    context_proxy = purchase_context_proxy(source)
    context_email = str(first_non_empty(source.get("email"), source.get("accountEmail"), source.get("account_email")) or "").strip()
    if requested_provider not in {"hero", "herosms"} and TELE_AUTO.configured:
        tele_filters = get_filters(source)
        reusable_candidates = []
        if hasattr(STORE, "list"):
            for original in list_local_tele_activations(include_closed=True):
                # Only records explicitly created in the current registration
                # pool may be reused. Legacy local activations remain visible
                # for history but never re-enter registration allocation.
                if str(original.get("poolScope") or "") != "active_registration":
                    continue
                if not phone_record_reusable_for_sms(original):
                    continue
                item = dict(original)
                details = {}
                account_details = getattr(TELE_AUTO, "account_details", None)
                if callable(account_details):
                    try:
                        details = account_details(item) or {}
                    except TeleAutoError:
                        details = {}
                if details:
                    # Keep the local lifecycle, but preserve the current upstream state
                    # and refresh all usage/expiry fields used by quota decisions.
                    tele_status = str(details.get("status") or "").strip()
                    item.update({
                        key: value for key, value in details.items()
                        if (
                            key not in {"status", "rawDetails"}
                            and value not in (None, "")
                        )
                        or key in {"teleSuccessCount", "teleMaxSuccessCount", "teleReuseAfterSeconds"}
                    })
                    if tele_status:
                        item["teleStatus"] = tele_status
                    if tele_status.lower() == "sold":
                        item.update({"status": "sold", "statusLabel": "Tele 已售"})
                    item = STORE.upsert(item)
                if str(item.get("status") or "").lower() == "sold":
                    continue
                # Do not pass activation_id here: a prior code on the same local
                # record must still obey the one-hour cooldown.
                if phone_code_quota_status(item.get("phoneNumber")).get("allowed"):
                    reusable_candidates.append(item)
        reusable_candidates.sort(
            key=lambda item: (-int(item.get("teleSuccessCount") or 0), phone_record_sort_value(item)),
        )
        if reusable_candidates:
            item = reusable_candidates[0]
            compatibility = phone_proxy_compatibility(item.get("phoneNumber"), context_proxy)
            item_payload = normalize_record(item)
            item_payload.update(
                {
                    "identityBinding": compatibility.get("binding"),
                    "identityProxy": compatibility.get("proxy") or context_proxy,
                    "reusedLocal": True,
                    "status": "number_issued",
                    "statusLabel": "冷却结束，复用本地号码",
                }
            )
            STORE.upsert(item_payload)
            return {
                "filters": tele_filters,
                "item": item_payload,
                "rawPurchase": item.get("rawPurchase"),
                "attempts": [
                    {
                        "index": 1,
                        "label": "Tele Auto 本地复用",
                        "filters": tele_filters,
                        "success": True,
                        "provider": "tele-auto",
                        "reusedLocal": True,
                        "phoneNumber": item.get("phoneNumber"),
                    }
                ],
            }
        tele_attempt_limit = parse_positive_int(
            source.get("teleAttemptLimit")
            or source.get("tele_attempt_limit")
            or source.get("phoneAttemptLimit")
            or source.get("phone_attempt_limit"),
            default=5,
        )
        for tele_attempt in range(1, max(1, tele_attempt_limit) + 1):
            account: dict[str, Any] | None = None
            item: dict[str, Any] | None = None
            try:
                account = TELE_AUTO.issue_account()
                item = STORE.upsert(build_tele_auto_purchase_item(account, tele_filters))
                quota = phone_code_quota_status(account.get("phoneNumber"), account.get("id"))
                if not quota.get("allowed"):
                    try:
                        if quota.get("reason") in {"total_limit", "sold"}:
                            upstream = TELE_AUTO.sold_account(account, quota.get("message") or "累计接码达到上限")
                        else:
                            upstream = TELE_AUTO.release_account(account)
                        released = True
                    except TeleAutoError as release_error:
                        upstream = {"raw": None, "result": "release_failed", "error": str(release_error)}
                        released = False
                    quota_label = "累计取码次数已满" if quota.get("reason") in {"total_limit", "sold"} else "号码仍在冷却"
                    item = STORE.upsert(
                        {
                            **item,
                            "status": "sold" if quota.get("reason") in {"total_limit", "sold"} else "released",
                            "statusLabel": (
                                f"{quota_label}，已转已售" if released else f"{quota_label}，同步失败"
                            ) if quota.get("reason") in {"total_limit", "sold"} else (
                                f"{quota_label}，已释放" if released else f"{quota_label}，释放失败"
                            ),
                            "lastAction": "sold" if quota.get("reason") in {"total_limit", "sold"} else "release",
                            "holdReason": quota.get("message") or quota_label,
                            "codeQuota": quota,
                            "teleAutoActionResult": upstream,
                            "updatedAt": now_iso(),
                        }
                    )
                    attempts_summary.append(
                        {
                            "index": len(attempts_summary) + 1,
                            "label": "Tele Auto",
                            "filters": tele_filters,
                            "success": False,
                            "provider": "tele-auto",
                            "phoneNumber": account.get("phoneNumber"),
                            "error": quota.get("message") or quota_label,
                            "quota": quota,
                            "released": released,
                        }
                    )
                    continue
                compatibility = phone_proxy_compatibility(account.get("phoneNumber"), context_proxy)
                item_payload = normalize_record(item)
                item_payload["identityBinding"] = compatibility.get("binding")
                item_payload["identityProxy"] = compatibility.get("proxy") or context_proxy
                return {
                    "filters": tele_filters,
                    "item": item_payload,
                    "rawPurchase": account.get("raw"),
                    "attempts": attempts_summary
                    + [
                        {
                            "index": len(attempts_summary) + 1,
                            "label": "Tele Auto",
                            "filters": tele_filters,
                            "success": True,
                            "provider": "tele-auto",
                            "proxyRegion": (compatibility.get("proxy") or context_proxy or {}).get("region", ""),
                            "email": context_email,
                        }
                    ],
                }
            except TeleAutoError as error:
                last_error = error
                attempts_summary.append(
                    {
                        "index": len(attempts_summary) + 1,
                        "label": "Tele Auto",
                        "filters": tele_filters,
                        "success": False,
                        "provider": "tele-auto",
                        "error": str(error),
                    }
                )
                break
            except HeroSmsError as error:
                last_error = error
                if account:
                    try:
                        TELE_AUTO.release_account(account)
                    except TeleAutoError:
                        pass
                attempts_summary.append(
                    {
                        "index": len(attempts_summary) + 1,
                        "label": "Tele Auto",
                        "filters": tele_filters,
                        "success": False,
                        "provider": "tele-auto",
                        "error": str(error),
                    }
                )
                continue
        if attempts_summary:
            last_error = last_error or TeleAutoError("Tele Auto 没有返回符合代理归属的手机号")
        if last_error:
            if requested_provider not in {"hero", "herosms"}:
                detail = "；".join(f"{item['index']}. {item['label']}: {item['error']}" for item in attempts_summary)
                raise PurchaseError(f"所有购买配置都失败: {detail}", attempts_summary) from last_error
        else:
            last_error = TeleAutoError("Tele Auto 没有返回符合代理归属的手机号")

        if requested_provider not in {"hero", "herosms"}:
            detail = "；".join(f"{item['index']}. {item['label']}: {item['error']}" for item in attempts_summary)
            raise PurchaseError(f"所有购买配置都失败: {detail}", attempts_summary) from last_error

    for index, attempt in enumerate(build_purchase_attempts(source), start=len(attempts_summary) + 1):
        filters = attempt["filters"]
        label = str(attempt.get("label") or build_purchase_group_label(filters, index=index))
        try:
            purchase, resolved = execute_purchase(filters)
            return {
                "filters": filters,
                "item": build_purchase_item(
                    purchase,
                    resolved,
                    purchase_group_index=int(attempt["groupIndex"]) if attempt.get("groupIndex") is not None else None,
                ),
                "rawPurchase": purchase["raw"],
                "attempts": attempts_summary
                + [
                    {
                        "index": index,
                        "label": label,
                        "filters": filters,
                        "success": True,
                        "groupIndex": attempt.get("groupIndex"),
                    }
                ],
            }
        except HeroSmsError as error:
            last_error = error
            attempts_summary.append(
                {
                    "index": index,
                    "label": label,
                    "filters": filters,
                    "success": False,
                    "groupIndex": attempt.get("groupIndex"),
                    "error": str(error),
                }
            )
            continue

    detail = "；".join(f"{item['index']}. {item['label']}: {item['error']}" for item in attempts_summary) or "没有可执行的购买组"
    raise PurchaseError(f"所有购买配置都失败: {detail}", attempts_summary) from last_error


def find_activation_by_phone(phone_number: str) -> dict[str, Any] | None:
    normalized = str(phone_number or "").strip()
    if not normalized:
        return None
    local = find_local_tele_activation_by_phone(normalized)
    if local:
        return local
    target_key = normalize_phone_key(normalized)
    items = fetch_upstream_activations()
    return next(
        (
            item for item in items
            if str(item.get("phoneNumber")) == normalized
            or (target_key and normalize_phone_key(item.get("phoneNumber")) == target_key)
        ),
        None,
    )


def sync_record_status(record: dict[str, Any]) -> dict[str, Any]:
    from server import CLIENT, STORE

    status = CLIENT.get_status(str(record["id"]))
    next_record = STORE.upsert(
        {
            **record,
            "status": status["localStatus"],
            "statusLabel": status["label"],
            "upstreamStatus": status["upstreamStatus"],
            "rawStatus": status["raw"],
        }
    )
    if status.get("code"):
        next_record = STORE.append_code(str(record["id"]), status["code"]) or next_record
    return {"record": normalize_record(next_record), "status": status}
