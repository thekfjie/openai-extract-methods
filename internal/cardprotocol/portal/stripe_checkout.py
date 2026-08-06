"""Compatibility helpers required by the recovered card portal.

The sanitized snapshot referenced these helpers from its former payment-core
runtime but did not include that runtime in the archive.
"""
from __future__ import annotations

from typing import Any

from curl_cffi.requests import Session

STRIPE_API = "https://api.stripe.com"
STRIPE_VERSION_FULL = "2025-07-30.basil"
DEFAULT_STRIPE_RUNTIME_VERSION = "2025-07-30.basil"


def build_http(proxy: str | None = None) -> Session:
    http = Session(impersonate="chrome136")
    try:
        http.trust_env = False
    except Exception:
        pass
    if proxy:
        http.proxies = {"http": proxy, "https": proxy}
    return http


def _stripe_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://js.stripe.com",
        "Referer": "https://js.stripe.com/",
    }


def _find_next_action(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        action = value.get("next_action")
        if isinstance(action, dict):
            return action
        for item in value.values():
            found = _find_next_action(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_next_action(item)
            if found:
                return found
    return {}
