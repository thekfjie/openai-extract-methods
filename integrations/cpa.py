from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .common import http_json, write_json_atomic
from .grok_oauth import token_to_cliproxy_entry, write_cliproxy_file


class CpaError(Exception):
    pass


class CpaClient:
    def __init__(
        self,
        *,
        enabled: bool = True,
        auth_dir: str = "",
        remote_url: str = "",
        management_key: str = "",
        api_key: str = "",
    ) -> None:
        self.enabled = bool(enabled)
        self.auth_dir = Path(auth_dir).expanduser() if auth_dir else None
        self.remote_url = str(remote_url or "").rstrip("/")
        self.management_key = str(management_key or "").strip()
        self.api_key = str(api_key or "").strip()

    def health(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "enabled": self.enabled,
            "authDir": str(self.auth_dir) if self.auth_dir else "",
            "remoteUrl": self.remote_url,
            "localReady": bool(self.auth_dir),
            "remoteReady": bool(self.remote_url and self.management_key),
        }
        if self.remote_url:
            status, payload, raw = http_json(
                "GET",
                f"{self.remote_url}/v0/management/auth-files",
                headers=self._mgmt_headers(),
                timeout=8,
            )
            result["remoteStatus"] = status
            result["remoteOk"] = status in {200, 401, 403}
            if status == 200:
                result["remoteFiles"] = payload
            elif raw:
                result["remoteError"] = raw[:200]
        return result

    def _mgmt_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.management_key:
            headers["Authorization"] = f"Bearer {self.management_key}"
            headers["X-Management-Key"] = self.management_key
        return headers

    def import_token(self, token: dict, email: str = "") -> dict[str, Any]:
        if not self.enabled:
            raise CpaError("CPA 未启用")
        filename, entry = token_to_cliproxy_entry(token, email=email)
        out: dict[str, Any] = {
            "filename": filename,
            "email": entry.get("email") or email,
            "localPath": "",
            "remote": None,
        }
        if self.auth_dir:
            path = write_cliproxy_file(self.auth_dir, token, email=email)
            out["localPath"] = str(path)
        if self.remote_url and self.management_key:
            try:
                out["remote"] = self.upload_auth_file(filename, entry)
            except Exception as error:
                # Local auth-dir write is usually enough when CPA mounts the same volume.
                out["remoteError"] = str(error)
                if not out["localPath"]:
                    raise
        if not out["localPath"] and not out["remote"]:
            raise CpaError("未配置 CPA_AUTH_DIR 或远程 Management")
        return out

    def upload_auth_file(self, filename: str, entry: dict[str, Any]) -> dict[str, Any]:
        """Upload auth JSON via multipart form-data (required by current CLIProxyAPI)."""
        safe_name = str(filename or "").strip()
        if not safe_name.endswith(".json"):
            safe_name = f"{safe_name}.json"
        content = json.dumps(entry, ensure_ascii=False, indent=2).encode("utf-8")

        # Primary: multipart that management center expects.
        try:
            return self._upload_multipart(safe_name, content)
        except Exception as multi_err:
            multi_msg = str(multi_err)
        # Fallback: older JSON shapes (kept for compatibility).
        errors = [f"multipart -> {multi_msg[:160]}"]
        from urllib.parse import quote

        for name in (safe_name, safe_name.replace("@", "_at_"), safe_name.replace("@", "-")):
            encoded = quote(name, safe="._-")
            candidates = [
                ("POST", f"{self.remote_url}/v0/management/auth-files", {"name": name, "content": entry}),
                ("POST", f"{self.remote_url}/v0/management/auth-files", {"filename": name, "data": entry}),
                ("PUT", f"{self.remote_url}/v0/management/auth-files/{encoded}", entry),
            ]
            for method, url, body in candidates:
                status, payload, raw = http_json(method, url, headers=self._mgmt_headers(), body=body, timeout=20)
                if 200 <= status < 300:
                    return {"ok": True, "status": status, "payload": payload, "url": url, "name": name, "mode": "json"}
                errors.append(f"{method} {url} -> {status} {(raw or '')[:120]}")
        raise CpaError("CPA 远程上传失败: " + " | ".join(errors[:8]))

    def _upload_multipart(self, filename: str, content: bytes) -> dict[str, Any]:
        boundary = f"----automyai{uuid.uuid4().hex}"
        last_error = "unknown"
        crlf = "\r\n"
        attempts = [
            [("name", filename), ("file", filename, content)],
            [("filename", filename), ("file", filename, content)],
            [("name", filename), ("auth_file", filename, content)],
        ]
        for attempt in attempts:
            parts: list[bytes] = []
            for item in attempt:
                if len(item) == 2:
                    name, value = item
                    parts.append(
                        (
                            f"--{boundary}" + crlf
                            + f'Content-Disposition: form-data; name="{name}"' + crlf + crlf
                            + f"{value}" + crlf
                        ).encode("utf-8")
                    )
                else:
                    field, file_name, data = item
                    parts.append(
                        (
                            f"--{boundary}" + crlf
                            + f'Content-Disposition: form-data; name="{field}"; filename="{file_name}"' + crlf
                            + "Content-Type: application/json" + crlf + crlf
                        ).encode("utf-8")
                    )
                    parts.append(data)
                    parts.append(crlf.encode("utf-8"))
            parts.append(("--" + boundary + "--" + crlf).encode("utf-8"))
            body = b"".join(parts)
            headers = self._mgmt_headers()
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            req = Request(
                f"{self.remote_url}/v0/management/auth-files",
                data=body,
                method="POST",
                headers=headers,
            )
            try:
                with urlopen(req, timeout=20) as response:
                    raw = response.read()
                    try:
                        payload = json.loads(raw.decode("utf-8") or "{}")
                    except Exception:
                        payload = {"raw": raw.decode("utf-8", errors="replace")[:300]}
                    return {
                        "ok": True,
                        "status": getattr(response, "status", 200),
                        "payload": payload,
                        "name": filename,
                        "mode": "multipart",
                        "fields": [i[0] for i in attempt],
                    }
            except HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")[:180]
                last_error = f"HTTP {error.code}: {detail}"
            except URLError as error:
                last_error = str(error)
        raise CpaError(last_error)
