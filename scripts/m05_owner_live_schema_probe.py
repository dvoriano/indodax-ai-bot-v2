"""Read-only current V2 owner SQLite schema and market advisory DB ACK.

Compares the ACTIVE mode=ro SQLite schema with original V2 M05 expectations.
Does not initialize OwnerDecisionLedger, migrate, send Telegram, touch orders,
open a token, or change an active service. No callback payloads are printed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

class OwnerSchemaProbeError(ValueError):
    """Fixed diagnostic code; never print a path, token or SQL payload."""


_PREVIEW_COLUMNS = frozenset({
    "preview_id", "owner_user_id", "client_order_id",
    "symbol", "side", "price_idr", "quantity_base",
    "send_state", "owner_choice", "execution_allowed",
})
_ENV = Path.home() / ".config/indodax-ai-bot-v2/m05/service.env"


def _private_regular(path: Path, max_bytes: int) -> None:
    try:
        st = path.lstat()
        parent = path.parent.lstat()
        if (not path.is_absolute() or not stat.S_ISREG(st.st_mode)
                or st.st_uid != os.geteuid() or st.st_nlink != 1
                or stat.S_IMODE(st.st_mode) != 0o600
                or not 0 < st.st_size <= max_bytes
                or not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != os.geteuid()
                or stat.S_IMODE(parent.st_mode) != 0o700):
            raise OwnerSchemaProbeError("M05_PROBE_UNTRUSTED_PRIVATE_FILE")
    except OSError:
        raise OwnerSchemaProbeError("M05_PROBE_PRIVATE_FILE_UNAVAILABLE") from None


def _database_path_from_env(path: Path, home: Path) -> Path:
    _private_regular(path, 8192)
    try:
        raw = path.read_text("utf-8")
        found = []
        for line in raw.splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key == "M05_TELEGRAM_OWNER_DB":
                found.append(value.strip().strip('"').strip("'"))
        if len(found) != 1:
            raise OwnerSchemaProbeError("M05_PROBE_OWNER_DB_CONFIG_UNVERIFIED")
        db = Path(found[0])
        if not db.is_absolute() or not db.is_relative_to(home) or db.is_symlink():
            raise OwnerSchemaProbeError("M05_PROBE_DB_PATH_UNTRUSTED")
        _private_regular(db, 100_000_000)
        return db
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, OwnerSchemaProbeError):
            raise
        raise OwnerSchemaProbeError("M05_PROBE_DB_PATH_UNTRUSTED") from None


def inspect_original_owner_schema(db: Path) -> dict:
    _private_regular(db, 100_000_000)
    # mode=ro opens the latest live database snapshot (unlike immutable=1).
    # query_only and a single read transaction forbid schema/row writes.
    try:
        with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=3) as con:
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            owner = con.execute(
                "SELECT mode FROM m05_config WHERE singleton=1"
            ).fetchone()
            if owner != ("MANUAL",):
                raise OwnerSchemaProbeError("M05_PROBE_OWNER_MODE_NOT_MANUAL")
            present = con.execute(
                "SELECT type FROM sqlite_master "
                "WHERE name='m05_manual_trade_previews'"
            ).fetchone()
            columns = {row[1] for row in con.execute(
                "PRAGMA table_info(m05_manual_trade_previews)"
            )} if present == ("table",) else set()
            preview = (
                "ABSENT" if present is None else
                "PRESENT_REQUIRED_COLUMNS" if _PREVIEW_COLUMNS <= columns else
                "UNEXPECTED_OR_PARTIAL_SCHEMA"
            )
            count = con.execute(
                "SELECT COUNT(*) FROM m05_market_advisory_outbox "
                "WHERE delivery_state='SENT' AND pair!='reviewtest_idr' "
                "AND sent_message_id IS NOT NULL AND sent_message_id>0"
            ).fetchone()[0]
            queued = con.execute(
                "SELECT COUNT(*) FROM m05_market_advisory_outbox "
                "WHERE delivery_state='QUEUED'"
            ).fetchone()[0]
            checks = con.execute("PRAGMA quick_check(1)").fetchone()
            integrity = checks == ("ok",)
            if not integrity:
                raise OwnerSchemaProbeError("M05_PROBE_DB_QUICK_CHECK_FAILED")
            return {
                "status": "M05_OWNER_SCHEMA_READONLY_OBSERVED",
                "owner_mode": "MANUAL",
                "manual_preview_table": preview,
                "preview_missing_required_columns": sorted(_PREVIEW_COLUMNS - columns),
                "real_market_db_ack_non_test_count": int(count),
                "queued_advisories": int(queued),
                "quick_check": "ok",
                "database_contents_modified": False,
                "telegram_network_used": False,
                "orders_or_trades_sent": False,
            }
    except OwnerSchemaProbeError:
        raise
    except (OSError, sqlite3.Error, TypeError):
        raise OwnerSchemaProbeError("M05_PROBE_READONLY_DB_UNAVAILABLE") from None


def main() -> int:
    try:
        db = _database_path_from_env(_ENV, Path.home())
        result = inspect_original_owner_schema(db)
        print(json.dumps(result, sort_keys=True))
        return 0
    except OwnerSchemaProbeError as exc:
        print(json.dumps({"status": "DENIED", "reason": str(exc)},
                         sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
