"""Reconcile ORIGINAL local WS publications and trade index as of a cutoff.

The two tables are populated by the SAME collector, not independent exchange
sources. Matching local identities is a consistency observation, never G1,
an ACK, recovery proof, or a certification of the complete exchange tape.
No network, writes, service control or authority.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
import sqlite3

from .trade_stream import _trade_rows, StreamEvidenceError

_WINDOWS = (("15m", 900), ("1h", 3600))
_PAIR = re.compile(r"[a-z0-9]+_idr\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_MAX_PUBLICATIONS = 20000
_MAX_PAYLOAD_BYTES = 131072
_MAX_TOTAL_PAYLOAD_BYTES = 32 * 1024 * 1024
_MAX_IDENTITIES = 100000
_TABLES = (
    "ws_trade_publications", "m03_ws3_trade_publication_times",
    "m03_ws3_trade_samples",
)


def _base(status, **fields):
    return {
        "status": status,
        "local_tables_share_original_collector": True,
        "independent_upstream_source_verified": False,
        "pair_specific_ack_verified": False,
        "independent_gap_recovery_verified": False,
        "complete_trade_tape_verified": False,
        "g1_receipt_issuance_allowed": False,
        "execution_allowed": False,
        **fields,
    }


def _decimal(raw):
    if type(raw) is not str or not 1 <= len(raw) <= 64:
        raise ValueError("INVALID_LOCAL_DECIMAL")
    number = Decimal(raw)
    if not number.is_finite() or number <= 0 or abs(number.adjusted()) > 30:
        raise ValueError("INVALID_LOCAL_DECIMAL")
    return number


def _receipt_valid(receipt, exchange):
    return type(receipt) is int and 0 <= receipt - exchange <= 86400


def reconcile_local_trade_windows_nonlive(
    conn: sqlite3.Connection | None, *, pair: str, end_epoch: int,
) -> dict:
    """One existing read-only SQLite snapshot; [start,end) exchange windows.

    Receipt cutoff is inclusive (recorded_epoch <= end_epoch). A timestamp
    after the cutoff is retrospective evidence only. A missing receipt stays
    unknown, not an on-time trade. Raw identity is (exchange second, sequence)
    per pair, matching the original index PK, irrespective of publication
    epoch. Exact repeated raw identities collapse; conflicting values block.
    """
    if conn is None:
        return _base("LOCAL_RECONCILIATION_JOURNAL_NOT_SUPPLIED")
    if (not isinstance(conn, sqlite3.Connection)
            or type(pair) is not str or not _PAIR.fullmatch(pair)
            or type(end_epoch) is not int or end_epoch <= 3600
            or end_epoch % 60):
        return _base("LOCAL_RECONCILIATION_INVALID_SCOPE")
    start = end_epoch - 3600
    try:
        present = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN (?,?,?)", _TABLES)}
        if present != set(_TABLES):
            return _base("LOCAL_RECONCILIATION_TABLES_UNAVAILABLE",
                         missing_tables=sorted(set(_TABLES)-present))
        # Include retained publications without a receipt as well as late
        # receipts. Their exchange time is only known by parsing the payload.
        # Stream rows rather than loading all historical payloads at once.
        cursor = conn.execute("""
            SELECT p.epoch,p.offset,p.payload_sha256,
                   CASE WHEN length(CAST(p.rows_json AS BLOB))<=?
                        THEN p.rows_json END,
                   t.recorded_epoch
            FROM ws_trade_publications AS p
            LEFT JOIN m03_ws3_trade_publication_times AS t
              ON t.pair=p.pair AND t.epoch=p.epoch AND t.offset=p.offset
            WHERE p.pair=?
            ORDER BY p.epoch,p.offset LIMIT ?
        """, (_MAX_PAYLOAD_BYTES, pair, _MAX_PUBLICATIONS+1))
        raw = {}
        inspected = total_bytes = repeats = 0
        for epoch, offset, digest, payload, receipt in cursor:
            inspected += 1
            if inspected > _MAX_PUBLICATIONS:
                return _base("LOCAL_RECONCILIATION_PUBLICATION_CAP")
            if (type(epoch) is not str or not epoch.isascii()
                    or not epoch.isdecimal() or type(offset) is not int
                    or offset <= 0 or type(digest) is not str
                    or not _HEX.fullmatch(digest) or type(payload) is not str):
                return _base("LOCAL_RECONCILIATION_PUBLICATION_INVALID")
            encoded = payload.encode("utf-8")
            total_bytes += len(encoded)
            if total_bytes > _MAX_TOTAL_PAYLOAD_BYTES:
                return _base("LOCAL_RECONCILIATION_BYTE_CAP")
            if sha256(encoded).hexdigest() != digest:
                return _base("LOCAL_RECONCILIATION_PUBLICATION_DIGEST_MISMATCH")
            for row in _trade_rows(json.loads(payload), pair):
                exchange, seq = row[1], row[2]
                if not start <= exchange < end_epoch:
                    continue
                identity = (exchange, seq)
                values = (_decimal(row[4]), _decimal(row[5]))
                prior = raw.get(identity)
                valid = _receipt_valid(receipt, exchange)
                if prior is not None:
                    repeats += 1
                    if prior[0] != values:
                        return _base("LOCAL_RECONCILIATION_RAW_IDENTITY_CONFLICT")
                    earliest = prior[1]
                    if valid and (earliest is None or receipt < earliest):
                        earliest = receipt
                    raw[identity] = (values, earliest)
                else:
                    raw[identity] = (values, receipt if valid else None)
                if len(raw) > _MAX_IDENTITIES:
                    return _base("LOCAL_RECONCILIATION_IDENTITY_CAP")

        indexed = {}
        rows = conn.execute("""
            SELECT exchange_epoch,sequence,price_idr,quote_idr,recorded_epoch
            FROM m03_ws3_trade_samples
            WHERE pair=? AND exchange_epoch>=? AND exchange_epoch<?
            ORDER BY exchange_epoch,sequence LIMIT ?
        """, (pair, start, end_epoch, _MAX_IDENTITIES+1))
        for exchange, seq, price, quote, receipt in rows:
            if (type(exchange) is not int or not start <= exchange < end_epoch
                    or type(seq) is not int or seq <= 0):
                return _base("LOCAL_RECONCILIATION_INDEX_IDENTITY_INVALID")
            identity = exchange, seq
            if identity in indexed:
                return _base("LOCAL_RECONCILIATION_INDEX_IDENTITY_CONFLICT")
            indexed[identity] = (
                (_decimal(price), _decimal(quote)),
                receipt if _receipt_valid(receipt, exchange) else None,
            )
            if len(indexed) > _MAX_IDENTITIES:
                return _base("LOCAL_RECONCILIATION_IDENTITY_CAP")

        windows = {}
        for label, seconds in _WINDOWS:
            cutoff = end_epoch - seconds
            raw_w = {k: v for k, v in raw.items() if k[0] >= cutoff}
            idx_w = {k: v for k, v in indexed.items() if k[0] >= cutoff}
            raw_on = {k for k, v in raw_w.items()
                      if v[1] is not None and v[1] <= end_epoch}
            idx_on = {k for k, v in idx_w.items()
                      if v[1] is not None and v[1] <= end_epoch}
            both = raw_on & idx_on
            values_match = {k for k in both if raw_w[k][0] == idx_w[k][0]}
            time_match = {k for k in values_match if raw_w[k][1] == idx_w[k][1]}
            raw_unknown = sum(v[1] is None for v in raw_w.values())
            idx_unknown = sum(v[1] is None for v in idx_w.values())
            windows[label] = {
                "raw_unique_trade_identities": len(raw_w),
                "index_unique_trade_identities": len(idx_w),
                "raw_received_by_cutoff": len(raw_on),
                "index_received_by_cutoff": len(idx_on),
                "raw_receipt_after_cutoff": len(raw_w)-len(raw_on)-raw_unknown,
                "index_receipt_after_cutoff": len(idx_w)-len(idx_on)-idx_unknown,
                "raw_receipt_missing_or_invalid": raw_unknown,
                "index_receipt_missing_or_invalid": idx_unknown,
                "raw_only_asof_identities": len(raw_on-idx_on),
                "index_only_asof_identities": len(idx_on-raw_on),
                "matched_identity_and_values_asof": len(values_match),
                "matched_identity_values_and_receipt_asof": len(time_match),
                "value_conflicts_asof": len(both-values_match),
                "receipt_timestamp_differences_asof": len(values_match-time_match),
                "nonempty_asof_local_identity_value_receipt_agreement": (
                    bool(raw_on) and raw_on == idx_on == time_match
                ),
                "zero_observed_trades_is_not_zero_exchange_trades": True,
                "complete_trade_tape_verified": False,
            }
        return _base("LOCAL_PUBLICATION_INDEX_RECONCILIATION_RESEARCH_ONLY",
                     retained_publications_inspected=inspected,
                     repeated_raw_window_identities_collapsed=repeats,
                     window_end_epoch=end_epoch, windows=windows)
    except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError,
            InvalidOperation, StreamEvidenceError):
        return _base("LOCAL_RECONCILIATION_SOURCE_INVALID_OR_UNAVAILABLE")
