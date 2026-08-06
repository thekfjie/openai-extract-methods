#!/usr/bin/env python3
"""
ChatGPT 注册 + Sub2API OAuth 导入
用法: python3 uc_signup.py
"""
import argparse, base64, hashlib, json, os, re, secrets, shutil, signal, string, subprocess, sys, time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

from integrations.browser_resume import (
    capture_browser_checkpoint,
    resume_state_path,
    restore_browser_checkpoint,
)
from integrations.oai_fingerprint import (
    align_fingerprint_locale_to_region,
    apply_chromium_fingerprint,
    chromium_launch_args,
    fingerprint_summary,
    force_fingerprint_screen,
    generate_entry_fingerprint,
    load_or_create_uc_fingerprint_identity,
)
from integrations.proxy_config import MIHOMO_SUB2API_PROFILES, normalize_proxy_region, parse_proxy_pool_urls
from integrations.opus_mail_client import OpusMailClient, OpusMailError

# ── 配置 ────────────────────────────────────────────────
API    = os.getenv("UC_SIGNUP_API_BASE", os.getenv("API_BASE", "http://127.0.0.1:13030"))
PROXY  = os.getenv("UC_SIGNUP_PROXY", os.getenv("BROWSER_PROXY", os.getenv("PROXY", ""))).strip()
ROOT   = Path(__file__).resolve().parent

def load_app_config():
    try:
        data = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

APP_CONFIG = load_app_config()

def app_config_value(key, default=""):
    value = APP_CONFIG.get(key, default)
    if value in (None, ""):
        return default
    return str(value)


def redact_log_url(value):
    """Keep host/path diagnostics while removing OAuth query material."""
    raw = str(value or "").strip()
    try:
        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.hostname:
            return "[URL_REDACTED]"
        return f"{parsed.scheme}://{parsed.hostname}{parsed.path or '/'}"
    except Exception:
        return "[URL_REDACTED]"


def redact_log_payload(value):
    """Redact token/code/session fields before a payload enters task logs."""
    secret_keys = {
        "access_token", "refresh_token", "session_token", "id_token",
        "token", "code", "state", "redirect_url", "auth_url", "url",
    }
    if isinstance(value, dict):
        return {
            str(key): ("[REDACTED]" if str(key).lower() in secret_keys else redact_log_payload(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_log_payload(item) for item in value]
    return value

API = os.getenv(
    "UC_SIGNUP_API_BASE",
    os.getenv("API_BASE", f"http://127.0.0.1:{os.getenv('AUTOMYAI_PORT') or app_config_value('PORT', '13030')}")
).rstrip("/")
PROXY = os.getenv(
    "UC_SIGNUP_PROXY",
    os.getenv(
        "BROWSER_PROXY",
        os.getenv("PROXY", app_config_value("UC_SIGNUP_PROXY", app_config_value("BROWSER_PROXY", PROXY))),
    ),
).strip()
if not PROXY:
    # 不允许静默回落 Mihomo/直连。没传 --proxy / 环境变量就保持空，后面 launch 直接拒绝。
    proxy_mode = app_config_value("SIGNUP_PROXY_MODE", "custom").strip().lower()
    if proxy_mode == "cliproxy":
        proxy_source = app_config_value("CLIPROXY_PROXY_URL", "")
    else:
        proxy_source = app_config_value(
            "SIGNUP_PROXY_CUSTOM_URL",
            app_config_value("UC_SIGNUP_PROXY", app_config_value("BROWSER_PROXY", "")),
        )
    configured_proxies = parse_proxy_pool_urls(proxy_source)
    PROXY = configured_proxies[0] if configured_proxies else ""
MAX_RETRIES    = 3   # 每步最大重试次数
MAX_ERROR_REFRESH = 5  # 错误页刷新次数
PHONE_RETRY_LIMIT = int(os.getenv("UC_SIGNUP_PHONE_RETRIES", app_config_value("UC_SIGNUP_PHONE_RETRIES", "0")))
PHONE_PURCHASE_ATTEMPT_LIMIT = int(os.getenv("UC_SIGNUP_PHONE_PURCHASE_ATTEMPTS", "12") or "12")
FORCED_PHONE = os.getenv("UC_SIGNUP_FORCED_PHONE", "").strip()
SMS_TIMEOUT_SECONDS = int(os.getenv("UC_SIGNUP_SMS_TIMEOUT_SECONDS", app_config_value("UC_SIGNUP_SMS_TIMEOUT_SECONDS", "135")))
SMS_POLL_INTERVAL_SECONDS = int(os.getenv("UC_SIGNUP_SMS_POLL_INTERVAL_SECONDS", app_config_value("UC_SIGNUP_SMS_POLL_INTERVAL_SECONDS", "10")))
PHONE_PASSWORD_PAGE_TIMEOUT = int(os.getenv("UC_SIGNUP_PHONE_PASSWORD_PAGE_TIMEOUT", app_config_value("UC_SIGNUP_PHONE_PASSWORD_PAGE_TIMEOUT", "25")))
CF_CLEARANCE_ENABLED = os.getenv("UC_SIGNUP_CF_CLEARANCE_ENABLED", app_config_value("UC_SIGNUP_CF_CLEARANCE_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}
CF_CLEARANCE_API_URL = os.getenv("UC_SIGNUP_CF_CLEARANCE_API_URL", app_config_value("UC_SIGNUP_CF_CLEARANCE_API_URL", "http://127.0.0.1:18191/v1")).strip()
CF_CLEARANCE_TARGET_URL = os.getenv(
    "UC_SIGNUP_CF_CLEARANCE_TARGET_URL", app_config_value("UC_SIGNUP_CF_CLEARANCE_TARGET_URL", "https://chatgpt.com/auth/login?intent=signup")
).strip()
CF_CLEARANCE_TIMEOUT_SECONDS = int(os.getenv("UC_SIGNUP_CF_CLEARANCE_TIMEOUT_SECONDS", app_config_value("UC_SIGNUP_CF_CLEARANCE_TIMEOUT_SECONDS", "90")))
CF_CLEARANCE_CACHE_SECONDS = int(os.getenv("UC_SIGNUP_CF_CLEARANCE_CACHE_SECONDS", app_config_value("UC_SIGNUP_CF_CLEARANCE_CACHE_SECONDS", "1800")) or "1800")
KEEP_BROWSER_ON_FAILURE = os.getenv("UC_SIGNUP_KEEP_BROWSER_ON_FAILURE", app_config_value("UC_SIGNUP_KEEP_BROWSER_ON_FAILURE", "false")).strip().lower() in {"1", "true", "yes", "on"}
KEEP_BROWSER_SECONDS = int(os.getenv("UC_SIGNUP_KEEP_BROWSER_SECONDS", app_config_value("UC_SIGNUP_KEEP_BROWSER_SECONDS", "0")) or "0")
KEEP_BROWSER_MAX_SECONDS = int(os.getenv("UC_SIGNUP_KEEP_BROWSER_MAX_SECONDS", "900") or "900")
UC_SIGNUP_NETWORK_DIAGNOSTICS = os.getenv(
    "UC_SIGNUP_NETWORK_DIAGNOSTICS",
    app_config_value("UC_SIGNUP_NETWORK_DIAGNOSTICS", "true"),
).strip().lower() in {"1", "true", "yes", "on"}
PROFILE_BASE_DIR = Path(os.getenv("UC_SIGNUP_PROFILE_BASE_DIR", app_config_value("UC_SIGNUP_PROFILE_BASE_DIR", str(ROOT / "data" / "browser_profiles")))).expanduser()
UC_SIGNUP_RESUME_ENABLED = os.getenv(
    "UC_SIGNUP_RESUME_ENABLED",
    app_config_value("UC_SIGNUP_RESUME_ENABLED", "true"),
).strip().lower() in {"1", "true", "yes", "on"}
EMAIL_STAGE_PATH = ROOT / "data" / "uc_signup_email_stage.json"
CF_CLEARANCE_CACHE_PATH = ROOT / "data" / "cf_clearance_cache.json"
FAILURE_ARTIFACT_BASE_DIR = ROOT / "data" / "openai4" / "failure_artifacts"

# 注册参数。新邮箱会生成独立随机密码；这个值只作为旧账号无存档密码时的备用值。
PW   = os.getenv("SIGNUP_PASSWORD", app_config_value("SIGNUP_PASSWORD", "ChangeMe123456!"))
NAME = os.getenv("SIGNUP_NAME", app_config_value("SIGNUP_NAME", ""))
AGE  = os.getenv("SIGNUP_AGE", app_config_value("SIGNUP_AGE", ""))
DISPLAY = os.getenv("UC_SIGNUP_DISPLAY", os.getenv("BROWSER_DISPLAY", app_config_value("BROWSER_DISPLAY", ":1")))
VNC_RESOLUTION = os.getenv("VNC_RESOLUTION", "1280x800x24")
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def headed_desktop_size(value: str = VNC_RESOLUTION) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in str(value).split("x", 2)[:2])
    except (TypeError, ValueError):
        return 1280, 800
    if width < 800 or height < 600:
        return 1280, 800
    return width, height

PLACEHOLDER_SIGNUP_NAMES = {"", "test user", "fuck oai", "help oai"}
FALLBACK_FIRST_NAMES = (
    "Aaron", "Adam", "Adrian", "Alex", "Andrew", "Anthony", "Ashley", "Benjamin",
    "Brandon", "Brian", "Brittany", "Charles", "Christopher", "Daniel", "David", "Dylan",
    "Elizabeth", "Emily", "Emma", "Eric", "Ethan", "Gary", "Grace", "Hannah", "James",
    "Jennifer", "Jessica", "John", "Joseph", "Kevin", "Laura", "Lauren", "Leslie", "Lindsay",
    "Madison", "Matthew", "Michael", "Natalie", "Nicholas", "Olivia", "Rachel", "Rebecca",
    "Robert", "Ryan", "Sarah", "Sophia", "Steven", "Thomas", "Victoria", "William",
)
FALLBACK_LAST_NAMES = (
    "Adams", "Allen", "Anderson", "Baker", "Bennett", "Brooks", "Brown", "Bryant",
    "Butler", "Campbell", "Carter", "Clark", "Collins", "Cooper", "Davis", "Edwards",
    "Evans", "Fisher", "Foster", "Garcia", "Gonzalez", "Gray", "Green", "Hall", "Harris",
    "Hayes", "Henderson", "Hill", "Howard", "Jackson", "James", "Johnson", "Kelly", "King",
    "Lee", "Lewis", "Martin", "Martinez", "Miller", "Mitchell", "Moore", "Morgan", "Morris",
    "Murphy", "Nelson", "Parker", "Perez", "Phillips", "Reed", "Richardson", "Rivera",
    "Roberts", "Robinson", "Rodriguez", "Rogers", "Ross", "Russell", "Sanchez", "Sanders",
    "Scott", "Smith", "Stewart", "Taylor", "Thomas", "Thompson", "Turner", "Walker", "Ward",
    "Watson", "White", "Williams", "Wilson", "Wood", "Wright", "Young",
)
FALLBACK_NAME_POOL = tuple(
    f"{first} {last}" for first in FALLBACK_FIRST_NAMES for last in FALLBACK_LAST_NAMES
)
COMMON_FIRST_NAMES = tuple(name.lower() for name in FALLBACK_FIRST_NAMES)

PASSWORD_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
PASSWORD_LOWER = "abcdefghijkmnopqrstuvwxyz"
PASSWORD_DIGITS = "23456789"
PASSWORD_SYMBOLS = "!@#$%^&*_-+="


def generate_signup_password(length=20):
    rng = secrets.SystemRandom()
    chars = [
        secrets.choice(PASSWORD_UPPER),
        secrets.choice(PASSWORD_LOWER),
        secrets.choice(PASSWORD_DIGITS),
        secrets.choice(PASSWORD_SYMBOLS),
    ]
    alphabet = PASSWORD_UPPER + PASSWORD_LOWER + PASSWORD_DIGITS + PASSWORD_SYMBOLS
    chars.extend(secrets.choice(alphabet) for _ in range(max(length, 16) - len(chars)))
    rng.shuffle(chars)
    return "".join(chars)


def mask_secret(value):
    text = str(value or "")
    if not text:
        return ""
    return "*" * min(max(len(text), 8), 12)


def registration_profile_url(url):
    """Return whether an auth URL is a registration profile/details page.

    ``/create-account/password`` contains the create-account token too, but it
    is still a password form.  Treating it as a profile page makes the generic
    fallback write name/age into a React form while it is navigating.
    """
    value = str(url or "").strip().lower()
    return "about-you" in value or ("create-account" in value and "password" not in value)


def title_name_part(value):
    text = re.sub(r"[^A-Za-z]+", "", str(value or ""))
    return text[:1].upper() + text[1:].lower() if text else ""


def stable_index(value, size, *, namespace=""):
    raw = f"{namespace}:{str(value or '').strip().casefold()}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") % max(1, size)


def signup_age(email):
    configured = str(AGE or "").strip()
    if configured:
        try:
            configured_age = int(configured)
            if 20 <= configured_age <= 50:
                return str(configured_age)
        except ValueError:
            pass
    return str(20 + stable_index(email, 31, namespace="signup-age"))


def fallback_signup_name(email):
    return FALLBACK_NAME_POOL[
        stable_index(email, len(FALLBACK_NAME_POOL), namespace="signup-name")
    ]


def signup_display_name(email):
    configured = str(NAME or "").strip()
    if configured.lower() not in PLACEHOLDER_SIGNUP_NAMES:
        return configured

    local = str(email or "").split("@", 1)[0]
    local = re.sub(r"\+.*$", "", local)
    local = re.split(r"\d", local, maxsplit=1)[0]
    local = re.sub(r"[_\-.]+", " ", local)
    local = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", local)
    parts = [part for part in re.split(r"\s+", local.strip()) if part]
    if len(parts) == 1:
        lowered = parts[0].lower()
        for first in COMMON_FIRST_NAMES:
            if lowered.startswith(first) and len(lowered) > len(first) + 1:
                parts = [first, lowered[len(first):]]
                break
    clean_parts = [title_name_part(part) for part in parts[:2]]
    clean_parts = [part for part in clean_parts if 2 <= len(part) <= 24]
    if len(clean_parts) >= 2:
        return " ".join(clean_parts[:2])
    if len(clean_parts) == 1:
        last_name = FALLBACK_LAST_NAMES[
            stable_index(email, len(FALLBACK_LAST_NAMES), namespace="signup-last-name")
        ]
        return f"{clean_parts[0]} {last_name}"
    return fallback_signup_name(email)

def detect_chrome_binary():
    configured = os.getenv("UC_SIGNUP_CHROME_BINARY", os.getenv("CHROME_BINARY", "")).strip()
    if configured:
        return configured
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    return "/usr/bin/google-chrome"

def detect_chrome_version(binary):
    configured = os.getenv("UC_SIGNUP_CHROME_VERSION", "").strip()
    if configured:
        try:
            return int(configured)
        except ValueError:
            pass
    try:
        out = subprocess.check_output([binary, "--version"], text=True, stderr=subprocess.STDOUT, timeout=5)
        m = re.search(r"(\d+)\.", out)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 149

def detect_chromedriver_binary():
    configured = os.getenv("UC_SIGNUP_CHROMEDRIVER_BINARY", os.getenv("CHROMEDRIVER_BINARY", "")).strip()
    if configured:
        return configured
    for name in ("chromedriver",):
        path = shutil.which(name)
        if path:
            return path
    for path in ("/usr/bin/chromedriver", "/usr/lib/chromium/chromedriver"):
        if Path(path).exists():
            return path
    return ""

CHROME_BINARY = detect_chrome_binary()
CHROME_VERSION = detect_chrome_version(CHROME_BINARY)
CHROMEDRIVER_BINARY = detect_chromedriver_binary()

def is_blocked_direct_proxy(proxy_url):
    text = str(proxy_url or "").strip()
    if not text:
        return False
    if "://" not in text:
        text = f"http://{text}"
    try:
        parsed = urlparse(text)
        return parsed.hostname in {"172.19.0.1", "127.0.0.1", "localhost"} and parsed.port == 7911
    except Exception:
        return False


def browser_major_from_user_agent(user_agent):
    match = re.search(r"(?:Chrome|Chromium)/(\d+)", str(user_agent or ""))
    return int(match.group(1)) if match else 0


def is_plain_chatgpt_home(url):
    parsed = urlparse(str(url or ""))
    return (
        (parsed.hostname or "").lower() in {"chatgpt.com", "www.chatgpt.com"}
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def is_oauth_consent_page(url, visible_text):
    parsed = urlparse(str(url or ""))
    if (parsed.hostname or "").lower() != "auth.openai.com":
        return False
    path = (parsed.path or "").lower()
    text = str(visible_text or "").lower()
    consent_route = (
        "oauth" in path
        or path.endswith("/consent")
        or "/consent/" in path
        or "authorize" in path
    )
    return (
        consent_route
        and "codex" in text
        and any(token in text for token in ("authorize", "allow", "continue"))
    )


def choose_account_label_score(label, *, email="", display_name=""):
    """Score a choose-an-account control label. Higher is better; <=0 means skip."""
    text = " ".join(str(label or "").split())
    if not text:
        return 0
    lowered = text.casefold()
    score = 0

    # Never click destructive account controls (remove/delete/logout).
    destructive_needles = (
        "remove account",
        "delete account",
        "remove this account",
        "log out",
        "sign out",
        "logout",
        "アカウントを削除",
        "を削除する",
        "を削除",
        "削除する",
        "ログアウト",
        "サインアウト",
        "删除账户",
        "移除账户",
        "删除账号",
        "移除账号",
        "退出登录",
        "登出",
        "계정 삭제",
        "계정 제거",
        "로그아웃",
        "eliminar cuenta",
        "quitar cuenta",
        "supprimer le compte",
        "konto entfernen",
        "konto löschen",
    )
    if any(needle.casefold() in lowered for needle in destructive_needles):
        return 0
    if "削除" in text or "删除" in text or "移除" in text:
        return 0

    # Avatar/initial-only controls like "OR" from "Ovila Renneke" are common and noisy.
    compact = re.sub(r"\s+", "", text)
    if len(compact) <= 3 and "@" not in text and not any(ch.isdigit() for ch in text):
        # Keep a tiny score only if it matches initials of the display name; never top-rank it.
        initials = ""
        name = " ".join(str(display_name or "").split())
        if name:
            parts = [p for p in re.split(r"\s+", name) if p]
            initials = "".join(p[0] for p in parts[:2])
        if initials and compact.casefold() == initials.casefold():
            score += 5
        else:
            return 0

    email_l = normalize_email(email)
    if email_l and email_l in lowered:
        score += 100
    if email_l and "@" in email_l:
        local = email_l.split("@", 1)[0]
        if local and len(local) >= 3 and local in lowered:
            score += 45

    name = " ".join(str(display_name or "").split())
    if name and name.casefold() in lowered:
        score += 55
    # Prefer full cards that contain both first and last tokens from display name.
    if name:
        parts = [p.casefold() for p in re.split(r"\s+", name) if len(p) >= 2]
        if parts and all(part in lowered for part in parts):
            score += 35

    select_needles = (
        "select account",
        "choose account",
        "use this account",
        "continue as",
        "アカウントを選択",
        "アカウント選択",
        "このアカウントを使用",
        "このアカウント",
        "选择账户",
        "选择账号",
        "選擇帳戶",
        "選擇帳號",
        "계정 선택",
        "이 계정",
        "seleccionar cuenta",
        "elegir cuenta",
        "choisir un compte",
        "utiliser ce compte",
        "konto auswählen",
        "dieses konto",
    )
    if any(needle.casefold() in lowered for needle in select_needles):
        score += 80

    # English/localized cards often render name + email, or a phone with +.
    if "@" in text and "." in text.rsplit("@", 1)[-1]:
        score += 25
    if "+" in text and len(text) > 10 and any(ch.isdigit() for ch in text):
        score += 30

    exclude_needles = (
        "continue with google",
        "continue with apple",
        "continue with microsoft",
        "continue with github",
        "create account",
        "create a new",
        "new account",
        "add account",
        "another account",
        "use a different",
        "sign up",
        "新しいアカウント",
        "別のアカウント",
        "アカウントを作成",
        "アカウントを追加",
        "新建账户",
        "创建账户",
        "其他账户",
        "另一个账户",
        "새 계정",
        "다른 계정",
        "cuenta nueva",
        "otra cuenta",
        "nouveau compte",
        "neues konto",
    )
    if any(needle.casefold() in lowered for needle in exclude_needles):
        return 0
    return score


def element_action_label(element):
    try:
        parts = [
            element.text,
            element.get_attribute("aria-label"),
            element.get_attribute("title"),
            element.get_attribute("value"),
            element.get_attribute("data-testid"),
        ]
    except Exception:
        return ""
    return " ".join(str(part or "") for part in parts).strip()



# ── 工具函数 ────────────────────────────────────────────
def log(msg, level="info"):
    p = {"error":"❌","warn":"⚠️","info":"  "}.get(level,"  ")
    print(f"{p} [{datetime.now(BEIJING_TZ):%H:%M:%S}] {msg}", flush=True)

def api(method, path, body=None):
    url = f"{API}{path}"
    h = {"Accept": "application/json"}
    admin_password = os.getenv("UC_SIGNUP_ADMIN_PASSWORD", os.getenv("ADMIN_PASSWORD", "")).strip()
    if admin_password:
        h["X-Admin-Password"] = admin_password
    data = json.dumps(body).encode() if body else None
    if data: h["Content-Type"] = "application/json"
    try:
        resp = urlopen(Request(url, data=data, method=method, headers=h), timeout=30)
        return json.loads(resp.read().decode())
    except HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace").strip()
        detail = raw
        if raw:
            try:
                payload = json.loads(raw)
                detail = payload.get("error") or payload.get("message") or raw
            except Exception:
                pass
        raise ApiError(f"{method} {path} HTTP {e.code}: {detail or e.reason}") from e
    except URLError as e:
        raise ApiError(f"{method} {path} 连接失败: {e.reason}") from e

def normalize_email(value):
    return str(value or "").strip().lower()

def load_email_stage_state():
    try:
        data = json.loads(EMAIL_STAGE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_email_stage_state(data):
    EMAIL_STAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EMAIL_STAGE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def email_registration_completed(email):
    key = normalize_email(email)
    if not key:
        return False
    record = load_email_stage_state().get(key)
    return isinstance(record, dict) and record.get("registered") is True

def email_stage_record(email):
    key = normalize_email(email)
    if not key:
        return {}
    record = load_email_stage_state().get(key)
    return record if isinstance(record, dict) else {}

def saved_signup_password(email):
    password = str(email_stage_record(email).get("password") or "").strip()
    return password

def ensure_signup_password(email, *, create=True):
    key = normalize_email(email)
    if not key:
        return PW
    data = load_email_stage_state()
    existing = data.get(key) if isinstance(data.get(key), dict) else {}
    password = str(existing.get("password") or "").strip()
    if password:
        return password
    if not create:
        return PW

    password = generate_signup_password()
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    data[key] = {
        **existing,
        "email": str(email or "").strip(),
        "password": password,
        "passwordGeneratedAt": now,
        "passwordPolicy": "random-v1",
        "updatedAt": now,
    }
    save_email_stage_state(data)
    return password

def mark_email_registration_completed(email, password=""):
    key = normalize_email(email)
    if not key:
        return
    data = load_email_stage_state()
    existing = data.get(key) if isinstance(data.get(key), dict) else {}
    password = str(password or existing.get("password") or "").strip()
    now = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
    data[key] = {
        **existing,
        "registered": True,
        "email": str(email or "").strip(),
        # Immutable first-success timestamp.  RT/AT retries must never move
        # an account in Mail Admin, so this is deliberately set once here,
        # at the moment signup itself completes.
        "registrationCreatedAt": existing.get("registrationCreatedAt") or now,
        "updatedAt": now,
    }
    if password:
        data[key]["password"] = password
        data[key].setdefault("passwordPolicy", "random-v1")
    save_email_stage_state(data)


def mark_email_oauth_material_saved(email, *, has_refresh_token=False, has_session_token=False):
    key = normalize_email(email)
    if not key:
        return
    data = load_email_stage_state()
    existing = data.get(key) if isinstance(data.get(key), dict) else {}
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    data[key] = {
        **existing,
        "email": str(email or "").strip(),
        "oauthStoredInMailAdmin": True,
        "oauthAccessTokenStoredInMailAdmin": True,
        "oauthRefreshTokenStoredInMailAdmin": bool(has_refresh_token),
        "oauthSessionTokenStoredInMailAdmin": bool(has_session_token),
        "flowStage": "oauth_saved" if has_refresh_token else "oauth_at_saved_rt_pending",
        "oauthStoredAt": now,
        "updatedAt": now,
    }
    save_email_stage_state(data)


def mark_email_flow_state(email, stage, *, retryable=None, reason="", **flags):
    """Persist the account stage without ever writing token values to this file."""
    key = normalize_email(email)
    if not key:
        return {}
    data = load_email_stage_state()
    existing = data.get(key) if isinstance(data.get(key), dict) else {}
    now = datetime.now(BEIJING_TZ).isoformat(timespec="seconds")
    record = {
        **existing,
        "email": str(email or "").strip(),
        "flowStage": str(stage or "unknown")[:80],
        "flowStageAt": now,
        "updatedAt": now,
    }
    if retryable is not None:
        record["retryable"] = bool(retryable)
    if reason:
        record["lastFailureReason"] = str(reason)[:500]
        record["lastFailureAt"] = now
    for name, value in flags.items():
        if isinstance(value, (bool, int, float, str)) or value is None:
            record[str(name)] = value
    data[key] = record
    save_email_stage_state(data)
    return record


def mark_email_web_session_saved(email, *, has_session_token=False):
    return mark_email_flow_state(
        email,
        "web_session_saved_rt_pending",
        webAccessTokenStoredInMailAdmin=True,
        webSessionTokenStoredInMailAdmin=bool(has_session_token),
        oauthRefreshTokenStoredInMailAdmin=False,
    )


def archive_registered_email_in_mail_admin(email, password="", reason=""):
    """Best-effort immediate archive once signup itself has completed."""
    try:
        client = OpusMailClient.from_project(ROOT)
        stage = email_stage_record(email)
        result = client.import_registered_email(
            email=email,
            password=password,
            reason=reason or "邮箱注册已完成，等待 OAuth / 手机号",
            registration_created_at=str(stage.get("registrationCreatedAt") or "").strip(),
        )
        if result.get("imported"):
            log(f"  Mail Admin 已收纳已注册待授权账号: {email}")
        elif result.get("configured"):
            log(f"  Mail Admin 已注册待授权账号写入未完成: {email}", "warn")
        return result
    except OpusMailError as e:
        log(f"  Mail Admin 已注册待授权账号写入失败: {e}", "warn")
    except Exception as e:
        log(f"  Mail Admin 已注册待授权账号写入异常: {e}", "warn")
    return {"imported": False}


def clear_email_registration_completed(email, *, reason=""):
    """Drop a false-positive registered flag so the next run redoes signup."""
    key = normalize_email(email)
    if not key:
        return False
    data = load_email_stage_state()
    existing = data.get(key) if isinstance(data.get(key), dict) else {}
    if not existing or existing.get("registered") is not True:
        return False
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    existing = dict(existing)
    existing["registered"] = False
    existing["registrationClearedAt"] = now
    existing["updatedAt"] = now
    if reason:
        existing["registrationClearedReason"] = str(reason)[:240]
    data[key] = existing
    save_email_stage_state(data)
    return True


def move_mail_account(email, target="pending", label="待授权分组"):
    try:
        payload = {
            "identifiersText": str(email or "").strip(),
        }
        target_text = str(target or "").strip()
        if target_text in {"pending", "success", "bad", "half", "registered", "used", "ok", "failed", "badmail"}:
            payload["target"] = target_text
        elif target_text:
            payload["targetGroupName"] = target_text
        else:
            payload["target"] = "pending"
        api("POST", "/api/outlook-email/accounts/move", payload)
        log(f"  邮箱已移到{label}: {email} -> {target_text or 'pending'}")
        return True
    except Exception as e:
        log(f"  邮箱移动到{label}失败: {e}", "warn")
        return False

def move_registered_mail_to_new_group(email):
    # Only call this after registration actually finished. Pending is "待授权", not "已写入成功".
    return move_mail_account(email, target="pending", label="待授权分组")

def restore_mail_to_source_group(email, source_group=""):
    group = str(source_group or os.getenv("UC_SIGNUP_MAIL_SOURCE_GROUP", "") or "").strip()
    if not group:
        return False
    return move_mail_account(email, target=group, label=f"来源分组({group})")

def load_cf_clearance_cache():
    try:
        data = json.loads(CF_CLEARANCE_CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_cf_clearance_cache(data):
    CF_CLEARANCE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CF_CLEARANCE_CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def clearance_host_for_url(target_url):
    host = (urlparse(str(target_url or "")).hostname or "").lower()
    return host or "chatgpt.com"

def clearance_cache_key(target_url):
    return f"{PROXY or 'direct'}|{clearance_host_for_url(target_url)}"

def cookie_expiry_timestamp(cookie):
    for key in ("expiry", "expires", "expirationDate"):
        value = cookie.get(key) if isinstance(cookie, dict) else None
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0

def cached_clearance_still_valid(entry):
    if not isinstance(entry, dict):
        return False
    cookies = entry.get("cookies")
    if not isinstance(cookies, list) or not cookies:
        return False
    expires_at = float(entry.get("expiresAt") or 0)
    if expires_at and expires_at <= time.time() + 60:
        return False
    return True

# 加载 .env
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            key = k.strip()
            if key == "ADMIN_PASSWORD":
                os.environ.setdefault(key, v.strip())

API = os.getenv("UC_SIGNUP_API_BASE", os.getenv("API_BASE", API)).rstrip("/")
PROXY = os.getenv("UC_SIGNUP_PROXY", os.getenv("BROWSER_PROXY", os.getenv("PROXY", app_config_value("UC_SIGNUP_PROXY", app_config_value("BROWSER_PROXY", PROXY))))).strip()
PROXY_REGION = str(os.getenv("UC_SIGNUP_PROXY_REGION", "") or "").strip().upper()
PW = os.getenv("SIGNUP_PASSWORD", app_config_value("SIGNUP_PASSWORD", PW))
NAME = os.getenv("SIGNUP_NAME", app_config_value("SIGNUP_NAME", NAME))
AGE = os.getenv("SIGNUP_AGE", app_config_value("SIGNUP_AGE", AGE))
DISPLAY = os.getenv("UC_SIGNUP_DISPLAY", os.getenv("BROWSER_DISPLAY", app_config_value("BROWSER_DISPLAY", DISPLAY)))
AUTH_ONLY = os.getenv("UC_SIGNUP_AUTH_ONLY", "false").strip().lower() in {"1", "true", "yes", "on"}
GET_REFRESH_TOKEN = os.getenv("UC_SIGNUP_GET_REFRESH_TOKEN", "true").strip().lower() in {"1", "true", "yes", "on"}
MANUAL_MODE = os.getenv("UC_SIGNUP_MANUAL_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
UC_SIGNUP_FINGERPRINT_ENABLED = os.getenv(
    "UC_SIGNUP_FINGERPRINT_ENABLED",
    app_config_value("UC_SIGNUP_FINGERPRINT_ENABLED", "true"),
).strip().lower() in {"1", "true", "yes", "on"}
KEEP_BROWSER_ON_FAILURE = os.getenv("UC_SIGNUP_KEEP_BROWSER_ON_FAILURE", str(KEEP_BROWSER_ON_FAILURE)).strip().lower() in {"1", "true", "yes", "on"}
KEEP_BROWSER_SECONDS = int(os.getenv("UC_SIGNUP_KEEP_BROWSER_SECONDS", str(KEEP_BROWSER_SECONDS)) or "0")
KEEP_BROWSER_MAX_SECONDS = int(os.getenv("UC_SIGNUP_KEEP_BROWSER_MAX_SECONDS", str(KEEP_BROWSER_MAX_SECONDS)) or "900")
PROFILE_BASE_DIR = Path(os.getenv("UC_SIGNUP_PROFILE_BASE_DIR", str(PROFILE_BASE_DIR))).expanduser()
CHROME_BINARY = detect_chrome_binary()
CHROME_VERSION = detect_chrome_version(CHROME_BINARY)
CHROMEDRIVER_BINARY = detect_chromedriver_binary()

# ── 异常类 ──────────────────────────────────────────────
class StepError(Exception):
    """可重试的步骤错误"""
    pass

class FatalError(Exception):
    """不可恢复的错误"""
    pass

class ApiError(Exception):
    """内部 API 调用失败"""
    pass

class PhoneRetry(Exception):
    """当前手机号不可用，需要同一邮箱换号重试"""
    def __init__(
        self,
        message,
        *,
        cancel_phone=False,
        hold_phone=False,
        return_to_phone=False,
        stop_account=False,
    ):
        super().__init__(message)
        self.cancel_phone = cancel_phone
        self.hold_phone = hold_phone
        self.return_to_phone = return_to_phone
        self.stop_account = stop_account

class BrowserBlocked(Exception):
    """浏览器或代理被风控/挑战卡住，不应按手机号失败处理"""
    pass

class AuthSessionEnded(Exception):
    """授权链接会话过期，可在未提交手机号时重新取授权链接。"""
    pass

# ── 主类 ────────────────────────────────────────────────
class SignupBot:
    SIGNUP_URL = "https://chatgpt.com/auth/login?intent=signup"
    OPENAI_AUTH_HOSTS = {
        "chatgpt.com",
        "www.chatgpt.com",
        "auth.openai.com",
        "chat.openai.com",
        "openai.com",
        "www.openai.com",
    }
    THIRD_PARTY_AUTH_HOST_SUFFIXES = (
        "apple.com",
        "google.com",
        "microsoftonline.com",
        "live.com",
    )

    def __init__(self, email=""):
        self.d = None
        self.requested_email = str(email or "").strip()
        self.current_email = self.requested_email
        self.display_name = signup_display_name(self.current_email)
        self.signup_age = signup_age(self.current_email)
        self.signup_password = saved_signup_password(self.current_email) or PW
        self.cf_clearance = None
        self.phone_submitted = False
        self.phone_code_submitted = False
        self.used_email_codes = set()
        self.used_email_code_keys = set()
        self.email_code_not_before = {}
        self.failure_kind = ""
        self.fingerprint = None
        self.flow_stage = "initialized"
        self.resume_state = {}
        self._network_log_seen = set()
        self._network_request_urls = {}
        self._auth_response_events = []

    @staticmethod
    def _safe_network_url(value):
        """Return only scheme/host/path; do not persist query strings or credentials."""
        try:
            parsed = urlparse(str(value or ""))
            if not parsed.scheme or not parsed.hostname:
                return ""
            return f"{parsed.scheme}://{parsed.hostname}{(parsed.path or '/')[:180]}"
        except Exception:
            return ""

    def capture_network_diagnostics(self, label=""):
        """Drain Chromium performance events and log sanitized auth request details."""
        if not UC_SIGNUP_NETWORK_DIAGNOSTICS or not self.d:
            return
        try:
            events = self.d.get_log("performance") or []
        except Exception:
            events = []
        for item in events:
            try:
                message = json.loads(item.get("message", "{}")).get("message", {})
                method = str(message.get("method") or "")
                params = message.get("params") or {}
                if method == "Network.requestWillBeSent":
                    request = params.get("request") or {}
                    request_id = str(params.get("requestId") or "")
                    url = self._safe_network_url(request.get("url"))
                    if request_id and url:
                        self._network_request_urls[request_id] = url
                elif method == "Network.responseReceived":
                    response = params.get("response") or {}
                    url = self._safe_network_url(response.get("url"))
                    status = response.get("status")
                    if not url or not any(host in url for host in ("openai.com/", "chatgpt.com/")):
                        continue
                    self._auth_response_events.append({
                        "at": time.time(),
                        "url": url,
                        "status": int(status or 0),
                        "type": str(params.get("type") or ""),
                    })
                    self._auth_response_events = self._auth_response_events[-200:]
                    important_path = any(fragment in url for fragment in (
                        "/api/accounts/",
                        "/api/auth/signin/openai",
                        "/api/auth/callback",
                        "/api/auth/providers",
                        "/api/auth/csrf",
                        "/email-verification",
                        "/create-account/",
                        "/phone-verification",
                    ))
                    # Retain all response events for flow decisions, but print
                    # only the auth chain and failures. Static assets plus
                    # CES/AWE telemetry previously buried the useful steps.
                    if int(status or 0) < 400 and not important_path:
                        continue
                    key = ("response", url, str(status), str(params.get("type") or ""))
                    if key in self._network_log_seen:
                        continue
                    self._network_log_seen.add(key)
                    level = "warn" if int(status or 0) >= 400 else "info"
                    log(
                        f"  [网络诊断]{' '+label if label else ''} response "
                        f"{url} status={status} type={params.get('type') or '-'}", level,
                    )
                elif method == "Network.loadingFailed":
                    request_id = str(params.get("requestId") or "")
                    error_text = str(params.get("errorText") or "unknown")[:120]
                    key = ("failed", request_id, error_text)
                    if key in self._network_log_seen:
                        continue
                    self._network_log_seen.add(key)
                    log(
                        f"  [网络诊断]{' '+label if label else ''} loadingFailed "
                        f"url={self._network_request_urls.get(request_id, '-') } "
                        f"request={request_id[:32] or '-'} error={error_text} "
                        f"canceled={bool(params.get('canceled'))} type={params.get('type') or '-'}", "warn",
                    )
            except Exception:
                continue
        try:
            resources = self.d.execute_script("""
                return Array.from(performance.getEntriesByType('resource') || [])
                  .filter(e => /openai\\.com|chatgpt\\.com/i.test(e.name))
                  .slice(-40).map(e => ({name:e.name, duration:e.duration, transferSize:e.transferSize}));
            """) or []
            for entry in resources:
                url = self._safe_network_url(entry.get("name"))
                if not url:
                    continue
                duration = float(entry.get("duration") or 0)
                important_path = any(fragment in url for fragment in (
                    "/api/accounts/",
                    "/api/auth/signin/openai",
                    "/api/auth/callback",
                    "/email-verification",
                    "/create-account/",
                    "/phone-verification",
                ))
                # Normal 200-400ms RUM calls are noise. Timing output is only
                # useful for a slow auth request or an extremely slow resource.
                if not (important_path and duration >= 1500) and duration < 5000:
                    continue
                key = ("timing", url)
                if key in self._network_log_seen:
                    continue
                self._network_log_seen.add(key)
                log(
                    f"  [网络诊断]{' '+label if label else ''} timing {url} "
                    f"duration={duration:.0f}ms "
                    f"transfer={int(entry.get('transferSize') or 0)}B", "warn",
                )
        except Exception:
            pass

    def recent_auth_responses(self, path_fragment, *, since=0.0):
        fragment = str(path_fragment or "")
        return [
            event for event in self._auth_response_events
            if float(event.get("at") or 0) >= float(since or 0)
            and fragment in str(event.get("url") or "")
        ]

    def profile_dir(self):
        key = normalize_email(self.current_email or self.requested_email)
        if not key:
            key = "default"
        safe_key = re.sub(r"[^A-Za-z0-9_.@-]+", "_", key)[:120] or "default"
        base = PROFILE_BASE_DIR
        if not base.is_absolute():
            base = ROOT / base
        return base / safe_key

    def checkpoint_browser_state(self, stage=""):
        if stage:
            self.flow_stage = str(stage)
        if not UC_SIGNUP_RESUME_ENABLED or not self.d:
            return {}
        try:
            result = capture_browser_checkpoint(
                self.d,
                self.profile_dir(),
                email=self.current_email or self.requested_email,
                stage=self.flow_stage,
            )
            self.resume_state = {**self.resume_state, **result}
            return result
        except Exception as e:
            log(f"  浏览器断点保存失败: {e}", "warn")
            return {}

    def load_cached_cf_clearance_for_url(self, target_url):
        entry = load_cf_clearance_cache().get(clearance_cache_key(target_url))
        if not cached_clearance_still_valid(entry):
            return None
        host = clearance_host_for_url(target_url)
        log(f"  Clearance 使用缓存: {host}")
        return {
            "cookies": entry.get("cookies") or [],
            "user_agent": entry.get("user_agent") or "",
            "target_url": entry.get("target_url") or target_url,
            "host": entry.get("host") or host,
        }

    def save_cached_cf_clearance_for_url(self, target_url, bundle):
        cookies = bundle.get("cookies") or []
        cookie_expiries = [cookie_expiry_timestamp(cookie) for cookie in cookies]
        cookie_expiries = [value for value in cookie_expiries if value > time.time() + 60]
        fallback_expires = time.time() + max(CF_CLEARANCE_CACHE_SECONDS, 60)
        expires_at = min(cookie_expiries) if cookie_expiries else fallback_expires
        cache = load_cf_clearance_cache()
        cache[clearance_cache_key(target_url)] = {
            "cookies": cookies,
            "user_agent": bundle.get("user_agent") or "",
            "target_url": target_url,
            "host": bundle.get("host") or clearance_host_for_url(target_url),
            "proxy": PROXY or "",
            "createdAt": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "expiresAt": expires_at,
        }
        save_cf_clearance_cache(cache)

    def fetch_cf_clearance_for_url(self, target_url, *, force=False):
        target_url = str(target_url or "").strip() or CF_CLEARANCE_TARGET_URL or "https://chatgpt.com/auth/login?intent=signup"
        if not force:
            cached = self.load_cached_cf_clearance_for_url(target_url)
            if cached:
                return cached
        payload = {
            "cmd": "request.get",
            "url": target_url,
            "maxTimeout": max(CF_CLEARANCE_TIMEOUT_SECONDS, 10) * 1000,
        }
        if PROXY:
            payload["proxy"] = {"url": PROXY}
        request = Request(
            CF_CLEARANCE_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        log(f"  Clearance 预检: {urlparse(target_url).hostname or target_url}")
        try:
            with urlopen(request, timeout=max(CF_CLEARANCE_TIMEOUT_SECONDS, 10) + 30) as resp:
                result = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:
            raise BrowserBlocked(f"Clearance 请求失败: {e}") from e
        if result.get("status") != "ok":
            raise BrowserBlocked(f"Clearance 返回失败: {result.get('message') or result.get('status')}")
        solution = result.get("solution") if isinstance(result.get("solution"), dict) else {}
        cookies = solution.get("cookies") if isinstance(solution.get("cookies"), list) else []
        if not cookies:
            raise BrowserBlocked("Clearance 未返回 cookie")
        host = (urlparse(target_url).hostname or "").lower()
        filtered = [
            cookie for cookie in cookies
            if not host or not cookie.get("domain") or host.endswith(str(cookie.get("domain", "")).lstrip(".").lower())
        ]
        bundle = {
            "cookies": filtered or cookies,
            "user_agent": solution.get("userAgent") or "",
            "target_url": target_url,
            "host": host,
        }
        names = ", ".join(str(cookie.get("name") or "") for cookie in bundle["cookies"] if cookie.get("name"))
        log(f"  Clearance 已获取: {names or 'cookies'}")
        self.save_cached_cf_clearance_for_url(target_url, bundle)
        return bundle

    def merge_cf_clearance_bundle(self, base, extra):
        base = base or {"cookies": [], "user_agent": "", "target_url": "", "host": ""}
        by_key = {}
        for cookie in (base.get("cookies") or []):
            key = (
                str(cookie.get("name") or ""),
                str(cookie.get("domain") or ""),
                str(cookie.get("path") or "/"),
            )
            if not key[0]:
                continue
            by_key[key] = cookie
        for cookie in (extra.get("cookies") or []):
            key = (
                str(cookie.get("name") or ""),
                str(cookie.get("domain") or ""),
                str(cookie.get("path") or "/"),
            )
            if not key[0]:
                continue
            by_key[key] = cookie
        return {
            "cookies": list(by_key.values()),
            "user_agent": extra.get("user_agent") or base.get("user_agent") or "",
            "target_url": extra.get("target_url") or base.get("target_url") or "",
            "host": extra.get("host") or base.get("host") or "",
        }

    def fetch_cf_clearance(self, target_url=None, *, force=False):
        if not CF_CLEARANCE_ENABLED:
            return None
        if not CF_CLEARANCE_API_URL:
            raise BrowserBlocked("已启用 Clearance，但未配置 API 地址")
        primary_url = target_url or CF_CLEARANCE_TARGET_URL or "https://chatgpt.com/auth/login?intent=signup"
        target_urls = [primary_url]
        if "auth.openai.com" not in primary_url:
            target_urls.append("https://auth.openai.com/")

        cookies = []
        seen = set()
        user_agent = ""
        for target_url in target_urls:
            bundle = self.fetch_cf_clearance_for_url(target_url, force=force)
            user_agent = user_agent or str(bundle.get("user_agent") or "")
            for cookie in bundle.get("cookies") or []:
                key = (
                    str(cookie.get("name") or ""),
                    str(cookie.get("domain") or ""),
                    str(cookie.get("path") or "/"),
                )
                if key in seen:
                    continue
                seen.add(key)
                cookies.append(cookie)
        if not cookies:
            raise BrowserBlocked("Clearance 未返回可注入 cookie")
        return {
            "cookies": cookies,
            "user_agent": user_agent,
            "target_url": primary_url,
            "host": (urlparse(primary_url).hostname or "").lower(),
        }

    def ensure_cf_clearance(self, target_url=None):
        if CF_CLEARANCE_ENABLED and self.cf_clearance is None:
            self.cf_clearance = self.fetch_cf_clearance(target_url)
        elif CF_CLEARANCE_ENABLED and target_url:
            extra = self.fetch_cf_clearance(target_url)
            self.cf_clearance = self.merge_cf_clearance_bundle(self.cf_clearance, extra)
        return self.cf_clearance

    def apply_cf_clearance(self):
        bundle = self.cf_clearance
        if not bundle or not self.d:
            return
        try:
            self.d.execute_cdp_cmd("Network.enable", {})
            for cookie in bundle.get("cookies") or []:
                name = str(cookie.get("name") or "").strip()
                value = str(cookie.get("value") or "")
                if not name:
                    continue
                params = {
                    "name": name,
                    "value": value,
                    "domain": str(cookie.get("domain") or f".{bundle.get('host') or 'chatgpt.com'}"),
                    "path": str(cookie.get("path") or "/"),
                    "secure": bool(cookie.get("secure", True)),
                    "httpOnly": bool(cookie.get("httpOnly", False)),
                }
                expires = cookie.get("expiry") or cookie.get("expires")
                if expires:
                    try:
                        params["expires"] = float(expires)
                    except (TypeError, ValueError):
                        pass
                self.d.execute_cdp_cmd("Network.setCookie", params)
            log("  Clearance cookie 已注入浏览器")
        except Exception as e:
            raise BrowserBlocked(f"Clearance cookie 注入失败: {e}") from e

    def refresh_cf_clearance(self, target_url=None):
        if not CF_CLEARANCE_ENABLED:
            return
        refreshed = self.fetch_cf_clearance(target_url, force=True)
        self.cf_clearance = self.merge_cf_clearance_bundle(self.cf_clearance, refreshed)
        self.apply_cf_clearance()


    def _proxy_needs_auth_bridge(self, proxy_url: str) -> bool:
        raw = str(proxy_url or "").strip()
        if not raw or "://" not in raw:
            return False
        try:
            from urllib.parse import urlparse
            parsed = urlparse(raw)
        except Exception:
            return False
        return bool(parsed.username or parsed.password)

    def _ensure_browser_proxy(self) -> str:
        """Chrome cannot consume user:pass in --proxy-server.

        Wrap authenticated upstream proxies with the local traffic-meter bridge
        so Chromium only sees 127.0.0.1:port while auth is injected upstream.
        """
        global PROXY
        raw = str(PROXY or "").strip()
        if not raw:
            raise FatalError("未配置注册代理，已阻止直连浏览器注册")
        if is_blocked_direct_proxy(raw):
            raise FatalError(f"注册代理是 DIRECT 直连端口，已阻止: {raw}")
        if not self._proxy_needs_auth_bridge(raw):
            return raw
        if getattr(self, "_auth_proxy_bridge", None) is not None:
            local = str(getattr(self, "_browser_proxy", "") or "").strip()
            if local:
                return local
        try:
            from tools.traffic_meter.meter_proxy import MeteredProxy
        except Exception as error:
            raise FatalError(
                f"注册代理带账号密码，但本地代理桥接不可用（Chrome 不支持 --proxy-server 内嵌认证）: {error}"
            ) from error
        bridge = MeteredProxy(raw)
        try:
            local = bridge.start()
        except Exception as error:
            raise FatalError(f"启动本地认证代理桥接失败: {error}") from error
        self._auth_proxy_bridge = bridge
        self._browser_proxy = local
        self._upstream_proxy = raw
        log(f"  代理认证桥接: 浏览器 -> {local} -> 上游认证代理")
        PROXY = local
        return local

    def _stop_auth_proxy_bridge(self) -> None:
        bridge = getattr(self, "_auth_proxy_bridge", None)
        if bridge is None:
            return
        try:
            bridge.stop()
        except Exception as error:
            log(f"  关闭本地认证代理桥接失败: {error}", "warn")
        self._auth_proxy_bridge = None

    def launch(self):
        browser_proxy = self._ensure_browser_proxy()
        os.environ["DISPLAY"] = DISPLAY
        opts = uc.ChromeOptions()
        opts.binary_location = CHROME_BINARY
        profile_dir = self.profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        browser_version = str(CHROME_VERSION or "145").strip()
        if browser_version and "." not in browser_version:
            browser_version = f"{browser_version}.0.0.0"
        fingerprint_identity = (
            load_or_create_uc_fingerprint_identity(profile_dir)
            if UC_SIGNUP_FINGERPRINT_ENABLED
            else None
        )
        self.fingerprint = (
            generate_entry_fingerprint(
                "uc_signup",
                seed=fingerprint_identity["seed"],
                preset=fingerprint_identity["preset"],
                browser_version=browser_version or "145.0.0.0",
            )
            if fingerprint_identity
            else None
        )
        if self.fingerprint and PROXY_REGION:
            self.fingerprint = align_fingerprint_locale_to_region(self.fingerprint, PROXY_REGION)
        window_width, window_height = headed_desktop_size()
        self.fingerprint = force_fingerprint_screen(
            self.fingerprint,
            window_width,
            window_height,
        )
        fingerprint = self.fingerprint or {}
        clearance = None
        user_agent = str(fingerprint.get("user_agent") or "").strip()
        if fingerprint:
            clearance = self.ensure_cf_clearance()
            clearance_user_agent = str((clearance or {}).get("user_agent") or "").strip()
            clearance_major = browser_major_from_user_agent(clearance_user_agent)
            fingerprint_major = browser_major_from_user_agent(user_agent)
            if clearance_major and fingerprint_major and clearance_major != fingerprint_major:
                log(
                    f"  Clearance UA Chrome {clearance_major} 与本机 Chrome {fingerprint_major} 不一致，"
                    "已丢弃外部浏览器 cookie",
                    "warn",
                )
                clearance = None
                self.cf_clearance = None
        elif CF_CLEARANCE_ENABLED:
            # Native mode must not import cookies generated by a different browser build.
            # Doing so previously replaced Chromium 150's UA with FlareSolverr's Chrome
            # 142 UA and removed native Client Hints, creating an internally impossible
            # browser fingerprint.
            log("  原生浏览器模式：跳过外部 Clearance cookie/UA 注入", "warn")
            self.cf_clearance = None
        args = chromium_launch_args(
            fingerprint,
            user_data_dir=profile_dir,
            proxy=browser_proxy,
            user_agent=user_agent,
        )
        # The real Xvfb desktop is the single source of truth for both the
        # native window and JS-visible screen metrics. Fingerprint templates
        # must not resize the headed browser differently from one task to the next.
        args = [arg for arg in args if not str(arg).startswith("--window-size=")]
        args.append(f"--window-size={window_width},{window_height}")
        if "--enable-unsafe-swiftshader" not in args:
            args.append("--enable-unsafe-swiftshader")
        if not fingerprint:
            args.append("--lang=en-US")
        for a in args:
            opts.add_argument(a)
        if UC_SIGNUP_NETWORK_DIAGNOSTICS:
            try:
                opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
            except Exception as error:
                log(f"  网络诊断能力启用失败: {type(error).__name__}", "warn")
        kwargs = {"options": opts, "version_main": CHROME_VERSION}
        if CHROMEDRIVER_BINARY:
            kwargs["driver_executable_path"] = CHROMEDRIVER_BINARY
        try:
            self.d = uc.Chrome(**kwargs)
        except Exception as e:
            raise BrowserBlocked(f"浏览器启动失败: {e}") from e
        try:
            self.d.set_window_rect(
                x=0,
                y=0,
                width=window_width,
                height=window_height,
            )
        except Exception as e:
            log(f"  窗口尺寸设置失败: {e}", "warn")
        if fingerprint:
            failures = apply_chromium_fingerprint(
                self.d,
                fingerprint,
                override_user_agent=True,
            )
            if failures:
                log(f"  指纹 CDP 有 {len(failures)} 项未应用: {'; '.join(failures[:2])}", "warn")
        log(f"  Chrome profile: {profile_dir}")
        if fingerprint:
            summary = fingerprint_summary(fingerprint)
            log(f"  Fingerprint: {json.dumps(summary, ensure_ascii=False)}")
        else:
            log("  Fingerprint: native Chromium runtime (no navigator/WebGL spoofing)")

        runtime = self.d.execute_script("""
            const canvas = document.createElement('canvas');
            const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
            return {
                userAgent: navigator.userAgent,
                webdriver: navigator.webdriver,
                platform: navigator.platform,
                language: navigator.language,
                languages: Array.from(navigator.languages || []),
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                screen: `${screen.width}x${screen.height}`,
                outer: `${outerWidth}x${outerHeight}`,
                webgl: Boolean(gl),
                userAgentData: navigator.userAgentData ? navigator.userAgentData.toJSON() : null,
            };
        """)
        actual_major = browser_major_from_user_agent(runtime.get("userAgent"))
        if actual_major and CHROME_VERSION and actual_major != CHROME_VERSION:
            raise BrowserBlocked(
                f"浏览器 UA 主版本不一致: runtime={actual_major}, binary={CHROME_VERSION}"
            )
        if runtime.get("webdriver") is not False:
            raise BrowserBlocked(f"浏览器 webdriver 暴露: {runtime.get('webdriver')!r}")
        if not runtime.get("webgl"):
            raise BrowserBlocked("浏览器 WebGL 不可用")
        log(f"  Browser runtime: {json.dumps(runtime, ensure_ascii=False)}")
        if UC_SIGNUP_RESUME_ENABLED:
            try:
                restored = restore_browser_checkpoint(self.d, profile_dir)
                self.resume_state = restored
                if restored.get("restored"):
                    self.flow_stage = str(restored.get("stage") or "restored")
                    log(
                        f"  浏览器断点已恢复: stage={self.flow_stage}, "
                        f"tabs={restored.get('tabs', 0)}, cookies={restored.get('cookies', 0)}"
                    )
            except Exception as e:
                log(f"  浏览器断点恢复失败，继续使用 Chrome profile: {e}", "warn")

    # ── 页面等待 ────────────────────────────────────────
    def _sleep(self, seconds):
        time.sleep(seconds)

    def wait_ready(self, timeout=10):
        """等页面完全加载"""
        try:
            WebDriverWait(self.d, timeout).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except TimeoutException: pass
        time.sleep(1)

    def wait_url_contains(self, keyword, timeout=30):
        """等 URL 包含关键字"""
        for _ in range(timeout):
            if keyword in self.d.current_url: return
            time.sleep(1)
        raise StepError(f"URL等待超时: {keyword}")

    def is_error_page(self):
        """检测是否错误页（含日文/多语言 unknown error）"""
        title = str(getattr(self.d, "title", "") or "")
        text = " ".join(self.visible_text().split())
        hay = f"{title} {text}".lower()
        needles = (
            "oops",
            "something went wrong",
            "unknown error",
            "failed to fetch",
            "unexpected error",
            "route error",
            "出错了",
            "发生错误",
            "不明なエラー",
            "エラーが発生",
            "もう一度試す",
            "try again",
            "retry",
        )
        return any(n.lower() in hay for n in needles)

    def visible_text(self):
        """Best-effort visible page text for detecting hard phone rejections."""
        try:
            return self.d.execute_script("return document.body ? document.body.innerText : ''") or ""
        except Exception:
            return ""

    def page_signature(self):
        """Stable page identity: route + semantic controls, not changing body copy."""
        url = self._safe_network_url(getattr(self.d, "current_url", ""))
        title = " ".join(str(getattr(self.d, "title", "") or "").split())[:160]
        controls = []
        try:
            elements = self.d.find_elements(
                By.CSS_SELECTOR,
                "input, button, select, textarea, a, [role=button], [contenteditable=true]",
            )
        except Exception:
            elements = []
        for element in elements[:40]:
            try:
                if not element.is_displayed():
                    continue
                semantic = "|".join(str(element.get_attribute(name) or "")[:80] for name in (
                    "type", "name", "role", "aria-label", "data-testid", "data-type", "placeholder"
                ))
                label = element_action_label(element)[:100]
                controls.append(f"{getattr(element, 'tag_name', '')}|{semantic}|{label}")
            except Exception:
                continue
        if not controls:
            fallback = re.sub(r"\d+", "#", " ".join(self.visible_text().split())[:300])
            controls.append(fallback)
        return hashlib.sha256(f"{url}|{title}|{'||'.join(controls)}".encode("utf-8")).hexdigest()

    @staticmethod
    def _redact_failure_text(value, *, email="", password=""):
        text = " ".join(str(value or "").split())
        if email:
            text = re.sub(re.escape(str(email)), "[EMAIL]", text, flags=re.IGNORECASE)
        if password:
            text = text.replace(str(password), "[PASSWORD]")
        text = re.sub(r"\b\d{6}\b", "[OTP]", text)
        text = re.sub(r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)", "[PHONE]", text)
        text = re.sub(r"\beyJ[A-Za-z0-9_-]{20,}(?:\.[A-Za-z0-9_-]{10,}){1,2}\b", "[TOKEN]", text)
        return text[:500]

    def capture_failure_artifact(self, stage="", reason=""):
        """Save a privacy-safe page inventory that survives failed profile cleanup."""
        if not self.d:
            return {}
        email = self.current_email or self.requested_email or ""
        safe_url = self._safe_network_url(getattr(self.d, "current_url", ""))
        try:
            page_kind = self.classify_auth_page()
        except Exception:
            page_kind = "unknown"
        try:
            features = self.auth_page_features()
            semantic_features = {
                "roles": sorted(features.get("roles") or []),
                "actionKinds": self.semantic_action_kinds(features.get("actions") or ()),
            }
        except Exception:
            semantic_features = {"roles": [], "actionKinds": []}
        timestamp = datetime.now(BEIJING_TZ)
        account_key = hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()[:16]
        kind_key = re.sub(r"[^a-z0-9_-]+", "-", page_kind.lower())[:32] or "unknown"
        artifact_dir = FAILURE_ARTIFACT_BASE_DIR / timestamp.strftime("%Y%m%d") / (
            f"{timestamp:%H%M%S}-{account_key}-{kind_key}"
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        recent_responses = []
        for event in self._auth_response_events[-12:]:
            try:
                safe_event_url = self._safe_network_url(event.get("url"))
                if not safe_event_url:
                    continue
                recent_responses.append({
                    "at": event.get("at"),
                    "path": urlparse(safe_event_url).path[:180],
                    "status": int(event.get("status") or 0),
                    "type": str(event.get("type") or "")[:30],
                })
            except Exception:
                continue
        payload = {
            "capturedAtBeijing": timestamp.isoformat(timespec="seconds"),
            "stage": str(stage or self.flow_stage or "unknown")[:120],
            "reason": self._redact_failure_text(reason, email=email, password=self.signup_password),
            "pageKind": page_kind,
            "semanticFeatures": semantic_features,
            "url": safe_url,
            "title": self._redact_failure_text(
                getattr(self.d, "title", ""), email=email, password=self.signup_password
            ),
            "state": {
                "registered": email_registration_completed(email),
                "phoneSubmitted": bool(self.phone_submitted),
                "phoneCodeSubmitted": bool(self.phone_code_submitted),
            },
            "recentAuthResponses": recent_responses,
        }
        metadata_path = artifact_dir / "page.json"
        metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"  已保存精简故障记录: {artifact_dir}", "warn")
        return {"directory": str(artifact_dir), "pageKind": page_kind, "screenshot": False}

    @staticmethod
    def _jwt_email(token):
        try:
            part = str(token or "").split(".")[1]
            part += "=" * (-len(part) % 4)
            payload = json.loads(base64.urlsafe_b64decode(part.encode("ascii")))
            profile = payload.get("https://api.openai.com/profile")
            profile = profile if isinstance(profile, dict) else {}
            return normalize_email(payload.get("email") or profile.get("email"))
        except Exception:
            return ""

    def extract_chatgpt_web_session(self, email=""):
        """Read ChatGPT Web session material without exposing it in logs."""
        if not self.d:
            return {}
        expected_email = normalize_email(email or self.current_email or self.requested_email)
        original_handle = None
        session_handle = None
        try:
            original_handle = self.d.current_window_handle
            existing_handles = set(self.d.window_handles)
            self.d.execute_script("window.open('about:blank', '_blank');")
            WebDriverWait(self.d, 5).until(lambda driver: len(driver.window_handles) > len(existing_handles))
            session_handle = next(handle for handle in self.d.window_handles if handle not in existing_handles)
            self.d.switch_to.window(session_handle)
            self.d.get("https://chatgpt.com/api/auth/session")
            WebDriverWait(self.d, 12).until(
                lambda driver: bool((driver.find_element(By.TAG_NAME, "body").text or "").strip())
            )
            raw = self.d.find_element(By.TAG_NAME, "body").text.strip()
            session = json.loads(raw)
            if not isinstance(session, dict):
                return {}
            access_token = str(session.get("accessToken") or session.get("access_token") or "").strip()
            if not access_token:
                return {}
            user = session.get("user") if isinstance(session.get("user"), dict) else {}
            session_email = normalize_email(user.get("email") or session.get("email")) or self._jwt_email(access_token)
            if expected_email and session_email != expected_email:
                log("  Web Session 账号与当前邮箱不一致，拒绝保存 AT", "warn")
                return {}
            session_token = ""
            for cookie in self.d.get_cookies() or []:
                if str(cookie.get("name") or "") in {
                    "__Secure-next-auth.session-token", "next-auth.session-token", "__Secure-authjs.session-token"
                }:
                    session_token = str(cookie.get("value") or "").strip()
                    if session_token:
                        break
            return {
                "access_token": access_token,
                "session_token": session_token,
                "email": session_email or expected_email,
                "credentialKind": "chatgpt_web_session",
                "statusMessage": "ChatGPT Web AT 已保存；OAuth RT 尚未完成，需重新授权",
            }
        except Exception:
            return {}
        finally:
            try:
                if session_handle and session_handle in self.d.window_handles:
                    self.d.close()
                if original_handle and original_handle in self.d.window_handles:
                    self.d.switch_to.window(original_handle)
            except Exception:
                pass

    def persist_chatgpt_web_session(self, email=""):
        material = self.extract_chatgpt_web_session(email)
        if not material.get("access_token"):
            return {"imported": False, "hasAccessToken": False}
        try:
            result = OpusMailClient.from_project(ROOT).import_openai_oauth(material, email=email)
        except Exception as error:
            log(f"  Mail Admin 保存 Web AT 失败: {type(error).__name__}: {error}", "warn")
            return {"imported": False, "hasAccessToken": True}
        if result.get("imported"):
            mark_email_web_session_saved(email, has_session_token=bool(material.get("session_token")))
            log(
                "  Mail Admin 已保存 ChatGPT Web AT"
                + (" / Session Token" if material.get("session_token") else "")
                + "；OAuth RT 待补"
            )
        return result

    def finalize_failed_account(self, email, reason, *, retryable=True):
        try:
            return self._finalize_failed_account(email, reason, retryable=retryable)
        except Exception as error:
            log(f"  失败收尾异常: {type(error).__name__}: {error}", "warn")
            return {}

    def _finalize_failed_account(self, email, reason, *, retryable=True):
        """One failure exit for signup, OAuth phone walls and future unknown pages."""
        artifact = self.capture_failure_artifact(self.flow_stage, reason) if self.d else {}
        registered = email_registration_completed(email)
        web_result = {"imported": False, "hasAccessToken": False}
        stage_record = email_stage_record(email)
        oauth_already_saved = bool(stage_record.get("oauthStoredInMailAdmin"))
        web_already_saved = bool(stage_record.get("webAccessTokenStoredInMailAdmin"))
        if registered:
            if not oauth_already_saved and not web_already_saved:
                archive_registered_email_in_mail_admin(
                    email,
                    self.signup_password,
                    str(reason or "已注册，OAuth / 手机号未完成")[:500],
                )
                web_result = self.persist_chatgpt_web_session(email) if self.d else web_result
        stage = (
            "oauth_saved_downstream_pending" if oauth_already_saved
            else
            "web_session_saved_rt_pending" if web_already_saved or web_result.get("imported")
            else "registered_oauth_pending" if registered
            else "signup_retryable" if retryable
            else "signup_failed"
        )
        mark_email_flow_state(
            email,
            stage,
            retryable=retryable,
            reason=reason,
            registered=registered,
            hasWebAccessToken=bool(web_already_saved or web_result.get("hasAccessToken")),
            hasOAuthRefreshToken=bool(stage_record.get("oauthRefreshTokenStoredInMailAdmin")),
            failureArtifact=str(artifact.get("directory") or ""),
        )
        return {"registered": registered, "webSession": web_result, "artifact": artifact}

    def account_already_exists_hint(self):
        text = " ".join(self.visible_text().lower().split())
        checks = [
            ("already", "account"),
            ("already", "registered"),
            ("already", "signed up"),
            ("log in", "instead"),
            ("welcome back",),
            ("sign in", "instead"),
            ("已有", "账号"),
            ("已经", "注册"),
        ]
        return any(all(term in text for term in terms) for terms in checks)

    def visible_elements(self, selector):
        try:
            return [
                element for element in self.d.find_elements(By.CSS_SELECTOR, selector)
                if element.is_displayed()
            ]
        except Exception:
            return []

    def has_visible_input(self, selectors):
        return any(self.visible_elements(selector) for selector in selectors)

    def email_input_visible(self):
        return self.has_visible_input([
            "input[type=email]",
            "input[name=email]",
            "input[name=username]",
            "input[autocomplete=email]",
            "input#email-input",
        ])

    def email_input_matches(self, email):
        """Return whether a visible email field still contains the expected address.

        The ChatGPT auth page can re-render the login form after a slow or failed
        request.  The URL and visible copy remain almost unchanged, but React
        clears the field.  Tracking only ``email_submitted`` then makes the retry
        loop click an empty form until its repetition guard fires.
        """
        expected = str(email or "").strip().casefold()
        if not expected:
            return False
        selectors = (
            "input[type=email]",
            "input[name=email]",
            "input[name=username]",
            "input[autocomplete=email]",
            "input#email-input",
        )
        seen = set()
        for selector in selectors:
            for element in self.visible_elements(selector):
                element_id = getattr(element, "id", None) or id(element)
                if element_id in seen:
                    continue
                seen.add(element_id)
                try:
                    value = str(element.get_attribute("value") or "").strip().casefold()
                except Exception:
                    continue
                if value == expected:
                    return True
        return False

    def password_input_visible(self):
        return self.has_visible_input([
            "input[name=new-password]",
            "input[autocomplete='new-password']",
            "input[name=current-password]",
            "input[autocomplete='current-password']",
            "input[type=password]",
        ])

    def password_input_matches(self):
        expected = str(self.signup_password or "")
        if not expected:
            return False
        selectors = (
            "input[name=new-password]",
            "input[autocomplete='new-password']",
            "input[name=current-password]",
            "input[autocomplete='current-password']",
            "input[type=password]",
        )
        seen = set()
        for selector in selectors:
            for element in self.visible_elements(selector):
                element_id = getattr(element, "id", None) or id(element)
                if element_id in seen:
                    continue
                seen.add(element_id)
                try:
                    if str(element.get_attribute("value") or "") == expected:
                        return True
                except Exception:
                    continue
        return False

    def password_input_diagnostics(self):
        """Describe password form state without exposing the password itself."""
        fields = self.visible_elements(
            "input[name=new-password], input[autocomplete='new-password'], "
            "input[name=current-password], input[autocomplete='current-password'], input[type=password]"
        )
        if not fields:
            return "field=missing"
        element = fields[0]
        try:
            value_length = len(str(element.get_attribute("value") or ""))
            disabled = bool(element.get_attribute("disabled"))
            aria_invalid = str(element.get_attribute("aria-invalid") or "")
            validation = str(
                self.d.execute_script(
                    "return arguments[0].validationMessage || '';",
                    element,
                )
                or ""
            )
        except Exception as error:
            return f"field=stale error={type(error).__name__}"
        return (
            f"field=present length={value_length}/{len(str(self.signup_password or ''))} "
            f"disabled={disabled} ariaInvalid={aria_invalid or '-'} "
            f"validation={validation[:80] or '-'}"
        )

    def looks_like_phone_input(self, element):
        try:
            semantic = " ".join(str(element.get_attribute(name) or "") for name in (
                "name", "id", "autocomplete", "aria-label", "placeholder"
            )).lower()
            input_type = str(element.get_attribute("type") or "").lower()
            inputmode = str(element.get_attribute("inputmode") or "").lower()
            data_type = str(element.get_attribute("data-type") or "").lower().strip()
            maxlength_raw = str(element.get_attribute("maxlength") or "").strip()
            maxlength = int(maxlength_raw) if maxlength_raw.isdigit() else 0
        except Exception:
            return False

        if any(term in semantic for term in ("code", "otp", "one-time", "verification", "passcode", "security")):
            return False
        if maxlength and maxlength <= 8 and inputmode in {"numeric", "decimal"}:
            return False
        return (
            "phone" in semantic
            or "tel" in semantic
        )

    def phone_code_page_visible(self):
        text = " ".join(self.visible_text().split()).lower()
        return any(marker in text for marker in (
            "check your phone",
            "enter the verification code",
            "verification code we just sent",
            "resend whatsapp message",
            "resend text message",
        ))

    def phone_input_elements(self):
        if self.phone_code_page_visible():
            return []
        selectors = [
            "input[type=tel]",
            "input[name=phoneNumberInput]",
            "input[autocomplete=tel]",
        ]
        inputs = []
        seen = set()
        for selector in selectors:
            for element in self.visible_elements(selector):
                element_id = element.id
                if element_id in seen:
                    continue
                seen.add(element_id)
                if self.looks_like_phone_input(element):
                    inputs.append(element)
        return inputs

    def phone_input_visible(self):
        return bool(self.phone_input_elements())

    def phone_verification_prompt_visible(self):
        url = str(getattr(self.d, "current_url", "") or "").lower()
        text = " ".join(self.visible_text().split()).lower()
        title = str(getattr(self.d, "title", "") or "").lower()
        combined = f"{title} {text}"
        if self.phone_input_visible():
            return True
        if "phone-verification" in url:
            return True
        if "check your phone" in combined:
            return True
        if "verify your phone number" in combined:
            return True
        if "phone verification" in combined or "verify phone" in combined:
            return True
        if "phone number" in combined and any(word in combined for word in ("verify", "verification", "confirm")):
            return True
        return False

    def whatsapp_code_prompt_visible(self):
        if not self.phone_code_page_visible():
            return False
        text = " ".join(self.visible_text().split()).lower()
        title = str(getattr(self.d, "title", "") or "").lower()
        combined = f"{title} {text}"
        return "whatsapp" in combined and any(
            marker in combined
            for marker in ("sent on whatsapp", "sent to whatsapp", "whatsapp message", "resend whatsapp")
        )

    def looks_like_code_input(self, element):
        try:
            semantic = " ".join(str(element.get_attribute(name) or "") for name in (
                "name", "id", "autocomplete", "aria-label", "placeholder"
            )).lower()
            transport = " ".join(str(element.get_attribute(name) or "") for name in (
                "type", "inputmode"
            )).lower()
            maxlength_raw = str(element.get_attribute("maxlength") or "").strip()
            maxlength = int(maxlength_raw) if maxlength_raw.isdigit() else 0
        except Exception:
            return False

        reject_terms = ("age", "birth", "phone", "tel", "email", "name", "username")
        if any(term in semantic for term in reject_terms):
            return False

        accept_terms = ("code", "otp", "one-time", "verification", "verify", "passcode", "security")
        if any(term in semantic for term in accept_terms):
            return True
        if maxlength == 1 and "numeric" in transport:
            return True
        if 4 <= maxlength <= 8 and ("numeric" in transport or "text" in transport or "tel" in transport):
            return True
        return False

    def code_input_elements(self):
        selectors = [
            "input[name=code]",
            "input[autocomplete='one-time-code']",
            "input[inputmode=numeric]",
            "input[type=tel][maxlength]",
            "input[type=text][maxlength]",
        ]
        inputs = []
        seen = set()
        for selector in selectors:
            for element in self.visible_elements(selector):
                element_id = element.id
                if element_id in seen:
                    continue
                seen.add(element_id)
                if self.looks_like_code_input(element):
                    inputs.append(element)
        return inputs

    def code_input_visible(self):
        if self.code_input_elements():
            return True
        if self.phone_verification_prompt_visible():
            return False
        text = self.visible_text().lower()
        return "verification code" in text or "enter code" in text or "验证码" in text

    def phone_code_confirmation_pending(self):
        """True after a phone OTP was submitted and the page now needs its final action.

        The phone-verification route/title can remain unchanged after the OTP input
        disappears.  At that point re-polling/re-filling the OTP is wrong: the page
        must be driven by its current controls and allowed to reach the OAuth callback.
        """
        if not bool(getattr(self, "phone_code_submitted", False)):
            return False
        url = str(getattr(self.d, "current_url", "") or "").lower()
        if "localhost:1455" in url or ("code=" in url and "auth.openai.com" not in url):
            return False
        if self.phone_input_visible() or self.code_input_elements():
            return False
        text = " ".join(self.visible_text().split()).lower()
        title = str(getattr(self.d, "title", "") or "").lower()
        combined = f"{title} {text}"
        return (
            "phone-verification" in url
            or "check your phone" in combined
            or "verification code" in combined
            or "verify your phone" in combined
        )

    def semantic_input_role(self, element):
        """Return the intent of a visible form control from its semantics.

        Auth pages are React variants: URLs and headings can lag behind the
        controls that are actually actionable.  Keep this detector independent
        from route names so the classifier can follow the page's current form.
        """
        try:
            semantic_raw = " ".join(
                str(element.get_attribute(name) or "")
                for name in (
                    "name", "id", "type", "autocomplete", "aria-label",
                    "placeholder", "data-testid", "data-type", "data-segment",
                )
            )
            semantic = re.sub(r"([a-z])([A-Z])", r"\1 \2", semantic_raw).lower()
            input_type = str(element.get_attribute("type") or "").lower()
            inputmode = str(element.get_attribute("inputmode") or "").lower()
            data_type = str(element.get_attribute("data-type") or "").lower().strip()
            maxlength_raw = str(element.get_attribute("maxlength") or "").strip()
            maxlength = int(maxlength_raw) if maxlength_raw.isdigit() else 0
        except Exception:
            return "unknown"
        semantic_words = set(re.findall(r"[a-z0-9]+", re.sub(r"[_-]+", " ", semantic)))
        if data_type in {"month", "day", "year"}:
            return "birthdate"
        if input_type == "password" or "password" in semantic:
            return "password"
        if any(term in semantic for term in (
            "one-time", "otp", "passcode", "verification code", "security code",
            "verification", "verify code", "auth code",
        )):
            return "code"
        if "email" in semantic_words or input_type == "email":
            return "email"
        if any(term in semantic_words or term in semantic for term in ("phone", "tel", "mobile", "telephone")):
            if not (maxlength and maxlength <= 8 and inputmode in {"numeric", "decimal"}):
                return "phone"
        if any(term in semantic for term in (
            "birth", "birthday", "bday", "date-of-birth", "date of birth",
            "dob", "出生", "生日", "生年月日",
        )) or input_type == "date":
            return "birthdate"
        if any(term in semantic_words or term in semantic for term in ("age", "年龄", "年齢")):
            return "age"
        if any(term in semantic_words or term in semantic for term in (
            "fullname", "given", "first", "last", "display", "name", "姓名", "名字",
        )) and "username" not in semantic_words:
            return "name"
        if maxlength == 1 and inputmode in {"numeric", "decimal"}:
            return "code"
        if 4 <= maxlength <= 8 and inputmode in {"numeric", "decimal"}:
            return "code"
        return "unknown"

    def auth_page_features(self):
        """Capture one semantic snapshot used by all auth-page classification.

        The snapshot is intentionally value-free: it records control roles and
        action labels, not entered email/phone/password/OTP values.
        """
        url = str(getattr(self.d, "current_url", "") or "").lower()
        title = " ".join(str(getattr(self.d, "title", "") or "").split())
        text = " ".join(self.visible_text().split())
        lowered = text.lower()
        roles = set()
        action_labels = []
        try:
            controls = self.visible_elements(
                "input, textarea, select, button, a, [role=button], "
                "[role=spinbutton], [contenteditable=true]"
            )
        except Exception:
            controls = []
        for element in controls:
            role = self.semantic_input_role(element)
            if role != "unknown":
                roles.add(role)
            try:
                label = " ".join(element_action_label(element).split())
            except Exception:
                label = ""
            if label:
                action_labels.append(label[:120])
        # Existing role-specific detectors provide compatibility with custom
        # controls that do not expose useful attributes to Selenium.
        for role, detector in (
            ("phone", self.phone_input_visible),
            ("code", lambda: bool(self.code_input_elements())),
            ("password", self.password_input_visible),
            ("email", self.email_input_visible),
        ):
            try:
                if detector():
                    roles.add(role)
            except Exception:
                pass
        profile_roles = {"name", "age", "birthdate"}
        if roles & profile_roles:
            roles.add("profile")
        return {
            "url": url,
            "title": title,
            "text": text,
            "lowered": lowered,
            "roles": roles,
            "actions": tuple(action_labels),
        }

    @staticmethod
    def semantic_action_kinds(labels):
        """Reduce visible action labels to a small, value-free diagnostic set."""
        mapping = (
            ("cancel", ("cancel", "取消")),
            ("back", ("back", "返回")),
            ("resend", ("resend", "重发", "重新发送", "再送信")),
            ("continue", ("continue", "继续", "繼續", "続行", "계속")),
            ("next", ("next", "下一步", "次へ")),
            ("verify", ("verify", "verification", "验证", "確認", "認証")),
            ("confirm", ("confirm", "确认", "確定")),
            ("authorize", ("authorize", "allow", "授权", "允许", "承認")),
            ("finish", ("finish", "完成", "done")),
            ("submit", ("submit", "提交", "送信")),
        )
        kinds = set()
        for label in labels or ():
            lowered = " ".join(str(label or "").split()).lower()
            for kind, terms in mapping:
                if any(term in lowered for term in terms):
                    kinds.add(kind)
        return sorted(kinds)

    def semantic_input_elements(self, role):
        """Return visible controls whose attributes describe ``role``."""
        wanted = str(role or "").strip().lower()
        if not wanted:
            return []
        try:
            controls = self.visible_elements(
                "input:not([type=hidden]):not([type=submit]), textarea, "
                "[role=spinbutton], [contenteditable=true]"
            )
        except Exception:
            return []
        return [element for element in controls if self.semantic_input_role(element) == wanted]

    def fill_semantic_input(self, role, value, *, sensitive=False):
        """Fill the first visible semantic control for a role, if present."""
        elements = self.semantic_input_elements(role)
        if not elements:
            return False
        element = elements[0]
        try:
            ActionChains(self.d).move_to_element(element).click().perform()
            element.send_keys(Keys.CONTROL, "a")
            element.send_keys(Keys.BACKSPACE)
            try:
                element.clear()
            except Exception:
                pass
            for char in str(value):
                element.send_keys(char)
                time.sleep(0.03)
            log(f"  填入: {mask_secret(value) if sensitive else value}")
            return True
        except Exception as error:
            log(f"  语义字段填写失败({role}): {type(error).__name__}", "warn")
            return False

    def signup_done(self):
        """True only when registration clearly left the auth/signup funnel."""
        url = (self.d.current_url or "").lower()
        title = str(getattr(self.d, "title", "") or "").lower()
        if "localhost:1455" in url or ("code=" in url and "auth.openai.com" not in url):
            return True
        if any(token in url for token in (
            "auth.openai.com",
            "email-verification",
            "about-you",
            "create-account",
            "password",
            "phone-verification",
            "choose-an-account",
            "log-in",
            "sign-in",
            "authorize",
        )):
            return False
        if self.is_error_page() or self.transient_auth_error_visible() or self.auth_session_ended_visible():
            return False
        if "chatgpt.com" in url and "/auth/" not in url and "intent=signup" not in url:
            if "error" in title or "不明" in title:
                return False
            return True
        return False

    def classify_auth_page(self):
        """Classify the current page from one semantic snapshot.

        URL fragments are fallback hints only.  Visible fields/actions win so
        localized and React-re-rendered page variants take the correct branch.
        """
        snapshot = self.auth_page_features()
        url = snapshot["url"]
        text = snapshot["text"]
        lowered = snapshot["lowered"]
        roles = snapshot["roles"]
        if "localhost:1455" in url or ("code=" in url and "auth.openai.com" not in url):
            return "oauth_callback"
        if self.cloudflare_challenge_visible():
            return "cloudflare"
        if self.is_error_page() or self.transient_auth_error_visible():
            return "error"
        if self.auth_session_ended_visible():
            return "session_ended"
        if "choose-an-account" in url:
            return "choose_account"
        if is_oauth_consent_page(self.d.current_url, lowered):
            return "oauth_consent"
        if "codex" in lowered and any(
            any(token in label.lower() for token in ("continue", "authorize", "allow", "授权", "继续"))
            for label in snapshot["actions"]
        ):
            return "oauth_consent"
        if "add-email" in url:
            return "add_email"
        if self.phone_code_confirmation_pending():
            return "phone_confirmation"
        if "code" in roles:
            if "phone-verification" in url or self.phone_code_page_visible() or self.phone_submitted:
                return "phone_code"
            return "email_code"
        if "phone" in roles:
            return "phone"
        if "password" in roles:
            return "password"
        if "profile" in roles:
            return "profile"
        if "email" in roles:
            return "email"
        # Text/route fallback is intentionally below visible-control semantics.
        if "phone-verification" in url or self.phone_verification_prompt_visible():
            return "phone"
        if "email-verification" in url or "検証コード" in text or "verification code" in lowered:
            return "email_code"
        if "/password" in url:
            return "password"
        if (
            "about-you" in url
            or registration_profile_url(url)
            or self.visible_elements("input[name=name], input[name=age], input[name=birthday], input[name=birthdate]")
        ):
            return "profile"
        if "log-in" in url or "sign-in" in url:
            return "email"
        if is_plain_chatgpt_home(self.d.current_url):
            return "chatgpt_home"
        if "auth.openai.com" in url or "chatgpt.com/auth" in url:
            return "auth_other"
        return "unknown"

    def phone_rejection_reason(self):
        text = " ".join(self.visible_text().split())
        lowered = text.lower()
        rate_limit = self.phone_verification_rate_limited_reason()
        if rate_limit:
            return rate_limit
        checks = [
            ("whatsapp_only", ("couldn't send a text message", "switched to whatsapp")),
            ("whatsapp_only", ("could not send a text message", "switched to whatsapp")),
            ("already_exists", ("account", "phone number", "already exists")),
            ("already_used", ("already", "phone", "used")),
            ("already_used", ("already", "associated")),
            ("unsupported_sms", ("can't", "sms")),
            ("unsupported_sms", ("cannot", "sms")),
            ("unsupported_sms", ("unable", "sms")),
            ("try_another", ("try", "another", "phone")),
            ("try_another", ("different", "phone")),
            ("invalid_phone", ("invalid", "phone")),
            ("too_many", ("too many", "phone")),
            ("used_cn", ("已", "使用")),
            ("used_cn", ("已", "注册")),
            ("unsupported_cn", ("无法", "短信")),
            ("unsupported_cn", ("不能", "短信")),
            ("try_another_cn", ("换", "手机号")),
        ]
        for label, terms in checks:
            if all(term in lowered for term in terms):
                snippet = text[:240]
                return f"{label}: {snippet}"
        return ""

    def phone_verification_rate_limited_reason(self):
        text = " ".join(self.visible_text().split())
        lowered = text.lower()
        compact = re.sub(r"\s+", "", text)
        checks = (
            ("phone_rate_limited", ("too many", "phone")),
            ("phone_rate_limited", ("too many", "verification")),
            ("phone_rate_limited", ("request", "too many")),
            ("phone_rate_limited", ("請稍後再試",)),
            ("phone_rate_limited", ("请稍后再试",)),
            ("phone_rate_limited", ("稍後再試",)),
            ("phone_rate_limited", ("稍后再试",)),
            ("phone_rate_limited", ("次數過多",)),
            ("phone_rate_limited", ("次数过多",)),
            ("phone_rate_limited", ("要求電話驗證的次數過多",)),
            ("phone_rate_limited", ("请求电话验证的次数过多",)),
        )
        for label, terms in checks:
            haystack = compact if any(any(ord(ch) > 127 for ch in term) for term in terms) else lowered
            if all(term.lower() in haystack for term in terms):
                return f"{label}: {text[:240]}"
        return ""

    def whatsapp_verification_reason(self):
        if self.phone_verification_rate_limited_reason():
            return ""
        text = " ".join(self.visible_text().split())
        lowered = text.lower()
        if "whatsapp" not in lowered:
            return ""
        switched_from_sms = any(
            marker in lowered
            for marker in (
                "couldn't send a text message",
                "could not send a text message",
                "switched to whatsapp",
            )
        )
        if not switched_from_sms and not self.whatsapp_code_prompt_visible():
            return ""
        indicators = (
            "on whatsapp",
            "whatsapp message",
            "resend whatsapp",
            "sent to",
        )
        if switched_from_sms or any(item in lowered for item in indicators):
            return f"whatsapp_only: {text[:240]}"
        return ""

    def proxy_block_reason(self):
        text = " ".join(self.visible_text().split())
        lowered = text.lower()
        checks = [
            ("unable to load site", "vpn"),
            ("try turning it off", "vpn"),
            ("ray id", "unable to load site"),
            ("access denied", "ray id"),
            ("unsupported country",),
            ("blocked", "vpn"),
        ]
        for terms in checks:
            if all(term in lowered for term in terms):
                return text[:240]
        return ""

    def auth_session_ended_visible(self):
        text = " ".join(self.visible_text().split()).lower()
        title = str(getattr(self.d, "title", "") or "").lower()
        raw = f"{title} {text}"
        needles = (
            "session ended",
            "session expired",
            "session has expired",
            "your session has expired",
            "セッションが終了",
            "セッションの有効期限",
            "有効期限が切れ",
            "会话已结束",
            "会话已过期",
            "工作阶段已结束",
            "登入工作階段已結束",
            "세션이 종료",
            "세션이 만료",
            "sesión finalizada",
            "sesión expirada",
            "session terminée",
            "session expirée",
            "sitzung beendet",
            "sitzung abgelaufen",
        )
        return any(n.lower() in raw for n in needles)

    def auth_route_error_visible(self):
        raw_text = " ".join(self.visible_text().split())
        text = raw_text.lower()
        title = str(getattr(self.d, "title", "") or "").lower()
        url = str(getattr(self.d, "current_url", "") or "").lower()
        combined = f"{title} {text}"
        hard_markers = (
            "route error",
            "invalid content type",
            "unexpected token",
            "not valid json",
            "<!doctype",
            "failed to fetch",
            "unknown error",
            "不明なエラー",
            "エラーが発生しました",
        )
        soft_error = any(token in combined for token in ("oops", "something went wrong", "出错了", "不明なエラー", "unknown error"))
        if not soft_error and not any(marker in combined for marker in hard_markers):
            return ""
        if "route error" in text and "invalid content type" in text:
            return raw_text[:240]
        if "email-verification" in url and (
            "invalid content type" in text
            or "failed to fetch" in text
            or "不明なエラー" in raw_text
            or "unknown error" in text
        ):
            return raw_text[:240]
        if any(marker in combined for marker in hard_markers):
            return raw_text[:240]
        if soft_error:
            return raw_text[:240]
        return ""

    def transient_auth_error_visible(self):
        """Unknown/failed-to-fetch style auth errors that should retry instead of blind CTA spam."""
        return self.auth_route_error_visible() or (self.is_error_page() and "email-verification" in str(getattr(self.d, "current_url", "") or "").lower())

    def recover_transient_auth_error(self):
        """Click retry / refresh when OpenAI shows a temporary auth error page."""
        reason = self.transient_auth_error_visible()
        if not reason and not self.is_error_page():
            return False
        log(f"  检测到临时错误页，尝试恢复: {str(reason or self.d.title)[:160]}", "warn")
        retry_labels = (
            "Try again",
            "Retry",
            "Reload",
            "Refresh",
            "もう一度試す",
            "再試行",
            "再読み込み",
            "重试",
            "再试一次",
            "重新加载",
            "다시 시도",
            "Reintentar",
            "Réessayer",
            "Erneut versuchen",
        )
        if self.click_text_element(retry_labels, wait_seconds=2):
            time.sleep(3)
            self.wait_ready(timeout=5)
            return True
        try:
            self.d.refresh()
            time.sleep(4)
            self.wait_ready(timeout=5)
            return True
        except Exception as error:
            log(f"  临时错误页刷新失败: {error}", "warn")
            return False

    def invalid_login_credentials_visible(self):
        text = " ".join(self.visible_text().split()).lower()
        checks = (
            "incorrect email address or password",
            "incorrect password",
            "wrong password",
            "invalid email or password",
            "invalid password",
            "邮箱地址或密码不正确",
            "电子邮件地址或密码不正确",
            "密码不正确",
        )
        for needle in checks:
            if needle in text:
                return text[:240]
        return ""

    def try_one_time_code_login(self, email):
        text = " ".join(self.visible_text().split()).lower()
        if not any(term in text for term in ("one-time code", "一次性", "验证码登录", "일회용 코드")):
            return False
        requested_at = time.time()
        clicked = self.click_text_element([
            "log in with a one-time code",
            "one-time code",
            "一次性验证码",
            "验证码登录",
            "일회용 코드로 로그인",
            "일회용 코드",
        ], wait_seconds=3)
        if not clicked:
            return False
        self.start_email_code_batch(email, requested_at=requested_at, reason="请求一次性登录验证码")
        code = self.poll_email(email)
        if not code:
            raise FatalError("一次性登录邮箱验证码超时")
        log(f"  一次性登录邮箱码: {code}")
        self._step("一次性登录邮箱验证码", lambda: (
            self.fill_code_input(code),
            self.click_primary_action()
        ))
        return True

    def cloudflare_challenge_visible(self):
        text = self.visible_text().lower()
        if "verify you are human" in text or "cloudflare" in text and "privacy" in text:
            return True
        try:
            for frame in self.d.find_elements(By.TAG_NAME, "iframe"):
                src = str(frame.get_attribute("src") or "").lower()
                title = str(frame.get_attribute("title") or "").lower()
                if "challenges.cloudflare.com" in src or "cloudflare" in title or "turnstile" in src:
                    return True
        except Exception:
            pass
        return False

    def solve_cloudflare_challenge(self):
        if not self.cloudflare_challenge_visible():
            return True
        if CF_CLEARANCE_ENABLED:
            try:
                current_url = str(getattr(self.d, "current_url", "") or "")
                self.refresh_cf_clearance(current_url)
                self.d.refresh()
                time.sleep(8)
                self.wait_ready(timeout=5)
                if not self.cloudflare_challenge_visible():
                    log("  Clearance 刷新后验证已通过")
                    return True
            except Exception as e:
                log(f"  Clearance 按当前页刷新失败: {e}", "warn")
        log("  检测到 Cloudflare 验证，尝试点击验证框", "warn")
        for attempt in range(3):
            clicked = False
            try:
                self.d.switch_to.default_content()
                frames = self.d.find_elements(By.TAG_NAME, "iframe")
                for frame in frames:
                    src = str(frame.get_attribute("src") or "").lower()
                    title = str(frame.get_attribute("title") or "").lower()
                    if not ("challenges.cloudflare.com" in src or "cloudflare" in title or "turnstile" in src):
                        continue
                    self.d.switch_to.frame(frame)
                    targets = self.d.find_elements(By.CSS_SELECTOR, "input[type=checkbox], label, body")
                    for target in targets:
                        try:
                            if target.is_displayed():
                                ActionChains(self.d).move_to_element(target).click().perform()
                                clicked = True
                                break
                        except Exception:
                            continue
                    self.d.switch_to.default_content()
                    if clicked:
                        break
            except Exception as e:
                log(f"  Cloudflare 验证点击失败: {e}", "warn")
                try:
                    self.d.switch_to.default_content()
                except Exception:
                    pass

            if not clicked:
                try:
                    self.click_optional("Verify you are human", wait_seconds=1)
                except Exception:
                    pass
            time.sleep(8)
            self.wait_ready(timeout=3)
            if not self.cloudflare_challenge_visible():
                log("  Cloudflare 验证已通过")
                return True
            log(f"  Cloudflare 验证仍存在，重试 {attempt + 1}/3", "warn")
        return self.click_cloudflare_checkbox_by_coordinates()

    def click_cloudflare_checkbox_by_coordinates(self):
        try:
            width, height = self.d.execute_script("return [window.innerWidth || 0, window.innerHeight || 0]")
            width = int(width or 1440)
            height = int(height or 900)
        except Exception:
            width, height = 1440, 900
        points = [
            (int(width / 2 - 130), int(height / 2 + 65)),
            (int(width / 2 - 125), int(height / 2 + 95)),
            (int(width / 2), int(height / 2 + 75)),
        ]
        for x, y in points:
            try:
                log(f"  Cloudflare 坐标点击: {x},{y}", "warn")
                actions = ActionBuilder(self.d)
                actions.pointer_action.move_to_location(x, y)
                actions.pointer_action.click()
                actions.perform()
                time.sleep(8)
                self.wait_ready(timeout=3)
                url = (self.d.current_url or "").lower()
                if self.email_input_visible() or self.password_input_visible() or self.code_input_visible():
                    log("  Cloudflare 坐标点击后表单已恢复")
                    return True
                if not self.cloudflare_challenge_visible():
                    if "/api/auth/error" not in url and "/auth/error" not in url:
                        log("  Cloudflare 坐标点击后验证已消失")
                        return True
                    log("  Cloudflare 坐标点击后仍在错误页", "warn")
                    continue
                log("  Cloudflare 坐标点击后验证仍存在", "warn")
            except Exception as e:
                log(f"  Cloudflare 坐标点击失败: {e}", "warn")
        return False

    def wait_and_solve_cloudflare_challenge(self, timeout=12):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.wait_ready(timeout=2)
            if self.cloudflare_challenge_visible():
                return self.solve_cloudflare_challenge()
            time.sleep(1)
        return not self.cloudflare_challenge_visible()

    # ── 元素操作（带重试）────────────────────────────────
    def _find_button(self, text):
        """找到匹配的按钮元素"""
        # 精确匹配
        for b in self.d.find_elements(By.TAG_NAME, "button"):
            try:
                bt = (b.text or "").strip()
                if bt == text: return b
            except StaleElementReferenceException: continue
        # 包含匹配（排除 "Continue with xxx"）
        for b in self.d.find_elements(By.TAG_NAME, "button"):
            try:
                bt = (b.text or "").strip()
                if text in bt and not bt.startswith("Continue with"): return b
            except StaleElementReferenceException: continue
        return None

    def click(self, text, retries=MAX_RETRIES, refresh_on_fail=True):
        """点击按钮，带重试和刷新"""
        for attempt in range(retries):
            self.wait_ready()
            btn = self._find_button(text)
            if btn:
                try:
                    log(f"  点击: {btn.text.strip()[:50]}")
                    ActionChains(self.d).move_to_element(btn).click().perform()
                    time.sleep(3)
                    return
                except Exception as e:
                    log(f"  点击失败: {e}", "warn")
            else:
                log(f"  未找到按钮: {text}", "warn")

            if attempt < retries - 1:
                if refresh_on_fail and attempt >= 1:
                    log(f"  刷新页面重试...", "warn")
                    self.d.refresh(); time.sleep(8)
                else:
                    time.sleep(2)
        raise StepError(f"点击失败(已重试{retries}次): {text}")

    def click_optional(self, text, wait_seconds=5, log_missing=True):
        """点击可选按钮；不存在或点不了时跳过，不阻断流程。"""
        deadline = time.time() + wait_seconds
        last_error = None
        while True:
            self.wait_ready(timeout=2)
            btn = self._find_button(text)
            if btn:
                try:
                    log(f"  点击可选按钮: {btn.text.strip()[:50]}")
                    ActionChains(self.d).move_to_element(btn).click().perform()
                    time.sleep(3)
                    return True
                except Exception as e:
                    last_error = e

            if time.time() >= deadline:
                break
            time.sleep(1)

        if last_error:
            log(f"  可选按钮点击失败，跳过: {text} ({last_error})", "warn")
        elif log_missing:
            log(f"  可选按钮不存在，跳过: {text}")
        return False

    def click_text_element(self, needles, wait_seconds=5):
        """Click a visible button/link-like element containing any text needle."""
        if isinstance(needles, str):
            needles = [needles]
        lowered_needles = [str(item or "").strip().lower() for item in needles if str(item or "").strip()]
        deadline = time.time() + wait_seconds
        while True:
            self.wait_ready(timeout=2)
            try:
                elements = self.d.find_elements(By.CSS_SELECTOR, "button, a, [role=button]")
            except Exception:
                elements = []
            for element in elements:
                try:
                    label = " ".join((
                        element.text
                        or element.get_attribute("aria-label")
                        or element.get_attribute("title")
                        or ""
                    ).split())
                    lowered = label.lower()
                    if label and element.is_displayed() and any(needle in lowered for needle in lowered_needles):
                        log(f"  点击: {label[:60]}")
                        ActionChains(self.d).move_to_element(element).click().perform()
                        time.sleep(3)
                        return True
                except Exception:
                    continue
            if time.time() >= deadline:
                return False
            time.sleep(1)

    def fill(self, selector, value, retries=MAX_RETRIES, *, sensitive=False):
        """填输入框"""
        is_sensitive = sensitive or "password" in str(selector or "").lower()
        for attempt in range(retries):
            self.wait_ready()
            try:
                el = self.d.find_element(By.CSS_SELECTOR, selector)
                ActionChains(self.d).move_to_element(el).click().perform()
                time.sleep(0.2)
                try: el.clear()
                except: pass
                for ch in value: el.send_keys(ch); time.sleep(0.03)
                log(f"  填入: {mask_secret(value) if is_sensitive else value}")
                return
            except Exception as e:
                if attempt == retries - 1:
                    raise StepError(f"填框失败: {selector}")
                time.sleep(2)

    def fill_any(self, selectors, value, *, fallback=True):
        """尝试多个选择器"""
        is_sensitive = any("password" in str(sel or "").lower() for sel in selectors)
        for sel in selectors:
            try: self.fill(sel, value, sensitive=is_sensitive); return
            except StepError: continue
        if not fallback:
            raise StepError("找不到目标输入框")
        # 兜底：找任意 input
        for inp in self.d.find_elements(By.CSS_SELECTOR, "input:not([type=hidden]):not([type=submit])"):
            try:
                ActionChains(self.d).move_to_element(inp).click().perform()
                time.sleep(0.2)
                try: inp.clear()
                except: pass
                for ch in value: inp.send_keys(ch); time.sleep(0.03)
                log(f"  填入(fb): {mask_secret(value) if is_sensitive else value}")
                return
            except: pass
        raise StepError("找不到任何输入框")

    def fill_email_input(self, email):
        if self.fill_semantic_input("email", email):
            return
        self.fill_any([
            "input[type=email]",
            "input[name=email]",
            "input[name=username]",
            "input[autocomplete=email]",
            "input#email-input",
        ], email)

    def fill_password_input(self):
        if self.fill_semantic_input("password", self.signup_password, sensitive=True):
            return
        self.fill_any([
            "input[name=new-password]",
            "input[autocomplete='new-password']",
            "input[name=current-password]",
            "input[autocomplete='current-password']",
            "input[type=password]",
        ], self.signup_password)

    def fill_phone_input(self, phone):
        full_phone = "+" + re.sub(r"\D", "", phone)
        inputs = self.phone_input_elements()
        if not inputs:
            raise StepError("找不到手机号输入框")
        element = inputs[0]
        ActionChains(self.d).move_to_element(element).click().perform()
        try:
            element.clear()
        except Exception:
            pass
        for char in full_phone:
            element.send_keys(char)
            time.sleep(0.03)
        log(f"  填入: {full_phone}")
        return full_phone

    def phone_input_validation_hint(self):
        """Return sanitized native validation state without exposing the number."""
        hints = []
        for element in self.phone_input_elements():
            try:
                state = self.d.execute_script(
                    """
                    const el = arguments[0];
                    return {
                      valid: !el.validity || el.validity.valid,
                      message: el.validationMessage || '',
                      ariaInvalid: el.getAttribute('aria-invalid') || '',
                      patternMismatch: !!(el.validity && el.validity.patternMismatch),
                      typeMismatch: !!(el.validity && el.validity.typeMismatch),
                      tooShort: !!(el.validity && el.validity.tooShort),
                      tooLong: !!(el.validity && el.validity.tooLong)
                    };
                    """,
                    element,
                ) or {}
            except Exception:
                state = {}
            if not isinstance(state, dict):
                continue
            message = " ".join(str(state.get("message") or "").split())[:160]
            flags = [
                key for key in ("patternMismatch", "typeMismatch", "tooShort", "tooLong")
                if state.get(key)
            ]
            if message or flags or str(state.get("ariaInvalid") or "").lower() == "true":
                hints.append(
                    f"message={message or '-'} flags={','.join(flags) or '-'} "
                    f"ariaInvalid={state.get('ariaInvalid') or '-'}"
                )
        return "; ".join(hints)[:300]

    def fill_code_input(self, code):
        code = str(code or "").strip()
        unique = self.code_input_elements()
        one_char_inputs = [
            element for element in unique
            if str(element.get_attribute("maxlength") or "").strip() == "1"
        ]
        if len(one_char_inputs) >= len(code) >= 4:
            for element in one_char_inputs:
                self.clear_input_element(element)
            for element, char in zip(one_char_inputs, code):
                ActionChains(self.d).move_to_element(element).click().perform()
                element.send_keys(char)
                time.sleep(0.05)
            log(f"  填入验证码: {code}")
            return
        if unique:
            element = unique[0]
            ActionChains(self.d).move_to_element(element).click().perform()
            self.clear_input_element(element)
            element.send_keys(code)
            log(f"  填入验证码: {code}")
            return
        raise StepError("找不到验证码输入框")

    def clear_input_element(self, element):
        """Clear controlled inputs before entering a new OTP.

        React-controlled OTP fields can ignore WebElement.clear().  Keyboard
        replacement plus input/change events makes the old code impossible to
        survive into the next submission batch.
        """
        try:
            ActionChains(self.d).move_to_element(element).click().perform()
        except Exception:
            pass
        try:
            element.send_keys(Keys.CONTROL, "a")
            element.send_keys(Keys.BACKSPACE)
        except Exception:
            pass
        try:
            element.clear()
        except Exception:
            pass
        try:
            self.d.execute_script(
                """
                const el = arguments[0];
                const setter = Object.getOwnPropertyDescriptor(
                  el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
                  'value'
                )?.set;
                if (setter) setter.call(el, ''); else el.value = '';
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                """,
                element,
            )
        except Exception:
            pass

    def start_email_code_batch(self, addr, *, requested_at=None, reason="发送"):
        """Start one OTP batch and reject every message from earlier batches."""
        key = normalize_email(addr)
        if not key:
            return 0.0
        if not hasattr(self, "email_code_not_before"):
            self.email_code_not_before = {}
        # Mail providers commonly expose only whole-second precision.  Flooring
        # preserves a code sent during the same displayed second as the click.
        not_before = float(int(requested_at if requested_at is not None else time.time()))
        self.email_code_not_before[key] = not_before
        stamp = datetime.fromtimestamp(not_before, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
        log(f"  邮箱验证码批次基准（北京时间）: {stamp}，原因={reason}")
        return not_before

    def primary_action_elements(self):
        """Rank visible form actions by their page semantics.

        Buttons are not assumed to be in a fixed DOM position.  Destructive,
        resend, and third-party controls are excluded; submit/continue/confirm
        actions are ranked ahead of generic buttons.  This lets every page
        variant use its own localized label while preserving the existing
        fallback vocabulary below.
        """
        accept = (
            "continue", "next", "verify", "submit", "confirm", "finish",
            "authorize", "allow", "create account", "sign up", "sign in",
            "log in", "proceed", "done", "继续", "下一步", "确认", "验证",
            "提交", "授权", "允许", "完成", "登録", "確認", "認証",
        )
        reject = (
            "cancel", "back", "close", "resend", "send again", "try again",
            "remove", "delete", "logout", "log out", "sign out", "forgot",
            "google", "apple", "microsoft", "github", "phone", "qr",
            "取消", "返回", "关闭", "重发", "重新发送", "删除", "退出",
        )
        ranked = []
        try:
            elements = self.visible_elements(
                "button, input[type=submit], [role=button]"
            )
        except Exception:
            elements = []
        for index, element in enumerate(elements):
            try:
                if not element.is_enabled():
                    continue
                label = " ".join(element_action_label(element).split())
                lowered = label.lower()
                if any(term in lowered for term in reject):
                    continue
                score = 0
                input_type = str(element.get_attribute("type") or "").lower()
                if input_type == "submit":
                    score += 100
                if any(lowered == term for term in accept):
                    score += 80
                elif any(term in lowered for term in accept):
                    score += 50
                # Empty submit buttons are still valid form actions.
                if not label and input_type == "submit":
                    score += 20
                if score > 0:
                    ranked.append((score, -index, element, label))
            except Exception:
                continue
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [(element, label) for _score, _index, element, label in ranked]

    def click_primary_action(self, *, optional=False):
        for button, label in self.primary_action_elements():
            try:
                log(f"  点击提交按钮: {label[:50] or '[无文本]'}")
                ActionChains(self.d).move_to_element(button).click().perform()
                time.sleep(3)
                return True
            except Exception:
                continue
        for text in (
            "Continue",
            "Continuar",
            "Continuer",
            "Weiter",
            "続行",
            "继续",
            "繼續",
            "계속",
            "Next",
            "Próximo",
            "Siguiente",
            "Suivant",
            "Nächste",
            "次へ",
            "下一步",
            "다음",
            "Verify",
            "Verificar",
            "Vérifier",
            "Überprüfen",
            "確認",
            "验证",
            "驗證",
            "확인",
            "Submit",
            "Enviar",
            "Envoyer",
            "Absenden",
            "送信",
            "提交",
            "제출",
            "Finish creating account",
            "Authorize",
            "Autorizar",
            "Autoriser",
            "Autorisieren",
            "承認",
            "授权",
            "授權",
            "승인",
            "Allow",
            "Permitir",
            "Zulassen",
            "許可",
            "允许",
            "允許",
            "허용",
        ):
            if self.click_optional(text, wait_seconds=1):
                return True
        if optional:
            return False
        raise StepError("找不到继续/提交按钮")

    # ── SMS/邮箱轮询 ─────────────────────────────────────
    def poll_sms(self, phone):
        deadline = time.time() + SMS_TIMEOUT_SECONDS
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            try:
                r = api("GET", f"/api/phones/{phone}/code")
                code = r.get("status", {}).get("code")
                if code: return str(code)
            except ApiError as e:
                text = str(e)
                if "PHONE_CODE_QUOTA" in text or "冷却" in text or "接码" in text:
                    raise PhoneRetry(f"手机号接码冷却/上限: {text}", hold_phone=True, return_to_phone=True)
            except:
                pass
            remaining = max(0, int(deadline - time.time()))
            if attempt == 1 or attempt % 3 == 0:
                log(f"  SMS 等待中，剩余约 {remaining}s")
            time.sleep(min(SMS_POLL_INTERVAL_SECONDS, max(1, deadline - time.time())))
        return None

    def poll_email(self, addr):
        email_key = normalize_email(addr)
        min_mail_ts = self.email_code_not_before.get(email_key)
        if min_mail_ts is None:
            min_mail_ts = self.start_email_code_batch(addr, reason="开始等待（无既有发送基准）")

        def mail_timestamp(item):
            raw = str(item.get("date") or item.get("receivedAt") or item.get("createdAt") or "").strip()
            if not raw:
                return 0.0
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    # OutlookEmail/Mail Admin currently return Beijing wall time
                    # without an offset, e.g. 2026-08-03 02:49:06.
                    parsed = parsed.replace(tzinfo=BEIJING_TZ)
                return parsed.timestamp()
            except Exception:
                return 0.0

        stale_resend_requested = False
        for i in range(15):
            stale_code_seen = False
            if self.d and self.auth_session_ended_visible():
                raise AuthSessionEnded("等待邮箱验证码时授权会话已结束")
            if self.d:
                # Page may have already advanced while we wait for mail.
                try:
                    url = str(getattr(self.d, "current_url", "") or "").lower()
                except Exception:
                    url = ""
                if any(token in url for token in ("about-you", "password", "create-account", "phone-verification", "callback", "chatgpt.com/")) and "email-verification" not in url:
                    log(f"  等待邮箱码期间页面已离开验证码页: {self.d.title} | {url[:120]}", "warn")
                    return "__PAGE_ADVANCED__"
                if self.password_input_visible() or self.phone_input_visible() or self.signup_done():
                    log(f"  等待邮箱码期间已出现后续页面控件: {self.d.title}", "warn")
                    return "__PAGE_ADVANCED__"
                if not self.code_input_visible() and not self.email_input_visible():
                    # Could be about-you/profile without password input names we know.
                    if "about-you" in url or self.visible_elements("input[name=name], input[name=age], input[name=birthday], input[name=birthdate]"):
                        log(f"  等待邮箱码期间已到资料页: {self.d.title}", "warn")
                        return "__PAGE_ADVANCED__"
            try:
                r = api("GET", f"/api/email-queue/mail/latest?address={addr}")
                item = r.get("item", {}) or r.get("mail", {})
                txt = str(item.get("decodedText","")) + " " + str(item.get("decodedSubject",""))
                m = re.search(r'\b(\d{6})\b', txt)
                code = m.group(1) if m else str(item.get("verificationCode") or "").strip()
                if code:
                    ts = mail_timestamp(item)
                    mail_key = str(item.get("id") or item.get("messageId") or item.get("internetMessageId") or "").strip()
                    code_key = mail_key or f"{code}:{str(item.get('date') or item.get('receivedAt') or item.get('createdAt') or '')}"
                    if not ts:
                        stale_code_seen = True
                        if i == 0:
                            log("  跳过无收件时间的邮箱验证码，无法证明属于当前批次", "warn")
                    elif ts < min_mail_ts:
                        stale_code_seen = True
                        if i == 0:
                            received = datetime.fromtimestamp(ts, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
                            baseline = datetime.fromtimestamp(min_mail_ts, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
                            log(f"  跳过旧邮箱验证码邮件: 收件={received}，本批基准={baseline}")
                    elif code_key and code_key in self.used_email_code_keys:
                        if i == 0:
                            log("  跳过已使用的邮箱验证码邮件")
                    else:
                        self.used_email_codes.add(code)
                        if code_key:
                            self.used_email_code_keys.add(code_key)
                        return code
            except: pass
            # A resumed verification page commonly has only an expired code in
            # the mailbox. Request a new one immediately instead of idling for
            # the first 30 seconds; keep periodic retries for delayed pages.
            should_resend_stale = stale_code_seen and not stale_resend_requested
            if (should_resend_stale or i in (3, 8, 12)) and self.request_email_code_resend(addr):
                if should_resend_stale:
                    stale_resend_requested = True
                min_mail_ts = self.email_code_not_before[email_key]
            if i % 3 == 0: log(f"  邮箱 {i+1}/15")
            time.sleep(10)
        return None

    def prepare_email(self):
        if not self.requested_email:
            return api("POST", "/api/temp-mail/address", {}).get("item", {}).get("address", "")

        if "@" not in self.requested_email:
            raise FatalError(f"邮箱格式无效: {self.requested_email}")

        # Mail Opus / externally supplied aliases already exist.  Calling the
        # legacy temp-mail endpoint here only creates a guaranteed 500 when
        # TEMP_MAIL_API_URL is unset and adds noise/latency to every account.
        temp_mail_url = str(os.environ.get("TEMP_MAIL_API_URL") or "").strip()
        if not temp_mail_url:
            log(f"  使用传入邮箱（跳过 temp-mail 创建确认）: {self.requested_email}")
            return self.requested_email
        name, domain = self.requested_email.split("@", 1)
        try:
            api("POST", "/api/temp-mail/address", {
                "name": name,
                "domain": domain,
                "enablePrefix": False,
            })
            log(f"  邮箱已创建/确认: {self.requested_email}")
        except Exception as e:
            log(f"  邮箱创建确认失败，继续使用传入邮箱: {e}", "warn")
        return self.requested_email

    def close_browser(self):
        if self.d:
            try: self.d.quit()
            except: pass
            self.d = None
        self._stop_auth_proxy_bridge()

    def reset_failed_browser_profile(self):
        """Rebuild a failed browser environment without changing its fingerprint."""
        if MANUAL_MODE:
            return False
        profile_dir = self.profile_dir()
        identity_path = profile_dir / ".automyai-fingerprint.json"
        identity = None
        try:
            if identity_path.is_file():
                identity = identity_path.read_bytes()
        except OSError:
            identity = None
        try:
            shutil.rmtree(profile_dir, ignore_errors=True)
            # Chromium may release file handles shortly after quit.  A second
            # bounded pass removes the residual directory without delaying the
            # next account indefinitely.
            if profile_dir.exists():
                time.sleep(0.25)
                shutil.rmtree(profile_dir, ignore_errors=True)
        except Exception as error:
            log(f"  清理失败浏览器环境失败: {type(error).__name__}: {error}", "warn")
            return False
        try:
            profile_dir.mkdir(parents=True, exist_ok=True)
            if identity:
                identity_path.write_bytes(identity)
                identity_path.chmod(0o600)
            log("  已丢弃失败浏览器环境；保留同一指纹身份，下次从干净页面重建", "warn")
            return True
        except Exception as error:
            log(f"  重建干净浏览器环境失败: {type(error).__name__}: {error}", "warn")
            return False

    def keep_browser_for_manual_takeover(self):
        if not self.d:
            return
        log("  浏览器已保留，可在当前 DISPLAY/VNC 里人工接管", "warn")
        keep_seconds = KEEP_BROWSER_SECONDS if KEEP_BROWSER_SECONDS > 0 else KEEP_BROWSER_MAX_SECONDS
        if KEEP_BROWSER_MAX_SECONDS > 0:
            keep_seconds = min(keep_seconds, KEEP_BROWSER_MAX_SECONDS)
        if keep_seconds <= 0:
            log("  保留窗口未启用，立即关闭浏览器", "warn")
            return
        log(f"  保留 {keep_seconds}s 后自动关闭", "warn")
        deadline = time.time() + keep_seconds
        while time.time() < deadline:
            remaining = max(0, int(deadline - time.time()))
            time.sleep(min(30, max(1, remaining)))
            remaining = max(0, int(deadline - time.time()))
            if remaining:
                log(f"  人工接管窗口仍保留，剩余约 {remaining}s", "info")

    def cancel_phone(self, phone, reason=""):
        if not phone:
            return False
        try:
            result = api("POST", f"/api/phones/{phone}/cancel")
            warning = str(result.get("warning") or "").strip() if isinstance(result, dict) else ""
            if warning:
                log(f"  手机号 {phone} 取消已提交但上游暂不允许立即取消: {warning}", "warn")
            else:
                log(f"  已取消手机号 {phone}{'：' + reason if reason else ''}", "warn")
            return True
        except Exception as e:
            log(f"  取消手机号失败 {phone}: {e}", "warn")
            return False

    def release_phone(self, phone, reason=""):
        if not phone:
            return False
        try:
            api("POST", f"/api/phones/{phone}/release")
            log(f"  已释放手机号 {phone}{'：' + reason if reason else ''}", "warn")
            return True
        except Exception as e:
            log(f"  释放手机号失败 {phone}: {e}", "warn")
            return False

    def hold_phone(self, phone, reason=""):
        if not phone:
            return False
        try:
            api("POST", f"/api/phones/{phone}/hold", {"reason": reason})
            log(f"  已保留手机号链接 {phone}{'：' + reason if reason else ''}", "warn")
            return True
        except Exception as e:
            log(f"  保留手机号链接失败 {phone}: {e}", "warn")
            return False

    def bind_phone_proxy(self, phone, email, stage="submitted"):
        if not phone:
            return None
        try:
            result = api("POST", f"/api/phones/{phone}/bind-proxy", {
                "email": email,
                "proxy": PROXY,
                "stage": stage,
            })
            binding = result.get("binding") if isinstance(result, dict) else None
            if isinstance(binding, dict):
                label = binding.get("region") or binding.get("proxyName") or binding.get("proxyUrl") or ""
                log(f"  手机号归属已记录: {phone}{' -> ' + label if label else ''}")
            return binding
        except Exception as e:
            log(f"  手机号归属记录失败 {phone}: {e}", "warn")
            return None

    def wait_phone_prompt_visible(self, timeout=12):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.wait_ready(timeout=2)
            if self.phone_input_visible():
                return True
            time.sleep(1)
        return False

    def open_phone_input_if_prompted(self):
        if self.phone_input_visible():
            return True
        if self.whatsapp_code_prompt_visible() or self.code_input_elements():
            return False
        if not self.phone_verification_prompt_visible():
            return False
        for _ in range(2):
            if self.phone_input_visible():
                return True
            if not self.click_primary_action(optional=True):
                break
            time.sleep(3)
            if self.wait_phone_prompt_visible(timeout=8):
                return True
        return self.phone_input_visible()

    def back_to_phone_prompt(self):
        if not self.d:
            return False
        if self.phone_input_visible():
            return True
        for text in (
            "Change phone number",
            "Use a different phone number",
            "Edit phone number",
            "Back",
        ):
            try:
                if self.click_optional(text, wait_seconds=1) and self.wait_phone_prompt_visible(timeout=8):
                    log("  已返回手机号输入页")
                    return True
            except Exception:
                pass
        for _ in range(3):
            try:
                self.d.back()
                time.sleep(3)
                if self.wait_phone_prompt_visible(timeout=8):
                    log("  已返回手机号输入页")
                    return True
            except Exception:
                pass
        snippet = " ".join(self.visible_text().split())[:240]
        log(f"  未能返回手机号输入页: {snippet}", "warn")
        return False

    def wait_password_input_after_phone(self):
        deadline = time.time() + PHONE_PASSWORD_PAGE_TIMEOUT
        last_url = ""
        while time.time() < deadline:
            self.wait_ready(timeout=2)
            last_url = self.d.current_url
            if "/api/auth/error" in last_url or "/auth/error" in last_url:
                raise BrowserBlocked(
                    f"手机号提交后进入验证/错误页，疑似浏览器或代理风控，不按坏号反馈: {last_url[:120]}",
                )
            rate_limit_reason = self.phone_verification_rate_limited_reason()
            if rate_limit_reason:
                raise PhoneRetry(
                    f"电话验证请求过多，稍后再试: {rate_limit_reason}",
                    hold_phone=True,
                    return_to_phone=False,
                )
            reason = self.phone_rejection_reason()
            if reason:
                whatsapp_only = reason.startswith("whatsapp_only:")
                raise PhoneRetry(
                    f"手机号提交后被拒绝: {reason}",
                    cancel_phone=not whatsapp_only,
                    hold_phone=whatsapp_only,
                    return_to_phone=True,
                )
            whatsapp_reason = self.whatsapp_verification_reason()
            if whatsapp_reason:
                raise PhoneRetry(
                    f"手机号只进入 WhatsApp 验证: {whatsapp_reason}",
                    hold_phone=True,
                    return_to_phone=True,
                )
            try:
                if self.d.find_elements(By.CSS_SELECTOR, "input[name=new-password], input[autocomplete='new-password']"):
                    return
            except Exception:
                pass
            time.sleep(1)
        raise PhoneRetry(
            f"手机号提交后未进入创建密码页，可能已被使用: {last_url[:120]}",
            hold_phone=True,
            return_to_phone=True,
        )

    def wait_contact_verification_after_password(self):
        deadline = time.time() + 30
        while time.time() < deadline:
            self.wait_ready(timeout=2)
            if "contact-verification" in self.d.current_url:
                return
            rate_limit_reason = self.phone_verification_rate_limited_reason()
            if rate_limit_reason:
                raise PhoneRetry(
                    f"电话验证请求过多，稍后再试: {rate_limit_reason}",
                    hold_phone=True,
                    return_to_phone=False,
                )
            reason = self.phone_rejection_reason()
            if reason:
                whatsapp_only = reason.startswith("whatsapp_only:")
                raise PhoneRetry(
                    f"手机号密码提交后被拒绝: {reason}",
                    cancel_phone=not whatsapp_only,
                    hold_phone=whatsapp_only,
                    return_to_phone=True,
                )
            whatsapp_reason = self.whatsapp_verification_reason()
            if whatsapp_reason:
                raise PhoneRetry(
                    f"手机号只进入 WhatsApp 验证: {whatsapp_reason}",
                    hold_phone=True,
                    return_to_phone=True,
                )
            time.sleep(1)
        raise StepError("URL等待超时: contact-verification")

    @staticmethod
    def _url_hostname(url):
        try:
            return (urlparse(str(url or "")).hostname or "").lower().rstrip(".")
        except Exception:
            return ""

    def third_party_auth_page(self, url=None):
        """Return True when the active page belongs to a known external IdP."""
        if url is None:
            url = getattr(self.d, "current_url", "")
        host = self._url_hostname(url)
        return any(host == suffix or host.endswith(f".{suffix}") for suffix in self.THIRD_PARTY_AUTH_HOST_SUFFIXES)

    def _discard_signup_checkpoint(self):
        """Drop only the explicit resume snapshot; keep the persistent browser profile."""
        try:
            checkpoint = resume_state_path(self.profile_dir())
            if checkpoint.exists():
                checkpoint.unlink()
                log("  已删除不可用浏览器断点")
        except Exception as error:
            log(f"  删除不可用浏览器断点失败: {error}", "warn")

    def _open_clean_signup_tab(self):
        """Open signup in one newly-created tab and close all stale tabs."""
        driver = self.d
        old_handles = list(getattr(driver, "window_handles", []) or [])
        target_handle = None
        try:
            driver.execute_script("window.open('about:blank', '_blank')")
            new_handles = list(getattr(driver, "window_handles", []) or [])
            target_handle = next((handle for handle in reversed(new_handles) if handle not in old_handles), None)
            if target_handle is None and new_handles:
                target_handle = new_handles[-1]
        except Exception as error:
            log(f"  新建注册标签页失败，改用当前标签页: {error}", "warn")

        if target_handle is not None:
            try:
                driver.switch_to.window(target_handle)
                for handle in list(getattr(driver, "window_handles", []) or []):
                    if handle == target_handle:
                        continue
                    try:
                        driver.switch_to.window(handle)
                        driver.close()
                    except Exception:
                        pass
                driver.switch_to.window(target_handle)
            except Exception as error:
                log(f"  清理旧标签页失败，继续使用当前标签页: {error}", "warn")

        driver.get(self.SIGNUP_URL)
        time.sleep(12)

    def _recover_third_party_signup_redirect(self):
        current = str(getattr(self.d, "current_url", "") or "")
        host = self._url_hostname(current) or "unknown"
        log(f"  注册入口跑到第三方登录页 ({host})，清理标签页后重开一次", "warn")
        self._discard_signup_checkpoint()
        self._open_clean_signup_tab()
        if self.third_party_auth_page():
            retry_host = self._url_hostname(getattr(self.d, "current_url", "")) or "unknown"
            raise BrowserBlocked(
                f"注册入口连续跳转到第三方登录页 ({retry_host})，已停止当前账号，未扫描页面按钮"
            )

    def _validate_signup_page_host(self):
        current = str(getattr(self.d, "current_url", "") or "")
        if self.third_party_auth_page(current):
            self._recover_third_party_signup_redirect()
            current = str(getattr(self.d, "current_url", "") or "")
        host = self._url_hostname(current)
        if host not in self.OPENAI_AUTH_HOSTS:
            raise BrowserBlocked(
                f"注册入口停在非 OpenAI 页面 ({host or 'unknown'})，已停止当前账号，未扫描页面按钮"
            )

    def open_signup_email_form(self):
        self.launch()
        self.ensure_cf_clearance(self.SIGNUP_URL)
        self.apply_cf_clearance()

        current = str(getattr(self.d, "current_url", "") or "").lower()
        # Resume-aware: only continue from a healthy *registration* page.
        # choose-an-account / oauth / login welcome-back are auth-stage pages and must not
        # short-circuit a fresh signup when registered flag was cleared.
        registration_tokens = (
            "email-verification",
            "create-account",
            "about-you",
            "password",
            "phone-verification",
            "auth/login?intent=signup",
            "intent=signup",
        )
        auth_only_tokens = (
            "choose-an-account",
            "oauth",
            "authorize",
            "log-in",
            "sign-in",
            "welcome-back",
        )
        already_on_auth = any(token in current for token in registration_tokens)
        if any(token in current for token in auth_only_tokens) and not any(token in current for token in ("email-verification", "about-you", "create-account", "password", "phone-verification", "intent=signup")):
            log(f"  断点是授权页而非注册页，丢弃并重新打开注册: {self.d.title} | {current[:120]}", "warn")
            already_on_auth = False
        if already_on_auth and (
            self.auth_session_ended_visible()
            or self.transient_auth_error_visible()
            or self.is_error_page()
            or self.classify_auth_page() in {"error", "session_ended", "choose_account", "oauth_consent", "chatgpt_home"}
        ):
            log(
                f"  断点页不可用，丢弃并重新打开注册页: {self.d.title} | {current[:120]}",
                "warn",
            )
            already_on_auth = False
            try:
                # Clear poisoned auth tabs so later resume doesn't snap back to dead session.
                self.d.delete_all_cookies()
            except Exception:
                pass
        if not already_on_auth:
            # Also drop a bad browser checkpoint so the next launch does not snap back.
            self._discard_signup_checkpoint()
            self._open_clean_signup_tab()
        else:
            log(f"  使用断点当前页继续: {self.d.title} | {current[:120]}")
            self.wait_ready(timeout=5)
        self._validate_signup_page_host()
        log(f"邮箱注册页: {self.d.title}")

        self._step("Cookie", lambda: self.click_optional("Accept all", log_missing=False))
        email_buttons = ("Continue with email", "Sign up with email", "Use email", "Email")
        clicked_email_button = False
        for button_text in email_buttons:
            if self.email_input_visible():
                break
            if self.click_optional(button_text, wait_seconds=2, log_missing=False):
                clicked_email_button = True
        if not self.email_input_visible() and not clicked_email_button:
            log(f"  注册入口未找到邮箱方式按钮（已扫描 {len(email_buttons)} 个候选）", "warn")

    def fill_profile_if_present(self):
        self._profile_fill_error = ""
        name_filled = False
        age_filled = False
        url = str(getattr(self.d, "current_url", "") or "").lower()
        profile_url = registration_profile_url(url)
        page_text = " ".join(self.visible_text().split()).lower()
        page_heading = f"{str(getattr(self.d, 'title', '') or '').lower()} {page_text}"
        age_required = profile_url and any(token in page_heading for token in (
            "confirm your age",
            "date of birth",
            "birth date",
            "birthday",
            "your age",
            "生年月日",
            "出生日期",
            "生日",
            "年龄",
            "年齢",
        ))
        # about-you / profile pages
        name_selectors = [
            "input[name=name]",
            "input[name=fullName]",
            "input[name=username]",
            "input[autocomplete=name]",
            "input[id*=name i]",
        ]
        age_selectors = [
            "input[name=age]",
            "input[name=birthday]",
            "input[name=birthdate]",
            "input[name=birthDate]",
            "input[autocomplete=bday]",
            "input[type=date]",
            "input[placeholder*='birth' i]",
            "input[aria-label*='birth' i]",
            "input[id*=age i]",
            "input[id*=birth i]",
        ]

        def fill_profile_element(element, value):
            current = str(element.get_attribute("value") or "").strip()
            if current == str(value):
                return True
            ActionChains(self.d).move_to_element(element).click().perform()
            # React-controlled inputs can ignore WebElement.clear() and append
            # on the next render. Select-all produces a real input event and
            # reliably replaces the old value.
            element.send_keys(Keys.CONTROL, "a")
            element.send_keys(Keys.BACKSPACE)
            for char in str(value):
                element.send_keys(char)
                time.sleep(0.03)
            log(f"  填入: {value}")
            return True

        def fill_profile_value(selector, value):
            elements = self.visible_elements(selector)
            return fill_profile_element(elements[0], value) if elements else False

        # Prefer semantic roles from the current DOM over fixed selectors. This
        # covers localized/variant profile forms whose names/IDs differ.
        try:
            semantic_controls = self.visible_elements(
                "input:not([type=hidden]):not([type=submit]), textarea, [contenteditable=true]"
            )
        except Exception:
            semantic_controls = []
        for element in semantic_controls:
            role = self.semantic_input_role(element)
            if role == "name" and not name_filled:
                name_filled = fill_profile_element(element, self.display_name)
            elif role == "age" and not age_filled:
                age_filled = fill_profile_element(element, self.signup_age)
            elif role == "birthdate" and not age_filled:
                # React-Aria segmented birthday controls require arrow-key
                # updates plus hidden-input verification; leave those to the
                # dedicated segment routine instead of typing into one segment.
                segmented = False
                try:
                    segmented = bool(
                        element.get_attribute("data-type")
                        or element.get_attribute("data-segment")
                        or str(element.get_attribute("role") or "").lower() == "spinbutton"
                        or str(element.get_attribute("contenteditable") or "").lower() == "true"
                    )
                except Exception:
                    pass
                if not segmented:
                    age_filled = fill_profile_element(element, self.profile_birthdate())

        if not name_filled:
            for sel in name_selectors:
                if self.visible_elements(sel):
                    name_filled = fill_profile_value(sel, self.display_name)
                    break
        if not age_filled:
            for sel in age_selectors:
                if self.visible_elements(sel):
                    # OpenAI sometimes wants age years, sometimes birthday.
                    value = self.signup_age
                    if "birth" in sel.lower() or "bday" in sel.lower() or "type=date" in sel.lower():
                        value = self.profile_birthdate()
                    age_filled = fill_profile_value(sel, value)
                    break
        if not age_filled:
            age_filled = self.fill_profile_birthdate_segments()

        if not name_filled and profile_url:
            # Fallback: first two visible text/number inputs.
            selector = "input:not([type=hidden]):not([type=submit]):not([type=checkbox]):not([type=radio])"

            def fill_fallback(index, value, label):
                last_error = None
                for _ in range(3):
                    try:
                        # React may replace every input after the preceding field.
                        # Re-query on each attempt instead of keeping stale elements.
                        inputs = self.visible_elements(selector)
                        if len(inputs) <= index:
                            return False
                        element = inputs[index]
                        ActionChains(self.d).move_to_element(element).click().perform()
                        try:
                            element.clear()
                        except Exception:
                            pass
                        for char in str(value):
                            element.send_keys(char)
                            time.sleep(0.03)
                        log(f"  填入{label}(fb): {value}")
                        return True
                    except StaleElementReferenceException as error:
                        last_error = error
                        time.sleep(0.5)
                    except Exception as error:
                        last_error = error
                        break
                if last_error:
                    log(f"  {label}兜底失败: {last_error}", "warn")
                return False

            name_filled = fill_fallback(0, self.display_name, "资料名")
            if not age_filled:
                age_filled = fill_fallback(1, self.signup_age, "年龄")

        if age_required and not age_filled:
            self._profile_fill_error = "资料页要求生日/年龄，但未找到或未填入日期控件，已停止提交"
            log(f"  {self._profile_fill_error}", "warn")
            return False
        if not name_filled and not age_filled:
            self._profile_fill_error = "资料页没有找到可填写的姓名或生日字段"
            return False

        self.click_primary_action(optional=True)
        self.confirm_profile_age_if_present()
        return True

    def profile_birthdate(self):
        """Build a stable adult birthday matching the configured per-email age."""
        try:
            age = min(80, max(18, int(str(self.signup_age or "30"))))
        except (TypeError, ValueError):
            age = 30
        now = datetime.utcnow()
        seed = self.current_email or self.requested_email or "profile"
        month = 1 + stable_index(seed, 12, namespace="signup-birth-month")
        day = 1 + stable_index(seed, 28, namespace="signup-birth-day")
        year = now.year - age
        if (month, day) > (now.month, now.day):
            year -= 1
        return f"{year:04d}-{month:02d}-{day:02d}"

    def fill_profile_birthdate_segments(self):
        """Fill React-Aria style month/day/year contenteditable spinbuttons."""
        selectors = (
            "[role=spinbutton][data-type]",
            "[role=spinbutton][aria-label]",
            "[contenteditable=true][data-type]",
            "[contenteditable=true][inputmode=numeric]",
        )
        elements = []
        seen = set()
        for selector in selectors:
            for element in self.visible_elements(selector):
                key = getattr(element, "id", None) or id(element)
                if key in seen:
                    continue
                seen.add(key)
                elements.append(element)

        parts = {}
        aliases = {
            "month": ("month", "月份", "月"),
            "day": ("day", "日期", "日"),
            "year": ("year", "年份", "年"),
        }
        for element in elements:
            try:
                semantic = " ".join(str(element.get_attribute(name) or "") for name in (
                    "data-type", "data-segment", "aria-label", "data-placeholder",
                    "placeholder", "name", "id",
                )).lower()
            except Exception:
                continue
            for part, needles in aliases.items():
                if part not in parts and any(needle in semantic for needle in needles):
                    parts[part] = element
                    break

        if not all(part in parts for part in ("month", "day", "year")):
            return False

        year, month, day = self.profile_birthdate().split("-")
        values = {"month": int(month), "day": int(day), "year": int(year)}

        def current_segment(part):
            selector = f"[role=spinbutton][data-type='{part}']"
            matches = self.visible_elements(selector)
            return matches[0] if matches else parts[part]

        def set_segment(part, target):
            element = current_segment(part)
            raw_now = str(element.get_attribute("aria-valuenow") or "").strip()
            if raw_now.lstrip("-").isdigit():
                current = int(raw_now)
                delta = target - current
                key = Keys.ARROW_UP if delta > 0 else Keys.ARROW_DOWN
                for _ in range(abs(delta)):
                    element = current_segment(part)
                    ActionChains(self.d).move_to_element(element).click().perform()
                    element.send_keys(key)
                return
            ActionChains(self.d).move_to_element(element).click().perform()
            element.send_keys(str(target))

        expected = f"{year}-{month}-{day}"
        try:
            # Match the page's logical order. Arrow changes are slower than
            # direct text assignment but reliably update React-Aria state.
            for part in ("year", "month", "day"):
                set_segment(part, values[part])
                time.sleep(0.15)
            hidden = self.d.find_elements(By.CSS_SELECTOR, "input[type=hidden][name=birthday]")
            actual = str(hidden[0].get_attribute("value") or "").strip() if hidden else ""
            if actual != expected:
                log(f"  分段生日回验失败: 期望 {expected}，页面实际 {actual or '空'}", "warn")
                return False
            log(f"  填入生日并回验通过: {expected}")
            return True
        except Exception as error:
            log(f"  分段生日控件填写失败: {type(error).__name__}: {error}", "warn")
            return False

    def confirm_profile_age_if_present(self, timeout=6):
        """Confirm the age-to-birthday dialog shown after profile submit."""
        labels = {"ok", "confirm", "確認", "确定", "確定"}
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                dialogs = self.visible_elements("[role=dialog], [aria-modal=true]")
                scope = dialogs[0] if dialogs else self.d
                buttons = scope.find_elements(By.CSS_SELECTOR, "button[type=submit], button")
            except Exception:
                buttons = []
            for button in buttons:
                try:
                    label = " ".join(
                        (button.text or button.get_attribute("aria-label") or "").split()
                    ).strip()
                    if button.is_displayed() and button.is_enabled() and label.casefold() in labels:
                        log(f"  确认年龄弹窗: {label}")
                        ActionChains(self.d).move_to_element(button).click().perform()
                        time.sleep(3)
                        return True
                except Exception:
                    continue
            time.sleep(0.5)
        return False

    def request_email_code_resend(self, email=None):
        """Request a fresh email code using the localized auth-page control."""
        labels = (
            "Resend email",
            "Resend code",
            "Resend",
            "メールを再送信する",
            "コードを再送信",
            "再送信する",
            "Reenviar correo electrónico",
            "Reenviar código",
            "Reenviar",
            "Gửi lại email",
            "Gửi lại mã",
            "Gửi lại",
            "重新发送",
            "重发",
        )
        requested_at = time.time()
        if not self.click_text_element(labels, wait_seconds=3):
            return False
        self.start_email_code_batch(email or self.current_email, requested_at=requested_at, reason="点击重发")
        log("  已请求重发邮箱验证码")
        self._sleep(3)
        return True

    def wait_registration_transition(self, previous_kind, timeout=18):
        """Wait for one explicit signup step to leave its current page.

        The upstream implementation performs one form action at a time and then
        waits for navigation.  Keep that model here instead of inferring progress
        from booleans that survive React re-renders.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.wait_ready(timeout=2)
            if self.signup_done():
                return "done"
            kind = self.classify_auth_page()
            if kind != previous_kind:
                return kind
            self._sleep(1)
        return previous_kind

    def submit_signup_email_and_wait(self, email, timeout=45):
        """Submit the real browser form and follow the auth redirect chain.

        HAR comparison shows the expected path is:
        chatgpt /api/auth/signin/openai -> auth /api/accounts/authorize ->
        /email-verification.  React can keep rendering the email form while
        these requests are still in flight, so page text alone is not a safe
        signal to click the form repeatedly.
        """
        started = time.time()
        self.fill_email_input(email)
        self.capture_network_diagnostics("邮箱提交前")
        self.start_email_code_batch(email, reason="提交注册邮箱")
        if not self.click_primary_action():
            raise StepError("邮箱页找不到提交按钮")
        deadline = time.time() + timeout
        saw_signin = False
        saw_authorize = False
        while time.time() < deadline:
            self.wait_ready(timeout=2)
            self.capture_network_diagnostics("邮箱提交后")
            saw_signin = saw_signin or any(
                int(event.get("status") or 0) in {200, 302}
                for event in self.recent_auth_responses("/api/auth/signin/openai", since=started)
            )
            saw_authorize = saw_authorize or any(
                int(event.get("status") or 0) in {200, 302, 403}
                for event in self.recent_auth_responses("/api/accounts/authorize", since=started)
            )
            kind = self.classify_auth_page()
            if kind != "email" or self.signup_done():
                log(
                    f"  邮箱认证链: signin={'yes' if saw_signin else 'no'} "
                    f"authorize={'yes' if saw_authorize else 'no'} -> {kind}"
                )
                return kind
            # A successful signin request means the browser is already working
            # through the auth redirect. Do not click the submit button again.
            self._sleep(1)
        log(
            f"  邮箱认证链超时: signin={'yes' if saw_signin else 'no'} "
            f"authorize={'yes' if saw_authorize else 'no'}", "warn"
        )
        return self.classify_auth_page()

    def complete_registration_with_email(self, email):
        self.open_signup_email_form()
        deadline = time.time() + 300
        attempts = {"email": 0, "email_code": 0, "password": 0, "profile": 0, "other": 0}
        transient_error_recoveries = 0
        need_fresh_email_code = False
        session_restarts = 0
        attempted_unknown_signatures = set()

        while time.time() < deadline:
            self.wait_ready(timeout=2)
            url = str(self.d.current_url or "")

            if "/api/auth/error" in url or "/auth/error" in url:
                if self.wait_and_solve_cloudflare_challenge():
                    continue
                raise BrowserBlocked(f"邮箱注册进入错误页: {url[:120]}")
            if self.cloudflare_challenge_visible():
                if self.solve_cloudflare_challenge():
                    continue
                raise BrowserBlocked("邮箱注册 Cloudflare 验证未通过")
            proxy_block = self.proxy_block_reason()
            if proxy_block:
                raise BrowserBlocked(f"邮箱注册代理被拒: {proxy_block}")
            if self.auth_session_ended_visible():
                session_restarts += 1
                if session_restarts > 2:
                    raise BrowserBlocked("邮箱注册会话重复结束")
                log(f"  注册会话已结束，重新打开注册页: {self.d.title}", "warn")
                self.d.get("https://chatgpt.com/auth/login?intent=signup")
                time.sleep(8)
                self.wait_ready(timeout=5)
                continue
            if self.transient_auth_error_visible() or self.is_error_page():
                self.capture_network_diagnostics("临时错误页")
                transient_error_recoveries += 1
                if transient_error_recoveries > MAX_ERROR_REFRESH:
                    reason = self.transient_auth_error_visible() or self.d.title
                    raise BrowserBlocked(
                        f"注册页面连续临时错误 {transient_error_recoveries} 次，停止并更换代理: "
                        f"{str(reason)[:160]}"
                    )
                if self.recover_transient_auth_error():
                    continue
                raise BrowserBlocked(f"邮箱注册临时错误无法恢复: {self.d.title} | {url[:120]}")

            if self.phone_input_visible():
                raise FatalError("邮箱注册阶段出现手机号验证，按当前流程不在注册阶段取号")

            if self.account_already_exists_hint():
                log(f"  邮箱看起来已存在，跳过注册阶段进入授权: {email}", "warn")
                return True

            page_kind = self.classify_auth_page()
            if page_kind in {"done", "chatgpt_home"} or self.signup_done():
                log(f"✅ 邮箱注册阶段完成: {email}")
                return True

            if page_kind == "email":
                attempts["email"] += 1
                if attempts["email"] > 3:
                    raise BrowserBlocked("邮箱页多次提交仍无进展")
                next_kind = self.submit_signup_email_and_wait(email)
                log(f"  邮箱提交结果: {page_kind} -> {next_kind}")
                if next_kind == "email" and any(
                    int(event.get("status") or 0) == 403
                    for event in self.recent_auth_responses("/api/accounts/authorize", since=time.time() - 60)
                ):
                    if self.wait_and_solve_cloudflare_challenge(timeout=15):
                        continue
                    raise BrowserBlocked("邮箱认证跳转被 Cloudflare 拒绝")
                continue

            if page_kind == "email_code":
                attempts["email_code"] += 1
                if attempts["email_code"] > 4:
                    raise BrowserBlocked("邮箱验证码多次提交仍无进展")
                if need_fresh_email_code:
                    if not self.request_email_code_resend(email):
                        raise BrowserBlocked("邮箱验证码未推进且找不到重发按钮")
                    need_fresh_email_code = False
                code = self.poll_email(email)
                if code == "__PAGE_ADVANCED__":
                    continue
                if not code:
                    raise FatalError("邮箱验证码超时")
                submitted_at = time.time()
                self._step("邮箱验证码", lambda: (
                    self.fill_code_input(code),
                    self.click_primary_action()
                ))
                next_kind = self.wait_registration_transition("email_code")
                self.capture_network_diagnostics("邮箱验证码提交后")
                log(f"  验证码提交结果: {page_kind} -> {next_kind}")
                if next_kind == "email_code":
                    statuses = [
                        int(event.get("status") or 0)
                        for event in self.recent_auth_responses(
                            "/api/accounts/email-otp/validate", since=submitted_at
                        )
                    ]
                    detail = f"，validate={statuses[-1]}" if statuses else ""
                    log(f"  当前验证码未通过{detail}；作废本批验证码并请求新码", "warn")
                    need_fresh_email_code = True
                continue

            if page_kind == "password":
                attempts["password"] += 1
                if attempts["password"] > 3:
                    raise BrowserBlocked(
                        "密码页多次提交仍无进展，停止并更换代理/会话: "
                        f"{self.password_input_diagnostics()}"
                    )
                self._step("设置密码", lambda: (
                    self.fill_password_input(),
                    self.click_primary_action()
                ))
                next_kind = self.wait_registration_transition("password")
                log(
                    f"  密码提交结果: {page_kind} -> {next_kind}; "
                    f"{self.password_input_diagnostics()}"
                )
                continue

            if page_kind == "profile":
                attempts["profile"] += 1
                if attempts["profile"] > 3:
                    raise BrowserBlocked("资料页多次提交仍无进展")
                profile_submit_started = time.time()
                if not self.fill_profile_if_present():
                    reason = str(getattr(self, "_profile_fill_error", "") or "").strip()
                    raise BrowserBlocked(reason or f"资料页找不到可填写字段: {self.d.title} | {url[:120]}")
                next_kind = self.wait_registration_transition("profile", timeout=35)
                self.capture_network_diagnostics("资料提交后")
                create_account_responses = self.recent_auth_responses(
                    "/api/accounts/create_account", since=profile_submit_started
                )
                create_statuses = [int(event.get("status") or 0) for event in create_account_responses]
                log(f"  资料提交结果: {page_kind} -> {next_kind}")
                if 409 in create_statuses:
                    # Captured browser flow shows invalid/consumed signup state
                    # can mean the account was already created. Continue through
                    # a fresh login/OAuth session instead of submitting profile
                    # again or marking the mailbox bad.
                    log("  create_account 返回 409，转入已有账号授权流程", "warn")
                    return True
                if 500 in create_statuses and next_kind == "profile":
                    log("  create_account 临时 500，保留同一会话重试一次", "warn")
                continue

            if page_kind == "choose_account":
                log(f"  注册流程进入账户选择，视为邮箱已有账号: {email}", "warn")
                return True

            attempts["other"] += 1
            signature = self.page_signature()
            if signature in attempted_unknown_signatures:
                snippet = " ".join(self.visible_text().split())[:240]
                raise BrowserBlocked(
                    f"邮箱注册停在未识别页面({page_kind})，页面签名无变化，停止重复点击: {snippet}"
                )
            attempted_unknown_signatures.add(signature)
            if not self.click_primary_action(optional=True):
                snippet = " ".join(self.visible_text().split())[:240]
                raise BrowserBlocked(
                    f"邮箱注册停在未识别页面({page_kind})且无安全主按钮: {snippet}"
                )
            next_kind = self.wait_registration_transition(page_kind, timeout=12)
            log(f"  未识别页面单次提交结果: {page_kind} -> {next_kind}")
            if next_kind == page_kind:
                snippet = " ".join(self.visible_text().split())[:240]
                raise BrowserBlocked(
                    f"邮箱注册未识别页面单次安全提交后无进展，停止重复点击: {snippet}"
                )

        raise BrowserBlocked("邮箱注册流程超时")

    def wait_code_input_after_phone(self):
        deadline = time.time() + max(PHONE_PASSWORD_PAGE_TIMEOUT, 30)
        last_url = ""
        while time.time() < deadline:
            self.wait_ready(timeout=2)
            last_url = self.d.current_url
            if "/api/auth/error" in last_url or "/auth/error" in last_url:
                raise BrowserBlocked(
                    f"手机号提交后进入验证/错误页，疑似浏览器或代理风控: {last_url[:120]}",
                )
            rate_limit_reason = self.phone_verification_rate_limited_reason()
            if rate_limit_reason:
                raise PhoneRetry(
                    f"电话验证请求过多，稍后再试: {rate_limit_reason}",
                    hold_phone=True,
                    return_to_phone=False,
                )
            whatsapp_reason = self.whatsapp_verification_reason()
            if whatsapp_reason:
                raise PhoneRetry(f"手机号只进入 WhatsApp 验证: {whatsapp_reason}", hold_phone=True, return_to_phone=True)
            if self.code_input_visible():
                return "code"
            if self.password_input_visible():
                return "password"
            reason = self.phone_rejection_reason()
            if reason:
                whatsapp_only = reason.startswith("whatsapp_only:")
                raise PhoneRetry(
                    f"手机号提交后被拒绝: {reason}",
                    cancel_phone=not whatsapp_only,
                    hold_phone=whatsapp_only,
                    return_to_phone=True,
                )
            time.sleep(1)
        self.capture_network_diagnostics("手机号提交停滞")
        validation_hint = self.phone_input_validation_hint()
        detail = f"；原生校验: {validation_hint}" if validation_hint else "；原生校验未报告号码错误"
        raise PhoneRetry(
            f"手机号提交后未进入验证码页，疑似账号/会话级手机号墙: {last_url[:120]}{detail}",
            hold_phone=True,
            return_to_phone=False,
            stop_account=True,
        )

    def complete_phone_prompt(self, phone, email):
        full_phone = ""
        if (
            not self.phone_input_visible()
            and not self.open_phone_input_if_prompted()
            and not self.back_to_phone_prompt()
        ):
            raise BrowserBlocked("提交新手机号前找不到手机号输入页，停止避免误填")

        def submit_phone():
            nonlocal full_phone
            self.capture_network_diagnostics("手机号提交前")
            full_phone = self.fill_phone_input(phone)
            self.click_primary_action()
            self.phone_submitted = True
            self.bind_phone_proxy(phone, email, stage="submitted")
            self.capture_network_diagnostics("手机号提交后")

        self._step("授权手机号", submit_phone)
        next_step = self.wait_code_input_after_phone()
        if next_step == "password":
            self._step("手机号登录密码", lambda: (
                self.fill_password_input(),
                self.click_primary_action()
            ))
            return full_phone

        code = self.poll_sms(phone)
        if not code:
            raise PhoneRetry(f"短信验证码 {SMS_TIMEOUT_SECONDS}s 超时", hold_phone=True, return_to_phone=True)
        log(f"  SMS: {code}")
        self._step("短信验证码", lambda: (
            self.fill_code_input(code),
            self.click_primary_action()
        ))
        self.phone_code_submitted = True
        return full_phone

    def verify_phone_if_requested(self, email):
        last_phone_error = ""
        phone_attempt = 0
        while True:
            phone_attempt += 1
            if PHONE_RETRY_LIMIT > 0 and phone_attempt > PHONE_RETRY_LIMIT:
                raise FatalError(f"授权阶段换号重试已达上限: {last_phone_error}")
            attempt_label = f"{phone_attempt}/{PHONE_RETRY_LIMIT}" if PHONE_RETRY_LIMIT > 0 else f"{phone_attempt}/不限"
            forced_phone_active = bool(FORCED_PHONE and phone_attempt == 1)
            if forced_phone_active:
                phone = FORCED_PHONE
                try:
                    lookup = api("GET", f"/api/phones/{phone}")
                    item = lookup.get("item") or {}
                except Exception:
                    item = {}
            else:
                purchase = api("POST", "/api/purchase", {
                    "email": email,
                    "proxy": PROXY,
                    "phoneAttemptLimit": PHONE_RETRY_LIMIT if PHONE_RETRY_LIMIT > 0 else 5,
                    "teleAttemptLimit": PHONE_PURCHASE_ATTEMPT_LIMIT,
                })
                item = purchase.get("item") or {}
                phone = item.get("phoneNumber")
                if not phone:
                    raise FatalError("授权阶段拿号成功但返回缺少手机号")
            self.phone_submitted = False
            self.phone_code_submitted = False
            forced_note = "指定" if forced_phone_active else "自动"
            log(f"📱 {phone}  授权手机号尝试 {attempt_label} ({forced_note})")
            identity_proxy = item.get("identityProxy") if isinstance(item, dict) else None
            if isinstance(identity_proxy, dict):
                label = identity_proxy.get("region") or identity_proxy.get("proxyName") or identity_proxy.get("proxyUrl") or ""
                if label:
                    log(f"  当前手机号使用代理归属: {label}")
            try:
                full_phone = self.complete_phone_prompt(phone, email)
                return phone, full_phone
            except PhoneRetry as e:
                last_phone_error = str(e)
                log(f"  当前手机号不可用: {e}", "warn")
                was_submitted = self.phone_submitted
                rate_limited = "电话验证请求过多" in last_phone_error or "phone_rate_limited" in last_phone_error
                if forced_phone_active:
                    self.hold_phone(phone, f"指定手机号未完成: {e}")
                    if rate_limited:
                        raise BrowserBlocked(f"指定手机号触发电话验证冷却，稍后重试: {e}") from e
                    raise FatalError(f"指定手机号不可用: {e}") from e
                if rate_limited:
                    if was_submitted:
                        log("  电话验证请求过多，保留链接等待冷却", "warn")
                    self.hold_phone(phone, str(e))
                    raise BrowserBlocked(f"电话验证请求过多，稍后重试: {e}") from e
                if e.stop_account:
                    self.hold_phone(phone, str(e))
                    raise BrowserBlocked(
                        f"手机号页无明确号码错误且未推进，停止当前账号避免连续烧号: {e}"
                    ) from e
                if e.cancel_phone:
                    self.cancel_phone(phone, str(e))
                elif e.hold_phone or was_submitted:
                    if was_submitted:
                        log("  手机号已提交给授权页，保留链接不放回库存", "warn")
                    self.hold_phone(phone, str(e))
                else:
                    self.release_phone(phone, str(e))
                if (e.return_to_phone or was_submitted) and not self.back_to_phone_prompt():
                    raise BrowserBlocked("换号前无法返回手机号输入页，停止避免误填验证码框")
                self.phone_submitted = False
                self.phone_code_submitted = False
                continue
            except BrowserBlocked:
                if self.phone_submitted:
                    self.hold_phone(phone, "授权阶段风控，手机号已提交")
                else:
                    self.release_phone(phone, "授权阶段风控，手机号未提交")
                self.phone_submitted = False
                self.phone_code_submitted = False
                raise

    def drive_auth_flow(self, email):
        phone = ""
        full_phone = ""
        deadline = time.time() + 300
        last_log = 0
        attempted_primary_signatures = set()
        profile_submitted = False
        self._auth_error_recoveries = 0
        while time.time() < deadline:
            self.wait_ready(timeout=2)
            url = (self.d.current_url or "").lower()
            text = self.visible_text().lower()

            if "/api/auth/error" in url or "/auth/error" in url:
                if self.wait_and_solve_cloudflare_challenge():
                    continue
                if not phone and not self.phone_submitted:
                    raise AuthSessionEnded("授权错误页验证未通过，重新获取授权链接")
                raise BrowserBlocked(f"授权登录进入错误页: {self.d.current_url[:120]}")
            if self.cloudflare_challenge_visible():
                if self.solve_cloudflare_challenge():
                    continue
                if not phone and not self.phone_submitted:
                    raise AuthSessionEnded("授权验证未通过，重新获取授权链接")
                raise BrowserBlocked("授权登录 Cloudflare 验证未通过")
            proxy_block = self.proxy_block_reason()
            if proxy_block:
                raise BrowserBlocked(f"授权登录代理被拒: {proxy_block}")
            page_kind = self.classify_auth_page()
            if time.time() - last_log >= 12:
                log(f"  授权页状态: {page_kind} | {self.d.title} | {self.d.current_url[:100]}")
                last_log = time.time()

            if page_kind == "error" or self.transient_auth_error_visible() or self.is_error_page():
                # Failed-to-fetch / 不明なエラー: recover, never click account/delete controls.
                if not hasattr(self, "_auth_error_recoveries"):
                    self._auth_error_recoveries = 0
                self._auth_error_recoveries += 1
                if self._auth_error_recoveries <= 3 and self.recover_transient_auth_error():
                    time.sleep(2)
                    continue
                if phone or self.phone_submitted:
                    raise BrowserBlocked(f"授权页面临时错误，但手机号已参与，停止避免重复消耗: {self.d.title}")
                raise AuthSessionEnded(f"授权页面临时错误，重新获取授权链接: {self.d.title}")

            if self.auth_session_ended_visible() or page_kind == "session_ended":
                if phone or self.phone_submitted:
                    raise BrowserBlocked("授权会话结束，但手机号已参与，停止避免重复消耗")
                raise AuthSessionEnded("授权会话已结束")
            route_error = self.auth_route_error_visible()
            if route_error:
                if phone or self.phone_submitted:
                    raise BrowserBlocked(f"授权页面临时错误，但手机号已参与，停止避免重复消耗: {route_error}")
                raise AuthSessionEnded(f"授权页面临时错误，重新获取授权链接: {route_error}")
            invalid_credentials = self.invalid_login_credentials_visible()
            if invalid_credentials:
                if self.try_one_time_code_login(email):
                    continue
                raise FatalError(f"授权登录密码错误: {invalid_credentials}")

            if page_kind == "oauth_callback" or "localhost:1455" in url or ("code=" in url and "auth.openai.com" not in url):
                return phone, full_phone
            if page_kind == "choose_account" or "choose-an-account" in url:
                if self.is_error_page() or self.transient_auth_error_visible():
                    continue
                self._step("选择账户", lambda: self._click_account_button())
                continue
            if page_kind == "oauth_consent":
                log("  已识别 OAuth 最终确认页，交给回调阶段点击 Continue")
                return phone, full_phone
            if is_plain_chatgpt_home(self.d.current_url):
                if phone or self.phone_submitted:
                    raise BrowserBlocked("OAuth 授权返回 ChatGPT 首页，但手机号已参与，停止避免重复消耗")
                raise AuthSessionEnded("OAuth 授权返回 ChatGPT 首页，重新获取授权链接")
            if "add-email" in url:
                return phone, full_phone
            if self.phone_code_submitted and page_kind == "phone":
                # State wins over stale route/title: once the SMS code has been
                # submitted, never fetch/fill the same code again.  The current
                # phone page is still finishing its submit/navigation.  Wait for
                # the controls/route to change; a real button-only confirmation
                # is classified separately as phone_confirmation below.
                snippet = " ".join(self.visible_text().split())[:180]
                log(f"  短信验证码已提交，重新识别当前页面并等待推进: {snippet}")
                time.sleep(2)
                continue
            rate_limit_reason = self.phone_verification_rate_limited_reason()
            if rate_limit_reason:
                if phone or self.phone_submitted:
                    raise PhoneRetry(
                        f"电话验证请求过多，稍后再试: {rate_limit_reason}",
                        hold_phone=True,
                        return_to_phone=False,
                    )
                raise BrowserBlocked(f"电话验证请求过多，稍后再试: {rate_limit_reason}")
            if self.phone_input_visible():
                phone, full_phone = self.verify_phone_if_requested(email)
                continue
            if page_kind == "phone_confirmation" or self.phone_code_confirmation_pending():
                snippet = " ".join(self.visible_text().split())[:180]
                log(f"  短信验证码已提交，按当前页面执行最终确认: {snippet}")
                if self.click_primary_action(optional=True):
                    log("  已点击最终确认，继续等待 OAuth 回调")
                    continue
                # Some variants navigate automatically after OTP verification.
                # Stay on this state instead of polling the same SMS or discarding
                # the bound phone merely because the OTP input is gone.
                time.sleep(2)
                continue
            if self.whatsapp_code_prompt_visible():
                if phone or self.phone_submitted:
                    raise PhoneRetry("手机号只进入 WhatsApp 验证", hold_phone=True, return_to_phone=True)
                raise FatalError("当前账号要求 WhatsApp 验证，无法使用短信接码")
            if (page_kind == "phone_code" or self.phone_verification_prompt_visible()) and not self.phone_input_visible():
                if self.open_phone_input_if_prompted():
                    continue
                rate_limit_reason = self.phone_verification_rate_limited_reason()
                if rate_limit_reason:
                    raise BrowserBlocked(f"电话验证请求过多，稍后再试: {rate_limit_reason}")
                whatsapp_reason = self.whatsapp_verification_reason()
                if whatsapp_reason:
                    raise FatalError(f"当前账号要求 WhatsApp 验证，无法使用短信接码: {whatsapp_reason}")
                if self.code_input_elements():
                    existing_phone = phone or FORCED_PHONE
                    if not existing_phone:
                        raise BrowserBlocked("授权页正在等待既有手机号验证码，但未配置可复用手机号链接")
                    phone = existing_phone
                    self.phone_submitted = True
                    log(f"📱 {phone}  复用既有手机号接码")
                    code = self.poll_sms(phone)
                    if not code:
                        self.hold_phone(phone, "既有手机号短信验证码超时")
                        raise BrowserBlocked("既有手机号短信验证码超时，保留链接稍后重试")
                    log(f"  SMS: {code}")
                    self._step("既有手机号短信验证码", lambda: (
                        self.fill_code_input(code),
                        self.click_primary_action()
                    ))
                    self.phone_code_submitted = True
                    continue
                raise BrowserBlocked("授权页停在手机号验证页，但没有可提交的手机号输入框")
            if self.email_input_visible():
                self.start_email_code_batch(email, reason="提交授权邮箱")
                self._step("授权邮箱", lambda: (
                    self.fill_email_input(email),
                    self.click_primary_action()
                ))
                continue
            if self.password_input_visible():
                self._step("授权密码", lambda: (
                    self.fill_password_input(),
                    self.click_primary_action()
                ))
                continue
            if not profile_submitted and self.fill_profile_if_present():
                profile_submitted = True
                continue
            if self.code_input_visible():
                if self.phone_code_submitted:
                    if time.time() - last_log >= 15:
                        snippet = " ".join(self.visible_text().split())[:180]
                        log(f"  短信验证码已提交，等待授权页面推进: {snippet}")
                        last_log = time.time()
                    if self.click_primary_action(optional=True):
                        continue
                    time.sleep(2)
                    continue
                code = self.poll_email(email)
                if not code:
                    raise FatalError("授权阶段邮箱验证码超时")
                self._step("授权邮箱验证码", lambda: (
                    self.fill_code_input(code),
                    self.click_primary_action()
                ))
                continue
            if page_kind == "oauth_consent" or is_oauth_consent_page(self.d.current_url, text):
                log("  已识别 OAuth 最终确认页，交给回调阶段点击 Continue")
                return phone, full_phone
            signature = self.page_signature()
            if signature in attempted_primary_signatures:
                snippet = " ".join(self.visible_text().split())[:240]
                raise BrowserBlocked(f"授权页面签名无变化，停止重复点击: {snippet}")
            if self.click_primary_action(optional=True):
                attempted_primary_signatures.add(signature)
                continue

            if time.time() - last_log >= 15:
                log(f"  等待授权页面推进: {self.d.title} | {self.d.current_url[:100]}")
                if text:
                    log(f"  页面文本: {' '.join(text.split())[:180]}")
                last_log = time.time()
            time.sleep(2)
        raise BrowserBlocked("授权登录流程超时")

    def open_signup_phone_form(self):
        self.launch()
        self.apply_cf_clearance()

        self.d.get("https://chatgpt.com/auth/login?intent=signup")
        time.sleep(12)
        log(f"注册: {self.d.title}")

        self._step("Cookie", lambda: self.click_optional("Accept all"))

        self._step("展开手机表单", lambda: (
            self.click("Continue with phone"), time.sleep(4)
        ))

    def complete_registration_with_phone(self, phone, email):
        self.current_email = str(email or self.current_email or "").strip()
        self.signup_password = ensure_signup_password(self.current_email, create=True)
        full_phone = "+" + re.sub(r'\D', '', phone)
        log(f"📱 {phone}  📧 {email}")

        def submit_phone():
            self.fill("input[name=phoneNumberInput]", full_phone)
            self.click("Continue")
            self.phone_submitted = True
            self.bind_phone_proxy(phone, email, stage="submitted")

        self._step("填手机号", submit_phone)
        self.wait_password_input_after_phone()
        log(f"→ {self.d.title}")

        self._step("填密码", lambda: (
            self.fill("input[name=new-password]", self.signup_password, sensitive=True),
            self.click("Continue")
        ))
        self.wait_contact_verification_after_password()
        log(f"→ {self.d.title}")

        code = self.poll_sms(phone)
        if not code:
            raise PhoneRetry(f"短信验证码 {SMS_TIMEOUT_SECONDS}s 超时", hold_phone=True, return_to_phone=True)
        log(f"  SMS: {code}")

        self._step("短信验证", lambda: (
            self.fill("input[name=code]", code),
            self.click("Continue")
        ))
        time.sleep(3)
        log(f"→ {self.d.title}")

        self._step("姓名年龄", lambda: (
            self.fill("input[name=name]", self.display_name),
            self.fill("input[name=age]", self.signup_age),
            self.click("Finish creating account")
        ))
        time.sleep(8)
        log(f"✅ 注册完成: {self.d.title}")
        return full_phone

    def register_with_phone(self, phone, email):
        self.open_signup_phone_form()
        return self.complete_registration_with_phone(phone, email)

    # ── 步骤执行器（带错误恢复）──────────────────────────
    def _step(self, name, fn):
        """执行一个步骤，出错时刷新并从当前页重试"""
        self.flow_stage = str(name or self.flow_stage)
        for attempt in range(MAX_RETRIES):
            try:
                self.wait_ready()
                # 检查是否错误页
                if self.is_error_page():
                    log(f"  [{name}] 检测到错误页，刷新...", "warn")
                    self.d.refresh(); time.sleep(8)
                    continue
                fn()
                self.checkpoint_browser_state(name)
                return
            except StepError as e:
                log(f"  [{name}] {e} (attempt {attempt+1}/{MAX_RETRIES})", "warn")
                if attempt < MAX_RETRIES - 1:
                    self.d.refresh(); time.sleep(8)
            except Exception as e:
                log(f"  [{name}] {e}", "error")
                raise
        raise FatalError(f"步骤 [{name}] 失败，已重试{MAX_RETRIES}次")

    # ── 主流程 ───────────────────────────────────────────
    def run(self):
        log("=" * 55)
        log("邮箱注册 → 授权登录 → 导入")
        log("=" * 55)

        phone = email = full_phone = ""
        completed_success = False
        try:
            if not PROXY:
                raise FatalError("未配置注册代理，已阻止直连注册")
            if is_blocked_direct_proxy(PROXY):
                raise FatalError(f"注册代理是 DIRECT 直连端口，已阻止: {PROXY}")

            # ═══ 准备 ═══
            email = self.prepare_email()
            self.current_email = email
            self.flow_stage = "准备邮箱"
            self.display_name = signup_display_name(email)
            self.signup_age = signup_age(email)
            log(f"  注册资料名: {self.display_name}")
            if AUTH_ONLY:
                self.signup_password = ensure_signup_password(email, create=False)
                if not saved_signup_password(email):
                    log("  未找到该邮箱的存档密码，使用备用密码或验证码登录", "warn")
                log(f"  auth-only 模式，跳过注册阶段，直接重新授权: {email}", "warn")
                self.launch()
                self.apply_cf_clearance()
            elif email_registration_completed(email):
                self.signup_password = ensure_signup_password(email, create=False)
                if not saved_signup_password(email):
                    log("  该邮箱已是待授权记录但无存档密码，使用备用密码或验证码登录", "warn")
                log(f"  邮箱注册阶段已有记录，直接进入授权: {email}", "warn")
                move_registered_mail_to_new_group(email)
                stage_record = email_stage_record(email)
                if not (
                    stage_record.get("oauthStoredInMailAdmin")
                    or stage_record.get("webAccessTokenStoredInMailAdmin")
                ):
                    archive_registered_email_in_mail_admin(
                        email,
                        self.signup_password,
                        "已注册账号重新进入 OAuth / 手机号授权",
                    )
                self.launch()
                self.apply_cf_clearance()
            else:
                if MANUAL_MODE:
                    self.launch()
                    self.apply_cf_clearance()
                    self.open_signup_email_form()
                    self.fill_any(["input[type=email]", "input[name=email]"] , email)
                    self.checkpoint_browser_state("人工接管：邮箱已填入，等待手动操作")
                    log("🖐 人工接管模式：邮箱、指纹、代理已准备；未点击提交，请在 VNC 中手动操作。完成后请在控制台点停止。", "warn")
                    # Stay attached until the control plane sends stop().  The
                    # manager treats that as a clean manual handoff and returns
                    # the mailbox to its source group rather than marking it bad.
                    while True:
                        time.sleep(30)
                self.signup_password = ensure_signup_password(email, create=True)
                log("  已为该邮箱准备随机注册密码并写入本地记录")
                self.complete_registration_with_email(email)
                mark_email_registration_completed(email, self.signup_password)
                mark_email_flow_state(email, "registered", registered=True, retryable=True)
                move_registered_mail_to_new_group(email)
                archive_registered_email_in_mail_admin(
                    email,
                    self.signup_password,
                    "邮箱注册已完成，等待 OAuth / 手机号",
                )

            if not GET_REFRESH_TOKEN:
                log("✅ 邮箱注册阶段完成；按配置跳过 OAuth / RT / Sub2API")
                self.checkpoint_browser_state("注册完成（未获取 RT）")
                completed_success = True
                return True

            # Registration may already have created a valid ChatGPT Web session.
            # Preserve that AT before navigating into OAuth, where an add-phone
            # wall can block the callback/code exchange that creates the RT.
            self.persist_chatgpt_web_session(email)

            # ═══ Part 2: 授权导入（同一浏览器，保持登录态）═══
            oa_session_id = ""
            oa_state = ""
            for auth_attempt in range(1, 4):
                if auth_attempt > 1:
                    self.refresh_cf_clearance()
                oa = api("GET", "/api/sub2api/openai-auth-url")
                oa_url = oa.get("auth_url") or oa.get("url", "")
                oa_session_id = oa.get("session_id") or oa.get("sessionId", "")
                oa_state = oa.get("state", "")
                if not oa_url or not oa_session_id:
                    raise FatalError("授权链接返回缺少 auth_url/session_id")
                log("🔗 已获取授权会话（标识已隐藏）")

                self.ensure_cf_clearance(oa_url)
                self.apply_cf_clearance()
                self.d.get(oa_url)
                time.sleep(8)
                log(f"授权页: {self.d.title} | {self.d.current_url[:80]}")
                self.checkpoint_browser_state("OAuth 授权登录")

                try:
                    phone, full_phone = self.drive_auth_flow(email)
                    break
                except AuthSessionEnded as e:
                    if auth_attempt >= 3:
                        raise BrowserBlocked(f"授权会话重复结束: {e}") from e
                    log("  授权会话已结束，重新获取授权链接", "warn")
                    continue

            # 绑定邮箱
            if "add-email" in self.d.current_url.lower():
                self.start_email_code_batch(email, reason="提交绑定邮箱")
                self._step("绑定邮箱", lambda: (
                    self.fill_any(["input[type=email]", "input[name=email]"], email),
                    self.click_primary_action(), time.sleep(5)
                ))
                log(f"  → {self.d.title}")

                code2 = self.poll_email(email)
                if not code2: raise FatalError("邮箱码超时")
                log(f"  邮箱码: {code2}")

                self._step("邮箱验证", lambda: (
                    self.fill_code_input(code2),
                    self.click_primary_action(), time.sleep(5)
                ))
                log(f"  → {self.d.title}")

            for _ in range(3):
                if self.phone_input_visible():
                    phone, full_phone = self.verify_phone_if_requested(email)
                    break
                if self.phone_verification_prompt_visible() and self.open_phone_input_if_prompted():
                    continue
                break

            # 授权
            if "localhost:1455" not in self.d.current_url and "code=" not in self.d.current_url:
                log(f"授权确认页: {self.d.title}")
                self._step("授权确认", lambda: self.click_primary_action())

            # ═══ Part 3: 捕获回调 → Sub2API 导入 ═══
            log("等待回调 localhost:1455...")
            callback_url = ""
            for _ in range(15):
                url = self.d.current_url
                if "localhost:1455" in url or "code=" in url:
                    callback_url = url
                    log(f"  ✅ 回调: {redact_log_url(url)}")
                    break
                time.sleep(2)

            if not callback_url:
                # 可能在 consent 页没点到
                self._step("重试授权", lambda: self.click_primary_action())
                time.sleep(5)
                for _ in range(10):
                    url = self.d.current_url
                    if "localhost:1455" in url or "code=" in url:
                        callback_url = url
                        log(f"  ✅ 回调: {redact_log_url(url)}")
                        break
                    time.sleep(2)

            if not callback_url:
                raise FatalError("OAuth回调超时")

            self.checkpoint_browser_state("OAuth 回调已获取")
            log("📥 导入...")
            result = api("POST", "/api/sub2api/openai-callback", {
                "redirect_url": callback_url,
                "session_id": oa_session_id,
                "state": oa_state,
                "email": email,
            })
            log(f"  导入结果: {json.dumps(redact_log_payload(result), ensure_ascii=False)[:500]}")
            if ((result.get("opusMail") or {}).get("imported")
                    and (result.get("tokens") or {}).get("hasAccessToken")):
                mark_email_oauth_material_saved(
                    email,
                    has_refresh_token=bool((result.get("tokens") or {}).get("hasRefreshToken")),
                    has_session_token=bool((result.get("tokens") or {}).get("hasSessionToken")),
                )
            if result.get("success") is False:
                sub2api_error = str((result.get("sub2api") or {}).get("error") or "").strip()
                group_error = str((result.get("groupBind") or {}).get("error") or "").strip()
                raise FatalError(
                    "OAuth 已写入 Mail Admin，但最后导入未完成: "
                    + (sub2api_error or group_error or "Sub2API / 分组失败")
                )

            # 清理
            if phone:
                try: api("POST", f"/api/phones/{phone}/finish")
                except: pass

            log("=" * 55)
            log(f"✅ 全部完成! {email}")
            self.checkpoint_browser_state("已完成并导入")
            completed_success = True
            return True

        except BrowserBlocked as e:
            self.failure_kind = "retryable"
            log(f"💀 {e}", "error")
            if email and not completed_success:
                self.finalize_failed_account(email, str(e), retryable=True)
            if self.d and not completed_success and KEEP_BROWSER_ON_FAILURE and MANUAL_MODE:
                self.keep_browser_for_manual_takeover()
            self.close_browser()
            if not completed_success:
                self.reset_failed_browser_profile()
        except FatalError as e:
            if any(token in str(e) for token in ("流程超时", "页面无进展", "OAuth回调超时")):
                self.failure_kind = "retryable"
            log(f"💀 {e}", "error")
            if email and not completed_success:
                self.finalize_failed_account(
                    email,
                    str(e),
                    retryable=(self.failure_kind == "retryable" or email_registration_completed(email)),
                )
            if self.d and not completed_success and KEEP_BROWSER_ON_FAILURE and MANUAL_MODE:
                self.keep_browser_for_manual_takeover()
            self.close_browser()
            if not completed_success:
                self.reset_failed_browser_profile()
        except Exception as e:
            if email and not completed_success:
                try:
                    page_hint = ""
                    try:
                        page_hint = f"{getattr(self.d, 'title', '')} {getattr(self.d, 'current_url', '')}"
                    except Exception:
                        page_hint = ""
                    bad_auth = any(
                        token in page_hint
                        for token in ("不明なエラー", "Failed to fetch", "choose-an-account", "unknown error", "お帰りなさい")
                    ) or "找不到账户" in str(e) or "选择账户" in str(e) or "授权" in str(e)
                    if bad_auth and clear_email_registration_completed(email, reason=str(e)[:160] or page_hint[:160]):
                        log(f"  已清除不可靠的 registered 标记，下次将重跑注册: {email}", "warn")
                        restore_mail_to_source_group(email)
                except Exception as clear_error:
                    log(f"  清理 registered 标记失败: {clear_error}", "warn")
            if email and not completed_success:
                self.finalize_failed_account(
                    email,
                    str(e),
                    retryable=email_registration_completed(email),
                )
            if self.d and not completed_success and KEEP_BROWSER_ON_FAILURE and MANUAL_MODE:
                self.keep_browser_for_manual_takeover()
            self.close_browser()
            if not completed_success:
                self.reset_failed_browser_profile()
        return False

    def _choose_account_candidates(self):
        """Collect visible account controls on /choose-an-account."""
        email = self.current_email or self.requested_email or ""
        display_name = self.display_name or signup_display_name(email)
        try:
            elements = self.d.find_elements(
                By.CSS_SELECTOR,
                "button, a[role='button'], [role='button'], [data-testid*='account'], [data-testid*='Account'], div[role='listitem']",
            )
        except Exception:
            elements = []
        scored = []
        seen = set()
        for element in elements:
            try:
                if not element.is_displayed():
                    continue
                label = element_action_label(element)
                key = (element.id, label)
                if key in seen:
                    continue
                seen.add(key)
                score = choose_account_label_score(
                    label,
                    email=email,
                    display_name=display_name,
                )
                if score <= 0:
                    continue
                scored.append((score, label, element))
            except StaleElementReferenceException:
                continue
            except Exception:
                continue
        # Drop nested avatar/short labels that are already covered by a richer parent label.
        filtered = []
        rich_labels = [" ".join(str(label or "").split()).casefold() for _, label, _ in scored if len(" ".join(str(label or "").split())) > 8]
        for score, label, element in scored:
            preview = " ".join(str(label or "").split())
            compact = re.sub(r"\s+", "", preview)
            if len(compact) <= 3 and any(preview.casefold() in rich or compact.casefold() in re.sub(r"\s+", "", rich) for rich in rich_labels):
                continue
            filtered.append((score, label, element))
        filtered.sort(key=lambda item: (-item[0], -len(" ".join(str(item[1] or "").split())), item[1]))
        return filtered

    def _dump_choose_account_debug(self, candidates=None):
        """Persist a redacted failure package when account selection fails."""
        labels = []
        try:
            elements = self.d.find_elements(By.CSS_SELECTOR, "button, a, [role=button], [role=listitem]")
        except Exception:
            elements = []
        for element in elements[:40]:
            try:
                if not element.is_displayed():
                    continue
                label = element_action_label(element)
                if label:
                    labels.append(label[:120])
            except Exception:
                continue
        if labels:
            log("  选择账户页可见控件: " + " | ".join(labels[:12]), "warn")
        else:
            snippet = " ".join(self.visible_text().split())[:240]
            log(f"  选择账户页无可用控件文本: {snippet}", "warn")

        self.capture_failure_artifact("选择账户", "找不到与当前邮箱匹配的安全账户控件")
        if candidates:
            preview = " | ".join(f"{score}:{label[:40]}" for score, label, _ in candidates[:5])
            log(f"  选择账户候选: {preview}", "warn")

    def _click_account_button(self):
        """choose-account 页面：点匹配当前邮箱/多语言文案的账户"""
        self.wait_ready(timeout=3)
        if self.is_error_page() or self.transient_auth_error_visible():
            raise StepError(f"选择账户页实际是错误页: {self.d.title}")
        email = normalize_email(self.current_email or self.requested_email)

        def label_preview(label):
            return " ".join(str(label or "").split())[:120]

        def still_on_choose_account():
            url = str(getattr(self.d, "current_url", "") or "").lower()
            text = " ".join(self.visible_text().split()).lower()
            if self.is_error_page() or self.transient_auth_error_visible():
                return False
            # URL is authoritative: once we leave /choose-an-account, stop retrying.
            if "choose-an-account" not in url:
                return False
            return (
                "select an account" in text
                or "choose an account" in text
                or "アカウントを選択" in text
                or "选择账户" in text
                or True  # still on the choose-an-account route
            )

        # Re-query candidates every attempt so stale/delete buttons never get a second chance.
        last_candidates = []
        for attempt in range(1, 4):
            candidates = self._choose_account_candidates()
            last_candidates = candidates
            if email:
                email_hits = [item for item in candidates if email in str(item[1] or "").casefold()]
                if email_hits:
                    candidates = email_hits + [item for item in candidates if item not in email_hits]
            if not candidates:
                break
            for score, label, element in candidates:
                try:
                    preview = label_preview(label)
                    if choose_account_label_score(preview, email=email, display_name=self.display_name) <= 0:
                        continue
                    compact = re.sub(r"\s+", "", preview)
                    if len(compact) <= 3 and any(len(re.sub(r"\s+", "", label_preview(c[1]))) > 8 for c in candidates):
                        log(f"  跳过疑似头像缩写控件({score}): {preview[:40]}")
                        continue
                    log(f"  点击账户控件({score}): {preview}")
                    clicked = False
                    try:
                        self.d.execute_script(
                            "arguments[0].scrollIntoView({block:'center', inline:'center'});",
                            element,
                        )
                    except Exception:
                        pass
                    try:
                        chains = ActionChains(self.d).move_to_element(element)
                        try:
                            chains = chains.pause(0.2)
                        except Exception:
                            pass
                        chains.click().perform()
                        clicked = True
                    except Exception:
                        clicked = False
                    if not clicked:
                        try:
                            self.d.execute_script("arguments[0].click();", element)
                            clicked = True
                        except Exception as e:
                            log(f"  账户控件 JS 点击失败: {e}", "warn")
                            continue
                    self._sleep(4)
                    if self.is_error_page() or self.transient_auth_error_visible():
                        log("  选择账户后进入错误页，停止继续点候选", "warn")
                        raise StepError(f"选择账户后进入错误页: {self.d.title}")
                    if still_on_choose_account():
                        log("  账户点击后仍在选择页，尝试下一个候选", "warn")
                        continue
                    if self.auth_session_ended_visible():
                        raise AuthSessionEnded("选择账户后会话结束")
                    return
                except AuthSessionEnded:
                    raise
                except StepError:
                    raise
                except StaleElementReferenceException:
                    break
                except Exception as e:
                    log(f"  账户控件点击失败: {e}", "warn")
                    continue
            self._sleep(1)

        candidates = last_candidates or self._choose_account_candidates()
        needles = [
            email,
            (email.split("@", 1)[0] if email and "@" in email else ""),
            self.display_name or "",
            "Select account",
            "Choose account",
            "Use this account",
            "Continue as",
            "アカウントを選択",
            "このアカウント",
            "选择账户",
            "选择账号",
            "選擇帳戶",
            "계정 선택",
            "Seleccionar cuenta",
            "Choisir un compte",
            "Konto auswählen",
        ]
        needles = [item for item in needles if str(item or "").strip()]
        if self.click_text_element(needles, wait_seconds=2):
            self._sleep(2)
            if self.is_error_page() or self.transient_auth_error_visible():
                raise StepError(f"选择账户后进入错误页: {self.d.title}")
            if not still_on_choose_account():
                return

        self._dump_choose_account_debug(candidates)
        raise StepError("找不到账户按钮")


# ── 入口 ────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ChatGPT 注册 + Sub2API OAuth 导入")
    parser.add_argument("--email", default="", help="指定本次注册使用的邮箱")
    parser.add_argument("--api-base", default="", help="本地 fuckoai API 地址")
    parser.add_argument("--proxy", default="", help="Chrome 代理地址")
    parser.add_argument("--display", default="", help="X11 DISPLAY")
    parser.add_argument("--chrome-binary", default="", help="Chrome 可执行文件路径")
    parser.add_argument("--chromedriver-binary", default="", help="ChromeDriver 可执行文件路径")
    parser.add_argument("--chrome-version", type=int, default=0, help="Chrome 主版本号")
    parser.add_argument("--auth-only", action="store_true", help="跳过注册阶段，仅重新授权并导入")
    parser.add_argument("--skip-refresh-token", action="store_true", help="注册完成后跳过 OAuth、RT 获取和 Sub2API 导入")
    parser.add_argument("--forced-phone", default="", help="指定授权阶段优先使用的手机号")
    args = parser.parse_args()

    if args.api_base:
        API = args.api_base.rstrip("/")
    if args.proxy:
        PROXY = args.proxy
    if args.display:
        DISPLAY = args.display
    if args.chrome_binary:
        CHROME_BINARY = args.chrome_binary
    if args.chromedriver_binary:
        CHROMEDRIVER_BINARY = args.chromedriver_binary
    if args.chrome_version:
        CHROME_VERSION = args.chrome_version
    if args.auth_only:
        AUTH_ONLY = True
    if args.skip_refresh_token:
        GET_REFRESH_TOKEN = False
    if args.forced_phone:
        FORCED_PHONE = args.forced_phone.strip()

    signal.signal(signal.SIGINT, lambda s, f: sys.exit(1))
    bot = SignupBot(email=args.email)
    ok = bot.run()
    if ok:
        sys.exit(0)
    sys.exit(2 if bot.failure_kind == "retryable" else 1)
