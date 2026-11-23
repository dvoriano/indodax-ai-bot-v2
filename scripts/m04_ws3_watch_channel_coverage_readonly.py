"""One-shot original M04 WATCH -> M03 trade-channel coverage evidence, READ ONLY.

No scanner, sockets, REST, provider, Telegram, DB writes, M04 reranking,
execution or service action. Current WATCH is captured only from the existing
original M04 handoff. Historical source is explicitly *retrospective*, never
faked as immutable at original capture time. A checkpoint membership means a
subscription REQUEST appeared, not pair-specific ACK or full trade tape.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
import subprocess
import time

from indodax_ai_bot.m05_radar_momentum_nonlive import DEFAULT_DB
from scripts.m09_ws3_watch_price_history_readonly import (
    capture_original_ws3_watch_readonly,
    evaluate_saved_ws3_watch_readonly,
)

_STAGE = "M04_ORIGINAL_WATCH_TRADE_CHANNEL_COVERAGE_READONLY"
_PAIR = re.compile(r"[a-z0-9]+_idr\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_CHECKPOINT_AGE = 120
_MAX_JOURNAL_BYTES = 4_000_000
_MAX_CHECKPOINTS = 200


def _blocked(reason: str) -> dict:
    return {
        "stage": _STAGE, "status": reason,
        "candidate_count": 0, "observations": [],
        "pair_specific_ack_verified": False,
        "independent_g1_verified": False,
        "m04_selection_modified": False,
        "paper_fill_created": False, "paper_orders_created": 0,
        "provider_calls": 0, "telegram_messages_sent": 0,
        "order_sent": False, "execution_allowed": False,
    }


def _journal_checkpoints(capture_epoch: int) -> tuple[list[dict], str]:
    """Bounded local journal read. Only distilled JSON enters result, not logs."""
    cmd = [
        "journalctl", "--user", "-u", "indodax-v2-m03-mainpath.service",
        "--since", "@" + str(capture_epoch - _CHECKPOINT_AGE),
        "--until", "@" + str(capture_epoch),
        "-o", "cat", "--no-pager",
    ]
    try:
        done = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=15,
        )
        if len(done.stdout) > _MAX_JOURNAL_BYTES:
            return [], "JOURNAL_OUTPUT_TOO_LARGE"
    except (OSError, ValueError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired):
        return [], "JOURNAL_READ_UNAVAILABLE"
    rows = []
    for line in done.stdout.splitlines():
        if len(line) > 32768:
            continue
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        if (type(record) is dict
                and record.get("status") == "M03_WS3_CONTINUOUS_CHECKPOINT"):
            rows.append(record)
            if len(rows) > _MAX_CHECKPOINTS:
                return [], "JOURNAL_CHECKPOINT_LIMIT_EXCEEDED"
    return rows, "JOURNAL_READ_OK"


def _validated_checkpoints(
        rows: list[dict], *, capture_epoch: int) -> list[dict]:
    if type(rows) is not list or len(rows) > _MAX_CHECKPOINTS:
        raise ValueError("CHECKPOINT_INPUT_INVALID")
    by_epoch = {}
    for record in rows:
        if type(record) is not dict:
            continue
        epoch = record.get("observed_epoch")
        pairs = record.get("indexed_trade_pairs")
        if (record.get("status") != "M03_WS3_CONTINUOUS_CHECKPOINT"
                or type(epoch) is not int
                or not capture_epoch - _CHECKPOINT_AGE <= epoch <= capture_epoch
                or type(pairs) is not list or len(pairs) > 40
                or any(type(p) is not str or not _PAIR.fullmatch(p)
                       for p in pairs)
                or len(pairs) != len(set(pairs))):
            continue
        # Duplicate timestamp with conflicting membership cannot prove state.
        if epoch in by_epoch and by_epoch[epoch] != pairs:
            raise ValueError("CHECKPOINT_TIMESTAMP_CONFLICT")
        by_epoch[epoch] = pairs
    return [
        {"observed_epoch": epoch, "indexed_trade_pairs": pairs}
        for epoch, pairs in sorted(by_epoch.items())
    ]


def _seeds_from_current_capture(source_db: Path, epoch: int) -> tuple[dict, list]:
    original = capture_original_ws3_watch_readonly(
        source_db=source_db, capture_epoch=epoch)
    if original.get("status") != "LOCAL_UNCALLED_WATCH_CAPTURED":
        return original, []
    rows = original["handoff"]["candidates"]
    return original, [
        (r["pair"], r["evidence_digest_sha256"],
         r["public_evidence"]["observed_epoch"])
        for r in rows
    ]


def _seeds_from_saved_capture(
        source_db: Path, capture_file: Path, epoch: int) -> tuple[dict, list]:
    original = evaluate_saved_ws3_watch_readonly(
        capture_file=capture_file, source_db=source_db,
        evaluation_epoch=epoch)
    if original.get("status") != "LOCAL_CAPTURED_WATCH_PRICE_RESEARCH_ONLY":
        return original, []
    return original, [
        (r["pair"], r["evidence_digest_sha256"], r["source_observed_epoch"])
        for r in original["observations"]
    ]


def inspect_m04_watch_trade_channels_readonly(
        *, source_db: Path, evaluation_epoch: int,
        capture_file: Path | None = None,
        checkpoint_records: list[dict] | None = None) -> dict:
    """Observe current original WATCH or retrospectively inspect saved capture.

    Explicitly qualify any later-read mutable SQLite rows and archived journal
    records as research ONLY, not independently authenticated historical
    coverage, G1 or a verified per-pair subscription ACK.
    """
    if type(evaluation_epoch) is not int or evaluation_epoch <= 0:
        return _blocked("EVALUATION_CLOCK_INVALID")
    current = capture_file is None
    try:
        if current:
            capture, seeds = _seeds_from_current_capture(
                source_db, evaluation_epoch)
            cutoff = evaluation_epoch
            valid_status = "LOCAL_UNCALLED_WATCH_CAPTURED"
        else:
            capture, seeds = _seeds_from_saved_capture(
                source_db, capture_file, evaluation_epoch)
            cutoff = capture.get("captured_epoch")
            valid_status = "LOCAL_CAPTURED_WATCH_PRICE_RESEARCH_ONLY"
        if capture.get("status") != valid_status:
            return _blocked("ORIGINAL_M04_" + str(capture.get("status")))
        if (type(cutoff) is not int or not 0 < cutoff <= evaluation_epoch
                or len(seeds) not in (1, 2)
                or len(seeds) != capture["candidate_count"]
                or any(type(p) is not str or not _PAIR.fullmatch(p)
                       or type(d) is not str or not _DIGEST.fullmatch(d)
                       or type(observed) is not int
                       or not 0 <= cutoff - observed <= 150
                       for p, d, observed in seeds)):
            return _blocked("ORIGINAL_CAPTURE_INVALID")
        if checkpoint_records is None:
            raw_checkpoints, journal_status = _journal_checkpoints(cutoff)
        else:
            raw_checkpoints, journal_status = (
                checkpoint_records, "OPERATOR_SUPPLIED_CHECKPOINT_FIXTURE")
        checkpoints = _validated_checkpoints(
            raw_checkpoints, capture_epoch=cutoff)
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
        return _blocked("ORIGINAL_OR_CHECKPOINT_INVALID")

    newest = checkpoints[-1] if checkpoints else None
    result = {
        "stage": _STAGE,
        "status": "M04_WATCH_TRADE_CHANNEL_COVERAGE_RESEARCH_ONLY",
        "captured_epoch": cutoff,
        "evaluated_epoch": evaluation_epoch,
        "capture_mode": ("CURRENT_OPERATOR_ONE_SHOT" if current
                         else "SAVED_CAPTURE_RETROSPECTIVE"),
        "capture_and_source_db_read_are_atomic": False,
        "historical_source_immutability_verified": False,
        "journal_status": journal_status,
        "checkpoint_count": len(checkpoints),
        "latest_checkpoint_epoch":
            newest["observed_epoch"] if newest else None,
        "latest_checkpoint_age_seconds":
            cutoff - newest["observed_epoch"] if newest else None,
        "checkpoint_membership_is_only_subscription_request_evidence": True,
        "checkpoint_not_listed_is_not_proof_of_exchange_inactivity": True,
        "pair_specific_ack_verified": False,
        "no_pair_specific_ack_timestamp_in_existing_checkpoint": True,
        "trade_index_not_complete_exchange_tape": True,
        "independent_g1_verified": False,
        "m04_selection_modified": False,
        "real_ai_role_responses_observed": False,
        "candidate_count": len(seeds),
        "observations": [],
        "paper_fill_created": False,
        "paper_orders_created": 0,
        "provider_calls": 0,
        "telegram_messages_sent": 0,
        "order_sent": False,
        "execution_allowed": False,
    }
    try:
        p = Path(source_db).absolute()
        if p.is_symlink() or not p.is_file() or p.stat().st_mode & 0o077:
            return _blocked("SOURCE_DB_FILE_UNTRUSTED")
        with sqlite3.connect(p.resolve().as_uri() + "?mode=ro",
                             uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            tables = {
                r[0] for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "m03_ws3_trade_samples" not in tables:
                return _blocked("TRADE_INDEX_NOT_AVAILABLE")
            for pair, digest, source_epoch in seeds:
                membership = (
                    pair in newest["indexed_trade_pairs"] if newest else None)
                ranges = {}
                for minutes in (1, 15, 60):
                    start = cutoff - minutes * 60
                    count, first, last, most_recent_receipt = db.execute("""
                        SELECT COUNT(*),MIN(exchange_epoch),
                               MAX(exchange_epoch),MAX(recorded_epoch)
                        FROM m03_ws3_trade_samples
                        WHERE pair=?
                          AND exchange_epoch BETWEEN ? AND ?
                          AND recorded_epoch BETWEEN exchange_epoch AND ?
                    """, (pair, start, cutoff, cutoff)).fetchone()
                    ranges[str(minutes)+"m"] = {
                        "retained_local_trade_rows_by_capture_cutoff": count,
                        "first_exchange_epoch": first,
                        "last_exchange_epoch": last,
                        "last_receipt_epoch": most_recent_receipt,
                        "local_rows_are_not_complete_trade_tape_proof": True,
                    }
                publication_count = None
                if "m03_ws3_trade_publication_times" in tables:
                    publication_count = db.execute("""
                        SELECT COUNT(*)
                        FROM m03_ws3_trade_publication_times
                        WHERE pair=? AND recorded_epoch BETWEEN ? AND ?
                    """, (pair, cutoff - 3600, cutoff)).fetchone()[0]
                discontinuity_count = None
                if "ws_stream_discontinuities" in tables:
                    discontinuity_count = db.execute("""
                        SELECT COUNT(*)
                        FROM ws_stream_discontinuities
                        WHERE pair=? AND observed_at_epoch BETWEEN ? AND ?
                    """, (pair, cutoff - 3600, cutoff)).fetchone()[0]
                cursor_status = None
                if current and "ws_stream_state" in tables:
                    state = db.execute("""
                        SELECT status FROM ws_stream_state WHERE pair=?
                    """, (pair,)).fetchone()
                    cursor_status = state[0] if state is not None else None
                result["observations"].append({
                    "pair": pair,
                    "m04_original_evidence_digest_sha256": digest,
                    "m04_source_observed_epoch": source_epoch,
                    "checkpoint_request_membership": membership,
                    "checkpoint_request_evidence_status": (
                        "REQUEST_LISTED_IN_LATEST_RECENT_CHECKPOINT"
                        if membership is True else
                        "NOT_LISTED_IN_LATEST_RECENT_CHECKPOINT"
                        if membership is False else
                        "RECENT_CHECKPOINT_UNAVAILABLE"),
                    "pair_specific_ack_evidence": "NOT_AVAILABLE_IN_CHECKPOINT",
                    "current_cursor_status_not_timestamped_ack":
                        cursor_status,
                    "cursor_status_proves_active_subscription": False,
                    "source_local_retained_trade_windows": ranges,
                    "local_trade_publications_60m_by_receipt": publication_count,
                    "recorded_discontinuities_60m": discontinuity_count,
                    "zero_rows_do_not_prove_zero_exchange_trades": True,
                    "qualified_for_g1_or_market_decision": False,
                })
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return _blocked("LOCAL_SOURCE_QUERY_UNAVAILABLE")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Original M04 WATCH vs M03 channel checkpoint evidence, read-only")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--capture-current", action="store_true")
    mode.add_argument("--audit-capture", metavar="PRIVATE_ORIGINAL_M04_JSON")
    parser.add_argument("--source-db", default=str(DEFAULT_DB))
    args = parser.parse_args(argv)
    report = inspect_m04_watch_trade_channels_readonly(
        source_db=Path(args.source_db), evaluation_epoch=int(time.time()),
        capture_file=Path(args.audit_capture) if args.audit_capture else None)
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return 0 if report["status"] == (
        "M04_WATCH_TRADE_CHANNEL_COVERAGE_RESEARCH_ONLY") else 2


if __name__ == "__main__":
    raise SystemExit(main())
