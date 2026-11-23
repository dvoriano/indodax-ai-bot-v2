"""Operator one-shot M04 WATCH capture -> original M09 price outcomes, READ ONLY.

CAPTURE must be taken *at the actual WATCH time*. An as-of query against an
archive is not equivalent: original minute rows can be replaced later.
The operator may redirect the JSON stdout to a private 0600 evidence file.
No code path writes a database, contacts a provider, sends Telegram or orders.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import time

from indodax_ai_bot.m04_radar_three_role_handoff_readonly import (
    inspect_original_ws3_three_role_handoff_readonly,
)
from indodax_ai_bot.m05_radar_momentum_nonlive import DEFAULT_DB
from indodax_ai_bot.m09_ws3_watch_price_outcomes_readonly import (
    inspect_m09_ws3_watch_price_outcomes_readonly,
)

_CAPTURE_STAGE = "M09_ORIGINAL_WS3_WATCH_CAPTURE_READONLY"
_EVALUATION_STAGE = "M09_ORIGINAL_WS3_CAPTURED_PRICE_RESEARCH_READONLY"
_MAX_CAPTURE_BYTES = 512_000


def _blocked(stage: str, reason: str) -> dict:
    return {
        "stage": stage, "status": reason,
        "candidate_count": 0, "paper_fill_created": False,
        "paper_orders_created": 0, "provider_calls": 0,
        "telegram_messages_sent": 0, "order_sent": False,
        "execution_allowed": False,
    }


def _db_id(path: Path) -> str:
    # Identity is an explicit operator-selected path, not a G1 source proof.
    return str(Path(path).expanduser().absolute())


def capture_original_ws3_watch_readonly(
        *, source_db: Path, capture_epoch: int) -> dict:
    """Observe the existing original M04 WATCH once, WITHOUT retro-replay."""
    if type(capture_epoch) is not int or capture_epoch <= 0:
        return _blocked(_CAPTURE_STAGE, "CAPTURE_CLOCK_INVALID")
    report = inspect_original_ws3_three_role_handoff_readonly(
        db_path=source_db, now_epoch=capture_epoch)
    if (report.get("status") != "M04_WS3_HANDOFF_ROLE_INPUTS_PREPARED"
            or report.get("candidate_count") not in (1, 2)
            or report.get("execution_allowed") is not False
            or report.get("g1_independent_issuer_verified") is not False
            or report.get("three_real_role_responses_observed") is not False):
        return _blocked(_CAPTURE_STAGE, "NO_ELIGIBLE_UNCALLED_WATCH")
    rows = report.get("candidates")
    if (type(rows) is not list or len(rows) != report["candidate_count"]
            or any(type(r) is not dict or type(r.get("public_evidence")) is not dict
                   or type(r["public_evidence"].get("observed_epoch")) is not int
                   or not 0 <= capture_epoch - r["public_evidence"]["observed_epoch"] <= 150
                   for r in rows)):
        return _blocked(_CAPTURE_STAGE, "CAPTURE_SOURCE_CLOCK_INVALID")
    return {
        "stage": _CAPTURE_STAGE,
        "status": "LOCAL_UNCALLED_WATCH_CAPTURED",
        "captured_epoch": capture_epoch,
        "source_db_path": _db_id(source_db),
        "handoff": report,
        "candidate_count": len(rows),
        "evidence_is_independently_authenticated": False,
        "paper_fill_created": False,
        "paper_orders_created": 0,
        "provider_calls": 0,
        "telegram_messages_sent": 0,
        "order_sent": False,
        "execution_allowed": False,
    }


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("DUPLICATE_JSON_KEY")
        output[key] = value
    return output


def evaluate_saved_ws3_watch_readonly(
        *, capture_file: Path, source_db: Path, evaluation_epoch: int) -> dict:
    """Evaluate a previously captured WATCH; NEVER reconstruct it from later data."""
    if type(evaluation_epoch) is not int or evaluation_epoch <= 0:
        return _blocked(_EVALUATION_STAGE, "EVALUATION_CLOCK_INVALID")
    path = Path(capture_file).expanduser()
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or info.st_mode & 0o077
                or not 0 < info.st_size <= _MAX_CAPTURE_BYTES):
            return _blocked(_EVALUATION_STAGE, "CAPTURE_FILE_UNTRUSTED")
        with path.open("r", encoding="utf-8") as stream:
            capture = json.load(stream, object_pairs_hook=_no_duplicate_keys)
    except (OSError, UnicodeError, ValueError, TypeError):
        return _blocked(_EVALUATION_STAGE, "CAPTURE_FILE_UNREADABLE")

    if (type(capture) is not dict
            or capture.get("stage") != _CAPTURE_STAGE
            or capture.get("status") != "LOCAL_UNCALLED_WATCH_CAPTURED"
            or capture.get("execution_allowed") is not False
            or capture.get("evidence_is_independently_authenticated") is not False
            or type(capture.get("captured_epoch")) is not int
            or not 0 < capture["captured_epoch"] <= evaluation_epoch
            or capture.get("source_db_path") != _db_id(source_db)
            or capture.get("candidate_count") not in (1, 2)
            or type(capture.get("handoff")) is not dict):
        return _blocked(_EVALUATION_STAGE, "CAPTURE_CONTRACT_INVALID")
    rows = capture["handoff"].get("candidates")
    if (type(rows) is not list or len(rows) != capture["candidate_count"]
            or any(type(r) is not dict or type(r.get("public_evidence")) is not dict
                   or type(r["public_evidence"].get("observed_epoch")) is not int
                   or not 0 <= capture["captured_epoch"] -
                       r["public_evidence"]["observed_epoch"] <= 150
                   for r in rows)):
        return _blocked(_EVALUATION_STAGE, "CAPTURE_SOURCE_CLOCK_INVALID")

    observed = inspect_m09_ws3_watch_price_outcomes_readonly(
        handoff=capture["handoff"], db_path=source_db,
        evaluation_epoch=evaluation_epoch)
    if (observed.get("status") != "LOCAL_PRICE_PATH_RESEARCH_ONLY"
            or observed.get("candidate_count") != capture["candidate_count"]
            or observed.get("paper_fill_authorized") is not False
            or observed.get("execution_allowed") is not False):
        return _blocked(
            _EVALUATION_STAGE,
            "OUTCOME_" + str(observed.get("status", "UNAVAILABLE")))
    return {
        "stage": _EVALUATION_STAGE,
        "status": "LOCAL_CAPTURED_WATCH_PRICE_RESEARCH_ONLY",
        "captured_epoch": capture["captured_epoch"],
        "evaluated_epoch": evaluation_epoch,
        "candidate_count": observed["candidate_count"],
        "observations": observed["observations"],
        "horizons_minutes": observed["horizons_minutes"],
        "source_is_local_partial_observation_only": True,
        "real_ai_role_responses_observed": False,
        "complete_trade_tape_verified": False,
        "independent_g1_verified": False,
        "net_profit_or_fills_inferred": False,
        "paper_fill_created": False,
        "paper_orders_created": 0,
        "provider_calls": 0,
        "telegram_messages_sent": 0,
        "order_sent": False,
        "execution_allowed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Actual current M04 WATCH capture, then read-only M09 outcomes")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--capture-current", action="store_true")
    mode.add_argument("--evaluate-capture", metavar="PRIVATE_0600_JSON")
    parser.add_argument("--source-db", default=str(DEFAULT_DB))
    args = parser.parse_args(argv)
    now = int(time.time())
    result = (capture_original_ws3_watch_readonly(
        source_db=Path(args.source_db), capture_epoch=now)
        if args.capture_current else evaluate_saved_ws3_watch_readonly(
            capture_file=Path(args.evaluate_capture),
            source_db=Path(args.source_db), evaluation_epoch=now))
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return (0 if result["status"] in (
        "LOCAL_UNCALLED_WATCH_CAPTURED",
        "LOCAL_CAPTURED_WATCH_PRICE_RESEARCH_ONLY") else 2)


if __name__ == "__main__":
    raise SystemExit(main())
