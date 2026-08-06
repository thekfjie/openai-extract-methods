#!/usr/bin/env python3
"""Local-only comparison of the OpenAI3 fingerprint and at-maker schema.

It does not make network requests or alter registration behavior.  The
at-maker field list is copied from its ``DeviceProfile`` interface so we can
audit compatibility before choosing an implementation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.oai_fingerprint import generate_entry_fingerprint


AT_MAKER_FIELDS = {
    "id", "family", "browser", "os", "osVersion", "userAgent", "locale",
    "languages", "acceptLanguage", "timezoneId", "viewportWidth", "viewportHeight",
    "screenWidth", "screenHeight", "outerWidth", "outerHeight", "deviceScaleFactor",
    "hardwareConcurrency", "deviceMemory", "jsHeapSizeLimit", "platform", "vendor",
    "maxTouchPoints", "hasTouch", "isMobile", "colorDepth", "pixelDepth",
}

OUR_TOP_LEVEL_FIELDS = {
    "profile_id", "device_id", "user_agent", "lang", "lang_full", "languages",
    "timezone", "screen_width", "screen_height", "device_pixel_ratio",
    "hardware_concurrency", "device_memory", "platform", "webgl_vendor",
    "webgl_renderer", "max_touch_points", "mobile", "sec_ch_ua",
    "sec_ch_ua_platform", "sec_ch_ua_mobile", "impersonate", "profile",
}


def compare(seed: str = "fingerprint-compare") -> dict:
    ours = generate_entry_fingerprint("openai3", seed=seed)
    if not ours:
        raise RuntimeError("OpenAI3 fingerprint generation returned no profile")
    return {
        "ours": {
            "entry": ours.get("entry"),
            "preset": ours.get("preset"),
            "browser_major": str(ours.get("user_agent") or "").split("Chrome/")[-1].split(".")[0],
            "fields": sorted(OUR_TOP_LEVEL_FIELDS),
            "profile_fields": sorted((ours.get("profile") or {}).keys()),
        },
        "at_maker": {
            "fields": sorted(AT_MAKER_FIELDS),
            "client_hint_fields": [
                "secChUa", "secChUaFullVersionList", "secChUaMobile",
                "secChUaPlatform", "secChUaPlatformVersion", "secChViewportWidth",
            ],
        },
        "compatibility": {
            "shared_concepts": [
                "user agent", "locale/languages", "timezone", "viewport/screen",
                "device scale factor", "hardware concurrency", "device memory",
                "platform/vendor", "touch capability", "Client Hints",
            ],
            "ours_extra": ["profile_id", "device_id", "Sentinel navigator", "WebGL renderer"],
            "at_maker_extra": ["family", "browser", "osVersion", "outerWidth", "outerHeight", "jsHeapSizeLimit"],
            "decision": "keep openai3 central profile; adapt only a local schema mapper if needed",
        },
    }


if __name__ == "__main__":
    print(json.dumps(compare(), ensure_ascii=False, indent=2))
