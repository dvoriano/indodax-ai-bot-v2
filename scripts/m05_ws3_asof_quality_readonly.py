"""Owner original M05 ACK → private M03 WS3 source/micro audit (READ ONLY).

Runs only against the ONE original V2 owner service and private SQLite.
It does not modify systemd, send Telegram, invoke paid AI, or place orders.
Do not print process environment, credentials, owner ID, private paths or
Telegram message IDs.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import time

from indodax_ai_bot.m03_ws3_evidence_archive_nonlive import (
    DEFAULT_EVIDENCE_DB,
    RETENTION_SECONDS,
)
from indodax_ai_bot.m05_radar_momentum_nonlive import _owned_private_regular
from indodax_ai_bot.m05_ws3_asof_quality_nonlive import (
    WIB, audit_ws3_ack_cohort_nonlive,
)
from scripts.m05_strategy_a_four_ack_followup_readonly import _unit_pid
from scripts.m05_strategy_a_priority_receipts_readonly import _process_env

DEFAULT_START_WIB = "2026-11-12T15:18:00"
DEFAULT_END_WIB = "2026-11-12T16:05:05"


def _secure_research_archive(path: Path) -> bool:
    try:
        parent = path.parent.lstat()
        return (
            stat.S_ISDIR(parent.st_mode)
            and parent.st_uid == os.geteuid()
            and stat.S_IMODE(parent.st_mode) == 0o700
            and _owned_private_regular(path)
        )
    except OSError:
        return False


def inspect_original_m05_ws3_asof_quality_readonly(
    *, start_epoch: int, end_epoch: int,
) -> dict:
    try:
        now = int(time.time())
        if (type(start_epoch) is not int or type(end_epoch) is not int
                or start_epoch <= 0 or start_epoch > end_epoch
                or end_epoch - start_epoch > 24*3600
                or start_epoch < now - RETENTION_SECONDS
                or end_epoch > now):
            raise ValueError("RESEARCH_WINDOW_OUT_OF_RETENTION_OR_INVALID")
        pid = _unit_pid()
        process = _process_env(pid)  # original live NO/NO/DRY_RUN, priority opt-in
        owner = Path(process["owner_db"])
        research = DEFAULT_EVIDENCE_DB
        if (not _owned_private_regular(owner)
                or not _secure_research_archive(research)):
            raise ValueError("PRIVATE_SOURCE_NOT_TRUSTED")
        with sqlite3.connect(
            owner.as_uri() + "?mode=ro", uri=True, timeout=5
        ) as owner_db, sqlite3.connect(
            research.as_uri() + "?mode=ro", uri=True, timeout=5
        ) as research_db:
            owner_db.execute("PRAGMA query_only=ON")
            research_db.execute("PRAGMA query_only=ON")
            output = audit_ws3_ack_cohort_nonlive(
                owner_db, research_db,
                start_epoch=start_epoch, end_epoch=end_epoch,
            )
        if _unit_pid() != pid:
            raise ValueError("ORIGINAL_M05_PID_CHANGED_DURING_AUDIT")
        return {
            **output,
            "original_m05_pid": pid,
            "original_m05_process_unchanged": True,
            "original_read_only_owner_guard_verified": True,
        }
    except (OSError, ValueError, sqlite3.Error,
            subprocess.SubprocessError, OverflowError):
        return {
            "status": "M05_WS3_ASOF_QUALITY_AUDIT_BLOCKED_READONLY",
            "reason": "ORIGINAL_OWNER_PROCESS_OR_RESEARCH_EVIDENCE_NOT_VERIFIED",
            "execution_allowed": False,
            "final_decision": "REJECT",
            "orders_sent": 0,
            "telegram_messages_sent_by_audit": 0,
            "paid_ai_calls_by_audit": 0,
            "service_restarted": False,
        }


def _parse_wib(value: str) -> int:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
        return int(parsed.replace(tzinfo=WIB).timestamp())
    except (TypeError, ValueError, OverflowError):
        raise ValueError("WINDOW_DATE_FORMAT_INVALID") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Research-only original owner ACK → WS3 source/micro timeline"
    )
    parser.add_argument("--start-wib", default=DEFAULT_START_WIB)
    parser.add_argument("--end-wib", default=DEFAULT_END_WIB)
    args = parser.parse_args(argv)
    try:
        start = _parse_wib(args.start_wib)
        end = _parse_wib(args.end_wib)
    except ValueError:
        print(json.dumps({
            "status": "M05_WS3_ASOF_QUALITY_AUDIT_BLOCKED_READONLY",
            "reason": "WINDOW_DATE_FORMAT_INVALID",
            "execution_allowed": False,
            "orders_sent": 0,
        }, sort_keys=True))
        return 2
    result = inspect_original_m05_ws3_asof_quality_readonly(
        start_epoch=start, end_epoch=end,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 2 if result["status"] == (
        "M05_WS3_ASOF_QUALITY_AUDIT_BLOCKED_READONLY"
    ) else 0


if __name__ == "__main__":
    sys.exit(main())
