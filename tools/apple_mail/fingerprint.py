#!/usr/bin/env python3
"""Apple Mail fingerprint / browser environment.

Aligned to the Outlook protocol register machine environment:
- curl_cffi impersonate = chrome131
- fixed Windows Chrome UA + sec-ch-ua
- Accept-Language zh-CN
- one consistent bundle per account (device_id still unique)
"""
from __future__ import annotations

import random
import uuid
from typing import Any


# Keep in lockstep with Outlook 注册机:
# impersonate="chrome131"
# Chrome/131.0.6778.86 + matching sec-ch-ua
OUTLOOK_ENV = {
    'family': 'chrome',
    'impersonate': 'chrome131',
    'chrome_major': '131',
    'chrome_full': '131.0.6778.86',
    'user_agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/131.0.6778.86 Safari/537.36'
    ),
    'sec_ch_ua': '"Chromium";v="131", "Google Chrome";v="131", "Not/A)Brand";v="99"',
    'sec_ch_ua_mobile': '?0',
    'sec_ch_ua_platform': '"Windows"',
    'accept_language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'lang': 'zh-CN',
    'lang_full': 'zh-CN,zh;q=0.9,en;q=0.8',
    'platform': 'Windows',
    'screen': '1920x1080',
    'source': 'outlook_register_protocol',
}


def generate_fingerprint(rng: random.Random | None = None, prefer: str = '') -> dict[str, Any]:
    """Return Outlook-aligned environment. prefer is ignored on purpose."""
    _ = prefer
    rng = rng or random.Random()
    # keep tiny non-identity jitter only on device id; TLS/UA stay fixed like Outlook machine
    fp = dict(OUTLOOK_ENV)
    fp['device_id'] = str(uuid.UUID(int=rng.getrandbits(128)))
    return fp


def outlook_headers(extra: dict | None = None) -> dict[str, str]:
    h = {
        'User-Agent': OUTLOOK_ENV['user_agent'],
        'sec-ch-ua': OUTLOOK_ENV['sec_ch_ua'],
        'sec-ch-ua-mobile': OUTLOOK_ENV['sec_ch_ua_mobile'],
        'sec-ch-ua-platform': OUTLOOK_ENV['sec_ch_ua_platform'],
        'Accept-Language': OUTLOOK_ENV['accept_language'],
    }
    if extra:
        h.update(extra)
    return h
