#!/usr/bin/env python3
"""Standalone HTTP service for the PayPal protocol workbench."""
from __future__ import annotations

import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import secrets
from typing import Any
from urllib.parse import urlparse

from integrations.paypal_protocol import (
    delete_paypal_protocol_task,
    get_paypal_protocol_task,
    list_paypal_protocol_tasks,
    paypal_protocol_countries,
    paypal_protocol_status,
    prepare_paypal_protocol,
    start_paypal_protocol_task,
    submit_paypal_protocol_otp,
)
from integrations.account_run_guard import (
    AccountRunBusy,
    acquire_account_run,
    release_account_run,
)
from integrations.card_protocol import (
    card_protocol_status,
    delete_card_protocol_task,
    get_card_protocol_task,
    list_card_protocol_tasks,
    inspect_card_checkout_context,
    load_card_elements_context,
    preflight_card_protocol_proxies,
    start_card_protocol_task,
)


HOST = os.getenv("PAYPAL_PROTOCOL_HOST", "127.0.0.1")
PORT = int(os.getenv("PAYPAL_PROTOCOL_PORT", "18795"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_COOKIE_NAME = "automyai_admin"


def admin_session_token() -> str:
    if not ADMIN_PASSWORD:
        return ""
    return hmac.new(ADMIN_PASSWORD.encode("utf-8"), b"automyai-admin-session", hashlib.sha256).hexdigest()


class PayPalProtocolHandler(BaseHTTPRequestHandler):
    server_version = "automyai-paypal-protocol/1.0"

    def send_json(self, status: int, payload: Any) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        if length < 0 or length > 1024 * 1024:
            raise ValueError("请求体不能超过 1 MiB")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as error:
            raise ValueError("请求体必须是 JSON 对象") from error
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return payload

    def authenticated(self) -> bool:
        if not ADMIN_PASSWORD:
            return True
        header_password = self.headers.get("X-Admin-Password", "")
        if header_password and hmac.compare_digest(header_password, ADMIN_PASSWORD):
            return True
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        morsel = cookie.get(ADMIN_COOKIE_NAME)
        return bool(morsel) and hmac.compare_digest(morsel.value, admin_session_token())

    def require_auth(self) -> bool:
        if self.authenticated():
            return True
        self.send_json(401, {"error": "需要管理员密码", "authenticated": False})
        return False

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self.send_json(200, {"ok": True, "service": "paypal-protocol"})
            return
        if not self.require_auth():
            return
        routes = {
            "/api/status": paypal_protocol_status,
            "/api/countries": paypal_protocol_countries,
            "/api/card/status": card_protocol_status,
        }
        handler = routes.get(path)
        if path == "/api/jobs":
            self.send_json(200, {"ok": True, "items": list_paypal_protocol_tasks()})
            return
        if path.startswith("/api/jobs/"):
            task = get_paypal_protocol_task(path.rsplit("/", 1)[-1])
            if not task:
                self.send_json(404, {"error": "PP 协议任务不存在"})
                return
            self.send_json(200, {"ok": True, "task": task})
            return
        if path == "/api/card/jobs":
            self.send_json(200, {"ok": True, "items": list_card_protocol_tasks()})
            return
        if path.startswith("/api/card/jobs/"):
            task = get_card_protocol_task(path.rsplit("/", 1)[-1])
            if not task:
                self.send_json(404, {"error": "直卡协议任务不存在"})
                return
            self.send_json(200, {"ok": True, "task": task})
            return
        if not handler:
            self.send_json(404, {"error": "接口不存在"})
            return
        self.send_json(200, handler())

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self.require_auth():
            return
        handlers = {
            "/api/prepare": prepare_paypal_protocol,
            "/api/jobs": start_paypal_protocol_task,
            "/api/card/jobs": start_card_protocol_task,
            "/api/card/checkout-context": inspect_card_checkout_context,
            "/api/card/elements-context": load_card_elements_context,
            "/api/card/proxy-preflight": preflight_card_protocol_proxies,
        }
        handler = handlers.get(path)
        task_otp_match = re.fullmatch(r"/api/jobs/([^/]+)/otp", path)
        if task_otp_match:
            try:
                self.send_json(200, submit_paypal_protocol_otp(task_otp_match.group(1), self.read_json()))
            except KeyError as error:
                self.send_json(404, {"ok": False, "error": str(error)})
            except ValueError as error:
                self.send_json(400, {"ok": False, "error": str(error)})
            return
        if not handler:
            self.send_json(404, {"error": "接口不存在"})
            return
        try:
            payload = self.read_json()
            account_run_lock = None
            if path in {"/api/card/checkout-context", "/api/card/elements-context"}:
                token = str(payload.get("accessToken") or payload.get("access_token") or "")
                method = "读取 Checkout 上下文" if path.endswith("checkout-context") else "加载卡片框"
                account_run_lock = acquire_account_run(token, secrets.token_hex(6), method)
            try:
                self.send_json(200, handler(payload))
            finally:
                release_account_run(account_run_lock)
        except AccountRunBusy as error:
            self.send_json(409, {"ok": False, "code": "ACCOUNT_ALREADY_RUNNING", "error": str(error)})
        except ValueError as error:
            self.send_json(400, {"ok": False, "error": str(error)})
        except Exception as error:  # upstream protocol/context failure
            self.send_json(502, {"ok": False, "error": str(error)})

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self.require_auth():
            return
        if path.startswith("/api/jobs/"):
            if not delete_paypal_protocol_task(path.rsplit("/", 1)[-1]):
                self.send_json(409, {"error": "任务不存在或仍在运行"})
                return
            self.send_json(200, {"ok": True})
            return
        if not path.startswith("/api/card/jobs/"):
            self.send_json(404, {"error": "接口不存在"})
            return
        if not delete_card_protocol_task(path.rsplit("/", 1)[-1]):
            self.send_json(409, {"error": "任务不存在或仍在运行"})
            return
        self.send_json(200, {"ok": True})

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"paypal-protocol: {self.address_string()} - {format_string % args}", flush=True)


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), PayPalProtocolHandler)
    print(f"paypal-protocol listening on {HOST}:{PORT}", flush=True)
    server.serve_forever()
