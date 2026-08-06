#!/usr/bin/env python3
"""Import existing active Grok2API SSO sessions into CLIProxyAPI OAuth files.

The importer is intentionally sequential and checkpointed. It never changes the
source SQLite database and only creates or updates auth files after a successful
OAuth device-flow exchange.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from integrations.grok_oauth import GrokOAuthError, RateLimitedError, sso_to_token, write_cliproxy_file


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def token_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:20]


def read_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_proxies(config: dict[str, Any]) -> list[str]:
    values = [config.get("PROXY_POOL_URLS", ""), config.get("BROWSER_PROXY", "")]
    seen: set[str] = set()
    proxies: list[str] = []
    for value in values:
        for item in str(value or "").replace("\n", ",").split(","):
            proxy = item.strip()
            parsed = urlsplit(proxy)
            if parsed.scheme not in {"http", "https", "socks4", "socks5", "socks5h"} or not parsed.hostname:
                continue
            if proxy not in seen:
                seen.add(proxy)
                proxies.append(proxy)
    return proxies


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"created_at": utc_now(), "items": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"checkpoint is not an object: {path}")
    payload.setdefault("items", {})
    return payload


def save_checkpoint(path: Path, checkpoint: dict[str, Any]) -> None:
    checkpoint["updated_at"] = utc_now()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def active_source_tokens(database: Path) -> list[str]:
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT token FROM accounts WHERE deleted_at IS NULL AND status = 'active' ORDER BY created_at, token"
        ).fetchall()
    finally:
        connection.close()
    return [str(row[0]).strip() for row in rows if str(row[0]).strip()]


def log(message: str) -> None:
    print(f"[{utc_now()}] {message}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("/opt/grok2api/data/accounts.db"))
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.json")
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=PROJECT_ROOT / "data/imports/grok2api-to-cpa-checkpoint.json",
    )
    parser.add_argument("--limit", type=int, default=0, help="Process at most this many pending accounts")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--pause", type=float, default=25.0, help="Pause between accounts in seconds")
    parser.add_argument(
        "--failure-cooldown",
        type=float,
        default=90.0,
        help="Additional cooldown after an upstream failure in seconds",
    )
    args = parser.parse_args()

    config = read_config(args.config)
    auth_dir = Path(str(config["CPA_AUTH_DIR"])).expanduser()
    if not auth_dir.is_dir():
        raise SystemExit(f"CPA auth directory does not exist: {auth_dir}")
    checkpoint_path = args.checkpoint
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = load_checkpoint(checkpoint_path)
    proxies = parse_proxies(config)
    source_tokens = active_source_tokens(args.database)
    log(f"source active accounts={len(source_tokens)}, CPA auth directory={auth_dir}, proxies={len(proxies)}")

    processed = successful = failed = skipped = 0
    for source_index, source_token in enumerate(source_tokens):
        source_id = token_id(source_token)
        previous = checkpoint["items"].get(source_id, {})
        status = previous.get("status")
        if status == "success" or (status == "failed" and not args.retry_failed):
            skipped += 1
            continue
        if args.limit and processed >= args.limit:
            break
        processed += 1
        proxy = proxies[(processed - 1) % len(proxies)] if proxies else ""
        log(f"{processed}: converting source={source_id} ({source_index + 1}/{len(source_tokens)})")
        try:
            oauth_token = sso_to_token(source_token, proxy=proxy, log=lambda detail: log(f"{source_id}: {detail}"))
            if not oauth_token:
                raise GrokOAuthError("device flow returned no OAuth token")
            output_path = write_cliproxy_file(auth_dir, oauth_token, email=str(oauth_token.get("email") or ""))
            checkpoint["items"][source_id] = {
                "status": "success",
                "completed_at": utc_now(),
                "auth_file": output_path.name,
                "email_present": bool(oauth_token.get("email") or oauth_token.get("_email")),
            }
            successful += 1
            log(f"{source_id}: wrote {output_path.name}")
        except (GrokOAuthError, RateLimitedError, OSError, ValueError) as error:
            checkpoint["items"][source_id] = {
                "status": "failed",
                "updated_at": utc_now(),
                "error": str(error)[:500],
                "attempts": int(previous.get("attempts", 0)) + 1,
            }
            failed += 1
            log(f"{source_id}: failed: {error}")
        except Exception as error:
            checkpoint["items"][source_id] = {
                "status": "failed",
                "updated_at": utc_now(),
                "error": f"unexpected {type(error).__name__}: {error}"[:500],
                "attempts": int(previous.get("attempts", 0)) + 1,
            }
            failed += 1
            log(f"{source_id}: unexpected failure: {type(error).__name__}")
        save_checkpoint(checkpoint_path, checkpoint)
        delay = args.pause + (args.failure_cooldown if source_id in checkpoint["items"] and checkpoint["items"][source_id].get("status") == "failed" else 0)
        if delay > 0 and processed < len(source_tokens):
            time.sleep(delay)

    log(f"done processed={processed} success={successful} failed={failed} skipped={skipped}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
