from __future__ import annotations
import json, re
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

def find_token(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("accessToken", "access_token", "token"):
            token = str(value.get(key) or "").strip()
            if token:
                return token
        for item in value.values():
            token = find_token(item)
            if token:
                return token
    if isinstance(value, list):
        for item in value:
            token = find_token(item)
            if token:
                return token
    return ""

def normalize_token(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if value.lower().startswith("bearer "):
        value = value.split(None, 1)[1].strip()
    if value.startswith("{") or value.startswith("["):
        try:
            return find_token(json.loads(value)) or value
        except Exception:
            return value
    return re.sub(r"\s+", "", value)

def normalize_proxy(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if "://" not in value:
        parts = value.split(":")
        if len(parts) >= 4 and parts[1].isdigit():
            host, port, user = parts[0], parts[1], parts[2]
            password = ":".join(parts[3:])
            value = f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
        elif "@" in value:
            value = f"http://{value}"
        else:
            value = f"http://{value}"
    return value

def proxy_country_hint(proxy: str) -> str:
    try:
        username = unquote(urlsplit(normalize_proxy(proxy)).username or "")
    except Exception:
        return ""
    match = re.search(r"(?:^|[-_])(?:region|res|country|cc)-([A-Za-z]{2})(?:[-_]|$)", username)
    return match.group(1).upper() if match else ""

def rewrite_proxy_country(proxy: str, country: str) -> str:
    proxy_url = normalize_proxy(proxy)
    target = str(country or "").strip().upper()
    if not proxy_url or not re.fullmatch(r"[A-Z]{2}", target):
        return proxy_url
    try:
        parsed = urlsplit(proxy_url)
        username = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
        if not username:
            return proxy_url
        pattern = re.compile(r"((?:^|[-_])(?:region|res|country|cc)-)([A-Za-z]{2})(?=[-_]|$)", re.I)
        def repl(match: re.Match[str]) -> str:
            prev = match.group(2)
            rep = target.lower() if prev.islower() else target.upper()
            return f"{match.group(1)}{rep}"
        username2, n = pattern.subn(repl, username, count=1)
        if not n:
            return proxy_url
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        auth = quote(username2, safe="")
        if password:
            auth = f"{auth}:{quote(password, safe='')}"
        return urlunsplit((parsed.scheme, f"{auth}@{host}", parsed.path, parsed.query, parsed.fragment))
    except Exception:
        return proxy_url

def amount_is_zero(value: Any) -> bool:
    text = str(value if value is not None else "").strip()
    if not text:
        return False
    try:
        return float(re.sub(r"[^\d.-]+", "", text) or "nan") == 0
    except Exception:
        return text in {"0", "0.0", "0.00"}
