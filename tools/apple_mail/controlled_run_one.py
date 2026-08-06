#!/usr/bin/env python3
"""Controlled single-account Apple Mail trial.

- Force project proxy (no direct real IP)
- Per-account random TLS fingerprint + best-effort proxy node isolation
- Random Japanese name
- iCloud plus-alias from base mailbox
- Pull OTP from apimail via proxy
- Optional import to project import API (auto sourceEmail; Opus generates mailbox password)
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import string
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path('/opt/automyai')
DATA = ROOT / 'data' / 'apple_mail'
ENGINE = ROOT / 'tools' / 'openai3' / 'engine'
DEFAULT_PROXY = 'http://172.19.0.1:7905'
DEFAULT_MAIL_BASE = 'https://apimail.kfjie.me'
DEFAULT_IMPORT_BASE = 'https://cloud.opus.sryze.cc'
IMPERSONATE_POOL = [
    'firefox147', 'firefox144', 'firefox133',
    'chrome136', 'chrome131', 'chrome124', 'chrome120',
    'safari18_0', 'safari17_0', 'edge101',
]
IMPERSONATE = 'chrome131'  # Outlook 注册机同款环境

MIHOMO_API = os.environ.get('MIHOMO_API', 'http://127.0.0.1:9090').rstrip('/')
MIHOMO_GROUP = os.environ.get('APPLE_MAIL_PROXY_GROUP', 'TW-AUTO')



sys.path.insert(0, str(ENGINE))
TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

from curl_cffi import requests  # noqa: E402
from chatgpt_register import OpenAIAuthClient, SentinelTokenProvider  # noqa: E402
from fingerprint import generate_fingerprint  # noqa: E402
from run_status import start_run, finish_run, log_step  # noqa: E402
from codex_rt import obtain_refresh_token_async  # noqa: E402


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def resolve_proxy(cli: str = '') -> str:
    cfg = load_json(DATA / 'config.json', {}) or {}
    main = load_json(ROOT / 'config.json', {}) or {}
    proxy = (
        (cli or '').strip()
        or os.environ.get('APPLE_MAIL_PROXY', '').strip()
        or str(cfg.get('proxyUrl') or '').strip()
        or str(main.get('BROWSER_PROXY') or '').strip()
        or str(main.get('UC_SIGNUP_PROXY') or '').strip()
        or DEFAULT_PROXY
    )
    if not proxy:
        raise SystemExit('proxy required; refusing direct real-IP run')
    if proxy.lower() in {'direct', 'none', 'off', 'disable', 'disabled'}:
        raise SystemExit('direct egress forbidden for Apple Mail controlled run')
    return proxy


def random_jp_name() -> str:
    names = load_json(DATA / 'names.json', []) or []
    kana = [n for n in names if isinstance(n, str) and n.strip() and not any(c.isascii() and c.isalpha() for c in n.replace(' ', ''))]
    pool = kana or [n for n in names if isinstance(n, str) and n.strip()]
    if pool:
        return random.choice(pool)
    # fallback tiny set
    return random.choice(['たなか はるか', 'やまだ けんと', 'きむら ゆい', 'いのうえ りく', 'あべ さくら'])


def random_password(length: int = 16) -> str:
    upper = string.ascii_uppercase
    lower = string.ascii_lowercase
    digits = string.digits
    special = '!@#$%^&*'
    must = [random.choice(upper), random.choice(lower), random.choice(digits), random.choice(special)]
    rest = random.choices(upper + lower + digits + special, k=max(0, length - 4))
    arr = must + rest
    random.shuffle(arr)
    return ''.join(arr)


def make_icloud_alias(base_email: str, tag: str = '') -> str:
    """iCloud supports plus addressing: local+tag@icloud.com.

    Dot variants are less reliable for arbitrary icloud locals, so plus-tag is preferred.
    """
    base_email = base_email.strip()
    if '@' not in base_email:
        raise ValueError('invalid email')
    local, domain = base_email.split('@', 1)
    # strip existing plus tag
    local = local.split('+', 1)[0]
    if not tag:
        tag = 'oai' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    tag = re.sub(r'[^a-zA-Z0-9._-]', '', tag)[:24] or 'oai'
    return f'{local}+{tag}@{domain}'



def extract_openai_source_email(item: dict | None) -> str:
    """Extract Hide-My-Email OpenAI relay From address from an apimail item."""
    if not item:
        return ''
    raw = str(item.get('raw') or item.get('source') or '')
    patterns = [
        r"From:\s*[^<\n]*<((?:noreply|otp)_at_tm[0-9]*_openai_com_[A-Za-z0-9_]+@icloud\.com)>",
        r"Reply-To:\s*((?:noreply|otp)_at_tm[0-9]*_openai_com_[A-Za-z0-9_]+@icloud\.com)",
        r"((?:noreply|otp)_at_tm[0-9]*_openai_com_[A-Za-z0-9_]+@icloud\.com)",
    ]
    for pat in patterns:
        m = re.search(pat, raw, re.I)
        if m:
            return m.group(1).strip().lower()
    blob = json.dumps(item, ensure_ascii=False)
    m = re.search(r"((?:noreply|otp)_at_tm[0-9]*_openai_com_[A-Za-z0-9_]+@icloud\.com)", blob, re.I)
    return m.group(1).strip().lower() if m else ''


def pick_impersonate(preferred: str = '') -> str:
    preferred = (preferred or '').strip()
    if preferred and preferred in IMPERSONATE_POOL:
        # still randomize a bit but bias preferred
        if random.random() < 0.55:
            return preferred
    return random.choice(IMPERSONATE_POOL)


async def maybe_rotate_mihomo_node(group: str = MIHOMO_GROUP) -> dict:
    """Best-effort per-account node isolation via local mihomo API.

    Never fatal: registration can continue on current proxy if API unavailable.
    """
    info = {'group': group, 'rotated': False}
    try:
        # use plain requests without proxy for local api
        import urllib.request
        with urllib.request.urlopen(f'{MIHOMO_API}/proxies/{urllib.parse.quote(group)}', timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8', 'ignore'))
        all_nodes = list(data.get('all') or [])
        now = str(data.get('now') or '')
        candidates = [n for n in all_nodes if n and n != now]
        if not candidates:
            info.update({'now': now, 'reason': 'no_other_node'})
            return info
        target = random.choice(candidates)
        req = urllib.request.Request(
            f'{MIHOMO_API}/proxies/{urllib.parse.quote(group)}',
            data=json.dumps({'name': target}).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='PUT',
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            _ = resp.read()
        info.update({'rotated': True, 'from': now, 'to': target})
        return info
    except Exception as e:
        info['error'] = str(e)[:200]
        return info


class ApiMailClient:
    """Apple/iCloud OTPs land in a shared apimail inbox (e.g. thekfjie@kfjie.me).

    Matching must use raw To/Hide-My-Email headers, not address=icloud query.
    """

    def __init__(self, mail_base: str, admin_auth: str, proxy: str, shared_inbox: str = 'thekfjie@kfjie.me'):
        self.mail_base = mail_base.rstrip('/')
        self.admin_auth = admin_auth
        self.proxy = proxy
        self.shared_inbox = shared_inbox
        self._session: Optional[requests.AsyncSession] = None

    async def _get(self) -> requests.AsyncSession:
        if not self._session:
            self._session = requests.AsyncSession(
                impersonate=impersonate if 'impersonate' in locals() else IMPERSONATE,
                proxies={'http': self.proxy, 'https': self.proxy},
                timeout=30,
            )
        return self._session

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None

    async def list_mails(self, address: str = '', limit: int = 30, offset: int = 0) -> list[dict]:
        s = await self._get()
        # Prefer shared inbox listing; fallback to global recent mails.
        urls = []
        if address and address.endswith('@kfjie.me'):
            urls.append(f"{self.mail_base}/admin/mails?limit={limit}&offset={offset}&address={urllib.parse.quote(address)}")
        if self.shared_inbox:
            urls.append(f"{self.mail_base}/admin/mails?limit={limit}&offset={offset}&address={urllib.parse.quote(self.shared_inbox)}")
        urls.append(f"{self.mail_base}/admin/mails?limit={limit}&offset={offset}")
        last_err = None
        for url in urls:
            r = await s.get(url, headers={'Accept': 'application/json', 'x-admin-auth': self.admin_auth})
            if r.status_code >= 400:
                last_err = f'list_mails HTTP {r.status_code}: {r.text[:200]}'
                continue
            data = r.json()
            if isinstance(data, dict):
                for k in ('results', 'data', 'messages', 'mails'):
                    if isinstance(data.get(k), list):
                        return data[k]
            if isinstance(data, list):
                return data
        if last_err:
            raise RuntimeError(last_err)
        return []

    @staticmethod
    def _ts(item: dict) -> float:
        for k in ('created_at', 'createdAt', 'date', 'timestamp', 'time'):
            v = item.get(k)
            if v is None:
                continue
            if isinstance(v, (int, float)):
                val = float(v)
                if val > 1e12:
                    val = val / 1000.0
                return val
            s = str(v).strip()
            for cand in (s, s.replace('Z', '+00:00'), s.replace(' ', 'T') + '+00:00', s.replace(' ', 'T')):
                try:
                    dt = datetime.fromisoformat(cand)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt.timestamp()
                except Exception:
                    pass
            try:
                from email.utils import parsedate_to_datetime
                return parsedate_to_datetime(s).timestamp()
            except Exception:
                pass
        raw = str(item.get('raw') or '')
        m = re.search(r'^Date:\s*(.+)$', raw, re.I | re.M)
        if m:
            try:
                from email.utils import parsedate_to_datetime
                return parsedate_to_datetime(m.group(1)).timestamp()
            except Exception:
                return 0.0
        return 0.0

    @staticmethod
    def _blob(item: dict) -> str:
        parts = []
        for k in ('subject', 'decodedSubject', 'decodedText', 'text', 'content', 'body', 'html', 'intro', 'snippet', 'raw', 'source', 'from', 'sender'):
            v = item.get(k)
            if isinstance(v, str) and v.strip():
                parts.append(v)
        return '\n'.join(parts)

    @staticmethod
    def _decode_qp(text: str) -> str:
        text = text.replace('=\r\n', '').replace('=\n', '').replace('=\r', '')
        text = text.replace('=3D', '=').replace('=20', ' ')
        def repl(m):
            try:
                return bytes.fromhex(m.group(1)).decode('utf-8', 'ignore')
            except Exception:
                return m.group(0)
        return re.sub(r'=([0-9A-Fa-f]{2})', repl, text)

    @staticmethod
    def extract_code(blob: str) -> Optional[str]:
        if not blob:
            return None
        text = ApiMailClient._decode_qp(blob)
        # 1) explicit text phrases
        for ptn in [
            r"temporary verification code to continue[:\s]*([0-9]{6})",
            r"enter this code[:\s]*([0-9]{6})",
            r"<h1[^>]*>\s*([0-9]{6})\s*</h1>",
            r"验证码[^0-9]{0,40}([0-9]{6})",
            r"临时验证码[^0-9]{0,40}([0-9]{6})",
        ]:
            m = re.search(ptn, text, re.I)
            if m:
                return m.group(1)
        # 2) body pure-line OTP, skip CSS color #353740 context
        for m in re.finditer(r"(?m)^[ \t>]*([0-9]{6})[ \t]*\r?$", text):
            code = m.group(1)
            left = text[max(0, m.start()-80):m.start()].lower()
            if code == "353740" or "color:#" in left or "color: #" in left or "background-color" in left:
                continue
            # skip header-ish contexts
            if any(x in left for x in ["received:", "message-id", "esmtps", "mailgun", "dkim", "arc-"]):
                continue
            return code
        return None

    @staticmethod
    def _target_locals(address: str) -> set[str]:
        addr = (address or '').strip().lower()
        if '@' not in addr:
            return {addr} if addr else set()
        local, domain = addr.split('@', 1)
        base_local = local.split('+', 1)[0]
        out = {addr, f'{base_local}@{domain}', local, base_local}
        # plus alias local fully
        out.add(local)
        return {x for x in out if x}

    def _matches_target(self, item: dict, address: str) -> bool:
        targets = self._target_locals(address)
        if not targets:
            return True
        blob = self._blob(item).lower()
        # common headers / body mentions
        for t in targets:
            if t and t.lower() in blob:
                return True
        # To: Hide My Email <xxx@icloud.com>
        raw = str(item.get('raw') or '')
        for m in re.finditer(r'<([^>]+@[^>]+)>', raw):
            if m.group(1).strip().lower() in targets:
                return True
            local = m.group(1).split('@',1)[0].lower().split('+',1)[0]
            if local in targets:
                return True
        return False

    async def poll_code(self, address: str, since: float, timeout: int = 120, interval: int = 4) -> Optional[dict]:
        deadline = time.time() + timeout
        min_ts = since - 3
        while time.time() < deadline:
            items = await self.list_mails(limit=50)
            scored = []
            for it in items:
                ts = self._ts(it)
                if not ts or ts < min_ts:
                    continue
                if not self._matches_target(it, address):
                    continue
                blob = self._blob(it)
                low = blob.lower()
                if not any(x in low for x in ['openai', 'chatgpt', 'verify', 'verification', 'code', '验证码', 'temporary', 'privaterelay', 'tm.openai']):
                    if not re.search(r'\b\d{6}\b', blob):
                        continue
                code = self.extract_code(blob)
                if code:
                    scored.append((ts, code, it, blob[:220]))
            if scored:
                scored.sort(key=lambda x: x[0], reverse=True)
                ts, code, it, preview = scored[0]
                return {'code': code, 'item': it, 'preview': preview, 'ts': ts, 'mailId': it.get('id'), 'sourceEmail': extract_openai_source_email(it), 'matchedAddress': address}
            await asyncio.sleep(interval)
        return None


async def ensure_proxy_identity(proxy: str) -> dict:
    s = requests.AsyncSession(impersonate=impersonate if 'impersonate' in locals() else IMPERSONATE, proxies={'http': proxy, 'https': proxy}, timeout=20)
    try:
        r = await s.get('http://ip-api.com/json')
        data = r.json()
        ip = str(data.get('query') or '')
        if ip.startswith(('10.', '127.', '192.168.', '172.16.', '172.17.', '172.18.', '172.19.', '172.20.')):
            raise RuntimeError(f'proxy appears private/local: {ip}')
        # refuse if somehow equals known host public? still ok as long as not direct local machine path.
        return data
    finally:
        await s.close()


async def import_account(import_base: str, import_key: str, payload: dict, proxy: str) -> dict:
    s = requests.AsyncSession(impersonate=impersonate if 'impersonate' in locals() else IMPERSONATE, proxies={'http': proxy, 'https': proxy}, timeout=30)
    try:
        r = await s.post(
            import_base.rstrip('/') + '/api/v1/accounts',
            headers={
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'X-API-Key': import_key,
                'Authorization': f'Bearer {import_key}',
            },
            json=payload,
        )
        text = r.text
        try:
            data = r.json()
        except Exception:
            data = {'raw': text[:500]}
        return {'status': r.status_code, 'data': data}
    finally:
        await s.close()


async def run_one(base_email: str, proxy: str, do_import: bool = True, otp_timeout: int = 150) -> dict:
    cfg = load_json(DATA / 'config.json', {}) or {}
    secrets = load_json(DATA / 'secrets.json', {}) or {}
    mail_base = str(cfg.get('mailBase') or DEFAULT_MAIL_BASE)
    import_base = str(cfg.get('importBase') or DEFAULT_IMPORT_BASE)
    admin_auth = str(secrets.get('adminAuth') or os.environ.get('APPLE_MAIL_ADMIN_AUTH') or '').strip()
    import_key = str(secrets.get('importApiKey') or '').strip()
    password = random_password()  # ChatGPT signup password only; Opus mailbox password is generated by Opus
    if not admin_auth:
        raise SystemExit('missing apimail adminAuth')

    # Hide My Email already unique; plus-tag optional via APPLE_MAIL_FORCE_PLUS=1
    if os.environ.get('APPLE_MAIL_FORCE_PLUS', '').strip().lower() in {'1', 'true', 'yes'}:
        email = make_icloud_alias(base_email)
    else:
        email = base_email.strip()
    name = random_jp_name()
    age = random.randint(24, 34)
    birthdate = (datetime.now() - timedelta(days=age * 365 + random.randint(0, 300))).strftime('%Y-%m-%d')

    # Per-account isolation (gpt_outlook style fingerprint bundle + optional node rotate).
    # ONLY use Outlook 注册机 environment (chrome131 / Windows Chrome UA)
    fp = generate_fingerprint(prefer='chrome')
    impersonate = str(fp.get('impersonate') or 'chrome131')
    # Only rotate TW-AUTO when the actual run proxy is the TW gateway.
    # If operator passes another regional proxy (e.g. JP01:7913), do not mutate TW group.
    if ':7905' in str(proxy) or str(proxy).rstrip('/').endswith('7905'):
        rotate_info = await maybe_rotate_mihomo_node(MIHOMO_GROUP)
    else:
        rotate_info = {'group': MIHOMO_GROUP, 'rotated': False, 'reason': 'proxy_not_tw_gateway', 'proxy': proxy}


    out = {
        'ok': False,
        'baseEmail': base_email,
        'email': email,
        'name': name,
        'age': age,
        'password': password,
        'proxy': proxy,
        'impersonate': impersonate,
        'fingerprint': fp,
        'proxyRotate': rotate_info,
        'startedAt': datetime.now(timezone.utc).isoformat(),
    }
    start_run({
        'email': email,
        'baseEmail': base_email,
        'name': name,
        'proxy': proxy,
        'impersonate': impersonate,
        'fingerprint': {
            'family': fp.get('family'),
            'impersonate': fp.get('impersonate'),
            'screen': fp.get('screen'),
            'lang': fp.get('lang'),
            'platform': fp.get('platform'),
            'device_id': fp.get('device_id'),
        },
        'proxyRotate': rotate_info,
    })
    log_step('isolation', f"指纹 {fp.get('family')}/{impersonate} screen={fp.get('screen')} lang={fp.get('lang')}", email=email, impersonate=impersonate, deviceId=fp.get('device_id'))
    if rotate_info.get('rotated'):
        log_step('proxy_rotate', f"节点切换 {rotate_info.get('from')} -> {rotate_info.get('to')}", **rotate_info)
    else:
        log_step('proxy_rotate', f"节点未切换: {rotate_info.get('reason') or rotate_info.get('error') or 'keep'}")

    print('== isolation ==')
    print({'fingerprint': {k: fp.get(k) for k in ('family','impersonate','screen','lang','platform','device_id')}, 'proxyRotate': rotate_info})
    print('== proxy identity ==')
    log_step('proxy_check', '检查代理出口...')
    ident = await ensure_proxy_identity(proxy)
    out['proxyIdentity'] = {
        'ip': ident.get('query'),
        'country': ident.get('countryCode'),
        'city': ident.get('city'),
        'isp': ident.get('isp'),
    }
    print(out['proxyIdentity'])
    log_step('proxy_check', f"出口 {out['proxyIdentity'].get('country')}/{out['proxyIdentity'].get('ip')}", proxyIdentity=out['proxyIdentity'], proxy=proxy)

    print('== profile ==')
    print({'email': email, 'name': name, 'age': age, 'password': password[:3] + '***'})
    log_step('profile', f"准备资料 name={name} age={age}", email=email)

    mail = ApiMailClient(mail_base, admin_auth, proxy)
    sentinel = SentinelTokenProvider(impersonate=impersonate, proxy=proxy)
    auth = OpenAIAuthClient(impersonate=impersonate, sentinel=sentinel, proxy=proxy)

    t0 = time.time()
    def ts():
        return f'[{time.time()-t0:.1f}s]'

    try:
        # preflight shared inbox
        pre = await mail.list_mails(limit=5)
        matched_pre = [m for m in pre if mail._matches_target(m, base_email)]
        print(ts(), 'mailbox preflight shared', len(pre), 'matched_base', len(matched_pre))

        print(ts(), 'init openai page email mode...')
        log_step('init', '初始化 OpenAI 注册页/会话', email=email)
        since = time.time()
        await auth.share_session_with_sentinel()
        init = await auth.init_page_email(email)
        out['deviceId'] = init.get('device_id')
        print(ts(), 'device', str(init.get('device_id'))[:12])
        sentinel.set_cookies(init.get('cookies') or {})

        # Force a fresh OTP in the SAME auth session (critical for validate).
        if os.environ.get('APPLE_MAIL_FORCE_RESEND', '').strip().lower() in {'1', 'true', 'yes'}:
            try:
                s = await auth._get_session()
                print(ts(), 'FORCE resend otp via /api/accounts/email-otp/resend ...')
                r = await s.post(
                    f'{auth.BASE_URL}/api/accounts/email-otp/resend',
                    json={},
                    headers={
                        'accept': 'application/json',
                        'content-type': 'application/json',
                        'referer': f'{auth.BASE_URL}/email-verification',
                    },
                )
                out['resend'] = {'status': r.status_code, 'body': (r.text or '')[:240]}
                print(ts(), 'resend', r.status_code, (r.text or '')[:160])
            except Exception as e:
                out['resendError'] = str(e)
                print(ts(), 'resend error', e)
        else:
            out['resend'] = {'skipped': True, 'reason': 'one_pass_no_default_resend'}
            print(ts(), 'skip default resend (one-pass mode)')
            log_step('otp_wait', '一遍过：等待首封 OTP，不主动 resend', email=email)

        await asyncio.sleep(2)

        print(ts(), f'wait otp up to {otp_timeout}s via shared inbox ...')
        log_step('otp_wait', f'拉信等待 OTP，超时 {otp_timeout}s', email=email)
        # Apple Hide My Email usually delivers To: base HME; plus-tag may not appear in To.
        code_info = await mail.poll_code(base_email, since=since, timeout=otp_timeout, interval=3)
        if not code_info:
            # fallback: exact alias mention
            code_info = await mail.poll_code(email, since=since, timeout=15, interval=3)
        if not code_info:
            out['error'] = 'otp_timeout'
            print(ts(), 'otp timeout')
            log_step('otp_wait', 'OTP 超时', level='ERROR', email=email)
            return out
        code_info['matchedAddress'] = base_email

        code = code_info['code']
        source_email = code_info.get('sourceEmail') or extract_openai_source_email(code_info.get('item'))
        out['sourceEmail'] = source_email
        out['otp'] = {
            'code': code,
            'matchedAddress': code_info.get('matchedAddress') or base_email,
            'preview': code_info.get('preview'),
            'mailId': code_info.get('mailId'),
            'sourceEmail': source_email,
        }
        print(ts(), 'otp', code, 'via', code_info.get('matchedAddress'), 'source', source_email)
        log_step('otp_got', f'拿到验证码 {code} source={source_email or "-"}', email=email, sourceEmail=source_email)

        print(ts(), 'validate otp...', code, 'mailId', code_info.get('mailId'))
        log_step('otp_validate', f'校验 OTP {code}', email=email)
        validate_result = await auth.validate_email_otp(code)
        out['validate'] = validate_result if isinstance(validate_result, dict) else {'raw': str(validate_result)[:300]}
        if isinstance(validate_result, dict) and 'error' in validate_result:
            err = validate_result.get('error') or {}
            err_code = err.get('code') if isinstance(err, dict) else ''
            print(ts(), 'validate failed', validate_result)
            if str(err_code) == 'wrong_email_otp_code':
                try:
                    s = await auth._get_session()
                    print(ts(), 'recovery resend after wrong code...')
                    since2 = time.time()
                    rr = await s.post(
                        f'{auth.BASE_URL}/api/accounts/email-otp/resend',
                        json={},
                        headers={
                            'accept': 'application/json',
                            'content-type': 'application/json',
                            'referer': f'{auth.BASE_URL}/email-verification',
                        },
                    )
                    out['recoveryResend'] = {'status': rr.status_code, 'body': (rr.text or '')[:200]}
                    print(ts(), 'recovery resend', rr.status_code, (rr.text or '')[:120])
                    await asyncio.sleep(2)
                    prev_id = int(code_info.get('mailId') or 0)
                    code_info2 = await mail.poll_code(base_email, since=since2, timeout=90, interval=3)
                    if code_info2 and int(code_info2.get('mailId') or 0) != prev_id:
                        code = code_info2['code']
                        code_info = code_info2
                        out['otp'] = {'code': code, 'matchedAddress': base_email, 'preview': code_info.get('preview'), 'mailId': code_info.get('mailId')}
                        print(ts(), 'recovery otp', code, 'mailId', code_info.get('mailId'))
                        validate_result = await auth.validate_email_otp(code)
                        out['validate'] = validate_result if isinstance(validate_result, dict) else {'raw': str(validate_result)[:300]}
                        if isinstance(validate_result, dict) and 'error' in validate_result:
                            out['error'] = f"validate_failed:{validate_result.get('error')}"
                            print(ts(), 'validate failed again', validate_result)
                            return out
                        print(ts(), 'otp ok after recovery')
                    else:
                        out['error'] = f"validate_failed:{err}"
                        return out
                except Exception as e:
                    out['error'] = f"validate_failed:{err}; recovery_error:{e}"
                    return out
            else:
                out['error'] = f"validate_failed:{err}"
                return out
        else:
            print(ts(), 'otp ok')
            log_step('otp_validate', 'OTP 校验通过', level='OK', email=email)

        # Detect passwordless signup: OTP often jumps straight to about-you.
        # In that mode, do NOT call /user/register password (causes username_already_exists / invalid_auth_step).
        page_info = {}
        sess_info = {}
        cont_url = ''
        if isinstance(validate_result, dict):
            cont_url = str(validate_result.get('continue_url') or '')
            page_info = validate_result.get('page') if isinstance(validate_result.get('page'), dict) else {}
            if not page_info and isinstance(validate_result.get('page'), str):
                page_info = {'type': validate_result.get('page')}
            sess_info = validate_result.get('oai-client-auth-session') if isinstance(validate_result.get('oai-client-auth-session'), dict) else {}
        page_type = str(page_info.get('type') or '').lower()
        email_mode = str(sess_info.get('email_verification_mode') or sess_info.get('signup_mode') or '').lower()
        is_passwordless = (
            'passwordless' in email_mode
            or page_type in {'about_you', 'about-you'}
            or '/about-you' in cont_url
        )
        out['authMode'] = {
            'pageType': page_type,
            'emailVerificationMode': email_mode,
            'passwordless': is_passwordless,
            'continueUrl': cont_url[:160],
        }
        print(ts(), 'auth mode', out['authMode'])
        log_step('auth_mode', f"mode={'passwordless' if is_passwordless else 'password'} page={page_type or '-'} emailMode={email_mode or '-'}", email=email)

        if not is_passwordless:
            # classic create-account/password path only
            try:
                print(ts(), 'set password...')
                log_step('password', '提交 ChatGPT 注册密码（非 Opus 接码密码）', email=email)
                pw_res = await auth.register_password_email(email, password)
                out['passwordStep'] = pw_res if isinstance(pw_res, dict) else {'raw': str(pw_res)[:300]}
                print(ts(), 'password step', str(pw_res)[:180])
                if isinstance(pw_res, dict) and isinstance(pw_res.get('error'), dict):
                    code_err = str(pw_res['error'].get('code') or '')
                    # if OpenAI says username already exists, fall through to about-you path instead of hard fail
                    if code_err in {'username_already_exists', 'invalid_auth_step'}:
                        print(ts(), 'password step indicates existing/passwordless state, continue about-you')
                        is_passwordless = True
            except Exception as e:
                out['passwordStepError'] = str(e)
                print(ts(), 'password step skip/err', e)
        else:
            out['passwordStep'] = {'skipped': True, 'reason': 'passwordless_signup'}
            print(ts(), 'skip password (passwordless/about-you)')
            log_step('password', 'passwordless 流程，跳过设密', email=email)

        about_you_url = cont_url if cont_url else (validate_result.get('continue_url', '') if isinstance(validate_result, dict) else '')
        if about_you_url:
            print(ts(), 'goto about-you')
            s = await auth._get_session()
            await s.get(about_you_url, headers={'referer': f'{auth.BASE_URL}/email-verification'})

        print(ts(), 'create account...')
        log_step('create', '创建账号 / about-you', email=email, name=name)
        create_result = await auth.create_account(name, birthdate)
        out['create'] = create_result if isinstance(create_result, dict) else {'raw': str(create_result)[:300]}
        if isinstance(create_result, dict) and 'error' in create_result:
            err = create_result.get('error', {})
            code_err = err.get('code') if isinstance(err, dict) else err
            print(ts(), 'create error', code_err)
            # registration_disallowed is deterministic for this email/session; do not thrash retries
            out['error'] = f'create_failed:{code_err}'
            return out
        print(ts(), 'create ok')

        access_token = ''
        session_token = ''
        account_id = ''
        sess_data = {}
        continue_url = create_result.get('continue_url', '') if isinstance(create_result, dict) else ''
        if continue_url:
            print(ts(), 'oauth callback...')
            s = await auth._get_session()
            cb = await s.get(continue_url, allow_redirects=True)
            out['callbackStatus'] = cb.status_code
            sess_resp = await s.get(f'{auth.CHATGPT_URL}/api/auth/session')
            try:
                sess_data = sess_resp.json()
            except Exception:
                sess_data = {'raw': sess_resp.text[:300]}
            access_token = str(sess_data.get('accessToken') or '')
            session_token = str(sess_data.get('sessionToken') or '')
            account_id = str((sess_data.get('account') or {}).get('id') or '')
            out['session'] = {
                'hasAccessToken': bool(access_token),
                'hasSessionToken': bool(session_token),
                'accountId': account_id,
                'user': sess_data.get('user'),
                'expires': sess_data.get('expires'),
            }
            print(ts(), 'session', out['session'])

        out['ok'] = bool(access_token or create_ok)
        if out['ok']:
            log_step('session', '拿到 session/accessToken', level='OK', email=email, accountId=(out.get('session') or {}).get('accountId'))
        out['accessTokenPrefix'] = access_token[:24] if access_token else ''

        # Post-register: same proxy/fingerprint/device -> Codex OAuth -> phone pool -> RT
        want_rt = os.environ.get('APPLE_MAIL_FETCH_RT', '1').strip().lower() not in {'0', 'false', 'no', 'off'}
        if out['ok'] and want_rt and (access_token or session_token):
            print(ts(), 'codex rt exchange...')
            log_step('codex_rt', '注册后继续 Codex 授权获取 Refresh Token（同代理/同指纹）', email=email, impersonate=impersonate, deviceId=out.get('deviceId') or fp.get('device_id'))
            try:
                def _mail_otp_wait(email=email, since=0.0, timeout=120):
                    # Sync apimail poll for codex_rt login drive.
                    s = requests.Session(
                        impersonate=impersonate,
                        timeout=30,
                        proxies={'http': proxy, 'https': proxy},
                    )
                    try:
                        deadline = time.time() + max(30, int(timeout or 120))
                        min_ts = float(since or time.time()) - 5
                        target_local = (email or '').split('@', 1)[0].split('+', 1)[0].lower()
                        while time.time() < deadline:
                            r = s.get(
                                f"{mail_base.rstrip('/')}/admin/mails?limit=50&offset=0",
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
                                blob = raw + '\n' + (json.dumps(meta, ensure_ascii=False) if meta else '')
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
                                code = ApiMailClient.extract_code(blob) or ''
                                if not code:
                                    for ptn in [
                                        r"temporary verification code to continue[:\s]*([0-9]{6})",
                                        r"Your temporary ChatGPT (?:login|verification) code[^0-9]{0,80}([0-9]{6})",
                                        r"<h1[^>]*>\s*([0-9]{6})\s*</h1>",
                                    ]:
                                        mm = re.search(ptn, blob, re.I)
                                        if mm and mm.group(1) != '353740':
                                            code = mm.group(1)
                                            break
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

                rt_res = await obtain_refresh_token_async(
                    email=email,
                    proxy=proxy,
                    impersonate=impersonate,
                    device_id=str(out.get('deviceId') or fp.get('device_id') or ''),
                    session_token=session_token,
                    access_token=access_token,
                    password=password,
                    prefer_cpa=True,
                    upload_cpa=os.environ.get('APPLE_MAIL_UPLOAD_CPA', '1').strip().lower() not in {'0', 'false', 'no', 'off'},
                    mail_otp_wait=_mail_otp_wait,
                )
                out['codexRt'] = {
                    'ok': bool(rt_res.get('ok')),
                    'mode': rt_res.get('mode'),
                    'hasRefreshToken': bool(rt_res.get('refreshToken') or rt_res.get('hasRefreshToken')),
                    'accountId': rt_res.get('accountId') or '',
                    'cpaUpload': rt_res.get('cpaUpload'),
                    'error': rt_res.get('error'),
                    'warnings': rt_res.get('warnings'),
                }
                if rt_res.get('refreshToken'):
                    out['refreshToken'] = rt_res.get('refreshToken')
                    out['refreshTokenPrefix'] = str(rt_res.get('refreshToken') or '')[:16]
                if rt_res.get('accessToken'):
                    # keep fresher AT if RT exchange rotated it
                    access_token = str(rt_res.get('accessToken') or access_token)
                    out['accessTokenPrefix'] = access_token[:24]
                if rt_res.get('idToken'):
                    out['idTokenPrefix'] = str(rt_res.get('idToken') or '')[:16]
                if rt_res.get('codexAuthJson'):
                    out['codexAuthJson'] = rt_res.get('codexAuthJson')
                if rt_res.get('ok') and out['codexRt']['hasRefreshToken']:
                    log_step('codex_rt', f"拿到 RT mode={rt_res.get('mode')}", level='OK', email=email, mode=rt_res.get('mode'))
                else:
                    log_step('codex_rt', f"RT 未完成: {rt_res.get('error') or rt_res.get('mode') or 'unknown'}", level='WARN', email=email, error=rt_res.get('error'))
            except Exception as e:
                out['codexRt'] = {'ok': False, 'error': str(e)}
                log_step('codex_rt', f'RT 异常: {e}', level='WARN', email=email, error=str(e))
                print(ts(), 'codex rt error', e)

        out['finishedAt'] = datetime.now(timezone.utc).isoformat()

        # persist local result
        run_dir = DATA / 'runs'
        run_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        result_path = run_dir / f'run_{stamp}_{email.replace("@","_at_")}.json'
        # store full but redact long tokens in file? keep for operator, mode 600
        full = dict(out)
        full['accessToken'] = access_token
        full['sessionToken'] = session_token
        full['sessionRaw'] = sess_data
        if out.get('refreshToken'):
            full['refreshToken'] = out.get('refreshToken')
        if out.get('codexAuthJson'):
            full['codexAuthJson'] = out.get('codexAuthJson')
        result_path.write_text(json.dumps(full, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        result_path.chmod(0o600)
        out['resultPath'] = str(result_path)
        with (DATA / 'accounts.txt').open('a', encoding='utf-8') as f:
            f.write(f"{email}----{password}----{name}----{access_token[:20]}\n")

        if do_import and import_key and (access_token or session_token):
            print(ts(), 'import to project...')
            log_step('import', '导入 Opus Mail（密码由 Opus 生成，自动 sourceEmail）', email=email, sourceEmail=out.get('sourceEmail'))
            oauth_result = rt_res if 'rt_res' in locals() and isinstance(rt_res, dict) else {}
            credential = f"{email}---{session_token}" if session_token else (f"{email}---{access_token}" if access_token else '')
            # Do NOT send password: Opus Mail generates random mailbox login password.
            payload = {
                'email': email,
                'note': f"apple-mail controlled run | name={name} | base={base_email}",
                'billingChannelOverride': '',
                'manualPlus': False,
                'sold': False,
                'autoFlag': True,
            }
            if out.get('sourceEmail'):
                payload['sourceEmail'] = out['sourceEmail']
                payload['toEmail'] = email
            if credential:
                payload['credential'] = credential
            if session_token:
                payload['sessionToken'] = session_token
            if access_token:
                payload['accessToken'] = access_token
            if out.get('refreshToken'):
                payload['refreshToken'] = out['refreshToken']
            if oauth_result.get('idToken'):
                payload['idToken'] = oauth_result['idToken']
            if out.get('refreshToken') or access_token:
                payload['oauthTokens'] = {
                    'access_token': access_token,
                    'refresh_token': out.get('refreshToken') or '',
                    'id_token': oauth_result.get('idToken') or '',
                    'session_token': session_token,
                }
            if sess_data:
                payload['session'] = sess_data
                payload['sessionJson'] = json.dumps(sess_data, ensure_ascii=False)
            imp = await import_account(import_base, import_key, payload, proxy)
            out['import'] = {'status': imp.get('status'), 'data': imp.get('data')}
            print(ts(), 'import', imp.get('status'), str(imp.get('data'))[:200])
            # update result file
            full['import'] = out['import']
            result_path.write_text(json.dumps(full, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            result_path.chmod(0o600)

        return out
    finally:
        try:
            await auth.close()
        except Exception:
            pass
        try:
            await mail.close()
        except Exception:
            pass


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--email', required=True, help='base iCloud email, e.g. leer.dip55@icloud.com')
    parser.add_argument('--proxy', default='', help='project proxy url')
    parser.add_argument('--no-import', action='store_true')
    parser.add_argument('--no-rt', action='store_true', help='skip post-register Codex RT acquisition')
    parser.add_argument('--otp-timeout', type=int, default=150)
    args = parser.parse_args()
    proxy = resolve_proxy(args.proxy)
    print('USING PROXY', proxy)
    print('IMPERSONATE', impersonate if 'impersonate' in dir() else IMPERSONATE)
    if args.no_rt:
        os.environ['APPLE_MAIL_FETCH_RT'] = '0'
    try:
        result = asyncio.run(run_one(args.email, proxy=proxy, do_import=not args.no_import, otp_timeout=args.otp_timeout))
    except Exception as e:
        finish_run(False, f'异常失败: {e}')
        raise
    print('\nRESULT_JSON')
    # print compact without full tokens
    safe = {k: v for k, v in result.items() if k not in {'accessToken', 'sessionToken', 'sessionRaw', 'refreshToken', 'idToken', 'codexAuthJson'}}
    print(json.dumps(safe, ensure_ascii=False, indent=2))
    if result.get('ok'):
        finish_run(True, '受控试跑成功', email=result.get('email'), sourceEmail=result.get('sourceEmail'), accountId=(result.get('session') or {}).get('accountId'))
    else:
        finish_run(False, f"失败: {result.get('error') or result.get('currentStep') or 'unknown'}", email=result.get('email'), error=result.get('error'))
    raise SystemExit(0 if result.get('ok') else 1)


if __name__ == '__main__':
    main()
