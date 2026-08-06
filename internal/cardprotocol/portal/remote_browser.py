from __future__ import annotations

import itertools
import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


_DISPLAY_COUNTER = itertools.count(220)
_DISPLAY_LOCK = threading.Lock()
_BROWSER_LIMIT = max(1, min(int(os.getenv("PH_PORTAL_BROWSER_LIMIT", "2") or 2), 4))
_BROWSER_SEMAPHORE = threading.BoundedSemaphore(_BROWSER_LIMIT)
_CHECKOUT_RE = re.compile(r"^https://chatgpt\.com/checkout/(?:openai_ie|openai_llc)/[^/?#]+")
_SESSION_COOKIE_NAMES = {
    "__Secure-next-auth.session-token",
    "next-auth.session-token",
    "__Secure-authjs.session-token",
    "authjs.session-token",
    "oai-did",
    "_account",
    "_account_is_fedramp",
    "__Secure-oai-is",
    "auth_provider",
    "oai-client-auth-session",
    "unified_session_manifest",
    "oaicom-stable-id",
    "oai-sc",
}


def playwright_proxy(raw: str) -> dict[str, str] | None:
    value = str(raw or "").strip()
    if not value or value.upper() == "DIRECT":
        return None
    if "://" not in value:
        value = f"http://{value}"
    parsed = urlsplit(value)
    if not parsed.hostname or not parsed.port:
        raise ValueError("账号代理格式不正确")
    result = {"server": f"{parsed.scheme or 'http'}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        result["username"] = unquote(parsed.username)
    if parsed.password:
        result["password"] = unquote(parsed.password)
    return result


@dataclass
class RemoteBrowserState:
    ready: bool = False
    done: bool = False
    error: str = ""
    message: str = "正在启动临时 Chromium…"
    current_url: str = ""
    checkout_url: str = ""


class RemoteBrowserController:
    """Render one isolated Chromium context and reuse a stored ChatGPT session."""

    def __init__(
        self,
        *,
        start_url: str,
        executable_path: str = "<REPLACE_ME>",
        viewport_width: int = 1280,
        viewport_height: int = 800,
        locale: str = "en-PH",
        timezone_id: str = "Asia/Manila",
        session_cookies: dict[str, str] | None = None,
        session_token: str = "",
        access_token: str = "",
        chatgpt_account_id: str = "",
        proxy: str = "",
        user_agent: str = "",
    ) -> None:
        self.start_url = start_url
        self.executable_path = executable_path
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.locale = locale
        self.timezone_id = timezone_id
        self.session_cookies = dict(session_cookies or {})
        self.session_token = str(session_token or "").strip()
        self.access_token = str(access_token or "").strip()
        self.chatgpt_account_id = str(chatgpt_account_id or "").strip()
        self.proxy = str(proxy or "").strip()
        self.user_agent = str(user_agent or "").strip()
        self._lock = threading.RLock()
        self._state = RemoteBrowserState()
        self._frame = b""
        self._commands: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=100)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="ph-short-remote-browser", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self._commands.put_nowait({"type": "stop"})
        except queue.Full:
            pass

    def state(self) -> RemoteBrowserState:
        with self._lock:
            return RemoteBrowserState(**self._state.__dict__)

    def frame(self) -> bytes:
        with self._lock:
            return bytes(self._frame)

    def action(self, payload: dict[str, Any]) -> None:
        action_type = str(payload.get("type") or "").strip().lower()
        if action_type not in {"click", "text", "key", "reload", "scroll"}:
            raise ValueError("不支持的浏览器操作")
        command = dict(payload)
        command["type"] = action_type
        try:
            self._commands.put_nowait(command)
        except queue.Full as exc:
            raise ValueError("浏览器操作队列已满") from exc

    def _set_state(self, **values: Any) -> None:
        with self._lock:
            for key, value in values.items():
                setattr(self._state, key, value)

    @staticmethod
    def _next_display() -> int:
        with _DISPLAY_LOCK:
            for _ in range(100):
                number = next(_DISPLAY_COUNTER)
                if not Path(f"/tmp/.X{number}-lock").exists():
                    return number
        raise RuntimeError("没有可用的临时显示编号")

    def _cookie_payload(self) -> list[dict[str, Any]]:
        values = {
            str(name): str(value)
            for name, value in self.session_cookies.items()
            if str(name) in _SESSION_COOKIE_NAMES and str(value)
        }
        if self.session_token:
            values.setdefault("__Secure-next-auth.session-token", self.session_token)
        return [
            {
                "name": name,
                "value": value,
                "url": "<REPLACE_ME>",
                "secure": True,
                "sameSite": "Lax",
            }
            for name, value in values.items()
        ]

    def _run(self) -> None:
        xvfb: subprocess.Popen | None = None
        browser = None
        acquired = False
        try:
            self._set_state(message="等待临时浏览器资源…")
            while not self._stop_event.is_set():
                if _BROWSER_SEMAPHORE.acquire(timeout=0.5):
                    acquired = True
                    break
            if not acquired:
                return

            display_number = self._next_display()
            display = f":{display_number}"
            xvfb = subprocess.Popen(
                [
                    "Xvfb", display, "-screen", "0",
                    f"{self.viewport_width}x{self.viewport_height}x24",
                    "-nolisten", "tcp", "-ac",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.35)
            if xvfb.poll() is not None:
                raise RuntimeError("Xvfb 启动失败")

            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright

            self._set_state(message="正在注入账号已有 Cookie、AT 与代理…")
            with sync_playwright() as playwright:
                launch_options: dict[str, Any] = {
                    "headless": False,
                    "executable_path": self.executable_path,
                    "env": {**os.environ, "DISPLAY": display},
                    "args": [
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-background-networking",
                        f"--window-size={self.viewport_width},{self.viewport_height}",
                    ],
                }
                proxy_config = playwright_proxy(self.proxy)
                if proxy_config:
                    launch_options["proxy"] = proxy_config
                browser = playwright.chromium.launch(**launch_options)

                context_options: dict[str, Any] = {
                    "viewport": {"width": self.viewport_width, "height": self.viewport_height},
                    "locale": self.locale,
                    "timezone_id": self.timezone_id,
                }
                if self.user_agent:
                    context_options["user_agent"] = self.user_agent
                context = browser.new_context(**context_options)
                cookies = self._cookie_payload()
                if cookies:
                    context.add_cookies(cookies)

                page = context.new_page()
                if self.access_token:
                    def add_existing_auth(route, req):
                        headers = dict(req.headers)
                        headers["authorization"] = f"Bearer {self.access_token}"
                        if self.chatgpt_account_id:
                            headers["chatgpt-account-id"] = self.chatgpt_account_id
                        route.continue_(headers=headers)

                    page.route("<REPLACE_ME>backend-api/**", add_existing_auth)

                try:
                    page.goto(self.start_url, wait_until="domcontentloaded", timeout=30000)
                except PlaywrightTimeoutError:
                    self._set_state(message="官方短链仍在加载，可在画面中继续操作")
                self._set_state(
                    ready=True,
                    message="账号已有会话已注入；没有执行登录，也没有刷新 AT",
                )

                last_frame_at = 0.0
                while not self._stop_event.is_set():
                    pages = [item for item in context.pages if not item.is_closed()]
                    if pages:
                        page = pages[-1]
                    current_url = str(page.url or "")
                    checkout_match = _CHECKOUT_RE.match(current_url)
                    state_values: dict[str, Any] = {"current_url": current_url}
                    if checkout_match:
                        state_values.update(
                            checkout_url=checkout_match.group(0),
                            message="菲律宾 Checkout 已在原账号会话中打开；全程未刷新 AT",
                        )
                    elif "/auth/" in current_url or "/login" in current_url:
                        state_values.update(
                            message="保存的登录 Cookie 已过期；本次没有刷新 AT，也没有改动短链",
                        )
                    self._set_state(**state_values)

                    try:
                        command = self._commands.get(timeout=0.12)
                    except queue.Empty:
                        command = None
                    if command:
                        command_type = command.get("type")
                        if command_type == "stop":
                            break
                        if command_type == "click":
                            x = max(0.0, min(float(command.get("x", 0)), self.viewport_width))
                            y = max(0.0, min(float(command.get("y", 0)), self.viewport_height))
                            page.mouse.click(x, y)
                        elif command_type == "text":
                            page.keyboard.type(str(command.get("value") or "")[:500], delay=18)
                        elif command_type == "key":
                            key = str(command.get("key") or "")
                            if key in {"Enter", "Tab", "Backspace", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"}:
                                page.keyboard.press(key)
                        elif command_type == "reload":
                            page.reload(wait_until="domcontentloaded", timeout=45000)
                        elif command_type == "scroll":
                            page.mouse.wheel(0, max(-1400, min(int(command.get("delta_y", 600)), 1400)))

                    now = time.time()
                    if now - last_frame_at >= 0.65:
                        try:
                            frame = page.screenshot(type="jpeg", quality=72, full_page=False, timeout=8000)
                            with self._lock:
                                self._frame = frame
                        except Exception as exc:
                            self._set_state(message=f"画面生成中：{str(exc)[:100]}")
                        last_frame_at = now
        except Exception as exc:
            self._set_state(done=True, error=str(exc), message="临时浏览器运行失败")
        finally:
            try:
                if browser is not None:
                    browser.close()
            except Exception:
                pass
            if xvfb is not None:
                try:
                    xvfb.terminate()
                    xvfb.wait(timeout=3)
                except Exception:
                    try:
                        xvfb.kill()
                    except Exception:
                        pass
            if acquired:
                _BROWSER_SEMAPHORE.release()
            self._set_state(done=True)
