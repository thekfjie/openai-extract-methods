from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urlparse, urlsplit
from urllib.request import Request, urlopen

from flask import Flask, jsonify, redirect, render_template, request, make_response

from cdk_store import (
    activate_code,
    create_codes,
    delete_code,
    finalize_usage,
    list_codes,
    lookup_code_detail,
    lookup_merge_chain,
    merge_codes,
    reserve_usage,
    session_status,
    set_enabled,
    validate_session,
)


ROOT = Path(__file__).resolve().parent
app = Flask(__name__, template_folder="templates", static_folder="static")
app.json.ensure_ascii = False


async def sentinel_headers(proxy: str, flow: str, device_id: str, did: str = "") -> dict[str, str]:
    from sentinel_compat import build_headers
    return build_headers(proxy, flow, device_id, did)

REG_SESSION_SECRET = os.getenv("REG_SESSION_SECRET", "").strip()
REG_SESSION_COOKIE = os.getenv("REG_SESSION_COOKIE", "reg_access").strip() or "reg_access"
REG_INTERNAL_BASE = os.getenv("REG_INTERNAL_BASE", "<REPLACE_ME>").strip().rstrip("/")
PH_SHORT_BRIDGE_KEY = os.getenv("PH_SHORT_BRIDGE_KEY", "").strip()
EXTRACT_API_BASE = os.getenv("EXTRACT_API_BASE", "http://127.0.0.1:18794").strip().rstrip("/")
JOB_TTL_SECONDS = max(1800, int(os.getenv("PH_PORTAL_JOB_TTL", "86400")))
MAX_JOBS = max(20, min(500, int(os.getenv("PH_PORTAL_MAX_JOBS", "120"))))
PAY153_PYTHON = os.getenv("PAY153_PYTHON", "/opt/payment-core/venv/bin/python").strip()
PAY153_INTERNAL_BASE = os.getenv("PAY153_INTERNAL_BASE", "<REPLACE_ME>").strip().rstrip("/")
PAY153_INTERNAL_KEY = os.getenv("PAY153_INTERNAL_KEY", "").strip()
CURRENT_PROTOCOL_BASE = os.getenv("CURRENT_PROTOCOL_BASE", "http://127.0.0.1:18795").strip().rstrip("/")
CURRENT_PROTOCOL_PASSWORD = os.getenv("CURRENT_PROTOCOL_PASSWORD", "").strip()
MAIN_API_BASE = os.getenv("MAIN_API_BASE", "http://127.0.0.1:13030").strip().rstrip("/")
CARD_ACCOUNT_API_BASE = os.getenv("CARD_ACCOUNT_API_BASE", "").strip().rstrip("/")
PROTOCOL_HELPER = str(ROOT / "ph_checkout_protocol.py")
CARD_BIND_HELPER = str(ROOT / "card_bind_session.py")
CARD_DEFAULT_HELPER = str(ROOT / "card_set_default.py")
CARD_CHECKOUT_CONTEXT_HELPER = str(ROOT / "card_checkout_context.py")
CARD_SERVER_TOKEN_HELPER = str(ROOT / "server_confirmation_token.py")
STANDALONE_PAY_HELPER = str(ROOT / "standalone_protocol_pay.py")
CHECKOUT_CONTEXT_PATH = ROOT / "data" / "checkout_contexts.jsonl"
CARD_AUDIT_PATH = ROOT / "data" / "card_audit.jsonl"
PROXY_PREFLIGHT_HELPER = str(ROOT / "proxy_preflight.py")
CARD_BIND_ACCOUNT_EMAIL = os.getenv("CARD_BIND_ACCOUNT_EMAIL", "user@example.com").strip().lower()
CARD_CDK_COOKIE = os.getenv("CARD_CDK_COOKIE", "<REPLACE_ME>").strip() or "<REPLACE_ME>"
ACCOUNT_RUN_LOCK_DIR = Path(os.getenv("AUTOMYAI_ACCOUNT_RUN_LOCK_DIR", "/app/data/account-run-locks"))
ACCOUNT_RUN_GUARD_ENABLED = os.getenv("AUTOMYAI_ACCOUNT_RUN_GUARD", "1").strip().lower() not in {"0", "false"}
_jobs: dict[str, dict] = {}
_protocol_pay_jobs: dict[str, dict] = {}
_card_checkout_jobs: dict[str, dict] = {}
_key_probe_jobs: dict[str, dict] = {}
_lock = threading.RLock()
_external_at_lock = threading.Lock()


class AccountRunBusy(RuntimeError):
    pass


def _account_run_key(access_token: str) -> tuple[str, str]:
    token = str(access_token or "").strip()
    parts = token.split(".")
    claims = {}
    if len(parts) >= 2:
        try:
            segment = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(segment.encode("ascii")).decode("utf-8"))
        except Exception:
            claims = {}
    auth = claims.get("https://api.openai.com/auth") or {}
    profile = claims.get("https://api.openai.com/profile") or {}
    auth = auth if isinstance(auth, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    account_id = str(auth.get("chatgpt_account_id") or auth.get("account_id") or claims.get("account_id") or "").strip()
    email = str(profile.get("email") or claims.get("email") or "").strip().lower()
    identity = "account:" + account_id if account_id else "token:" + hashlib.sha256(token.encode()).hexdigest()[:16]
    return identity, email


def _acquire_account_run(access_token: str, job_id: str, method: str):
    if not ACCOUNT_RUN_GUARD_ENABLED:
        return None
    identity, email = _account_run_key(access_token)
    ACCOUNT_RUN_LOCK_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = ACCOUNT_RUN_LOCK_DIR / (hashlib.sha256(identity.encode()).hexdigest()[:16] + ".lock")
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.seek(0)
        try:
            owner = json.loads(handle.read() or "{}")
        except Exception:
            owner = {}
        handle.close()
        where = "/".join(filter(None, [str(owner.get("service") or ""), str(owner.get("method") or "")]))
        detail = f"，当前位于 {where}" if where else ""
        raise AccountRunBusy(f"ACCOUNT_ALREADY_RUNNING: {email or identity}{detail}；请等待该任务结束或先停止它") from exc
    owner = {
        "service": "支付中心", "jobId": job_id, "method": method,
        "label": email, "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps(owner, ensure_ascii=False))
    handle.flush()
    os.fsync(handle.fileno())
    return handle


def _release_account_run(handle) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _release_job_account_run(collection: dict[str, dict], job_id: str) -> None:
    with _lock:
        job = collection.get(job_id)
        handle = (job or {}).pop("_account_run_lock", None)
    _release_account_run(handle)


class PrefixMiddleware:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    def __call__(self, environ, start_response):
        prefix = str(environ.get("HTTP_X_SCRIPT_NAME") or "").rstrip("/")
        if prefix:
            path = str(environ.get("PATH_INFO") or "/")
            if path == prefix:
                path = "/"
            elif path.startswith(prefix + "/"):
                path = path[len(prefix):]
            environ["SCRIPT_NAME"] = prefix
            environ["PATH_INFO"] = path
        return self.wrapped(environ, start_response)


app.wsgi_app = PrefixMiddleware(app.wsgi_app)


def shared_reg_session_valid() -> bool:
    if not REG_SESSION_SECRET:
        return True
    register_expected = hmac.new(
        REG_SESSION_SECRET.encode("utf-8"),
        b"register-console-access",
        hashlib.sha256,
    ).hexdigest()
    automyai_expected = hmac.new(
        REG_SESSION_SECRET.encode("utf-8"),
        b"automyai-admin-session",
        hashlib.sha256,
    ).hexdigest()
    provided = str(request.cookies.get(REG_SESSION_COOKIE) or "")
    header_password = str(request.headers.get("X-Admin-Password") or "")
    return bool(
        (provided and (
            hmac.compare_digest(provided, register_expected)
            or hmac.compare_digest(provided, automyai_expected)
        ))
        or (header_password and hmac.compare_digest(header_password, REG_SESSION_SECRET))
    )


@app.before_request
def require_shared_reg_session():
    public_mount = request.script_root in {"/card-link", "/protocol-pay"}
    if request.path == "/healthz":
        return None
    if public_mount:
        page_or_asset = request.path in {
            "/", "/static/card-flow.css", "/static/card-flow.js", "/static/cdk-gate.js",
            "/static/cdk-admin.css", "/static/cdk-admin.js", "/api/cdk/status", "/api/cdk/activate", "/api/cdk/merge",
        }
        if page_or_asset:
            return None
        admin_request = request.path == "/cdk-admin/" or request.path.startswith("/api/cdk-admin/")
        if admin_request:
            if request.headers.get("X-CDK-Admin-Verified") == "1":
                return None
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "CDK_ADMIN_AUTH_REQUIRED"}), 403
            return "CDK admin authentication required", 403
        if not admin_request and request.path.startswith("/api/"):
            raw_cdk = str(request.cookies.get(CARD_CDK_COOKIE) or "")
            session = validate_session(raw_cdk)
            if session:
                return None
            # An exhausted CDK may still read the final state of tasks it
            # already submitted; only new write operations are stopped.
            readonly_task_status = request.method == "GET" and (
                request.path.startswith("/api/protocol-pay/jobs/")
                or request.path.startswith("/api/card-flow/task/")
                or request.path == "/api/cdk/status"
            )
            if readonly_task_status and session_status(raw_cdk):
                return None
            return jsonify({"ok": False, "error": "CDK_REQUIRED"}), 401
    if shared_reg_session_valid():
        return None
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "LOGIN_REQUIRED"}), 401
    return redirect("/login")


def reg_console_request(path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    if not REG_SESSION_SECRET:
        raise RuntimeError("REG_SESSION_SECRET_MISSING")
    session_value = hmac.new(
        REG_SESSION_SECRET.encode("utf-8"),
        b"register-console-access",
        hashlib.sha256,
    ).hexdigest()
    data = None
    headers = {
        "Cookie": f"{REG_SESSION_COOKIE}={session_value}",
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(
        f"{REG_INTERNAL_BASE}{path}",
        data=data,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return int(response.status), json.loads(raw or "{}")
    except Exception as exc:
        status = int(getattr(exc, "code", 502) or 502)
        raw = ""
        if hasattr(exc, "read"):
            try:
                raw = exc.read().decode("utf-8", errors="replace")
            except Exception:
                raw = ""
        try:
            body = json.loads(raw or "{}")
        except Exception:
            body = {"ok": False, "error": raw[:300] or str(exc)}
        return status, body


def reg_bridge_get(path: str, params: dict | None = None) -> dict:
    current_paths = {
        "/api/internal/ph-short/tasks": "/internal/card-portal/source-tasks",
        "/api/internal/ph-short/session": "/internal/card-portal/source-session",
    }
    if path in current_paths:
        query = f"?{urlencode(params or {})}" if params else ""
        req = Request(f"{EXTRACT_API_BASE}{current_paths[path]}{query}", headers={"Accept": "application/json"})
        try:
            with urlopen(req, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            raise RuntimeError(f"读取本机短链任务失败：{exc}") from exc
        if path.endswith("/session") and isinstance(payload.get("session"), dict):
            return payload["session"]
        if isinstance(payload, dict) and payload.get("ok") is not False:
            return payload
        raise RuntimeError(str((payload or {}).get("error") or "本机短链接口返回异常"))
    if path == "/api/internal/ph-short/proxy":
        raise RuntimeError("请在恢复门户填写代理池；当前项目不自动混入外部代理")
    if not PH_SHORT_BRIDGE_KEY:
        raise RuntimeError("短链桥接密钥尚未配置")
    query = f"?{urlencode(params or {})}" if params else ""
    req = Request(
        f"{REG_INTERNAL_BASE}{path}{query}",
        headers={"X-PH-Short-Bridge-Key": PH_SHORT_BRIDGE_KEY, "Accept": "application/json"},
    )
    try:
        with urlopen(req, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        detail = ""
        if hasattr(exc, "read"):
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                detail = ""
        raise RuntimeError(f"读取 app.example.com 账号会话失败：{detail or exc}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is False or payload.get("error"):
        raise RuntimeError(str((payload or {}).get("error") or "账号会话接口返回异常"))
    return payload


def validate_short_url(raw: str) -> tuple[str, str, str]:
    value = str(raw or "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "chatgpt.com":
        raise ValueError("短链必须使用 chatgpt.com 官方 HTTPS 地址")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 3 or parts[0] != "checkout":
        raise ValueError("短链路径格式不正确")
    processor, session_id = parts[1], parts[2]
    if processor not in {"openai_ie", "openai_llc"}:
        raise ValueError("短链处理实体不正确")
    if not re.fullmatch(r"(?:oaics_|cs_)[A-Za-z0-9_-]{12,}", session_id):
        raise ValueError("Checkout Session ID 格式不正确")
    return value, processor, session_id


def cleanup_jobs() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    with _lock:
        expired = [
            key for key, value in _jobs.items()
            if value.get("created_at", 0) < cutoff
            and value.get("status") not in {"queued", "running", "verification_required"}
        ]
        for key in expired:
            _jobs.pop(key, None)
        if len(_jobs) > MAX_JOBS:
            finished = sorted(
                (item for item in _jobs.values() if item.get("status") not in {"queued", "running", "verification_required"}),
                key=lambda item: item.get("created_at", 0),
            )
            for item in finished[: max(0, len(_jobs) - MAX_JOBS)]:
                _jobs.pop(str(item.get("id") or ""), None)


def public_job(job: dict) -> dict:
    return {
        "id": job["id"],
        "task_id": job.get("task_id") or "",
        "account_email": job.get("account_email") or "",
        "status": job.get("status") or "queued",
        "progress": int(job.get("progress") or 0),
        "stage": job.get("stage") or "等待执行",
        "message": job.get("message") or "",
        "error": job.get("error") or "",
        "result": dict(job.get("result") or {}),
        "logs": list(job.get("logs") or []),
        "cancel_requested": bool(job.get("cancel_requested")),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "finished_at": job.get("finished_at"),
    }


def add_log(job_id: str, level: str, message: str) -> None:
    text = str(message or "").strip()
    if not text:
        return
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        logs = job.setdefault("logs", [])
        if logs and logs[-1].get("message") == text:
            return
        logs.append({"time": time.strftime("%H:%M:%S"), "type": level, "message": text[:800]})
        if len(logs) > 100:
            del logs[:-100]
        job["updated_at"] = time.time()


def parse_cards(raw) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def luhn_ok(number: str) -> bool:
        total = 0
        parity = len(number) % 2
        for index, char in enumerate(number):
            digit = int(char)
            if index % 2 == parity:
                digit *= 2
                if digit > 9:
                    digit -= 9
            total += digit
        return total % 10 == 0

    def append_card(number: Any, month: Any, year: Any, cvc: Any) -> None:
        number_text = re.sub(r"\D", "", str(number or ""))
        month_text = re.sub(r"\D", "", str(month or ""))
        year_text = re.sub(r"\D", "", str(year or ""))
        cvc_text = re.sub(r"\D", "", str(cvc or ""))
        if len(year_text) == 2:
            year_text = "20" + year_text
        if not (12 <= len(number_text) <= 19 and luhn_ok(number_text)
                and 1 <= int(month_text or 0) <= 12 and len(year_text) == 4
                and 3 <= len(cvc_text) <= 4):
            return
        key = (number_text, str(int(month_text)), year_text, cvc_text)
        if key not in seen:
            seen.add(key)
            cards.append({"number": key[0], "exp_month": key[1], "exp_year": key[2], "cvc": key[3]})

    if isinstance(raw, list):
        for value in raw:
            if isinstance(value, dict):
                append_card(value.get("number"), value.get("exp_month") or value.get("month"),
                            value.get("exp_year") or value.get("year"),
                            value.get("cvc") or value.get("cvv") or value.get("csc"))
            else:
                parts = [item for item in re.split(r"[|,;\s]+", str(value or "").strip()) if item]
                if len(parts) >= 4:
                    append_card(*parts[:4])
    else:
        text = str(raw or "")
        for line in text.splitlines():
            parts = [item for item in re.split(r"[|,;\s]+", line.strip()) if item]
            if len(parts) >= 4:
                append_card(*parts[:4])
        number_pattern = re.compile(r"(?<!\d)(\d{12,19}|(?:\d{4}[ -]){3}\d{4}|(?:\d{4}[ -]){2}\d{3,4})(?!\d)")
        expiry_pattern = re.compile(r"(?<!\d)(0?[1-9]|1[0-2])\s*[/|,-]\s*(\d{2}|\d{4})(?!\d)")
        cvc_pattern = re.compile(r"(?:cvc|cvv|csc|verification|security(?:\s*code)?|\u5b89\u5168\u7801|\u9a8c\u8bc1\u7801)[^\d]{0,24}(\d{3,4})", re.I)
        for match in number_pattern.finditer(text):
            number = re.sub(r"\D", "", match.group(1))
            if not (12 <= len(number) <= 19 and luhn_ok(number)):
                continue
            tail = text[match.end():match.end() + 240]
            expiry = expiry_pattern.search(tail)
            if not expiry:
                continue
            cvc_match = cvc_pattern.search(tail)
            if cvc_match:
                cvc = cvc_match.group(1)
            else:
                plain = re.search(r"(?<!\d)(\d{3,4})(?!\d)", tail[expiry.end():])
                cvc = plain.group(1) if plain else ""
            append_card(number, expiry.group(1), expiry.group(2), cvc)
    if not cards:
        raise ValueError("未识别到完整支付卡，请检查卡号、有效期和 CVC")
    return cards[:20]


def parse_proxies(raw) -> list[str]:
    values = raw if isinstance(raw, list) else re.split(r"[\r\n,]+", str(raw or ""))
    result = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in result:
            result.append(item)
    return result[:100]


def update_job(job_id: str, **updates) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        job.update(updates)
        job["updated_at"] = time.time()
        return dict(job)


def job_cancelled(job_id: str) -> bool:
    with _lock:
        return bool((_jobs.get(job_id) or {}).get("cancel_requested"))


def stop_if_cancelled(job_id: str) -> None:
    if job_cancelled(job_id):
        raise InterruptedError("任务已停止")


def run_job(job_id: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        return
    try:
        update_job(job_id, status="running", progress=8, stage="读取账号任务", message="正在读取短链和原账号任务信息")
        add_log(job_id, "info", "读取菲律宾短链与原账号会话")
        stop_if_cancelled(job_id)
        snapshot = dict(job.get("_source_snapshot") or {})
        if not snapshot:
            snapshot = reg_bridge_get("/api/internal/ph-short/session", {"task_id": job["task_id"]})

        update_job(job_id, progress=20, stage="校验短链", message="正在核对 Checkout、币种和金额")
        stop_if_cancelled(job_id)
        short_url, processor, checkout_session_id = validate_short_url(snapshot.get("short_url"))
        # Existing extracted links may predate identity metadata in the extract
        # result. Recover the persisted Checkout context by session id before
        # constructing the payment payload, keeping the original identity and
        # sticky proxy instead of silently inventing a new UUID.
        saved_context = _lookup_checkout_context(checkout_session_id)
        if saved_context:
            for key in ("user_agent", "checkout_user_agent", "checkout_device_id", "checkout_chatgpt_session_id", "checkout_proxy"):
                if not str(snapshot.get(key) or "").strip() and str(saved_context.get(key) or "").strip():
                    snapshot[key] = saved_context[key]
            if not snapshot.get("session_cookies") and saved_context.get("session_cookies"):
                snapshot["session_cookies"] = dict(saved_context.get("session_cookies") or {})
            add_log(job_id, "info", "已从历史 Checkout 上下文恢复设备、会话和固定代理")
        currency = str(snapshot.get("currency") or "PHP").upper()
        country = str(snapshot.get("country") or snapshot.get("billing_country") or "PH").upper()
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise RuntimeError(f"短链币种无效：{currency or '未知'}")
        if country and not re.fullmatch(r"[A-Z]{2}", country):
            raise RuntimeError(f"短链地区无效：{country}")
        amount = int(snapshot.get("amount") or 0)
        if amount < 0:
            raise RuntimeError("短链金额不正确")

        update_job(job_id, progress=28, stage="检查账号环境", message="正在确认原任务仍有可用账号环境")
        stop_if_cancelled(job_id)
        has_account_context = bool(
            str(snapshot.get("access_token") or "").strip()
            or str(snapshot.get("session_token") or "").strip()
            or snapshot.get("session_cookies")
        )
        if not has_account_context:
            raise RuntimeError("原任务账号环境已失效，请先在账号页刷新后重新提取短链")

        user_proxy_pool = list(job.get("_proxies") or [])
        proxy_candidates: list[str] = []
        proxy_source = "user_pool" if user_proxy_pool else ""
        if user_proxy_pool:
            proxy_candidates.extend(user_proxy_pool)
        else:
            checkout_proxy = str(snapshot.get("checkout_proxy") or "").strip()
            if checkout_proxy:
                proxy_candidates.append(checkout_proxy)
                proxy_source = "checkout_sticky_us"
                add_log(job_id, "info", "Reusing the original US checkout session")
            if not str(job.get("_confirmation_token") or "").strip():
                for _ in range(2):
                    try:
                        generated = reg_bridge_get("/api/internal/ph-short/proxy", {"country": "US"})
                        candidate = str(generated.get("proxy") or "").strip()
                        if candidate and candidate not in proxy_candidates:
                            proxy_candidates.append(candidate)
                    except Exception as exc:
                        add_log(job_id, "warn", f"US fallback proxy fetch failed: {type(exc).__name__}")
            else:
                add_log(job_id, "info", "ConfirmationToken is locked to the original Checkout proxy")
        if not proxy_candidates:
            raise RuntimeError("No usable payment proxy is available")
        proxy = proxy_candidates[0]
        if not proxy_source:
            proxy_source = "auto_us"
        update_job(job_id, _selected_proxy=proxy, _proxy_source=proxy_source)
        payload = {
            "short_url": short_url,
            "processor_entity": processor,
            "checkout_session_id": checkout_session_id,
            "access_token": snapshot.get("access_token"),
            "chatgpt_account_id": str(snapshot.get("chatgpt_account_id") or ""),
            "session_cookies": snapshot.get("session_cookies") or {},
            "user_agent": str(snapshot.get("user_agent") or snapshot.get("checkout_user_agent") or ""),
            "checkout_device_id": str(snapshot.get("checkout_device_id") or ""),
            "checkout_chatgpt_session_id": str(snapshot.get("checkout_chatgpt_session_id") or ""),
            "email": str(snapshot.get("email") or job.get("account_email") or ""),
            "proxy": proxy,
            "proxies": proxy_candidates,
            "cards": list(job.get("_cards") or []),
            "saved_payment_method_id": str(job.get("_saved_payment_method_id") or ""),
            "confirmation_token": str(job.get("_confirmation_token") or ""),
            "preconfirmed_checkout": dict(job.get("_preconfirmed_checkout") or {}),
            "card_retry_count": int(job.get("_card_retry_count", 2)),
            "card_retry_delay": 1,
        }
        update_job(job_id, progress=36, stage="初始化 Stripe", message="正在初始化现有 PH Checkout")
        process = subprocess.Popen(
            [PAY153_PYTHON, PROTOCOL_HELPER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        with _lock:
            if job_id in _jobs:
                _jobs[job_id]["_process"] = process
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(payload, ensure_ascii=False))
        process.stdin.close()
        result = {}
        last_error = ""
        for line in process.stdout:
            stop_if_cancelled(job_id)
            text = line.strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except Exception:
                add_log(job_id, "info", text)
                continue
            if event.get("type") == "log":
                level = str(event.get("level") or "info")
                message = str(event.get("message") or "")
                add_log(job_id, level, message)
                lower = message.lower()
                if "oaics session resolved" in lower:
                    update_job(job_id, progress=48, stage="Resolve Checkout", message="Loading Stripe Elements context")
                elif "elements session initialized" in lower or "billing snapshot" in lower:
                    update_job(job_id, progress=62, stage="Initialize payment", message="Preparing the saved PaymentMethod")
                elif "paymentmethod created" in lower:
                    update_job(job_id, progress=74, stage="Prepare authorization", message="PaymentMethod is ready for Checkout")
                elif "confirmationtoken created" in lower:
                    update_job(job_id, progress=82, stage="Submit payment", message="Submitting the Checkout confirmation")
                elif "payment succeeded" in lower:
                    update_job(job_id, progress=96, stage="Payment complete", message="Payment succeeded; finalizing authorization")
                elif "post-payment card binding status" in lower:
                    update_job(job_id, progress=98, stage="Finalize", message="Checking the final account binding")
                elif "checkout confirm returned" in lower:
                    update_job(job_id, progress=90, stage="Checkout confirmed", message="Confirming the Stripe Intent")
                elif "submit chatgpt checkout approval" in lower:
                    update_job(job_id, progress=94, stage="Checkout approval", message="Submitting final approval and confirming again")
            elif event.get("type") == "result":
                result = dict(event.get("result") or {})
            elif event.get("type") == "error":
                last_error = str(event.get("error") or "")
        code = process.wait(timeout=10)
        with _lock:
            if job_id in _jobs:
                _jobs[job_id]["_process"] = None
        if code != 0 or not result:
            clean_error = last_error or f"Protocol helper exited with code {code}"
            if clean_error.startswith("RuntimeError: "):
                clean_error = clean_error[len("RuntimeError: "):]
            raise RuntimeError(clean_error)
        result.update({
            "short_url": short_url,
            "processor_entity": processor,
            "checkout_session_id": checkout_session_id,
            "amount_minor": amount,
            "currency": currency,
            "billing_country": country or str((snapshot.get("billing_details") or {}).get("address", {}).get("country") or "").upper(),
            "account_email": str(snapshot.get("email") or job.get("account_email") or ""),
            "protocol_mode": "stripe_checkout_protocol",
        })
        if result.get("status") == "verification_required":
            update_job(
                job_id,
                status="verification_required",
                progress=92,
                stage="等待支付验证",
                message="支付方式需要额外验证，完成后点击继续确认",
                result=result,
                _resume_payload={
                    "mode": "resume",
                    "checkout_session_id": checkout_session_id,
                    "stripe_publishable_key": str(result.get("stripe_publishable_key") or ""),
                    "intent_type": str(result.get("intent_type") or ""),
                    "intent_client_secret": str(result.get("intent_client_secret") or ""),
                    "proxy": proxy,
                    "session_cookies": snapshot.get("session_cookies") or {},
                },
            )
        else:
            update_job(job_id, status="ready", progress=100, stage="支付完成", message="菲律宾 Checkout 协议支付完成", result=result, finished_at=time.time())
    except InterruptedError as exc:
        with _lock:
            process = (_jobs.get(job_id) or {}).get("_process")
        if process and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
        update_job(
            job_id,
            status="cancelled",
            progress=100,
            stage="已停止",
            message=str(exc),
            finished_at=time.time(),
        )
    except Exception as exc:
        add_log(job_id, "error", f"{type(exc).__name__}: {exc}")
        update_job(
            job_id,
            status="error",
            progress=100,
            stage="执行失败",
            error=str(exc),
            message="任务执行失败",
            finished_at=time.time(),
        )
    finally:
        with _lock:
            status = str((_jobs.get(job_id) or {}).get("status") or "")
        if status != "verification_required":
            _release_job_account_run(_jobs, job_id)


def run_resume_job(job_id: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        payload = dict((job or {}).get("_resume_payload") or {})
        previous_result = dict((job or {}).get("result") or {})
    if not job or not payload:
        return
    try:
        update_job(job_id, status="running", progress=94, stage="确认验证结果", message="正在轮询原 Checkout 的最终状态", error="")
        process = subprocess.Popen(
            [PAY153_PYTHON, PROTOCOL_HELPER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        with _lock:
            if job_id in _jobs:
                _jobs[job_id]["_process"] = process
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(payload, ensure_ascii=False))
        process.stdin.close()
        result: dict = {}
        last_error = ""
        for line in process.stdout:
            stop_if_cancelled(job_id)
            text = line.strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except Exception:
                add_log(job_id, "info", text)
                continue
            if event.get("type") == "log":
                add_log(job_id, str(event.get("level") or "info"), str(event.get("message") or ""))
            elif event.get("type") == "result":
                result = dict(event.get("result") or {})
            elif event.get("type") == "error":
                last_error = str(event.get("error") or "")
        code = process.wait(timeout=10)
        with _lock:
            if job_id in _jobs:
                _jobs[job_id]["_process"] = None
        if code != 0 or not result:
            raise RuntimeError(last_error or f"协议支付续跑进程退出：{code}")
        merged = {**previous_result, **result}
        if result.get("status") == "verification_required":
            update_job(job_id, status="verification_required", progress=94, stage="等待支付验证", message="尚未检测到验证完成，可稍后再次继续确认", result=merged)
            return
        update_job(job_id, status="ready", progress=100, stage="支付完成", message="菲律宾 Checkout 已确认到账", result=merged, finished_at=time.time())
    except InterruptedError as exc:
        with _lock:
            process = (_jobs.get(job_id) or {}).get("_process")
        if process and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
        update_job(job_id, status="cancelled", progress=100, stage="已停止", message=str(exc), finished_at=time.time())
    except Exception as exc:
        add_log(job_id, "error", f"{type(exc).__name__}: {exc}")
        update_job(job_id, status="error", progress=100, stage="最终确认失败", error=str(exc), finished_at=time.time())
    finally:
        with _lock:
            status = str((_jobs.get(job_id) or {}).get("status") or "")
        if status != "verification_required":
            _release_job_account_run(_jobs, job_id)
def _sanitize_card_audit_text(value: object, limit: int = 300) -> str:
    text = str(value or "")[:limit]
    text = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[redacted-token]", text)
    text = re.sub(r"(?<!\d)\d{12,19}(?!\d)", "[redacted-card]", text)
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._-]+", "Bearer [redacted-token]", text)
    return text


def append_card_audit(event: str, **fields) -> dict:
    record = {
        "id": secrets.token_hex(8),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": _sanitize_card_audit_text(event, 80),
    }
    allowed = {
        "stage", "status", "account_index", "account_email", "type", "code",
        "decline_code", "message", "probe_id", "task_id", "payment_status",
        "card_brand", "card_last4",
    }
    for key in allowed:
        value = fields.get(key)
        if value in (None, ""):
            continue
        if key == "account_index":
            record[key] = max(0, min(100, int(value or 0)))
        elif key == "card_last4":
            digits = re.sub(r"\D", "", str(value or ""))[-4:]
            if digits:
                record[key] = digits
        else:
            record[key] = _sanitize_card_audit_text(value, 300 if key == "message" else 120)
    CARD_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _lock:
        if CARD_AUDIT_PATH.exists() and CARD_AUDIT_PATH.stat().st_size > 5 * 1024 * 1024:
            rows = CARD_AUDIT_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()[-4000:]
            CARD_AUDIT_PATH.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
        with CARD_AUDIT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line)
        try:
            CARD_AUDIT_PATH.chmod(0o600)
        except OSError:
            pass
    return record


def list_card_audit(limit: int = 100) -> list[dict]:
    count = max(1, min(500, int(limit or 100)))
    if not CARD_AUDIT_PATH.is_file():
        return []
    rows = CARD_AUDIT_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()[-count:]
    items = []
    for line in rows:
        try:
            value = json.loads(line)
        except Exception:
            continue
        if isinstance(value, dict):
            items.append(value)
    return items


def request_script_root(value: str | None) -> str:
    root = str(value or "").rstrip("/")
    return root




def _decode_access_token_claims(access_token: str) -> dict:
    token = str(access_token or "").strip()
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("AT_FORMAT_INVALID")
    segment = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(segment.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise ValueError("AT_PAYLOAD_INVALID") from exc
    if not isinstance(claims, dict):
        raise ValueError("AT_PAYLOAD_INVALID")
    return claims


def _external_at_identity(access_token: str) -> tuple[str, str, str]:
    claims = _decode_access_token_claims(access_token)
    auth = claims.get("https://api.openai.com/auth") or {}
    profile = claims.get("https://api.openai.com/profile") or {}
    if not isinstance(auth, dict):
        auth = {}
    if not isinstance(profile, dict):
        profile = {}
    account_id = str(auth.get("chatgpt_account_id") or claims.get("chatgpt_account_id") or claims.get("account_id") or "").strip()
    if not account_id:
        raise ValueError("AT_ACCOUNT_ID_MISSING")
    token_hash = hashlib.sha256(access_token.encode("utf-8")).hexdigest()
    email = str(profile.get("email") or claims.get("email") or "").strip().lower()
    if not email or "@" not in email:
        email = f"external-{token_hash[:16]}@example.com"
    record_id = hashlib.sha256(("external-at:" + account_id).encode("utf-8")).hexdigest()[:12]
    return record_id, email, account_id


def ensure_external_at_account(access_token: str) -> str:
    token = str(access_token or "").strip()
    if len(token) < 300 or len(token) > 30000:
        raise ValueError("AT_FORMAT_INVALID")
    _record_id, email, _account_id = _external_at_identity(token)
    # External ATs are decoded in memory only. They are never appended to the
    # app.example.com account archive.
    return email


def resolve_card_bind_email(body: dict | None = None) -> str:
    body = body or {}
    requested_id = str(body.get("record_id") or request.args.get("record_id") or "").strip()
    requested_email = str(body.get("email") or request.args.get("email") or "").strip().lower()
    requested_at = str(body.get("access_token") or body.get("at") or request.args.get("access_token") or "").strip()
    if not requested_id and not requested_email and not requested_at:
        return CARD_BIND_ACCOUNT_EMAIL
    matched_email = ""
    account_file = Path("/opt/account-service/success_accounts.jsonl")
    if account_file.exists():
        with account_file.open(encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                item_id = str(item.get("id") or "").strip()
                item_email = str(item.get("email") or "").strip().lower()
                item_at = str(item.get("access_token") or "").strip()
                if ((requested_id and item_id == requested_id) or
                    (requested_email and item_email == requested_email) or
                    (requested_at and item_at and hmac.compare_digest(requested_at, item_at))):
                    matched_email = item_email
                    break
    if matched_email:
        return matched_email
    if requested_at:
        return ensure_external_at_account(requested_at)
    raise ValueError("ACCOUNT_NOT_FOUND")


@app.get("/api/cdk/status")
def cdk_status():
    session = session_status(str(request.cookies.get(CARD_CDK_COOKIE) or ""))
    valid = bool(session and int(session.get("remaining_uses") or 0) > 0)
    return jsonify({"ok": True, "valid": valid, "session": session or {}})


@app.post("/api/cdk/activate")
def cdk_activate():
    body = request.get_json(silent=True) or {}
    try:
        token, detail = activate_code(str(body.get("code") or ""), str(request.headers.get("User-Agent") or ""))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    response = make_response(jsonify({"ok": True, "session": detail}))
    max_age = max(60, int(float(detail.get("expires_at") or time.time()) - time.time()))
    response.set_cookie(CARD_CDK_COOKIE, token, max_age=max_age, secure=True, httponly=True, samesite="Lax", path="/")
    return response


@app.post("/api/cdk/merge")
def cdk_public_merge():
    body = request.get_json(silent=True) or {}
    raw_codes = body.get("codes") or []
    if isinstance(raw_codes, str):
        raw_codes = [line.strip() for line in raw_codes.replace("\r", "").split("\n") if line.strip()]
    try:
        item = merge_codes(list(raw_codes))
        token, session = activate_code(str(item.get("code") or ""), str(request.headers.get("User-Agent") or ""))
    except (TypeError, ValueError, RuntimeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    response = make_response(jsonify({"ok": True, "item": item, "session": session}))
    max_age = max(60, int(float(session.get("expires_at") or time.time()) - time.time()))
    response.set_cookie(CARD_CDK_COOKIE, token, max_age=max_age, secure=True, httponly=True, samesite="Lax", path="/")
    return response


@app.post("/api/cdk/tasks/query")
def cdk_task_query():
    body = request.get_json(silent=True) or {}
    raw_code = str(body.get("code") or "").strip()
    session = validate_session(str(request.cookies.get(CARD_CDK_COOKIE) or ""))
    try:
        detail = lookup_code_detail(raw_code) if raw_code else session
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not detail:
        return jsonify({"ok": False, "error": "CDK_REQUIRED"}), 401
    cdk_id = int(detail.get("id") or 0)
    with _lock:
        jobs = [dict(item) for item in _card_checkout_jobs.values() if int(item.get("_cdk_id") or 0) == cdk_id]
    jobs.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
    summary = {key: 0 for key in ("queued", "running", "done", "error", "cancelled")}
    for item in jobs:
        status = str(item.get("status") or "")
        summary[status] = summary.get(status, 0) + 1
    return jsonify({
        "ok": True, "cdk": detail, "summary": summary, "total": len(jobs),
        "items": [_card_checkout_public(item) for item in jobs[:300]],
    })


@app.post("/api/cdk/merge-lookup")
def cdk_merge_lookup():
    body = request.get_json(silent=True) or {}
    try:
        result = lookup_merge_chain(str(body.get("code") or ""))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, **result})


@app.get("/cdk-admin/")
def cdk_admin_page():
    return render_template("cdk-admin.html")


@app.get("/api/cdk-admin/codes")
def cdk_admin_codes():
    return jsonify({"ok": True, "items": list_codes()})


@app.post("/api/cdk-admin/codes")
def cdk_admin_create():
    body = request.get_json(silent=True) or {}
    try:
        items = create_codes(
            int(body.get("quantity") or 1), int(body.get("valid_days") or 30),
            int(body.get("max_activations") or 1), str(body.get("note") or ""),
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "items": items})


@app.post("/api/cdk-admin/merge")
def cdk_admin_merge():
    body = request.get_json(silent=True) or {}
    raw_codes = body.get("codes") or []
    if isinstance(raw_codes, str):
        raw_codes = [line.strip() for line in raw_codes.replace("\r", "").split("\n") if line.strip()]
    try:
        result = merge_codes(list(raw_codes))
    except (TypeError, ValueError, RuntimeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "item": result})


@app.patch("/api/cdk-admin/codes/<int:cdk_id>")
def cdk_admin_update(cdk_id: int):
    body = request.get_json(silent=True) or {}
    if "enabled" not in body:
        return jsonify({"ok": False, "error": "ENABLED_REQUIRED"}), 400
    if not set_enabled(cdk_id, bool(body.get("enabled"))):
        return jsonify({"ok": False, "error": "CDK_NOT_FOUND"}), 404
    return jsonify({"ok": True})


@app.delete("/api/cdk-admin/codes/<int:cdk_id>")
def cdk_admin_delete(cdk_id: int):
    if not delete_code(cdk_id):
        return jsonify({"ok": False, "error": "CDK_NOT_FOUND"}), 404
    return jsonify({"ok": True})


@app.get("/card-long/")
def card_long_page():
    return render_template("card-long-flow.html")


def _find_protocol_publishable_key(value) -> str:
    pattern = re.compile(r"pk_(?:live|test)_[A-Za-z0-9]{24,}")
    if isinstance(value, dict):
        for name in ("stripe_publishable_key", "publishable_key", "public_key", "key"):
            candidate = str(value.get(name) or "").strip()
            if pattern.fullmatch(candidate):
                return candidate
        for item in value.values():
            found = _find_protocol_publishable_key(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_protocol_publishable_key(item)
            if found:
                return found
    elif isinstance(value, str):
        match = pattern.search(value)
        if match:
            return match.group(0)
    return ""


def initialize_card_stripe_via_checkout(
    record_id: str,
    proxy_pool: list[str],
    access_token: str = "",
    account_run_lease_id: str = "",
) -> dict:
    # Creating the temporary Checkout is the initialization side effect.  Its
    # response key is optional because a fresh payment_method call follows it.
    token = str(access_token or "").strip()
    if not token:
        raise RuntimeError("AT_REQUIRED_FOR_CHECKOUT_KEY")
    status, created = _pay153_request("/api/checkout", "POST", {
        "token": token, "plan": "plus", "link_type": "ph_short", "country": "US",
        "entry_proxies": list(proxy_pool), "exit_proxies": list(proxy_pool),
        "entry_proxy_country": "US", "exit_proxy_country": "US", "use_promo": False,
        "promo_country": "US", "retry_count": 3, "use_sen": True, "use_so": True,
        "allow_missing_customer_session": True,
        "account_run_lease_id": str(account_run_lease_id or "").strip(),
    })
    pay_job_id = str((created or {}).get("job_id") or "")
    if status >= 400 or not pay_job_id:
        raise RuntimeError(str((created or {}).get("error") or "CHECKOUT_KEY_TASK_CREATE_FAILED"))
    observed_key = ""
    for _ in range(720):
        pstatus, progress = _pay153_request("/api/checkout-progress", params={"job_id": pay_job_id})
        if pstatus >= 400:
            raise RuntimeError(str((progress or {}).get("error") or "CHECKOUT_KEY_TASK_QUERY_FAILED"))
        progress = progress if isinstance(progress, dict) else {}
        state = str(progress.get("status") or "")
        observed_key = observed_key or _find_protocol_publishable_key(progress)
        if state == "done":
            return {
                "pay_job_id": pay_job_id,
                "publishable_key": observed_key or _find_protocol_publishable_key(progress.get("result") or {}),
                "result": progress.get("result") or {},
            }
        if state in {"error", "failed", "cancelled"}:
            raise RuntimeError(str(progress.get("error") or progress.get("message") or "CHECKOUT_KEY_TASK_FAILED"))
        time.sleep(1)
    raise RuntimeError("CHECKOUT_INITIALIZATION_TIMEOUT")


def _run_card_bind_session_helper(account_email: str, selected_proxy: str, access_token: str) -> tuple[int, dict]:
    completed = subprocess.run(
        [PAY153_PYTHON, CARD_BIND_HELPER, account_email, selected_proxy, access_token],
        capture_output=True, text=True, timeout=70, check=False,
    )
    try:
        payload = json.loads((completed.stdout or "{}").strip().splitlines()[-1])
    except Exception:
        stderr = str(completed.stderr or "").strip().splitlines()
        payload = {
            "ok": False,
            "error": "BIND_SESSION_INVALID_RESPONSE",
            "detail": (stderr[-1] if stderr else "helper returned no JSON")[:300],
        }
    return completed.returncode, payload


def _resolve_initialized_checkout_context(initialized: dict, access_token: str, selected_proxy: str, account_email: str) -> dict:
    result = initialized.get("result") if isinstance(initialized.get("result"), dict) else {}
    checkout_url = str(result.get("checkout_url") or result.get("short_link") or result.get("url") or "").strip()
    if not checkout_url:
        return {}
    try:
        _record_id, _email, account_id = _external_at_identity(access_token)
    except ValueError:
        account_id = ""
    snapshot = {
        "short_url": checkout_url,
        "checkout_session_id": str(result.get("checkout_session_id") or result.get("checkoutId") or ""),
        "processor_entity": str(result.get("processor_entity") or result.get("processorEntity") or "openai_llc"),
        "access_token": access_token,
        "chatgpt_account_id": account_id,
        "session_cookies": result.get("session_cookies") if isinstance(result.get("session_cookies"), dict) else {},
        "user_agent": str(result.get("checkout_user_agent") or result.get("user_agent") or ""),
        "checkout_device_id": str(result.get("checkout_device_id") or ""),
        "checkout_chatgpt_session_id": str(result.get("checkout_chatgpt_session_id") or ""),
        "email": account_email,
        "checkout_proxy": selected_proxy,
        "proxy": selected_proxy,
    }
    completed = subprocess.run(
        [PAY153_PYTHON, CARD_CHECKOUT_CONTEXT_HELPER],
        input=json.dumps(snapshot, ensure_ascii=False), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=90, check=False,
    )
    try:
        payload = json.loads((completed.stdout or "{}").strip().splitlines()[-1])
    except Exception:
        return {}
    return payload if completed.returncode == 0 and payload.get("ok") else {}


def _key_probe_public(job: dict) -> dict:
    return {
        "id": job.get("id"), "status": job.get("status"), "progress": int(job.get("progress") or 0),
        "message": job.get("message") or "", "error": job.get("error") or "",
        "publishable_key": job.get("publishable_key") or "",
        "session": dict(job.get("session") or {}),
    }


def _run_key_probe(probe_id: str):
    with _lock:
        job = dict(_key_probe_jobs.get(probe_id) or {})
    if not job:
        return
    try:
        token = str(job.get("_access_token") or "")
        account_email = str(job.get("_account_email") or "")
        selected_proxy = str(job.get("_selected_proxy") or "")
        proxy_pool = [selected_proxy] + [
            str(item or "") for item in list(job.get("_proxies") or [])
            if str(item or "") and str(item or "") != selected_proxy
        ]
        with _lock:
            _key_probe_jobs[probe_id].update(
                status="running", progress=12,
                message="Creating a temporary Checkout to initialize Stripe",
            )
        app.logger.info("key-probe %s: initialize checkout for %s", probe_id, account_email)
        # Keep the first initialization attempt on the exact proxy selected by
        # card-bind/session. This avoids creating the Checkout on one sticky
        # identity and refetching payment_method on another.
        initialized = initialize_card_stripe_via_checkout(
            job.get("record_id") or "", [selected_proxy] if selected_proxy else proxy_pool, token,
            account_run_lease_id=probe_id,
        )
        initialized_key = str(initialized.get("publishable_key") or "")
        initialized_context = {}
        if not initialized_key.startswith("pk_"):
            initialized_context = _resolve_initialized_checkout_context(
                initialized, token, selected_proxy, account_email,
            )
            initialized_key = str(initialized_context.get("publishable_key") or "")
            if initialized_key.startswith("pk_"):
                app.logger.info("key-probe %s: publishable key resolved from generated Checkout context", probe_id)
        last_detail = ""
        refreshed = {}
        resolved_key = ""
        # payment_method propagation is not immediate for accounts that never
        # created a Checkout. Retry with a short backoff instead of turning the
        # first transient 400/403/502 into a public 502.
        # Keep the original identity while Stripe propagates the new session.
        # Rotating proxies on every poll can make a valid key look missing.
        for attempt in range(1, 17):
            if attempt <= 10 or not proxy_pool:
                probe_proxy = selected_proxy
            else:
                probe_proxy = proxy_pool[(attempt - 11) % len(proxy_pool)]
            with _lock:
                _key_probe_jobs[probe_id].update(
                    progress=min(96, 76 + attempt),
                    message=f"Checkout initialized; refreshing SetupIntent and Stripe key ({attempt}/16)",
                )
            returncode, candidate = _run_card_bind_session_helper(account_email, probe_proxy, token)
            candidate = candidate if isinstance(candidate, dict) else {}
            upstream = int(candidate.get("status") or 0)
            if returncode == 0 and candidate.get("ok") and str(candidate.get("client_secret") or "").startswith("seti_"):
                key = _find_protocol_publishable_key(candidate) or initialized_key
                if str(key).startswith("pk_"):
                    refreshed = candidate
                    resolved_key = key
                    break
                last_detail = "SetupIntent ready but publishable key is still propagating"
            else:
                last_detail = str(candidate.get("detail") or candidate.get("error") or f"HTTP {upstream or 'unknown'}")
                if upstream == 401:
                    raise RuntimeError("AT_INVALIDATED_OR_EXPIRED")
            app.logger.info(
                "key-probe %s: refetch %s/16 pending, upstream=%s detail=%s",
                probe_id, attempt, upstream, last_detail[:160],
            )
            time.sleep(min(1.5 + attempt * 0.35, 6))
        if not refreshed:
            raise RuntimeError(f"BIND_SESSION_REFETCH_EXHAUSTED: {last_detail or 'Stripe context did not propagate'}")
        if job.get("_billing_details"):
            refreshed["billing_details"] = dict(job["_billing_details"])
        refreshed["publishable_key"] = resolved_key
        refreshed["publishable_key_source"] = "checkout_then_refetch"
        refreshed["pending"] = False
        with _lock:
            _key_probe_jobs[probe_id].update(
                status="done", progress=100,
                message="Fresh SetupIntent and Stripe key resolved",
                publishable_key=resolved_key, session=refreshed,
                _access_token="", finished_at=time.time(),
            )
        append_card_audit("stripe-key-probe", stage="加载卡片会话", status="succeeded", account_email=account_email, probe_id=probe_id, message="Stripe publishable key resolved")
        app.logger.info("key-probe %s: resolved for %s", probe_id, account_email)
    except Exception as exc:
        display_error = str(exc)
        if display_error.startswith("RuntimeError: "):
            display_error = display_error[len("RuntimeError: "):]
        append_card_audit("stripe-key-probe", stage="加载卡片会话", status="failed", account_email=account_email, probe_id=probe_id, type=type(exc).__name__, message=str(exc))
        app.logger.warning("key-probe %s failed: %s: %s", probe_id, type(exc).__name__, exc)
        with _lock:
            if probe_id in _key_probe_jobs:
                _key_probe_jobs[probe_id].update(
                    status="error", progress=100,
                    message="Stripe initialization/refetch failed",
                    error=display_error[:500],
                    _access_token="", finished_at=time.time(),
                )
    finally:
        _release_account_run(job.get("_account_run_lock"))


def _start_key_probe(record_id: str, account_email: str, selected_proxy: str, access_token: str, proxies: list[str], initial_session: dict | None = None, billing_details: dict | None = None) -> dict:
    probe_id = secrets.token_hex(6); now = time.time()
    account_run_lock = _acquire_account_run(access_token, probe_id, "加载卡片会话")
    job = {"id": probe_id, "record_id": record_id, "status": "queued", "progress": 2, "message": "Stripe initialization queued", "error": "", "publishable_key": "", "session": dict(initial_session or {}), "created_at": now, "finished_at": None, "_account_email": account_email, "_selected_proxy": selected_proxy, "_access_token": access_token, "_proxies": list(proxies), "_billing_details": dict(billing_details or {}), "_account_run_lock": account_run_lock}
    with _lock:
        _key_probe_jobs[probe_id] = job
        if len(_key_probe_jobs) > 100:
            finished = sorted((x for x in _key_probe_jobs.values() if x.get("status") in {"done", "error"}), key=lambda x: x.get("created_at", 0))
            for old in finished[:max(0, len(_key_probe_jobs)-100)]: _key_probe_jobs.pop(str(old.get("id") or ""), None)
    threading.Thread(target=_run_key_probe, args=(probe_id,), daemon=True, name=f"key-probe-{probe_id}").start()
    return job


@app.get("/api/card-bind/key-probe/<probe_id>")
def card_bind_key_probe(probe_id: str):
    value = str(probe_id or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{12}", value):
        return jsonify({"ok": False, "error": "INVALID_KEY_PROBE_ID"}), 400
    with _lock:
        job = _key_probe_jobs.get(value)
        payload = _key_probe_public(job) if job else None
    if not payload:
        return jsonify({"ok": False, "error": "KEY_PROBE_NOT_FOUND"}), 404
    return jsonify({"ok": True, "probe": payload})


@app.post("/api/card-bind/client-event")
def card_bind_client_event():
    body = request.get_json(silent=True) or {}
    stage = str(body.get("stage") or "unknown")[:80]
    event_type = str(body.get("type") or "")[:80]
    code = str(body.get("code") or "")[:80]
    decline_code = str(body.get("decline_code") or "")[:80]
    message = str(body.get("message") or "")[:300]
    message = re.sub(r"(?<!\d)\d{12,19}(?!\d)", "[redacted-card]", message)
    account_index = max(0, min(100, int(body.get("account_index") or 0)))
    record = append_card_audit(
        "client-card-flow", stage=stage, status=str(body.get("status") or "failed")[:40],
        account_index=account_index, account_email=str(body.get("account_email") or "")[:160],
        type=event_type, code=code, decline_code=decline_code, message=message,
        payment_status=str(body.get("payment_status") or "")[:80],
        card_brand=str(body.get("card_brand") or "")[:40], card_last4=str(body.get("card_last4") or "")[-4:],
    )
    app.logger.warning(
        "card-client-event account=%s stage=%s type=%s code=%s decline=%s message=%s",
        account_index, stage, event_type, code, decline_code, message,
    )
    return jsonify({"ok": True, "audit_id": record["id"]})


@app.get("/api/card-bind/audit")
def card_bind_audit():
    try:
        limit = int(request.args.get("limit") or 100)
    except ValueError:
        limit = 100
    return jsonify({"ok": True, "items": list_card_audit(limit)})


def _normalize_proxy_protocol(value: str | None) -> str:
    scheme = str(value or "http").strip().lower()
    if scheme == "socks":
        scheme = "socks5h"
    if scheme not in {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}:
        raise ValueError("UNSUPPORTED_PROXY_SCHEME")
    return scheme


def normalize_user_proxy(raw: str, default_scheme: str = "http", force_scheme: bool = False) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("PROXY_REQUIRED")
    default_scheme = _normalize_proxy_protocol(default_scheme)
    if "://" in value:
        parsed = urlsplit(value)
        scheme = default_scheme if force_scheme else parsed.scheme.lower()
        if scheme not in {"http", "https", "socks5", "socks5h"}:
            raise ValueError("UNSUPPORTED_PROXY_SCHEME")
        if not parsed.hostname or parsed.port is None:
            raise ValueError("PROXY_HOST_OR_PORT_MISSING")
        auth = ""
        if parsed.username is not None:
            auth = f"{quote(unquote(parsed.username), safe='')}:{quote(unquote(parsed.password or ''), safe='')}@"
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        return f"{scheme}://{auth}{host}:{parsed.port}"
    if value.count("@") == 1:
        left, right = value.split("@", 1)
        if left.count(":") == 1 and right.count(":") >= 1:
            username, password = left.split(":", 1)
            host, port = right.rsplit(":", 1)
        else:
            host, port = left.rsplit(":", 1)
            username, password = right.split(":", 1)
        if not port.isdigit():
            raise ValueError("PROXY_PORT_INVALID")
        return f"{default_scheme}://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"
    parts = value.split(":")
    if len(parts) >= 4 and parts[1].isdigit():
        host, port, username = parts[0], parts[1], parts[2]
        password = ":".join(parts[3:])
        return f"{default_scheme}://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"
    if len(parts) == 2 and parts[1].isdigit():
        return f"{default_scheme}://{value}"
    raise ValueError("PROXY_FORMAT_INVALID")


def normalize_user_proxy_pool(raw, default_scheme: str = "http", force_scheme: bool = False) -> list[str]:
    values = raw if isinstance(raw, list) else str(raw or "").replace("\r", "").split("\n")
    result = []
    seen = set()
    for index, item in enumerate(values, 1):
        text = str(item or "").strip()
        if not text:
            continue
        try:
            proxy = normalize_user_proxy(text, default_scheme=default_scheme, force_scheme=force_scheme)
        except Exception as exc:
            raise ValueError(f"PROXY_LINE_{index}: {exc}") from exc
        if proxy not in seen:
            seen.add(proxy); result.append(proxy)
    if not result:
        raise ValueError("PROXY_POOL_REQUIRED")
    if len(result) > 500:
        raise ValueError("PROXY_POOL_MAX_500")
    return result


def card_helper_error_response(payload: dict, fallback_status: int = 502):
    result = dict(payload or {})
    if not result.get("error"):
        result["error"] = "CARD_PROTOCOL_EMPTY_RESPONSE"
    if result.get("error") == "ACCOUNT_API_BASE_MISSING":
        result["message"] = "Card account API is not configured"
        return jsonify(result), 503
    upstream = int(result.get("status") or 0)
    if upstream == 401:
        result["error"] = "AT_INVALIDATED_OR_EXPIRED"
        result["upstream_status"] = upstream
        return jsonify(result), 422
    if upstream == 403:
        result["error"] = "UPSTREAM_ROUTE_BLOCKED"
        result["upstream_status"] = upstream
        return jsonify(result), 503
    if upstream == 429:
        result["error"] = result.get("error") or "UPSTREAM_RATE_LIMITED"
        return jsonify(result), 429
    if upstream >= 500:
        return jsonify(result), 503
    return jsonify(result), fallback_status


def normalize_billing_details(value: dict | None, account_email: str = "") -> dict:
    submitted = value if isinstance(value, dict) else {}
    address = submitted.get("address") if isinstance(submitted.get("address"), dict) else submitted
    return {
        "name": str(submitted.get("name") or "").strip(),
        "email": str(submitted.get("email") or account_email).strip(),
        "phone": str(submitted.get("phone") or "").strip(),
        "address": {
            "country": str(address.get("country") or "US").upper(),
            "line1": str(address.get("line1") or "").strip(),
            "line2": str(address.get("line2") or "").strip(),
            "city": str(address.get("city") or "").strip(),
            "state": str(address.get("state") or "").strip(),
            "postal_code": str(address.get("postal_code") or address.get("postalCode") or "").strip(),
        },
    }


@app.post("/api/card-bind/default")
def card_bind_default():
    body = request.get_json(silent=True) or {}
    payment_method_id = str(body.get("payment_method_id") or "").strip()
    if not re.fullmatch(r"pm_[A-Za-z0-9]+", payment_method_id):
        return jsonify({"ok": False, "error": "INVALID_PAYMENT_METHOD"}), 400
    try:
        account_email = resolve_card_bind_email(body)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    try:
        proxy_protocol = body.get("proxy_protocol") or body.get("proxyProtocol") or "http"
        selected_proxy = normalize_user_proxy(body.get("proxy"), proxy_protocol, force_scheme=bool(body.get("proxy_protocol") or body.get("proxyProtocol")))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    submitted_at = str(body.get("access_token") or body.get("at") or "").strip()
    account_run_lock = None
    try:
        if submitted_at:
            account_run_lock = _acquire_account_run(submitted_at, secrets.token_hex(6), "设置默认卡")
        completed = subprocess.run(
            [PAY153_PYTHON, CARD_DEFAULT_HELPER, account_email, payment_method_id, selected_proxy, submitted_at],
            capture_output=True, text=True, timeout=90, check=False,
        )
    except AccountRunBusy as exc:
        return jsonify({"ok": False, "code": "ACCOUNT_ALREADY_RUNNING", "error": str(exc)}), 409
    finally:
        _release_account_run(account_run_lock)
    try:
        payload = json.loads((completed.stdout or "{}").strip().splitlines()[-1])
    except Exception:
        payload = {"ok": False, "error": "SET_DEFAULT_INVALID_RESPONSE"}
    if completed.returncode != 0 or not payload.get("ok"):
        return card_helper_error_response(payload)
    return jsonify(payload)

@app.post("/api/card-flow/context")
def card_flow_context():
    body = request.get_json(silent=True) or {}
    task_id = str(body.get("task_id") or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{12}", task_id):
        return jsonify({"ok": False, "error": "Checkout task ID is invalid"}), 400
    account_run_lock = None
    try:
        try:
            snapshot = reg_bridge_get("/api/internal/ph-short/session", {"task_id": task_id})
        except RuntimeError:
            token = _extract_protocol_access_token(body.get("access_token"))
            source = reg_bridge_get("/api/internal/ph-short/tasks", {"limit": 100})
            item = next((entry for entry in source.get("items") or [] if entry.get("task_id") == task_id), {})
            snapshot = {**item, "access_token": token, "session_cookies": {}}
        account_run_lock = _acquire_account_run(str(snapshot.get("access_token") or ""), task_id, "读取 Checkout 上下文")
        completed = subprocess.run(
            [PAY153_PYTHON, CARD_CHECKOUT_CONTEXT_HELPER],
            input=json.dumps(snapshot, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
        try:
            payload = json.loads((completed.stdout or "{}").strip().splitlines()[-1])
        except Exception:
            payload = {"ok": False, "error": "Checkout context response is invalid"}
        if completed.returncode != 0 or not payload.get("ok"):
            detail = (completed.stderr or "").strip().splitlines()
            return jsonify({"ok": False, "error": payload.get("error") or (detail[-1] if detail else "Checkout context failed")}), 502
        return jsonify(payload)
    except AccountRunBusy as exc:
        return jsonify({"ok": False, "code": "ACCOUNT_ALREADY_RUNNING", "error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    finally:
        _release_account_run(account_run_lock)


@app.post("/api/card-flow/server-token")
def card_flow_server_token():
    body = request.get_json(silent=True) or {}
    task_id = str(body.get("task_id") or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{12}", task_id):
        return jsonify({"ok": False, "error": "Checkout task ID is invalid"}), 400
    account_run_lock = None
    try:
        try:
            snapshot = reg_bridge_get("/api/internal/ph-short/session", {"task_id": task_id})
        except RuntimeError:
            token = _extract_protocol_access_token(body.get("access_token"))
            source = reg_bridge_get("/api/internal/ph-short/tasks", {"limit": 100})
            item = next((entry for entry in source.get("items") or [] if entry.get("task_id") == task_id), {})
            snapshot = {**item, "access_token": token, "session_cookies": {}}
        account_run_lock = _acquire_account_run(str(snapshot.get("access_token") or ""), task_id, "创建服务器令牌")
        completed = subprocess.run(
            [PAY153_PYTHON, CARD_SERVER_TOKEN_HELPER],
            input=json.dumps(snapshot, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        lines = (completed.stdout or "").strip().splitlines()
        try:
            payload = json.loads(lines[-1] if lines else "{}")
        except Exception:
            payload = {"ok": False, "error": "Server ConfirmationToken response is invalid"}
        if completed.returncode != 0 or not payload.get("ok"):
            detail = (completed.stderr or "").strip().splitlines()
            error_code = str(payload.get("error_code") or "")
            status_code = 409 if error_code == "OFFER_NOT_ELIGIBLE" else 502
            return jsonify({
                "ok": False,
                "error_code": error_code,
                "error": payload.get("error") or (detail[-1] if detail else "Server ConfirmationToken failed"),
                "offer_check_ok": payload.get("offer_check_ok"),
                "offer_eligible": payload.get("offer_eligible"),
            }), status_code
        return jsonify(payload)
    except AccountRunBusy as exc:
        return jsonify({"ok": False, "code": "ACCOUNT_ALREADY_RUNNING", "error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    finally:
        _release_account_run(account_run_lock)


@app.get("/card-flow/")
def card_flow_page():
    return render_template("card-flow.html")



def _checkout_result_context(result: dict, entry_proxies: list[str], source: str = "") -> dict:
    checkout_url = str(result.get("checkout_url") or result.get("short_link") or result.get("url") or "").strip()
    session_id = str(result.get("checkout_session_id") or "").strip()
    processor = str(result.get("processor_entity") or "").strip()
    if checkout_url:
        try:
            checkout_url, parsed_processor, parsed_session = _normalize_protocol_checkout_url(checkout_url)
            processor = processor or parsed_processor
            session_id = session_id or parsed_session
        except ValueError:
            return {}
    if not session_id:
        return {}
    # Older isolated-card runs did not retain identity metadata. Derive a
    # stable compatibility tuple once from the Checkout session so retries do
    # not hit the missing-identity guard or rotate to a new UUID each time.
    compat_device = str(uuid.uuid5(uuid.NAMESPACE_URL, "automyai:checkout-device:" + session_id))
    compat_session = str(uuid.uuid5(uuid.NAMESPACE_URL, "automyai:checkout-session:" + session_id))
    checkout_user_agent = str(result.get("checkout_user_agent") or result.get("user_agent") or "").strip()
    if not checkout_user_agent:
        checkout_user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    try: attempt = max(1, int(result.get("attempt") or 1))
    except Exception: attempt = 1
    selected_proxy = ""
    if entry_proxies:
        selected_proxy = str(entry_proxies[(attempt - 1) % len(entry_proxies)] or "").strip()
    return {
        "checkout_session_id": session_id,
        "checkout_url": checkout_url,
        "processor_entity": processor or "openai_llc",
        "checkout_proxy": selected_proxy,
        "checkout_device_id": str(result.get("checkout_device_id") or compat_device),
        "checkout_chatgpt_session_id": str(result.get("checkout_chatgpt_session_id") or compat_session),
        "checkout_user_agent": checkout_user_agent,
        "country": str(result.get("country") or result.get("billing_country") or "").upper(),
        "currency": str(result.get("currency") or result.get("checkout_currency") or "").upper(),
        "created_at": time.time(),
        "source": source,
    }


def _read_checkout_context_rows(path=None) -> list[dict]:
    target = Path(path) if path is not None else CHECKOUT_CONTEXT_PATH
    if not target.exists():
        return []
    text = target.read_text(encoding="utf-8", errors="ignore")
    # Repair the previous writer, which emitted the two literal characters
    # backslash+n instead of JSONL line breaks. Without this, every new write
    # discarded all earlier Checkout identities and final payment lost the
    # original sticky proxy for every row except the last one.
    if "\\n" in text:
        text = text.replace("\\n", "\n")
    rows = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict) and item.get("checkout_session_id"):
            rows.append(item)
    return rows


def _persist_checkout_context(context: dict) -> None:
    if not context or not context.get("checkout_session_id"):
        return
    CHECKOUT_CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        rows = []
        for item in _read_checkout_context_rows():
            if str(item.get("checkout_session_id") or "") == str(context.get("checkout_session_id") or ""): continue
            if float(item.get("created_at") or 0) < time.time() - 7 * 86400: continue
            rows.append(item)
        rows.append(dict(context))
        CHECKOUT_CONTEXT_PATH.write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in rows[-1000:]), encoding="utf-8")


def _lookup_checkout_context(session_id: str) -> dict:
    target = str(session_id or "").strip()
    if not target: return {}
    candidates = []
    pay153_contexts = Path("/opt/payment-core/data/ph_short_contexts.jsonl")
    if pay153_contexts.exists():
        for line in pay153_contexts.read_text(encoding="utf-8", errors="ignore").splitlines():
            try: item = json.loads(line)
            except Exception: continue
            if isinstance(item, dict) and str(item.get("checkout_session_id") or "") == target:
                candidates.append(item)
    for item in _read_checkout_context_rows():
        if str(item.get("checkout_session_id") or "") == target:
            candidates.append(item)
    # Backfill links created by the older reg quick-checkout path.
    legacy_tasks = Path("/opt/account-service/account_tasks.jsonl")
    if legacy_tasks.exists():
        for line in legacy_tasks.read_text(encoding="utf-8", errors="ignore").splitlines():
            try: task = json.loads(line)
            except Exception: continue
            result = task.get("result") if isinstance(task.get("result"), dict) else {}
            if str(result.get("checkout_session_id") or "") != target: continue
            options = task.get("options") if isinstance(task.get("options"), dict) else {}
            entry = [str(item).strip() for item in (options.get("entry_proxies") or []) if str(item).strip()]
            context = _checkout_result_context(result, entry, "legacy_reg_task")
            context["created_at"] = float(task.get("finished_at") or task.get("created_at") or time.time())
            candidates.append(context)
    if not candidates: return {}
    context = dict(max(candidates, key=lambda item: float(item.get("created_at") or 0)))
    # Repair rows written by the old isolated-card adapter in memory and on
    # disk. This keeps the current already-extracted Checkout usable while
    # new runs now persist the exact runtime values above.
    target = str(context.get("checkout_session_id") or target)
    if target:
        changed = False
        defaults = {
            "checkout_device_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "automyai:checkout-device:" + target)),
            "checkout_chatgpt_session_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "automyai:checkout-session:" + target)),
            "checkout_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        }
        for key, value in defaults.items():
            if not str(context.get(key) or "").strip():
                context[key] = value
                changed = True
        if changed:
            _persist_checkout_context(context)
    return context


def _lookup_reg_account_session(email: str, account_id: str) -> dict:
    target_email = str(email or "").strip().lower()
    target_account = str(account_id or "").strip()
    source = Path("/opt/account-service/success_accounts.jsonl")
    if not source.exists():
        return {}
    matches = []
    for line in source.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        same_email = target_email and str(row.get("email") or "").strip().lower() == target_email
        same_account = target_account and str(row.get("account_id") or "").strip() == target_account
        if not (same_email or same_account):
            continue
        cookies = row.get("session_cookies") if isinstance(row.get("session_cookies"), dict) else {}
        if cookies:
            matches.append(row)
    if not matches:
        return {}
    latest = max(matches, key=lambda row: str(row.get("last_checked_at") or row.get("created_at") or ""))
    return {str(k): str(v) for k, v in dict(latest.get("session_cookies") or {}).items() if str(k) and str(v)}


def _pay153_request(path: str, method: str = "GET", payload: dict | None = None, params: dict | None = None) -> tuple[int, dict]:
    if path in {"/api/checkout", "/api/checkout-progress", "/api/checkout-cancel"}:
        return _current_protocol_request(path, method, payload, params)
    if not PAY153_INTERNAL_KEY:
        raise RuntimeError("PAY153_INTERNAL_KEY_MISSING")
    query = f"?{urlencode(params or {})}" if params else ""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"X-Pay153-Internal-Key": PAY153_INTERNAL_KEY, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(f"{PAY153_INTERNAL_BASE}{path}{query}", data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=35) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return int(response.status), json.loads(raw or "{}")
    except Exception as exc:
        status = int(getattr(exc, "code", 502) or 502)
        raw = ""
        if hasattr(exc, "read"):
            try: raw = exc.read().decode("utf-8", errors="replace")
            except Exception: raw = ""
        try: body = json.loads(raw or "{}")
        except Exception: body = {"error": raw[:300] or str(exc)}
        return status, body


def _legacy_checkout_result(value: dict) -> dict:
    outer = value if isinstance(value, dict) else {}
    public = outer.get("result") if isinstance(outer.get("result"), dict) else outer
    checkout_url = str(
        public.get("checkout_url")
        or public.get("short_link")
        or public.get("shortLink")
        or public.get("url")
        or public.get("checkoutUrl")
        or public.get("longUrl")
        or ""
    )
    checkout_id = str(public.get("checkoutId") or public.get("checkout_session_id") or "")
    processor = str(public.get("processorEntity") or public.get("processor_entity") or "")
    return {
        **public, "checkout_url": checkout_url, "short_link": checkout_url,
        "checkout_session_id": checkout_id, "processor_entity": processor,
        "attempt": int(outer.get("attempt") or 1),
    }


def _current_protocol_request(path: str, method: str, payload: dict | None, params: dict | None) -> tuple[int, dict]:
    headers = {"Accept": "application/json"}
    if CURRENT_PROTOCOL_PASSWORD:
        headers["X-Admin-Password"] = CURRENT_PROTOCOL_PASSWORD
    request_payload = payload if isinstance(payload, dict) else {}
    if path == "/api/checkout":
        target = "/api/card/jobs"
        request_payload = {
            "accessToken": request_payload.get("token"),
            "proxyPool1": request_payload.get("entry_proxies") or [],
            "proxyPool2": request_payload.get("exit_proxies") or [],
            "checkoutCountry": request_payload.get("country") or request_payload.get("checkout_country") or "PH",
            "checkoutCurrency": request_payload.get("currency") or request_payload.get("checkout_currency") or "PHP",
            "amountGate": "any_known", "maxAttempts": int(request_payload.get("retry_count") or 3),
            "accountRunLease": str(request_payload.get("account_run_lease_id") or "").strip(),
        }
        method = "POST"
    elif path == "/api/checkout-progress":
        target = f"/api/card/jobs/{quote(str((params or {}).get('job_id') or ''))}"
        method = "GET"
    else:
        target = f"/api/card/jobs/{quote(str(request_payload.get('job_id') or ''))}"
        method = "DELETE"
    data = json.dumps(request_payload, ensure_ascii=False).encode("utf-8") if method == "POST" else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(f"{CURRENT_PROTOCOL_BASE}{target}", data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=35) as response:
            status = int(response.status)
            body = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
    except Exception as exc:
        status = int(getattr(exc, "code", 502) or 502)
        raw = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        try: body = json.loads(raw or "{}")
        except Exception: body = {"error": raw[:300] or str(exc)}
        return status, body
    if path == "/api/checkout":
        return status, {**body, "job_id": str(body.get("id") or body.get("task_id") or "")}
    if path == "/api/checkout-progress":
        task = body.get("task") if isinstance(body.get("task"), dict) else body
        state = str(task.get("status") or "")
        if state == "ready":
            return status, {"status": "done", "percent": 100, "text": task.get("stage") or "Checkout completed", "result": _legacy_checkout_result(task.get("result") or {})}
        if state == "failed":
            return status, {"status": "error", "percent": 100, "error": task.get("error") or "Checkout failed"}
        percent = 12 if state == "queued" else 55
        return status, {"status": state or "running", "percent": percent, "text": task.get("stage") or "Checkout extraction running"}
    return status, body


def _extend_sticky_proxy_ttl(value: str, minutes: int = 120) -> str:
    proxy = str(value or "").strip()
    if not proxy:
        return proxy
    if re.search(r"-t-\d+", proxy):
        return re.sub(r"-t-\d+", f"-t-{int(minutes)}", proxy, count=1)
    return proxy


def _card_checkout_public(job: dict) -> dict:
    return {
        "id": job.get("id"), "task_id": job.get("id"), "status": job.get("status"),
        "progress": int(job.get("progress") or 0), "message": job.get("message") or "",
        "error": job.get("error") or "", "result": dict(job.get("result") or {}),
        "created_at": job.get("created_at"), "finished_at": job.get("finished_at"),
    }


def _run_isolated_card_checkout(task_id: str):
    with _lock: job = dict(_card_checkout_jobs.get(task_id) or {})
    if not job: return
    try:
        entry_country = str(job.get("_entry_proxy_country") or "US").upper()
        exit_country = str(job.get("_exit_proxy_country") or "TR").upper()
        with _lock: _card_checkout_jobs[task_id].update(status="running", progress=5, message=f"Validating {entry_country}/{exit_country} proxy pools")
        preflight = subprocess.run(
            [PAY153_PYTHON, PROXY_PREFLIGHT_HELPER],
            input=json.dumps({
                "entry": [_extend_sticky_proxy_ttl(item, 120) for item in job["_entry_proxies"]],
                "exit": [_extend_sticky_proxy_ttl(item, 120) for item in job["_exit_proxies"]],
                "entry_expected": entry_country,
                "exit_expected": exit_country,
            }, ensure_ascii=False),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45, check=False,
        )
        try: checked = json.loads((preflight.stdout or "{}").strip().splitlines()[-1])
        except Exception: checked = {}
        entry_valid = list(checked.get("entry") or [])
        exit_valid = list(checked.get("exit") or [])
        if not entry_valid or not exit_valid:
            raise RuntimeError(
                f"PROXY_PREFLIGHT_FAILED: {entry_country} {int(checked.get('entry_valid') or 0)}/{int(checked.get('entry_total') or 0)}, "
                f"{exit_country} {int(checked.get('exit_valid') or 0)}/{int(checked.get('exit_total') or 0)}"
            )
        with _lock: _card_checkout_jobs[task_id].update(status="running", progress=12, message=f"Proxy preflight passed: {entry_country} {len(entry_valid)}, {exit_country} {len(exit_valid)}")
        payload = {
            "token": job["_access_token"], "plan": "plus", "link_type": "ph_short",
            "country": job.get("_checkout_country") or "PH",
            "currency": job.get("_checkout_currency") or "PHP",
            "entry_proxies": entry_valid, "exit_proxies": exit_valid,
            "entry_proxy_country": entry_country, "exit_proxy_country": exit_country, "use_promo": True,
            "promo_country": exit_country, "retry_count": 10, "paired_proxy_rotation": True,
            "allow_missing_customer_session": True,
            "use_sen": True, "use_so": True,
        }
        status, created = _pay153_request("/api/checkout", "POST", payload)
        pay_job_id = str(created.get("job_id") or "")
        if status >= 400 or not pay_job_id:
            raise RuntimeError(str(created.get("error") or f"PAY153_HTTP_{status}"))
        with _lock: _card_checkout_jobs[task_id].update(progress=18, message="Checkout extraction running", _pay_job_id=pay_job_id)
        deadline = time.monotonic() + 15 * 60
        while time.monotonic() < deadline:
            status, progress = _pay153_request("/api/checkout-progress", params={"job_id": pay_job_id})
            if status >= 400: raise RuntimeError(str(progress.get("error") or f"PAY153_PROGRESS_HTTP_{status}"))
            state = str(progress.get("status") or "")
            with _lock:
                current = _card_checkout_jobs.get(task_id)
                if not current: return
                current.update(progress=min(96, max(18, int(progress.get("percent") or 0))), message=str(progress.get("text") or "Checkout extraction running"))
            if state == "done":
                result = progress.get("result") if isinstance(progress.get("result"), dict) else {}
                _persist_checkout_context(_checkout_result_context(result, entry_valid, "isolated_card_flow"))
                with _lock: _card_checkout_jobs[task_id].update(status="done", progress=100, message="Checkout completed", result=result, finished_at=time.time(), _access_token="")
                return
            if state in {"error", "cancelled"}: raise RuntimeError(str(progress.get("error") or progress.get("text") or "Checkout failed"))
            time.sleep(2)
        raise TimeoutError("CHECKOUT_TIMEOUT")
    except Exception as exc:
        with _lock:
            child_job_id = str((_card_checkout_jobs.get(task_id) or {}).get("_pay_job_id") or "")
        if child_job_id:
            try:
                _pay153_request("/api/checkout-cancel", "POST", {"job_id": child_job_id})
            except Exception:
                pass
        with _lock:
            if task_id in _card_checkout_jobs:
                current = _card_checkout_jobs[task_id]
                if current.get("status") != "cancelled":
                    current.update(status="error", progress=100, message="Checkout failed", error=f"{type(exc).__name__}: {exc}"[:500], finished_at=time.time(), _access_token="")
    finally:
        _release_account_run(job.get("_account_run_lock"))


@app.post("/api/card-flow/quick-checkout")
def public_card_flow_quick_checkout():
    body = request.get_json(silent=True) or {}
    cdk_session = validate_session(str(request.cookies.get(CARD_CDK_COOKIE) or ""))
    # The integrated AutoMyAI UI is already protected by the shared admin
    # session. CDK remains available on the public legacy mount only.
    if not cdk_session and shared_reg_session_valid():
        cdk_session = {"id": 0, "usage_count": 0, "remaining_uses": 100}
    if not cdk_session:
        return jsonify({"ok": False, "error": "LOGIN_REQUIRED"}), 401
    try:
        access_token = _extract_protocol_access_token(body.get("access_token") or body.get("at"))
        proxy_protocol = body.get("proxy_protocol") or body.get("proxyProtocol") or "http"
        force_proxy_protocol = bool(body.get("proxy_protocol") or body.get("proxyProtocol"))
        entry_proxy_pool = normalize_user_proxy_pool(body.get("entry_proxy_pool") or body.get("bind_proxy_pool") or body.get("proxy_pool"), proxy_protocol, force_proxy_protocol)
        exit_proxy_pool = normalize_user_proxy_pool(body.get("exit_proxy_pool") or body.get("promo_proxy_pool"), proxy_protocol, force_proxy_protocol)
        checkout_country = str(body.get("checkout_country") or body.get("checkoutCountry") or "PH").strip().upper()
        checkout_currency = str(body.get("checkout_currency") or body.get("checkoutCurrency") or "PHP").strip().upper()
        entry_proxy_country = str(body.get("entry_proxy_country") or body.get("entryProxyCountry") or "US").strip().upper()
        exit_proxy_country = str(body.get("exit_proxy_country") or body.get("exitProxyCountry") or "TR").strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", checkout_country):
            raise ValueError("Checkout 地区必须是 2 位国家代码")
        if not re.fullmatch(r"[A-Z]{3}", checkout_currency):
            raise ValueError("Checkout 币种必须是 3 位币种代码")
        if not re.fullmatch(r"[A-Z]{2}", entry_proxy_country) or not re.fullmatch(r"[A-Z]{2}", exit_proxy_country):
            raise ValueError("代理地区必须是 2 位国家代码")
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    task_id = secrets.token_hex(6); now = time.time()
    cdk_id = int(cdk_session.get("id") or 0)
    usage_baseline = int(cdk_session.get("usage_count") or 0)
    remaining_uses = int(cdk_session.get("remaining_uses") or 0)
    job = {"id": task_id, "status": "queued", "progress": 0, "message": "Task created", "error": "", "result": {}, "created_at": now, "finished_at": None, "_access_token": access_token, "_entry_proxies": entry_proxy_pool, "_exit_proxies": exit_proxy_pool, "_entry_proxy_country": entry_proxy_country, "_exit_proxy_country": exit_proxy_country, "_checkout_country": checkout_country, "_checkout_currency": checkout_currency, "_pay_job_id": "", "_cdk_id": cdk_id, "_cdk_usage_baseline": usage_baseline}
    with _lock:
        occupied = sum(
            1 for item in _card_checkout_jobs.values()
            if int(item.get("_cdk_id") or 0) == cdk_id
            and int(item.get("_cdk_usage_baseline", -1)) == usage_baseline
            and str(item.get("status") or "") not in {"error", "cancelled"}
        )
        if occupied >= remaining_uses:
            return jsonify({
                "ok": False, "error": "CDK_AT_LIMIT",
                "message": f"CDK remaining uses: {remaining_uses}; existing checkout links: {occupied}",
                "remaining_uses": remaining_uses, "occupied_links": occupied,
            }), 409
        _card_checkout_jobs[task_id] = job
    threading.Thread(target=_run_isolated_card_checkout, args=(task_id,), daemon=True, name=f"card-checkout-{task_id}").start()
    return jsonify({"ok": True, "task_id": task_id, "status": "queued", "remaining_uses": remaining_uses, "occupied_links": occupied + 1}), 201


@app.get("/api/card-flow/task/<task_id>")
def public_card_flow_task(task_id: str):
    value = str(task_id or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{12}", value):
        return jsonify({"ok": False, "error": "INVALID_TASK_ID"}), 400
    with _lock:
        isolated = _card_checkout_jobs.get(value)
        if isolated:
            return jsonify(_card_checkout_public(isolated))
    status, payload = reg_console_request(f"/api/tasks/{value}", "GET")
    if isinstance(payload, dict) and isinstance(payload.get("options"), dict):
        payload = dict(payload)
        payload["options"] = {
            key: val for key, val in payload["options"].items()
            if key not in {"entry_proxies", "exit_proxies", "proxies", "proxy_pool"}
        }
    return jsonify(payload), status


@app.post("/api/card-flow/task/<task_id>/cancel")
def public_card_flow_cancel(task_id: str):
    value = str(task_id or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{12}", value):
        return jsonify({"ok": False, "error": "INVALID_TASK_ID"}), 400
    with _lock:
        job = _card_checkout_jobs.get(value)
        child_job_id = str((job or {}).get("_pay_job_id") or "")
        if job:
            job.update(status="cancelled", progress=100, message="Task replaced by retry", error="", finished_at=time.time(), _access_token="")
    if child_job_id:
        try:
            _pay153_request("/api/checkout-cancel", "POST", {"job_id": child_job_id})
        except Exception:
            pass
    return jsonify({"ok": True, "task_id": value, "child_cancelled": bool(child_job_id)})


@app.post("/api/card-flow/tasks/clear")
def public_card_flow_clear_tasks():
    cdk_session = validate_session(str(request.cookies.get(CARD_CDK_COOKIE) or ""))
    if not cdk_session:
        return jsonify({"ok": False, "error": "CDK_REQUIRED"}), 401
    cdk_id = int(cdk_session.get("id") or 0)
    child_job_ids = []
    released = 0
    with _lock:
        for item in _card_checkout_jobs.values():
            if int(item.get("_cdk_id") or 0) != cdk_id:
                continue
            child_job_id = str(item.get("_pay_job_id") or "")
            if child_job_id:
                child_job_ids.append(child_job_id)
            item.update(
                status="cancelled", progress=100, message="Checkout slot released",
                error="", finished_at=time.time(), _access_token="",
            )
            released += 1
    cancelled_children = 0
    for child_job_id in dict.fromkeys(child_job_ids):
        try:
            _pay153_request("/api/checkout-cancel", "POST", {"job_id": child_job_id})
            cancelled_children += 1
        except Exception:
            pass
    return jsonify({
        "ok": True, "released": released, "child_cancelled": cancelled_children,
        "remaining_uses": int(cdk_session.get("remaining_uses") or 0),
    })


@app.get("/api/card-bind/config")
def card_bind_config():
    return jsonify({
        "ok": True,
        "requires_access_token": True,
        "account_api_configured": bool(CARD_ACCOUNT_API_BASE),
    })


@app.get("/api/billing-address")
def billing_address():
    # The main address provider may be temporarily retired (it currently
    # answers HTTP 410).  The card UI only needs a complete, internally
    # consistent billing profile to render and to let the operator continue;
    # keep a local fixture fallback so PP and card workspaces remain decoupled.
    def local_fallback(requested_state: str = "") -> dict:
        pools = {
            "AK": [("Anchorage", "99501", "1200 W Northern Lights Blvd")],
            "DE": [("Wilmington", "19801", "1200 N Market St")],
            "MT": [("Billings", "59101", "1200 Grand Ave")],
            "NH": [("Manchester", "03101", "1200 Elm St")],
            "OR": [("Portland", "97205", "1200 SW Morrison St")],
        }
        state_code = requested_state if requested_state in pools else secrets.choice(tuple(pools))
        city, postal_code, line1 = secrets.choice(pools[state_code])
        names = (("James", "Wilson"), ("Emma", "Davis"), ("Michael", "Brown"), ("Olivia", "Taylor"))
        first, last = secrets.choice(names)
        area_codes = {"AK": "907", "DE": "302", "MT": "406", "NH": "603", "OR": "503"}
        phone = f"+1{area_codes[state_code]}{secrets.randbelow(9000000) + 1000000}"
        email = f"{first.lower()}.{last.lower()}{secrets.randbelow(9000) + 1000}@outlook.com"
        return {
            "ok": True,
            "item": {
                "schema": "automyai.us-tax-free-address.v1",
                "source": {"provider": "automyai-local-fallback", "fallback": True, "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                "address": {"line1": line1, "line2": "", "city": city, "state": state_code, "stateName": {"AK":"Alaska","DE":"Delaware","MT":"Montana","NH":"New Hampshire","OR":"Oregon"}[state_code], "postalCode": postal_code, "country": "US", "formatted": f"{line1}, {city}, {state_code} {postal_code}, US"},
                "profile": {"name": f"{first} {last}", "firstName": first, "lastName": last, "email": email, "phone": phone},
                "billing": {"name": f"{first} {last}", "email": email, "phone": phone, "address": {"line1": line1, "line2": "", "city": city, "state": state_code, "postal_code": postal_code, "country": "US"}},
            },
        }
    state = str(request.args.get("state") or "").strip().upper()
    query = f"?state={quote(state)}" if state else ""
    headers = {"Accept": "application/json"}
    if CURRENT_PROTOCOL_PASSWORD:
        headers["X-Admin-Password"] = CURRENT_PROTOCOL_PASSWORD
    try:
        req = Request(f"{MAIN_API_BASE}/api/address-profiles/us-tax-free{query}", headers=headers)
        with urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
            return jsonify(payload), int(response.status)
    except Exception as exc:
        status = int(getattr(exc, "code", 502) or 502)
        raw = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        try:
            payload = json.loads(raw or "{}")
        except Exception:
            payload = {"ok": False, "error": str(exc)}
        # HTTP 410 is the retired upstream route observed in production.  Use
        # the local complete fixture for any upstream outage/invalid response,
        # while retaining a diagnostic marker for the UI and audit logs.
        if status in {410, 429, 500, 502, 503, 504} or not payload.get("item", {}).get("address"):
            fallback = local_fallback(state)
            fallback["item"]["source"]["upstreamError"] = payload.get("error") or str(exc)
            return jsonify(fallback), 200
        return jsonify(payload), status


@app.post("/api/card-bind/session")
def card_bind_session():
    body = request.get_json(silent=True) or {}
    try:
        account_email = resolve_card_bind_email(body)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    try:
        proxy_protocol = body.get("proxy_protocol") or body.get("proxyProtocol") or "http"
        force_proxy_protocol = bool(body.get("proxy_protocol") or body.get("proxyProtocol"))
        proxy_pool = normalize_user_proxy_pool(body.get("proxy_pool") or [body.get("proxy")], proxy_protocol, force_proxy_protocol)
        selected_proxy = normalize_user_proxy(body.get("proxy") or proxy_pool[0], proxy_protocol, force_scheme=force_proxy_protocol)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    submitted_at = str(body.get("access_token") or "").strip()
    submitted_billing = body.get("billing_details") or body.get("billingDetails")
    normalized_billing = normalize_billing_details(submitted_billing, account_email) if isinstance(submitted_billing, dict) else {}
    account_run_lock = None
    try:
        if submitted_at:
            account_run_lock = _acquire_account_run(submitted_at, secrets.token_hex(6), "读取卡片会话")
        returncode, payload = _run_card_bind_session_helper(account_email, selected_proxy, submitted_at)
    except AccountRunBusy as exc:
        return jsonify({"ok": False, "code": "ACCOUNT_ALREADY_RUNNING", "error": str(exc)}), 409
    finally:
        _release_account_run(account_run_lock)
    if returncode != 0 or not payload.get("ok"):
        if payload.get("error") == "ACCOUNT_API_BASE_MISSING":
            return card_helper_error_response(payload)
        upstream = int(payload.get("status") or 0)
        if submitted_at and str(body.get("checkout_url") or "").strip():
            return jsonify({
                "ok": True, "pending": True, "retry_existing_checkout": True,
                "account_email": account_email,
                "detail": str(payload.get("detail") or payload.get("error") or "SETUP_INTENT_PROPAGATING")[:300],
            }), 202
        # Accounts with no Checkout can answer 400/404/409, an empty-status
        # transport failure, 403, or 5xx. Any non-auth/non-rate-limit failure
        # with a submitted AT enters the initialization pipeline.
        if submitted_at and upstream not in {401, 429}:
            try:
                probe = _start_key_probe("", account_email, selected_proxy, submitted_at, proxy_pool, billing_details=normalized_billing)
            except AccountRunBusy as exc:
                return jsonify({"ok": False, "code": "ACCOUNT_ALREADY_RUNNING", "error": str(exc)}), 409
            return jsonify({
                "ok": True, "pending": True, "key_probe_id": str(probe.get("id") or ""),
                "account_email": account_email, "publishable_key_source": "checkout_then_refetch",
            }), 202
        return card_helper_error_response(payload)
    if not str(payload.get("publishable_key") or "").startswith("pk_"):
        if submitted_at and str(body.get("checkout_url") or "").strip():
            payload.update({
                "pending": True, "retry_existing_checkout": True,
                "publishable_key_source": "existing_checkout",
            })
            return jsonify(payload), 202
        try:
            probe = _start_key_probe(
                str(payload.get("record_id") or ""), account_email, selected_proxy,
                submitted_at, proxy_pool, initial_session=payload, billing_details=normalized_billing,
            )
        except AccountRunBusy as exc:
            return jsonify({"ok": False, "code": "ACCOUNT_ALREADY_RUNNING", "error": str(exc)}), 409
        payload.update({
            "pending": True,
            "key_probe_id": str(probe.get("id") or ""),
            "publishable_key_source": "checkout_then_refetch",
        })
        return jsonify(payload), 202
    if normalized_billing:
        payload["billing_details"] = normalized_billing
    payload["publishable_key_source"] = "access_token_protocol"
    return jsonify(payload)

@app.get("/")
def index():
    if request.script_root in {"/card-link", "/protocol-pay"}:
        return render_template("card-flow.html")
    return render_template("index.html")


@app.get("/api/source/tasks")
def source_tasks():
    try:
        payload = reg_bridge_get("/api/internal/ph-short/tasks", {"limit": 100})
    except (RuntimeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify({"ok": True, "items": payload.get("items") or []})


@app.get("/api/jobs")
def list_jobs():
    cleanup_jobs()
    with _lock:
        items = sorted(_jobs.values(), key=lambda item: item.get("created_at", 0), reverse=True)
        result = [public_job(item) for item in items]
    return jsonify({"ok": True, "items": result})


@app.post("/api/jobs")
def create_job():
    cleanup_jobs()
    payload = request.get_json(silent=True) or {}
    task_id = str(payload.get("task_id") or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{12}", task_id):
        return jsonify({"ok": False, "error": "请选择一个已完成的菲律宾短链任务"}), 400
    try:
        submitted_access_token = ""
        if str(payload.get("access_token") or "").strip():
            submitted_access_token = _extract_protocol_access_token(payload.get("access_token"))
        confirmation_token = str(payload.get("confirmation_token") or "").strip()
        if confirmation_token and not re.fullmatch(r"ctoken_[A-Za-z0-9]+", confirmation_token):
            raise ValueError("ConfirmationToken format is invalid")
        preconfirmed_checkout = payload.get("preconfirmed_checkout") or {}
        if not isinstance(preconfirmed_checkout, dict):
            raise ValueError("Preconfirmed Checkout response is invalid")
        saved_payment_method_id = str(payload.get("saved_payment_method_id") or "").strip()
        if saved_payment_method_id and not re.fullmatch(r"pm_[A-Za-z0-9]+", saved_payment_method_id):
            raise ValueError("Saved PaymentMethod format is invalid")
        cards = [] if (confirmation_token or saved_payment_method_id) else parse_cards(payload.get("cards") or payload.get("card_pool"))
        proxies = parse_proxies(payload.get("proxies") or payload.get("proxy_pool"))
        try:
            card_retry_count = int(payload.get("card_retry_count", 2))
        except (TypeError, ValueError) as exc:
            raise ValueError("\u91cd\u8bd5\u6b21\u6570\u5fc5\u987b\u662f\u6574\u6570") from exc
        card_retry_count = max(0, min(10, card_retry_count))
        source = reg_bridge_get("/api/internal/ph-short/tasks", {"limit": 100})
        source_item = next((item for item in source.get("items") or [] if item.get("task_id") == task_id), None)
        if not source_item:
            raise RuntimeError("短链任务不存在或尚未完成")
        source_snapshot = {}
        try:
            source_snapshot = reg_bridge_get("/api/internal/ph-short/session", {"task_id": task_id})
        except RuntimeError:
            if not submitted_access_token:
                raise RuntimeError("该历史短链的本地凭证已清理，请补充 AT 后继续")
            source_snapshot = {
                **source_item, "access_token": submitted_access_token,
                "session_cookies": {}, "chatgpt_account_id": "",
            }
    except (RuntimeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409

    with _lock:
        active = next(
            (
                item for item in _jobs.values()
                if item.get("task_id") == task_id and item.get("status") in {"queued", "running", "verification_required"}
            ),
            None,
        )
        if active:
            return jsonify({"ok": True, "job": public_job(active), "existing": True})
        job_id = secrets.token_hex(6)
        access_token = str(source_snapshot.get("access_token") or submitted_access_token or "").strip()
        try:
            account_run_lock = _acquire_account_run(access_token, job_id, "纸卡协议")
        except AccountRunBusy as exc:
            return jsonify({"ok": False, "code": "ACCOUNT_ALREADY_RUNNING", "error": str(exc)}), 409
        now = time.time()
        job = {
            "id": job_id,
            "task_id": task_id,
            "account_email": str(source_item.get("email") or ""),
            "status": "queued",
            "progress": 0,
            "stage": "等待执行",
            "message": "任务已创建",
            "error": "",
            "result": {},
            "logs": [],
            "cancel_requested": False,
            "created_at": now,
            "updated_at": now,
            "finished_at": None,
            "script_root": request.script_root,
            "_cards": cards,
            "_saved_payment_method_id": saved_payment_method_id,
            "_confirmation_token": confirmation_token,
            "_preconfirmed_checkout": preconfirmed_checkout,
            "_proxies": proxies,
            "_card_retry_count": card_retry_count,
            "_source_snapshot": source_snapshot,
            "_process": None,
            "_account_run_lock": account_run_lock,
        }
        _jobs[job_id] = job
    threading.Thread(target=run_job, args=(job_id,), name=f"ph-short-{job_id}", daemon=True).start()
    return jsonify({"ok": True, "job": public_job(job)}), 201


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
        result = public_job(job) if job else None
    if not result:
        return jsonify({"ok": False, "error": "任务不存在或已过期"}), 404
    return jsonify({"ok": True, "job": result})


@app.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "任务不存在或已过期"}), 404
        if job.get("status") in {"ready", "error", "cancelled"}:
            return jsonify({"ok": True, "job": public_job(job)})
        if job.get("status") == "verification_required" and not job.get("_process"):
            job.update({
                "status": "cancelled",
                "progress": 100,
                "stage": "已停止",
                "message": "任务已停止",
                "finished_at": time.time(),
                "updated_at": time.time(),
            })
            handle = job.pop("_account_run_lock", None)
            _release_account_run(handle)
            return jsonify({"ok": True, "job": public_job(job)})
        job["cancel_requested"] = True
        job["message"] = "正在停止任务"
        job["updated_at"] = time.time()
        result = public_job(job)
    return jsonify({"ok": True, "job": result})


@app.post("/api/jobs/<job_id>/resume")
def resume_job(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "任务不存在或已过期"}), 404
        if job.get("status") != "verification_required":
            return jsonify({"ok": False, "error": "当前任务不在等待验证状态"}), 409
        if not job.get("_resume_payload"):
            return jsonify({"ok": False, "error": "任务缺少验证续跑上下文"}), 409
        job["cancel_requested"] = False
        job["status"] = "queued"
        job["stage"] = "准备确认验证结果"
        job["message"] = "续跑任务已提交"
        job["updated_at"] = time.time()
        result = public_job(job)
    threading.Thread(target=run_resume_job, args=(job_id,), name=f"ph-resume-{job_id}", daemon=True).start()
    return jsonify({"ok": True, "job": result})


@app.delete("/api/jobs/<job_id>")
def delete_job(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"ok": True})
        if job.get("status") in {"queued", "running", "verification_required"}:
            return jsonify({"ok": False, "error": "运行中的任务请先停止"}), 409
        _jobs.pop(job_id, None)
    return jsonify({"ok": True})


@app.get("/jobs/<job_id>/open")
def open_checkout(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
        result = dict((job or {}).get("result") or {})
    short_url = str(result.get("short_url") or "")
    if not short_url:
        return redirect(f"{request.script_root}/?error=job_not_ready")
    return redirect(short_url, code=302)


@app.get("/healthz")
def healthz():
    cleanup_jobs()
    with _lock:
        active = sum(1 for item in _jobs.values() if item.get("status") in {"queued", "running", "verification_required"})
    return jsonify({
        "ok": True,
        "service": "reg153-ph-short-protocol",
        "mode": "stripe_checkout_protocol",
        "temporary_browser": False,
        "active_jobs": active,
        "jobs": len(_jobs),
    })


@app.get("/api/health")
def api_health():
    return healthz()



# --- Standalone saved-card payment: headless bootstrap, then protocol confirmation ---
def _extract_protocol_access_token(raw) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("AT_REQUIRED")
    if value.startswith("{"):
        try:
            obj = json.loads(value)
        except Exception as exc:
            raise ValueError("AT_JSON_INVALID") from exc
        def find_token(item):
            if isinstance(item, dict):
                for key in ("accessToken", "access_token", "token", "at"):
                    candidate = str(item.get(key) or "").strip()
                    if candidate.count(".") == 2 and candidate.startswith("eyJ"):
                        return candidate
                for child in item.values():
                    found = find_token(child)
                    if found:
                        return found
            elif isinstance(item, list):
                for child in item:
                    found = find_token(child)
                    if found:
                        return found
            return ""
        value = find_token(obj)
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if value.count(".") != 2 or not value.startswith("eyJ"):
        match = re.search(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", value)
        value = match.group(0) if match else ""
    if not value:
        raise ValueError("AT_FORMAT_INVALID")
    return value


def _protocol_token_identity(token: str) -> dict:
    try:
        segment = token.split(".")[1]
        segment += "=" * (-len(segment) % 4)
        claims = json.loads(base64.urlsafe_b64decode(segment.encode()).decode("utf-8"))
    except Exception as exc:
        raise ValueError("AT_PAYLOAD_INVALID") from exc
    auth = claims.get("https://api.openai.com/auth") or {}
    profile = claims.get("https://api.openai.com/profile") or {}
    account_id = str(auth.get("chatgpt_account_id") or auth.get("account_id") or "").strip()
    email = str(profile.get("email") or claims.get("email") or "").strip().lower()
    if not account_id:
        raise ValueError("AT_ACCOUNT_ID_MISSING")
    exp = int(claims.get("exp") or 0)
    if exp and exp <= int(time.time()):
        raise ValueError("AT_EXPIRED")
    return {"account_id": account_id, "email": email}


def _normalize_protocol_checkout_url(raw: str) -> tuple[str, str, str]:
    value = str(raw or "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise ValueError("CHECKOUT_HTTPS_REQUIRED")
    if parsed.hostname == "chatgpt.com":
        return validate_short_url(value)
    if parsed.hostname == "pay.openai.com":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 3 and parts[0] == "c" and parts[1] == "pay" and re.fullmatch(r"cs_[A-Za-z0-9_-]{12,}", parts[2]):
            session_id = parts[2]
            return f"https://chatgpt.com/checkout/openai_llc/{session_id}", "openai_llc", session_id
    raise ValueError("CHECKOUT_URL_UNSUPPORTED")


def _proxy_affinity_key(value: str) -> str:
    normalized = normalize_user_proxy(str(value or ""))
    # The extraction path extends only the sticky-session TTL. Treat that as
    # the same user-supplied proxy identity so final payment keeps the exact
    # Checkout route instead of silently falling back to a fresh session.
    return re.sub(r"-t-\d+", "-t-*", normalized, count=1)


def _protocol_public_job(job: dict) -> dict:
    return {
        "id": job.get("id"), "status": job.get("status"), "progress": int(job.get("progress") or 0),
        "stage": job.get("stage"), "message": job.get("message"), "error": job.get("error"),
        "result": dict(job.get("result") or {}), "logs": list(job.get("logs") or []),
        "cdk_usage": dict(job.get("cdk_usage") or {}),
        "account_email": job.get("account_email") or "", "checkout_url": job.get("checkout_url") or "",
        "created_at": job.get("created_at"), "updated_at": job.get("updated_at"), "finished_at": job.get("finished_at"),
    }


def _protocol_update(job_id: str, **updates):
    with _lock:
        job = _protocol_pay_jobs.get(job_id)
        if not job:
            return
        job.update(updates); job["updated_at"] = time.time()


def _protocol_log(job_id: str, level: str, message: str):
    text = str(message or "").strip()
    if not text:
        return
    with _lock:
        job = _protocol_pay_jobs.get(job_id)
        if not job:
            return
        logs = job.setdefault("logs", [])
        logs.append({"time": time.strftime("%H:%M:%S"), "type": level, "message": text[:600]})
        if len(logs) > 24:
            del logs[:-24]
        job["updated_at"] = time.time()


def _current_cdk_token() -> str:
    return str(request.cookies.get(CARD_CDK_COOKIE) or "").strip()


def _reserve_protocol_usage(event_key: str) -> bool:
    """Reserve public-CDK usage; integrated admin sessions need no CDK."""
    if shared_reg_session_valid():
        return False
    reserve_usage(_current_cdk_token(), event_key)
    return True


def _finalize_job_usage(job_id: str) -> None:
    """Finalize a non-batch payment use after the job reaches a terminal state."""
    with _lock:
        job = dict(_protocol_pay_jobs.get(job_id) or {})
    event_key = str(job.get("_cdk_usage_event") or "")
    if not event_key:
        return
    success = str(job.get("status") or "").lower() == "ready"
    detail = finalize_usage(event_key, success)
    if detail:
        _protocol_update(job_id, cdk_usage=detail)


def _finalize_batch_usage(usage_events: dict[str, str], job_ids: list[str]) -> None:
    """Consume one CDK use for each account whose payment succeeded."""
    deadline = time.time() + 1800
    terminal = {"ready", "error", "cancelled", "verification_required"}
    while time.time() < deadline:
        with _lock:
            jobs = [dict(_protocol_pay_jobs.get(job_id) or {}) for job_id in job_ids]
        if jobs and all(str(job.get("status") or "") in terminal for job in jobs):
            break
        time.sleep(0.5)
    with _lock:
        jobs = [dict(_protocol_pay_jobs.get(job_id) or {}) for job_id in job_ids]
    latest_detail = None
    for job_id, job in zip(job_ids, jobs):
        event_key = str(usage_events.get(job_id) or "")
        success = str(job.get("status") or "").lower() == "ready"
        detail = finalize_usage(event_key, success)
        if detail:
            latest_detail = detail
            _protocol_update(job_id, cdk_usage=detail)
    if latest_detail:
        for job_id in job_ids:
            _protocol_update(job_id, cdk_usage=latest_detail)


def _run_standalone_protocol_pay(job_id: str):
    with _lock:
        job = dict(_protocol_pay_jobs.get(job_id) or {})
    if not job:
        return
    try:
        _protocol_update(job_id, status="running", progress=8, stage="\u6821\u9a8c\u8d26\u53f7", message="\u6b63\u5728\u89e3\u6790 AT \u4e0e Checkout")
        origin = dict(job.get("_origin_context") or {})
        # Rehydrate identity for jobs created before the new metadata fields
        # were added.  The session id is the join key for the saved context.
        saved_context = _lookup_checkout_context(str(job.get("checkout_session_id") or ""))
        if saved_context:
            for key in ("checkout_user_agent", "user_agent", "checkout_device_id", "checkout_chatgpt_session_id", "checkout_proxy"):
                if not str(origin.get(key) or "").strip() and str(saved_context.get(key) or "").strip():
                    origin[key] = saved_context[key]
            if not origin.get("session_cookies") and saved_context.get("session_cookies"):
                origin["session_cookies"] = dict(saved_context.get("session_cookies") or {})
        origin_cookies = dict(origin.get("session_cookies") or {})
        account_cookies = _lookup_reg_account_session(job.get("account_email") or "", job.get("account_id") or "")
        merged_cookies = dict(account_cookies)
        merged_cookies.update(origin_cookies)
        _protocol_log(job_id, "info", f"Session context merged: account={len(account_cookies)} checkout={len(origin_cookies)} total={len(merged_cookies)}")
        snapshot = {
            "short_url": job["checkout_url"], "processor_entity": job["processor_entity"],
            "checkout_session_id": job["checkout_session_id"], "access_token": job["_access_token"],
            "chatgpt_account_id": job["account_id"], "session_cookies": merged_cookies,
            "user_agent": str(origin.get("checkout_user_agent") or origin.get("user_agent") or ""),
            "checkout_device_id": str(origin.get("checkout_device_id") or ""),
            "checkout_chatgpt_session_id": str(origin.get("checkout_chatgpt_session_id") or ""),
            "email": job.get("account_email") or "", "proxy": job["_proxies"][0],
            "checkout_proxy": job["_proxies"][0],
            "preserve_checkout_identity": bool(origin),
            "context_source": str(origin.get("source") or "generated"),
            "country": str(origin.get("country") or origin.get("billing_country") or "").upper(),
            "currency": str(origin.get("currency") or "").upper(),
            "billing_details": dict(job.get("_billing_details") or {}),
        }
        missing_identity = [key for key in ("user_agent", "checkout_device_id") if not str(snapshot.get(key) or "").strip()]
        if missing_identity:
            raise RuntimeError("CHECKOUT_IDENTITY_MISSING: " + ", ".join(missing_identity))
        proc = subprocess.Popen(
            [PAY153_PYTHON, STANDALONE_PAY_HELPER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        with _lock:
            if job_id in _protocol_pay_jobs:
                _protocol_pay_jobs[job_id]["_process"] = proc
        assert proc.stdin is not None and proc.stdout is not None
        helper_mode = "prepare" if job.get("_defer_confirm") else "run"
        proc.stdin.write(json.dumps({"mode": helper_mode, "snapshot": snapshot, "proxies": job["_proxies"]}, ensure_ascii=False)); proc.stdin.close()
        result = {}; last_error = ""
        for line in proc.stdout:
            text = line.strip()
            if not text: continue
            try: event = json.loads(text)
            except Exception:
                _protocol_log(job_id, "info", text); continue
            if event.get("type") == "log":
                message = str(event.get("message") or ""); phase = str(event.get("phase") or "")
                _protocol_log(job_id, str(event.get("level") or "info"), message)
                if phase == "validate": _protocol_update(job_id, progress=18, stage="\u68c0\u67e5\u5df2\u7ed1\u5361", message=message)
                elif phase == "headless": _protocol_update(job_id, progress=38, stage="Checkout \u521d\u59cb\u5316", message=message)
                elif phase == "headless_done": _protocol_update(job_id, progress=66, stage="\u534f\u8bae\u4e0a\u4e0b\u6587\u5c31\u7eea", message=message)
                elif phase == "protocol": _protocol_update(job_id, progress=76, stage="\u534f\u8bae\u786e\u8ba4", message=message)
                else:
                    lower = message.lower()
                    if "checkout confirm" in lower: _protocol_update(job_id, progress=84, stage="Checkout \u786e\u8ba4", message=message)
                    elif "stripe" in lower and "status=" in lower: _protocol_update(job_id, progress=93, stage="Stripe \u6700\u7ec8\u72b6\u6001", message=message)
            elif event.get("type") == "result": result = dict(event.get("result") or {})
            elif event.get("type") == "error": last_error = str(event.get("error") or "")
        code = proc.wait(timeout=10)
        if code != 0 or not result:
            raise RuntimeError(last_error or f"PAY_HELPER_EXIT_{code}")
        status = str(result.get("status") or "").lower()
        if status == "prepared" and job.get("_defer_confirm"):
            private_prepared = dict(result.get("prepared") or {})
            safe_result = dict(result.get("safe") or {})
            if not private_prepared:
                raise RuntimeError("PREPARED_CONTEXT_MISSING")
            _protocol_update(job_id, status="prepared", progress=72, stage="\u51c6\u5907\u5b8c\u6210", message="\u5df2\u5c31\u7eea\uff0c\u7b49\u5f85\u6240\u6709\u4efb\u52a1\u540c\u65f6\u63d0\u4ea4", result=safe_result, _prepared=private_prepared)
            append_card_audit("protocol-pay", stage="最后支付准备", status="succeeded", account_email=job.get("account_email") or "", task_id=job_id, payment_status="prepared")
            return
        if status == "verification_required":
            _protocol_update(job_id, status="verification_required", progress=94, stage="\u7b49\u5f85\u9a8c\u8bc1", message="\u652f\u4ed8\u9700\u8981\u989d\u5916\u9a8c\u8bc1", result=result)
            append_card_audit(
                "protocol-pay", stage="\u6700\u540e\u652f\u4ed8", status="verification_required",
                account_email=job.get("account_email") or "", task_id=job_id,
                payment_status="verification_required",
            )
        else:
            _protocol_update(job_id, status="ready", progress=100, stage="\u652f\u4ed8\u5b8c\u6210", message="\u7eaf\u534f\u8bae\u76f4\u5361\u652f\u4ed8\u5df2\u5b8c\u6210", result=result, finished_at=time.time())
            append_card_audit("protocol-pay", stage="最后支付", status="succeeded", account_email=job.get("account_email") or "", task_id=job_id, payment_status="ready")
    except Exception as exc:
        message = str(exc)
        if message.startswith("RuntimeError: "):
            message = message[14:]
        _protocol_log(job_id, "error", message)
        _protocol_update(job_id, status="error", progress=100, stage="\u6267\u884c\u5931\u8d25", message=message, error=message, finished_at=time.time())
        append_card_audit("protocol-pay", stage="最后支付准备" if job.get("_defer_confirm") else "最后支付", status="failed", account_email=job.get("account_email") or "", task_id=job_id, type=type(exc).__name__, message=message)
    finally:
        with _lock:
            final_status = str((_protocol_pay_jobs.get(job_id) or {}).get("status") or "")
        if final_status not in {"prepared"}:
            _release_job_account_run(_protocol_pay_jobs, job_id)
        with _lock:
            if job_id in _protocol_pay_jobs:
                _protocol_pay_jobs[job_id]["_process"] = None
                _protocol_pay_jobs[job_id].pop("_access_token", None)
                _protocol_pay_jobs[job_id].pop("_cards", None)
        _finalize_job_usage(job_id)


@app.get("/protocol-pay/")
def standalone_protocol_pay_page():
    return render_template("card-flow.html")


@app.post("/api/protocol-pay/jobs")
def create_standalone_protocol_pay_job():
    body = request.get_json(silent=True) or {}
    try:
        token = _extract_protocol_access_token(body.get("access_token") or body.get("at"))
        identity = _protocol_token_identity(token)
        raw_cards = body.get("cards") or body.get("card") or []
        cards = parse_cards(raw_cards) if raw_cards else []
        checkout_url, processor, session_id = _normalize_protocol_checkout_url(body.get("checkout_url") or body.get("url"))
        origin_context = _lookup_checkout_context(session_id)
        origin_proxy_raw = str(origin_context.get("checkout_proxy") or "")
        ttl_match = re.search(r"-t-(\d+)", origin_proxy_raw)
        origin_created = float(origin_context.get("created_at") or 0)
        if ttl_match and origin_created:
            ttl_seconds = max(60, int(ttl_match.group(1)) * 60)
            age_seconds = max(0, time.time() - origin_created)
            if age_seconds > max(60, ttl_seconds - 30):
                raise ValueError(
                    f"CHECKOUT_PROXY_SESSION_EXPIRED: link age {int(age_seconds // 60)}m exceeds sticky session {int(ttl_seconds // 60)}m; regenerate the Checkout link"
                )
        proxy_protocol = body.get("proxy_protocol") or body.get("proxyProtocol") or "http"
        submitted_proxy_raw = body.get("proxy_pool") or body.get("proxies") or []
        force_proxy_protocol = bool(body.get("proxy_protocol") or body.get("proxyProtocol"))
        submitted_proxies = normalize_user_proxy_pool(submitted_proxy_raw, proxy_protocol, force_proxy_protocol) if submitted_proxy_raw else []
        proxies = []
        origin_proxy = str(origin_context.get("checkout_proxy") or "").strip()
        if origin_proxy:
            normalized_origin = normalize_user_proxy(origin_proxy)
            # The original Checkout route remains first when it belongs to the
            # supplied pool. No new provider proxy is mixed into a user pool.
            submitted_affinities = {_proxy_affinity_key(item) for item in submitted_proxies}
            if not submitted_proxies or _proxy_affinity_key(normalized_origin) in submitted_affinities:
                proxies.append(normalized_origin)
        for candidate in submitted_proxies:
            if _proxy_affinity_key(candidate) not in {_proxy_affinity_key(item) for item in proxies}:
                proxies.append(candidate)
        proxy_errors = []
        if not submitted_proxies:
            for _ in range(3):
                try:
                    generated = reg_bridge_get("/api/internal/ph-short/proxy", {"country": "US"})
                    candidate = normalize_user_proxy(str(generated.get("proxy") or ""), proxy_protocol, force_scheme=force_proxy_protocol)
                    if candidate not in proxies:
                        proxies.append(candidate)
                except Exception as exc:
                    proxy_errors.append(str(exc))
        if not proxies:
            source = "USER_PROXY_POOL_EMPTY" if submitted_proxy_raw else "AUTO_PROXY_FAILED"
            raise RuntimeError(source + ": " + (proxy_errors[-1] if proxy_errors else "NO_PROXY_RETURNED"))
        checked = {}
        for preflight_attempt in range(1, 4):
            preflight = subprocess.run(
                [PAY153_PYTHON, PROXY_PREFLIGHT_HELPER],
                input=json.dumps({"proxies": proxies, "expected": "US", "limit": 3}, ensure_ascii=False),
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45, check=False,
            )
            try:
                checked = json.loads((preflight.stdout or "{}").strip().splitlines()[-1])
            except Exception:
                checked = {}
            if checked.get("proxies"):
                break
            if preflight_attempt < 3:
                time.sleep(preflight_attempt)
        proxies = list(checked.get("proxies") or [])
        if not proxies:
            raise RuntimeError(
                f"US_PROXY_PREFLIGHT_FAILED: {int(checked.get('valid') or 0)}/{int(checked.get('total') or 0)}; "
                f"protocol={proxy_protocol}; attempts=3"
            )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    job_id = secrets.token_hex(6); now = time.time()
    try:
        account_run_lock = _acquire_account_run(token, job_id, "最终 Checkout")
    except AccountRunBusy as exc:
        return jsonify({"ok": False, "code": "ACCOUNT_ALREADY_RUNNING", "error": str(exc)}), 409
    defer_confirm = bool(body.get("defer_confirm"))
    usage_event = ""
    if not defer_confirm:
        usage_event = f"payment:{job_id}"
        try:
            if not _reserve_protocol_usage(usage_event):
                usage_event = ""
        except ValueError as exc:
            _release_account_run(account_run_lock)
            return jsonify({"ok": False, "error": str(exc)}), 403
    job = {
        "id": job_id, "status": "queued", "progress": 0, "stage": "\u7b49\u5f85\u6267\u884c", "message": "\u4efb\u52a1\u5df2\u521b\u5efa",
        "error": "", "result": {}, "logs": [], "account_email": identity.get("email") or "",
        "account_id": identity["account_id"], "checkout_url": checkout_url, "processor_entity": processor,
        "checkout_session_id": session_id, "created_at": now, "updated_at": now, "finished_at": None,
        "_access_token": token, "_proxies": proxies, "_cards": cards, "_origin_context": origin_context, "_process": None,
        "_defer_confirm": defer_confirm, "_prepared": None, "_cdk_usage_event": usage_event,
        "_billing_details": body.get("billing_details") if isinstance(body.get("billing_details"), dict) else {},
        "_account_run_lock": account_run_lock,
    }
    with _lock:
        _protocol_pay_jobs[job_id] = job
        if len(_protocol_pay_jobs) > 100:
            finished = sorted((x for x in _protocol_pay_jobs.values() if x.get("status") in {"ready","error","cancelled"}), key=lambda x:x.get("created_at",0))
            for old in finished[:max(0,len(_protocol_pay_jobs)-100)]: _protocol_pay_jobs.pop(str(old.get("id")),None)
    threading.Thread(target=_run_standalone_protocol_pay, args=(job_id,), daemon=True, name=f"protocol-pay-{job_id}").start()
    return jsonify({"ok": True, "job": _protocol_public_job(job)}), 201


def _run_prepared_protocol_confirm(job_id: str, gate: threading.Event, burst_count: int = 10) -> None:
    with _lock:
        job = dict(_protocol_pay_jobs.get(job_id) or {})
    if not job or not job.get("_prepared"):
        return
    proc = None
    try:
        _protocol_update(job_id, status="queued", progress=78, stage="\u7b49\u5f85\u540c\u6b65\u5c4f\u969c", message=f"\u5355\u8fdb\u7a0b\u5df2\u9884\u70ed\uff0c\u51c6\u5907 {burst_count} \u8def\u540c\u6b65\u786e\u8ba4")
        proc = subprocess.Popen(
            [PAY153_PYTHON, STANDALONE_PAY_HELPER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        with _lock:
            if job_id in _protocol_pay_jobs:
                _protocol_pay_jobs[job_id]["_process"] = proc
        gate.wait(timeout=30)
        _protocol_update(job_id, status="running", progress=82, stage="\u540c\u65f6\u63d0\u4ea4\u8ba2\u9605", message=f"{burst_count} \u8def\u786e\u8ba4\u5df2\u540c\u65f6\u653e\u884c")
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps({"mode":"confirm_burst","prepared":job["_prepared"],"burst_count":burst_count},ensure_ascii=False));proc.stdin.close()
        result={};last_error=""
        for line in proc.stdout:
            text=line.strip()
            if not text:continue
            try:event=json.loads(text)
            except Exception:continue
            if event.get("type")=="log":
                message=str(event.get("message") or "");_protocol_log(job_id,str(event.get("level") or "info"),message)
                if "checkout confirm" in message.lower():_protocol_update(job_id,progress=90,stage="Checkout \u786e\u8ba4",message=message)
            elif event.get("type")=="result":result=dict(event.get("result") or {})
            elif event.get("type")=="error":last_error=str(event.get("error") or "")
        code=proc.wait(timeout=10)
        if code!=0 or not result:raise RuntimeError(last_error or f"PAY_HELPER_EXIT_{code}")
        if str(result.get("status") or "").lower()=="verification_required":
            _protocol_update(job_id,status="verification_required",progress=94,stage="\u7b49\u5f85\u9a8c\u8bc1",message="\u652f\u4ed8\u9700\u8981\u989d\u5916\u9a8c\u8bc1",result=result,finished_at=time.time())
            append_card_audit("protocol-pay",stage="同步最后支付",status="verification_required",account_email=job.get("account_email") or "",task_id=job_id,payment_status="verification_required")
        else:
            _protocol_update(job_id,status="ready",progress=100,stage="\u8ba2\u9605\u5b8c\u6210",message=f"{burst_count} \u8def\u540c\u6b65\u786e\u8ba4\u5df2\u5b8c\u6210",result=result,finished_at=time.time())
            append_card_audit("protocol-pay",stage="同步最后支付",status="succeeded",account_email=job.get("account_email") or "",task_id=job_id,payment_status="ready")
    except Exception as exc:
        message=str(exc)
        _protocol_log(job_id,"error",message);_protocol_update(job_id,status="error",progress=100,stage="\u6267\u884c\u5931\u8d25",message=message,error=message,finished_at=time.time());append_card_audit("protocol-pay",stage="同步最后支付",status="failed",account_email=job.get("account_email") or "",task_id=job_id,type=type(exc).__name__,message=message)
    finally:
        _release_job_account_run(_protocol_pay_jobs, job_id)
        with _lock:
            if job_id in _protocol_pay_jobs:
                _protocol_pay_jobs[job_id]["_process"]=None;_protocol_pay_jobs[job_id].pop("_prepared",None)

@app.post("/api/protocol-pay/batch-confirm")
def confirm_protocol_pay_batch():
    body=request.get_json(silent=True) or {};job_ids=[]
    try:burst_count=1
    except (TypeError,ValueError):burst_count=1
    for value in body.get("job_ids") or []:
        jid=str(value or "").strip()
        if jid and jid not in job_ids:job_ids.append(jid)
    if not job_ids or len(job_ids)>50:return jsonify({"ok":False,"error":"JOB_IDS_INVALID"}),400
    with _lock:
        missing=[jid for jid in job_ids if jid not in _protocol_pay_jobs]
        unready=[jid for jid in job_ids if jid in _protocol_pay_jobs and _protocol_pay_jobs[jid].get("status")!="prepared"]
    if missing:return jsonify({"ok":False,"error":"TASK_NOT_FOUND","job_ids":missing}),404
    if unready:return jsonify({"ok":False,"error":"TASKS_NOT_PREPARED","job_ids":unready}),409
    batch_key="batch:"+hashlib.sha256("|".join(sorted(job_ids)).encode("utf-8")).hexdigest()[:32]
    usage_events={jid:f"{batch_key}:{jid}" for jid in job_ids}
    reserved_events=[]
    try:
        for jid in job_ids:
            if _reserve_protocol_usage(usage_events[jid]):
                reserved_events.append(usage_events[jid])
            else:
                usage_events[jid] = ""
    except ValueError as exc:
        for reserved_event in reserved_events:
            finalize_usage(reserved_event,False)
        return jsonify({"ok":False,"error":str(exc)}),403
    gate=threading.Event()
    with _lock:
        for jid in job_ids:
            _protocol_pay_jobs[jid]["_cdk_usage_event"]=usage_events[jid]
    for jid in job_ids:
        threading.Thread(target=_run_prepared_protocol_confirm,args=(jid,gate,burst_count),daemon=True,name=f"protocol-confirm-{jid}").start()
    time.sleep(0.35);gate.set()
    threading.Thread(target=_finalize_batch_usage,args=(usage_events,list(job_ids)),daemon=True,name=f"cdk-usage-{batch_key[-8:]}").start()
    with _lock:jobs=[_protocol_public_job(_protocol_pay_jobs[jid]) for jid in job_ids]
    return jsonify({"ok":True,"jobs":jobs,"released":len(jobs),"mode":"simultaneous_burst","burst_count":burst_count})

@app.get("/api/protocol-pay/jobs/<job_id>")
def get_standalone_protocol_pay_job(job_id: str):
    with _lock:
        job = _protocol_pay_jobs.get(str(job_id or ""))
        payload = _protocol_public_job(job) if job else None
    if not payload:
        return jsonify({"ok": False, "error": "TASK_NOT_FOUND"}), 404
    return jsonify({"ok": True, "job": payload})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5088")), debug=False)
