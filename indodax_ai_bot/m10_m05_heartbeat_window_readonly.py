"""M10-06 read-only 24h heartbeat evidence from the ONE ORIGINAL M05 service.

Reads allowlisted systemd user journal records only, never prints raw logs.
The fast receiver emits M05_FAST_LOOP_HEARTBEAT_NONLIVE every 10 batches
and on radar checks (including batch 1). This tool creates NO worker,
timer, GO, order, API call or log file.
A completed M05 heartbeat window is NOT full M10-06 acceptance, because
BTC WS recovery and external alert-delivery guarantees are independent.
"""
from __future__ import annotations

import json
from collections import Counter
import re
import subprocess
import time
from typing import Any, Iterable

UNIT = "indodax-v2-m05-telegram.service"
MARKER = "M05_FAST_LOOP_HEARTBEAT_NONLIVE"
WINDOW_SECONDS = 86400
MAX_GAP_SECONDS = 180
MAX_RECORDS = 9500
MAX_JOURNAL_BYTES = 12_000_000
_INVOCATION_RE = re.compile(r"^[0-9a-f]{32}$")


def assess_m05_heartbeat_window(
    records: Iterable[dict[str, Any]], *, now_epoch: int,
    window_seconds: int = WINDOW_SECONDS,
    max_gap_seconds: int = MAX_GAP_SECONDS,
    journal_truncated: bool = False,
) -> dict[str, Any]:
    """Pure audit for deterministic offline tests, no systemctl or network."""
    if (type(now_epoch) is not int or now_epoch <= WINDOW_SECONDS
            or type(window_seconds) is not int or window_seconds <= 0
            or type(max_gap_seconds) is not int or max_gap_seconds <= 0):
        raise ValueError("M10_HEARTBEAT_INVALID_WINDOW")
    start = now_epoch - window_seconds
    events: list[tuple[int, str, int]] = []
    invalid = False
    rejected: Counter[str] = Counter()
    rejected_fields: Counter[str] = Counter()
    count = 0
    for row in records:
        count += 1
        if count > MAX_RECORDS or not isinstance(row, dict):
            invalid = True
            rejected["RECORD_LIMIT_OR_ROW_INVALID"] += 1
            break
        message = row.get("MESSAGE")
        if not isinstance(message, str) or len(message) > 2048:
            invalid = True
            rejected["MESSAGE_NOT_BOUNDED"] += 1
            continue
        try:
            body = json.loads(message)
        except (ValueError, UnicodeError):
            invalid = True
            rejected["MESSAGE_NOT_JSON"] += 1
            continue
        if not isinstance(body, dict) or body.get("status") != MARKER:
            invalid = True
            rejected["MATCHED_NON_HEARTBEAT"] += 1
            continue
        stamp = row.get("__REALTIME_TIMESTAMP")
        invocation = row.get("_SYSTEMD_INVOCATION_ID")
        batches = body.get("batches")
        counters = ("handled", "ignored", "outbound_sent")
        # Validate the original producer's contract, not its periodic cadence:
        # radar emits on the first poll and on non-multiples of ten as well.
        checks = {
            "TIMESTAMP_INVALID": isinstance(stamp, str) and len(stamp) <= 19
                and stamp.isascii() and stamp.isdecimal(),
            "INVOCATION_INVALID": isinstance(invocation, str)
                and _INVOCATION_RE.fullmatch(invocation) is not None,
            "BATCHES_INVALID": type(batches) is int and batches >= 1,
            "EXECUTION_NOT_FALSE": body.get("execution_allowed") is False,
        }
        checks.update({
            k.upper() + "_INVALID": type(body.get(k)) is int and body[k] >= 0
            for k in counters
        })
        failed_fields = [name for name, ok in checks.items() if not ok]
        if failed_fields:
            invalid = True
            rejected["HEARTBEAT_FIELDS_UNTRUSTED"] += 1
            rejected_fields.update(failed_fields)
            continue
        when = int(stamp) // 1_000_000
        if not start <= when <= now_epoch:
            invalid = True
            rejected["HEARTBEAT_OUTSIDE_WINDOW"] += 1
            continue
        events.append((when, invocation, batches))

    events.sort(key=lambda x: x[0])
    max_gap = None
    max_gap_kind = "NO_EVENTS"
    max_gap_from_epoch = None
    max_gap_to_epoch = None
    progress_valid = True
    invocation_segments: list[dict[str, int]] = []
    transitions: list[dict[str, int]] = []
    segment_invocation = None
    if events:
        # Include BOTH edges of the audit window. Never present a window
        # boundary gap as a confirmed in-process transport interruption.
        intervals = [(start, events[0][0], "WINDOW_START")]
        intervals.extend((a[0], b[0], "BETWEEN_HEARTBEATS")
                         for a, b in zip(events, events[1:]))
        intervals.append((events[-1][0], now_epoch, "WINDOW_END"))
        maximum = max(intervals, key=lambda x: x[1] - x[0])
        max_gap_from_epoch, max_gap_to_epoch, max_gap_kind = maximum
        max_gap = max_gap_to_epoch - max_gap_from_epoch
        transitions = [{
            "previous_heartbeat_epoch": a[0],
            "next_heartbeat_epoch": b[0],
            "gap_seconds": b[0] - a[0],
        } for a, b in zip(events, events[1:]) if a[1] != b[1]]
        last_batches_by_invocation: dict[str, int] = {}
        for epoch, invocation, batches in events:
            previous = last_batches_by_invocation.get(invocation)
            if previous is not None and batches <= previous:
                progress_valid = False
            last_batches_by_invocation[invocation] = batches
            # Output aggregates without exposing systemd invocation IDs,
            # PIDs, journal MESSAGE content, tokens or timestamps of texts.
            if invocation != segment_invocation:
                invocation_segments.append({
                    "first_heartbeat_epoch": epoch,
                    "last_heartbeat_epoch": epoch,
                    "sample_count": 1,
                })
                segment_invocation = invocation
            else:
                invocation_segments[-1]["last_heartbeat_epoch"] = epoch
                invocation_segments[-1]["sample_count"] += 1
    invocations = len({e[1] for e in events})
    # Single-invocation continuity is intentionally stricter than
    # restart recovery. A successful restart needs separate evidence.
    continuity = (
        not journal_truncated and not invalid and progress_valid
        and bool(events) and max_gap is not None
        and max_gap <= max_gap_seconds and invocations == 1
    )
    return {
        "status": "M10_M05_24H_HEARTBEAT_READONLY_AUDIT",
        "scope": "ONE_ORIGINAL_V2_M05_SYSTEMD_USER_UNIT",
        "window_seconds": window_seconds,
        "sample_count": len(events),
        "invalid_or_untrusted_records": invalid,
        "rejected_record_counts": dict(sorted(rejected.items())),
        # One rejected row can fail multiple fields; these are not row totals.
        "rejected_field_counts": dict(sorted(rejected_fields.items())),
        "journal_truncated": bool(journal_truncated),
        "counter_progress_valid": progress_valid,
        "observed_process_invocation_count": invocations,
        "first_heartbeat_epoch": events[0][0] if events else None,
        "last_heartbeat_epoch": events[-1][0] if events else None,
        "maximum_observed_gap_seconds": max_gap,
        "maximum_gap_kind": max_gap_kind,
        "maximum_gap_start_epoch": max_gap_from_epoch,
        "maximum_gap_end_epoch": max_gap_to_epoch,
        "invocation_segments": invocation_segments[:8],
        "invocation_segments_truncated": len(invocation_segments) > 8,
        "invocation_transitions": transitions[:8],
        "invocation_transitions_truncated": len(transitions) > 8,
        "original_m05_heartbeat_window_complete": continuity,
        "continuous_24h_heartbeat_verified": (
            continuity and window_seconds == WINDOW_SECONDS
        ),
        "restart_recovery_proven": False,
        "btc_ws_24h_uptime_verified": False,
        "automatic_alert_24h_delivery_verified": False,
        "m10_06_full_acceptance": False,
        "service_modified": False,
        "trading_enabled": False,
    }


def collect_m05_heartbeat_readonly(*, now_epoch: int | None = None,
                                   runner=subprocess.run) -> dict[str, Any]:
    now = int(time.time()) if now_epoch is None else now_epoch
    if type(now) is not int or now <= WINDOW_SECONDS:
        raise ValueError("M10_HEARTBEAT_INVALID_CLOCK")
    args = [
        "journalctl", "--user", "--unit", UNIT,
        "--since", "@" + str(now - WINDOW_SECONDS),
        "--until", "@" + str(now),
        "--output=json", "--no-pager", "--quiet",
        "--grep=" + MARKER, "--lines=" + str(MAX_RECORDS),
    ]
    try:
        result = runner(args, capture_output=True, timeout=18, check=False)
        raw = result.stdout
        if (result.returncode != 0 or not isinstance(raw, bytes)
                or len(raw) > MAX_JOURNAL_BYTES):
            raise ValueError("M10_JOURNAL_UNAVAILABLE_OR_OVERSIZED")
        lines = raw.splitlines()
        if len(lines) >= MAX_RECORDS:
            raise ValueError("M10_JOURNAL_RECORD_CAP_REACHED")
        records = [json.loads(line) for line in lines]
        if any(not isinstance(record, dict) for record in records):
            raise ValueError("M10_JOURNAL_INVALID_RECORD")
        report = assess_m05_heartbeat_window(records, now_epoch=now)
        report["journal_read_successful"] = True
        return report
    except (OSError, subprocess.TimeoutExpired, ValueError, UnicodeError):
        report = assess_m05_heartbeat_window(
            [], now_epoch=now, journal_truncated=True,
        )
        report["journal_read_successful"] = False
        return report


def main() -> int:
    print(json.dumps(collect_m05_heartbeat_readonly(), sort_keys=True,
                     separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

