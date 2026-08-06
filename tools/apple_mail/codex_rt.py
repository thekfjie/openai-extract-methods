#!/usr/bin/env python3
"""Apple Mail post-register Codex RT acquisition.

Design:
- Same proxy / impersonate / device_id as the register session (per-account isolation)
- Prefer CPA Codex OAuth link (management codex-auth-url)
- Drive authorize with existing ChatGPT session cookies
- On add-phone: take number from Automyai TeleAuto phone pool and SMS OTP
- Intercept localhost callback and submit to CPA
- Also support direct PKCE token exchange as fallback to hold RT locally
- Normalize/decode tokens with automyai converters (gpt2codex style)
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import secrets as _secrets_mod
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse

ROOT = Path('/opt/automyai')
sys.path.insert(0, str(ROOT))

from curl_cffi import requests  # noqa: E402

from integrations.common import decode_jwt_payload  # noqa: E402
from converters.openai_formats import convert_openai, normalize_account  # noqa: E402


OPENAI_AUTH = 'https://auth.openai.com'
CHATGPT = 'https://chatgpt.com'
DEFAULT_CLIENT_ID = 'app_EMoamEEZ73f0CkXaXp7hrann'
DEFAULT_REDIRECT = 'http://localhost:1455/auth/callback'
DEFAULT_SCOPE = 'openid email profile offline_access'


def _decode_qp(text: str) -> str:
    if not text:
        return ''
    text = text.replace('=\r\n', '').replace('=\n', '')
    text = text.replace('=3D', '=').replace('=20', ' ')
    def repl(m):
        try:
            return bytes.fromhex(m.group(1)).decode('utf-8', 'ignore')
        except Exception:
            return m.group(0)
    return re.sub(r'=([0-9A-Fa-f]{2})', repl, text)


def extract_mail_otp_code(blob: str) -> str:
    """Extract OpenAI login OTP from raw MIME / HTML mail body."""
    if not blob:
        return ''
    text = _decode_qp(blob)
    for ptn in [
        r"temporary verification code to continue[:\s]*([0-9]{6})",
        r"enter this code[:\s]*([0-9]{6})",
        r"Your temporary ChatGPT (?:login|verification) code[^0-9]{0,80}([0-9]{6})",
        r"<h1[^>]*>\s*([0-9]{6})\s*</h1>",
        r"验证码[^0-9]{0,40}([0-9]{6})",
        r"临时验证码[^0-9]{0,40}([0-9]{6})",
    ]:
        m = re.search(ptn, text, re.I)
        if m and m.group(1) != '353740':
            return m.group(1)
    for m in re.finditer(r"(?m)^[ \t>]*([0-9]{6})[ \t]*\r?$", text):
        code = m.group(1)
        if code == '353740':
            continue
        left = text[max(0, m.start()-80):m.start()].lower()
        if any(x in left for x in ['color:#', 'color: #', 'background-color', 'received:', 'message-id', 'dkim', 'arc-']):
            continue
        return code
    return ''



def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(_secrets_mod.token_bytes(64))
    if len(verifier) < 43:
        verifier = (verifier + ('A' * 43))[:43]
    challenge = _b64url(hashlib.sha256(verifier.encode('utf-8')).digest())
    return verifier, challenge


def load_main_config() -> dict[str, Any]:
    path = ROOT / 'config.json'
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


class TeleAutoPhonePool:
    """Thin client for Automyai's TeleAuto phone pool (sms-api-proxy)."""

    def __init__(self, cfg: Optional[dict[str, Any]] = None):
        cfg = cfg or load_main_config()
        self.enabled = str(cfg.get('TELE_AUTO_ENABLED', 'true')).lower() in {'1', 'true', 'yes', 'on'}
        self.base_url = str(cfg.get('TELE_AUTO_API_URL') or 'http://127.0.0.1:8028').rstrip('/')
        self.username = str(cfg.get('TELE_AUTO_USERNAME') or '')
        self.password = str(cfg.get('TELE_AUTO_PASSWORD') or '')
        self.timeout = 20
        self.record: dict[str, Any] = {}
        self.completed = False

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.base_url and self.username and self.password)

    def _auth_header(self) -> str:
        token = base64.b64encode(f'{self.username}:{self.password}'.encode()).decode('ascii')
        return f'Basic {token}'

    def _request(self, method: str, path_or_url: str, *, body: Optional[dict] = None, auth: bool = True) -> Any:
        if path_or_url.startswith('http://') or path_or_url.startswith('https://'):
            url = path_or_url
        else:
            url = f"{self.base_url}{path_or_url if path_or_url.startswith('/') else '/' + path_or_url}"
        headers = {
            'Accept': 'application/json,text/plain;q=0.9,*/*;q=0.8',
            'User-Agent': 'apple-mail-codex-rt/1.0',
        }
        if auth:
            headers['Authorization'] = self._auth_header()
        kwargs: dict[str, Any] = {'headers': headers, 'timeout': self.timeout}
        if body is not None:
            headers['Content-Type'] = 'application/json'
            kwargs['data'] = json.dumps(body, ensure_ascii=False)
        s = requests.Session(impersonate='chrome')
        try:
            resp = s.request(method, url, **kwargs)
        finally:
            try:
                s.close()
            except Exception:
                pass
        text = (resp.text or '').strip()
        if resp.status_code >= 400:
            raise RuntimeError(f'TeleAuto HTTP {resp.status_code}: {text[:200]}')
        if not text:
            return {}
        if text.startswith('{') or text.startswith('['):
            return json.loads(text)
        return text

    def issue(self) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError('TeleAuto 未配置')
        payload = self._request('POST', '/api/auto/account', body=None, auth=True)
        if not isinstance(payload, dict):
            raise RuntimeError('TeleAuto 出号返回异常')
        if str(payload.get('code', '0')) not in {'0', ''}:
            raise RuntimeError(str(payload.get('msg') or payload.get('error') or 'TeleAuto 出号失败'))
        data = payload.get('data') if isinstance(payload.get('data'), dict) else payload
        phone = str(data.get('phone') or data.get('phoneNumber') or '').strip()
        public_url = str(data.get('url') or data.get('publicUrl') or data.get('smsUrl') or '').strip()
        if not phone or not public_url:
            raise RuntimeError('TeleAuto 出号缺少 phone/url')
        if not phone.startswith('+'):
            phone = '+' + re.sub(r'\D', '', phone)
        # localize sms url when possible
        sms_url = public_url
        parsed = urlparse(public_url)
        if parsed.path.startswith('/api/') and self.base_url:
            sms_url = f"{self.base_url}{parsed.path}{'?' + parsed.query if parsed.query else ''}"
        self.record = {
            'phoneNumber': phone,
            'publicUrl': public_url,
            'smsUrl': sms_url,
            'line': data.get('line') or '',
            'raw': data,
        }
        self.completed = False
        return self.record

    @staticmethod
    def _extract_code(payload: Any) -> str:
        texts: list[str] = []
        if isinstance(payload, dict):
            for key in ('sms', 'smsCode', 'codeValue', 'message', 'msg', 'text', 'data', 'raw', 'code'):
                value = payload.get(key)
                if isinstance(value, (dict, list)):
                    texts.append(json.dumps(value, ensure_ascii=False))
                elif value not in (None, ''):
                    texts.append(str(value))
        else:
            texts.append(str(payload or ''))
        for text in texts:
            low = text.lower()
            if low.startswith('no|') or 'waiting' in low:
                continue
            m = re.search(r'(?<!\d)(\d{4,8})(?!\d)', text)
            if m:
                return m.group(1)
        return ''

    def wait_code(self, timeout: int = 120, interval: float = 3.0) -> str:
        if not self.record:
            raise RuntimeError('TeleAuto 尚未出号')
        sms_url = str(self.record.get('smsUrl') or self.record.get('publicUrl') or '')
        deadline = time.time() + max(20, timeout)
        while time.time() < deadline:
            payload = self._request('GET', sms_url, auth=False)
            code = self._extract_code(payload)
            if code:
                return code
            time.sleep(interval)
        raise RuntimeError('TeleAuto 等待短信超时')

    def release(self, fail: bool = False) -> None:
        if not self.record:
            return
        value = self.record.get('publicUrl') or self.record.get('smsUrl') or self.record.get('line') or ''
        if not value:
            return
        path = '/api/auto/account/fail' if fail else '/api/auto/account/release'
        try:
            self._request('POST', path, body={'url': value}, auth=True)
        except Exception:
            pass

    def mark_success(self) -> None:
        self.completed = True
        self.release(fail=False)

    def cleanup(self) -> None:
        if self.record and not self.completed:
            self.release(fail=True)
        self.record = {}


class CpaManagement:
    def __init__(self, cfg: Optional[dict[str, Any]] = None):
        cfg = cfg or load_main_config()
        self.enabled = str(cfg.get('CPA_ENABLED', 'true')).lower() in {'1', 'true', 'yes', 'on'}
        self.base = str(cfg.get('CPA_REMOTE_URL') or os.environ.get('CPA_BASE') or '').rstrip('/')
        self.key = str(cfg.get('CPA_MANAGEMENT_KEY') or os.environ.get('CPA_KEY') or '').strip()
        self.auth_dir = Path(str(cfg.get('CPA_AUTH_DIR') or '/opt/cliproxyapi/auths'))
        self._session = requests.Session(impersonate='chrome', timeout=30)

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.base and self.key)

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass

    def _headers(self) -> dict[str, str]:
        return {
            'Authorization': f'Bearer {self.key}',
            'X-Management-Key': self.key,
            'Accept': 'application/json',
        }

    def get_codex_auth_url(self) -> dict[str, Any]:
        r = self._session.get(
            f'{self.base}/v0/management/codex-auth-url',
            params={'is_webui': 'true'},
            headers=self._headers(),
            timeout=20,
        )
        data = r.json() if r.text else {}
        if r.status_code >= 400:
            raise RuntimeError(f'CPA codex-auth-url HTTP {r.status_code}: {str(data)[:200]}')
        if str(data.get('status') or '').lower() not in {'ok', 'success', ''}:
            # some builds only return url/state
            if not data.get('url'):
                raise RuntimeError(f'CPA codex-auth-url failed: {data}')
        if not data.get('url'):
            raise RuntimeError(f'CPA codex-auth-url missing url: {data}')
        return data

    def submit_callback(self, callback_url: str) -> dict[str, Any]:
        r = self._session.post(
            f'{self.base}/v0/management/oauth-callback',
            json={'provider': 'codex', 'redirect_url': callback_url},
            headers=self._headers(),
            timeout=30,
        )
        data = r.json() if r.text else {}
        if r.status_code >= 400:
            raise RuntimeError(f'CPA oauth-callback HTTP {r.status_code}: {str(data)[:200]}')
        return data

    def poll_auth_status(self, state: str, timeout: int = 90, interval: float = 2.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            r = self._session.get(
                f'{self.base}/v0/management/get-auth-status',
                params={'state': state},
                headers=self._headers(),
                timeout=20,
            )
            try:
                last = r.json() if r.text else {}
            except Exception:
                last = {'raw': (r.text or '')[:300], 'http': r.status_code}
            status = str(last.get('status') or '').lower()
            if status and status not in {'wait', 'pending', 'processing'}:
                return last
            # some builds return tokens directly without status field
            if last.get('refresh_token') or last.get('access_token') or last.get('file_name') or last.get('auth'):
                return last
            time.sleep(interval)
        last = dict(last or {})
        last['status'] = last.get('status') or 'timeout'
        return last

    def list_auth_files(self) -> list[dict[str, Any]]:
        r = self._session.get(f'{self.base}/v0/management/auth-files', headers=self._headers(), timeout=20)
        data = r.json() if r.text else {}
        files = data.get('files') if isinstance(data, dict) else data
        return files if isinstance(files, list) else []

    def upload_codex_auth(self, email: str, tokens: dict[str, Any]) -> dict[str, Any]:
        entry = {
            'type': 'codex',
            'email': email,
            'name': email,
            'disabled': False,
            'access_token': tokens.get('access_token') or '',
            'refresh_token': tokens.get('refresh_token') or '',
            'id_token': tokens.get('id_token') or '',
            'session_token': tokens.get('session_token') or '',
            'account_id': tokens.get('account_id') or '',
            'chatgpt_account_id': tokens.get('account_id') or '',
            'plan_type': tokens.get('plan_type') or 'free',
            'chatgpt_plan_type': tokens.get('plan_type') or 'free',
            'expired': tokens.get('expired') or '',
            'last_refresh': tokens.get('last_refresh') or _now_iso(),
        }
        filename = f"codex-{email}.json"
        content = json.dumps(entry, ensure_ascii=False, indent=2).encode('utf-8')
        # local write first
        local_path = ''
        try:
            self.auth_dir.mkdir(parents=True, exist_ok=True)
            path = self.auth_dir / filename
            path.write_bytes(content)
            path.chmod(0o600)
            local_path = str(path)
        except Exception as e:
            local_path = f'write_error:{e}'
        # remote multipart
        boundary = f'----applemail{secrets.token_hex(8)}'
        body = b''.join([
            f'--{boundary}\r\nContent-Disposition: form-data; name="name"\r\n\r\n{filename}\r\n'.encode(),
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f'Content-Type: application/json\r\n\r\n'
            ).encode() + content + b'\r\n',
            f'--{boundary}--\r\n'.encode(),
        ])
        headers = self._headers()
        headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
        remote: dict[str, Any]
        try:
            r = self._session.post(f'{self.base}/v0/management/auth-files', data=body, headers=headers, timeout=30)
            try:
                payload = r.json()
            except Exception:
                payload = {'raw': (r.text or '')[:300]}
            remote = {'ok': 200 <= r.status_code < 300, 'status': r.status_code, 'payload': payload}
        except Exception as e:
            remote = {'ok': False, 'error': str(e)}
        return {'filename': filename, 'localPath': local_path, 'remote': remote, 'entry': entry}


class CodexRtClient:
    def __init__(
        self,
        *,
        proxy: str,
        impersonate: str,
        device_id: str = '',
        session_token: str = '',
        access_token: str = '',
        email: str = '',
        password: str = '',
        user_agent: str = '',
        mail_otp_wait=None,
    ):
        if not proxy:
            raise RuntimeError('proxy required for Codex RT (isolation)')
        self.proxy = proxy
        self.impersonate = impersonate or 'chrome131'
        self.device_id = device_id or str(__import__('uuid').uuid4())
        self.session_token = session_token or ''
        self.access_token = access_token or ''
        self.email = email or ''
        self.password = password or ''
        self.user_agent = user_agent or (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.86 Safari/537.36'
        )
        self.mail_otp_wait = mail_otp_wait  # optional sync callable(email, since, timeout)->code
        self.session = requests.Session(impersonate=self.impersonate, timeout=60, proxies={
            'http': proxy,
            'https': proxy,
        })
        # seed cookies / identity (same account = same device + same proxy)
        self.session.cookies.set('oai-did', self.device_id, domain='.chatgpt.com')
        self.session.cookies.set('oai-did', self.device_id, domain='.openai.com')
        self.session.cookies.set('oai-did', self.device_id, domain='auth.openai.com')
        if self.session_token:
            self.session.cookies.set('__Secure-next-auth.session-token', self.session_token, domain='.chatgpt.com')
        self.phone_pool = TeleAutoPhonePool()
        self.cpa = CpaManagement()
        self.logs: list[str] = []
        self._last_sentinel = ''
        self._login_driven = False
        self._phone_done = False

    def log(self, msg: str) -> None:
        line = f'[{time.strftime("%H:%M:%S")}] {msg}'
        self.logs.append(line)
        print(line, flush=True)

    def close(self) -> None:
        try:
            self.phone_pool.cleanup()
        except Exception:
            pass
        try:
            self.cpa.close()
        except Exception:
            pass
        try:
            self.session.close()
        except Exception:
            pass

    def _headers(self, referer: str = '', accept: str = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8') -> dict[str, str]:
        h = {
            'Accept': accept,
            'User-Agent': self.user_agent,
            'oai-device-id': self.device_id,
        }
        if referer:
            h['Referer'] = referer
        return h

    def refresh_session(self) -> dict[str, Any]:
        headers = self._headers('https://chatgpt.com/', accept='application/json')
        # ensure cookie on both domain forms
        if self.session_token:
            for dom in ('.chatgpt.com', 'chatgpt.com'):
                try:
                    self.session.cookies.set('__Secure-next-auth.session-token', self.session_token, domain=dom)
                except Exception:
                    pass
        r = self.session.get(f'{CHATGPT}/api/auth/session', headers=headers, timeout=30)
        data = {}
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {'raw': (r.text or '')[:200], 'status': r.status_code}
        if not isinstance(data, dict):
            data = {'raw': data, 'status': getattr(r, 'status_code', None)}
        at = str(data.get('accessToken') or data.get('access_token') or '')
        st = str(data.get('sessionToken') or data.get('session_token') or '')
        if at:
            self.access_token = at
        if st:
            self.session_token = st
            for dom in ('.chatgpt.com', 'chatgpt.com'):
                try:
                    self.session.cookies.set('__Secure-next-auth.session-token', st, domain=dom)
                except Exception:
                    pass
        # 403/empty is non-fatal: Codex authorize can still drive auth.openai.com login
        if not at and not data.get('user'):
            data.setdefault('status', getattr(r, 'status_code', None))
            data.setdefault('keptExistingTokens', True)
            data.setdefault('hasAccessToken', bool(self.access_token))
            data.setdefault('hasSessionToken', bool(self.session_token))
            self.log(f"session refresh soft-fail status={data.get('status')} keep existing ST/AT")
        email = ''
        user = data.get('user') if isinstance(data, dict) else None
        if isinstance(user, dict):
            email = str(user.get('email') or '')
            if email:
                self.email = email
        return data

    @staticmethod
    def _callback_has_code(url: str, redirect_uri: str = DEFAULT_REDIRECT) -> bool:
        if not url:
            return False
        try:
            cb_base = (redirect_uri or '').split('?', 1)[0].rstrip('/')
            target = url.split('?', 1)[0].rstrip('/')
            if cb_base and target == cb_base:
                qs = parse_qs(urlparse(url).query)
                return bool((qs.get('code', [''])[0] or '').strip())
        except Exception:
            return False
        return False

    def _is_add_phone(self, url: str = '', page: str = '') -> bool:
        blob = f'{url} {page}'.lower()
        return 'add-phone' in blob or 'add_phone' in blob

    def _phone_headers(self, referer: str) -> dict[str, str]:
        return {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Origin': OPENAI_AUTH,
            'Referer': referer,
            'User-Agent': self.user_agent,
            'oai-device-id': self.device_id,
        }

    def add_phone_send(self, phone: str) -> dict[str, Any]:
        r = self.session.post(
            f'{OPENAI_AUTH}/api/accounts/add-phone/send',
            headers=self._phone_headers(f'{OPENAI_AUTH}/add-phone'),
            json={'phone_number': phone},
            timeout=30,
        )
        if r.status_code != 200:
            raise RuntimeError(f'add-phone/send {r.status_code}: {(r.text or "")[:180]}')
        try:
            return r.json()
        except Exception:
            return {}

    def phone_otp_validate(self, code: str) -> dict[str, Any]:
        r = self.session.post(
            f'{OPENAI_AUTH}/api/accounts/phone-otp/validate',
            headers=self._phone_headers(f'{OPENAI_AUTH}/phone-verification'),
            json={'code': code},
            timeout=30,
        )
        if r.status_code != 200:
            raise RuntimeError(f'phone-otp/validate {r.status_code}: {(r.text or "")[:180]}')
        try:
            return r.json()
        except Exception:
            return {}

    def phone_otp_resend(self) -> bool:
        r = self.session.post(
            f'{OPENAI_AUTH}/api/accounts/phone-otp/resend',
            headers=self._phone_headers(f'{OPENAI_AUTH}/phone-verification'),
            timeout=30,
        )
        return r.status_code == 200

    def handle_add_phone(self, max_attempts: int = 3) -> dict[str, Any]:
        if not self.phone_pool.configured:
            raise RuntimeError('需要绑手机但 TeleAuto 号码池未配置')
        last_err: Optional[Exception] = None
        for i in range(1, max_attempts + 1):
            self.log(f'add-phone 尝试 {i}/{max_attempts}：从号码池取号')
            try:
                rec = self.phone_pool.issue()
                phone = rec['phoneNumber']
                self.log(f'拿到号码 {phone}')
                self.add_phone_send(phone)
                self.log('已触发短信，等待验证码')
                code = self.phone_pool.wait_code(timeout=120, interval=3)
                self.log(f'收到短信验证码 {code}')
                result = self.phone_otp_validate(code)
                self.phone_pool.mark_success()
                self._phone_done = True
                cont = self._continue_url(result) if isinstance(result, dict) else ''
                self.log(f'手机号绑定成功 cont={(cont or "-")[:100]}')
                return {'ok': True, 'phone': phone, 'result': result, 'continue_url': cont}
            except Exception as e:
                last_err = e
                self.log(f'add-phone 失败: {e}')
                try:
                    self.phone_pool.cleanup()
                except Exception:
                    pass
                time.sleep(1.0)
        raise RuntimeError(f'add-phone 失败: {last_err}')


    def _page_type(self, data: dict) -> str:
        if not isinstance(data, dict):
            return ''
        page = data.get('page')
        if isinstance(page, dict):
            return str(page.get('type') or page.get('name') or '').strip()
        return str(data.get('page') or data.get('page_type') or '').strip()

    def _continue_url(self, data: dict) -> str:
        if not isinstance(data, dict):
            return ''
        for key in ('continue_url', 'continueUrl', 'url', 'redirect_url'):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        page = data.get('page') if isinstance(data.get('page'), dict) else {}
        payload = page.get('payload') if isinstance(page.get('payload'), dict) else {}
        for key in ('continue_url', 'continueUrl', 'url'):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return ''

    def _json_headers(self, referer: str, sentinel: bool = False) -> dict[str, str]:
        h = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Origin': OPENAI_AUTH,
            'Referer': referer,
            'User-Agent': self.user_agent,
            'oai-device-id': self.device_id,
        }
        if sentinel and self._last_sentinel:
            # sentinel provider returns dict; OpenAI expects JSON string
            tok = self._last_sentinel
            if isinstance(tok, dict):
                h['openai-sentinel-token'] = json.dumps(tok, separators=(',', ':'))
            else:
                h['openai-sentinel-token'] = str(tok)
        return h

    def _get_sentinel(self, flow: str = 'authorize_continue') -> str:
        """Best-effort sentinel token for auth.openai.com login drive.

        Uses a dedicated temporary session with the SAME proxy/impersonate/device.
        Never closes the main Codex session.
        """
        try:
            import asyncio
            engine = Path('/opt/automyai/tools/chatgpt_register')
            if str(engine) not in sys.path:
                sys.path.insert(0, str(engine))
            from sentinel_token import SentinelTokenProvider

            async def _run():
                provider = SentinelTokenProvider(impersonate=self.impersonate)
                # dedicated session (do NOT share/close main session)
                try:
                    if hasattr(provider, '__init__'):
                        # recreate with proxy if supported by wrapper in chatgpt_register
                        pass
                except Exception:
                    pass
                # inject proxy on provider session creation
                old_get = getattr(provider, '_get_session', None)
                async def _get_session_proxy():
                    if getattr(provider, '_session', None) is None:
                        kwargs = {'impersonate': self.impersonate, 'timeout': 60}
                        if self.proxy:
                            kwargs['proxies'] = {'http': self.proxy, 'https': self.proxy}
                        provider._session = requests.AsyncSession(**kwargs)
                    return provider._session
                if old_get is not None:
                    provider._get_session = _get_session_proxy  # type: ignore
                try:
                    cookies = {c.name: c.value for c in getattr(self.session.cookies, 'jar', [])}
                    if hasattr(provider, 'set_cookies'):
                        provider.set_cookies(cookies)
                except Exception:
                    pass
                token = await provider.get_token(flow, self.device_id)
                try:
                    await provider.close()
                except Exception:
                    pass
                return token

            try:
                token = asyncio.run(_run())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    token = loop.run_until_complete(_run())
                finally:
                    loop.close()
            if token:
                self._last_sentinel = token
                return token if isinstance(token, str) else json.dumps(token, separators=(',', ':'))
        except Exception as e:
            self.log(f'sentinel 获取失败(可继续): {e}')
        return ''

    def authorize_continue_email(self, email: str, screen_hint: str = 'login') -> dict[str, Any]:
        self._get_sentinel('authorize_continue')
        referer = f'{OPENAI_AUTH}/log-in' if screen_hint == 'login' else f'{OPENAI_AUTH}/create-account'
        r = self.session.post(
            f'{OPENAI_AUTH}/api/accounts/authorize/continue',
            headers=self._json_headers(referer, sentinel=True),
            json={'username': {'value': email, 'kind': 'email'}, 'screen_hint': screen_hint},
            timeout=30,
        )
        if r.status_code != 200:
            raise RuntimeError(f'authorize/continue HTTP {r.status_code}: {(r.text or "")[:220]}')
        try:
            return r.json()
        except Exception:
            return {}

    def login_password_verify(self, password: str) -> dict[str, Any]:
        r = self.session.post(
            f'{OPENAI_AUTH}/api/accounts/password/verify',
            headers=self._json_headers(f'{OPENAI_AUTH}/log-in/password', sentinel=True),
            json={'password': password},
            timeout=30,
        )
        if r.status_code != 200:
            raise RuntimeError(f'password/verify HTTP {r.status_code}: {(r.text or "")[:220]}')
        try:
            return r.json()
        except Exception:
            return {}

    def email_otp_resend(self) -> bool:
        r = self.session.post(
            f'{OPENAI_AUTH}/api/accounts/email-otp/resend',
            headers=self._json_headers(f'{OPENAI_AUTH}/email-verification', sentinel=True),
            json={},
            timeout=30,
        )
        return r.status_code == 200

    def email_otp_validate(self, code: str) -> dict[str, Any]:
        r = self.session.post(
            f'{OPENAI_AUTH}/api/accounts/email-otp/validate',
            headers=self._json_headers(f'{OPENAI_AUTH}/email-verification', sentinel=True),
            json={'code': code},
            timeout=30,
        )
        if r.status_code != 200:
            raise RuntimeError(f'email-otp/validate HTTP {r.status_code}: {(r.text or "")[:220]}')
        try:
            return r.json()
        except Exception:
            return {}

    def wait_email_otp(self, email: str, since: float, timeout: int = 120) -> str:
        if not callable(self.mail_otp_wait):
            raise RuntimeError('需要邮箱 OTP，但未提供 mail_otp_wait')
        code = self.mail_otp_wait(email=email, since=since, timeout=timeout)
        code = str(code or '').strip()
        if not code:
            raise RuntimeError('邮箱 OTP 等待超时')
        return code

    def drive_login_from_log_in(self) -> str:
        """When Codex OAuth falls back to /log-in, drive protocol login in same environment."""
        email = (self.email or '').strip()
        if not email:
            raise RuntimeError('log-in 推进缺少 email')
        password = (self.password or '').strip()
        self.log(f'Codex 落到 /log-in，开始协议登录推进 email={email}')
        step = self.authorize_continue_email(email, screen_hint='login')
        page = self._page_type(step)
        cont = self._continue_url(step)
        self.log(f'authorize/continue => page={page or "-"} cont={(cont or "-")[:100]}')

        if page == 'login_password' or '/log-in/password' in (cont or ''):
            if not password:
                raise RuntimeError('需要密码登录但 password 为空')
            step = self.login_password_verify(password)
            page = self._page_type(step)
            cont = self._continue_url(step)
            self.log(f'password/verify => page={page or "-"} cont={(cont or "-")[:100]}')

        need_otp = page in {'email_otp_verification', 'email_otp'} or '/email-verification' in (cont or '')
        if need_otp:
            since = time.time() - 8
            force_resend = os.environ.get('APPLE_MAIL_FORCE_RESEND', '').strip().lower() in {'1', 'true', 'yes', 'on'}
            code = ''
            # one-pass: first wait existing mail; only resend when forced or first wait empty
            if force_resend:
                try:
                    self.email_otp_resend()
                    self.log('已请求邮箱 OTP resend（APPLE_MAIL_FORCE_RESEND）')
                    since = time.time()
                except Exception as e:
                    self.log(f'邮箱 OTP resend 跳过: {e}')
            try:
                code = self.wait_email_otp(email, since=since, timeout=90 if not force_resend else 150)
            except Exception as e:
                self.log(f'首轮 OTP 等待未拿到: {e}')
                code = ''
            if not code and not force_resend:
                try:
                    self.email_otp_resend()
                    self.log('首轮无码，恢复性 resend 1 次')
                    since = time.time()
                    code = self.wait_email_otp(email, since=since, timeout=120)
                except Exception as e:
                    raise RuntimeError(f'邮箱 OTP 等待失败: {e}')
            if not code:
                raise RuntimeError('邮箱 OTP 等待超时')
            self.log(f'拿到邮箱 OTP {code}')
            step = self.email_otp_validate(code)
            page = self._page_type(step)
            cont = self._continue_url(step)
            self.log(f'email-otp/validate => page={page or "-"} cont={(cont or "-")[:100]}')

        if self._is_add_phone(cont, page) and not self._phone_done:
            self.log('登录推进命中 add-phone，走号池接码')
            ph = self.handle_add_phone()
            cont = str((ph or {}).get('continue_url') or cont or f'{OPENAI_AUTH}/sign-in-with-chatgpt/codex/consent')
        elif self._is_add_phone(cont, page) and self._phone_done:
            self.log('add-phone 已完成，跳过重绑')
            cont = cont if 'add-phone' not in (cont or '') else f'{OPENAI_AUTH}/sign-in-with-chatgpt/codex/consent'

        # workspace/consent may already be ready
        if 'workspace' in (page + cont).lower() or 'consent' in (page + cont).lower():
            return cont or ''
        self._login_driven = True
        return cont or ''


    def _iter_cookie_values(self, name: str = '') -> list[str]:
        vals: list[str] = []
        try:
            jar = self.session.cookies
            # curl_cffi jar may support iteration / get_dict
            try:
                d = jar.get_dict() if hasattr(jar, 'get_dict') else {}
                if isinstance(d, dict):
                    for k, v in d.items():
                        if not name or k == name:
                            if v:
                                vals.append(str(v))
            except Exception:
                pass
            try:
                for c in list(jar):
                    try:
                        n = getattr(c, 'name', None) or (c[0] if isinstance(c, (list, tuple)) else None)
                        v = getattr(c, 'value', None) or (c[1] if isinstance(c, (list, tuple)) and len(c) > 1 else None)
                        if name and n != name:
                            continue
                        if v:
                            vals.append(str(v))
                    except Exception:
                        continue
            except Exception:
                pass
            # direct get fallbacks
            if name:
                for dom in (None, '.openai.com', 'auth.openai.com', '.chatgpt.com', 'chatgpt.com'):
                    try:
                        if dom is None:
                            v = jar.get(name)
                        else:
                            v = jar.get(name, domain=dom)
                        if v:
                            vals.append(str(v))
                    except Exception:
                        try:
                            v = jar.get(name)
                            if v:
                                vals.append(str(v))
                        except Exception:
                            pass
        except Exception:
            pass
        # unique keep order
        out = []
        seen = set()
        for v in vals:
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def _decode_cookie_segments(self, raw: str) -> list[dict]:
        out: list[dict] = []
        if not raw:
            return out
        for segment in str(raw).split('.'):
            segment = (segment or '').strip()
            if not segment or len(segment) < 8:
                continue
            pad = '=' * (-len(segment) % 4)
            for cand in (segment + pad, segment):
                try:
                    data = json.loads(base64.urlsafe_b64decode(cand.encode('utf-8')).decode('utf-8'))
                    if isinstance(data, dict):
                        out.append(data)
                        break
                except Exception:
                    continue
        return out

    def _extract_workspace_id(self, html: str = '') -> str:
        # 1) cookies oai-client-auth-session
        for raw in self._iter_cookie_values('oai-client-auth-session') + self._iter_cookie_values('oai-client-auth-session'.replace('-', '_')):
            for data in self._decode_cookie_segments(raw):
                wid = str(data.get('workspace_id') or '').strip()
                if wid:
                    return wid
                workspaces = data.get('workspaces') or []
                if isinstance(workspaces, list):
                    for it in workspaces:
                        if isinstance(it, dict):
                            wid = str(it.get('id') or it.get('workspace_id') or '').strip()
                            if wid:
                                return wid
        # 2) html body
        blob = html or ''
        for ptn in [
            r'"workspace_id"\s*:\s*"(org-[^"]+)"',
            r'"id"\s*:\s*"(org-[^"]+)"',
            r'workspace_id=(org-[A-Za-z0-9]+)',
            r'(org-[a-zA-Z0-9]{10,})',
        ]:
            m = re.search(ptn, blob)
            if m:
                return m.group(1)
        # 3) access token orgs
        try:
            payload = decode_jwt_payload(self.access_token)
            auth = payload.get('https://api.openai.com/auth', {}) if isinstance(payload, dict) else {}
            if isinstance(auth, dict):
                orgs = auth.get('organizations') or []
                if isinstance(orgs, list) and orgs:
                    wid = str((orgs[0] or {}).get('id') or '')
                    if wid:
                        return wid
                wid = str(auth.get('chatgpt_account_id') or '')
        except Exception:
            pass
        return ''

    def _workspace_select(self, workspace_id: str) -> str:
        if not workspace_id:
            return ''
        self.log(f'选择 workspace {workspace_id[:24]}...')
        r = self.session.post(
            f'{OPENAI_AUTH}/api/accounts/workspace/select',
            headers=self._phone_headers(f'{OPENAI_AUTH}/sign-in-with-chatgpt/codex/consent'),
            json={'workspace_id': workspace_id},
            timeout=30,
        )
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {}
        if r.status_code != 200:
            self.log(f'workspace/select HTTP {r.status_code}: {(r.text or "")[:160]}')
            return ''
        cont = ''
        if isinstance(data, dict):
            cont = str(data.get('continue_url') or data.get('continueUrl') or '')
            if not cont and isinstance(data.get('data'), dict):
                cont = str(data['data'].get('continue_url') or data['data'].get('continueUrl') or '')
        if cont:
            self.log(f'workspace/select continue={(cont or "")[:120]}')
        return cont

    def _choose_account_select(self, html_text: str) -> str:
        m = re.search(r'us_[A-Za-z0-9]{16,}', html_text or '')
        if not m:
            return ''
        session_id = m.group(0)
        self.log(f'/choose-an-account 选 session_id={session_id[:28]}...')
        r = self.session.post(
            f'{OPENAI_AUTH}/api/accounts/session/select',
            headers=self._phone_headers(f'{OPENAI_AUTH}/choose-an-account'),
            json={'session_id': session_id},
            timeout=30,
        )
        try:
            data = r.json() if r.text else {}
        except Exception:
            data = {}
        if r.status_code != 200:
            self.log(f'session/select HTTP {r.status_code}: {(r.text or "")[:160]}')
            return ''
        cont = ''
        if isinstance(data, dict):
            cont = str(data.get('continue_url') or data.get('continueUrl') or '')
        return cont


    def follow_authorize(self, start_url: str, redirect_uri: str = DEFAULT_REDIRECT, max_hops: int = 14) -> tuple[str, str]:
        current = start_url
        callback = ''
        final = start_url
        for i in range(max_hops):
            if self._callback_has_code(current, redirect_uri):
                return current, current
            resp = self.session.get(
                current,
                headers=self._headers(referer='https://chatgpt.com/'),
                timeout=30,
                allow_redirects=False,
            )
            final = current
            loc = resp.headers.get('location') or resp.headers.get('Location') or ''
            # absolute-ize
            if loc and loc.startswith('/'):
                p = urlparse(current)
                loc = f'{p.scheme}://{p.netloc}{loc}'
            body = ''
            try:
                body = resp.text or ''
            except Exception:
                body = ''
            page_hint = ''
            m = re.search(r'"page"\s*:\s*"([^"]+)"', body)
            if m:
                page_hint = m.group(1)
            self.log(f'authorize hop {i+1}: status={resp.status_code} page={page_hint or "-"} url={(current or "")[:120]}')

            if (self._is_add_phone(current, page_hint) or self._is_add_phone(loc, '')) and not self._phone_done:
                self.log('命中 add-phone，开始号池接码')
                ph = self.handle_add_phone()
                cont_ph = str((ph or {}).get('continue_url') or '')
                current = cont_ph or start_url
                continue
            if (self._is_add_phone(current, page_hint) or self._is_add_phone(loc, '')) and self._phone_done:
                self.log('add-phone 已完成，重进 authorize')
                current = start_url
                continue

            # Codex OAuth often falls back to auth.openai.com/log-in when only chatgpt session cookie exists.
            if (
                resp.status_code == 200
                and (
                    '/log-in' in (current or '')
                    or page_hint in {'login', 'log_in', 'login_password'}
                    or (loc and '/log-in' in loc)
                )
            ):
                try:
                    cont_login = self.drive_login_from_log_in()
                except Exception as e:
                    self.log(f'/log-in 协议推进失败: {e}')
                    break
                if cont_login:
                    if self._callback_has_code(cont_login, redirect_uri):
                        return cont_login, cont_login
                    # after login, continue from continue_url; if empty-ish, re-hit authorize
                    current = cont_login
                    continue
                # no continue_url: re-enter authorize with now-authenticated auth.openai.com cookies
                self.log('/log-in 推进完成，重新进入 authorize')
                current = start_url
                continue

            if resp.status_code in {301, 302, 303, 307, 308} and loc:
                if self._callback_has_code(loc, redirect_uri):
                    return loc, loc
                current = loc
                continue

            # 200 pages: try extract continue_url
            cont = ''
            for pat in [
                r'"continue_url"\s*:\s*"([^"]+)"',
                r'"continueUrl"\s*:\s*"([^"]+)"',
                r'href="(https://auth\.openai\.com/[^"]+)"',
            ]:
                mm = re.search(pat, body)
                if mm:
                    cont = mm.group(1).encode('utf-8').decode('unicode_escape') if '\\u' in mm.group(1) else mm.group(1)
                    break
            if cont:
                if self._callback_has_code(cont, redirect_uri):
                    return cont, cont
                current = cont
                continue

            # /choose-an-account
            if '/choose-an-account' in (current or '') or 'choose_account' in (page_hint or ''):
                nxt = self._choose_account_select(body)
                if nxt:
                    if nxt.startswith('/'):
                        nxt = f'{OPENAI_AUTH}{nxt}'
                    current = nxt
                    continue

            # consent/workspace JSON-ish forms
            if (
                'workspace' in (current + page_hint).lower()
                or 'consent' in (current + page_hint).lower()
                or '/sign-in-with-chatgpt/' in (current or '')
            ):
                ws = self._extract_workspace_id(body)
                if ws:
                    cont2 = self._workspace_select(ws)
                    if cont2:
                        if cont2.startswith('/'):
                            cont2 = f'{OPENAI_AUTH}{cont2}'
                        if self._callback_has_code(cont2, redirect_uri):
                            return cont2, cont2
                        current = cont2
                        continue
                else:
                    self.log('consent 页未解析到 workspace_id，尝试 cookie/html 细节')
                    # dump small diagnostics
                    cookies = self._iter_cookie_values('oai-client-auth-session')
                    self.log(f'oai-client-auth-session count={len(cookies)} body_org={bool(re.search(r"org-", body or ""))}')
            # no progress
            break
        return callback, final

    def exchange_code(
        self,
        callback_url: str,
        *,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
        expected_state: str = '',
    ) -> dict[str, Any]:
        qs = parse_qs(urlparse(callback_url).query)
        code = (qs.get('code', [''])[0] or '').strip()
        state = (qs.get('state', [''])[0] or '').strip()
        if not code:
            raise RuntimeError('callback 缺少 code')
        if expected_state and state and state != expected_state:
            raise RuntimeError('callback state 不匹配')
        form = {
            'grant_type': 'authorization_code',
            'client_id': client_id,
            'code': code,
            'redirect_uri': redirect_uri,
            'code_verifier': code_verifier,
        }
        r = self.session.post(
            f'{OPENAI_AUTH}/oauth/token',
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json',
                'Origin': OPENAI_AUTH,
                'Referer': f'{OPENAI_AUTH}/sign-in-with-chatgpt/codex/consent',
                'User-Agent': self.user_agent,
            },
            data=urlencode(form),
            timeout=30,
        )
        if r.status_code != 200:
            raise RuntimeError(f'oauth/token 失败 {r.status_code}: {(r.text or "")[:220]}')
        data = r.json() if r.text else {}
        if not data.get('refresh_token') and not data.get('access_token'):
            raise RuntimeError(f'oauth/token 无 token: {data}')
        return data

    def build_direct_auth_url(self, prompt: str | None = 'login') -> tuple[str, str, str, str, str]:
        client_id = os.environ.get('OAUTH_CODEX_CLIENT_ID', DEFAULT_CLIENT_ID).strip() or DEFAULT_CLIENT_ID
        redirect_uri = os.environ.get('OAUTH_CODEX_REDIRECT_URI', DEFAULT_REDIRECT).strip() or DEFAULT_REDIRECT
        scope = os.environ.get('OAUTH_CODEX_SCOPE', DEFAULT_SCOPE).strip() or DEFAULT_SCOPE
        verifier, challenge = _pkce_pair()
        state = _b64url(_secrets_mod.token_bytes(24))
        params = {
            'client_id': client_id,
            'response_type': 'code',
            'redirect_uri': redirect_uri,
            'scope': scope,
            'state': state,
            'code_challenge': challenge,
            'code_challenge_method': 'S256',
            'id_token_add_organizations': 'true',
            'codex_cli_simplified_flow': 'true',
        }
        if prompt is not None and str(prompt) != '':
            params['prompt'] = str(prompt)
        auth_url = f'{OPENAI_AUTH}/oauth/authorize?{urlencode(params)}'
        return auth_url, state, verifier, redirect_uri, client_id

    def normalize_tokens(self, token_payload: dict[str, Any], session_token: str = '') -> dict[str, Any]:
        access = str(token_payload.get('access_token') or self.access_token or '')
        refresh = str(token_payload.get('refresh_token') or '')
        id_token = str(token_payload.get('id_token') or '')
        expires_in = int(token_payload.get('expires_in') or 0) or 0
        payload = decode_jwt_payload(access)
        id_payload = decode_jwt_payload(id_token) if id_token else {}
        account_id = ''
        auth_claim = payload.get('https://api.openai.com/auth') if isinstance(payload, dict) else None
        if isinstance(auth_claim, dict):
            account_id = str(auth_claim.get('chatgpt_account_id') or auth_claim.get('account_id') or '')
            orgs = auth_claim.get('organizations') or []
            if not account_id and isinstance(orgs, list) and orgs:
                account_id = str((orgs[0] or {}).get('id') or '')
        email = self.email or str(id_payload.get('email') or payload.get('email') or '')
        plan = 'free'
        if isinstance(auth_claim, dict):
            plan = str(auth_claim.get('chatgpt_plan_type') or auth_claim.get('plan_type') or plan)
        expired = ''
        if 'exp' in payload:
            expired = datetime.fromtimestamp(float(payload['exp']), tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        elif expires_in:
            expired = datetime.fromtimestamp(time.time() + expires_in, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        normalized = {
            'email': email,
            'access_token': access,
            'refresh_token': refresh,
            'id_token': id_token,
            'session_token': session_token or self.session_token,
            'account_id': account_id,
            'plan_type': plan,
            'expired': expired,
            'last_refresh': _now_iso(),
            'token_type': token_payload.get('token_type') or 'Bearer',
            'expires_in': expires_in,
            'decoded_access': payload,
            'decoded_id': id_payload,
        }
        # gpt2codex / converter shapes
        codex_doc = convert_openai([
            {
                'email': email,
                'access_token': access,
                'refresh_token': refresh,
                'id_token': id_token,
                'session_token': session_token or self.session_token,
                'account_id': account_id,
                'plan_type': plan,
                'expired': expired,
            }
        ], 'codex')
        cpa_doc = convert_openai([
            {
                'email': email,
                'access_token': access,
                'refresh_token': refresh,
                'id_token': id_token,
                'session_token': session_token or self.session_token,
                'account_id': account_id,
                'plan_type': plan,
                'expired': expired,
            }
        ], 'cpa')
        normalized['codex_auth_json'] = codex_doc
        normalized['cpa_auth_json'] = cpa_doc
        normalized['account'] = normalize_account({
            'email': email,
            'access_token': access,
            'refresh_token': refresh,
            'id_token': id_token,
            'session_token': session_token or self.session_token,
            'account_id': account_id,
            'plan_type': plan,
            'expired': expired,
        })
        return normalized

    def obtain_via_cpa(self) -> dict[str, Any]:
        if not self.cpa.configured:
            raise RuntimeError('CPA 未配置')
        before_names = set()
        try:
            before_names = {str(f.get('name') or '') for f in self.cpa.list_auth_files() if isinstance(f, dict)}
        except Exception:
            before_names = set()
        auth = self.cpa.get_codex_auth_url()
        auth_url = str(auth.get('url') or '')
        state = str(auth.get('state') or '')
        if not auth_url:
            raise RuntimeError(f'CPA 未返回 auth url: {auth}')
        # parse redirect from auth url
        qs = parse_qs(urlparse(auth_url).query)
        redirect_uri = (qs.get('redirect_uri', [DEFAULT_REDIRECT])[0] or DEFAULT_REDIRECT).strip()
        client_id = (qs.get('client_id', [DEFAULT_CLIENT_ID])[0] or DEFAULT_CLIENT_ID).strip()
        self.log(f'CPA 授权链接已生成 state={state[:12]} client={client_id[:16]}')
        callback, final = self.follow_authorize(auth_url, redirect_uri=redirect_uri)
        if not callback:
            # retry once after session refresh / phone
            self.refresh_session()
            callback, final = self.follow_authorize(auth_url, redirect_uri=redirect_uri)
        if not callback:
            raise RuntimeError(f'CPA 授权未捕获 callback, final={(final or "")[:180]}')
        self.log(f'拦截到 callback: {callback[:80]}...')
        cb_res = self.cpa.submit_callback(callback)
        self.log(f'已提交 CPA callback: {str(cb_res)[:160]}')
        status = self.cpa.poll_auth_status(state, timeout=90, interval=2)
        self.log(f'CPA 认证状态: {str({k: status.get(k) for k in list(status)[:8]})[:200]}')
        tokens: dict[str, Any] = {}
        for key in ('refresh_token', 'access_token', 'id_token'):
            if status.get(key):
                tokens[key] = status[key]
        auth_obj = status.get('auth') if isinstance(status.get('auth'), dict) else {}
        for key in ('refresh_token', 'access_token', 'id_token'):
            if auth_obj.get(key):
                tokens[key] = auth_obj[key]
        # watch local/remote auth files for new codex entry
        if not tokens.get('refresh_token'):
            deadline = time.time() + 20
            while time.time() < deadline and not tokens.get('refresh_token'):
                try:
                    files = self.cpa.list_auth_files()
                except Exception:
                    files = []
                for f in files:
                    if not isinstance(f, dict):
                        continue
                    name = str(f.get('name') or '')
                    email = str(f.get('email') or f.get('account') or '')
                    if name in before_names and email and email != self.email:
                        continue
                    # candidate local path
                    path = f.get('path') or ''
                    local = Path(str(path)) if path else (self.cpa.auth_dir / name if name else None)
                    if local and local.exists():
                        try:
                            data = json.loads(local.read_text(encoding='utf-8'))
                        except Exception:
                            continue
                        if data.get('refresh_token') or data.get('access_token'):
                            # match email when possible
                            if self.email and data.get('email') and data.get('email') != self.email and email != self.email:
                                continue
                            tokens = {
                                'access_token': data.get('access_token') or '',
                                'refresh_token': data.get('refresh_token') or '',
                                'id_token': data.get('id_token') or '',
                                'account_id': data.get('account_id') or data.get('chatgpt_account_id') or '',
                                'email': data.get('email') or email or self.email,
                            }
                            break
                if tokens.get('refresh_token'):
                    break
                time.sleep(2)
        ok = str(status.get('status') or '').lower() in {'ok', 'success'} or bool(tokens.get('refresh_token') or tokens.get('access_token'))
        return {
            'ok': ok,
            'mode': 'cpa',
            'state': state,
            'authUrl': auth_url,
            'callback': callback,
            'cpaStatus': status,
            'tokenPayload': tokens,
            'client_id': client_id,
            'redirect_uri': redirect_uri,
        }

    def obtain_via_direct_pkce(self) -> dict[str, Any]:
        auth_url, state, verifier, redirect_uri, client_id = self.build_direct_auth_url()
        self.log('使用直连 Codex PKCE 授权换 RT')
        callback, final = self.follow_authorize(auth_url, redirect_uri=redirect_uri)
        if not callback:
            # second pass: already authenticated cookies, rebuild without prompt=login
            auth_url2, state, verifier, redirect_uri, client_id = self.build_direct_auth_url(prompt='')
            self.log('直连二次 authorize（无 prompt）')
            callback, final = self.follow_authorize(auth_url2, redirect_uri=redirect_uri)
            if callback:
                auth_url = auth_url2
        if not callback:
            raise RuntimeError(f'直连授权未捕获 callback, final={(final or "")[:180]}')
        self.log(f'直连 callback: {callback[:80]}...')
        token_payload = self.exchange_code(
            callback,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_verifier=verifier,
            expected_state=state,
        )
        return {
            'ok': bool(token_payload.get('refresh_token') or token_payload.get('access_token')),
            'mode': 'direct_pkce',
            'state': state,
            'authUrl': auth_url,
            'callback': callback,
            'tokenPayload': token_payload,
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'code_verifier': verifier,
        }

    def run(self, prefer_cpa: bool = True, upload_cpa: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            'ok': False,
            'email': self.email,
            'proxy': self.proxy,
            'impersonate': self.impersonate,
            'deviceId': self.device_id,
            'startedAt': _now_iso(),
            'logs': self.logs,
        }
        try:
            sess = self.refresh_session()
            out['session'] = {
                'hasAccessToken': bool(self.access_token),
                'hasSessionToken': bool(self.session_token),
                'email': self.email,
                'accountId': str(((sess.get('account') or {}) if isinstance(sess, dict) else {}).get('id') or ''),
            }
            if not (self.session_token or self.access_token):
                raise RuntimeError('缺少 session/access token，无法继续 Codex 授权')

            errors: list[str] = []
            result: dict[str, Any] = {}
            if prefer_cpa and self.cpa.configured:
                try:
                    result = self.obtain_via_cpa()
                except Exception as e:
                    errors.append(f'cpa:{e}')
                    self.log(f'CPA 路径失败，回退直连 PKCE: {e}')
            if not result.get('ok') or not (result.get('tokenPayload') or {}).get('refresh_token'):
                # If CPA succeeded but didn't return RT body, still try direct PKCE to hold RT.
                try:
                    direct = self.obtain_via_direct_pkce()
                    if direct.get('ok'):
                        result = direct
                except Exception as e:
                    errors.append(f'direct:{e}')
                    if not result.get('ok'):
                        raise

            token_payload = result.get('tokenPayload') or {}
            # if CPA ok without local RT, keep placeholders
            normalized = self.normalize_tokens(token_payload, session_token=self.session_token)
            out['mode'] = result.get('mode')
            out['callback'] = result.get('callback')
            out['cpaStatus'] = result.get('cpaStatus')
            out['refreshToken'] = normalized.get('refresh_token') or ''
            out['accessToken'] = normalized.get('access_token') or self.access_token
            out['idToken'] = normalized.get('id_token') or ''
            out['accountId'] = normalized.get('account_id') or ''
            out['decoded'] = {
                'access': normalized.get('decoded_access') or {},
                'id': normalized.get('decoded_id') or {},
            }
            out['codexAuthJson'] = normalized.get('codex_auth_json')
            out['cpaAuthJson'] = normalized.get('cpa_auth_json')
            out['hasRefreshToken'] = bool(out['refreshToken'])
            out['ok'] = bool(out['hasRefreshToken'] or result.get('ok'))

            if upload_cpa and out.get('hasRefreshToken') and self.cpa.configured:
                try:
                    up = self.cpa.upload_codex_auth(self.email or normalized.get('email') or 'unknown', normalized)
                    out['cpaUpload'] = {
                        'filename': up.get('filename'),
                        'localPath': up.get('localPath'),
                        'remote': up.get('remote'),
                    }
                    self.log(f"已上传/写入 CPA auth: {up.get('filename')}")
                except Exception as e:
                    out['cpaUploadError'] = str(e)
                    self.log(f'CPA 上传失败: {e}')

            if errors:
                out['warnings'] = errors
            out['finishedAt'] = _now_iso()
            return out
        except Exception as e:
            out['ok'] = False
            out['error'] = str(e)
            out['finishedAt'] = _now_iso()
            return out
        finally:
            # keep phone cleanup but don't close caller-owned nothing
            try:
                self.phone_pool.cleanup()
            except Exception:
                pass


def obtain_refresh_token(
    *,
    email: str,
    proxy: str,
    impersonate: str,
    device_id: str = '',
    session_token: str = '',
    access_token: str = '',
    password: str = '',
    prefer_cpa: bool = True,
    upload_cpa: bool = True,
    mail_otp_wait=None,
) -> dict[str, Any]:
    client = CodexRtClient(
        proxy=proxy,
        impersonate=impersonate,
        device_id=device_id,
        session_token=session_token,
        access_token=access_token,
        email=email,
        password=password,
        mail_otp_wait=mail_otp_wait,
    )
    try:
        return client.run(prefer_cpa=prefer_cpa, upload_cpa=upload_cpa)
    finally:
        client.close()


async def obtain_refresh_token_async(**kwargs) -> dict[str, Any]:
    return await asyncio.to_thread(obtain_refresh_token, **kwargs)


if __name__ == '__main__':
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(description='Apple Mail Codex RT')
    parser.add_argument('--email', required=True)
    parser.add_argument('--proxy', required=True)
    parser.add_argument('--impersonate', default='firefox147')
    parser.add_argument('--device-id', default='')
    parser.add_argument('--session-token', default='')
    parser.add_argument('--access-token', default='')
    parser.add_argument('--from-run', default='', help='apple_mail run json path')
    parser.add_argument('--no-cpa-prefer', action='store_true')
    parser.add_argument('--no-upload', action='store_true')
    args = parser.parse_args()

    st = args.session_token
    at = args.access_token
    did = args.device_id
    email = args.email
    imp = args.impersonate
    password = ''
    if args.from_run:
        data = json.loads(Path(args.from_run).read_text(encoding='utf-8'))
        email = data.get('email') or email
        st = st or data.get('sessionToken') or ''
        at = at or data.get('accessToken') or ''
        did = did or data.get('deviceId') or ''
        imp = data.get('impersonate') or imp
        password = data.get('password') or ''
        proxy = data.get('proxy') or args.proxy
    else:
        proxy = args.proxy

    mail_otp_wait = None
    try:
        apple_secrets = json.loads(Path('/opt/automyai/data/apple_mail/secrets.json').read_text(encoding='utf-8'))
        acfg = json.loads(Path('/opt/automyai/data/apple_mail/config.json').read_text(encoding='utf-8'))
        mail_base = str(acfg.get('mailBase') or 'https://apimail.kfjie.me').rstrip('/')
        admin_auth = str(apple_secrets.get('adminAuth') or '')
        if admin_auth:
            def mail_otp_wait(email, since, timeout=120):
                s = requests.Session(
                    impersonate=imp or 'chrome131',
                    timeout=30,
                    proxies={'http': proxy, 'https': proxy},
                )
                try:
                    deadline = time.time() + max(30, int(timeout or 120))
                    min_ts = float(since or time.time()) - 5
                    target_local = (email or '').split('@', 1)[0].split('+', 1)[0].lower()
                    while time.time() < deadline:
                        r = s.get(
                            f'{mail_base}/admin/mails?limit=40&offset=0',
                            headers={'Accept': 'application/json', 'x-admin-auth': admin_auth},
                            timeout=20,
                        )
                        items = []
                        try:
                            data = r.json()
                            if isinstance(data, dict):
                                items = data.get('results') or data.get('data') or data.get('mails') or []
                            elif isinstance(data, list):
                                items = data
                        except Exception:
                            items = []
                        best = ''
                        best_ts = 0.0
                        for it in items if isinstance(items, list) else []:
                            raw = str((it or {}).get('raw') or '')
                            meta = (it or {}).get('metadata')
                            blob = raw + "\n" + (json.dumps(meta, ensure_ascii=False) if meta else '')
                            low = blob.lower()
                            if target_local and target_local not in low and (email or '').lower() not in low:
                                if 'openai' not in low and 'chatgpt' not in low:
                                    continue
                            if not any(x in low for x in ['login code', 'verification code', 'temporary', '验证码', 'otp', 'openai', 'chatgpt']):
                                continue
                            ts = 0.0
                            created = (it or {}).get('created_at') or (it or {}).get('createdAt') or (it or {}).get('date') or ''
                            if created:
                                try:
                                    dt = datetime.fromisoformat(str(created).replace('Z', '+00:00').replace(' ', 'T'))
                                    if dt.tzinfo is None:
                                        dt = dt.replace(tzinfo=timezone.utc)
                                    ts = dt.timestamp()
                                except Exception:
                                    ts = 0.0
                            if ts and ts < min_ts:
                                continue
                            code = extract_mail_otp_code(blob)
                            if code and ts >= best_ts:
                                best_ts = ts
                                best = code
                        if best:
                            return best
                        time.sleep(3)
                    return ''
                finally:
                    try:
                        s.close()
                    except Exception:
                        pass
    except Exception:
        mail_otp_wait = None

    result = obtain_refresh_token(
        email=email,
        proxy=proxy,
        impersonate=imp,
        device_id=did,
        session_token=st,
        access_token=at,
        password=password,
        prefer_cpa=not args.no_cpa_prefer,
        upload_cpa=not args.no_upload,
        mail_otp_wait=mail_otp_wait,
    )
    safe = {k: v for k, v in result.items() if k not in {'refreshToken', 'accessToken', 'idToken', 'codexAuthJson', 'cpaAuthJson', 'decoded'}}
    safe['hasRefreshToken'] = bool(result.get('refreshToken'))
    safe['refreshTokenPrefix'] = (result.get('refreshToken') or '')[:16]
    safe['accessTokenPrefix'] = (result.get('accessToken') or '')[:16]
    print(json.dumps(safe, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.get('ok') else 1)
