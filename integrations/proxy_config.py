"""Proxy URL parsing and the configured proxy pool.

Pure translation between proxy URLs, Sub2API proxy records and the local mihomo
profile names. `configured_*` reads live settings, so it imports CONFIG inside
the function to avoid an import cycle with server.
"""
from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from integrations.common import first_non_empty


MIHOMO_SUB2API_PROFILES: dict[str, tuple[str, str]] = {
    "US": ("Mihomo US", "http://172.19.0.1:7901"),
    "HK": ("Mihomo HK", "http://172.19.0.1:7902"),
    "JP": ("Mihomo JP", "http://172.19.0.1:7903"),
    "SG": ("Mihomo SG", "http://172.19.0.1:7904"),
    "TW": ("Mihomo TW", "http://172.19.0.1:7905"),
    "UK": ("Mihomo UK", "http://172.19.0.1:7906"),
    "KR": ("Mihomo KR", "http://172.19.0.1:7907"),
    "MY": ("Mihomo MY", "http://172.19.0.1:7908"),
    "NL": ("Mihomo NL", "http://172.19.0.1:7909"),
    "DE": ("Mihomo DE", "http://172.19.0.1:7910"),
    "DIRECT": ("Mihomo DIRECT", "http://172.19.0.1:7911"),
}
MIHOMO_DIRECT_PROXY_URL = MIHOMO_SUB2API_PROFILES["DIRECT"][1]


ROOT = Path(__file__).resolve().parent.parent
CLIPROXY_SOURCE_PATHS = (
    ROOT / "data" / "apple_mail" / "status.json",
    ROOT / "data" / "grok_ttk" / "status.json",
)


def _string_values(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _string_values(item)
    elif isinstance(value, str):
        yield value


def saved_cliproxy_url() -> str:
    candidates = [os.getenv("CLIPROXY_PROXY_URL", "").strip()]
    for path in CLIPROXY_SOURCE_PATHS:
        try:
            candidates.extend(_string_values(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    traffic_dir = ROOT / "data" / "traffic_meter" / "active"
    try:
        traffic_files = sorted(traffic_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        traffic_files = []
    for path in traffic_files[:20]:
        try:
            candidates.extend(_string_values(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    for candidate in candidates:
        parsed = parse_proxy_url(candidate)
        if not parsed:
            continue
        host = str(parsed.get("host") or "").lower()
        if host.endswith(".cliproxy.io") and parsed.get("username") and parsed.get("password"):
            return proxy_url_from_parsed(parsed)
    return ""


def resolve_proxy_source(value: Any) -> str:
    text = str(value or "").strip()
    if text.upper() in {"CLIPROXY", "CLIPROXY_SAVED", "SAVED_CLIPROXY"}:
        return saved_cliproxy_url()
    return text

def parse_proxy_url(value: Any) -> dict[str, Any] | None:
    text = str(value or "").strip().strip("\"'")
    if not text:
        return None
    if "://" not in text:
        parts = text.split(":")
        # Standard provider form used by Cliproxy / DataImpulse / many residential pools.
        if len(parts) >= 4 and parts[1].isdigit():
            host, port, username = parts[0], parts[1], parts[2]
            password = ":".join(parts[3:])
            text = (
                f"http://{quote(username, safe='')}:"
                f"{quote(password, safe='')}@{host}:{port}"
            )
        else:
            text = f"http://{text}"
    parsed = urlparse(text)
    protocol = (parsed.scheme or "http").lower()
    host = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError:
        port = None
    if not host or port is None:
        return None
    if protocol not in {"http", "https", "socks5", "socks5h"}:
        return None
    return {
        "protocol": protocol,
        "host": host,
        "port": int(port),
        "username": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
    }


def proxy_url_from_parsed(proxy: dict[str, Any]) -> str:
    protocol = str(proxy.get("protocol") or "http").lower()
    host = str(proxy.get("host") or "")
    port = str(proxy.get("port") or "")
    username = str(proxy.get("username") or "")
    password = str(proxy.get("password") or "")
    auth = (
        f"{quote(username, safe='')}:{quote(password, safe='')}@"
        if username or password
        else ""
    )
    return f"{protocol}://{auth}{host}:{port}"


def sub2api_proxy_key(proxy: dict[str, Any]) -> str:
    return "|".join(
        [
            str(proxy.get("protocol") or "http").lower(),
            str(proxy.get("host") or ""),
            str(proxy.get("port") or ""),
            str(proxy.get("username") or ""),
            str(proxy.get("password") or ""),
        ]
    )


def normalize_proxy_region(value: Any) -> str:
    text = str(value or "").strip().upper().replace("-", "_")
    aliases = {
        "USA": "US",
        "UNITED_STATES": "US",
        "HONGKONG": "HK",
        "HONG_KONG": "HK",
        "JAPAN": "JP",
        "SINGAPORE": "SG",
        "TAIWAN": "TW",
        "UNITED_KINGDOM": "UK",
        "KOREA": "KR",
        "SOUTH_KOREA": "KR",
        "MALAYSIA": "MY",
        "NETHERLANDS": "NL",
        "GERMANY": "DE",
    }
    return aliases.get(text, text)


def known_mihomo_proxy_name(proxy: dict[str, Any]) -> str:
    host = str(proxy.get("host") or "")
    port = int(proxy.get("port") or 0)
    for name, url in MIHOMO_SUB2API_PROFILES.values():
        parsed = parse_proxy_url(url)
        if parsed and parsed.get("host") == host and parsed.get("port") == port:
            return name
    return ""


def proxy_name_for_url(proxy_url: str) -> str:
    parsed = parse_proxy_url(proxy_url)
    if not parsed:
        return str(proxy_url or "").strip()
    known_name = known_mihomo_proxy_name(parsed)
    if known_name:
        return known_name
    return f"{parsed['protocol']}://{parsed['host']}:{parsed['port']}"


def sub2api_proxy_from_url(proxy_url: str, name: str = "") -> dict[str, Any] | None:
    parsed = parse_proxy_url(proxy_url)
    if not parsed:
        return None
    proxy = {
        "name": name or proxy_name_for_url(proxy_url),
        "protocol": parsed["protocol"],
        "host": parsed["host"],
        "port": parsed["port"],
        "username": parsed.get("username") or "",
        "password": parsed.get("password") or "",
        "status": "active",
    }
    proxy["proxy_key"] = sub2api_proxy_key(proxy)
    return proxy


def configured_sub2api_proxy() -> dict[str, Any] | None:
    from server import CONFIG  # live settings; server imports this module

    # Sub2API/OAuth import has its own proxy lane. If no dedicated setting
    # exists, use the JP gateway; never inherit a browser/signup proxy.
    region = normalize_proxy_region(CONFIG.sub2api_proxy_region) or "JP"
    profile = MIHOMO_SUB2API_PROFILES.get(region) if region else None
    profile_name, profile_url = profile if profile else ("", "")
    proxy_url = resolve_proxy_source(
        first_non_empty(CONFIG.sub2api_proxy_url, profile_url)
    )
    parsed = parse_proxy_url(proxy_url)
    if not parsed:
        return None
    name = str(first_non_empty(CONFIG.sub2api_proxy_name, profile_name, known_mihomo_proxy_name(parsed)) or "").strip()
    if not name:
        name = f"{parsed['protocol']}://{parsed['host']}:{parsed['port']}"
    proxy = {
        "name": name,
        "protocol": parsed["protocol"],
        "host": parsed["host"],
        "port": parsed["port"],
        "username": parsed.get("username") or "",
        "password": parsed.get("password") or "",
        "status": "active",
    }
    proxy["proxy_key"] = sub2api_proxy_key(proxy)
    return proxy


def parse_proxy_pool_urls(value: Any) -> list[str]:
    text = resolve_proxy_source(value)
    if not text:
        return []
    if text.upper() in {"MIHOMO", "BUILTIN_MIHOMO", "DEFAULT_MIHOMO"}:
        return [url for region, (_, url) in MIHOMO_SUB2API_PROFILES.items() if region != "DIRECT"]
    candidates = re.split(r"[\s,;，；|]+", text)
    urls: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        raw = str(candidate or "").strip()
        if not raw:
            continue
        parsed = parse_proxy_url(raw)
        if not parsed:
            continue
        url = proxy_url_from_parsed(parsed)
        key = sub2api_proxy_key(parsed)
        if key in seen:
            continue
        seen.add(key)
        urls.append(url)
    return urls


def configured_signup_proxy_candidates(explicit_proxy: Any = "") -> list[dict[str, Any]]:
    from server import CONFIG  # live settings; server imports this module

    # Task-level explicit proxy always wins. OpenAI 注册面板的自定义代理会走这里，
    # 不能再被全局 SIGNUP_PROXY_MODE=regional 静默盖掉。
    explicit_urls = parse_proxy_pool_urls(str(explicit_proxy or "").strip())
    if explicit_urls:
        raw_urls = explicit_urls
        forced_name = "自定义注册代理"
    else:
        mode = str(getattr(CONFIG, "signup_proxy_mode", "") or "").strip().lower()
        region = normalize_proxy_region(getattr(CONFIG, "signup_proxy_region", ""))
        custom_url = str(getattr(CONFIG, "signup_proxy_custom_url", "") or "").strip()
        cliproxy_url = str(getattr(CONFIG, "cliproxy_proxy_url", "") or "").strip()
        forced_name = ""
        # 无显式任务代理时：只接受用户主动配置的代理。
        # regional 不再自动塞 Mihomo 默认出口；没配代理就返回空，启动侧直接拒绝。
        if mode == "cliproxy":
            raw_urls = parse_proxy_pool_urls(cliproxy_url)
        elif mode in {"custom", "regional", "manual", ""}:
            raw_urls = parse_proxy_pool_urls(custom_url or CONFIG.uc_signup_proxy or CONFIG.browser_proxy)
        else:
            raw_urls = parse_proxy_pool_urls(CONFIG.proxy_pool_urls)
            for value in (CONFIG.uc_signup_proxy, CONFIG.browser_proxy, custom_url, cliproxy_url):
                if value:
                    raw_urls.extend(parse_proxy_pool_urls(value))

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    direct_proxy_key = ""
    direct_proxy = sub2api_proxy_from_url(MIHOMO_DIRECT_PROXY_URL)
    if direct_proxy:
        direct_proxy_key = str(direct_proxy.get("proxy_key") or "")
    for url in raw_urls:
        proxy = sub2api_proxy_from_url(url, forced_name) if forced_name else sub2api_proxy_from_url(url)
        if not proxy:
            continue
        key = str(proxy.get("proxy_key") or "")
        if key and key == direct_proxy_key:
            continue
        if not key or key in seen:
            continue
        seen.add(key)
        result.append({"url": proxy_url_from_parsed(proxy), "key": key, "name": proxy.get("name") or "", "proxy": proxy})
    return result
