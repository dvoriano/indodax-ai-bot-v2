"""Original M03 local ACK checkpoint history, bounded READ ONLY.

Sampled local observations, never continuous subscription, complete tape,
exact ACK arrival or independent source proof. No new connections, DB
writes, service actions, trading, model calls or Telegram.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import re
import subprocess
import time
from zoneinfo import ZoneInfo

from scripts.m04_ws3_watch_channel_coverage_readonly import (
    _validated_checkpoints,
)

_UNIT = "indodax-v2-m03-mainpath.service"
_PAIR = re.compile(r"[a-z0-9]+_idr\Z")
_INVOCATION = re.compile(r"[0-9a-f]{32}\Z")
_PID = re.compile(r"[1-9][0-9]{0,9}\Z")
_MAX_RECORDS = 2000
_MAX_BYTES = 8_000_000
_MAX_MESSAGE_BYTES = 32768
_WINDOWS = (("15m", 900), ("1h", 3600))


def _result(status, **fields):
    return {
        "stage": "M04_G1_ORIGINAL_LOCAL_ACK_WINDOW_RESEARCH_ONLY",
        "status": status,
        "sampled_checkpoints_do_not_prove_continuous_subscription": True,
        "checkpoint_timestamp_is_not_exact_ack_arrival": True,
        "independent_gap_recovery_verified": False,
        "independent_g1_verified": False,
        "complete_trade_tape_verified": False,
        "pair_specific_ack_verified": False,
        "execution_allowed": False,
        "provider_calls": 0,
        "telegram_messages_sent": 0,
        "orders_sent": 0,
        **fields,
    }


def _scope(pairs, end):
    return (type(end) is int and end > 3600 and end % 60 == 0
            and type(pairs) is list and 1 <= len(pairs) <= 12
            and all(type(p) is str and len(p) <= 40 and _PAIR.fullmatch(p)
                    for p in pairs)
            and len(set(pairs)) == len(pairs))


def _decode_records(records, end_epoch):
    if type(records) is not list or len(records) > _MAX_RECORDS:
        raise ValueError("CHECKPOINT_JOURNAL_SCOPE_INVALID")
    seen = {}
    for record in records:
        if type(record) is not dict:
            raise ValueError("JOURNAL_RECORD_INVALID")
        raw = record.get("MESSAGE")
        if type(raw) is not str or len(raw.encode("utf-8")) > _MAX_MESSAGE_BYTES:
            # An opaque/oversized journal line cannot be safely classified.
            raise ValueError("JOURNAL_MESSAGE_UNAVAILABLE_OR_OVERSIZED")
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if type(obj) is not dict or obj.get("status") != "M03_WS3_CONTINUOUS_CHECKPOINT":
            continue
        stamp = obj.get("observed_epoch")
        if type(stamp) is not int:
            raise ValueError("CHECKPOINT_EPOCH_INVALID")
        if not end_epoch - 3600 <= stamp < end_epoch:
            continue
        if obj.get("execution_allowed") is not False:
            raise ValueError("CHECKPOINT_NONLIVE_STATE_INVALID")
        checked = _validated_checkpoints([obj], capture_epoch=stamp)
        if len(checked) != 1:
            raise ValueError("CHECKPOINT_STATE_INVALID")
        row = checked[0]
        # Journal metadata is local provenance, not remote authentication.
        # Missing invocation or PID is UNKNOWN, never fabricate a session.
        pid = record.get("_PID")
        inv = record.get("_SYSTEMD_INVOCATION_ID")
        row["pid"] = pid if type(pid) is str and _PID.fullmatch(pid) else None
        row["invocation"] = inv if type(inv) is str and _INVOCATION.fullmatch(inv) else None
        receipt = record.get("__REALTIME_TIMESTAMP")
        if (type(receipt) is not str or not receipt.isascii()
                or not receipt.isdecimal() or len(receipt) > 20):
            raise ValueError("CHECKPOINT_JOURNAL_RECEIPT_UNKNOWN")
        receipt_epoch = int(receipt) // 1000000
        if receipt_epoch < stamp:
            raise ValueError("CHECKPOINT_JOURNAL_RECEIPT_PRECEDES_OBSERVATION")
        if receipt_epoch >= end_epoch:
            continue  # Not available by this closed-window cutoff.
        if stamp in seen and seen[stamp] != row:
            raise ValueError("CHECKPOINT_TIMESTAMP_CONFLICT")
        seen[stamp] = row
    return [seen[k] for k in sorted(seen)]


def summarize_local_ack_windows(*, records, pairs, end_epoch):
    if not _scope(pairs, end_epoch):
        return _result("ACK_WINDOW_SCOPE_INVALID")
    try:
        rows = _decode_records(records, end_epoch)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return _result("ACK_WINDOW_CHECKPOINT_CONTRACT_BLOCKED")

    windows = {}
    for label, seconds in _WINDOWS:
        start = end_epoch - seconds
        sample = [r for r in rows if r["observed_epoch"] >= start]
        stamps = [r["observed_epoch"] for r in sample]
        boundaries = [start, *stamps, end_epoch]
        pids = {r["pid"] for r in sample if r["pid"] is not None}
        invs = {r["invocation"] for r in sample if r["invocation"] is not None}
        stats = []
        for pair in pairs:
            listed = ack = not_ack = legacy = absent = 0
            for r in sample:
                requested = pair in r["indexed_trade_pairs"]
                active = r.get("indexed_trade_pairs_acknowledged")
                listed += requested
                absent += not requested
                if active is None:
                    legacy += 1
                elif pair in active:
                    ack += 1
                else:
                    not_ack += 1
            stats.append({
                "pair": pair,
                "request_listed_checkpoints": listed,
                "request_not_listed_checkpoints": absent,
                "local_ack_listed_checkpoints": ack,
                "local_ack_not_listed_checkpoints": not_ack,
                "local_ack_unknown_legacy_checkpoints": legacy,
                "all_nonempty_checkpoint_samples_ack_listed": (
                    bool(sample) and ack == len(sample)
                ),
                "continuous_subscription_verified": False,
            })
        windows[label] = {
            "window_start_epoch": start,
            "window_end_epoch": end_epoch,
            "checkpoint_samples": len(sample),
            "new_ack_schema_checkpoints": sum(
                "indexed_trade_pairs_acknowledged" in r for r in sample),
            "legacy_checkpoint_samples": sum(
                "indexed_trade_pairs_acknowledged" not in r for r in sample),
            "first_checkpoint_epoch": stamps[0] if stamps else None,
            "last_checkpoint_epoch": stamps[-1] if stamps else None,
            "max_interval_without_checkpoint_including_boundaries_seconds": max(
                b-a for a,b in zip(boundaries,boundaries[1:])),
            "distinct_observed_pids": len(pids),
            "distinct_observed_invocations": len(invs),
            "checkpoints_missing_pid": sum(r["pid"] is None for r in sample),
            "checkpoints_missing_invocation": sum(r["invocation"] is None for r in sample),
            "multiple_process_or_invocation_ids_observed": len(pids)>1 or len(invs)>1,
            "absence_of_transition_does_not_prove_no_reconnect": True,
            "pairs": stats,
        }
    return _result("LOCAL_ACK_WINDOW_OBSERVATIONS_ONLY",
                   window_end_epoch=end_epoch, windows=windows)


def _journal_read(end_epoch):
    # Request at most one extra record to detect truncation, including
    # non-checkpoint messages. No -f, retries or new sockets.
    cmd = [
        "journalctl", "--user", "-u", _UNIT, "--no-pager", "-o", "json",
        "--output-fields=MESSAGE,_PID,_SYSTEMD_INVOCATION_ID,__REALTIME_TIMESTAMP",
        "--since", "@"+str(end_epoch-3600), "--until", "@"+str(end_epoch),
        "-n", str(_MAX_RECORDS+1),
    ]
    completed = subprocess.run(cmd, capture_output=True, timeout=20, check=True)
    if len(completed.stdout) > _MAX_BYTES:
        raise ValueError("JOURNAL_BYTE_CAP")
    lines = completed.stdout.splitlines()
    if len(lines) > _MAX_RECORDS:
        raise ValueError("JOURNAL_RECORD_CAP")
    return [json.loads(line) for line in lines]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", nargs="+", required=True)
    parser.add_argument("--end-wib")
    args = parser.parse_args(argv)
    now = int(time.time())
    try:
        end = (int(datetime.strptime(args.end_wib, "%Y-%m-%dT%H:%M:%S")
                   .replace(tzinfo=ZoneInfo("Asia/Jakarta")).timestamp())
               if args.end_wib else now//60*60)
        if not _scope(args.pairs, end) or not now-86400 <= end <= now:
            result = _result("ACK_WINDOW_SCOPE_INVALID")
        else:
            result = summarize_local_ack_windows(
                records=_journal_read(end), pairs=args.pairs, end_epoch=end)
    except (OSError, ValueError, UnicodeError, OverflowError, subprocess.SubprocessError):
        result = _result("ACK_WINDOW_JOURNAL_UNAVAILABLE_OR_BOUNDED")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "LOCAL_ACK_WINDOW_OBSERVATIONS_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
