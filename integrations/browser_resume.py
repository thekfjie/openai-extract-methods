"""Secure per-profile browser checkpoint capture and restoration."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from integrations.common import load_json_file, write_json_atomic


RESUME_STATE_FILE = ".automyai-resume.json"
MAX_TABS = 8
MAX_STORAGE_ITEMS = 128
MAX_STORAGE_VALUE_LENGTH = 65536
MAX_FORM_FIELDS = 64
ALLOWED_RESUME_HOSTS = {
    "chatgpt.com",
    "www.chatgpt.com",
    "auth.openai.com",
    "chat.openai.com",
    "openai.com",
    "www.openai.com",
}
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "code",
    "id_token",
    "refresh_token",
    "session_token",
    "state",
    "token",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def resume_state_path(profile_dir: Path) -> Path:
    return Path(profile_dir) / RESUME_STATE_FILE


def sanitize_resume_url(value: Any) -> str:
    text = str(value or "").strip()
    try:
        parsed = urlparse(text)
    except Exception:
        return ""
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        return ""
    if host in {"127.0.0.1", "localhost"} and parsed.port == 1455:
        return "https://chatgpt.com/"
    if host not in ALLOWED_RESUME_HOSTS:
        return ""
    query = urlencode(
        [(key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() not in SENSITIVE_QUERY_KEYS],
        doseq=True,
    )
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, ""))


def _storage_payload(driver: Any) -> dict[str, Any]:
    value = driver.execute_script(
        """
        const dump = (storage) => {
          const result = {};
          try {
            for (let index = 0; index < Math.min(storage.length, 128); index++) {
              const key = storage.key(index);
              if (!key) continue;
              const value = String(storage.getItem(key) ?? '');
              result[key.slice(0, 512)] = value.slice(0, 65536);
            }
          } catch (_) {}
          return result;
        };
        const forbidden = /(password|passcode|otp|one.?time|verification.?code|token|secret|credit|card|cvv|cvc)/i;
        const fields = [];
        for (const element of Array.from(document.querySelectorAll('input, textarea, select')).slice(0, 128)) {
          const type = String(element.type || element.tagName || '').toLowerCase();
          const name = String(element.name || '').slice(0, 256);
          const id = String(element.id || '').slice(0, 256);
          const autocomplete = String(element.autocomplete || '').slice(0, 128);
          if (['password', 'hidden', 'file'].includes(type) || forbidden.test(`${name} ${id} ${autocomplete}`)) continue;
          if (!name && !id && !autocomplete) continue;
          fields.push({
            name, id, autocomplete, type,
            value: String(element.value ?? '').slice(0, 4096),
            checked: Boolean(element.checked),
          });
          if (fields.length >= 64) break;
        }
        return { localStorage: dump(localStorage), sessionStorage: dump(sessionStorage), fields };
        """
    )
    return value if isinstance(value, dict) else {"localStorage": {}, "sessionStorage": {}, "fields": []}


def _all_cookies(driver: Any) -> list[dict[str, Any]]:
    try:
        payload = driver.execute_cdp_cmd("Network.getAllCookies", {})
        cookies = payload.get("cookies") if isinstance(payload, Mapping) else None
    except Exception:
        cookies = None
    if not isinstance(cookies, list):
        try:
            cookies = driver.get_cookies()
        except Exception:
            cookies = []
    return [dict(item) for item in cookies[:500] if isinstance(item, Mapping) and item.get("name")]


def capture_browser_checkpoint(
    driver: Any,
    profile_dir: Path,
    *,
    email: str = "",
    stage: str = "",
) -> dict[str, Any]:
    path = resume_state_path(profile_dir)
    handles = list(getattr(driver, "window_handles", []) or [])[:MAX_TABS]
    active_handle = str(getattr(driver, "current_window_handle", "") or "")
    tabs: list[dict[str, Any]] = []
    for handle in handles:
        try:
            driver.switch_to.window(handle)
            url = sanitize_resume_url(getattr(driver, "current_url", ""))
            if not url:
                continue
            storage = _storage_payload(driver)
            tabs.append(
                {
                    "url": url,
                    "title": str(getattr(driver, "title", "") or "")[:512],
                    "localStorage": dict(list((storage.get("localStorage") or {}).items())[:MAX_STORAGE_ITEMS]),
                    "sessionStorage": dict(list((storage.get("sessionStorage") or {}).items())[:MAX_STORAGE_ITEMS]),
                    "fields": list(storage.get("fields") or [])[:MAX_FORM_FIELDS],
                    "wasActive": handle == active_handle,
                }
            )
        except Exception:
            continue
    if active_handle:
        try:
            driver.switch_to.window(active_handle)
        except Exception:
            pass

    previous = load_json_file(path, {})
    history = list(previous.get("history") or []) if isinstance(previous, Mapping) else []
    current_url = next((tab["url"] for tab in tabs if tab.get("wasActive")), tabs[0]["url"] if tabs else "")
    event = {"stage": str(stage or "unknown")[:160], "url": current_url, "savedAt": now_iso()}
    if not history or history[-1].get("stage") != event["stage"] or history[-1].get("url") != event["url"]:
        history.append(event)

    snapshot = {
        "schemaVersion": 1,
        "email": str(email or "").strip(),
        "stage": str(stage or "unknown")[:160],
        "savedAt": now_iso(),
        "tabs": tabs,
        "cookies": _all_cookies(driver),
        "history": history[-20:],
    }
    write_json_atomic(path, snapshot, mode=0o600)
    return {"stage": snapshot["stage"], "tabs": len(tabs), "cookies": len(snapshot["cookies"]), "path": str(path)}


def _restore_cookies(driver: Any, cookies: Any) -> int:
    if not isinstance(cookies, list):
        return 0
    restored = 0
    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception:
        pass
    for raw in cookies[:500]:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or "")
        if not name:
            continue
        params: dict[str, Any] = {
            "name": name,
            "value": str(raw.get("value") or ""),
            "domain": str(raw.get("domain") or ""),
            "path": str(raw.get("path") or "/"),
            "secure": bool(raw.get("secure", False)),
            "httpOnly": bool(raw.get("httpOnly", False)),
        }
        if raw.get("sameSite") in {"Strict", "Lax", "None"}:
            params["sameSite"] = raw["sameSite"]
        try:
            expires = float(raw.get("expires") or raw.get("expiry") or 0)
            if expires > 0:
                params["expires"] = expires
        except (TypeError, ValueError):
            pass
        try:
            result = driver.execute_cdp_cmd("Network.setCookie", params)
            if not isinstance(result, Mapping) or result.get("success", True):
                restored += 1
        except Exception:
            continue
    return restored


def _restore_tab_payload(driver: Any, tab: Mapping[str, Any]) -> None:
    driver.execute_script(
        """
        const payload = arguments[0] || {};
        const restore = (storage, values) => {
          try { for (const [key, value] of Object.entries(values || {})) storage.setItem(key, String(value)); } catch (_) {}
        };
        restore(localStorage, payload.localStorage);
        restore(sessionStorage, payload.sessionStorage);
        const find = (field) => {
          if (field.id) { const value = document.getElementById(field.id); if (value) return value; }
          if (field.name) { const value = document.getElementsByName(field.name)[0]; if (value) return value; }
          if (field.autocomplete) return document.querySelector(`[autocomplete="${CSS.escape(field.autocomplete)}"]`);
          return null;
        };
        for (const field of payload.fields || []) {
          const element = find(field);
          if (!element) continue;
          try {
            if (field.type === 'checkbox' || field.type === 'radio') element.checked = Boolean(field.checked);
            else element.value = String(field.value ?? '');
            element.dispatchEvent(new Event('input', { bubbles: true }));
            element.dispatchEvent(new Event('change', { bubbles: true }));
          } catch (_) {}
        }
        """,
        {
            "localStorage": dict(tab.get("localStorage") or {}),
            "sessionStorage": dict(tab.get("sessionStorage") or {}),
            "fields": list(tab.get("fields") or []),
        },
    )


def restore_browser_checkpoint(driver: Any, profile_dir: Path) -> dict[str, Any]:
    path = resume_state_path(profile_dir)
    snapshot = load_json_file(path, {})
    if not isinstance(snapshot, Mapping):
        return {"restored": False, "reason": "missing"}
    tabs = [tab for tab in snapshot.get("tabs") or [] if isinstance(tab, Mapping) and sanitize_resume_url(tab.get("url"))]
    cookies = _restore_cookies(driver, snapshot.get("cookies"))
    restored_tabs = 0
    active_index = 0
    for index, tab in enumerate(tabs[:MAX_TABS]):
        url = sanitize_resume_url(tab.get("url"))
        if not url:
            continue
        try:
            if restored_tabs:
                driver.execute_script("window.open('about:blank', '_blank')")
                driver.switch_to.window(driver.window_handles[-1])
            driver.get(url)
            _restore_tab_payload(driver, tab)
            if tab.get("wasActive"):
                active_index = restored_tabs
            restored_tabs += 1
        except Exception:
            continue
    if restored_tabs:
        try:
            driver.switch_to.window(driver.window_handles[min(active_index, len(driver.window_handles) - 1)])
        except Exception:
            pass
    return {
        "restored": bool(restored_tabs or cookies),
        "stage": str(snapshot.get("stage") or ""),
        "tabs": restored_tabs,
        "cookies": cookies,
        "savedAt": str(snapshot.get("savedAt") or ""),
        "path": str(path),
    }
