#!/usr/bin/env python3
"""Session bookkeeping for optional traffic metering."""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .meter_proxy import MeteredProxy, redact_proxy

def _resolve_data_dir() -> Path:
    """Prefer the real mounted data dir (host or container)."""
    candidates = [
        Path("/app/data/traffic_meter"),
        Path("/opt/automyai/data/traffic_meter"),
        Path(__file__).resolve().parents[2] / "data" / "traffic_meter",
    ]
    # Prefer a path that already has history, else first writable parent.
    for candidate in candidates:
        try:
            if (candidate / "sessions.jsonl").is_file():
                return candidate
        except Exception:
            continue
    for candidate in candidates:
        try:
            if candidate.is_dir() or candidate.parent.is_dir():
                return candidate
        except Exception:
            continue
    return candidates[0]


DATA_DIR = _resolve_data_dir()
SESSIONS_FILE = DATA_DIR / "sessions.jsonl"
ACTIVE_DIR = DATA_DIR / "active"

_lock = threading.Lock()
_active: dict[str, "MeterSession"] = {}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)


def _fmt_bytes(n: int) -> str:
    n = max(0, int(n or 0))
    for unit, div in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= div:
            return f"{n / div:.2f} {unit}"
    return f"{n} B"


class MeterSession:
    def __init__(
        self,
        *,
        service: str,
        run_id: str,
        upstream_proxy: str,
        meta: Optional[dict[str, Any]] = None,
    ) -> None:
        self.id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        self.service = service
        self.run_id = run_id or self.id
        self.upstream_proxy = str(upstream_proxy or "").strip()
        self.meta = dict(meta or {})
        self.proxy = MeteredProxy(self.upstream_proxy)
        self.started_at = _now()
        self.finished_at = ""
        self.status = "starting"
        self.local_url = ""
        self.error = ""

    def start(self) -> str:
        self.local_url = self.proxy.start()
        self.status = "running"
        self._persist_active()
        with _lock:
            _active[self.id] = self
        return self.local_url

    def snapshot(self) -> dict[str, Any]:
        m = self.proxy.snapshot()
        total = int(m.get("bytes_total") or 0)
        return {
            "id": self.id,
            "service": self.service,
            "run_id": self.run_id,
            "status": self.status,
            "upstream": redact_proxy(self.upstream_proxy),
            "local_url": self.local_url or m.get("local_url") or "",
            "bytes_sent": int(m.get("bytes_sent") or 0),
            "bytes_recv": int(m.get("bytes_recv") or 0),
            "bytes_total": total,
            "bytes_total_h": _fmt_bytes(total),
            "connections": int(m.get("connections") or 0),
            "active_connections": int(m.get("active_connections") or 0),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error or m.get("last_error") or "",
            "meta": self.meta,
        }

    def stop(self, *, status: str = "done") -> dict[str, Any]:
        try:
            self.proxy.stop()
        except Exception as exc:
            self.error = str(exc)[:240]
        self.status = status
        self.finished_at = _now()
        snap = self.snapshot()
        self._append_history(snap)
        self._clear_active()
        with _lock:
            _active.pop(self.id, None)
        return snap

    def _persist_active(self) -> None:
        _ensure_dirs()
        try:
            (ACTIVE_DIR / f"{self.id}.json").write_text(
                json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def _clear_active(self) -> None:
        try:
            p = ACTIVE_DIR / f"{self.id}.json"
            if p.is_file():
                p.unlink()
        except Exception:
            pass

    def _append_history(self, snap: dict[str, Any]) -> None:
        _ensure_dirs()
        try:
            with SESSIONS_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(snap, ensure_ascii=False) + "\n")
        except Exception:
            pass


def start_meter_for_proxy(
    upstream_proxy: str,
    *,
    service: str,
    run_id: str = "",
    meta: Optional[dict[str, Any]] = None,
) -> tuple[str, MeterSession]:
    """Start metering for an upstream proxy URL. Returns (local_proxy_url, session)."""
    upstream = str(upstream_proxy or "").strip()
    if not upstream:
        raise ValueError("启用流量统计需要先填写上游代理")
    session = MeterSession(service=service, run_id=run_id, upstream_proxy=upstream, meta=meta)
    local = session.start()
    return local, session


def stop_meter(session: Optional[MeterSession], *, status: str = "done") -> Optional[dict[str, Any]]:
    if session is None:
        return None
    return session.stop(status=status)


def public_session(session: Optional[MeterSession]) -> Optional[dict[str, Any]]:
    if session is None:
        return None
    return session.snapshot()


def load_sessions(*, service: str = "", tail: int = 50) -> list[dict[str, Any]]:
    _ensure_dirs()
    items: list[dict[str, Any]] = []
    # active first
    with _lock:
        actives = [s.snapshot() for s in _active.values()]
    for p in sorted(ACTIVE_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # prefer live object if present
                sid = str(data.get("id") or "")
                live = next((a for a in actives if a.get("id") == sid), None)
                items.append(live or data)
        except Exception:
            continue
    if SESSIONS_FILE.is_file():
        try:
            lines = SESSIONS_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                if isinstance(data, dict):
                    items.append(data)
        except Exception:
            pass
    if service:
        items = [x for x in items if str(x.get("service") or "") == service]
    # de-dup by id keeping first (active/live preferred)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        sid = str(item.get("id") or "")
        if sid and sid in seen:
            continue
        if sid:
            seen.add(sid)
        out.append(item)
        if len(out) >= max(1, min(int(tail or 50), 500)):
            break
    return out
