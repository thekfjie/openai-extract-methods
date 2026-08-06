"""
Microsoft Outlook 纯协议注册脚本 (Automyai tools/outlook_register)
通过 HTTP API 直接调用完成注册, 无需浏览器。

注意: 这是「微软邮箱本身」的注册机，不是 ChatGPT/OpenAI 注册。
产物通常为 4 段:
  email----password----client_id----microsoft_refresh_token
可导入 mail_manager / OutlookEmail 号池，供后续 OpenAI 2/3 等流程使用。

注册流程:
  1. GET  /signup           -> 获取 ServerData (apiCanary, uaid, DFP/PX iframe)
  2. POST CheckAvailableSigninNames -> 检查用户名可用性
  3. POST risk/initialize    -> 风险评估初始化, 获得 continuationToken
  4. POST risk/verify (1st)  -> 不带 PX tokens, 触发 riskChallengeRequired
  5. CaptchaRun PxCaptcha2   -> silent token -> press token
  6. POST risk/verify (2nd)  -> 带 challengeSolution (press token), 获得 finalToken
  7. POST CreateAccount      -> 创建账号
  8. OAuth2 native client    -> 换取 Microsoft refresh_token (Graph mail scopes)

依赖:
  pip install curl_cffi requests

用法:
  python outlook_register.py --cr-token TOKEN --proxy-file proxies.txt --country CN
  # 或:
  export CAPTCHARUN_TOKEN=...
  export OUTLOOK_REGISTER_OUTPUT=/opt/automyai/data/outlook_register/accounts.txt
  python outlook_register.py --proxy-file proxies.txt --threads 3
"""

import re
import json
import time
import random
import string
import argparse
import threading
import hashlib
from curl_cffi import requests as cffi_requests
import requests as req
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, urlencode

# 线程局部存储: 每个线程的日志标签
_thread_local = threading.local()
_print_lock = threading.Lock()

# 全局统计
_stats = {"reg_and_oauth": 0, "reg_ok_oauth_fail": 0, "captcha_fail": 0, "reg_fail": 0, "no_proxy": 0, "import_ok": 0, "import_fail": 0}
_stats_lock = threading.Lock()

# 导入服务器配置 (通过命令行参数覆盖)
_import_url = None
_import_password = None
_import_auth_cookie = None
_import_cookie_lock = threading.Lock()


def set_thread_tag(tag: str):
    """设置当前线程的日志标签 (如 T1, T2)"""
    _thread_local.tag = tag


def _get_tag() -> str:
    return getattr(_thread_local, "tag", "")


# ============================================================
#  CaptchaRun API
# ============================================================

CAPTCHARUN_API = "https://api.captcha-run.com/v2/tasks"


def parse_proxy_url(proxy_url: str) -> dict:
    """解析代理 URL: http://user:pass@host:port/ -> {host, port, login, password}"""
    parsed = urlparse(proxy_url)
    host = parsed.hostname or ""
    port = str(parsed.port or "")
    login = parsed.username or ""
    password = parsed.password or ""
    return {"host": host, "port": port, "login": login, "password": password}



def detect_proxy_geo(proxy_url: str) -> tuple:
    """
    通过代理检测 IP 地理位置, 返回 (country_code, timezone)
    如果检测失败返回 ("US", "America/New_York")
    """
    try:
        proxy_str = proxy_url if proxy_url.startswith("http://") else f"http://{proxy_url}"
        r = cffi_requests.get("http://ip-api.com/json", proxy=proxy_str, timeout=15)
        data = r.json()
        country = data.get("countryCode", "US")
        timezone = data.get("timezone", "America/New_York")
        log_step("Proxy", f"IP {data.get('query', '')} → {country}, {timezone}, {data.get('city', '')}", "OK")
        return country, timezone
    except Exception as e:
        log_step("Proxy", f"IP 检测失败, 使用默认 US: {e}", "WARN")
        return "US", "America/New_York"


class CaptchaRunSolver:
    """CaptchaRun PxCaptcha2 验证码求解器"""

    def __init__(self, token: str, proxy_host: str = "", proxy_port: str = "",
                 proxy_login: str = "", proxy_password: str = "",
                 user_agent: str = "", country: str = "CN",
                 timezone_str: str = "Asia/Shanghai"):
        self.token = token
        self.proxy_host = proxy_host
        self.proxy_port = str(proxy_port)
        self.proxy_login = proxy_login
        self.proxy_password = proxy_password
        self.user_agent = user_agent
        self.country = country
        self.timezone = timezone_str
        self.task_id = None

    @classmethod
    def from_proxy_url(cls, token: str, proxy_url: str, country: str = "",
                       timezone_str: str = "", user_agent: str = ""):
        """从完整代理 URL 构造 solver, 自动检测代理 IP 地理位置"""
        p = parse_proxy_url(proxy_url)
        # 如果未指定 country/timezone, 自动检测
        if not country or not timezone_str:
            det_country, det_tz = detect_proxy_geo(proxy_url)
            country = country or det_country
            timezone_str = timezone_str or det_tz
        return cls(
            token=token,
            proxy_host=p["host"],
            proxy_port=p["port"],
            proxy_login=p["login"],
            proxy_password=p["password"],
            user_agent=user_agent,
            country=country,
            timezone_str=timezone_str,
        )

    def create_task(self, uaid: str, px_uuid: str = "", px_vid: str = "") -> str:
        """创建 PxCaptcha2 验证任务"""
        body = {
            "captchaType": "PxCaptcha2",
            "uaid": uaid,
            "country": self.country,
            "timezone": self.timezone,
            "host": self.proxy_host,
            "port": self.proxy_port,
            "login": self.proxy_login,
            "password": self.proxy_password,
        }
        if self.user_agent:
            body["userAgent"] = self.user_agent
        if px_uuid:
            body["uuid"] = px_uuid
        if px_vid:
            body["vid"] = px_vid

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

        log_step("CaptchaRun", f"创建任务 uaid={uaid[:16]}...")
        resp = req.post(CAPTCHARUN_API, json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        self.task_id = data.get("taskId")
        if not self.task_id:
            raise ValueError(f"CaptchaRun 返回无 taskId: {data}")
        log_step("CaptchaRun", f"taskId={self.task_id}", "OK")
        return self.task_id



# ============================================================
#  常量
# ============================================================

SIGNUP_BASE = "https://signup.live.com"
SIGNUP_PATH = (
    "/signup?sru=https%3a%2f%2flogin.live.com%2foauth20_authorize.srf"
    "%3flc%3d2052%26client_id%3d9199bf20-a13f-4107-85dc-02114787ef48"
    "%26cobrandid%3dab0455a0-8d03-46b9-b18b-df2f57b9e44c"
    "%26mkt%3dZH-CN%26opid%3d{opid}%26opidt%3d{opidt}"
    "%26uaid%3d{uaid}%26contextid%3d{contextid}%26opignore%3d1"
    "&mkt=ZH-CN&uiflavor=web&fl=dob%2cflname%2cwld"
    "&cobrandid=ab0455a0-8d03-46b9-b18b-df2f57b9e44c"
    "&client_id=9199bf20-a13f-4107-85dc-02114787ef48"
    "&uaid={uaid}&suc=9199bf20-a13f-4107-85dc-02114787ef48"
    "&fluent=2&lic=1"
)

RISK_BASE = "https://login.microsoftonline.com"
RISK_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"

DOMAINS = ["outlook.com", "hotmail.com"]

# Chrome 主版本号 (与 impersonate 一致)
_CHROME_MAJOR = 131
_CHROME_VER = "131.0.6778.86"
_CHROME_UA = (
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/{_CHROME_VER} Safari/537.36"
)
_CHROME_SEC_CH = f'"Chromium";v="{_CHROME_MAJOR}", "Google Chrome";v="{_CHROME_MAJOR}", "Not/A)Brand";v="99"'


def gen_chrome_ua() -> tuple:
    """返回固定 Chrome UA, 与 impersonate=chrome136 一致"""
    return _CHROME_UA, _CHROME_SEC_CH


# ============================================================
#  工具
# ============================================================

# ANSI 颜色
C_DIM = "\033[90m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_CYAN = "\033[96m"
C_BOLD = "\033[1m"
C_RESET = "\033[0m"

_LEVEL_COLOR = {"INFO": C_DIM, "OK": C_GREEN, "WARN": C_YELLOW, "ERROR": C_RED}


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    color = _LEVEL_COLOR.get(level, C_DIM)
    tag = _get_tag()
    prefix = f"{C_CYAN}{tag}{C_RESET} " if tag else ""
    with _print_lock:
        print(f"{C_DIM}{ts}{C_RESET} {prefix}{color}[{level}]{C_RESET} {msg}")


def log_step(step, msg, status=""):
    """带步骤编号的日志, status: '' / 'OK' / 'WARN' / 'ERROR'"""
    ts = datetime.now().strftime("%H:%M:%S")
    icon = {"OK": f"{C_GREEN}✓{C_RESET}", "WARN": f"{C_YELLOW}!{C_RESET}",
            "ERROR": f"{C_RED}✗{C_RESET}", "": f"{C_DIM}→{C_RESET}"}.get(status, "")
    tag = _get_tag()
    prefix = f"{C_CYAN}{tag}{C_RESET} " if tag else ""
    with _print_lock:
        print(f"{C_DIM}{ts}{C_RESET} {prefix}{icon} {C_CYAN}{step}{C_RESET} {msg}")


def log_box(title, lines, color=C_CYAN):
    """打印带边框的信息框"""
    tag = _get_tag()
    prefix = f"{C_CYAN}{tag}{C_RESET} " if tag else ""
    width = max(len(title), max(len(l) for l in lines) if lines else 0) + 4
    border = f"{color}{'─' * (width + 2)}{C_RESET}"
    with _print_lock:
        print(f"{prefix}{border}")
        print(f"{prefix}{color}│{C_RESET} {C_BOLD}{title}{C_RESET}{' ' * (width - len(title) - 1)}{color}│{C_RESET}")
        for line in lines:
            print(f"{prefix}{color}│{C_RESET} {line}{' ' * (width - len(line) - 1)}{color}│{C_RESET}")
        print(f"{prefix}{border}")


def extract_server_data(html: str) -> dict:
    """从注册页面 HTML 中提取 ServerData JSON"""
    # ServerData 以 var ServerData= 开头
    for pattern in [
        r'var\s+ServerData\s*=\s*(\{.*?\});\s*</script>',
        r'var\s+ServerData\s*=\s*(\{.*?\});',
    ]:
        match = re.search(pattern, html, re.DOTALL)
        if not match:
            continue
        raw = match.group(1)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    raise ValueError("无法从页面中提取 ServerData")


def build_query_string(server_data: dict) -> str:
    """构建 API 请求附加的 query string (来自当前页面 URL)"""
    # RI 函数: 直接附加 window.location.search
    # 我们从 signup URL 构造
    ct = int(time.time())
    return (
        f"lcid=2052&wa=wsignin1.0&rpsnv=13&ct={ct}"
        "&rver=7.0.6730.0&wp=MBI_SSL"
        "&wreply=https%3a%2f%2foutlook.live.com%2fmail%2F"
        "&id=292841&CBCXT=out&lw=1&fl=dob%2Cflname%2Cwld"
        "&cobrandid=ab0455a0-8d03-46b9-b18b-df2f57b9e44c"
        f"&uaid={server_data.get('sUnauthSessionID', '')}"
        "&lic=1"
    )


def build_headers(server_data: dict, use_api_canary: bool = True) -> dict:
    """
    构建 API 请求头 (对应 JS 中的 Zp 函数)
    """
    h = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "hpgid": str(server_data.get("hpgid", 200225)),
        "hpgact": str(server_data.get("hpgact", 0)),
    }
    # canary
    canary = server_data.get("apiCanary", "")
    if use_api_canary and canary:
        h["canary"] = canary
    # correlation — 真实请求用大写 I: correlationId
    corr_id = server_data.get("sUnauthSessionID", "")
    if corr_id:
        h["correlationId"] = corr_id
        h["client-request-id"] = corr_id
    return h


def format_birthdate(day, month, year) -> str:
    """
    格式化出生日期 (对应 JS 中的 uL 函数)
    格式: DD:MM:YYYY (零填充)
    """
    d = str(day).zfill(2)
    m = str(month).zfill(2)
    y = str(year)
    return f"{d}:{m}:{y}"


# ============================================================
#  注册器
# ============================================================

class MicrosoftSignupProtocol:
    """Microsoft Outlook 纯协议注册器"""

    def __init__(self, proxy: str = None, timeout: int = 30, user_agent: str = None, sec_ch_ua: str = None):
        proxies = {"http": proxy, "https": proxy} if proxy else None
        self.client = cffi_requests.Session(
            impersonate="chrome131",
            timeout=timeout,
            proxies=proxies,
        )
        # 生成 Chrome UA, 覆盖 curl_cffi 默认 UA 以增加随机性
        if user_agent and sec_ch_ua:
            self.user_agent = user_agent
            self.sec_ch_ua = sec_ch_ua
        else:
            self.user_agent, self.sec_ch_ua = gen_chrome_ua()
        # 设置默认 headers, 模拟真实 Chrome 浏览器
        self.client.headers.update({
            "User-Agent": self.user_agent,
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        self.server_data = None
        self.query_string = None
        self.accept_language = "zh-CN,zh;q=0.9"

    def close(self):
        self.client.close()

    def step1_fetch_signup_page(self) -> dict:
        """
        Step 1: GET /signup -> 获取初始页面
        从 Outlook 登录页入口进入, 带 sru/client_id 等OAuth参数
        提取 ServerData (apiCanary, uaid, URLs, etc.)
        """
        import uuid as _uuid
        ct = int(time.time())
        # 生成 Outlook 登录页跳转所需的参数
        pre_uaid = _uuid.uuid4().hex  # 32位hex, 和微软格式一致
        opid = _uuid.uuid4().hex.upper()[:16] + _uuid.uuid4().hex.upper()[:8]
        opidt = str(ct)
        contextid = _uuid.uuid4().hex.upper()[:16] + _uuid.uuid4().hex.upper()[:8]
        url = SIGNUP_BASE + SIGNUP_PATH.format(
            uaid=pre_uaid, opid=opid, opidt=opidt, contextid=contextid
        )
        log_step("Step 1", f"GET signup.live.com")

        resp = self.client.get(url, headers={
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        })
        resp.raise_for_status()
        html = resp.text

        # 提取 ServerData
        sd = extract_server_data(html)
        self.server_data = sd
        self.query_string = build_query_string(sd)

        log_step("Step 1", f"uaid={sd.get('sUnauthSessionID', '')}", "OK")

        # 加载 DFP (Device Fingerprint) — 对 risk/verify 至关重要
        captcha_info = sd.get("oCaptchaInfo", {})
        dfp_url = captcha_info.get("urlDfp", "")
        if dfp_url:
            try:
                self.client.get(dfp_url, headers={
                    "Referer": url,
                    "Sec-Fetch-Dest": "iframe",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "cross-site",
                    "Upgrade-Insecure-Requests": "1",
                })
                log_step("Step 1", "DFP loaded", "OK")
            except Exception as e:
                log_step("Step 1", f"DFP load failed: {e}", "WARN")

        # 加载 PX iframe — 初始化 PX 会话
        human_iframe_url = sd.get("urlHumanIframe", "")
        if human_iframe_url:
            try:
                self.client.get(human_iframe_url, headers={
                    "Referer": url,
                    "Sec-Fetch-Dest": "iframe",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "cross-site",
                    "Upgrade-Insecure-Requests": "1",
                })
                log_step("Step 1", "PX iframe loaded", "OK")
            except Exception as e:
                log_step("Step 1", f"PX iframe load failed: {e}", "WARN")

        return sd

    def step3_risk_initialize(self) -> dict:
        """
        Step 3: POST /risk/initialize (login.microsoftonline.com)
        风险评估初始化, 返回 continuationToken
        """
        path = self.server_data.get("urlRiskInitialize", f"/{RISK_TENANT}/api/v1.0/risk/initialize")
        url = RISK_BASE + path
        log_step("Step 3", "POST risk/initialize")

        headers = build_headers(self.server_data)
        headers["Referer"] = f"{SIGNUP_BASE}/"
        headers["Origin"] = SIGNUP_BASE
        headers["Sec-Fetch-Dest"] = "empty"
        headers["Sec-Fetch-Mode"] = "cors"
        headers["Sec-Fetch-Site"] = "cross-site"
        headers["Sec-GPC"] = "1"
        headers["Priority"] = "u=0"
        headers["Accept-Language"] = self.accept_language

        body = {"continuationToken": ""}

        try:
            resp = self.client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            token = data.get("continuationToken", "")
            log_step("Step 3", f"continuationToken={token[:32]}...", "OK")
            return data
        except Exception as e:
            log_step("Step 3", f"risk/initialize 失败 (非致命): {e}", "WARN")
            return {}

    def step4_check_username(self, email: str) -> dict:
        """
        Step 4: POST /API/CheckAvailableSigninNames
        检查用户名可用性
        """
        url = self.server_data.get("urlCheckAvailableSigninNames", SIGNUP_BASE + "/API/CheckAvailableSigninNames")
        url_with_qs = f"{url}?{self.query_string}"
        log_step("Step 2", f"CheckAvailableSigninNames → {email}")

        body = {
            "includeSuggestions": True,
            "signInName": email,
            "uiflvr": self.server_data.get("iUiFlavor", 1001),
            "scid": self.server_data.get("iScenarioId", 100118),
            "uaid": self.server_data.get("sUnauthSessionID", ""),
            "hpgid": self.server_data.get("hpgid", 200225),
        }

        headers = build_headers(self.server_data)
        headers.update({
            "Referer": f"{SIGNUP_BASE}/",
            "Origin": SIGNUP_BASE,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Accept-Language": self.accept_language,
        })
        resp = self.client.post(url_with_qs, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        # 更新 apiCanary
        if "apiCanary" in data:
            self.server_data["apiCanary"] = data["apiCanary"]

        available = data.get("isAvailable", False)
        if available:
            log_step("Step 2", f"可用 ({data.get('type', '')})", "OK")
        else:
            log_step("Step 2", "用户名已被占用", "ERROR")
        return data

    def _risk_verify_headers(self) -> dict:
        """构建 risk/verify 请求的公共 headers"""
        headers = build_headers(self.server_data)
        headers.update({
            "Referer": f"{SIGNUP_BASE}/",
            "Origin": SIGNUP_BASE,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
            "X-Edge-Shopping-Flag": "0",
            "Accept-Language": self.accept_language,
        })
        return headers

    def _clear_microsoftonline_cookies(self):
        """清除 login.microsoftonline.com 域的 cookies"""
        to_remove = [c for c in self.client.cookies.jar
                     if 'login.microsoftonline.com' in (c.domain or '')]
        for cookie in to_remove:
            try:
                self.client.cookies.jar.clear(cookie.domain, cookie.path, cookie.name)
            except Exception:
                pass
        if to_remove:
            log_step("Cookies", f"清除 {len(to_remove)} 个 microsoftonline.com cookies")

    def step5_risk_verify(self, continuation_token: str,
                          email: str, country: str, birth_date: str,
                          first_name: str, last_name: str) -> dict:
        """
        Step 5: POST /risk/verify (第一次)
        不带 riskProviderMetadata — 只发 continuationToken + 用户信息
        返回 state=riskChallengeRequired + 新 continuationToken
        """
        path = self.server_data.get("urlRiskVerify", f"/{RISK_TENANT}/api/v1.0/risk/verify")
        url = RISK_BASE + path
        log_step("Step 4", "risk/verify (1st, 无 PX tokens)")

        headers = self._risk_verify_headers()
        site_id = "00000000487A244A"

        body = {
            "continuationToken": continuation_token,
            "msaRiskVerifySignature": {
                "memberName": email,
                "siteId": site_id,
                "uiFlavor": "Web",
                "appId": site_id,
                "birthdate": birth_date,
                "firstName": first_name,
                "lastName": last_name,
                "countryCode": country,
                "verificationCode": "",
                "deviceDetails": {"isRdm": False},
                "action": "SignUp",
            },
        }

        self._clear_microsoftonline_cookies()
        resp = self.client.post(url, json=body, headers=headers)
        if resp.status_code >= 400:
            log_step("Step 4", f"HTTP {resp.status_code}: {resp.text[:300]}", "ERROR")
        resp.raise_for_status()
        data = resp.json()
        state = data.get("state", "")
        new_token = data.get("continuationToken", "")
        log_step("Step 4", f"state={state}", "OK" if state else "WARN")
        return data

    def step5b_risk_verify(self, continuation_token: str, px_cookies: dict,
                           email: str = "", country: str = "", birth_date: str = "",
                           first_name: str = "", last_name: str = "") -> dict:
        """
        Step 5b: POST /risk/verify (第二次)
        同时包含 riskProviderMetadata (PX cookies) + msaRiskVerifySignature (用户信息)
        与浏览器真实请求结构一致
        """
        path = self.server_data.get("urlRiskVerify", f"/{RISK_TENANT}/api/v1.0/risk/verify")
        url = RISK_BASE + path
        log_step("Step 5", "risk/verify (2nd, PX + 用户信息)")

        headers = self._risk_verify_headers()
        site_id = "00000000487A244A"

        body = {
            "continuationToken": continuation_token,
            "challengeSolution": {
                "challengeType": "HumanCaptcha",
                "px3": px_cookies.get("_px3", ""),
                "pxde": px_cookies.get("_pxde", ""),
                "pxvid": px_cookies.get("_pxvid", ""),
            },
            "msaRiskVerifySignature": {
                "memberName": email,
                "siteId": site_id,
                "uiFlavor": "Web",
                "appId": site_id,
                "birthdate": birth_date,
                "firstName": first_name,
                "lastName": last_name,
                "countryCode": country,
                "verificationCode": "",
                "deviceDetails": {"isRdm": False},
                "action": "SignUp",
            },
        }

        self._clear_microsoftonline_cookies()
        resp = self.client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        state = data.get("state", "")
        final_token = data.get("continuationToken", "")
        log_step("Step 5", f"state={state}", "OK" if state == "continue" else "WARN")
        return data

    def step6_get_captcha(self) -> dict:
        """
        Step 6: 获取人机验证信息
        返回 captcha 配置 (HSPProtect + Arkose/FunCaptcha)
        """
        cap = self.server_data.get("oCaptchaInfo", {})
        log_step("Captcha", f"HIP FID: {cap.get('sHipFid', '')[:20]}...")
        log_step("Captcha", f"Human Iframe: {cap.get('sHumanAppId', '')}")
        return cap

    def _wait_silent_token(self, solver: CaptchaRunSolver) -> dict:
        """轮询 CaptchaRun 任务, 等待 silentToken 出现 (仅用于让 CaptchaRun 进入 press 阶段)"""
        url = f"{CAPTCHARUN_API}/{solver.task_id}?captchaType=silent"
        headers = {"Authorization": f"Bearer {solver.token}"}
        max_wait, interval, elapsed = 180, 3, 0

        log_step("CaptchaRun", f"等待 silentToken (最多 {max_wait}s)...")
        while elapsed < max_wait:
            resp = req.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "")
            response = data.get("response", {})

            if response.get("silentToken"):
                log_step("CaptchaRun", f"silentToken 已获取 ({elapsed}s)", "OK")
                return response["silentToken"]

            if status == "Fail":
                raise ValueError(f"CaptchaRun silent 失败: {data.get('reason', '')}")

            time.sleep(interval)
            elapsed += interval

        raise TimeoutError(f"CaptchaRun silentToken 超时 ({max_wait}s)")

    def _wait_press_token(self, solver: CaptchaRunSolver) -> dict:
        """继续轮询同一任务, 等待 pressToken 出现"""
        url = f"{CAPTCHARUN_API}/{solver.task_id}?captchaType=press"
        headers = {"Authorization": f"Bearer {solver.token}"}
        max_wait = 120
        interval = 3
        elapsed = 0

        log_step("CaptchaRun", f"等待 pressToken (最多 {max_wait}s)...")
        while elapsed < max_wait:
            try:
                resp = req.get(url, headers=headers, timeout=30)
                resp.raise_for_status()
            except req.exceptions.HTTPError:
                if resp.status_code == 404:
                    time.sleep(interval)
                    elapsed += interval
                    continue
                raise
            data = resp.json()
            status = data.get("status", "")
            response = data.get("response", {})

            if response.get("pressToken"):
                log_step("CaptchaRun", f"pressToken 已获取 ({elapsed}s)", "OK")
                return response["pressToken"]

            if status == "Fail":
                raise ValueError("打码失败")

            time.sleep(interval)
            elapsed += interval

        raise TimeoutError(f"CaptchaRun pressToken 超时 ({max_wait}s)")

    def step7_create_account(
        self,
        email: str,
        password: str,
        country: str,
        birth_day: int,
        birth_month: int,
        birth_year: int,
        first_name: str,
        last_name: str,
        continuation_token: str = "",
    ) -> dict:
        """
        Step 7: POST /API/CreateAccount
        创建账号, 需要 risk/verify 返回的最终 continuationToken
        """
        url = self.server_data.get("urlCreateAccount", SIGNUP_BASE + "/API/CreateAccount")
        url_with_qs = f"{url}?{self.query_string}"
        log_step("Step 6", f"CreateAccount → {email}")

        birth_date = format_birthdate(birth_day, birth_month, birth_year)

        body = {
            "BirthDate": birth_date,
            "CheckAvailStateMap": [f"{email}:false"],
            "Country": country,
            "EvictionWarningShown": [],
            "FirstName": first_name,
            "IsRDM": False,
            "IsOptOutEmailDefault": True,
            "IsOptOutEmailShown": 1,
            "IsOptOutEmail": True,
            "IsUserConsentedToChinaPIPL": country == "CN",
            "LastName": last_name,
            "LW": 1,
            "MemberName": email,
            "RequestTimeStamp": datetime.now(timezone.utc).isoformat(),
            "ReturnUrl": "",
            "SignupReturnUrl": self.server_data.get("sSignupReturnUrl", ""),
            "SuggestedAccountType": self.server_data.get("sSuggestedAccountType", "EASI"),
            "SiteId": "00000000487A244A",
            "VerificationCodeSlt": "",
            "PrivateAccessToken": "",
            "WReply": self.server_data.get("sWReply", ""),
            "MemberNameChangeCount": 1,
            "MemberNameAvailableCount": 1,
            "MemberNameUnavailableCount": 0,
            "Password": password,
            "ContinuationToken": continuation_token,
            "uiflvr": self.server_data.get("iUiFlavor", 1001),
            "scid": self.server_data.get("iScenarioId", 100118),
            "uaid": self.server_data.get("sUnauthSessionID", ""),
            "hpgid": self.server_data.get("hpgid", 200225),
        }

        headers = build_headers(self.server_data)
        headers.update({
            "Referer": f"{SIGNUP_BASE}/",
            "Origin": SIGNUP_BASE,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Accept-Language": self.accept_language,
        })
        resp = self.client.post(url_with_qs, json=body, headers=headers)
        if resp.status_code >= 400:
            log_step("Step 6", f"HTTP {resp.status_code}: {resp.text[:300]}", "ERROR")
        data = resp.json()

        if "error" in data:
            err = data["error"]
            log_step("Step 6", f"失败: code={err.get('code')}", "ERROR")
        elif "redirectUrl" in data:
            log_step("Step 6", "注册成功!", "OK")
        else:
            log_step("Step 6", f"未知响应: {json.dumps(data)[:150]}", "WARN")

        return data

    def register(
        self,
        username: str,
        domain: str,
        password: str,
        country: str = "US",
        birth_year: int = 1995,
        birth_month: int = 6,
        birth_day: int = 15,
        first_name: str = "三",
        last_name: str = "张",
        cr_solver: CaptchaRunSolver = None,
    ) -> dict:
        """执行完整注册流程, 使用 CaptchaRun 自动解决验证码"""
        email = f"{username}@{domain}"
        tag = _get_tag()
        prefix = f"{C_CYAN}{tag}{C_RESET} " if tag else ""
        with _print_lock:
            print(f"\n{prefix}{C_BOLD}{'━' * 46}{C_RESET}")
            print(f"{prefix}{C_BOLD}  Outlook 注册{C_RESET} {C_DIM}|{C_RESET} {email}")
            print(f"{prefix}{C_BOLD}{'━' * 46}{C_RESET}")

        try:
            # Step 1: 获取页面 + DFP + PX iframe
            self.step1_fetch_signup_page()

            # Step 2: 检查用户名可用性
            check = self.step4_check_username(email)
            if not check.get("isAvailable", False):
                return {"success": False, "error": "username_unavailable"}

            # Step 3: 风险初始化
            risk_init = self.step3_risk_initialize()
            continuation_token = risk_init.get("continuationToken", "")

            birth_date = format_birthdate(birth_day, birth_month, birth_year)

            # Step 4: 获取 captcha 信息
            self.step6_get_captcha()

            if not cr_solver:
                log_step("Captcha", "需要 --cr-token 参数", "ERROR")
                return {"success": False, "error": "no_captcha_solver"}

            # Step 5a: 创建 CaptchaRun 任务并等待 silentToken
            uaid = self.server_data.get("sUnauthSessionID", "")
            if not uaid:
                raise ValueError("无 uaid, 无法创建验证任务")
            cr_solver.create_task(uaid)
            self._wait_silent_token(cr_solver)

            # Step 5b: 第一次 risk/verify (不带 PX tokens) -> riskChallengeRequired
            verify1 = self.step5_risk_verify(
                continuation_token=continuation_token,
                email=email, country=country, birth_date=birth_date,
                first_name=first_name, last_name=last_name,
            )
            state = verify1.get("state", "")
            new_token = verify1.get("continuationToken", "")

            # Step 5c: 等待 pressToken 并提交 challengeSolution
            if state == "riskChallengeRequired":
                press = self._wait_press_token(cr_solver)
                px_cookies = {k: v for k, v in press.items()
                              if k in ("_px3", "_pxde", "_pxvid") and v}

                verify2 = self.step5b_risk_verify(
                    continuation_token=new_token,
                    px_cookies=px_cookies,
                    email=email, country=country, birth_date=birth_date,
                    first_name=first_name, last_name=last_name,
                )
                if verify2.get("state") != "continue":
                    log_step("Step 5", f"PX 验证未通过, state={verify2.get('state', '')}", "ERROR")
                    return {"success": False, "error": "px_verify_failed"}
                final_token = verify2.get("continuationToken", "")
            else:
                final_token = new_token

            # Step 7: 创建账号
            result = self.step7_create_account(
                email=email, password=password, country=country,
                birth_day=birth_day, birth_month=birth_month, birth_year=birth_year,
                first_name=first_name, last_name=last_name,
                continuation_token=final_token,
            )

            success = "redirectUrl" in (result or {})
            if success:
                log_box("注册成功", [
                    f"邮箱:  {email}",
                    f"密码:  {password}",
                    f"姓名:  {first_name} {last_name}",
                    f"生日:  {birth_year}-{birth_month}-{birth_day}",
                ], C_GREEN)
            return {"success": success, "email": email, "password": password, "result": result}

        except Exception as e:
            log_step("ERROR", str(e), "ERROR")
            return {"success": False, "error": str(e)}
        finally:
            self.close()


# ============================================================
#  OAuth2 授权 (模拟浏览器登录, Authorization Code 流程)
# ============================================================

# Thunderbird 公共 client_id, 支持 IMAP/POP3/SMTP/Graph
OAUTH_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
OAUTH_REDIRECT = "https://login.microsoftonline.com/common/oauth2/nativeclient"
OAUTH_SCOPE = (
    "offline_access openid profile "
    "https://graph.microsoft.com/IMAP.AccessAsUser.All "
    "https://graph.microsoft.com/POP.AccessAsUser.All "
    "https://graph.microsoft.com/SMTP.Send "
    "https://graph.microsoft.com/Mail.Read "
    "https://graph.microsoft.com/Mail.Send"
)


def _extract_config(html: str) -> dict:
    """从 HTML 中提取 $Config 或 ServerData JSON"""
    for pattern in [
        r'\$Config\s*=\s*({.*?});\s*</script>',
        r'var\s+ServerData\s*=\s*({.*?});\s*</script>',
        r'\$Config\s*=\s*({.*?});',
        r'var\s+ServerData\s*=\s*({.*?});',
    ]:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
    return {}


def _extract_hidden_inputs(html: str) -> dict:
    """提取所有 hidden input 的 name/value"""
    fields = {}
    for m in re.finditer(
        r'<input[^>]*type="hidden"[^>]*name="([^"]*)"[^>]*value="([^"]*)"',
        html,
    ):
        fields[m.group(1)] = m.group(2)
    for m in re.finditer(
        r'<input[^>]*name="([^"]*)"[^>]*type="hidden"[^>]*value="([^"]*)"',
        html,
    ):
        if m.group(1) not in fields:
            fields[m.group(1)] = m.group(2)
    return fields


def oauth2_authorize(email: str, password: str, proxy_url: str = None) -> dict:
    """模拟浏览器登录走 Authorization Code 流程获取 OAuth2 token

    流程 (通过 MCP 浏览器抓包):
      1. GET authorize → 登录页 ($Config: urlPost, sFT, sCtx, apiCanary)
      2. POST GetCredentialType → 检查凭据类型
      3. 浏览器跳转到 login.live.com 密码页 (ServerData: urlPost=ppsecure/post.srf)
      4. POST checkpassword.srf → {username, password} → 返回 vanguardflowtoken
      5. POST ppsecure/post.srf → PPFT + passwd + vanguardflowtoken → consent 或 code
      6. POST Consent/Update → ucaction=Yes → 302 → code
      7. POST token → 用 code 换 access_token + refresh_token
    """
    log_step("OAuth2", "正在获取令牌 (Auth Code)...")
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    try:
        s = cffi_requests.Session(impersonate="chrome136")
        if proxy_url:
            s.proxies = {"http": proxy_url, "https": proxy_url}

        # Step 1: GET authorize → 登录页
        auth_url = (
            "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?"
            + urlencode({
                "client_id": OAUTH_CLIENT_ID,
                "response_type": "code",
                "redirect_uri": OAUTH_REDIRECT,
                "scope": OAUTH_SCOPE,
                "response_mode": "query",
            })
        )
        resp = s.get(auth_url, allow_redirects=True, timeout=30)
        html = resp.text
        config = _extract_config(html)
        sFT = config.get("sFT", "")
        sCtx = config.get("sCtx", "")
        apiCanary = config.get("apiCanary", "")
        url_post_login = config.get("urlPost", "")
        log_step("OAuth2", f"登录页已加载, urlPost={url_post_login[:60]}")

        # Step 2: POST GetCredentialType (检查凭据类型)
        if url_post_login and sFT:
            try:
                s.post(
                    "https://login.microsoftonline.com/common/GetCredentialType?mkt=zh-CN",
                    json={
                        "username": email,
                        "isOtherIdpSupported": True,
                        "checkPhones": False,
                        "isRemoteNGCSupported": True,
                        "isCookieBannerShown": False,
                        "isFidoSupported": True,
                        "originalRequest": sCtx,
                        "country": "US",
                        "forceotclogin": False,
                        "isExternalFederationDisallowed": False,
                        "isRemoteConnectSupported": False,
                        "federationFlags": 0,
                        "isSignup": False,
                        "flowToken": sFT,
                        "isAccessPassSupported": True,
                        "isQrCodePinSupported": True,
                    },
                    headers={
                        "hpgid": "1104",
                        "hpgact": "1800",
                        "canary": apiCanary,
                        "Accept": "application/json",
                        "Content-Type": "application/json; charset=UTF-8",
                    },
                    timeout=15,
                )
            except Exception:
                pass

        # Step 3: 构造 login.live.com 密码页 URL
        # 浏览器从 BssoInterrupt 页面 JS 跳转到 login.live.com, 带额外参数
        # 直接构造完整 URL (参数从浏览器抓包获取)
        import uuid as _uuid
        uaid = _uuid.uuid4().hex
        live_url = (
            "https://login.live.com/oauth20_authorize.srf?"
            + urlencode({
                "client_id": OAUTH_CLIENT_ID,
                "scope": OAUTH_SCOPE,
                "redirect_uri": OAUTH_REDIRECT,
                "response_type": "code",
                "response_mode": "query",
                "username": email,
                "login_hint": email,
                "uaid": uaid,
                "msproxy": "1",
                "issuer": "mso",
                "tenant": "common",
                "ui_locales": "zh-CN",
            })
        )
        resp = s.get(live_url, allow_redirects=True, timeout=30)
        html = resp.text

        # 解析 ServerData (login.live.com 用 var ServerData = {...})
        live_config = _extract_config(html)
        ppsecure_url = live_config.get("urlPost", "")

        # PPFT 在 ServerData.sFTTag 中: <input type="hidden" name="PPFT" value="...">
        sft_tag = live_config.get("sFTTag", "")
        ppft_match = re.search(r'value="([^"]+)"', sft_tag)
        ppft = ppft_match.group(1) if ppft_match else ""

        if not ppsecure_url or not ppft:
            log_step("OAuth2", f"无法解析密码页 (ppsecure={'有' if ppsecure_url else '无'}, PPFT={'有' if ppft else '无'})", "ERROR")
            return {"client_id": OAUTH_CLIENT_ID, "refresh_token": ""}

        log_step("OAuth2", f"密码页已加载, PPFT={ppft[:40]}...")

        # Step 4: POST checkpassword.srf → 获取 vanguardflowtoken
        check_resp = s.post(
            "https://login.live.com/checkpassword.srf",
            json={
                "username": email,
                "password": password,
                "checkpasswordflowtoken": "",
            },
            headers={
                "hpgid": "37",
                "hpgact": "0",
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=15,
        )
        check_data = check_resp.json()
        if check_data.get("validationresult") != "succeed":
            log_step("OAuth2", f"密码验证失败: {check_data.get('validationresult', '')}", "ERROR")
            return {"client_id": OAUTH_CLIENT_ID, "refresh_token": ""}

        vanguardflowtoken = check_data.get("vanguardflowtoken", "")
        log_step("OAuth2", "密码验证成功, 获取 vanguardflowtoken")

        # Step 5: POST ppsecure/post.srf → 登录提交
        # 构造 form data (从浏览器抓包获取的字段)
        post_data = {
            "ps": "2",
            "psRNGCDefaultType": "",
            "psRNGCEntropy": "",
            "psRNGCSLK": "",
            "canary": "",
            "ctx": "",
            "hpgrequestid": "",
            "PPFT": ppft,
            "PPSX": "Pas",
            "NewUser": "1",
            "FoundMSAs": "",
            "fspost": "0",
            "i21": "0",
            "CookieDisclosure": "0",
            "IsFidoSupported": "1",
            "isSignupPost": "0",
            "isRecoveryAttemptPost": "0",
            "i13": "1",
            "login": email,
            "loginfmt": email,
            "type": "11",
            "LoginOptions": "1",
            "lrt": "",
            "lrtPartition": "",
            "hisRegion": "",
            "hisScaleUnit": "",
            "cpr": "0",
            "passwd": password,
            "vanguardflowtoken": vanguardflowtoken,
        }

        resp = s.post(ppsecure_url, data=post_data, allow_redirects=True, timeout=30,
                      headers={
                          "Referer": live_url,
                          "Origin": "https://login.live.com",
                          "Content-Type": "application/x-www-form-urlencoded",
                      })
        final_url = str(resp.url)
        html = resp.text

        log_step("OAuth2", f"ppsecure POST → status={resp.status_code}, url={final_url[:80]}")

        # 处理 JS 自动提交的表单 (ppsecure 返回的中断页面)
        # HTML 包含: <form name="fmHF" action="https://account.live.com/interrupt/...">
        # 需要手动提取 hidden inputs 并 POST
        max_js_redirects = 5
        while "code=" not in final_url and max_js_redirects > 0:
            # 检查是否是 passkey 中断页面 (跳过 passkey 注册)
            # passkey 页面包含 fido/create form 和 postBackUrl
            if "interrupt/passkey" in final_url and "postBackUrl" in html:
                # passkey 页面有 postBackUrl, 其中 ru 参数指向 oauth20_authorize.srf
                # 直接 GET ru URL 可跳过 passkey 到达 consent
                pb_match = re.search(r"name='postBackUrl' value='([^']+)'", html)
                if pb_match:
                    postback_url = pb_match.group(1).replace("&amp;", "&")
                    pb_params = parse_qs(urlparse(postback_url).query)
                    ru_url = pb_params.get("ru", [""])[0]
                    if ru_url:
                        log_step("OAuth2", "跳过 passkey 中断 → GET ru URL")
                        resp = s.get(ru_url, allow_redirects=True, timeout=30)
                        final_url = str(resp.url)
                        html = resp.text
                        log_step("OAuth2", f"passkey 跳过结果 → status={resp.status_code}, url={final_url[:80]}")
                        break

            # 检查是否有 JS 自动提交表单
            form_match = re.search(r'<form[^>]+name="fmHF"[^>]+action="([^"]+)"', html)
            if not form_match:
                # 也检查通用 form (但排除 fido/create form, 那是 passkey 注册)
                form_match = re.search(r'<form[^>]+action="([^"]+)"[^>]*>', html)
                if form_match and "fido/create" in form_match.group(1):
                    # 这是 passkey 注册 form, 不应该提交, 而是跳过
                    # 提取 postBackUrl 中的 ru URL
                    pb_match = re.search(r"name='postBackUrl' value='([^']+)'", html)
                    if pb_match:
                        postback_url = pb_match.group(1).replace("&amp;", "&")
                        pb_params = parse_qs(urlparse(postback_url).query)
                        ru_url = pb_params.get("ru", [""])[0]
                        if ru_url:
                            log_step("OAuth2", "跳过 passkey 注册 → GET ru URL")
                            resp = s.get(ru_url, allow_redirects=True, timeout=30)
                            final_url = str(resp.url)
                            html = resp.text
                            log_step("OAuth2", f"passkey 跳过结果 → status={resp.status_code}, url={final_url[:80]}")
                            break
                    form_match = None
            if not form_match:
                break

            action_url = form_match.group(1).replace("&amp;", "&")
            hidden = _extract_hidden_inputs(html)
            log_step("OAuth2", f"JS 表单跳转 → {action_url[:80]}")

            resp = s.post(action_url, data=hidden, allow_redirects=True, timeout=30)
            final_url = str(resp.url)
            html = resp.text
            log_step("OAuth2", f"跳转结果 → status={resp.status_code}, url={final_url[:80]}")
            max_js_redirects -= 1

        # 检查是否需要 KMSI (Keep me signed in) 页面
        if "kmsi" in final_url.lower() or "Kmsi" in html or "Stay signed in" in html or "保持登录" in html:
            log_step("OAuth2", "检测到 KMSI 页面, 自动确认...")
            kmsi_config = _extract_config(html)
            kmsi_hidden = _extract_hidden_inputs(html)
            kmsi_data = dict(kmsi_hidden)
            kmsi_data["LoginOptions"] = "1"
            # KMSI 页面通常有个 form POST 确认
            kmsi_action = kmsi_config.get("urlPost", "")
            if not kmsi_action:
                action_match = re.search(r'<form[^>]+action="([^"]+)"', html)
                kmsi_action = action_match.group(1) if action_match else final_url
            if kmsi_action.startswith("/"):
                kmsi_action = "https://login.live.com" + kmsi_action
            kmsi_action = kmsi_action.replace("&amp;", "&")
            resp = s.post(kmsi_action, data=kmsi_data, allow_redirects=True, timeout=30)
            final_url = str(resp.url)
            html = resp.text
            log_step("OAuth2", f"KMSI 确认 → status={resp.status_code}, url={final_url[:80]}")

        # Step 6: 检查是否需要 consent 或已拿到 code
        code = None
        if "code=" in final_url:
            qs = parse_qs(urlparse(final_url).query)
            code = qs.get("code", [None])[0]
        elif "Consent" in final_url or "consent" in html.lower():
            log_step("OAuth2", "检测到授权同意页面, 自动同意...")
            # consent 页面也是 JS 渲染, 从 ServerData/$Config 提取
            consent_config = _extract_config(html)
            consent_hidden = _extract_hidden_inputs(html)
            consent_data = dict(consent_hidden)
            consent_data["ucaction"] = "Yes"

            # 从 ServerData 补充 canary
            if "canary" not in consent_data or not consent_data["canary"]:
                consent_data["canary"] = consent_config.get("canary", "")
            # 补充 client_id 和 scope (从抓包得知)
            if "client_id" not in consent_data:
                consent_data["client_id"] = "0000000040C8F39E"
            if "scope" not in consent_data:
                consent_data["scope"] = (
                    "0000000040C8F39E:int.offline_access "
                    "0000000040C8F39E:int.profile "
                    "0000000040C8F39E:IMAP.AccessAsUser.All "
                    "0000000040C8F39E:Mail.ReadWrite "
                    "0000000040C8F39E:Mail.Send "
                    "0000000040C8F39E:POP.AccessAsUser.All "
                    "0000000040C8F39E:SMTP.Send "
                    "0000000040C8F39E:User.Read"
                )

            # 查找 form action (从 ServerData.urlPost 或 HTML form)
            action_url = consent_config.get("urlPost", "")
            if not action_url:
                action_match = re.search(r'<form[^>]+action="([^"]+)"', html)
                action_url = action_match.group(1) if action_match else final_url
            if action_url.startswith("/"):
                action_url = "https://account.live.com" + action_url
            # HTML entities
            action_url = action_url.replace("&amp;", "&")

            resp = s.post(action_url, data=consent_data, allow_redirects=True, timeout=30)
            final_url = str(resp.url)
            if "code=" in final_url:
                qs = parse_qs(urlparse(final_url).query)
                code = qs.get("code", [None])[0]

            # 如果还没有 code, 检查是否需要再跟一次重定向
            if not code and "res=success" in final_url:
                resp = s.get(final_url, allow_redirects=True, timeout=30)
                final_url = str(resp.url)
                if "code=" in final_url:
                    qs = parse_qs(urlparse(final_url).query)
                    code = qs.get("code", [None])[0]

        if not code:
            log_step("OAuth2", f"未获取到 code, final_url={final_url[:100]}", "ERROR")
            return {"client_id": OAUTH_CLIENT_ID, "refresh_token": ""}

        # Step 7: 用 code 换 token
        log_step("OAuth2", "已获取 code, 正在换取令牌...")
        token_resp = req.post(
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            data={
                "client_id": OAUTH_CLIENT_ID,
                "code": code,
                "redirect_uri": OAUTH_REDIRECT,
                "grant_type": "authorization_code",
                "scope": OAUTH_SCOPE,
            },
            proxies=proxies,
            timeout=30,
        )
        data = token_resp.json()
        if "access_token" in data:
            log_step("OAuth2", f"令牌获取成功, scope={data.get('scope', '')[:60]}...", "OK")
            return {
                "client_id": OAUTH_CLIENT_ID,
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", ""),
                "scope": data.get("scope", ""),
                "expires_in": data.get("expires_in", 0),
            }
        else:
            log_step("OAuth2", f"换取令牌失败: {data.get('error', '')} - {data.get('error_description', '')[:100]}", "ERROR")
            return {"client_id": OAUTH_CLIENT_ID, "refresh_token": ""}
    except Exception as e:
        log_step("OAuth2", f"授权异常: {e}", "ERROR")
        return {"client_id": OAUTH_CLIENT_ID, "refresh_token": ""}


# ============================================================
#  CLI
# ============================================================

def random_username() -> str:
    """生成随机邮箱用户名: 字母+数字, 10-14位"""
    prefix = "".join(random.choices(string.ascii_lowercase, k=random.randint(6, 8)))
    suffix = "".join(random.choices(string.digits, k=random.randint(3, 5)))
    return prefix + suffix


def random_password() -> str:
    """生成随机密码: 12位, 含大小写+数字+符号"""
    upper = random.choice(string.ascii_uppercase)
    lower = "".join(random.choices(string.ascii_lowercase, k=6))
    digit = "".join(random.choices(string.digits, k=3))
    symbol = random.choice("!@#$%^&*")
    pwd = upper + lower + digit + symbol
    pwd_list = list(pwd)
    random.shuffle(pwd_list)
    return "".join(pwd_list)


def random_birthdate() -> tuple:
    """随机出生日期: 1975-2005, 返回 (year, month, day)"""
    year = random.randint(1975, 2005)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return year, month, day


# 美国常用英文名
FIRST_NAMES = [
    "James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael", "Linda",
    "William", "Elizabeth", "David", "Barbara", "Richard", "Susan", "Joseph", "Jessica",
    "Thomas", "Sarah", "Christopher", "Karen", "Daniel", "Nancy", "Matthew", "Lisa",
    "Anthony", "Betty", "Mark", "Helen", "Steven", "Sandra", "Andrew", "Donna",
    "Joshua", "Carol", "Kenneth", "Ruth", "Kevin", "Sharon", "Brian", "Michelle",
    "George", "Laura", "Edward", "Emily", "Ronald", "Kimberly", "Timothy", "Deborah",
    "Jason", "Carolyn", "Jeffrey", "Virginia", "Ryan", "Anna", "Jacob", "Brenda",
    "Gary", "Pamela", "Nicholas", "Nicole", "Eric", "Emma", "Jonathan", "Samantha",
    "Stephen", "Katherine", "Larry", "Christine", "Justin", "Debra", "Scott", "Rachel",
    "Brandon", "Catherine", "Benjamin", "Carolyn", "Samuel", "Janet", "Gregory", "Ruth",
    "Frank", "Maria", "Alexander", "Heather", "Raymond", "Diane", "Patrick", "Julie",
    "Jack", "Joyce", "Dennis", "Victoria", "Jerry", "Olivia", "Tyler", "Kelly",
    "Aaron", "Christina", "Henry", "Joan", "Douglas", "Evelyn", "Peter", "Judith",
    "Adam", "Megan", "Zachary", "Andrea", "Nathan", "Cheryl", "Walter", "Hannah",
    "Harold", "Jacqueline", "Kyle", "Martha", "Carl", "Gloria", "Arthur", "Teresa",
    "Gerald", "Ann", "Roger", "Sara", "Keith", "Madison", "Jeremy", "Frances",
    "Terry", "Kathryn", "Lawrence", "Janice", "Sean", "Jean", "Christian", "Abigail",
    "Ethan", "Alice", "Austin", "Judy", "Joe", "Sophia", "Noah", "Grace",
    "Jesse", "Denise", "Willie", "Amber", "Billy", "Doris", "Bryan", "Marilyn",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
    "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker",
    "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores",
    "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell", "Mitchell",
    "Carter", "Roberts", "Gomez", "Phillips", "Evans", "Turner", "Diaz", "Parker",
    "Cruz", "Edwards", "Collins", "Reyes", "Stewart", "Morris", "Morales", "Murphy",
    "Cook", "Rogers", "Gutierrez", "Ortiz", "Morgan", "Cooper", "Bailey", "Reed",
    "Kelly", "Howard", "Ramos", "Kim", "Cox", "Ward", "Richardson", "Watson",
    "Brooks", "Chavez", "Wood", "James", "Bennett", "Gray", "Mendoza", "Ruiz",
    "Hughes", "Price", "Alvarez", "Castillo", "Sanders", "Patel", "Myers", "Long",
    "Ross", "Foster", "Jimenez", "Powell", "Jenkins", "Perry", "Russell", "Sullivan",
    "Bell", "Coleman", "Butler", "Henderson", "Barnes", "Gonzales", "Fisher", "Vasquez",
    "Simmons", "Romero", "Jordan", "Patterson", "Alexander", "Hamilton", "Graham",
]


def _parse_proxy_line(line: str) -> str:
    """解析单行代理, 返回 http://login:pass@host:port 格式"""
    if '@' in line:
        auth_part, host_part = line.rsplit('@', 1)
        proxy_login, proxy_pass = auth_part.split(':', 1)
        host, port = host_part.split(':', 1)
        return f"http://{proxy_login}:{proxy_pass}@{host}:{port}"
    parts = line.split(":")
    if len(parts) == 4:
        return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    elif len(parts) == 2:
        return f"http://{parts[0]}:{parts[1]}"
    return ""


def _get_import_auth_cookie(tag: str = "") -> str:
    """获取导入服务器的 auth cookie (带缓存, 线程安全)"""
    global _import_auth_cookie
    with _import_cookie_lock:
        if _import_auth_cookie:
            return _import_auth_cookie
    # 登录获取新 cookie
    login_resp = req.post(
        f"{_import_url}/api/login",
        json={"password": _import_password},
        timeout=15,
    )
    cookie = login_resp.cookies.get("mail_auth", "")
    if not cookie:
        with _print_lock:
            print(f"{C_CYAN}{tag}{C_RESET} ⚠ 导入服务器登录失败: {login_resp.text[:100]}")
        return ""
    with _import_cookie_lock:
        _import_auth_cookie = cookie
    return cookie


def _import_to_server(email: str, password: str, client_id: str, refresh_token: str, tag: str = ""):
    """注册成功后自动导入到 mail_manager 服务器"""
    if not _import_url or not _import_password:
        return
    payload = [{
        "email": email,
        "password": password,
        "clientId": client_id,
        "refreshToken": refresh_token,
    }]
    try:
        auth_cookie = _get_import_auth_cookie(tag)
        if not auth_cookie:
            with _stats_lock:
                _stats["import_fail"] += 1
            return
        r = req.post(
            f"{_import_url}/api/accounts",
            json=payload,
            cookies={"mail_auth": auth_cookie},
            timeout=15,
        )
        # cookie 过期则重新登录重试一次
        if r.status_code == 401:
            global _import_auth_cookie
            with _import_cookie_lock:
                _import_auth_cookie = None
            auth_cookie = _get_import_auth_cookie(tag)
            if not auth_cookie:
                with _stats_lock:
                    _stats["import_fail"] += 1
                return
            r = req.post(
                f"{_import_url}/api/accounts",
                json=payload,
                cookies={"mail_auth": auth_cookie},
                timeout=15,
            )
        if r.status_code == 200 and r.json().get("success"):
            with _print_lock:
                print(f"{C_CYAN}{tag}{C_RESET} ✓ 导入服务器成功: {email}")
            with _stats_lock:
                _stats["import_ok"] += 1
        else:
            with _print_lock:
                print(f"{C_CYAN}{tag}{C_RESET} ⚠ 导入服务器失败: {r.status_code} {r.text[:100]}")
            with _stats_lock:
                _stats["import_fail"] += 1
    except Exception as e:
        with _print_lock:
            print(f"{C_CYAN}{tag}{C_RESET} ⚠ 导入服务器异常: {e}")
        with _stats_lock:
            _stats["import_fail"] += 1


def _register_one(
    thread_id: int,
    proxy_url: str,
    cr_token: str,
    domain: str,
    country: str,
    output_file: str,
    file_lock: threading.Lock,
):
    """单个注册任务 (在一个线程中执行)"""
    tag = f"T{thread_id}"
    set_thread_tag(tag)

    # 每个线程独立生成随机信息
    username = random_username()
    password = random_password()
    first_name = random.choice(FIRST_NAMES)
    last_name = random.choice(LAST_NAMES)
    birth_year, birth_month, birth_day = random_birthdate()
    email = f"{username}@{domain}"

    # 生成本线程专属 Chrome UA
    ua, sec_ch = gen_chrome_ua()

    # 构建 CaptchaRun solver
    cr_solver = None
    flow_proxy = proxy_url
    if cr_token:
        if not proxy_url:
            with _print_lock:
                print(f"{C_CYAN}{tag}{C_RESET} ✗ 需要代理")
            with _stats_lock:
                _stats["no_proxy"] += 1
            return
        cr_solver = CaptchaRunSolver.from_proxy_url(
            token=cr_token,
            proxy_url=proxy_url,
        )
        flow_proxy = proxy_url

    signup = MicrosoftSignupProtocol(proxy=flow_proxy, user_agent=ua, sec_ch_ua=sec_ch)
    result = signup.register(
        username=username,
        domain=domain,
        password=password,
        country=country,
        birth_year=birth_year,
        birth_month=birth_month,
        birth_day=birth_day,
        first_name=first_name,
        last_name=last_name,
        cr_solver=cr_solver,
    )

    if result.get("success"):
        # 注册成功后自动 OAuth2 授权 (带重试)
        oauth = {"refresh_token": ""}
        for attempt in range(3):
            if attempt > 0:
                with _print_lock:
                    print(f"{C_CYAN}{tag}{C_RESET} → OAuth2 重试第 {attempt + 1} 次...")
                time.sleep(3)
            oauth = oauth2_authorize(
                email=result["email"],
                password=result["password"],
                proxy_url=proxy_url,
            )
            if oauth.get("refresh_token"):
                break
        refresh_token = oauth.get("refresh_token", "")
        client_id = oauth.get("client_id", OAUTH_CLIENT_ID)
        line = f"{result['email']}----{result['password']}----{client_id}----{refresh_token}\n"
        with file_lock:
            with open(output_file, "a", encoding="utf-8") as f:
                f.write(line)
        if refresh_token:
            with _print_lock:
                print(f"{C_CYAN}{tag}{C_RESET} ✓ Done: {result['email']} (含 OAuth2 令牌)")
            with _stats_lock:
                _stats["reg_and_oauth"] += 1
            # 自动导入到 mail_manager 服务器
            _import_to_server(result['email'], result['password'], client_id, refresh_token, tag)
        else:
            with _print_lock:
                print(f"{C_CYAN}{tag}{C_RESET} ⚠ Done: {result['email']} (OAuth2 授权失败, 仅保存账号密码)")
            with _stats_lock:
                _stats["reg_ok_oauth_fail"] += 1
    else:
        err = result.get('error', 'unknown')
        with _print_lock:
            print(f"{C_CYAN}{tag}{C_RESET} ✗ Done: 失败 - {err}")
        with _stats_lock:
            if '打码' in err:
                _stats["captcha_fail"] += 1
            else:
                _stats["reg_fail"] += 1


def _fix_auth(accounts_file: str, proxy_file: str = None, proxy: str = None):
    """扫描 accounts.json, 对缺 refresh_token 的账号重新授权"""
    try:
        with open(accounts_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"文件不存在: {accounts_file}")
        return

    # 加载代理列表
    proxy_list = []
    if proxy_file:
        try:
            with open(proxy_file, "r", encoding="utf-8") as f:
                proxy_list = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        except FileNotFoundError:
            print(f"代理文件不存在: {proxy_file}")
    if proxy:
        proxy_list.append(proxy)

    # 找出缺授权的账号
    missing = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        parts = line.split("----")
        if len(parts) >= 4 and parts[3].strip():
            continue
        email = parts[0]
        password = parts[1]
        client_id = parts[2] if len(parts) >= 3 else OAUTH_CLIENT_ID
        missing.append((i, email, password, client_id))

    if not missing:
        print(f"扫描完成: {len(lines)} 行, 全部已授权, 无需补全")
        return

    print(f"扫描完成: {len(lines)} 行, {len(missing)} 个账号缺授权, 开始补全...\n")

    fixed = 0
    for idx, email, password, client_id in missing:
        proxy_url = None
        if proxy_list:
            chosen = random.choice(proxy_list)
            proxy_url = _parse_proxy_line(chosen)
        print(f"[{idx+1}] 补授权: {email}")
        oauth = oauth2_authorize(email=email, password=password, proxy_url=proxy_url)
        rt = oauth.get("refresh_token", "")
        if rt:
            lines[idx] = f"{email}----{password}----{client_id}----{rt}\n"
            fixed += 1
            print(f"    ✓ 成功\n")
        else:
            print(f"    ✗ 失败\n")
        time.sleep(1)

    with open(accounts_file, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"补全完成: {fixed}/{len(missing)} 个账号已授权")


def main():
    parser = argparse.ArgumentParser(description="Microsoft Outlook 纯协议注册脚本")
    parser.add_argument("--username", default="", help="邮箱用户名 (留空随机)")
    parser.add_argument("--domain", default="outlook.com", choices=DOMAINS, help="邮箱域名")
    parser.add_argument("--password", default="", help="密码 (留空随机)")
    parser.add_argument("--country", default="US", help="国家 ISO 代码 (默认 US)")
    parser.add_argument("--year", type=int, default=0, help="出生年份 (留空随机)")
    parser.add_argument("--month", type=int, default=0, help="出生月份 (留空随机)")
    parser.add_argument("--day", type=int, default=0, help="出生日期 (留空随机)")
    parser.add_argument("--lastname", default="", help="姓氏 (留空随机)")
    parser.add_argument("--firstname", default="", help="名字 (留空随机)")
    parser.add_argument("--proxy", default=None, help="代理地址 (如 http://127.0.0.1:7890)")
    parser.add_argument("--proxy-file", default=None, help="代理列表文件 (每行 login:password@host:port)")
    parser.add_argument("--cr-token", default="", help="CaptchaRun API token (or env CAPTCHARUN_TOKEN)")
    parser.add_argument("--output", default="accounts.json", help="输出文件路径")
    parser.add_argument("--threads", type=int, default=1, help="并发线程数 (默认 1)")
    parser.add_argument("--fix-auth", action="store_true", help="扫描 accounts.json 补全缺授权的账号")
    parser.add_argument("--import-url", default="", help="mail_manager 服务器地址 (留空则不自动导入; or env OUTLOOK_REGISTER_IMPORT_URL)")
    parser.add_argument("--import-password", default="", help="mail_manager 访问密码 (or env OUTLOOK_REGISTER_IMPORT_PASSWORD)")
    args = parser.parse_args()

    # Automyai runtime defaults from env (do not hardcode secrets)
    import os
    if not args.cr_token:
        args.cr_token = os.environ.get("CAPTCHARUN_TOKEN", "").strip()
    if not args.import_url:
        args.import_url = os.environ.get("OUTLOOK_REGISTER_IMPORT_URL", "").strip()
    if not args.import_password:
        args.import_password = os.environ.get("OUTLOOK_REGISTER_IMPORT_PASSWORD", "").strip()
    if args.output == "accounts.json":
        env_out = os.environ.get("OUTLOOK_REGISTER_OUTPUT", "").strip()
        if env_out:
            args.output = env_out

    # 配置自动导入
    global _import_url, _import_password
    if args.import_url:
        _import_url = args.import_url.rstrip("/")
        _import_password = args.import_password

    if args.fix_auth:
        _fix_auth(args.output, args.proxy_file, args.proxy)
        return

    # 加载代理列表
    proxy_list = []
    if args.proxy_file:
        try:
            with open(args.proxy_file, "r", encoding="utf-8") as f:
                proxy_list = [line.strip() for line in f if line.strip() and not line.startswith("#")]
            if not proxy_list:
                with _print_lock:
                    print("代理列表文件为空")
                return
            with _print_lock:
                print(f"已加载 {len(proxy_list)} 个代理")
        except FileNotFoundError:
            with _print_lock:
                print(f"代理文件不存在: {args.proxy_file}")
            return

    if args.proxy:
        proxy_list.append(args.proxy)

    file_lock = threading.Lock()
    num_threads = max(1, args.threads)

    with _print_lock:
        print(f"启动 {num_threads} 个并发注册线程\n")

    if num_threads == 1:
        # 单线程模式 (兼容旧逻辑, 支持自定义参数)
        proxy_url = proxy_list[0] if proxy_list else None
        if proxy_url and not proxy_url.startswith("http://"):
            proxy_url = _parse_proxy_line(proxy_url)
        _register_one(
            thread_id=1,
            proxy_url=proxy_url,
            cr_token=args.cr_token,
            domain=args.domain,
            country=args.country,
            output_file=args.output,
            file_lock=file_lock,
        )
    else:
        # 多线程模式: 每个线程随机选代理, 随机生成账号信息
        threads = []
        for i in range(num_threads):
            proxy_url = None
            if proxy_list:
                chosen = random.choice(proxy_list)
                proxy_url = _parse_proxy_line(chosen)
                if not proxy_url:
                    with _print_lock:
                        print(f"{C_CYAN}T{i+1}{C_RESET} 代理格式无法解析: {chosen}")
                    continue
            t = threading.Thread(
                target=_register_one,
                args=(i + 1, proxy_url, args.cr_token, args.domain, args.country, args.output, file_lock),
                daemon=False,
            )
            threads.append(t)
            t.start()
            # 错开启动时间, 避免同时请求
            time.sleep(1)

        for t in threads:
            t.join()

    # 统计汇总
    total = sum(_stats.values())
    with _print_lock:
        print("\n" + "═" * 50)
        print(f"  任务统计 (共 {total} 个)")
        print("═" * 50)
        print(f"  {C_GREEN}注册 + OAuth2 成功:  {_stats['reg_and_oauth']}{C_RESET}")
        print(f"  {C_YELLOW}注册成功, OAuth2 失败: {_stats['reg_ok_oauth_fail']}{C_RESET}")
        print(f"  {C_RED}打码失败:            {_stats['captcha_fail']}{C_RESET}")
        print(f"  {C_RED}注册失败 (其他):     {_stats['reg_fail']}{C_RESET}")
        if _stats['no_proxy']:
            print(f"  {C_DIM}无代理跳过:           {_stats['no_proxy']}{C_RESET}")
        if _stats['import_ok'] or _stats['import_fail']:
            print(f"  {C_GREEN}导入服务器成功:       {_stats['import_ok']}{C_RESET}")
            print(f"  {C_YELLOW}导入服务器失败:       {_stats['import_fail']}{C_RESET}")
        print("═" * 50)


if __name__ == "__main__":
    main()
