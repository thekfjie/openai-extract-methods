"""Background runner for tools/outlook_register (Microsoft mailbox protocol signup)."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "outlook_register" / "outlook_register.py"
DATA_DIR = ROOT / "data" / "outlook_register"
ACCOUNTS_PATH = DATA_DIR / "accounts.txt"
PROXY_FILE_PATH = DATA_DIR / "proxies.txt"
STATUS_PATH = DATA_DIR / "status.json"
LIVE_LOG_PATH = DATA_DIR / "runs" / "live.log"
CAPTCHA_TOKEN_PATH = DATA_DIR / "captcharun.token"
LOG_MAX_LINES = 400
ACCOUNT_LINE_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+----")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "runs").mkdir(parents=True, exist_ok=True)
    if not ACCOUNTS_PATH.exists():
        ACCOUNTS_PATH.write_text("", encoding="utf-8")
    if not PROXY_FILE_PATH.exists():
        PROXY_FILE_PATH.write_text("", encoding="utf-8")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def _tail_lines(text: str, limit: int) -> list[str]:
    lines = text.splitlines()
    if limit <= 0 or len(lines) <= limit:
        return lines
    return lines[-limit:]


def parse_account_lines(text: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "----" not in line:
            continue
        parts = line.split("----")
        email = parts[0].strip() if parts else ""
        password = parts[1].strip() if len(parts) > 1 else ""
        client_id = parts[2].strip() if len(parts) > 2 else ""
        refresh_token = parts[3].strip() if len(parts) > 3 else ""
        if not email:
            continue
        items.append(
            {
                "email": email,
                "password": password,
                "clientId": client_id,
                "refreshToken": refresh_token,
                "hasRefreshToken": bool(refresh_token),
                "line": line,
            }
        )
    return items


def mask_secret(value: str, keep: int = 4) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= keep * 2:
        return "*" * len(text)
    return f"{text[:keep]}…{text[-keep:]}"


@dataclass
class OutlookRegisterState:
    running: bool = False
    stop_requested: bool = False
    phase: str = "idle"
    started_at: str = ""
    finished_at: str = ""
    updated_at: str = field(default_factory=_now)
    pid: int | None = None
    threads: int = 1
    domain: str = "outlook.com"
    country: str = "US"
    fill_auth: bool = False
    proxy_count: int = 0
    has_captcha_token: bool = False
    import_enabled: bool = False
    import_to_default_group: bool = True
    import_group_name: str = "默认分组"
    output_file: str = str(ACCOUNTS_PATH)
    last_exit_code: int | None = None
    last_error: str = ""
    command_summary: list[str] = field(default_factory=list)
    account_count: int = 0
    account_with_token: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OutlookRegisterManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        self._state = OutlookRegisterState()
        self._logs: list[dict[str, str]] = []
        _ensure_dirs()
        self._refresh_account_stats()

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_account_stats_locked()
            return self._state.to_dict()

    def get_logs(self, tail: int = 200) -> list[dict[str, str]]:
        with self._lock:
            if tail <= 0 or len(self._logs) <= tail:
                return list(self._logs)
            return list(self._logs[-tail:])

    def status_payload(self, tail: int = 200) -> dict[str, Any]:
        accounts = self.list_accounts(limit=20)
        return {
            "ok": True,
            "state": self.get_state(),
            "logs": self.get_logs(tail=tail),
            "paths": {
                "script": str(SCRIPT_PATH),
                "dataDir": str(DATA_DIR),
                "accounts": str(ACCOUNTS_PATH),
                "proxyFile": str(PROXY_FILE_PATH),
                "liveLog": str(LIVE_LOG_PATH),
            },
            "importDefaults": self.default_import_settings(),
            "captchaTokenConfigured": bool(
                os.environ.get("CAPTCHARUN_TOKEN", "").strip()
                or (CAPTCHA_TOKEN_PATH.is_file() and CAPTCHA_TOKEN_PATH.read_text(encoding="utf-8").strip())
            ),
            "accountFormat": {
                "segments": 4,
                "pattern": "email----password----client_id----refresh_token",
                "example": "name@outlook.com----Passw0rd!----9e5f94bc-e8a4-4e73-b8be-63364c29d753----0.AXxxx",
                "accountFormat": "client_id_refresh_token",
            },
            "accountsPreview": accounts["items"],
            "accountTotal": accounts["total"],
            "accountWithToken": accounts["withToken"],
        }

    def list_accounts(self, limit: int = 100) -> dict[str, Any]:
        _ensure_dirs()
        items = parse_account_lines(_read_text(ACCOUNTS_PATH))
        with_token = sum(1 for item in items if item.get("hasRefreshToken"))
        limited = items[-max(1, int(limit or 100)) :]
        # newest first for UI
        limited = list(reversed(limited))
        safe_items = []
        for item in limited:
            safe_items.append(
                {
                    "email": item["email"],
                    "hasRefreshToken": item["hasRefreshToken"],
                    "clientId": item["clientId"],
                    "passwordMasked": mask_secret(item["password"]),
                    "refreshTokenMasked": mask_secret(item["refreshToken"], keep=6),
                    "line": item["line"] if item["hasRefreshToken"] else f"{item['email']}----{item['password']}----{item['clientId']}----",
                }
            )
        return {"items": safe_items, "total": len(items), "withToken": with_token}

    def read_accounts_raw(self) -> str:
        _ensure_dirs()
        return _read_text(ACCOUNTS_PATH)

    def save_proxy_file(self, content: str) -> int:
        _ensure_dirs()
        lines = []
        for raw in str(content or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            lines.append(line)
        PROXY_FILE_PATH.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return len(lines)

    def append_log(self, message: str, level: str = "info") -> None:
        entry = {
            "time": _now(),
            "message": str(message).rstrip(),
            "level": level,
        }
        with self._lock:
            self._logs.append(entry)
            while len(self._logs) > LOG_MAX_LINES:
                self._logs.pop(0)
            self._state.updated_at = _now()
        try:
            _ensure_dirs()
            with LIVE_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(f"{entry['time']} [{entry['level']}] {entry['message']}\n")
        except OSError:
            pass

    def default_import_settings(self) -> dict[str, Any]:
        """Resolve OutlookEmail import defaults from live AutoMyAI config."""
        group_name = "默认分组"
        api_url = ""
        configured = False
        try:
            from server import CONFIG, OUTLOOK_EMAIL_ADMIN

            group_name = str(getattr(CONFIG, "mail_source_group_name", "") or "默认分组").strip() or "默认分组"
            api_url = str(getattr(CONFIG, "outlook_email_api_url", "") or "").rstrip("/")
            configured = bool(getattr(OUTLOOK_EMAIL_ADMIN, "configured", False) and api_url)
        except Exception:
            configured = False
        return {
            "enabledByDefault": True,
            "groupName": group_name,
            "apiUrl": api_url,
            "configured": configured,
            "provider": "outlook",
            "accountFormat": "client_id_refresh_token",
        }

    def import_registered_accounts(
        self,
        account_lines: list[str] | str,
        *,
        group_name: str = "",
    ) -> dict[str, Any]:
        """Import four-segment lines into OutlookEmail source group (默认分组)."""
        if isinstance(account_lines, str):
            raw_lines = [line.strip() for line in account_lines.splitlines() if line.strip()]
        else:
            raw_lines = [str(line or "").strip() for line in account_lines if str(line or "").strip()]

        usable: list[str] = []
        for line in raw_lines:
            if line.startswith("#"):
                continue
            parts = [part.strip() for part in line.split("----")]
            if len(parts) < 4 or not parts[0] or not parts[2] or not parts[3]:
                continue
            usable.append("----".join(parts[:4]))
        if not usable:
            return {"success": False, "error": "没有带 refresh_token 的四段账号可导入", "added_count": 0}

        try:
            from server import CONFIG, OUTLOOK_EMAIL_ADMIN
        except Exception as error:  # noqa: BLE001
            return {"success": False, "error": f"无法加载 OutlookEmail 管理客户端: {error}", "added_count": 0}

        if not getattr(OUTLOOK_EMAIL_ADMIN, "configured", False):
            return {"success": False, "error": "未配置 OutlookEmail 管理接口", "added_count": 0}

        target_group = str(group_name or getattr(CONFIG, "mail_source_group_name", "") or "默认分组").strip() or "默认分组"
        try:
            result = OUTLOOK_EMAIL_ADMIN.import_accounts(
                "\n".join(usable),
                group_name=target_group,
                provider="outlook",
                account_format="client_id_refresh_token",
                status="active",
                remark="outlook-register",
            )
        except Exception as error:  # noqa: BLE001
            return {"success": False, "error": str(error), "added_count": 0, "groupName": target_group, "lineCount": len(usable)}

        return {
            "success": True,
            "groupName": target_group,
            "lineCount": len(usable),
            **(result if isinstance(result, dict) else {}),
        }

    def _snapshot_account_lines(self) -> set[str]:
        return {item["line"] for item in parse_account_lines(_read_text(ACCOUNTS_PATH)) if item.get("line")}

    def start(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = options or {}
        if not SCRIPT_PATH.is_file():
            return {"error": f"未找到脚本: {SCRIPT_PATH}", "state": self.get_state()}

        cr_token = str(options.get("crToken") or options.get("captchaToken") or "").strip()
        if not cr_token:
            cr_token = os.environ.get("CAPTCHARUN_TOKEN", "").strip()
        if not cr_token and CAPTCHA_TOKEN_PATH.is_file():
            try:
                cr_token = CAPTCHA_TOKEN_PATH.read_text(encoding="utf-8").strip().splitlines()[0].strip()
            except OSError:
                cr_token = ""
        proxy = str(options.get("proxy") or "").strip()
        proxy_text = str(options.get("proxyText") or options.get("proxies") or "").strip()
        domain = str(options.get("domain") or "outlook.com").strip().lower() or "outlook.com"
        if domain not in {"outlook.com", "hotmail.com"}:
            return {"error": "domain 仅支持 outlook.com 或 hotmail.com", "state": self.get_state()}
        country = str(options.get("country") or "US").strip().upper() or "US"
        try:
            threads = max(1, min(20, int(options.get("threads") or 1)))
        except (TypeError, ValueError):
            threads = 1
        fill_auth = bool(options.get("fillAuth") or options.get("fill_auth"))

        import_defaults = self.default_import_settings()
        # Default ON: write successful registers into OutlookEmail 默认分组.
        if "importToDefaultGroup" in options:
            import_to_default = bool(options.get("importToDefaultGroup"))
        elif "import_to_default_group" in options:
            import_to_default = bool(options.get("import_to_default_group"))
        else:
            import_to_default = True
        import_group_name = str(
            options.get("importGroupName")
            or options.get("import_group_name")
            or import_defaults.get("groupName")
            or "默认分组"
        ).strip() or "默认分组"

        # Legacy external mail_manager import remains optional and off by default.
        import_url = str(options.get("importUrl") or options.get("import_url") or "").strip()
        import_password = str(options.get("importPassword") or options.get("import_password") or "").strip()

        if not fill_auth and not cr_token:
            return {"error": "缺少 CaptchaRun token（crToken 或环境变量 CAPTCHARUN_TOKEN）", "state": self.get_state()}

        with self._lock:
            if self._state.running:
                return {"error": "已有 Outlook 注册任务在运行", "state": self._state.to_dict()}
            self._stop_event.clear()
            self._logs = []
            self._state = OutlookRegisterState(
                running=True,
                phase="starting",
                started_at=_now(),
                updated_at=_now(),
                threads=threads,
                domain=domain,
                country=country,
                fill_auth=fill_auth,
                import_enabled=bool(import_url) or import_to_default,
                import_to_default_group=import_to_default,
                import_group_name=import_group_name,
                output_file=str(ACCOUNTS_PATH),
            )

        _ensure_dirs()
        if proxy_text:
            try:
                self.save_proxy_file(proxy_text)
            except Exception as error:  # noqa: BLE001
                with self._lock:
                    self._state.running = False
                    self._state.phase = "failed"
                    self._state.last_error = str(error)
                    self._state.finished_at = _now()
                    self._state.updated_at = _now()
                return {"error": f"保存代理失败: {error}", "state": self.get_state()}

        proxy_count = len([line for line in _read_text(PROXY_FILE_PATH).splitlines() if line.strip() and not line.strip().startswith("#")])
        command = [sys.executable, str(SCRIPT_PATH), "--domain", domain, "--country", country, "--threads", str(threads), "--output", str(ACCOUNTS_PATH)]
        if fill_auth:
            command.append("--fill-auth")
        if proxy:
            command.extend(["--proxy", proxy])
        if proxy_count > 0:
            command.extend(["--proxy-file", str(PROXY_FILE_PATH)])
        if import_url:
            command.extend(["--import-url", import_url])
            if import_password:
                command.extend(["--import-password", import_password])

        summary: list[str] = []
        for part in command:
            if part == cr_token and cr_token:
                summary.append(mask_secret(cr_token, keep=3))
            elif part == import_password and import_password:
                summary.append(mask_secret(import_password, keep=2))
            else:
                summary.append(part)
        if import_to_default:
            summary.append(f"[import→{import_group_name}]")

        with self._lock:
            self._state = OutlookRegisterState(
                running=True,
                phase="starting",
                started_at=self._state.started_at or _now(),
                updated_at=_now(),
                threads=threads,
                domain=domain,
                country=country,
                fill_auth=fill_auth,
                proxy_count=proxy_count + (1 if proxy and proxy_count == 0 else 0),
                has_captcha_token=bool(cr_token),
                import_enabled=bool(import_url) or import_to_default,
                import_to_default_group=import_to_default,
                import_group_name=import_group_name,
                output_file=str(ACCOUNTS_PATH),
                command_summary=summary,
            )
            self._refresh_account_stats_locked()

        try:
            LIVE_LOG_PATH.write_text("", encoding="utf-8")
        except OSError:
            pass

        self._thread = threading.Thread(
            target=self._run,
            args=(command, cr_token, import_to_default, import_group_name),
            daemon=True,
        )
        self._thread.start()
        return {"state": self.get_state(), "importDefaults": import_defaults}

    def stop(self) -> dict[str, Any]:
        process: subprocess.Popen[str] | None = None
        with self._lock:
            if not self._state.running:
                return {"state": self._state.to_dict(), "message": "没有运行中的 Outlook 注册任务"}
            self._state.stop_requested = True
            self._state.phase = "stopping"
            self._state.updated_at = _now()
            process = self._process
        self._stop_event.set()
        self._terminate_process(process)
        self.append_log("收到停止请求", level="warn")
        return {"state": self.get_state()}

    def _run(
        self,
        command: list[str],
        cr_token: str,
        import_to_default: bool = True,
        import_group_name: str = "默认分组",
    ) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        if cr_token:
            env["CAPTCHARUN_TOKEN"] = cr_token
        env["OUTLOOK_REGISTER_OUTPUT"] = str(ACCOUNTS_PATH)
        before_lines = self._snapshot_account_lines()
        self.append_log(f"启动 Outlook 注册: {' '.join(self.get_state().get('command_summary') or command)}")
        if import_to_default:
            self.append_log(f"注册成功后将自动导入 OutlookEmail 分组: {import_group_name}")
        code = 1
        try:
            process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            with self._lock:
                self._process = process
                self._state.pid = process.pid
                self._state.phase = "running"
                self._state.updated_at = _now()
            assert process.stdout is not None
            for line in process.stdout:
                if self._stop_event.is_set():
                    break
                message = line.rstrip("\n")
                if message:
                    self.append_log(message)
            if self._stop_event.is_set() and process.poll() is None:
                self._terminate_process(process)
            code = process.wait()
        except Exception as error:  # noqa: BLE001 - surface runner failures in UI logs
            code = 1
            self.append_log(f"任务异常: {error}", level="error")
            with self._lock:
                self._state.last_error = str(error)
        finally:
            after_items = parse_account_lines(_read_text(ACCOUNTS_PATH))
            new_lines = [
                item["line"]
                for item in after_items
                if item.get("line") and item["line"] not in before_lines and item.get("hasRefreshToken")
            ]
            if import_to_default and new_lines and not self._stop_event.is_set():
                self.append_log(f"开始导入 {len(new_lines)} 个新账号到 {import_group_name}")
                result = self.import_registered_accounts(new_lines, group_name=import_group_name)
                if result.get("success"):
                    added = result.get("added_count") or result.get("addedCount") or 0
                    skipped = result.get("skipped_count") or result.get("skippedCount") or 0
                    self.append_log(
                        f"导入完成: group={result.get('groupName') or import_group_name} added={added} skipped={skipped}"
                    )
                else:
                    self.append_log(f"导入失败: {result.get('error') or result}", level="error")
                    with self._lock:
                        if not self._state.last_error:
                            self._state.last_error = str(result.get("error") or "导入默认分组失败")
            elif import_to_default and not new_lines:
                self.append_log("没有新增带 refresh_token 的账号，跳过导入")

            with self._lock:
                self._state.running = False
                self._state.pid = None
                self._process = None
                self._state.last_exit_code = code
                self._state.finished_at = _now()
                self._state.updated_at = _now()
                self._state.phase = "stopped" if self._state.stop_requested else ("succeeded" if code == 0 else "failed")
                self._refresh_account_stats_locked()
            self.append_log(f"任务结束 exit={code} phase={self.get_state().get('phase')}")
    def _terminate_process(self, process: subprocess.Popen[str] | None) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                process.terminate()
            except Exception:
                pass
        try:
            process.wait(timeout=8)
        except Exception:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    def _refresh_account_stats(self) -> None:
        with self._lock:
            self._refresh_account_stats_locked()

    def _refresh_account_stats_locked(self) -> None:
        items = parse_account_lines(_read_text(ACCOUNTS_PATH))
        self._state.account_count = len(items)
        self._state.account_with_token = sum(1 for item in items if item.get("hasRefreshToken"))
        self._state.updated_at = _now()


OUTLOOK_REGISTER_MANAGER = OutlookRegisterManager()
