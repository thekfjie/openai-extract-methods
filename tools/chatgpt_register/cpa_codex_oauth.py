"""
CPA Codex OAuth 完整协议 (纯 HTTP, 无浏览器)
依赖: curl_cffi, sentinel_token

完整流程:
  1. CPA   GET  /v0/management/codex-auth-url?is_webui=true  → 获取授权链接 + state
  2. OpenAI GET  授权链接 → 跟随重定向建立 session cookies
  3. OpenAI POST /api/accounts/authorize/continue            → 提交手机号
  4. OpenAI POST /api/accounts/password/verify               → 提交密码 (登录)
  5. OpenAI POST /api/accounts/add-email/send                → 提交邮箱 (如需要)
  6. Mail   GET  /api/verify-code?email=...&keyword=openai   → 轮询邮箱验证码
  7. OpenAI POST /api/accounts/email-otp/validate            → 验证邮箱
  8. OpenAI POST /api/accounts/workspace/select              → 选择工作空间
  9. OpenAI GET  重定向 → 捕获 localhost:1455/auth/callback?code=...&state=...
  10. CPA  POST /v0/management/oauth-callback               → 提交回调 URL
  11. CPA  GET  /v0/management/get-auth-status?state=...     → 轮询认证状态

用法:
  python cpa_codex_oauth.py                          # 从 accounts.txt 随机选账号
  python cpa_codex_oauth.py +27621715527 qQYA9Wjhf0b72@7I  # 指定账号
"""

import asyncio
import json
import time
import uuid
import random
import os
from typing import Optional
from curl_cffi import requests
from sentinel_token import SentinelTokenProvider

# ============================================================
# 配置
# ============================================================
CPA_BASE = __import__("os").environ.get("CPA_BASE", "")
CPA_KEY = __import__("os").environ.get("CPA_KEY", "")

MAIL_BASE = __import__("os").environ.get("MAIL_BASE", "")
MAIL_PASS = __import__("os").environ.get("MAIL_PASS", "")

OPENAI_BASE = "https://auth.openai.com"

PROXY = __import__("os").environ.get("CHATGPT_REGISTER_PROXY", "")
IMPERSONATE = "firefox144"


def _accounts_file() -> str:
    return os.environ.get("OPENAI3_ACCOUNTS_FILE") or os.path.join(os.path.dirname(__file__), "accounts.txt")


# ============================================================
# Mail Manager Client
# ============================================================
class MailClient:
    def __init__(self):
        self._session: Optional[requests.AsyncSession] = None

    async def _get_session(self) -> requests.AsyncSession:
        if not self._session:
            self._session = requests.AsyncSession(impersonate="chrome", timeout=30)
            await self._session.post(f"{MAIL_BASE}/api/login", json={"password": MAIL_PASS})
        return self._session

    async def get_unbound_email(self) -> str:
        s = await self._get_session()
        r = await s.get(f"{MAIL_BASE}/api/random-unbound")
        if r.status_code == 404:
            return None
        return r.text.strip()

    async def poll_verify_code(
        self,
        email: str,
        timeout: int = 120,
        interval: int = 5,
        since: float = None,
        exclude_codes: set[str] | None = None,
    ) -> Optional[str]:
        """Poll a current challenge, excluding codes already submitted in it."""
        s = await self._get_session()
        from datetime import datetime, timezone
        excluded = {str(code) for code in (exclude_codes or set()) if str(code)}
        for i in range(timeout // interval):
            params = {"email": email, "keyword": "openai"}
            if since:
                params["since"] = f"{float(since):.6f}"
            if excluded:
                params["exclude_code"] = sorted(excluded)
            r = await s.get(f"{MAIL_BASE}/api/verify-code", params=params)
            try:
                d = r.json()
            except Exception:
                await asyncio.sleep(interval)
                continue
            if d.get("success"):
                code = d.get("code", "")
                if len(code) < 6:
                    await asyncio.sleep(interval)
                    continue
                if code in excluded:
                    await asyncio.sleep(interval)
                    continue
                if since:
                    mail_date = d.get("mail", {}).get("date", "")
                    if mail_date:
                        try:
                            dt = datetime.fromisoformat(mail_date.replace("Z", "+00:00"))
                            if dt.timestamp() < since:
                                await asyncio.sleep(interval)
                                continue
                        except Exception:
                            pass
                return code
            await asyncio.sleep(interval)
        return None


# ============================================================
# OpenAI Codex OAuth Client
# ============================================================
class CodexOAuthClient:
    def __init__(self, impersonate: str = IMPERSONATE, proxy: str = None):
        self.impersonate = impersonate
        self.proxy = proxy
        self.sentinel = SentinelTokenProvider(impersonate=impersonate)
        self._proxy = proxy
        self._session: Optional[requests.AsyncSession] = None
        self.device_id = str(uuid.uuid4())

    async def _get_session(self) -> requests.AsyncSession:
        if not self._session:
            kwargs = {"impersonate": self.impersonate}
            if self.proxy:
                kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
            kwargs["timeout"] = 60
            self._session = requests.AsyncSession(**kwargs)
            self.sentinel._session = self._session
        return self._session

    def _common_headers(self, referer: str = None) -> dict:
        h = {"accept": "application/json", "content-type": "application/json"}
        if referer:
            h["referer"] = referer
        return h

    async def _sentinel_headers(self, headers: dict, flow: str, referer: str) -> dict:
        token = await self.sentinel.get_token(flow, self.device_id)
        if token:
            headers["openai-sentinel-token"] = json.dumps(token)
            so_token = await self.sentinel.get_so_token(flow, self.device_id)
            if so_token:
                headers["openai-sentinel-so-token"] = json.dumps(so_token)
        return headers

    async def open_auth_url(self, auth_url: str) -> dict:
        """跟随授权链接重定向链, 建立 session cookies"""
        s = await self._get_session()
        loc = auth_url
        cookies = {}
        while loc:
            resp = await s.get(loc, allow_redirects=False)
            loc = resp.headers.get("location", "")
            for c in resp.cookies.jar:
                cookies[c.name] = c.value
        # 访问最终登录页
        resp = await s.get(f"{OPENAI_BASE}/log-in-or-create-account")
        for c in resp.cookies.jar:
            cookies[c.name] = c.value
        # 提取 device id
        for name, value in cookies.items():
            if name == "oai-did":
                self.device_id = value
                break
        self.sentinel._device_id = self.device_id
        return {"cookies": cookies, "device_id": self.device_id}

    async def authorize_continue(self, phone: str) -> dict:
        """POST /api/accounts/authorize/continue → 提交手机号"""
        s = await self._get_session()
        referer = f"{OPENAI_BASE}/log-in-or-create-account?usernameKind=phone_number"
        headers = await self._sentinel_headers(
            self._common_headers(referer=referer),
            "authorize_continue",
            referer
        )
        body = {
            "username": {"value": phone, "kind": "phone_number"},
            "screen_hint": "login_or_signup",
        }
        resp = await s.post(f"{OPENAI_BASE}/api/accounts/authorize/continue", json=body, headers=headers)
        try:
            return resp.json()
        except Exception:
            return {"error": {"code": "json_parse_error", "message": resp.text[:200]}, "status": resp.status_code}

    async def verify_password(self, password: str) -> dict:
        """POST /api/accounts/password/verify → 提交密码"""
        s = await self._get_session()
        referer = f"{OPENAI_BASE}/log-in/password"
        headers = await self._sentinel_headers(
            self._common_headers(referer=referer),
            "password_verify",
            referer
        )
        body = {"password": password}
        resp = await s.post(f"{OPENAI_BASE}/api/accounts/password/verify", json=body, headers=headers)
        try:
            result = resp.json()
        except Exception:
            return {"error": {"code": "json_parse_error", "message": resp.text[:200]}, "status": resp.status_code}
        # 如果需要邮箱验证, GET /email-verification 页面 (模拟浏览器导航, 确保邮件发出)
        pw_page = result.get("page", {}).get("type", "")
        if pw_page in ("email_otp_verification", "add_email"):
            await s.get(f"{OPENAI_BASE}/email-verification", headers=self._common_headers(referer=referer))
        return result

    async def add_email(self, email: str) -> dict:
        """POST /api/accounts/add-email/send → 提交邮箱并发送验证码"""
        s = await self._get_session()
        referer = f"{OPENAI_BASE}/add-email"
        headers = self._common_headers(referer=referer)
        body = {"email": email}
        resp = await s.post(f"{OPENAI_BASE}/api/accounts/add-email/send", json=body, headers=headers)
        try:
            result = resp.json()
        except Exception:
            return {"error": {"code": "json_parse_error", "message": resp.text[:200]}, "status": resp.status_code}
        # GET /email-verification 页面 (模拟浏览器导航, 确保邮件发出)
        ae_page = result.get("page", {}).get("type", "")
        if ae_page == "email_otp_verification":
            await s.get(f"{OPENAI_BASE}/email-verification", headers=self._common_headers(referer=referer))
        return result

    async def validate_email_otp(self, code: str) -> dict:
        """POST /api/accounts/email-otp/validate → 验证邮箱验证码"""
        s = await self._get_session()
        referer = f"{OPENAI_BASE}/email-verification"
        headers = self._common_headers(referer=referer)
        body = {"code": code}
        resp = await s.post(f"{OPENAI_BASE}/api/accounts/email-otp/validate", json=body, headers=headers)
        try:
            return resp.json()
        except Exception:
            return {"error": {"code": "json_parse_error", "message": resp.text[:200]}, "status": resp.status_code}

    async def select_workspace(self, workspace_id: str) -> dict:
        """POST /api/accounts/workspace/select → 选择工作空间, 触发 OAuth 回调重定向"""
        s = await self._get_session()
        referer = f"{OPENAI_BASE}/sign-in-with-chatgpt/codex/consent"
        headers = self._common_headers(referer=referer)
        body = {"workspace_id": workspace_id}
        resp = await s.post(f"{OPENAI_BASE}/api/accounts/workspace/select", json=body, headers=headers)
        try:
            return resp.json()
        except Exception:
            return {"error": {"code": "json_parse_error", "message": resp.text[:200]}, "status": resp.status_code}

    async def get_callback_url(self, consent_resp: dict) -> Optional[str]:
        """从 workspace/select 响应中提取回调 URL, 跟随重定向链直到 localhost"""
        s = await self._get_session()
        continue_url = consent_resp.get("continue_url", "")
        if not continue_url:
            return None
        # 如果直接就是 localhost 回调 (host 是 localhost)
        if continue_url.startswith("http://localhost") or continue_url.startswith("https://localhost"):
            return continue_url
        # 跟随重定向链直到找到 localhost 作为 host
        loc = continue_url
        for _ in range(20):
            resp = await s.get(loc, allow_redirects=False)
            loc = resp.headers.get("location", "")
            if not loc:
                break
            if loc.startswith("http://localhost") or loc.startswith("https://localhost"):
                return loc
        return None

    async def close(self):
        if self._session:
            await self._session.close()


# ============================================================
# CPA API Client
# ============================================================
class CPAClient:
    def __init__(self):
        self._session = requests.AsyncSession(impersonate="chrome", timeout=30)

    async def get_auth_url(self) -> dict:
        r = await self._session.get(
            f"{CPA_BASE}/v0/management/codex-auth-url",
            params={"is_webui": "true"},
            headers={"Authorization": f"Bearer {CPA_KEY}"},
        )
        return r.json()

    async def submit_callback(self, callback_url: str) -> dict:
        r = await self._session.post(
            f"{CPA_BASE}/v0/management/oauth-callback",
            json={"provider": "codex", "redirect_url": callback_url},
            headers={"Authorization": f"Bearer {CPA_KEY}"},
        )
        return r.json()

    async def poll_auth_status(self, state: str, timeout: int = 60, interval: int = 2) -> dict:
        for i in range(timeout // interval):
            r = await self._session.get(
                f"{CPA_BASE}/v0/management/get-auth-status",
                params={"state": state},
                headers={"Authorization": f"Bearer {CPA_KEY}"},
            )
            d = r.json()
            if d.get("status") != "wait":
                return d
            await asyncio.sleep(interval)
        return {"status": "timeout"}


# ============================================================
# 主流程
# ============================================================
async def codex_oauth(phone: str, password: str, proxy: str = None):
    print("  ┌─────────────────────────────┐")
    print("  │   Codex OAuth 授权           │")
    print("  └─────────────────────────────┘")

    cpa = CPAClient()
    mail = MailClient()
    oauth = CodexOAuthClient(proxy=proxy)
    used_email = None

    try:
        # 1. 获取授权链接
        print("  ❏ 获取 CPA 授权链接...")
        auth_data = await cpa.get_auth_url()
        if auth_data.get("status") != "ok":
            print(f"  ✗ 失败: {auth_data}")
            return False
        auth_url = auth_data["url"]
        state = auth_data["state"]
        print(f"  ✓ state: {state[:16]}...")

        # 2. 打开授权链接, 建立 session
        print("  ❏ 建立 OpenAI session...")
        session_info = await oauth.open_auth_url(auth_url)
        print(f"  ✓ device_id: {session_info['device_id'][:12]}...")

        # 3. 提交手机号
        print(f"  ❏ 提交手机号: {phone}...")
        ac_result = await oauth.authorize_continue(phone)
        continue_url = ac_result.get("continue_url", "")
        page_type = ac_result.get("page", {}).get("type", "")

        if page_type != "login_password":
            print(f"  ✗ 非登录密码页: {page_type}")
            return False
        print("  ✓ 登录密码页")

        # 4. 提交密码
        print("  ❏ 提交密码...")
        pw_since = time.time()
        pw_result = await oauth.verify_password(password)
        pw_page = pw_result.get("page", {}).get("type", "")

        if not pw_page:
            if "error" in pw_result:
                print(f"  ✗ 错误: {pw_result['error']}")
                return False
            if "oai-client-auth-session" in pw_result:
                pw_page = "add_email"

        if pw_page == "add_email":
            # 5-7. 循环: 获取邮箱 → 提交 → 等验证码(20s) → 超时换邮箱
            max_email_retries = 5
            for email_attempt in range(1, max_email_retries + 1):
                print(f"  ❏ 获取邮箱 ({email_attempt}/{max_email_retries})...")
                email = await mail.get_unbound_email()
                if not email:
                    print("  ✗ 没有可用邮箱")
                    return False
                used_email = email
                print(f"  ✓ 邮箱: {email}")

                # 6. 提交邮箱
                print("  ❏ 提交邮箱...")
                ae_result = await oauth.add_email(email)
                ae_page = ae_result.get("page", {}).get("type", "")

                if ae_page != "email_otp_verification":
                    print(f"  ✗ 邮箱提交失败: {ae_result}")
                    return False
                print("  ✓ 验证码已发送")

                # 7. 轮询邮箱验证码 (20秒超时)
                print("  ❏ 等待邮箱验证码 (20s)...")
                code = await mail.poll_verify_code(email, timeout=20, interval=5, since=pw_since)
                if not code:
                    print("  ⚠ 超时, 换邮箱重试")
                    continue
                print(f"  ✓ 验证码: {code}")
                break
            else:
                print("  ✗ 多次换邮箱仍未收到验证码")
                return False

            # 8. 验证邮箱
            print("  ❏ 验证邮箱...")
            ev_result = await oauth.validate_email_otp(code)
            ev_page = ev_result.get("page", {}).get("type", "")

            if ev_page != "sign_in_with_chatgpt_codex_consent":
                print(f"  ✗ 验证后非授权页: {ev_page}")
                return False
            print("  ✓ 邮箱验证通过")

            # 9. 选择工作空间
            print("  ❏ 选择工作空间...")
            workspace_id = ev_result.get("oai-client-auth-session", {}).get("workspaces", [{}])[0].get("id", "")
            if not workspace_id:
                print("  ✗ 未找到 workspace_id")
                return False
            print(f"  ✓ workspace: {workspace_id[:16]}...")

            ws_result = await oauth.select_workspace(workspace_id)

            # 10. 捕获回调 URL
            print("  ❏ 捕获回调 URL...")
            callback_url = await oauth.get_callback_url(ws_result)
            if not callback_url:
                print("  ✗ 未能捕获回调 URL")
                return False
            print(f"  ✓ 回调: {callback_url[:40]}...")

        elif pw_page == "email_otp_verification":
            # 账号已有邮箱, 记录邮箱即可
            session_data = pw_result.get("oai-client-auth-session", {})
            email = session_data.get("email", "")
            if not email:
                print("  ✗ 未找到邮箱地址")
                return {"success": False}
            used_email = email
            print(f"  ✓ 账号已有邮箱: {email}")
            return {"success": True, "phone": phone, "email": used_email}

        elif pw_page == "sign_in_with_chatgpt_codex_consent":
            # 已有邮箱, 直接同意
            print("  ❏ 已有邮箱, 选择工作空间...")
            workspace_id = pw_result.get("oai-client-auth-session", {}).get("workspaces", [{}])[0].get("id", "")
            if not workspace_id:
                print("  ✗ 未找到 workspace_id")
                return False
            print(f"  ✓ workspace: {workspace_id[:16]}...")

            ws_result = await oauth.select_workspace(workspace_id)
            callback_url = await oauth.get_callback_url(ws_result)
            if not callback_url:
                print("  ✗ 未能捕获回调 URL")
                return False
            print(f"  ✓ 回调: {callback_url[:40]}...")
        else:
            print(f"  ✗ 未知页面类型: {pw_page}")
            return False

        # 11. 提交回调到 CPA
        print("  ❏ 提交回调到 CPA...")
        cb_result = await cpa.submit_callback(callback_url)

        if cb_result.get("status") != "ok":
            print(f"  ✗ 提交失败: {cb_result}")
            return False
        print("  ✓ 已提交")

        # 12. 轮询认证状态
        print("  ❏ 轮询 CPA 认证状态...")
        status = await cpa.poll_auth_status(state, timeout=60, interval=2)

        if status.get("status") == "ok":
            print()
            print("  ┌──────────────────────────────────┐")
            print("  │ ✓ Codex OAuth 认证成功            │")
            print("  ├──────────────────────────────────┤")
            print(f"  │ 手机号: {phone:<24} │")
            if used_email:
                print(f"  │ 邮箱:   {used_email:<24} │")
            print("  └──────────────────────────────────┘")
            return {"success": True, "phone": phone, "email": used_email}
        else:
            print(f"  ✗ 认证失败: {status}")
            return {"success": False}

    finally:
        await oauth.close()


def pick_account(phone: str = None, password: str = None) -> tuple:
    """从 accounts.txt 随机选账号, 或使用指定的"""
    if phone and password:
        return phone, password

    accounts_file = _accounts_file()
    accounts = []
    if os.path.exists(accounts_file):
        with open(accounts_file, "r") as f:
            for line in f:
                line = line.strip()
                if "|" in line:
                    parts = line.split("|", 1)
                    p = parts[0].strip()
                    pw = parts[1].strip()
                    if p and pw:
                        accounts.append((p, pw))

    if not accounts:
        print("没有可用账号")
        return None, None

    return random.choice(accounts)


_async_file_lock = None

def _get_async_lock():
    global _async_file_lock
    if _async_file_lock is None:
        import asyncio
        _async_file_lock = asyncio.Lock()
    return _async_file_lock


async def update_accounts_file(phone: str, email: str):
    """在 accounts.txt 中给对应手机号行追加邮箱 (异步安全)"""
    accounts_file = _accounts_file()
    if not os.path.exists(accounts_file):
        return
    lock = _get_async_lock()
    async with lock:
        await asyncio.to_thread(_update_accounts_file_sync, accounts_file, phone, email)


def _update_accounts_file_sync(accounts_file: str, phone: str, email: str):
    """同步文件读写 (供 asyncio.to_thread 调用)"""
    with open(accounts_file, "r") as f:
        lines = f.readlines()
    updated = False
    with open(accounts_file, "w") as f:
        for line in lines:
            stripped = line.strip()
            if "|" in stripped:
                parts = stripped.split("|", 1)
                p = parts[0].strip()
                if p == phone and email not in stripped:
                    f.write(f"{stripped} | {email}\n")
                    updated = True
                    print(f"已更新 accounts.txt: {phone} -> {email}")
                else:
                    f.write(line)
            else:
                f.write(line)
    if not updated:
        print(f"未在 accounts.txt 中找到 {phone} 或邮箱已存在")


def get_all_accounts_without_email() -> list:
    """获取所有还没有邮箱的账号"""
    accounts_file = _accounts_file()
    accounts = []
    if os.path.exists(accounts_file):
        with open(accounts_file, "r") as f:
            for line in f:
                line = line.strip()
                if "|" in line:
                    parts = line.split("|")
                    p = parts[0].strip()
                    pw = parts[1].strip()
                    has_email = len(parts) > 2 and parts[2].strip()
                    if p and pw and not has_email:
                        accounts.append((p, pw))
    return accounts


async def run_one(phone: str, password: str, semaphore: asyncio.Semaphore):
    """并发执行单个账号的 OAuth 流程"""
    async with semaphore:
        print(f"\n{'='*40}")
        print(f"使用账号: {phone}")
        print(f"{'='*40}")
        try:
            result = await codex_oauth(phone, password, proxy=PROXY)
            if isinstance(result, dict) and result.get("success") and result.get("email"):
                await update_accounts_file(result["phone"], result["email"])
        except Exception as e:
            print(f"    异常: {e}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        # 单账号模式
        phone = sys.argv[1]
        password = sys.argv[2]
        print(f"使用账号: {phone}")

        async def _single():
            result = await codex_oauth(phone, password, proxy=PROXY)
            if isinstance(result, dict) and result.get("success") and result.get("email"):
                await update_accounts_file(result["phone"], result["email"])
            return result

        asyncio.run(_single())
    else:
        # 并发模式: 处理所有缺邮箱的账号
        accounts = get_all_accounts_without_email()
        if not accounts:
            print("所有账号都已绑定邮箱")
        else:
            print(f"共 {len(accounts)} 个账号需要处理, 并发数: 5")
            semaphore = asyncio.Semaphore(5)

            async def main():
                tasks = [run_one(p, pw, semaphore) for p, pw in accounts]
                await asyncio.gather(*tasks)

            asyncio.run(main())
