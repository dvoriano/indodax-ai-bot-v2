"""M09 original captured WATCH price coverage, READ ONLY and NON-EXECUTABLE.

Audit the *same* private natural M04 capture validated by the existing M09
price evaluator. Count local WS3 receipts around each 5/15/30/60-minute
horizon without assigning an UNKNOWN observation a zero/negative return.

This is observational source coverage, not a cause-of-gap diagnosis,
MARKET/RISK/STRATEGY verdict, G1 attestation, PAPER fill or order permission.
No network, provider, Telegram, DB writes, new scanner, or service scheduling.
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

_STAGE = "M09_ORIGINAL_WS3_CAPTURE_COVERAGE_AUDIT_READONLY"
_PAIR = re.compile(r"[a-z0-9]+_idr\Z")
_HORIZONS = ("5m", "15m", "30m", "60m")
_WINDOW_SECONDS = 30
_NEARBY_SECONDS = 120


def _blocked(reason: str) -> dict:
    return {
        "stage": _STAGE, "status": reason,
        "candidate_count": 0, "observations": [],
        "matured_horizons": 0, "observed_matured_horizons": 0,
        "unknown_matured_horizons": 0, "observed_coverage_percent": None,
        "m04_qualified_role_decisions": 0,
        "independent_g1_verified": False,
        "paper_fill_created": False, "paper_orders_created": 0,
        "provider_calls": 0, "telegram_messages_sent": 0,
        "order_sent": False, "execution_allowed": False,
    }


def audit_saved_ws3_watch_coverage_readonly(
        *, capture_file: Path, source_db: Path, evaluation_epoch: int) -> dict:
    """Retrospective evidence-matched source diagnostic, never selection.

    The original evaluator alone validates capture file, source path, M04
    digest and uncalled role slots. We refuse to audit an invalid input.
    Read at a single requested epoch: never count later recorded receipts.
    """
    evaluated = evaluate_saved_ws3_watch_readonly(
        capture_file=capture_file, source_db=source_db,
        evaluation_epoch=evaluation_epoch,
    )
    if evaluated.get("status") != "LOCAL_CAPTURED_WATCH_PRICE_RESEARCH_ONLY":
        return _blocked("CAPTURE_OR_OUTCOMES_" + str(evaluated.get("status")))
    observations = evaluated.get("observations")
    if (type(observations) is not list
            or len(observations) not in (1, 2)
            or len(observations) != evaluated.get("candidate_count")
            or type(evaluation_epoch) is not int
            or evaluation_epoch <= 0):
        return _blocked("EVALUATED_COHORT_INVALID")

    result = {
        "stage": _STAGE,
        "status": "LOCAL_CAPTURE_COVERAGE_RESEARCH_ONLY",
        "captured_epoch": evaluated["captured_epoch"],
        "evaluated_epoch": evaluation_epoch,
        "candidate_count": len(observations),
        "horizons_minutes": [5, 15, 30, 60],
        "observations": [],
        "matured_horizons": 0,
        "observed_matured_horizons": 0,
        "unknown_matured_horizons": 0,
        "observed_coverage_percent": None,
        "m04_qualified_role_decisions": 0,
        "real_ai_role_responses_observed": False,
        "independent_g1_verified": False,
        "complete_trade_tape_verified": False,
        "coverage_does_not_diagnose_collector_or_pair_liquidity": True,
        "missing_is_not_zero_or_negative_return": True,
        "gross_price_is_not_executable_net_roi": True,
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
            if db.execute("""SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='m03_ws3_price_samples_5s'"""
            ).fetchone() is None:
                return _blocked("SOURCE_PRICE_INDEX_MISSING")
            for item in observations:
                pair = item["pair"]
                digest = item["evidence_digest_sha256"]
                targets = item["observed_outcomes"]
                if (type(pair) is not str or not _PAIR.fullmatch(pair)
                        or type(digest) is not str or len(digest) != 64
                        or type(targets) is not dict):
                    return _blocked("EVALUATED_PAIR_INVALID")
                windows = {}
                for horizon in _HORIZONS:
                    point = targets[horizon]
                    target = point["target_epoch"]
                    state = point["status"]
                    if (type(target) is not int or target <= 0
                            or type(state) is not str):
                        return _blocked("EVALUATED_WINDOW_INVALID")

                    # A source sample must have existed by evaluation time.
                    # Exact-window conditions mirror the original M09 evaluator.
                    exact = db.execute("""
                        SELECT COUNT(*) FROM m03_ws3_price_samples_5s
                        WHERE pair=? AND exchange_epoch BETWEEN ? AND ?
                          AND recorded_epoch BETWEEN ? AND ?
                    """, (
                        pair, target, min(target + _WINDOW_SECONDS, evaluation_epoch),
                        target, evaluation_epoch,
                    )).fetchone()[0]
                    nearby = db.execute("""
                        SELECT COUNT(*) FROM m03_ws3_price_samples_5s
                        WHERE pair=? AND exchange_epoch BETWEEN ? AND ?
                          AND exchange_epoch<=?
                          AND recorded_epoch<=?
                          AND recorded_epoch>=exchange_epoch
                    """, (
                        pair, target - _NEARBY_SECONDS,
                        target + _NEARBY_SECONDS,
                        evaluation_epoch, evaluation_epoch,
                    )).fetchone()[0]

                    observed = state == "OBSERVED_LOCAL_GROSS_PRICE_CHANGE_ONLY"
                    mature = evaluation_epoch >= target + _WINDOW_SECONDS
                    if observed:
                        classification = "OBSERVED_ON_TARGET_PRICE"
                    elif evaluation_epoch < target:
                        classification = "NOT_YET_MATURED"
                    elif not mature:
                        classification = "HORIZON_30S_WINDOW_STILL_OPEN"
                    elif state == "UNKNOWN_INVALID_OBSERVED_PRICE":
                        classification = "INVALID_ON_TARGET_PRICE"
                    elif nearby:
                        classification = "NO_ON_TARGET_WITH_NEARBY_LOCAL_SAMPLES"
                    else:
                        classification = "NO_LOCAL_PRICE_WITHIN_120S"

                    if mature:
                        result["matured_horizons"] += 1
                        if observed:
                            result["observed_matured_horizons"] += 1
                        else:
                            result["unknown_matured_horizons"] += 1
                    windows[horizon] = {
                        "target_epoch": target,
                        "exchange_time_window_closed": mature,
                        "outcome_status": state,
                        "coverage_class": classification,
                        "exact_horizon_receipts": exact,
                        "nearby_120s_local_receipts": nearby,
                        "observed_gross_price_return_percent":
                            point["observed_gross_price_return_percent"],
                        "market_fills_or_net_roi_inferred": False,
                    }
                result["observations"].append({
                    "pair": pair,
                    "m04_evidence_digest_sha256": digest,
                    "source_observed_epoch": item["source_observed_epoch"],
                    "source_price_idr": item["source_price_idr"],
                    "m04_role_review_status": "UNCALLED_WAIT_ONLY",
                    "source_coverage_only_no_trade_decision": True,
                    "coverage": windows,
                })
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError):
        return _blocked("SOURCE_COVERAGE_READ_UNAVAILABLE")

    matured = result["matured_horizons"]
    if matured:
        result["observed_coverage_percent"] = (
            str(round(100 * result["observed_matured_horizons"] / matured, 2))
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Actual captured M04 WATCH: local WS3 coverage 5/15/30/60m, read only")
    parser.add_argument("--audit-capture", required=True, metavar="PRIVATE_0600_JSON")
    parser.add_argument("--source-db", default=str(DEFAULT_DB))
    args = parser.parse_args(argv)
    report = audit_saved_ws3_watch_coverage_readonly(
        capture_file=Path(args.audit_capture), source_db=Path(args.source_db),
        evaluation_epoch=int(time.time()),
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return 0 if report["status"] == "LOCAL_CAPTURE_COVERAGE_RESEARCH_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
