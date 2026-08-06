"""Pure helpers shared by server.py and the domain modules under integrations/.

Nothing here reads configuration, runtime state or the network, so these are safe
to import from anywhere without an import cycle.

Note: `now_iso` here returns local time with a UTC offset, which is what the
control API and the stored runtime files use. `integrations.common.now_iso`
returns a UTC `Z` timestamp and is used by the CPA and Grok integrations. They
are deliberately different; do not merge them without checking both consumers.
"""
from __future__ import annotations

import base64
import errno
import json
import random
import string
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(text, encoding="utf-8")
    try:
        temp_path.replace(path)
    except OSError as error:
        if error.errno not in {errno.EBUSY, errno.EXDEV}:
            raise
        path.write_text(text, encoding="utf-8")
        try:
            temp_path.unlink()
        except OSError:
            pass


def parse_bool_flag(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def normalize_fixed_price_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value or "").strip().lower()
    return "true" if text in {"1", "true", "yes", "on"} else "false" if text in {"0", "false", "no", "off"} else text


def parse_positive_int(value: Any, default: int = 1) -> int:
    try:
        parsed = int(str(value).strip())
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def parse_timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def timestamp_is_future(value: Any) -> bool:
    parsed = parse_timestamp(value)
    return bool(parsed and parsed > time.time())


def strip_empty_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: cleaned
            for key, item in value.items()
            if (cleaned := strip_empty_values(item)) not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [cleaned for item in value if (cleaned := strip_empty_values(item)) not in (None, "", [], {})]
    return value


def decode_jwt_payload(token: Any) -> dict[str, Any]:
    text = str(token or "").strip()
    parts = text.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def email_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text.split("@", 1)[0] if "@" in text else text


def generate_random_local_part(length: int = 10) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choices(alphabet, k=length))
