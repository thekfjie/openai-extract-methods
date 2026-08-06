#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Headless entry for the existing Grok TTK registrar.

Reuses tools/grok_ttk/grok_register_ttk.py without rewriting registration logic.
Supports start/stop from automyai web API via process control + stop flag file.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
# Allow optional config/export override for container rw mounts.
DEFAULT_STATE_DIR = Path(os.environ.get("GROK_TTK_STATE_DIR") or (ROOT.parent.parent / "data" / "grok_ttk"))
DEFAULT_CONFIG = ROOT / "config.json"
STATE_DIR = Path(os.environ.get("GROK_TTK_STATE_DIR") or DEFAULT_STATE_DIR)
STATE_DIR.mkdir(parents=True, exist_ok=True)
STOP_FLAG = STATE_DIR / "stop.flag"
STATUS_FILE = STATE_DIR / "status.json"
RESULTS_FILE = STATE_DIR / "results.json"
LOG_FILE = STATE_DIR / "run.log"
ACCOUNT_DIR = STATE_DIR / "account"
EXPORT_DIR = STATE_DIR / "exports"
ACCOUNT_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# Make registrar importable and pin its CONFIG_FILE before import side effects.
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONUNBUFFERED", "1")

CONFIG_PATH = Path(os.environ.get("GROK_TTK_CONFIG") or DEFAULT_CONFIG)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _append_log(message: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _load_status() -> dict[str, Any]:
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _update_status(**fields: Any) -> dict[str, Any]:
    state = _load_status()
    state.update(fields)
    state["updatedAt"] = _now()
    _write_json(STATUS_FILE, state)
    return state


def _load_results() -> list[dict[str, Any]]:
    try:
        data = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_results(rows: list[dict[str, Any]]) -> None:
    _write_json(RESULTS_FILE, {"items": rows} if False else rows)  # keep list shape for simplicity
    # RESULTS_FILE stores a list
    RESULTS_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_stop_flag() -> None:
    try:
        if STOP_FLAG.exists():
            STOP_FLAG.unlink()
    except Exception:
        pass


def request_stop() -> None:
    STOP_FLAG.write_text(_now(), encoding="utf-8")


def should_stop() -> bool:
    return STOP_FLAG.exists()


def _import_registrar():
    # Ensure config path used by registrar is the one we manage.
    import grok_register_ttk as ttk

    ttk.CONFIG_FILE = str(CONFIG_PATH)
    # Prefer state dir for account exports so container can write.
    original_get_account_export_dir = ttk.get_account_export_dir

    def get_account_export_dir(day=None):
        day = day or datetime.now().strftime("%Y%m%d")
        export_dir = ACCOUNT_DIR / day
        export_dir.mkdir(parents=True, exist_ok=True)
        return str(export_dir)

    ttk.get_account_export_dir = get_account_export_dir
    # Keep original available if needed.
    ttk._original_get_account_export_dir = original_get_account_export_dir
    return ttk


def _apply_overrides(ttk_mod, overrides: dict[str, Any]) -> dict[str, Any]:
    ttk_mod.load_config()
    cfg = ttk_mod.config
    # Map web-facing keys onto registrar config keys.
    key_map = {
        "emailProvider": "email_provider",
        "email_provider": "email_provider",
        "registerCount": "register_count",
        "register_count": "register_count",
        "registerThreads": "register_threads",
        "register_threads": "register_threads",
        "threadStartInterval": "thread_start_interval",
        "thread_start_interval": "thread_start_interval",
        "proxy": "proxy",
        "enableNsfw": "enable_nsfw",
        "enable_nsfw": "enable_nsfw",
        "duckmailApiKey": "duckmail_api_key",
        "duckmail_api_key": "duckmail_api_key",
        "yydsApiKey": "yyds_api_key",
        "yyds_api_key": "yyds_api_key",
        "yydsJwt": "yyds_jwt",
        "yyds_jwt": "yyds_jwt",
        "cloudflareApiBase": "cloudflare_api_base",
        "cloudflare_api_base": "cloudflare_api_base",
        "cloudflareApiKey": "cloudflare_api_key",
        "cloudflare_api_key": "cloudflare_api_key",
        "cloudflareAuthMode": "cloudflare_auth_mode",
        "cloudflare_auth_mode": "cloudflare_auth_mode",
        "cloudflareCustomAuth": "cloudflare_custom_auth",
        "cloudflare_custom_auth": "cloudflare_custom_auth",
        "cloudflarePathDomains": "cloudflare_path_domains",
        "cloudflare_path_domains": "cloudflare_path_domains",
        "cloudflarePathAccounts": "cloudflare_path_accounts",
        "cloudflare_path_accounts": "cloudflare_path_accounts",
        "cloudflarePathToken": "cloudflare_path_token",
        "cloudflare_path_token": "cloudflare_path_token",
        "cloudflarePathMessages": "cloudflare_path_messages",
        "cloudflare_path_messages": "cloudflare_path_messages",
        "defaultDomains": "defaultDomains",
        "userAgent": "user_agent",
        "user_agent": "user_agent",
        "grok2apiAutoAddLocal": "grok2api_auto_add_local",
        "grok2api_auto_add_local": "grok2api_auto_add_local",
        "grok2apiLocalTokenFile": "grok2api_local_token_file",
        "grok2api_local_token_file": "grok2api_local_token_file",
        "grok2apiPoolName": "grok2api_pool_name",
        "grok2api_pool_name": "grok2api_pool_name",
        "grok2apiAutoAddRemote": "grok2api_auto_add_remote",
        "grok2api_auto_add_remote": "grok2api_auto_add_remote",
        "grok2apiRemoteBase": "grok2api_remote_base",
        "grok2api_remote_base": "grok2api_remote_base",
        "grok2apiRemoteAppKey": "grok2api_remote_app_key",
        "grok2api_remote_app_key": "grok2api_remote_app_key",
        "cpaAutoAdd": "cpa_auto_add",
        "cpa_auto_add": "cpa_auto_add",
        "cpaAuthDir": "cpa_auth_dir",
        "cpa_auth_dir": "cpa_auth_dir",
        "cpaRemoteUrl": "cpa_remote_url",
        "cpa_remote_url": "cpa_remote_url",
        "cpaManagementKey": "cpa_management_key",
        "cpa_management_key": "cpa_management_key",
    }
    for src, dst in key_map.items():
        if src in overrides and overrides[src] is not None:
            cfg[dst] = overrides[src]
    # Convenience: paths as single comma string.
    if overrides.get("cloudflarePaths"):
        raw_paths = [x.strip() for x in str(overrides["cloudflarePaths"]).split(",") if x.strip()]
        if len(raw_paths) >= 4:
            cfg["cloudflare_path_domains"] = raw_paths[0] if raw_paths[0].startswith("/") else "/" + raw_paths[0]
            cfg["cloudflare_path_accounts"] = raw_paths[1] if raw_paths[1].startswith("/") else "/" + raw_paths[1]
            cfg["cloudflare_path_token"] = raw_paths[2] if raw_paths[2].startswith("/") else "/" + raw_paths[2]
            cfg["cloudflare_path_messages"] = raw_paths[3] if raw_paths[3].startswith("/") else "/" + raw_paths[3]
    # Numeric clamps
    try:
        cfg["register_count"] = max(1, min(100, int(cfg.get("register_count") or 1)))
    except Exception:
        cfg["register_count"] = 1
    try:
        cfg["register_threads"] = max(1, min(10, int(cfg.get("register_threads") or 1)))
    except Exception:
        cfg["register_threads"] = 1
    try:
        cfg["thread_start_interval"] = max(0.0, float(cfg.get("thread_start_interval") or 0.8))
    except Exception:
        cfg["thread_start_interval"] = 0.8
    cfg["show_tutorial_on_start"] = False
    # Persist into the registrar config file when writable.
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=4), encoding="utf-8")
    except Exception as exc:
        # Fall back to state dir config copy.
        fallback = STATE_DIR / "config.runtime.json"
        fallback.write_text(json.dumps(cfg, ensure_ascii=False, indent=4), encoding="utf-8")
        ttk_mod.CONFIG_FILE = str(fallback)
        _append_log(f"[Debug] 主配置只读，改写 runtime 配置: {fallback} ({exc})")
    ttk_mod.config = cfg
    return cfg


def _maybe_patch_cpa_export(ttk_mod) -> None:
    """Optional CPA auto-add using automyai integrations (no rewrite of register core)."""
    if not bool(ttk_mod.config.get("cpa_auto_add")):
        return
    # Patch success path by wrapping add_token_to_grok2api_pools.
    original = ttk_mod.add_token_to_grok2api_pools

    def wrapped(raw_token, email="", log_callback=None):
        original(raw_token, email=email, log_callback=log_callback)
        try:
            root = ROOT.parent.parent
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from integrations.grok_oauth import decode_jwt_payload, sso_to_token, write_cliproxy_file
            from integrations.cpa import CpaClient

            def clog(msg: str) -> None:
                if log_callback:
                    log_callback(f"[CPA] {msg}")

            proxy = str(ttk_mod.config.get("proxy") or "")
            token = sso_to_token(raw_token, proxy=proxy, log=clog)
            if not token:
                clog("device-flow 换 token 失败，跳过")
                return
            # New-account guard: inspect both OAuth JWTs before importing to CPA.
            # A flagged account remains usable through api.x.ai (using_api=true),
            # but stop the current batch so the operator can review the event.
            jwt_payloads = []
            for jwt_key in ("access_token", "id_token"):
                raw_jwt = str(token.get(jwt_key) or "").strip()
                if raw_jwt:
                    payload = decode_jwt_payload(raw_jwt)
                    if isinstance(payload, dict):
                        jwt_payloads.append(payload)
            bot_flagged = any(
                payload.get("bot_flag_source") in (1, "1", True)
                for payload in jwt_payloads
            )
            auth_dir = str(ttk_mod.config.get("cpa_auth_dir") or "").strip()
            remote_url = str(ttk_mod.config.get("cpa_remote_url") or "").strip()
            management_key = str(ttk_mod.config.get("cpa_management_key") or "").strip()
            client = CpaClient(
                enabled=True,
                auth_dir=auth_dir,
                remote_url=remote_url,
                management_key=management_key,
            )
            if auth_dir:
                path = write_cliproxy_file(Path(auth_dir), token, email=email)
                clog(f"已写入本地 {path}")
            if remote_url and management_key:
                result = client.import_token(token, email=email)
                clog(f"远程导入: {result}")
            if bot_flagged:
                clog(f"[!] JWT 检测到 bot_flag_source=1，已保留并启用 using_api，立即停止后续账号")
                request_stop()
        except Exception as exc:
            if log_callback:
                log_callback(f"[CPA] 直出失败: {exc}")

    ttk_mod.add_token_to_grok2api_pools = wrapped


def _linux_browser_options_patch(ttk_mod) -> None:
    """Ensure Chromium runs headfully under container Xvfb DISPLAY."""
    original = ttk_mod.create_browser_options

    def create_browser_options():
        options = original()
        # Prefer system chromium in container/linux.
        for candidate in (
            os.environ.get("CHROME_PATH"),
            os.environ.get("CHROMIUM_PATH"),
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
        ):
            if candidate and os.path.exists(candidate):
                try:
                    options.set_browser_path(candidate)
                except Exception:
                    try:
                        options.set_paths(browser_path=candidate)
                    except Exception:
                        pass
                break
        # Required flags for Docker/Xvfb.
        for arg in (
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1440,900",
            "--lang=en-US",
        ):
            try:
                options.set_argument(arg)
            except Exception:
                try:
                    options.add_argument(arg)
                except Exception:
                    pass
        # Keep headless false so Turnstile can render on Xvfb if available.
        display = os.environ.get("DISPLAY") or os.environ.get("BROWSER_DISPLAY") or ":1"
        os.environ["DISPLAY"] = display if str(display).startswith(":") else f":{display}"
        return options

    ttk_mod.create_browser_options = create_browser_options


class HeadlessGrokRegister:
    """Minimal stand-in for GrokRegisterGUI, reusing _run_single_registration / _worker_loop patterns."""

    def __init__(self, ttk_mod, count: int, worker_count: int):
        self.ttk = ttk_mod
        self.count = count
        self.worker_count = worker_count
        self.is_running = False
        self.stop_requested = False
        self.success_count = 0
        self.fail_count = 0
        self.results: list[dict[str, Any]] = []
        self.stats_lock = threading.Lock()
        self.accounts_output_file = ""
        self.eyj_tokens_output_file = ""
        self._log_lock = threading.Lock()

    def log(self, message: str) -> None:
        with self._log_lock:
            _append_log(message)
            # keep rolling status log pointer
            st = _load_status()
            lines = st.get("recentLogs") or []
            lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
            st["recentLogs"] = lines[-200:]
            st["success"] = self.success_count
            st["failed"] = self.fail_count
            st["completed"] = self.success_count + self.fail_count
            st["phase"] = "running" if self.is_running else st.get("phase") or "running"
            st["updatedAt"] = _now()
            _write_json(STATUS_FILE, st)

    def should_stop(self) -> bool:
        return self.stop_requested or should_stop()

    def update_stats(self) -> None:
        _update_status(
            success=self.success_count,
            failed=self.fail_count,
            completed=self.success_count + self.fail_count,
            total=self.count,
            phase="running" if self.is_running else "idle",
            accountsFile=self.accounts_output_file,
            tokensFile=self.eyj_tokens_output_file,
        )

    def _set_running_ui(self, running: bool) -> None:
        self.is_running = running
        _update_status(running=running, phase="running" if running else "idle")

    def _run_single_registration(self, idx, total, logf):
        # Reuse GrokRegisterGUI._run_single_registration body without constructing Tk UI.
        gui = self.ttk.GrokRegisterGUI.__new__(self.ttk.GrokRegisterGUI)
        gui.is_running = True
        gui.stop_requested = False
        gui.success_count = self.success_count
        gui.fail_count = self.fail_count
        gui.results = self.results
        gui.stats_lock = self.stats_lock
        gui.accounts_output_file = self.accounts_output_file
        gui.eyj_tokens_output_file = self.eyj_tokens_output_file
        gui.should_stop = self.should_stop
        gui.log = self.log
        gui.update_stats = self.update_stats
        try:
            return self.ttk.GrokRegisterGUI._run_single_registration(gui, idx, total, logf)
        finally:
            with self.stats_lock:
                self.success_count = int(getattr(gui, "success_count", self.success_count) or 0)
                self.fail_count = max(self.fail_count, int(getattr(gui, "fail_count", 0) or 0))

    def _worker_loop(self, worker_id, total, task_queue):
        prefix = f"[T{worker_id}]"
        logf: Callable[[str], None] = lambda m: self.log(f"{prefix} {m}")
        try:
            self.ttk.start_browser(log_callback=logf)
            logf("[*] 浏览器已启动，猫猫出发喵~")
            while not self.should_stop():
                try:
                    idx = task_queue.get_nowait()
                except queue.Empty:
                    break
                logf(f"--- 开始第 {idx}/{total} 个账号，猫爪开工喵 ---")
                try:
                    self._run_single_registration(idx, total, logf)
                    with self.stats_lock:
                        # success increments happen inside _run_single_registration
                        pass
                except self.ttk.RegistrationCancelled:
                    logf("[!] 注册被用户停止")
                    break
                except Exception as exc:
                    with self.stats_lock:
                        self.fail_count += 1
                    logf(f"[-] 注册失败: {exc}")
                finally:
                    # sync counts from results if needed
                    with self.stats_lock:
                        # keep success_count consistent with results length when possible
                        if len(self.results) > self.success_count:
                            self.success_count = len(self.results)
                    self.update_stats()
                    if self.should_stop():
                        break
                    self.ttk.restart_browser(log_callback=logf)
                    self.ttk.sleep_with_cancel(1, self.should_stop)
        except Exception as exc:
            logf(f"[!] 线程异常: {exc}")
            logf(traceback.format_exc())
        finally:
            self.ttk.stop_browser()

    def run(self) -> None:
        self.accounts_output_file, self.eyj_tokens_output_file = self.ttk.get_account_export_paths()
        # Also symlink/copy-friendly exports under EXPORT_DIR
        try:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # ensure parent exists
            Path(self.accounts_output_file).parent.mkdir(parents=True, exist_ok=True)
            Path(self.eyj_tokens_output_file).parent.mkdir(parents=True, exist_ok=True)
            # mirror path names into exports for UI download convenience
            (EXPORT_DIR / f"accounts_{stamp}.txt").write_text("", encoding="utf-8")
            (EXPORT_DIR / f"tokens_eyJ_{stamp}.txt").write_text("", encoding="utf-8")
        except Exception:
            pass
        self._set_running_ui(True)
        self.update_stats()
        self.log(f"[*] 配置已加载，猫猫开始执行喵~ 目标数量: {self.count}，并发线程: {self.worker_count}")
        self.log(f"[*] 成功账号会实时收好在: {self.accounts_output_file}")
        self.log(f"[*] eyJ Token 会额外收好在: {self.eyj_tokens_output_file}")
        task_queue: queue.Queue = queue.Queue()
        for i in range(1, self.count + 1):
            task_queue.put(i)
        workers = []
        try:
            start_interval = float(self.ttk.config.get("thread_start_interval", 0.8))
        except Exception:
            start_interval = 0.8
        if start_interval < 0:
            start_interval = 0.0
        for wid in range(1, self.worker_count + 1):
            t = threading.Thread(target=self._worker_loop, args=(wid, self.count, task_queue), daemon=True)
            workers.append(t)
            t.start()
            if wid < self.worker_count and start_interval > 0:
                self.ttk.sleep_with_cancel(start_interval, self.should_stop)
        for t in workers:
            t.join()
        # persist results
        with self.stats_lock:
            rows = list(self.results)
        try:
            existing = _load_results()
            # merge unique by email+sso
            seen = {(r.get("email"), r.get("sso")) for r in existing if isinstance(r, dict)}
            for r in rows:
                key = (r.get("email"), r.get("sso"))
                if key not in seen:
                    existing.insert(0, {**r, "savedAt": _now()})
                    seen.add(key)
            existing = existing[:500]
            RESULTS_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            self.log(f"[Debug] 保存 results 失败: {exc}")
        # mirror export files into EXPORT_DIR
        try:
            if self.accounts_output_file and Path(self.accounts_output_file).exists():
                target = EXPORT_DIR / Path(self.accounts_output_file).name
                target.write_text(Path(self.accounts_output_file).read_text(encoding="utf-8"), encoding="utf-8")
            if self.eyj_tokens_output_file and Path(self.eyj_tokens_output_file).exists():
                target = EXPORT_DIR / Path(self.eyj_tokens_output_file).name
                target.write_text(Path(self.eyj_tokens_output_file).read_text(encoding="utf-8"), encoding="utf-8")
        except Exception as exc:
            self.log(f"[Debug] 同步导出文件失败: {exc}")
        self._set_running_ui(False)
        phase = "stopped" if self.should_stop() else "completed"
        _update_status(
            running=False,
            phase=phase,
            success=self.success_count,
            failed=self.fail_count,
            completed=self.success_count + self.fail_count,
            total=self.count,
            finishedAt=_now(),
            accountsFile=self.accounts_output_file,
            tokensFile=self.eyj_tokens_output_file,
        )
        self.log(f"[*] 任务结束，猫猫收工啦~ 成功 {self.success_count} | 失败 {self.fail_count}")



def _parse_proxy_url(proxy: str) -> dict[str, str]:
    """Parse http://user:pass@host:port or host:port:user:pass forms."""
    raw = str(proxy or "").strip()
    out = {"scheme": "http", "host": "", "port": "", "user": "", "password": "", "url": raw}
    if not raw:
        return out
    # host:port:user:pass  (cliproxy style, 4 segments)
    if "://" not in raw and raw.count(":") >= 3:
        host, port, user, password = raw.split(":", 3)
        out.update(scheme="http", host=host, port=port, user=user, password=password)
        out["url"] = f"http://{user}:{password}@{host}:{port}"
        return out
    try:
        from urllib.parse import urlparse, unquote
        parsed = urlparse(raw if "://" in raw else f"http://{raw}")
        out["scheme"] = parsed.scheme or "http"
        out["host"] = parsed.hostname or ""
        out["port"] = str(parsed.port or (443 if parsed.scheme == "https" else 80))
        out["user"] = unquote(parsed.username or "")
        out["password"] = unquote(parsed.password or "")
        if out["user"]:
            out["url"] = f"{out['scheme']}://{out['user']}:{out['password']}@{out['host']}:{out['port']}"
        else:
            out["url"] = f"{out['scheme']}://{out['host']}:{out['port']}"
    except Exception:
        pass
    return out


class _AuthProxyRelay:
    """Tiny local HTTP CONNECT/forward proxy that injects Proxy-Authorization.

    Chromium cannot pass user:pass in --proxy-server. For cliproxy-style auth proxies,
    we start a local no-auth relay and point Chromium at it.
    """

    def __init__(self, upstream: str):
        self.upstream = _parse_proxy_url(upstream)
        self.server = None
        self.thread = None
        self.listen_host = "127.0.0.1"
        self.listen_port = 0

    @property
    def needs_relay(self) -> bool:
        return bool(self.upstream.get("user") and self.upstream.get("host"))

    @property
    def local_url(self) -> str:
        return f"http://{self.listen_host}:{self.listen_port}"

    def start(self) -> str:
        import base64
        import select
        import socket
        import socketserver
        import threading

        if not self.needs_relay:
            return self.upstream.get("url") or ""

        up_host = self.upstream["host"]
        up_port = int(self.upstream["port"] or 80)
        user = self.upstream["user"]
        password = self.upstream["password"]
        auth_header = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    first = self.rfile.readline(65535)
                    if not first:
                        return
                    line = first.decode("iso-8859-1", errors="replace").strip()
                    parts = line.split()
                    if len(parts) < 2:
                        return
                    method, target = parts[0].upper(), parts[1]
                    # consume headers
                    headers = []
                    while True:
                        h = self.rfile.readline(65535)
                        if not h or h in (b"\r\n", b"\n"):
                            break
                        headers.append(h)
                    upstream = socket.create_connection((up_host, up_port), timeout=30)
                    try:
                        if method == "CONNECT":
                            req = (
                                f"CONNECT {target} HTTP/1.1\r\n"
                                f"Host: {target}\r\n"
                                f"Proxy-Authorization: {auth_header}\r\n"
                                f"Proxy-Connection: keep-alive\r\n\r\n"
                            ).encode()
                            upstream.sendall(req)
                            # read upstream response headers
                            resp = b""
                            while b"\r\n\r\n" not in resp and len(resp) < 65535:
                                chunk = upstream.recv(4096)
                                if not chunk:
                                    break
                                resp += chunk
                            if b" 200 " not in resp.split(b"\r\n", 1)[0]:
                                self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                                return
                            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                            self._pipe(self.connection, upstream)
                        else:
                            # absolute-form HTTP request
                            rebuilt = [f"{method} {target} HTTP/1.1\r\n".encode()]
                            for h in headers:
                                low = h.lower()
                                if low.startswith(b"proxy-authorization:") or low.startswith(b"proxy-connection:"):
                                    continue
                                rebuilt.append(h)
                            rebuilt.append(f"Proxy-Authorization: {auth_header}\r\n".encode())
                            rebuilt.append(b"Proxy-Connection: keep-alive\r\n\r\n")
                            upstream.sendall(b"".join(rebuilt))
                            self._pipe(self.connection, upstream)
                    finally:
                        try:
                            upstream.close()
                        except Exception:
                            pass
                except Exception:
                    try:
                        self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                    except Exception:
                        pass

            def _pipe(self, client, upstream):
                sockets = [client, upstream]
                while True:
                    r, _, x = select.select(sockets, [], sockets, 60)
                    if x or not r:
                        break
                    for sock in r:
                        other = upstream if sock is client else client
                        try:
                            data = sock.recv(65535)
                        except Exception:
                            return
                        if not data:
                            return
                        try:
                            other.sendall(data)
                        except Exception:
                            return

        class ThreadingTCPServer(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = ThreadingTCPServer((self.listen_host, 0), Handler)
        self.listen_port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.local_url

    def stop(self) -> None:
        if self.server is not None:
            try:
                self.server.shutdown()
            except Exception:
                pass
            try:
                self.server.server_close()
            except Exception:
                pass
            self.server = None


def prepare_browser_proxy(proxy: str) -> tuple[str, Any]:
    """Return (browser_proxy_url, relay_or_None). Normalizes host:port:user:pass."""
    info = _parse_proxy_url(proxy)
    if not info.get("host"):
        return "", None
    # mihomo no-auth already fine
    if not info.get("user"):
        return info["url"], None
    relay = _AuthProxyRelay(info["url"])
    local = relay.start()
    return local, relay


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Headless Grok TTK registrar")
    parser.add_argument("--config-json", default="", help="JSON overrides for this run")
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--threads", type=int, default=0)
    parser.add_argument("--proxy", default="")
    parser.add_argument("--email-provider", default="")
    args = parser.parse_args(argv)

    clear_stop_flag()
    # reset log for this run
    try:
        LOG_FILE.write_text("", encoding="utf-8")
    except Exception:
        pass

    overrides: dict[str, Any] = {}
    if args.config_json:
        try:
            overrides = json.loads(args.config_json)
        except Exception as exc:
            _append_log(f"[!] 无效 config-json: {exc}")
            return 2
    if args.count:
        overrides["register_count"] = args.count
    if args.threads:
        overrides["register_threads"] = args.threads
    if args.proxy:
        overrides["proxy"] = args.proxy
    if args.email_provider:
        overrides["email_provider"] = args.email_provider

    _update_status(
        running=True,
        phase="starting",
        pid=os.getpid(),
        startedAt=_now(),
        success=0,
        failed=0,
        completed=0,
        total=int(overrides.get("register_count") or overrides.get("registerCount") or 1),
        recentLogs=[],
        stopRequested=False,
        error="",
    )

    def _handle_sig(_signum, _frame):
        request_stop()
        _append_log("[!] 收到停止信号")

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    try:
        _proxy_relay = None
        ttk = _import_registrar()
        cfg = _apply_overrides(ttk, overrides)
        if cfg.get("email_provider") == "cloudflare" and not cfg.get("cloudflare_api_base"):
            raise RuntimeError("Cloudflare 模式需要先填写 Cloudflare API Base")
        _linux_browser_options_patch(ttk)
        _maybe_patch_cpa_export(ttk)
        # Ensure browser uses proxy (Chromium auth via local relay when needed).
        raw_proxy = str(cfg.get("proxy") or "").strip()
        browser_proxy, _proxy_relay = prepare_browser_proxy(raw_proxy)
        # FlareSolverr / CF clearance env (share same upstream proxy)
        os.environ.setdefault("GROK_CF_CLEARANCE_ENABLED", str(cfg.get("cf_clearance_enabled", True)).lower())
        os.environ.setdefault(
            "GROK_CF_CLEARANCE_API_URL",
            str(cfg.get("cf_clearance_api_url") or "http://127.0.0.1:18191/v1"),
        )
        os.environ.setdefault("GROK_CF_CLEARANCE_TARGET_URL", "https://accounts.x.ai/sign-up")
        if browser_proxy:
            os.environ["GROK_TTK_BROWSER_PROXY"] = browser_proxy
            # keep HTTP client proxy as full credential URL
            if raw_proxy and "://" not in raw_proxy and raw_proxy.count(":") >= 3:
                cfg["proxy"] = _parse_proxy_url(raw_proxy)["url"]
                ttk.config = cfg
            _append_log(f"[*] 浏览器代理已接通，猫尾巴信号稳定喵: {browser_proxy}" + (" (本地鉴权中继)" if _proxy_relay else ""))
        else:
            os.environ.pop("GROK_TTK_BROWSER_PROXY", None)
            _append_log("[!] 未配置 proxy，浏览器可能暴露主机真实 IP")
        count = max(1, int(cfg.get("register_count") or 1))
        workers = max(1, min(int(cfg.get("register_threads") or 1), count, 10))
        _update_status(total=count, phase="running", configSnapshot={
            "email_provider": cfg.get("email_provider"),
            "register_count": count,
            "register_threads": workers,
            "proxy": cfg.get("proxy"),
            "enable_nsfw": cfg.get("enable_nsfw"),
            "cloudflare_api_base": cfg.get("cloudflare_api_base"),
            "cloudflare_auth_mode": cfg.get("cloudflare_auth_mode"),
            "defaultDomains": cfg.get("defaultDomains"),
            "grok2api_auto_add_remote": cfg.get("grok2api_auto_add_remote"),
            "grok2api_remote_base": cfg.get("grok2api_remote_base"),
            "grok2api_pool_name": cfg.get("grok2api_pool_name"),
            "cpa_auto_add": cfg.get("cpa_auto_add"),
            "cpa_auth_dir": cfg.get("cpa_auth_dir"),
            "cpa_remote_url": cfg.get("cpa_remote_url"),
        })
        runner = HeadlessGrokRegister(ttk, count=count, worker_count=workers)
        try:
            runner.run()
        finally:
            try:
                if _proxy_relay is not None:
                    _proxy_relay.stop()
            except Exception:
                pass
        return 0
    except Exception as exc:
        _append_log(f"[!] 任务异常: {exc}")
        _append_log(traceback.format_exc())
        _update_status(running=False, phase="error", error=str(exc), finishedAt=_now())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
