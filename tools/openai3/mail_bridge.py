#!/usr/bin/env python3
"""OpenAI3 mail bridge: adapt local OutlookEmail API to chatgpt_register MailClient.

chatgpt_register expects:
  POST /api/login {"password": ...}
  GET  /api/random-unbound  -> plain email text
  GET  /api/verify-code?email=...&keyword=openai -> {"success":true,"code":"123456"}

This bridge uses the same OutlookEmail instance as AutoMyAI / Grok mail pool.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

for _parent in Path(__file__).resolve().parents:
    if (_parent / "integrations" / "openai3_control.py").is_file():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

from integrations.openai3_control import select_latest_verification_code

DATA = Path(os.environ.get("OPENAI3_DATA_DIR", "/opt/automyai/data/openai3"))
CFG_PATH = DATA / "config.json"
STATE_PATH = DATA / "mail_bridge_state.json"
_lock = threading.Lock()


def load_cfg() -> dict[str, str]:
    cfg = {}
    if CFG_PATH.is_file():
        try:
            cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    # env overrides
    return {
        "outlook_api_url": os.environ.get("OUTLOOK_EMAIL_API_URL") or cfg.get("outlook_api_url") or "http://127.0.0.1:5010",
        "outlook_api_key": os.environ.get("OUTLOOK_EMAIL_API_KEY") or cfg.get("outlook_api_key") or "",
        "outlook_admin_password": os.environ.get("OUTLOOK_EMAIL_ADMIN_PASSWORD") or cfg.get("outlook_admin_password") or "",
        "mail_source_group": cfg.get("mail_source_group") or "默认分组",
        "mail_pending_group": cfg.get("mail_pending_group") or "oai_pending",
        "mail_pass": cfg.get("mail_pass") or os.environ.get("MAIL_PASS") or "local-bridge",
    }


def _load_state() -> dict[str, Any]:
    if STATE_PATH.is_file():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"claimed": {}, "cursor": 0}


def _save_state(st: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


class OutlookClient:
    def __init__(self, base: str, api_key: str, admin_password: str) -> None:
        self.base = base.rstrip("/")
        self.api_key = api_key
        self.admin_password = admin_password
        self._opener = None
        self._csrf = ""

    def _ext(self, path: str, query: dict | None = None) -> Any:
        qs = urlencode({k: v for k, v in (query or {}).items() if v not in (None, "")})
        url = f"{self.base}{path}" + (f"?{qs}" if qs else "")
        req = Request(url, headers={"Accept": "application/json", "X-API-Key": self.api_key, "User-Agent": "openai3-mail-bridge/1.0"})
        with urlopen(req, timeout=20) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        return json.loads(text) if text.startswith("{") or text.startswith("[") else text

    def _admin_login(self):
        if self._opener and self._csrf:
            return
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        login_req = Request(
            f"{self.base}/login",
            data=json.dumps({"password": self.admin_password}).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with opener.open(login_req, timeout=20) as resp:
            payload = json.loads(resp.read().decode() or "{}")
        if isinstance(payload, dict) and payload.get("success") is False:
            raise RuntimeError(payload.get("error") or "login failed")
        csrf_req = Request(f"{self.base}/api/csrf-token", headers={"Accept": "application/json"})
        with opener.open(csrf_req, timeout=20) as resp:
            csrf_payload = json.loads(resp.read().decode() or "{}")
        self._opener = opener
        self._csrf = str(csrf_payload.get("csrf_token") or "")

    def list_accounts(self) -> list[dict]:
        # Prefer external API; fallback admin session /api/accounts
        try:
            payload = self._ext("/api/external/accounts", {"limit": 5000, "offset": 0, "sort_by": "created_at", "sort_order": "desc"})
            if isinstance(payload, dict):
                acc = payload.get("accounts") or payload.get("data") or []
                if isinstance(acc, list) and acc:
                    return [x for x in acc if isinstance(x, dict)]
        except Exception:
            pass
        self._admin_login()
        req = Request(f"{self.base}/api/accounts", headers={"Accept": "application/json", "X-CSRFToken": self._csrf})
        with self._opener.open(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode() or "{}")
        acc = payload.get("accounts") if isinstance(payload, dict) else []
        return [x for x in acc if isinstance(x, dict)] if isinstance(acc, list) else []

    def list_groups(self) -> list[dict]:
        self._admin_login()
        req = Request(f"{self.base}/api/groups", headers={"Accept": "application/json", "X-CSRFToken": self._csrf})
        with self._opener.open(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode() or "{}")
        groups = payload.get("groups") if isinstance(payload, dict) else []
        return [g for g in groups if isinstance(g, dict)] if isinstance(groups, list) else []

    def list_mails(self, email: str, limit: int = 100) -> list[dict]:
        try:
            payload = self._ext(
                "/api/external/emails",
                {"email": email, "folder": "all", "top": max(1, min(int(limit), 100)), "skip": 0},
            )
            if isinstance(payload, dict):
                emails = payload.get("emails") or []
                return [e for e in emails if isinstance(e, dict)] if isinstance(emails, list) else []
        except Exception:
            return []
        return []


app = FastAPI(title="OpenAI3 Mail Bridge", docs_url=None, redoc_url=None)
_logged_in = False


class LoginReq(BaseModel):
    password: str = ""


@app.post("/api/login")
def login(req: LoginReq | None = None):
    global _logged_in
    cfg = load_cfg()
    req = req or LoginReq()
    # accept configured bridge password or empty if matches
    if req.password and req.password not in {cfg.get("mail_pass"), "local-bridge", ""}:
        # still allow if admin chose empty bridge password
        if cfg.get("mail_pass") and req.password != cfg.get("mail_pass"):
            raise HTTPException(401, "bad password")
    _logged_in = True
    return {"success": True}


@app.get("/api/random-unbound", response_class=PlainTextResponse)
def random_unbound():
    cfg = load_cfg()
    if not cfg["outlook_api_key"] and not cfg["outlook_admin_password"]:
        raise HTTPException(500, "outlook not configured")
    client = OutlookClient(cfg["outlook_api_url"], cfg["outlook_api_key"], cfg["outlook_admin_password"])
    try:
        accounts = client.list_accounts()
    except Exception as e:
        raise HTTPException(502, f"list accounts failed: {e}")
    with _lock:
        st = _load_state()
        claimed = st.get("claimed") or {}
        # prefer not claimed / not recently used
        candidates = []
        for acc in accounts:
            email = str(acc.get("email") or acc.get("address") or "").strip().lower()
            if not email or "@" not in email:
                continue
            # skip disabled if field present
            if acc.get("disabled") or acc.get("status") in {"disabled", "bad", "dead"}:
                continue
            group = str(acc.get("group_name") or acc.get("group") or acc.get("groupName") or "")
            # Prefer the configured source group (默认分组 by default), but keep
            # legacy fallback behavior if that group is temporarily empty.
            candidates.append((email, group, acc))
        if not candidates:
            raise HTTPException(404, "no emails")
        # sort: source group first, then never claimed, then oldest claimed
        src = (cfg.get("mail_source_group") or "默认分组").lower()
        def score(item):
            email, group, _ = item
            g = (group or "").lower()
            pref = 0 if src and src in g else 1
            last = float(claimed.get(email) or 0)
            return (pref, last, email)
        candidates.sort(key=score)
        email = candidates[0][0]
        claimed[email] = time.time()
        st["claimed"] = claimed
        _save_state(st)
    return PlainTextResponse(email)


@app.get("/api/verify-code")
def verify_code(
    email: str = Query(...),
    keyword: str = Query("openai"),
    since: float = Query(0.0, ge=0.0),
    exclude_code: list[str] = Query(default=[]),
):
    cfg = load_cfg()
    client = OutlookClient(cfg["outlook_api_url"], cfg["outlook_api_key"], cfg["outlook_admin_password"])
    try:
        mails = client.list_mails(email, limit=100)
    except Exception as e:
        return {"success": False, "error": str(e)}
    return select_latest_verification_code(
        mails,
        keyword,
        not_before=since,
        excluded_codes=set(exclude_code),
    )


@app.get("/health")
def health():
    cfg = load_cfg()
    return {
        "ok": True,
        "service": "openai3-mail-bridge",
        "outlook_api_url": cfg["outlook_api_url"],
        "has_api_key": bool(cfg["outlook_api_key"]),
        "has_admin": bool(cfg["outlook_admin_password"]),
    }


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("OPENAI3_MAIL_HOST", "127.0.0.1")
    port_value = str(os.environ.get("OPENAI3_MAIL_PORT") or "").strip()
    if not port_value.isdigit():
        raise RuntimeError("OPENAI3_MAIL_PORT must come from config/ports.env")
    port = int(port_value)
    uvicorn.run(app, host=host, port=port, log_level="info")
