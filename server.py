from __future__ import annotations

import json
import errno
import functools
import hashlib
import hmac
import os
import random
import re
import select
import signal
import string
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.parser import Parser
from html import unescape as html_unescape
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, quote_plus, unquote, urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

# Upstream service clients. They live in integrations/ so this file stays a
# server rather than a client library; the names are re-exported because other
# modules and tools import them from here.
from integrations.mail_text import (
    decode_mail_payload,
    enrich_temp_mail_item,
    extract_verification_code_from_mail,
)
from integrations.proxy_config import (
    MIHOMO_DIRECT_PROXY_URL,
    MIHOMO_SUB2API_PROFILES,
    configured_signup_proxy_candidates,
    configured_sub2api_proxy,
    known_mihomo_proxy_name,
    normalize_proxy_region,
    parse_proxy_pool_urls,
    parse_proxy_url,
    proxy_name_for_url,
    proxy_url_from_parsed,
    resolve_proxy_source,
    saved_cliproxy_url,
    sub2api_proxy_from_url,
    sub2api_proxy_key,
)
from integrations.address_profiles import (
    AddressProfileError,
    address_country_catalog,
    fetch_address_profile,
    fetch_us_tax_free_address,
)
from integrations.common import http_json
from integrations.core_utils import (
    decode_jwt_payload,
    email_key,
    generate_random_local_part,
    load_json_file,
    normalize_fixed_price_value,
    now_iso,
    parse_bool_flag,
    parse_positive_int,
    parse_timestamp,
    save_json_file,
    strip_empty_values,
    timestamp_is_future,
)
from integrations.file_library import (
    FILE_LIBRARY_MAX_REQUEST_BYTES,
    FileLibraryError,
    create_file as create_library_file,
    delete_file as delete_library_file,
    get_file as get_library_file,
    list_files as list_library_files,
    update_file as update_library_file,
)
from integrations.outlook_register_manager import OUTLOOK_REGISTER_MANAGER
from integrations.herosms import (
    NORMALIZED_STATES,
    STATUS_LABELS,
    HeroSmsClient,
    HeroSmsError,
    PurchaseError,
    TeleAutoClient,
    TeleAutoError,
)
from integrations.outlook_email_client import (
    OutlookEmailAdminClient,
    OutlookEmailClient,
    OutlookEmailError,
)
from integrations.opus_mail_client import OpusMailClient, OpusMailError
from integrations.opus_mail_admin_reader import (
    OpusMailAdminReader,
    OpusMailAdminReaderError,
)
from integrations.sms import (
    ACTIVE_STATUS_MAP,
    ActivationStore,
    CATALOG_CACHE_PATH,
    COUNTRY_ALIASES_BY_NAME,
    DEFAULT_SERVICE_CODE,
    DEFAULT_SERVICE_NAME,
    PHONE_CODE_USAGE_LOCK,
    PHONE_CODE_USAGE_PATH,
    PURCHASE_CONFIG_PATH,
    PURCHASE_FILTER_KEYS,
    PURCHASE_GROUP_CURSOR_LOCK,
    PURCHASE_GROUP_NEXT_INDEX,
    advance_purchase_group_cursor,
    advance_purchase_group_cursor_after_group,
    bind_phone_proxy,
    build_country_lookup,
    build_purchase_attempts,
    build_purchase_group_label,
    build_purchase_item,
    build_service_lookup,
    build_tele_auto_purchase_item,
    execute_purchase,
    fetch_upstream_activations,
    filter_activations,
    find_activation_by_phone,
    find_bound_phone_for_email,
    find_by_code,
    find_local_tele_activation_by_phone,
    get_cached_countries,
    get_cached_operators,
    get_country_search_fields,
    get_current_filtered_activations,
    get_display_name,
    get_enabled_purchase_groups,
    get_filters,
    get_phone_proxy_binding,
    get_purchase_config,
    get_purchase_defaults,
    get_purchase_group_start_index,
    get_purchase_settings,
    import_active_activations,
    is_purchase_group_configured,
    is_tele_auto_record,
    list_local_tele_activations,
    list_local_tele_activations_by_phone,
    load_catalog_cache,
    load_phone_code_usage,
    merge_activation_items,
    normalize_phone_key,
    normalize_purchase_group,
    normalize_record,
    phone_activation_link_payload,
    phone_code_max_per_window,
    phone_code_max_total,
    phone_code_quota_status,
    mark_phone_cooldown,
    phone_code_window_seconds,
    phone_detail_payload,
    phone_pool_payload,
    phone_proxy_compatibility,
    phone_record_reusable_for_sms,
    phone_record_sort_value,
    public_phone_pool_status,
    purchase_context_proxy,
    purchase_with_fallback,
    record_phone_code_usage,
    resolve_selections,
    save_catalog_cache,
    save_phone_code_usage,
    search_countries_by_name,
    search_country_items,
    serialize_purchase_settings,
    sync_record_status,
    update_purchase_settings,
    update_tele_record_from_status,
)
from integrations.sub2api_client import Sub2ApiClient, Sub2ApiError, sub2api_account_group_ids
from integrations.temp_mail_client import TempMailClient, TempMailError
from integrations.text_utils import (
    ZH_COUNTRY_CHAR_MAP,
    collect_string_values,
    html_to_text,
    normalize_text,
)

ROOT = Path(__file__).resolve().parent
APP_NAME = "automyai"
ADMIN_COOKIE_NAME = "automyai_admin"
CONFIG_PATH = ROOT / "config.json"
FRONTEND_DIR = ROOT / "frontend"

EMAIL_QUEUE_PATH = ROOT / "data/email_queue.json"
PROXY_USAGE_PATH = ROOT / "data/proxy_usage.json"
PROXY_GEO_CACHE_PATH = ROOT / "data/proxy_geo_cache.json"
IDENTITY_BINDINGS_PATH = ROOT / "data/identity_region_bindings.json"
SUB2API_REAUTH_ATTEMPTS_PATH = ROOT / "data/sub2api_reauth_attempts.json"
UC_SIGNUP_EMAIL_STAGE_PATH = ROOT / "data/uc_signup_email_stage.json"
SIGNUP_URL = "https://chatgpt.com/auth/login?intent=signup"
APP_SETTING_FIELDS = (
    "HOST",
    "PORT",
    "HERO_SMS_API_KEY",
    "HERO_SMS_API_URL",
    "TELE_AUTO_ENABLED",
    "TELE_AUTO_API_URL",
    "TELE_AUTO_USERNAME",
    "TELE_AUTO_PASSWORD",
    "TEMP_MAIL_API_URL",
    "TEMP_MAIL_ADMIN_PASSWORD",
    "OUTLOOK_EMAIL_API_URL",
    "OUTLOOK_EMAIL_API_KEY",
    "OUTLOOK_EMAIL_ADMIN_PASSWORD",
    "MAIL_SOURCE_GROUP_NAME",
    "MAIL_PENDING_GROUP_NAME",
    "MAIL_SUCCESS_GROUP_NAME",
    "MAIL_BAD_GROUP_NAME",
    "SUB2API_API_URL",
    "SUB2API_ADMIN_EMAIL",
    "SUB2API_ADMIN_PASSWORD",
    "SUB2API_ADMIN_TOKEN",
    "SUB2API_MONITOR_ENABLED",
    "SUB2API_MONITOR_GROUP_NAME",
    "SUB2API_IMPORT_GROUP_NAMES",
    "SUB2API_MONITOR_MIN_OK_ACCOUNTS",
    "SUB2API_MONITOR_BUSY_MIN_OK_ACCOUNTS",
    "SUB2API_MONITOR_INTERVAL_SECONDS",
    "SUB2API_MONITOR_BUSY_WINDOW_SECONDS",
    "SUB2API_MONITOR_TRIGGER_COOLDOWN_SECONDS",
    "SUB2API_MONITOR_MAX_START_ACCOUNTS",
    "SUB2API_MONITOR_IMPORT_MAIL_SOURCE",
    "CPA_MONITOR_ENABLED",
    "CPA_MONITOR_MIN_OK_ACCOUNTS",
    "CPA_MONITOR_INTERVAL_SECONDS",
    "CPA_MONITOR_TRIGGER_COOLDOWN_SECONDS",
    "CPA_MONITOR_REGISTER_COUNT",
    "CPA_MONITOR_REGISTER_THREADS",
    "CPA_MONITOR_PROXY",
    "SIGNUP_PASSWORD",
    "SIGNUP_NAME",
    "SIGNUP_AGE",
    "BROWSER_DISPLAY",
    "BROWSER_PROXY",
    "UC_SIGNUP_PROXY",
    "SIGNUP_PROXY_MODE",
    "SIGNUP_PROXY_REGION",
    "SIGNUP_PROXY_CUSTOM_URL",
    "CLIPROXY_PROXY_URL",
    "PROXY_POOL_URLS",
    "PROXY_RANDOMIZE",
    "PROXY_USAGE_WINDOW_SECONDS",
    "PROXY_USAGE_MAX_PER_WINDOW",
    "PHONE_CODE_WINDOW_SECONDS",
    "PHONE_CODE_MAX_PER_WINDOW",
    "PHONE_CODE_MAX_TOTAL",
    "PHONE_WHATSAPP_COOLDOWN_SECONDS",
    "PHONE_SMS_COOLDOWN_SECONDS",
    "SUB2API_PROXY_REGION",
    "SUB2API_PROXY_URL",
    "SUB2API_PROXY_NAME",
    "SUB2API_IMPORT_USE_SIGNUP_PROXY",
    "UC_SIGNUP_PHONE_RETRIES",
    "UC_SIGNUP_SMS_TIMEOUT_SECONDS",
    "UC_SIGNUP_SMS_POLL_INTERVAL_SECONDS",
    "UC_SIGNUP_PHONE_PASSWORD_PAGE_TIMEOUT",
    "UC_SIGNUP_CF_CLEARANCE_ENABLED",
    "UC_SIGNUP_CF_CLEARANCE_API_URL",
    "UC_SIGNUP_CF_CLEARANCE_TARGET_URL",
    "UC_SIGNUP_CF_CLEARANCE_TIMEOUT_SECONDS",
    "UC_SIGNUP_CF_CLEARANCE_CACHE_SECONDS",
    "UC_SIGNUP_CHROME_BINARY",
    "UC_SIGNUP_CHROME_VERSION",
    "UC_SIGNUP_KEEP_BROWSER_ON_FAILURE",
    "UC_SIGNUP_KEEP_BROWSER_SECONDS",
    "UC_SIGNUP_IDLE_TIMEOUT_SECONDS",
    "UC_SIGNUP_RETRYABLE_EMAIL_COOLDOWN_SECONDS",
    "UC_SIGNUP_PROFILE_BASE_DIR",
    "UC_SIGNUP_FINGERPRINT_ENABLED",
    "OAI_FINGERPRINT_ENABLED",
    "OAI_FINGERPRINT_PROVIDER",
    "OAI_FINGERPRINT_PRESET",
    "OAI_FINGERPRINT_BROWSER_VERSION",
    "OAI_FINGERPRINT_SEED",
    "OAI_FINGERPRINT_STRICT",
    "OAI_FINGERPRINT_TIMEOUT_SECONDS",
    "OAI_FINGERPRINT_SDK_DIR",
    "OAI_FINGERPRINT_AUTHORIZED_API_BASE_URL",
    "OAI_FINGERPRINT_AUTHORIZED_HEADERS_FILE",
	"OAI_FINGERPRINT_CLOUD_ENABLED",
	"OAI_FINGERPRINT_CLOUD_API_BASE_URL",
	"OAI_FINGERPRINT_CLOUD_HEADERS_FILE",
	"OAI_FINGERPRINT_CLOUD_INCLUDE_MAC",
    "OAI_FINGERPRINT_API_URL",
    "OAI_FINGERPRINT_API_KEY_FILE",
	"ROXY_OPENAPI_ENABLED",
	"ROXY_OPENAPI_URL",
	"ROXY_OPENAPI_KEY_FILE",
	"ROXY_OPENAPI_TIMEOUT_SECONDS",
    "REQUEST_TIMEOUT_MS",
    "ENABLE_CORS",
    "CORS_ALLOWED_ORIGINS",
    "PUBLIC_STATUS_ENABLED",
    "PUBLIC_STATUS_ALLOW_ORIGINS",
    "PUBLIC_STATUS_TOKEN",
    "STORE_FILE",
    "PURCHASE_CONFIG_FILE",
    "CPA_ENABLED",
    "CPA_AUTH_DIR",
    "CPA_REMOTE_URL",
    "CPA_MANAGEMENT_KEY",
    "CPA_API_KEY",
    "GROK2API_BASE_URL",
    "GROK2API_ADMIN_KEY",
    "GROK2API_POOL",
    "GROK_SIGNUP_PROXY",
    "GROK_CF_CLEARANCE_ENABLED",
    "GROK_CF_CLEARANCE_API_URL",
    "GROK_CF_CLEARANCE_TARGET_URL",
    "GROK_CF_CLEARANCE_TIMEOUT_SECONDS",
    "GROK_DOMAIN_ROOT",
    "DOMAIN_MAIL_ROOT",
    "DOMAIN_MAIL_PREFER_SUBDOMAIN",
    "DOMAIN_MAIL_SUBDOMAINS",
    "DOMAIN_MAIL_NAME_STYLE",
    "DOMAIN_MAIL_NAME_DIGITS",
    "DOMAIN_MAIL_GROUP_NAME",
    "MAIL_PREFER_INVENTORY",
    "GROK_MAIL_PENDING_GROUP_NAME",
    "GROK_MAIL_SUCCESS_GROUP_NAME",
    "GROK_MAIL_OLD_GROUP_NAME",
    "TRAFFIC_METER_ENABLED",
    "UI_THEME",
    "UI_STATIC_DIR",
)

UI_SETTINGS_PUBLIC_FIELDS = (
    "CPA_ENABLED",
    "CPA_AUTH_DIR",
    "CPA_REMOTE_URL",
    "GROK2API_BASE_URL",
    "GROK2API_POOL",
    "DOMAIN_MAIL_ROOT",
    "DOMAIN_MAIL_PREFER_SUBDOMAIN",
    "DOMAIN_MAIL_SUBDOMAINS",
    "DOMAIN_MAIL_NAME_STYLE",
    "DOMAIN_MAIL_NAME_DIGITS",
    "MAIL_PREFER_INVENTORY",
    "MAIL_SOURCE_GROUP_NAME",
    "MAIL_PENDING_GROUP_NAME",
    "MAIL_SUCCESS_GROUP_NAME",
    "MAIL_BAD_GROUP_NAME",
    "GROK_MAIL_PENDING_GROUP_NAME",
    "GROK_MAIL_SUCCESS_GROUP_NAME",
    "SUB2API_API_URL",
    "SUB2API_IMPORT_GROUP_NAMES",
    "SIGNUP_PROXY_MODE",
    "SIGNUP_PROXY_REGION",
    "SIGNUP_PROXY_CUSTOM_URL",
    "CLIPROXY_PROXY_URL",
    "SUB2API_PROXY_REGION",
    "SUB2API_PROXY_URL",
    "SUB2API_PROXY_NAME",
    "SUB2API_IMPORT_USE_SIGNUP_PROXY",
    "TRAFFIC_METER_ENABLED",
	"UI_THEME",
	"OAI_FINGERPRINT_CLOUD_ENABLED",
	"OAI_FINGERPRINT_CLOUD_API_BASE_URL",
	"OAI_FINGERPRINT_CLOUD_HEADERS_FILE",
	"OAI_FINGERPRINT_CLOUD_INCLUDE_MAC",
	"ROXY_OPENAPI_ENABLED",
	"ROXY_OPENAPI_URL",
	"ROXY_OPENAPI_KEY_FILE",
	"ROXY_OPENAPI_TIMEOUT_SECONDS",
)
UI_SETTINGS_SECRET_FIELDS = (
    "CPA_MANAGEMENT_KEY",
    "CPA_API_KEY",
    "GROK2API_ADMIN_KEY",
)
UI_SETTINGS_FIELDS = frozenset((*UI_SETTINGS_PUBLIC_FIELDS, *UI_SETTINGS_SECRET_FIELDS))
UI_THEME_VALUES = frozenset(("light", "dark-purple", "dark-cyberpunk", "dark-matrix", "dark-obsidian"))

DEFAULT_APP_SETTINGS: dict[str, Any] = {
    "HOST": "127.0.0.1",
    "PORT": "13030",
    "HERO_SMS_API_KEY": "",
    "HERO_SMS_API_URL": "https://hero-sms.com/stubs/handler_api.php",
    "TELE_AUTO_ENABLED": "true",
    "TELE_AUTO_API_URL": "http://127.0.0.1:8028",
    "TELE_AUTO_USERNAME": "",
    "TELE_AUTO_PASSWORD": "",
    "TEMP_MAIL_API_URL": "",
    "TEMP_MAIL_ADMIN_PASSWORD": "",
    "OUTLOOK_EMAIL_API_URL": "http://127.0.0.1:5010",
    "OUTLOOK_EMAIL_API_KEY": "",
    "OUTLOOK_EMAIL_ADMIN_PASSWORD": "",
    "MAIL_SOURCE_GROUP_NAME": "默认分组",
    "MAIL_PENDING_GROUP_NAME": "gpt_pending_account",
    "MAIL_SUCCESS_GROUP_NAME": "gpt_new_account",
    "MAIL_BAD_GROUP_NAME": "badmail",
    "SUB2API_API_URL": "http://127.0.0.1:8080/api/v1",
    "SUB2API_ADMIN_EMAIL": "",
    "SUB2API_ADMIN_PASSWORD": "",
    "SUB2API_ADMIN_TOKEN": "",
    "SUB2API_MONITOR_ENABLED": "true",
    "SUB2API_MONITOR_GROUP_NAME": "auto",
    "SUB2API_IMPORT_GROUP_NAMES": "auto",
    "SUB2API_MONITOR_MIN_OK_ACCOUNTS": "1",
    "SUB2API_MONITOR_BUSY_MIN_OK_ACCOUNTS": "2",
    "SUB2API_MONITOR_INTERVAL_SECONDS": "30",
    "SUB2API_MONITOR_BUSY_WINDOW_SECONDS": "300",
    "SUB2API_MONITOR_TRIGGER_COOLDOWN_SECONDS": "60",
    "SUB2API_MONITOR_MAX_START_ACCOUNTS": "2",
    "SUB2API_MONITOR_IMPORT_MAIL_SOURCE": "true",
    "CPA_MONITOR_ENABLED": "true",
    "CPA_MONITOR_MIN_OK_ACCOUNTS": "5",
    "CPA_MONITOR_INTERVAL_SECONDS": "60",
    "CPA_MONITOR_MIN_INTERVAL_SECONDS": "10",
    "CPA_MONITOR_MAX_INTERVAL_SECONDS": "300",
    "CPA_MONITOR_HEALTHY_SLOWDOWN_AFTER_SECONDS": "1800",
    "CPA_PUBLIC_WAKE_MIN_INTERVAL_SECONDS": "60",
    "CPA_MONITOR_TRIGGER_COOLDOWN_SECONDS": "0",
    "CPA_MONITOR_REGISTER_COUNT": "2",
    "CPA_MONITOR_REGISTER_THREADS": "2",
    "CPA_MONITOR_PROXY": "",
    "SIGNUP_PASSWORD": "FuckOAI123456!",
    "SIGNUP_NAME": "",
    "SIGNUP_AGE": "18",
    "BROWSER_DISPLAY": ":1",
    "BROWSER_PROXY": "",
    "UC_SIGNUP_PROXY": "",
    "SIGNUP_PROXY_MODE": "custom",
    "SIGNUP_PROXY_REGION": "JP",
    "SIGNUP_PROXY_CUSTOM_URL": "",
    "CLIPROXY_PROXY_URL": "",
    "PROXY_POOL_URLS": "",
    "PROXY_RANDOMIZE": "true",
    "PROXY_USAGE_WINDOW_SECONDS": "86400",
    "PROXY_USAGE_MAX_PER_WINDOW": "3",
    "PHONE_CODE_WINDOW_SECONDS": "3600",
    "PHONE_CODE_MAX_PER_WINDOW": "1",
    "PHONE_CODE_MAX_TOTAL": "3",
    "PHONE_WHATSAPP_COOLDOWN_SECONDS": "21600",
    "PHONE_SMS_COOLDOWN_SECONDS": "1800",
    "SUB2API_PROXY_REGION": "",
    "SUB2API_PROXY_URL": "",
    "SUB2API_PROXY_NAME": "",
    "SUB2API_IMPORT_USE_SIGNUP_PROXY": "false",
    "UC_SIGNUP_PHONE_RETRIES": "0",
    "UC_SIGNUP_SMS_TIMEOUT_SECONDS": "135",
    "UC_SIGNUP_SMS_POLL_INTERVAL_SECONDS": "10",
    "UC_SIGNUP_PHONE_PASSWORD_PAGE_TIMEOUT": "25",
    "UC_SIGNUP_CF_CLEARANCE_ENABLED": "false",
    "UC_SIGNUP_CF_CLEARANCE_API_URL": "http://127.0.0.1:18191/v1",
    "UC_SIGNUP_CF_CLEARANCE_TARGET_URL": "https://chatgpt.com/auth/login?intent=signup",
    "UC_SIGNUP_CF_CLEARANCE_TIMEOUT_SECONDS": "90",
    "UC_SIGNUP_CF_CLEARANCE_CACHE_SECONDS": "1800",
    "UC_SIGNUP_CHROME_BINARY": "",
    "UC_SIGNUP_CHROME_VERSION": "",
    "UC_SIGNUP_KEEP_BROWSER_ON_FAILURE": "false",
    "UC_SIGNUP_KEEP_BROWSER_SECONDS": "0",
    "UC_SIGNUP_IDLE_TIMEOUT_SECONDS": "600",
    "UC_SIGNUP_RETRYABLE_EMAIL_COOLDOWN_SECONDS": "900",
    "UC_SIGNUP_PROFILE_BASE_DIR": "./data/browser_profiles",
    "UC_SIGNUP_FINGERPRINT_ENABLED": "false",
    "OAI_FINGERPRINT_ENABLED": "false",
    "OAI_FINGERPRINT_PROVIDER": "local",
    "OAI_FINGERPRINT_PRESET": "",
    "OAI_FINGERPRINT_BROWSER_VERSION": "",
    "OAI_FINGERPRINT_SEED": "",
    "OAI_FINGERPRINT_STRICT": "false",
    "OAI_FINGERPRINT_TIMEOUT_SECONDS": "15",
    "OAI_FINGERPRINT_SDK_DIR": "",
    "OAI_FINGERPRINT_AUTHORIZED_API_BASE_URL": "",
    "OAI_FINGERPRINT_AUTHORIZED_HEADERS_FILE": "",
	"OAI_FINGERPRINT_CLOUD_ENABLED": "false",
	"OAI_FINGERPRINT_CLOUD_API_BASE_URL": "",
	"OAI_FINGERPRINT_CLOUD_HEADERS_FILE": "",
	"OAI_FINGERPRINT_CLOUD_INCLUDE_MAC": "true",
    "OAI_FINGERPRINT_API_URL": "http://127.0.0.1:50001",
    "OAI_FINGERPRINT_API_KEY_FILE": "",
	"ROXY_OPENAPI_ENABLED": "false",
	"ROXY_OPENAPI_URL": "http://127.0.0.1:50000",
	"ROXY_OPENAPI_KEY_FILE": "/app/data/roxy-openapi/api.key",
	"ROXY_OPENAPI_TIMEOUT_SECONDS": "10",
    "REQUEST_TIMEOUT_MS": "15000",
    "ENABLE_CORS": "true",
    "CORS_ALLOWED_ORIGINS": "https://automyai.kfjie.me",
    "PUBLIC_STATUS_ENABLED": "true",
    "PUBLIC_STATUS_ALLOW_ORIGINS": "https://kfjie.me,https://www.kfjie.me,https://automyai.kfjie.me",
    "PUBLIC_STATUS_TOKEN": "",
    "STORE_FILE": "./data/activations.json",
    "PURCHASE_CONFIG_FILE": "./data/purchase_config.json",
    "CPA_ENABLED": "true",
    "CPA_AUTH_DIR": "/opt/cliproxyapi/auths",
    "CPA_REMOTE_URL": "http://127.0.0.1:8317",
    "CPA_MANAGEMENT_KEY": "",
    "CPA_API_KEY": "",
    "GROK2API_BASE_URL": "http://127.0.0.1:8000",
    "GROK2API_ADMIN_KEY": "",
    "GROK2API_POOL": "basic",
    "GROK_SIGNUP_PROXY": "",
    "GROK_CF_CLEARANCE_ENABLED": "true",
    "GROK_CF_CLEARANCE_API_URL": "http://127.0.0.1:18191/v1",
    "GROK_CF_CLEARANCE_TARGET_URL": "https://accounts.x.ai/sign-up",
    "GROK_CF_CLEARANCE_TIMEOUT_SECONDS": "90",
    "GROK_DOMAIN_ROOT": "",
    "DOMAIN_MAIL_ROOT": "",
    "DOMAIN_MAIL_PREFER_SUBDOMAIN": "true",
    "DOMAIN_MAIL_SUBDOMAINS": "sub,x,grok",
    "DOMAIN_MAIL_NAME_STYLE": "outlook",
    "DOMAIN_MAIL_NAME_DIGITS": "4",
    "DOMAIN_MAIL_GROUP_NAME": "domain_pool",
    "MAIL_PREFER_INVENTORY": "true",
    "GROK_MAIL_PENDING_GROUP_NAME": "grok_pending",
    "GROK_MAIL_SUCCESS_GROUP_NAME": "grok_success",
    "GROK_MAIL_OLD_GROUP_NAME": "grok_old",
    "TRAFFIC_METER_ENABLED": "false",
    "UI_THEME": "dark-purple",
    "UI_STATIC_DIR": "./frontend",
}


def load_env_file(path: Path, *, allowed_keys: set[str] | None = None) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if allowed_keys is not None and key not in allowed_keys:
            continue
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file(ROOT / ".env", allowed_keys={"ADMIN_PASSWORD"})


def load_config_values() -> dict[str, str]:
    file_config = load_json_file(CONFIG_PATH)
    values = {key: str(DEFAULT_APP_SETTINGS.get(key, "")) for key in APP_SETTING_FIELDS}
    for key in APP_SETTING_FIELDS:
        if key in file_config and file_config[key] is not None:
            values[key] = str(file_config[key])
    return values


def write_config_values(values: dict[str, Any]) -> None:
    serialized = {key: str(values.get(key, DEFAULT_APP_SETTINGS.get(key, ""))) for key in APP_SETTING_FIELDS}
    save_json_file(CONFIG_PATH, serialized)


APP_CONFIG_VALUES = load_config_values()


def app_config_value(key: str, default: Any = "") -> str:
    return str(APP_CONFIG_VALUES.get(key, DEFAULT_APP_SETTINGS.get(key, default)))


@dataclass
class UcSignupState:
    running: bool = False
    stop_requested: bool = False
    total: int = 0
    completed: int = 0
    success: int = 0
    failed: int = 0
    current_index: int = 0
    current_email: str = ""
    current_phone: str = ""
    current_proxy: str = ""
    current_step: str = ""
    phase: str = "idle"
    started_at: str = ""
    updated_at: str = ""
    current_pid: int | None = None
    results: list[dict[str, Any]] = None
    errors: list[dict[str, str]] = None
    log_lines: list[dict[str, str]] = None

    def __post_init__(self) -> None:
        if self.results is None:
            self.results = []
        if self.errors is None:
            self.errors = []
        if self.log_lines is None:
            self.log_lines = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "stopRequested": self.stop_requested,
            "total": self.total,
            "completed": self.completed,
            "success": self.success,
            "failed": self.failed,
            "currentIndex": self.current_index,
            "currentEmail": self.current_email,
            "currentPhone": self.current_phone,
            "currentProxy": self.current_proxy,
            "currentStep": self.current_step,
            "phase": self.phase,
            "startedAt": self.started_at,
            "updatedAt": self.updated_at,
            "currentPid": self.current_pid,
            "results": list(self.results),
            "errors": list(self.errors),
            "logLines": list(self.log_lines),
        }


@dataclass
class Config:
    host: str = os.getenv("AUTOMYAI_HOST") or app_config_value("HOST", "127.0.0.1")
    port: int = int(os.getenv("AUTOMYAI_PORT") or app_config_value("PORT", "13030"))
    api_key: str = app_config_value("HERO_SMS_API_KEY", "")
    api_url: str = app_config_value("HERO_SMS_API_URL", "https://hero-sms.com/stubs/handler_api.php")
    tele_auto_enabled: bool = parse_bool_flag(app_config_value("TELE_AUTO_ENABLED", "true"), default=True)
    tele_auto_api_url: str = app_config_value("TELE_AUTO_API_URL", "http://127.0.0.1:8028")
    tele_auto_username: str = app_config_value("TELE_AUTO_USERNAME", "")
    tele_auto_password: str = app_config_value("TELE_AUTO_PASSWORD", "")
    default_service_name: str = DEFAULT_SERVICE_NAME
    default_service_code: str = DEFAULT_SERVICE_CODE
    default_service_aliases: list[str] = None
    default_country_name: str = ""
    default_country_code: str = ""
    default_country_aliases: list[str] = None
    default_operator: str = "any"
    default_max_price: str = ""
    default_exact_price: str = ""
    default_fixed_price: str = ""
    timeout_ms: int = int(app_config_value("REQUEST_TIMEOUT_MS", "15000"))
    enable_cors: bool = app_config_value("ENABLE_CORS", "true").lower() == "true"
    cors_allowed_origins: str = app_config_value("CORS_ALLOWED_ORIGINS", "https://automyai.kfjie.me")
    public_status_enabled: bool = parse_bool_flag(app_config_value("PUBLIC_STATUS_ENABLED", "true"), default=True)
    public_status_allow_origins: str = app_config_value(
        "PUBLIC_STATUS_ALLOW_ORIGINS",
        "https://kfjie.me,https://www.kfjie.me,https://automyai.kfjie.me",
    )
    public_status_token: str = app_config_value("PUBLIC_STATUS_TOKEN", "")
    store_file: Path = ROOT / app_config_value("STORE_FILE", "./data/activations.json")
    purchase_config_file: Path = PURCHASE_CONFIG_PATH
    temp_mail_api_url: str = app_config_value("TEMP_MAIL_API_URL", "")
    temp_mail_admin_password: str = app_config_value("TEMP_MAIL_ADMIN_PASSWORD", "")
    outlook_email_api_url: str = app_config_value("OUTLOOK_EMAIL_API_URL", "")
    outlook_email_api_key: str = app_config_value("OUTLOOK_EMAIL_API_KEY", "")
    outlook_email_admin_password: str = app_config_value("OUTLOOK_EMAIL_ADMIN_PASSWORD", "")
    mail_source_group_name: str = app_config_value("MAIL_SOURCE_GROUP_NAME", "默认分组")
    mail_pending_group_name: str = app_config_value("MAIL_PENDING_GROUP_NAME", "gpt_pending_account")
    mail_success_group_name: str = app_config_value("MAIL_SUCCESS_GROUP_NAME", "gpt_new_account")
    mail_bad_group_name: str = app_config_value("MAIL_BAD_GROUP_NAME", "badmail")
    sub2api_api_url: str = app_config_value("SUB2API_API_URL", "")
    sub2api_admin_email: str = app_config_value("SUB2API_ADMIN_EMAIL", "")
    sub2api_admin_password: str = app_config_value("SUB2API_ADMIN_PASSWORD", "")
    sub2api_admin_token: str = app_config_value("SUB2API_ADMIN_TOKEN", "")
    sub2api_monitor_enabled: bool = parse_bool_flag(app_config_value("SUB2API_MONITOR_ENABLED", "true"), default=True)
    sub2api_monitor_group_name: str = app_config_value("SUB2API_MONITOR_GROUP_NAME", "auto")
    sub2api_import_group_names: str = app_config_value("SUB2API_IMPORT_GROUP_NAMES", "auto")
    sub2api_monitor_min_ok_accounts: str = app_config_value("SUB2API_MONITOR_MIN_OK_ACCOUNTS", "1")
    sub2api_monitor_busy_min_ok_accounts: str = app_config_value("SUB2API_MONITOR_BUSY_MIN_OK_ACCOUNTS", "2")
    sub2api_monitor_interval_seconds: str = app_config_value("SUB2API_MONITOR_INTERVAL_SECONDS", "30")
    sub2api_monitor_busy_window_seconds: str = app_config_value("SUB2API_MONITOR_BUSY_WINDOW_SECONDS", "300")
    sub2api_monitor_trigger_cooldown_seconds: str = app_config_value("SUB2API_MONITOR_TRIGGER_COOLDOWN_SECONDS", "60")
    sub2api_monitor_max_start_accounts: str = app_config_value("SUB2API_MONITOR_MAX_START_ACCOUNTS", "2")
    sub2api_monitor_import_mail_source: bool = parse_bool_flag(app_config_value("SUB2API_MONITOR_IMPORT_MAIL_SOURCE", "true"), default=True)
    cpa_monitor_enabled: bool = parse_bool_flag(app_config_value("CPA_MONITOR_ENABLED", "true"), default=True)
    cpa_monitor_min_ok_accounts: str = app_config_value("CPA_MONITOR_MIN_OK_ACCOUNTS", "5")
    cpa_monitor_interval_seconds: str = app_config_value("CPA_MONITOR_INTERVAL_SECONDS", "60")
    cpa_monitor_trigger_cooldown_seconds: str = app_config_value("CPA_MONITOR_TRIGGER_COOLDOWN_SECONDS", "900")
    cpa_monitor_register_count: str = app_config_value("CPA_MONITOR_REGISTER_COUNT", "2")
    cpa_monitor_register_threads: str = app_config_value("CPA_MONITOR_REGISTER_THREADS", "2")
    cpa_monitor_proxy: str = app_config_value("CPA_MONITOR_PROXY", "")
    browser_display: str = app_config_value("BROWSER_DISPLAY", ":1")
    browser_proxy: str = app_config_value("BROWSER_PROXY", "")
    uc_signup_proxy: str = app_config_value("UC_SIGNUP_PROXY", "")
    signup_proxy_mode: str = app_config_value("SIGNUP_PROXY_MODE", "custom")
    signup_proxy_region: str = app_config_value("SIGNUP_PROXY_REGION", "JP")
    signup_proxy_custom_url: str = app_config_value("SIGNUP_PROXY_CUSTOM_URL", "")
    cliproxy_proxy_url: str = app_config_value("CLIPROXY_PROXY_URL", "")
    proxy_pool_urls: str = app_config_value("PROXY_POOL_URLS", "")
    proxy_randomize: bool = parse_bool_flag(app_config_value("PROXY_RANDOMIZE", "true"), default=True)
    proxy_usage_window_seconds: str = app_config_value("PROXY_USAGE_WINDOW_SECONDS", "86400")
    proxy_usage_max_per_window: str = app_config_value("PROXY_USAGE_MAX_PER_WINDOW", "3")
    phone_code_window_seconds: str = app_config_value("PHONE_CODE_WINDOW_SECONDS", "3600")
    phone_code_max_per_window: str = app_config_value("PHONE_CODE_MAX_PER_WINDOW", "1")
    phone_code_max_total: str = app_config_value("PHONE_CODE_MAX_TOTAL", "3")
    phone_whatsapp_cooldown_seconds: str = app_config_value("PHONE_WHATSAPP_COOLDOWN_SECONDS", "21600")
    phone_sms_cooldown_seconds: str = app_config_value("PHONE_SMS_COOLDOWN_SECONDS", "1800")
    sub2api_proxy_region: str = app_config_value("SUB2API_PROXY_REGION", "")
    sub2api_proxy_url: str = app_config_value("SUB2API_PROXY_URL", "")
    sub2api_proxy_name: str = app_config_value("SUB2API_PROXY_NAME", "")
    sub2api_import_use_signup_proxy: bool = parse_bool_flag(
        app_config_value("SUB2API_IMPORT_USE_SIGNUP_PROXY", "false"), default=False
    )
    uc_signup_phone_retries: str = app_config_value("UC_SIGNUP_PHONE_RETRIES", "0")
    uc_signup_sms_timeout_seconds: str = app_config_value("UC_SIGNUP_SMS_TIMEOUT_SECONDS", "135")
    uc_signup_sms_poll_interval_seconds: str = app_config_value("UC_SIGNUP_SMS_POLL_INTERVAL_SECONDS", "10")
    uc_signup_phone_password_page_timeout: str = app_config_value("UC_SIGNUP_PHONE_PASSWORD_PAGE_TIMEOUT", "25")
    uc_signup_cf_clearance_enabled: str = app_config_value("UC_SIGNUP_CF_CLEARANCE_ENABLED", "false")
    uc_signup_cf_clearance_api_url: str = app_config_value("UC_SIGNUP_CF_CLEARANCE_API_URL", "http://127.0.0.1:18191/v1")
    uc_signup_cf_clearance_target_url: str = app_config_value(
        "UC_SIGNUP_CF_CLEARANCE_TARGET_URL", "https://chatgpt.com/auth/login?intent=signup"
    )
    uc_signup_cf_clearance_timeout_seconds: str = app_config_value("UC_SIGNUP_CF_CLEARANCE_TIMEOUT_SECONDS", "90")
    uc_signup_cf_clearance_cache_seconds: str = app_config_value("UC_SIGNUP_CF_CLEARANCE_CACHE_SECONDS", "1800")
    uc_signup_chrome_binary: str = app_config_value("UC_SIGNUP_CHROME_BINARY", "")
    uc_signup_chrome_version: str = app_config_value("UC_SIGNUP_CHROME_VERSION", "")
    uc_signup_keep_browser_on_failure: str = app_config_value("UC_SIGNUP_KEEP_BROWSER_ON_FAILURE", "false")
    uc_signup_keep_browser_seconds: str = app_config_value("UC_SIGNUP_KEEP_BROWSER_SECONDS", "0")
    uc_signup_idle_timeout_seconds: str = app_config_value("UC_SIGNUP_IDLE_TIMEOUT_SECONDS", "600")
    uc_signup_retryable_email_cooldown_seconds: str = app_config_value("UC_SIGNUP_RETRYABLE_EMAIL_COOLDOWN_SECONDS", "900")
    uc_signup_profile_base_dir: str = app_config_value("UC_SIGNUP_PROFILE_BASE_DIR", "./data/browser_profiles")
    signup_password: str = app_config_value("SIGNUP_PASSWORD", "FuckOAI123456!")
    signup_name: str = app_config_value("SIGNUP_NAME", "")
    signup_age: str = app_config_value("SIGNUP_AGE", "18")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "")

    def __post_init__(self) -> None:
        self.default_service_aliases = [
            item.strip()
            for item in "OpenAI,ChatGPT".split(",")
            if item.strip()
        ]
        self.default_country_aliases = []
        self.store_file = (ROOT / app_config_value("STORE_FILE", "./data/activations.json")).resolve()
        self.purchase_config_file = (ROOT / app_config_value("PURCHASE_CONFIG_FILE", "./data/purchase_config.json")).resolve()
        self.tele_auto_enabled = parse_bool_flag(app_config_value("TELE_AUTO_ENABLED", "true"), default=True)
        self.tele_auto_api_url = app_config_value("TELE_AUTO_API_URL", "http://127.0.0.1:8028").rstrip("/")
        self.tele_auto_username = app_config_value("TELE_AUTO_USERNAME", "")
        self.tele_auto_password = app_config_value("TELE_AUTO_PASSWORD", "")
        self.temp_mail_api_url = app_config_value("TEMP_MAIL_API_URL", "").rstrip("/")
        self.temp_mail_admin_password = app_config_value("TEMP_MAIL_ADMIN_PASSWORD", "")
        self.outlook_email_api_url = app_config_value("OUTLOOK_EMAIL_API_URL", "").rstrip("/")
        self.outlook_email_api_key = app_config_value("OUTLOOK_EMAIL_API_KEY", "")
        self.outlook_email_admin_password = app_config_value("OUTLOOK_EMAIL_ADMIN_PASSWORD", "")
        self.mail_source_group_name = app_config_value("MAIL_SOURCE_GROUP_NAME", "默认分组") or "默认分组"
        self.mail_pending_group_name = app_config_value("MAIL_PENDING_GROUP_NAME", "gpt_pending_account") or "gpt_pending_account"
        self.mail_success_group_name = app_config_value("MAIL_SUCCESS_GROUP_NAME", "gpt_new_account") or "gpt_new_account"
        self.mail_bad_group_name = app_config_value("MAIL_BAD_GROUP_NAME", "badmail") or "badmail"
        self.sub2api_api_url = app_config_value("SUB2API_API_URL", "").rstrip("/")
        self.sub2api_admin_email = app_config_value("SUB2API_ADMIN_EMAIL", "")
        self.sub2api_admin_password = app_config_value("SUB2API_ADMIN_PASSWORD", "")
        self.sub2api_admin_token = app_config_value("SUB2API_ADMIN_TOKEN", "")
        self.sub2api_monitor_enabled = parse_bool_flag(app_config_value("SUB2API_MONITOR_ENABLED", "true"), default=True)
        self.sub2api_monitor_group_name = app_config_value("SUB2API_MONITOR_GROUP_NAME", "auto") or "auto"
        self.sub2api_import_group_names = app_config_value("SUB2API_IMPORT_GROUP_NAMES", "auto") or "auto"
        self.sub2api_monitor_min_ok_accounts = app_config_value("SUB2API_MONITOR_MIN_OK_ACCOUNTS", "1")
        self.sub2api_monitor_busy_min_ok_accounts = app_config_value("SUB2API_MONITOR_BUSY_MIN_OK_ACCOUNTS", "2")
        self.sub2api_monitor_interval_seconds = app_config_value("SUB2API_MONITOR_INTERVAL_SECONDS", "30")
        self.sub2api_monitor_busy_window_seconds = app_config_value("SUB2API_MONITOR_BUSY_WINDOW_SECONDS", "300")
        self.sub2api_monitor_trigger_cooldown_seconds = app_config_value("SUB2API_MONITOR_TRIGGER_COOLDOWN_SECONDS", "60")
        self.sub2api_monitor_max_start_accounts = app_config_value("SUB2API_MONITOR_MAX_START_ACCOUNTS", "2")
        self.sub2api_monitor_import_mail_source = parse_bool_flag(app_config_value("SUB2API_MONITOR_IMPORT_MAIL_SOURCE", "true"), default=True)
        self.cpa_monitor_enabled = parse_bool_flag(app_config_value("CPA_MONITOR_ENABLED", "true"), default=True)
        self.cpa_monitor_min_ok_accounts = app_config_value("CPA_MONITOR_MIN_OK_ACCOUNTS", "5")
        self.cpa_monitor_interval_seconds = app_config_value("CPA_MONITOR_INTERVAL_SECONDS", "60")
        self.cpa_monitor_trigger_cooldown_seconds = app_config_value("CPA_MONITOR_TRIGGER_COOLDOWN_SECONDS", "900")
        self.cpa_monitor_register_count = app_config_value("CPA_MONITOR_REGISTER_COUNT", "2")
        self.cpa_monitor_register_threads = app_config_value("CPA_MONITOR_REGISTER_THREADS", "2")
        self.cpa_monitor_proxy = app_config_value("CPA_MONITOR_PROXY", "")
        self.browser_display = app_config_value("BROWSER_DISPLAY", ":1")
        self.browser_proxy = app_config_value("BROWSER_PROXY", "")
        self.uc_signup_proxy = app_config_value("UC_SIGNUP_PROXY", "")
        self.signup_proxy_mode = app_config_value("SIGNUP_PROXY_MODE", "custom")
        self.signup_proxy_region = app_config_value("SIGNUP_PROXY_REGION", "JP")
        self.signup_proxy_custom_url = app_config_value("SIGNUP_PROXY_CUSTOM_URL", "")
        self.cliproxy_proxy_url = app_config_value("CLIPROXY_PROXY_URL", "")
        self.proxy_pool_urls = app_config_value("PROXY_POOL_URLS", "")
        self.proxy_randomize = parse_bool_flag(app_config_value("PROXY_RANDOMIZE", "true"), default=True)
        self.proxy_usage_window_seconds = app_config_value("PROXY_USAGE_WINDOW_SECONDS", "86400")
        self.proxy_usage_max_per_window = app_config_value("PROXY_USAGE_MAX_PER_WINDOW", "3")
        self.phone_code_window_seconds = app_config_value("PHONE_CODE_WINDOW_SECONDS", "3600")
        self.phone_code_max_per_window = app_config_value("PHONE_CODE_MAX_PER_WINDOW", "1")
        self.phone_code_max_total = app_config_value("PHONE_CODE_MAX_TOTAL", "3")
        self.phone_whatsapp_cooldown_seconds = app_config_value("PHONE_WHATSAPP_COOLDOWN_SECONDS", "21600")
        self.phone_sms_cooldown_seconds = app_config_value("PHONE_SMS_COOLDOWN_SECONDS", "1800")
        self.sub2api_proxy_region = app_config_value("SUB2API_PROXY_REGION", "")
        self.sub2api_proxy_url = app_config_value("SUB2API_PROXY_URL", "")
        self.sub2api_proxy_name = app_config_value("SUB2API_PROXY_NAME", "")
        self.sub2api_import_use_signup_proxy = parse_bool_flag(
            app_config_value("SUB2API_IMPORT_USE_SIGNUP_PROXY", "false"), default=False
        )
        self.uc_signup_phone_retries = app_config_value("UC_SIGNUP_PHONE_RETRIES", "0")
        self.uc_signup_sms_timeout_seconds = app_config_value("UC_SIGNUP_SMS_TIMEOUT_SECONDS", "135")
        self.uc_signup_sms_poll_interval_seconds = app_config_value("UC_SIGNUP_SMS_POLL_INTERVAL_SECONDS", "10")
        self.uc_signup_phone_password_page_timeout = app_config_value("UC_SIGNUP_PHONE_PASSWORD_PAGE_TIMEOUT", "25")
        self.uc_signup_cf_clearance_enabled = app_config_value("UC_SIGNUP_CF_CLEARANCE_ENABLED", "false")
        self.uc_signup_cf_clearance_api_url = app_config_value("UC_SIGNUP_CF_CLEARANCE_API_URL", "http://127.0.0.1:18191/v1")
        self.uc_signup_cf_clearance_target_url = app_config_value(
            "UC_SIGNUP_CF_CLEARANCE_TARGET_URL", "https://chatgpt.com/auth/login?intent=signup"
        )
        self.uc_signup_cf_clearance_timeout_seconds = app_config_value("UC_SIGNUP_CF_CLEARANCE_TIMEOUT_SECONDS", "90")
        self.uc_signup_cf_clearance_cache_seconds = app_config_value("UC_SIGNUP_CF_CLEARANCE_CACHE_SECONDS", "1800")
        self.uc_signup_chrome_binary = app_config_value("UC_SIGNUP_CHROME_BINARY", "")
        self.uc_signup_chrome_version = app_config_value("UC_SIGNUP_CHROME_VERSION", "")
        self.uc_signup_keep_browser_on_failure = app_config_value("UC_SIGNUP_KEEP_BROWSER_ON_FAILURE", "false")
        self.uc_signup_keep_browser_seconds = app_config_value("UC_SIGNUP_KEEP_BROWSER_SECONDS", "0")
        self.uc_signup_idle_timeout_seconds = app_config_value("UC_SIGNUP_IDLE_TIMEOUT_SECONDS", "600")
        self.uc_signup_retryable_email_cooldown_seconds = app_config_value("UC_SIGNUP_RETRYABLE_EMAIL_COOLDOWN_SECONDS", "900")
        self.uc_signup_profile_base_dir = app_config_value("UC_SIGNUP_PROFILE_BASE_DIR", "./data/browser_profiles")
        self.signup_password = app_config_value("SIGNUP_PASSWORD", "FuckOAI123456!")
        self.signup_name = app_config_value("SIGNUP_NAME", "")
        self.signup_age = app_config_value("SIGNUP_AGE", "18")
        self.cors_allowed_origins = app_config_value("CORS_ALLOWED_ORIGINS", "https://automyai.kfjie.me")
        self.public_status_enabled = parse_bool_flag(app_config_value("PUBLIC_STATUS_ENABLED", "true"), default=True)
        self.public_status_allow_origins = app_config_value(
            "PUBLIC_STATUS_ALLOW_ORIGINS",
            "https://kfjie.me,https://www.kfjie.me,https://automyai.kfjie.me",
        )
        self.public_status_token = app_config_value("PUBLIC_STATUS_TOKEN", "")
        self.admin_password = os.getenv("ADMIN_PASSWORD", "")


CONFIG = Config()

ENV_PATH = ROOT / ".env"


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def email_identity_key(value: Any) -> str:
    return str(value or "").strip().lower()


def proxy_region_for_url(proxy_url: Any, proxy_name: Any = "") -> str:
    parsed = parse_proxy_url(proxy_url)
    if not parsed:
        return ""
    target_key = sub2api_proxy_key(parsed)
    for region, (name, url) in MIHOMO_SUB2API_PROFILES.items():
        known = parse_proxy_url(url)
        if known and sub2api_proxy_key(known) == target_key:
            return region
        if str(proxy_name or "").strip().lower() == name.lower():
            return region
    name_text = str(proxy_name or "").upper()
    if name_text.startswith(("HTTP://", "HTTPS://", "SOCKS://", "SOCKS5://")):
        return ""
    for region in MIHOMO_SUB2API_PROFILES:
        if re.search(rf"(^|[^A-Z]){re.escape(region)}([^A-Z]|$)", name_text):
            return region
    return ""


def identity_proxy_descriptor(proxy_url: Any, proxy_name: Any = "") -> dict[str, str]:
    parsed = parse_proxy_url(proxy_url)
    if not parsed:
        return {}
    url = proxy_url_from_parsed(parsed)
    name = str(proxy_name or proxy_name_for_url(url) or "").strip()
    return {
        "proxyUrl": url,
        "proxyKey": sub2api_proxy_key(parsed),
        "proxyName": name,
        "region": proxy_region_for_url(url, name),
    }


def identity_proxy_from_candidate(candidate: dict[str, Any]) -> dict[str, str]:
    return identity_proxy_descriptor(candidate.get("url"), candidate.get("name"))


def proxy_candidate_from_binding(binding: dict[str, Any]) -> dict[str, Any] | None:
    descriptor = identity_proxy_descriptor(binding.get("proxyUrl"), binding.get("proxyName"))
    if not descriptor:
        return None
    proxy = sub2api_proxy_from_url(descriptor["proxyUrl"], descriptor["proxyName"])
    if not proxy:
        return None
    return {
        "url": descriptor["proxyUrl"],
        "key": descriptor["proxyKey"],
        "name": descriptor["proxyName"],
        "proxy": proxy,
    }


def load_identity_bindings() -> dict[str, dict[str, Any]]:
    data = load_json_file(IDENTITY_BINDINGS_PATH)
    emails = data.get("emails") if isinstance(data, dict) else {}
    phones = data.get("phones") if isinstance(data, dict) else {}
    return {
        "emails": {str(key): value for key, value in (emails or {}).items() if isinstance(value, dict)},
        "phones": {str(key): value for key, value in (phones or {}).items() if isinstance(value, dict)},
    }


def save_identity_bindings(data: dict[str, Any]) -> dict[str, Any]:
    emails = data.get("emails") if isinstance(data, dict) else {}
    phones = data.get("phones") if isinstance(data, dict) else {}
    payload = {
        "emails": {str(key): value for key, value in (emails or {}).items() if isinstance(value, dict)},
        "phones": {str(key): value for key, value in (phones or {}).items() if isinstance(value, dict)},
    }
    save_json_file(IDENTITY_BINDINGS_PATH, payload)
    return payload


def get_email_proxy_binding(email: Any) -> dict[str, Any] | None:
    key = email_identity_key(email)
    if not key:
        return None
    with IDENTITY_BINDINGS_LOCK:
        record = load_identity_bindings().get("emails", {}).get(key)
    return dict(record) if isinstance(record, dict) else None


def bind_email_proxy(email: Any, proxy_data: dict[str, Any], *, source: str = "signup") -> dict[str, Any] | None:
    key = email_identity_key(email)
    descriptor = identity_proxy_descriptor(proxy_data.get("proxyUrl") or proxy_data.get("url"), proxy_data.get("proxyName") or proxy_data.get("name"))
    if not key or not descriptor:
        return None
    with IDENTITY_BINDINGS_LOCK:
        data = load_identity_bindings()
        emails = data.setdefault("emails", {})
        existing = emails.get(key) if isinstance(emails.get(key), dict) else {}
        now = now_iso()
        record = {
            **existing,
            "email": str(email or "").strip(),
            **descriptor,
            "source": source,
            "boundAt": existing.get("boundAt") or now,
            "updatedAt": now,
        }
        emails[key] = record
        save_identity_bindings(data)
        return dict(record)


def proxy_binding_matches(binding: dict[str, Any] | None, descriptor: dict[str, Any] | None) -> bool:
    if not binding or not descriptor:
        return True
    bound_region = normalize_proxy_region(binding.get("region"))
    current_region = normalize_proxy_region(descriptor.get("region"))
    if bound_region and current_region:
        return bound_region == current_region
    bound_key = str(binding.get("proxyKey") or "")
    current_key = str(descriptor.get("proxyKey") or "")
    return bool(bound_key and current_key and bound_key == current_key)


def load_proxy_usage() -> dict[str, Any]:
    data = load_json_file(PROXY_USAGE_PATH)
    events = data.get("events") if isinstance(data, dict) else []
    if not isinstance(events, list):
        events = []
    return {"events": [event for event in events if isinstance(event, dict)]}


def save_proxy_usage(data: dict[str, Any]) -> dict[str, Any]:
    events = data.get("events") if isinstance(data, dict) else []
    if not isinstance(events, list):
        events = []
    save_json_file(PROXY_USAGE_PATH, {"events": events[-2000:]})
    return {"events": events[-2000:]}


def proxy_usage_window_seconds() -> int:
    return parse_positive_int(CONFIG.proxy_usage_window_seconds, default=86400)


def proxy_usage_max_per_window() -> int:
    try:
        return max(0, int(str(CONFIG.proxy_usage_max_per_window).strip()))
    except (TypeError, ValueError):
        return 3


def proxy_region_for_candidate(candidate: dict[str, Any]) -> str:
    descriptor = identity_proxy_from_candidate(candidate)
    known_region = normalize_proxy_region(descriptor.get("region"))
    if known_region:
        return known_region
    key = str(candidate.get("key") or descriptor.get("proxyKey") or "")
    cache = load_json_file(PROXY_GEO_CACHE_PATH)
    entry = cache.get(key) if isinstance(cache, dict) and isinstance(cache.get(key), dict) else {}
    if entry and time.time() - float(entry.get("checkedAtTs") or 0) < 21600:
        return normalize_proxy_region(entry.get("countryCode"))
    try:
        result = probe_proxy_location(candidate.get("url"))
    except ValueError:
        return ""
    return normalize_proxy_region(result.get("countryCode"))


def prune_proxy_usage_events(events: list[dict[str, Any]], window_seconds: int) -> list[dict[str, Any]]:
    cutoff = time.time() - max(60, int(window_seconds))
    return [
        event for event in events
        if float(event.get("reservedAtTs") or event.get("updatedAtTs") or 0) >= cutoff
        or str(event.get("status") or "") == "success"
    ][-2000:]


def reserve_signup_proxy(email: str, explicit_proxy: Any = "") -> dict[str, Any]:
    explicit = str(explicit_proxy or "").strip()
    candidates = configured_signup_proxy_candidates(explicit)
    if not candidates:
        raise HeroSmsError("未找到可用注册代理，已阻止直连注册")

    email_binding = get_email_proxy_binding(email)
    # Only pin to the old email binding when this task did not pass an explicit proxy.
    # OpenAI 注册面板每次都会传自定义代理；它必须能换绑，不能被旧 Mihomo 绑定锁死。
    if email_binding and not explicit:
        bound_candidate = next(
            (
                candidate for candidate in candidates
                if str(candidate.get("key") or "") == str(email_binding.get("proxyKey") or "")
            ),
            None,
        )
        if bound_candidate:
            candidates = [bound_candidate]

    window_seconds = proxy_usage_window_seconds()
    max_per_window = proxy_usage_max_per_window()
    with PROXY_USAGE_LOCK:
        data = load_proxy_usage()
        events = prune_proxy_usage_events(data.get("events") or [], window_seconds)
        cutoff = time.time() - max(60, window_seconds)
        active_events = [
            event for event in events
            if float(event.get("reservedAtTs") or 0) >= cutoff
            and str(event.get("status") or "") in {"reserved", "running", "success", "fail", "stopped"}
        ]
        usage_identities_by_key: dict[str, set[str]] = {}
        for event in active_events:
            key = str(event.get("proxyKey") or "")
            if key:
                identity = email_identity_key(event.get("email")) or str(event.get("id") or "")
                usage_identities_by_key.setdefault(key, set()).add(identity)
        usage_by_key = {key: len(identities) for key, identities in usage_identities_by_key.items()}
        current_identity = email_identity_key(email)
        available = [
            {**candidate, "usageCount": usage_by_key.get(str(candidate["key"]), 0)}
            for candidate in candidates
            if max_per_window == 0
            or usage_by_key.get(str(candidate["key"]), 0) < max_per_window
            or (current_identity and current_identity in usage_identities_by_key.get(str(candidate["key"]), set()))
        ]
        if not available:
            labels = ", ".join(f"{candidate['name']}={usage_by_key.get(str(candidate['key']), 0)}" for candidate in candidates)
            raise HeroSmsError(f"所有代理在 {window_seconds}s 窗口内都已达到 {max_per_window} 次注册上限: {labels}")
        min_count = min(int(candidate.get("usageCount") or 0) for candidate in available)
        balanced = [candidate for candidate in available if int(candidate.get("usageCount") or 0) == min_count]
        chosen = random.choice(balanced) if CONFIG.proxy_randomize else balanced[0]
        event_id = f"{int(time.time() * 1000)}-{random.randint(100000, 999999)}"
        event = {
            "id": event_id,
            "email": str(email or ""),
            "proxyUrl": chosen["url"],
            "proxyKey": chosen["key"],
            "proxyName": chosen["name"],
            "proxyRegion": proxy_region_for_candidate(chosen),
            "emailBound": bool(email_binding),
            "status": "reserved",
            "reservedAt": now_iso(),
            "reservedAtTs": time.time(),
        }
        events.append(event)
        save_proxy_usage({"events": events})
        next_email_binding = bind_email_proxy(email, chosen, source="signup")
        return {
            "eventId": event_id,
            "proxyUrl": chosen["url"],
            "proxyName": chosen["name"],
            "proxyKey": chosen["key"],
            "proxyRegion": event["proxyRegion"],
            "emailWasBound": bool(email_binding),
            "emailBinding": next_email_binding,
            "usageCount": int(chosen.get("usageCount") or 0) + 1,
            "usageLimit": max_per_window,
            "windowSeconds": window_seconds,
        }


def update_signup_proxy_usage(event_id: str, status: str, details: dict[str, Any] | None = None) -> None:
    if not event_id:
        return
    with PROXY_USAGE_LOCK:
        data = load_proxy_usage()
        events = data.get("events") or []
        for event in events:
            if str(event.get("id") or "") == str(event_id):
                event["status"] = status
                event["updatedAt"] = now_iso()
                event["updatedAtTs"] = time.time()
                if details:
                    event.update(details)
                break
        save_proxy_usage({"events": events})


def latest_signup_proxy_for_email(email: Any) -> dict[str, Any] | None:
    binding = get_email_proxy_binding(email)
    if binding:
        return sub2api_proxy_from_url(str(binding.get("proxyUrl") or ""), str(binding.get("proxyName") or ""))
    target = str(email or "").strip().lower()
    if not target:
        return None
    events = load_proxy_usage().get("events") or []
    matched = [
        event for event in events
        if str(event.get("email") or "").strip().lower() == target
        and str(event.get("proxyUrl") or "")
        and str(event.get("status") or "") in {"success", "running", "reserved"}
    ]
    if not matched:
        return None
    matched.sort(key=lambda event: float(event.get("updatedAtTs") or event.get("reservedAtTs") or 0), reverse=True)
    event = matched[0]
    return sub2api_proxy_from_url(str(event.get("proxyUrl") or ""), str(event.get("proxyName") or ""))


def sub2api_import_proxy_for_email(email: Any) -> dict[str, Any] | None:
    if CONFIG.sub2api_import_use_signup_proxy:
        signup_proxy = latest_signup_proxy_for_email(email)
        if signup_proxy:
            return signup_proxy
    return configured_sub2api_proxy()


def probe_proxy_location(proxy_url: Any) -> dict[str, Any]:
    resolved = resolve_proxy_source(proxy_url)
    parsed = parse_proxy_url(resolved)
    if not parsed:
        raise ValueError("代理地址格式不正确")
    status, payload, raw = http_json(
        "GET",
        "http://ip-api.com/json/?fields=status,message,country,countryCode,regionName,city,isp,org,query",
        timeout=20,
        proxy_url=proxy_url_from_parsed(parsed),
    )
    if status != 200 or not isinstance(payload, dict) or payload.get("status") != "success":
        message = payload.get("message") if isinstance(payload, dict) else raw
        raise ValueError(f"代理检测失败: {message or status or 'unknown error'}")
    result = {
        "proxyUrl": proxy_url_from_parsed(parsed),
        "protocol": parsed.get("protocol"),
        "host": parsed.get("host"),
        "port": parsed.get("port"),
        "authenticated": bool(parsed.get("username") or parsed.get("password")),
        "ip": payload.get("query"),
        "country": payload.get("country"),
        "countryCode": payload.get("countryCode"),
        "region": payload.get("regionName"),
        "city": payload.get("city"),
        "isp": payload.get("isp"),
        "org": payload.get("org"),
        "checkedAt": now_iso(),
    }
    key = sub2api_proxy_key(parsed)
    cache = load_json_file(PROXY_GEO_CACHE_PATH)
    cache[key] = {
        "countryCode": result.get("countryCode"),
        "country": result.get("country"),
        "region": result.get("region"),
        "city": result.get("city"),
        "isp": result.get("isp"),
        "checkedAt": result.get("checkedAt"),
        "checkedAtTs": time.time(),
    }
    save_json_file(PROXY_GEO_CACHE_PATH, cache)
    return result


def probe_proxy_pool(raw_proxies: Any, *, limit: int = 8) -> dict[str, Any]:
    """Probe one or more extraction proxies and summarize reachability."""
    raw_text = str(first_non_empty(raw_proxies, "") or "")
    candidates: list[str] = []
    seen: set[str] = set()
    normalized_text = (
        raw_text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace(";", "\n")
        .replace(",", "\n")
    )
    for line in normalized_text.split("\n"):
        item = str(line or "").strip()
        if not item or item.startswith("#") or item in seen:
            continue
        seen.add(item)
        candidates.append(item)
    if not candidates:
        for url in parse_proxy_pool_urls(raw_text):
            if url not in seen:
                seen.add(url)
                candidates.append(url)
    if not candidates:
        raise ValueError("请填写要检测的代理")

    max_items = max(1, min(int(limit or 8), 20))
    selected = candidates[:max_items]
    results: list[dict[str, Any]] = []
    for index, candidate in enumerate(selected, start=1):
        entry: dict[str, Any] = {
            "index": index,
            "input": candidate,
            "ok": False,
        }
        try:
            probed = probe_proxy_location(candidate)
            entry.update({
                "ok": True,
                "proxyUrl": probed.get("proxyUrl"),
                "ip": probed.get("ip"),
                "country": probed.get("country"),
                "countryCode": probed.get("countryCode"),
                "region": probed.get("region"),
                "city": probed.get("city"),
                "isp": probed.get("isp"),
                "host": probed.get("host"),
                "port": probed.get("port"),
                "checkedAt": probed.get("checkedAt"),
            })
        except Exception as error:
            entry["error"] = str(error)
            parsed = parse_proxy_url(candidate)
            if parsed:
                entry.update({
                    "proxyUrl": proxy_url_from_parsed(parsed),
                    "host": parsed.get("host"),
                    "port": parsed.get("port"),
                })
        results.append(entry)

    ok_count = sum(1 for item in results if item.get("ok"))
    first_ok = next((item for item in results if item.get("ok")), None)
    return {
        "ok": ok_count > 0,
        "total": len(candidates),
        "checked": len(results),
        "okCount": ok_count,
        "failedCount": len(results) - ok_count,
        "truncated": len(candidates) > len(results),
        "result": first_ok,
        "results": results,
    }


STORE = ActivationStore(CONFIG.store_file)


def parse_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if not (text.startswith("{") or text.startswith("[")):
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def iter_auth_records(payload: Any, source_name: str = "") -> list[tuple[str, dict[str, Any]]]:
    parsed = parse_jsonish(payload)
    records: list[tuple[str, dict[str, Any]]] = []
    if isinstance(parsed, list):
        for index, item in enumerate(parsed):
            records.extend(iter_auth_records(item, source_name or f"item-{index + 1}"))
        return records
    if not isinstance(parsed, dict):
        return records

    content = parse_jsonish(first_non_empty(parsed.get("content"), parsed.get("data"), parsed.get("body")))
    if isinstance(content, (dict, list)) and content is not parsed:
        content_name = str(first_non_empty(parsed.get("name"), parsed.get("filename"), source_name) or "")
        records.extend(iter_auth_records(content, content_name))

    if isinstance(parsed.get("files"), list):
        for item in parsed["files"]:
            records.extend(iter_auth_records(item, source_name))
    if isinstance(parsed.get("auth_files"), list):
        for item in parsed["auth_files"]:
            records.extend(iter_auth_records(item, source_name))
    if isinstance(parsed.get("accounts"), list):
        for item in parsed["accounts"]:
            records.extend(iter_auth_records(item, source_name))

    has_auth_token = any(
        first_non_empty(parsed.get(key))
        for key in ("access_token", "accessToken", "id_token", "idToken", "refresh_token", "refreshToken")
    )
    nested_tokens = parsed.get("tokens") if isinstance(parsed.get("tokens"), dict) else {}
    nested_credentials = parsed.get("credentials") if isinstance(parsed.get("credentials"), dict) else {}
    if has_auth_token or first_non_empty(nested_tokens.get("access_token"), nested_credentials.get("access_token")):
        name = str(first_non_empty(parsed.get("name"), parsed.get("filename"), parsed.get("path"), source_name) or "")
        records.append((name, parsed))

    for key, value in parsed.items():
        if key in {"files", "auth_files", "accounts", "content", "data", "body", "tokens", "credentials"}:
            continue
        if isinstance(value, (dict, list)):
            records.extend(iter_auth_records(value, str(key)))
        elif isinstance(value, str) and (key.endswith(".json") or value.strip().startswith(("{", "["))):
            records.extend(iter_auth_records(value, str(key)))

    return records


def normalize_sub2api_account(
    record: dict[str, Any],
    source_name: str,
    proxy_key: str | None = None,
    source_label: str = "automyai_sub2api_oauth",
) -> dict[str, Any] | None:
    tokens = record.get("tokens") if isinstance(record.get("tokens"), dict) else {}
    credentials = record.get("credentials") if isinstance(record.get("credentials"), dict) else {}
    extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}

    access_token = first_non_empty(
        record.get("access_token"),
        record.get("accessToken"),
        tokens.get("access_token"),
        credentials.get("access_token"),
    )
    if not access_token:
        return None

    id_token = first_non_empty(record.get("id_token"), record.get("idToken"), tokens.get("id_token"), credentials.get("id_token"))
    refresh_token = first_non_empty(
        record.get("refresh_token"),
        record.get("refreshToken"),
        tokens.get("refresh_token"),
        credentials.get("refresh_token"),
    )
    session_token = first_non_empty(record.get("session_token"), record.get("sessionToken"), tokens.get("session_token"))

    access_claims = decode_jwt_payload(access_token)
    id_claims = decode_jwt_payload(id_token)
    access_auth = access_claims.get("https://api.openai.com/auth")
    id_auth = id_claims.get("https://api.openai.com/auth")
    access_profile = access_claims.get("https://api.openai.com/profile")
    access_auth = access_auth if isinstance(access_auth, dict) else {}
    id_auth = id_auth if isinstance(id_auth, dict) else {}
    access_profile = access_profile if isinstance(access_profile, dict) else {}

    email = first_non_empty(
        record.get("email"),
        record.get("email_address"),
        credentials.get("email"),
        credentials.get("email_address"),
        extra.get("email"),
        extra.get("email_address"),
        access_profile.get("email"),
        access_claims.get("email"),
        id_claims.get("email"),
    )
    account_id = first_non_empty(
        record.get("account_id"),
        record.get("chatgpt_account_id"),
        credentials.get("chatgpt_account_id"),
        tokens.get("account_id"),
        access_auth.get("chatgpt_account_id"),
        id_auth.get("chatgpt_account_id"),
    )
    user_id = first_non_empty(
        record.get("chatgpt_user_id"),
        record.get("user_id"),
        credentials.get("chatgpt_user_id"),
        access_auth.get("chatgpt_user_id"),
        access_auth.get("user_id"),
        id_auth.get("chatgpt_user_id"),
        id_auth.get("user_id"),
    )
    plan_type = first_non_empty(
        record.get("plan_type"),
        record.get("chatgpt_plan_type"),
        credentials.get("plan_type"),
        access_auth.get("chatgpt_plan_type"),
        id_auth.get("chatgpt_plan_type"),
    )
    organization_id = first_non_empty(
        record.get("organization_id"),
        credentials.get("organization_id"),
        access_auth.get("organization_id"),
        id_auth.get("organization_id"),
    )
    client_id = first_non_empty(
        record.get("client_id"),
        credentials.get("client_id"),
        access_claims.get("azp"),
        id_claims.get("azp"),
    )

    access_exp = access_claims.get("exp")
    access_exp = int(access_exp) if isinstance(access_exp, (int, float)) else None
    credentials_expires_at = first_non_empty(
        credentials.get("expires_at"),
        record.get("expires_at"),
        record.get("expired"),
        record.get("expiresAt"),
        access_exp,
    )
    top_level_expires_at = None if refresh_token else access_exp
    name = str(first_non_empty(record.get("name"), email, source_name, "ChatGPT Account") or "ChatGPT Account")

    account = {
        "name": name,
        "platform": "openai",
        "type": "oauth",
        "expires_at": top_level_expires_at,
        "auto_pause_on_expired": True if top_level_expires_at else None,
        "concurrency": 10,
        "priority": 1,
        "proxy_key": proxy_key,
        "credentials": {
            "access_token": access_token,
            "id_token": id_token,
            "refresh_token": refresh_token,
            "session_token": session_token,
            "chatgpt_account_id": account_id,
            "chatgpt_user_id": user_id,
            "organization_id": organization_id,
            "email": email,
            "expires_at": credentials_expires_at,
            "plan_type": plan_type,
            "client_id": client_id,
        },
        "extra": {
            "email": email,
            "email_key": email_key(email),
            "name": name,
            "source": source_label,
            "last_refresh": now_iso(),
        },
    }
    return strip_empty_values(account)


def build_sub2api_document_from_auth_files(auth_files: Any) -> dict[str, Any]:
    accounts: list[dict[str, Any]] = []
    proxies_by_key: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for source_name, record in iter_auth_records(auth_files):
        account = normalize_sub2api_account(record, source_name, source_label="automyai_auth_files")
        if not account:
            continue
        credentials = account.get("credentials") if isinstance(account.get("credentials"), dict) else {}
        extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
        account_email = credentials.get("email") or extra.get("email") or ""
        proxy = sub2api_import_proxy_for_email(account_email)
        if proxy:
            proxy_key = str(proxy.get("proxy_key") or "")
            account["proxy_key"] = proxy_key
            if proxy_key:
                proxies_by_key[proxy_key] = proxy
        signature = "|".join(
            [
                str(credentials.get("chatgpt_account_id") or ""),
                str(credentials.get("email") or ""),
                str(credentials.get("access_token") or "")[:48],
            ]
        )
        if signature in seen:
            continue
        seen.add(signature)
        accounts.append(account)

    document: dict[str, Any] = {
        "exported_at": now_iso(),
        "proxies": list(proxies_by_key.values()),
        "accounts": accounts,
    }
    return document


def extract_oauth_callback_params(payload: dict[str, Any]) -> tuple[str, str, str]:
    redirect_url = str(first_non_empty(payload.get("redirect_url"), payload.get("url"), payload.get("callback_url")) or "")
    code = str(payload.get("code") or "").strip()
    state = str(payload.get("state") or "").strip()
    if redirect_url:
        parsed = urlparse(redirect_url)
        query = parse_qs(parsed.query)
        fragment = parse_qs(parsed.fragment)
        code = code or str(first_non_empty((query.get("code") or [""])[-1], (fragment.get("code") or [""])[-1]) or "")
        state = state or str(first_non_empty((query.get("state") or [""])[-1], (fragment.get("state") or [""])[-1]) or "")
        error_text = str(first_non_empty((query.get("error") or [""])[-1], (fragment.get("error") or [""])[-1]) or "")
        if error_text:
            raise Sub2ApiError(f"OAuth 回调返回错误: {error_text}")
    if not code or not state:
        raise Sub2ApiError("OAuth 回调缺少 code/state")
    return code, state, redirect_url


def build_sub2api_document_from_openai_oauth(oauth_payload: dict[str, Any], requested_email: str = "") -> dict[str, Any]:
    record = dict(oauth_payload)
    requested_email = str(requested_email or "").strip()
    if requested_email and not first_non_empty(record.get("email"), record.get("email_address")):
        record["email"] = requested_email
    source_name = str(first_non_empty(record.get("email"), record.get("email_address"), requested_email, "OpenAI OAuth Account") or "")
    account = normalize_sub2api_account(record, source_name, source_label="automyai_sub2api_openai_oauth")
    if not account:
        raise Sub2ApiError("Sub2API OpenAI OAuth 未返回可导入的 access_token")

    credentials = account.setdefault("credentials", {})
    extra = account.setdefault("extra", {})
    account_email = str(first_non_empty(credentials.get("email"), extra.get("email"), requested_email) or "").strip()
    if account_email:
        credentials.setdefault("email", account_email)
        extra.setdefault("email", account_email)
        extra["email_key"] = email_key(account_email)
        if account.get("name") in ("OpenAI OAuth Account", "ChatGPT Account", source_name):
            account["name"] = account_email

    proxies_by_key: dict[str, dict[str, Any]] = {}
    proxy = sub2api_import_proxy_for_email(account_email)
    if proxy:
        proxy_key = str(proxy.get("proxy_key") or "")
        account["proxy_key"] = proxy_key
        if proxy_key:
            proxies_by_key[proxy_key] = proxy

    return {
        "exported_at": now_iso(),
        "proxies": list(proxies_by_key.values()),
        "accounts": [strip_empty_values(account)],
    }


def summarize_sub2api_document(document: dict[str, Any]) -> dict[str, Any]:
    accounts = document.get("accounts") if isinstance(document, dict) else []
    proxies = document.get("proxies") if isinstance(document, dict) else []
    account = accounts[0] if isinstance(accounts, list) and accounts else {}
    credentials = account.get("credentials") if isinstance(account, dict) and isinstance(account.get("credentials"), dict) else {}
    extra = account.get("extra") if isinstance(account, dict) and isinstance(account.get("extra"), dict) else {}
    return {
        "accounts": len(accounts) if isinstance(accounts, list) else 0,
        "proxies": len(proxies) if isinstance(proxies, list) else 0,
        "email": first_non_empty(credentials.get("email"), extra.get("email"), account.get("name") if isinstance(account, dict) else ""),
        "chatgptAccountId": credentials.get("chatgpt_account_id"),
        "exportedAt": document.get("exported_at") if isinstance(document, dict) else "",
    }


def sub2api_document_account_identity(document: dict[str, Any]) -> dict[str, str]:
    accounts = document.get("accounts") if isinstance(document, dict) else []
    account = accounts[0] if isinstance(accounts, list) and accounts and isinstance(accounts[0], dict) else {}
    credentials = account.get("credentials") if isinstance(account.get("credentials"), dict) else {}
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    return {
        "email": str(first_non_empty(credentials.get("email"), extra.get("email"), account.get("name")) or "").strip(),
        "emailKey": email_key(first_non_empty(credentials.get("email"), extra.get("email"), account.get("name")) or ""),
        "chatgptAccountId": str(credentials.get("chatgpt_account_id") or "").strip(),
        "name": str(account.get("name") or "").strip(),
    }


def find_sub2api_accounts_by_identity(identity: dict[str, str]) -> list[dict[str, Any]]:
    chatgpt_account_id = str(identity.get("chatgptAccountId") or "").strip()
    email = str(identity.get("email") or "").strip().lower()
    identity_email_key = str(identity.get("emailKey") or "").strip().lower()
    matches: list[dict[str, Any]] = []
    for account in SUB2API.list_accounts():
        credentials = account.get("credentials") if isinstance(account.get("credentials"), dict) else {}
        extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
        account_chatgpt_id = str(credentials.get("chatgpt_account_id") or "").strip()
        account_email = str(first_non_empty(credentials.get("email"), extra.get("email"), account.get("name")) or "").strip().lower()
        account_email_key = str(first_non_empty(extra.get("email_key"), email_key(account_email)) or "").strip().lower()
        if chatgpt_account_id and account_chatgpt_id == chatgpt_account_id:
            matches.append(account)
            continue
        if email and account_email == email:
            matches.append(account)
            continue
        if identity_email_key and account_email_key == identity_email_key:
            matches.append(account)
    return matches


def parse_group_names(value: Any) -> list[str]:
    raw_items = value if isinstance(value, list) else re.split(r"[\n,;，；]+", str(value or ""))
    names: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        name = str(item or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def bind_sub2api_import_to_target_groups(document: dict[str, Any], group_names: Any = None) -> dict[str, Any]:
    names = parse_group_names(group_names if group_names is not None else CONFIG.sub2api_import_group_names)
    if not names:
        names = parse_group_names(CONFIG.sub2api_monitor_group_name)
    if not names:
        return {"success": False, "skipped": True, "reason": "empty_target_groups"}
    identity = sub2api_document_account_identity(document)
    accounts = find_sub2api_accounts_by_identity(identity)
    account_ids: list[int] = []
    for account in accounts:
        try:
            account_id = int(account.get("id"))
        except (TypeError, ValueError):
            continue
        if account_id not in account_ids:
            account_ids.append(account_id)
    if not account_ids:
        return {"success": False, "reason": "imported_account_not_found", "identity": identity}
    results: list[dict[str, Any]] = []
    updated = 0
    skipped = 0
    failed = 0
    for group_name in names:
        try:
            result = SUB2API.bind_accounts_to_group(account_ids, group_name, platform="openai")
        except Exception as error:
            result = {"success": False, "failed": len(account_ids), "error": str(error), "targetGroup": {"name": group_name}}
        updated += int(result.get("updated") or 0)
        skipped += int(result.get("skipped") or 0)
        failed += int(result.get("failed") or 0)
        if not result.get("success"):
            failed = max(failed, 1)
        results.append(result)
    return {
        "success": all(item.get("success") for item in results),
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "targetGroupNames": names,
        "results": results,
        "identity": identity,
        "accountIds": account_ids,
    }


def bind_sub2api_import_to_monitor_group(document: dict[str, Any]) -> dict[str, Any]:
    return bind_sub2api_import_to_target_groups(document)


CLIENT = HeroSmsClient(CONFIG.api_key, CONFIG.api_url, CONFIG.timeout_ms)
TELE_AUTO = TeleAutoClient(
    CONFIG.tele_auto_enabled,
    CONFIG.tele_auto_api_url,
    CONFIG.tele_auto_username,
    CONFIG.tele_auto_password,
    CONFIG.timeout_ms,
)
TEMP_MAIL = TempMailClient(CONFIG.temp_mail_api_url, CONFIG.temp_mail_admin_password, CONFIG.timeout_ms)
OUTLOOK_EMAIL = OutlookEmailClient(CONFIG.outlook_email_api_url, CONFIG.outlook_email_api_key, CONFIG.timeout_ms)
OUTLOOK_EMAIL_ADMIN = OutlookEmailAdminClient(
    CONFIG.outlook_email_api_url,
    CONFIG.outlook_email_admin_password,
    CONFIG.timeout_ms,
)
SUB2API = Sub2ApiClient(
    CONFIG.sub2api_api_url,
    CONFIG.sub2api_admin_email,
    CONFIG.sub2api_admin_password,
    CONFIG.sub2api_admin_token,
    CONFIG.timeout_ms,
)
PROXY_USAGE_LOCK = threading.Lock()
IDENTITY_BINDINGS_LOCK = threading.Lock()
LOGIN_ATTEMPTS_LOCK = threading.Lock()
LOGIN_FAILED_ATTEMPTS: dict[str, list[float]] = {}
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 600
LOGIN_RATE_LIMIT_MAX_FAILURES = 8


def reload_runtime_config() -> None:
    global APP_CONFIG_VALUES, CLIENT, TELE_AUTO, TEMP_MAIL, OUTLOOK_EMAIL, OUTLOOK_EMAIL_ADMIN, SUB2API, STORE

    APP_CONFIG_VALUES = load_config_values()
    CONFIG.host = os.getenv("AUTOMYAI_HOST") or app_config_value("HOST", "127.0.0.1")
    CONFIG.port = int(os.getenv("AUTOMYAI_PORT") or app_config_value("PORT", str(CONFIG.port)))
    CONFIG.api_key = app_config_value("HERO_SMS_API_KEY", "")
    CONFIG.api_url = app_config_value("HERO_SMS_API_URL", "https://hero-sms.com/stubs/handler_api.php")
    CONFIG.default_service_name = DEFAULT_SERVICE_NAME
    CONFIG.default_service_code = DEFAULT_SERVICE_CODE
    CONFIG.default_country_name = ""
    CONFIG.default_country_code = ""
    CONFIG.default_operator = "any"
    CONFIG.default_max_price = ""
    CONFIG.default_exact_price = ""
    CONFIG.default_fixed_price = ""
    CONFIG.timeout_ms = int(app_config_value("REQUEST_TIMEOUT_MS", str(CONFIG.timeout_ms)))
    CONFIG.enable_cors = app_config_value("ENABLE_CORS", "true").lower() == "true"
    CONFIG.store_file = (ROOT / app_config_value("STORE_FILE", "./data/activations.json")).resolve()
    CONFIG.purchase_config_file = (ROOT / app_config_value("PURCHASE_CONFIG_FILE", "./data/purchase_config.json")).resolve()
    CONFIG.tele_auto_enabled = parse_bool_flag(app_config_value("TELE_AUTO_ENABLED", "true"), default=True)
    CONFIG.tele_auto_api_url = app_config_value("TELE_AUTO_API_URL", "http://127.0.0.1:8028").rstrip("/")
    CONFIG.tele_auto_username = app_config_value("TELE_AUTO_USERNAME", "")
    CONFIG.tele_auto_password = app_config_value("TELE_AUTO_PASSWORD", "")
    CONFIG.temp_mail_api_url = app_config_value("TEMP_MAIL_API_URL", "").rstrip("/")
    CONFIG.temp_mail_admin_password = app_config_value("TEMP_MAIL_ADMIN_PASSWORD", "")
    CONFIG.outlook_email_api_url = app_config_value("OUTLOOK_EMAIL_API_URL", "").rstrip("/")
    CONFIG.outlook_email_api_key = app_config_value("OUTLOOK_EMAIL_API_KEY", "")
    CONFIG.outlook_email_admin_password = app_config_value("OUTLOOK_EMAIL_ADMIN_PASSWORD", "")
    CONFIG.mail_source_group_name = app_config_value("MAIL_SOURCE_GROUP_NAME", "默认分组") or "默认分组"
    CONFIG.mail_pending_group_name = app_config_value("MAIL_PENDING_GROUP_NAME", "gpt_pending_account") or "gpt_pending_account"
    CONFIG.mail_success_group_name = app_config_value("MAIL_SUCCESS_GROUP_NAME", "gpt_new_account") or "gpt_new_account"
    CONFIG.mail_bad_group_name = app_config_value("MAIL_BAD_GROUP_NAME", "badmail") or "badmail"
    CONFIG.sub2api_api_url = app_config_value("SUB2API_API_URL", "").rstrip("/")
    CONFIG.sub2api_admin_email = app_config_value("SUB2API_ADMIN_EMAIL", "")
    CONFIG.sub2api_admin_password = app_config_value("SUB2API_ADMIN_PASSWORD", "")
    CONFIG.sub2api_admin_token = app_config_value("SUB2API_ADMIN_TOKEN", "")
    CONFIG.sub2api_monitor_enabled = parse_bool_flag(app_config_value("SUB2API_MONITOR_ENABLED", "true"), default=True)
    CONFIG.sub2api_monitor_group_name = app_config_value("SUB2API_MONITOR_GROUP_NAME", "auto") or "auto"
    CONFIG.sub2api_import_group_names = app_config_value("SUB2API_IMPORT_GROUP_NAMES", "auto") or "auto"
    CONFIG.sub2api_monitor_min_ok_accounts = app_config_value("SUB2API_MONITOR_MIN_OK_ACCOUNTS", "1")
    CONFIG.sub2api_monitor_busy_min_ok_accounts = app_config_value("SUB2API_MONITOR_BUSY_MIN_OK_ACCOUNTS", "2")
    CONFIG.sub2api_monitor_interval_seconds = app_config_value("SUB2API_MONITOR_INTERVAL_SECONDS", "30")
    CONFIG.sub2api_monitor_busy_window_seconds = app_config_value("SUB2API_MONITOR_BUSY_WINDOW_SECONDS", "300")
    CONFIG.sub2api_monitor_trigger_cooldown_seconds = app_config_value("SUB2API_MONITOR_TRIGGER_COOLDOWN_SECONDS", "60")
    CONFIG.sub2api_monitor_max_start_accounts = app_config_value("SUB2API_MONITOR_MAX_START_ACCOUNTS", "2")
    CONFIG.sub2api_monitor_import_mail_source = parse_bool_flag(app_config_value("SUB2API_MONITOR_IMPORT_MAIL_SOURCE", "true"), default=True)
    CONFIG.cpa_monitor_enabled = parse_bool_flag(app_config_value("CPA_MONITOR_ENABLED", "true"), default=True)
    CONFIG.cpa_monitor_min_ok_accounts = app_config_value("CPA_MONITOR_MIN_OK_ACCOUNTS", "5")
    CONFIG.cpa_monitor_interval_seconds = app_config_value("CPA_MONITOR_INTERVAL_SECONDS", "60")
    CONFIG.cpa_monitor_trigger_cooldown_seconds = app_config_value("CPA_MONITOR_TRIGGER_COOLDOWN_SECONDS", "900")
    CONFIG.cpa_monitor_register_count = app_config_value("CPA_MONITOR_REGISTER_COUNT", "2")
    CONFIG.cpa_monitor_register_threads = app_config_value("CPA_MONITOR_REGISTER_THREADS", "2")
    CONFIG.cpa_monitor_proxy = app_config_value("CPA_MONITOR_PROXY", "")
    CONFIG.browser_display = app_config_value("BROWSER_DISPLAY", ":1")
    CONFIG.browser_proxy = app_config_value("BROWSER_PROXY", "")
    CONFIG.uc_signup_proxy = app_config_value("UC_SIGNUP_PROXY", "")
    CONFIG.signup_proxy_mode = app_config_value("SIGNUP_PROXY_MODE", "custom")
    CONFIG.signup_proxy_region = app_config_value("SIGNUP_PROXY_REGION", "JP")
    CONFIG.signup_proxy_custom_url = app_config_value("SIGNUP_PROXY_CUSTOM_URL", "")
    CONFIG.cliproxy_proxy_url = app_config_value("CLIPROXY_PROXY_URL", "")
    CONFIG.proxy_pool_urls = app_config_value("PROXY_POOL_URLS", "")
    CONFIG.proxy_randomize = parse_bool_flag(app_config_value("PROXY_RANDOMIZE", "true"), default=True)
    CONFIG.proxy_usage_window_seconds = app_config_value("PROXY_USAGE_WINDOW_SECONDS", "86400")
    CONFIG.proxy_usage_max_per_window = app_config_value("PROXY_USAGE_MAX_PER_WINDOW", "3")
    CONFIG.phone_code_window_seconds = app_config_value("PHONE_CODE_WINDOW_SECONDS", "3600")
    CONFIG.phone_code_max_per_window = app_config_value("PHONE_CODE_MAX_PER_WINDOW", "1")
    CONFIG.phone_code_max_total = app_config_value("PHONE_CODE_MAX_TOTAL", "3")
    CONFIG.phone_whatsapp_cooldown_seconds = app_config_value("PHONE_WHATSAPP_COOLDOWN_SECONDS", "21600")
    CONFIG.phone_sms_cooldown_seconds = app_config_value("PHONE_SMS_COOLDOWN_SECONDS", "1800")
    CONFIG.sub2api_proxy_region = app_config_value("SUB2API_PROXY_REGION", "")
    CONFIG.sub2api_proxy_url = app_config_value("SUB2API_PROXY_URL", "")
    CONFIG.sub2api_proxy_name = app_config_value("SUB2API_PROXY_NAME", "")
    CONFIG.sub2api_import_use_signup_proxy = parse_bool_flag(
        app_config_value("SUB2API_IMPORT_USE_SIGNUP_PROXY", "false"), default=False
    )
    CONFIG.uc_signup_phone_retries = app_config_value("UC_SIGNUP_PHONE_RETRIES", "0")
    CONFIG.uc_signup_sms_timeout_seconds = app_config_value("UC_SIGNUP_SMS_TIMEOUT_SECONDS", "135")
    CONFIG.uc_signup_sms_poll_interval_seconds = app_config_value("UC_SIGNUP_SMS_POLL_INTERVAL_SECONDS", "10")
    CONFIG.uc_signup_phone_password_page_timeout = app_config_value("UC_SIGNUP_PHONE_PASSWORD_PAGE_TIMEOUT", "25")
    CONFIG.uc_signup_cf_clearance_enabled = app_config_value("UC_SIGNUP_CF_CLEARANCE_ENABLED", "false")
    CONFIG.uc_signup_cf_clearance_api_url = app_config_value("UC_SIGNUP_CF_CLEARANCE_API_URL", "http://127.0.0.1:18191/v1")
    CONFIG.uc_signup_cf_clearance_target_url = app_config_value(
        "UC_SIGNUP_CF_CLEARANCE_TARGET_URL", "https://chatgpt.com/auth/login?intent=signup"
    )
    CONFIG.uc_signup_cf_clearance_timeout_seconds = app_config_value("UC_SIGNUP_CF_CLEARANCE_TIMEOUT_SECONDS", "90")
    CONFIG.uc_signup_cf_clearance_cache_seconds = app_config_value("UC_SIGNUP_CF_CLEARANCE_CACHE_SECONDS", "1800")
    CONFIG.uc_signup_chrome_binary = app_config_value("UC_SIGNUP_CHROME_BINARY", "")
    CONFIG.uc_signup_chrome_version = app_config_value("UC_SIGNUP_CHROME_VERSION", "")
    CONFIG.uc_signup_keep_browser_on_failure = app_config_value("UC_SIGNUP_KEEP_BROWSER_ON_FAILURE", "false")
    CONFIG.uc_signup_keep_browser_seconds = app_config_value("UC_SIGNUP_KEEP_BROWSER_SECONDS", "0")
    CONFIG.uc_signup_idle_timeout_seconds = app_config_value("UC_SIGNUP_IDLE_TIMEOUT_SECONDS", "600")
    CONFIG.uc_signup_retryable_email_cooldown_seconds = app_config_value("UC_SIGNUP_RETRYABLE_EMAIL_COOLDOWN_SECONDS", "900")
    CONFIG.uc_signup_profile_base_dir = app_config_value("UC_SIGNUP_PROFILE_BASE_DIR", "./data/browser_profiles")
    CONFIG.signup_password = app_config_value("SIGNUP_PASSWORD", "FuckOAI123456!")
    CONFIG.signup_name = app_config_value("SIGNUP_NAME", "")
    CONFIG.signup_age = app_config_value("SIGNUP_AGE", "18")
    CONFIG.cors_allowed_origins = app_config_value("CORS_ALLOWED_ORIGINS", "https://automyai.kfjie.me")
    CONFIG.public_status_enabled = parse_bool_flag(app_config_value("PUBLIC_STATUS_ENABLED", "true"), default=True)
    CONFIG.public_status_allow_origins = app_config_value(
        "PUBLIC_STATUS_ALLOW_ORIGINS",
        "https://kfjie.me,https://www.kfjie.me,https://automyai.kfjie.me",
    )
    CONFIG.public_status_token = app_config_value("PUBLIC_STATUS_TOKEN", "")
    CONFIG.admin_password = os.getenv("ADMIN_PASSWORD", "")

    CLIENT = HeroSmsClient(CONFIG.api_key, CONFIG.api_url, CONFIG.timeout_ms)
    TELE_AUTO = TeleAutoClient(
        CONFIG.tele_auto_enabled,
        CONFIG.tele_auto_api_url,
        CONFIG.tele_auto_username,
        CONFIG.tele_auto_password,
        CONFIG.timeout_ms,
    )
    TEMP_MAIL = TempMailClient(CONFIG.temp_mail_api_url, CONFIG.temp_mail_admin_password, CONFIG.timeout_ms)
    OUTLOOK_EMAIL = OutlookEmailClient(CONFIG.outlook_email_api_url, CONFIG.outlook_email_api_key, CONFIG.timeout_ms)
    OUTLOOK_EMAIL_ADMIN = OutlookEmailAdminClient(
        CONFIG.outlook_email_api_url,
        CONFIG.outlook_email_admin_password,
        CONFIG.timeout_ms,
    )
    SUB2API = Sub2ApiClient(
        CONFIG.sub2api_api_url,
        CONFIG.sub2api_admin_email,
        CONFIG.sub2api_admin_password,
        CONFIG.sub2api_admin_token,
        CONFIG.timeout_ms,
    )
    STORE = ActivationStore(CONFIG.store_file)


def make_admin_session_token() -> str:
    password = CONFIG.admin_password
    if not password:
        return ""
    return hmac.new(password.encode("utf-8"), b"automyai-admin-session", hashlib.sha256).hexdigest()


PUBLIC_STATUS_PATHS = {"/api/public/status", "/api/public/lab-status", "/api/public/cpa-pool", "/api/public/cpa", "/api/public/cpa/wake", "/api/public/wake", "/api/public/ttk/logs", "/api/public/logs"}


def is_public_status_path(path: str) -> bool:
    return path in PUBLIC_STATUS_PATHS


def parse_config_list(value: Any) -> list[str]:
    items = value if isinstance(value, list) else re.split(r"[\s,;，；]+", str(value or ""))
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        key = text.lower().rstrip("/")
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def allowed_cors_origin(origin: str, configured_origins: Any) -> str:
    origin = str(origin or "").strip()
    if not origin:
        return ""
    allowed = parse_config_list(configured_origins)
    if "*" in allowed:
        return "*"
    normalized_origin = origin.rstrip("/")
    for item in allowed:
        if item.rstrip("/") == normalized_origin:
            return origin
    return ""


def request_client_ip(headers: Any, fallback: str = "") -> str:
    real_ip = str(headers.get("X-Real-IP") or "").strip()
    forwarded = str(headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
    return real_ip or forwarded or fallback or "unknown"


def login_rate_limit_status(client_ip: str) -> dict[str, Any]:
    now_ts = time.time()
    cutoff = now_ts - LOGIN_RATE_LIMIT_WINDOW_SECONDS
    with LOGIN_ATTEMPTS_LOCK:
        attempts = [
            timestamp for timestamp in LOGIN_FAILED_ATTEMPTS.get(client_ip, [])
            if float(timestamp or 0) >= cutoff
        ]
        LOGIN_FAILED_ATTEMPTS[client_ip] = attempts
    if len(attempts) < LOGIN_RATE_LIMIT_MAX_FAILURES:
        return {"limited": False, "failed": len(attempts), "retryAfterSeconds": 0}
    oldest = min(attempts)
    retry_after = max(1, int(oldest + LOGIN_RATE_LIMIT_WINDOW_SECONDS - now_ts))
    return {"limited": True, "failed": len(attempts), "retryAfterSeconds": retry_after}


def record_failed_login(client_ip: str) -> dict[str, Any]:
    now_ts = time.time()
    cutoff = now_ts - LOGIN_RATE_LIMIT_WINDOW_SECONDS
    with LOGIN_ATTEMPTS_LOCK:
        attempts = [
            timestamp for timestamp in LOGIN_FAILED_ATTEMPTS.get(client_ip, [])
            if float(timestamp or 0) >= cutoff
        ]
        attempts.append(now_ts)
        LOGIN_FAILED_ATTEMPTS[client_ip] = attempts[-LOGIN_RATE_LIMIT_MAX_FAILURES:]
    return login_rate_limit_status(client_ip)


def clear_failed_logins(client_ip: str) -> None:
    with LOGIN_ATTEMPTS_LOCK:
        LOGIN_FAILED_ATTEMPTS.pop(client_ip, None)


def browser_live_targets() -> list[dict[str, Any]]:
    try:
        output = subprocess.check_output(["ps", "-ef"], text=True, timeout=3)
    except Exception as error:
        return [{"error": f"读取进程失败: {error}"}]

    targets: list[dict[str, Any]] = []
    seen_ports: set[str] = set()
    for line in output.splitlines():
        if "chromium" not in line or "--remote-debugging-port=" not in line:
            continue
        port_match = re.search(r"--remote-debugging-port=(\d+)", line)
        if not port_match:
            continue
        port = port_match.group(1)
        if port in seen_ports:
            continue
        seen_ports.add(port)
        profile_match = re.search(r"--user-data-dir=([^\s]+)", line)
        proxy_match = re.search(r"--proxy-server=([^\s]+)", line)
        parts = line.split(None, 7)
        pid = parts[1] if len(parts) > 1 else ""
        target: dict[str, Any] = {
            "pid": pid,
            "port": port,
            "profile": profile_match.group(1) if profile_match else "",
            "proxy": proxy_match.group(1) if proxy_match else "",
            "pages": [],
        }
        try:
            with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
                pages = json.loads(response.read().decode("utf-8", errors="replace"))
            if isinstance(pages, list):
                target["pages"] = [
                    {
                        "id": str(page.get("id") or ""),
                        "title": str(page.get("title") or ""),
                        "url": str(page.get("url") or ""),
                        "webSocketDebuggerUrl": str(page.get("webSocketDebuggerUrl") or ""),
                    }
                    for page in pages
                    if isinstance(page, dict) and str(page.get("type") or "") == "page"
                ]
        except Exception as error:
            target["error"] = str(error)
        targets.append(target)
    return targets


def browser_live_active_target() -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    targets = [target for target in browser_live_targets() if not target.get("error")]
    for target in targets:
        pages = target.get("pages") if isinstance(target.get("pages"), list) else []
        for page in pages:
            if str(page.get("webSocketDebuggerUrl") or ""):
                return target, page
    return (targets[0], None) if targets else (None, None)


def browser_live_status() -> dict[str, Any]:
    target, page = browser_live_active_target()
    return {
        "running": bool(target and page),
        "target": target or {},
        "page": page or {},
        "targets": browser_live_targets(),
        "updatedAt": now_iso(),
    }


def get_app_settings() -> dict[str, Any]:
    values = load_config_values()
    return {
        "configFile": str(CONFIG_PATH),
        "settings": {key: values.get(key, "") for key in APP_SETTING_FIELDS},
    }


def update_app_settings(payload: dict[str, Any]) -> dict[str, Any]:
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
    if not isinstance(settings, dict):
        raise ValueError("settings 必须是对象")

    current = load_config_values()
    next_values = {
        key: str(settings[key]).strip() if key in settings and settings[key] is not None else current.get(key, "")
        for key in APP_SETTING_FIELDS
    }
    write_config_values(next_values)
    reload_runtime_config()
    return get_app_settings()


def get_ui_settings() -> dict[str, Any]:
    values = load_config_values()
    if not values.get("CLIPROXY_PROXY_URL"):
        values["CLIPROXY_PROXY_URL"] = saved_cliproxy_url()
    return {
        "settings": {key: values.get(key, "") for key in UI_SETTINGS_PUBLIC_FIELDS},
        "secretsConfigured": {key: bool(values.get(key, "")) for key in UI_SETTINGS_SECRET_FIELDS},
    }


def update_ui_settings(payload: dict[str, Any]) -> dict[str, Any]:
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
    if not isinstance(settings, dict):
        raise ValueError("settings 必须是对象")
    unsupported = sorted(str(key) for key in settings if str(key) not in UI_SETTINGS_FIELDS)
    if unsupported:
        raise ValueError(f"不支持的设置字段: {', '.join(unsupported)}")
    current = load_config_values()
    for key, value in settings.items():
        if value is None:
            continue
        text = str(value).strip()
        if key in UI_SETTINGS_SECRET_FIELDS and not text:
            continue
        if key == "UI_THEME" and text not in UI_THEME_VALUES:
            raise ValueError(f"不支持的界面主题: {text}")
        current[key] = text
    write_config_values(current)
    reload_runtime_config()
    return get_ui_settings()


def normalize_email_prefix(value: Any, random_length: int = 10) -> str:
    prefix = str(value or "").strip()
    if not prefix:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", prefix):
        raise ValueError("邮箱前缀只能包含字母、数字、点、下划线、加号和短横线")
    if len(prefix) + random_length > 64:
        raise ValueError(f"邮箱前缀最多 {64 - random_length} 个字符")
    return prefix


def normalize_email_lines(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value or "").splitlines()
    emails: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        email = str(raw_item or "").strip()
        if not email:
            continue
        if "@" not in email:
            raise ValueError(f"邮箱格式不正确: {email}")
        if email in seen:
            continue
        seen.add(email)
        emails.append(email)
    return emails


def generate_random_emails(domain: str, total: int, prefix: str = "") -> list[str]:
    normalized_domain = domain.strip()
    if not normalized_domain:
        raise ValueError("请填写邮箱后缀域名，例如 example.com")
    normalized_prefix = normalize_email_prefix(prefix)
    results: list[str] = []
    seen: set[str] = set()
    while len(results) < total:
        address = f"{normalized_prefix}{generate_random_local_part()}@{normalized_domain}"
        if address in seen:
            continue
        seen.add(address)
        results.append(address)
    return results


def default_email_queue() -> dict[str, Any]:
    return {
        "emails": [],
        "cursor": 0,
        "activeEmail": "",
        "activeStartedAt": "",
        "lastMail": None,
        "randomPrefix": "",
    }


def load_email_queue() -> dict[str, Any]:
    data = load_json_file(EMAIL_QUEUE_PATH)
    queue = default_email_queue()
    emails = normalize_email_lines(data.get("emails", [])) if isinstance(data, dict) else []
    cursor = parse_positive_int(data.get("cursor", 0), default=0) if isinstance(data, dict) else 0
    queue.update(
        {
            "emails": emails,
            "cursor": min(max(cursor, 0), max(len(emails) - 1, 0)),
            "activeEmail": str(data.get("activeEmail") or "").strip() if isinstance(data, dict) else "",
            "activeStartedAt": str(data.get("activeStartedAt") or "").strip() if isinstance(data, dict) else "",
            "lastMail": data.get("lastMail") if isinstance(data, dict) else None,
            "randomPrefix": normalize_email_prefix(data.get("randomPrefix") if isinstance(data, dict) else ""),
        }
    )
    return queue


def save_email_queue(queue: dict[str, Any]) -> dict[str, Any]:
    queue = {
        **default_email_queue(),
        **queue,
        "emails": normalize_email_lines(queue.get("emails", [])),
    }
    queue["cursor"] = min(max(int(queue.get("cursor") or 0), 0), max(len(queue["emails"]) - 1, 0))
    save_json_file(EMAIL_QUEUE_PATH, queue)
    return queue


def update_email_queue(payload: dict[str, Any]) -> dict[str, Any]:
    current = load_email_queue()
    emails = normalize_email_lines(payload.get("emailsText", payload.get("emails", [])))
    cursor = parse_positive_int(payload.get("cursor", current.get("cursor", 0)), default=0)
    return save_email_queue({**current, "emails": emails, "cursor": cursor})


def generate_email_queue(payload: dict[str, Any]) -> dict[str, Any]:
    total = parse_positive_int(payload.get("total"), default=1)
    prefix = normalize_email_prefix(payload.get("prefix"))
    emails = generate_random_emails(str(payload.get("domain") or ""), total, prefix)
    return save_email_queue({
        **load_email_queue(),
        "emails": emails,
        "cursor": 0,
        "activeEmail": "",
        "activeStartedAt": "",
        "lastMail": None,
        "randomPrefix": prefix,
    })


def refresh_active_email_mail(address: str | None = None) -> dict[str, Any]:
    queue = load_email_queue()
    email = str(address or queue.get("activeEmail") or "").strip()
    if not email:
        raise TempMailError("当前没有活动邮箱")
    source = "tempMail"
    warning = ""
    opus_reader = OpusMailAdminReader.from_project(ROOT)
    opus_mapping = None
    if opus_reader.configured:
        try:
            opus_mapping = opus_reader.find_mapping_by_email(email)
        except OpusMailAdminReaderError as error:
            warning = str(error)
    if opus_mapping:
        mail = opus_reader.latest_verification_code(email)
        source = "opusMail"
    elif OUTLOOK_EMAIL.configured:
        try:
            mail = enrich_temp_mail_item(OUTLOOK_EMAIL.latest_mail(email))
            source = "outlookEmail"
        except OutlookEmailError as error:
            if not (CONFIG.temp_mail_api_url and CONFIG.temp_mail_admin_password):
                raise
            warning = str(error)
            mail = enrich_temp_mail_item(TEMP_MAIL.latest_mail(email))
    else:
        mail = enrich_temp_mail_item(TEMP_MAIL.latest_mail(email))
    queue = save_email_queue({**queue, "activeEmail": email, "lastMail": mail})
    result = {"emailQueue": queue, "address": email, "item": mail, "source": source}
    if warning:
        result["warning"] = warning
    return result


def normalize_outlook_accounts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    accounts = payload.get("accounts") if isinstance(payload, dict) else []
    if not isinstance(accounts, list):
        return []
    return [account for account in accounts if isinstance(account, dict)]


def is_usable_outlook_account(account: dict[str, Any]) -> bool:
    status = str(account.get("status") or "").strip().lower()
    refresh_status = str(account.get("last_refresh_status") or "").strip().lower()
    return status in {"", "active"} and refresh_status not in {"failed", "error"}


def compact_outlook_account(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": account.get("id"),
        "email": account.get("email") or "",
        "groupId": account.get("group_id"),
        "groupName": account.get("group_name") or "",
        "status": account.get("status") or "",
        "lastRefreshStatus": account.get("last_refresh_status") or "",
        "lastRefreshError": account.get("last_refresh_error") or "",
        "updatedAt": account.get("updated_at") or "",
    }


def build_outlook_account_control_payload(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the read-only account picker surface used by OpenAI 3."""
    bad_group_name = str(CONFIG.mail_bad_group_name or "badmail").strip() or "badmail"
    items: list[dict[str, Any]] = []
    group_counts: dict[str, dict[str, Any]] = {}
    for account in accounts:
        compact = compact_outlook_account(account)
        refresh_error_present = bool(str(compact.pop("lastRefreshError", "") or "").strip())
        group_name = str(compact.get("groupName") or "未分组")
        is_badmail = group_name == bad_group_name
        upstream_usable = is_usable_outlook_account(account)
        selectable = upstream_usable and not is_badmail
        status = str(compact.get("status") or "").strip().lower()
        refresh_status = str(compact.get("lastRefreshStatus") or "").strip().lower()
        if is_badmail:
            health_label = "已隔离"
        elif refresh_status in {"failed", "error"}:
            health_label = "刷新失败"
        elif status not in {"", "active"}:
            health_label = f"账号状态 {status}"
        else:
            health_label = "可选择"
        item = {
            **compact,
            "badmail": is_badmail,
            "upstreamUsable": upstream_usable,
            "selectable": selectable,
            "healthLabel": health_label,
            "refreshErrorPresent": refresh_error_present,
        }
        items.append(item)
        group = group_counts.setdefault(
            group_name,
            {"name": group_name, "total": 0, "selectable": 0, "blocked": 0, "badmail": is_badmail},
        )
        group["total"] += 1
        group["selectable" if selectable else "blocked"] += 1

    items.sort(
        key=lambda item: (
            not bool(item.get("selectable")),
            str(item.get("groupName") or ""),
            str(item.get("email") or "").lower(),
        )
    )
    groups = sorted(
        group_counts.values(),
        key=lambda item: (bool(item.get("badmail")), str(item.get("name") or "")),
    )
    return {
        "success": True,
        "total": len(items),
        "selectable": sum(1 for item in items if item.get("selectable")),
        "blocked": sum(1 for item in items if not item.get("selectable")),
        "badmail": sum(1 for item in items if item.get("badmail")),
        "groups": groups,
        "accounts": items,
        "updatedAt": now_iso(),
    }


def outlook_source_account_status(account: dict[str, Any], stage_record: dict[str, Any] | None) -> dict[str, Any]:
    email = str(account.get("email") or "").strip()
    status = str(account.get("status") or "").strip().lower()
    refresh_status = str(account.get("last_refresh_status") or "").strip().lower()
    upstream_usable = is_usable_outlook_account(account)
    registered = isinstance(stage_record, dict) and stage_record.get("registered") is True
    locally_touched = signup_email_has_local_state(stage_record)
    retry_after = stage_record.get("retryAfter") or stage_record.get("retryAfterAt") if isinstance(stage_record, dict) else ""
    retry_after_ts = signup_email_retry_after(stage_record) if isinstance(stage_record, dict) else 0.0
    retryable_hold = retry_after_ts > time.time()
    queue_eligible = upstream_usable and not retryable_hold and not locally_touched

    if retryable_hold:
        queue_skip_reason = "cooldown"
        label = "冷却中"
    elif locally_touched:
        queue_skip_reason = "claimed"
        label = "已注册待授权" if registered else "已取用待处理"
    elif not upstream_usable:
        queue_skip_reason = "unusable"
        if refresh_status in {"failed", "error"}:
            label = "邮箱刷新失败"
        elif status and status != "active":
            label = f"账号状态 {status}"
        else:
            label = "不可用"
    else:
        queue_skip_reason = ""
        label = "已注册待授权" if registered else "可导入"

    result = {
        **compact_outlook_account(account),
        "upstreamUsable": upstream_usable,
        "registered": registered,
        "locallyTouched": locally_touched,
        "retryableHold": retryable_hold,
        "retryAfter": retry_after if retryable_hold else "",
        "retryableCount": stage_record.get("retryableCount") if isinstance(stage_record, dict) else 0,
        "lastRetryableError": stage_record.get("lastRetryableError") if isinstance(stage_record, dict) else "",
        "queueEligible": queue_eligible,
        "queueSkipReason": queue_skip_reason,
        "queueStatusLabel": label,
    }
    if not result["email"]:
        result["email"] = email
    return result


def outlook_group_names(source_group_name: str = "") -> dict[str, str]:
    return {
        "source": str(source_group_name or CONFIG.mail_source_group_name or "默认分组").strip() or "默认分组",
        "pending": CONFIG.mail_pending_group_name or "gpt_pending_account",
        "success": CONFIG.mail_success_group_name or "gpt_new_account",
        "bad": CONFIG.mail_bad_group_name or "badmail",
    }


def build_outlook_email_inventory(source_group_name: str = "") -> dict[str, Any]:
    payload = OUTLOOK_EMAIL.list_accounts()
    accounts = normalize_outlook_accounts(payload)
    groups_by_key: dict[str, dict[str, Any]] = {}
    status_counts: dict[str, int] = {}
    refresh_status_counts: dict[str, int] = {}

    for account in accounts:
        group_id = account.get("group_id")
        group_name = str(account.get("group_name") or "未分组")
        group_key = str(group_id) if group_id not in (None, "") else f"name:{group_name}"
        group = groups_by_key.setdefault(
            group_key,
            {
                "id": group_id,
                "name": group_name,
                "color": account.get("group_color") or "",
                "total": 0,
                "usable": 0,
                "failedRefresh": 0,
                "active": 0,
            },
        )
        group["total"] += 1
        if is_usable_outlook_account(account):
            group["usable"] += 1
        if str(account.get("status") or "").strip().lower() in {"", "active"}:
            group["active"] += 1
        if str(account.get("last_refresh_status") or "").strip().lower() in {"failed", "error"}:
            group["failedRefresh"] += 1

        status = str(account.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        refresh_status = str(account.get("last_refresh_status") or "unknown")
        refresh_status_counts[refresh_status] = refresh_status_counts.get(refresh_status, 0) + 1

    admin_warning = ""
    if OUTLOOK_EMAIL_ADMIN.configured:
        try:
            for group in OUTLOOK_EMAIL_ADMIN.list_groups():
                group_id = group.get("id")
                group_key = str(group_id) if group_id not in (None, "") else f"name:{group.get('name') or ''}"
                existing = groups_by_key.setdefault(
                    group_key,
                    {
                        "id": group_id,
                        "name": group.get("name") or "",
                        "color": group.get("color") or "",
                        "total": 0,
                        "usable": 0,
                        "failedRefresh": 0,
                        "active": 0,
                    },
                )
                existing["id"] = group_id
                existing["name"] = group.get("name") or existing.get("name") or ""
                existing["color"] = group.get("color") or existing.get("color") or ""
                if "account_count" in group and existing.get("total", 0) == 0:
                    existing["total"] = int(group.get("account_count") or 0)
                if "descendant_account_count" in group:
                    existing["descendantTotal"] = int(group.get("descendant_account_count") or 0)
        except OutlookEmailError as error:
            admin_warning = str(error)

    names = outlook_group_names(source_group_name)
    source_accounts = [
        account for account in accounts
        if str(account.get("group_name") or "") == names["source"]
    ]
    usable_source_accounts = [account for account in source_accounts if is_usable_outlook_account(account)]
    stage_state = load_signup_email_stage_state()
    source_account_statuses = [
        outlook_source_account_status(
            account,
            stage_state.get(str(account.get("email") or "").strip().lower()),
        )
        for account in source_accounts
    ]
    queue_eligible_accounts = [account for account in source_account_statuses if account.get("queueEligible")]
    groups = sorted(
        groups_by_key.values(),
        key=lambda item: (str(item.get("name") or "") != names["source"], str(item.get("name") or "")),
    )
    result = {
        "success": True,
        "total": len(accounts),
        "usable": sum(1 for account in accounts if is_usable_outlook_account(account)),
        "statusCounts": status_counts,
        "refreshStatusCounts": refresh_status_counts,
        "groups": groups,
        "groupNames": names,
        "sourceGroup": {
            "name": names["source"],
            "total": len(source_accounts),
            "usable": len(usable_source_accounts),
            "queueEligible": len(queue_eligible_accounts),
            "registered": sum(1 for account in source_account_statuses if account.get("registered")),
            "claimed": sum(1 for account in source_account_statuses if account.get("locallyTouched")),
            "cooldown": sum(1 for account in source_account_statuses if account.get("retryableHold")),
            "unusable": sum(1 for account in source_account_statuses if not account.get("upstreamUsable")),
            "failedRefresh": sum(
                1 for account in source_accounts
                if str(account.get("last_refresh_status") or "").strip().lower() in {"failed", "error"}
            ),
        },
        "pendingGroup": next((group for group in groups if group.get("name") == names["pending"]), None),
        "successGroup": next((group for group in groups if group.get("name") == names["success"]), None),
        "badGroup": next((group for group in groups if group.get("name") == names["bad"]), None),
        "sourceAccounts": [compact_outlook_account(account) for account in usable_source_accounts[:50]],
        "sourceAccountsAll": source_account_statuses[:200],
        "queueEligibleAccounts": queue_eligible_accounts[:200],
        "sourceAccountsLimit": 200,
        "updatedAt": now_iso(),
        "rawTotal": payload.get("total") if isinstance(payload, dict) else len(accounts),
    }
    if admin_warning:
        result["adminWarning"] = admin_warning
    return result


def parse_mail_account_identifiers(payload: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("identifiers", "accountIds", "account_ids", "emails"):
        value = payload.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif value not in (None, ""):
            values.append(value)
    text = str(payload.get("identifiersText") or payload.get("text") or "").strip()
    if text:
        values.extend(re.split(r"[\s,;，；]+", text))
    result = []
    seen = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def resolve_outlook_account_ids(identifiers: list[str]) -> tuple[list[int], list[dict[str, Any]], list[str]]:
    payload = OUTLOOK_EMAIL.list_accounts()
    accounts = normalize_outlook_accounts(payload)
    by_id = {str(account.get("id")): account for account in accounts if account.get("id") not in (None, "")}
    by_email = {str(account.get("email") or "").lower(): account for account in accounts if account.get("email")}
    ids: list[int] = []
    matched: list[dict[str, Any]] = []
    missing: list[str] = []
    seen_ids: set[int] = set()
    for identifier in identifiers:
        account = by_id.get(identifier)
        if account is None:
            account = by_email.get(identifier.lower())
        if account is None:
            missing.append(identifier)
            continue
        try:
            account_id = int(account.get("id"))
        except (TypeError, ValueError):
            missing.append(identifier)
            continue
        if account_id in seen_ids:
            continue
        seen_ids.add(account_id)
        ids.append(account_id)
        matched.append(compact_outlook_account(account))
    return ids, matched, missing


def target_mail_group_name(payload: dict[str, Any]) -> str:
    target = str(payload.get("target") or "").strip().lower()
    if target in {"pending", "half", "registered", "待授权", "gpt_pending_account"}:
        return CONFIG.mail_pending_group_name
    if target in {"success", "used", "ok", "gpt_new_account"}:
        return CONFIG.mail_success_group_name
    if target in {"bad", "failed", "badmail"}:
        return CONFIG.mail_bad_group_name
    name = str(payload.get("targetGroupName") or payload.get("groupName") or "").strip()
    if name:
        return name
    raise OutlookEmailError("缺少目标分组")


def move_outlook_accounts(payload: dict[str, Any]) -> dict[str, Any]:
    identifiers = parse_mail_account_identifiers(payload)
    if not identifiers:
        raise OutlookEmailError("请输入账号 ID 或邮箱")
    account_ids, matched, missing = resolve_outlook_account_ids(identifiers)
    if not account_ids:
        raise OutlookEmailError("没有匹配到可移动的 OutlookEmail 账号")
    target_group = target_mail_group_name(payload)
    moved = OUTLOOK_EMAIL_ADMIN.move_accounts(account_ids, target_group)
    return {
        "success": True,
        "targetGroupName": target_group,
        "matched": matched,
        "missing": missing,
        "moveResult": moved,
    }


def move_outlook_email_after_signup(email: str, target_group_name: str, *, provider: str = "") -> dict[str, Any]:
    if str(provider or "").strip().lower() in {"opusmail", "mailopus", "opus"}:
        return {"success": True, "skipped": True, "reason": "Mail Opus 账号使用 Mail Admin 状态路径"}
    if not email or not OUTLOOK_EMAIL.configured or not OUTLOOK_EMAIL_ADMIN.configured:
        return {"success": False, "skipped": True, "reason": "OutlookEmail 管理接口未配置"}
    payload = OUTLOOK_EMAIL.list_accounts()
    accounts = normalize_outlook_accounts(payload)
    account = next((item for item in accounts if str(item.get("email") or "").lower() == email.lower()), None)
    if not account:
        return {"success": False, "skipped": True, "reason": "邮箱不在 OutlookEmail 账号列表"}
    if str(account.get("group_name") or "") == target_group_name:
        return {"success": True, "skipped": True, "reason": "邮箱已在目标分组", "account": compact_outlook_account(account)}
    moved = OUTLOOK_EMAIL_ADMIN.move_accounts([int(account["id"])], target_group_name)
    return {"success": True, "account": compact_outlook_account(account), "moveResult": moved}


def load_signup_email_stage_state() -> dict[str, Any]:
    data = load_json_file(UC_SIGNUP_EMAIL_STAGE_PATH)
    if not isinstance(data, dict):
        return {}
    return data


def save_signup_email_stage_state(data: dict[str, Any]) -> None:
    save_json_file(UC_SIGNUP_EMAIL_STAGE_PATH, data)


def signup_email_retry_cooldown_seconds() -> int:
    return parse_positive_int(CONFIG.uc_signup_retryable_email_cooldown_seconds, default=900)


def signup_email_retry_after(record: dict[str, Any] | None) -> float:
    if not isinstance(record, dict):
        return 0.0
    retry_after = record.get("retryAfter") or record.get("retryAfterAt")
    parsed = parse_timestamp(retry_after)
    return float(parsed or 0)


def signup_email_has_local_state(record: dict[str, Any] | None) -> bool:
    if not isinstance(record, dict):
        return False
    keys = (
        "claimedAt",
        "password",
        "passwordGeneratedAt",
        "registered",
        "retryableCount",
        "lastRetryableError",
    )
    return any(record.get(key) not in (None, "", False, 0) for key in keys)


def retryable_signup_email_holds() -> dict[str, dict[str, Any]]:
    now_ts = time.time()
    result: dict[str, dict[str, Any]] = {}
    for key, record in load_signup_email_stage_state().items():
        if not isinstance(record, dict):
            continue
        retry_after_ts = signup_email_retry_after(record)
        if retry_after_ts > now_ts:
            result[str(key).strip().lower()] = record
    return result


def mark_signup_email_claimed(email: str) -> None:
    key = str(email or "").strip().lower()
    if not key:
        return
    now = now_iso()
    data = load_signup_email_stage_state()
    existing = data.get(key) if isinstance(data.get(key), dict) else {}
    data[key] = {
        **existing,
        "email": str(email or "").strip(),
        "claimedAt": existing.get("claimedAt") or now,
        "updatedAt": now,
    }
    save_signup_email_stage_state(data)


def mark_signup_email_retryable_hold(email: str, error: Any = "") -> None:
    key = str(email or "").strip().lower()
    if not key:
        return
    now = now_iso()
    data = load_signup_email_stage_state()
    existing = data.get(key) if isinstance(data.get(key), dict) else {}
    try:
        retryable_count = int(existing.get("retryableCount") or 0) + 1
    except (TypeError, ValueError):
        retryable_count = 1
    cooldown = signup_email_retry_cooldown_seconds()
    if cooldown <= 0:
        return
    # Repeated transient failures should stop cycling through the same alias
    # every few minutes.  Exponential backoff is capped to six hours so an
    # account remains recoverable without blocking the entire pool.
    backoff = min(cooldown * (2 ** max(0, retryable_count - 1)), 6 * 60 * 60)
    retry_after = datetime.fromtimestamp(time.time() + backoff).astimezone().isoformat()
    data[key] = {
        **existing,
        "email": str(email or "").strip(),
        "retryableCount": retryable_count,
        "retryableBackoffSeconds": int(backoff),
        "lastRetryableAt": now,
        "retryAfter": retry_after,
        "lastRetryableError": str(error or "")[:500],
        "updatedAt": now,
    }
    save_signup_email_stage_state(data)


def registered_signup_email_keys() -> set[str]:
    data = load_signup_email_stage_state()
    return {
        str(key or "").strip().lower()
        for key, record in data.items()
        if isinstance(record, dict) and record.get("registered") is True and str(key or "").strip()
    }


def promote_registered_opus_email(email: str, reason: Any = "") -> dict[str, Any]:
    """Move a registered/tokenless Mail Opus alias into the main admin view."""
    key = str(email or "").strip().lower()
    if not key:
        return {"configured": False, "imported": False, "reason": "missing_email"}
    record = load_signup_email_stage_state().get(key)
    record = record if isinstance(record, dict) else {}
    if record.get("oauthStoredInMailAdmin") is True:
        return {
            "configured": True,
            "imported": True,
            "skipped": True,
            "reason": "oauth_already_stored",
        }
    if record.get("webAccessTokenStoredInMailAdmin") is True:
        return {
            "configured": True,
            "imported": True,
            "skipped": True,
            "reason": "web_session_already_stored_rt_pending",
        }
    password = str(record.get("password") or "").strip()
    client = OpusMailClient.from_project(ROOT)
    return client.import_registered_email(
        email=email,
        password=password,
        reason=str(reason or "OAuth / RT 尚未完成，可重新授权")[:500],
    )


def import_outlook_source_group_to_email_queue(source_group_name: str = "") -> dict[str, Any]:
    inventory = build_outlook_email_inventory(source_group_name)
    names = outlook_group_names(source_group_name)
    payload = OUTLOOK_EMAIL.list_accounts()
    source_accounts = [
        account for account in normalize_outlook_accounts(payload)
        if str(account.get("group_name") or "") == names["source"] and is_usable_outlook_account(account)
    ]
    retryable_holds = retryable_signup_email_holds()
    stage_state = load_signup_email_stage_state()
    emails = []
    skipped_retryable = []
    skipped_claimed = []
    skipped_unusable = []
    for account in source_accounts:
        email = str(account.get("email") or "").strip()
        if not email:
            continue
        email_key = email.lower()
        stage_record = stage_state.get(email_key)
        if signup_email_has_local_state(stage_record):
            skipped_claimed.append(
                {
                    "email": email,
                    "registered": bool(stage_record.get("registered")) if isinstance(stage_record, dict) else False,
                    "reason": "local_state_exists",
                }
            )
            continue
        if email_key in retryable_holds:
            skipped_retryable.append(
                {
                    "email": email,
                    "retryAfter": retryable_holds[email_key].get("retryAfter") or retryable_holds[email_key].get("retryAfterAt"),
                    "error": retryable_holds[email_key].get("lastRetryableError") or "",
                }
            )
            continue
        if not is_usable_outlook_account(account):
            skipped_unusable.append({"email": email, "reason": "unusable"})
            continue
        emails.append(email)
    queue = save_email_queue({
        **load_email_queue(),
        "emails": emails,
        "cursor": 0,
        "activeEmail": emails[0] if emails else "",
        "activeStartedAt": "",
        "lastMail": None,
    })
    return {
        "success": True,
        "imported": len(emails),
        "skippedRegistered": [],
        "skippedClaimed": skipped_claimed,
        "skippedRetryable": skipped_retryable,
        "skippedUnusable": skipped_unusable,
        "emailQueue": queue,
        "inventory": inventory,
    }


UC_SIGNUP_LOG_MAX_LINES = 200
BEIJING_TZ = ZoneInfo("Asia/Shanghai")

class UcSignupManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._state = UcSignupState()
        self._log_buffer: list[dict[str, str]] = []

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            return self._state.to_dict()

    def get_logs(self) -> list[dict[str, str]]:
        with self._lock:
            return list(self._log_buffer)

    def append_log(self, message: str, level: str = "info") -> None:
        entry = {
            "time": datetime.now(BEIJING_TZ).isoformat(timespec="seconds"),
            "message": str(message),
            "level": level,
        }
        with self._lock:
            self._log_buffer.append(entry)
            while len(self._log_buffer) > UC_SIGNUP_LOG_MAX_LINES:
                self._log_buffer.pop(0)
            self._state.log_lines = list(self._log_buffer)
            self._state.updated_at = now_iso()

    def _update_state(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._state, key):
                    setattr(self._state, key, value)
            self._state.updated_at = now_iso()

    def _add_error(self, message: str) -> None:
        with self._lock:
            self._state.errors.append({"time": now_iso(), "message": str(message)})
            if len(self._state.errors) > 50:
                self._state.errors = self._state.errors[-50:]

    def start(self, emails: list[str], **options: Any) -> dict[str, Any]:
        if not (ROOT / "uc_signup.py").exists():
            return {"error": "未找到 uc_signup.py", "ucSignupState": self.get_state()}

        with self._lock:
            if self._state.running:
                return {"error": "UC 注册任务已在运行中", "ucSignupState": self._state.to_dict()}
            self._stop_event.clear()
            self._process = None
            self._log_buffer = []
            self._state = UcSignupState(
                running=True,
                total=len(emails),
                phase="running",
                started_at=now_iso(),
                updated_at=now_iso(),
            )

        self._thread = threading.Thread(target=self._run, args=(emails, options), daemon=True)
        self._thread.start()
        return {"ucSignupState": self.get_state()}

    def stop(self) -> dict[str, Any]:
        process: subprocess.Popen | None = None
        with self._lock:
            if not self._state.running:
                return {"ucSignupState": self._state.to_dict(), "message": "没有运行中的 UC 注册任务"}
            self._state.stop_requested = True
            self._state.phase = "stopping"
            self._state.updated_at = now_iso()
            process = self._process
        self._stop_event.set()
        self._terminate_process(process)
        return {"ucSignupState": self.get_state()}

    def _run(self, emails: list[str], options: dict[str, Any]) -> None:
        task_label = "重新授权" if parse_bool_flag(options.get("authOnly"), default=False) else "注册"
        move_mail = parse_bool_flag(options.get("moveMail"), default=True)
        mail_provider = str(options.get("mailProvider") or "").strip()
        pending_group = str(
            options.get("mailPendingGroup")
            or options.get("pendingGroup")
            or CONFIG.mail_pending_group_name
            or "oai_pending"
        ).strip()
        success_group = str(
            options.get("mailSuccessGroup")
            or options.get("successGroup")
            or CONFIG.mail_success_group_name
            or "oai_success"
        ).strip()
        bad_group = str(
            options.get("mailBadGroup")
            or options.get("badGroup")
            or CONFIG.mail_bad_group_name
            or "badmail"
        ).strip()
        def move_account(email_value: str, target_group: str, label: str) -> None:
            if mail_provider:
                self._move_completed_mail_account(email_value, target_group, label, provider=mail_provider)
            else:
                self._move_completed_mail_account(email_value, target_group, label)
        self.append_log(
            f"UC 最终版{task_label}任务启动: {len(emails)} 个邮箱"
            f"（pending={pending_group} success={success_group} bad={bad_group}）"
        )
        for index, email in enumerate(emails):
            if self._stop_event.is_set():
                self.append_log("收到停止信号，结束 UC 注册任务", level="warn")
                break

            self._update_state(
                current_index=index,
                current_email=email,
                current_phone="",
                current_proxy="",
                current_step="starting",
                current_pid=None,
            )
            self.append_log("")
            self.append_log(f"===== UC 第 {index + 1}/{len(emails)} 个: {email} =====")
            if move_mail:
                # Claim only. Do NOT move into pending/Mail Admin groups before the
                # browser flow actually starts producing progress; early moves made
                # unfinished accounts look "written" in Mail Admin after UI rewires.
                mark_signup_email_claimed(email)
            started_at = now_iso()
            result, error, return_code = self._run_one(email, options)
            finished_at = now_iso()
            # Re-read after child exit: uc_signup may clear a false registered flag on auth failure.
            registered_email = str(email or "").strip().lower() in registered_signup_email_keys()

            with self._lock:
                self._state.completed += 1
                if result == "success":
                    self._state.success += 1
                elif result in {"fail", "retryable"}:
                    self._state.failed += 1
                self._state.results.append({
                    "email": email,
                    "status": result,
                    "error": error or "",
                    "returnCode": return_code,
                    "startedAt": started_at,
                    "finishedAt": finished_at,
                })
                self._state.results = self._state.results[-500:]
                self._state.current_pid = None
                self._state.updated_at = now_iso()

            if result == "success":
                self.append_log(f"UC 第 {index + 1}/{len(emails)} 个完成: {email}")
                if move_mail:
                    move_account(email, success_group, "成功")
                    self._advance_queue_cursor(index)
            elif result == "stopped":
                self.append_log(f"UC 第 {index + 1}/{len(emails)} 个已停止: {email}", level="warn")
                # Stop mid-flight: if registration never finished, put mail back to source pool.
                if move_mail and not registered_email:
                    source_group = str(
                        options.get("mailSourceGroup")
                        or options.get("sourceGroup")
                        or CONFIG.mail_source_group_name
                        or ""
                    ).strip()
                    if source_group:
                        move_account(email, source_group, "来源池(停止回退)")
                break
            elif result == "retryable":
                self.append_log(f"UC 第 {index + 1}/{len(emails)} 个未完成，可更换代理重试: {email} ({error or return_code})", level="warn")
                mark_signup_email_retryable_hold(email, error or return_code or "")
                if registered_email:
                    try:
                        opus_result = promote_registered_opus_email(email, error or return_code or "")
                        if opus_result.get("imported"):
                            self.append_log(f"Mail Admin 已收纳已注册待授权账号: {email}")
                        elif opus_result.get("configured"):
                            self.append_log(f"Mail Admin 已注册待授权账号写入未完成: {email}", level="warn")
                    except Exception as opus_error:
                        self.append_log(f"Mail Admin 已注册待授权账号写入失败: {email}: {opus_error}", level="warn")
                if move_mail:
                    if registered_email:
                        move_account(email, pending_group, "待授权")
                    else:
                        source_group = str(
                            options.get("mailSourceGroup")
                            or options.get("sourceGroup")
                            or CONFIG.mail_source_group_name
                            or ""
                        ).strip()
                        if source_group:
                            move_account(email, source_group, "来源池(可重试回退)")
                        else:
                            self.append_log(f"可重试失败且未完成注册，保留当前分组: {email}", level="warn")
                if error:
                    self._add_error(error)
            else:
                if registered_email:
                    self.append_log(
                        f"UC 第 {index + 1}/{len(emails)} 个停在待授权阶段: {email} ({error or return_code})",
                        level="warn",
                    )
                    try:
                        opus_result = promote_registered_opus_email(email, error or return_code or "")
                        if opus_result.get("imported"):
                            self.append_log(f"Mail Admin 已收纳已注册待授权账号: {email}")
                    except Exception as opus_error:
                        self.append_log(f"Mail Admin 已注册待授权账号写入失败: {email}: {opus_error}", level="warn")
                    if move_mail:
                        move_account(email, pending_group, "待授权")
                else:
                    self.append_log(f"UC 第 {index + 1}/{len(emails)} 个失败: {email} ({error or return_code})", level="error")
                    if move_mail:
                        move_account(email, bad_group, "失败")
                if error:
                    self._add_error(error)

        with self._lock:
            self._state.running = False
            self._state.phase = "stopped" if self._stop_event.is_set() else "done"
            self._state.current_step = ""
            self._state.current_email = ""
            self._state.current_phone = ""
            self._state.current_proxy = ""
            self._state.current_pid = None
            self._state.updated_at = now_iso()
        self.append_log(f"UC 任务结束: 成功 {self._state.success} / 失败 {self._state.failed}")

    def _run_one(self, email: str, options: dict[str, Any]) -> tuple[str, str | None, int | None]:
        proxy_reservation: dict[str, Any] = {}
        try:
            proxy_reservation = reserve_signup_proxy(email, options.get("proxy"))
        except Exception as error:
            return ("retryable", f"选择注册代理失败: {error}", None)

        command = [
            sys.executable,
            "-u",
            str(ROOT / "uc_signup.py"),
            "--api-base",
            str(options.get("apiBase") or f"http://127.0.0.1:{CONFIG.port}"),
            "--display",
            str(options.get("display") or CONFIG.browser_display),
        ]
        if email:
            command.extend(["--email", email])
        if parse_bool_flag(options.get("authOnly"), default=False):
            command.append("--auth-only")
        if not parse_bool_flag(options.get("getRefreshToken"), default=True):
            command.append("--skip-refresh-token")
        forced_phone = str(options.get("forcedPhone") or options.get("phone") or options.get("phoneNumber") or "").strip()
        if forced_phone:
            command.extend(["--forced-phone", forced_phone])
        proxy = str(proxy_reservation.get("proxyUrl") or options.get("proxy") or CONFIG.uc_signup_proxy or CONFIG.browser_proxy).strip()
        if not proxy:
            update_signup_proxy_usage(str(proxy_reservation.get("eventId") or ""), "fail", {"error": "missing proxy"})
            return ("retryable", "未找到注册代理，已阻止直连注册", None)
        command.extend(["--proxy", proxy])
        self._update_state(current_proxy=proxy)
        proxy_region = str(proxy_reservation.get("proxyRegion") or "")
        binding_note = "；邮箱已有绑定" if proxy_reservation.get("emailWasBound") else "；邮箱新建绑定"
        self.append_log(
            f"注册代理: {proxy_reservation.get('proxyName') or proxy} "
            f"({proxy_reservation.get('usageCount') or 1}/{proxy_reservation.get('usageLimit') or '-'})"
            f"{'；地区 ' + proxy_region if proxy_region else ''}{binding_note}"
        )
        # Do NOT write the mailbox into Opus/Mail Admin here.
        # Pending-only import was firing before signup finished and polluted Mail Admin
        # with incomplete accounts. Tokens are written only after Sub2API OAuth callback.
        chrome_binary = str(options.get("chromeBinary") or CONFIG.uc_signup_chrome_binary).strip()
        if chrome_binary:
            command.extend(["--chrome-binary", chrome_binary])
        chrome_version = str(options.get("chromeVersion") or CONFIG.uc_signup_chrome_version).strip()
        if chrome_version:
            command.extend(["--chrome-version", chrome_version])

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["UC_SIGNUP_API_BASE"] = str(options.get("apiBase") or f"http://127.0.0.1:{CONFIG.port}")
        env["UC_SIGNUP_DISPLAY"] = str(options.get("display") or CONFIG.browser_display)
        if CONFIG.admin_password:
            env["UC_SIGNUP_ADMIN_PASSWORD"] = CONFIG.admin_password
        if proxy:
            env["UC_SIGNUP_PROXY"] = proxy
        if proxy_region:
            env["UC_SIGNUP_PROXY_REGION"] = proxy_region
        if parse_bool_flag(options.get("authOnly"), default=False):
            env["UC_SIGNUP_AUTH_ONLY"] = "true"
        env["UC_SIGNUP_GET_REFRESH_TOKEN"] = (
            "true" if parse_bool_flag(options.get("getRefreshToken"), default=True) else "false"
        )
        if parse_bool_flag(options.get("manualMode"), default=False):
            env["UC_SIGNUP_MANUAL_MODE"] = "true"
        if forced_phone:
            env["UC_SIGNUP_FORCED_PHONE"] = forced_phone
        keep_browser_on_failure = first_non_empty(
            options.get("keepBrowserOnFailure"),
            options.get("keep_browser_on_failure"),
            CONFIG.uc_signup_keep_browser_on_failure,
        )
        keep_browser_seconds = first_non_empty(
            options.get("keepBrowserSeconds"),
            options.get("keep_browser_seconds"),
            CONFIG.uc_signup_keep_browser_seconds,
        )
        for env_key, value in (
            ("UC_SIGNUP_PHONE_RETRIES", CONFIG.uc_signup_phone_retries),
            ("UC_SIGNUP_SMS_TIMEOUT_SECONDS", CONFIG.uc_signup_sms_timeout_seconds),
            ("UC_SIGNUP_SMS_POLL_INTERVAL_SECONDS", CONFIG.uc_signup_sms_poll_interval_seconds),
            ("UC_SIGNUP_PHONE_PASSWORD_PAGE_TIMEOUT", CONFIG.uc_signup_phone_password_page_timeout),
            ("UC_SIGNUP_CF_CLEARANCE_ENABLED", CONFIG.uc_signup_cf_clearance_enabled),
            ("UC_SIGNUP_CF_CLEARANCE_API_URL", CONFIG.uc_signup_cf_clearance_api_url),
            ("UC_SIGNUP_CF_CLEARANCE_TARGET_URL", CONFIG.uc_signup_cf_clearance_target_url),
            ("UC_SIGNUP_CF_CLEARANCE_TIMEOUT_SECONDS", CONFIG.uc_signup_cf_clearance_timeout_seconds),
            ("UC_SIGNUP_CF_CLEARANCE_CACHE_SECONDS", CONFIG.uc_signup_cf_clearance_cache_seconds),
            ("UC_SIGNUP_KEEP_BROWSER_ON_FAILURE", keep_browser_on_failure),
            ("UC_SIGNUP_KEEP_BROWSER_SECONDS", keep_browser_seconds),
            ("UC_SIGNUP_PROFILE_BASE_DIR", CONFIG.uc_signup_profile_base_dir),
        ):
            if str(value).strip():
                env[env_key] = str(value).strip()
        for key, env_key in (
            ("password", "SIGNUP_PASSWORD"),
            ("name", "SIGNUP_NAME"),
            ("age", "SIGNUP_AGE"),
        ):
            fallback = {
                "password": CONFIG.signup_password,
                "name": CONFIG.signup_name,
                "age": CONFIG.signup_age,
            }.get(key, "")
            value = str(options.get(key) or fallback).strip()
            if value:
                env[env_key] = value

        try:
            process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=True,
            )
        except Exception as error:
            update_signup_proxy_usage(str(proxy_reservation.get("eventId") or ""), "fail", {"error": str(error)})
            return ("fail", f"启动 uc_signup.py 失败: {error}", None)

        with self._lock:
            self._process = process
            self._state.current_pid = process.pid
            self._state.updated_at = now_iso()
        update_signup_proxy_usage(str(proxy_reservation.get("eventId") or ""), "running", {"pid": process.pid})

        idle_timeout = self._signup_idle_timeout_seconds()
        last_output_ts = time.time()
        timed_out = False
        if process.stdout:
            while process.poll() is None:
                if self._stop_event.is_set():
                    break
                ready, _, _ = select.select([process.stdout], [], [], 1)
                if ready:
                    raw_line = process.stdout.readline()
                    if raw_line == "":
                        break
                    last_output_ts = time.time()
                    line = raw_line.rstrip("\r\n")
                    if line:
                        self._handle_process_line(line)
                if idle_timeout > 0 and time.time() - last_output_ts > idle_timeout:
                    timed_out = True
                    message = f"UC 子进程 {idle_timeout}s 无新日志，判定卡住并终止"
                    self.append_log(message, level="warn")
                    self._terminate_process(process)
                    break
            while process.poll() is None and self._stop_event.is_set():
                self._terminate_process(process)
                break
            for raw_line in process.stdout:
                line = raw_line.rstrip("\r\n")
                if line:
                    self._handle_process_line(line)

        return_code = process.wait()
        with self._lock:
            if self._process is process:
                self._process = None

        if self._stop_event.is_set():
            update_signup_proxy_usage(str(proxy_reservation.get("eventId") or ""), "stopped")
            return ("stopped", "已停止", return_code)
        if timed_out:
            update_signup_proxy_usage(
                str(proxy_reservation.get("eventId") or ""),
                "fail",
                {"returnCode": return_code, "retryable": True, "idleTimeoutSeconds": idle_timeout},
            )
            return ("retryable", f"UC 子进程 {idle_timeout}s 无新日志，已自动终止", return_code)
        if return_code == 0:
            update_signup_proxy_usage(str(proxy_reservation.get("eventId") or ""), "success")
            return ("success", None, return_code)
        if return_code == 2:
            update_signup_proxy_usage(str(proxy_reservation.get("eventId") or ""), "fail", {"returnCode": return_code, "retryable": True})
            return ("retryable", "代理或浏览器环境被拒，邮箱未标坏", return_code)
        if return_code == 1 and self._is_retryable_signup_failure(email):
            update_signup_proxy_usage(str(proxy_reservation.get("eventId") or ""), "fail", {"returnCode": return_code, "retryable": True})
            return ("retryable", "授权/验证码阶段未完成，邮箱未标坏", return_code)
        update_signup_proxy_usage(str(proxy_reservation.get("eventId") or ""), "fail", {"returnCode": return_code})
        return ("fail", f"uc_signup.py 退出码 {return_code}", return_code)

    def _recent_log_text(self, limit: int = 80) -> str:
        with self._lock:
            entries = self._log_buffer[-limit:]
        return "\n".join(str(entry.get("message") or "") for entry in entries)

    def _is_retryable_signup_failure(self, email: str) -> bool:
        text = self._recent_log_text().lower()
        retryable_markers = (
            "一次性登录邮箱验证码超时",
            "授权阶段邮箱验证码超时",
            "邮箱验证码超时",
            "流程超时",
            "页面无进展",
            "oauth回调超时",
            "代理或浏览器环境被拒",
            "unable to load site",
            "vpn",
            "cloudflare",
            "验证码",
            "待授权",
            "找不到账户",
            "选择账户",
            "授权页面临时错误",
            "failed to fetch",
            "不明なエラー",
        )
        if any(marker.lower() in text for marker in retryable_markers):
            return True
        return str(email or "").strip().lower() in registered_signup_email_keys()

    def _signup_idle_timeout_seconds(self) -> int:
        try:
            return max(0, int(str(CONFIG.uc_signup_idle_timeout_seconds or "600").strip()))
        except (TypeError, ValueError):
            return 600

    def _handle_process_line(self, line: str) -> None:
        level = "error" if any(token in line for token in ("❌", "💀")) else "warn" if "⚠" in line else "info"
        self.append_log(line, level=level)
        phone_match = re.search(r"📱\s*([+\d][^\s]*)", line)
        if phone_match:
            self._update_state(current_phone=phone_match.group(1))
        step = self._infer_step(line)
        if step:
            self._update_state(current_step=step)

    def _infer_step(self, line: str) -> str:
        # Order matters: more specific markers first. UI stage order is:
        # 1 准备 2 注册页 3 账号资料 4 电话验证 5 OAuth 邮箱/授权 6 Sub2API
        checks = [
            ("全部完成", "completed"),
            ("导入结果", "sub2api_import"),
            ("📥 导入", "sub2api_import"),
            ("导入...", "sub2api_import"),
            ("等待回调", "waiting_oauth_callback"),
            ("授权确认", "authorizing"),
            ("选择账户", "oauth"),
            ("授权页状态", "oauth"),
            ("授权页", "oauth"),
            ("授权会话", "oauth"),
            ("OAuth", "oauth"),
            ("短信验证码", "filling_sms_code"),
            ("SMS:", "filling_sms_code"),
            ("SMS ", "waiting_sms"),
            ("授权手机号", "filling_phone"),
            ("填手机号", "filling_phone"),
            ("展开手机表单", "filling_phone"),
            ("📱", "buying_phone"),
            ("邮箱注册资料", "filling_account_details"),
            ("已提交邮箱注册资料", "filling_account_details"),
            ("姓名年龄", "filling_account_details"),
            ("设置密码", "filling_password"),
            ("填密码", "filling_password"),
            ("手机号登录密码", "filling_password"),
            ("邮箱验证码", "filling_email_code"),
            ("邮箱码", "filling_email_code"),
            ("填入验证码", "filling_email_code"),
            ("绑定邮箱", "filling_email"),
            ("邮箱注册页", "opening_signup"),
            ("邮箱注册阶段完成", "signup_done"),
            ("邮箱注册阶段已有记录", "oauth"),
            ("邮箱注册", "filling_email"),
            ("填入:", "filling_email"),
            ("注册资料名", "preparing_email"),
            ("随机注册密码", "preparing_email"),
            ("已为该邮箱准备", "preparing_email"),
            ("Cookie", "accepting_cookie"),
            ("Fingerprint", "preparing_browser"),
            ("Browser runtime", "preparing_browser"),
            ("Chrome profile", "preparing_browser"),
            ("浏览器断点已恢复", "restoring_session"),
            ("注册代理", "preparing_proxy"),
        ]
        for needle, step in checks:
            if needle in line:
                return step
        return ""

    def _advance_queue_cursor(self, index: int) -> None:
        try:
            queue = load_email_queue()
            emails = queue.get("emails") or []
            if emails:
                save_email_queue({**queue, "cursor": min(index + 1, len(emails))})
        except Exception:
            pass

    def _move_completed_mail_account(self, email: str, target_group_name: str, label: str, *, provider: str = "") -> None:
        try:
            result = move_outlook_email_after_signup(email, target_group_name, provider=provider)
            if result.get("success"):
                if result.get("skipped") and "Mail Opus" in str(result.get("reason") or ""):
                    self.append_log(f"Mail Opus 状态走 Mail Admin 路径: {email} ({label})")
                else:
                    self.append_log(f"OutlookEmail 分组移动完成: {email} -> {target_group_name}")
            elif not result.get("skipped"):
                self.append_log(f"OutlookEmail 分组移动未完成: {email} ({result.get('reason') or label})", level="warn")
        except Exception as error:
            self.append_log(f"OutlookEmail 分组移动失败: {email} -> {target_group_name}: {error}", level="warn")

    def _terminate_process(self, process: subprocess.Popen | None) -> None:
        if not process or process.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except Exception:
            try:
                process.terminate()
            except Exception:
                return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except Exception:
                process.kill()
            process.wait(timeout=5)


UC_SIGNUP_MANAGER = UcSignupManager()


def sub2api_account_email(account: dict[str, Any]) -> str:
    credentials = account.get("credentials") if isinstance(account.get("credentials"), dict) else {}
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    candidates = [
        account.get("name"),
        credentials.get("email"),
        credentials.get("email_address"),
        extra.get("email"),
        extra.get("email_address"),
    ]
    for value in candidates:
        text = str(value or "").strip()
        if "@" in text:
            return text
    return ""


def sub2api_account_error_text(account: dict[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "error_message",
        "last_error",
        "last_error_message",
        "status_reason",
        "reason",
    ):
        value = account.get(key)
        if value:
            values.append(str(value))
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    for key in ("error", "error_message", "last_error", "last_refresh_error"):
        value = extra.get(key)
        if value:
            values.append(str(value))
    return " ".join(values).strip()


def sub2api_account_token_revoked(account: dict[str, Any]) -> bool:
    text = sub2api_account_error_text(account).lower()
    if not text:
        return False
    if "token revoked" in text or "invalidated oauth token" in text:
        return True
    if "oauth 401" in text or "401:" in text:
        return True
    if "401" in text and any(token in text for token in ("oauth", "token", "revoked", "invalidated", "unauthorized")):
        return True
    return False


def sub2api_account_ok_status(account: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if account.get("deleted_at"):
        reasons.append("deleted")
    if str(account.get("status") or "").lower() != "active":
        reasons.append(f"status:{account.get('status') or 'unknown'}")
    if account.get("schedulable") is False:
        reasons.append("paused")
    if timestamp_is_future(account.get("rate_limit_reset_at")):
        reasons.append("rate_limited")
    if timestamp_is_future(account.get("overload_until")):
        reasons.append("overload")
    if timestamp_is_future(account.get("temp_unschedulable_until")):
        reasons.append("temp_unschedulable")
    expires_at = account.get("expires_at")
    expires_ts = parse_timestamp(expires_at)
    if expires_at and expires_ts is not None and expires_ts <= time.time():
        reasons.append("expired")
    if sub2api_account_token_revoked(account):
        reasons.append("token_revoked")
    return not reasons, reasons


def sub2api_account_usage_state(account: dict[str, Any], *, now_ts: float, busy_window_seconds: int) -> dict[str, Any]:
    try:
        current_concurrency = int(account.get("current_concurrency") or 0)
    except (TypeError, ValueError):
        current_concurrency = 0
    try:
        concurrency = int(account.get("concurrency") or 0)
    except (TypeError, ValueError):
        concurrency = 0
    last_used_at = account.get("last_used_at")
    last_used_ts = parse_timestamp(last_used_at)
    used_recently = (
        bool(last_used_ts)
        and busy_window_seconds > 0
        and 0 <= now_ts - float(last_used_ts) <= busy_window_seconds
    )
    in_use = current_concurrency > 0 or used_recently
    return strip_empty_values(
        {
            "inUse": in_use,
            "usedRecently": used_recently,
            "currentConcurrency": current_concurrency,
            "concurrency": concurrency,
            "lastUsedAt": last_used_at,
        }
    )


def build_sub2api_group_health(
    group_name: str | None = None,
    min_ok_accounts: int | None = None,
    busy_min_ok_accounts: int | None = None,
    busy_window_seconds: int | None = None,
) -> dict[str, Any]:
    target_name = str(group_name or CONFIG.sub2api_monitor_group_name or "auto").strip() or "auto"
    base_min_ok = min_ok_accounts if min_ok_accounts is not None else parse_positive_int(CONFIG.sub2api_monitor_min_ok_accounts, default=1)
    busy_min_ok = (
        busy_min_ok_accounts
        if busy_min_ok_accounts is not None
        else parse_positive_int(CONFIG.sub2api_monitor_busy_min_ok_accounts, default=2)
    )
    busy_min_ok = max(base_min_ok, busy_min_ok)
    busy_window = (
        busy_window_seconds
        if busy_window_seconds is not None
        else parse_positive_int(CONFIG.sub2api_monitor_busy_window_seconds, default=300)
    )
    groups = SUB2API.list_groups()
    matches = [
        group
        for group in groups
        if str(group.get("name") or "").strip().lower() == target_name.lower()
        and not group.get("deleted_at")
    ]
    if not matches:
        return {
            "ok": False,
            "status": "missing_group",
            "groupName": target_name,
            "minOkAccounts": base_min_ok,
            "baseMinOkAccounts": base_min_ok,
            "busyMinOkAccounts": busy_min_ok,
            "busyWindowSeconds": busy_window,
            "usageActive": False,
            "okAccounts": 0,
            "totalAccounts": 0,
            "message": f"Sub2API 分组不存在: {target_name}",
            "checkedAt": now_iso(),
        }

    group = next((item for item in matches if str(item.get("platform") or "").lower() == "openai"), matches[0])
    try:
        group_id = int(group.get("id"))
    except (TypeError, ValueError):
        raise Sub2ApiError(f"Sub2API 分组 ID 异常: {target_name}")

    accounts = SUB2API.list_accounts()
    matched_accounts = [account for account in accounts if group_id in sub2api_account_group_ids(account)]
    summaries: list[dict[str, Any]] = []
    usage_accounts: list[dict[str, Any]] = []
    ok_count = 0
    now_ts = time.time()
    for account in matched_accounts:
        is_ok, reasons = sub2api_account_ok_status(account)
        usage = sub2api_account_usage_state(account, now_ts=now_ts, busy_window_seconds=busy_window)
        if usage.get("inUse"):
            usage_accounts.append(
                strip_empty_values(
                    {
                        "id": account.get("id"),
                        "name": account.get("name"),
                        "email": sub2api_account_email(account),
                        **usage,
                    }
                )
            )
        if is_ok:
            ok_count += 1
        summaries.append(
            strip_empty_values(
                {
                    "id": account.get("id"),
                    "name": account.get("name"),
                    "platform": account.get("platform"),
                    "type": account.get("type"),
                    "status": account.get("status"),
                    "schedulable": account.get("schedulable"),
                    "ok": is_ok,
                    "reasons": reasons,
                    "email": sub2api_account_email(account),
                    "errorMessage": sub2api_account_error_text(account),
                    "rateLimitResetAt": account.get("rate_limit_reset_at"),
                    "overloadUntil": account.get("overload_until"),
                    "tempUnschedulableUntil": account.get("temp_unschedulable_until"),
                    "expiresAt": account.get("expires_at"),
                    **usage,
                }
            )
        )

    usage_active = bool(usage_accounts)
    min_ok = busy_min_ok if usage_active else base_min_ok
    healthy = ok_count >= min_ok

    reason_counts: dict[str, int] = {}
    limited_count = 0
    overload_count = 0
    paused_count = 0
    expired_count = 0
    revoked_count = 0
    other_bad_count = 0
    for item in summaries:
        reasons = item.get("reasons") if isinstance(item.get("reasons"), list) else []
        if not reasons:
            continue
        for reason in reasons:
            key = str(reason or "unknown")
            reason_counts[key] = reason_counts.get(key, 0) + 1
        if "rate_limited" in reasons:
            limited_count += 1
        if "overload" in reasons or "temp_unschedulable" in reasons:
            overload_count += 1
        if "paused" in reasons or any(str(r).startswith("status:") for r in reasons):
            paused_count += 1
        if "expired" in reasons:
            expired_count += 1
        if "token_revoked" in reasons:
            revoked_count += 1
        if not any(r in reasons for r in ("rate_limited", "overload", "temp_unschedulable", "paused", "expired", "token_revoked")) and not any(str(r).startswith("status:") for r in reasons):
            other_bad_count += 1

    not_ok = max(0, len(matched_accounts) - ok_count)
    return {
        "ok": healthy,
        "status": "ok" if healthy else "depleted",
        "group": {
            "id": group.get("id"),
            "name": group.get("name"),
            "platform": group.get("platform"),
            "status": group.get("status"),
        },
        "groupName": target_name,
        "minOkAccounts": min_ok,
        "baseMinOkAccounts": base_min_ok,
        "busyMinOkAccounts": busy_min_ok,
        "busyWindowSeconds": busy_window,
        "usageActive": usage_active,
        "usageAccounts": usage_accounts[:20],
        "okAccounts": ok_count,
        "totalAccounts": len(matched_accounts),
        "notOkAccounts": not_ok,
        "limitedAccounts": limited_count,
        "rateLimitedAccounts": limited_count,
        "overloadAccounts": overload_count,
        "pausedAccounts": paused_count,
        "expiredAccounts": expired_count,
        "revokedAccounts": revoked_count,
        "otherBadAccounts": other_bad_count,
        "reasonCounts": reason_counts,
        "needAccounts": max(min_ok - ok_count, 0),
        "accounts": summaries[:50],
        "checkedAt": now_iso(),
    }


def load_sub2api_reauth_attempts() -> dict[str, dict[str, Any]]:
    data = load_json_file(SUB2API_REAUTH_ATTEMPTS_PATH)
    attempts = data.get("attempts") if isinstance(data, dict) else {}
    return {str(key): value for key, value in (attempts or {}).items() if isinstance(value, dict)}


def save_sub2api_reauth_attempts(attempts: dict[str, dict[str, Any]]) -> None:
    save_json_file(SUB2API_REAUTH_ATTEMPTS_PATH, {"attempts": attempts})


class Sub2ApiMonitor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_trigger_ts = 0.0
        self._reauth_attempts: dict[str, dict[str, Any]] = load_sub2api_reauth_attempts()
        self._state: dict[str, Any] = {
            "running": False,
            "lastCheckAt": "",
            "lastError": "",
            "lastTriggerAt": "",
            "lastTriggerResult": None,
            "groupHealth": None,
        }

    def _settings(self) -> dict[str, Any]:
        return {
            "enabled": bool(CONFIG.sub2api_monitor_enabled),
            "groupName": CONFIG.sub2api_monitor_group_name or "auto",
            "minOkAccounts": parse_positive_int(CONFIG.sub2api_monitor_min_ok_accounts, default=1),
            "busyMinOkAccounts": parse_positive_int(CONFIG.sub2api_monitor_busy_min_ok_accounts, default=2),
            "intervalSeconds": parse_positive_int(CONFIG.sub2api_monitor_interval_seconds, default=30),
            "busyWindowSeconds": parse_positive_int(CONFIG.sub2api_monitor_busy_window_seconds, default=300),
            "triggerCooldownSeconds": parse_positive_int(CONFIG.sub2api_monitor_trigger_cooldown_seconds, default=60),
            "maxStartAccounts": parse_positive_int(CONFIG.sub2api_monitor_max_start_accounts, default=2),
            "importMailSource": bool(CONFIG.sub2api_monitor_import_mail_source),
        }

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)
            reauth_attempts = dict(self._reauth_attempts)
        return {**state, "settings": self._settings(), "reauthAttempts": reauth_attempts}

    def _set_state(self, **kwargs: Any) -> None:
        with self._lock:
            self._state.update(kwargs)

    def check_once(self, *, trigger: bool = False) -> dict[str, Any]:
        settings = self._settings()
        if not settings["enabled"] and not trigger:
            state = {
                "status": "disabled",
                "ok": False,
                "message": "Sub2API 监控未启用",
                "checkedAt": now_iso(),
            }
            self._set_state(lastCheckAt=state["checkedAt"], lastError="", groupHealth=state)
            return self.status()

        try:
            health = build_sub2api_group_health(
                settings["groupName"],
                settings["minOkAccounts"],
                settings["busyMinOkAccounts"],
                settings["busyWindowSeconds"],
            )
            self._set_state(lastCheckAt=health["checkedAt"], lastError="", groupHealth=health)
        except Exception as error:
            blocked = "HTTP 423" in str(error) or "ADMIN_COMPLIANCE_ACK_REQUIRED" in str(error)
            health = {
                "ok": False,
                "status": "blocked" if blocked else "error",
                "groupName": settings["groupName"],
                "minOkAccounts": settings["minOkAccounts"],
                "baseMinOkAccounts": settings["minOkAccounts"],
                "busyMinOkAccounts": settings["busyMinOkAccounts"],
                "busyWindowSeconds": settings["busyWindowSeconds"],
                "usageActive": False,
                "okAccounts": 0,
                "totalAccounts": 0,
                "message": str(error),
                "checkedAt": now_iso(),
            }
            self._set_state(lastCheckAt=health["checkedAt"], lastError=str(error), groupHealth=health)
            return self.status()

        if trigger:
            trigger_result = self._maybe_trigger(health, settings)
            self._set_state(lastTriggerResult=trigger_result)
        return self.status()

    def _select_monitor_emails(self, limit: int, settings: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        queue_payload: dict[str, Any] = {}
        if settings.get("importMailSource"):
            queue_payload = import_outlook_source_group_to_email_queue()
            queue = queue_payload.get("emailQueue") if isinstance(queue_payload, dict) else {}
        else:
            queue = load_email_queue()
        emails = normalize_email_lines((queue or {}).get("emails", []))
        return emails[:limit], queue_payload

    def _token_revoked_accounts(self, health: dict[str, Any]) -> list[dict[str, Any]]:
        accounts = health.get("accounts") if isinstance(health, dict) else []
        if not isinstance(accounts, list):
            return []
        candidates: list[dict[str, Any]] = []
        for account in accounts:
            if not isinstance(account, dict):
                continue
            reasons = account.get("reasons") if isinstance(account.get("reasons"), list) else []
            if "token_revoked" not in reasons:
                continue
            email = str(account.get("email") or account.get("name") or "").strip()
            if "@" not in email:
                continue
            candidates.append(account)
        return candidates

    def _reauth_attempt_allowed(self, account_id: Any, now_ts: float) -> bool:
        key = str(account_id or "").strip()
        if not key:
            return False
        attempt = self._reauth_attempts.get(key) or {}
        try:
            count = int(attempt.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count >= 1:
            return False
        try:
            last_ts = float(attempt.get("lastTs") or 0)
        except (TypeError, ValueError):
            last_ts = 0.0
        reauth_cooldown = max(parse_positive_int(CONFIG.sub2api_monitor_trigger_cooldown_seconds, default=900), 300)
        return not last_ts or now_ts - last_ts >= reauth_cooldown

    def _mark_reauth_attempts(self, accounts: list[dict[str, Any]], now_ts: float, result: dict[str, Any]) -> None:
        with self._lock:
            for account in accounts:
                key = str(account.get("id") or "").strip()
                if not key:
                    continue
                previous = self._reauth_attempts.get(key) or {}
                try:
                    previous_count = int(previous.get("count") or 0)
                except (TypeError, ValueError):
                    previous_count = 0
                self._reauth_attempts[key] = {
                    "count": previous_count + 1,
                    "lastTs": now_ts,
                    "lastAt": now_iso(),
                    "email": account.get("email") or account.get("name") or "",
                    "result": result,
                }
            save_sub2api_reauth_attempts(self._reauth_attempts)

    def _reset_ok_reauth_attempts(self, health: dict[str, Any]) -> None:
        accounts = health.get("accounts") if isinstance(health, dict) else []
        if not isinstance(accounts, list):
            return
        ok_ids = {str(account.get("id")) for account in accounts if isinstance(account, dict) and account.get("ok")}
        if not ok_ids:
            return
        with self._lock:
            changed = False
            for account_id in list(self._reauth_attempts):
                if account_id in ok_ids:
                    self._reauth_attempts.pop(account_id, None)
                    changed = True
            if changed:
                save_sub2api_reauth_attempts(self._reauth_attempts)

    def _maybe_trigger_reauth(self, health: dict[str, Any], settings: dict[str, Any], now_ts: float) -> dict[str, Any] | None:
        candidates = self._token_revoked_accounts(health)
        if not candidates:
            return None
        limit = max(int(settings.get("maxStartAccounts") or 1), 1)
        selected = [account for account in candidates if self._reauth_attempt_allowed(account.get("id"), now_ts)][:limit]
        if not selected:
            return None
        emails = normalize_email_lines([str(account.get("email") or account.get("name") or "") for account in selected])
        if not emails:
            return None
        forced_phone = find_bound_phone_for_email(emails[0]) if len(emails) == 1 else ""

        result = UC_SIGNUP_MANAGER.start(
            emails,
            apiBase=f"http://127.0.0.1:{CONFIG.port}",
            display=CONFIG.browser_display,
            password=CONFIG.signup_password,
            name=CONFIG.signup_name,
            age=CONFIG.signup_age,
            authOnly=True,
            moveMail=False,
            forcedPhone=forced_phone,
            keepBrowserOnFailure="false",
            keepBrowserSeconds="0",
        )
        if "error" in result:
            return {"triggered": False, "reason": "reauth_start_failed", "error": result["error"], "checkedAt": now_iso()}

        self._last_trigger_ts = now_ts
        self._mark_reauth_attempts(selected, now_ts, {"started": True})
        triggered = {
            "triggered": True,
            "reason": "reauth_token_revoked",
            "emails": emails,
            "forcedPhone": forced_phone,
            "accountIds": [account.get("id") for account in selected],
            "checkedAt": now_iso(),
            "ucSignupState": result.get("ucSignupState"),
        }
        self._set_state(lastTriggerAt=triggered["checkedAt"])
        return triggered

    def _maybe_trigger(self, health: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        if health.get("status") in {"blocked", "error", "missing_group"}:
            return {"triggered": False, "reason": health.get("status"), "checkedAt": now_iso()}
        self._reset_ok_reauth_attempts(health)
        if health.get("ok"):
            return {"triggered": False, "reason": "enough_accounts", "checkedAt": now_iso()}
        signup_state = UC_SIGNUP_MANAGER.get_state()
        if signup_state.get("running"):
            return {"triggered": False, "reason": "signup_running", "checkedAt": now_iso()}

        now_ts = time.time()
        reauth_result = self._maybe_trigger_reauth(health, settings, now_ts)
        if reauth_result is not None:
            return reauth_result

        cooldown = int(settings.get("triggerCooldownSeconds") or 0)
        if cooldown > 0 and self._last_trigger_ts and now_ts - self._last_trigger_ts < cooldown:
            return {"triggered": False, "reason": "cooldown", "checkedAt": now_iso()}

        need = parse_positive_int(health.get("needAccounts"), default=settings["minOkAccounts"])
        limit = min(max(need, 1), max(int(settings.get("maxStartAccounts") or 1), 1))
        emails, queue_payload = self._select_monitor_emails(limit, settings)
        if not emails:
            return {"triggered": False, "reason": "no_mail_inventory", "checkedAt": now_iso(), "queue": queue_payload}

        result = UC_SIGNUP_MANAGER.start(
            emails,
            apiBase=f"http://127.0.0.1:{CONFIG.port}",
            display=CONFIG.browser_display,
            password=CONFIG.signup_password,
            name=CONFIG.signup_name,
            age=CONFIG.signup_age,
            keepBrowserOnFailure="false",
            keepBrowserSeconds="0",
        )
        if "error" in result:
            return {"triggered": False, "reason": "signup_start_failed", "error": result["error"], "checkedAt": now_iso()}

        self._last_trigger_ts = now_ts
        queue = load_email_queue()
        save_email_queue({**queue, "cursor": 0, "activeEmail": emails[0] if emails else ""})
        triggered = {
            "triggered": True,
            "reason": "below_threshold",
            "emails": emails,
            "needAccounts": need,
            "startedAccounts": len(emails),
            "checkedAt": now_iso(),
            "ucSignupState": result.get("ucSignupState"),
        }
        self._set_state(lastTriggerAt=triggered["checkedAt"])
        return triggered

    def _run(self) -> None:
        self._set_state(running=True)
        while not self._stop_event.is_set():
            settings = self._settings()
            wait_seconds = max(int(settings.get("intervalSeconds") or 300), 30)
            if settings.get("enabled"):
                self.check_once(trigger=True)
            if self._stop_event.wait(wait_seconds):
                break
        self._set_state(running=False)


SUB2API_MONITOR = Sub2ApiMonitor()


def _cpa_monitor_status_safe() -> dict:
    try:
        import extensions_api
        return extensions_api.CPA_MONITOR.status()
    except Exception as error:
        return {"running": False, "lastError": str(error)}



def is_early_cancel_denied_error(error: Exception | str) -> bool:
    text = str(error or "")
    return "EARLY_CANCEL_DENIED" in text and "minActivationTime" in text


def binding_latest_link(binding: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(binding, dict):
        return {}
    links = binding.get("links")
    if isinstance(links, list):
        for item in links:
            if isinstance(item, dict):
                return dict(item)
    return {}


def public_mail_inventory_status() -> dict[str, Any]:
    if not OUTLOOK_EMAIL.configured:
        return {"configured": False, "ok": False, "status": "not_configured"}
    try:
        inventory = build_outlook_email_inventory()
    except Exception:
        return {
            "configured": True,
            "ok": False,
            "status": "unavailable",
            "sourceGroupName": CONFIG.mail_source_group_name,
        }
    groups = []
    for group in inventory.get("groups", []):
        if not isinstance(group, dict):
            continue
        groups.append(
            strip_empty_values(
                {
                    "name": group.get("name"),
                    "total": group.get("total"),
                    "usable": group.get("usable"),
                    "failedRefresh": group.get("failedRefresh"),
                    "active": group.get("active"),
                }
            )
        )
    source_group = inventory.get("sourceGroup") if isinstance(inventory.get("sourceGroup"), dict) else {}
    return {
        "configured": True,
        "ok": True,
        "status": "ok",
        "total": inventory.get("total", 0),
        "usable": inventory.get("usable", 0),
        "sourceGroupName": source_group.get("name") or CONFIG.mail_source_group_name,
        "sourceTotal": source_group.get("total", 0),
        "sourceUsable": source_group.get("usable", 0),
        "sourceFailedRefresh": source_group.get("failedRefresh", 0),
        "successGroupName": CONFIG.mail_success_group_name,
        "badGroupName": CONFIG.mail_bad_group_name,
        "groups": groups[:30],
        "updatedAt": inventory.get("updatedAt") or now_iso(),
    }


def public_monitor_status() -> dict[str, Any]:
    monitor = SUB2API_MONITOR.status()
    settings = monitor.get("settings") if isinstance(monitor.get("settings"), dict) else {}
    health = monitor.get("groupHealth") if isinstance(monitor.get("groupHealth"), dict) else {}
    return strip_empty_values(
        {
            "enabled": bool(settings.get("enabled")),
            "running": bool(monitor.get("running")),
            "status": health.get("status") or ("enabled" if settings.get("enabled") else "disabled"),
            "ok": health.get("ok"),
            "groupName": health.get("groupName") or settings.get("groupName"),
            "okAccounts": health.get("okAccounts"),
            "totalAccounts": health.get("totalAccounts"),
            "notOkAccounts": health.get("notOkAccounts"),
            "limitedAccounts": health.get("limitedAccounts") or health.get("rateLimitedAccounts"),
            "rateLimitedAccounts": health.get("rateLimitedAccounts") or health.get("limitedAccounts"),
            "overloadAccounts": health.get("overloadAccounts"),
            "pausedAccounts": health.get("pausedAccounts"),
            "expiredAccounts": health.get("expiredAccounts"),
            "revokedAccounts": health.get("revokedAccounts"),
            "reasonCounts": health.get("reasonCounts"),
            "needAccounts": health.get("needAccounts"),
            "minOkAccounts": health.get("minOkAccounts") or settings.get("minOkAccounts"),
            "busyMinOkAccounts": settings.get("busyMinOkAccounts"),
            "usageActive": health.get("usageActive"),
            "intervalSeconds": settings.get("intervalSeconds"),
            "lastCheckAt": monitor.get("lastCheckAt"),
            "lastTriggerAt": monitor.get("lastTriggerAt"),
            "lastError": monitor.get("lastError"),
            "checkedAt": health.get("checkedAt"),
            "message": health.get("message"),
        }
    )


def public_task_status() -> dict[str, Any]:
    manager = globals().get("UC_SIGNUP_MANAGER")
    state = manager.get_state() if manager else {}
    return {
        "running": bool(state.get("running")),
        "phase": state.get("phase") or "idle",
        "total": state.get("total", 0),
        "completed": state.get("completed", 0),
        "success": state.get("success", 0),
        "failed": state.get("failed", 0),
        "currentStep": state.get("currentStep") or "",
        "startedAt": state.get("startedAt") or "",
        "updatedAt": state.get("updatedAt") or "",
    }


def public_sub2api_account_status() -> dict[str, Any]:
    """Live Sub2API pool snapshot for public status (not only monitor cache)."""
    settings = {
        "enabled": bool(CONFIG.sub2api_monitor_enabled),
        "groupName": str(CONFIG.sub2api_monitor_group_name or "auto").strip() or "auto",
        "minOkAccounts": parse_positive_int(CONFIG.sub2api_monitor_min_ok_accounts, default=1),
        "busyMinOkAccounts": parse_positive_int(CONFIG.sub2api_monitor_busy_min_ok_accounts, default=2),
        "busyWindowSeconds": parse_positive_int(CONFIG.sub2api_monitor_busy_window_seconds, default=300),
        "intervalSeconds": parse_positive_int(CONFIG.sub2api_monitor_interval_seconds, default=30),
    }
    if not SUB2API.configured:
        return {
            "ok": False,
            "status": "misconfigured",
            "enabled": settings["enabled"],
            "groupName": settings["groupName"],
            "okAccounts": 0,
            "totalAccounts": 0,
            "minOkAccounts": settings["minOkAccounts"],
            "message": "Sub2API 未配置",
            "checkedAt": now_iso(),
        }
    try:
        health = build_sub2api_group_health(
            settings["groupName"],
            settings["minOkAccounts"],
            settings["busyMinOkAccounts"],
            settings["busyWindowSeconds"],
        )
    except Exception as error:
        return {
            "ok": False,
            "status": "error",
            "enabled": settings["enabled"],
            "groupName": settings["groupName"],
            "okAccounts": 0,
            "totalAccounts": 0,
            "minOkAccounts": settings["minOkAccounts"],
            "busyMinOkAccounts": settings["busyMinOkAccounts"],
            "message": str(error)[:240],
            "checkedAt": now_iso(),
        }

    ok_accounts = int(health.get("okAccounts") or 0)
    total_accounts = int(health.get("totalAccounts") or 0)
    limited_accounts = int(health.get("limitedAccounts") or health.get("rateLimitedAccounts") or 0)
    not_ok = int(health.get("notOkAccounts") if health.get("notOkAccounts") is not None else max(0, total_accounts - ok_accounts))
    message = str(health.get("message") or "").strip()
    if not message:
        parts = [f"可用 {ok_accounts}/{total_accounts}"]
        if limited_accounts:
            parts.append(f"限流 {limited_accounts}")
        if health.get("overloadAccounts"):
            parts.append(f"过载 {health.get('overloadAccounts')}")
        if health.get("pausedAccounts"):
            parts.append(f"暂停/异常 {health.get('pausedAccounts')}")
        if health.get("revokedAccounts"):
            parts.append(f"吊销 {health.get('revokedAccounts')}")
        if health.get("expiredAccounts"):
            parts.append(f"过期 {health.get('expiredAccounts')}")
        message = "，".join(parts)

    return strip_empty_values(
        {
            "ok": bool(health.get("ok")),
            "status": health.get("status") or "unknown",
            "enabled": settings["enabled"],
            "groupName": health.get("groupName") or settings["groupName"],
            "group": health.get("group"),
            "okAccounts": ok_accounts,
            "totalAccounts": total_accounts,
            "notOkAccounts": not_ok,
            "limitedAccounts": limited_accounts,
            "rateLimitedAccounts": limited_accounts,
            "overloadAccounts": health.get("overloadAccounts", 0),
            "pausedAccounts": health.get("pausedAccounts", 0),
            "expiredAccounts": health.get("expiredAccounts", 0),
            "revokedAccounts": health.get("revokedAccounts", 0),
            "otherBadAccounts": health.get("otherBadAccounts", 0),
            "reasonCounts": health.get("reasonCounts") or {},
            "needAccounts": health.get("needAccounts"),
            "minOkAccounts": health.get("minOkAccounts") or settings["minOkAccounts"],
            "baseMinOkAccounts": health.get("baseMinOkAccounts") or settings["minOkAccounts"],
            "busyMinOkAccounts": health.get("busyMinOkAccounts") or settings["busyMinOkAccounts"],
            "usageActive": health.get("usageActive"),
            "intervalSeconds": settings["intervalSeconds"],
            "message": message,
            "checkedAt": health.get("checkedAt") or now_iso(),
        }
    )


def public_first_token_latency() -> dict[str, Any]:
    """Public-safe recent TTFT aggregate; never expose individual usage rows."""
    if not SUB2API.configured:
        return {
            "ok": False,
            "averageMs": None,
            "averageSeconds": None,
            "sampleCount": 0,
            "windowSize": 10,
            "error": "Sub2API 未配置",
        }
    try:
        return SUB2API.recent_first_token_latency(sample_limit=10)
    except Exception as error:
        return {
            "ok": False,
            "averageMs": None,
            "averageSeconds": None,
            "sampleCount": 0,
            "windowSize": 10,
            "error": str(error)[:200],
        }


def public_cpa_pool_status() -> dict[str, Any]:
    """Expose CPA/xAI auth pool health for public dashboards."""
    try:
        import extensions_api

        monitor = extensions_api.CPA_MONITOR
        settings = monitor._settings() if hasattr(monitor, "_settings") else {}
        cached = monitor.status() if hasattr(monitor, "status") else {}
        health = cached.get("health") if isinstance(cached.get("health"), dict) else {}
        # Prefer fresh probe so public page is accurate even if monitor loop is quiet.
        try:
            health = monitor.fetch_health()
        except Exception:
            if not health:
                raise
        inspection: dict[str, Any] = {}
        inspection_error = ""
        try:
            inspection = _grok2_cpa_inspection_status()
        except Exception as error:
            inspection_error = str(error)[:240]
        inspection_summary = inspection.get("summary") if isinstance(inspection.get("summary"), dict) else {}
        inspection_ok = bool(inspection.get("ok") and inspection_summary)
        ok_accounts = int(health.get("okAccounts") or 0)
        total_accounts = int(health.get("totalAccounts") or 0)
        limited_accounts = (
            int(inspection_summary.get("quota_exhausted") or 0)
            if inspection_ok
            else int(health.get("limitedAccounts") or 0)
        )
        abnormal_accounts = int(inspection_summary.get("abnormal") or 0) if inspection_ok else 0
        disabled_accounts = int(health.get("disabledAccounts") or 0)
        unavailable_accounts = max(0, total_accounts - ok_accounts)
        message = str(health.get("message") or "").strip()
        if not message:
            message = f"可用账号 {ok_accounts}/{total_accounts}"
        # Always surface rate-limit / disabled counts in public message.
        extras = []
        if limited_accounts and "限流" not in message and "spending" not in message.lower():
            extras.append(f"限流/额度 {limited_accounts}")
        if disabled_accounts and "禁用" not in message:
            extras.append(f"禁用 {disabled_accounts}")
        if abnormal_accounts and "异常" not in message:
            extras.append(f"异常账号 {abnormal_accounts}")
        if extras:
            message = message + "，" + "，".join(extras)

        adaptive = cached.get("adaptive") if isinstance(cached.get("adaptive"), dict) else {}
        base_poll = int(settings.get("intervalSeconds") or adaptive.get("baseIntervalSeconds") or 60)
        min_poll = int(settings.get("minIntervalSeconds") or adaptive.get("minIntervalSeconds") or 10)
        max_poll = int(settings.get("maxIntervalSeconds") or adaptive.get("maxIntervalSeconds") or 300)
        enough = health.get("enough")
        if enough is False or limited_accounts > 0 and ok_accounts < int(health.get("minOkAccounts") or settings.get("minOkAccounts") or 5):
            recommended = min_poll
        else:
            recommended = int(adaptive.get("recommendedPollSeconds") or base_poll)
        recommended = max(min_poll, min(max_poll, int(recommended)))

        def public_inspection_items(key: str) -> list[dict[str, Any]]:
            rows = inspection.get(key) if isinstance(inspection.get(key), list) else []
            return [
                strip_empty_values(
                    {
                        "email": row.get("email"),
                        "fileName": row.get("file_name"),
                        "classification": row.get("classification"),
                        "httpStatus": row.get("http_status"),
                        "reason": row.get("reason"),
                        "disabledByInspection": row.get("disabled_by_inspection"),
                        "manualPreserved": row.get("manual_preserved"),
                        "lastSeenAt": row.get("last_seen_at"),
                    }
                )
                for row in rows
                if isinstance(row, dict)
            ]

        inspection_runtime = inspection.get("runtime") if isinstance(inspection.get("runtime"), dict) else {}
        inspection_config = inspection.get("config") if isinstance(inspection.get("config"), dict) else {}
        inspection_public = strip_empty_values(
            {
                "ok": inspection_ok,
                "error": inspection_error,
                "checkedAt": inspection_runtime.get("last_processed_finished_at"),
                "healthyAccounts": int(inspection_summary.get("healthy") or 0),
                "quotaExhaustedAccounts": limited_accounts,
                "limitedAccounts": limited_accounts,
                "abnormalAccounts": abnormal_accounts,
                "reasonCounts": inspection_summary.get("reason_counts") or {},
                "trackedAccounts": int(inspection_summary.get("tracked") or 0),
                "autoDisabledAccounts": int(inspection_summary.get("owned_disabled") or 0),
                "manualPreservedAccounts": int(inspection_summary.get("manual_preserved") or 0),
                "limitedAccountItems": public_inspection_items("limited_accounts"),
                "abnormalAccountItems": public_inspection_items("abnormal_accounts"),
                "schedule": {
                    "idleIntervalSeconds": inspection_config.get("interval_sec"),
                    "activeIntervalSeconds": inspection_config.get("active_interval_sec"),
                    "activeWindowSeconds": inspection_config.get("active_window_sec"),
                    "activeUntil": inspection_runtime.get("active_until"),
                    "effectiveIntervalSeconds": inspection_runtime.get("effective_active_interval_sec"),
                    "workers": inspection_config.get("workers"),
                    "firstRecoveryDelaySeconds": inspection_config.get("restore_after_sec"),
                    "quotaRetrySeconds": inspection_config.get("recovery_retry_sec"),
                    "errorRetrySeconds": inspection_config.get("error_retry_sec"),
                    "healthyStreakRequired": inspection_config.get("healthy_streak_required"),
                    "lastRunType": inspection_runtime.get("last_start_reason"),
                },
            }
        )

        return strip_empty_values(
            {
                "ok": bool(health.get("ok")),
                "status": health.get("status") or ("enabled" if settings.get("enabled") else "disabled"),
                "managementOk": health.get("managementOk"),
                "managementStatus": health.get("managementStatus"),
                "requestStatus": health.get("requestStatus"),
                "requestAvailable": health.get("requestAvailable"),
                "requestMessage": health.get("requestMessage"),
                "lastFailureAt": health.get("lastFailureAt"),
                "lastFailureAgeSeconds": health.get("lastFailureAgeSeconds"),
                "recent429": health.get("recent429"),
                "lastRateLimitAt": health.get("lastRateLimitAt"),
                "recentPoolUnavailable": health.get("recentPoolUnavailable"),
                "lastPoolUnavailableAt": health.get("lastPoolUnavailableAt"),
                "lastRequestStatusCode": health.get("lastRequestStatusCode"),
                "runtimeLogAvailable": health.get("runtimeLogAvailable"),
                "enabled": bool(settings.get("enabled")),
                "running": bool(cached.get("running")),
                "okAccounts": ok_accounts,
                "totalAccounts": total_accounts,
                "limitedAccounts": limited_accounts,
                "rateLimitedAccounts": limited_accounts,
                "quotaExhaustedAccounts": limited_accounts,
                "abnormalAccounts": abnormal_accounts,
                # The public Lab card labels these two fields as
                # "异常 / 冷却". Keep those fields aligned with the inspection
                # classifications instead of the legacy CPA cooldown cache.
                "coolingAccounts": limited_accounts,
                "recoveredFromCooldown": int(health.get("recoveredFromCooldown") or 0),
                "disabledAccounts": disabled_accounts,
                "recentFailureAccounts": health.get("recentFailureAccounts"),
                "recentSuccessAccounts": health.get("recentSuccessAccounts"),
                "recentFailedRequests": health.get("recentFailedRequests"),
                "recentSuccessRequests": health.get("recentSuccessRequests"),
                "cumulativeFailedRequests": health.get("cumulativeFailedRequests"),
                "cumulativeSuccessRequests": health.get("cumulativeSuccessRequests"),
                "notOkAccounts": abnormal_accounts,
                "unavailableAccounts": unavailable_accounts,
                "minOkAccounts": health.get("minOkAccounts") or settings.get("minOkAccounts"),
                "enough": health.get("enough"),
                "message": message,
                "ttkRunning": bool(cached.get("ttkRunning")),
                "pollIntervalSeconds": recommended,
                "recommendedPollSeconds": recommended,
                "monitorIntervalSeconds": int(adaptive.get("currentIntervalSeconds") or base_poll),
                "adaptiveMode": adaptive.get("mode") or "base",
                "adaptive": {
                    "mode": adaptive.get("mode") or "base",
                    "recommendedPollSeconds": recommended,
                    "currentIntervalSeconds": int(adaptive.get("currentIntervalSeconds") or base_poll),
                    "baseIntervalSeconds": base_poll,
                    "minIntervalSeconds": min_poll,
                    "maxIntervalSeconds": max_poll,
                },
                "routingStrategy": "fill-first",
                "lastCheckAt": cached.get("lastCheckAt") or health.get("checkedAt"),
                "lastTriggerAt": cached.get("lastTriggerAt"),
                "lastError": cached.get("lastError") or "",
                "inspection": inspection_public,
                "checkedAt": health.get("checkedAt") or now_iso(),
            }
        )
    except Exception as error:
        return {
            "ok": False,
            "status": "unavailable",
            "okAccounts": 0,
            "totalAccounts": 0,
            "message": str(error)[:240],
            "checkedAt": now_iso(),
        }

def _grok2_admin_opener() -> tuple[Any, str]:
    secret_path = Path("/run/secrets/grok_register_lite_env")
    password = ""
    if secret_path.is_file():
        for raw_line in secret_path.read_text(encoding="utf-8").splitlines():
            if raw_line.startswith("GROK_REGISTER_ADMIN_BOOTSTRAP_PASSWORD="):
                password = raw_line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not password:
        raise RuntimeError("Grok 2 管理密码未挂载")
    base = "http://127.0.0.1:8788/grok2"
    jar = CookieJar()
    opener = build_opener(HTTPCookieProcessor(jar))
    login = Request(
        f"{base}/api/auth/login",
        data=json.dumps({"password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(login, timeout=8) as response:
        json.loads(response.read().decode("utf-8"))
    return opener, base


def _grok2_cpa_inspection_status() -> dict[str, Any]:
    opener, base = _grok2_admin_opener()
    with opener.open(Request(f"{base}/api/cpa/inspection-automation"), timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Grok 2 巡检状态格式无效")
    return payload


def _mask_public_email(value: Any) -> str:
    email = str(value or "").strip()
    if "@" not in email:
        return ""
    local, domain = email.rsplit("@", 1)
    shown = local[:3] if len(local) > 3 else local[:1]
    return f"{shown}***@{domain}"


def _public_registration_message(value: Any, status: Any = "") -> str:
    """Translate and redact a native Grok 2 event for the public Lab page."""
    message = str(value or "").strip()
    message = re.sub(r"(?i)adapter_build=[^;\s]+;?\s*", "", message)
    message = re.sub(r"(?i)sso_prefix=(?:'[^']*'|\"[^\"]*\"|[^;\s]+);?\s*", "", message)
    message = re.sub(r"(?i)\b(user_code|verification_code|email_code)\s*[:=]\s*[^,;\s]+", r"\1=***", message)
    message = re.sub(r"\beyJ[A-Za-z0-9_-]{20,}(?:\.[A-Za-z0-9_-]+){0,2}\b", "[凭证已隐藏]", message)
    message = re.sub(r"^\[!\]\s*", "", message)
    message = re.sub(r"^failed:\s*", "", message, flags=re.I)

    low = message.lower()
    if "wrong_version_number" in low or "tls connect error" in low or "curl: (35)" in low or "curl (35)" in low:
        message = "注册代理 TLS 握手失败（代理协议或出口连接异常）"
    elif "connection closed abruptly" in low or "connection reset" in low:
        message = "注册代理连接被远端中断"
    elif "rate_limited" in low or "slow_down" in low or re.search(r"\bhttp\s*429\b", low):
        message = "xAI Device Flow 触发限流，系统已退避重试"
    elif "invalid_grant" in low:
        message = "xAI 拒绝 Device Flow 凭证（invalid_grant）"
    elif "sso obtained but sso_to_auth_json conversion failed" in low:
        message = "CPA 凭证转换失败"
    elif "token poll" in low and ("timeout" in low or "超时" in message):
        message = "Device Flow 授权轮询超时"
    elif "timed out" in low or "timeout" in low:
        message = "访问 xAI 超时"
    elif "failed to perform" in low and "curl" in low:
        message = "注册代理网络连接失败"

    event_status = str(status or "").lower()
    if event_status in {"error", "failed", "probe_failed", "protocol_error", "protocol_blocked"}:
        message = re.sub(r"^(?:失败|错误)[：:]\s*", "", message)
        return f"[失败] {message or '任务失败'}"
    return message


def _grok2_registration_logs(tail: int = 300, *, public: bool = True) -> dict[str, Any]:
    opener, base = _grok2_admin_opener()
    with opener.open(Request(f"{base}/api/accounts/register-email/sessions"), timeout=8) as response:
        listing = json.loads(response.read().decode("utf-8"))
    batches = listing.get("batches") if isinstance(listing, dict) else []
    sessions = listing.get("sessions") if isinstance(listing, dict) else []

    terminal_statuses = {
        "imported",
        "done",
        "completed",
        "success",
        "partial",
        "error",
        "failed",
        "probe_failed",
        "expired",
        "protocol_error",
        "protocol_blocked",
        "cancelled",
        "stopped",
    }

    def task_sort_key(item: dict[str, Any]) -> float:
        for key in ("updated_at", "created_at", "started_at", "finished_at"):
            value = item.get(key)
            if value in (None, ""):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                try:
                    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
                except (TypeError, ValueError):
                    continue
        return 0.0

    def task_id_of(item: dict[str, Any]) -> str:
        return str(item.get("id") or item.get("batch_id") or "")

    def task_is_running(item: dict[str, Any]) -> bool:
        status = str(item.get("status") or item.get("batch_status") or "").lower()
        try:
            running_count = int(item.get("running") or 0)
        except (TypeError, ValueError):
            running_count = 0
        return running_count > 0 or (bool(status) and status not in terminal_statuses)

    # A batch is one registration round. Do not also count its child sessions
    # as independent rounds; only genuinely standalone sessions are candidates.
    batch_items = [item for item in (batches or []) if isinstance(item, dict)]
    batch_ids = {task_id_of(item) for item in batch_items if task_id_of(item)}
    standalone_sessions = [
        item
        for item in (sessions or [])
        if isinstance(item, dict) and str(item.get("batch_id") or "") not in batch_ids
    ]
    candidates: list[tuple[dict[str, Any], str]] = [
        *((item, "batches") for item in batch_items),
        *((item, "sessions") for item in standalone_sessions),
    ]
    candidates.sort(
        key=lambda candidate: (task_sort_key(candidate[0]), candidate[1] == "batches"),
        reverse=True,
    )

    # Public history is round-aware:
    # - while a round is live, keep that round and the previous round;
    # - while idle, keep the latest completed round;
    # - older rounds are visible only while their latest activity is <= 30 min.
    # This intentionally keeps the immediately previous round even after a long
    # idle period, without leaking an indefinitely growing history.
    live = [candidate for candidate in candidates if task_is_running(candidate[0])]
    primary = max(
        live or candidates,
        key=lambda candidate: (task_sort_key(candidate[0]), candidate[1] == "batches"),
        default=({}, "sessions"),
    )
    primary_id = task_id_of(primary[0])
    always_keep = 2 if live else 1
    ordered: list[tuple[dict[str, Any], str]] = []
    if primary_id:
        ordered.append(primary)
    ordered.extend(
        candidate
        for candidate in candidates
        if task_id_of(candidate[0]) and task_id_of(candidate[0]) != primary_id
    )
    cutoff = time.time() - 1800.0
    visible_candidates = [
        candidate
        for index, candidate in enumerate(ordered)
        if index < always_keep or task_sort_key(candidate[0]) >= cutoff
    ]

    detailed_rounds: list[tuple[dict[str, Any], str, str]] = []
    for summary, task_type in visible_candidates:
        round_id = task_id_of(summary)
        detail = summary
        try:
            with opener.open(
                Request(f"{base}/api/accounts/register-email/{task_type}/{round_id}"), timeout=8
            ) as response:
                loaded = json.loads(response.read().decode("utf-8"))
                if isinstance(loaded, dict):
                    detail = loaded
        except Exception:
            # Keep the listing snapshot if a just-finished in-memory detail
            # disappears between the list and detail requests.
            pass
        detailed_rounds.append((detail, task_type, round_id))

    task = detailed_rounds[0][0] if detailed_rounds else primary[0]
    task_id = task_id_of(task) or primary_id
    events: list[dict[str, Any]] = []
    visible_rounds: list[dict[str, Any]] = []
    for round_index, (round_task, task_type, round_id) in enumerate(detailed_rounds):
        round_updated_at = task_sort_key(round_task)
        round_role = (
            "current"
            if round_index == 0 and task_is_running(round_task)
            else "previous"
            if round_index < always_keep
            else "recent"
        )
        visible_rounds.append({
            "taskId": round_id,
            "type": "batch" if task_type == "batches" else "session",
            "role": round_role,
            "running": task_is_running(round_task),
            "phase": str(round_task.get("status") or round_task.get("batch_status") or "idle").lower(),
            "updatedAt": (
                datetime.fromtimestamp(round_updated_at).astimezone().isoformat()
                if round_updated_at
                else ""
            ),
            "updatedAtEpoch": round_updated_at,
        })
        for session in (round_task.get("sessions") or [round_task]):
            email = str(session.get("email") or "")
            session_events = session.get("events") or []
            if session_events:
                for event in session_events:
                    if isinstance(event, dict):
                        events.append({
                            **event,
                            "email": email,
                            "taskId": round_id,
                            "roundIndex": round_index,
                            "roundRole": round_role,
                        })
            elif session.get("message"):
                events.append({
                    "at": session.get("updated_at") or time.time(),
                    "status": session.get("status") or "running",
                    "message": session.get("message"),
                    "email": email,
                    "taskId": round_id,
                    "roundIndex": round_index,
                    "roundRole": round_role,
                })
    events.sort(key=lambda item: float(item.get("at") or 0))
    if public:
        noisy_fragments = (
            "waiting poll slice",
            "still processing (",
            "gettaskresult for",
        )
        events = [
            event
            for event in events
            if not any(fragment in str(event.get("message") or "").lower() for fragment in noisy_fragments)
        ]
    logs: list[dict[str, Any]] = []
    for event in events[-max(1, min(int(tail or 300), 1000)):]:
        stamp = datetime.fromtimestamp(float(event.get("at") or time.time())).strftime("%H:%M:%S")
        email = _mask_public_email(event.get("email")) if public else str(event.get("email") or "")
        who = f" [{email}]" if email else ""
        status_value = str(event.get("status") or "running").lower()
        message = (
            _public_registration_message(event.get("message") or event.get("error"), status_value)
            if public
            else str(event.get("message") or event.get("error") or status_value)
        )
        level = "error" if status_value in {"error", "failed", "probe_failed", "protocol_error", "protocol_blocked"} else "info"
        logs.append({
            "time": "",
            "message": f"[{stamp}]{who} {message}",
            "level": level,
            "eventAt": event.get("at"),
            "status": status_value,
            "taskId": event.get("taskId") or "",
            "roundIndex": event.get("roundIndex"),
            "roundRole": event.get("roundRole") or "",
        })
    status = str(task.get("status") or task.get("batch_status") or "idle").lower()
    running = bool(task_id) and task_is_running(task)
    last_event_at = max((float(event.get("at") or 0) for event in events), default=0.0)
    if not last_event_at:
        last_event_at = task_sort_key(task)
    updated_at = datetime.fromtimestamp(last_event_at).astimezone().isoformat() if last_event_at else ""
    return {
        "ok": True,
        "channel": "grok2",
        "logs": logs,
        "count": len(logs),
        "tail": max(1, min(int(tail or 300), 1000)),
        "retentionSeconds": 1800,
        "retentionMode": "round-aware",
        "visibleRoundCount": len(visible_rounds),
        "rounds": visible_rounds,
        "running": running,
        "phase": status,
        "success": task.get("ok_count") or task.get("imported") or 0,
        "failed": task.get("fail_count") or task.get("error") or 0,
        "completed": task.get("done") or 0,
        "total": task.get("total") or task.get("count") or 0,
        "taskId": task_id or "",
        "ttkState": {
            "running": running,
            "phase": status,
            "success": task.get("ok_count") or task.get("imported") or 0,
            "failed": task.get("fail_count") or task.get("error") or 0,
            "completed": task.get("done") or 0,
            "total": task.get("total") or task.get("count") or 0,
            "taskId": task_id or "",
            "updatedAt": updated_at,
        },
        "updatedAt": updated_at,
    }


def _public_registration_logs(tail: int = 300) -> dict[str, Any]:
    channel = str(app_config_value("GROK_REGISTRATION_CHANNEL", "grok2") or "grok2").lower()
    if channel == "grok2":
        return _grok2_registration_logs(tail=tail, public=True)
    import extensions_api
    return extensions_api.public_ttk_logs(tail=tail)


def _public_ttk_summary() -> dict[str, Any]:
    try:
        if str(app_config_value("GROK_REGISTRATION_CHANNEL", "grok2") or "grok2").lower() == "grok2":
            data = _grok2_registration_logs(tail=30, public=True)
            return {
                **data,
                "recentLogs": data.get("logs") or [],
                "logsUrl": "/api/public/ttk/logs",
                "retentionSeconds": 1800,
            }
        import extensions_api
        state = extensions_api.GROK_TTK_MANAGER.get_state()
        logs = extensions_api.GROK_TTK_MANAGER.get_logs(tail=30)
        return {
            "running": bool(state.get("running")),
            "phase": state.get("phase") or "idle",
            "success": state.get("success") or 0,
            "failed": state.get("failed") or 0,
            "completed": state.get("completed") or 0,
            "total": state.get("total") or 0,
            "updatedAt": state.get("updated_at") or state.get("updatedAt") or "",
            "recentLogs": logs,
            "logsUrl": "/api/public/ttk/logs",
            "retentionSeconds": getattr(extensions_api, "GROK_TTK_LOG_RETENTION_SECONDS", 1800),
        }
    except Exception as error:
        return {"ok": False, "error": str(error)[:200]}




def apple_mail_status_payload(tail: int = 200) -> dict:
    """Live status for Apple Mail controlled runs."""
    status_path = Path('/opt/automyai/data/apple_mail/status.json')
    live_log = Path('/opt/automyai/data/apple_mail/runs/live.log')
    data = {
        'ok': True,
        'running': False,
        'currentStep': 'idle',
        'currentStepLabel': '空闲',
        'logs': [],
        'updatedAt': '',
    }
    try:
        if status_path.exists():
            loaded = json.loads(status_path.read_text(encoding='utf-8'))
            if isinstance(loaded, dict):
                data.update(loaded)
    except Exception as error:
        data['ok'] = False
        data['error'] = str(error)
    logs = data.get('logs') if isinstance(data.get('logs'), list) else []
    n = max(1, min(int(tail or 200), 1000))
    data['logs'] = logs[-n:]
    data['logFile'] = str(live_log)
    data['statusFile'] = str(status_path)
    return data

def public_status_payload() -> dict[str, Any]:
    sub2 = public_sub2api_account_status()
    first_token_latency = public_first_token_latency()
    sub2 = {
        **sub2,
        "firstTokenLatency": first_token_latency,
        "recent10FirstTokenAverageMs": first_token_latency.get("averageMs"),
    }
    # Keep legacy `monitor` field compatible with older consumers, but enrich with live counts.
    monitor = public_monitor_status()
    monitor["firstTokenLatency"] = first_token_latency
    monitor["recent10FirstTokenAverageMs"] = first_token_latency.get("averageMs")
    for key in (
        "ok",
        "status",
        "groupName",
        "okAccounts",
        "totalAccounts",
        "notOkAccounts",
        "limitedAccounts",
        "rateLimitedAccounts",
        "overloadAccounts",
        "pausedAccounts",
        "expiredAccounts",
        "revokedAccounts",
        "reasonCounts",
        "needAccounts",
        "minOkAccounts",
        "busyMinOkAccounts",
        "usageActive",
        "checkedAt",
        "message",
    ):
        if monitor.get(key) in (None, "", [], {}) and sub2.get(key) not in (None, "", [], {}):
            monitor[key] = sub2[key]
    # If monitor is disabled, still expose live account snapshot under monitor.*
    if monitor.get("status") == "disabled" and sub2.get("okAccounts") is not None:
        for key in (
            "okAccounts",
            "totalAccounts",
            "notOkAccounts",
            "limitedAccounts",
            "rateLimitedAccounts",
            "overloadAccounts",
            "pausedAccounts",
            "expiredAccounts",
            "revokedAccounts",
            "reasonCounts",
            "needAccounts",
            "ok",
            "message",
            "checkedAt",
        ):
            if sub2.get(key) not in (None, "", [], {}):
                monitor[key] = sub2.get(key)
        if sub2.get("status") and sub2.get("status") != "disabled":
            monitor["poolStatus"] = sub2.get("status")

    cpa = public_cpa_pool_status()
    task = public_task_status()
    mail = public_mail_inventory_status()
    # OAI / Sub2API: legacy consumers read `monitor`; also expose explicit aliases.
    oai = {
        **sub2,
        "registerTask": task,
        "mail": {
            "total": mail.get("total"),
            "usable": mail.get("usable"),
            "sourceGroupName": mail.get("sourceGroupName"),
            "sourceTotal": mail.get("sourceTotal"),
            "sourceUsable": mail.get("sourceUsable"),
            "successGroupName": mail.get("successGroupName"),
            "badGroupName": mail.get("badGroupName"),
        },
        "platform": "openai",
        "kind": "oai",
    }
    return {
        "ok": True,
        "service": APP_NAME,
        "public": True,
        "updatedAt": now_iso(),
        "mail": mail,
        # OpenAI / Sub2API pool (legacy + explicit names)
        "monitor": monitor,
        "sub2api": sub2,
        "oai": oai,
        "openai": oai,
        # Latest 10 valid Sub2API first-token latency samples (aggregate only).
        "firstTokenLatency": first_token_latency,
        "recent10FirstTokenAverageMs": first_token_latency.get("averageMs"),
        # CPA / Grok pool
        "cpa": cpa,
        "cpaPool": cpa,
        "phonePool": public_phone_pool_status(),
        "task": task,
        "ttk": _public_ttk_summary(),
    }


class AutomyaiHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


@functools.lru_cache(maxsize=64)
def _read_static_asset(path: str, modified_ns: int, size: int) -> bytes:
    del modified_ns, size
    return Path(path).read_bytes()


class AppHandler(BaseHTTPRequestHandler):
    server_version = "automyai/1.0"

    def is_authenticated(self) -> bool:
        if not CONFIG.admin_password:
            return True
        header_password = self.headers.get("X-Admin-Password", "")
        if header_password and hmac.compare_digest(header_password, CONFIG.admin_password):
            return True
        cookie_header = self.headers.get("Cookie", "")
        cookies: dict[str, str] = {}
        for part in cookie_header.split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()
        return hmac.compare_digest(cookies.get(ADMIN_COOKIE_NAME, ""), make_admin_session_token())

    def require_authenticated(self) -> bool:
        if self.is_authenticated():
            return True
        self.send_json(401, {"error": "需要管理员密码", "authenticated": False})
        return False

    def request_is_https(self) -> bool:
        proto = str(self.headers.get("X-Forwarded-Proto") or "").lower()
        forwarded_ssl = str(self.headers.get("X-Forwarded-Ssl") or "").lower()
        return proto == "https" or forwarded_ssl == "on"

    def admin_cookie_header(self, value: str, *, clear: bool = False) -> str:
        parts = [f"{ADMIN_COOKIE_NAME}={value}", "Path=/", "HttpOnly", "SameSite=Strict"]
        if clear:
            parts.append("Max-Age=0")
        if self.request_is_https():
            parts.append("Secure")
        return "; ".join(parts)

    def public_status_token_from_request(self, query: dict[str, str]) -> str:
        auth_header = str(self.headers.get("Authorization") or "").strip()
        if auth_header.lower().startswith("bearer "):
            return auth_header.split(" ", 1)[1].strip()
        return str(
            first_non_empty(
                query.get("token"),
                query.get("publicStatusToken"),
                self.headers.get("X-Public-Status-Token"),
            )
            or ""
        ).strip()

    def require_public_status_allowed(self, query: dict[str, str]) -> bool:
        if not CONFIG.public_status_enabled:
            self.send_json(404, {"error": "接口不存在"})
            return False
        expected = str(CONFIG.public_status_token or "").strip()
        if not expected:
            return True
        provided = self.public_status_token_from_request(query)
        if provided and hmac.compare_digest(provided, expected):
            return True
        self.send_json(401, {"error": "公开状态接口 token 错误"})
        return False

    def handle_auth_api(self, method: str, path: str) -> bool:
        if method == "GET" and path == "/api/auth/status":
            self.send_json(
                200,
                {
                    "authRequired": bool(CONFIG.admin_password),
                    "authenticated": self.is_authenticated(),
                },
            )
            return True
        if method == "POST" and path == "/api/auth/login":
            client_ip = request_client_ip(self.headers, self.client_address[0] if self.client_address else "")
            limit = login_rate_limit_status(client_ip)
            if limit.get("limited"):
                self.send_json(
                    429,
                    {
                        "error": "登录失败次数过多，请稍后再试",
                        "retryAfterSeconds": limit.get("retryAfterSeconds", 0),
                    },
                )
                return True
            body = self.read_json_body()
            password = str(body.get("password") or "")
            if not CONFIG.admin_password or hmac.compare_digest(password, CONFIG.admin_password):
                clear_failed_logins(client_ip)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                if CONFIG.admin_password:
                    self.send_header(
                        "Set-Cookie",
                        self.admin_cookie_header(make_admin_session_token()),
                    )
                self.end_headers()
                self.wfile.write(json.dumps({"authenticated": True}, ensure_ascii=False).encode("utf-8"))
                return True
            record_failed_login(client_ip)
            self.send_json(401, {"error": "管理员密码错误", "authenticated": False})
            return True
        if method == "POST" and path == "/api/auth/logout":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", self.admin_cookie_header("", clear=True))
            self.end_headers()
            self.wfile.write(json.dumps({"authenticated": False}, ensure_ascii=False).encode("utf-8"))
            return True
        return False

    def end_headers(self) -> None:
        path = urlparse(self.path).path
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'; object-src 'none'")
        if path.startswith("/api") or path.startswith("/card-payment-api"):
            self.send_header("Cache-Control", "no-store")
        if CONFIG.enable_cors:
            origin = str(self.headers.get("Origin") or "").strip()
            configured = CONFIG.public_status_allow_origins if is_public_status_path(path) else CONFIG.cors_allowed_origins
            allowed_origin = allowed_cors_origin(origin, configured)
            if allowed_origin:
                self.send_header("Access-Control-Allow-Origin", allowed_origin)
                if allowed_origin != "*":
                    self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Content-Type, Authorization, X-Public-Status-Token, X-Admin-Password",
                )
                if not is_public_status_path(path) and allowed_origin != "*":
                    self.send_header("Access-Control-Allow-Credentials", "true")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:
        self.handle_request("GET")

    def do_POST(self) -> None:
        self.handle_request("POST")

    def do_PATCH(self) -> None:
        self.handle_request("PATCH")

    def do_DELETE(self) -> None:
        self.handle_request("DELETE")

    def handle_request(self, method: str) -> None:
        parsed = urlparse(self.path)
        try:
            if method == "GET" and parsed.path == "/ui":
                location = "/ui/"
                if parsed.query:
                    location = f"{location}?{parsed.query}"
                self.send_response(302)
                self.send_header("Location", location)
                self.end_headers()
                return
            if method == "GET" and parsed.path == "/panel":
                self.send_response(302)
                self.send_header("Location", "/ui/legacy/control-panel.html")
                self.end_headers()
                return
            if method == "GET" and parsed.path in {"/payments", "/payments/extract", "/payments/center"}:
                location = f"/ui{parsed.path}"
                if parsed.query:
                    location = f"{location}?{parsed.query}"
                self.send_response(302)
                self.send_header("Location", location)
                self.end_headers()
                return
            if method == "GET" and parsed.path.startswith("/ui/"):
                is_public = (
                    parsed.path in {"/ui/", "/ui/login", "/ui/auth/login.html"}
                    or parsed.path.startswith("/ui/assets/")
                    or parsed.path in {
                        "/ui/css/nature.css",
                        "/ui/css/auth.css",
                        "/ui/js/runtime-config.js",
                        "/ui/js/api-client.js",
                    }
                )
                if not self.is_authenticated() and not is_public:
                    next_path = parsed.path
                    if parsed.query:
                        next_path = f"{next_path}?{parsed.query}"
                    self.send_response(302)
                    self.send_header("Location", f"/ui/login?next={quote(next_path, safe='')}")
                    self.end_headers()
                    return
                if self.serve_web_ui(parsed.path):
                    return
                self.send_json(404, {"error": "前端资源不存在", "path": parsed.path})
                return
            if parsed.path == "/card-payment-api":
                self.send_response(308)
                self.send_header("Location", "/card-payment-api/")
                self.end_headers()
                return
            if parsed.path.startswith("/card-payment-api/"):
                if not self.require_authenticated():
                    return
                self.handle_card_payment_proxy(method, parsed)
                return
            if parsed.path == "/paypal-protocol/api":
                location = "/paypal-protocol/api/"
                if parsed.query:
                    location = f"{location}?{parsed.query}"
                self.send_response(308)
                self.send_header("Location", location)
                self.end_headers()
                return
            if parsed.path.startswith("/paypal-protocol/api/"):
                if not self.require_authenticated():
                    return
                self.handle_paypal_protocol_proxy(method, parsed)
                return
            if parsed.path.startswith("/api"):
                self.handle_api(method, parsed)
                return
            if parsed.path == "/":
                self.send_json(
                    200,
                    {
                        "name": APP_NAME,
                        "apiBase": "/api",
                        "health": "/api/health",
                        "purchase": "/api/purchase",
                    },
                )
                return
            self.send_json(404, {"error": "接口不存在"})
        except (BrokenPipeError, ConnectionResetError):
            return
        except PurchaseError as error:
            print(f"[API ERROR] {method} {parsed.path}: {type(error).__name__}: {error}", flush=True)
            self.send_json(
                500,
                {
                    "error": str(error),
                    "type": type(error).__name__,
                    "path": parsed.path,
                    "attempts": error.attempts,
                },
            )
        except HeroSmsError as error:
            print(f"[API ERROR] {method} {parsed.path}: {type(error).__name__}: {error}", flush=True)
            self.send_json(500, {"error": str(error), "type": type(error).__name__, "path": parsed.path})

        except OutlookEmailError as error:
            print(f"[API ERROR] {method} {parsed.path}: {type(error).__name__}: {error}", flush=True)
            self.send_json(502, {"error": str(error), "type": type(error).__name__, "path": parsed.path})
        except Sub2ApiError as error:
            print(f"[API ERROR] {method} {parsed.path}: {type(error).__name__}: {error}", flush=True)
            self.send_json(502, {"error": str(error), "type": type(error).__name__, "path": parsed.path})
        except Exception as error:
            print(f"[API ERROR] {method} {parsed.path}: {type(error).__name__}: {error}", flush=True)
            self.send_json(500, {"error": str(error), "type": type(error).__name__, "path": parsed.path})

    def handle_card_payment_proxy(self, method: str, parsed: Any) -> None:
        """Proxy the native payment-center API when the UI is served without Nginx."""
        suffix = parsed.path.removeprefix("/card-payment-api/")
        target_path = "/api/" + suffix
        if parsed.query:
            target_path += "?" + parsed.query
        port = int(os.getenv("CARD_PAYMENT_PORT", "18797"))
        target = f"http://127.0.0.1:{port}{target_path}"
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length > 0 else None
        headers = {
            "Accept": str(self.headers.get("Accept") or "application/json"),
            "User-Agent": str(self.headers.get("User-Agent") or "automyai-main-proxy/1.0"),
        }
        for name in ("Content-Type", "Cookie", "X-Admin-Password"):
            value = str(self.headers.get(name) or "").strip()
            if value:
                headers[name] = value
        request_value = Request(target, data=body, headers=headers, method=method)
        try:
            response = urlopen(request_value, timeout=180)
            status = int(response.status)
            payload = response.read()
            content_type = str(response.headers.get("Content-Type") or "application/json; charset=utf-8")
            response_headers = response.headers
        except HTTPError as error:
            status = int(error.code)
            payload = error.read()
            content_type = str(error.headers.get("Content-Type") or "application/json; charset=utf-8")
            response_headers = error.headers
        except (URLError, TimeoutError, OSError) as error:
            self.send_json(502, {"ok": False, "error": "CARD_PAYMENT_PORTAL_UNAVAILABLE", "detail": str(error)[:240]})
            return
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for name in ("Set-Cookie", "Location"):
            value = response_headers.get(name)
            if value:
                self.send_header(name, str(value).replace("\r", " ").replace("\n", " "))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def handle_paypal_protocol_proxy(self, method: str, parsed: Any) -> None:
        """Proxy the PP protocol API when the UI is served without Nginx."""
        suffix = parsed.path.removeprefix("/paypal-protocol/api/")
        target_path = "/api/" + suffix
        if parsed.query:
            target_path += "?" + parsed.query
        port = int(os.getenv("PAYPAL_PROTOCOL_PORT", "18795"))
        target = f"http://127.0.0.1:{port}{target_path}"
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length > 0 else None
        headers = {
            "Accept": str(self.headers.get("Accept") or "application/json"),
            "User-Agent": str(self.headers.get("User-Agent") or "automyai-main-proxy/1.0"),
        }
        for name in ("Content-Type", "Cookie", "X-Admin-Password"):
            value = str(self.headers.get(name) or "").strip()
            if value:
                headers[name] = value
        request_value = Request(target, data=body, headers=headers, method=method)
        try:
            response = urlopen(request_value, timeout=180)
            status = int(response.status)
            payload = response.read()
            content_type = str(response.headers.get("Content-Type") or "application/json; charset=utf-8")
            response_headers = response.headers
        except HTTPError as error:
            status = int(error.code)
            payload = error.read()
            content_type = str(error.headers.get("Content-Type") or "application/json; charset=utf-8")
            response_headers = error.headers
        except (URLError, TimeoutError, OSError) as error:
            self.send_json(502, {"ok": False, "error": "PAYPAL_PROTOCOL_UNAVAILABLE", "detail": str(error)[:240]})
            return
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for name in ("Set-Cookie", "Location"):
            value = response_headers.get(name)
            if value:
                self.send_header(name, str(value).replace("\r", " ").replace("\n", " "))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def serve_web_ui(self, path: str) -> bool:
        """Serve the standalone frontend under /ui. Returns True if handled."""
        from pathlib import Path
        import mimetypes

        rel = path[3:] if path.startswith("/ui") else path
        rel = rel.lstrip("/")
        if not rel or rel in {"panel"}:
            rel = "index.html"
        base = FRONTEND_DIR
        try:
            custom = str(app_config_value("UI_STATIC_DIR", "./frontend") or "./frontend")
            base = (ROOT / custom).resolve() if not Path(custom).is_absolute() else Path(custom)
        except Exception:
            base = FRONTEND_DIR

        dist_dir = base / "dist"
        if dist_dir.is_dir():
            dist_file = (dist_dir / rel).resolve()
            base_file = (base / rel).resolve()
            if dist_file.exists() and dist_file.is_file():
                target = dist_file
            elif base_file.exists() and base_file.is_file():
                target = base_file
            else:
                target = (dist_dir / "index.html").resolve()
        else:
            target = (base / rel).resolve()

        try:
            target.relative_to(base.resolve())
        except Exception:
            return False
        if target.is_dir():
            target = target / "index.html"
        if not target.exists() or not target.is_file():
            if (dist_dir / "index.html").exists():
                target = (dist_dir / "index.html").resolve()
            else:
                return False
        stat = target.stat()
        content = _read_static_asset(str(target), stat.st_mtime_ns, stat.st_size)
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif target.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif target.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        if "/assets/" in target.as_posix():
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        elif target.suffix == ".html":
            self.send_header("Cache-Control", "no-cache")
        else:
            self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(content)
        return True

    API_ROUTE_GROUPS = (
        "handle_grok_log_api",
        "handle_system_api",
        "handle_address_profiles_api",
        "handle_file_library_api",
        "handle_extract_api",
        "handle_browser_live_api",
        "handle_mail_queue_api",
        "handle_temp_mail_api",
        "handle_sub2api_api",
        "handle_purchase_api",
        "handle_activation_api",
        "handle_phone_api",
        "handle_uc_signup_api",
        "handle_apple_mail_api",
        "handle_mail_admin_api",
        "handle_outlook_register_api",
    )

    def handle_api(self, method: str, parsed: Any) -> None:
        """Dispatch one JSON API request across the route groups below."""
        path = parsed.path
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}

        if self.handle_auth_api(method, path):
            return
        if self.handle_public_status_api(method, path, query):
            return
        if not self.require_authenticated():
            return
        for group_name in self.API_ROUTE_GROUPS:
            if getattr(self, group_name)(method, path, query):
                return
        if self.handle_extension_dispatch(method, parsed):
            return
        self.send_json(404, {"error": "接口不存在"})

    def handle_address_profiles_api(self, method: str, path: str, query: dict[str, str]) -> bool:
        """Fetch address fixtures and forward third-party fields without filtering."""
        if method == "GET" and path == "/api/address-profiles/us-tax-free":
            client_ip = request_client_ip(self.headers, self.client_address[0] if self.client_address else "")
            try:
                result = fetch_us_tax_free_address(query.get("state", ""), client_key=client_ip)
            except AddressProfileError as error:
                self.send_json(error.status_code, {"ok": False, "error": str(error)})
                return True
            self.send_json(200, {"ok": True, "item": result})
            return True
        if method == "GET" and path == "/api/address-profiles/countries":
            self.send_json(
                200,
                {
                    "items": address_country_catalog(),
                    "random": {"code": "RANDOM", "label": "随机国家"},
                    "sources": ["meiguodizhi.com", "cn.americaaddress.com"],
                },
            )
            return True
        if path == "/api/address-profiles/random":
            if method != "POST":
                self.send_json(405, {"error": "仅支持 POST /api/address-profiles/random"})
                return True
            body = self.read_json_body()
            if not isinstance(body, dict):
                self.send_json(400, {"error": "请求体必须是 JSON 对象"})
                return True
            client_ip = request_client_ip(self.headers, self.client_address[0] if self.client_address else "")
            try:
                result = fetch_address_profile(
                    body.get("country", "RANDOM"),
                    body.get("city", ""),
                    client_key=client_ip,
                )
            except AddressProfileError as error:
                self.send_json(error.status_code, {"ok": False, "error": str(error)})
                return True
            self.send_json(200, {"ok": True, "profile": result})
            return True
        return False

    def handle_file_library_api(self, method: str, path: str, query: dict[str, str]) -> bool:
        """Manage small UTF-8 text assets persisted under data/file_library."""
        if method in {"GET", "POST"} and path == "/api/file-library":
            if method == "GET":
                items = list_library_files()
                self.send_json(200, {"items": items, "total": len(items), "maxFileBytes": 1024 * 1024})
                return True
            try:
                request_length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                request_length = 0
            if request_length > FILE_LIBRARY_MAX_REQUEST_BYTES:
                self.send_json(413, {"error": "文本文件不能超过 1 MiB"})
                return True
            try:
                body = self.read_json_body()
                item = create_library_file(body.get("name"), body.get("content"))
                self.send_json(201, {"item": item})
            except FileLibraryError as error:
                self.send_json(error.status_code, {"error": str(error)})
            return True

        if method in {"GET", "POST", "DELETE"} and path.startswith("/api/file-library/"):
            item_id = unquote(path.removeprefix("/api/file-library/"))
            try:
                if method == "GET":
                    self.send_json(200, {"item": get_library_file(item_id)})
                elif method == "DELETE":
                    self.send_json(200, {"deleted": True, "item": delete_library_file(item_id)})
                else:
                    request_length = int(self.headers.get("Content-Length", "0") or 0)
                    if request_length > FILE_LIBRARY_MAX_REQUEST_BYTES:
                        self.send_json(413, {"error": "文本文件不能超过 1 MiB"})
                        return True
                    body = self.read_json_body()
                    if "name" not in body and "content" not in body:
                        raise FileLibraryError("至少需要提供文件名或内容")
                    item = update_library_file(
                        item_id,
                        name=body.get("name") if "name" in body else None,
                        content=body.get("content") if "content" in body else None,
                    )
                    self.send_json(200, {"item": item})
            except (FileLibraryError, ValueError) as error:
                status_code = error.status_code if isinstance(error, FileLibraryError) else 400
                self.send_json(status_code, {"error": str(error)})
            return True
        return False

    def handle_public_status_api(self, method: str, path: str, query: dict[str, str]) -> bool:
        """Unauthenticated public status surface. Gated by PUBLIC_STATUS_* settings."""
        if method == "GET" and is_public_status_path(path):
            if not self.require_public_status_allowed(query):
                return True
            # Single public status surface. /cpa and /cpa-pool are compatibility aliases.
            if path in {"/api/public/cpa", "/api/public/cpa-pool"}:
                self.send_json(200, public_status_payload())
                return True
            if path in {"/api/public/ttk/logs", "/api/public/logs"}:
                try:
                    tail = query.get("tail") or query.get("limit") or "300"
                    self.send_json(200, _public_registration_logs(tail=int(tail)))
                except Exception as error:
                    self.send_json(500, {"ok": False, "error": str(error)})
                return True
            if path in {"/api/public/cpa/wake", "/api/public/wake"}:
                # GET wake unsupported; tell clients to POST
                self.send_json(405, {"error": "Use POST /api/public/cpa/wake", "ok": False})
                return True
            self.send_json(200, public_status_payload())
            return True

        # Public interactive wake (no admin login). Internally rate-limited to >= 60s,
        # and shortens recommended poll down to 10s when pool is low.
        if method == "POST" and path in {"/api/public/cpa/wake", "/api/public/wake", "/api/public/cpa-pool/wake"}:
            if not self.require_public_status_allowed(query):
                return True
            try:
                import extensions_api
                body = self.read_json_body() if (self.headers.get("Content-Length") not in (None, "0")) else {}
                force = False
                if isinstance(body, dict):
                    force = parse_bool_flag(body.get("force"), default=False)
                wake = extensions_api.CPA_MONITOR.public_wake(force=force)
                cpa = public_cpa_pool_status()
                # Prefer wake-computed poll interval for clients.
                poll = wake.get("pollIntervalSeconds") or cpa.get("pollIntervalSeconds") or 60
                cpa = {**cpa, "pollIntervalSeconds": poll, "recommendedPollSeconds": poll}
                self.send_json(
                    200,
                    {
                        "ok": True,
                        "service": APP_NAME,
                        "public": True,
                        "updatedAt": now_iso(),
                        "accepted": wake.get("accepted"),
                        "reason": wake.get("reason"),
                        "retryAfterSeconds": wake.get("retryAfterSeconds"),
                        "pollIntervalSeconds": poll,
                        "recommendedPollSeconds": poll,
                        "triggered": wake.get("triggered"),
                        "lastTriggerResult": wake.get("lastTriggerResult"),
                        "cpa": cpa,
                        "cpaPool": cpa,
                        "routingStrategy": cpa.get("routingStrategy") or "fill-first",
                        "wake": wake,
                    },
                )
            except Exception as error:
                self.send_json(500, {"ok": False, "error": str(error)})
            return True
        return False

    def handle_extract_api(self, method: str, path: str, query: dict[str, str]) -> bool:
        """Forward authenticated extraction requests to the loopback Go service."""
        from integrations.extract_go_client import forward_request, handles_path

        if not handles_path(path):
            return False
        if method not in {"GET", "POST", "DELETE"}:
            self.send_json(405, {"ok": False, "error": "请求方法不允许"})
            return True
        try:
            body = self.read_json_body() if method == "POST" else {}
            status, payload = forward_request(method, path, query=query, body=body)
            self.send_json(status, payload)
        except Exception as error:
            self.send_json(502, {"ok": False, "error": str(error)})
        return True

    def handle_grok_log_api(self, method: str, path: str, query: dict[str, str]) -> bool:
        """Grok registration logs. The rest of the Grok surface lives in extensions_api."""
        if method == "GET" and path == "/api/grok/registration/logs":
            try:
                tail = query.get("tail") or query.get("limit") or "400"
                self.send_json(200, _grok2_registration_logs(tail=int(tail), public=False))
            except Exception as error:
                self.send_json(500, {"ok": False, "error": str(error)})
            return True
        return False

    def handle_system_api(self, method: str, path: str, query: dict[str, str]) -> bool:
        """Service health, runtime settings and the API index."""
        if method == "GET" and path == "/api/settings":
            self.send_json(200, get_ui_settings())
            return True

        if method == "POST" and path == "/api/settings":
            try:
                self.send_json(200, update_ui_settings(self.read_json_body()))
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
            return True

        if method == "POST" and path == "/api/proxy/check":
            body = self.read_json_body()
            proxy_url = first_non_empty(
                body.get("proxyUrl"),
                body.get("proxy"),
                body.get("url"),
                body.get("proxies"),
                body.get("pool"),
            )
            try:
                limit_raw = first_non_empty(body.get("limit"), body.get("max"), 8)
                try:
                    limit = int(limit_raw)
                except (TypeError, ValueError):
                    limit = 8
                payload = probe_proxy_pool(proxy_url, limit=limit)
                primary = payload.get("result") if payload.get("ok") else None
                if primary is None and payload.get("checked") == 1 and not payload.get("ok"):
                    detail = ""
                    if payload.get("results"):
                        detail = str(payload["results"][0].get("error") or "")
                    self.send_json(400, {"ok": False, "error": detail or "代理检测失败", **payload})
                    return True
                self.send_json(200, {"ok": bool(payload.get("ok")), "result": primary, **payload})
            except ValueError as error:
                self.send_json(400, {"ok": False, "error": str(error)})
            return True

        if method == "GET" and path == "/api/traffic":
            try:
                tail = int(query.get("tail") or query.get("limit") or "50")
            except (TypeError, ValueError):
                tail = 50
            service = str(query.get("service") or "").strip()
            items: list[dict[str, Any]] = []
            current = None
            try:
                import sys as _sys
                from pathlib import Path as _Path
                for _root in (_Path("/opt/automyai/tools"), _Path("/app/tools"), ROOT / "tools"):
                    if _root.is_dir() and str(_root) not in _sys.path:
                        _sys.path.insert(0, str(_root))
                    parent = _root.parent
                    if parent.is_dir() and str(parent) not in _sys.path:
                        _sys.path.insert(0, str(parent))
                load_fn = None
                try:
                    from traffic_meter import load_sessions as load_fn  # type: ignore
                except Exception:
                    try:
                        from tools.traffic_meter import load_sessions as load_fn  # type: ignore
                    except Exception:
                        import extensions_api as _ext
                        load_fn = getattr(_ext, "load_sessions", None)
                if load_fn is None:
                    raise RuntimeError("traffic_meter module missing")
                items = load_fn(service=service, tail=max(1, min(tail, 200)))
            except Exception as error:
                self.send_json(500, {"ok": False, "error": f"traffic history: {error}"})
                return True
            try:
                import extensions_api as _ext
                st = _ext.GROK_TTK_MANAGER.get_state() if hasattr(_ext, "GROK_TTK_MANAGER") else {}
                if isinstance(st, dict) and st.get("traffic"):
                    current = st.get("traffic")
            except Exception:
                current = None
            enabled = parse_bool_flag(app_config_value("TRAFFIC_METER_ENABLED", "false"), default=False)
            self.send_json(
                200,
                {
                    "ok": True,
                    "enabled": enabled,
                    "current": current,
                    "items": items,
                    "path": "/opt/automyai/data/traffic_meter/sessions.jsonl",
                },
            )
            return True

        if method == "GET" and path == "/api/health":
            self.send_json(
                200,
                {
                    "ok": True,
                    "configured": bool(CONFIG.api_key),
                    "teleAutoEnabled": bool(CONFIG.tele_auto_enabled),
                    "teleAutoConfigured": TELE_AUTO.configured,
                    "tempMailConfigured": bool(CONFIG.temp_mail_api_url and CONFIG.temp_mail_admin_password),
                    "outlookEmailConfigured": OUTLOOK_EMAIL.configured,
                    "outlookEmailAdminConfigured": OUTLOOK_EMAIL_ADMIN.configured,
                    "sub2apiConfigured": SUB2API.configured,
                    "sub2apiMonitor": SUB2API_MONITOR.status(),
                    "cpaMonitor": _cpa_monitor_status_safe(),
                    "apiUrl": CONFIG.api_url,
                    "purchaseConfigFile": str(CONFIG.purchase_config_file),
                    "purchaseConfig": get_purchase_config(),
                    "purchaseSettings": get_purchase_settings(),
                },
            )
            return True

        if method == "GET" and path == "/api/config":
            self.send_json(
                200,
                {
                    "appSettings": get_app_settings(),
                    "purchaseConfig": get_purchase_config(),
                    "purchaseSettings": get_purchase_settings(),
                },
            )
            return True

        if method == "GET" and path == "/api/app-settings":
            self.send_json(200, get_app_settings())
            return True

        if method == "POST" and path == "/api/app-settings":
            body = self.read_json_body()
            settings = update_app_settings(body)
            self.send_json(200, settings)
            return True

        if method == "GET" and path == "/api":
            self.send_json(
                200,
                {
                    "endpoints": {
                        "health": "GET /api/health",
                        "config": "GET /api/config",
                        "publicStatus": "GET /api/public/status",
                        "publicLabStatus": "GET /api/public/lab-status",
                        "appSettings": "GET /api/app-settings",
                        "saveAppSettings": "POST /api/app-settings",
                        "addressProfileCountries": "GET /api/address-profiles/countries",
                        "addressProfileRandom": "POST /api/address-profiles/random",
                        "balance": "GET /api/balance",
                        "purchaseCountries": "GET /api/purchase-catalog/countries?query=中国",
                        "refreshPurchaseCountries": "POST /api/purchase-catalog/countries/refresh",
                        "purchaseOperators": "GET /api/purchase-catalog/operators?countryCode=33&serviceCode=dr",
                        "purchase": "POST /api/purchase",
                        "listActive": "GET /api/activations",
                        "phonePool": "GET /api/phones/pool",
                        "phoneHistory": "GET /api/phones/:phone/history",
                        "emailQueue": "GET /api/email-queue",
                        "saveEmailQueue": "POST /api/email-queue",
                        "generateEmailQueue": "POST /api/email-queue/generate",
                        "allocateEmail": "POST /api/email-queue/allocate",
                        "emailAllocationResult": "POST /api/email-queue/allocation/result",
                        "emailPlatformUsage": "GET /api/email-queue/platform-usage?platform=grok",
                        "latestEmailMail": "GET /api/email-queue/mail/latest",
                        "tempMailSettings": "GET /api/temp-mail/settings",
                        "tempMailCreate": "POST /api/temp-mail/address",
                        "tempMailListMails": "GET /api/temp-mail/address/:address/mails",
                        "tempMailLatestMail": "GET /api/temp-mail/address/:address/mails/latest",
                        "tempMailDelete": "DELETE /api/temp-mail/address/:address",
                        "sub2apiCompliance": "GET /api/sub2api/compliance",
                        "sub2apiGroups": "GET /api/sub2api/groups",
                        "sub2apiMonitorStatus": "GET /api/sub2api/monitor/status",
                        "sub2apiMonitorCheck": "POST /api/sub2api/monitor/check",
                        "sub2apiOpenAIAuthUrl": "GET /api/sub2api/openai-auth-url",
                        "sub2apiOpenAICallback": "POST /api/sub2api/openai-callback",
                        "outlookEmailInventory": "GET /api/outlook-email/inventory",
                        "outlookEmailAccounts": "GET /api/outlook-email/accounts",
                        "outlookEmailEnsureGroups": "POST /api/outlook-email/groups/ensure",
                        "outlookEmailMoveAccounts": "POST /api/outlook-email/accounts/move",
                        "importOutlookSourceToQueue": "POST /api/email-queue/import-outlook-source",
                        "ucSignupStatus": "GET /api/uc-signup/status",
                        "ucSignupStart": "POST /api/uc-signup/start",
                        "ucSignupStop": "POST /api/uc-signup/stop",
                        "ucSignupLogs": "GET /api/uc-signup/logs",
                        "appleMailStatus": "GET /api/apple-mail/status",
                        "appleMailLogs": "GET /api/apple-mail/logs",
                        "mailAdminFreeAccounts": "GET /api/mail-admin/free-accounts",
                        "mailAdminMaterializeSessions": "POST /api/mail-admin/free-accounts/materialize",
                        "extractMethodsCatalog": "GET /api/extract-methods/catalog",
                        "extractMethodRun": "POST /api/extract-methods/run",
                    }
                },
            )
            return True
        return False

    def handle_browser_live_api(self, method: str, path: str, query: dict[str, str]) -> bool:
        """Browser process/page status; the UI uses the real noVNC session."""
        if method == "GET" and path == "/api/browser-live/status":
            self.send_json(200, browser_live_status())
            return True

        return False

    def handle_mail_queue_api(self, method: str, path: str, query: dict[str, str]) -> bool:
        """Signup email queue and OutlookEmail inventory/groups."""
        if method == "GET" and path == "/api/email-queue":
            self.send_json(200, {"emailQueue": load_email_queue()})
            return True

        if method == "POST" and path == "/api/email-queue":
            body = self.read_json_body()
            self.send_json(200, {"emailQueue": update_email_queue(body)})
            return True

        if method == "POST" and path == "/api/email-queue/generate":
            body = self.read_json_body()
            self.send_json(200, {"emailQueue": generate_email_queue(body)})
            return True

        if method == "GET" and path == "/api/email-queue/mail/latest":
            self.send_json(200, refresh_active_email_mail(query.get("address")))
            return True

        if method == "POST" and path == "/api/email-queue/import-outlook-source":
            body = self.read_json_body()
            source_group_name = str(
                first_non_empty(body.get("sourceGroupName"), body.get("groupName"), body.get("mailSourceGroupName")) or ""
            ).strip()
            self.send_json(200, import_outlook_source_group_to_email_queue(source_group_name))
            return True

        if method == "GET" and path == "/api/outlook-email/inventory":
            source_group_name = str(first_non_empty(query.get("sourceGroupName"), query.get("groupName")) or "").strip()
            self.send_json(200, build_outlook_email_inventory(source_group_name))
            return True

        if method == "GET" and path == "/api/outlook-email/accounts":
            payload = OUTLOOK_EMAIL.list_accounts(limit=10000, offset=0)
            self.send_json(200, build_outlook_account_control_payload(normalize_outlook_accounts(payload)))
            return True

        if method == "POST" and path == "/api/outlook-email/groups/ensure":
            names = outlook_group_names()
            result = OUTLOOK_EMAIL_ADMIN.ensure_groups([names["source"], names["pending"], names["success"], names["bad"]])
            self.send_json(200, result)
            return True

        if method == "POST" and path == "/api/outlook-email/accounts/move":
            body = self.read_json_body()
            self.send_json(200, move_outlook_accounts(body))
            return True
        return False

    def handle_temp_mail_api(self, method: str, path: str, query: dict[str, str]) -> bool:
        """Temp-mail address lifecycle and mailbox reads."""
        if method == "GET" and path == "/api/temp-mail/settings":
            provider_configured = bool(CONFIG.temp_mail_api_url)
            admin_configured = bool(CONFIG.temp_mail_admin_password)
            if not provider_configured:
                self.send_json(
                    200,
                    {
                        "configured": False,
                        "providerConfigured": False,
                        "adminConfigured": admin_configured,
                        "settings": {},
                    },
                )
                return True
            self.send_json(
                200,
                {
                    "configured": provider_configured and admin_configured,
                    "providerConfigured": provider_configured,
                    "adminConfigured": admin_configured,
                    "settings": TEMP_MAIL.get_settings(),
                },
            )
            return True

        if method == "POST" and path == "/api/temp-mail/address":
            body = self.read_json_body()
            settings = TEMP_MAIL.get_settings()
            domain = str(body.get("domain") or (settings.get("defaultDomains") or settings.get("domains") or [""])[0])
            name = str(body.get("name") or f"mail{int(datetime.now().timestamp())}")
            enable_prefix = bool(body.get("enablePrefix", True))
            result = TEMP_MAIL.create_address(name=name, domain=domain, enable_prefix=enable_prefix)
            self.send_json(201, {"item": result})
            return True

        if method == "GET" and path.startswith("/api/temp-mail/address/") and path.endswith("/mails/latest"):
            address = unquote(path.split("/")[-3])
            mail = enrich_temp_mail_item(TEMP_MAIL.latest_mail(address))
            self.send_json(200, {"address": address, "item": mail})
            return True

        if method == "GET" and path.startswith("/api/temp-mail/address/") and path.endswith("/mails"):
            address = unquote(path.split("/")[-2])
            limit = int(query.get("limit", "20"))
            offset = int(query.get("offset", "0"))
            mails = TEMP_MAIL.list_mails(address, limit=limit, offset=offset)
            if isinstance(mails.get("results"), list):
                mails["results"] = [enrich_temp_mail_item(item) for item in mails["results"]]
            self.send_json(200, {"address": address, **mails})
            return True

        if method == "DELETE" and path.startswith("/api/temp-mail/address/"):
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "temp-mail" and parts[2] == "address":
                address = unquote(parts[3])
                result = TEMP_MAIL.delete_address(address)
                self.send_json(200, {"address": address, **result})
                return True
        return False

    def handle_sub2api_api(self, method: str, path: str, query: dict[str, str]) -> bool:
        """Sub2API compliance/groups/monitor, CPA monitor and the OpenAI OAuth import."""
        if method == "GET" and path == "/api/sub2api/compliance":
            self.send_json(200, SUB2API.admin_compliance_status())
            return True

        if method == "GET" and path == "/api/sub2api/groups":
            groups = SUB2API.list_groups()
            self.send_json(
                200,
                {
                    "groups": [
                        strip_empty_values(
                            {
                                "id": group.get("id"),
                                "name": group.get("name"),
                                "platform": group.get("platform"),
                                "status": group.get("status"),
                                "deletedAt": group.get("deleted_at"),
                            }
                        )
                        for group in groups
                        if isinstance(group, dict)
                    ]
                },
            )
            return True

        if method == "GET" and path == "/api/sub2api/monitor/status":
            self.send_json(200, SUB2API_MONITOR.status())
            return True

        if method == "POST" and path == "/api/sub2api/monitor/check":
            body = self.read_json_body()
            trigger = parse_bool_flag(body.get("trigger"), default=False)
            self.send_json(200, SUB2API_MONITOR.check_once(trigger=trigger))
            return True

        if method == "GET" and path == "/api/cpa/monitor/status":
            try:
                import extensions_api
                self.send_json(200, extensions_api.CPA_MONITOR.status())
            except Exception as error:
                self.send_json(500, {"error": str(error)})
            return True

        if method == "POST" and path == "/api/cpa/monitor/check":
            try:
                import extensions_api
                body = self.read_json_body()
                trigger = parse_bool_flag(body.get("trigger"), default=False)
                force = parse_bool_flag(body.get("force"), default=False)
                if parse_bool_flag(body.get("manual"), default=False):
                    force = True
                self.send_json(200, extensions_api.CPA_MONITOR.check_once(trigger=True if force else trigger, force=force))
            except Exception as error:
                self.send_json(500, {"error": str(error)})
            return True

        if method == "GET" and path == "/api/sub2api/openai-auth-url":
            redirect_uri = str(query.get("redirect_uri") or "http://localhost:1455/auth/callback").strip()
            proxy_id = str(query.get("proxy_id") or "").strip()
            self.send_json(200, SUB2API.openai_generate_auth_url(redirect_uri=redirect_uri, proxy_id=proxy_id))
            return True

        if method == "POST" and path == "/api/sub2api/openai-callback":
            body = self.read_json_body()
            code, state, redirect_url = extract_oauth_callback_params(body)
            session_id = str(first_non_empty(body.get("session_id"), body.get("sessionId")) or "").strip()
            proxy_id = str(first_non_empty(body.get("proxy_id"), body.get("proxyId")) or "").strip()
            email = str(first_non_empty(body.get("email"), body.get("account_email"), body.get("accountEmail")) or "").strip()
            target_group_names = first_non_empty(body.get("targetGroupNames"), body.get("targetGroups"), body.get("groupNames"))
            exchange_result = SUB2API.openai_exchange_code(session_id=session_id, code=code, state=state, proxy_id=proxy_id)
            document = build_sub2api_document_from_openai_oauth(exchange_result, requested_email=email)
            # Persist OAuth material to Mail Admin first. If Sub2API import or
            # group binding fails afterwards, AT/RT/Session are still retained
            # and the account can be retried without redoing the browser flow.
            opus_client = OpusMailClient.from_project(ROOT, proxy_url=CONFIG.uc_signup_proxy or CONFIG.browser_proxy)
            try:
                opus_import = opus_client.import_openai_oauth(exchange_result, email=email)
            except OpusMailError as error:
                opus_import = {
                    "configured": opus_client.configured,
                    "imported": False,
                    "error": str(error),
                }
            try:
                import_result = SUB2API.import_accounts_document(document)
            except Exception as error:
                import_result = {"success": False, "error": str(error), "retryable": True}
            try:
                group_bind = bind_sub2api_import_to_target_groups(document, target_group_names)
            except Exception as error:
                group_bind = {"success": False, "error": str(error)}
            import_ok = not (
                isinstance(import_result, dict)
                and (import_result.get("success") is False or import_result.get("error"))
            )
            group_ok = bool(group_bind.get("success")) or bool(group_bind.get("skipped"))
            callback_success = bool(import_ok and group_ok)
            if not callback_success and opus_import.get("imported"):
                failure_reason = str(
                    first_non_empty(
                        import_result.get("error") if isinstance(import_result, dict) else "",
                        group_bind.get("error"),
                        group_bind.get("reason"),
                        "Sub2API / 分组未完成",
                    )
                    or ""
                )[:500]
                try:
                    status_update = opus_client.import_openai_oauth(
                        {**exchange_result, "statusMessage": failure_reason},
                        email=email,
                    )
                    opus_import = {**opus_import, "statusMessageUpdated": bool(status_update.get("imported"))}
                except OpusMailError as error:
                    opus_import = {
                        **opus_import,
                        "statusMessageUpdated": False,
                        "statusMessageError": str(error),
                    }
            account = document.get("accounts", [{}])[0] if isinstance(document.get("accounts"), list) else {}
            credentials = account.get("credentials") if isinstance(account, dict) and isinstance(account.get("credentials"), dict) else {}
            self.send_json(
                200,
                {
                    "success": callback_success,
                    "document": summarize_sub2api_document(document),
                    "sub2api": import_result,
                    "groupBind": group_bind,
                    "tokens": {
                        "hasAccessToken": bool(credentials.get("access_token")),
                        "hasRefreshToken": bool(credentials.get("refresh_token")),
                        "hasIdToken": bool(credentials.get("id_token")),
                        "hasSessionToken": bool(credentials.get("session_token")),
                    },
                    "opusMail": opus_import,
                    "redirectUrl": redirect_url,
                },
            )
            return True

        if path.startswith("/api/codex-oauth"):
            self.send_json(410, {"error": "旧 OAuth 接口已停用，请使用 Sub2API OpenAI OAuth 接口"})
            return True
        return False

    def handle_purchase_api(self, method: str, path: str, query: dict[str, str]) -> bool:
        """Purchase settings plus the HeroSMS catalog, pricing and balance reads."""
        if method == "GET" and path == "/api/purchase-settings":
            self.send_json(200, {"purchaseSettings": get_purchase_settings()})
            return True

        if method == "POST" and path == "/api/purchase-settings":
            body = self.read_json_body()
            settings = update_purchase_settings(body)
            self.send_json(200, {"purchaseSettings": settings, "purchaseConfig": get_purchase_config()})
            return True

        if method == "GET" and path == "/api/purchase-catalog/countries":
            if not CONFIG.api_key:
                self.send_json(
                    200,
                    {"items": [], "total": 0, "cachedAt": "", "refreshed": False, "warning": "HeroSMS 未配置"},
                )
                return True
            refresh = parse_bool_flag(query.get("refresh"), default=False)
            countries, cache = get_cached_countries(refresh=refresh)
            limit = parse_positive_int(query.get("limit"), default=len(countries))
            matches = search_country_items(countries, query.get("query", ""), limit=min(max(limit, 1), len(countries)))
            self.send_json(
                200,
                {
                    "items": matches,
                    "total": len(countries),
                    "cachedAt": cache.get("countriesCachedAt", ""),
                    "refreshed": refresh,
                },
            )
            return True

        if method == "POST" and path == "/api/purchase-catalog/countries/refresh":
            if not CONFIG.api_key:
                self.send_json(
                    200,
                    {"items": [], "total": 0, "cachedAt": "", "refreshed": False, "warning": "HeroSMS 未配置"},
                )
                return True
            countries, cache = get_cached_countries(refresh=True)
            self.send_json(
                200,
                {
                    "items": search_country_items(countries, "", limit=len(countries)),
                    "total": len(countries),
                    "cachedAt": cache.get("countriesCachedAt", ""),
                    "refreshed": True,
                },
            )
            return True

        if method == "GET" and path == "/api/purchase-catalog/operators":
            service_code = str(query.get("serviceCode") or get_purchase_settings().get("serviceCode") or DEFAULT_SERVICE_CODE).strip()
            country_code = str(query.get("countryCode") or "").strip()
            if not country_code:
                self.send_json(400, {"error": "缺少 countryCode"})
                return True
            refresh = parse_bool_flag(query.get("refresh"), default=False)
            operators, cache = get_cached_operators(service_code, country_code, refresh=refresh)
            operator_entry = (cache.get("operators") or {}).get(f"{service_code}:{country_code}") or {}
            self.send_json(
                200,
                {
                    "items": operators,
                    "serviceCode": service_code,
                    "countryCode": country_code,
                    "cachedAt": operator_entry.get("cachedAt", ""),
                    "refreshed": refresh,
                },
            )
            return True

        if method == "GET" and path == "/api/options":
            defaults = get_purchase_config()
            self.send_json(
                200,
                {
                    "services": CLIENT.get_services(),
                    "countries": CLIENT.get_countries(),
                    "defaults": {
                        "serviceName": defaults.get("serviceName", ""),
                        "serviceCode": defaults.get("serviceCode", ""),
                        "countryName": defaults.get("countryName", ""),
                        "countryCode": defaults.get("countryCode", ""),
                        "operator": defaults.get("operator", CONFIG.default_operator),
                    },
                },
            )
            return True

        if method == "GET" and path == "/api/country-lookup":
            name = str(query.get("name") or "").strip()
            if not name:
                self.send_json(400, {"error": "缺少国家名称 name"})
                return True
            service_code = str(query.get("serviceCode") or get_purchase_settings().get("serviceCode") or DEFAULT_SERVICE_CODE).strip()
            matches = search_countries_by_name(name)
            if not matches:
                self.send_json(404, {"error": f"找不到国家/地区: {name}"})
                return True
            country = matches[0]
            operators = CLIENT.get_operators(service_code, str(country.get("code") or ""))
            self.send_json(
                200,
                {
                    "query": name,
                    "serviceCode": service_code,
                    "country": country,
                    "operators": operators,
                    "matches": matches,
                },
            )
            return True

        if method == "GET" and path == "/api/balance":
            try:
                balance = CLIENT.get_balance_cached()
            except HeroSmsError:
                balance = None
            self.send_json(200, {"balance": balance})
            return True

        if method == "GET" and path == "/api/pricing":
            filters = get_filters(query)
            resolved = resolve_selections(filters)
            pricing = CLIENT.get_pricing(resolved["service"]["code"], resolved["country"]["code"])
            self.send_json(
                200,
                {
                    "filters": filters,
                    "service": resolved["service"],
                    "country": resolved["country"],
                    "operators": ["any"],
                    "pricing": pricing,
                },
            )
            return True

        if method == "GET" and path == "/api/catalog":
            filters = get_filters(query)
            resolved = resolve_selections(filters)
            pricing = CLIENT.get_pricing(resolved["service"]["code"], resolved["country"]["code"])
            try:
                balance = CLIENT.get_balance_cached()
            except HeroSmsError:
                balance = None
            self.send_json(
                200,
                {
                    "filters": filters,
                    "service": resolved["service"],
                    "country": resolved["country"],
                    "operators": ["any"],
                    "pricing": pricing,
                    "balance": balance,
                    "note": "当前兼容 API 主要返回国家维度价格，运营商选择用于下单通道。",
                },
            )
            return True
        return False

    def handle_activation_api(self, method: str, path: str, query: dict[str, str]) -> bool:
        """SMS activation records: list, import, purchase, sync and per-activation reads."""
        if method == "GET" and path == "/api/activations":
            items = fetch_upstream_activations()
            items = filter_activations(
                items,
                service_code=query.get("serviceCode", ""),
                country_code=query.get("countryCode", ""),
                operator=query.get("operator", ""),
                price=query.get("price", ""),
            )
            self.send_json(200, {"items": items})
            return True

        if method == "GET" and path == "/api/current-phone":
            items = get_current_filtered_activations()
            self.send_json(
                200,
                {
                    "purchaseSettings": get_purchase_settings(),
                    "item": items[0] if items else None,
                    "items": items,
                },
            )
            return True

        if method == "GET" and path == "/api/activations/latest":
            items = fetch_upstream_activations()
            items = filter_activations(
                items,
                service_code=query.get("serviceCode", ""),
                country_code=query.get("countryCode", ""),
                operator=query.get("operator", ""),
                price=query.get("price", ""),
            )
            self.send_json(200, {"item": items[0] if items else None})
            return True

        if method == "POST" and path == "/api/activations/import":
            items = fetch_upstream_activations()
            self.send_json(200, {"items": items})
            return True

        if method == "POST" and path == "/api/activations":
            body = self.read_json_body()
            result = purchase_with_fallback(body)
            item = dict(result["item"])
            item["rawPurchase"] = result["rawPurchase"]
            self.send_json(201, {"item": item, "filters": result["filters"], "attempts": result["attempts"]})
            return True

        if method == "POST" and path == "/api/purchase":
            body = self.read_json_body()
            result = purchase_with_fallback(body)
            self.send_json(
                201,
                {
                    "filters": result["filters"],
                    "item": result["item"],
                    "attempts": result["attempts"],
                },
            )
            return True

        if method == "POST" and path == "/api/activations/sync":
            self.send_json(200, {"items": fetch_upstream_activations()})
            return True

        if method == "GET" and path.startswith("/api/activations/") and path.endswith("/code"):
            activation_id = path.split("/")[-2]
            local_record = STORE.get(activation_id)
            if is_tele_auto_record(local_record):
                phone_number_for_quota = str(local_record.get("phoneNumber") or "")
                if phone_number_for_quota:
                    quota = phone_code_quota_status(phone_number_for_quota, activation_id)
                    if not quota.get("allowed"):
                        self.send_json(429, {"error": f"PHONE_CODE_QUOTA: {quota.get('message')}", "quota": quota})
                        return True
                status = TELE_AUTO.get_status(local_record)
                if status.get("code"):
                    quota = record_phone_code_usage(phone_number_for_quota, activation_id, status["code"])
                    if not quota.get("allowed"):
                        self.send_json(429, {"error": f"PHONE_CODE_QUOTA: {quota.get('message')}", "quota": quota})
                        return True
                matched = update_tele_record_from_status(local_record, status)
                self.send_json(200, {"record": matched, "status": status})
                return True

            upstream_items = fetch_upstream_activations()
            matched = next((item for item in upstream_items if str(item.get("id")) == str(activation_id)), None)
            phone_number_for_quota = str((matched or {}).get("phoneNumber") or "")
            if phone_number_for_quota:
                quota = phone_code_quota_status(phone_number_for_quota, activation_id)
                if not quota.get("allowed"):
                    self.send_json(429, {"error": f"PHONE_CODE_QUOTA: {quota.get('message')}", "quota": quota})
                    return True
            status = CLIENT.get_status(activation_id)
            if matched is None:
                matched = normalize_record(
                    {
                        "id": activation_id,
                        "phoneNumber": "--",
                        "serviceName": "--",
                        "countryName": "--",
                        "operator": "any",
                        "activationCost": None,
                        "status": status["localStatus"],
                        "statusLabel": status["label"],
                        "upstreamStatus": status["upstreamStatus"],
                        "lastCode": status.get("code"),
                        "codes": [status["code"]] if status.get("code") else [],
                        "updatedAt": now_iso(),
                    }
                )
            elif status.get("code"):
                quota = record_phone_code_usage(phone_number_for_quota, activation_id, status["code"])
                if not quota.get("allowed"):
                    self.send_json(429, {"error": f"PHONE_CODE_QUOTA: {quota.get('message')}", "quota": quota})
                    return True
                matched["lastCode"] = status["code"]
                matched["codes"] = [status["code"]]
                matched["status"] = status["localStatus"]
                matched["statusLabel"] = status["label"]
                matched["upstreamStatus"] = status["upstreamStatus"]
                matched["updatedAt"] = now_iso()
            self.send_json(200, {"record": matched, "status": status})
            return True

        if method == "GET" and path.startswith("/api/activations/"):
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "activations":
                activation_id = parts[2]
                local_record = STORE.get(activation_id)
                if is_tele_auto_record(local_record):
                    self.send_json(200, {"item": normalize_record(local_record)})
                    return True
                upstream_items = fetch_upstream_activations()
                matched = next((item for item in upstream_items if str(item.get("id")) == str(activation_id)), None)
                if matched is None:
                    status = CLIENT.get_status(activation_id)
                    matched = normalize_record(
                        {
                            "id": activation_id,
                            "phoneNumber": "--",
                            "serviceName": "--",
                            "countryName": "--",
                            "operator": "any",
                            "activationCost": None,
                            "status": status["localStatus"],
                            "statusLabel": status["label"],
                            "upstreamStatus": status["upstreamStatus"],
                            "lastCode": status.get("code"),
                            "codes": [status["code"]] if status.get("code") else [],
                            "updatedAt": now_iso(),
                        }
                    )
                self.send_json(200, {"item": matched})
                return True

        if method == "POST" and path.startswith("/api/activations/"):
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "activations":
                activation_id = parts[2]
                action = parts[3]
                if action in {"cancel", "finish", "ready"}:
                    action_map = {
                        "cancel": {"status": 8, "localStatus": "canceled", "label": "已取消"},
                        "finish": {"status": 6, "localStatus": "finished", "label": "已完成"},
                        "ready": {"status": 1, "localStatus": "waiting_for_code", "label": "等待验证码"},
                    }
                    current = action_map[action]
                    existing_record = STORE.get(activation_id)
                    if is_tele_auto_record(existing_record):
                        if action == "cancel":
                            advance_purchase_group_cursor_after_group(existing_record.get("purchaseGroupIndex"))
                            upstream = TELE_AUTO.fail_account(existing_record)
                        else:
                            upstream = {"raw": None, "result": "tele_auto_noop"}
                        item = STORE.upsert(
                            {
                                **existing_record,
                                "status": current["localStatus"],
                                "statusLabel": current["label"],
                                "upstreamStatus": "STATUS_CANCEL" if action == "cancel" else existing_record.get("upstreamStatus", ""),
                                "lastAction": action,
                                "teleAutoActionResult": upstream,
                                "updatedAt": now_iso(),
                            }
                        )
                        self.send_json(200, {"item": normalize_record(item), "upstream": upstream})
                        return True
                    if action == "cancel" and existing_record:
                        advance_purchase_group_cursor_after_group(existing_record.get("purchaseGroupIndex"))
                    try:
                        upstream = CLIENT.set_status(activation_id, current["status"])
                    except HeroSmsError as error:
                        if action != "cancel" or not is_early_cancel_denied_error(error):
                            raise
                        item = normalize_record(
                            {
                                **(existing_record or {"id": activation_id}),
                                "lastAction": "cancel_denied",
                                "cancelDeferred": True,
                                "cancelWarning": str(error),
                                "updatedAt": now_iso(),
                            }
                        )
                        item = STORE.upsert(item)
                        self.send_json(
                            200,
                            {
                                "item": normalize_record(item),
                                "upstream": {"raw": None, "result": "cancel_deferred"},
                                "warning": str(error),
                            },
                        )
                        return True
                    item = normalize_record(
                        {
                            "id": activation_id,
                            "status": current["localStatus"],
                            "statusLabel": current["label"],
                            "lastAction": action,
                            "updatedAt": now_iso(),
                        }
                    )
                    self.send_json(200, {"item": item, "upstream": upstream})
                    return True
        return False

    def handle_phone_api(self, method: str, path: str, query: dict[str, str]) -> bool:
        """Phone pool and per-phone code, history and action routes."""
        if method == "GET" and path == "/api/phones/pool":
            limit = parse_positive_int(query.get("limit"), default=200)
            self.send_json(200, phone_pool_payload(limit))
            return True

        if method == "GET" and path.startswith("/api/phones/") and path.endswith("/code"):
            phone_number = path.split("/")[-2]
            matched = find_activation_by_phone(phone_number)
            if not matched:
                self.send_json(404, {"error": "上游当前活跃号码中找不到该手机号"})
                return True
            quota = phone_code_quota_status(phone_number, matched.get("id"))
            if not quota.get("allowed"):
                self.send_json(429, {"error": f"PHONE_CODE_QUOTA: {quota.get('message')}", "quota": quota})
                return True
            if is_tele_auto_record(matched):
                status = TELE_AUTO.get_status(matched)
                if status.get("code"):
                    quota = record_phone_code_usage(phone_number, matched.get("id"), status["code"])
                    if not quota.get("allowed"):
                        self.send_json(429, {"error": f"PHONE_CODE_QUOTA: {quota.get('message')}", "quota": quota})
                        return True
                matched = update_tele_record_from_status(matched, status)
                self.send_json(200, {"phoneNumber": phone_number, "record": matched, "status": status})
                return True
            status = CLIENT.get_status(str(matched["id"]))
            if status.get("code"):
                quota = record_phone_code_usage(phone_number, matched.get("id"), status["code"])
                if not quota.get("allowed"):
                    self.send_json(429, {"error": f"PHONE_CODE_QUOTA: {quota.get('message')}", "quota": quota})
                    return True
                matched["lastCode"] = status["code"]
                matched["codes"] = [status["code"]]
                matched["status"] = status["localStatus"]
                matched["statusLabel"] = status["label"]
                matched["upstreamStatus"] = status["upstreamStatus"]
                matched["updatedAt"] = now_iso()
            self.send_json(200, {"phoneNumber": phone_number, "record": matched, "status": status})
            return True

        if method == "GET" and path.startswith("/api/phones/") and path.endswith("/history"):
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "phones" and parts[3] == "history":
                phone_number = parts[2]
                payload = phone_detail_payload(phone_number)
                if not payload.get("item") and not payload.get("binding"):
                    self.send_json(404, {"error": "本地没有该手机号记录"})
                    return True
                self.send_json(200, payload)
                return True

        if method == "GET" and path.startswith("/api/phones/"):
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "phones":
                phone_number = parts[2]
                payload = phone_detail_payload(phone_number)
                if not payload.get("item") and not payload.get("binding"):
                    self.send_json(404, {"error": "本地没有该手机号记录"})
                    return True
                self.send_json(200, payload)
                return True

        if method == "POST" and path.startswith("/api/phones/"):
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "phones":
                phone_number = parts[2]
                action = parts[3]
                matched = find_activation_by_phone(phone_number)
                if not matched:
                    self.send_json(404, {"error": "上游当前活跃号码中找不到该手机号"})
                    return True
                if action not in {"cancel", "finish", "ready", "release", "hold", "bind-proxy"}:
                    self.send_json(404, {"error": "接口不存在"})
                    return True
                if action == "bind-proxy":
                    body = self.read_json_body()
                    proxy_url = first_non_empty(body.get("proxy"), body.get("proxyUrl"), body.get("ucSignupProxy"))
                    proxy_name = first_non_empty(body.get("proxyName"), body.get("proxy_name"))
                    binding = bind_phone_proxy(
                        phone_number,
                        identity_proxy_descriptor(proxy_url, proxy_name),
                        email=first_non_empty(body.get("email"), body.get("accountEmail"), body.get("account_email")) or "",
                        activation_id=matched.get("id"),
                        stage=str(body.get("stage") or "submitted"),
                        activation=matched,
                    )
                    item = STORE.upsert(
                        {
                            **matched,
                            "identityBinding": binding,
                            "identityProxy": identity_proxy_descriptor(proxy_url, proxy_name),
                            "updatedAt": now_iso(),
                        }
                    )
                    self.send_json(200, {"phoneNumber": phone_number, "item": normalize_record(item), "binding": binding})
                    return True
                if action == "hold":
                    body = self.read_json_body()
                    reason = str(body.get("reason") or body.get("message") or "").strip() if isinstance(body, dict) else ""
                    is_whatsapp = "whatsapp" in reason.lower()
                    cooldown_seconds = parse_positive_int(
                        CONFIG.phone_whatsapp_cooldown_seconds if is_whatsapp else CONFIG.phone_sms_cooldown_seconds,
                        default=21600 if is_whatsapp else 1800,
                    )
                    lifecycle = mark_phone_cooldown(
                        phone_number,
                        reason,
                        cooldown_seconds,
                        source="tele-auto" if is_tele_auto_record(matched) else "self-maintained",
                    )
                    item = STORE.upsert(
                        {
                            **matched,
                            "status": "cooldown",
                            "statusLabel": lifecycle.get("statusLabel") or "冷却中",
                            "lastAction": "hold",
                            "holdReason": reason,
                            "cooldownUntil": lifecycle.get("cooldownUntil"),
                            "cooldownSeconds": cooldown_seconds,
                            "teleAutoActionResult": {"raw": None, "result": "local_hold"},
                            "updatedAt": now_iso(),
                        }
                    )
                    self.send_json(
                        200,
                        {
                            "phoneNumber": phone_number,
                            "item": normalize_record(item),
                            "upstream": {"raw": None, "result": "local_hold"},
                            "lifecycle": lifecycle,
                        },
                    )
                    return True
                action_map = {
                    "cancel": {"status": 8, "localStatus": "canceled", "label": "已取消"},
                    "finish": {"status": 6, "localStatus": "finished", "label": "已完成"},
                    "ready": {"status": 1, "localStatus": "waiting_for_code", "label": "等待验证码"},
                    "release": {"status": 4, "localStatus": "released", "label": "已释放"},
                }
                current = action_map[action]
                if is_tele_auto_record(matched):
                    if action == "cancel":
                        advance_purchase_group_cursor_after_group(matched.get("purchaseGroupIndex"))
                        upstream = TELE_AUTO.fail_account(matched)
                    elif action == "release":
                        upstream = TELE_AUTO.release_account(matched)
                    else:
                        upstream = {"raw": None, "result": "tele_auto_noop"}
                    item = STORE.upsert(
                        {
                            **matched,
                            "status": current["localStatus"],
                            "statusLabel": current["label"],
                            "upstreamStatus": "STATUS_CANCEL" if action == "cancel" else matched.get("upstreamStatus", ""),
                            "lastAction": action,
                            "teleAutoActionResult": upstream,
                            "updatedAt": now_iso(),
                        }
                    )
                    self.send_json(200, {"phoneNumber": phone_number, "item": normalize_record(item), "upstream": upstream})
                    return True
                if action == "release":
                    self.send_json(409, {"error": "release 仅支持 Tele Auto 记录"})
                    return True
                if action == "cancel":
                    advance_purchase_group_cursor_after_group(matched.get("purchaseGroupIndex"))
                try:
                    upstream = CLIENT.set_status(str(matched["id"]), current["status"])
                except HeroSmsError as error:
                    if action != "cancel" or not is_early_cancel_denied_error(error):
                        raise
                    item = STORE.upsert(
                        {
                            **matched,
                            "lastAction": "cancel_denied",
                            "cancelDeferred": True,
                            "cancelWarning": str(error),
                            "updatedAt": now_iso(),
                        }
                    )
                    self.send_json(
                        200,
                        {
                            "phoneNumber": phone_number,
                            "item": normalize_record(item),
                            "upstream": {"raw": None, "result": "cancel_deferred"},
                            "warning": str(error),
                        },
                    )
                    return True
                item = normalize_record(
                    {
                        **matched,
                        "status": current["localStatus"],
                        "statusLabel": current["label"],
                        "lastAction": action,
                        "updatedAt": now_iso(),
                    }
                )
                self.send_json(200, {"phoneNumber": phone_number, "item": item, "upstream": upstream})
                return True
        return False

    def handle_uc_signup_api(self, method: str, path: str, query: dict[str, str]) -> bool:
        """OpenAI browser signup task control and logs."""
        if method == "GET" and path == "/api/uc-signup/status":
            self.send_json(200, {"ucSignupState": UC_SIGNUP_MANAGER.get_state()})
            return True

        if method == "POST" and path == "/api/uc-signup/start":
            body = self.read_json_body()
            emails = normalize_email_lines(body.get("emails", []))
            if not emails:
                queue = load_email_queue()
                emails = normalize_email_lines(queue.get("emails", []))
            if not emails:
                self.send_json(400, {"error": "没有可处理的邮箱，请先从默认分组导入或保存邮箱列表"})
                return True
            result = UC_SIGNUP_MANAGER.start(
                emails,
                apiBase=body.get("apiBase"),
                display=body.get("display"),
                proxy=body.get("proxy"),
                chromeBinary=body.get("chromeBinary"),
                chromeVersion=body.get("chromeVersion"),
                password=body.get("password"),
                name=body.get("name"),
                age=body.get("age"),
                authOnly=body.get("authOnly"),
                getRefreshToken=body.get("getRefreshToken") if "getRefreshToken" in body else body.get("get_refresh_token"),
                manualMode=body.get("manualMode") if "manualMode" in body else body.get("manual_mode"),
                moveMail=body.get("moveMail"),
                forcedPhone=body.get("forcedPhone") or body.get("phone") or body.get("phoneNumber"),
                keepBrowserOnFailure=body.get("keepBrowserOnFailure"),
                keepBrowserSeconds=body.get("keepBrowserSeconds"),
                mailSourceGroup=body.get("mailSourceGroup") or body.get("sourceGroup"),
                mailPendingGroup=body.get("mailPendingGroup") or body.get("pendingGroup"),
                mailSuccessGroup=body.get("mailSuccessGroup") or body.get("successGroup"),
                mailBadGroup=body.get("mailBadGroup") or body.get("badGroup"),
                mailProvider=body.get("mailProvider") or body.get("provider"),
            )
            if "error" in result:
                self.send_json(409, result)
                return True
            queue = load_email_queue()
            queue = save_email_queue({**queue, "cursor": 0, "activeEmail": emails[0] if emails else ""})
            self.send_json(200, {"ucSignupState": result["ucSignupState"], "emailQueue": queue})
            return True

        if method == "POST" and path == "/api/uc-signup/stop":
            self.send_json(200, UC_SIGNUP_MANAGER.stop())
            return True

        if method == "GET" and path == "/api/uc-signup/logs":
            self.send_json(200, {"logs": UC_SIGNUP_MANAGER.get_logs()})
            return True
        return False

    def handle_apple_mail_api(self, method: str, path: str, query: dict[str, str]) -> bool:
        """Apple Mail channel status and logs."""
        if method == "GET" and path == "/api/apple-mail/status":
            tail = query.get("tail") or query.get("limit") or "200"
            try:
                self.send_json(200, apple_mail_status_payload(tail=int(tail)))
            except Exception as error:
                self.send_json(500, {"ok": False, "error": str(error)})
            return True

        if method == "GET" and path == "/api/apple-mail/logs":
            tail = query.get("tail") or query.get("limit") or "300"
            try:
                payload = apple_mail_status_payload(tail=int(tail))
                self.send_json(200, {"ok": True, "logs": payload.get("logs") or [], "state": {
                    "running": payload.get("running"),
                    "currentStep": payload.get("currentStep"),
                    "currentStepLabel": payload.get("currentStepLabel"),
                    "email": payload.get("email"),
                    "impersonate": payload.get("impersonate"),
                    "updatedAt": payload.get("updatedAt"),
                }})
            except Exception as error:
                self.send_json(500, {"ok": False, "error": str(error)})
            return True
        return False



    def handle_mail_admin_api(self, method: str, path: str, query: dict[str, str]) -> bool:
        """Browse Free / unactivated Mail Admin accounts and materialize extraction sessions."""
        if method == "GET" and path == "/api/mail-admin/free-accounts":
            try:
                reader = OpusMailAdminReader.from_project(ROOT)
                if not reader.configured:
                    self.send_json(503, {
                        "success": False,
                        "configured": False,
                        "error": "Mail Admin 读取未配置（缺少 admin 口令或地址）",
                        "accounts": [],
                    })
                    return True
                marked_only = str(query.get("markedOnly") or query.get("marked_only") or "").strip().lower() in {"1", "true", "yes", "on"}
                include_sold = str(query.get("includeSold") or query.get("include_sold") or "1").strip().lower() not in {"0", "false", "no", "off"}
                mark_color = str(query.get("markColor") or query.get("mark_color") or query.get("color") or "").strip()
                search = str(query.get("q") or query.get("query") or query.get("search") or "").strip()
                try:
                    limit = int(query.get("limit") or 300)
                except ValueError:
                    limit = 300
                payload = reader.list_free_unactivated(
                    marked_only=marked_only,
                    mark_color=mark_color,
                    include_sold=include_sold,
                    query=search,
                    limit=limit,
                )
                self.send_json(200, payload)
            except OpusMailAdminReaderError as error:
                self.send_json(error.status_code, {"success": False, "error": str(error), "accounts": []})
            except Exception as error:
                self.send_json(500, {"success": False, "error": str(error), "accounts": []})
            return True

        if method == "GET" and path == "/api/mail-admin/openai-signup-pool":
            try:
                reader = OpusMailAdminReader.from_project(ROOT)
                if not reader.configured:
                    self.send_json(503, {
                        "success": False,
                        "configured": False,
                        "error": "Mail Opus 读取未配置",
                        "accounts": [],
                    })
                    return True
                try:
                    limit = int(query.get("limit") or 500)
                except ValueError:
                    limit = 500
                self.send_json(200, reader.list_pending_signup_accounts(limit=limit))
            except OpusMailAdminReaderError as error:
                self.send_json(error.status_code, {"success": False, "error": str(error), "accounts": []})
            except Exception as error:
                self.send_json(500, {"success": False, "error": str(error), "accounts": []})
            return True

        if method == "POST" and path == "/api/mail-admin/openai-signup-pool/import":
            try:
                body = self.read_json_body()
            except Exception:
                body = {}
            raw = body.get("emailsText", body.get("emails", [])) if isinstance(body, dict) else []
            if isinstance(raw, list):
                candidates = [str(item or "").strip() for item in raw]
            else:
                candidates = [part.strip() for part in re.split(r"[\s,;]+", str(raw or ""))]
            emails = []
            seen = set()
            invalid = []
            for value in candidates:
                if not value:
                    continue
                normalized = value.lower()
                if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
                    invalid.append(value[:160])
                    continue
                if normalized not in seen:
                    seen.add(normalized)
                    emails.append(normalized)
            if invalid:
                self.send_json(400, {"success": False, "error": f"包含无效邮箱（{len(invalid)} 个）", "invalid": invalid[:10]})
                return True
            if not emails:
                self.send_json(400, {"success": False, "error": "请至少填写一个邮箱"})
                return True
            if len(emails) > 200:
                self.send_json(400, {"success": False, "error": "单次最多导入 200 个邮箱"})
                return True
            try:
                reader = OpusMailAdminReader.from_project(ROOT)
                client = OpusMailClient.from_project(ROOT)
                if not reader.configured or not client.configured:
                    self.send_json(503, {"success": False, "configured": False, "error": "Mail Opus 读写接口未完整配置"})
                    return True
                existing = {
                    str(item.get("email") or item.get("login") or "").strip().lower(): item
                    for item in reader.list_mappings()
                    if str(item.get("email") or item.get("login") or "").strip()
                }
                imported = []
                skipped = []
                failed = []
                for email in emails:
                    if email in existing:
                        skipped.append({"email": email, "reason": "already_exists"})
                        continue
                    try:
                        result = client.import_pending_email(email=email)
                        if result.get("imported"):
                            imported.append({"email": email, "accountId": result.get("accountId") or ""})
                        else:
                            failed.append({"email": email, "error": str(result.get("reason") or "not_imported")})
                    except OpusMailError as error:
                        failed.append({"email": email, "error": str(error)[:240]})
                status = 200 if not failed else (207 if imported or skipped else 502)
                self.send_json(status, {
                    "success": not failed,
                    "requested": len(emails),
                    "importedCount": len(imported),
                    "skippedCount": len(skipped),
                    "failedCount": len(failed),
                    "imported": imported,
                    "skipped": skipped,
                    "failed": failed,
                })
            except OpusMailAdminReaderError as error:
                self.send_json(error.status_code, {"success": False, "error": str(error)})
            except Exception as error:
                self.send_json(500, {"success": False, "error": str(error)})
            return True

        if method == "POST" and path == "/api/mail-admin/free-accounts/materialize":
            try:
                body = self.read_json_body()
            except Exception:
                body = {}
            ids = body.get("ids") or body.get("accountIds") or body.get("account_ids") or []
            if isinstance(ids, str):
                ids = [part.strip() for part in ids.replace(",", "\n").splitlines() if part.strip()]
            if not isinstance(ids, list):
                self.send_json(400, {"success": False, "error": "ids 必须是数组"})
                return True
            try:
                reader = OpusMailAdminReader.from_project(ROOT)
                if not reader.configured:
                    self.send_json(503, {"success": False, "configured": False, "error": "Mail Admin 读取未配置"})
                    return True
                payload = reader.materialize_credentials([str(item) for item in ids])
                self.send_json(200, payload)
            except OpusMailAdminReaderError as error:
                self.send_json(error.status_code, {"success": False, "error": str(error)})
            except Exception as error:
                self.send_json(500, {"success": False, "error": str(error)})
            return True

        return False


    def handle_outlook_register_api(self, method: str, path: str, query: dict[str, str]) -> bool:
        """Microsoft Outlook/Hotmail pure-protocol mailbox registration console."""
        if method == "GET" and path == "/api/outlook-register/status":
            tail = query.get("tail") or query.get("limit") or "200"
            try:
                self.send_json(200, OUTLOOK_REGISTER_MANAGER.status_payload(tail=int(tail)))
            except Exception as error:
                self.send_json(500, {"ok": False, "error": str(error)})
            return True

        if method == "GET" and path == "/api/outlook-register/logs":
            tail = query.get("tail") or query.get("limit") or "300"
            try:
                self.send_json(200, {"ok": True, "logs": OUTLOOK_REGISTER_MANAGER.get_logs(tail=int(tail)), "state": OUTLOOK_REGISTER_MANAGER.get_state()})
            except Exception as error:
                self.send_json(500, {"ok": False, "error": str(error)})
            return True

        if method == "GET" and path == "/api/outlook-register/accounts":
            limit = query.get("limit") or "100"
            try:
                raw = str(query.get("raw") or "").strip().lower() in {"1", "true", "yes", "on"}
                payload = OUTLOOK_REGISTER_MANAGER.list_accounts(limit=int(limit))
                if raw:
                    payload["raw"] = OUTLOOK_REGISTER_MANAGER.read_accounts_raw()
                self.send_json(200, {"ok": True, **payload})
            except Exception as error:
                self.send_json(500, {"ok": False, "error": str(error)})
            return True

        if method == "POST" and path == "/api/outlook-register/start":
            try:
                body = self.read_json_body() if (self.headers.get("Content-Length") not in (None, "0")) else {}
            except Exception:
                body = {}
            result = OUTLOOK_REGISTER_MANAGER.start(body if isinstance(body, dict) else {})
            if "error" in result:
                self.send_json(409, {"ok": False, **result})
                return True
            self.send_json(200, {"ok": True, **result})
            return True

        if method == "POST" and path == "/api/outlook-register/stop":
            self.send_json(200, {"ok": True, **OUTLOOK_REGISTER_MANAGER.stop()})
            return True

        if method == "POST" and path == "/api/outlook-register/proxies":
            try:
                body = self.read_json_body() if (self.headers.get("Content-Length") not in (None, "0")) else {}
            except Exception:
                body = {}
            content = ""
            if isinstance(body, dict):
                content = str(body.get("proxyText") or body.get("content") or body.get("proxies") or "")
            try:
                count = OUTLOOK_REGISTER_MANAGER.save_proxy_file(content)
                self.send_json(200, {"ok": True, "proxyCount": count, "state": OUTLOOK_REGISTER_MANAGER.get_state()})
            except Exception as error:
                self.send_json(500, {"ok": False, "error": str(error)})
            return True

        return False

    def handle_extension_dispatch(self, method: str, parsed: Any) -> bool:
        """Hand the request to extensions_api (Grok, converters, mail policy)."""
        path = parsed.path
        try:
            import extensions_api
            body: dict[str, Any] = {}
            if method in {"POST", "PUT", "PATCH"}:
                try:
                    body = self.read_json_body()
                except Exception:
                    body = {}
            query = {k: (v[0] if isinstance(v, list) and v else v) for k, v in parse_qs(parsed.query).items()}
            if extensions_api.handle_extension_api(self, method, path, query, body or {}):
                return True
        except (BrokenPipeError, ConnectionResetError):
            return True
        except Exception as error:
            print(f"[API ERROR] extension dispatch {method} {path}: {error}", flush=True)
            self.send_json(500, {"error": f"extension error: {error}"})
            return True
        return False

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise HeroSmsError("请求体不是合法 JSON")

    def send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        try:
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            return

    def send_html(self, status_code: int, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        try:
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            return

    def send_binary(
        self,
        status_code: int,
        data: bytes,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            safe_value = str(value).replace("\r", " ").replace("\n", " ")
            self.send_header(key, safe_value)
        self.send_header("Content-Length", str(len(data)))
        try:
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            return


def main() -> None:
    SUB2API_MONITOR.start()
    try:
        import extensions_api
        extensions_api.CPA_MONITOR.start()
    except Exception as error:
        print(f"CPA monitor start failed: {error}")
    try:
        server = AutomyaiHTTPServer((CONFIG.host, CONFIG.port), AppHandler)
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            raise SystemExit(
                f"automyai cannot bind {CONFIG.host}:{CONFIG.port}: port already in use"
            ) from error
        raise
    print(f"{APP_NAME} listening on http://{CONFIG.host}:{CONFIG.port}")
    with server:
        server.serve_forever()


if __name__ == "__main__":
    main()
