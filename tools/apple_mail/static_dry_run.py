#!/usr/bin/env python3
"""Apple Mail static dry-run.

Validates local assets + proxy-gated outbound connectivity.
Does NOT open ChatGPT, does NOT register accounts, does NOT import accounts.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path('/opt/automyai')
DATA = ROOT / 'data' / 'apple_mail'
TOOLS = ROOT / 'tools' / 'apple_mail'
WEB_JS = ROOT / 'frontend' / 'js'
WEB_PAGES = ROOT / 'frontend' / 'pages'
DEFAULT_PROXY = 'http://172.19.0.1:7905'


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def resolve_proxy(cli_proxy: str = '') -> str:
    cfg = load_json(DATA / 'config.json', {}) or {}
    main = load_json(ROOT / 'config.json', {}) or {}
    return (
        (cli_proxy or '').strip()
        or str(cfg.get('proxyUrl') or '').strip()
        or str(main.get('BROWSER_PROXY') or '').strip()
        or str(main.get('UC_SIGNUP_PROXY') or '').strip()
        or os.environ.get('APPLE_MAIL_PROXY', '').strip()
        or os.environ.get('BROWSER_PROXY', '').strip()
        or DEFAULT_PROXY
    )


def fetch(url: str, proxy: str, headers: dict | None = None, timeout: int = 20, method: str = 'GET', data: bytes | None = None):
    if not proxy:
        raise RuntimeError('proxy required; refusing direct egress')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({
        'http': proxy,
        'https': proxy,
    }))
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with opener.open(req, timeout=timeout) as resp:
        body = resp.read()
        return resp.status, dict(resp.headers), body


def must_exist(path: Path) -> dict:
    ok = path.exists() and path.is_file() and path.stat().st_size > 0
    return {'path': str(path), 'ok': ok, 'size': path.stat().st_size if path.exists() else 0}


def main() -> int:
    parser = argparse.ArgumentParser(description='Apple Mail static dry-run (proxy required)')
    parser.add_argument('--proxy', default='', help='proxy url, default from automyai config')
    parser.add_argument('--allow-direct', action='store_true', help='dangerous: allow non-proxy checks (disabled by default)')
    parser.add_argument('--probe-import', action='store_true', help='optionally probe import host via proxy GET only')
    parser.add_argument('--json-out', default=str(DATA / 'static_dry_run_last.json'))
    args = parser.parse_args()

    proxy = resolve_proxy(args.proxy)
    if not proxy and not args.allow_direct:
        print('ERROR: no proxy configured; refuse to run against real local IP', file=sys.stderr)
        return 2

    cfg = load_json(DATA / 'config.json', {}) or {}
    secrets = load_json(DATA / 'secrets.json', {}) or {}
    emails = load_json(DATA / 'emails.json', []) or []
    names = load_json(DATA / 'names.json', []) or []
    console = (TOOLS / 'flowgpt_console.js').read_text(encoding='utf-8', errors='replace')
    page = (WEB_PAGES / 'apple_mail.html').read_text(encoding='utf-8', errors='replace')

    checks = []
    def add(name, ok, detail=None):
        item = {'name': name, 'ok': bool(ok)}
        if detail is not None:
            item['detail'] = detail
        checks.append(item)
        status = 'OK' if ok else 'FAIL'
        print(f'[{status}] {name}' + (f' :: {detail}' if detail is not None and not isinstance(detail, (dict, list)) else ''))

    # local static assets
    for p in [
        TOOLS / 'flowgpt_console.js',
        WEB_JS / 'apple_mail_console.js',
        WEB_JS / 'apple_mail_emails.json',
        WEB_JS / 'apple_mail_names.json',
        WEB_JS / 'apple_mail_config.json',
        WEB_PAGES / 'apple_mail.html',
        DATA / 'emails.json',
        DATA / 'names.json',
        DATA / 'config.json',
        DATA / 'secrets.json',
    ]:
        info = must_exist(p)
        add(f'asset:{p.name}', info['ok'], info)

    add('emails_count>=1', len(emails) >= 1, len(emails))
    add('names_count>=1', len(names) >= 1, len(names))
    add('console_has_AppleMail', 'window.AppleMail' in console)
    add('console_has_FlowGPT_alias', 'window.FlowGPT' in console)
    add('console_has_listMails', 'function listMails' in console or 'async function listMails' in console)
    add('console_has_waitCode', 'function waitCode' in console or 'async function waitCode' in console)
    add('console_has_import', 'function importAccount' in console or 'async function importAccount' in console)
    import_key = str(secrets.get('importApiKey') or '')
    add('console_has_no_embedded_import_key', bool(import_key) and import_key[:8] not in console)
    add('page_is_isolated', 'data-page="apple-mail"' in page and 'Apple Mail' in page)
    add('page_firefox_147_tip', 'Firefox 147' in page)
    add('page_not_openai2_mixed', 'gpt_outlook2' not in page and 'OpenAI 注册 2' not in page)
    add('require_proxy_config', bool(cfg.get('requireProxy', True)))
    add('proxy_configured', bool(proxy), proxy)

    # proxy egress identity
    if proxy:
        try:
            status, headers, body = fetch('http://ip-api.com/json', proxy=proxy, timeout=15)
            info = json.loads(body.decode('utf-8', 'replace'))
            add('proxy_egress', status == 200 and info.get('status') == 'success', {
                'ip': info.get('query'),
                'country': info.get('countryCode'),
                'city': info.get('city'),
                'isp': info.get('isp'),
            })
            # refuse obvious local/private markers
            ip = str(info.get('query') or '')
            add('proxy_not_private_ip', not ip.startswith(('10.', '127.', '192.168.', '172.16.', '172.17.', '172.18.', '172.19.', '172.20.')))
        except Exception as e:
            add('proxy_egress', False, str(e))

        # mail host reachability via proxy (no admin auth mutation)
        mail_base = str(cfg.get('mailBase') or 'https://apimail.kfjie.me').rstrip('/')
        try:
            status, headers, body = fetch(mail_base + '/', proxy=proxy, timeout=20, headers={'User-Agent': 'automyai-apple-mail-dryrun/1.0'})
            add('mail_host_via_proxy', status < 500, {'status': status, 'bytes': len(body)})
        except urllib.error.HTTPError as e:
            # HTTP error still proves host reachable via proxy
            add('mail_host_via_proxy', True, {'status': e.code, 'reason': str(e.reason)})
        except Exception as e:
            add('mail_host_via_proxy', False, str(e))

        if args.probe_import:
            import_base = str(cfg.get('importBase') or 'https://cloud.opus.sryze.cc').rstrip('/')
            try:
                status, headers, body = fetch(import_base + '/', proxy=proxy, timeout=20, headers={'User-Agent': 'automyai-apple-mail-dryrun/1.0'})
                add('import_host_via_proxy', status < 500, {'status': status, 'bytes': len(body)})
            except urllib.error.HTTPError as e:
                add('import_host_via_proxy', True, {'status': e.code, 'reason': str(e.reason)})
            except Exception as e:
                add('import_host_via_proxy', False, str(e))
    else:
        add('proxy_egress', False, 'no proxy')

    # static logic graph checks
    for fn in ['next', 'detectPage', 'fill', 'fillCode', 'waitCode', 'captureSession', 'fetchSessionFromApi', 'buildImportPayload', 'importAccount', 'auto', 'autoBatch']:
        add(f'logic:{fn}', re.search(rf'function\s+{fn}\b|{fn}\s*\(', console) is not None)

    failed = [c for c in checks if not c['ok']]
    result = {
        'ok': not failed,
        'proxy': proxy,
        'dryRun': True,
        'emails': len(emails),
        'names': len(names),
        'failed': [c['name'] for c in failed],
        'checks': checks,
    }
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('\nSUMMARY', 'PASS' if result['ok'] else 'FAIL', f"failed={len(failed)} proxy={proxy}")
    print('wrote', out)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
