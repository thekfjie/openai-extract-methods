#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Grok 注册机 - TTK GUI 版本
整合 DrissionPage_example.py, openai_register.py, batch_open_nsfw.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import datetime
import time
import os
import sys
import queue
import secrets
import struct
import random
import re
import string
import json
import hashlib
import ipaddress
from urllib.parse import urlparse

from DrissionPage import Chromium, ChromiumOptions
from DrissionPage.errors import PageDisconnectedError
from curl_cffi import CurlMime, requests


CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "duckmail_api_key": "",
    "cloudflare_api_base": "",
    "cloudflare_api_key": "",
    "cloudflare_auth_mode": "none",
    "cloudflare_custom_auth": "",
    "cloudflare_path_domains": "/api/domains",
    "cloudflare_path_accounts": "/api/new_address",
    "cloudflare_path_token": "/api/token",
    "cloudflare_path_messages": "/api/mails",
    "proxy": "http://127.0.0.1:7890",
    "enable_nsfw": True,
    "register_count": 1,
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "grok2api_auto_add_local": True,
    "grok2api_local_token_file": "",
    "grok2api_pool_name": "ssoBasic",
    "grok2api_auto_add_remote": False,
    "grok2api_remote_base": "",
    "grok2api_remote_app_key": "",
    "register_threads": 1,
    "thread_start_interval": 0.8,
    "show_tutorial_on_start": True,
}

config = DEFAULT_CONFIG.copy()
_cf_domain_index = 0


class RegistrationCancelled(Exception):
    pass


def load_config():
    global config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            config = {**DEFAULT_CONFIG, **loaded}
        except Exception:
            config = DEFAULT_CONFIG.copy()
    return config


def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"保存配置失败: {e}")


def get_account_export_dir(day=None):
    day = day or datetime.datetime.now().strftime("%Y%m%d")
    export_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "account", day)
    os.makedirs(export_dir, exist_ok=True)
    return export_dir


def get_account_export_paths():
    now = datetime.datetime.now()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    export_dir = get_account_export_dir(now.strftime("%Y%m%d"))
    return (
        os.path.join(export_dir, f"accounts_{stamp}.txt"),
        os.path.join(export_dir, f"tokens_eyJ_{stamp}.txt"),
    )


def normalize_export_token(raw_token):
    token = str(raw_token or "").strip()
    if token.startswith("sso="):
        token = token[4:].strip()
    return token


def ensure_stable_python_runtime():
    if sys.version_info < (3, 14) or os.environ.get("DPE_REEXEC_DONE") == "1":
        return

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(local_app_data, "Programs", "Python", "Python312", "python.exe"),
        os.path.join(local_app_data, "Programs", "Python", "Python313", "python.exe"),
    ]

    current_python = os.path.normcase(os.path.abspath(sys.executable))
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        if os.path.normcase(os.path.abspath(candidate)) == current_python:
            return

        print(
            f"[*] 检测到 Python {sys.version.split()[0]}，自动切换到更稳定的解释器: {candidate}"
        )
        env = os.environ.copy()
        env["DPE_REEXEC_DONE"] = "1"
        os.execve(candidate, [candidate, os.path.abspath(__file__), *sys.argv[1:]], env)


def warn_runtime_compatibility():
    if sys.version_info >= (3, 14):
        print(
            "[提示] 当前 Python 为 3.14+；若出现 Mail.tm TLS 异常，建议改用 Python 3.12 或 3.13。"
        )


ensure_stable_python_runtime()
warn_runtime_compatibility()

load_config()

EXTENSION_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "turnstilePatch")
)


DUCKMAIL_API_BASE = "https://api.duckmail.sbs"


def get_proxies():
    proxy = config.get("proxy", "")
    if proxy:
        return {"http": proxy, "https": proxy}
    return {}


def get_duckmail_api_key():
    return config.get("duckmail_api_key", "")


def get_cloudflare_api_base():
    return str(config.get("cloudflare_api_base", "") or "").rstrip("/")


def get_cloudflare_api_key():
    return config.get("cloudflare_api_key", "")


def get_cloudflare_auth_mode():
    return str(config.get("cloudflare_auth_mode", "none") or "none").lower()


def get_cloudflare_custom_auth():
    """Global access password for cloudflare_temp_email PASSWORDS (x-custom-auth)."""
    return str(config.get("cloudflare_custom_auth", "") or "").strip()


def cloudflare_apply_custom_auth(headers):
    custom_auth = get_cloudflare_custom_auth()
    if custom_auth:
        headers["x-custom-auth"] = custom_auth
    return headers


def get_cloudflare_path(key, default_path):
    raw = str(config.get(key, default_path) or default_path).strip()
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw


def cloudflare_build_headers(content_type=False):
    headers = {"Content-Type": "application/json"} if content_type else {}
    key = get_cloudflare_api_key()
    mode = get_cloudflare_auth_mode()
    if key:
        if mode == "x-api-key":
            headers["X-API-Key"] = key
        elif mode == "x-admin-auth":
            headers["x-admin-auth"] = key
        elif mode != "none":
            headers["Authorization"] = f"Bearer {key}"
    cloudflare_apply_custom_auth(headers)
    return headers


def cloudflare_apply_auth_params(params=None):
    merged = dict(params or {})
    key = get_cloudflare_api_key()
    mode = get_cloudflare_auth_mode()
    if key and mode == "query-key":
        merged["key"] = key
    return merged


def cloudflare_next_default_domain():
    domains = [x.strip() for x in str(config.get("defaultDomains", "") or "").split(",") if x.strip()]
    if not domains:
        return ""
    # 多个已验证域名随机分流，避免长期固定使用同一个二级域名。
    return secrets.choice(domains)


def cloudflare_is_admin_create_path(path):
    return str(path or "").rstrip("/").lower() == "/admin/new_address"


def _pick_list_payload(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            return data.get("results")
        if isinstance(data.get("hydra:member"), list):
            return data.get("hydra:member")
        if isinstance(data.get("data"), list):
            return data.get("data")
        if isinstance(data.get("messages"), list):
            return data.get("messages")
        if isinstance(data.get("data"), dict):
            nested = data.get("data")
            if isinstance(nested.get("messages"), list):
                return nested.get("messages")
    return []


def cloudflare_create_temp_address(api_base):
    """适配 cloudflare_temp_email 新建地址接口，兼容 admin 创建与 x-custom-auth。"""
    path = get_cloudflare_path("cloudflare_path_accounts", "/api/new_address")
    url = f"{api_base}{path}"
    domain = cloudflare_next_default_domain()
    is_admin_create = cloudflare_is_admin_create_path(path)
    if is_admin_create:
        payload = {"name": generate_username(10), "enablePrefix": True}
        if domain:
            payload["domain"] = domain
        headers = cloudflare_build_headers(content_type=True)
    else:
        payload = {}
        if domain:
            payload["domain"] = domain
        headers = cloudflare_apply_custom_auth({"Content-Type": "application/json"})
    resp = http_post(url, json=payload, headers=headers)
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"Cloudflare {path} 返回非JSON: {resp.text[:300]}")
    address = data.get("address")
    jwt = data.get("jwt")
    if not address or not jwt:
        raise Exception(f"Cloudflare {path} 缺少 address/jwt: {data}")
    return address, jwt


def get_user_agent():
    return config.get(
        "user_agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    )


def resolve_grok2api_local_token_file():
    configured = str(config.get("grok2api_local_token_file", "") or "").strip()
    if configured:
        return configured
    return r"D:\注册机\3255d5ee6e702db9220a897df64635a1ec9df644\vendor\grok2api\data\token.json"


def _normalize_sso_token(raw_token):
    token = str(raw_token or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    return token


def add_token_to_grok2api_local_pool(raw_token, email="", log_callback=None):
    token = _normalize_sso_token(raw_token)
    if not token:
        return False
    token_file = resolve_grok2api_local_token_file()
    pool_name = str(config.get("grok2api_pool_name", "ssoBasic") or "ssoBasic").strip()
    if not pool_name:
        pool_name = "ssoBasic"
    os.makedirs(os.path.dirname(token_file), exist_ok=True)
    data = {}
    if os.path.exists(token_file):
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}
    pool = data.get(pool_name)
    if not isinstance(pool, list):
        pool = []
    existing = set()
    for item in pool:
        if isinstance(item, str):
            existing.add(_normalize_sso_token(item))
        elif isinstance(item, dict):
            existing.add(_normalize_sso_token(item.get("token", "")))
    if token in existing:
        if log_callback:
            log_callback(f"[*] grok2api 本地池已存在 token: {pool_name}")
        return True
    entry = {"token": token, "tags": ["auto-register"], "note": email}
    pool.append(entry)
    data[pool_name] = pool
    with open(token_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if log_callback:
        log_callback(f"[+] 已写入 grok2api 本地池: {pool_name} ({token_file})，入池完成喵~")
    return True


def _load_grok2api_admin_credentials():
    username = str(config.get("grok2api_remote_username", "") or "").strip()
    password = str(config.get("grok2api_remote_password", "") or "").strip()
    credentials_file = str(
        config.get("grok2api_remote_credentials_file", "")
        or os.environ.get("GROK2API_ADMIN_CREDENTIALS_FILE")
        or "/run/secrets/grok2api_admin_credentials"
    ).strip()
    if credentials_file and os.path.isfile(credentials_file):
        values = {}
        with open(credentials_file, "r", encoding="utf-8") as handle:
            for line in handle:
                key, separator, value = line.partition("=")
                if separator:
                    values[key.strip().lower()] = value.strip()
        username = username or values.get("username", "")
        password = password or values.get("password", "")
    # Compatibility for v3 installations still using the default admin name.
    legacy_key = str(config.get("grok2api_remote_app_key", "") or "").strip()
    if not password and legacy_key:
        username = username or "admin"
        password = legacy_key
    if not username or not password:
        raise RuntimeError("grok2api v3 管理员用户名或密码未配置")
    return username, password


def _parse_grok2api_import_events(raw):
    complete = None
    for block in str(raw or "").replace("\r\n", "\n").split("\n\n"):
        event = ""
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not event or not data_lines:
            continue
        try:
            value = json.loads("\n".join(data_lines))
        except Exception:
            value = {}
        if event == "error":
            raise RuntimeError(str(value.get("message") or value.get("code") or "grok2api 导入失败"))
        if event == "complete" and isinstance(value, dict):
            complete = value
    if complete is None:
        raise RuntimeError("grok2api 导入响应缺少 complete 事件")
    return complete


def add_token_to_grok2api_remote_pool(raw_token, email="", log_callback=None):
    """Append one Web SSO account through grok2api v3's import endpoint."""
    token = _normalize_sso_token(raw_token)
    if not token:
        return False
    base = str(config.get("grok2api_remote_base", "") or "").strip().rstrip("/")
    for suffix in ("/api/admin/v1", "/admin/api"):
        if base.endswith(suffix):
            base = base[:-len(suffix)].rstrip("/")
    if not base:
        if log_callback:
            log_callback("[Debug] grok2api 远端 Base 未配置，跳过")
        return False

    username, password = _load_grok2api_admin_credentials()
    login = http_post(
        f"{base}/api/admin/v1/auth/login",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json={"username": username, "password": password},
        timeout=20,
        proxies={},
    )
    login.raise_for_status()
    try:
        access_token = str(login.json()["data"]["tokens"]["accessToken"] or "").strip()
    except Exception as exc:
        raise RuntimeError("grok2api v3 登录响应缺少 accessToken") from exc
    if not access_token:
        raise RuntimeError("grok2api v3 登录响应缺少 accessToken")

    pool_name = str(config.get("grok2api_pool_name", "ssoBasic") or "ssoBasic").strip()
    tier_map = {
        "ssoBasic": "basic", "ssoSuper": "super", "basic": "basic",
        "super": "super", "heavy": "heavy", "auto": "auto",
    }
    tier = tier_map.get(pool_name, "auto")
    account_name = str(email or "").strip() or f"AutomyAI {hashlib.sha256(token.encode()).hexdigest()[:8]}"
    document = {
        "provider": "grok_web",
        "accounts": [{"name": account_name, "sso_token": token, "tier": tier}],
    }
    multipart = CurlMime()
    multipart.addpart(
        name="files",
        filename="automyai-grok-web.json",
        content_type="application/json",
        data=json.dumps(document, ensure_ascii=False).encode("utf-8"),
    )
    try:
        response = http_post(
            f"{base}/api/admin/v1/accounts/web/import",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "text/event-stream"},
            multipart=multipart,
            timeout=120,
            proxies={},
        )
        response.raise_for_status()
        result = _parse_grok2api_import_events(response.text)
    finally:
        multipart.close()
    if log_callback:
        log_callback(
            "[+] 已追加导入 grok2api Web 账号喵~ "
            f"tier={tier} created={result.get('created', 0)} updated={result.get('updated', 0)}"
        )
    return True


def add_token_to_grok2api_pools(raw_token, email="", log_callback=None):
    if config.get("grok2api_auto_add_local", True):
        try:
            add_token_to_grok2api_local_pool(raw_token, email=email, log_callback=log_callback)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 写入 grok2api 本地池失败: {exc}")
    if config.get("grok2api_auto_add_remote", False):
        try:
            add_token_to_grok2api_remote_pool(raw_token, email=email, log_callback=log_callback)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 写入 grok2api 远端池失败: {exc}")



def fetch_flaresolverr_clearance(target_url=None, proxy=None, log_callback=None, timeout=90):
    """Use FlareSolverr to obtain CF cookies (same path as automyai OAI/Grok signup)."""
    api = (
        str(os.environ.get("GROK_CF_CLEARANCE_API_URL") or "").strip()
        or str(config.get("cf_clearance_api_url") or "").strip()
        or "http://127.0.0.1:18191/v1"
    )
    enabled = str(
        os.environ.get("GROK_CF_CLEARANCE_ENABLED")
        or config.get("cf_clearance_enabled", "true")
        or "true"
    ).lower() in {"1", "true", "yes", "on"}
    if not enabled:
        if log_callback:
            log_callback("[Debug] CF clearance 已关闭")
        return None
    target = (
        target_url
        or str(os.environ.get("GROK_CF_CLEARANCE_TARGET_URL") or "").strip()
        or "https://accounts.x.ai/sign-up"
    )
    proxy = proxy if proxy is not None else str(config.get("proxy") or "").strip()
    payload = {
        "cmd": "request.get",
        "url": target,
        "maxTimeout": max(int(timeout), 30) * 1000,
    }
    if proxy:
        # FlareSolverr expects {url: ...}; normalize host:port:user:pass
        p = proxy
        if "://" not in p and p.count(":") >= 3:
            host, port, user, password = p.split(":", 3)
            p = f"http://{user}:{password}@{host}:{port}"
        payload["proxy"] = {"url": p}
    if log_callback:
        log_callback(f"[*] FlareSolverr 预解挑战，猫爪先去探路喵: {target}")
    try:
        resp = http_post(api, json=payload, headers={"Content-Type": "application/json"}, timeout=timeout + 30, proxies={})
        data = resp.json()
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] FlareSolverr 请求失败: {exc}")
        return None
    if not isinstance(data, dict) or data.get("status") != "ok":
        if log_callback:
            log_callback(f"[Debug] FlareSolverr 未成功: {(data or {})}")
        return None
    solution = data.get("solution") if isinstance(data.get("solution"), dict) else {}
    cookies = solution.get("cookies") if isinstance(solution.get("cookies"), list) else []
    ua = str(solution.get("userAgent") or "")
    names = [str(c.get("name") or "") for c in cookies if c.get("name")]
    if log_callback:
        log_callback(f"[*] FlareSolverr 探路完成喵~ status={solution.get('status')} cookies=[{','.join(names)}]")
    # status=200 with empty cookies is NOT useful clearance (common for already-open pages / bot blocks).
    if not cookies or not any(n.lower() in {"cf_clearance", "__cf_bm", "cf_bm"} or "cf" in n.lower() for n in names):
        if log_callback:
            log_callback("[Debug] FlareSolverr 未返回有效 CF cookie（这不代表浏览器 Turnstile 已过；注册页内嵌挑战仍需浏览器处理）")
        # Still return UA if present, but mark empty so apply can skip.
        if not cookies:
            return None
    return {"cookies": cookies, "user_agent": ua, "url": solution.get("url") or target}


def apply_clearance_to_page(page, bundle, log_callback=None):
    """Seed cookies/user-agent from FlareSolverr into the active DrissionPage tab."""
    if not bundle or page is None:
        return False
    cookies = bundle.get("cookies") or []
    if not cookies:
        return False
    applied = 0
    for cookie in cookies:
        try:
            name = str(cookie.get("name") or "")
            value = str(cookie.get("value") or "")
            if not name or not value:
                continue
            domain = str(cookie.get("domain") or ".x.ai")
            path = str(cookie.get("path") or "/")
            item = {"name": name, "value": value, "domain": domain, "path": path}
            # DrissionPage set.cookies variants
            try:
                page.set.cookies(item)
            except Exception:
                try:
                    page.set.cookies([item])
                except Exception:
                    try:
                        page.run_cdp("Network.setCookie", **{
                            "name": name,
                            "value": value,
                            "domain": domain.lstrip(".") if domain.startswith(".") else domain,
                            "path": path,
                            "secure": True,
                        })
                    except Exception:
                        continue
            applied += 1
        except Exception:
            continue
    if log_callback:
        log_callback(f"[*] 已写入浏览器 cookie {applied} 个（含 cf_clearance 若有）")
    return applied > 0


def page_looks_like_cf_challenge(page) -> bool:
    """True only for full-page CF interstitial, not normal pages with Turnstile widgets."""
    try:
        html = (page.html or "")[:12000].lower()
        title = (page.title or "").lower()
        url = (page.url or "").lower()
    except Exception:
        return False
    strong = (
        "just a moment",
        "checking your browser",
        "attention required",
        "cf-browser-verification",
        "enable javascript and cookies to continue",
    )
    blob = f"{title}\n{html}"
    if any(s in blob for s in strong):
        return True
    try:
        has_email = page.run_js(
            """
const input = document.querySelector('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"]');
return !!input;
"""
        )
        if has_email:
            return False
    except Exception:
        pass
    if "/cdn-cgi/challenge" in url or "cf-chl" in url:
        return True
    # bare "turnstile" alone is NOT a full-page block
    return False


def wait_for_email_form(timeout=25, log_callback=None, cancel_callback=None):
    page = refresh_active_page()
    deadline = time.time() + timeout
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            ready = page.run_js(
                """
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
const input = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"]')).find((n)=>isVisible(n) && !n.disabled);
return !!input;
"""
            )
            if ready:
                return True
        except Exception:
            pass
        if page_looks_like_cf_challenge(page):
            if log_callback:
                log_callback("[Debug] 检测到 Cloudflare 挑战页，等待/尝试 Turnstile...")
            try:
                getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] Turnstile 等待中: {exc}")
        sleep_with_cancel(0.8, cancel_callback)
        page = refresh_active_page()
    return False


def create_browser_options():
    options = ChromiumOptions()
    options.auto_port()
    options.set_timeouts(base=1)
    if os.path.exists(EXTENSION_PATH):
        options.add_extension(EXTENSION_PATH)
        # Note: log_callback not available here; print for container logs.
        print(f"[*] 已加载 Turnstile 扩展: {EXTENSION_PATH}", flush=True)
    else:
        print(f"[!] 未找到 turnstilePatch 扩展目录: {EXTENSION_PATH}", flush=True)
    # Linux / container friendly defaults (Xvfb + system chromium)
    for candidate in (
        os.environ.get("CHROME_PATH"),
        os.environ.get("CHROMIUM_PATH"),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ):
        if candidate and os.path.exists(candidate):
            try:
                options.set_browser_path(candidate)
            except Exception:
                try:
                    options.set_paths(browser_path=candidate)
                except Exception:
                    pass
            break
    if os.name != "nt":
        for arg in ("--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--window-size=1440,900"):
            try:
                options.set_argument(arg)
            except Exception:
                try:
                    options.add_argument(arg)
                except Exception:
                    pass
        display = os.environ.get("DISPLAY") or os.environ.get("BROWSER_DISPLAY")
        if display:
            os.environ["DISPLAY"] = display if str(display).startswith(":") else f":{display}"
    # Critical: route browser traffic via proxy so registration never uses host real IP.
    # Prefer env override from headless launcher (may be a local auth-relay), else config.proxy.
    proxy = (
        str(os.environ.get("GROK_TTK_BROWSER_PROXY") or "").strip()
        or str(config.get("proxy") or "").strip()
    )
    if proxy:
        # Chromium --proxy-server does not accept user:pass; relay is provided by headless runner.
        # Strip credentials if present as a last resort (will fail auth upstream).
        browser_proxy = proxy
        if "@" in proxy and "://" in proxy:
            try:
                scheme, rest = proxy.split("://", 1)
                if "@" in rest:
                    creds, hostpart = rest.rsplit("@", 1)
                    browser_proxy = f"{scheme}://{hostpart}"
            except Exception:
                browser_proxy = proxy
        # Prefer set_proxy when available
        applied = False
        try:
            if hasattr(options, "set_proxy") and "@" not in (proxy.split("://", 1)[-1] if "://" in proxy else proxy):
                options.set_proxy(browser_proxy)
                applied = True
        except Exception:
            applied = False
        if not applied:
            arg = f"--proxy-server={browser_proxy}"
            try:
                options.set_argument(arg)
            except Exception:
                try:
                    options.add_argument(arg)
                except Exception:
                    pass
        # Avoid Chrome bypassing proxy for localhost only is fine; do not add no-proxy for external.
    return options


def _should_bypass_proxy(url):
    """Keep our own/internal APIs off the registration proxy."""
    try:
        host = (urlparse(str(url or "")).hostname or "").strip().lower()
    except Exception:
        return False
    if not host:
        return False
    if host in {"localhost", "apimail.kfjie.me", "automyai.kfjie.me"} or host.endswith(".localhost"):
        return True
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        return False


def _build_request_kwargs(url=None, **kwargs):
    request_kwargs = dict(kwargs)
    proxies = request_kwargs.pop("proxies", None)
    if _should_bypass_proxy(url):
        proxies = {}
    elif proxies is None:
        proxies = get_proxies()
    if proxies:
        request_kwargs["proxies"] = proxies
    request_kwargs.setdefault("timeout", 15)
    return request_kwargs


def http_get(url, **kwargs):
    try:
        return requests.get(url, **_build_request_kwargs(url, **kwargs))
    except Exception as exc:
        err = str(exc)
        # 代理不可用或代理 TLS 握手异常时自动回退为直连。
        # curl_cffi 经部分 HTTP CONNECT 中继访问邮箱 API 时可能报 curl(35)
        # OPENSSL_internal:invalid library，这与邮箱地址/域名本身无关。
        proxy_errors = (
            "127.0.0.1 port 7890",
            "Could not connect to server",
            "TLS connect error",
            "OPENSSL_internal:invalid library",
        )
        if any(marker in err for marker in proxy_errors):
            retry_kwargs = dict(kwargs)
            retry_kwargs["proxies"] = {}
            return requests.get(url, **_build_request_kwargs(url, **retry_kwargs))
        raise


def http_post(url, **kwargs):
    try:
        return requests.post(url, **_build_request_kwargs(url, **kwargs))
    except Exception as exc:
        err = str(exc)
        proxy_errors = (
            "127.0.0.1 port 7890",
            "Could not connect to server",
            "TLS connect error",
            "OPENSSL_internal:invalid library",
        )
        if any(marker in err for marker in proxy_errors):
            retry_kwargs = dict(kwargs)
            retry_kwargs["proxies"] = {}
            return requests.post(url, **_build_request_kwargs(url, **retry_kwargs))
        raise


def raise_if_cancelled(cancel_callback=None):
    if cancel_callback and cancel_callback():
        raise RegistrationCancelled("鐢ㄦ埛鍋滄娉ㄥ唽")


def sleep_with_cancel(seconds, cancel_callback=None):
    deadline = time.time() + max(seconds, 0)
    while True:
        raise_if_cancelled(cancel_callback)
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def get_domains(api_key=None):
    headers = {}
    key = api_key or get_duckmail_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    resp = http_get(f"{DUCKMAIL_API_BASE}/domains", headers=headers)
    resp.raise_for_status()
    return resp.json().get("hydra:member", [])


def create_account(address, password, api_key=None, expires_in=0):
    headers = {"Content-Type": "application/json"}
    key = api_key or get_duckmail_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = {"address": address, "password": password, "expiresIn": expires_in}
    resp = http_post(f"{DUCKMAIL_API_BASE}/accounts", json=data, headers=headers)
    resp.raise_for_status()
    return resp.json()


def get_token(address, password):
    data = {"address": address, "password": password}
    resp = http_post(f"{DUCKMAIL_API_BASE}/token", json=data)
    resp.raise_for_status()
    return resp.json().get("token")


def get_messages(token):
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_get(f"{DUCKMAIL_API_BASE}/messages", headers=headers)
    resp.raise_for_status()
    return resp.json().get("hydra:member", [])


def get_message_detail(token, message_id):
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_get(f"{DUCKMAIL_API_BASE}/messages/{message_id}", headers=headers)
    resp.raise_for_status()
    return resp.json()


def cloudflare_get_domains(api_base, api_key=None):
    headers = cloudflare_build_headers(content_type=False)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    path = get_cloudflare_path("cloudflare_path_domains", "/domains")
    params = cloudflare_apply_auth_params()
    resp = http_get(f"{api_base}{path}", headers=headers, params=params)
    resp.raise_for_status()
    return _pick_list_payload(resp.json())


def cloudflare_create_account(api_base, address, password, api_key=None, expires_in=0):
    headers = cloudflare_build_headers(content_type=True)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    payload = {"address": address, "password": password, "expiresIn": expires_in}
    path = get_cloudflare_path("cloudflare_path_accounts", "/accounts")
    params = cloudflare_apply_auth_params()
    resp = http_post(f"{api_base}{path}", json=payload, headers=headers, params=params)
    resp.raise_for_status()
    return resp.json()


def cloudflare_get_token(api_base, address, password, api_key=None):
    headers = cloudflare_build_headers(content_type=True)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    path = get_cloudflare_path("cloudflare_path_token", "/token")
    resp = http_post(
        f"{api_base}{path}",
        json={"address": address, "password": password},
        headers=headers,
        params=cloudflare_apply_auth_params(),
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        if data.get("token"):
            return data.get("token")
        if isinstance(data.get("data"), dict) and data["data"].get("token"):
            return data["data"].get("token")
    return None


def cloudflare_get_messages(api_base, token):
    headers = {"Authorization": f"Bearer {token}"}
    path = get_cloudflare_path("cloudflare_path_messages", "/messages")
    params = {"limit": 20, "offset": 0}
    params = cloudflare_apply_auth_params(params)
    resp = http_get(f"{api_base}{path}", headers=headers, params=params)
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"Cloudflare messages 返回非JSON: {resp.text[:300]}")
    return _pick_list_payload(data)


def cloudflare_get_message_detail(api_base, token, message_id):
    headers = {"Authorization": f"Bearer {token}"}
    candidates = [
        f"{api_base}/api/mail/{message_id}",
        f"{api_base}{get_cloudflare_path('cloudflare_path_messages', '/messages')}/{message_id}",
    ]
    last_err = None
    for url in candidates:
        try:
            resp = http_get(
                url,
                headers=headers,
                params=cloudflare_apply_auth_params(),
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and isinstance(data.get("data"), dict):
                return data["data"]
            return data
        except Exception as exc:
            last_err = exc
            continue
    raise Exception(f"Cloudflare 获取邮件详情失败: {last_err}")


YYDS_API_BASE = "https://maliapi.215.im/v1"


def get_yyds_api_key():
    return config.get("yyds_api_key", "")


def get_yyds_jwt():
    return config.get("yyds_jwt", "")


def yyds_get_domains(api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(f"{YYDS_API_BASE}/domains", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", []) if data.get("success") else []


def yyds_create_account(address=None, domain=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    payload = {}
    if address:
        payload["address"] = address
    if domain:
        payload["domain"] = domain
    elif key or token:
        payload["autoDomainStrategy"] = "prefer_owned"
    resp = http_post(f"{YYDS_API_BASE}/accounts", json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {})
    raise Exception(f"YYDS 鍒涘缓閭澶辫触: {data}")


def yyds_get_token(address, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_post(
        f"{YYDS_API_BASE}/token", json={"address": address}, headers=headers
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {}).get("token")
    raise Exception(f"YYDS 鑾峰彇token澶辫触: {data}")


def yyds_get_messages(address, token=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    temp_token = token or jwt or get_yyds_jwt()
    headers = {}
    if temp_token:
        headers["Authorization"] = f"Bearer {temp_token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(
        f"{YYDS_API_BASE}/messages",
        params={"address": address},
        headers=headers,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {}).get("messages", [])
    return []


def yyds_get_message_detail(message_id, token=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    temp_token = token or jwt or get_yyds_jwt()
    headers = {}
    if temp_token:
        headers["Authorization"] = f"Bearer {temp_token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(f"{YYDS_API_BASE}/messages/{message_id}", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {})
    raise Exception(f"YYDS 鑾峰彇閭欢璇︽儏澶辫触: {data}")


def yyds_generate_username(length=10):
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def yyds_pick_domain(api_key=None, jwt=None):
    domains = yyds_get_domains(api_key=api_key, jwt=jwt)
    if not domains:
        raise Exception("YYDS 娌℃湁杩斿洖浠讳綍鍙敤鍩熷悕")
    private = [d for d in domains if d.get("isVerified") and not d.get("isPublic")]
    if private:
        return private[0]["domain"]
    public = [d for d in domains if d.get("isVerified") and d.get("isPublic")]
    if public:
        return public[0]["domain"]
    verified = [d for d in domains if d.get("isVerified")]
    if verified:
        return verified[0]["domain"]
    raise Exception("YYDS 鏃犲凡楠岃瘉鍩熷悕鍙敤")


def yyds_get_email_and_token(api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    if not token and not key:
        raise Exception("YYDS API Key 或 JWT 未配置")
    domain = yyds_pick_domain(api_key=key, jwt=token)
    username = yyds_generate_username(10)
    result = yyds_create_account(
        address=username, domain=domain, api_key=key, jwt=token
    )
    address = result.get("address") or f"{username}@{domain}"
    temp_token = result.get("token")
    if not temp_token:
        temp_token = yyds_get_token(address, api_key=key, jwt=token)
    if not temp_token:
        raise Exception("鑾峰彇 YYDS token 澶辫触")
    print(f"[*] 宸插垱寤?YYDS 閭: {address}")
    return address, temp_token


def yyds_get_oai_code(
    token,
    address,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    jwt=None,
    cancel_callback=None,
):
    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            messages = yyds_get_messages(address, token=token, jwt=jwt)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] YYDS 鎷夊彇閭欢鍒楄〃澶辫触: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        for msg in messages:
            msg_id = msg.get("id")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            to_addrs = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if address.lower() not in to_addrs:
                continue
            try:
                detail = yyds_get_message_detail(msg_id, token=token, jwt=jwt)
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] YYDS 鑾峰彇閭欢璇︽儏澶辫触: {exc}")
                continue
            parts = []
            text_body = detail.get("text") or ""
            if text_body:
                parts.append(text_body)
            html_list = detail.get("html") or []
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            combined = "\n".join(parts)
            subject = detail.get("subject", "")
            if log_callback:
                log_callback(f"[Debug] YYDS 鏀跺埌閭欢: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] YYDS 浠庨偖浠朵腑鎻愬彇鍒伴獙璇佺爜: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"YYDS 在 {timeout}s 内未收到验证码邮件")


def generate_username(length=10):
    first_names = ("Alex", "Andrew", "Anthony", "Ashley", "Brandon", "Brian", "Daniel", "David", "Emily", "Gary", "James", "Jennifer", "Jessica", "John", "Joseph", "Kevin", "Laura", "Leslie", "Lindsay", "Michael", "Robert", "Sarah", "Thomas", "William", "Adam", "Amy", "April", "Cassandra", "Erica", "Jamie", "Jerry", "Julian", "Kimberly", "Melinda", "Nicholas", "Ronald", "Terri", "Taylor", "Morgan", "Jordan", "Casey", "Riley")
    last_names = ("Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis", "Wilson", "Moore", "Taylor", "Anderson", "Thomas", "Jackson", "White", "Harris", "Martin", "Thompson", "Garcia", "Robinson", "Clark", "Lewis", "Lee", "Walker", "Hall", "Allen", "Young", "King", "Wright", "Scott", "Green", "Baker", "Adams", "Nelson", "Hill", "Carter", "Roberts", "Collins", "Stewart", "Morris", "Rogers", "Reed", "Cook", "Bell", "Murphy", "Bailey", "Rivera", "Cooper", "Richardson", "Cox", "Howard", "Ward", "Peterson", "Gray", "Watson", "Brooks", "Kelly", "Sanders", "Price", "Bennett", "Wood", "Barnes", "Ross", "Henderson", "Jenkins", "Perry", "Powell", "Long", "Patterson", "Hughes", "Flores", "Butler", "Simmons", "Foster", "Bryant", "Alexander", "Russell", "Griffin", "Hayes", "Myers", "Ford", "Hamilton", "Graham", "Sullivan", "Wallace", "Cole", "West", "Owens", "Reynolds", "Fisher", "Ellis", "Harrison", "Gibson", "Crosby")
    return f"{secrets.choice(first_names)}{secrets.choice(last_names)}{secrets.randbelow(10000):04d}"


def pick_domain(api_key=None):
    domains = get_domains(api_key=api_key)
    if not domains:
        raise Exception("DuckMail 娌℃湁杩斿洖浠讳綍鍙敤鍩熷悕")
    private = [d for d in domains if d.get("ownerId")]
    verified_private = [d for d in private if d.get("isVerified")]
    if verified_private:
        return verified_private[0]["domain"]
    public = [d for d in domains if d.get("isVerified")]
    if public:
        return public[0]["domain"]
    raise Exception("DuckMail 鏃犲凡楠岃瘉鍩熷悕鍙敤")


def get_email_provider():
    return config.get("email_provider", "duckmail")


def get_email_and_token(api_key=None):
    provider = get_email_provider()
    if provider == "yyds":
        return yyds_get_email_and_token(api_key=api_key, jwt=get_yyds_jwt())
    if provider == "cloudflare":
        api_base = get_cloudflare_api_base()
        if not api_base:
            raise Exception("Cloudflare API Base 未配置")
        try:
            # cloudflare_temp_email 专用模式
            return cloudflare_create_temp_address(api_base)
        except Exception as primary_exc:
            # 兜底回退到 Mail.tm 风格
            key = api_key or get_cloudflare_api_key()
            domains = cloudflare_get_domains(api_base, api_key=key)
            if not domains:
                raise Exception(f"Cloudflare 创建邮箱失败: {primary_exc}")
            verified = [d for d in domains if d.get("isVerified")]
            target = verified[0] if verified else domains[0]
            domain = target.get("domain")
            if not domain:
                raise Exception("Cloudflare 域名数据格式错误，缺少 domain 字段")
            username = generate_username(10)
            address = f"{username}@{domain}"
            password = secrets.token_urlsafe(12)
            cloudflare_create_account(
                api_base, address, password, api_key=key, expires_in=0
            )
            token = cloudflare_get_token(api_base, address, password, api_key=key)
            if not token:
                raise Exception("获取 Cloudflare 邮箱 token 失败")
            return address, token
    key = api_key or get_duckmail_api_key()
    domain = pick_domain(api_key=key)
    username = generate_username(10)
    address = f"{username}@{domain}"
    password = secrets.token_urlsafe(12)
    create_account(address, password, api_key=key, expires_in=0)
    token = get_token(address, password)
    if not token:
        raise Exception("鑾峰彇 DuckMail token 澶辫触")
    return address, token


def get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
    resend_callback=None,
):
    provider = get_email_provider()
    if provider == "yyds":
        return yyds_get_oai_code(
            dev_token,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            log_callback=log_callback,
            jwt=get_yyds_jwt(),
            cancel_callback=cancel_callback,
        )
    if provider == "cloudflare":
        return cloudflare_get_oai_code(
            dev_token,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            log_callback=log_callback,
            cancel_callback=cancel_callback,
            resend_callback=resend_callback,
        )
    return duckmail_get_oai_code(
        dev_token,
        email,
        timeout=timeout,
        poll_interval=poll_interval,
        log_callback=log_callback,
        cancel_callback=cancel_callback,
    )


def extract_verification_code(text, subject=""):
    if subject:
        match = re.search(r"^([A-Z0-9]{3}-[A-Z0-9]{3})\s+xAI", subject, re.IGNORECASE)
        if match:
            return match.group(1)
    match = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", text, re.IGNORECASE)
    if match:
        return match.group(1)
    patterns = [
        r"verification\s+code[:\s]+(\d{4,8})",
        r"your\s+code[:\s]+(\d{4,8})",
        r"confirm(?:ation)?\s+code[:\s]+(\d{4,8})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def duckmail_get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
):
    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            messages = get_messages(dev_token)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 鎷夊彇閭欢鍒楄〃澶辫触: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        for msg in messages:
            msg_id = msg.get("id") or msg.get("msgid")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            recipients = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if email.lower() not in recipients:
                continue
            try:
                detail = get_message_detail(dev_token, msg_id)
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 鑾峰彇閭欢璇︽儏澶辫触: {exc}")
                continue
            parts = []
            text_body = detail.get("text") or ""
            if text_body:
                parts.append(text_body)
            html_list = detail.get("html") or []
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            combined = "\n".join(parts)
            subject = detail.get("subject", "")
            if log_callback:
                log_callback(f"[Debug] 鏀跺埌閭欢: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] 浠庨偖浠朵腑鎻愬彇鍒伴獙璇佺爜: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"在 {timeout}s 内未收到验证码邮件")


def cloudflare_get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
    resend_callback=None,
):
    api_base = get_cloudflare_api_base()
    if not api_base:
        raise Exception("Cloudflare API Base 未配置")
    deadline = time.time() + timeout
    # 同一封邮件正文可能延迟可读，允许多次重试解析，避免偶发漏码
    seen_attempts = {}
    next_resend_at = time.time() + 35
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if resend_callback and time.time() >= next_resend_at:
            try:
                resend_callback()
                if log_callback:
                    log_callback("[*] 已触发重新发送验证码")
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 触发重发验证码失败: {exc}")
            next_resend_at = time.time() + 35
        try:
            messages = cloudflare_get_messages(api_base, dev_token)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] Cloudflare 拉取邮件列表失败: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        if log_callback:
            log_callback(f"[Debug] Cloudflare 本轮邮件数量: {len(messages)}")

        for msg in messages:
            msg_id = msg.get("id") or msg.get("msgid")
            if not msg_id:
                continue
            attempt = int(seen_attempts.get(msg_id, 0))
            if attempt >= 5:
                continue
            seen_attempts[msg_id] = attempt + 1
            recipients = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            msg_addr = str(msg.get("address", "")).lower()
            # 优先匹配目标邮箱；若结构不一致也允许继续解析，避免接口字段漂移导致漏码
            address_matched = True
            if recipients:
                address_matched = email.lower() in recipients
            elif msg_addr:
                address_matched = msg_addr == email.lower()
            if not address_matched and log_callback:
                log_callback(f"[Debug] 跳过疑似非目标邮件 id={msg_id} address={msg_addr} to={recipients}")
                continue
            parts = []
            # 先直接从列表项取内容，避免 detail 接口差异导致漏码
            for field in ("text", "raw", "content", "intro", "body", "snippet"):
                value = msg.get(field)
                if isinstance(value, str) and value.strip():
                    parts.append(value)
            html_list = msg.get("html") or []
            if isinstance(html_list, str):
                html_list = [html_list]
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            subject = str(msg.get("subject", "") or "")
            combined = "\n".join(parts)
            # 再尝试 detail 接口补全内容
            try:
                detail = cloudflare_get_message_detail(api_base, dev_token, msg_id)
                for field in ("text", "raw", "content", "intro", "body", "snippet"):
                    value = detail.get(field)
                    if isinstance(value, str) and value.strip():
                        combined += "\n" + value
                html_list2 = detail.get("html") or []
                if isinstance(html_list2, str):
                    html_list2 = [html_list2]
                for h in html_list2:
                    combined += "\n" + re.sub(r"<[^>]+>", " ", h)
                if not subject:
                    subject = str(detail.get("subject", "") or "")
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] Cloudflare detail接口失败，改用列表内容解析: {exc}")
            if log_callback:
                log_callback(f"[Debug] Cloudflare 收到邮件: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] Cloudflare 验证码叼回来啦喵: {code}")
                return code
            elif log_callback:
                log_callback(f"[Debug] 邮件已解析但未提取到验证码 id={msg_id} attempt={seen_attempts[msg_id]}")
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"Cloudflare 在 {timeout}s 内未收到验证码邮件")


def generate_random_birthdate():
    import datetime as dt

    today = dt.date.today()
    age = random.randint(20, 40)
    birth_year = today.year - age
    birth_month = random.randint(1, 12)
    birth_day = random.randint(1, 28)
    return f"{birth_year}-{birth_month:02d}-{birth_day:02d}T16:00:00.000Z"


def set_birth_date(session, log_callback=None):
    url = "https://grok.com/rest/auth/set-birth-date"
    new_headers = {
        "content-type": "application/json",
        "origin": "https://grok.com",
        "referer": "https://grok.com/",
    }
    payload = {"birthDate": generate_random_birthdate()}
    try:
        res = session.post(url, json=payload, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(
                f"[Debug] set_birth_date status: {res.status_code}, body: {res.text[:200]}"
            )
        return res.status_code == 200
    except Exception as e:
        if log_callback:
            log_callback(f"[set_birth_date] 寮傚父: {e}")
        return False


def set_tos_accepted(session, log_callback=None):
    url = "https://accounts.x.ai/auth_mgmt.AuthManagement/SetTosAcceptedVersion"
    payload = struct.pack("B", (2 << 3) | 0) + struct.pack("B", 1)
    data = b"\x00" + struct.pack(">I", len(payload)) + payload
    new_headers = {
        "content-type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "x-user-agent": "connect-es/2.1.1",
        "origin": "https://accounts.x.ai",
        "referer": "https://accounts.x.ai/accept-tos",
    }
    try:
        res = session.post(url, data=data, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(f"[Debug] set_tos_accepted status: {res.status_code}")
        return res.status_code == 200
    except Exception as e:
        if log_callback:
            log_callback(f"[set_tos_accepted] 寮傚父: {e}")
        return False


def encode_grpc_nsfw_settings():
    field1_content = bytes([0x10, 0x01])
    field1 = bytes([0x0A, len(field1_content)]) + field1_content
    nsfw_string = b"always_show_nsfw_content"
    field2_inner = bytes([0x0A, len(nsfw_string)]) + nsfw_string
    field2 = bytes([0x12, len(field2_inner)]) + field2_inner
    payload = field1 + field2
    return b"\x00" + struct.pack(">I", len(payload)) + payload


def update_nsfw_settings(session, log_callback=None):
    url = "https://grok.com/auth_mgmt.AuthManagement/UpdateUserFeatureControls"
    data = encode_grpc_nsfw_settings()
    new_headers = {
        "content-type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "origin": "https://grok.com",
        "referer": "https://grok.com/",
    }
    try:
        res = session.post(url, data=data, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(f"[Debug] update_nsfw status: {res.status_code}")
        return res.status_code == 200
    except Exception as e:
        if log_callback:
            log_callback(f"[update_nsfw] 寮傚父: {e}")
        return False


def enable_nsfw_for_token(token, cf_clearance="", log_callback=None):
    proxies = get_proxies()
    user_agent = get_user_agent()
    try:
        with requests.Session(impersonate="chrome120", proxies=proxies) as session:
            session.headers.update(
                {
                    "user-agent": user_agent,
                    "cookie": f"sso={token}; sso-rw={token}; cf_clearance={cf_clearance}",
                }
            )
            if not set_tos_accepted(session, log_callback):
                return False, "set_tos_accepted 澶辫触!"
            if not set_birth_date(session, log_callback):
                return False, "set_birth_date 澶辫触!"
            if not update_nsfw_settings(session, log_callback):
                return False, "update_nsfw_settings 澶辫触!"
            return True, "鎴愬姛寮€鍚疦SFW"
    except Exception as e:
        return False, f"寮傚父: {str(e)}"


SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"

_thread_ctx = threading.local()
_browser_launch_semaphore = threading.Semaphore(2)


def _get_browser():
    return getattr(_thread_ctx, "browser", None)


def _set_browser(value):
    _thread_ctx.browser = value


def _get_page():
    return getattr(_thread_ctx, "page", None)


def _set_page(value):
    _thread_ctx.page = value


def start_browser(log_callback=None):
    last_exc = None
    for attempt in range(1, 5):
        try:
            # 高并发下限制同时启动浏览器数量，降低 auto_port/user_data 竞争
            with _browser_launch_semaphore:
                browser = Chromium(create_browser_options())
                tabs = browser.get_tabs()
                page = tabs[-1] if tabs else browser.new_tab()
            _set_browser(browser)
            _set_page(page)
            if log_callback and getattr(browser, "user_data_path", None):
                log_callback(f"[Debug] 当前浏览器资料目录: {browser.user_data_path}")
            if log_callback and attempt > 1:
                log_callback(f"[*] 浏览器第 {attempt} 次启动成功")
            return browser, page
        except Exception as exc:
            last_exc = exc
            if log_callback:
                log_callback(f"[Debug] 浏览器启动失败(第{attempt}/4次): {exc}")
            try:
                current = _get_browser()
                if current is not None:
                    current.quit(del_data=True)
            except Exception:
                pass
            _set_browser(None)
            _set_page(None)
            time.sleep(min(1.5 * attempt, 4))
    raise Exception(f"浏览器启动失败，已重试4次: {last_exc}")


def stop_browser():
    browser = _get_browser()
    if browser is not None:
        try:
            browser.quit(del_data=True)
        except Exception:
            pass
    _set_browser(None)
    _set_page(None)


def restart_browser(log_callback=None):
    stop_browser()
    return start_browser(log_callback=log_callback)


def refresh_active_page():
    browser = _get_browser()
    if browser is None:
        browser, _ = restart_browser()
    try:
        tabs = browser.get_tabs()
        if tabs:
            page = tabs[-1]
        else:
            page = browser.new_tab()
        _set_page(page)
    except Exception:
        _, page = restart_browser()
    return _get_page()



def dismiss_cookie_banner(log_callback=None):
    """Accept/close OneTrust-style cookie banners that block signup buttons."""
    page = refresh_active_page()
    try:
        clicked = page.run_js(r"""
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
  return [node.innerText, node.textContent, node.getAttribute('aria-label'), node.id, node.className].filter(Boolean).join(' ').replace(/\s+/g,' ').trim();
}
const selectors = [
  '#onetrust-accept-btn-handler',
  '#accept-recommended-btn-handler',
  'button#onetrust-accept-btn-handler',
  'button[aria-label*="Accept" i]',
];
for (const sel of selectors) {
  const el = document.querySelector(sel);
  if (el && isVisible(el)) { el.click(); return 'sel:' + sel; }
}
const btns = Array.from(document.querySelectorAll('button, a, [role="button"]')).filter(isVisible);
const prefer = btns.find((n) => {
  const t = textOf(n).toLowerCase();
  return t.includes('accept all cookies') || t.includes('accept all') || t.includes('允许全部') || t.includes('接受全部');
});
if (prefer) { prefer.click(); return 'text:' + textOf(prefer).slice(0,80); }
const reject = btns.find((n) => {
  const t = textOf(n).toLowerCase();
  return t.includes('reject all') || t.includes('拒绝全部');
});
if (reject) { reject.click(); return 'reject:' + textOf(reject).slice(0,80); }
const close = btns.find((n) => {
  const t = textOf(n).toLowerCase();
  return t === 'close' || t.includes('关闭');
});
if (close) { close.click(); return 'close'; }
return false;
        """)
        if clicked and log_callback:
            log_callback(f"[Debug] 已处理 Cookie 弹窗: {clicked}")
        return bool(clicked)
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] Cookie 弹窗处理失败: {exc}")
        return False


def click_email_signup_button(timeout=10, log_callback=None, cancel_callback=None):
    page = _get_page()
    deadline = time.time() + timeout
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if log_callback:
            log_callback("[Debug] 尝试查找“使用邮箱注册”按钮...")

        clicked = page.run_js(r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function nodeText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('href'),
        node.getAttribute('data-testid'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function scoreEntry(node) {
    const compact = nodeText(node).replace(/\s+/g, '');
    const lower = compact.toLowerCase();
    if (compact.includes('使用邮箱注册')) return 100;
    if (lower.includes('signupwithemail')) return 95;
    if (lower.includes('continuewithemail')) return 90;
    if (lower.includes('sign') && lower.includes('email')) return 85;
    if (lower.includes('email') && (lower.includes('continue') || lower.includes('use') || lower.includes('with'))) return 80;
    if (lower.includes('邮箱') && (lower.includes('注册') || lower.includes('继续') || lower.includes('使用'))) return 78;
    if (lower === 'email' || lower === '邮箱') return 70;
    return 0;
}
const candidates = Array.from(document.querySelectorAll('button, a, [role="button"], div[role="button"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
    .map((node) => ({ node, score: scoreEntry(node), text: nodeText(node) }))
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score);
const target = candidates[0]?.node || null;
if (!target) {
    return {
      ok: false,
      all: Array.from(document.querySelectorAll('button, a, [role="button"]')).filter(isVisible).map(nodeText).slice(0, 12)
    };
}
target.scrollIntoView({block:'center', inline:'center'});
target.click();
return {ok:true, text: candidates[0].text, score: candidates[0].score};
        """)

        ok = False
        detail = ""
        if isinstance(clicked, dict):
            ok = bool(clicked.get("ok"))
            detail = str(clicked.get("text") or "")
            if (not ok) and log_callback:
                log_callback(f"[Debug] 邮箱按钮候选不足: buttons={clicked.get('all')}")
        elif clicked:
            ok = True
            detail = str(clicked)

        if ok:
            if log_callback:
                extra = f": {detail}" if detail else ""
                log_callback(f"[*] 已点击「使用邮箱注册」按钮{extra}")
            sleep_with_cancel(2.5, cancel_callback)
            return True

        if log_callback:
            current_url = page.url if page else "none"
            log_callback(f"[Debug] 当前URL: {current_url}")

        sleep_with_cancel(1, cancel_callback)
        page = refresh_active_page()

    if log_callback:
        try:
            page = refresh_active_page()
            page_html = (page.html or "")[:500]
            log_callback(f"[Debug] 页面内容片段: {page_html}")
        except Exception:
            pass

    raise Exception("未找到「使用邮箱注册」按钮")

def open_signup_page(log_callback=None, cancel_callback=None):
    browser = _get_browser()
    page = _get_page()
    raise_if_cancelled(cancel_callback)
    if browser is None:
        browser, page = start_browser()
        if log_callback:
            log_callback("[*] 浏览器已启动，猫猫出发喵~")
    # Pre-solve CF via FlareSolverr (same proxy as registration) then seed cookies.
    clearance = None
    try:
        clearance = fetch_flaresolverr_clearance("https://accounts.x.ai/sign-up", log_callback=log_callback)
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] clearance 预检异常: {exc}")
    try:
        page = browser.get_tab(0)
        _set_page(page)
        # Visit domain root first so cookies can attach, then signup.
        try:
            page.get("https://accounts.x.ai/")
            page.wait.doc_loaded()
            if clearance:
                apply_clearance_to_page(page, clearance, log_callback=log_callback)
            sleep_with_cancel(0.5, cancel_callback)
        except Exception as seed_exc:
            if log_callback:
                log_callback(f"[Debug] 预置 cookie 阶段异常: {seed_exc}")
        page.get(SIGNUP_URL)
    except Exception as e:
        if log_callback:
            log_callback(f"[Debug] 打开URL异常: {e}")
        try:
            page = browser.new_tab(SIGNUP_URL)
            _set_page(page)
        except Exception as e2:
            if log_callback:
                log_callback(f"[Debug] 创建新标签页异常: {e2}")
            browser, _ = restart_browser()
            page = browser.new_tab(SIGNUP_URL)
            _set_page(page)
    page.wait.doc_loaded()
    sleep_with_cancel(2, cancel_callback)
    if page_looks_like_cf_challenge(page):
        if log_callback:
            log_callback("[*] 注册页仍有 Cloudflare 挑战，尝试浏览器内 Turnstile...")
        try:
            getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
            sleep_with_cancel(2, cancel_callback)
            page = refresh_active_page()
            page.get(SIGNUP_URL)
            page.wait.doc_loaded()
            sleep_with_cancel(2, cancel_callback)
        except Exception as cf_exc:
            if log_callback:
                log_callback(f"[Debug] 打开阶段 Turnstile 失败: {cf_exc}")
    if log_callback:
        log_callback(f"[*] 当前URL: {page.url}")
    dismiss_cookie_banner(log_callback=log_callback)
    sleep_with_cancel(0.5, cancel_callback)
    click_email_signup_button(
        log_callback=log_callback, cancel_callback=cancel_callback
    )
    # Ensure email form is actually present before fill stage.
    if not wait_for_email_form(timeout=20, log_callback=log_callback, cancel_callback=cancel_callback):
        if log_callback:
            log_callback("[Debug] 邮箱表单未就绪，再点一次邮箱注册并等待")
        try:
            click_email_signup_button(timeout=8, log_callback=log_callback, cancel_callback=cancel_callback)
        except Exception:
            pass
        wait_for_email_form(timeout=15, log_callback=log_callback, cancel_callback=cancel_callback)


def has_profile_form(log_callback=None):
    page = refresh_active_page()
    try:
        return bool(
            page.run_js(
                """
const givenInput = document.querySelector('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]');
const familyInput = document.querySelector('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]');
const passwordInput = document.querySelector('input[data-testid="password"], input[name="password"], input[type="password"]');
return !!(givenInput && familyInput && passwordInput);
            """
            )
        )
    except Exception:
        return False



def detect_signup_stage(page=None):
    """Return: email | code | profile | unknown | cf based on visible DOM."""
    page = page or refresh_active_page()
    try:
        stage = page.run_js(r"""
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  const rect = node.getBoundingClientRect();
  // OTP widgets sometimes report tiny/zero height while still interactive.
  if (rect.width <= 0 && rect.height <= 0) return false;
  return true;
}
function hasSel(sel) {
  return Array.from(document.querySelectorAll(sel)).some(isVisible);
}
const body = ((document.body && document.body.innerText) || '').toLowerCase();
const hasEmail = hasSel('input[type="email"], input[name="email"], input[data-testid="email"], input[autocomplete="email"]');
const hasCode = hasSel('input[name="code"], input[data-input-otp="true"], input[autocomplete="one-time-code"], input[inputmode="numeric"]')
  || !!document.querySelector('input[name="code"]');
const hasProfile = hasSel('input[type="password"], input[name="password"], input[data-testid="password"]')
  && (hasSel('input[name="givenName"], input[data-testid="givenName"], input[autocomplete="given-name"]')
      || hasSel('input[name="familyName"], input[data-testid="familyName"], input[autocomplete="family-name"]')
      || body.includes('create your account') || body.includes('first name') || body.includes('last name'));
const hasConfirm = Array.from(document.querySelectorAll('button, [role="button"]')).some((n) => {
  if (!isVisible(n)) return false;
  const t = ((n.innerText || n.textContent || '') + ' ' + (n.getAttribute('aria-label') || '')).toLowerCase();
  return t.includes('confirm email') || t.includes('确认邮箱') || t.includes('verify');
});
const cf = /just a moment|checking your browser|attention required/i.test(document.title + ' ' + body);
if (cf) return 'cf';
if (hasProfile) return 'profile';
if (hasCode || hasConfirm) return 'code';
if (hasEmail) return 'email';
return 'unknown';
""")
        return str(stage or "unknown")
    except Exception:
        return "unknown"


def fill_email_and_submit(timeout=45, log_callback=None, cancel_callback=None):
    page = refresh_active_page()
    raise_if_cancelled(cancel_callback)

    # Ensure email form is visible before burning a mailbox.
    if not wait_for_email_form(timeout=min(25, timeout), log_callback=log_callback, cancel_callback=cancel_callback):
        if log_callback:
            log_callback("[Debug] 邮箱表单未出现，重试点击「使用邮箱注册」")
        try:
            click_email_signup_button(timeout=10, log_callback=log_callback, cancel_callback=cancel_callback)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 重试邮箱注册失败: {exc}")
        if not wait_for_email_form(timeout=15, log_callback=log_callback, cancel_callback=cancel_callback):
            try:
                page = refresh_active_page()
                snap = page.run_js(r"""
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
function nodeText(node) {
  return [node.innerText, node.textContent, node.getAttribute('aria-label'), node.getAttribute('placeholder'), node.getAttribute('data-testid'), node.getAttribute('name'), node.getAttribute('type')].filter(Boolean).join(' ').replace(/\s+/g,' ').trim().slice(0,120);
}
return {
  url: location.href,
  title: document.title,
  inputs: Array.from(document.querySelectorAll('input,textarea')).filter(isVisible).map(nodeText).slice(0,12),
  buttons: Array.from(document.querySelectorAll('button,a,[role="button"]')).filter(isVisible).map(nodeText).slice(0,12),
  hasTurnstile: !!(document.querySelector('input[name="cf-turnstile-response"], iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey]')),
  bodyText: (document.body && document.body.innerText || '').replace(/\s+/g,' ').trim().slice(0,300)
};
""")
                if log_callback:
                    log_callback(f"[Debug] 表单诊断: {snap}")
            except Exception as dump_exc:
                if log_callback:
                    log_callback(f"[Debug] 表单诊断失败: {dump_exc}")
            raise Exception("未找到邮箱输入框或注册按钮（邮箱表单未出现）")

    page = refresh_active_page()
    email, dev_token = get_email_and_token()
    if not email or not dev_token:
        raise Exception("获取邮箱失败")
    if log_callback:
        log_callback(f"[*] 新邮箱叼回来啦喵: {email}")

    # If submit already advanced before we start typing, do not treat code page as email failure.
    stage0 = detect_signup_stage(page)
    if stage0 == "code":
        if log_callback:
            log_callback("[*] 已进入邮箱验证码页，跳过邮箱填写")
        return email, dev_token
    if stage0 == "profile":
        if log_callback:
            log_callback("[*] 已进入资料页，跳过邮箱填写")
        return email, dev_token

    deadline = time.time() + timeout
    last_diag_time = 0.0
    last_reclick_time = 0.0
    last_snapshot = None
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        page = refresh_active_page()
        filled = page.run_js(
            r"""
const email = arguments[0];
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('placeholder'),
        node.getAttribute('data-testid'),
        node.getAttribute('name'),
        node.getAttribute('id'),
        node.getAttribute('autocomplete'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function describeInput(node) {
    return [
        `type=${node.getAttribute('type') || ''}`,
        `name=${node.getAttribute('name') || ''}`,
        `id=${node.getAttribute('id') || ''}`,
        `placeholder=${node.getAttribute('placeholder') || ''}`,
        `aria=${node.getAttribute('aria-label') || ''}`,
        `testid=${node.getAttribute('data-testid') || ''}`,
    ].join(' ').replace(/\s+/g, ' ').trim().slice(0, 160);
}
function emailCandidates() {
    const direct = Array.from(document.querySelectorAll(
      'input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i], input[placeholder*="邮箱"], input[aria-label*="邮箱"]'
    ));
    const all = Array.from(document.querySelectorAll('input, textarea'));
    for (const node of all) {
        const type = (node.getAttribute('type') || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'search', 'password'].includes(type)) continue;
        const meta = textOf(node).toLowerCase();
        if (meta.includes('email') || meta.includes('e-mail') || meta.includes('mail') || meta.includes('邮箱') || meta.includes('电子邮件')) {
            direct.push(node);
        }
    }
    return Array.from(new Set(direct));
}
const visibleInputs = Array.from(document.querySelectorAll('input, textarea'))
    .filter((node) => isVisible(node) && !node.disabled && !node.readOnly)
    .map(describeInput)
    .slice(0, 8);
const visibleActions = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
    .map((node) => textOf(node).slice(0, 120))
    .filter(Boolean)
    .slice(0, 10);
const input = emailCandidates().find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input) {
    return {
        state: 'not-ready',
        url: location.href,
        title: document.title,
        inputs: visibleInputs,
        buttons: visibleActions,
        hasTurnstile: !!(document.querySelector('input[name="cf-turnstile-response"], iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey]')),
    };
}
input.scrollIntoView({block:'center'});
input.focus(); input.click();
const valueProto = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
const valueSetter = Object.getOwnPropertyDescriptor(valueProto, 'value')?.set;
const tracker = input._valueTracker;
if (tracker) tracker.setValue('');
if (valueSetter) valueSetter.call(input, email); else input.value = email;
input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new InputEvent('input', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new Event('change', { bubbles: true }));
const inputType = (input.getAttribute('type') || '').toLowerCase();
const isValid = inputType !== 'email' || input.checkValidity();
if ((input.value || '').trim() !== email || !isValid) {
    return {
        state: 'fill-failed',
        value: input.value || '',
        valid: isValid,
        input: describeInput(input),
        url: location.href,
    };
}
input.blur();
return {
    state: 'filled',
    input: describeInput(input),
    url: location.href,
};
            """,
            email,
        )
        state = filled.get("state") if isinstance(filled, dict) else filled
        if isinstance(filled, dict):
            last_snapshot = filled

        if state == "not-ready":
            stage = detect_signup_stage(page)
            if stage == "code":
                if log_callback:
                    log_callback(f"[*] 页面已进入验证码步骤，邮箱阶段完成: {email}")
                return email, dev_token
            if stage == "profile":
                if log_callback:
                    log_callback(f"[*] 页面已进入资料步骤，邮箱阶段完成: {email}")
                return email, dev_token
            now = time.time()
            if now - last_reclick_time >= 3 and stage == "email":
                last_reclick_time = now
                try:
                    click_email_signup_button(timeout=6, log_callback=log_callback, cancel_callback=cancel_callback)
                except Exception:
                    pass
            if now - last_diag_time >= 5 and log_callback and isinstance(filled, dict):
                last_diag_time = now
                log_callback(
                    f"[Debug] 仍无邮箱框 url={filled.get('url')} buttons={filled.get('buttons')} inputs={filled.get('inputs')} turnstile={filled.get('hasTurnstile')}"
                )
            sleep_with_cancel(0.6, cancel_callback)
            continue

        if state == "fill-failed":
            if log_callback:
                log_callback(f"[Debug] 邮箱输入框已出现，但写入失败: {filled}")
            sleep_with_cancel(0.5, cancel_callback)
            continue

        if state != "filled":
            sleep_with_cancel(0.5, cancel_callback)
            continue

        sleep_with_cancel(0.6, cancel_callback)
        clicked = page.run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('data-testid'),
        node.getAttribute('type'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const input = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i], input[placeholder*="邮箱"]'))
    .find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input || !(input.value || '').trim()) return false;
const inputType = (input.getAttribute('type') || '').toLowerCase();
if (inputType === 'email' && !input.checkValidity()) return false;
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true');
const submitButton = buttons.find((node) => {
    const text = textOf(node).replace(/\s+/g, '');
    const lower = text.toLowerCase();
    return (
        text === '注册' ||
        text.includes('注册') ||
        text.includes('继续') ||
        text.includes('下一步') ||
        text.includes('确认') ||
        lower.includes('signup') ||
        lower.includes('sign up') ||
        lower.includes('continue') ||
        lower.includes('next') ||
        lower.includes('createaccount') ||
        lower.includes('submit')
    );
});
if (submitButton) {
    submitButton.scrollIntoView({block:'center'});
    submitButton.click();
    return textOf(submitButton) || true;
}
const form = input.closest('form');
if (form) {
    if (form.requestSubmit) form.requestSubmit();
    else form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    return 'form-submit';
}
input.focus();
input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
return 'enter';
            """
        )
        if clicked:
            sleep_with_cancel(1.2, cancel_callback)
            stage = detect_signup_stage(page)
            advanced = stage in ("code", "profile")
            if not advanced:
                try:
                    advanced = bool(page.run_js(r"""
const body = ((document.body && document.body.innerText) || '').toLowerCase();
const hasCode = !!document.querySelector('input[name="code"], input[data-input-otp="true"], input[autocomplete="one-time-code"]');
const hasConfirm = Array.from(document.querySelectorAll('button,[role="button"]')).some((n) => {
  const t = ((n.innerText||n.textContent||'') + ' ' + (n.getAttribute('aria-label')||'')).toLowerCase();
  return t.includes('confirm email') || t.includes('确认邮箱');
});
const emailStill = Array.from(document.querySelectorAll('input[type="email"], input[name="email"], input[data-testid="email"]')).some((n) => {
  if (!n || n.disabled) return false;
  const r = n.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
});
return hasCode || hasConfirm || !emailStill || body.includes('confirm email') || body.includes('verification');
"""))
                except Exception:
                    advanced = False
            if advanced:
                if log_callback:
                    detail = f" ({clicked})" if isinstance(clicked, str) else ""
                    log_callback(f"[*] 邮箱已填写并提交喵: {email}{detail}")
                return email, dev_token
            if log_callback and time.time() - last_diag_time >= 5:
                last_diag_time = time.time()
                log_callback(f"[Debug] 已点击注册但页面未前进，重试提交: {email}")
            try:
                if page.run_js('return !!(document.querySelector(\'input[name="cf-turnstile-response"], iframe[src*="turnstile"], div.cf-turnstile\'))'):
                    if log_callback:
                        log_callback("[Debug] 邮箱页检测到 Turnstile，尝试点击/等待...")
                    try:
                        getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
                    except Exception as te:
                        if log_callback:
                            log_callback(f"[Debug] Turnstile 未完成: {te}")
            except Exception:
                pass
        sleep_with_cancel(0.5, cancel_callback)

    try:
        page = refresh_active_page()
        if log_callback:
            log_callback(f"[Debug] 失败时 URL: {getattr(page, 'url', '')}")
            log_callback(f"[Debug] 失败时 title: {getattr(page, 'title', '')}")
            log_callback(f"[Debug] CF挑战判定: {page_looks_like_cf_challenge(page)}")
            if last_snapshot:
                log_callback(f"[Debug] 最后快照: {last_snapshot}")
            html = (page.html or "")[:350].replace("\n", " ")
            log_callback(f"[Debug] 失败时页面片段: {html}")
    except Exception:
        pass
    if last_snapshot:
        inputs = " | ".join((last_snapshot.get("inputs") or [])[:6])
        buttons = " | ".join((last_snapshot.get("buttons") or [])[:8])
        url = last_snapshot.get("url", "")
        raise Exception(
            f"未找到邮箱输入框或注册按钮，最后页面: url={url}; inputs={inputs or 'none'}; buttons={buttons or 'none'}"
        )
    raise Exception("未找到邮箱输入框或注册按钮")

def fill_code_and_submit(email, dev_token, timeout=180, log_callback=None, cancel_callback=None):
    page = refresh_active_page()

    def _resend_code():
        try:
            page = refresh_active_page()
            return page.run_js(r"""
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 || rect.height > 0;
}
const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
const target = nodes.find((node) => {
  if (!isVisible(node) || node.disabled) return false;
  const t = ((node.innerText || node.textContent || '') + ' ' + (node.getAttribute('aria-label') || '')).replace(/\s+/g, '').toLowerCase();
  return t.includes('重新发送') || t.includes('resend') || t.includes('再次发送');
});
if (target) { target.click(); return true; }
return false;
""")
        except Exception:
            return False

    # Wait briefly for code page if still transitioning.
    for _ in range(20):
        raise_if_cancelled(cancel_callback)
        stage = detect_signup_stage(page)
        if stage in ("code", "profile"):
            break
        sleep_with_cancel(0.5, cancel_callback)
        page = refresh_active_page()
    if detect_signup_stage(page) == "profile":
        if log_callback:
            log_callback("[*] 已在资料页，跳过验证码填写")
        return "already-on-profile"

    code = get_oai_code(
        dev_token,
        email,
        log_callback=log_callback,
        cancel_callback=cancel_callback,
        resend_callback=_resend_code,
    )
    if not code:
        raise Exception("获取验证码失败")
    # Keep alnum only; emails may show D49-GL4.
    clean_code = "".join(ch for ch in str(code) if ch.isalnum())
    if log_callback:
        log_callback(f"[*] 使用验证码，猫爪正在填写喵: {code} (clean={clean_code})")

    deadline = time.time() + timeout
    last_diag = 0.0
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        page = refresh_active_page()
        stage = detect_signup_stage(page)
        if stage == "profile":
            if log_callback:
                log_callback(f"[*] 验证码步骤已通过，进入资料页")
            return clean_code or code

        filled = page.run_js(
            r"""
const code = String(arguments[0] || '').trim();
if (!code) return {state:'empty-code'};
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  const rect = node.getBoundingClientRect();
  // OTP inputs on this site can be visually compact; accept tiny boxes.
  return true;
}
function setInputValue(input, value) {
  const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
  const tracker = input._valueTracker;
  if (tracker) tracker.setValue('');
  if (nativeSetter) nativeSetter.call(input, value);
  else input.value = value;
  input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
  input.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
}
// Prefer single aggregate OTP field (xAI uses name=code).
const aggregate = Array.from(document.querySelectorAll(
  'input[name="code"], input[data-input-otp="true"], input[autocomplete="one-time-code"], input[inputmode="numeric"], input[inputmode="text"]'
)).find((node) => !node.disabled && !node.readOnly && Number(node.maxLength || 20) !== 1) || null;

if (aggregate) {
  aggregate.focus();
  try { aggregate.click(); } catch(e) {}
  setInputValue(aggregate, code);
  // Some OTP widgets need keystrokes to enable Confirm.
  try {
    aggregate.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: code.slice(-1) || '0' }));
  } catch(e) {}
  const val = String(aggregate.value || '').replace(/\s+/g, '');
  return {state: val ? 'filled-aggregate' : 'aggregate-failed', value: val, name: aggregate.name || '', maxLength: aggregate.maxLength};
}

const otpBoxes = Array.from(document.querySelectorAll('input')).filter((node) => {
  if (node.disabled || node.readOnly) return false;
  const maxLength = Number(node.maxLength || 0);
  const ac = String(node.autocomplete || '').toLowerCase();
  const name = String(node.name || '').toLowerCase();
  return maxLength === 1 || ac === 'one-time-code' || name === 'code';
});
if (otpBoxes.length >= Math.min(code.length, 4)) {
  for (let i = 0; i < code.length && i < otpBoxes.length; i += 1) {
    const ch = code[i] || '';
    const box = otpBoxes[i];
    box.focus();
    try { box.click(); } catch(e) {}
    setInputValue(box, ch);
  }
  const merged = otpBoxes.slice(0, code.length).map((x) => String(x.value || '').trim()).join('');
  return {state: merged.length ? 'filled-boxes' : 'boxes-failed', value: merged};
}
return {
  state: 'not-ready',
  inputs: Array.from(document.querySelectorAll('input')).map((n)=>[n.type,n.name,n.autocomplete,n.maxLength,n.disabled].join('|')).slice(0,12),
  body: ((document.body&&document.body.innerText)||'').replace(/\s+/g,' ').trim().slice(0,180)
};
            """,
            clean_code,
        )

        state = filled.get("state") if isinstance(filled, dict) else filled
        if state in (None, "not-ready", "empty-code", "aggregate-failed", "boxes-failed"):
            now = time.time()
            if log_callback and now - last_diag >= 4:
                last_diag = now
                log_callback(f"[Debug] 验证码框未就绪/填写失败: {filled} stage={stage}")
            sleep_with_cancel(0.5, cancel_callback)
            continue

        sleep_with_cancel(0.4, cancel_callback)
        clicked = page.run_js(
            r"""
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 || rect.height > 0 || node.tagName === 'BUTTON';
}
function textOf(node) {
  return [node.innerText, node.textContent, node.getAttribute('aria-label'), node.getAttribute('type'), node.getAttribute('data-testid')]
    .filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]'))
  .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true');
const scored = buttons.map((node) => {
  const raw = textOf(node).toLowerCase();
  const compact = raw.replace(/\s+/g, '');
  let score = 0;
  if (compact.includes('confirmemail') || raw.includes('confirm email') || compact.includes('确认邮箱')) score += 100;
  if (compact.includes('verify') || compact.includes('验证')) score += 80;
  if (compact.includes('continue') || compact.includes('next') || compact.includes('继续') || compact.includes('下一步')) score += 50;
  if ((node.getAttribute('type') || '').toLowerCase() === 'submit') score += 20;
  return {node, score, text: textOf(node)};
}).filter((x) => x.score > 0).sort((a,b)=>b.score-a.score);
const btn = scored[0]?.node || null;
if (!btn) {
  // fallback: Enter on code input
  const input = document.querySelector('input[name="code"], input[data-input-otp="true"], input[autocomplete="one-time-code"]');
  if (input) {
    input.focus();
    input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter', bubbles:true, cancelable:true}));
    input.dispatchEvent(new KeyboardEvent('keyup', {key:'Enter', code:'Enter', bubbles:true, cancelable:true}));
    const form = input.closest('form');
    if (form) {
      if (form.requestSubmit) form.requestSubmit();
      else form.dispatchEvent(new Event('submit', {bubbles:true, cancelable:true}));
    }
    return {state:'enter', buttons: buttons.map(textOf).slice(0,10)};
  }
  return {state:'no-button', buttons: buttons.map(textOf).slice(0,10)};
}
btn.scrollIntoView({block:'center'});
btn.focus();
btn.click();
return {state:'clicked', text: scored[0].text};
            """
        )
        cstate = clicked.get("state") if isinstance(clicked, dict) else clicked
        if cstate in ("clicked", "enter", "no-button"):
            if log_callback:
                detail = ""
                if isinstance(clicked, dict) and clicked.get("text"):
                    detail = f" ({clicked.get('text')})"
                log_callback(f"[*] 验证码已填写并提交喵: {code}{detail}")
            # Wait for navigation to profile (or leave code page).
            for _ in range(25):
                raise_if_cancelled(cancel_callback)
                sleep_with_cancel(0.4, cancel_callback)
                page = refresh_active_page()
                st = detect_signup_stage(page)
                if st == "profile":
                    if log_callback:
                        log_callback("[*] 验证码确认成功，顺利溜到资料页喵~")
                    return clean_code or code
                if st not in ("code",):
                    # unknown/email may mean redirect in progress
                    pass
            # If still on code page, loop and retry fill/click.
            if log_callback and time.time() - last_diag >= 3:
                last_diag = time.time()
                log_callback(f"[Debug] 验证码已点提交但未离开验证码页, stage={detect_signup_stage(page)}, click={clicked}")
            continue

        sleep_with_cancel(0.5, cancel_callback)

    raise Exception("验证码已获取，但自动填写/提交失败")


def getTurnstileToken(log_callback=None, cancel_callback=None):
    page = _get_page()
    if page is None:
        raise Exception("页面未就绪，无法执行 Turnstile")

    try:
        page.run_js(
            "try { if (window.turnstile && typeof turnstile.reset === 'function') turnstile.reset(); } catch(e) {}"
        )
    except Exception:
        pass

    for _ in range(0, 20):
        raise_if_cancelled(cancel_callback)
        try:
            token = page.run_js(
                """
try {
  const byInput = String((document.querySelector('input[name="cf-turnstile-response"]') || {}).value || '').trim();
  if (byInput) return byInput;
  if (window.turnstile && typeof turnstile.getResponse === 'function') {
    return String(turnstile.getResponse() || '').trim();
  }
  return '';
} catch(e) { return ''; }
                """
            )
            token = str(token or "").strip()
            if len(token) >= 80:
                if log_callback:
                    log_callback(f"[*] Turnstile 已通过，猫猫顺利过关喵~ token长度={len(token)}")
                return token

            challenge_input = page.ele("@name=cf-turnstile-response")
            if challenge_input:
                wrapper = challenge_input.parent()
                iframe = None
                try:
                    iframe = wrapper.shadow_root.ele("tag:iframe")
                except Exception:
                    iframe = None
                if iframe:
                    try:
                        iframe.run_js(
                            """
window.dtp = 1;
function getRandomInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
let sx = getRandomInt(800, 1200);
let sy = getRandomInt(400, 700);
Object.defineProperty(MouseEvent.prototype, 'screenX', { value: sx });
Object.defineProperty(MouseEvent.prototype, 'screenY', { value: sy });
                            """
                        )
                    except Exception:
                        pass
                    try:
                        body_sr = iframe.ele("tag:body").shadow_root
                        btn = body_sr.ele("tag:input")
                        if btn:
                            btn.click()
                    except Exception:
                        pass
            else:
                # 兜底：尝试触发页面上可见的 Turnstile 容器
                page.run_js(
                    """
const nodes = Array.from(document.querySelectorAll('div,span,iframe')).filter((n) => {
  const txt = (n.className || '') + ' ' + (n.id || '') + ' ' + (n.getAttribute?.('src') || '');
  return String(txt).toLowerCase().includes('turnstile');
});
if (nodes.length && typeof nodes[0].click === 'function') nodes[0].click();
                    """
                )
        except Exception:
            pass
        sleep_with_cancel(1, cancel_callback)

    raise Exception("Turnstile 获取 token 失败")


def build_profile():
    given_name_pool = [
        "Neo", "Ethan", "Liam", "Noah", "Lucas", "Mason", "Ryan", "Leo",
        "Owen", "Aiden", "Elio", "Aron", "Ivan", "Nolan", "Evan", "Kai",
        "Caleb", "Adam", "Ezra", "Miles", "Logan", "Carter", "Hunter", "Jason",
        "Brian", "Dylan", "Alex", "Colin", "Blake", "Gavin", "Henry", "Julian",
        "Kevin", "Louis", "Marcus", "Nathan", "Oscar", "Peter", "Quinn", "Robin",
        "Simon", "Tristan", "Victor", "Wesley", "Xavier", "Yuri", "Zane", "Felix",
        "Aaron", "Damian",
    ]
    family_name_pool = [
        "Lin", "Wang", "Zhao", "Liu", "Chen", "Zhang", "Xu", "Sun",
        "Guo", "He", "Yang", "Wu", "Zhou", "Tang", "Qin", "Shi",
        "Fang", "Peng", "Cao", "Deng", "Fan", "Fu", "Gao", "Han",
        "Hu", "Jiang", "Kong", "Lu", "Ma", "Nie", "Pan", "Qiao",
        "Ren", "Shao", "Tian", "Xie", "Yan", "Yao", "Yu", "Zeng",
        "Bai", "Duan", "Hou", "Jin", "Kang", "Luo", "Mao", "Song",
        "Wei", "Xiong",
    ]
    given_name = random.choice(given_name_pool)
    family_name = random.choice(family_name_pool)
    password = "N" + secrets.token_hex(4) + "!a7#" + secrets.token_urlsafe(6)
    return given_name, family_name, password


def fill_profile_and_submit(timeout=120, log_callback=None, cancel_callback=None):
    page = refresh_active_page()
    given_name, family_name, password = build_profile()
    deadline = time.time() + timeout
    form_filled_once = False
    wait_cf_since = None
    last_cf_retry_at = 0.0
    last_diag_at = 0.0

    submit_match_js = r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function btnText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('value'),
        node.getAttribute('data-testid'),
        node.getAttribute('type'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function cfStatus() {
    const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
    const cfPresent = !!cfInput
      || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
    const token = String((cfInput && cfInput.value) || '').trim();
    return { present: cfPresent, tokenLen: token.length, solved: !cfPresent || token.length >= 80 };
}
function findSubmit() {
    const buttons = Array.from(document.querySelectorAll('button, [role="button"], input[type="submit"], button[type="submit"]'))
      .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true');
    const scored = buttons.map((node) => {
      const raw = btnText(node).toLowerCase();
      const compact = raw.replace(/\s+/g, '');
      let score = 0;
      if (compact.includes('完成注册') || compact.includes('createaccount') || compact.includes('createyouraccount')) score += 100;
      if (compact.includes('signup') || raw.includes('sign up')) score += 90;
      if (compact.includes('创建账户') || compact.includes('创建账号') || compact === '注册' || compact.includes('注册')) score += 80;
      if (compact.includes('continue') || compact.includes('next') || compact.includes('submit')) score += 40;
      if ((node.getAttribute('type') || '').toLowerCase() === 'submit') score += 20;
      return {node, score, text: btnText(node)};
    }).filter((x) => x.score > 0).sort((a,b)=>b.score-a.score);
    return scored[0] || null;
}
"""

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        page = refresh_active_page()
        if not form_filled_once:
            filled = page.run_js(
                submit_match_js
                + r"""
const givenName = arguments[0];
const familyName = arguments[1];
const password = arguments[2];

function pickInput(selector) {
    return Array.from(document.querySelectorAll(selector)).find((node) => {
        return isVisible(node) && !node.disabled && !node.readOnly;
    }) || null;
}

function setInputValue(input, value) {
    if (!input) return false;
    input.focus();
    input.click();
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) nativeSetter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.blur();
    return String(input.value || '').trim() === String(value || '').trim();
}

const givenInput = pickInput('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"], input[placeholder*="irst" i], input[aria-label*="名"], input[aria-label*="First" i]');
const familyInput = pickInput('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"], input[placeholder*="ast" i], input[aria-label*="姓"], input[aria-label*="Last" i]');
const passwordInput = pickInput('input[data-testid="password"], input[name="password"], input[type="password"], input[autocomplete="new-password"]');

if (!givenInput || !familyInput || !passwordInput) {
  return {
    state: 'not-ready',
    inputs: Array.from(document.querySelectorAll('input')).filter(isVisible).map((n)=>[n.type,n.name,n.placeholder,n.getAttribute('data-testid')].join('|')).slice(0,10)
  };
}

const ok1 = setInputValue(givenInput, givenName);
const ok2 = setInputValue(familyInput, familyName);
const ok3 = setInputValue(passwordInput, password);
if (!ok1 || !ok2 || !ok3) return {state:'fill-failed'};

const cf = cfStatus();
const sub = findSubmit();
if (cf.present && !cf.solved) return {state:'wait-cloudflare', tokenLen: cf.tokenLen, submit: sub && sub.text};
if (sub) return {state:'ready-to-submit', submit: sub.text, tokenLen: cf.tokenLen};
return {state:'filled-no-submit', tokenLen: cf.tokenLen, buttons: Array.from(document.querySelectorAll('button,[role="button"],input[type="submit"]')).filter(isVisible).map(btnText).slice(0,12)};
                """,
                given_name,
                family_name,
                password,
            )

            state = filled.get("state") if isinstance(filled, dict) else filled
            if state == "wait-cloudflare":
                form_filled_once = True
                token_len = filled.get("tokenLen") if isinstance(filled, dict) else "0"
                if log_callback:
                    log_callback(f"[*] 资料已填写，猫尾巴耐心等 Cloudflare 放行喵... 当前token长度={token_len}")
                now = time.time()
                if wait_cf_since is None:
                    wait_cf_since = now
                if now - wait_cf_since >= 12 and now - last_cf_retry_at >= 10:
                    if log_callback:
                        log_callback("[*] Cloudflare 验证卡住，开始二次复用 Turnstile...")
                    try:
                        token = getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
                        if token:
                            synced = page.run_js(
                                """
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return 0;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(cfInput.value || '').trim().length;
                                """,
                                token,
                            )
                            if log_callback:
                                log_callback(f"[*] Turnstile 二次复用完成，猫爪回填成功喵~ 长度={synced}")
                    except Exception as cf_exc:
                        if log_callback:
                            log_callback(f"[Debug] Turnstile 二次复用失败: {cf_exc}")
                    last_cf_retry_at = now
                sleep_with_cancel(0.8, cancel_callback)
                continue

            if state in ("ready-to-submit", "filled-no-submit"):
                form_filled_once = True
                if state == "filled-no-submit" and log_callback and isinstance(filled, dict):
                    log_callback(f"[Debug] 资料已填但暂无提交按钮: {filled.get('buttons')}")
            elif state == "fill-failed":
                if log_callback:
                    log_callback("[Debug] 资料输入失败，重试中...")
                sleep_with_cancel(0.5, cancel_callback)
                continue
            elif state == "not-ready":
                if log_callback and time.time() - last_diag_at >= 5:
                    last_diag_at = time.time()
                    log_callback(f"[Debug] 资料页未就绪: {filled}")
                sleep_with_cancel(0.5, cancel_callback)
                continue

        submit_state = page.run_js(
            submit_match_js
            + r"""
const cf = cfStatus();
if (cf.present && !cf.solved) return {state:'wait-cloudflare', tokenLen: cf.tokenLen};
const sub = findSubmit();
if (!sub) {
  return {
    state: 'no-submit-button',
    tokenLen: cf.tokenLen,
    buttons: Array.from(document.querySelectorAll('button,[role="button"],input[type="submit"]')).filter(isVisible).map(btnText).slice(0,15),
    body: (document.body && document.body.innerText || '').replace(/\s+/g,' ').trim().slice(0,220),
    url: location.href,
  };
}
sub.node.scrollIntoView({block:'center'});
sub.node.focus();
sub.node.click();
return {state:'submitted', submit: sub.text, tokenLen: cf.tokenLen};
            """
        )

        state = submit_state.get("state") if isinstance(submit_state, dict) else submit_state

        if state == "wait-cloudflare":
            token_len = submit_state.get("tokenLen") if isinstance(submit_state, dict) else "0"
            if log_callback:
                log_callback(f"[*] 等待 Cloudflare 人机验证通过后再提交... 当前token长度={token_len}")
            now = time.time()
            if wait_cf_since is None:
                wait_cf_since = now
            if now - wait_cf_since >= 12 and now - last_cf_retry_at >= 10:
                if log_callback:
                    log_callback("[*] 提交前仍卡住，猫猫自动再次复用 Turnstile 喵...")
                try:
                    token = getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
                    if token:
                        synced = page.run_js(
                            """
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return 0;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(cfInput.value || '').trim().length;
                            """,
                            token,
                        )
                        if log_callback:
                            log_callback(f"[*] Turnstile 二次复用完成，猫爪回填成功喵~ 长度={synced}")
                except Exception as cf_exc:
                    if log_callback:
                        log_callback(f"[Debug] Turnstile 二次复用失败: {cf_exc}")
                last_cf_retry_at = now
            sleep_with_cancel(0.8, cancel_callback)
            continue

        if state == "submitted":
            if log_callback:
                detail = ""
                if isinstance(submit_state, dict) and submit_state.get("submit"):
                    detail = f" ({submit_state.get('submit')})"
                log_callback(f"[*] 注册资料已填写并提交喵: {given_name} {family_name}{detail}")
            return {"given_name": given_name, "family_name": family_name, "password": password}

        wait_cf_since = None
        if state == "no-submit-button":
            now = time.time()
            if log_callback and now - last_diag_at >= 3:
                last_diag_at = now
                log_callback(f"[Debug] 未找到提交按钮: {submit_state}")
            try:
                page.run_js(r"""
const pwd = document.querySelector('input[type="password"], input[name="password"], input[autocomplete="new-password"]');
if (!pwd) return false;
pwd.focus();
pwd.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter', bubbles:true, cancelable:true}));
pwd.dispatchEvent(new KeyboardEvent('keyup', {key:'Enter', code:'Enter', bubbles:true, cancelable:true}));
const form = pwd.closest('form');
if (form) {
  if (form.requestSubmit) form.requestSubmit();
  else form.dispatchEvent(new Event('submit', {bubbles:true, cancelable:true}));
}
return true;
""")
            except Exception:
                pass

        sleep_with_cancel(0.8, cancel_callback)

    raise Exception("最终注册页资料填写失败")


def wait_for_sso_cookie(timeout=120, log_callback=None, cancel_callback=None):
    deadline = time.time() + timeout
    last_seen_names = set()
    last_submit_retry = 0.0
    last_cf_retry_at = 0.0

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            refresh_active_page()
            page = _get_page()
            if page is None:
                sleep_with_cancel(1, cancel_callback)
                continue

            # 仍停留在“完成注册”页时，若 Cloudflare 已通过，周期性重试点击提交
            now = time.time()
            if now - last_submit_retry >= 2.5:
                retried = page.run_js(
                    r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function btnText(node) {
    return [node.innerText, node.textContent, node.getAttribute('aria-label'), node.getAttribute('type')].filter(Boolean).join(' ').replace(/\s+/g,' ').trim();
}
// English page often says "Create your account" / "Sign up", not 完成注册.
const body = (document.body && document.body.innerText || '').toLowerCase();
const onProfile = (
  !!document.querySelector('input[type="password"], input[name="password"], input[data-testid="password"]') ||
  body.includes('create your account') || body.includes('完成注册') || body.includes('sign up')
);
if (!onProfile) return 'not-final-page';

const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    const solved = token.length >= 80;
    if (!solved) return 'final-page-wait-cf:' + token.length;
}

const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const raw = btnText(node).toLowerCase();
    const t = raw.replace(/\s+/g, '');
    return (
      t.includes('完成注册') || t.includes('创建账户') || t.includes('createaccount') ||
      t.includes('signup') || raw.includes('sign up') || raw.includes('create account') ||
      t.includes('continue') || t.includes('next') || t.includes('submit') ||
      (node.getAttribute('type')||'').toLowerCase()==='submit'
    );
});
if (!submitBtn) return 'final-page-no-submit';
submitBtn.focus();
submitBtn.click();
return 'final-page-clicked-submit';
"""
                )
                last_submit_retry = now
                if log_callback and retried in ("final-page-no-submit", "final-page-clicked-submit"):
                    log_callback(f"[Debug] 最终页状态: {retried}")
                if log_callback and isinstance(retried, str) and retried.startswith("final-page-wait-cf"):
                    token_len = retried.split(":", 1)[1] if ":" in retried else "0"
                    log_callback(f"[Debug] 最终页状态: final-page-wait-cf, token长度={token_len}")
                    if now - last_cf_retry_at >= 10:
                        if log_callback:
                            log_callback("[*] 最终页 Cloudflare 卡住，自动二次复用 Turnstile...")
                        try:
                            token = getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
                            if token:
                                synced = page.run_js(
                                    """
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(cfInput.value || '').trim().length;
                                    """,
                                    token,
                                )
                                if log_callback:
                                    log_callback(f"[*] 最终页 Turnstile 二次复用完成，回填长度={synced}")
                        except Exception as cf_exc:
                            if log_callback:
                                log_callback(f"[Debug] 最终页 Turnstile 二次复用失败: {cf_exc}")
                        last_cf_retry_at = now

            cookies = page.cookies(all_domains=True, all_info=True) or []
            for item in cookies:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    value = str(item.get("value", "")).strip()
                else:
                    name = str(getattr(item, "name", "")).strip()
                    value = str(getattr(item, "value", "")).strip()

                if name:
                    last_seen_names.add(name)

                if name == "sso" and value:
                    if log_callback:
                        log_callback("[*] 已抓到 sso cookie，稳稳收好啦喵~")
                    return value
        except PageDisconnectedError:
            refresh_active_page()
        except Exception:
            pass

        sleep_with_cancel(1, cancel_callback)

    raise Exception(
        f"等待超时：未获取到 sso cookie。已看到 cookies: {sorted(last_seen_names)}"
    )


class GrokRegisterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Grok 注册机")
        self.root.geometry("980x860")
        self.root.minsize(900, 760)
        self.is_running = False
        self.batch_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.results = []
        self.stop_requested = False
        self.ui_queue = queue.Queue()
        self.accounts_output_file = ""
        self.eyj_tokens_output_file = ""
        self.stats_lock = threading.Lock()
        self._tutorial_window = None
        self.setup_ui()
        self.root.after(200, self._maybe_show_tutorial_on_start)

    def setup_ui(self):
        load_config()
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        config_frame = ttk.LabelFrame(main_frame, text="配置", padding=10)
        config_frame.pack(fill=tk.X, pady=5)
        ttk.Label(config_frame, text="邮箱服务商:").grid(row=0, column=0, sticky=tk.W)
        self.email_provider_var = tk.StringVar(value=config.get("email_provider", "duckmail"))
        self.email_provider_combo = ttk.Combobox(config_frame, textvariable=self.email_provider_var, values=["duckmail", "yyds", "cloudflare"], width=12, state="readonly")
        self.email_provider_combo.grid(row=0, column=1, sticky=tk.W, padx=5)
        ttk.Label(config_frame, text="注册数量:").grid(row=0, column=2, sticky=tk.W, padx=10)
        self.count_var = tk.StringVar(value=str(config.get("register_count", 1)))
        self.count_spinbox = ttk.Spinbox(config_frame, from_=1, to=100, width=8, textvariable=self.count_var)
        self.count_spinbox.grid(row=0, column=3, sticky=tk.W, padx=5)
        ttk.Label(config_frame, text="并发线程:").grid(row=1, column=2, sticky=tk.W, padx=10)
        self.thread_var = tk.StringVar(value=str(config.get("register_threads", 1)))
        self.thread_spinbox = ttk.Spinbox(config_frame, from_=1, to=10, width=8, textvariable=self.thread_var)
        self.thread_spinbox.grid(row=1, column=3, sticky=tk.W, padx=5)
        self.nsfw_var = tk.BooleanVar(value=config.get("enable_nsfw", True))
        self.nsfw_check = ttk.Checkbutton(config_frame, text="注册后开启 NSFW", variable=self.nsfw_var)
        self.nsfw_check.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=5)
        ttk.Label(config_frame, text="代理（可选）:").grid(row=2, column=0, sticky=tk.W)
        self.proxy_var = tk.StringVar(value=config.get("proxy", ""))
        self.proxy_entry = ttk.Entry(config_frame, textvariable=self.proxy_var, width=30)
        self.proxy_entry.grid(row=2, column=1, columnspan=3, sticky=tk.W, padx=5)
        ttk.Label(config_frame, text="DuckMail API Key:").grid(row=3, column=0, sticky=tk.W)
        self.api_key_var = tk.StringVar(value=config.get("duckmail_api_key", ""))
        self.api_key_entry = ttk.Entry(config_frame, textvariable=self.api_key_var, width=30)
        self.api_key_entry.grid(row=3, column=1, columnspan=3, sticky=tk.W, padx=5)
        ttk.Label(config_frame, text="Cloudflare API Base:").grid(row=4, column=0, sticky=tk.W)
        self.cloudflare_api_base_var = tk.StringVar(value=config.get("cloudflare_api_base", ""))
        self.cloudflare_api_base_entry = ttk.Entry(config_frame, textvariable=self.cloudflare_api_base_var, width=30)
        self.cloudflare_api_base_entry.grid(row=4, column=1, columnspan=3, sticky=tk.W, padx=5)
        ttk.Label(config_frame, text="Cloudflare API Key:").grid(row=5, column=0, sticky=tk.W)
        self.cloudflare_api_key_var = tk.StringVar(value=config.get("cloudflare_api_key", ""))
        self.cloudflare_api_key_entry = ttk.Entry(config_frame, textvariable=self.cloudflare_api_key_var, width=30)
        self.cloudflare_api_key_entry.grid(row=5, column=1, columnspan=3, sticky=tk.W, padx=5)
        ttk.Label(config_frame, text="Cloudflare 全局密码:").grid(row=5, column=2, sticky=tk.W, padx=10)
        self.cloudflare_custom_auth_var = tk.StringVar(value=config.get("cloudflare_custom_auth", ""))
        self.cloudflare_custom_auth_entry = ttk.Entry(config_frame, textvariable=self.cloudflare_custom_auth_var, width=16)
        self.cloudflare_custom_auth_entry.grid(row=5, column=3, sticky=tk.W, padx=5)
        ttk.Label(config_frame, text="Cloudflare 鉴权模式:").grid(row=6, column=0, sticky=tk.W)
        self.cloudflare_auth_mode_var = tk.StringVar(value=config.get("cloudflare_auth_mode", "bearer"))
        self.cloudflare_auth_mode_combo = ttk.Combobox(
            config_frame,
            textvariable=self.cloudflare_auth_mode_var,
            values=["none", "bearer", "x-api-key", "x-admin-auth", "query-key"],
            width=12,
            state="readonly",
        )
        self.cloudflare_auth_mode_combo.grid(row=6, column=1, sticky=tk.W, padx=5)
        ttk.Label(config_frame, text="CF 路径(domains/accounts/token/messages):").grid(row=7, column=0, sticky=tk.W)
        self.cloudflare_paths_var = tk.StringVar(
            value=",".join(
                [
                    config.get("cloudflare_path_domains", "/domains"),
                    config.get("cloudflare_path_accounts", "/accounts"),
                    config.get("cloudflare_path_token", "/token"),
                    config.get("cloudflare_path_messages", "/messages"),
                ]
            )
        )
        self.cloudflare_paths_entry = ttk.Entry(config_frame, textvariable=self.cloudflare_paths_var, width=30)
        self.cloudflare_paths_entry.grid(row=7, column=1, columnspan=3, sticky=tk.W, padx=5)

        ttk.Label(config_frame, text="grok2api 本地自动入池:").grid(row=8, column=0, sticky=tk.W)
        self.grok2api_local_auto_var = tk.BooleanVar(value=bool(config.get("grok2api_auto_add_local", True)))
        self.grok2api_local_auto_check = ttk.Checkbutton(config_frame, variable=self.grok2api_local_auto_var)
        self.grok2api_local_auto_check.grid(row=8, column=1, sticky=tk.W, padx=5)

        ttk.Label(config_frame, text="grok2api 本地 token.json:").grid(row=9, column=0, sticky=tk.W)
        self.grok2api_local_file_var = tk.StringVar(value=str(config.get("grok2api_local_token_file", "")))
        self.grok2api_local_file_entry = ttk.Entry(config_frame, textvariable=self.grok2api_local_file_var, width=30)
        self.grok2api_local_file_entry.grid(row=9, column=1, columnspan=3, sticky=tk.W, padx=5)

        ttk.Label(config_frame, text="grok2api 池名:").grid(row=10, column=0, sticky=tk.W)
        self.grok2api_pool_name_var = tk.StringVar(value=str(config.get("grok2api_pool_name", "ssoBasic")))
        self.grok2api_pool_name_combo = ttk.Combobox(
            config_frame,
            textvariable=self.grok2api_pool_name_var,
            values=["ssoBasic", "ssoSuper"],
            width=12,
            state="readonly",
        )
        self.grok2api_pool_name_combo.grid(row=10, column=1, sticky=tk.W, padx=5)

        ttk.Label(config_frame, text="grok2api 远端自动入池:").grid(row=11, column=0, sticky=tk.W)
        self.grok2api_remote_auto_var = tk.BooleanVar(value=bool(config.get("grok2api_auto_add_remote", False)))
        self.grok2api_remote_auto_check = ttk.Checkbutton(config_frame, variable=self.grok2api_remote_auto_var)
        self.grok2api_remote_auto_check.grid(row=11, column=1, sticky=tk.W, padx=5)

        ttk.Label(config_frame, text="grok2api 远端 Base:").grid(row=12, column=0, sticky=tk.W)
        self.grok2api_remote_base_var = tk.StringVar(value=str(config.get("grok2api_remote_base", "")))
        self.grok2api_remote_base_entry = ttk.Entry(config_frame, textvariable=self.grok2api_remote_base_var, width=30)
        self.grok2api_remote_base_entry.grid(row=12, column=1, columnspan=3, sticky=tk.W, padx=5)

        ttk.Label(config_frame, text="grok2api 旧版兼容密钥:").grid(row=13, column=0, sticky=tk.W)
        self.grok2api_remote_key_var = tk.StringVar(value=str(config.get("grok2api_remote_app_key", "")))
        self.grok2api_remote_key_entry = ttk.Entry(config_frame, textvariable=self.grok2api_remote_key_var, width=30)
        self.grok2api_remote_key_entry.grid(row=13, column=1, columnspan=3, sticky=tk.W, padx=5)
        ttk.Label(config_frame, text="默认域名(defaultDomains):").grid(row=14, column=0, sticky=tk.W)
        self.default_domains_var = tk.StringVar(value=str(config.get("defaultDomains", "")))
        self.default_domains_entry = ttk.Entry(config_frame, textvariable=self.default_domains_var, width=30)
        self.default_domains_entry.grid(row=14, column=1, columnspan=3, sticky=tk.W, padx=5)
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=10)
        self.start_btn = ttk.Button(btn_frame, text="开始注册", command=self.start_registration)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = ttk.Button(btn_frame, text="停止", command=self.stop_registration, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        self.clear_btn = ttk.Button(btn_frame, text="清空日志", command=self.clear_log)
        self.clear_btn.pack(side=tk.LEFT, padx=5)
        self.help_btn = ttk.Button(btn_frame, text="教程", command=self.show_tutorial)
        self.help_btn.pack(side=tk.LEFT, padx=5)
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=5)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status_frame, text="状态: ").pack(side=tk.LEFT)
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, foreground="green")
        self.status_label.pack(side=tk.LEFT)
        self.stats_var = tk.StringVar(value="成功: 0 | 失败: 0")
        ttk.Label(status_frame, textvariable=self.stats_var).pack(side=tk.RIGHT)
        log_frame = ttk.LabelFrame(main_frame, text="日志", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, width=60)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def log(self, message):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        # 仅当用户当前就在底部时自动跟随，避免手动上滑后被强制拉回底部
        yview = self.log_text.yview()
        at_bottom = bool(yview) and yview[1] >= 0.999
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        if at_bottom:
            self.log_text.see(tk.END)

    def clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def update_stats(self):
        self.stats_var.set(f"成功: {self.success_count} | 失败: {self.fail_count}")

    def _set_running_ui(self, running):
        self.is_running = running
        self.start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)
        self.status_var.set("运行中..." if running else "就绪")
        self.status_label.config(foreground="blue" if running else "green")

    def _maybe_show_tutorial_on_start(self):
        if bool(config.get("show_tutorial_on_start", True)):
            self.show_tutorial()

    def _tutorial_text(self):
        return """欢迎使用 Grok 注册机。建议按下面顺序填写（从最关键到可选）：

【第一步：先确定邮箱后端信息从哪里来】
如果你使用 cloudflare 模式（你当前主要是这套），先去你的临时邮箱服务配置接口查信息：
- 常见接口: /open_api/settings、/api/settings、/health_check
- 重点字段:
  - api_base（对应本工具的 Cloudflare API Base）
  - domains / defaultDomains（可用域名）
  - needAuth（是否需要鉴权）
  - admin_password 或 api_key（需要鉴权时使用）
  - provider.type（应为 cloudflare_temp_email）

【第二步：先填最小可运行配置】
1) 邮箱服务商
- duckmail: 需要 DuckMail API Key
- yyds: 需要 YYDS API Key 或 JWT
- cloudflare: 需要 Cloudflare API Base（推荐你当前使用）

2) Cloudflare API Base（cloudflare 模式必填）
- 示例: https://xxxx.pages.dev
- 填写规则: 与 settings 接口中的 api_base 保持一致

3) 默认域名(defaultDomains)
- 填写你要优先使用的域名
- 支持单域名或逗号分隔多域名轮换
- 示例: a.com,b.com

4) CF 路径(domains/accounts/token/messages)
- 必须与后端真实路由一致
- 常见新路径:
  - /api/domains,/api/new_address,/api/token,/api/mails
- 常见旧路径:
  - /domains,/accounts,/token,/messages

5) Cloudflare API Key / 鉴权模式
- needAuth=false: 通常鉴权模式选 none，key 可留空
- needAuth=true: 按后端要求填 key，并选择 bearer/x-api-key/query-key

【第三步：并发与稳定性】
6) 注册数量
- 本次要注册的总账号数

7) 并发线程
- 建议先 3-6 稳定后再升到 10

8) 代理（可选）
- 不填=直连
- 示例: http://127.0.0.1:7890
- 代理不稳会影响验证码和注册稳定性

9) 注册后开启 NSFW
- 勾选后成功账号会自动调用接口开启对应设置

【第四步：grok2api 入池（可选）】
10) grok2api 本地自动入池
- 开启后把成功 sso 自动写入本地池
- 本地 token.json 填 grok2api 的 token.json 路径

11) grok2api 池名
- ssoBasic 或 ssoSuper

12) grok2api 远端自动入池
- 开启后调用远端管理接口自动加 token
- 远端 Base 示例: https://xxx/admin/api
- app_key 按远端服务配置填写

【最后：快速自检】
1) 先设置: 注册数量=1，并发线程=1
2) 点开始后看日志是否出现：
- 已创建邮箱: xxx@你的域名
- Cloudflare 本轮邮件数量: ...
- 从邮件中提取到验证码: ...
3) 若第一步就失败，优先检查 API Base / CF 路径 / 鉴权模式

提示:
- 点“开始注册”会自动保存当前配置到 config.json。
- 如果关闭了启动教程，可随时点主界面的“教程”按钮重新打开。"""

    def show_tutorial(self):
        if self._tutorial_window is not None and self._tutorial_window.winfo_exists():
            self._tutorial_window.lift()
            self._tutorial_window.focus_force()
            return

        win = tk.Toplevel(self.root)
        self._tutorial_window = win
        win.title("使用教程")
        win.geometry("760x620")
        win.minsize(680, 520)
        win.transient(self.root)

        frame = ttk.Frame(win, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        txt = scrolledtext.ScrolledText(frame, wrap=tk.WORD, height=26)
        txt.pack(fill=tk.BOTH, expand=True)
        txt.insert("1.0", self._tutorial_text())
        txt.config(state=tk.DISABLED)

        footer = ttk.Frame(frame)
        footer.pack(fill=tk.X, pady=(8, 0))

        dont_show_var = tk.BooleanVar(value=not bool(config.get("show_tutorial_on_start", True)))
        chk = ttk.Checkbutton(
            footer,
            text="以后不再自动显示本教程",
            variable=dont_show_var,
        )
        chk.pack(side=tk.LEFT)

        def on_close():
            config["show_tutorial_on_start"] = not bool(dont_show_var.get())
            save_config()
            try:
                win.destroy()
            except Exception:
                pass

        close_btn = ttk.Button(footer, text="关闭", command=on_close)
        close_btn.pack(side=tk.RIGHT, padx=5)
        win.protocol("WM_DELETE_WINDOW", on_close)

    def should_stop(self):
        return self.stop_requested or not self.is_running

    def start_registration(self):
        if self.is_running:
            self.log("[!] 当前已有任务在运行")
            return

        config["email_provider"] = self.email_provider_var.get().strip() or "duckmail"
        config["enable_nsfw"] = bool(self.nsfw_var.get()) if hasattr(self, "nsfw_var") else bool(config.get("enable_nsfw", True))
        config["proxy"] = self.proxy_var.get().strip()
        config["duckmail_api_key"] = self.api_key_var.get().strip()
        config["cloudflare_api_base"] = self.cloudflare_api_base_var.get().strip()
        config["cloudflare_api_key"] = self.cloudflare_api_key_var.get().strip()
        if hasattr(self, "cloudflare_custom_auth_var"):
            config["cloudflare_custom_auth"] = self.cloudflare_custom_auth_var.get().strip()
        config["cloudflare_auth_mode"] = self.cloudflare_auth_mode_var.get().strip() or "bearer"
        config["grok2api_auto_add_local"] = bool(self.grok2api_local_auto_var.get())
        config["grok2api_local_token_file"] = self.grok2api_local_file_var.get().strip()
        config["grok2api_pool_name"] = self.grok2api_pool_name_var.get().strip() or "ssoBasic"
        config["grok2api_auto_add_remote"] = bool(self.grok2api_remote_auto_var.get())
        config["grok2api_remote_base"] = self.grok2api_remote_base_var.get().strip()
        config["grok2api_remote_app_key"] = self.grok2api_remote_key_var.get().strip()
        config["defaultDomains"] = self.default_domains_var.get().strip()
        try:
            config["register_threads"] = max(1, min(10, int(self.thread_var.get())))
        except Exception:
            config["register_threads"] = 1
        raw_paths = [x.strip() for x in self.cloudflare_paths_var.get().split(",") if x.strip()]
        if len(raw_paths) >= 4:
            config["cloudflare_path_domains"] = raw_paths[0] if raw_paths[0].startswith("/") else ("/" + raw_paths[0])
            config["cloudflare_path_accounts"] = raw_paths[1] if raw_paths[1].startswith("/") else ("/" + raw_paths[1])
            config["cloudflare_path_token"] = raw_paths[2] if raw_paths[2].startswith("/") else ("/" + raw_paths[2])
            config["cloudflare_path_messages"] = raw_paths[3] if raw_paths[3].startswith("/") else ("/" + raw_paths[3])
        save_config()
        if config["email_provider"] == "cloudflare" and not config["cloudflare_api_base"]:
            self.log("[!] Cloudflare 模式需要先填写 Cloudflare API Base")
            return
        try:
            count = int(self.count_var.get())
        except Exception:
            self.log("[!] 注册数量无效")
            return
        self.stop_requested = False
        self.success_count = 0
        self.fail_count = 0
        self.results = []
        self.accounts_output_file, self.eyj_tokens_output_file = get_account_export_paths()
        self.update_stats()
        self._set_running_ui(True)
        worker_count = max(1, min(config.get("register_threads", 1), count))
        self.log(f"[*] 配置已保存，猫猫开始执行喵~ 目标数量: {count}，并发线程: {worker_count}")
        self.log(f"[*] 成功账号会实时收好在: {self.accounts_output_file}")
        self.log(f"[*] eyJ Token 会额外收好在: {self.eyj_tokens_output_file}")
        threading.Thread(
            target=self.run_registration,
            args=(count, worker_count),
            daemon=True,
        ).start()

    def stop_registration(self):
        self.stop_requested = True
        self.log("[!] 用户停止注册")

    def _run_single_registration(self, idx, total, logf):
        email = ""
        dev_token = ""
        code = ""
        mail_ok = False
        max_mail_retry = 3
        for mail_try in range(1, max_mail_retry + 1):
            logf(f"[*] 1. 打开注册页，猫爪先探路喵 (尝试 {mail_try}/{max_mail_retry})")
            open_signup_page(log_callback=logf, cancel_callback=self.should_stop)
            logf("[*] 2. 创建邮箱并提交，去叼个新邮箱回来喵")
            email, dev_token = fill_email_and_submit(log_callback=logf, cancel_callback=self.should_stop)
            logf(f"[*] 邮箱: {email}")
            try:
                with open(os.path.join(os.path.dirname(__file__), "mail_credentials.txt"), "a", encoding="utf-8") as f:
                    f.write(f"{email}\t{dev_token}\n")
            except Exception:
                pass
            logf("[*] 3. 蹲守验证码邮件喵")
            try:
                code = fill_code_and_submit(email, dev_token, log_callback=logf, cancel_callback=self.should_stop)
                mail_ok = True
                break
            except Exception as mail_exc:
                msg = str(mail_exc)
                if ("未收到验证码" in msg or "验证码" in msg) and mail_try < max_mail_retry:
                    logf(f"[!] 本邮箱未取到验证码，自动更换新邮箱重试: {msg}")
                    restart_browser(log_callback=logf)
                    sleep_with_cancel(1, self.should_stop)
                    continue
                raise
        if not mail_ok:
            raise Exception("验证码阶段失败，已达到最大重试次数")
        logf(f"[*] 验证码: {code}")
        logf("[*] 4. 填写资料，摇着尾巴继续喵")
        profile = fill_profile_and_submit(log_callback=logf, cancel_callback=self.should_stop)
        logf(f"[*] 资料已填: {profile.get('given_name')} {profile.get('family_name')}")
        logf("[*] 5. 蹲守 sso cookie 喵")
        sso = wait_for_sso_cookie(log_callback=logf, cancel_callback=self.should_stop)
        with self.stats_lock:
            self.results.append({"email": email, "sso": sso, "profile": profile})
            self.success_count += 1
            line = f"{email}----{profile.get('password','')}----{sso}\n"
            try:
                with open(self.accounts_output_file, "a", encoding="utf-8") as f:
                    f.write(line)
                token = normalize_export_token(sso)
                if token.startswith("eyJ"):
                    with open(self.eyj_tokens_output_file, "a", encoding="utf-8") as f:
                        f.write(f"{token}\n")
            except Exception as file_exc:
                logf(f"[Debug] 保存账号文件失败: {file_exc}")
        add_token_to_grok2api_pools(sso, email=email, log_callback=logf)
        logf(f"[+] 注册成功，已经记好啦喵~ {email}")

    def _worker_loop(self, worker_id, total, task_queue):
        prefix = f"[T{worker_id}]"
        logf = lambda m: self.log(f"{prefix} {m}")
        try:
            start_browser(log_callback=logf)
            logf("[*] 浏览器已启动，猫猫出发喵~")
            while not self.should_stop():
                try:
                    idx = task_queue.get_nowait()
                except queue.Empty:
                    break
                logf(f"--- 开始第 {idx}/{total} 个账号，猫爪开工喵 ---")
                try:
                    self._run_single_registration(idx, total, logf)
                except RegistrationCancelled:
                    logf("[!] 注册被用户停止")
                    break
                except Exception as exc:
                    with self.stats_lock:
                        self.fail_count += 1
                    logf(f"[-] 注册失败: {exc}")
                finally:
                    self.update_stats()
                    if self.should_stop():
                        break
                    restart_browser(log_callback=logf)
                    sleep_with_cancel(1, self.should_stop)
        except Exception as exc:
            logf(f"[!] 线程异常: {exc}")
        finally:
            stop_browser()

    def run_registration(self, count, worker_count):
        task_queue = queue.Queue()
        for i in range(1, count + 1):
            task_queue.put(i)
        workers = []
        try:
            start_interval = float(config.get("thread_start_interval", 0.8))
        except Exception:
            start_interval = 0.8
        if start_interval < 0:
            start_interval = 0.0
        for wid in range(1, worker_count + 1):
            t = threading.Thread(target=self._worker_loop, args=(wid, count, task_queue), daemon=True)
            workers.append(t)
            t.start()
            if wid < worker_count and start_interval > 0:
                sleep_with_cancel(start_interval, self.should_stop)
        for t in workers:
            t.join()
        self._set_running_ui(False)
        self.log("[*] 任务结束，猫猫收工啦~")

def main():
    root = tk.Tk()
    app = GrokRegisterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
