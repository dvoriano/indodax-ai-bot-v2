"""Read only already-recorded BTC public WS tape; no market HTTP or private API.

Run against ORIGINAL V2 runtime SQLite. stdout is bounded aggregate JSON only.
This is an evidence INVENTORY, not attested full-market completeness.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
import time


def inspect_existing_btc_ws(
    path: str, *, max_publications: int = 200000,
    now_epoch: int | None = None,
) -> dict:
    """Only inventory historical observations; no G1 or live authorization."""
    if now_epoch is None:
        now_epoch = int(time.time())
    if type(now_epoch) is not int or now_epoch <= 0:
        raise ValueError("WS_EVIDENCE_INVALID_NOW_EPOCH")
    db_path = Path(path)
    if not db_path.is_absolute() or not db_path.is_file():
        raise ValueError("WS_EVIDENCE_DB_NOT_FOUND_ABSOLUTE")
    if (type(max_publications) is not int
            or not 1 <= max_publications <= 500000):
        raise ValueError("WS_EVIDENCE_INVALID_PUBLICATION_CAP")
    # URI mode=ro, BEGIN consistent SQLite snapshot. No schema or sidecar
    # created and never starts/changes the real public WS collector.
    con = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        state = con.execute(
            "SELECT epoch,last_offset,status FROM ws_stream_state WHERE pair=?",
            ("btc_idr",)
        ).fetchone()
        if not state:
            return {"status": "NO_BTC_WS_STREAM_STATE",
                    "g1_authenticated": False, "execution_allowed": False}
        total = con.execute(
            "SELECT COUNT(*) FROM ws_trade_publications WHERE pair=?",
            ("btc_idr",)
        ).fetchone()[0]
        gaps = con.execute(
            "SELECT COUNT(*) FROM ws_stream_discontinuities WHERE pair=?",
            ("btc_idr",)
        ).fetchone()[0]
        if total > max_publications:
            return {
                "status": "PUBLIC_WS_DATASET_EXCEEDS_BOUNDED_INSPECTION",
                "stream_epoch": state[0], "stream_status": state[2],
                "total_publications": total,
                "discontinuities_recorded": gaps,
                "g1_authenticated": False, "execution_allowed": False,
            }
        times: list[int] = []
        offset_gaps = 0
        seq_gaps = 0
        hash_mismatch = 0
        invalid_rows = 0
        previous_offset: dict[str, int] = {}
        previous_seq: dict[str, int] = {}
        seen_epoch: Counter[str] = Counter()
        observed_trade_rows = 0
        for epoch, offset, digest, raw in con.execute(
            """SELECT epoch,offset,payload_sha256,rows_json
               FROM ws_trade_publications WHERE pair=?
               ORDER BY epoch,offset""", ("btc_idr",)
        ):
            seen_epoch[epoch] += 1
            old_offset = previous_offset.get(epoch)
            if old_offset is not None and offset != old_offset + 1:
                offset_gaps += 1
            previous_offset[epoch] = offset
            if sha256(raw.encode()).hexdigest() != digest:
                hash_mismatch += 1
                continue
            try:
                rows = json.loads(raw)
            except (ValueError, TypeError):
                invalid_rows += 1
                continue
            if not isinstance(rows, list):
                invalid_rows += 1
                continue
            for row in rows:
                if (not isinstance(row, list) or len(row) != 7
                        or row[0] != "btcidr"
                        or type(row[1]) is not int or row[1] <= 0
                        or type(row[2]) is not int or row[2] <= 0):
                    invalid_rows += 1
                    continue
                ts, seq = row[1], row[2]
                times.append(ts)
                observed_trade_rows += 1
                older = previous_seq.get(epoch)
                if older is not None and seq != older + 1:
                    # Publication ordering is not independently certified
                    # as exchange-wide trade sequence continuity.
                    seq_gaps += 1
                previous_seq[epoch] = seq
        if not times:
            return {"status": "NO_VALID_WS_TRADE_ROWS",
                    "stream_epoch": state[0],
                    "total_publications": total,
                    "g1_authenticated": False, "execution_allowed": False}
        newest = max(times)
        # A healthy-looking historical hour must never be treated as a
        # fresh market feed by a downstream scanner or owner proposal.
        observed_lag_seconds = now_epoch - newest
        observed_recent = 0 <= observed_lag_seconds <= 120
        newest_closed = newest // 60 * 60
        # These timestamps locate when the locally recorded discontinuity
        # was SEEN, not the full interval of trades absent upstream.
        # A gap observed outside a window may still impact its coverage.
        recorded_gap_times = [
            epoch for (epoch,) in con.execute(
                "SELECT observed_at_epoch FROM ws_stream_discontinuities "
                "WHERE pair=? ORDER BY id", ("btc_idr",)
            )
            if type(epoch) is int and epoch > 0
        ]
        windows = {}
        # Original aligned windows retained for existing tests/callers.
        # Rolling windows end at the SAME latest completed minute to
        # remove ambiguity when the last closed 15m/1h do not overlap.
        specs = (
            (900, "last_closed_15m", (newest_closed // 900) * 900,
             "ALIGNED_15M_BOUNDARY"),
            (3600, "last_closed_1h", (newest_closed // 3600) * 3600,
             "ALIGNED_1H_BOUNDARY"),
            (900, "rolling_last_15_closed_minutes", newest_closed,
             "SAME_LAST_CLOSED_MINUTE"),
            (3600, "rolling_last_60_closed_minutes", newest_closed,
             "SAME_LAST_CLOSED_MINUTE"),
        )
        for seconds, key, end, alignment in specs:
            start = end - seconds
            within = [t for t in times if start <= t < end]
            minutes = {t // 60 for t in within}
            events = sum(
                start <= epoch < end for epoch in recorded_gap_times
            )
            windows[key] = {
                "start_epoch": start, "end_epoch": end,
                "window_alignment": alignment,
                "observed_trade_rows": len(within),
                "minutes_with_observed_trades": len(minutes),
                "minutes_without_observed_trades_or_missing_data":
                    seconds // 60 - len(minutes),
                "recorded_gap_events_observed_during_window": events,
                "no_gap_event_observed_does_not_prove_trade_completeness": True,
                "complete_exchange_trade_tape_proven": False,
                "independent_issuer_attested": False,
            }
        con.execute("ROLLBACK")
        return {
            "status": "LOCAL_PUBLIC_WS_EVIDENCE_INVENTORY_ONLY",
            "stream_epoch": state[0], "last_offset": state[1],
            "stream_status": state[2],
            "total_publications": total, "observed_trade_rows": observed_trade_rows,
            "first_trade_epoch": min(times),
            "last_trade_epoch": newest,
            "inspection_as_of_epoch": now_epoch,
            "latest_observed_trade_lag_seconds": observed_lag_seconds,
            "latest_observed_trade_recent_120s": observed_recent,
            "last_persisted_stream_status_contiguous": (
                state[2] == "CONTIGUOUS_OBSERVED"
            ),
            "live_collector_heartbeat_proven": False,
            "source_eligible_for_live_decision": False,
            "distinct_stream_epochs": len(seen_epoch),
            "discontinuities_recorded": gaps,
            "latest_recorded_gap_observed_at_epoch": (
                max(recorded_gap_times) if recorded_gap_times else None
            ),
            "same_end_rolling_windows_are_g1_attested": False,
            "offset_jumps_between_stored_publications": offset_gaps,
            "trade_sequence_jumps_between_observed_rows": seq_gaps,
            "invalid_trade_rows": invalid_rows,
            "publication_hash_mismatches": hash_mismatch,
            "windows": windows,
            "g1_authenticated": False,
            "complete_public_15m_1h_tape_proven": False,
            "execution_allowed": False,
        }
    except sqlite3.Error:
        raise ValueError("WS_EVIDENCE_READONLY_SQLITE_UNAVAILABLE") from None
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Original V2 observed WS evidence inventory; never trading",
    )
    parser.add_argument("--ws-db", required=True)
    args = parser.parse_args(argv)
    try:
        report = inspect_existing_btc_ws(args.ws_db)
    except ValueError as exc:
        print(json.dumps({"status": str(exc), "execution_allowed": False}))
        return 3
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
