"""Aggregate several *genuine current* M04 WATCH channel reports, READ ONLY.

Inputs come ONLY from operator-saved --capture-current output of
scripts.m04_ws3_watch_channel_coverage_readonly. This code does not generate
WATCHs, replay M04 selection, query SQLite/journal, access the network, send
Telegram, call AI, write files, or issue orders.

Different current observation timestamps alone do not prove independent
market cohorts. Deduplicate by the original M04 pair/evidence SHA256,
retain request-membership and local trade counts as distinct properties,
and never label absence of evidence as no exchange activity.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

_STAGE = "M04_WS3_CURRENT_WATCH_MULTICOHORT_COVERAGE_READONLY"
_INPUT_STAGE = "M04_ORIGINAL_WATCH_TRADE_CHANNEL_COVERAGE_READONLY"
_INPUT_STATUS = "M04_WATCH_TRADE_CHANNEL_COVERAGE_RESEARCH_ONLY"
_PAIR = re.compile(r"[a-z0-9]+_idr\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MAX_FILES = 32
_MAX_FILE_SIZE = 262144
_MIN_EPOCH = 1500000000
_MIN_DISTINCT_PACKETS = 3
_JOURNAL_OK = frozenset({
    "JOURNAL_READ_OK",
    "JOURNAL_OUTPUT_TOO_LARGE",
    "JOURNAL_READ_UNAVAILABLE",
    "JOURNAL_CHECKPOINT_LIMIT_EXCEEDED",
})
_MEMBERSHIPS = {
    True: "REQUEST_LISTED_IN_LATEST_RECENT_CHECKPOINT",
    False: "NOT_LISTED_IN_LATEST_RECENT_CHECKPOINT",
    None: "RECENT_CHECKPOINT_UNAVAILABLE",
}


def _blocked(reason: str) -> dict:
    return {
        "stage": _STAGE,
        "status": "M04_MULTICOHORT_INPUT_BLOCKED",
        "reason": reason,
        "accepted_input_reports": 0,
        "distinct_watch_packet_fingerprints": 0,
        "distinct_candidate_evidence": 0,
        "pair_summaries": [],
        "complete_trade_tape_verified": False,
        "pair_specific_ack_verified": False,
        "independent_g1_verified": False,
        "execution_allowed": False,
        "provider_calls": 0,
        "telegram_messages_sent": 0,
        "paper_fill_created": False,
        "paper_orders_created": 0,
    }


def _no_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_JSON_PROPERTY")
        result[key] = value
    return result


def _read_private_report(path: Path) -> dict:
    p = Path(path).expanduser().absolute()
    f = p.lstat()
    parent = p.parent.stat()
    if (not stat.S_ISREG(f.st_mode)
            or f.st_uid != os.geteuid()
            or f.st_nlink != 1
            or stat.S_IMODE(f.st_mode) & 0o077
            or not 0 < f.st_size <= _MAX_FILE_SIZE
            or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) & 0o022):
        raise ValueError("EVIDENCE_FILE_NOT_PRIVATE_OR_BOUNDED")
    with p.open("r", encoding="utf-8") as stream:
        report = json.load(
            stream, object_pairs_hook=_no_duplicate_json_keys,
            parse_constant=lambda _raw: (_ for _ in ()).throw(
                ValueError("NONFINITE_JSON_NUMBER")),
        )
    if type(report) is not dict:
        raise ValueError("EVIDENCE_REPORT_NOT_OBJECT")
    return report


def _natural_nonnegative(raw: object, *, allow_none: bool = False) -> bool:
    return (raw is None and allow_none) or (type(raw) is int and 0 <= raw <= 1000000)


def _validate_report(r: dict) -> tuple[int, tuple, list[dict]]:
    if (r.get("stage") != _INPUT_STAGE
            or r.get("status") != _INPUT_STATUS
            or r.get("capture_mode") != "CURRENT_OPERATOR_ONE_SHOT"
            or r.get("capture_and_source_db_read_are_atomic") is not False
            or r.get("historical_source_immutability_verified") is not False
            or r.get("pair_specific_ack_verified") is not False
            or r.get("checkpoint_membership_is_only_subscription_request_evidence") is not True
            or r.get("checkpoint_not_listed_is_not_proof_of_exchange_inactivity") is not True
            or r.get("no_pair_specific_ack_timestamp_in_existing_checkpoint") is not True
            or r.get("trade_index_not_complete_exchange_tape") is not True
            or r.get("independent_g1_verified") is not False
            or r.get("m04_selection_modified") is not False
            or r.get("real_ai_role_responses_observed") is not False
            or r.get("paper_fill_created") is not False
            or r.get("paper_orders_created") != 0
            or r.get("provider_calls") != 0
            or r.get("telegram_messages_sent") != 0
            or r.get("order_sent") is not False
            or r.get("execution_allowed") is not False):
        raise ValueError("EVIDENCE_SOURCE_OR_AUTHORITY_INVALID")

    epoch = r.get("captured_epoch")
    evaluated = r.get("evaluated_epoch")
    checkpoint_count = r.get("checkpoint_count")
    if (type(epoch) is not int or epoch < _MIN_EPOCH
            or type(evaluated) is not int or epoch != evaluated
            or r.get("journal_status") not in _JOURNAL_OK
            or type(checkpoint_count) is not int
            or not 0 <= checkpoint_count <= 200):
        raise ValueError("EVIDENCE_CAPTURE_OR_CHECKPOINT_INVALID")
    latest = r.get("latest_checkpoint_epoch")
    age = r.get("latest_checkpoint_age_seconds")
    if checkpoint_count:
        if (r.get("journal_status") != "JOURNAL_READ_OK"
                or type(latest) is not int
                or type(age) is not int
                or not 0 <= age <= 120
                or epoch - latest != age):
            raise ValueError("EVIDENCE_CHECKPOINT_TIMESTAMP_INVALID")
    elif latest is not None or age is not None:
        raise ValueError("EVIDENCE_MISSING_CHECKPOINT_CONFLICT")

    candidates = r.get("observations")
    candidate_count = r.get("candidate_count")
    if (type(candidate_count) is not int
            or candidate_count not in (1, 2)
            or type(candidates) is not list
            or len(candidates) != candidate_count):
        raise ValueError("EVIDENCE_COHORT_INVALID")
    sanitized = []
    for row in candidates:
        if type(row) is not dict:
            raise ValueError("EVIDENCE_CANDIDATE_INVALID")
        pair = row.get("pair")
        digest = row.get("m04_original_evidence_digest_sha256")
        source_epoch = row.get("m04_source_observed_epoch")
        membership = row.get("checkpoint_request_membership", "MISSING")
        windows = row.get("source_local_retained_trade_windows")
        if (type(pair) is not str or not _PAIR.fullmatch(pair)
                or type(digest) is not str or not _SHA.fullmatch(digest)
                or type(source_epoch) is not int
                or not 0 <= epoch - source_epoch <= 150
                or membership not in _MEMBERSHIPS
                or row.get("checkpoint_request_evidence_status") !=
                   _MEMBERSHIPS[membership]
                or ((membership is None) != (checkpoint_count == 0))
                or row.get("pair_specific_ack_evidence") != "NOT_AVAILABLE_IN_CHECKPOINT"
                or row.get("cursor_status_proves_active_subscription") is not False
                or row.get("zero_rows_do_not_prove_zero_exchange_trades") is not True
                or row.get("qualified_for_g1_or_market_decision") is not False
                or type(windows) is not dict
                or set(windows) != {"1m", "15m", "60m"}):
            raise ValueError("EVIDENCE_CANDIDATE_PROVENANCE_INVALID")
        counts = []
        for h in ("1m", "15m", "60m"):
            w = windows[h]
            if (type(w) is not dict or
                    not _natural_nonnegative(
                        w.get("retained_local_trade_rows_by_capture_cutoff"))
                    or w.get("local_rows_are_not_complete_trade_tape_proof") is not True):
                raise ValueError("EVIDENCE_LOCAL_TRADE_COUNT_INVALID")
            counts.append(w["retained_local_trade_rows_by_capture_cutoff"])
        if counts != sorted(counts):
            raise ValueError("EVIDENCE_WINDOW_COUNTS_INCONSISTENT")
        publications = row.get("local_trade_publications_60m_by_receipt")
        gaps = row.get("recorded_discontinuities_60m")
        if (not _natural_nonnegative(publications, allow_none=True)
                or not _natural_nonnegative(gaps, allow_none=True)):
            raise ValueError("EVIDENCE_PUBLICATION_OR_GAP_INVALID")
        sanitized.append({
            "pair": pair, "digest": digest,
            "request_membership": membership,
            "local_trades_1m": counts[0],
            "local_trades_15m": counts[1],
            "local_trades_60m": counts[2],
            "publications_60m": publications,
            "recorded_discontinuities_60m": gaps,
        })
    if len({x["pair"] for x in sanitized}) != len(sanitized):
        raise ValueError("EVIDENCE_DUPLICATE_PAIR")
    packet_key = tuple(sorted((x["pair"], x["digest"]) for x in sanitized))
    return epoch, packet_key, sanitized


def summarize_original_current_watch_reports_readonly(
        *, evidence_files: list[Path]) -> dict:
    """Strictly metadata-only aggregate; source reports remain untouched."""
    if (type(evidence_files) is not list
            or not 1 <= len(evidence_files) <= _MAX_FILES):
        return _blocked("EVIDENCE_INPUT_FILE_COUNT_INVALID")
    checked = []
    try:
        seen_paths = set()
        for path in evidence_files:
            if not isinstance(path, Path):
                return _blocked("EVIDENCE_PATH_INVALID")
            resolved = str(path.expanduser().absolute())
            if resolved in seen_paths:
                return _blocked("DUPLICATE_EVIDENCE_PATH")
            seen_paths.add(resolved)
            epoch, key, candidates = _validate_report(_read_private_report(path))
            checked.append((epoch, key, candidates))
    except (OSError, ValueError, TypeError, UnicodeError,
            json.JSONDecodeError):
        return _blocked("EVIDENCE_FILE_OR_CONTRACT_INVALID")
    checked.sort(key=lambda x: (x[0], x[1]))

    seen_packets: set[tuple] = set()
    seen_candidate_keys: set[tuple] = set()
    duplicates_packets = 0
    duplicates_candidates = 0
    stats = defaultdict(lambda: {
        "distinct_candidate_evidence": 0,
        "request_listed": 0,
        "not_listed_latest_checkpoint": 0,
        "checkpoint_unavailable": 0,
        "with_retained_local_trades_1m": 0,
        "with_retained_local_trades_15m": 0,
        "with_retained_local_trades_60m": 0,
        "without_retained_local_trades_60m": 0,
        "latest_observed_capture_epoch": None,
    })
    for epoch, packet_key, rows in checked:
        if packet_key in seen_packets:
            duplicates_packets += 1
            duplicates_candidates += len(rows)
            continue
        seen_packets.add(packet_key)
        for row in rows:
            identity = row["pair"], row["digest"]
            if identity in seen_candidate_keys:
                duplicates_candidates += 1
                continue
            seen_candidate_keys.add(identity)
            counts = stats[row["pair"]]
            counts["distinct_candidate_evidence"] += 1
            counts["latest_observed_capture_epoch"] = epoch
            membership = row["request_membership"]
            if membership is True:
                counts["request_listed"] += 1
            elif membership is False:
                counts["not_listed_latest_checkpoint"] += 1
            else:
                counts["checkpoint_unavailable"] += 1
            for period in (1, 15, 60):
                if row["local_trades_" + str(period) + "m"]:
                    counts["with_retained_local_trades_" + str(period) + "m"] += 1
            if row["local_trades_60m"] == 0:
                counts["without_retained_local_trades_60m"] += 1

    summaries = [
        {"pair": pair, **stats[pair]}
        for pair in sorted(stats)
    ]
    distinct = len(seen_packets)
    return {
        "stage": _STAGE,
        "status": "M04_CURRENT_WATCH_MULTICOHORT_RESEARCH_ONLY",
        "accepted_input_reports": len(checked),
        "distinct_watch_packet_fingerprints": distinct,
        "distinct_candidate_evidence": len(seen_candidate_keys),
        "duplicate_watch_packet_reports_ignored": duplicates_packets,
        "duplicate_candidate_evidence_ignored": duplicates_candidates,
        "minimum_distinct_watch_packet_fingerprints_for_pattern": _MIN_DISTINCT_PACKETS,
        "enough_distinct_packet_fingerprints_for_repeat_pattern":
            distinct >= _MIN_DISTINCT_PACKETS,
        "distinct_packet_hash_is_not_independently_verified_market_cohort": True,
        "pair_summaries": summaries,
        "genuine_watch_selection_reverified_from_raw_source": False,
        "input_reports_are_owner_saved_local_research_not_immutable_proof": True,
        "unlisted_checkpoint_is_not_rejected_trade_subscription_proof": True,
        "zero_trades_is_not_zero_exchange_activity": True,
        "ranking_cause_of_missed_trade_channel_verified": False,
        "pair_specific_ack_verified": False,
        "complete_trade_tape_verified": False,
        "independent_g1_verified": False,
        "m04_selection_modified": False,
        "provider_calls": 0,
        "telegram_messages_sent": 0,
        "paper_fill_created": False,
        "paper_orders_created": 0,
        "order_sent": False,
        "execution_allowed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bounded current M04 WATCH channel provenance cohort summary")
    parser.add_argument("--evidence-file", required=True, type=Path,
                        action="append", metavar="PRIVATE_CURRENT_WATCH_JSON")
    args = parser.parse_args(argv)
    report = summarize_original_current_watch_reports_readonly(
        evidence_files=args.evidence_file)
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return 0 if report["status"] == (
        "M04_CURRENT_WATCH_MULTICOHORT_RESEARCH_ONLY") else 2


if __name__ == "__main__":
    raise SystemExit(main())
