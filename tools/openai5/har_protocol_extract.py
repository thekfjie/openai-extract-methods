#!/usr/bin/env python3
"""Extract a redacted protocol manifest from browser HAR captures.

The input captures may contain cookies, bearer tokens, OTPs, challenge blobs,
and account identifiers.  This tool never copies values from those fields into
the output; it keeps only request shape, endpoint path, status, redirect path,
cookie names, and JSON/form field names.  The result is suitable as the
fixture contract for a later browser/headless implementation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any


AUTH_HOSTS = {"chatgpt.com", "auth.openai.com"}
NOISE_PATH = re.compile(
    r"(?:^|/)(?:cdn|cdn-cgi|ces|awe|fonts|signals|collect|tr|px|action)(?:/|$)|"
    r"(?:favicon|manifest|\.css$|\.js$|\.woff2?$)"
)

REGISTRATION_PATHS = {
    ("chatgpt.com", "/api/auth/providers"): "discover_auth_provider",
    ("chatgpt.com", "/api/auth/csrf"): "fetch_csrf",
    ("chatgpt.com", "/api/auth/signin/openai"): "begin_openai_oauth",
    ("auth.openai.com", "/api/accounts/authorize"): "authorize_account",
    ("auth.openai.com", "/api/accounts/authorize/continue"): "submit_identifier",
    ("auth.openai.com", "/api/accounts/email-otp/validate"): "validate_email_otp",
    ("auth.openai.com", "/api/accounts/create_account"): "create_account",
    ("chatgpt.com", "/api/auth/callback/openai"): "oauth_callback",
    ("auth.openai.com", "/oauth/authorize"): "authorize_existing_session",
    ("auth.openai.com", "/api/accounts/session/select"): "select_session",
    ("auth.openai.com", "/api/accounts/add-phone/send"): "send_phone_otp",
    ("auth.openai.com", "/api/accounts/phone-otp/validate"): "validate_phone_otp",
    ("auth.openai.com", "/api/accounts/workspace/select"): "select_workspace",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _field_name(value: str) -> str:
    # Cloudflare/browser challenge forms use a per-request hash as the field
    # name.  Keep the fact that it is dynamic, never the actual identifier.
    if re.fullmatch(r"[0-9a-f]{32,128}", value, re.I):
        return "<dynamic_challenge_field>"
    if value.lower() in {"csrfToken".lower(), "code", "username", "phone_number", "session_id", "workspace_id"}:
        return value
    return value


def _body_fields(request: dict[str, Any]) -> list[str]:
    post = request.get("postData") or {}
    text = str(post.get("text") or "")
    if not text:
        return []
    mime = str(post.get("mimeType") or "").lower()
    if "json" in mime:
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            return ["<opaque>"]
        return sorted(_field_name(str(k)) for k in value) if isinstance(value, dict) else ["<opaque>"]
    if "form-urlencoded" in mime:
        fields = urllib.parse.parse_qsl(text, keep_blank_values=True)
        return sorted({_field_name(str(k)) for k, _ in fields})
    return ["<opaque>"]


def _response_cookie_names(response: dict[str, Any]) -> list[str]:
    names = {str(item.get("name")) for item in response.get("cookies", []) if item.get("name")}
    for header in response.get("headers", []):
        if str(header.get("name") or "").lower() == "set-cookie":
            raw = str(header.get("value") or "")
            if "=" in raw:
                names.add(raw.split("=", 1)[0].strip())
    return sorted(names)


def _location(response: dict[str, Any]) -> str:
    for header in response.get("headers", []):
        if str(header.get("name") or "").lower() != "location":
            continue
        parsed = urllib.parse.urlsplit(str(header.get("value") or ""))
        host = (parsed.hostname or "").lower()
        path = parsed.path or "/"
        return f"{host}{path}" if host else path
    return ""


def _entry_record(entry: dict[str, Any]) -> dict[str, Any] | None:
    request = entry.get("request") or {}
    parsed = urllib.parse.urlsplit(str(request.get("url") or ""))
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    if host not in AUTH_HOSTS or request.get("method") == "CONNECT":
        return None
    if NOISE_PATH.search(path) and (host, path) not in REGISTRATION_PATHS:
        return None
    method = str(request.get("method") or "GET").upper()
    key = (host, path)
    return {
        "method": method,
        "host": host,
        "path": path,
        "role": REGISTRATION_PATHS.get(key, "session_or_runtime_probe"),
        "status": int((entry.get("response") or {}).get("status") or 0),
        "body_fields": _body_fields(request),
        "redirect": _location(entry.get("response") or {}),
        "set_cookie_names": _response_cookie_names(entry.get("response") or {}),
    }


def summarize(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw.get("log", {}).get("entries", [])
    records: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for entry in entries:
        record = _entry_record(entry)
        if not record:
            continue
        key = (
            record["method"], record["host"], record["path"], record["status"],
            tuple(record["body_fields"]), record["redirect"], tuple(record["set_cookie_names"]),
        )
        if key in seen:
            continue
        seen.add(key)
        records.append(record)
    registration = [item for item in records if item["role"] != "session_or_runtime_probe"]
    return {
        "file": str(path),
        "sha256": _sha256(path),
        "entry_count": len(entries),
        "registration_records": registration,
        "registration_roles": sorted({item["role"] for item in registration}),
        "notes": [
            "Values, cookies, OTPs, challenge payloads, authorization tokens, and account identifiers are intentionally omitted.",
            "Repeated records are deduplicated by method/path/status/shape.",
        ],
    }


def build_manifest(paths: list[Path]) -> dict[str, Any]:
    captures = [summarize(path) for path in paths]
    roles = sorted({role for capture in captures for role in capture["registration_roles"]})
    return {
        "schema": "automyai.openai5.protocol-manifest.v1",
        "generated_by": "tools/openai5/har_protocol_extract.py",
        "captures": captures,
        "protocol_roles": roles,
        "branches": {
            "new_account": [
                "discover_auth_provider", "fetch_csrf", "begin_openai_oauth",
                "authorize_account", "validate_email_otp", "create_account", "oauth_callback",
            ],
            "existing_account": [
                "discover_auth_provider", "fetch_csrf", "begin_openai_oauth",
                "authorize_existing_session", "submit_identifier", "validate_email_otp", "oauth_callback",
            ],
            "post_callback_enrichment": [
                "select_session", "send_phone_otp", "validate_phone_otp", "select_workspace",
            ],
        },
        "dynamic_fields": {
            "csrf": ["csrfToken", "__Host-next-auth.csrf-token"],
            "otp": ["code"],
            "identity": ["username", "phone_number", "session_id", "workspace_id"],
            "challenge": ["<dynamic_challenge_field>", "opaque"],
        },
        "fingerprint_contract": {
            "source_service": "openai4 / automyai-fingerprint-api",
            "endpoint": "POST http://127.0.0.1:50001/oai/fingerprint/generate",
            "entry": "openai3",
            "preset": "windows-11-chrome",
            "browser_version": "150.0.0.0",
            "source": "local or cloud",
            "required_outputs": [
                "user_agent", "http_headers", "chromium_base_args", "chromium_cdp_commands",
                "sentinel_navigator", "device_id", "profile_id", "provenance",
            ],
            "reuse_rule": "One generated profile per mailbox transaction; preserve profile_id and device_id across the 500/409 login-recovery branch and transport-only restart.",
        },
        "headless_execution_plan": {
            "context": "No Chromium context on the normal path; bind the generated profile to the HTTP/Sentinel transaction.",
            "transport": "HTTP protocol client for provider, CSRF, OAuth, OTP, profile, callback, session, and import requests; stop with challenge_required if a browser-only challenge appears.",
            "otp_provider": "typed slot: EMAIL_OTP_PROVIDER",
            "phone_provider": "typed slot: PHONE_OTP_PROVIDER",
            "state_store": "typed slot: REGISTRATION_STATE_STORE",
            "dry_run_default": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("har", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    manifest = build_manifest(args.har)
    encoded = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
