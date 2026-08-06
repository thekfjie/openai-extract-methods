"""Authenticated bridge from the Python panel to the loopback Go extraction API.

The extraction workflow itself lives in Go. This module only forwards already
authenticated panel requests to the internal service; it never parses tokens,
selects proxies, or executes a payment flow.
"""

from __future__ import annotations

import http.client
import json
import os
from typing import Any
from urllib.parse import urlencode


COMPATIBILITY_PATHS = {
    "/api/extract-methods/catalog",
    "/api/extract-methods/run",
    "/api/long-link-task",
    "/api/extract-pp",
    "/api/paper-card-task",
    "/api/ph-link-task",
    "/api/momo-eligibility",
    "/api/kakao-long-link-task",
    "/api/upi-long-link-task",
    "/api/ideal-long-link-task",
    "/api/gopay-long-link-task",
}


def handles_path(path: str) -> bool:
    return path.startswith("/api/extract/") or path in COMPATIBILITY_PATHS


def forward_request(
    method: str,
    path: str,
    query: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    port = int(os.environ.get("EXTRACT_API_PORT", "18794"))
    target = path
    if query:
        target = f"{target}?{urlencode(query)}"
    encoded: bytes | None = None
    headers = {"Accept": "application/json"}
    if method in {"POST", "PUT", "PATCH"}:
        encoded = json.dumps(body or {}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=35)
    try:
        connection.request(method, target, body=encoded, headers=headers)
        response = connection.getresponse()
        raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise RuntimeError("Go 提炼服务响应超过 8 MiB")
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Go 提炼服务返回了无效 JSON（HTTP {response.status}）") from error
        if not isinstance(payload, dict):
            payload = {"ok": response.status < 400, "data": payload}
        return response.status, payload
    except (OSError, http.client.HTTPException) as error:
        raise RuntimeError(f"Go 提炼服务不可用: {error}") from error
    finally:
        connection.close()
