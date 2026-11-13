"""Original M04 G1 15m/1h evidence inventory CLI, strictly read-only.

Reads only EXISTING original M03 research database and, optionally, an
operator-selected EXISTING PublicTradeStreamJournal. No network, no private
Indodax API, no issuer key, model, service action, Telegram send or order.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time
from zoneinfo import ZoneInfo

from indodax_ai_bot.m03_ws3_evidence_archive_nonlive import (
    DEFAULT_EVIDENCE_DB, RETENTION_SECONDS, _private_owned_regular,
)
from indodax_ai_bot.m04_g1_15m_1h_source_inventory_nonlive import (
    inventory_g1_windows_nonlive,
)

WIB = ZoneInfo("Asia/Jakarta")
DEFAULT_PAIRS = (
    "tmg_idr", "ankr_idr", "anoa_idr",
    "nbtnew_idr", "cst_idr", "onl_idr",
)


def _error(reason: str) -> dict:
    return {
        "status": "M04_G1_15M_1H_SOURCE_INVENTORY_BLOCKED_READONLY",
        "reason": reason,
        "g1_independent_issuer_verified": False,
        "g1_coverage_manifest_issuance_allowed": False,
        "provider_http_calls": 0,
        "telegram_messages_sent": 0,
        "additional_public_ws_connections": 0,
        "orders_sent": 0,
        "execution_allowed": False,
        "service_restarts": 0,
        "final_decision": "REJECT",
    }


def _private_archive_ok(path: Path) -> bool:
    try:
        parent = path.parent.lstat()
        return (
            parent.st_uid == os.geteuid()
            and stat.S_ISDIR(parent.st_mode)
            and stat.S_IMODE(parent.st_mode) == 0o700
            and _private_owned_regular(path)
        )
    except OSError:
        return False


def _optional_existing_journal_ok(path: Path) -> bool:
    try:
        if not path.is_absolute():
            return False
        fs = path.lstat()
        return (
            fs.st_uid == os.geteuid()
            and stat.S_ISREG(fs.st_mode)
            and fs.st_nlink == 1
            and not (stat.S_IMODE(fs.st_mode) & 0o022)
            and fs.st_size <= 2_000_000_000
        )
    except OSError:
        return False


def inspect_original_g1_window_source_inventory_readonly(
    *, pairs: list[str], end_epoch: int,
    research_path: Path = DEFAULT_EVIDENCE_DB,
    optional_journal_path: Path | None = None,
    now_epoch: int | None = None,
) -> dict:
    """All I/O is SQLite mode=ro, snapshot BEGIN and sanitized outputs."""
    try:
        now = int(time.time()) if now_epoch is None else now_epoch
        if (type(now) is not int or now <= 0
                or type(end_epoch) is not int
                or end_epoch <= 3600 or end_epoch % 60 != 0
                or end_epoch > now // 60 * 60
                or end_epoch < now - RETENTION_SECONDS):
            return _error("WINDOW_FUTURE_OR_OUTSIDE_RETENTION")
        private = Path(research_path)
        if not _private_archive_ok(private):
            return _error("ORIGINAL_MICRO_ARCHIVE_NOT_PRIVATE_OR_UNAVAILABLE")
        optional = (Path(optional_journal_path)
                    if optional_journal_path is not None else None)
        if optional is not None and not _optional_existing_journal_ok(optional):
            return _error("OPTIONAL_EXISTING_JOURNAL_UNTRUSTED")
        with ExitStack() as stack:
            micro = stack.enter_context(closing(sqlite3.connect(
                private.as_uri() + "?mode=ro", uri=True, timeout=5
            )))
            micro.execute("PRAGMA query_only=ON")
            micro.execute("BEGIN")
            journal = None
            if optional is not None:
                journal = stack.enter_context(closing(sqlite3.connect(
                    optional.as_uri() + "?mode=ro", uri=True, timeout=5
                )))
                journal.execute("PRAGMA query_only=ON")
                journal.execute("BEGIN")
            report = inventory_g1_windows_nonlive(
                micro_db=micro,
                journal_db=journal,
                end_epoch=end_epoch,
                pairs=pairs,
            )
        return report
    except (OSError, ValueError, sqlite3.Error, OverflowError, TypeError):
        return _error("READONLY_SOURCE_SCHEMA_OR_QUERY_INVALID")


def _parse_wib_end(raw: str | None, now: int) -> int:
    if raw is None:
        return now // 60 * 60
    parsed = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S")
    return int(parsed.replace(tzinfo=WIB).timestamp())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Original M03 G1 trade-tape feasibility, NOT a G1 issuer"
    )
    ap.add_argument("--end-wib", default=None)
    ap.add_argument("--pairs", nargs="+", default=list(DEFAULT_PAIRS))
    ap.add_argument("--journal-db", default=None)
    args = ap.parse_args(argv)
    try:
        now = int(time.time())
        end = _parse_wib_end(args.end_wib, now)
    except (ValueError, TypeError, OverflowError):
        result = _error("WINDOW_WIB_FORMAT_INVALID")
    else:
        result = inspect_original_g1_window_source_inventory_readonly(
            pairs=args.pairs,
            end_epoch=end,
            optional_journal_path=(
                Path(args.journal_db) if args.journal_db is not None else None
            ),
            now_epoch=now,
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return (2 if result["status"] ==
            "M04_G1_15M_1H_SOURCE_INVENTORY_BLOCKED_READONLY" else 0)


if __name__ == "__main__":
    sys.exit(main())
