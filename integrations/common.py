from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def b64url_decode(seg: str) -> bytes:
    seg += "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg.encode("ascii"))


def decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        parts = str(token or "").split(".")
        if len(parts) < 2:
            return {}
        return json.loads(b64url_decode(parts[1]).decode("utf-8", errors="ignore"))
    except Exception:
        return {}


def rfc3339_sec(ts: float | None = None) -> str:
    if ts is None:
        ts = time.time()
    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def rfc3339_ns(ts: float | None = None) -> str:
    if ts is None:
        ts = time.time()
    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + ".000000000Z"


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def sanitize_sso(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower().startswith("sso="):
        text = text[4:].strip()
    # cookie header blob
    match = re.search(r"(?:^|;\s*)sso=([^;]+)", text, flags=re.I)
    if match:
        text = match.group(1).strip()
    return text


def http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: Any = None,
    timeout: float = 30,
    proxy_url: str = "",
) -> tuple[int, Any, str]:
    data = None
    req_headers = {"Accept": "application/json", "User-Agent": "help-oai/1.0"}
    if headers:
        req_headers.update(headers)
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        elif isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        else:
            data = str(body).encode("utf-8")
    handlers = []
    if proxy_url:
        handlers.append(ProxyHandler({"http": proxy_url, "https": proxy_url}))
    opener = build_opener(*handlers)
    req = Request(url, data=data, headers=req_headers, method=method.upper())
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200) or 200
            try:
                parsed = json.loads(raw) if raw else None
            except Exception:
                parsed = raw
            return int(status), parsed, raw
    except HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace") if hasattr(error, "read") else str(error)
        try:
            parsed = json.loads(raw) if raw else None
        except Exception:
            parsed = raw
        return int(error.code), parsed, raw
    except URLError as error:
        return 0, None, str(error.reason if hasattr(error, "reason") else error)


def write_json_atomic(path: Path, payload: Any, *, compact: bool = False, mode: int = 0o600) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    else:
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(mode)
    tmp.replace(path)
    return path


def load_json_file(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default
