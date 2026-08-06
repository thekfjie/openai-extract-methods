"""Device fingerprint generation for PayPal anti-fraud system."""
import json
import hashlib
import random
import time
import urllib.parse
import uuid
from copy import deepcopy
from typing import Iterable, Mapping
from paypal.models import generate_eteid
from config import SCREEN, USER_AGENT


def _compact_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


_PROFILE_VARIANTS = (
    {
        "screen": {"colorDepth": 24, "pixelDepth": 24, "height": 1080, "width": 1920, "availHeight": 1040, "availWidth": 1920},
        "viewport": {"width": 1324, "height": 842},
        "deviceMemory": 8,
        "hardwareConcurrency": 8,
        "webgl": "Google Inc. (NVIDIA)|ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "webglVendorAndRenderer": "Google Inc. (NVIDIA)~ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "fonts": ["Arial", "Calibri", "Courier New", "Georgia", "Times New Roman", "Verdana"],
    },
    {
        "screen": {"colorDepth": 24, "pixelDepth": 24, "height": 900, "width": 1440, "availHeight": 860, "availWidth": 1440},
        "viewport": {"width": 1180, "height": 760},
        "deviceMemory": 16,
        "hardwareConcurrency": 12,
        "webgl": "Google Inc. (Intel)|ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "webglVendorAndRenderer": "Google Inc. (Intel)~ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "fonts": ["Arial", "Calibri", "Consolas", "Georgia", "Segoe UI", "Times New Roman"],
    },
    {
        "screen": {"colorDepth": 24, "pixelDepth": 24, "height": 768, "width": 1366, "availHeight": 728, "availWidth": 1366},
        "viewport": {"width": 1100, "height": 650},
        "deviceMemory": 8,
        "hardwareConcurrency": 8,
        "webgl": "Google Inc. (Intel)|ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "webglVendorAndRenderer": "Google Inc. (Intel)~ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "fonts": ["Arial", "Courier New", "Georgia", "Segoe UI", "Tahoma", "Verdana"],
    },
)


def build_task_fingerprint(country: str, locale: str, seed: str | None = None) -> dict:
    """Create one stable, coherent task profile.

    This is deliberately a consistency profile: it keeps all declared values
    aligned for one task and rotates only between ordinary Windows/Chrome
    configurations. It is not a browser-runtime replacement and does not
    suppress PayPal challenges.
    """
    selected_seed = str(seed or uuid.uuid4().hex)
    rng = random.Random(selected_seed)
    variant = deepcopy(rng.choice(_PROFILE_VARIANTS))
    country = str(country or "GB").upper()
    locale = str(locale or ("en_GB" if country == "GB" else "pt_BR"))
    if country == "BR":
        language = "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
        timezone = "America/Sao_Paulo"
        timezone_offset = 180
    elif country in {"GB", "UK"}:
        language = "en-GB,en;q=0.9,en-US;q=0.8"
        timezone = "Europe/London"
        timezone_offset = 0
    else:
        language = f"{locale.replace('_', '-')},{locale.split('_')[0]};q=0.9,en-US;q=0.8,en;q=0.7"
        timezone = "UTC"
        timezone_offset = 0
    profile_id = hashlib.sha256(selected_seed.encode("utf-8")).hexdigest()[:16]
    return {
        "profileId": profile_id,
        "seed": selected_seed,
        "country": country,
        "locale": locale,
        "language": locale.replace("_", "-"),
        "user_agent": USER_AGENT,
        "platform": "Win32",
        "sec_ch_ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"Windows"',
        "accept_language": language,
        "timezone": timezone,
        "timezoneOffset": timezone_offset,
        "screen": variant["screen"],
        "viewport": variant["viewport"],
        "deviceMemory": variant["deviceMemory"],
        "hardwareConcurrency": variant["hardwareConcurrency"],
        "webgl": variant["webgl"],
        "webglVendorAndRenderer": variant["webglVendorAndRenderer"],
        "fonts": variant["fonts"],
        "audio": f"{rng.uniform(110.0, 140.0):.14f}",
        "eventSeed": rng.randrange(1, 2**31 - 1),
    }


def _active_profile(session=None, profile: Mapping | None = None) -> Mapping:
    if profile:
        return profile
    state = getattr(session, "state", None)
    return getattr(state, "fingerprint_profile", {}) or {}


def build_fn_sync_data(
    correlation_id: str,
    *,
    source: str = "IWC_NEXT_CHECKOUT",
    include_d: bool = False,
    profile: Mapping | None = None,
) -> str:
    """Build the FraudNet fn_sync_data hidden field.

    The /pay create-account action uses the BA token with IWC_NEXT_CHECKOUT.
    The checkoutweb SignUpNewMember submit uses the EC token with
    IWC_LOGIN_APP and includes typing/timing data (`d`).
    """
    now_ms = int(time.time() * 1000)
    active = dict(profile or {})
    screen = active.get("screen") or SCREEN
    user_agent = str(active.get("user_agent") or USER_AGENT)
    data = {
        "SC_VERSION": "2.0.4",
        "syncStatus": "data",
        "f": correlation_id,
        "s": source,
        "chk": {
            "ts": now_ms,
            "eteid": generate_eteid(),
            "tts": random.randint(20, 80),
        },
        "dc": _compact_json({
            "screen": screen,
            "ua": user_agent,
        }),
        "wv": False,
        "web_integration_type": "WEB_REDIRECT",
        "cookie_enabled": True,
    }
    if include_d:
        ts2_parts = [
            ("Di0", random.randint(12_000, 24_000)),
            ("Di1", random.randint(5, 18)),
            ("Di2", random.randint(80, 180)),
            ("Ui0", 24),
            ("Ui1", random.randint(40, 80)),
            ("Ui2", random.randint(45, 95)),
            ("Di3", random.randint(2_000, 5_000)),
            ("Di4", 24),
            ("Di5", random.randint(60, 140)),
            ("Uh", random.randint(2_500, 5_500)),
        ]
        base_a = random.randint(18_000, 56_000)
        rdt_chunks = []
        for _ in range(20):
            a = max(1000, base_a + random.randint(-28_000, 28_000))
            b = a + random.randint(-250, 250)
            c = max(1000, a - random.randint(250, 700))
            rdt_chunks.append(f"{a},{b},{c}")
        rdt_tail = f"{random.randint(8_000, 28_000)},{random.randint(20, 80)}"
        data["d"] = {
            "ts2": "".join(f"{k}:{v}" for k, v in ts2_parts),
            "rDT": ":".join(rdt_chunks) + ":" + rdt_tail,
        }
    return urllib.parse.quote(_compact_json(data), safe="")


def build_signup_fn_sync_data(ec_token: str, profile: Mapping | None = None) -> str:
    """Build SignUpNewMember fn_sync_data using the EC token."""
    return build_fn_sync_data(ec_token, source="IWC_LOGIN_APP", include_d=True, profile=profile)


def send_device_fingerprint(
    session,
    correlation_id: str,
    *,
    app_id: str = "IWC_NEXT_CHECKOUT",
    referer: str = "https://www.paypal.com/",
    wrapped: bool = False,
    profile: Mapping | None = None,
):
    """Send device fingerprint to PayPal's p1, p2, w endpoints.

    These requests establish the sc_f, KHcl0EuY7AKSMgfvHl7J5E7hPtK, and ddi cookies
    which are required for the checkout flow.
    """
    from loguru import logger

    active = _active_profile(session, profile)
    screen = dict(active.get("screen") or SCREEN)
    base_headers = {
        "Content-Type": "application/json",
        "Origin": "https://www.paypal.com",
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }

    ts = int(time.time() * 1000)

    # p1 payload - primary device fingerprint
    p1_data = {
        "f": correlation_id,
        "s": app_id,
        "cb1": "close498",
        "cb2": f"fingerprintSetup{ts}",
        "v": "5.8.2",
        "t": ts,
        "fp2": {
            "browser": {
                "ua": USER_AGENT,
                "lang": str(active.get("language") or (getattr(session.state, "locale", None) or "en-GB").replace("_", "-")),
                "colorDepth": screen["colorDepth"],
                "deviceMemory": int(active.get("deviceMemory") or 32),
                "hardwareConcurrency": int(active.get("hardwareConcurrency") or 16),
                "screenResolution": [screen["height"], screen["width"]],
                "availableScreenResolution": [screen["availHeight"], screen["availWidth"]],
                "timezoneOffset": int(active.get("timezoneOffset") or 0),
                "timezone": str(active.get("timezone") or "UTC"),
                "sessionStorage": True,
                "localStorage": True,
                "indexedDb": True,
                "openDatabase": True,
                "cpuClass": "not available",
                # Keep navigator.platform coherent with the Windows Chrome
                # UA/sec-ch headers used by the captured checkout flow.
                "platform": "Win32",
                "doNotTrack": "not available",
                "plugins": [],
                "webgl": str(active.get("webgl") or "Google Inc. (NVIDIA)|ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
                "webglVendorAndRenderer": str(active.get("webglVendorAndRenderer") or "Google Inc. (NVIDIA)~ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
                "hasLiedLanguages": False,
                "hasLiedResolution": False,
                "hasLiedOs": False,
                "hasLiedBrowser": False,
                "touchSupport": [0, False, False],
                "fonts": list(active.get("fonts") or ["Arial", "Courier New", "Georgia", "Times New Roman", "Verdana"]),
                "audio": str(active.get("audio") or "124.04347527516074"),
            },
        },
    }

    try:
        logger.info(f"Sending device fingerprint p1 app_id={app_id}...")
        p1_body = (
            {"appId": app_id, "correlationId": correlation_id, "payload": p1_data}
            if wrapped else p1_data
        )
        session.post(
            "https://c.paypal.com/v1/r/d/b/p1",
            json=p1_body,
            headers=base_headers,
        )
    except Exception as e:
        logger.warning(f"Fingerprint p1 failed: {e}")

    # p2 payload - secondary fingerprint
    p2_data = {
        "f": correlation_id,
        "s": app_id,
        "t": ts,
        "v": "5.8.2",
    }

    try:
        logger.info(f"Sending device fingerprint p2 app_id={app_id}...")
        p2_body = (
            {"appId": app_id, "correlationId": correlation_id, "payload": p2_data}
            if wrapped else p2_data
        )
        session.post(
            "https://c.paypal.com/v1/r/d/b/p2",
            json=p2_body,
            headers=base_headers,
        )
    except Exception as e:
        logger.warning(f"Fingerprint p2 failed: {e}")

    # w payload - WebSocket-level fingerprint
    w_data = {
        "f": correlation_id,
        "s": app_id,
        "t": ts,
    }

    try:
        logger.info(f"Sending device fingerprint w app_id={app_id}...")
        w_body = (
            {"appId": app_id, "correlationId": correlation_id, "payload": w_data}
            if wrapped else w_data
        )
        session.post(
            "https://c.paypal.com/v1/r/d/b/w",
            json=w_body,
            headers=base_headers,
        )
    except Exception as e:
        logger.warning(f"Fingerprint w failed: {e}")


def send_signup_field_events(
    session,
    ec_token: str,
    field_ids: Iterable[str],
    *,
    app_id: str = "CHECKOUTUINODEWEB_ONBOARDING_LITE",
):
    """Emit lightweight FraudNet field timing beacons for the signup form."""
    from loguru import logger

    headers = {
        "Origin": "https://www.paypal.com",
        "Referer": getattr(session.state, "signup_url", "") or "https://www.paypal.com/",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    elapsed = random.randint(700, 1400)
    for field_id in field_ids:
        if field_id in {"password", "cardCvv"}:
            ts_obj = (
                f"Di0:{elapsed}Di1:{random.randint(7, 45)}Di2:{random.randint(80, 420)}"
                f"Ui0:{random.randint(20, 45)}Ui1:{random.randint(45, 120)}"
                f"Uh:{random.randint(1200, 6500)}"
            )
        elif field_id == "cardNumber":
            ts_obj = (
                f"Dk91:{elapsed}Di0:{random.randint(120, 320)}"
                f"Uk91:{random.randint(80, 180)}Uh:{random.randint(1200, 2200)}"
            )
        else:
            ts_obj = f"Dk000:{elapsed}Uk000:{random.randint(4, 13)}Uh:{random.randint(850, 1300)}"

        payload = {
            "tsobj": {
                "elid": field_id,
                "sid": app_id,
                "tst": app_id,
                "wsps": False,
                "ts": ts_obj,
                "pf": {"psu": False, "val": False},
            }
        }
        try:
            session.get(
                "https://c.paypal.com/v1/r/d/b/w",
                params={
                    "f": ec_token,
                    "s": app_id,
                    "d": urllib.parse.quote(_compact_json(payload), safe=""),
                },
                headers=headers,
            )
        except Exception as e:
            logger.debug(f"Signup field event {field_id} failed: {e}")
        elapsed += random.randint(120, 380)
