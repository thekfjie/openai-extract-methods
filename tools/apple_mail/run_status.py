"""Shared live status/log writer for Apple Mail controlled runs."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('/opt/automyai')
DATA = ROOT / 'data' / 'apple_mail'
STATUS_PATH = DATA / 'status.json'
LIVE_LOG_PATH = DATA / 'runs' / 'live.log'
_LOCK = threading.Lock()
_MAX_LOGS = 500


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def _read_status() -> dict[str, Any]:
    if not STATUS_PATH.exists():
        return {
            'ok': True,
            'running': False,
            'currentStep': 'idle',
            'currentStepLabel': '空闲',
            'logs': [],
            'updatedAt': _now(),
        }
    try:
        return json.loads(STATUS_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {
            'ok': True,
            'running': False,
            'currentStep': 'idle',
            'currentStepLabel': '空闲',
            'logs': [],
            'updatedAt': _now(),
        }


def _write_status(data: dict[str, Any]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / 'runs').mkdir(parents=True, exist_ok=True)
    tmp = STATUS_PATH.with_suffix('.json.tmp')
    text = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    # access-config style: some dirs may not allow sibling temp create; fall back direct
    try:
        tmp.write_text(text, encoding='utf-8')
        tmp.replace(STATUS_PATH)
    except Exception:
        STATUS_PATH.write_text(text, encoding='utf-8')


def set_status(**fields: Any) -> dict[str, Any]:
    with _LOCK:
        st = _read_status()
        st.update(fields)
        st['updatedAt'] = _now()
        logs = st.get('logs')
        if not isinstance(logs, list):
            logs = []
            st['logs'] = logs
        _write_status(st)
        return st


def log_step(step: str, message: str, level: str = 'INFO', **extra: Any) -> dict[str, Any]:
    """Record a step for panel live view."""
    entry = {
        'time': _now(),
        'ts': time.time(),
        'step': step,
        'level': level,
        'message': message,
    }
    if extra:
        entry['extra'] = extra
    line = f"[{entry['time']}] [{level}] [{step}] {message}"
    with _LOCK:
        st = _read_status()
        logs = st.get('logs')
        if not isinstance(logs, list):
            logs = []
        logs.append(entry)
        if len(logs) > _MAX_LOGS:
            logs = logs[-_MAX_LOGS:]
        st['logs'] = logs
        st['currentStep'] = step
        st['currentStepLabel'] = message
        st['lastLevel'] = level
        st['updatedAt'] = _now()
        if extra:
            # keep latest useful fields on top-level for quick panel cards
            for k in ('email', 'impersonate', 'proxy', 'proxyIdentity', 'sourceEmail', 'deviceId', 'accountId'):
                if k in extra:
                    st[k] = extra[k]
        _write_status(st)
        try:
            with LIVE_LOG_PATH.open('a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            pass
        return st


def start_run(meta: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = meta or {}
    with _LOCK:
        st = {
            'ok': True,
            'running': True,
            'startedAt': _now(),
            'updatedAt': _now(),
            'finishedAt': '',
            'currentStep': 'start',
            'currentStepLabel': '开始任务',
            'lastLevel': 'INFO',
            'logs': [{
                'time': _now(),
                'ts': time.time(),
                'step': 'start',
                'level': 'INFO',
                'message': '开始任务',
                'extra': meta,
            }],
        }
        st.update(meta)
        _write_status(st)
        try:
            (DATA / 'runs').mkdir(parents=True, exist_ok=True)
            with LIVE_LOG_PATH.open('a', encoding='utf-8') as f:
                f.write(f"\n===== RUN START {_now()} =====\n")
        except Exception:
            pass
        return st


def finish_run(ok: bool, message: str = '', **extra: Any) -> dict[str, Any]:
    level = 'OK' if ok else 'ERROR'
    step = 'done' if ok else 'failed'
    log_step(step, message or ('完成' if ok else '失败'), level=level, **extra)
    with _LOCK:
        st = _read_status()
        st['running'] = False
        st['ok'] = ok
        st['finishedAt'] = _now()
        st['updatedAt'] = _now()
        if extra:
            st.update(extra)
        _write_status(st)
        return st


def get_status() -> dict[str, Any]:
    with _LOCK:
        return _read_status()


def get_logs(tail: int = 200) -> list[dict[str, Any]]:
    with _LOCK:
        st = _read_status()
        logs = st.get('logs') if isinstance(st.get('logs'), list) else []
        n = max(1, min(int(tail or 200), 1000))
        return list(logs[-n:])
