"""Cross-process account execution guard shared by protocol services."""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, IO


ACCOUNT_RUN_LOCK_DIR = Path(os.getenv("AUTOMYAI_ACCOUNT_RUN_LOCK_DIR", "/app/data/account-run-locks"))
ACCOUNT_RUN_GUARD_ENABLED = os.getenv("AUTOMYAI_ACCOUNT_RUN_GUARD", "1").strip().lower() not in {"0", "false"}


class AccountRunBusy(RuntimeError):
    """Raised when another workflow currently owns the account lease."""


def _claims(access_token: str) -> dict[str, Any]:
    parts = str(access_token or "").strip().split(".")
    if len(parts) < 2:
        return {}
    try:
        segment = parts[1] + "=" * (-len(parts[1]) % 4)
        value = json.loads(base64.urlsafe_b64decode(segment.encode("ascii")).decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def account_run_identity(access_token: str) -> tuple[str, str]:
    token = str(access_token or "").strip()
    claims = _claims(token)
    auth = claims.get("https://api.openai.com/auth") or {}
    profile = claims.get("https://api.openai.com/profile") or {}
    auth = auth if isinstance(auth, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    account_id = str(auth.get("chatgpt_account_id") or auth.get("account_id") or claims.get("account_id") or "").strip()
    email = str(profile.get("email") or claims.get("email") or "").strip().lower()
    identity = "account:" + account_id if account_id else "token:" + hashlib.sha256(token.encode()).hexdigest()[:16]
    return identity, email


def acquire_account_run(
    access_token: str,
    job_id: str,
    method: str,
    parent_lease_id: str = "",
) -> IO[str] | None:
    if not ACCOUNT_RUN_GUARD_ENABLED:
        return None
    identity, email = account_run_identity(access_token)
    ACCOUNT_RUN_LOCK_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = ACCOUNT_RUN_LOCK_DIR / (hashlib.sha256(identity.encode()).hexdigest()[:16] + ".lock")
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.seek(0)
        try:
            owner = json.loads(handle.read() or "{}")
        except Exception:
            owner = {}
        handle.close()
        # The payment portal may own the account while it delegates one
        # internal sub-step to this protocol process.  Reuse only the exact
        # opaque parent job id written into this account's own lock file; a
        # different UI action or account still conflicts normally.
        parent = str(parent_lease_id or "").strip()
        if parent and str(owner.get("jobId") or "").strip() == parent:
            return None
        where = "/".join(filter(None, [str(owner.get("service") or ""), str(owner.get("method") or "")]))
        detail = f"，当前位于 {where}" if where else ""
        raise AccountRunBusy(f"ACCOUNT_ALREADY_RUNNING: {email or identity}{detail}；请等待该任务结束或先停止它") from exc
    owner = {
        "service": "支付协议",
        "jobId": str(job_id or ""),
        "method": str(method or ""),
        "label": email,
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps(owner, ensure_ascii=False))
    handle.flush()
    os.fsync(handle.fileno())
    return handle


def release_account_run(handle: IO[str] | None) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
