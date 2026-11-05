"""Restore ONE missing M05 manual-preview table in the ORIGINAL V2 owner DB.

No reset or bulk migration: obtain exact table DDL from a fresh isolated
original OwnerDecisionLedger, preserve a SQLite online-backup snapshot, then
apply only that missing CREATE TABLE in an atomic SQLite transaction.

The original dedicated poller lock is shared. No Telegram network, token,
systemd modifications, Indodax private API, BUY/SELL or paid AI is invoked.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile

from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_dedicated_inbound import (
    dedicated_poller_lock,
)
from scripts.m05_owner_live_schema_probe import (
    _database_path_from_env,
    _private_regular,
)

CONFIG = Path.home() / ".config/indodax-ai-bot-v2/m05/service.env"
_TABLE = "m05_manual_trade_previews"
_REQUIRED = frozenset({
    "preview_id", "owner_user_id", "account_fingerprint",
    "client_order_id", "symbol", "side", "price_idr",
    "quantity_base", "draft_sha256", "created_epoch",
    "expires_epoch", "token_hash", "message_text",
    "confirm_callback", "reject_callback", "send_state",
    "sent_message_id", "owner_choice", "decided_epoch",
    "execution_allowed",
})


class PreviewRepairDenied(ValueError):
    """Never leak values from the private owner configuration or SQLite."""


def _verified_owner_id(env: Path) -> int:
    _private_regular(env, 8192)
    try:
        ids = []
        for line in env.read_text("utf-8").splitlines():
            key, sep, val = line.partition("=")
            if sep and key.strip() == "M05_TELEGRAM_OWNER_ID":
                ids.append(val.strip().strip('"').strip("'"))
        if (len(ids) != 1 or not ids[0].isascii()
                or not ids[0].isdecimal()
                or not 0 < int(ids[0]) < 10**19):
            raise PreviewRepairDenied("M05_PREVIEW_OWNER_CONFIG_INVALID")
        return int(ids[0])
    except (OSError, UnicodeError):
        raise PreviewRepairDenied("M05_PREVIEW_OWNER_CONFIG_INVALID") from None


def _expected_original_schema(parent: Path) -> str:
    # Derive DDL from the CURRENT original V2 owner ledger itself, not a
    # separate hand-copied CREATE TABLE (which can drift across versions).
    with tempfile.TemporaryDirectory(prefix=".m05-schema-source-", dir=parent) as d:
        fixture = Path(d) / "isolated.sqlite3"
        OwnerDecisionLedger(fixture, owner_user_id=1)
        with sqlite3.connect(fixture.as_uri() + "?mode=ro", uri=True) as conn:
            raw = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (_TABLE,),
            ).fetchone()
            fields = {row[1] for row in conn.execute(
                "PRAGMA table_info(m05_manual_trade_previews)"
            )}
    if (raw is None or not isinstance(raw[0], str)
            or not raw[0].lstrip().upper().startswith(
                "CREATE TABLE " + _TABLE.upper()
            ) or not _REQUIRED <= fields):
        raise PreviewRepairDenied("M05_PREVIEW_ORIGINAL_SOURCE_SCHEMA_INVALID")
    return raw[0]


def _read_identity(conn: sqlite3.Connection, owner_id: int) -> None:
    row = conn.execute(
        "SELECT owner_user_id,mode FROM m05_config WHERE singleton=1"
    ).fetchone()
    if row != (owner_id, "MANUAL"):
        raise PreviewRepairDenied("M05_PREVIEW_OWNER_MODE_OR_ID_MISMATCH")
    required_tables = {
        "m05_proposals", "m05_market_advisory_outbox",
        "m05_config", "m05_telegram_cursor", "m05_audit",
    }
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if not required_tables <= tables:
        raise PreviewRepairDenied("M05_PREVIEW_ORIGINAL_LEDGER_TABLES_MISSING")


def _preview_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute(
        "PRAGMA table_info(m05_manual_trade_previews)"
    )}


def _market_status(conn: sqlite3.Connection) -> tuple[int, int, int]:
    values = conn.execute(
        "SELECT "
        "SUM(CASE WHEN delivery_state='SENT' AND pair!='reviewtest_idr' "
        " AND sent_message_id>0 THEN 1 ELSE 0 END),"
        "SUM(CASE WHEN delivery_state='QUEUED' THEN 1 ELSE 0 END),"
        "COUNT(*) FROM m05_market_advisory_outbox"
    ).fetchone()
    return tuple(int(n or 0) for n in values)


def _backup_online(db: Path, owner_id: int) -> Path:
    fd, filename = tempfile.mkstemp(
        prefix=".m05-before-preview-repair-", suffix=".sqlite3",
        dir=db.parent,
    )
    os.close(fd)
    backup = Path(filename)
    try:
        with closing(sqlite3.connect(
            db.as_uri() + "?mode=ro", uri=True, timeout=10,
        )) as src, closing(sqlite3.connect(backup, timeout=10)) as dest:
            src.execute("PRAGMA query_only=ON")
            _read_identity(src, owner_id)
            src.backup(dest, pages=128, sleep=0.1)
            _read_identity(dest, owner_id)
            if dest.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
                raise PreviewRepairDenied("M05_PREVIEW_BACKUP_INTEGRITY_FAILED")
        os.chmod(backup, 0o600)
        with backup.open("rb") as f:
            os.fsync(f.fileno())
        return backup
    except BaseException:
        backup.unlink(missing_ok=True)
        raise


def repair_preview_table(*, db: Path, owner_id: int) -> dict:
    _private_regular(db, 100_000_000)
    if (not db.is_relative_to(Path.home())
            or "indodax-ai-bot-v2" not in db.parts
            or type(owner_id) is not int or owner_id <= 0):
        raise PreviewRepairDenied("M05_PREVIEW_ONLY_ORIGINAL_V2")
    sql = _expected_original_schema(db.parent)

    # The SAME lock used by the existing original M05 owner poller; do not
    # stop/restart it or introduce a competing Telegram recipient.
    with dedicated_poller_lock(str(db) + ".dedicated-poller.lock"):
        with closing(sqlite3.connect(
            db.as_uri() + "?mode=ro", uri=True, timeout=10,
        )) as readonly:
            readonly.execute("PRAGMA query_only=ON")
            _read_identity(readonly, owner_id)
            if readonly.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
                raise PreviewRepairDenied("M05_PREVIEW_ORIGINAL_DB_INTEGRITY_FAILED")
            present = readonly.execute(
                "SELECT type FROM sqlite_master WHERE name=?", (_TABLE,)
            ).fetchone()
            if present is not None:
                if present == ("table",) and _REQUIRED <= _preview_columns(readonly):
                    return {
                        "status": "M05_PREVIEW_SCHEMA_ALREADY_COMPLETE",
                        "database_changed": False,
                        "backup_created": False,
                        "telegram_network_used": False,
                        "trading_enabled": False,
                    }
                raise PreviewRepairDenied("M05_PREVIEW_PARTIAL_TABLE_REVIEW_REQUIRED")

        backup = _backup_online(db, owner_id)
        with closing(sqlite3.connect(db, timeout=10, isolation_level=None)) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                _read_identity(conn, owner_id)
                before = _market_status(conn)
                existing = conn.execute(
                    "SELECT type FROM sqlite_master WHERE name=?", (_TABLE,)
                ).fetchone()
                if existing is not None:
                    raise PreviewRepairDenied("M05_PREVIEW_CONCURRENT_SCHEMA_CHANGE")
                conn.execute(sql)
                after = _market_status(conn)
                if (not _REQUIRED <= _preview_columns(conn)
                        or before != after
                        or conn.execute("PRAGMA quick_check(1)").fetchone() != ("ok",)):
                    raise PreviewRepairDenied("M05_PREVIEW_REPAIR_POSTCHECK_FAILED")
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        with closing(sqlite3.connect(
            db.as_uri() + "?mode=ro", uri=True, timeout=10,
        )) as verify:
            verify.execute("PRAGMA query_only=ON")
            _read_identity(verify, owner_id)
            if (_market_status(verify) != after
                    or not _REQUIRED <= _preview_columns(verify)
                    or verify.execute("PRAGMA quick_check(1)").fetchone() != ("ok",)):
                raise PreviewRepairDenied("M05_PREVIEW_POSTCOMMIT_REVIEW_REQUIRED")
        return {
            "status": "M05_PREVIEW_SCHEMA_ORIGINAL_V2_RESTORED",
            "database_changed": True,
            "backup_created": True,
            "backup_file_name": backup.name,
            "real_market_acks_unchanged_during_transaction": before[0],
            "queued_outbox_rows_unchanged_during_transaction": before[1],
            "all_existing_outbox_rows_unchanged_during_transaction": before[2],
            "preview_table_has_original_required_columns": True,
            "quick_check": "ok",
            "owner_mode": "MANUAL",
            "telegram_network_used": False,
            "m05_timer_modified": False,
            "m03_timer_modified": False,
            "trading_enabled": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Original V2 one-table M05 preview schema repair"
    )
    parser.add_argument("--owner-approve-original-v2-schema-repair",
                        action="store_true")
    args = parser.parse_args()
    if not args.owner_approve_original_v2_schema_repair:
        print(json.dumps({"ok": False,
                          "reason": "M05_PREVIEW_EXPLICIT_OWNER_APPROVAL_REQUIRED"}))
        return 2
    try:
        db = _database_path_from_env(CONFIG, Path.home())
        owner_id = _verified_owner_id(CONFIG)
        result = repair_preview_table(db=db, owner_id=owner_id)
        print(json.dumps(result, sort_keys=True))
        return 0
    except PreviewRepairDenied as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}, sort_keys=True))
        return 2
    except (OSError, sqlite3.Error, ValueError) as exc:
        code = "M05_PREVIEW_SCHEMA_BUSY_OR_UNAVAILABLE"
        print(json.dumps({"ok": False, "reason": code}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
