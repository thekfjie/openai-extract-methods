"""Kakao Pay transport used by the Go extraction job runner.

The Go service remains the job/orchestration boundary.  This helper only owns
the HTTP transport for the Kakao route that was independently verified with
``curl_cffi``'s Chrome 136 impersonation.  Credentials and proxies arrive as a
single JSON document on stdin and are never accepted through argv or emitted
in logs.

Most importantly, the helper never adds a hidden payment method.  It stops at
the first Stripe init unless the upstream response itself advertises
``kakao_pay``.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit

try:
    from integrations.oai_fingerprint import (
        UC_CHROMIUM_PRESETS,
        align_fingerprint_locale_to_region,
        generate_entry_fingerprint,
    )
except Exception:  # pragma: no cover - keep transport usable without fingerprint package layout
    try:
        from oai_fingerprint import (  # type: ignore
            UC_CHROMIUM_PRESETS,
            align_fingerprint_locale_to_region,
            generate_entry_fingerprint,
        )
    except Exception:  # pragma: no cover
        UC_CHROMIUM_PRESETS = (
            "windows-10-chrome",
            "windows-11-chrome",
            "macos-intel-chrome",
            "macos-apple-chrome",
        )
        align_fingerprint_locale_to_region = None  # type: ignore
        generate_entry_fingerprint = None  # type: ignore

try:
    from curl_cffi.requests import Session as CurlCffiSession
except ImportError:  # pragma: no cover - exercised through the Go fallback.
    CurlCffiSession = None


MAX_INPUT_BYTES = 2 * 1024 * 1024
KAKAO_MODE_ELIGIBILITY = "eligibility"
KAKAO_MODE_PROVIDER_LINK = "provider_link"
KAKAO_MODES = {KAKAO_MODE_ELIGIBILITY, KAKAO_MODE_PROVIDER_LINK}
STRIPE_VERSION = "2025-03-31.basil; checkout_server_update_beta=v1; checkout_manual_approval_preview=v1"
STRIPE_RUNTIME = "c00af4ce81"
STRIPE_PAYMENT_UA = f"stripe.js/{STRIPE_RUNTIME}; stripe-js-v3/{STRIPE_RUNTIME}; checkout"

# Keep Korea locale/timezone fixed. Only browser/runtime identity is rotated, and
# one selected profile is reused for checkout/promotion/provider in the same run.
KAKAO_LOCALE = "ko-KR"
KAKAO_ACCEPT_LANGUAGE = "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
KAKAO_TIMEZONE = "Asia/Seoul"
KAKAO_ELEMENTS_LOCALE = "ko"

# Prefer currently observed workable Chrome majors first.
# Keep a short exploration tail, but never lead with versions that frequently
# waste full-chain attempts (unsupported / unstable TLS) in this runtime.
KAKAO_IMPERSONATE_CANDIDATES = (
    "chrome131",
    "chrome136",
    "chrome146",
    "chrome145",
    "chrome142",
    "chrome124",
    "chrome120",
)
KAKAO_FP_BROWSER_VERSIONS = (
    "131.0.6778.86",
    "136.0.7103.93",
    "146.0.7680.80",
    "145.0.7632.77",
    "142.0.7444.176",
    "124.0.6367.91",
    "120.0.6099.109",
)
KAKAO_FP_PRESETS = tuple(UC_CHROMIUM_PRESETS) or (
    "windows-10-chrome",
    "windows-11-chrome",
    "macos-intel-chrome",
    "macos-apple-chrome",
)

# Static fallback profiles if the shared fingerprint generator is unavailable.
KAKAO_BROWSER_PROFILES = (
    {
        "id": "chrome131-win",
        "impersonate": "chrome131",
        "ua_version": "131.0.6778.86",
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "platform": "Windows",
        "platform_version": "15.0.0",
        "architecture": "x86",
        "bitness": "64",
        "mobile": False,
        "os_token": "Windows NT 10.0; Win64; x64",
    },
    {
        "id": "chrome136-win",
        "impersonate": "chrome136",
        "ua_version": "136.0.7103.93",
        "sec_ch_ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
        "platform": "Windows",
        "platform_version": "15.0.0",
        "architecture": "x86",
        "bitness": "64",
        "mobile": False,
        "os_token": "Windows NT 10.0; Win64; x64",
    },
    {
        "id": "chrome146-win",
        "impersonate": "chrome146",
        "ua_version": "146.0.7680.80",
        "sec_ch_ua": '"Google Chrome";v="146", "Chromium";v="146", "Not.A/Brand";v="99"',
        "platform": "Windows",
        "platform_version": "15.0.0",
        "architecture": "x86",
        "bitness": "64",
        "mobile": False,
        "os_token": "Windows NT 10.0; Win64; x64",
    },
    {
        "id": "chrome145-win",
        "impersonate": "chrome145",
        "ua_version": "145.0.7632.77",
        "sec_ch_ua": '"Google Chrome";v="145", "Chromium";v="145", "Not.A/Brand";v="99"',
        "platform": "Windows",
        "platform_version": "15.0.0",
        "architecture": "x86",
        "bitness": "64",
        "mobile": False,
        "os_token": "Windows NT 10.0; Win64; x64",
    },
    {
        "id": "chrome131-mac",
        "impersonate": "chrome131",
        "ua_version": "131.0.6778.86",
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "platform": "macOS",
        "platform_version": "14.0.0",
        "architecture": "arm",
        "bitness": "64",
        "mobile": False,
        "os_token": "Macintosh; Intel Mac OS X 10_15_7",
    },
    {
        "id": "chrome136-mac",
        "impersonate": "chrome136",
        "ua_version": "136.0.7103.93",
        "sec_ch_ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
        "platform": "macOS",
        "platform_version": "14.0.0",
        "architecture": "arm",
        "bitness": "64",
        "mobile": False,
        "os_token": "Macintosh; Intel Mac OS X 10_15_7",
    },
)

# Process-wide active profile for one helper invocation / full chain attempt.
_ACTIVE_BROWSER_PROFILE: dict[str, Any] | None = None


def build_user_agent(profile: dict[str, Any]) -> str:
    return (
        f"Mozilla/5.0 ({profile['os_token']}) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{profile['ua_version']} Safari/537.36"
    )


def _chrome_major(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
    if not digits:
        return ""
    return digits.split(".", 1)[0]


def _resolve_impersonate(*candidates: Any) -> str:
    values: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, (list, tuple, set)):
            values.extend(str(item or "").strip() for item in candidate)
        else:
            values.append(str(candidate or "").strip())
    ordered: list[str] = []
    for value in values:
        lower = value.lower()
        if not lower:
            continue
        if lower.startswith("chrome") and lower not in ordered:
            ordered.append(lower)
        major = _chrome_major(lower)
        if major:
            token = f"chrome{major}"
            if token not in ordered:
                ordered.append(token)
    for value in list(ordered) + list(KAKAO_IMPERSONATE_CANDIDATES):
        if value in KAKAO_IMPERSONATE_CANDIDATES or value.startswith("chrome"):
            # Prefer known-good majors first, then any chrome* string.
            if value in KAKAO_IMPERSONATE_CANDIDATES:
                return value
    for value in ordered:
        if value.startswith("chrome"):
            return value
    return "chrome146"


def _os_token_from_user_agent(user_agent: str, platform_name: str) -> str:
    match = re.search(r"\(([^)]+)\)", str(user_agent or ""))
    if match:
        return match.group(1)
    if "mac" in str(platform_name or "").lower():
        return "Macintosh; Intel Mac OS X 10_15_7"
    return "Windows NT 10.0; Win64; x64"


def _sec_ch_ua_from_brands(brands: Any, major: str) -> str:
    values: list[str] = []
    if isinstance(brands, list):
        for item in brands:
            if not isinstance(item, dict):
                continue
            brand = str(item.get("brand") or "").replace('"', "").strip()
            version = str(item.get("version") or major).replace('"', "").strip()
            if brand:
                values.append(f'"{brand}";v="{version}"')
    if values:
        return ", ".join(values)
    return f'"Google Chrome";v="{major}", "Chromium";v="{major}", "Not.A/Brand";v="99"'


def _sec_ch_ua_full_from_brands(brands: Any, full_version: str, major: str) -> str:
    values: list[str] = []
    if isinstance(brands, list):
        for item in brands:
            if not isinstance(item, dict):
                continue
            brand = str(item.get("brand") or "").replace('"', "").strip()
            version = str(item.get("version") or full_version or major).replace('"', "").strip()
            if brand:
                values.append(f'"{brand}";v="{version}"')
    if values:
        return ", ".join(values)
    return (
        f'"Google Chrome";v="{full_version}", "Chromium";v="{full_version}", '
        f'"Not.A/Brand";v="99.0.0.0"'
    )


def profile_from_oai_fingerprint(fingerprint: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(fingerprint, dict):
        return None
    user_agent = str(fingerprint.get("user_agent") or "").strip()
    if not user_agent:
        return None
    profile = fingerprint.get("profile") if isinstance(fingerprint.get("profile"), dict) else {}
    engine = profile.get("engine") if isinstance(profile.get("engine"), dict) else {}
    navigator = profile.get("navigator") if isinstance(profile.get("navigator"), dict) else {}
    operating_system = profile.get("os") if isinstance(profile.get("os"), dict) else {}
    metadata = engine.get("userAgentMetadata") if isinstance(engine.get("userAgentMetadata"), dict) else {}

    ua_version = ""
    match = re.search(r"Chrome/([0-9.]+)", user_agent)
    if match:
        ua_version = match.group(1)
    if not ua_version:
        ua_version = str(engine.get("version") or "146.0.0.0")
    major = _chrome_major(ua_version) or "146"

    platform_name = str(
        metadata.get("platform")
        or operating_system.get("name")
        or fingerprint.get("platform")
        or "Windows"
    ).strip() or "Windows"
    if platform_name.lower() in {"macintel", "macintosh"}:
        platform_name = "macOS"
    if platform_name.lower().startswith("win"):
        platform_name = "Windows"

    architecture = str(metadata.get("architecture") or operating_system.get("architecture") or "x86")
    bitness = str(metadata.get("bitness") or "64")
    platform_version = str(
        metadata.get("platformVersion")
        or operating_system.get("version")
        or ("15.0.0" if platform_name == "Windows" else "14.6.0")
    )
    mobile = bool(metadata.get("mobile") if "mobile" in metadata else navigator.get("mobile") or fingerprint.get("mobile"))
    impersonate = _resolve_impersonate(
        fingerprint.get("impersonate"),
        fingerprint.get("impersonate_candidates"),
        f"chrome{major}",
    )
    brands = metadata.get("brands") or []
    full_brands = metadata.get("fullVersionList") or brands
    sec_ch_ua = str(fingerprint.get("sec_ch_ua") or _sec_ch_ua_from_brands(brands, major))
    sec_ch_full_list = _sec_ch_ua_full_from_brands(full_brands, ua_version, major)
    screen = str(fingerprint.get("screen") or "")
    if not screen:
        width = fingerprint.get("screen_width") or 1920
        height = fingerprint.get("screen_height") or 1080
        screen = f"{width}x{height}"

    return {
        "id": str(fingerprint.get("profile_id") or f"{impersonate}-{platform_name.lower()}"),
        "impersonate": impersonate,
        "ua_version": ua_version,
        "user_agent": user_agent,
        "sec_ch_ua": sec_ch_ua,
        "sec_ch_ua_full_version": f'"{ua_version}"',
        "sec_ch_ua_full_version_list": sec_ch_full_list,
        "platform": platform_name,
        "platform_version": platform_version,
        "architecture": architecture,
        "bitness": bitness,
        "mobile": mobile,
        "os_token": _os_token_from_user_agent(user_agent, platform_name),
        "locale": KAKAO_LOCALE,
        "accept_language": KAKAO_ACCEPT_LANGUAGE,
        "timezone": KAKAO_TIMEZONE,
        "elements_locale": KAKAO_ELEMENTS_LOCALE,
        "screen": screen,
        "hardware_concurrency": fingerprint.get("hardware_concurrency"),
        "device_memory": fingerprint.get("device_memory"),
        "device_name": fingerprint.get("device_name"),
        "webgl_vendor": fingerprint.get("webgl_vendor"),
        "webgl_renderer": fingerprint.get("webgl_renderer"),
        "profile_id": fingerprint.get("profile_id"),
        "preset": fingerprint.get("preset"),
        "source": fingerprint.get("source") or "oai-fingerprint",
        "seed": fingerprint.get("seed"),
        "fingerprint_entry": fingerprint.get("entry") or "uc_signup",
    }


def generate_kakao_oai_profile(seed: str = "", preferred: str = "", attempt: int = 0, weight_mode: bool = False, stage: str = "") -> dict[str, Any] | None:
    """Reuse the shared OAI fingerprint generator under a fixed KR locale/timezone."""
    if generate_entry_fingerprint is None or align_fingerprint_locale_to_region is None:
        return None
    material = f"{str(seed or '').strip()}|{int(attempt or 0)}|{str(preferred or '').strip()}|kakao-kr"
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    rng = random.Random(digest)
    stats = load_fingerprint_stats() if weight_mode else {"profiles": {}}
    preset_weights = []
    for preset_name in KAKAO_FP_PRESETS:
        # Approximate preset identity for mild bias when weight mode is on.
        pseudo = {"id": preset_name, "impersonate": f"chrome{_chrome_major(KAKAO_FP_BROWSER_VERSIONS[0]) or '146'}", "platform": "Windows"}
        preset_weights.append(profile_weight_for_selection(pseudo, stats, stage=stage, weight_mode=weight_mode))
    preset = weighted_choice(list(KAKAO_FP_PRESETS), preset_weights, rng) if KAKAO_FP_PRESETS else "windows-10-chrome"
    version_weights = []
    versions = list(KAKAO_FP_BROWSER_VERSIONS)
    for version in versions:
        major = _chrome_major(version) or "0"
        impersonate = f"chrome{major}"
        if is_bad_impersonate(impersonate):
            version_weights.append(0.02)
            continue
        pseudo = {"id": f"{impersonate}-weighted", "impersonate": impersonate, "platform": "Windows", "ua_version": version}
        version_weights.append(profile_weight_for_selection(pseudo, stats, stage=stage, weight_mode=weight_mode))
    browser_version = weighted_choice(versions, version_weights, rng) if versions else "146.0.7680.80"
    preferred_lower = str(preferred or "").strip().lower()
    if preferred_lower.startswith("chrome"):
        major = _chrome_major(preferred_lower) or _chrome_major(browser_version) or "146"
        # Keep KR locale fixed while still honoring an explicit Chrome major preference.
        for version in KAKAO_FP_BROWSER_VERSIONS:
            if version.startswith(f"{major}."):
                browser_version = version
                break
        else:
            browser_version = f"{major}.0.0.0"
    try:
        fingerprint = generate_entry_fingerprint(
            "uc_signup",
            seed=hashlib.sha256(material.encode("utf-8")).hexdigest(),
            preset=preset,
            browser_version=browser_version,
        )
    except Exception:
        return None
    if not fingerprint:
        return None
    aligned = align_fingerprint_locale_to_region(fingerprint, "KR") or fingerprint
    # Hard-enforce Korea language/timezone even if alignment helper changes later.
    aligned = dict(aligned)
    aligned["lang"] = KAKAO_LOCALE
    aligned["lang_full"] = KAKAO_ACCEPT_LANGUAGE
    aligned["languages"] = ["ko-KR", "ko", "en-US", "en"]
    aligned["timezone"] = KAKAO_TIMEZONE
    profile = aligned.get("profile") if isinstance(aligned.get("profile"), dict) else {}
    profile = dict(profile)
    profile["locale"] = {
        "appLocale": KAKAO_LOCALE,
        "acceptLanguage": KAKAO_ACCEPT_LANGUAGE,
        "timezone": KAKAO_TIMEZONE,
    }
    aligned["profile"] = profile
    headers = aligned.get("http_headers") if isinstance(aligned.get("http_headers"), dict) else {}
    headers = dict(headers)
    headers["Accept-Language"] = KAKAO_ACCEPT_LANGUAGE
    if aligned.get("user_agent"):
        headers["User-Agent"] = str(aligned["user_agent"])
    aligned["http_headers"] = headers
    return profile_from_oai_fingerprint(aligned)


def normalize_browser_profile(profile: dict[str, Any] | None = None) -> dict[str, Any]:
    base = dict(profile or KAKAO_BROWSER_PROFILES[-1])
    ua_version = str(base.get("ua_version") or "147.0.7727.56")
    major = _chrome_major(ua_version) or "147"
    impersonate = _resolve_impersonate(base.get("impersonate"), f"chrome{major}")
    platform = str(base.get("platform") or "Windows")
    if platform.lower().startswith("win"):
        platform = "Windows"
    elif "mac" in platform.lower():
        platform = "macOS"
    mobile = bool(base.get("mobile", False))
    sec_ch_ua = str(
        base.get("sec_ch_ua")
        or f'"Google Chrome";v="{major}", "Chromium";v="{major}", "Not.A/Brand";v="99"'
    )
    normalized = {
        "id": str(base.get("id") or f"{impersonate}-{platform.lower()}"),
        "impersonate": impersonate,
        "ua_version": ua_version,
        "sec_ch_ua": sec_ch_ua,
        "sec_ch_ua_full_version": str(base.get("sec_ch_ua_full_version") or f'"{ua_version}"'),
        "sec_ch_ua_full_version_list": str(
            base.get("sec_ch_ua_full_version_list")
            or sec_ch_ua.replace(f';v="{major}"', f';v="{ua_version}"')
        ),
        "platform": platform,
        "platform_version": str(base.get("platform_version") or "15.0.0"),
        "architecture": str(base.get("architecture") or "x86"),
        "bitness": str(base.get("bitness") or "64"),
        "mobile": mobile,
        "os_token": str(base.get("os_token") or _os_token_from_user_agent(str(base.get("user_agent") or ""), platform)),
        # Korea surfaces stay fixed across every stage of the same chain.
        "locale": KAKAO_LOCALE,
        "accept_language": KAKAO_ACCEPT_LANGUAGE,
        "timezone": KAKAO_TIMEZONE,
        "elements_locale": KAKAO_ELEMENTS_LOCALE,
        "screen": str(base.get("screen") or ""),
        "hardware_concurrency": base.get("hardware_concurrency"),
        "device_memory": base.get("device_memory"),
        "device_name": base.get("device_name"),
        "webgl_vendor": base.get("webgl_vendor"),
        "webgl_renderer": base.get("webgl_renderer"),
        "profile_id": base.get("profile_id"),
        "preset": base.get("preset"),
        "source": base.get("source") or "static-fallback",
        "seed": base.get("seed"),
        "fingerprint_entry": base.get("fingerprint_entry"),
    }
    normalized["user_agent"] = str(base.get("user_agent") or build_user_agent(normalized))
    normalized["sec_ch_ua_mobile"] = "?1" if mobile else "?0"
    normalized["sec_ch_ua_platform"] = f'"{platform}"'
    normalized["sec_ch_ua_platform_version"] = f'"{normalized["platform_version"]}"'
    normalized["sec_ch_ua_arch"] = f'"{normalized["architecture"]}"'
    normalized["sec_ch_ua_bitness"] = f'"{normalized["bitness"]}"'
    normalized["label"] = (
        f"{normalized['impersonate']} / {normalized['platform']} / "
        f"Chrome/{normalized['ua_version']} / {normalized['timezone']}"
    )
    return normalized


def select_static_browser_profile(seed: str = "", preferred: str = "", weight_mode: bool = False, stage: str = "") -> dict[str, Any]:
    preferred_lower = str(preferred or "").strip().lower()
    if preferred_lower:
        for profile in KAKAO_BROWSER_PROFILES:
            hay = " ".join(
                [
                    str(profile.get("id") or "").lower(),
                    str(profile.get("impersonate") or "").lower(),
                    str(profile.get("ua_version") or "").lower(),
                ]
            )
            if preferred_lower in hay:
                return normalize_browser_profile(profile)
            if preferred_lower.startswith("chrome") and preferred_lower == str(profile.get("impersonate") or "").lower():
                return normalize_browser_profile(profile)
        major = _chrome_major(preferred_lower)
        if major:
            return normalize_browser_profile(
                {
                    "id": f"chrome{major}-win",
                    "impersonate": preferred_lower if preferred_lower in KAKAO_IMPERSONATE_CANDIDATES else f"chrome{major}",
                    "ua_version": f"{major}.0.0.0",
                    "platform": "Windows",
                    "os_token": "Windows NT 10.0; Win64; x64",
                }
            )
    digest = hashlib.sha256(f"{seed}|static-kakao".encode("utf-8")).digest()
    rng = random.Random(digest)
    stats = load_fingerprint_stats() if weight_mode else {"profiles": {}}
    profiles = [normalize_browser_profile(item) for item in KAKAO_BROWSER_PROFILES]
    # Drop known-unsupported impersonates from normal rotation.
    usable = [item for item in profiles if not is_bad_impersonate(item.get("impersonate"))]
    if not usable:
        usable = profiles
    weights = [profile_weight_for_selection(item, stats, stage=stage, weight_mode=weight_mode) for item in usable]
    chosen = weighted_choice(usable, weights, rng)
    return normalize_browser_profile(chosen)



def select_browser_profile(seed: str = "", preferred: str = "", attempt: int = 0, weight_mode: bool = False, stage: str = "") -> dict[str, Any]:
    """Pick one browser identity for a full chain; KR locale/timezone never rotate."""
    generated = generate_kakao_oai_profile(
        seed=seed,
        preferred=preferred,
        attempt=attempt,
        weight_mode=weight_mode,
        stage=stage,
    )
    if generated:
        return normalize_browser_profile(generated)
    return select_static_browser_profile(
        seed=f"{seed}|{attempt}",
        preferred=preferred,
        weight_mode=weight_mode,
        stage=stage,
    )


def set_active_browser_profile(profile: dict[str, Any] | None) -> dict[str, Any]:
    global _ACTIVE_BROWSER_PROFILE
    _ACTIVE_BROWSER_PROFILE = normalize_browser_profile(profile)
    return _ACTIVE_BROWSER_PROFILE


def active_browser_profile() -> dict[str, Any]:
    global _ACTIVE_BROWSER_PROFILE
    if _ACTIVE_BROWSER_PROFILE is None:
        _ACTIVE_BROWSER_PROFILE = select_browser_profile(seed="default-kakao-profile")
    return _ACTIVE_BROWSER_PROFILE


FINGERPRINT_STAGES = ("checkout", "promotion", "provider", "approve")


def normalize_fingerprint_mode(value: Any, default: str = "follow") -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    if text in {"fresh", "new", "rotate", "independent", "random"}:
        return "fresh"
    if text in {"follow", "main", "shared", "reuse", "same", "inherit"}:
        return "follow"
    return default


def normalize_fingerprint_policy(raw: Any = None) -> dict[str, str]:
    """Per-stage fingerprint policy.

    - follow: reuse the full-chain main profile (including approve retries)
    - fresh: mint a new browser profile for that stage / retry
    Locale/timezone stay fixed to KR either way.
    """
    policy = {stage: "follow" for stage in FINGERPRINT_STAGES}
    # Checkout is the main identity source for a full chain.
    policy["checkout"] = "main"
    source: dict[str, Any] = {}
    if isinstance(raw, dict):
        source = raw
    elif isinstance(raw, str) and raw.strip():
        # Allow compact forms like "promotion,approve" meaning those stages are fresh.
        for part in re.split(r"[\s,;|]+", raw.strip()):
            stage = part.strip().lower()
            if stage in policy and stage != "checkout":
                policy[stage] = "fresh"
        return policy
    if not source:
        return policy
    for stage in FINGERPRINT_STAGES:
        if stage == "checkout":
            # checkout can be forced fresh on each full attempt via outer selector already;
            # within a chain it remains the main identity.
            continue
        if stage in source:
            policy[stage] = normalize_fingerprint_mode(source.get(stage), "follow")
        elif stage + "Fingerprint" in source:
            policy[stage] = normalize_fingerprint_mode(source.get(stage + "Fingerprint"), "follow")
    # aliases
    for alias, stage in (
        ("promo", "promotion"),
        ("promotionProxy", "promotion"),
        ("payment", "provider"),
        ("stripe", "provider"),
        ("kakao", "provider"),
        ("approval", "approve"),
    ):
        if alias in source and stage in policy:
            policy[stage] = normalize_fingerprint_mode(source.get(alias), policy[stage])
    return policy


def resolve_stage_profile(
    stage: str,
    main_profile: dict[str, Any],
    policy: dict[str, str],
    *,
    seed: str = "",
    preferred: str = "",
    attempt: int = 1,
    retry: int = 1,
    weight_mode: bool = False,
) -> dict[str, Any]:
    mode = normalize_fingerprint_mode(policy.get(stage), "follow")
    if stage == "checkout" or mode != "fresh":
        return normalize_browser_profile(main_profile)
    fresh_seed = "|".join(
        [
            str(seed or ""),
            str(stage),
            str(attempt),
            str(retry),
            str((main_profile or {}).get("id") or ""),
        ]
    )
    return select_browser_profile(
        seed=fresh_seed,
        preferred=preferred,
        attempt=max(1, int(attempt) + int(retry)),
        weight_mode=weight_mode,
        stage=stage,
    )


# ---------------------------------------------------------------------------
# Fingerprint risk ledger + mild success bias
# Recording is always on. Weighted selection only applies when weight mode is
# explicitly enabled by the caller/UI toggle.
# ---------------------------------------------------------------------------

_FINGERPRINT_STATS_LOCK = None
try:
    import threading

    _FINGERPRINT_STATS_LOCK = threading.Lock()
except Exception:  # pragma: no cover
    _FINGERPRINT_STATS_LOCK = None

_UNSUPPORTED_IMPERSONATE = {
    # Hard denylist from live runs: waste attempts or explode TLS frequently.
    "chrome140",
    "chrome147",
}

# Process-local runtime denylist. When an impersonate hits curl/TLS library failures
# during a real request, skip it for later attempts in this helper process.
_RUNTIME_BAD_IMPERSONATE: set[str] = set()


def _impersonate_token(value: Any) -> str:
    return str(value or "").strip().lower()


def is_bad_impersonate(value: Any) -> bool:
    token = _impersonate_token(value)
    return bool(token) and (token in _UNSUPPORTED_IMPERSONATE or token in _RUNTIME_BAD_IMPERSONATE)


def mark_bad_impersonate(value: Any, reason: str = "") -> None:
    token = _impersonate_token(value)
    if not token:
        return
    _RUNTIME_BAD_IMPERSONATE.add(token)
    if reason:
        # Keep this as a soft breadcrumb in stderr-free flow via step logs only when needed.
        pass


def is_transport_tls_error(detail: Any) -> bool:
    text = str(detail or "").lower()
    return (
        "curl: (35)" in text
        or "tls connect error" in text
        or "openssl_internal" in text
        or "invalid library" in text
        or "ssl connect error" in text
        or "error:00000000:invalid library" in text
    )


def is_impersonate_unsupported_error(detail: Any) -> bool:
    text = str(detail or "").lower()
    return "impersonating" in text and "not supported" in text


# Pure curl/OpenSSL library flakiness. Retry the same request/fingerprint without
# burning a full-chain attempt or rotating browser identity.
TLS_SOFT_RETRY_DEFAULT = 8
TLS_SOFT_RETRY_MAX = 30


def tls_soft_retry_limit(request: dict[str, Any] | None = None) -> int:
    raw = None
    if isinstance(request, dict):
        raw = (
            request.get("tlsSoftRetries")
            if request.get("tlsSoftRetries") is not None
            else request.get("tls_soft_retries")
            if request.get("tls_soft_retries") is not None
            else request.get("transportSoftRetries")
        )
    if raw is None:
        raw = os.environ.get("AUTOMYAI_KAKAO_TLS_SOFT_RETRIES") or TLS_SOFT_RETRY_DEFAULT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = TLS_SOFT_RETRY_DEFAULT
    return max(1, min(TLS_SOFT_RETRY_MAX, value))


def call_with_tls_soft_retry(
    operation,
    *,
    stage: str,
    label: str = "",
    retries: int | None = None,
):
    """Retry pure TLS/library failures in-place.

    Does not change fingerprint and does not consume full-chain attempt budget.
    Non-TLS errors are raised immediately.
    """
    limit = tls_soft_retry_limit({"tlsSoftRetries": retries} if retries is not None else None)
    last_error: Exception | None = None
    for index in range(1, limit + 1):
        try:
            return operation()
        except Exception as error:  # noqa: BLE001 - transport boundary
            if is_impersonate_unsupported_error(error):
                raise
            if not is_transport_tls_error(error):
                raise
            last_error = error
            if index >= limit:
                break
            detail = redact_text(error)
            prefix = f"{label} " if label else ""
            emit_step(
                stage,
                "retrying",
                f"{prefix}TLS 传输异常，同指纹软重试 {index}/{limit - 1}（不换指纹、不消耗完整链路次数）：{detail}",
            )
            time.sleep(min(2.0, 0.35 * index))
    assert last_error is not None
    raise last_error


def fingerprint_stats_path() -> str:
    configured = str(os.environ.get("AUTOMYAI_FINGERPRINT_STATS_PATH") or "").strip()
    if configured:
        return configured
    for candidate in (
        "/app/data/extract-api/fingerprint-stats.json",
        "/opt/automyai/data/extract-api/fingerprint-stats.json",
        str(Path(__file__).resolve().parents[1] / "data" / "extract-api" / "fingerprint-stats.json"),
    ):
        parent = str(Path(candidate).parent)
        if parent and (os.path.isdir(parent) or parent.startswith("/tmp")):
            return candidate
    return "/tmp/automyai-fingerprint-stats.json"


def _fingerprint_identity(profile: dict[str, Any] | None = None) -> dict[str, str]:
    current = normalize_browser_profile(profile or active_browser_profile())
    profile_id = str(current.get("id") or current.get("profile_id") or "").strip() or "unknown"
    impersonate = str(current.get("impersonate") or "").strip().lower() or "unknown"
    platform = str(current.get("platform") or current.get("browserPlatform") or "").strip() or "unknown"
    major = _chrome_major(current.get("ua_version") or impersonate) or "0"
    return {
        "key": f"{profile_id}|{impersonate}|{platform}|{major}",
        "profileId": profile_id,
        "impersonate": impersonate,
        "platform": platform,
        "browserMajor": major,
    }


def _empty_fingerprint_stats() -> dict[str, Any]:
    return {
        "version": 1,
        "updatedAt": "",
        "totalEvents": 0,
        "profiles": {},
    }


def load_fingerprint_stats() -> dict[str, Any]:
    path = fingerprint_stats_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            return _empty_fingerprint_stats()
        profiles = raw.get("profiles")
        if not isinstance(profiles, dict):
            raw["profiles"] = {}
        raw.setdefault("version", 1)
        raw.setdefault("totalEvents", 0)
        raw.setdefault("updatedAt", "")
        return raw
    except FileNotFoundError:
        return _empty_fingerprint_stats()
    except Exception:
        return _empty_fingerprint_stats()


def _write_fingerprint_stats(stats: dict[str, Any]) -> None:
    path = fingerprint_stats_path()
    parent = Path(path).parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(stats, handle, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def classify_fingerprint_outcome(stage: str, status: str = "", detail: str = "", result: str = "") -> str:
    stage_l = str(stage or "").strip().lower()
    status_l = str(status or "").strip().lower()
    detail_l = str(detail or "").strip().lower()
    result_l = str(result or "").strip().lower()
    blob = f"{status_l} {detail_l} {result_l}"

    if "impersonating" in blob and "not supported" in blob:
        return "unsupported"
    if "tls connect error" in blob or "curl: (35)" in blob or "openssl_internal" in blob:
        return "tls_error"
    if "curl: (56)" in blob or "connect tunnel failed" in blob or "proxy" in blob and "403" in blob:
        return "proxy_error"
    if result_l == "blocked" or "result=blocked" in blob or "approve_blocked" in blob or "approval_blocked" in blob:
        return "blocked"
    if result_l == "approved" or "result=approved" in blob:
        return "approved"
    if status_l in {"success", "succeeded"} and (
        "provider_link_ready" in blob
        or "nicepay" in blob
        or "full_attempt" in stage_l and "成功" in str(detail or "")
        or stage_l.endswith("redirect") and "success" in status_l
    ):
        return "success"
    if status_l in {"success", "succeeded"} and stage_l in {"chatgpt.approve", "kakao.full_attempt", "kakao.provider_redirect", "stripe.redirect_poll"}:
        if stage_l == "chatgpt.approve":
            return "approved"
        if "redirect" in stage_l or stage_l == "kakao.full_attempt":
            return "success"
    if status_l in {"failed", "error"}:
        return "failed"
    if status_l in {"retrying", "warning"}:
        return "retry"
    return "other"


def record_fingerprint_outcome(
    profile: dict[str, Any] | None,
    *,
    stage: str,
    outcome: str = "",
    status: str = "",
    detail: str = "",
    result: str = "",
    weight_mode: bool | None = None,
) -> dict[str, Any]:
    """Always persist fingerprint risk outcomes. Selection bias is separate."""
    identity = _fingerprint_identity(profile)
    label = str(outcome or classify_fingerprint_outcome(stage, status=status, detail=detail, result=result) or "other").strip().lower()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    event = {
        "at": now,
        "stage": str(stage or "")[:80],
        "outcome": label[:60],
        "status": str(status or "")[:40],
        "detail": redact_text(detail)[:220],
        "profileId": identity["profileId"],
        "impersonate": identity["impersonate"],
        "platform": identity["platform"],
        "browserMajor": identity["browserMajor"],
        "weightMode": bool(weight_mode) if weight_mode is not None else None,
    }

    def _mutate() -> dict[str, Any]:
        stats = load_fingerprint_stats()
        profiles = stats.setdefault("profiles", {})
        row = profiles.get(identity["key"])
        if not isinstance(row, dict):
            row = {
                "profileId": identity["profileId"],
                "impersonate": identity["impersonate"],
                "platform": identity["platform"],
                "browserMajor": identity["browserMajor"],
                "events": 0,
                "outcomes": {},
                "stages": {},
                "firstSeenAt": now,
                "lastSeenAt": now,
                "lastOutcome": "",
                "recent": [],
            }
        outcomes = row.setdefault("outcomes", {})
        stages = row.setdefault("stages", {})
        stage_row = stages.get(str(stage or "unknown"))
        if not isinstance(stage_row, dict):
            stage_row = {}
        outcomes[label] = int(outcomes.get(label) or 0) + 1
        stage_row[label] = int(stage_row.get(label) or 0) + 1
        stages[str(stage or "unknown")] = stage_row
        row["events"] = int(row.get("events") or 0) + 1
        row["lastSeenAt"] = now
        row["lastOutcome"] = label
        row["profileId"] = identity["profileId"]
        row["impersonate"] = identity["impersonate"]
        row["platform"] = identity["platform"]
        row["browserMajor"] = identity["browserMajor"]
        recent = row.get("recent")
        if not isinstance(recent, list):
            recent = []
        recent.append(event)
        row["recent"] = recent[-30:]
        profiles[identity["key"]] = row
        stats["profiles"] = profiles
        stats["totalEvents"] = int(stats.get("totalEvents") or 0) + 1
        stats["updatedAt"] = now
        _write_fingerprint_stats(stats)
        return event

    try:
        if _FINGERPRINT_STATS_LOCK is not None:
            with _FINGERPRINT_STATS_LOCK:
                return _mutate()
        return _mutate()
    except Exception:
        return event


def fingerprint_success_score(row: dict[str, Any] | None, stage: str = "") -> float:
    """Mild Bayesian-ish score in roughly 0.15..0.85. Never extreme."""
    if not isinstance(row, dict):
        return 0.5
    outcomes = row.get("outcomes") if isinstance(row.get("outcomes"), dict) else {}
    stage_rows = row.get("stages") if isinstance(row.get("stages"), dict) else {}
    stage_outcomes = stage_rows.get(stage) if stage and isinstance(stage_rows.get(stage), dict) else {}

    def _count(src: dict[str, Any], *names: str) -> float:
        total = 0.0
        for name in names:
            try:
                total += float(src.get(name) or 0)
            except (TypeError, ValueError):
                pass
        return total

    # Prefer stage-local evidence when present, otherwise global.
    src = stage_outcomes if sum(float(v or 0) for v in stage_outcomes.values()) >= 3 else outcomes
    success = _count(src, "approved", "success")
    blocked = _count(src, "blocked")
    hard_fail = _count(src, "tls_error", "proxy_error", "unsupported", "failed")
    other = _count(src, "retry", "other")
    # Pseudo-counts keep the prior near neutral and avoid overfit on tiny samples.
    alpha_s, alpha_f = 2.0, 2.0
    good = success + alpha_s
    bad = blocked * 1.25 + hard_fail + other * 0.25 + alpha_f
    score = good / max(good + bad, 1.0)
    # Clamp hard so weight mode only nudges, never monopolizes.
    return max(0.18, min(0.82, score))


def weighted_choice(items: list[Any], weights: list[float], rng: random.Random) -> Any:
    if not items:
        raise ValueError("items required")
    if len(items) == 1:
        return items[0]
    cleaned: list[float] = []
    for weight in weights:
        try:
            value = float(weight)
        except (TypeError, ValueError):
            value = 0.0
        cleaned.append(max(0.05, value))
    total = sum(cleaned)
    pick = rng.random() * total
    upto = 0.0
    for item, weight in zip(items, cleaned):
        upto += weight
        if pick <= upto:
            return item
    return items[-1]


def profile_weight_for_selection(
    profile: dict[str, Any],
    stats: dict[str, Any],
    *,
    stage: str = "",
    weight_mode: bool = False,
) -> float:
    identity = _fingerprint_identity(profile)
    impersonate = identity["impersonate"]
    if is_bad_impersonate(impersonate):
        return 0.02
    # Base exploration weight.
    weight = 1.0
    if not weight_mode:
        return weight
    profiles = stats.get("profiles") if isinstance(stats, dict) else {}
    row = profiles.get(identity["key"]) if isinstance(profiles, dict) else None
    score = fingerprint_success_score(row if isinstance(row, dict) else None, stage=stage)
    # Slight bias only: map 0.18..0.82 -> about 0.78..1.28
    weight *= 0.78 + score * 0.60
    # Keep unknown/new profiles competitive.
    events = int((row or {}).get("events") or 0) if isinstance(row, dict) else 0
    if events < 3:
        weight *= 1.05
    return max(0.05, weight)


def active_user_agent() -> str:
    return str(active_browser_profile().get("user_agent") or "")


def browser_client_hints(profile: dict[str, Any] | None = None) -> dict[str, str]:
    current = normalize_browser_profile(profile or active_browser_profile())
    return {
        "sec-ch-ua": str(current["sec_ch_ua"]),
        "sec-ch-ua-mobile": str(current["sec_ch_ua_mobile"]),
        "sec-ch-ua-platform": str(current["sec_ch_ua_platform"]),
        "sec-ch-ua-platform-version": str(current["sec_ch_ua_platform_version"]),
        "sec-ch-ua-arch": str(current["sec_ch_ua_arch"]),
        "sec-ch-ua-bitness": str(current["sec_ch_ua_bitness"]),
        "sec-ch-ua-full-version": str(current["sec_ch_ua_full_version"]),
        "sec-ch-ua-full-version-list": str(current["sec_ch_ua_full_version_list"]),
    }


def browser_profile_metadata(profile: dict[str, Any] | None = None) -> dict[str, Any]:
    current = normalize_browser_profile(profile or active_browser_profile())
    return {
        "transport": "curl_cffi",
        "impersonate": current.get("impersonate"),
        "userAgent": current.get("user_agent"),
        "browserProfile": current.get("id"),
        "browserPlatform": current.get("platform"),
        "browserVersion": current.get("ua_version"),
        "locale": current.get("locale"),
        "acceptLanguage": current.get("accept_language"),
        "timezone": current.get("timezone"),
        "fingerprintSource": current.get("source"),
        "fingerprintPreset": current.get("preset"),
        "fingerprintProfileId": current.get("profile_id") or current.get("id"),
        "screen": current.get("screen"),
        "deviceName": current.get("device_name"),
        "webglVendor": current.get("webgl_vendor"),
        "webglRenderer": current.get("webgl_renderer"),
    }


CHECKOUT_ID_RE = re.compile(r"cs_(?:live|test)_[A-Za-z0-9]+")
PUBLISHABLE_KEY_RE = re.compile(r"pk_(?:live|test)_[A-Za-z0-9]+")

KOREAN_FAMILY_NAMES = ("김", "이", "박", "최", "정", "강", "조", "윤", "장", "임")
KOREAN_GIVEN_NAMES = (
    "민준",
    "서준",
    "도윤",
    "예준",
    "시우",
    "주원",
    "하준",
    "지호",
    "서연",
    "서윤",
    "지우",
    "서현",
)
SEOUL_ADDRESSES = (
    {"district": "강남구", "road": "테헤란로", "postal": "06164", "base": 87, "span": 40},
    {"district": "강남구", "road": "봉은사로", "postal": "06097", "base": 524, "span": 32},
    {"district": "서초구", "road": "서초대로", "postal": "06611", "base": 396, "span": 36},
    {"district": "송파구", "road": "올림픽로", "postal": "05510", "base": 300, "span": 36},
    {"district": "마포구", "road": "월드컵북로", "postal": "03925", "base": 396, "span": 36},
)
EMAIL_DOMAINS = ("gmail.com", "naver.com", "daum.net", "kakao.com")

_REDACTION_VALUES: set[str] = set()


class FlowError(RuntimeError):
    """Expected upstream/protocol failure."""


class KakaoNotAdvertised(FlowError):
    """The checkout was observed successfully but did not expose Kakao Pay."""


class TransportUnavailable(RuntimeError):
    """The verified curl_cffi transport cannot run in this environment."""


def request_mode(payload: dict[str, Any]) -> str:
    raw = str(payload.get("mode") or "").strip().lower().replace("-", "_")
    if not raw and "eligibilityOnly" in payload:
        raw = KAKAO_MODE_ELIGIBILITY if bool(payload.get("eligibilityOnly")) else KAKAO_MODE_PROVIDER_LINK
    mode = raw or KAKAO_MODE_ELIGIBILITY
    if mode not in KAKAO_MODES:
        raise FlowError("mode must be eligibility or provider_link")
    return mode


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def emit_step(stage: str, status: str, detail: str = "") -> None:
    emit(
        {
            "type": "step",
            "stage": str(stage or "")[:120],
            "status": str(status or "")[:40],
            "detail": redact_text(detail)[:900],
        }
    )


def register_secret(value: Any) -> None:
    text = str(value or "").strip()
    if not text:
        return
    _REDACTION_VALUES.add(text)
    try:
        decoded = unquote(text)
        _REDACTION_VALUES.add(decoded)
        parsed = urlsplit(decoded)
        for candidate in (
            parsed.netloc,
            parsed.username,
            parsed.password,
            f"{parsed.hostname}:{parsed.port}" if parsed.hostname and parsed.port else parsed.hostname,
        ):
            if candidate:
                _REDACTION_VALUES.add(str(candidate))
    except (TypeError, ValueError):
        pass


def redact_text(value: Any) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    for secret in sorted(_REDACTION_VALUES, key=len, reverse=True):
        if secret:
            text = text.replace(secret, "[redacted]")
    return " ".join(text.split())


def response_error(response: Any, limit: int = 800) -> str:
    try:
        return redact_text(response.text)[:limit]
    except Exception:
        return ""


def response_json(response: Any, stage: str) -> dict[str, Any]:
    try:
        payload = response.json() or {}
    except (TypeError, ValueError) as error:
        raise FlowError(f"{stage} returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise FlowError(f"{stage} returned a non-object JSON payload")
    return payload


def deep_string(payload: Any, *keys: str) -> str:
    wanted = {key.lower() for key in keys}
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in wanted and isinstance(value, (str, int, float)):
                candidate = str(value).strip()
                if candidate:
                    return candidate
        for value in payload.values():
            candidate = deep_string(value, *keys)
            if candidate:
                return candidate
    elif isinstance(payload, list):
        for value in payload:
            candidate = deep_string(value, *keys)
            if candidate:
                return candidate
    return ""


def checkout_id_from(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    match = CHECKOUT_ID_RE.search(encoded)
    if match:
        return match.group(0)
    return deep_string(payload, "checkout_session_id", "session_id", "checkout_id")


def publishable_key_from(payload: dict[str, Any]) -> str:
    candidate = deep_string(
        payload,
        "publishable_key",
        "stripe_publishable_key",
        "publishableKey",
        "stripePublishableKey",
    )
    match = PUBLISHABLE_KEY_RE.search(candidate)
    return match.group(0) if match else candidate


def payment_methods(payload: Any) -> list[str]:
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key.lower() in {"payment_method_types", "ordered_payment_method_types"} and isinstance(child, list):
                    found.update(str(item).strip().lower() for item in child if str(item).strip())
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return sorted(found)


def expected_amount(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    options = payload.get("elements_options")
    if isinstance(options, dict) and options.get("amount") is not None:
        return str(int(options["amount"]))
    summary = payload.get("total_summary")
    if isinstance(summary, dict) and summary.get("due") is not None:
        return str(int(summary["due"]))
    invoice = payload.get("invoice")
    if isinstance(invoice, dict):
        for name in ("amount_due", "total"):
            if invoice.get(name) is not None:
                return str(int(invoice[name]))
    for name in ("amount_total", "amount", "due"):
        if payload.get(name) is not None and isinstance(payload.get(name), (int, float, str)):
            try:
                return str(int(float(payload[name])))
            except (TypeError, ValueError):
                pass
    for child in payload.values():
        amount = expected_amount(child)
        if amount:
            return amount
    return ""


def extract_redirect(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    action = payload.get("next_action")
    if isinstance(action, dict) and action.get("type") == "redirect_to_url":
        redirect = action.get("redirect_to_url")
        if isinstance(redirect, dict) and redirect.get("url"):
            return str(redirect["url"])
    for name in ("setup_intent", "payment_intent"):
        redirect = extract_redirect(payload.get(name))
        if redirect:
            return redirect
    return ""


def random_kakao_billing(token: str) -> dict[str, str]:
    seed = hashlib.sha256(f"{token}:{uuid.uuid4()}".encode()).digest()
    rng = random.Random(seed)
    address = rng.choice(SEOUL_ADDRESSES)
    name = f"{rng.choice(KOREAN_FAMILY_NAMES)}{rng.choice(KOREAN_GIVEN_NAMES)}"
    local_name = hashlib.sha256(name.encode()).hexdigest()[:10]
    return {
        "name": name,
        "email": f"{local_name}@{rng.choice(EMAIL_DOMAINS)}",
        "line1": f"{address['road']} {address['base'] + rng.randrange(address['span'])}",
        "line2": "",
        "city": "서울특별시",
        "state": str(address["district"]),
        "postal_code": str(address["postal"]),
        "country": "KR",
    }


def new_session(proxy: str, profile: dict[str, Any] | None = None) -> Any:
    if CurlCffiSession is None:
        raise TransportUnavailable("curl_cffi is not installed")
    current = normalize_browser_profile(profile or active_browser_profile())
    # Keep one browser identity across checkout/promotion/provider for this chain.
    set_active_browser_profile(current)
    impersonate_try = list(
        dict.fromkeys(
            [
                str(current.get("impersonate") or ""),
                *list(KAKAO_IMPERSONATE_CANDIDATES),
            ]
        )
    )
    impersonate_try = [item for item in impersonate_try if item and not is_bad_impersonate(item)]
    if not impersonate_try:
        impersonate_try = [item for item in KAKAO_IMPERSONATE_CANDIDATES if not is_bad_impersonate(item)] or list(KAKAO_IMPERSONATE_CANDIDATES)
    session = None
    last_error: Exception | None = None
    for impersonate in impersonate_try:
        if not impersonate:
            continue
        try:
            session = CurlCffiSession(impersonate=impersonate)
            current["impersonate"] = impersonate
            set_active_browser_profile(current)
            break
        except Exception as error:  # pragma: no cover - depends on local curl_cffi build
            last_error = error
            mark_bad_impersonate(impersonate, str(error))
            record_fingerprint_outcome(
                {**current, "impersonate": impersonate},
                stage="fingerprint.impersonate",
                outcome="unsupported" if is_impersonate_unsupported_error(error) else "tls_error",
                status="failed",
                detail=str(error),
            )
            session = None
    if session is None:
        raise TransportUnavailable(f"curl_cffi impersonate unavailable: {last_error}") from last_error
    if hasattr(session, "trust_env"):
        session.trust_env = False
    session.proxies = {"http": proxy, "https": proxy}
    try:
        session.headers.update(
            {
                "User-Agent": str(current["user_agent"]),
                "Accept-Language": KAKAO_ACCEPT_LANGUAGE,
                **browser_client_hints(current),
            }
        )
    except Exception:
        pass
    return session


def stripe_headers(publishable_key: str, referer: str) -> dict[str, str]:
    origin = "https://checkout.stripe.com" if "checkout.stripe.com" in referer else "https://pay.openai.com"
    headers = {
        "Authorization": f"Bearer {publishable_key}",
        "Origin": origin,
        "Referer": referer,
        "Accept": "application/json",
        "Accept-Language": KAKAO_ACCEPT_LANGUAGE,
        "Sec-Fetch-Site": "same-site" if origin == "https://checkout.stripe.com" else "cross-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": active_user_agent(),
    }
    headers.update(browser_client_hints())
    return headers


def elements_params(stripe_js_id: str, session_id: str = "") -> dict[str, str]:
    params = {
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[stripe_js_id]": stripe_js_id,
        "elements_session_client[locale]": KAKAO_ELEMENTS_LOCALE,
        "elements_session_client[is_aggregation_expected]": "false",
        "elements_options_client[saved_payment_method][enable_save]": "auto",
        "elements_options_client[saved_payment_method][enable_redisplay]": "auto",
    }
    if session_id:
        params["elements_session_client[session_id]"] = session_id
    return params



# ---------------------------------------------------------------------------
# OpenAI Sentinel: main token (SEN) + session-observer token (SO)
# Used on ChatGPT payments/checkout/approve. Pure-Python PoW path; SO only
# when the challenge marks it required and a lightweight proof is available.
# ---------------------------------------------------------------------------

SENTINEL_REQ_URL = "https://sentinel.openai.com/backend-api/sentinel/req"
SENTINEL_REFERER = "https://sentinel.openai.com/backend-api/sentinel/frame.html"
SENTINEL_SDK_URL = "https://sentinel.openai.com/sentinel/20260124ceb8/sdk.js"
SENTINEL_DEFAULT_FLOW = "authorize_continue"
_ACTIVE_OAI_DEVICE_ID = ""


def active_oai_device_id(seed: str = "") -> str:
    global _ACTIVE_OAI_DEVICE_ID
    if _ACTIVE_OAI_DEVICE_ID:
        return _ACTIVE_OAI_DEVICE_ID
    material = str(seed or "").strip() or str(uuid.uuid4())
    _ACTIVE_OAI_DEVICE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, f"automyai-kakao-device:{material}"))
    return _ACTIVE_OAI_DEVICE_ID


def set_active_oai_device_id(device_id: str) -> str:
    global _ACTIVE_OAI_DEVICE_ID
    token = str(device_id or "").strip()
    if token:
        _ACTIVE_OAI_DEVICE_ID = token
    return active_oai_device_id()


def _fnv1a_32(text: str) -> str:
    value = 2166136261
    for char in str(text or ""):
        value ^= ord(char)
        value = (value * 16777619) & 0xFFFFFFFF
    value ^= value >> 16
    value = (value * 2246822507) & 0xFFFFFFFF
    value ^= value >> 13
    value = (value * 3266489909) & 0xFFFFFFFF
    value ^= value >> 16
    return format(value & 0xFFFFFFFF, "08x")


def _sentinel_b64(data: Any) -> str:
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return __import__("base64").b64encode(raw).decode("ascii")


def _sentinel_config(sid: str, user_agent: str = "") -> list[Any]:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)")
    perf_now = random.uniform(1000, 50000)
    time_origin = time.time() * 1000 - perf_now
    nav_prop = random.choice(
        [
            "vendorSub",
            "productSub",
            "vendor",
            "maxTouchPoints",
            "hardwareConcurrency",
            "cookieEnabled",
            "languages",
            "platform",
        ]
    )
    return [
        "1920x1080",
        date_str,
        4294705152,
        random.random(),
        str(user_agent or active_user_agent()),
        SENTINEL_SDK_URL,
        None,
        None,
        KAKAO_LOCALE,
        KAKAO_ACCEPT_LANGUAGE,
        random.random(),
        f"{nav_prop}−undefined",
        random.choice(["location", "implementation", "URL", "documentURI", "compatMode"]),
        random.choice(["Object", "Function", "Array", "Number", "parseFloat", "undefined"]),
        perf_now,
        str(sid or uuid.uuid4()),
        "",
        random.choice([4, 8, 12, 16]),
        time_origin,
    ]


def generate_requirements_token(sid: str = "", user_agent: str = "") -> str:
    config = _sentinel_config(sid or str(uuid.uuid4()), user_agent=user_agent)
    config[3] = 1
    config[9] = round(random.uniform(5, 50))
    return "gAAAAAC" + _sentinel_b64(config)


def solve_sentinel_pow(seed: str, difficulty: str, sid: str = "", user_agent: str = "", max_attempts: int = 200000) -> str:
    seed = str(seed or "")
    difficulty = str(difficulty or "0")
    config = _sentinel_config(sid or str(uuid.uuid4()), user_agent=user_agent)
    started = time.time()
    for nonce in range(max(1, int(max_attempts))):
        config[3] = nonce
        config[9] = round((time.time() - started) * 1000)
        encoded = _sentinel_b64(config)
        digest = _fnv1a_32(seed + encoded)
        if digest[: len(difficulty)] <= difficulty:
            return "gAAAAAB" + encoded + "~S"
    return "gAAAAAB" + _sentinel_b64("e")


def fetch_sentinel_challenge(
    session: Any,
    *,
    device_id: str,
    flow: str,
    requirements_token: str,
    timeout: int = 20,
) -> dict[str, Any]:
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Accept": "*/*",
        "Origin": "https://sentinel.openai.com",
        "Referer": SENTINEL_REFERER,
        "User-Agent": active_user_agent(),
        "Accept-Language": KAKAO_ACCEPT_LANGUAGE,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        **browser_client_hints(),
    }
    body = json.dumps({"p": requirements_token, "id": device_id, "flow": flow}, separators=(",", ":"))
    response = session.post(SENTINEL_REQ_URL, data=body, headers=headers, timeout=timeout)
    if int(getattr(response, "status_code", 0) or 0) != 200:
        raise FlowError(f"sentinel/req HTTP {getattr(response, 'status_code', '?')}")
    try:
        payload = response.json()
    except Exception as error:  # noqa: BLE001
        raise FlowError(f"sentinel/req invalid JSON: {redact_text(error)}") from error
    if not isinstance(payload, dict):
        raise FlowError("sentinel/req returned non-object JSON")
    return payload


def build_sentinel_pair(
    session: Any,
    *,
    device_id: str = "",
    flow: str = "",
    timeout: int = 20,
) -> dict[str, Any]:
    """Build main (SEN) + optional SO tokens for ChatGPT protected calls.

    Returns:
      {
        "deviceId": ...,
        "flow": ...,
        "mainHeader": raw JSON string for openai-sentinel-token,
        "soHeader": raw JSON string for openai-sentinel-so-token (optional),
        "main": dict,
        "so": dict | None,
        "challengeKeys": [...],
      }
    """
    sid = str(uuid.uuid4())
    device = set_active_oai_device_id(device_id or active_oai_device_id())
    flow_name = str(flow or os.environ.get("AUTOMYAI_KAKAO_SENTINEL_FLOW") or SENTINEL_DEFAULT_FLOW).strip() or SENTINEL_DEFAULT_FLOW
    requirements = generate_requirements_token(sid=sid, user_agent=active_user_agent())
    challenge = fetch_sentinel_challenge(
        session,
        device_id=device,
        flow=flow_name,
        requirements_token=requirements,
        timeout=timeout,
    )
    c_token = str(challenge.get("token") or "").strip()
    if not c_token:
        raise FlowError("sentinel challenge missing token/c")
    pow_info = challenge.get("proofofwork") if isinstance(challenge.get("proofofwork"), dict) else {}
    if pow_info.get("required") and pow_info.get("seed"):
        p_token = solve_sentinel_pow(
            str(pow_info.get("seed") or ""),
            str(pow_info.get("difficulty") or "0"),
            sid=sid,
            user_agent=active_user_agent(),
        )
    else:
        p_token = requirements
    main = {
        "p": p_token,
        "t": "",
        "c": c_token,
        "id": device,
        "flow": flow_name,
    }
    so_payload: dict[str, Any] | None = None
    so_info = challenge.get("so") if isinstance(challenge.get("so"), dict) else {}
    # Full SO collector VM is optional. When the server marks SO required but we
    # cannot execute collector_dx, still submit the main token and continue.
    if so_info.get("required"):
        # Soft SO stub: reuse requirements proof as observer material only when
        # collector is not mandatory-executable in this helper runtime.
        collector = str(so_info.get("collector_dx") or "").strip()
        if collector:
            so_payload = {
                "c": c_token,
                "id": device,
                "flow": flow_name,
                # Keep structure expected by openai-sentinel-so-token consumers.
                # Real collector execution needs Node VM; leave so empty-ish marker
                # only when challenge did not demand a binary proof blob.
                "so": requirements,
            }
    return {
        "deviceId": device,
        "flow": flow_name,
        "main": main,
        "so": so_payload,
        "mainHeader": json.dumps(main, separators=(",", ":")),
        "soHeader": json.dumps(so_payload, separators=(",", ":")) if so_payload else "",
        "challengeKeys": sorted(str(key) for key in challenge.keys()),
        "soRequired": bool(so_info.get("required")),
    }


def attach_sentinel_headers(headers: dict[str, str], pair: dict[str, Any] | None) -> dict[str, str]:
    result = dict(headers or {})
    if not isinstance(pair, dict):
        return result
    main_header = str(pair.get("mainHeader") or "").strip()
    so_header = str(pair.get("soHeader") or "").strip()
    device_id = str(pair.get("deviceId") or "").strip()
    if main_header:
        result["openai-sentinel-token"] = main_header
    if so_header:
        result["openai-sentinel-so-token"] = so_header
    if device_id:
        result["oai-device-id"] = device_id
    return result


def maybe_generate_sentinel_pair(
    session: Any,
    *,
    stage: str,
    device_id: str = "",
    flow: str = "",
    timeout: int = 20,
) -> dict[str, Any] | None:
    enabled = str(os.environ.get("AUTOMYAI_KAKAO_SENTINEL", "1")).strip().lower() not in {"0", "false", "no", "off"}
    if not enabled:
        emit_step(stage, "warning", "Sentinel SEN+SO 已禁用（AUTOMYAI_KAKAO_SENTINEL=0）")
        return None
    try:
        pair = build_sentinel_pair(session, device_id=device_id, flow=flow, timeout=timeout)
    except Exception as error:  # noqa: BLE001
        emit_step(stage, "warning", f"Sentinel SEN+SO 生成失败，继续无令牌请求：{redact_text(error)}")
        return None
    so_state = "with SO" if pair.get("soHeader") else ("SO not required" if not pair.get("soRequired") else "SO required but collector skipped")
    emit_step(
        stage,
        "success",
        f"SEN+SO 已生成；flow={pair.get('flow')}; device={str(pair.get('deviceId') or '')[:8]}…；{so_state}",
    )
    return pair


def checkout_page_url(checkout_id: str, processor_entity: str) -> str:
    return f"https://chatgpt.com/checkout/{processor_entity}/{checkout_id}"


def checkout_api_headers(
    token: str,
    referer: str,
    target_path: str,
    *,
    sentinel_pair: dict[str, Any] | None = None,
    device_id: str = "",
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "oai-language": KAKAO_LOCALE,
        "User-Agent": active_user_agent(),
        "Accept-Language": KAKAO_ACCEPT_LANGUAGE,
        "Referer": referer,
    }
    headers.update(browser_client_hints())
    device = str(device_id or (sentinel_pair or {}).get("deviceId") or active_oai_device_id() or "").strip()
    if device:
        headers["oai-device-id"] = device
    if target_path:
        headers["x-openai-target-path"] = target_path
        headers["x-openai-target-route"] = target_path
    return attach_sentinel_headers(headers, sentinel_pair)


def create_checkout(
    session: Any,
    token: str,
    timeout: int,
    include_promo: bool,
    promo_id: str,
    variant_label: str,
) -> tuple[str, str, str, dict[str, Any]]:
    stage = "chatgpt.checkout"
    emit_step(stage, "running", f"创建 KR / KRW checkout；{variant_label}")
    payload: dict[str, Any] = {
        "plan_name": "chatgptplusplan",
        "billing_details": {"country": "KR", "currency": "KRW"},
        "cancel_url": "https://chatgpt.com/#pricing",
        "checkout_ui_mode": "custom",
    }
    if include_promo and promo_id:
        payload["promo_campaign"] = {
            "promo_campaign_id": promo_id,
            "is_coupon_from_query_param": False,
        }
    try:
        response = session.post(
            "https://chatgpt.com/backend-api/payments/checkout",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "oai-language": KAKAO_LOCALE,
                "User-Agent": active_user_agent(),
            },
            json=payload,
            timeout=timeout,
        )
    except Exception as error:
        emit_step(stage, "failed", str(error))
        raise FlowError(f"checkout request failed: {redact_text(error)}") from error
    if response.status_code != 200:
        detail = f"HTTP {response.status_code}: {response_error(response)}"
        if is_already_paid_error(detail) or is_already_paid_error(response_error(response)):
            emit_step(stage, "failed", f"账号已是付费状态，停止后续流程：{detail}")
            raise FlowError(f"already_paid: checkout failed {detail}")
        emit_step(stage, "failed", detail)
        raise FlowError(f"checkout failed {detail}")
    checkout = response_json(response, stage)
    checkout_id = checkout_id_from(checkout)
    publishable_key = publishable_key_from(checkout)
    processor_entity = deep_string(checkout, "processor_entity", "processorEntity") or "openai_llc"
    if not checkout_id.startswith("cs_") or not publishable_key.startswith("pk_"):
        keys = ",".join(sorted(checkout.keys()))
        emit_step(stage, "failed", f"checkout 缺少 Stripe cs/pk；keys={keys}")
        raise FlowError(f"checkout missing Stripe cs/pk; keys={keys}")
    emit_step(stage, "success", f"checkout={checkout_id}；processor={processor_entity}")
    return checkout_id, publishable_key, processor_entity, checkout



def is_already_paid_error(detail: Any) -> bool:
    text = str(detail or "").lower()
    return (
        "already paid" in text
        or "user is already paid" in text
        or '"detail":"user is already paid"' in text
    )




def checkout_variants(promo_enabled: bool, attempts: int) -> list[tuple[str, bool]]:
    """Return legal checkout creation shapes without inventing payment methods."""
    maximum = max(1, min(100, int(attempts or 1)))
    base = (
        [("创建时带 Promotion", True), ("创建时不带 Promotion，命中后再正常 update", False)]
        if promo_enabled
        else [("创建时不带 Promotion", False)]
    )
    return [base[index % len(base)] for index in range(maximum)]


def bootstrap_kakao_checkout(
    request: dict[str, Any],
    token: str,
    timeout: int,
    promo_enabled: bool,
    promo_id: str,
    partial: dict[str, Any],
) -> tuple[Any, str, str, str, dict[str, Any], str]:
    attempts = checkout_variants(promo_enabled, int(request.get("checkoutAttempts") or 1))
    history: list[dict[str, Any]] = []
    last_error = ""
    last_methods: list[str] = []
    metadata = partial.setdefault("metadata", {})
    metadata.update(browser_profile_metadata())
    metadata.update(
        {
            "eligibilitySource": "stripe.bootstrap_init",
            "bootstrapAttempts": history,
        }
    )

    for index, (variant_label, include_promo) in enumerate(attempts, 1):
        final_attempt = index == len(attempts)
        emit_step(
            "kakao.checkout_attempt",
            "running",
            f"第 {index}/{len(attempts)} 次；{variant_label}",
        )
        record: dict[str, Any] = {
            "attempt": index,
            "variant": variant_label,
            "includePromoAtCreation": include_promo,
        }
        try:
            checkout_session = new_session(str(request["checkoutProxy"]))
            checkout_id, publishable_key, processor_entity, checkout = create_checkout(
                checkout_session,
                token,
                timeout,
                include_promo,
                promo_id,
                variant_label,
            )
            partial.update({"checkoutId": checkout_id, "processorEntity": processor_entity})
            checkout_page = activate_stripe_checkout(checkout_session, checkout_id, timeout)
            _, _, amount, currency, methods = stripe_init(
                checkout_session,
                checkout_id,
                publishable_key,
                checkout_page,
                timeout,
                "stripe.bootstrap_init",
            )
            require_zero_amount("bootstrap init", amount)
            last_methods = methods
            record.update(
                {
                    "amount": amount,
                    "currency": currency or "KRW",
                    "methods": methods,
                    "kakaoAdvertised": "kakao_pay" in methods,
                }
            )
            history.append(record)
            partial.update(
                {
                    "amount": amount,
                    "amountDisplay": f"{amount} {currency or 'KRW'}" if amount else "",
                    "amountStatus": "zero" if amount == "0" else ("unknown" if not amount else "over_limit"),
                    "availableMethods": methods,
                }
            )
            metadata.update(
                {
                    "bootstrapAmount": amount,
                    "bootstrapMethods": methods,
                    "selectedCheckoutVariant": variant_label if "kakao_pay" in methods else "",
                }
            )
            if "kakao_pay" in methods:
                require_upstream_kakao("bootstrap init", amount, currency, methods, require_zero=True)
                emit_step(
                    "kakao.eligibility",
                    "success",
                    f"第 {index} 次 bootstrap init 由上游真实返回 kakao_pay",
                )
                emit_step(
                    "kakao.checkout_attempt",
                    "success",
                    f"第 {index}/{len(attempts)} 次命中；{variant_label}",
                )
                return checkout_session, checkout_id, publishable_key, processor_entity, checkout, checkout_page

            diagnostic_only = request_mode(request) == KAKAO_MODE_ELIGIBILITY
            status = "warning" if final_attempt and diagnostic_only else ("failed" if final_attempt else "retrying")
            last_error = (
                "upstream checkout did not advertise kakao_pay at bootstrap init; "
                f"methods={','.join(methods) or 'none'}"
            )
            emit_step(
                "kakao.eligibility",
                status,
                f"第 {index} 次上游未返回 kakao_pay；methods={','.join(methods) or 'none'}",
            )
            emit_step(
                "kakao.checkout_attempt",
                "success" if final_attempt and diagnostic_only else status,
                f"第 {index}/{len(attempts)} 次诊断完成，未命中；{variant_label}"
                if final_attempt and diagnostic_only
                else f"第 {index}/{len(attempts)} 次未命中；{variant_label}",
            )
        except FlowError as error:
            last_error = str(error)
            if is_already_paid_error(last_error):
                emit_step("kakao.checkout_attempt", "failed", "账号已付费，停止后续流程")
                raise
            record["error"] = redact_text(error)
            history.append(record)
            status = "failed" if final_attempt else "retrying"
            emit_step(
                "kakao.checkout_attempt",
                status,
                f"第 {index}/{len(attempts)} 次请求失败：{redact_text(error)}",
            )
        if not final_attempt:
            time.sleep(1)

    methods_text = ",".join(last_methods) or "none"
    if last_methods:
        raise KakaoNotAdvertised(
            f"upstream checkout did not advertise kakao_pay after {len(attempts)} attempts; methods={methods_text}"
        )
    raise FlowError(last_error or "Kakao bootstrap init did not return observable payment methods")


def activate_stripe_checkout(session: Any, checkout_id: str, timeout: int) -> str:
    stage = "stripe.activate"
    checkout_page = f"https://checkout.stripe.com/c/pay/{checkout_id}"
    emit_step(stage, "running", "加载 pay.openai 与 checkout.stripe 支付页")
    statuses: list[str] = []

    def _load_pages() -> list[str]:
        loaded: list[str] = []
        for target in (f"https://pay.openai.com/c/pay/{checkout_id}", checkout_page):
            response = session.get(
                target,
                headers={
                    "User-Agent": active_user_agent(),
                    "Accept": "text/html,*/*",
                    "Accept-Language": KAKAO_ACCEPT_LANGUAGE,
                    "Referer": "https://chatgpt.com/",
                    **browser_client_hints(),
                },
                timeout=timeout,
            )
            loaded.append(str(response.status_code))
        return loaded

    try:
        statuses = call_with_tls_soft_retry(
            _load_pages,
            stage=stage,
            label="Stripe activation",
            retries=3,
        )
    except Exception as error:
        emit_step(stage, "failed", str(error))
        raise FlowError(f"Stripe activation failed: {redact_text(error)}") from error
    emit_step(stage, "success", "HTTP " + "/".join(statuses))
    return checkout_page


def stripe_init(
    session: Any,
    checkout_id: str,
    publishable_key: str,
    checkout_page: str,
    timeout: int,
    stage: str,
) -> tuple[dict[str, Any], str, str, str, list[str]]:
    stripe_js_id = str(uuid.uuid4())
    body = {
        "key": publishable_key,
        "eid": "NA",
        "browser_locale": KAKAO_LOCALE,
        "browser_timezone": KAKAO_TIMEZONE,
        "redirect_type": "url",
        "_stripe_version": STRIPE_VERSION,
        **elements_params(stripe_js_id),
    }
    emit_step(stage, "running", "初始化 Stripe payment page")
    try:
        response = session.post(
            f"https://api.stripe.com/v1/payment_pages/{checkout_id}/init",
            data=body,
            headers=stripe_headers(publishable_key, checkout_page),
            timeout=timeout,
        )
    except Exception as error:
        emit_step(stage, "failed", str(error))
        raise FlowError(f"{stage} request failed: {redact_text(error)}") from error
    if response.status_code != 200:
        detail = f"HTTP {response.status_code}: {response_error(response)}"
        emit_step(stage, "failed", detail)
        raise FlowError(f"{stage} failed {detail}")
    payload = response_json(response, stage)
    amount = expected_amount(payload)
    currency = str(payload.get("currency") or "").upper()
    methods = payment_methods(payload)
    emit_step(stage, "success", f"amount={amount or 'unknown'} {currency or 'KRW'}；methods={','.join(methods) or 'none'}")
    return payload, stripe_js_id, amount, currency, methods


def require_upstream_kakao(
    stage: str,
    amount: str,
    currency: str,
    methods: list[str],
    *,
    require_zero: bool,
) -> None:
    if "kakao_pay" not in methods:
        emit_step("kakao.eligibility", "failed", f"{stage} 上游未返回 kakao_pay；methods={','.join(methods) or 'none'}")
        raise FlowError(f"upstream checkout did not advertise kakao_pay at {stage}; methods={','.join(methods) or 'none'}")
    if require_zero and (amount != "0" or currency.upper() != "KRW"):
        emit_step("kakao.eligibility", "failed", f"{stage} 金额/货币不符合零元 KRW；amount={amount or 'unknown'} {currency}")
        raise FlowError(f"Kakao checkout is not a zero KRW checkout at {stage}; amount={amount or 'unknown'} currency={currency}")
    emit_step("kakao.eligibility", "success", f"{stage} 由上游真实返回 kakao_pay")


def require_zero_amount(stage: str, amount: str) -> None:
    if amount != "0":
        emit_step("stripe.amount_gate", "failed", f"{stage} 金额必须严格为 0；amount={amount or 'unknown'}")
        raise FlowError(f"Stripe amount must be exactly zero at {stage}; amount={amount or 'unknown'}")
    emit_step("stripe.amount_gate", "success", f"{stage} amount=0")


def update_checkout_promotion(
    session: Any,
    token: str,
    checkout_id: str,
    processor_entity: str,
    timeout: int,
    promo_enabled: bool,
    promo_id: str,
    promotion_region: str,
) -> None:
    stage = "chatgpt.checkout_update"
    body: dict[str, Any] = {
        "checkout_session_id": checkout_id,
        "processor_entity": processor_entity,
        "plan_name": "chatgptplusplan",
        "price_interval": "month",
        "seat_quantity": 1,
    }
    if promo_enabled and promo_id:
        body["promo_campaign"] = {
            "promo_campaign_id": promo_id,
            "is_coupon_from_query_param": False,
        }
    target_path = "/backend-api/payments/checkout/update"
    emit_step(stage, "running", f"{promotion_region or 'Promotion'} 出口更新优惠")
    try:
        response = session.post(
            f"https://chatgpt.com{target_path}",
            headers=checkout_api_headers(
                token,
                checkout_page_url(checkout_id, processor_entity),
                target_path,
            ),
            json=body,
            timeout=timeout,
        )
    except Exception as error:
        emit_step(stage, "failed", str(error))
        raise FlowError(f"checkout/update request failed: {redact_text(error)}") from error
    if response.status_code >= 400:
        detail = f"HTTP {response.status_code}: {response_error(response)}"
        emit_step(stage, "failed", detail)
        raise FlowError(f"checkout/update failed {detail}")
    try:
        payload = response.json() or {}
    except (TypeError, ValueError):
        payload = {}
    if isinstance(payload, dict) and payload.get("success") is False:
        emit_step(stage, "failed", "上游返回 success=false")
        raise FlowError("checkout/update returned success=false")
    emit_step(stage, "success", f"promo={promo_id if promo_enabled and promo_id else 'off'}")


def update_checkout_taxes(
    session: Any,
    token: str,
    checkout_id: str,
    processor_entity: str,
    billing: dict[str, str],
    timeout: int,
) -> None:
    stage = "chatgpt.checkout_taxes"
    target_path = "/backend-api/payments/checkout/taxes"
    body = {
        "checkout_session_id": checkout_id,
        "checkout_email": billing["email"],
        "billing_country": "KR",
        "billing_name": billing["name"],
        "currency": "KRW",
        "tax_id": None,
        "processor_entity": processor_entity,
        "billing_address": {
            "line1": billing["line1"],
            "city": billing["city"],
            "country": "KR",
            "postal_code": billing["postal_code"],
            "state": billing["state"],
        },
    }
    emit_step(stage, "running", "同步 KR billing snapshot")
    try:
        response = session.post(
            f"https://chatgpt.com{target_path}",
            headers=checkout_api_headers(
                token,
                checkout_page_url(checkout_id, processor_entity),
                target_path,
            ),
            json=body,
            timeout=timeout,
        )
    except Exception as error:
        emit_step(stage, "failed", str(error))
        raise FlowError(f"checkout/taxes request failed: {redact_text(error)}") from error
    if response.status_code >= 400:
        detail = f"HTTP {response.status_code}: {response_error(response)}"
        emit_step(stage, "failed", detail)
        raise FlowError(f"checkout/taxes failed {detail}")
    emit_step(stage, "success", f"KR / {billing['postal_code']}")


def update_stripe_tax_region(
    session: Any,
    checkout_id: str,
    publishable_key: str,
    checkout_page: str,
    stripe_js_id: str,
    elements_session_id: str,
    billing: dict[str, str],
    timeout: int,
) -> None:
    stage = "stripe.tax_region"
    body = {
        "key": publishable_key,
        "_stripe_version": STRIPE_VERSION,
        **elements_params(stripe_js_id, elements_session_id),
        "tax_region[country]": "KR",
        "tax_region[postal_code]": billing["postal_code"],
        "tax_region[line1]": billing["line1"],
        "tax_region[city]": billing["city"],
        "tax_region[state]": billing["state"],
    }
    emit_step(stage, "running", "同步 Stripe KR tax_region")
    try:
        response = session.post(
            f"https://api.stripe.com/v1/payment_pages/{checkout_id}",
            data=body,
            headers=stripe_headers(publishable_key, checkout_page),
            timeout=timeout,
        )
    except Exception as error:
        emit_step(stage, "failed", str(error))
        raise FlowError(f"tax_region request failed: {redact_text(error)}") from error
    if response.status_code >= 400:
        detail = f"HTTP {response.status_code}: {response_error(response)}"
        emit_step(stage, "failed", detail)
        raise FlowError(f"tax_region failed {detail}")
    emit_step(stage, "success", f"KR / {billing['postal_code']}")


def create_payment_method(
    session: Any,
    checkout_id: str,
    publishable_key: str,
    checkout_page: str,
    init_payload: dict[str, Any],
    billing: dict[str, str],
    timeout: int,
) -> tuple[str, str, str, str, str]:
    stage = "stripe.payment_method"
    client_session_id = str(uuid.uuid4())
    guid = f"{uuid.uuid4()}{os.urandom(3).hex()}"
    muid = f"{uuid.uuid4()}{os.urandom(3).hex()}"
    sid = f"{uuid.uuid4()}{os.urandom(3).hex()}"
    body = {
        "type": "kakao_pay",
        "billing_details[name]": billing["name"],
        "billing_details[email]": billing["email"],
        "billing_details[address][country]": "KR",
        "billing_details[address][line1]": billing["line1"],
        "billing_details[address][line2]": billing["line2"],
        "billing_details[address][city]": billing["city"],
        "billing_details[address][postal_code]": billing["postal_code"],
        "billing_details[address][state]": billing["state"],
        "guid": guid,
        "muid": muid,
        "sid": sid,
        "_stripe_version": STRIPE_VERSION,
        "key": publishable_key,
        "payment_user_agent": STRIPE_PAYMENT_UA,
        "client_attribution_metadata[client_session_id]": client_session_id,
        "client_attribution_metadata[checkout_session_id]": checkout_id,
        "client_attribution_metadata[merchant_integration_source]": "checkout",
        "client_attribution_metadata[merchant_integration_version]": "custom_checkout",
        "client_attribution_metadata[payment_method_selection_flow]": "merchant_specified",
    }
    config_id = str(init_payload.get("config_id") or "")
    if config_id:
        body["client_attribution_metadata[checkout_config_id]"] = config_id
    emit_step(stage, "running", "创建上游已展示的 kakao_pay payment method")
    try:
        response = session.post(
            "https://api.stripe.com/v1/payment_methods",
            data=body,
            headers=stripe_headers(publishable_key, checkout_page),
            timeout=timeout,
        )
    except Exception as error:
        emit_step(stage, "failed", str(error))
        raise FlowError(f"payment method request failed: {redact_text(error)}") from error
    if response.status_code != 200:
        detail = f"HTTP {response.status_code}: {response_error(response, 1000)}"
        emit_step(stage, "failed", detail)
        raise FlowError(f"payment method failed {detail}")
    payment_method_id = str(response_json(response, stage).get("id") or "")
    if not payment_method_id.startswith("pm_"):
        emit_step(stage, "failed", "响应缺少 pm_ id")
        raise FlowError("payment method response did not contain a pm_ id")
    emit_step(stage, "success", payment_method_id)
    return payment_method_id, client_session_id, guid, muid, sid


def confirm_payment_method(
    session: Any,
    checkout_id: str,
    processor_entity: str,
    publishable_key: str,
    checkout_page: str,
    init_payload: dict[str, Any],
    stripe_js_id: str,
    elements_session_id: str,
    amount: str,
    payment_method_id: str,
    client_session_id: str,
    guid: str,
    muid: str,
    sid: str,
    timeout: int,
) -> dict[str, Any]:
    stage = "stripe.confirm"
    success_url = (
        f"https://chatgpt.com/backend-api/payments/checkout/{processor_entity}/{checkout_id}/success?"
        "billing_country=KR"
    )
    return_url = (
        f"https://checkout.stripe.com/c/pay/{checkout_id}?returned_from_redirect=true&ui_mode=custom&"
        f"return_url={quote(success_url, safe='')}"
    )
    body = {
        "eid": "NA",
        "payment_method": payment_method_id,
        "expected_amount": amount,
        "tax_id_collection[purchasing_as_business]": "false",
        "expected_payment_method_type": "kakao_pay",
        "return_url": return_url,
        "_stripe_version": STRIPE_VERSION,
        "guid": guid,
        "muid": muid,
        "sid": sid,
        "key": publishable_key,
        "version": STRIPE_RUNTIME,
        "init_checksum": str(init_payload.get("init_checksum") or ""),
        "client_attribution_metadata[client_session_id]": client_session_id,
        "client_attribution_metadata[checkout_session_id]": checkout_id,
        "client_attribution_metadata[merchant_integration_source]": "checkout",
        "client_attribution_metadata[merchant_integration_version]": "custom_checkout",
        "client_attribution_metadata[payment_method_selection_flow]": "merchant_specified",
        "link_brand": "link",
        **elements_params(stripe_js_id, elements_session_id),
    }
    config_id = str(init_payload.get("config_id") or "")
    if config_id:
        body["client_attribution_metadata[checkout_config_id]"] = config_id
    emit_step(stage, "running", "提交 Kakao confirm（不完成付款）")
    try:
        response = session.post(
            f"https://api.stripe.com/v1/payment_pages/{checkout_id}/confirm",
            data=body,
            headers=stripe_headers(publishable_key, checkout_page),
            timeout=timeout,
        )
    except Exception as error:
        emit_step(stage, "failed", str(error))
        raise FlowError(f"confirm request failed: {redact_text(error)}") from error
    if response.status_code != 200:
        detail = f"HTTP {response.status_code}: {response_error(response, 1000)}"
        emit_step(stage, "failed", detail)
        raise FlowError(f"confirm failed {detail}")
    payload = response_json(response, stage)
    submission = payload.get("submission_attempt") if isinstance(payload.get("submission_attempt"), dict) else {}
    state = str(submission.get("state") or deep_string(payload, "status") or "unknown")
    emit_step(stage, "success", f"state={state}")
    return payload


def approve_if_required(
    session: Any,
    token: str,
    checkout_id: str,
    processor_entity: str,
    checkout: dict[str, Any],
    confirm_payload: dict[str, Any],
    timeout: int,
    partial: dict[str, Any],
    approve_attempts: int = 3,
    *,
    provider_proxy: str = "",
    main_profile: dict[str, Any] | None = None,
    fingerprint_policy: dict[str, str] | None = None,
    preferred_profile: str = "",
    attempt: int = 1,
    weight_mode: bool = False,
) -> str:
    redirect = extract_redirect(confirm_payload)
    submission = confirm_payload.get("submission_attempt")
    if not isinstance(submission, dict):
        submission = {}
    requires_approval = submission.get("state") == "requires_approval" or bool(checkout.get("requires_manual_approval"))
    if redirect or not requires_approval:
        return redirect
    stage = "chatgpt.approve"
    # Historical successful materials needed approve=approved. Retry a few times on
    # the same checkout before giving up; blocked is often transient with a fresh
    # KR sticky identity on the next full-chain attempt.
    try:
        max_tries = max(1, min(10, int(approve_attempts or os.environ.get("KAKAO_APPROVE_RETRY_MAX", "3") or 3)))
    except (TypeError, ValueError):
        max_tries = 3
    last_result = ""
    policy = normalize_fingerprint_policy(fingerprint_policy)
    base_profile = normalize_browser_profile(main_profile or active_browser_profile())
    owned_sessions: list[Any] = []
    for index in range(1, max_tries + 1):
        approve_session = session
        approve_profile = resolve_stage_profile(
            "approve",
            base_profile,
            policy,
            seed=f"{checkout_id}|approve",
            preferred=preferred_profile,
            attempt=attempt,
            retry=index,
            weight_mode=weight_mode,
        )
        if policy.get("approve") == "fresh" and provider_proxy:
            try:
                approve_session = new_session(provider_proxy, approve_profile)
                owned_sessions.append(approve_session)
                detail_fp = f"；fingerprint={approve_profile.get('id')} / {approve_profile.get('impersonate')}"
            except Exception as error:
                last_result = f"request_failed:{redact_text(error)}"
                record_fingerprint_outcome(
                    approve_profile,
                    stage=stage,
                    status="retrying" if index < max_tries else "failed",
                    detail=last_result,
                    weight_mode=weight_mode,
                )
                emit_step(stage, "retrying" if index < max_tries else "failed", last_result)
                if index < max_tries:
                    time.sleep(0.8 * index)
                    continue
                raise FlowError(f"approve request failed: {redact_text(error)}") from error
        else:
            detail_fp = f"；fingerprint={approve_profile.get('id')} / {approve_profile.get('impersonate')}（跟随主指纹）"
        emit_step(stage, "running", f"同一 KR provider 会话 approve {index}/{max_tries}{detail_fp}")
        try:
            # Generate main + SO tokens together for this approve attempt.
            sentinel_pair = maybe_generate_sentinel_pair(
                approve_session,
                stage="chatgpt.sentinel",
                device_id=active_oai_device_id(f"{checkout_id}|{token[:24]}"),
                flow=str(os.environ.get("AUTOMYAI_KAKAO_SENTINEL_FLOW") or SENTINEL_DEFAULT_FLOW),
                timeout=min(30, max(8, int(timeout or 20))),
            )
            if sentinel_pair:
                partial.setdefault("metadata", {})["sentinel"] = {
                    "flow": sentinel_pair.get("flow"),
                    "deviceId": sentinel_pair.get("deviceId"),
                    "soRequired": bool(sentinel_pair.get("soRequired")),
                    "soAttached": bool(sentinel_pair.get("soHeader")),
                    "challengeKeys": list(sentinel_pair.get("challengeKeys") or []),
                    "attempt": index,
                }
            # Best-effort ping, matching the Go path's optional sentinel warm-up.
            try:
                approve_session.post(
                    "https://chatgpt.com/backend-api/sentinel/ping",
                    headers=checkout_api_headers(
                        token,
                        "https://chatgpt.com/",
                        "/backend-api/sentinel/ping",
                        sentinel_pair=sentinel_pair,
                    ),
                    json={},
                    timeout=min(15, max(5, int(timeout or 20))),
                )
            except Exception:
                pass
            response = approve_session.post(
                "https://chatgpt.com/backend-api/payments/checkout/approve",
                headers=checkout_api_headers(
                    token,
                    checkout_page_url(checkout_id, processor_entity),
                    "/backend-api/payments/checkout/approve",
                    sentinel_pair=sentinel_pair,
                ),
                json={"checkout_session_id": checkout_id, "processor_entity": processor_entity},
                timeout=timeout,
            )
        except Exception as error:
            last_result = f"request_failed:{redact_text(error)}"
            record_fingerprint_outcome(
                approve_profile,
                stage=stage,
                status="retrying" if index < max_tries else "failed",
                detail=last_result,
                weight_mode=weight_mode,
            )
            emit_step(stage, "retrying" if index < max_tries else "failed", last_result)
            if index < max_tries:
                time.sleep(0.8 * index)
                continue
            raise FlowError(f"approve request failed: {redact_text(error)}") from error
        result = ""
        if response.status_code == 200:
            try:
                payload = response.json() or {}
                if isinstance(payload, dict):
                    result = str(payload.get("result") or payload.get("status") or "").lower()
            except (TypeError, ValueError):
                result = ""
        if response.status_code == 200 and result == "approved":
            record_fingerprint_outcome(
                approve_profile,
                stage=stage,
                outcome="approved",
                status="success",
                detail=f"result=approved；第 {index} 次",
                result="approved",
                weight_mode=weight_mode,
            )
            emit_step(stage, "success", f"result=approved；第 {index} 次")
            close_sessions(*owned_sessions)
            return ""
        last_result = result or f"http_{response.status_code}"
        status = "retrying" if index < max_tries else "failed"
        record_fingerprint_outcome(
            approve_profile,
            stage=stage,
            status=status,
            detail=f"result={last_result}；第 {index}/{max_tries} 次",
            result=last_result,
            weight_mode=weight_mode,
        )
        emit_step(stage, status, f"result={last_result}；第 {index}/{max_tries} 次")
        if index < max_tries:
            time.sleep(0.8 * index)
    partial["decision"] = "approve_blocked" if last_result == "blocked" else "approve_failed"
    partial["paymentStatus"] = "approval_blocked" if last_result == "blocked" else "approval_failed"
    close_sessions(*owned_sessions)
    raise FlowError(f"approve result={last_result}")


def poll_redirect(
    session: Any,
    checkout_id: str,
    publishable_key: str,
    checkout_page: str,
    stripe_js_id: str,
    elements_session_id: str,
    timeout: int,
    poll_timeout: int,
    initial_redirect: str,
) -> str:
    if initial_redirect:
        return initial_redirect
    stage = "stripe.redirect_poll"
    params = {"key": publishable_key, **elements_params(stripe_js_id, elements_session_id)}
    emit_step(stage, "running", f"最长 {poll_timeout}s")
    deadline = time.monotonic() + poll_timeout
    redirect = ""
    last_status = 0
    while not redirect and time.monotonic() < deadline:
        try:
            response = session.get(
                f"https://api.stripe.com/v1/payment_pages/{checkout_id}",
                params=params,
                headers=stripe_headers(publishable_key, checkout_page),
                timeout=min(timeout, 8),
            )
            last_status = int(response.status_code)
            if response.status_code == 200:
                redirect = extract_redirect(response_json(response, stage))
        except FlowError:
            raise
        except Exception as error:
            emit_step(stage, "failed", str(error))
            raise FlowError(f"redirect poll failed: {redact_text(error)}") from error
        if not redirect:
            time.sleep(1)
    if not redirect:
        emit_step(stage, "failed", f"timeout；last_http={last_status or 'none'}")
        raise FlowError("Kakao provider redirect timed out")
    emit_step(stage, "success", "Stripe 已返回 provider redirect")
    return redirect


def follow_provider_redirect(session: Any, redirect: str, timeout: int) -> str:
    stage = "kakao.provider_redirect"
    emit_step(stage, "running", "解析 NicePay/KakaoPay 跳转")
    current = redirect
    try:
        for _ in range(6):
            host = (urlsplit(current).hostname or "").lower()
            if "nicepay" in host or "kakao" in host:
                break
            response = session.get(current, allow_redirects=False, timeout=timeout)
            location = str(response.headers.get("Location") or response.headers.get("location") or "")
            if response.status_code not in {301, 302, 303, 307, 308} or not location:
                break
            current = urljoin(current, location)
    except Exception as error:
        emit_step(stage, "failed", str(error))
        raise FlowError(f"provider redirect resolution failed: {redact_text(error)}") from error
    host = (urlsplit(current).hostname or "").lower()
    if "nicepay" not in host and "kakao" not in host:
        emit_step(stage, "failed", f"最终 host={host or 'unknown'}")
        raise FlowError(f"provider redirect did not resolve to NicePay/KakaoPay; host={host or 'unknown'}")
    emit_step(stage, "success", f"host={host}")
    return current



def parse_proxy_pool(raw: Any) -> list[str]:
    if isinstance(raw, list):
        values = [str(item or "").strip() for item in raw]
    else:
        text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
        for sep in (";", ","):
            text = text.replace(sep, "\n")
        values = [line.strip() for line in text.split("\n")]
    pool: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value.startswith("#") or value in seen:
            continue
        seen.add(value)
        pool.append(value)
    return pool


def parse_region_pool(raw: Any) -> list[str]:
    if isinstance(raw, list):
        values = [str(item or "").strip().upper() for item in raw]
    else:
        text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
        for sep in (";", ",", "|", " "):
            text = text.replace(sep, "\n")
        values = [line.strip().upper() for line in text.split("\n")]
    pool: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value == "UK":
            value = "GB"
        if value == "USA":
            value = "US"
        if not value or value in seen:
            continue
        seen.add(value)
        pool.append(value)
    return pool


def pick_pool_value(pool: list[str], attempt: int) -> str:
    if not pool:
        return ""
    if attempt < 1:
        attempt = 1
    return pool[(attempt - 1) % len(pool)]


def resolve_attempt_proxies(request: dict[str, Any], attempt: int) -> tuple[str, str, str, str]:
    checkout_pool = parse_proxy_pool(request.get("checkoutProxies") or request.get("checkoutProxy"))
    promotion_pool = parse_proxy_pool(request.get("promotionProxies") or request.get("promotionProxy"))
    region_pool = parse_region_pool(request.get("promotionRegions") or request.get("promotionRegion"))
    checkout_proxy = pick_pool_value(checkout_pool, attempt) or str(request.get("checkoutProxy") or "").strip()
    # Default Kakao behavior keeps the main/checkout identity sticky unless the
    # caller multi-selected main proxies. Promotion still rotates every attempt.
    if len(checkout_pool) <= 1:
        checkout_proxy = checkout_pool[0] if checkout_pool else checkout_proxy
    promotion_proxy = pick_pool_value(promotion_pool, attempt) or str(request.get("promotionProxy") or "").strip()
    promotion_region = pick_pool_value(region_pool, attempt) or str(request.get("promotionRegion") or "").strip().upper()
    provider_proxy = checkout_proxy
    return checkout_proxy, promotion_proxy, provider_proxy, promotion_region

def validate_request(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise FlowError("input must be a JSON object")
    token = str(payload.get("accessToken") or "").strip()
    checkout_pool = parse_proxy_pool(payload.get("checkoutProxies") or payload.get("checkoutProxy"))
    promotion_pool = parse_proxy_pool(payload.get("promotionProxies") or payload.get("promotionProxy"))
    if not checkout_pool:
        checkout_pool = parse_proxy_pool(payload.get("checkoutProxy"))
    if not promotion_pool:
        promotion_pool = parse_proxy_pool(payload.get("promotionProxy"))
    checkout_proxy = checkout_pool[0] if checkout_pool else str(payload.get("checkoutProxy") or "").strip()
    promotion_proxy = promotion_pool[0] if promotion_pool else str(payload.get("promotionProxy") or "").strip()
    requested_provider_proxy = str(payload.get("providerProxy") or checkout_proxy).strip()
    if requested_provider_proxy != checkout_proxy:
        raise FlowError("providerProxy must reuse the exact checkout proxy identity")
    provider_proxy = checkout_proxy
    for secret in (token, payload.get("sessionToken"), *checkout_pool, *promotion_pool, checkout_proxy, promotion_proxy, provider_proxy):
        register_secret(secret)
    if not token:
        raise FlowError("access token is required")
    if not checkout_proxy or not promotion_proxy:
        raise FlowError("checkout, promotion, and provider proxies are required")
    for name, proxy in (
        *[("checkout", value) for value in (checkout_pool or [checkout_proxy])],
        *[("promotion", value) for value in (promotion_pool or [promotion_proxy])],
        ("provider", provider_proxy),
    ):
        try:
            parsed = urlsplit(proxy)
        except ValueError as error:
            raise FlowError(f"{name} proxy is invalid") from error
        if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname or not parsed.port:
            raise FlowError(f"{name} proxy is invalid")
    payload["checkoutProxy"] = checkout_proxy
    payload["promotionProxy"] = promotion_proxy
    payload["providerProxy"] = provider_proxy
    payload["checkoutProxies"] = checkout_pool or [checkout_proxy]
    payload["promotionProxies"] = promotion_pool or [promotion_proxy]
    region_pool = parse_region_pool(payload.get("promotionRegions") or payload.get("promotionRegion"))
    if region_pool:
        payload["promotionRegions"] = region_pool
        payload["promotionRegion"] = region_pool[0]
    mode = request_mode(payload)
    if "eligibilityOnly" in payload and not isinstance(payload.get("eligibilityOnly"), bool):
        raise FlowError("eligibilityOnly must be a boolean")
    eligibility_only = mode == KAKAO_MODE_ELIGIBILITY
    if "eligibilityOnly" in payload and bool(payload.get("eligibilityOnly")) != eligibility_only:
        raise FlowError("mode and eligibilityOnly disagree")
    payload["mode"] = mode
    payload["eligibilityOnly"] = eligibility_only
    return payload


def finish_eligibility_probe(
    partial: dict[str, Any],
    *,
    eligible: bool,
    detail: str,
) -> dict[str, Any]:
    """Return a result that cannot be mistaken for a generated payment link."""
    metadata = partial.setdefault("metadata", {})
    metadata.update(
        {
            "diagnosticOnly": True,
            "stoppedBeforePayment": True,
            "stopStage": "stripe.bootstrap_init",
            "kakaoAdvertised": eligible,
        }
    )
    for key in (
        "checkoutId",
        "processorEntity",
        "paymentMethodId",
        "stripeRedirectUrl",
        "providerRedirectUrl",
        "longUrl",
    ):
        partial.pop(key, None)
    partial.update(
        {
            "ok": True,
            "extractionStatus": "probe_complete",
            "paymentStatus": "not_started",
            "decision": "eligible" if eligible else "ineligible",
        }
    )
    emit_step(
        "kakao.diagnostic_stop",
        "success",
        (
            "上游真实返回 kakao_pay；资格诊断到此停止，未进入 Promotion / pre_confirm / payment_method / confirm / approve"
            if eligible
            else f"上游未返回 kakao_pay；资格诊断到此停止；{detail}"
        ),
    )
    return partial


def new_partial_result() -> dict[str, Any]:
    return {
        "ok": False,
        "method": "kakao",
        "country": "KR",
        "currency": "KRW",
        "extractionStatus": "failed",
        "paymentStatus": "not_started",
        "metadata": {"flowMode": KAKAO_MODE_PROVIDER_LINK},
    }


def close_sessions(*sessions: Any) -> None:
    for session in sessions:
        if session is None:
            continue
        try:
            session.close()
        except Exception:
            pass


def run_provider_link_attempt(
    request: dict[str, Any],
    token: str,
    timeout: int,
    poll_timeout: int,
    promo_enabled: bool,
    promo_id: str,
    promotion_region: str,
    max_amount: int,
    attempt: int,
    total_attempts: int,
    partial: dict[str, Any],
) -> dict[str, Any]:
    checkout_session = None
    promotion_session = None
    provider_session = None
    try:
        # The original Kakao runner retried the entire chain with a fresh
        # checkout. Alternate the two legal checkout creation shapes while
        # keeping the normal Promotion update in every provider-link attempt.
        include_promo_at_creation = promo_enabled and attempt % 2 == 1
        try:
            approve_attempts = max(1, min(10, int(request.get("approveAttempts") or request.get("approve_attempts") or 3)))
        except (TypeError, ValueError):
            approve_attempts = 3
        fingerprint_policy = normalize_fingerprint_policy(
            request.get("fingerprintPolicy")
            or request.get("fingerprintStages")
            or request.get("stageFingerprints")
        )
        weight_mode = bool(
            request.get("fingerprintWeightMode")
            if request.get("fingerprintWeightMode") is not None
            else request.get("fingerprint_weight_mode")
            if request.get("fingerprint_weight_mode") is not None
            else request.get("weightMode")
            if request.get("weightMode") is not None
            else False
        )
        preferred_profile = str(
            request.get("browserProfile")
            or request.get("clientFingerprint")
            or request.get("fingerprint")
            or ""
        ).strip()
        main_profile = normalize_browser_profile(active_browser_profile())
        checkout_request = dict(request)
        checkout_request["checkoutAttempts"] = 1
        checkout_request["fingerprintPolicy"] = fingerprint_policy
        checkout_session, checkout_id, publishable_key, processor_entity, checkout, checkout_page = bootstrap_kakao_checkout(
            checkout_request,
            token,
            timeout,
            include_promo_at_creation,
            promo_id,
            partial,
        )
        partial.setdefault("metadata", {}).update(
            {
                "flowMode": KAKAO_MODE_PROVIDER_LINK,
                "fullAttempt": attempt,
                "fullAttemptsConfigured": total_attempts,
                "promotionRegion": promotion_region,
                "includePromoAtCreation": include_promo_at_creation,
            }
        )

        partial.setdefault("metadata", {})["fingerprintPolicy"] = dict(fingerprint_policy)
        promotion_profile = resolve_stage_profile(
            "promotion",
            main_profile,
            fingerprint_policy,
            seed=str(request.get("accessToken") or "")[:24] + "|" + str(request.get("promotionProxy") or ""),
            preferred=preferred_profile,
            attempt=attempt,
            weight_mode=weight_mode,
        )
        provider_profile = resolve_stage_profile(
            "provider",
            main_profile,
            fingerprint_policy,
            seed=str(request.get("accessToken") or "")[:24] + "|" + str(request.get("providerProxy") or request.get("checkoutProxy") or ""),
            preferred=preferred_profile,
            attempt=attempt,
            weight_mode=weight_mode,
        )
        # Keep the active main profile restored after stage-specific sessions so
        # later follow-mode stages still inherit the chain identity.
        promotion_session = new_session(str(request["promotionProxy"]), promotion_profile)
        set_active_browser_profile(main_profile)
        provider_session = new_session(str(request["providerProxy"]), provider_profile)
        set_active_browser_profile(main_profile)
        partial.setdefault("metadata", {}).update(
            {
                "promotionFingerprint": browser_profile_metadata(promotion_profile),
                "providerFingerprint": browser_profile_metadata(provider_profile),
            }
        )
        update_checkout_promotion(
            promotion_session,
            token,
            checkout_id,
            processor_entity,
            timeout,
            promo_enabled,
            promo_id,
            promotion_region,
        )
        init_payload, stripe_js_id, amount, currency, methods = stripe_init(
            provider_session,
            checkout_id,
            publishable_key,
            checkout_page,
            timeout,
            "stripe.post_promotion_init",
        )
        partial.update(
            {
                "amount": amount,
                "amountDisplay": f"{amount} {currency or 'KRW'}" if amount else "",
                "amountStatus": "zero" if amount == "0" else ("unknown" if not amount else "over_limit"),
                "availableMethods": methods,
            }
        )
        require_upstream_kakao("post-promotion init", amount, currency, methods, require_zero=True)

        billing = random_kakao_billing(token)
        tax_elements_session_id = f"elements_session_{uuid.uuid4().hex[:11]}"
        update_checkout_taxes(
            provider_session,
            token,
            checkout_id,
            processor_entity,
            billing,
            timeout,
        )
        update_stripe_tax_region(
            provider_session,
            checkout_id,
            publishable_key,
            checkout_page,
            stripe_js_id,
            tax_elements_session_id,
            billing,
            timeout,
        )

        init_payload, stripe_js_id, amount, currency, methods = stripe_init(
            provider_session,
            checkout_id,
            publishable_key,
            checkout_page,
            timeout,
            "stripe.post_tax_init",
        )
        partial.update(
            {
                "amount": amount,
                "amountDisplay": f"{amount} {currency or 'KRW'}" if amount else "",
                "amountStatus": "zero" if amount == "0" else ("unknown" if not amount else "over_limit"),
                "availableMethods": methods,
            }
        )
        require_upstream_kakao("post-tax init", amount, currency, methods, require_zero=True)

        elements_session_id = f"elements_session_{uuid.uuid4().hex[:11]}"
        stage = "stripe.pre_confirm"
        emit_step(stage, "running", "payment_method_type=kakao_pay")
        try:
            response = provider_session.post(
                f"https://api.stripe.com/v1/payment_pages/{checkout_id}/pre_confirm",
                data={
                    "eid": str(uuid.uuid4()),
                    "payment_method_type": "kakao_pay",
                    "key": publishable_key,
                    "_stripe_version": STRIPE_VERSION,
                },
                headers=stripe_headers(publishable_key, checkout_page),
                timeout=timeout,
            )
        except Exception as error:
            emit_step(stage, "failed", str(error))
            raise FlowError(f"pre_confirm request failed: {redact_text(error)}") from error
        if response.status_code != 200:
            detail = f"HTTP {response.status_code}: {response_error(response)}"
            emit_step(stage, "failed", detail)
            raise FlowError(f"pre_confirm failed {detail}")
        emit_step(stage, "success", "kakao_pay")

        payment_method_id, client_session_id, guid, muid, sid = create_payment_method(
            provider_session,
            checkout_id,
            publishable_key,
            checkout_page,
            init_payload,
            billing,
            timeout,
        )
        partial["paymentMethodId"] = payment_method_id
        confirm_payload = confirm_payment_method(
            provider_session,
            checkout_id,
            processor_entity,
            publishable_key,
            checkout_page,
            init_payload,
            stripe_js_id,
            elements_session_id,
            amount,
            payment_method_id,
            client_session_id,
            guid,
            muid,
            sid,
            timeout,
        )
        partial["paymentStatus"] = "awaiting_approval"
        try:
            redirect = approve_if_required(
                provider_session,
                token,
                checkout_id,
                processor_entity,
                checkout,
                confirm_payload,
                timeout,
                partial,
                approve_attempts=approve_attempts,
                provider_proxy=str(request.get("providerProxy") or request.get("checkoutProxy") or ""),
                main_profile=main_profile,
                fingerprint_policy=fingerprint_policy,
                preferred_profile=preferred_profile,
                attempt=attempt,
                weight_mode=weight_mode,
            )
        except FlowError as approve_error:
            # Some historically observed checkouts still expose a provider redirect
            # shortly after a blocked approve. Probe once before abandoning this
            # full-chain attempt so we do not miss a usable NicePay link.
            message = str(approve_error)
            emit_step("chatgpt.approve", "warning", f"{redact_text(message)}；仍尝试短轮询 redirect")
            try:
                redirect = poll_redirect(
                    provider_session,
                    checkout_id,
                    publishable_key,
                    checkout_page,
                    stripe_js_id,
                    elements_session_id,
                    timeout,
                    min(12, poll_timeout),
                    "",
                )
            except Exception:
                raise approve_error
            if not redirect:
                raise approve_error
            emit_step("chatgpt.approve", "success", "blocked 后短轮询仍拿到 redirect")
        redirect = poll_redirect(
            provider_session,
            checkout_id,
            publishable_key,
            checkout_page,
            stripe_js_id,
            elements_session_id,
            timeout,
            poll_timeout,
            redirect,
        )
        partial["stripeRedirectUrl"] = redirect
        provider_url = follow_provider_redirect(provider_session, redirect, timeout)
        generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # NicePay/KakaoPay QR / long-link channel is typically valid for ~10 minutes.
        link_ttl_seconds = 600
        expires_at = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() + link_ttl_seconds),
        )
        metadata = partial.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            partial["metadata"] = metadata
        metadata.update(
            {
                "linkChannel": "kakao_nicepay",
                "linkTtlSeconds": link_ttl_seconds,
                "linkGeneratedAt": generated_at,
                "expiresAt": expires_at,
                "providerLinkExpiresAt": expires_at,
            }
        )
        partial.update(
            {
                "ok": True,
                "providerRedirectUrl": provider_url,
                "longUrl": provider_url,
                "extractionStatus": "provider_link_ready",
                "paymentStatus": "awaiting_kakao_payment",
                "decision": "ready",
                "linkGeneratedAt": generated_at,
                "expiresAt": expires_at,
                "linkTtlSeconds": link_ttl_seconds,
            }
        )
        emit_step(
            "kakao.link_expiry",
            "success",
            f"NicePay/Kakao 二维码/长链有效期 {link_ttl_seconds // 60} 分钟；expiresAt={expires_at}",
        )
        return partial
    finally:
        close_sessions(checkout_session, promotion_session, provider_session)


def run_provider_link_flow(
    request: dict[str, Any],
    partial: dict[str, Any],
    token: str,
    timeout: int,
    poll_timeout: int,
    promo_enabled: bool,
    promo_id: str,
    promotion_region: str,
    max_amount: int,
) -> dict[str, Any]:
    total_attempts = max(1, min(100, int(request.get("checkoutAttempts") or 1)))
    tls_soft_limit = tls_soft_retry_limit(request)
    history: list[dict[str, Any]] = []
    last_error = ""
    attempt = 1
    while attempt <= total_attempts:
        attempt_partial = new_partial_result()
        attempt_request = dict(request)
        checkout_proxy, promotion_proxy, provider_proxy, attempt_region = resolve_attempt_proxies(request, attempt)
        attempt_request["checkoutProxy"] = checkout_proxy
        attempt_request["promotionProxy"] = promotion_proxy
        attempt_request["providerProxy"] = provider_proxy
        attempt_region = attempt_region or promotion_region
        preferred_profile = str(
            request.get("browserProfile")
            or request.get("clientFingerprint")
            or request.get("fingerprint")
            or ""
        ).strip()
        profile_seed = "|".join(
            [
                str(request.get("accessToken") or "")[:24],
                str(checkout_proxy or ""),
                str(promotion_proxy or ""),
                str(request.get("mode") or ""),
                str(attempt_region or promotion_region or ""),
            ]
        )
        # Rotate browser/version/hardware under fixed KR locale for each full chain,
        # then reuse that same identity for checkout + promotion + provider stages.
        weight_mode = bool(
            request.get("fingerprintWeightMode")
            if request.get("fingerprintWeightMode") is not None
            else request.get("fingerprint_weight_mode")
            if request.get("fingerprint_weight_mode") is not None
            else request.get("weightMode")
            if request.get("weightMode") is not None
            else False
        )
        profile = set_active_browser_profile(
            select_browser_profile(
                seed=profile_seed,
                preferred=preferred_profile,
                attempt=attempt,
                weight_mode=weight_mode,
                stage="checkout",
            )
        )
        locked_profile = normalize_browser_profile(profile)
        attempt_partial.setdefault("metadata", {}).update(browser_profile_metadata(profile))
        emit_step(
            "kakao.full_attempt",
            "running",
            (
                f"完整支付链 {attempt}/{total_attempts}：新 Checkout → Promotion({attempt_region or 'CUSTOM'}) "
                f"→ Taxes → Confirm → NicePay/Kakao；fingerprint={profile.get('id')} / {profile.get('impersonate')}"
            ),
        )

        tls_soft_used = 0
        result: dict[str, Any] | None = None
        while result is None:
            set_active_browser_profile(locked_profile)
            try:
                result = run_provider_link_attempt(
                    attempt_request,
                    token,
                    timeout,
                    poll_timeout,
                    promo_enabled,
                    promo_id,
                    attempt_region,
                    max_amount,
                    attempt,
                    total_attempts,
                    attempt_partial,
                )
            except TransportUnavailable:
                raise
            except Exception as error:
                last_error = redact_text(error)
                current_profile = active_browser_profile()
                pure_tls = is_transport_tls_error(last_error) and not is_impersonate_unsupported_error(last_error)
                # Pure TLS/library flakiness: soft-retry the same full attempt with the
                # same fingerprint. Do not burn attempt budget and do not rotate FP.
                if pure_tls and tls_soft_used < tls_soft_limit:
                    tls_soft_used += 1
                    emit_step(
                        "kakao.transport_retry",
                        "retrying",
                        (
                            f"完整支付链 {attempt}/{total_attempts} TLS 软重试 "
                            f"{tls_soft_used}/{tls_soft_limit}（同指纹，不消耗次数）：{last_error}"
                        ),
                    )
                    # Rebuild a clean partial for the same attempt index; keep locked FP.
                    attempt_partial = new_partial_result()
                    attempt_partial.setdefault("metadata", {}).update(browser_profile_metadata(locked_profile))
                    set_active_browser_profile(locked_profile)
                    time.sleep(min(2.0, 0.35 * tls_soft_used))
                    continue
                if pure_tls:
                    # Soft retries exhausted. Do not spend remaining full-chain attempts
                    # on the same OPENSSL/TLS library failure.
                    emit_step(
                        "kakao.full_attempt",
                        "failed",
                        (
                            f"完整支付链 {attempt}/{total_attempts} TLS 传输持续失败"
                            f"（已软重试 {tls_soft_used} 次，不换指纹、不继续扣次数）：{last_error}"
                        ),
                    )
                    partial.clear()
                    partial.update(attempt_partial)
                    partial.setdefault("metadata", {})["fullChainAttempts"] = list(history) + [{
                        "attempt": attempt,
                        "status": "transport_failed",
                        "promotionRegion": attempt_region,
                        "error": last_error[:900],
                        "impersonate": str((current_profile or {}).get("impersonate") or ""),
                        "transportFailure": True,
                        "tlsSoftRetries": tls_soft_used,
                    }]
                    raise FlowError(
                        f"TLS transport failed after {tls_soft_used} soft retries "
                        f"(same fingerprint, full-chain budget preserved): {last_error}"
                    )

                if is_impersonate_unsupported_error(last_error):
                    mark_bad_impersonate(current_profile.get("impersonate"), last_error)
                # Do not treat OPENSSL/TLS library noise as a bad fingerprint.
                if not pure_tls:
                    record_fingerprint_outcome(
                        current_profile,
                        stage="kakao.full_attempt",
                        status="failed",
                        detail=last_error,
                        weight_mode=bool(
                            request.get("fingerprintWeightMode")
                            if request.get("fingerprintWeightMode") is not None
                            else False
                        ),
                    )
                history.append({
                    "attempt": attempt,
                    "status": "failed",
                    "promotionRegion": attempt_region,
                    "error": last_error[:900],
                    "impersonate": str((current_profile or {}).get("impersonate") or ""),
                    "transportFailure": bool(pure_tls or is_impersonate_unsupported_error(last_error)),
                    "tlsSoftRetries": tls_soft_used,
                })
                # Only rotate after truly unsupported impersonate profiles.
                if attempt < total_attempts and is_impersonate_unsupported_error(last_error):
                    rotated = select_browser_profile(
                        seed=f"{profile_seed}|impersonate-rotate|{attempt}",
                        preferred=preferred_profile,
                        attempt=attempt + 1,
                        weight_mode=weight_mode,
                        stage="checkout",
                    )
                    if is_bad_impersonate(rotated.get("impersonate")) or str(rotated.get("impersonate") or "") == str(current_profile.get("impersonate") or ""):
                        for candidate in KAKAO_IMPERSONATE_CANDIDATES:
                            if is_bad_impersonate(candidate):
                                continue
                            if candidate == str(current_profile.get("impersonate") or ""):
                                continue
                            rotated = normalize_browser_profile(
                                {
                                    **rotated,
                                    "id": f"{candidate}-win",
                                    "impersonate": candidate,
                                    "platform": "Windows",
                                    "os_token": "Windows NT 10.0; Win64; x64",
                                }
                            )
                            break
                    set_active_browser_profile(rotated)
                    emit_step(
                        "fingerprint.rotate",
                        "running",
                        f"impersonate 不受支持，轮换指纹 → {rotated.get('id')} / {rotated.get('impersonate')}",
                    )
                partial.clear()
                partial.update(attempt_partial)
                partial.setdefault("metadata", {})["fullChainAttempts"] = list(history)
                status = "failed" if attempt == total_attempts else "retrying"
                emit_step(
                    "kakao.full_attempt",
                    status,
                    f"完整支付链 {attempt}/{total_attempts} 失败：{last_error}",
                )
                if attempt < total_attempts:
                    time.sleep(1)
                attempt += 1
                break

        if result is None:
            continue

        history.append({
            "attempt": attempt,
            "status": "success",
            "promotionRegion": attempt_region,
            "tlsSoftRetries": tls_soft_used,
        })
        record_fingerprint_outcome(
            active_browser_profile(),
            stage="kakao.full_attempt",
            outcome="success",
            status="success",
            detail=f"完整支付链第 {attempt}/{total_attempts} 次成功",
            weight_mode=bool(
                request.get("fingerprintWeightMode")
                if request.get("fingerprintWeightMode") is not None
                else False
            ),
        )
        result.setdefault("metadata", {})["fullChainAttempts"] = list(history)
        partial.clear()
        partial.update(result)
        emit_step("kakao.full_attempt", "success", f"完整支付链第 {attempt}/{total_attempts} 次成功")
        return partial

    raise FlowError(
        f"Kakao provider link failed after {total_attempts} full attempts: {last_error or 'unknown failure'}"
    )



def run_flow(request: dict[str, Any], partial: dict[str, Any]) -> dict[str, Any]:
    token = str(request["accessToken"])
    timeout = max(5, min(180, int(request.get("timeoutSeconds") or 45)))
    poll_timeout = max(30, min(180, int(request.get("pollTimeoutSeconds") or max(60, timeout * 2))))
    promo_enabled = bool(request.get("promoEnabled", True))
    promo_id = str(request.get("promoCampaignId") or "plus-1-month-free").strip()
    mode = request_mode(request)
    eligibility_only = mode == KAKAO_MODE_ELIGIBILITY
    if eligibility_only:
        promotion_default_pool = ["TR"]
    else:
        promotion_default_pool = ["JP", "VN", "TR"]
    region_pool = parse_region_pool(request.get("promotionRegions") or request.get("promotionRegion"))
    if not region_pool:
        region_pool = promotion_default_pool
    request["promotionRegions"] = region_pool
    promotion_region = region_pool[0]
    # All extraction channels are fail-closed zero-amount flows. Retain the
    # request field for compatibility, but never allow it to widen the gate.
    max_amount = 0

    partial.setdefault("metadata", {})["flowMode"] = mode
    preferred_profile = str(
        request.get("browserProfile")
        or request.get("clientFingerprint")
        or request.get("fingerprint")
        or ""
    ).strip()
    profile_seed = "|".join(
        [
            str(request.get("accessToken") or "")[:24],
            str(request.get("checkoutProxy") or ""),
            str(request.get("promotionProxy") or ""),
            str(request.get("mode") or ""),
            str(request.get("promotionRegion") or ""),
        ]
    )
    # One stable KR-aligned fingerprint for the whole helper run's first chain.
    # Provider-link retries may rotate non-locale fields while keeping ko-KR / Seoul.
    weight_mode = bool(
        request.get("fingerprintWeightMode")
        if request.get("fingerprintWeightMode") is not None
        else request.get("fingerprint_weight_mode")
        if request.get("fingerprint_weight_mode") is not None
        else request.get("weightMode")
        if request.get("weightMode") is not None
        else False
    )
    profile = set_active_browser_profile(
        select_browser_profile(
            seed=profile_seed,
            preferred=preferred_profile,
            attempt=1,
            weight_mode=weight_mode,
            stage="checkout",
        )
    )
    partial.setdefault("metadata", {}).update(browser_profile_metadata(profile))
    emit_step(
        "kakao.transport",
        "success",
        (
            f"curl_cffi impersonate={profile.get('impersonate')}；UA={profile.get('user_agent')}；"
            f"profile={profile.get('id')}；source={profile.get('source')}；"
            f"locale={KAKAO_LOCALE}；tz={KAKAO_TIMEZONE}"
        ),
    )

    if not eligibility_only:
        return run_provider_link_flow(
            request,
            partial,
            token,
            timeout,
            poll_timeout,
            promo_enabled,
            promo_id,
            promotion_region,
            max_amount,
        )

    try:
        checkout_session, _, _, _, _, _ = bootstrap_kakao_checkout(
            request,
            token,
            timeout,
            promo_enabled,
            promo_id,
            partial,
        )
    except KakaoNotAdvertised as error:
        return finish_eligibility_probe(partial, eligible=False, detail=redact_text(error))

    close_sessions(checkout_session)
    return finish_eligibility_probe(partial, eligible=True, detail="")


def read_request() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise FlowError("input exceeds 2 MiB")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FlowError("input must be valid UTF-8 JSON") from error
    return validate_request(payload)


def main() -> int:
    partial: dict[str, Any] = {
        "ok": False,
        "method": "kakao",
        "country": "KR",
        "currency": "KRW",
        "extractionStatus": "failed",
        "paymentStatus": "not_started",
    }
    try:
        if CurlCffiSession is None:
            raise TransportUnavailable("curl_cffi is not installed")
        request = read_request()
        result = run_flow(request, partial)
        emit({"type": "result", "result": result})
        return 0
    except TransportUnavailable as error:
        emit({"type": "unavailable", "message": redact_text(error)})
        return 78
    except FlowError as error:
        emit({"type": "error", "message": redact_text(error), "partial": partial})
        return 1
    except Exception as error:  # Keep unexpected failures structured and secret-safe.
        emit({"type": "error", "message": f"unexpected transport failure: {redact_text(error)}", "partial": partial})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
