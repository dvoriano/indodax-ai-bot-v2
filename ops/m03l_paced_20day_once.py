"""Owner-invoked paced M03-L original V2 BTC/IDR 1m history to 20 days.

This is ONE foreground command, not another collector/timer/bot/database.
Existing 4x360m M03-L windows, original shared REST ledger/owner egress
admission and append-only Research DecisionJournal remain authoritative.
Never modify M03/M05/WS systemd, no M04 paid inference or exchange orders.
On a missing source window, quota race, stale GO or SSH interruption,
stop without inventing bars; rerun safely from the existing oldest epoch.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import time
from typing import Callable
from urllib.parse import quote

from indodax_ai_bot.m03_egress_admission import (
    M03EgressAdmissionError, M03SharedEgressAdmission,
)
from indodax_ai_bot.public_rest_budget import SharedPublicRestBudget

TARGET_1M_ROWS = 20 * 24 * 60
ROWS_PER_WINDOW = 360
MAX_WINDOWS_PER_BATCH = 4
GET_PER_WINDOW = 3
REQUIRED_HEADROOM = 24  # one 12-GET batch + margin for scanner
BETWEEN_BATCH_SECONDS = 65
QUOTA_RECHECK_SECONDS = 20
MAX_WAIT_CHECKS = 45
MAX_BATCHES = 20  # hard bounded, including resumed attempts
BATCH_TIMEOUT_SECONDS = 150
ARCHIVE = Path("runtime/m03l_public_ohlc.sqlite3")
LOCK = Path("runtime/m03l_public_ohlc.paced-backfill.lock")


class M03LPacedDenied(RuntimeError):
    """Stable owner-safe codes; no private paths or API responses."""


def _readonly_uri(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise M03LPacedDenied("M03L_ORIGINAL_DB_MISSING_OR_SYMLINK")
    return "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"


def existing_contiguous_rows(path: Path = ARCHIVE) -> int:
    """Prove entire BTC 1m span, not merely count fragments as 20 days."""
    try:
        with sqlite3.connect(_readonly_uri(path), uri=True, timeout=3) as con:
            con.execute("PRAGMA query_only=ON")
            count, oldest, newest = con.execute(
                "SELECT COUNT(*), MIN(start_epoch), MAX(start_epoch) "
                "FROM public_ohlc_1m WHERE pair='btc_idr'"
            ).fetchone()
    except M03LPacedDenied:
        raise
    except (sqlite3.Error, OSError, ValueError):
        raise M03LPacedDenied("M03L_ARCHIVE_READ_FAILED") from None
    if (type(count) is not int or not 4320 <= count <= TARGET_1M_ROWS
            or count % ROWS_PER_WINDOW != 0
            or type(oldest) is not int or type(newest) is not int
            or oldest <= 0 or oldest % 60 != 0 or newest % 60 != 0
            or newest - oldest != (count - 1) * 60):
        raise M03LPacedDenied("M03L_ARCHIVE_NOT_CONTIGUOUS_OR_BOUND_INVALID")
    return count


def local_rest_headroom() -> tuple[int, int, int]:
    """READ ONLY snapshot. Existing reserve() remains the final per-GET gate."""
    allowed = M03SharedEgressAdmission().allowed_local_limit()
    budget = SharedPublicRestBudget()
    try:
        if budget.path.is_symlink() or not budget.path.is_file():
            raise M03LPacedDenied("M03L_SHARED_REST_LEDGER_UNAVAILABLE")
        with sqlite3.connect(_readonly_uri(budget.path), uri=True, timeout=3) as con:
            con.execute("PRAGMA query_only=ON")
            used = con.execute(
                "SELECT COUNT(*) FROM rest_get_reservations "
                "WHERE requested_at > ?", (time.time() - 60.0,),
            ).fetchone()[0]
            row = con.execute(
                "SELECT max_local_requests_60s "
                "FROM rest_shared_egress_cap WHERE id=1"
            ).fetchone()
    except M03LPacedDenied:
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError):
        raise M03LPacedDenied("M03L_SHARED_REST_READ_FAILED") from None
    durable = row[0] if row is not None else 168
    if (type(allowed) is not int or type(used) is not int
            or type(durable) is not int or not 1 <= durable <= 168
            or used < 0):
        raise M03LPacedDenied("M03L_SHARED_REST_DATA_INVALID")
    cap = min(allowed, budget.limit, durable)
    return cap, used, cap - used


def run_original_batch(
    *, db: Path, windows: int, expected_count: int,
    env: dict[str, str],
) -> dict:
    """Run only original V2 CLI, not private API, custom fetch or new journal."""
    if type(windows) is not int or not 1 <= windows <= MAX_WINDOWS_PER_BATCH:
        raise M03LPacedDenied("M03L_UNSUPPORTED_BATCH_SIZE")
    args = [
        sys.executable, "-B", "-m", "indodax_ai_bot",
        "--public-history-archive-once",
        "--history-minutes", str(ROWS_PER_WINDOW),
        "--history-windows", str(windows),
        "--pair", "btc_idr", "--db", str(db),
    ]
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=BATCH_TIMEOUT_SECONDS,
            check=False, env=env,
        )
    except (subprocess.TimeoutExpired, OSError, UnicodeError):
        raise M03LPacedDenied("M03L_ORIGINAL_CLI_UNAVAILABLE_OR_TIMEOUT") from None
    try:
        data = json.loads(proc.stdout)
        archive = data["public_history_archive"]
        summary = archive["archive_summary"]
    except (ValueError, TypeError, KeyError):
        raise M03LPacedDenied("M03L_ORIGINAL_CLI_RESULT_INVALID") from None
    # Even on partial failure, original CLI may have committed complete older
    # 360m windows: caller prints actual rows and STOPs for manual inspection.
    accepted = (
        proc.returncode == 0
        and archive.get("status") == "PUBLIC_ARCHIVE_BATCH_ACCEPTED_UNATTESTED"
        and archive.get("windows_committed_this_call") == windows
        and archive.get("source_failure") is None
        and summary.get("stored_1m_rows") == expected_count
        and archive.get("g1_verified") is False
        and data.get("final_decision") == "REJECT"
        and data.get("execution_allowed") is False
        and data.get("live_trading_authorized") is False
    )
    return {
        "accepted": bool(accepted),
        "windows_committed": archive.get("windows_committed_this_call"),
        "reported_rows": summary.get("stored_1m_rows"),
        "source_failure_code": (
            archive.get("source_failure")
            if isinstance(archive.get("source_failure"), str)
            and len(archive["source_failure"]) <= 100
            and all(ch.isupper() or ch.isdigit() or ch in "_:-"
                    for ch in archive["source_failure"])
            else "NONE" if archive.get("source_failure") is None
            else "M03L_SOURCE_ERROR_REDACTED"
        ),
        "final_decision": data.get("final_decision"),
        "execution_allowed": data.get("execution_allowed"),
        "http_calls_bounded_upper": 3 * windows,
    }


def run_paced(
    *, db: Path = ARCHIVE, count_rows: Callable[[], int] | None = None,
    headroom: Callable[[], tuple[int, int, int]] = local_rest_headroom,
    batch: Callable[..., dict] = run_original_batch,
    sleep: Callable[[float], None] = time.sleep,
    max_wait_checks: int = MAX_WAIT_CHECKS,
    max_batches: int = MAX_BATCHES,
) -> dict:
    """One bounded owner-controlled session; no scheduler, paid AI or LIVE."""
    required = {
        "INDODAX_TRADING_ENABLED": "NO",
        "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
        "INDODAX_EXECUTION_MODE": "DRY_RUN",
        "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "YES",
        "M03L_OWNER_PACED_20D_GO": "YES",
    }
    if any(os.environ.get(k) != v for k, v in required.items()):
        raise M03LPacedDenied("M03L_OWNER_GO_OR_NONLIVE_SAFETY_MISSING")
    if (type(max_wait_checks) is not int or not 1 <= max_wait_checks <= MAX_WAIT_CHECKS
            or type(max_batches) is not int or not 1 <= max_batches <= MAX_BATCHES):
        raise M03LPacedDenied("M03L_SESSION_LIMIT_INVALID")
    get_rows = count_rows if count_rows is not None else lambda: existing_contiguous_rows(db)
    current = get_rows()
    if not isinstance(current, int) or isinstance(current, bool):
        raise M03LPacedDenied("M03L_ARCHIVE_COUNT_INVALID")
    start_rows = current
    completed = 0
    waited_checks = 0
    while current < TARGET_1M_ROWS:
        if completed >= max_batches:
            raise M03LPacedDenied("M03L_BOUNDED_SESSION_LIMIT_REACHED")
        if not 4320 <= current < TARGET_1M_ROWS or current % ROWS_PER_WINDOW:
            raise M03LPacedDenied("M03L_ARCHIVE_BOUND_INVALID")
        # Enforce one bounded backfill at a time and never assume headroom
        # remains available after reading it; the original HTTP transport
        # atomically reserves EVERY individual GET.
        cap, used, remaining = headroom()
        if (type(cap) is not int or type(used) is not int
                or type(remaining) is not int or not 1 <= cap <= 168
                or used < 0 or remaining != cap - used):
            raise M03LPacedDenied("M03L_SHARED_REST_SNAPSHOT_INVALID")
        if remaining < REQUIRED_HEADROOM:
            waited_checks += 1
            if waited_checks >= max_wait_checks:
                raise M03LPacedDenied("M03L_SHARED_REST_CAPACITY_STILL_BUSY")
            print(json.dumps({
                "status": "WAIT_SHARED_REST_CAPACITY",
                "local_limit": cap, "last_60s_used": used,
                "headroom": remaining, "required_headroom": REQUIRED_HEADROOM,
                "archived_1m_rows": current,
            }, sort_keys=True), flush=True)
            sleep(QUOTA_RECHECK_SECONDS)
            continue
        waited_checks = 0
        windows = min(MAX_WINDOWS_PER_BATCH,
                      (TARGET_1M_ROWS - current) // ROWS_PER_WINDOW)
        expected = current + windows * ROWS_PER_WINDOW
        print(json.dumps({
            "status": "ORIGINAL_M03L_ONE_BATCH_START",
            "batch_number": completed + 1,
            "archived_1m_rows_before": current,
            "windows": windows,
            "max_public_get": GET_PER_WINDOW * windows,
            "observed_headroom": remaining,
        }, sort_keys=True), flush=True)
        outcome = batch(db=db, windows=windows, expected_count=expected,
                        env=dict(os.environ))
        # Read the original existing DB after the original CLI returns. A
        # partial batch is real durable progress but never silently retried.
        observed = get_rows()
        print(json.dumps({
            "status": "ORIGINAL_M03L_ONE_BATCH_RESULT",
            "archived_1m_rows_after": observed,
            "batch": completed + 1,
            "accepted": outcome.get("accepted") is True,
            "windows_committed": outcome.get("windows_committed"),
            "source_failure_code": outcome.get("source_failure_code"),
            "final_decision": outcome.get("final_decision"),
            "execution_allowed": outcome.get("execution_allowed"),
        }, sort_keys=True), flush=True)
        if outcome.get("accepted") is not True or observed != expected:
            raise M03LPacedDenied("M03L_BATCH_STOP_INSPECT_SOURCE_OR_QUOTA")
        completed += 1
        current = observed
        if current < TARGET_1M_ROWS:
            sleep(BETWEEN_BATCH_SECONDS)
    return {
        "status": "M03L_OWNER_PUBLIC_ARCHIVE_20D_TARGET_REACHED_UNATTESTED",
        "initial_1m_rows": start_rows, "final_1m_rows": current,
        "batches_completed_this_run": completed,
        "archived_days": current // 1440,
        "g1_verified": False, "paid_ai_calls": 0,
        "order_sent": False, "execution_allowed": False,
        "final_decision": "REJECT",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One foreground original V2 M03-L owner-paced 20d public OHLC archive; NONLIVE"
    )
    parser.add_argument("--owner-public-backfill-go", action="store_true")
    args = parser.parse_args(argv)
    if not args.owner_public_backfill_go:
        print(json.dumps({
            "status": "STOP", "reason": "M03L_EXPLICIT_OWNER_CLI_GO_REQUIRED",
            "execution_allowed": False,
        }, sort_keys=True))
        return 3
    try:
        if LOCK.is_symlink():
            raise M03LPacedDenied("M03L_BACKFILL_LOCK_UNTRUSTED")
        fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                     stat.S_IRUSR | stat.S_IWUSR)
        try:
            if os.fstat(fd).st_uid != os.geteuid():
                raise M03LPacedDenied("M03L_BACKFILL_LOCK_UNTRUSTED")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise M03LPacedDenied("M03L_BACKFILL_ALREADY_RUNNING") from None
            report = run_paced()
        finally:
            os.close(fd)
    except (M03LPacedDenied, M03EgressAdmissionError) as exc:
        print(json.dumps({
            "status": "STOP", "reason": str(exc),
            "g1_verified": False, "paid_ai_calls": 0,
            "order_sent": False, "execution_allowed": False,
            "final_decision": "REJECT",
        }, sort_keys=True))
        return 3
    except (sqlite3.Error, OSError, ValueError, TypeError):
        print(json.dumps({
            "status": "STOP", "reason": "M03L_LOCAL_OPERATION_UNAVAILABLE",
            "g1_verified": False, "paid_ai_calls": 0,
            "order_sent": False, "execution_allowed": False,
            "final_decision": "REJECT",
        }, sort_keys=True))
        return 3
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
