#!/usr/bin/env python3
"""Grok registration bot (Linux) + dual credential import helpers.

Usage:
  python3 grok_signup.py --email user@example.com
  python3 grok_signup.py --count 1
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import string
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.common import sanitize_sso
from integrations.cpa import CpaClient
from integrations.grok2api_client import Grok2ApiClient
from integrations.grok_oauth import sso_to_token
from integrations.mail_policy import generate_domain_emails


def load_app_config() -> dict[str, Any]:
    path = ROOT / "config.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


CFG = load_app_config()


def cfg(key: str, default: Any = "") -> Any:
    env = os.environ.get(key)
    if env is not None and str(env) != "":
        return env
    return CFG.get(key, default)


API_BASE = str(cfg("GROK_SIGNUP_API_BASE", f"http://127.0.0.1:{os.getenv('AUTOMYAI_PORT') or cfg('PORT', '13030')}/api")).rstrip("/")
DISPLAY = str(cfg("BROWSER_DISPLAY", ":1"))
PROXY = str(cfg("GROK_SIGNUP_PROXY") or cfg("UC_SIGNUP_PROXY") or cfg("BROWSER_PROXY") or "")
CF_ENABLED = str(cfg("GROK_CF_CLEARANCE_ENABLED", cfg("UC_SIGNUP_CF_CLEARANCE_ENABLED", "false"))).lower() in {"1", "true", "yes", "on"}
CF_API = str(cfg("GROK_CF_CLEARANCE_API_URL", cfg("UC_SIGNUP_CF_CLEARANCE_API_URL", "http://127.0.0.1:18191/v1")))
CF_TARGET = str(cfg("GROK_CF_CLEARANCE_TARGET_URL", "https://accounts.x.ai/sign-up"))
SIGNUP_URL = str(cfg("GROK_SIGNUP_URL", "https://accounts.x.ai/sign-up?redirect=grok-com"))
PASSWORD = str(cfg("GROK_SIGNUP_PASSWORD") or cfg("SIGNUP_PASSWORD") or ("Grok" + secrets.token_hex(6) + "!"))


def log(msg: str, level: str = "info") -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def api(method: str, path: str, body: dict | None = None) -> Any:
    url = f"{API_BASE}{path}"
    data = None
    headers = {"Accept": "application/json", "User-Agent": "grok-signup/1.0"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API {method} {path} -> {error.code}: {raw[:300]}") from error
    except URLError as error:
        raise RuntimeError(f"API 连接失败: {error}") from error


def random_name() -> tuple[str, str]:
    first = secrets.choice(["Alex", "Jordan", "Taylor", "Casey", "Riley", "Morgan", "Quinn", "Avery"])
    last = secrets.choice(["Smith", "Johnson", "Lee", "Brown", "Davis", "Wilson", "Clark", "Young"])
    return first, last


def fetch_cf_clearance() -> dict[str, Any] | None:
    if not CF_ENABLED:
        return None
    payload = {
        "cmd": "request.get",
        "url": CF_TARGET,
        "maxTimeout": int(cfg("GROK_CF_CLEARANCE_TIMEOUT_SECONDS", cfg("UC_SIGNUP_CF_CLEARANCE_TIMEOUT_SECONDS", 90))) * 1000,
    }
    if PROXY:
        payload["proxy"] = {"url": PROXY}
    req = Request(
        CF_API,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        solution = data.get("solution") if isinstance(data, dict) else None
        if not isinstance(solution, dict):
            log(f"CF clearance 响应异常: {str(data)[:200]}", "warn")
            return None
        cookies = solution.get("cookies") or []
        ua = solution.get("userAgent") or ""
        clearance = ""
        for cookie in cookies:
            if str(cookie.get("name")) == "cf_clearance":
                clearance = str(cookie.get("value") or "")
                break
        if not clearance:
            log("CF clearance 未拿到 cf_clearance", "warn")
            return None
        log("CF clearance 获取成功")
        return {"cf_clearance": clearance, "user_agent": ua, "cookies": cookies}
    except Exception as error:
        log(f"CF clearance 失败: {error}", "warn")
        return None


def build_driver(cf_bundle: dict[str, Any] | None = None):
    import undetected_chromedriver as uc

    options = uc.ChromeOptions()
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,900")
    if PROXY:
        options.add_argument(f"--proxy-server={PROXY}")
    if cf_bundle and cf_bundle.get("user_agent"):
        options.add_argument(f"--user-agent={cf_bundle['user_agent']}")
    # unique profile
    profile = ROOT / "data" / "browser_profiles_grok" / f"run_{int(time.time())}_{secrets.token_hex(3)}"
    profile.mkdir(parents=True, exist_ok=True)
    options.add_argument(f"--user-data-dir={profile}")
    display = DISPLAY if DISPLAY.startswith(":") else f":{DISPLAY}"
    os.environ["DISPLAY"] = display
    driver = uc.Chrome(options=options)
    driver.set_page_load_timeout(90)
    if cf_bundle and cf_bundle.get("cf_clearance"):
        driver.get("https://accounts.x.ai/")
        try:
            driver.add_cookie(
                {
                    "name": "cf_clearance",
                    "value": cf_bundle["cf_clearance"],
                    "domain": ".x.ai",
                    "path": "/",
                }
            )
        except Exception as error:
            log(f"写入 cf_clearance cookie 失败: {error}", "warn")
    return driver, profile


def wait_css(driver, css: str, timeout: float = 30):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, css)))


def type_into(driver, css: str, text: str, timeout: float = 20) -> None:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys

    el = wait_css(driver, css, timeout=timeout)
    el.click()
    try:
        el.clear()
    except Exception:
        pass
    el.send_keys(Keys.CONTROL, "a")
    el.send_keys(text)
    time.sleep(0.2)


def click_text(driver, texts: list[str], timeout: float = 10) -> bool:
    from selenium.webdriver.common.by import By

    deadline = time.time() + timeout
    lowered = [t.lower() for t in texts]
    while time.time() < deadline:
        for el in driver.find_elements(By.XPATH, "//button|//a|//div[@role='button']|//span"):
            try:
                label = (el.text or el.get_attribute("aria-label") or "").strip().lower()
            except Exception:
                continue
            if any(t in label for t in lowered) and el.is_displayed():
                try:
                    el.click()
                    return True
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                    return True
        time.sleep(0.4)
    return False


def extract_sso_from_driver(driver) -> str:
    # cookies first
    for cookie in driver.get_cookies():
        if cookie.get("name") == "sso" and cookie.get("value"):
            return sanitize_sso(cookie.get("value"))
    # local/session storage fallback not typical for sso
    return ""


def poll_email_code(email: str, timeout: int = 180) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            payload = api("GET", f"/api/email-code?email={email}")
            code = str(payload.get("code") or payload.get("verificationCode") or "").strip()
            if not code:
                # nested
                code = str(((payload.get("data") or {}) if isinstance(payload.get("data"), dict) else {}).get("code") or "").strip()
            if code:
                return code
        except Exception as error:
            log(f"读取验证码失败: {error}", "warn")
        time.sleep(5)
    raise TimeoutError(f"邮箱验证码超时: {email}")


def import_dual(sso: str, email: str) -> dict[str, Any]:
    result: dict[str, Any] = {"email": email, "sso": sso[:12] + "...", "grok2api": None, "cpa": None, "token": None}
    # grok2api SSO import
    try:
        g2 = Grok2ApiClient(
            base_url=str(cfg("GROK2API_BASE_URL", "http://127.0.0.1:8000")),
            admin_key=str(cfg("GROK2API_ADMIN_KEY", "")),
            pool=str(cfg("GROK2API_POOL", "basic")),
        )
        result["grok2api"] = g2.import_sso_tokens([sso], tags=["helpoai", "auto"])
        log("已导入 grok2api")
    except Exception as error:
        result["grok2api"] = {"ok": False, "error": str(error)}
        log(f"导入 grok2api 失败: {error}", "warn")

    # CPA via device flow
    try:
        token = sso_to_token(sso, proxy=PROXY, log=lambda m: log(m))
        if not token:
            raise RuntimeError("device-flow 未返回 token")
        result["token"] = {
            "email": token.get("_email") or token.get("email") or email,
            "hasAccess": bool(token.get("access_token")),
            "hasRefresh": bool(token.get("refresh_token")),
        }
        cpa = CpaClient(
            enabled=str(cfg("CPA_ENABLED", "true")).lower() in {"1", "true", "yes", "on"},
            auth_dir=str(cfg("CPA_AUTH_DIR", "/opt/cliproxyapi/auths")),
            remote_url=str(cfg("CPA_REMOTE_URL", "http://127.0.0.1:8317")),
            management_key=str(cfg("CPA_MANAGEMENT_KEY", "")),
        )
        result["cpa"] = cpa.import_token(token, email=email or token.get("_email") or "")
        log("已导入 CPA")
    except Exception as error:
        result["cpa"] = {"ok": False, "error": str(error)}
        log(f"导入 CPA 失败: {error}", "warn")
    return result


def run_once(email: str, allocation: dict[str, Any] | None = None) -> dict[str, Any]:
    log(f"开始 Grok 注册: {email}")
    cf_bundle = fetch_cf_clearance()
    driver = None
    profile = None
    try:
        driver, profile = build_driver(cf_bundle)
        driver.get(SIGNUP_URL)
        time.sleep(2)
        # Best-effort form fill. UI may change; keep resilient selectors.
        first, last = random_name()
        # email
        for selector in ["input[type='email']", "input[name='email']", "input[autocomplete='email']", "input[placeholder*='mail' i]"]:
            try:
                type_into(driver, selector, email, timeout=8)
                break
            except Exception:
                continue
        click_text(driver, ["continue", "next", "继续", "下一步", "sign up", "注册"], timeout=5)
        time.sleep(1)
        # password if present
        for selector in ["input[type='password']", "input[name='password']"]:
            try:
                type_into(driver, selector, PASSWORD, timeout=6)
                break
            except Exception:
                continue
        # names
        for selector, value in [
            ("input[name='firstName'],input[name='first_name'],input[autocomplete='given-name']", first),
            ("input[name='lastName'],input[name='last_name'],input[autocomplete='family-name']", last),
        ]:
            try:
                type_into(driver, selector, value, timeout=4)
            except Exception:
                pass
        click_text(driver, ["continue", "next", "继续", "create", "sign up", "注册"], timeout=5)
        # verification code
        try:
            code = poll_email_code(email, timeout=int(cfg("GROK_EMAIL_CODE_TIMEOUT_SECONDS", 180)))
            log(f"验证码: {code}")
            for selector in ["input[name='code']", "input[autocomplete='one-time-code']", "input[inputmode='numeric']", "input[type='text']"]:
                try:
                    type_into(driver, selector, code, timeout=6)
                    break
                except Exception:
                    continue
            click_text(driver, ["continue", "verify", "确认", "继续", "next"], timeout=8)
        except Exception as error:
            log(f"验证码阶段: {error}", "warn")

        # wait for SSO cookie
        sso = ""
        for _ in range(60):
            sso = extract_sso_from_driver(driver)
            if sso:
                break
            # maybe redirected to grok.com
            try:
                if "grok.com" in (driver.current_url or ""):
                    sso = extract_sso_from_driver(driver)
                    if sso:
                        break
            except Exception:
                pass
            time.sleep(2)
        if not sso:
            raise RuntimeError("注册流程结束但未捕获到 SSO cookie")

        dual = import_dual(sso, email)
        # notify server
        try:
            api(
                "POST",
                "/api/grok/signup/result",
                {
                    "email": email,
                    "password": PASSWORD,
                    "sso": sso,
                    "result": dual,
                    "ok": True,
                    "platform": "grok",
                    "allocation": allocation or {},
                },
            )
        except Exception as error:
            log(f"回传结果失败: {error}", "warn")
        dual["ok"] = True
        dual["password"] = PASSWORD
        return dual
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


def allocate_email_record() -> dict[str, Any]:
    """领取 Grok 邮箱并保留 OutlookEmail 项目 claim 元数据。"""
    try:
        payload = api("POST", "/api/email-queue/allocate", {"platform": "grok", "preferInventory": True})
        email = str(payload.get("email") or "").strip()
        if email:
            return {
                "email": email,
                "allocation": payload.get("allocation") if isinstance(payload.get("allocation"), dict) else None,
                "source": payload.get("source") or "",
            }
    except Exception as error:
        log(f"allocate email 失败: {error}", "warn")
        if str(cfg("GROK_ALLOW_DOMAIN_FALLBACK", cfg("ALLOW_DOMAIN_MAIL_FALLBACK", "false"))).lower() not in {"1", "true", "yes", "on"}:
            raise
    if str(cfg("GROK_ALLOW_DOMAIN_FALLBACK", cfg("ALLOW_DOMAIN_MAIL_FALLBACK", "false"))).lower() in {"1", "true", "yes", "on"}:
        domain = str(cfg("GROK_DOMAIN_ROOT") or cfg("DOMAIN_MAIL_ROOT") or "").strip()
        if domain:
            return {
                "email": generate_domain_emails(
                    root_domain=domain,
                    count=1,
                    prefer_subdomain=True,
                    subdomains=str(cfg("DOMAIN_MAIL_SUBDOMAINS", "sub,x,grok")),
                    name_style=str(cfg("DOMAIN_MAIL_NAME_STYLE", "outlook")),
                    name_digits=int(cfg("DOMAIN_MAIL_NAME_DIGITS", "4") or 4),
                )[0],
                "allocation": None,
                "source": "domain_sub",
            }
    raise RuntimeError("无法分配邮箱：OutlookEmail 项目池为空；未启用自有域名回退")


def allocate_email() -> str:
    return str(allocate_email_record().get("email") or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default="")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--sso-only", default="", help="Skip browser; import existing SSO")
    args = parser.parse_args()
    if args.sso_only:
        email = args.email or ""
        result = import_dual(args.sso_only, email)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if (result.get("grok2api", {}) or {}).get("ok") or (result.get("cpa") or {}).get("localPath") else 1

    count = max(1, int(args.count or 1))
    results = []
    for i in range(count):
        email = ""
        allocation = None
        try:
            if args.email and i == 0:
                email = args.email
            else:
                allocated = allocate_email_record()
                email = str(allocated.get("email") or "").strip()
                allocation = allocated.get("allocation") if isinstance(allocated.get("allocation"), dict) else None
            results.append(run_once(email, allocation=allocation))
        except Exception as error:
            log(f"注册失败 {email}: {error}", "error")
            results.append({"ok": False, "email": email, "error": str(error)})
            try:
                api("POST", "/api/grok/signup/result", {"email": email, "platform": "grok", "allocation": allocation or {}, "ok": False, "error": str(error)})
            except Exception:
                pass
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0 if any(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
