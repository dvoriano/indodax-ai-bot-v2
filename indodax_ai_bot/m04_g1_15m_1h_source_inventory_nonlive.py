"""Original V2 G1 15m/1h source availability inventory, NOT a G1 issuer.

Bounded, no-network, no-write inventory of the ORIGINAL M03 research archive
and an optional already-existing PublicTradeStreamJournal. Missing observations
are UNKNOWN, never evidence of no exchange trades. Neither local digest checks
nor observed contiguous WS offsets independently certify Indodax-origin tape.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import re
import sqlite3
from typing import Any

from .trade_stream import _trade_rows, StreamEvidenceError
from .m04_g1_local_receipt_reconciliation_nonlive import (
    reconcile_local_trade_windows_nonlive,
)

_WINDOWS = (("15m", 900), ("1h", 3600))
_MAX_MICRO_ROWS = 60
_MAX_JOURNAL_PAIR_PUBLICATIONS = 20_000
_MAX_JOURNAL_JSON_BYTES = 131_072
_PAIR = re.compile(r"[a-z0-9]+_idr\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_REQUIRED_MICRO_COLUMNS = (
    "minute_epoch", "recorded_local_epoch", "trade_frames",
    "last_trade_epoch", "trade_offset", "trade_gap_observed",
    "trade_truncated", "source_malformed", "book_local_epoch",
    "book_offset", "source_complete", "aggressor_verified", "g1_verified",
)


def _table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def _max_streak(minutes: set[int]) -> int:
    best = count = 0
    previous = None
    for minute in sorted(minutes):
        count = count + 1 if previous is not None and minute == previous + 60 else 1
        best = max(best, count)
        previous = minute
    return best


def _micro_inventory(
    conn: sqlite3.Connection, *, pair: str, end_epoch: int,
) -> dict[str, Any]:
    if not _table(conn, "ws3_micro_research_evidence"):
        return {
            "status": "MICRO_TABLE_ABSENT",
            "windows": {
                label: {
                    "expected_minutes": seconds // 60,
                    "micro_observed_minutes": 0,
                    "missing_minutes_unknown": seconds // 60,
                    "trade_activity_observed_minutes": 0,
                    "book_observed_minutes": 0,
                    "maximum_consecutive_observed_minutes": 0,
                    "flagged_or_invalid_minutes": 0,
                    "complete_tape_verified": False,
                } for label, seconds in _WINDOWS
            },
        }
    columns = {r[1] for r in conn.execute(
        "PRAGMA table_info(ws3_micro_research_evidence)"
    ).fetchall()}
    if not set(_REQUIRED_MICRO_COLUMNS).issubset(columns):
        raise ValueError("G1_MICRO_SCHEMA_MISMATCH")
    records = conn.execute(
        "SELECT " + ",".join(_REQUIRED_MICRO_COLUMNS) +
        " FROM ws3_micro_research_evidence"
        " WHERE pair=? AND minute_epoch>=? AND minute_epoch<?"
        " ORDER BY minute_epoch LIMIT ?",
        (pair, end_epoch - 3600, end_epoch, _MAX_MICRO_ROWS + 1),
    ).fetchall()
    if len(records) > _MAX_MICRO_ROWS:
        raise ValueError("G1_MICRO_HOURLY_ROW_CAP_EXCEEDED")
    rows = [dict(zip(_REQUIRED_MICRO_COLUMNS, r)) for r in records]
    out = {}
    for label, seconds in _WINDOWS:
        start = end_epoch - seconds
        sampled_minutes = set()
        trade_minutes = book_minutes = flagged = 0
        flags_seen = Counter()
        for r in rows:
            minute = r["minute_epoch"]
            if type(minute) is not int or minute % 60 != 0:
                raise ValueError("G1_MICRO_MINUTE_INVALID")
            if not start <= minute < end_epoch:
                continue
            if minute in sampled_minutes:
                raise ValueError("G1_MICRO_DUPLICATE_MINUTE")
            sampled_minutes.add(minute)
            captured = r["recorded_local_epoch"]
            frames = r["trade_frames"]
            last_trade = r["last_trade_epoch"]
            book_time = r["book_local_epoch"]
            critical_flags = ("trade_gap_observed", "trade_truncated",
                              "source_malformed")
            authority_flags = ("source_complete", "aggressor_verified", "g1_verified")
            valid = (
                type(captured) is int and minute <= captured < minute + 60
                and type(frames) is int and 0 <= frames <= 600
                and all(type(r[k]) is int and r[k] in (0, 1)
                        for k in critical_flags)
                and all(type(r[k]) is int and r[k] == 0
                        for k in authority_flags)
                and (last_trade is None or (
                    type(last_trade) is int and
                    0 <= captured - last_trade <= 90))
                and (book_time is None or (
                    type(book_time) is int and
                    0 <= captured - book_time <= 90))
                and (r["trade_offset"] is None or (
                    type(r["trade_offset"]) is int and
                    r["trade_offset"] >= 0))
                and (r["book_offset"] is None or (
                    type(r["book_offset"]) is int and
                    r["book_offset"] >= 0))
                and (frames == 0 or
                     (last_trade is not None and r["trade_offset"] is not None))
                and (book_time is None or r["book_offset"] is not None)
            )
            for k in critical_flags:
                if r[k] == 1:
                    flags_seen[k] += 1
            if not valid or any(r[k] == 1 for k in critical_flags):
                flagged += 1
            if valid and frames > 0 and not any(
                    r[k] == 1 for k in critical_flags):
                trade_minutes += 1
            if valid and book_time is not None and r["source_malformed"] == 0:
                book_minutes += 1
        out[label] = {
            "expected_minutes": seconds // 60,
            "micro_observed_minutes": len(sampled_minutes),
            "missing_minutes_unknown": seconds // 60 - len(sampled_minutes),
            "trade_activity_observed_minutes": trade_minutes,
            "book_observed_minutes": book_minutes,
            "maximum_consecutive_observed_minutes": _max_streak(sampled_minutes),
            "flagged_or_invalid_minutes": flagged,
            "observed_gap_flags": flags_seen["trade_gap_observed"],
            "observed_truncation_flags": flags_seen["trade_truncated"],
            "observed_malformed_flags": flags_seen["source_malformed"],
            "trade_publication_cursor_continuity_verified": False,
            "zero_trade_minutes_proven": False,
            "complete_tape_verified": False,
        }
    return {"status": "BOUNDED_ROTATING_MICRO_SAMPLE_ONLY", "windows": out}


def _journal_inventory(
    conn: sqlite3.Connection | None, *, pair: str, end_epoch: int,
) -> dict[str, Any]:
    blank = {
        label: {
            "observed_publications_with_window_trades": 0,
            "observed_trade_rows": 0,
            "distinct_trade_minutes_observed": 0,
            "source_epoch_count": 0,
            "observed_offset_jumps_in_pair_journal": 0,
            "explicit_gap_rebaseline_markers_in_window": 0,
            "independent_upstream_replay_receipt_verified": False,
            "complete_tape_verified": False,
        } for label, _ in _WINDOWS
    }
    if conn is None:
        return {"status": "INDEPENDENT_JOURNAL_NOT_SUPPLIED",
                "windows": blank}
    required = (
        "ws_trade_publications", "ws_stream_state",
        "ws_stream_discontinuities",
    )
    if any(not _table(conn, name) for name in required):
        return {"status": "JOURNAL_REQUIRED_TABLES_ABSENT",
                "windows": blank}
    try:
        records = conn.execute(
            "SELECT epoch,offset,payload_sha256,rows_json"
            " FROM ws_trade_publications WHERE pair=?"
            " ORDER BY epoch,offset LIMIT ?",
            (pair, _MAX_JOURNAL_PAIR_PUBLICATIONS + 1),
        ).fetchall()
        if len(records) > _MAX_JOURNAL_PAIR_PUBLICATIONS:
            return {"status": "JOURNAL_PAIR_CAP_EXCEEDED",
                    "windows": blank}
        state = conn.execute(
            "SELECT epoch,last_offset,status"
            " FROM ws_stream_state WHERE pair=?", (pair,)
        ).fetchone()
        marker_rows = conn.execute(
            "SELECT prior_epoch,prior_offset,new_epoch,new_offset,"
            " observed_at_epoch FROM ws_stream_discontinuities"
            " WHERE pair=? ORDER BY id LIMIT 10001", (pair,)
        ).fetchall()
        if len(marker_rows) > 10_000:
            return {"status": "JOURNAL_GAP_MARKER_CAP_EXCEEDED",
                    "windows": blank}
        if state is not None and (
                len(state) != 3 or type(state[0]) is not str
                or re.fullmatch(r"[0-9]+", state[0]) is None
                or type(state[1]) is not int or state[1] < 0
                or state[2] not in ("BASELINE_ONLY", "CONTIGUOUS_OBSERVED",
                                     "GAP", "DISCONNECTED")):
            return {"status": "JOURNAL_STATE_INVALID", "windows": blank}
        for marker in marker_rows:
            old_ep, old_off, new_ep, new_off, at = marker
            if (type(old_ep) is not str or not old_ep.isdecimal()
                    or type(new_ep) is not str or not new_ep.isdecimal()
                    or type(old_off) is not int or old_off < 0
                    or type(new_off) is not int or new_off < 0
                    or type(at) is not int or at <= 0):
                return {"status": "JOURNAL_GAP_MARKER_INVALID",
                        "windows": blank}
        windows = {}
        prior_offsets = {}
        observed_jumps = []
        epochs_in_window = {}
        minutes = {}
        publications = Counter()
        trades = Counter()
        identities = {label: set() for label, _ in _WINDOWS}
        for label, seconds in _WINDOWS:
            epochs_in_window[label] = set()
            minutes[label] = set()
            windows[label] = dict(blank[label])
        for epoch, offset, digest, encoded in records:
            if (type(epoch) is not str or
                    re.fullmatch(r"[0-9]+", epoch) is None or
                    type(offset) is not int or offset <= 0 or
                    type(digest) is not str or _HEX.fullmatch(digest) is None or
                    type(encoded) is not str or
                    len(encoded.encode("utf-8")) > _MAX_JOURNAL_JSON_BYTES or
                    sha256(encoded.encode("utf-8")).hexdigest() != digest):
                return {"status": "JOURNAL_DIGEST_OR_ROW_INVALID",
                        "windows": blank}
            try:
                trade_rows = _trade_rows(json.loads(encoded), pair)
            except (ValueError, TypeError, StreamEvidenceError, json.JSONDecodeError):
                return {"status": "JOURNAL_TRADE_ROWS_INVALID",
                        "windows": blank}
            if epoch in prior_offsets and offset != prior_offsets[epoch] + 1:
                observed_jumps.append((epoch, prior_offsets[epoch], offset))
            prior_offsets[epoch] = offset
            for label, seconds in _WINDOWS:
                start = end_epoch - seconds
                filtered = [row for row in trade_rows
                            if start <= row[1] < end_epoch]
                if not filtered:
                    continue
                publications[label] += 1
                epochs_in_window[label].add(epoch)
                for row in filtered:
                    identity = (epoch, row[1], row[2])
                    if identity in identities[label]:
                        return {"status": "JOURNAL_DUPLICATE_TRADE_IDENTITY",
                                "windows": blank}
                    identities[label].add(identity)
                    trades[label] += 1
                    minutes[label].add(row[1] // 60 * 60)
        for label, seconds in _WINDOWS:
            start = end_epoch - seconds
            windows[label].update({
                "observed_publications_with_window_trades": publications[label],
                "observed_trade_rows": trades[label],
                "distinct_trade_minutes_observed": len(minutes[label]),
                "source_epoch_count": len(epochs_in_window[label]),
                # Offset jumps in the locally persisted PAIR journal are
                # diagnostic; their historical wall-clock onset is UNKNOWN.
                "observed_offset_jumps_in_pair_journal": len(observed_jumps),
                "explicit_gap_rebaseline_markers_in_window": sum(
                    1 for marker in marker_rows
                    if start <= marker[4] < end_epoch
                ),
            })
        return {
            "status": (
                "JOURNAL_LOCAL_INTEGRITY_CHECKED_SAMPLE_ONLY"
                if records else "JOURNAL_PAIR_UNOBSERVED"
            ),
            "total_pair_publications_inspected": len(records),
            "latest_state": state[2] if state else "NO_PAIR_STATE",
            "all_pair_recorded_gap_markers": len(marker_rows),
            "has_multiple_publication_epochs": len(prior_offsets) > 1,
            "upstream_origin_authenticated": False,
            "offset_recovery_reconciled_independently": False,
            "windows": windows,
        }
    except (sqlite3.Error, OverflowError):
        return {"status": "JOURNAL_QUERY_UNAVAILABLE", "windows": blank}


def inventory_g1_windows_nonlive(
    *, micro_db: sqlite3.Connection,
    journal_db: sqlite3.Connection | None,
    end_epoch: int, pairs: list[str],
) -> dict[str, Any]:
    """Read genuine original archive bytes; never issue an issuer certificate."""
    if (type(end_epoch) is not int or end_epoch <= 3600
            or end_epoch % 60 != 0 or type(pairs) is not list
            or not 1 <= len(pairs) <= 8
            or any(type(p) is not str or _PAIR.fullmatch(p) is None
                   for p in pairs)
            or len(set(pairs)) != len(pairs)):
        raise ValueError("G1_INVENTORY_SCOPE_INVALID")
    if not isinstance(micro_db, sqlite3.Connection) or (
            journal_db is not None and
            not isinstance(journal_db, sqlite3.Connection)):
        raise ValueError("G1_INVENTORY_DB_INVALID")
    result = []
    for pair in pairs:
        micro = _micro_inventory(micro_db, pair=pair, end_epoch=end_epoch)
        journal = _journal_inventory(journal_db, pair=pair, end_epoch=end_epoch)
        result.append({
            "pair": pair,
            "rotating_micro_archive": micro,
            "separate_existing_trade_journal": journal,
            "local_publication_index_receipt_reconciliation": (
                reconcile_local_trade_windows_nonlive(
                    journal_db, pair=pair, end_epoch=end_epoch)
            ),
            "independent_g1_source_coverage_verified": False,
            "full_15m_tape_verified": False,
            "full_1h_tape_verified": False,
            "trade_side_aggressor_verified": False,
            "g1_receipt_issuance_allowed": False,
            "risk_veto": True,
            "final_decision": "REJECT",
            "execution_allowed": False,
        })
    return {
        "status": "M04_G1_15M_1H_INDEPENDENT_SOURCE_FEASIBILITY_READONLY",
        "window_end_epoch": end_epoch,
        "window_start_15m_epoch": end_epoch - 900,
        "window_start_1h_epoch": end_epoch - 3600,
        "source_windows": [
            {"label": label, "seconds": secs, "upstream_full_coverage_attested": False}
            for label, secs in _WINDOWS
        ],
        "pairs_checked": len(result),
        "results": result,
        "g1_independent_issuer_verified": False,
        "g1_coverage_manifest_issuance_allowed": False,
        "real_market_risk_strategy_role_responses": 0,
        "owner_paid_ai_go_authorized": False,
        "final_decision": "REJECT",
        "execution_allowed": False,
        "orders_sent": 0,
        "telegram_messages_sent": 0,
        "provider_http_calls": 0,
        "additional_public_ws_connections": 0,
        "service_restarts": 0,
    }
