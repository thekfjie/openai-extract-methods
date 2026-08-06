"""
ChatGPT 邮箱注册脚本 — OpenAI Auth API
依赖: curl_cffi (TLS 指纹), cpa_codex_oauth (邮箱验证码)

注册流程:
  1. OAuth 初始化 (带 login_hint=email) → 服务端自动发送验证码
  2. 轮询邮箱获取验证码
  3. POST /api/accounts/email-otp/validate   → 验证邮箱验证码
  4. GET  /about-you                          → 导航到 about-you 页面
  5. POST /api/accounts/create_account       → 创建账号 (姓名+生日)
  6. GET  /api/auth/callback/openai           → OAuth 回调
"""

import asyncio
import hashlib
import json
import os
import time
import uuid
import re
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from curl_cffi import requests
for _parent in Path(__file__).resolve().parents:
    if (_parent / "integrations" / "browser_fingerprint.py").is_file():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

from integrations.oai_fingerprint import (
    curl_cffi_session_kwargs,
    fingerprint_http_headers,
    generate_entry_fingerprint,
)
from integrations.openai3_control import classify_openai_signup_transition

# 随机姓名池
FIRST_NAMES = ["James", "John", "Robert", "Michael", "David", "William", "Richard", "Joseph", "Thomas", "Chris",
               "Mary", "Patricia", "Jennifer", "Linda", "Elizabeth", "Barbara", "Susan", "Jessica", "Sarah", "Karen",
               "Daniel", "Matthew", "Anthony", "Mark", "Donald", "Steven", "Paul", "Andrew", "Joshua", "Kenneth"]
LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
              "Wilson", "Anderson", "Taylor", "Thomas", "Moore", "Jackson", "Martin", "Lee", "Thompson", "White"]

def random_name() -> str:
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"

def random_age() -> int:
    return random.randint(24, 36)

# ============================================================
# Sentinel Token Provider (纯算法逆向实现)
# ============================================================
from sentinel_token import (
    SentinelTokenProvider as _SentinelImpl,
    generate_requirements_token,
    generate_enforcement_token,
    solve_pow,
    fnv1a_32,
)


class SentinelTokenProvider(_SentinelImpl):
    """继承 sentinel_token.py 中的实现, 支持 cookies 注入"""

    def __init__(
        self,
        impersonate: str = "chrome",
        cookies: dict = None,
        proxy: str = None,
        fingerprint_seed: str | None = None,
    ):
        fingerprint_entry = os.environ.get("OAI_FINGERPRINT_ENTRY", "chatgpt_register").strip()
        if fingerprint_entry not in {"openai3", "chatgpt_register"}:
            fingerprint_entry = "chatgpt_register"
        fingerprint = generate_entry_fingerprint(fingerprint_entry, seed=fingerprint_seed)
        effective_impersonate = str((fingerprint or {}).get("impersonate") or impersonate)
        super().__init__(impersonate=effective_impersonate, cookies=cookies, fingerprint=fingerprint)
        self._proxy = proxy

    async def _get_session(self) -> requests.AsyncSession:
        if not self._session:
            kwargs = curl_cffi_session_kwargs(
                self.fingerprint,
                fallback_impersonate=self.impersonate,
                proxy=self._proxy,
            )
            self._session = requests.AsyncSession(**kwargs)
        return self._session

    def set_session(self, session: requests.AsyncSession):
        """共享外部 session, 确保同一代理 IP"""
        self._session = session

    def set_cookies(self, cookies: dict):
        self._cookies = cookies


# ============================================================
# OpenAI Auth Client
# ============================================================
class OpenAIAuthClient:
    BASE_URL = "https://auth.openai.com"
    CHATGPT_URL = "https://chatgpt.com"

    def __init__(self, impersonate: str = "chrome", sentinel: Optional[SentinelTokenProvider] = None, proxy: str = None):
        self.proxy = proxy
        self.sentinel = sentinel or SentinelTokenProvider(proxy=proxy)
        self.fingerprint = getattr(self.sentinel, "fingerprint", None)
        self.impersonate = str((self.fingerprint or {}).get("impersonate") or impersonate)
        self._session: Optional[requests.AsyncSession] = None
        self.device_id: str = str((self.fingerprint or {}).get("device_id") or uuid.uuid4())
        self.cookies: dict = {}

    async def _get_session(self) -> requests.AsyncSession:
        if not self._session:
            kwargs = curl_cffi_session_kwargs(
                self.fingerprint,
                fallback_impersonate=self.impersonate,
                proxy=self.proxy,
            )
            self._session = requests.AsyncSession(**kwargs)
        return self._session

    async def share_session_with_sentinel(self):
        """让 sentinel provider 共享同一 session, 确保同一代理 IP"""
        s = await self._get_session()
        self.sentinel.set_session(s)

    def _common_headers(self, referer: str = None) -> dict:
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
        }
        headers.update(fingerprint_http_headers(self.fingerprint))
        if referer:
            headers["referer"] = referer
            try:
                parsed = urlparse(referer)
                if parsed.scheme and parsed.netloc:
                    headers["origin"] = f"{parsed.scheme}://{parsed.netloc}"
            except ValueError:
                pass
        trace_id = str(random.getrandbits(64))
        parent_id = str(random.getrandbits(64))
        headers.update({
            "traceparent": f"00-0000000000000000{int(trace_id):016x}-{int(parent_id):016x}-01",
            "tracestate": "dd=s:1;o:rum",
            "x-datadog-origin": "rum",
            "x-datadog-parent-id": parent_id,
            "x-datadog-sampling-priority": "1",
            "x-datadog-trace-id": trace_id,
        })
        return headers

    @staticmethod
    def _response_payload(resp) -> dict:
        try:
            payload = resp.json()
        except Exception:
            payload = {"text": str(getattr(resp, "text", "") or "")[:500]}
        if not isinstance(payload, dict):
            payload = {"data": payload}
        payload.setdefault("_http_status", int(getattr(resp, "status_code", 0) or 0))
        return payload

    @staticmethod
    def error_code(payload: object) -> str:
        if not isinstance(payload, dict):
            return ""
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("code") or error.get("message") or "").strip()
        return str(error or "").strip()

    async def _add_sentinel_headers(
        self,
        headers: dict,
        flow: str,
        referer: str,
        *,
        include_session_observer: bool = True,
        invocation_id: str = "",
    ) -> dict:
        """Add HAR-matched Sentinel headers for one auth action."""
        # Sentinel's backend receives explicit cookies, so refresh its snapshot
        # immediately before every token request instead of retaining a stale
        # pre-challenge cookie set.
        self._sync_auth_cookies(await self._get_session())
        headers["x-access-flow-invocation-id"] = invocation_id or str(uuid.uuid4())
        token = await self.sentinel.get_token(flow, self.device_id)
        if token:
            headers["openai-sentinel-token"] = json.dumps(token)
            if include_session_observer:
                # Reuse the flow-scoped Sentinel cache; do not start another PoW.
                so_token = await self.sentinel.get_so_token(flow, self.device_id)
                if so_token:
                    headers["openai-sentinel-so-token"] = json.dumps(so_token)
        return headers

    # ---- Step 6: 创建账号 ----
    async def create_account(self, name: str, birthdate: str) -> dict:
        """POST /api/accounts/create_account → 创建账号 (姓名+生日)"""
        s = await self._get_session()
        url = f"{self.BASE_URL}/api/accounts/create_account"
        referer = f"{self.BASE_URL}/about-you"
        headers = await self._add_sentinel_headers(
            self._common_headers(referer=referer),
            "oauth_create_account",
            referer,
            invocation_id=str(uuid.uuid4()),
        )

        body = {"name": name, "birthdate": birthdate}
        resp = await s.post(url, json=body, headers=headers)
        return self._response_payload(resp)

    # ---- Step 7: OAuth 回调 ----
    async def oauth_callback(self, code: str, state: str) -> dict:
        """GET /api/auth/callback/openai?code=... → OAuth 回调"""
        s = await self._get_session()
        url = f"{self.CHATGPT_URL}/api/auth/callback/openai"
        params = {
            "code": code,
            "scope": "openid email profile offline_access model.request model.read organization.read organization.write",
            "state": state,
        }
        headers = {
            "referer": f"{self.BASE_URL}/",
            "upgrade-insecure-requests": "1",
        }
        resp = await s.get(url, params=params, headers=headers, allow_redirects=False)
        return {"status": resp.status_code, "location": resp.headers.get("location", "")}

    def _sync_auth_cookies(self, session: requests.AsyncSession) -> None:
        """Keep the Sentinel identity tied to the browser-equivalent cookie jar."""
        for cookie in session.cookies.jar:
            if cookie.name == "oai-did":
                self.device_id = cookie.value
                break
        if isinstance(self.fingerprint, dict):
            # Persist a server-established device cookie on the shared profile
            # so a transport-only restart of this mailbox keeps one identity.
            self.fingerprint["device_id"] = self.device_id
        self.cookies = {cookie.name: cookie.value for cookie in session.cookies.jar}
        self.sentinel.set_cookies(self.cookies)

    def _ensure_chatgpt_device_cookie(self, session: requests.AsyncSession) -> str:
        """Return the HAR identity and ensure ChatGPT receives the same cookie.

        The browser flow sends ``ext-oai-did`` as a signin query parameter, and
        its value exactly matches the ``oai-did`` cookie.  Prefer an identity
        already established by ChatGPT; otherwise seed the cookie from this
        run's stable fingerprint identity before the signin request.
        """
        for cookie in session.cookies.jar:
            if cookie.name == "oai-did" and cookie.value:
                self.device_id = str(cookie.value)
                self._sync_auth_cookies(session)
                return self.device_id

        session.cookies.set("oai-did", self.device_id, domain="chatgpt.com", path="/")
        self._sync_auth_cookies(session)
        return self.device_id

    @staticmethod
    def _otp_not_before() -> float:
        """Return a mail-safe challenge baseline.

        Mail providers commonly expose second-precision receive timestamps.
        Flooring avoids rejecting a code delivered in the same second while
        still excluding every message from an earlier second.
        """
        return float(int(time.time()))

    async def _start_chatgpt_authorization(
        self,
        *,
        screen_hint: str,
        login_hint: str = "",
    ) -> dict:
        """Start the ChatGPT NextAuth handoff recorded in the browser HAR."""
        session = await self._get_session()
        login_page = f"{self.CHATGPT_URL}/auth/login_with" if screen_hint == "login" else f"{self.CHATGPT_URL}/auth/login"
        await session.get(login_page, headers=self._common_headers(f"{self.CHATGPT_URL}/"))

        csrf_resp = await session.get(
            f"{self.CHATGPT_URL}/api/auth/csrf",
            headers=self._common_headers(login_page),
        )
        if csrf_resp.status_code != 200:
            raise RuntimeError(f"CSRF 请求失败: {csrf_resp.status_code}")
        try:
            csrf_payload = csrf_resp.json()
        except Exception as error:
            raise RuntimeError("CSRF 响应不是 JSON") from error
        csrf_token = str((csrf_payload or {}).get("csrfToken") or "")
        if not csrf_token:
            raise RuntimeError("CSRF 响应缺少 csrfToken")

        # HAR happy path: prompt=login, login_or_signup, and login_hint=email.
        # HAR recovery path: prompt=login, screen_hint=login, without login_hint.
        # Both captures tie ext-oai-did to the oai-did cookie and allocate a
        # fresh auth_session_logging_id for each authorization handoff.
        device_id = self._ensure_chatgpt_device_cookie(session)
        params = {
            "prompt": "login",
            "screen_hint": screen_hint,
            "ext-oai-did": device_id,
            "auth_session_logging_id": str(uuid.uuid4()),
        }
        if login_hint:
            params["login_hint"] = login_hint
        signin_headers = self._common_headers(login_page)
        signin_headers["content-type"] = "application/x-www-form-urlencoded"
        signin_resp = await session.post(
            f"{self.CHATGPT_URL}/api/auth/signin/openai",
            params=params,
            data={"callbackUrl": f"{self.CHATGPT_URL}/", "csrfToken": csrf_token, "json": "true"},
            headers=signin_headers,
            allow_redirects=False,
        )
        auth_url = ""
        try:
            signin_payload = signin_resp.json()
            if isinstance(signin_payload, dict):
                auth_url = str(signin_payload.get("url") or "")
        except Exception:
            pass
        if not auth_url:
            auth_url = str(signin_resp.headers.get("location") or "")
        if not auth_url:
            raise RuntimeError(f"OAuth 初始化未返回授权地址: {signin_resp.status_code}")
        auth_url = urljoin(f"{self.CHATGPT_URL}/", auth_url)

        final_resp = await session.get(
            auth_url,
            headers={
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "referer": login_page,
            },
            allow_redirects=True,
        )
        self._sync_auth_cookies(session)
        return {
            "status": int(getattr(final_resp, "status_code", 0) or 0),
            "cookies": self.cookies,
            "device_id": self.device_id,
            "auth_url": auth_url,
            "landing_url": str(getattr(final_resp, "url", "") or ""),
        }

    @staticmethod
    def _landing_transition(landing_url: str, *, status: int, mode: str) -> dict:
        """Normalize the page reached by a browser-style authorization redirect."""
        path = urlparse(landing_url).path.lower()
        if "email-verification" in path:
            return {
                "_http_status": status,
                "page": {
                    "type": "email_otp_verification",
                    "payload": {"email_verification_mode": mode},
                },
                "continue_url": "/email-verification",
            }
        if "about-you" in path:
            return {
                "_http_status": status,
                "page": {"type": "about_you"},
                "continue_url": "/about-you",
            }
        return {"_http_status": status, "page": {"type": "unknown"}, "continue_url": landing_url}

    # ---- Email 注册: passwordless signup ----
    async def init_page_email(self, email: str) -> dict:
        """Begin the HAR-recorded passwordless signup flow.

        ChatGPT's sign-in request carries ``login_hint`` and reaches
        ``/email-verification`` by redirect.  The successful capture contains
        neither ``/authorize/continue`` nor a password-registration request.
        """
        otp_not_before = self._otp_not_before()
        started = await self._start_chatgpt_authorization(
            screen_hint="login_or_signup",
            login_hint=email,
        )
        started["otp_not_before"] = otp_not_before
        started["transition"] = self._landing_transition(
            str(started.get("landing_url") or ""),
            status=int(started.get("status") or 0),
            mode="passwordless_signup",
        )
        return started

    async def begin_passwordless_login(self, email: str) -> dict:
        """Recover a possibly-created account through HAR-recorded login.

        A failed ``create_account`` can still leave the account created on the
        server.  Capture 2 starts a fresh ``screen_hint=login`` handoff, then
        sends only the username object to ``authorize/continue``.  It never
        retries profile submission in that old signup state.
        """
        started = await self._start_chatgpt_authorization(screen_hint="login")
        if int(started.get("status") or 0) >= 400:
            started["transition"] = self._landing_transition(
                str(started.get("landing_url") or ""),
                status=int(started.get("status") or 0),
                mode="passwordless_login",
            )
            return started

        session = await self._get_session()
        landing_url = str(started.get("landing_url") or "")
        if "log-in" not in urlparse(landing_url).path.lower():
            login_resp = await session.get(
                f"{self.BASE_URL}/log-in",
                headers={
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "referer": f"{self.CHATGPT_URL}/auth/login",
                },
                allow_redirects=True,
            )
            landing_url = str(getattr(login_resp, "url", "") or landing_url)
            started["landing_url"] = landing_url
            self._sync_auth_cookies(session)

        referer = f"{self.BASE_URL}/log-in"
        headers = await self._add_sentinel_headers(
            self._common_headers(referer=referer),
            "authorize_continue",
            referer,
            include_session_observer=False,
            invocation_id=str(uuid.uuid4()),
        )
        otp_not_before = self._otp_not_before()
        transition_payload: dict = {}
        for attempt in range(2):
            try:
                response = await session.post(
                    f"{self.BASE_URL}/api/accounts/authorize/continue",
                    json={"username": {"value": email, "kind": "email"}},
                    headers=headers,
                )
                transition_payload = self._response_payload(response)
            except Exception as error:
                if attempt == 0:
                    await asyncio.sleep(1)
                    continue
                transition_payload = {"error": {"code": "authorize_continue_transport_error"}, "_http_status": 0}
            if int(transition_payload.get("_http_status") or 0) == 0 and attempt == 0:
                await asyncio.sleep(1)
                continue
            break

        started["status"] = int(transition_payload.get("_http_status") or 0)
        started["otp_not_before"] = otp_not_before
        started["transition"] = transition_payload
        return started

    async def reauthorize_for_session(self, original_auth_url: str) -> str:
        """Reuse an authenticated authorize session to capture callback URL.

        Some signup attempts return ``registration_disallowed`` after the
        account state is already established.  The captured protocol does not
        submit profile data again; it removes ``prompt=login`` and follows
        redirects until the callback URL is visible, leaving callback
        consumption to the normal session step.
        """
        if not original_auth_url:
            return ""
        parsed = urlparse(original_auth_url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        params.pop("prompt", None)
        authorize_url = urlunparse(parsed._replace(
            query=urlencode({key: values[0] for key, values in params.items()})
        ))
        session = await self._get_session()
        current_url = authorize_url
        for _ in range(10):
            if "code=" in current_url and "state=" in current_url and "/api/auth/callback/openai" in current_url:
                return current_url
            response = await session.get(current_url, allow_redirects=False)
            location = str(response.headers.get("location") or "")
            # Keep diagnostics value-free: expose only status and URL paths,
            # never authorization query parameters or mailbox identifiers.
            print(
                f"  [reauth] status={int(getattr(response, 'status_code', 0) or 0)} "
                f"path={urlparse(current_url).path[:100]} "
                f"location_path={urlparse(location).path[:100]}"
            )
            if not location:
                final_url = str(getattr(response, "url", "") or current_url)
                return final_url if "code=" in final_url and "state=" in final_url else ""
            current_url = urljoin(current_url, location)
        return current_url if "code=" in current_url and "state=" in current_url else ""

    # ---- Email 注册: 验证邮箱 OTP ----
    async def validate_email_otp(self, code: str, *, invocation_id: str = "") -> dict:
        """POST /api/accounts/email-otp/validate → 验证邮箱验证码"""
        s = await self._get_session()
        url = f"{self.BASE_URL}/api/accounts/email-otp/validate"
        referer = f"{self.BASE_URL}/email-verification"
        headers = await self._add_sentinel_headers(
            self._common_headers(referer=referer),
            "email_otp_validate",
            referer,
            invocation_id=invocation_id,
        )
        headers["accept"] = "application/json"
        body = {"code": code}
        resp = await s.post(url, json=body, headers=headers)
        return self._response_payload(resp)

    async def resend_email_otp(self) -> dict:
        """Resend OTP without creating a second auth challenge."""
        s = await self._get_session()
        referer = f"{self.BASE_URL}/email-verification"
        resp = await s.post(
            f"{self.BASE_URL}/api/accounts/email-otp/resend",
            json={},
            headers=self._common_headers(referer),
        )
        return self._response_payload(resp)

    async def select_workspace(self, payload: dict) -> dict:
        """Advance an OAuth workspace chooser using the workspace in auth session."""
        session = payload.get("oai-client-auth-session") if isinstance(payload, dict) else {}
        session = session if isinstance(session, dict) else {}
        workspaces = session.get("workspaces") if isinstance(session.get("workspaces"), list) else []
        workspace_id = ""
        for workspace in workspaces:
            if isinstance(workspace, dict) and workspace.get("id"):
                workspace_id = str(workspace["id"])
                break
        if not workspace_id:
            return {"error": {"code": "workspace_id_missing"}, "_http_status": 0}
        s = await self._get_session()
        referer = f"{self.BASE_URL}/sign-in-with-chatgpt/codex/consent"
        resp = await s.post(
            f"{self.BASE_URL}/api/accounts/workspace/select",
            json={"workspace_id": workspace_id},
            headers=self._common_headers(referer),
        )
        return self._response_payload(resp)

    async def close(self):
        session = self._session
        self._session = None
        if session:
            if getattr(self.sentinel, "_session", None) is session:
                self.sentinel._session = None
            await session.close()


# ============================================================
# 邮箱注册编排器
# ============================================================
TRANSIENT_OTP_ERRORS = frozenset({"get_chatgpt_account_error"})


def _is_failed_response(auth: OpenAIAuthClient, payload: dict) -> bool:
    return bool(auth.error_code(payload)) or int((payload or {}).get("_http_status") or 0) >= 400


def _is_recoverable_create_failure(auth: OpenAIAuthClient, payload: dict) -> bool:
    """Only the ambiguous HAR branch may enter existing-account recovery.

    A 409 means the profile may already exist and a 5xx means the single
    create request may have committed before the response failed.  Explicit
    business rejections such as ``registration_disallowed`` are terminal and
    must not start a second registration transaction.
    """
    status = int((payload or {}).get("_http_status") or 0)
    return status == 409 or status >= 500


def _is_transient_otp_failure(auth: OpenAIAuthClient, payload: dict) -> bool:
    return (
        auth.error_code(payload) in TRANSIENT_OTP_ERRORS
        or int((payload or {}).get("_http_status") or 0) >= 500
    )


def _otp_validation_did_not_advance(payload: dict) -> bool:
    """A 2xx response is useful only after it leaves the OTP challenge."""
    stage = classify_openai_signup_transition(payload).get("stage")
    return stage not in {"about_you", "callback", "workspace"}


def _fingerprint_seed_for_email(email: str) -> str:
    """Derive one opaque, stable profile seed per account for this run."""
    run_seed = str(
        os.environ.get("OPENAI3_FINGERPRINT_RUN_SEED")
        or os.environ.get("OPENAI3_FINGERPRINT_SEED")
        or "openai3-local"
    )
    material = f"openai3:{run_seed}:{str(email).strip().lower()}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


async def _try_register_one_email(
    email: str, name: str, birthdate: str,
    sentinel: SentinelTokenProvider, proxy: str, mail,
    _t0: float, otp_timeout: int = 10,
) -> dict | None:
    """单次邮箱注册尝试, 失败返回 None"""
    def _ts():
        return f"[{time.time()-_t0:.1f}s]"

    auth = OpenAIAuthClient(sentinel=sentinel, proxy=proxy)
    failure_reason = "headless_failed"

    def _failure(reason: str | None = None) -> dict:
        return {
            "email": email,
            "registered": False,
            "failure_reason": str(reason or failure_reason or "headless_failed"),
        }

    print(f"  {_ts()} [+] 邮箱任务已分配")

    try:
        async def _wait_and_validate(otp_since: float, flow_label: str) -> dict | None:
            """Poll only this challenge's newest code, with bounded recovery."""
            nonlocal failure_reason
            resend_used = False

            async def _resend_and_poll(*, exclude_code: str = "") -> str | None:
                nonlocal resend_used, failure_reason
                if resend_used:
                    return None
                resend_used = True
                retry_since = auth._otp_not_before()
                print("  [!] 本轮验证码未到或未确认，受控重发一次")
                resend_result = await auth.resend_email_otp()
                resend_status = int((resend_result or {}).get("_http_status") or 0)
                resend_error = auth.error_code(resend_result)
                print(
                    f"  [..] 验证码重发响应: status={resend_status} "
                    f"result={resend_error or 'accepted'}"
                )
                if _is_failed_response(auth, resend_result):
                    failure_reason = "otp_resend_failed"
                    print(f"  [x] 验证码重发失败: {auth.error_code(resend_result) or 'http_error'}")
                    return None
                replacement = await mail.poll_verify_code(
                    email,
                    timeout=otp_timeout,
                    interval=3,
                    since=retry_since,
                    exclude_codes={exclude_code} if exclude_code else None,
                )
                if not replacement:
                    failure_reason = "otp_not_received_after_resend"
                    print(f"  [x] {otp_timeout}s 内未收到重发后的验证码")
                    return None
                return replacement

            print(f"  {_ts()} [..] 等待{flow_label}验证码 ({otp_timeout}s 超时)...")
            code = await mail.poll_verify_code(
                email,
                timeout=otp_timeout,
                interval=3,
                since=otp_since,
            )
            if not code:
                print(f"  [!] {otp_timeout}s 内未收到本轮验证码")
                code = await _resend_and_poll()
                if not code:
                    return None
            print("  [+] 已获取本轮验证码")

            async def _submit(code_to_submit: str) -> dict:
                invocation_id = str(uuid.uuid4())
                result = await auth.validate_email_otp(code_to_submit, invocation_id=invocation_id)
                # Capture 2: a transient server error recovered by resubmitting
                # the exact same code once. Resending here would invalidate it.
                if _is_transient_otp_failure(auth, result) or _otp_validation_did_not_advance(result):
                    print("  [!] 验证未推进，重提同一验证码一次")
                    await asyncio.sleep(1)
                    result = await auth.validate_email_otp(code_to_submit, invocation_id=invocation_id)
                return result

            print(f"  {_ts()} [..] 提交邮箱验证码...")
            result = await _submit(code)
            error = auth.error_code(result)
            needs_replacement = error == "wrong_email_otp_code" or (
                not error
                and int(result.get("_http_status") or 0) < 400
                and _otp_validation_did_not_advance(result)
            )
            if not needs_replacement:
                if error or int(result.get("_http_status") or 0) >= 400:
                    failure_reason = "otp_validation_failed"
                    print(f"  [x] 验证失败: {error or 'http_error'}")
                    return None
                print(f"  {_ts()} [+] 邮箱验证通过")
                return result

            # A known wrong code, or a 2xx response that stayed on the OTP
            # page after its same-code retry, gets one replacement challenge.
            # It is never proactive and it receives a new timestamp baseline.
            print("  [!] 验证码未确认，仅接受未提交过的新验证码")
            replacement = await _resend_and_poll(exclude_code=code)
            if not replacement:
                return None
            result = await _submit(replacement)
            error = auth.error_code(result)
            if error or int(result.get("_http_status") or 0) >= 400:
                failure_reason = "otp_validation_failed"
                print(f"  [x] 重发后验证码仍失败: {error or 'http_error'}")
                return None
            if _otp_validation_did_not_advance(result):
                failure_reason = "otp_validation_stalled"
                print("  [x] 重发后页面仍未推进")
                return None
            print(f"  {_ts()} [+] 邮箱验证通过")
            return result

        async def _callback_url(payload: dict) -> str:
            """Resolve the only workspace detour seen by the auth state machine."""
            transition = classify_openai_signup_transition(payload)
            if transition["stage"] == "workspace":
                workspace_result = await auth.select_workspace(payload)
                if _is_failed_response(auth, workspace_result):
                    print(f"  [x] 工作空间选择失败: {auth.error_code(workspace_result) or 'http_error'}")
                    return ""
                transition = classify_openai_signup_transition(workspace_result)
            if transition["stage"] != "callback":
                print(f"  [x] OAuth 未到回调状态: {transition.get('pageType') or transition['stage']}")
                return ""
            return str(transition.get("continueUrl") or "")

        # 1. Capture 1 starts passwordless signup through login_hint. The
        # initial redirect itself creates the mail challenge; do not resend it.
        print(f"  {_ts()} [..] 初始化无密码注册状态...")
        await auth.share_session_with_sentinel()
        init = await auth.init_page_email(email)
        print(f"  [+] 设备ID: {init['device_id'][:12]}...")
        sentinel.set_cookies(init["cookies"])

        transition_payload = init.get("transition") if isinstance(init, dict) else {}
        init_error = auth.error_code(transition_payload)
        if init_error or int((transition_payload or {}).get("_http_status") or 0) >= 400:
            init_status = int((transition_payload or {}).get("_http_status") or 0)
            landing_path = urlparse(str(init.get("landing_url") or "")).path or "unknown"
            print(
                f"  [x] signup 初始化失败: {init_error or 'http_error'} "
                f"status={init_status} page={landing_path[:80]}"
            )
            if init_status == 403:
                reason = "cloudflare_challenge"
            elif init_status == 0 or init_status >= 500:
                reason = "signup_init_transient"
            else:
                reason = "signup_init_rejected"
            return {"email": email, "registered": False, "failure_reason": reason}
        transition = classify_openai_signup_transition(transition_payload)
        stage = transition["stage"]
        mode = transition.get("emailVerificationMode") or ""
        if stage != "email_otp" or mode != "passwordless_signup":
            landing_path = urlparse(str(init.get("landing_url") or "")).path or "unknown"
            landing_status = int(init.get("status") or 0)
            print(
                f"  [x] signup 状态不符合 HAR: {transition.get('pageType') or stage}/"
                f"{mode or 'unknown'} status={landing_status} page={landing_path[:80]}"
            )
            return {
                "email": email,
                "registered": False,
                "failure_reason": "signup_unknown_landing",
            }
        print(f"  {_ts()} [+] signup 状态: passwordless_signup")

        validate_result = await _wait_and_validate(
            float(init.get("otp_not_before") or auth._otp_not_before()),
            "注册",
        )
        if not validate_result:
            return {"email": email, "registered": False, "failure_reason": failure_reason}
        transition = classify_openai_signup_transition(validate_result)
        stage = transition["stage"]

        # 2. The normal path reaches about-you. Visit it once, then submit the
        # profile once. Repeating profile submission caused the 500 -> 409
        # failure sequence in Capture 2.
        final_payload = validate_result
        direct_callback_url = ""
        if stage == "about_you":
            continue_url = transition.get("continueUrl") or ""
            if not continue_url:
                print("  [x] about-you 缺少继续地址")
                return _failure("about_you_continue_missing")
            print(f"  {_ts()} [..] 导航到 about-you 页面...")
            session = await auth._get_session()
            page_resp = await session.get(
                urljoin(auth.BASE_URL, continue_url),
                headers=auth._common_headers(f"{auth.BASE_URL}/email-verification"),
            )
            if int(getattr(page_resp, "status_code", 0) or 0) >= 400:
                print("  [x] about-you 页面无法打开")
                return _failure("about_you_page_failed")
            if os.environ.get("OPENAI3_NO_COMMIT", "").strip().lower() in {"1", "true", "yes", "on"}:
                print("  [+] no-commit 验证完成：已到达 about-you，未提交 create_account")
                return {
                    "email": email,
                    "registered": False,
                    "validation_only": True,
                    "failure_reason": "no_commit_reached_about_you",
                }
            print("  [..] 创建账号...")
            create_result = await auth.create_account(name, birthdate)
            if not _is_failed_response(auth, create_result):
                print("  [+] 账号创建成功")
                final_payload = create_result
            elif _is_recoverable_create_failure(auth, create_result):
                # Capture 2's recovery: start a clean passwordless login and
                # never make a second create_account request in this signup.
                print("  [!] 资料提交未确认，切换到无密码登录恢复")
                recovery = await auth.begin_passwordless_login(email)
                recovery_payload = recovery.get("transition") if isinstance(recovery, dict) else {}
                recovery_transition = classify_openai_signup_transition(recovery_payload)
                recovery_error = auth.error_code(recovery_payload)
                if (
                    recovery_error
                    or int((recovery_payload or {}).get("_http_status") or 0) >= 400
                    or recovery_transition["stage"] != "email_otp"
                    or recovery_transition.get("emailVerificationMode") != "passwordless_login"
                ):
                    print(f"  [x] 登录恢复初始化失败: {recovery_error or 'unexpected_state'}")
                    return _failure("existing_login_recovery_init_failed")
                recovery_result = await _wait_and_validate(
                    float(recovery.get("otp_not_before") or auth._otp_not_before()),
                    "登录恢复",
                )
                if not recovery_result:
                    return {"email": email, "registered": False, "failure_reason": failure_reason}
                final_payload = recovery_result
            elif auth.error_code(create_result) == "registration_disallowed":
                # The account state may already exist even though the profile
                # endpoint rejected the request.  Reauthorize once using the
                # original auth URL; never submit create_account again.
                print("  [!] registration_disallowed，复用 authorize 会话抓取 callback")
                direct_callback_url = await auth.reauthorize_for_session(
                    str(init.get("auth_url") or "")
                )
                if not direct_callback_url:
                    # Some sessions remain on email-verification after the
                    # authorize URL is replayed.  Follow the second HAR's
                    # existing-login branch: request one login OTP, validate
                    # it, and continue to callback without another profile
                    # submission.
                    print("  [!] 未直接捕获 callback，切换到无密码登录恢复")
                    recovery = await auth.begin_passwordless_login(email)
                    recovery_payload = recovery.get("transition") if isinstance(recovery, dict) else {}
                    recovery_transition = classify_openai_signup_transition(recovery_payload)
                    recovery_error = auth.error_code(recovery_payload)
                    if (
                        recovery_error
                        or int((recovery_payload or {}).get("_http_status") or 0) >= 400
                        or recovery_transition["stage"] != "email_otp"
                        or recovery_transition.get("emailVerificationMode") != "passwordless_login"
                    ):
                        print(f"  [x] 登录恢复初始化失败: {recovery_error or 'unexpected_state'}")
                        return _failure("registration_disallowed_recovery_init_failed")
                    recovery_result = await _wait_and_validate(
                        float(recovery.get("otp_not_before") or auth._otp_not_before()),
                        "登录恢复",
                    )
                    if not recovery_result:
                        return {"email": email, "registered": False, "failure_reason": failure_reason}
                    final_payload = recovery_result
            else:
                create_error = auth.error_code(create_result)
                print(f"  [x] 创建失败: {create_error or 'http_error'}")
                return _failure(create_error or "create_account_rejected")

        # An existing account can reach callback directly after OTP validation;
        # both it and the normal create response pass through this one resolver.
        continue_url = direct_callback_url or await _callback_url(final_payload)
        if not continue_url:
            return _failure("oauth_callback_state_missing")

        # 3. OAuth callback and Web session are mandatory. A profile is not a
        # completed result until it has a usable access token and is imported.
        access_token = ""
        session_token = ""
        refresh_token = ""
        account_id = ""
        sess_data = {}
        print("  [..] OAuth 回调...")
        session = await auth._get_session()
        cb_resp = await session.get(urljoin(auth.BASE_URL, continue_url), allow_redirects=True)
        if int(getattr(cb_resp, "status_code", 0) or 0) >= 400:
            print("  [x] OAuth 回调失败")
            return _failure("oauth_callback_failed")
        print("  [..] 获取 session...")
        sess_resp = await session.get(f"{auth.CHATGPT_URL}/api/auth/session")
        try:
            sess_data = sess_resp.json()
        except Exception:
            print("  [x] session 解析失败")
            return _failure("session_parse_failed")
        if not isinstance(sess_data, dict):
            print("  [x] session 返回格式异常")
            return _failure("session_payload_invalid")
        access_token = str(sess_data.get("accessToken") or "")
        session_token = str(sess_data.get("sessionToken") or "")
        refresh_token = str(sess_data.get("refreshToken") or "")
        account_id = str((sess_data.get("account") or {}).get("id") or "")
        if not access_token:
            print("  [x] session 未返回可用 Access Token")
            return _failure("session_access_token_missing")

        if os.environ.get("OPENAI3_STOP_AFTER_AT", "").strip().lower() in {"1", "true", "yes", "on"}:
            print("  [+] Access Token 获取成功，按测试开关停止，未导入/落盘认证文件")
            return {
                "email": email,
                "registered": False,
                "access_token_acquired": True,
                "failure_reason": "test_stop_after_at",
            }

        cpa_base = str(os.environ.get("CPA_BASE") or "").rstrip("/")
        if not cpa_base:
            print("  [x] 未配置认证导入目标")
            return _failure("auth_import_target_missing")
        print("  [..] 导入认证结果...")
        try:
            now_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            expires = sess_data.get("expires", "")
            plan_type = (sess_data.get("account") or {}).get("planType", "free")
            auth_json = json.dumps({
                "type": "codex",
                "access_token": access_token,
                "refresh_token": refresh_token,
                "session_token": session_token,
                "account_id": account_id,
                "chatgpt_account_id": account_id,
                "chatgpt_plan_type": plan_type,
                "plan_type": plan_type,
                "email": email,
                "name": email,
                "disabled": False,
                "expired": expires,
                "last_refresh": now_utc,
            }, ensure_ascii=False)
            filename = f"codex-{email}.json"
            from curl_cffi import CurlMime
            mime = CurlMime()
            mime.addpart(name="file", filename=filename, content_type="application/json", data=auth_json)
            cpa_session = requests.AsyncSession(impersonate="chrome", timeout=30)
            try:
                cpa_resp = await cpa_session.post(
                    f"{cpa_base}/v0/management/auth-files",
                    multipart=mime,
                    headers={"Authorization": f"Bearer {os.environ.get('CPA_KEY', '')}"},
                )
            finally:
                await cpa_session.close()
            if not 200 <= int(cpa_resp.status_code or 0) < 300:
                print("  [x] 认证导入未成功")
                return _failure("auth_import_rejected")
        except Exception as error:
            print(f"  [x] 认证导入失败: {type(error).__name__}")
            return _failure("auth_import_failed")

        # Passwordless accounts have no usable password. Keep the local record
        # explicit so old phone/password tooling cannot mistake it for one.
        account_line = f"{email} | passwordless\n"
        async with _accounts_lock:
            await asyncio.to_thread(_append_account, account_line)
        print("  [+] 注册、会话获取和认证导入均已完成")

        return {
            "email": email,
            "name": name,
            "session_imported": True,
            "registered": True,
        }
    except Exception as error:
        error_name = type(error).__name__
        transport_names = {"SSLError", "ProxyError", "Timeout", "TimeoutError", "ConnectionError"}
        failure_reason = "transport_error" if error_name in transport_names else "headless_exception"
        print(f"  [x] 无头注册异常: {error_name}")
        return {"email": email, "registered": False, "failure_reason": failure_reason}
    finally:
        await auth.close()


async def register_chatgpt_email(
    email: str = None,
    password: str = None,
    name: str = None,
    age: int = None,
    max_retries: int = 10,
    otp_timeout: int = 30,
    sentinel: Optional[SentinelTokenProvider] = None,
    proxy: str = None,
):
    """
    邮箱注册流程 (30s 没收到验证码就换邮箱):
      1. ChatGPT login_hint 初始化 passwordless signup
      2. 只接受本轮 challenge 之后的最新邮箱验证码
      3. 验证 → 单次资料提交 → OAuth 回调 → 认证导入
    """
    from cpa_codex_oauth import MailClient

    provided_sentinel = sentinel
    mail = MailClient()

    _t0 = time.time()

    print()
    print("  +-----------------------------+")
    print("  |   ChatGPT 邮箱注册           |")
    print("  +-----------------------------+")
    print()

    name = name or random_name()
    age = age or random_age()
    birthdate = (datetime.now() - timedelta(days=age * 365)).strftime("%Y-%m-%d")
    if password:
        # Keep the public argument temporarily for callers of the old API, but
        # do not send or save it: HAR registration is passwordless.
        password = None
    print(f"  身份资料已生成: 年龄={age}，无密码注册")

    last_failure: dict | None = None
    attempts_used = 0
    # A preflighted mailbox is one transaction.  Reuse the exact generated
    # profile across its one permitted transport-level restart so device_id
    # and profile_id cannot drift between attempts.
    fixed_email_sentinel = None
    if email:
        fixed_email_sentinel = provided_sentinel or SentinelTokenProvider(
            proxy=proxy,
            fingerprint_seed=_fingerprint_seed_for_email(email),
        )
    for attempt in range(1, max_retries + 1):
        attempts_used = attempt
        print(f"\n  -- 尝试 {attempt}/{max_retries} --")

        # 获取邮箱
        if email:
            cur_email = email
        else:
            try:
                cur_email = await mail.get_unbound_email()
            except Exception:
                cur_email = None
            if not cur_email:
                print("  [x] 没有可用邮箱")
                break

        attempt_sentinel = fixed_email_sentinel or provided_sentinel or SentinelTokenProvider(
            proxy=proxy,
            fingerprint_seed=_fingerprint_seed_for_email(cur_email),
        )
        result = await _try_register_one_email(
            cur_email, name, birthdate,
            attempt_sentinel, proxy, mail, _t0, otp_timeout,
        )
        if result and result.get("registered"):
            return result
        if result:
            last_failure = result
            retryable = str(result.get("failure_reason") or "") in {
                "transport_error",
                "signup_init_transient",
            }
            if email and not retryable:
                break

    print(f"\n  [x] 实际尝试 {attempts_used} 次后失败")
    return last_failure



# ============================================================
# Main
# ============================================================

PROXY = __import__("os").environ.get("CHATGPT_REGISTER_PROXY", "")
_accounts_lock = asyncio.Lock()

def _append_account(line: str):
    """同步文件写入 (供 asyncio.to_thread 调用)"""
    accounts_file = os.environ.get("OPENAI3_ACCOUNTS_FILE", "accounts.txt")
    with open(accounts_file, "a", encoding="utf-8") as f:
        f.write(line)


async def main():
    import sys
    print()
    print("  ===================================")
    print("  |       ChatGPT 批量注册工具         |")
    print("  ===================================")
    print()

    # 命令行: python chatgpt_register.py [email] [并发数] [总次数]
    provider = sys.argv[1] if len(sys.argv) > 1 else "email"
    concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    total = int(sys.argv[3]) if len(sys.argv) > 3 else 1

    if provider == "email":
        print(f"  模式: 邮箱注册  并发: {concurrency}  总数: {total}")
        print()
        selected_email = os.environ.get("OPENAI3_SELECTED_ACCOUNT_EMAIL", "").strip()
        selected_emails = []
        try:
            selected_emails = [
                str(value or "").strip()
                for value in json.loads(os.environ.get("OPENAI3_SELECTED_ACCOUNT_EMAILS", "[]"))
                if str(value or "").strip()
            ]
        except Exception:
            selected_emails = []
        if selected_email and not selected_emails:
            selected_emails = [selected_email]
        if selected_emails:
            if len(selected_emails) < total:
                raise ValueError("not enough preflighted Outlook accounts")
            print(f"  邮箱: 使用启动前检查通过的 OutlookEmail 账号 ({len(selected_emails)})")

        async def _run_email_one(idx: int, sem: asyncio.Semaphore):
            async with sem:
                tag = f"[{idx}]"
                assigned_email = selected_emails[idx - 1] if idx <= len(selected_emails) else ""
                print(f"\n{'='*50}")
                print(f" {tag} 开始邮箱注册")
                print(f"{'='*50}")
                try:
                    result = await register_chatgpt_email(
                        email=assigned_email or None,
                        max_retries=2 if assigned_email else 10,
                        proxy=PROXY,
                    )
                    registered = bool(result and result.get("registered"))
                    failure_reason = str((result or {}).get("failure_reason") or "")
                    marker_status = (
                        "success" if registered
                        else "challenge_required" if failure_reason == "cloudflare_challenge"
                        else "failed"
                    )
                    marker = {
                        "status": marker_status,
                        "email": str((result or {}).get("email") or assigned_email),
                    }
                    if not registered and failure_reason:
                        marker["failure_reason"] = failure_reason
                    print("__AUTOMYAI_OPENAI3_RESULT__" + json.dumps(marker, ensure_ascii=False))
                    return result if registered else None
                except Exception as e:
                    print(f" {tag} 注册异常: {e}")
                    print("__AUTOMYAI_OPENAI3_RESULT__" + json.dumps({
                        "status": "failed",
                        "email": assigned_email,
                        "error": type(e).__name__,
                    }, ensure_ascii=False))
                    return None

        semaphore = asyncio.Semaphore(concurrency)
        tasks = [_run_email_one(i + 1, semaphore) for i in range(total)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success = sum(1 for r in results if r)
        print()
        print(f"  {'='*40}")
        print(f"  结果: {success}/{total} 成功  ({success*100//total}%)")
        print(f"  {'='*40}")
        return

    print(f"  [x] 未知模式: {provider}, 可选: email")


if __name__ == "__main__":
    asyncio.run(main())
