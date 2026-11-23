"""Research ONLY: original M04 WATCH's retained local price coverage by capture time.

This uses the *same* validated owner-only M04 capture as original M09,
but only queries source rows whose exchange and recording clocks are no later
than its capture_epoch. It cannot prove the present mutable/upsert SQLite index
is an immutable snapshot of what was available at the historical capture.

No scanner, socket, paid AI, paper fill, trading gate, interpolation, or writes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3
import time

from indodax_ai_bot.m05_radar_momentum_nonlive import DEFAULT_DB
from scripts.m09_ws3_watch_price_history_readonly import (
    evaluate_saved_ws3_watch_readonly,
)

_STAGE = "M04_WS3_RETAINED_PRECAPTURE_PRICE_COVERAGE_READONLY"
_PAIR = re.compile(r"[a-z0-9]+_idr\Z")
_WINDOWS = (5, 15)


def _blocked(reason: str) -> dict:
    return {
        "stage": _STAGE, "status": reason, "candidate_count": 0,
        "observations": [],
        "historical_db_immutability_verified": False,
        "m04_selection_modified": False,
        "role_decisions_observed": 0,
        "independent_g1_verified": False,
        "paper_fill_created": False, "paper_orders_created": 0,
        "provider_calls": 0, "telegram_messages_sent": 0,
        "order_sent": False, "execution_allowed": False,
    }


def inspect_m04_retained_precapture_coverage_readonly(
        *, capture_file: Path, source_db: Path, evaluation_epoch: int) -> dict:
    """Diagnostic using already-retained rows only, never a retrospective BUY vote."""
    # This validator enforces the original M04 digest, uncalled roles,
    # capture privacy and selected DB path. We use its captured handoff,
    # NEVER reconstruct or rerank an old snapshot after seeing future prices.
    validated = evaluate_saved_ws3_watch_readonly(
        capture_file=capture_file, source_db=source_db,
        evaluation_epoch=evaluation_epoch,
    )
    if validated.get("status") != "LOCAL_CAPTURED_WATCH_PRICE_RESEARCH_ONLY":
        return _blocked("CAPTURE_OR_SOURCE_" + str(validated.get("status")))
    cutoff = validated.get("captured_epoch")
    rows = validated.get("observations")
    if (type(cutoff) is not int or type(evaluation_epoch) is not int
            or not 0 < cutoff <= evaluation_epoch
            or type(rows) is not list or len(rows) not in (1, 2)
            or len(rows) != validated.get("candidate_count")):
        return _blocked("VALIDATED_COHORT_INVALID")

    out = {
        "stage": _STAGE,
        "status": "RETAINED_PRECAPTURE_SOURCE_RESEARCH_ONLY",
        "captured_epoch": cutoff,
        "evaluated_epoch": evaluation_epoch,
        "candidate_count": len(rows),
        "observations": [],
        "lookahead_rows_included": False,
        "historical_db_immutability_verified": False,
        "asof_reconstruction_is_complete_proof": False,
        "source_is_local_partial_observation_only": True,
        "no_sample_is_not_no_trade": True,
        "sample_density_is_not_liquidity": True,
        "m04_selection_modified": False,
        "role_decisions_observed": 0,
        "real_ai_role_responses_observed": False,
        "independent_g1_verified": False,
        "paper_fill_created": False,
        "paper_orders_created": 0,
        "provider_calls": 0,
        "telegram_messages_sent": 0,
        "order_sent": False,
        "execution_allowed": False,
    }
    try:
        with sqlite3.connect(
            Path(source_db).resolve().as_uri() + "?mode=ro",
            uri=True, timeout=2,
        ) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            for item in rows:
                pair = item["pair"]
                digest = item["evidence_digest_sha256"]
                if (type(pair) is not str or not _PAIR.fullmatch(pair)
                        or type(digest) is not str or
                        not re.fullmatch(r"[0-9a-f]{64}", digest)):
                    return _blocked("VALIDATED_PAIR_INVALID")
                by_window = {}
                for minutes in _WINDOWS:
                    start = cutoff - minutes * 60
                    # recorded<=cutoff blocks direct lookahead. Upserted
                    # historical rows can still have been overwritten later;
                    # hence retained rows are not an immutable as-of record.
                    found = db.execute("""
                        SELECT exchange_epoch,recorded_epoch
                        FROM m03_ws3_price_samples_5s
                        WHERE pair=?
                          AND exchange_epoch BETWEEN ? AND ?
                          AND recorded_epoch BETWEEN exchange_epoch AND ?
                        ORDER BY exchange_epoch ASC
                    """, (pair, start, cutoff, cutoff)).fetchall()
                    epochs = [r[0] for r in found]
                    receipts = [r[1] for r in found]
                    if any(type(a) is not int or type(b) is not int
                           or not start <= a <= b <= cutoff
                           for a, b in found):
                        return _blocked("SOURCE_TIMESTAMP_INVALID")
                    gaps = [b-a for a,b in zip(epochs,epochs[1:])]
                    by_window[str(minutes) + "m"] = {
                        "window_start_epoch": start,
                        "window_end_epoch": cutoff,
                        "retained_rows_by_capture_cutoff": len(epochs),
                        "oldest_exchange_epoch": epochs[0] if epochs else None,
                        "newest_exchange_epoch": epochs[-1] if epochs else None,
                        "latest_exchange_age_seconds":
                            cutoff-epochs[-1] if epochs else None,
                        "largest_observed_inter_sample_gap_seconds":
                            max(gaps) if gaps else None,
                        "max_observed_receipt_lag_seconds":
                            max((b-a for a,b in found), default=None),
                        "status": ("RETAINED_LOCAL_PRICE_ROWS_PRESENT"
                                   if epochs else "NO_RETAINED_LOCAL_PRICE_ROWS"),
                        "qualified_to_trade": False,
                    }
                out["observations"].append({
                    "pair": pair,
                    "m04_evidence_digest_sha256": digest,
                    "m04_original_source_observed_epoch": item["source_observed_epoch"],
                    "m04_source_observation_age_at_capture_seconds":
                        cutoff - item["source_observed_epoch"],
                    "m04_role_review_status": "UNCALLED_WAIT_ONLY",
                    "pre_capture_retained_windows": by_window,
                    "price_coverage_decision_gate": False,
                })
    except (sqlite3.Error, OSError, KeyError, ValueError, TypeError):
        return _blocked("LOCAL_SOURCE_UNAVAILABLE_OR_INVALID")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="M04 captured WATCH retrospective retained pre-capture prices only")
    parser.add_argument("--audit-capture", required=True, metavar="PRIVATE_0600_JSON")
    parser.add_argument("--source-db", default=str(DEFAULT_DB))
    args = parser.parse_args(argv)
    report = inspect_m04_retained_precapture_coverage_readonly(
        capture_file=Path(args.audit_capture), source_db=Path(args.source_db),
        evaluation_epoch=int(time.time()),
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return 0 if report["status"] == "RETAINED_PRECAPTURE_SOURCE_RESEARCH_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
