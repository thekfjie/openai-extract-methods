from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("CARD_CDK_DB", str(ROOT / "data" / "card_cdk.sqlite3")))
SECRET = os.getenv("CARD_CDK_SECRET", "").strip()
_LOCK = threading.RLock()
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PENDING_TTL_SECONDS = 6 * 3600


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_db():
    with _LOCK, _connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS cdks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_hash TEXT NOT NULL UNIQUE,
            code_hint TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            max_activations INTEGER NOT NULL DEFAULT 1,
            activation_count INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS cdk_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT NOT NULL UNIQUE,
            cdk_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            user_agent_hash TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(cdk_id) REFERENCES cdks(id)
        );
        CREATE INDEX IF NOT EXISTS idx_cdk_sessions_expiry ON cdk_sessions(expires_at);
        CREATE TABLE IF NOT EXISTS cdk_usage_events (
            event_key TEXT PRIMARY KEY,
            cdk_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL,
            finalized_at REAL,
            FOREIGN KEY(cdk_id) REFERENCES cdks(id),
            FOREIGN KEY(session_id) REFERENCES cdk_sessions(id)
        );
        CREATE INDEX IF NOT EXISTS idx_cdk_usage_events_cdk_status
            ON cdk_usage_events(cdk_id,status);
        CREATE TABLE IF NOT EXISTS cdk_merge_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_cdk_id INTEGER NOT NULL,
            merged_cdk_id INTEGER NOT NULL,
            merged_code_encrypted TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(source_cdk_id,merged_cdk_id)
        );
        CREATE INDEX IF NOT EXISTS idx_cdk_merge_source ON cdk_merge_history(source_cdk_id,id);
        CREATE INDEX IF NOT EXISTS idx_cdk_merge_target ON cdk_merge_history(merged_cdk_id,id);
        """)


def _digest(value: str) -> str:
    if not SECRET:
        raise RuntimeError("CARD_CDK_SECRET_MISSING")
    return hashlib.sha256((SECRET + "|" + value).encode("utf-8")).hexdigest()


def _merge_fernet() -> Fernet:
    if not SECRET:
        raise RuntimeError("CARD_CDK_SECRET_MISSING")
    key = base64.urlsafe_b64encode(hashlib.sha256((SECRET + "|merge-history").encode("utf-8")).digest())
    return Fernet(key)


def _encrypt_merge_code(code: str) -> str:
    return _merge_fernet().encrypt(str(code or "").strip().upper().encode("utf-8")).decode("ascii")


def _decrypt_merge_code(value: str) -> str:
    try:
        return _merge_fernet().decrypt(str(value or "").encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return ""


def normalize_code(value: str) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _new_code() -> str:
    groups = ["".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(5)]
    return "CDK-" + "-".join(groups)


def _usage_detail(row, *, pending: int = 0) -> dict:
    used = int(row["activation_count"])
    maximum = int(row["max_activations"])
    return {
        "id": int(row["id"]),
        "code_hint": row["code_hint"],
        "expires_at": float(row["expires_at"]),
        "usage_count": used,
        "max_uses": maximum,
        "remaining_uses": max(0, maximum - used),
        "pending_uses": max(0, int(pending)),
        # Compatibility fields for the existing admin API.
        "activation_count": used,
        "max_activations": maximum,
    }


def create_codes(quantity: int, valid_days: int, max_activations: int, note: str = "") -> list[dict]:
    quantity = max(1, min(100, int(quantity)))
    valid_days = max(1, min(3650, int(valid_days)))
    max_activations = max(1, min(10000, int(max_activations)))
    now = time.time()
    expires_at = now + valid_days * 86400
    output = []
    with _LOCK, _connect() as conn:
        for _ in range(quantity):
            for _attempt in range(20):
                code = _new_code()
                normalized = normalize_code(code)
                try:
                    cur = conn.execute(
                        "INSERT INTO cdks(code_hash,code_hint,note,created_at,expires_at,max_activations) VALUES(?,?,?,?,?,?)",
                        (_digest(normalized), code[:8] + "..." + code[-4:], str(note or "")[:120], now, expires_at, max_activations),
                    )
                    output.append({"id": cur.lastrowid, "code": code, "expires_at": expires_at, "max_activations": max_activations})
                    break
                except sqlite3.IntegrityError:
                    continue
    return output


def merge_codes(codes: list[str]) -> dict:
    normalized_codes = []
    for value in codes or []:
        normalized = normalize_code(value)
        if normalized and normalized not in normalized_codes:
            normalized_codes.append(normalized)
    if len(normalized_codes) < 2:
        raise ValueError("CDK_MERGE_REQUIRES_TWO")
    if len(normalized_codes) > 100:
        raise ValueError("CDK_MERGE_TOO_MANY")
    now = time.time()
    with _LOCK, _connect() as conn:
        rows = []
        for normalized in normalized_codes:
            row = conn.execute("SELECT * FROM cdks WHERE code_hash=?", (_digest(normalized),)).fetchone()
            if not row:
                raise ValueError("CDK_MERGE_CODE_INVALID")
            if not int(row["enabled"]):
                raise ValueError("CDK_MERGE_CODE_DISABLED")
            if float(row["expires_at"]) <= now:
                raise ValueError("CDK_MERGE_CODE_EXPIRED")
            pending = int(conn.execute(
                "SELECT COUNT(*) FROM cdk_usage_events WHERE cdk_id=? AND status='pending'",
                (int(row["id"]),),
            ).fetchone()[0])
            if pending:
                raise ValueError("CDK_MERGE_CODE_IN_USE")
            rows.append(row)
        maximum = sum(int(row["max_activations"]) for row in rows)
        used = sum(int(row["activation_count"]) for row in rows)
        expires_at = max(float(row["expires_at"]) for row in rows)
        new_code = ""
        new_id = 0
        for _attempt in range(30):
            candidate = _new_code()
            try:
                cur = conn.execute(
                    "INSERT INTO cdks(code_hash,code_hint,note,created_at,expires_at,max_activations,activation_count) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        _digest(normalize_code(candidate)), candidate[:8] + "..." + candidate[-4:],
                        f"Merged {len(rows)} CDKs", now, expires_at, maximum, used,
                    ),
                )
                new_code = candidate
                new_id = int(cur.lastrowid)
                break
            except sqlite3.IntegrityError:
                continue
        if not new_code:
            raise RuntimeError("CDK_MERGE_GENERATION_FAILED")
        source_ids = [int(row["id"]) for row in rows]
        encrypted_code = _encrypt_merge_code(new_code)
        for source_id in source_ids:
            conn.execute(
                "INSERT OR REPLACE INTO cdk_merge_history(source_cdk_id,merged_cdk_id,merged_code_encrypted,created_at) VALUES(?,?,?,?)",
                (source_id, new_id, encrypted_code, now),
            )
        placeholders = ",".join("?" for _ in source_ids)
        conn.execute(f"UPDATE cdks SET enabled=0 WHERE id IN ({placeholders})", source_ids)
        conn.execute(f"DELETE FROM cdk_sessions WHERE cdk_id IN ({placeholders})", source_ids)
        return {
            "id": new_id, "code": new_code, "expires_at": expires_at,
            "max_activations": maximum, "activation_count": used,
            "remaining_uses": max(0, maximum - used), "merged_count": len(rows),
            "source_ids": source_ids,
        }


def activate_code(code: str, user_agent: str = "") -> tuple[str, dict]:
    """Create a browser session. Entering a CDK no longer consumes a use."""
    normalized = normalize_code(code)
    if len(normalized) < 16:
        raise ValueError("CDK_FORMAT_INVALID")
    now = time.time()
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM cdk_sessions WHERE expires_at <= ?", (now,))
        row = conn.execute("SELECT * FROM cdks WHERE code_hash = ?", (_digest(normalized),)).fetchone()
        if not row:
            raise ValueError("CDK_INVALID")
        if not int(row["enabled"]):
            raise ValueError("CDK_DISABLED")
        if float(row["expires_at"]) <= now:
            raise ValueError("CDK_EXPIRED")
        if int(row["activation_count"]) >= int(row["max_activations"]):
            raise ValueError("CDK_USAGE_LIMIT")
        raw_token = secrets.token_urlsafe(36)
        expires_at = float(row["expires_at"])
        ua_hash = hashlib.sha256(str(user_agent or "").encode("utf-8")).hexdigest()[:24]
        conn.execute(
            "INSERT INTO cdk_sessions(token_hash,cdk_id,created_at,expires_at,user_agent_hash) VALUES(?,?,?,?,?)",
            (_digest(raw_token), int(row["id"]), now, expires_at, ua_hash),
        )
        return raw_token, _usage_detail(row)


def session_status(raw_token: str) -> dict | None:
    token = str(raw_token or "").strip()
    if not token:
        return None
    now = time.time()
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT s.id AS session_id,s.expires_at AS session_expires_at,c.* "
            "FROM cdk_sessions s JOIN cdks c ON c.id=s.cdk_id WHERE s.token_hash=?",
            (_digest(token),),
        ).fetchone()
        if (
            not row
            or not int(row["enabled"])
            or float(row["session_expires_at"]) <= now
            or float(row["expires_at"]) <= now
        ):
            return None
        pending = conn.execute(
            "SELECT COUNT(*) FROM cdk_usage_events WHERE cdk_id=? AND status='pending'",
            (int(row["id"]),),
        ).fetchone()[0]
        detail = _usage_detail(row, pending=int(pending))
        detail["session_id"] = int(row["session_id"])
        detail["expires_at"] = min(float(row["session_expires_at"]), float(row["expires_at"]))
        return detail


def validate_session(raw_token: str) -> dict | None:
    detail = session_status(raw_token)
    if not detail or int(detail.get("remaining_uses") or 0) <= 0:
        return None
    return detail


def reserve_usage(raw_token: str, event_key: str) -> dict:
    """Reserve exactly one use for a single payment or simultaneous payment batch."""
    token = str(raw_token or "").strip()
    key = str(event_key or "").strip()[:160]
    if not token:
        raise ValueError("CDK_REQUIRED")
    if not key:
        raise ValueError("CDK_USAGE_EVENT_INVALID")
    now = time.time()
    with _LOCK, _connect() as conn:
        conn.execute(
            "UPDATE cdk_usage_events SET status='released',finalized_at=? "
            "WHERE status='pending' AND created_at<?",
            (now, now - PENDING_TTL_SECONDS),
        )
        existing = conn.execute("SELECT * FROM cdk_usage_events WHERE event_key=?", (key,)).fetchone()
        if existing:
            row = conn.execute("SELECT * FROM cdks WHERE id=?", (int(existing["cdk_id"]),)).fetchone()
            if not row:
                raise ValueError("CDK_INVALID")
            detail = _usage_detail(row)
            detail.update({"event_key": key, "event_status": existing["status"]})
            return detail
        row = conn.execute(
            "SELECT s.id AS session_id,s.expires_at AS session_expires_at,c.* "
            "FROM cdk_sessions s JOIN cdks c ON c.id=s.cdk_id WHERE s.token_hash=?",
            (_digest(token),),
        ).fetchone()
        if not row or not int(row["enabled"]):
            raise ValueError("CDK_REQUIRED")
        if float(row["session_expires_at"]) <= now or float(row["expires_at"]) <= now:
            raise ValueError("CDK_EXPIRED")
        pending = int(conn.execute(
            "SELECT COUNT(*) FROM cdk_usage_events WHERE cdk_id=? AND status='pending'",
            (int(row["id"]),),
        ).fetchone()[0])
        if int(row["activation_count"]) + pending >= int(row["max_activations"]):
            raise ValueError("CDK_USAGE_LIMIT")
        conn.execute(
            "INSERT INTO cdk_usage_events(event_key,cdk_id,session_id,status,created_at) VALUES(?,?,?,?,?)",
            (key, int(row["id"]), int(row["session_id"]), "pending", now),
        )
        detail = _usage_detail(row, pending=pending + 1)
        detail.update({"event_key": key, "event_status": "pending"})
        return detail


def finalize_usage(event_key: str, success: bool) -> dict | None:
    """Charge a reserved use only when at least one payment in the group succeeded."""
    key = str(event_key or "").strip()[:160]
    if not key:
        return None
    now = time.time()
    with _LOCK, _connect() as conn:
        event = conn.execute("SELECT * FROM cdk_usage_events WHERE event_key=?", (key,)).fetchone()
        if not event:
            return None
        row = conn.execute("SELECT * FROM cdks WHERE id=?", (int(event["cdk_id"]),)).fetchone()
        if not row:
            return None
        status = str(event["status"] or "")
        if status == "pending":
            if success:
                conn.execute(
                    "UPDATE cdks SET activation_count=activation_count+1 "
                    "WHERE id=? AND activation_count<max_activations",
                    (int(row["id"]),),
                )
                conn.execute(
                    "UPDATE cdk_usage_events SET status='charged',finalized_at=? WHERE event_key=?",
                    (now, key),
                )
                status = "charged"
            else:
                conn.execute(
                    "UPDATE cdk_usage_events SET status='released',finalized_at=? WHERE event_key=?",
                    (now, key),
                )
                status = "released"
            row = conn.execute("SELECT * FROM cdks WHERE id=?", (int(row["id"]),)).fetchone()
        pending = int(conn.execute(
            "SELECT COUNT(*) FROM cdk_usage_events WHERE cdk_id=? AND status='pending'",
            (int(row["id"]),),
        ).fetchone()[0])
        detail = _usage_detail(row, pending=pending)
        detail.update({"event_key": key, "event_status": status})
        return detail


def list_codes(limit: int = 500) -> list[dict]:
    now = time.time()
    with _LOCK, _connect() as conn:
        rows = conn.execute("SELECT * FROM cdks ORDER BY id DESC LIMIT ?", (max(1, min(1000, int(limit))),)).fetchall()
        pending_by_id = {
            int(row["cdk_id"]): int(row["n"])
            for row in conn.execute(
                "SELECT cdk_id,COUNT(*) AS n FROM cdk_usage_events WHERE status='pending' GROUP BY cdk_id"
            ).fetchall()
        }
    output = []
    for row in rows:
        item = _usage_detail(row, pending=pending_by_id.get(int(row["id"]), 0))
        item.update({
            "note": row["note"], "created_at": float(row["created_at"]),
            "enabled": bool(row["enabled"]), "expired": float(row["expires_at"]) <= now,
        })
        output.append(item)
    return output


def lookup_code_detail(code: str) -> dict:
    normalized = normalize_code(code)
    if len(normalized) < 16:
        raise ValueError("CDK_FORMAT_INVALID")
    now = time.time()
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM cdks WHERE code_hash=?", (_digest(normalized),)).fetchone()
        if not row:
            raise ValueError("CDK_INVALID")
        pending = int(conn.execute(
            "SELECT COUNT(*) FROM cdk_usage_events WHERE cdk_id=? AND status='pending'", (int(row["id"]),)
        ).fetchone()[0])
        item = _usage_detail(row, pending=pending)
        item.update({"enabled": bool(row["enabled"]), "expired": float(row["expires_at"]) <= now, "note": row["note"]})
        return item


def lookup_merge_chain(code: str) -> dict:
    normalized = normalize_code(code)
    if len(normalized) < 16:
        raise ValueError("CDK_FORMAT_INVALID")
    with _LOCK, _connect() as conn:
        source = conn.execute("SELECT * FROM cdks WHERE code_hash=?", (_digest(normalized),)).fetchone()
        if not source:
            raise ValueError("CDK_INVALID")
        current_id = int(source["id"])
        chain = []
        seen = {current_id}
        for _ in range(30):
            record = conn.execute(
                "SELECT * FROM cdk_merge_history WHERE source_cdk_id=? ORDER BY id DESC LIMIT 1", (current_id,)
            ).fetchone()
            if not record:
                break
            merged_id = int(record["merged_cdk_id"])
            if merged_id in seen:
                break
            seen.add(merged_id)
            merged = conn.execute("SELECT * FROM cdks WHERE id=?", (merged_id,)).fetchone()
            code_value = _decrypt_merge_code(record["merged_code_encrypted"])
            chain.append({
                "source_cdk_id": current_id, "merged_cdk_id": merged_id,
                "merged_code": code_value, "merged_code_hint": merged["code_hint"] if merged else "",
                "enabled": bool(merged["enabled"]) if merged else False,
                "created_at": float(record["created_at"]),
            })
            current_id = merged_id
        return {
            "source": {"id": int(source["id"]), "code_hint": source["code_hint"], "enabled": bool(source["enabled"])},
            "chain": chain, "found": bool(chain),
            "final_code": chain[-1]["merged_code"] if chain else "",
            "final_code_hint": chain[-1]["merged_code_hint"] if chain else "",
        }


def set_enabled(cdk_id: int, enabled: bool) -> bool:
    with _LOCK, _connect() as conn:
        cur = conn.execute("UPDATE cdks SET enabled=? WHERE id=?", (1 if enabled else 0, int(cdk_id)))
        if not enabled:
            conn.execute("DELETE FROM cdk_sessions WHERE cdk_id=?", (int(cdk_id),))
            conn.execute(
                "UPDATE cdk_usage_events SET status='released',finalized_at=? WHERE cdk_id=? AND status='pending'",
                (time.time(), int(cdk_id)),
            )
        return cur.rowcount > 0


def delete_code(cdk_id: int) -> bool:
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM cdk_merge_history WHERE source_cdk_id=? OR merged_cdk_id=?", (int(cdk_id), int(cdk_id)))
        conn.execute("DELETE FROM cdk_usage_events WHERE cdk_id=?", (int(cdk_id),))
        conn.execute("DELETE FROM cdk_sessions WHERE cdk_id=?", (int(cdk_id),))
        cur = conn.execute("DELETE FROM cdks WHERE id=?", (int(cdk_id),))
        return cur.rowcount > 0


init_db()
