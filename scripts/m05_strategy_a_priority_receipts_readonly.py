"""M05 Strategy A original-process owner ACK / WS3 as-of source audit.

READ ONLY: no HTTP, exchange, Telegram send, local DB write or service restart.
Never print token, owner ID, Telegram message ID, raw process environment,
private paths, raw journal messages or exceptions. A source reconstruction is
NOT an attested original M05 sender input, a fill, or an AI recommendation.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import time
from zoneinfo import ZoneInfo

from indodax_ai_bot.m03_ws3_evidence_archive_nonlive import DEFAULT_EVIDENCE_DB
from indodax_ai_bot.m05_radar_momentum_nonlive import _owned_private_regular
from scripts.m05_strategy_a_priority_preflight_readonly import (
    M05, REQUIRED_FLAGS, PRIORITY_FLAG, summarize_allowlisted_process_flags,
)

WIB = ZoneInfo("Asia/Jakarta")
MAX_EVENTS = 100
SCAN_SECONDS = 86400
PAIRS = re.compile(r"^[a-z0-9]+_idr$")


def process_start_epoch_from_proc(*, stat_text: str, uptime_seconds: float,
                                  now_epoch: int, tick_hz: int) -> int:
    """Linux stat starttime (field 22) relative to uptime, not service age guess."""
    if (type(stat_text) is not str or type(uptime_seconds) is not float
            or type(now_epoch) is not int or type(tick_hz) is not int
            or tick_hz <= 0 or not 0 <= uptime_seconds <= 1e9):
        raise ValueError("INVALID_PROCESS_START_INPUT")
    tail = stat_text.rsplit(") ", 1)
    if len(tail) != 2:
        raise ValueError("INVALID_PROCESS_STAT")
    fields = tail[1].split()
    if len(fields) < 20:
        raise ValueError("INVALID_PROCESS_STAT_LENGTH")
    ticks = int(fields[19])
    if ticks < 0 or ticks / tick_hz > uptime_seconds + 1:
        raise ValueError("INVALID_PROCESS_START_TICKS")
    epoch = int(now_epoch - uptime_seconds + ticks / tick_hz)
    if not 0 < epoch <= now_epoch:
        raise ValueError("INVALID_PROCESS_START_EPOCH")
    return epoch


def _money(value: str, *, positive: bool = False) -> Decimal:
    if type(value) is not str or not 1 <= len(value) <= 64:
        raise ValueError("AUDIT_PRICE_OR_TURNOVER_INVALID")
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError("AUDIT_PRICE_OR_TURNOVER_INVALID") from None
    if (not number.is_finite() or number.adjusted() > 20
            or (number <= 0 if positive else number < 0)):
        raise ValueError("AUDIT_PRICE_OR_TURNOVER_INVALID")
    return number


def assess_ack_source_asof_offline(
    *, source_kind: str, source_epoch: int, claim_epoch: int,
    rows: list[tuple[int, int, str, str, str, int]],
) -> dict:
    """Recreate only an observable threshold; NOT the full M05 at-claim rank.

    Each row = (minute,exchange_epoch,last,rolling24h,status,recorded_local).
    Requires source+reference recorded by claim. Missing partial history
    is UNKNOWN, never treated as a rule violation or successful trade.
    """
    if source_kind != "WS3":
        return {"source_verdict": "REST_SOURCE_NOT_RECONSTRUCTED",
                "priority_threshold_reconstructed": False}
    if (type(source_epoch) is not int or type(claim_epoch) is not int
            or not 0 < source_epoch <= claim_epoch
            or type(rows) is not list or len(rows) > 20):
        return {"source_verdict": "SOURCE_SCOPE_INVALID_OR_INCOMPLETE",
                "priority_threshold_reconstructed": False}
    admissible = []
    for row in rows:
        if type(row) is not tuple or len(row) not in (6,8):
            return {"source_verdict": "SOURCE_ROW_MALFORMED",
                    "priority_threshold_reconstructed": False}
        minute, exchange, raw_price, raw_vol, status, recorded = row
        if (type(minute) is not int or minute % 60
                or type(exchange) is not int or type(recorded) is not int
                or recorded > claim_epoch or recorded < exchange
                or minute > claim_epoch // 60 * 60
                or exchange > claim_epoch
                or status not in ("ELIGIBLE_SOURCE_OBSERVATION",
                                  "BELOW_150M_VOLUME_FLOOR")):
            continue
        try:
            price = _money(raw_price, positive=True)
            vol = _money(raw_vol)
        except ValueError:
            return {"source_verdict": "SOURCE_ROW_MALFORMED",
                    "priority_threshold_reconstructed": False}
        admissible.append((minute, exchange, price, vol, status))
    latest = [x for x in admissible
              if x[1] == source_epoch and
              x[4] == "ELIGIBLE_SOURCE_OBSERVATION"]
    if len(latest) != 1:
        return {"source_verdict": "EXACT_ASOF_WS3_SOURCE_UNAVAILABLE",
                "priority_threshold_reconstructed": False}
    latest = latest[0]
    references = sorted(
        (r for r in admissible if
         60 <= latest[0] - r[0] <= 300
         and r[1] < latest[1]),
        key=lambda r: (r[0], r[1]), reverse=True,
    )
    if not references:
        return {"source_verdict": "ASOF_REFERENCE_MINUTE_UNAVAILABLE",
                "priority_threshold_reconstructed": False}
    reference = references[0]
    rise = (latest[2] / reference[2] - Decimal(1)) * Decimal(100)
    ok = rise >= Decimal("1.5") and latest[3] >= Decimal("400000000")
    return {
        "source_verdict": ("ASOF_ARCHIVE_PRIORITY_THRESHOLDS_OBSERVED"
                           if ok else "ASOF_ARCHIVE_THRESHOLD_MISMATCH_REVIEW"),
        "priority_threshold_reconstructed": bool(ok),
        "observed_rise_pct": format(rise, ".4f"),
        "rolling_24h_idr": format(latest[3], "f"),
        "asof_archive_is_not_original_sender_rank_receipt": True,
        "source_quote_not_verified_executable_bid": True,
    }


def summarize_priority_receipts_offline(*, pid_start: int, now_epoch: int,
                                        events: list[tuple],
                                        source_reader) -> dict:
    if (type(pid_start) is not int or type(now_epoch) is not int
            or not 0 < pid_start <= now_epoch
            or type(events) is not list or len(events) > MAX_EVENTS
            or not callable(source_reader)):
        raise ValueError("M05_PRIORITY_AUDIT_INVALID_SCOPE")
    counts = Counter()
    entries = []
    for row in events:
        if type(row) is not tuple or len(row) != 6:
            raise ValueError("M05_PRIORITY_AUDIT_EVENT_INVALID")
        pair, claim, source_epoch, kind, delivery, ack_epoch = row[:6]
        path, green = row[6:] if len(row)==8 else ("MOMENTUM",None)
        if (path not in ("MOMENTUM","INDEPENDENT_FOUR_GREEN")
                or (path=="INDEPENDENT_FOUR_GREEN" and (kind!="WS3" or
                    type(green) is not int or not 4<=green<=8))
                or (green is not None and (type(green) is not int or not 4<=green<=8))):
            raise ValueError("M05_RADAR_ROUTE_INVALID")
        if (type(pair) is not str or not PAIRS.fullmatch(pair)
                or type(claim) is not int or not pid_start <= claim <= now_epoch
                or type(source_epoch) is not int or source_epoch > claim
                or kind not in ("WS3", "REST")
                or delivery not in ("ACKED", "CLAIMED", "UNCERTAIN")
                or (delivery == "ACKED" and
                    (type(ack_epoch) is not int
                     or not claim <= ack_epoch <= now_epoch))
                or (delivery != "ACKED" and ack_epoch is not None)):
            raise ValueError("M05_PRIORITY_AUDIT_EVENT_INVALID")
        counts[delivery] += 1
        row_out = {"pair": pair, "delivery": delivery, "source_kind": kind,
                   "claim_wib": datetime.fromtimestamp(
                       claim, WIB).strftime("%Y-%m-%d %H:%M WIB")}
        if len(row)==8:
            row_out.update(watch_path=path,green_at_claim=green)
        if delivery == "ACKED" and path=="INDEPENDENT_FOUR_GREEN":
            row_out.update(source_verdict="INDEPENDENT_FOUR_GREEN_CLAIM_RECORDED",
                           priority_threshold_reconstructed=False,
                           local_green_claim_not_independent_attestation=True)
            counts["INDEPENDENT_FOUR_GREEN_CLAIM_RECORDED"] += 1
        elif delivery == "ACKED":
            raw = source_reader(pair, source_epoch, claim) if kind == "WS3" else []
            observed = assess_ack_source_asof_offline(
                source_kind=kind, source_epoch=source_epoch,
                claim_epoch=claim, rows=raw)
            row_out.update(observed)
            counts[observed["source_verdict"]] += 1
        entries.append(row_out)
    return {
        "status": ("M05_PRIORITY_ACK_EVENTS_OBSERVED_READONLY"
                   if counts["ACKED"] else
                   "M05_PRIORITY_NO_NEW_ACK_YET_READONLY"),
        "event_count": len(entries),
        "acked": counts["ACKED"],
        "claimed_unconfirmed": counts["CLAIMED"],
        "delivery_uncertain": counts["UNCERTAIN"],
        "asof_threshold_reconstructed_count":
            counts["ASOF_ARCHIVE_PRIORITY_THRESHOLDS_OBSERVED"],
        "independent_four_green_acked":counts["INDEPENDENT_FOUR_GREEN_CLAIM_RECORDED"],
        "archive_mismatch_needs_review":
            counts["ASOF_ARCHIVE_THRESHOLD_MISMATCH_REVIEW"],
        "missing_or_unverified_provenance":
            counts["EXACT_ASOF_WS3_SOURCE_UNAVAILABLE"]
            + counts["ASOF_REFERENCE_MINUTE_UNAVAILABLE"]
            + counts["REST_SOURCE_NOT_RECONSTRUCTED"]
            + counts["SOURCE_ROW_MALFORMED"]
            + counts["SOURCE_SCOPE_INVALID_OR_INCOMPLETE"],
        "events": entries,
        "ack_is_bot_api_only_not_owner_device_receipt": True,
        "asof_reconstruction_not_original_m05_rank_receipt": True,
        "no_1m_candle_or_buy_pressure_attested": True,
        "execution_allowed": False, "orders_sent": 0,
        "telegram_messages_sent_by_audit": 0, "service_restarted": False,
    }


def _process_env(pid: int) -> dict[str, str]:
    raw = (Path("/proc") / str(pid) / "environ").read_bytes()
    flags = summarize_allowlisted_process_flags(raw)
    if (any(flags.get(k) != v for k, v in REQUIRED_FLAGS.items())
            or flags.get(PRIORITY_FLAG) != "YES"):
        raise ValueError("M05_PRIORITY_RUNNING_GUARDS_MISSING")
    # The only other env field read is original owner DB LOCATION.
    # Do not return the process env, owner ID or any raw bytes.
    vals = [e.split(b"=", 1)[1] for e in raw.split(b"\0")
            if e.startswith(b"M05_TELEGRAM_OWNER_DB=")]
    if len(vals) != 1 or not 1 <= len(vals[0]) <= 1024:
        raise ValueError("M05_PRIORITY_OWNER_DB_PATH_UNVERIFIED")
    try:
        owner_path = vals[0].decode("utf-8", "strict")
    except UnicodeError:
        raise ValueError("M05_PRIORITY_OWNER_DB_PATH_UNVERIFIED") from None
    if ("\0" in owner_path or "\n" in owner_path
            or not Path(owner_path).is_absolute()):
        raise ValueError("M05_PRIORITY_OWNER_DB_PATH_UNVERIFIED")
    return {"owner_db": owner_path}


def _read_source_rows(path: Path, *, pair: str, source_epoch: int,
                      claim: int) -> list[tuple]:
    if path is None:
        return []
    minute = source_epoch // 60 * 60
    with sqlite3.connect(path.as_uri() + "?mode=ro",
                         uri=True, timeout=3) as db:
        db.execute("PRAGMA query_only=ON")
        return db.execute(
            "SELECT e.minute_epoch,e.source_exchange_epoch,e.last_idr,"
            "e.rolling_24h_idr,e.source_status,c.recorded_local_epoch "
            "FROM ws3_source_evidence AS e "
            "JOIN ws3_source_cycles AS c USING(minute_epoch) "
            "WHERE e.pair=? AND e.minute_epoch BETWEEN ? AND ? "
            "AND c.recorded_local_epoch<=? ORDER BY e.minute_epoch DESC LIMIT 21",
            (pair, minute - 360, minute, claim),
        ).fetchall()


def inspect_original_priority_ack_readonly() -> dict:
    """Original M05 process + original owner SQLite + optional source archive."""
    try:
        root = Path.home() / "indodax-ai-bot-v2"
        p = subprocess.run(["systemctl", "--user", "show", M05,
                            "-p", "MainPID", "-p", "ActiveState"],
                           capture_output=True, text=True, check=True, timeout=8)
        properties = dict(line.split("=", 1) for line in p.stdout.splitlines()
                          if "=" in line)
        raw_pid = properties.get("MainPID", "")
        if (properties.get("ActiveState") != "active"
                or re.fullmatch(r"[1-9][0-9]{0,9}", raw_pid) is None):
            raise ValueError("M05_NOT_ACTIVE")
        pid = int(raw_pid)
        process = _process_env(pid)
        now = int(time.time())
        stat_line = (Path("/proc") / raw_pid / "stat").read_text()
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        start = process_start_epoch_from_proc(
            stat_text=stat_line, uptime_seconds=uptime,
            now_epoch=now, tick_hz=os.sysconf("SC_CLK_TCK"))
        owner = Path(process["owner_db"])
        if not _owned_private_regular(owner):
            raise ValueError("OWNER_LEDGER_NOT_PRIVATE")
        archive = Path(DEFAULT_EVIDENCE_DB)
        astat = archive.parent.lstat() if archive.is_file() else None
        archive_ok = (
            astat is not None
            and stat.S_ISDIR(astat.st_mode)
            and astat.st_uid == os.geteuid()
            and stat.S_IMODE(astat.st_mode) == 0o700
            and _owned_private_regular(archive))
        min_epoch = max(start, now - SCAN_SECONDS)
        with sqlite3.connect(owner.as_uri() + "?mode=ro",
                             uri=True, timeout=3) as db:
            db.execute("PRAGMA query_only=ON")
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='m05_radar_delivery_events'"
            ).fetchone()
            columns={x[1] for x in db.execute("PRAGMA table_info(m05_radar_delivery_events)")} if exists else set()
            route_fields=(",watch_path,green_at_claim" if {"watch_path","green_at_claim"}<=columns else "")
            rows = db.execute(
                "SELECT pair,claimed_epoch,source_observed_epoch,source_kind,"
                "delivery_state,ack_received_epoch"+route_fields+" "
                "FROM m05_radar_delivery_events "
                "WHERE claimed_epoch BETWEEN ? AND ? "
                "ORDER BY claimed_epoch DESC LIMIT ?",
                (min_epoch, now, MAX_EVENTS + 1),
            ).fetchall() if exists else []
        if len(rows) > MAX_EVENTS:
            raise ValueError("M05_PRIORITY_TOO_MANY_EVENTS")
        def source_reader(pair, source_epoch, claim):
            return _read_source_rows(
                archive if archive_ok else None,
                pair=pair, source_epoch=source_epoch, claim=claim)
        output = summarize_priority_receipts_offline(
            pid_start=min_epoch, now_epoch=now, events=rows[::-1],
            source_reader=source_reader)
        after = subprocess.run(
            ["systemctl", "--user", "show", M05, "-p", "MainPID"],
            capture_output=True, text=True, check=True, timeout=8)
        if after.stdout.strip() != "MainPID=" + raw_pid:
            raise ValueError("M05_PID_CHANGED_DURING_READ")
        return {
            **output, "m05_pid": pid, "current_source_archive_available":
            bool(archive_ok),
            "since_new_m05_process_wib": datetime.fromtimestamp(
                min_epoch, WIB).strftime("%Y-%m-%d %H:%M WIB"),
        }
    except (OSError, ValueError, sqlite3.Error,
            subprocess.SubprocessError, OverflowError):
        return {
            "status": "M05_PRIORITY_ACK_AUDIT_BLOCKED_READONLY",
            "reason": "ORIGINAL_SOURCE_OR_PROCESS_NOT_VERIFIED",
            "execution_allowed": False, "orders_sent": 0,
            "telegram_messages_sent_by_audit": 0,
            "service_restarted": False,
        }


def main() -> int:
    result = inspect_original_priority_ack_readonly()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 2 if result["status"] == "M05_PRIORITY_ACK_AUDIT_BLOCKED_READONLY" else 0


if __name__ == "__main__":
    raise SystemExit(main())
