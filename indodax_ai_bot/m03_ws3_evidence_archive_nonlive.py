"""Optional, private, bounded WS3 source-evidence archive for ORIGINAL M03.

No additional socket, REST/Indodax request, Telegram message, order or AI.
Writes only a separate local research SQLite when explicitly opted in.
Original M03 WATCH storage, freshness and thresholds are unchanged.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

from .scanner import MAIN_EXCLUDED_PAIRS

PAIR = re.compile(r"^[a-z0-9]+_idr$")
MIN_VOLUME_IDR = Decimal("150000000")
MAX_SOURCE_PAIRS_PER_CYCLE = 250
RETENTION_SECONDS = 72 * 3600  # 3 days, not the 2h original WATCH retention
MAX_RUNS_PER_DAY = 1440  # informational only, no additional scheduled runs

DEFAULT_EVIDENCE_DIR = (
    Path.home() / ".local" / "state" / "m03-ws3-evidence"
)
DEFAULT_EVIDENCE_DB = DEFAULT_EVIDENCE_DIR / "observations.sqlite3"


def _private_owned_regular(path: Path) -> bool:
    try:
        st = path.lstat()
        return (stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid()
                and st.st_nlink == 1 and stat.S_IMODE(st.st_mode) == 0o600)
    except FileNotFoundError:
        return False


def _prepare_private_target(path: Path) -> None:
    if not path.is_absolute() or path.name != "observations.sqlite3":
        raise ValueError("EVIDENCE_PATH_UNTRUSTED")
    parent = path.parent
    parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    st = parent.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid()
            or stat.S_IMODE(st.st_mode) != 0o700):
        raise ValueError("EVIDENCE_PARENT_UNTRUSTED")
    try:
        path.lstat()
    except FileNotFoundError:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        old = os.umask(0o077)
        try:
            fd = os.open(path, flags, 0o600)
            os.close(fd)
        except FileExistsError:
            pass  # Recheck security below when another owner M03 tick won.
        finally:
            os.umask(old)
    if not _private_owned_regular(path):
        raise ValueError("EVIDENCE_DB_UNTRUSTED")


def _classify(row: dict, now: int) -> tuple[str, int | None, str | None, str | None, int | None]:
    stamp = row.get("observed_exchange_epoch")
    if type(stamp) is not int or not 0 <= now - stamp <= 90:
        return "STALE_OR_INVALID_EPOCH", stamp if type(stamp) is int else None, None, None, None
    offset = row.get("source_public_offset")
    offset = offset if type(offset) is int and offset >= 0 else None
    try:
        price = Decimal(row["last_idr"])
        volume = Decimal(row["volume_24h_idr"])
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return "INVALID_PRICE_OR_VOLUME", stamp, None, None, offset
    if (not price.is_finite() or price <= 0 or not volume.is_finite()
            or volume < 0 or price.adjusted() > 20 or volume.adjusted() > 20):
        return "INVALID_PRICE_OR_VOLUME", stamp, None, None, offset
    if offset is None:
        return "INVALID_OFFSET", stamp, str(price), str(volume), None
    if volume < MIN_VOLUME_IDR:
        return "BELOW_150M_VOLUME_FLOOR", stamp, str(price), str(volume), offset
    return "ELIGIBLE_SOURCE_OBSERVATION", stamp, str(price), str(volume), offset


def archive_ws3_evidence_nonlive(
    snapshot: dict, *, now_epoch: int,
    evidence_db: Path = DEFAULT_EVIDENCE_DB,
) -> dict:
    """One best-effort research write for an ALREADY collected official WS3 snapshot.

    This does NOT read/write original M03 DB, change WATCH candidates or
    request new exchange frames. Absence of a pair in snapshot is UNKNOWN.
    """
    if (not isinstance(snapshot, dict)
            or snapshot.get("status") != "M03_WS3_PUBLIC_SUMMARY_OBSERVED"
            or type(now_epoch) is not int or now_epoch <= 0
            or not isinstance(snapshot.get("pairs"), list)
            or type(snapshot.get("frames_observed")) is not int):
        raise ValueError("EVIDENCE_SNAPSHOT_INVALID")
    rows = snapshot["pairs"]
    if len(rows) > 800:
        raise ValueError("EVIDENCE_SNAPSHOT_TOO_LARGE")
    path = Path(evidence_db)
    _prepare_private_target(path)
    minute = now_epoch // 60 * 60
    accepted = []
    counts = {
        "ELIGIBLE_SOURCE_OBSERVATION": 0,
        "STALE_OR_INVALID_EPOCH": 0,
        "INVALID_PRICE_OR_VOLUME": 0,
        "INVALID_OFFSET": 0,
        "BELOW_150M_VOLUME_FLOOR": 0,
        "EXCLUDED_OR_INVALID_PAIR": 0,
    }
    # Deterministic per-minute cap, explicit partial coverage flag.
    # Do not assert that all Indodax markets were ever published.
    for row in rows:
        if not isinstance(row, dict):
            counts["EXCLUDED_OR_INVALID_PAIR"] += 1
            continue
        pair = row.get("pair")
        if (not isinstance(pair, str) or not PAIR.fullmatch(pair)
                or pair in MAIN_EXCLUDED_PAIRS):
            counts["EXCLUDED_OR_INVALID_PAIR"] += 1
            continue
        status, stamp, price, volume, offset = _classify(row, now_epoch)
        counts[status] += 1
        accepted.append((pair, minute, stamp, price, volume, offset, status))
    accepted.sort(key=lambda x: x[0])
    dropped = max(0, len(accepted) - MAX_SOURCE_PAIRS_PER_CYCLE)
    accepted = accepted[:MAX_SOURCE_PAIRS_PER_CYCLE]
    with sqlite3.connect(str(path), timeout=3) as db:
        db.execute("PRAGMA busy_timeout=3000")
        db.execute("BEGIN IMMEDIATE")
        db.execute("""
            CREATE TABLE IF NOT EXISTS ws3_source_evidence (
                pair TEXT NOT NULL, minute_epoch INTEGER NOT NULL,
                source_exchange_epoch INTEGER, last_idr TEXT,
                rolling_24h_idr TEXT, public_offset INTEGER,
                source_status TEXT NOT NULL,
                PRIMARY KEY(pair,minute_epoch)
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS ws3_source_evidence_minute_idx
            ON ws3_source_evidence(minute_epoch)
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS ws3_source_cycles (
                minute_epoch INTEGER PRIMARY KEY,
                recorded_local_epoch INTEGER NOT NULL,
                frames_observed INTEGER NOT NULL,
                source_pairs_seen INTEGER NOT NULL,
                pairs_saved_cap INTEGER NOT NULL,
                source_eligible_count INTEGER NOT NULL,
                stale_count INTEGER NOT NULL,
                low_turnover_count INTEGER NOT NULL,
                invalid_count INTEGER NOT NULL,
                excluded_count INTEGER NOT NULL,
                dropped_by_cap INTEGER NOT NULL
            )
        """)
        # Keep historical research evidence for 72h without changing M03's
        # existing 2h minute archive or any owner Telegram cooldown.
        cutoff = minute - RETENTION_SECONDS
        db.execute("DELETE FROM ws3_source_evidence WHERE minute_epoch < ?", (cutoff,))
        db.execute("DELETE FROM ws3_source_cycles WHERE minute_epoch < ?", (cutoff,))
        db.executemany("""
            INSERT OR IGNORE INTO ws3_source_evidence
            (pair,minute_epoch,source_exchange_epoch,last_idr,
             rolling_24h_idr,public_offset,source_status)
            VALUES (?,?,?,?,?,?,?)
        """, accepted)
        db.execute("""
            INSERT OR IGNORE INTO ws3_source_cycles
            (minute_epoch,recorded_local_epoch,frames_observed,
             source_pairs_seen,pairs_saved_cap,source_eligible_count,
             stale_count,low_turnover_count,invalid_count,
             excluded_count,dropped_by_cap)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """, (
            minute, now_epoch, snapshot["frames_observed"], len(rows),
            len(accepted), counts["ELIGIBLE_SOURCE_OBSERVATION"],
            counts["STALE_OR_INVALID_EPOCH"],
            counts["BELOW_150M_VOLUME_FLOOR"],
            counts["INVALID_PRICE_OR_VOLUME"] + counts["INVALID_OFFSET"],
            counts["EXCLUDED_OR_INVALID_PAIR"], dropped,
        ))
    return {
        "status": "M03_WS3_RESEARCH_ARCHIVE_RECORDED",
        "source_pairs_seen": len(rows),
        "source_pairs_saved_cap": len(accepted),
        "source_eligible_count": counts["ELIGIBLE_SOURCE_OBSERVATION"],
        "dropped_by_cap": dropped,
        "source_coverage_complete": False,
        "not_continuous_trade_tape": True,
        "trading_enabled": False,
        "orders_sent": 0,
        "telegram_messages_sent": 0,
        "exchange_requests_added": 0,
    }
