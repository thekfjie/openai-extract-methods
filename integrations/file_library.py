"""Small, local text-file library used by the administrator console.

Records are stored as individual JSON documents under ``data/file_library``.
The browser-visible filename is metadata only; storage paths always use a
generated identifier so filenames can never escape the library directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from integrations.core_utils import now_iso

ROOT = Path(__file__).resolve().parent.parent
FILE_LIBRARY_DIR = Path(os.environ.get("FILE_LIBRARY_DIR", ROOT / "data/file_library"))
FILE_LIBRARY_MAX_BYTES = 1024 * 1024
FILE_LIBRARY_MAX_REQUEST_BYTES = FILE_LIBRARY_MAX_BYTES * 2
FILE_LIBRARY_ALLOWED_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".xml",
        ".html",
        ".css",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".py",
        ".sh",
        ".ini",
        ".conf",
        ".log",
        ".sql",
    }
)

_ITEM_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_LOCK = threading.RLock()


class FileLibraryError(ValueError):
    """Expected validation/not-found error with an HTTP-friendly status."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _library_dir(root: Path | str | None = None) -> Path:
    return Path(root) if root is not None else FILE_LIBRARY_DIR


def _records_dir(root: Path | str | None = None) -> Path:
    return _library_dir(root) / "items"


def _record_path(item_id: str, root: Path | str | None = None) -> Path:
    if not _ITEM_ID_RE.fullmatch(str(item_id or "")):
        raise FileLibraryError("文件 ID 无效", 404)
    return _records_dir(root) / f"{item_id}.json"


def _normalize_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise FileLibraryError("文件名不能为空")
    if len(name) > 180:
        raise FileLibraryError("文件名不能超过 180 个字符")
    if name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise FileLibraryError("文件名不能包含路径或空字符")
    if any(ord(character) < 32 for character in name):
        raise FileLibraryError("文件名不能包含控制字符")
    extension = Path(name).suffix.lower()
    if extension not in FILE_LIBRARY_ALLOWED_EXTENSIONS:
        supported = "、".join(sorted(FILE_LIBRARY_ALLOWED_EXTENSIONS))
        raise FileLibraryError(f"不支持的文本文件类型；可用扩展名：{supported}")
    return name


def _normalize_content(value: Any) -> tuple[str, bytes]:
    if not isinstance(value, str):
        raise FileLibraryError("文件内容必须是 UTF-8 文本")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise FileLibraryError("文件内容必须是有效的 UTF-8 文本") from error
    if len(encoded) > FILE_LIBRARY_MAX_BYTES:
        raise FileLibraryError("文本文件不能超过 1 MiB", 413)
    return value, encoded


def _metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record.get(key)
        for key in (
            "id",
            "name",
            "sizeBytes",
            "charCount",
            "lineCount",
            "sha256",
            "createdAt",
            "updatedAt",
        )
    }


def _build_record(
    item_id: str,
    name: str,
    content: str,
    encoded: bytes,
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    timestamp = now_iso()
    return {
        "version": 1,
        "id": item_id,
        "name": name,
        "content": content,
        "sizeBytes": len(encoded),
        "charCount": len(content),
        "lineCount": len(content.splitlines()),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "createdAt": created_at or timestamp,
        "updatedAt": timestamp,
    }


def _read_record(item_id: str, root: Path | str | None = None) -> dict[str, Any]:
    path = _record_path(item_id, root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise FileLibraryError("文件不存在", 404) from error
    except (OSError, json.JSONDecodeError) as error:
        raise FileLibraryError("文件记录无法读取", 500) from error
    if not isinstance(payload, dict) or payload.get("id") != item_id or not isinstance(payload.get("content"), str):
        raise FileLibraryError("文件记录格式损坏", 500)
    return payload


def _write_record(record: dict[str, Any], root: Path | str | None = None) -> None:
    path = _record_path(str(record.get("id") or ""), root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _assert_name_available(name: str, root: Path | str | None = None, exclude_id: str = "") -> None:
    folded = name.casefold()
    for item in list_files(root):
        if item.get("id") != exclude_id and str(item.get("name") or "").casefold() == folded:
            raise FileLibraryError("同名文件已存在", 409)


def list_files(root: Path | str | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with _LOCK:
        directory = _records_dir(root)
        if not directory.exists():
            return []
        for path in directory.glob("*.json"):
            item_id = path.stem
            if not _ITEM_ID_RE.fullmatch(item_id):
                continue
            try:
                records.append(_metadata(_read_record(item_id, root)))
            except FileLibraryError:
                continue
    return sorted(records, key=lambda item: str(item.get("updatedAt") or ""), reverse=True)


def get_file(item_id: str, root: Path | str | None = None) -> dict[str, Any]:
    with _LOCK:
        return dict(_read_record(item_id, root))


def create_file(name: Any, content: Any, root: Path | str | None = None) -> dict[str, Any]:
    normalized_name = _normalize_name(name)
    normalized_content, encoded = _normalize_content(content)
    with _LOCK:
        _assert_name_available(normalized_name, root)
        item_id = uuid.uuid4().hex
        record = _build_record(item_id, normalized_name, normalized_content, encoded)
        _write_record(record, root)
        return dict(record)


def update_file(
    item_id: str,
    *,
    name: Any | None = None,
    content: Any | None = None,
    root: Path | str | None = None,
) -> dict[str, Any]:
    with _LOCK:
        current = _read_record(item_id, root)
        normalized_name = _normalize_name(current["name"] if name is None else name)
        normalized_content, encoded = _normalize_content(current["content"] if content is None else content)
        _assert_name_available(normalized_name, root, exclude_id=item_id)
        record = _build_record(
            item_id,
            normalized_name,
            normalized_content,
            encoded,
            created_at=str(current.get("createdAt") or "") or None,
        )
        _write_record(record, root)
        return dict(record)


def delete_file(item_id: str, root: Path | str | None = None) -> dict[str, Any]:
    with _LOCK:
        record = _read_record(item_id, root)
        try:
            _record_path(item_id, root).unlink()
        except FileNotFoundError as error:
            raise FileLibraryError("文件不存在", 404) from error
        return _metadata(record)
