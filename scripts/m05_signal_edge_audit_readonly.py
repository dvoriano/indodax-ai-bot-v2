"""Read-only, source-matched M05 ACK timing and ex-post WS3 price proxies.

No market requests, Telegram sends, AI calls, orders, or live service changes.
Price-only outcomes are NOT executable entry/exit returns or proven profit.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import os
import stat
import sqlite3
import time
from zoneinfo import ZoneInfo

from indodax_ai_bot.m05_radar_momentum_nonlive import (
    DEFAULT_DB, PAIR_ID, _owned_private_regular,
)
from scripts.m05_radar_owner_quality_replay_readonly import (
    replay_original_ws3_quality_timeline_readonly,
)

WIB = ZoneInfo("Asia/Jakarta")
HORIZONS = (300, 900)
FRESHNESS_SECONDS = 90
MAX_EVENTS = 500


def _positive_price(value: str) -> Decimal:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("INVALID_ORIGINAL_WS3_PRICE")
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError("INVALID_ORIGINAL_WS3_PRICE") from None
    if not number.is_finite() or number <= 0 or number.adjusted() > 20:
        raise ValueError("INVALID_ORIGINAL_WS3_PRICE")
    return number


def _assumed_cost(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        cost = Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValueError("INVALID_COST_ASSUMPTION") from None
    if not cost.is_finite() or not 0 <= cost <= 1000:
        raise ValueError("INVALID_COST_ASSUMPTION")
    return cost


def _wib(epoch: int | None) -> str | None:
    return (datetime.fromtimestamp(epoch, WIB).strftime("%Y-%m-%d %H:%M:%S WIB")
            if epoch is not None else None)


def _first_fresh_after(quotes: list[tuple[int, Decimal]],
                       earliest: int) -> tuple[int, Decimal] | None:
    return next((q for q in quotes
                 if earliest <= q[0] <= earliest + FRESHNESS_SECONDS), None)


def post_ack_price_proxy(quotes: list[tuple[int, Decimal]], *,
                         ack_epoch: int, horizon_seconds: int,
                         now_epoch: int,
                         total_cost_bps: Decimal | None = None) -> dict:
    """Two fresh post-ACK ticker quotes, never fill assumptions."""
    if horizon_seconds not in HORIZONS or type(ack_epoch) is not int:
        raise ValueError("INVALID_PRICE_PROXY_SCOPE")
    if now_epoch < ack_epoch + horizon_seconds + FRESHNESS_SECONDS:
        return {"status": "HORIZON_NOT_MATURE", "gross_move_bps": None,
                "assumed_cost_adjusted_bps": None}
    start = _first_fresh_after(quotes, ack_epoch)
    if start is None:
        return {"status": "NO_FRESH_POST_ACK_ENTRY_QUOTE",
                "gross_move_bps": None, "assumed_cost_adjusted_bps": None}
    end = _first_fresh_after(quotes, ack_epoch + horizon_seconds)
    if end is None or end[0] <= start[0]:
        return {"status": "NO_FRESH_HORIZON_QUOTE",
                "gross_move_bps": None, "assumed_cost_adjusted_bps": None}
    gross = (end[1] / start[1] - Decimal(1)) * Decimal(10000)
    return {
        "status": "OBSERVED_PRICE_ONLY_NOT_EXECUTABLE",
        "post_ack_quote_wib": _wib(start[0]),
        "horizon_quote_wib": _wib(end[0]),
        "post_ack_last_idr": str(start[1]),
        "horizon_last_idr": str(end[1]),
        "gross_move_bps": str(gross.quantize(Decimal("0.01"))),
        "assumed_cost_adjusted_bps": (
            str((gross - total_cost_bps).quantize(Decimal("0.01")))
            if total_cost_bps is not None else None),
    }


def inspect_postack_quality_readonly(
    *, start_epoch: int, end_epoch: int, pairs: tuple[str, ...],
    radar_claims_db: Path, db_path: Path = DEFAULT_DB,
    research_evidence_db: Path | None = None,
    fee_bps: str | None = None, slippage_bps: str | None = None,
    now_epoch: int | None = None,
) -> dict:
    """Bounded retrospective evaluation of natural ACKs, no backfill.

    The first stage timings come from existing as-of minute-end replay.
    Ex-post price proxy samples AFTER the real Bot API ACK are separate.
    """
    now = int(time.time()) if now_epoch is None else now_epoch
    if (type(now) is not int or type(start_epoch) is not int
            or type(end_epoch) is not int
            or start_epoch <= 0 or end_epoch < start_epoch
            or start_epoch % 60 != 0 or end_epoch % 60 != 0
            or end_epoch - start_epoch > 120 * 60
            or end_epoch > now or type(pairs) is not tuple
            or not 1 <= len(pairs) <= 10 or len(set(pairs)) != len(pairs)
            or any(type(p) is not str or not PAIR_ID.fullmatch(p) for p in pairs)):
        raise ValueError("INVALID_POSTACK_AUDIT_SCOPE")
    fee = _assumed_cost(fee_bps)
    slip = _assumed_cost(slippage_bps)
    if (fee is None) != (slip is None):
        raise ValueError("BOTH_COST_ASSUMPTIONS_REQUIRED")
    assumed_cost = fee + slip if fee is not None else None
    source = Path(db_path).expanduser()
    owner = Path(radar_claims_db).expanduser()
    if not _owned_private_regular(source) or not _owned_private_regular(owner):
        raise ValueError("ORIGINAL_PRIVATE_SOURCE_NOT_TRUSTED")

    timeline = replay_original_ws3_quality_timeline_readonly(
        start_epoch=start_epoch, end_epoch=end_epoch, pairs=pairs,
        db_path=source, radar_claims_db=owner,
    )
    if timeline["status"] != "M03_WS3_ORIGINAL_SOURCE_CHRONOLOGICAL_QUALITY_TIMELINE":
        raise ValueError("ORIGINAL_REPLAY_NOT_VERIFIED")
    if (timeline["owner_radar_delivery_event_source_status"]
            != "PER_PAIR_DELIVERY_EVENTS_AVAILABLE"):
        return {
            "status": "PER_PAIR_EVENTS_NOT_YET_AVAILABLE",
            "execution_allowed": False, "orders_sent": 0,
            "exchange_requests": 0, "telegram_messages_sent": 0,
            "events": [],
        }

    placeholders = ",".join("?" for _ in pairs)
    with sqlite3.connect(owner.absolute().as_uri() + "?mode=ro",
                         uri=True, timeout=3) as db:
        db.execute("PRAGMA query_only=ON")
        events = db.execute(
            "SELECT pair,claimed_epoch,source_observed_epoch,source_kind,"
            "delivery_state,ack_received_epoch FROM m05_radar_delivery_events "
            f"WHERE pair IN ({placeholders}) AND claimed_epoch BETWEEN ? AND ? "
            "ORDER BY claimed_epoch, pair LIMIT ?",
            (*pairs, start_epoch, end_epoch + 59, MAX_EVENTS + 1),
        ).fetchall()
    if len(events) > MAX_EVENTS:
        raise ValueError("TOO_MANY_EVENTS_FOR_BOUNDED_AUDIT")

    quote_map = defaultdict(list)
    source_epochs = defaultdict(set)
    with sqlite3.connect(source.absolute().as_uri() + "?mode=ro",
                         uri=True, timeout=3) as db:
        db.execute("PRAGMA query_only=ON")
        for pair, observed, price in db.execute(
            "SELECT pair,exchange_epoch,last_idr "
            "FROM m03_ws_summary_watch_minute "
            f"WHERE pair IN ({placeholders}) AND minute_epoch BETWEEN ? AND ? "
            "ORDER BY pair, exchange_epoch",
            (*pairs, start_epoch - 300,
             min(now, end_epoch + max(HORIZONS) + 180)),
        ):
            p = _positive_price(price)
            if type(observed) is not int:
                raise ValueError("INVALID_WS3_QUOTE_EPOCH")
            quote_map[pair].append((observed, p))
            source_epochs[pair].add(observed)

    # Explicit opt-in research fallback ONLY for historical quote matching.
    # The separate 72h M03 research DB is not the original 2h scanner ledger
    # and NEVER substitutes for M04/M05 chronological role/eligibility replay.
    # A source match requires the exact pair + exchange epoch + eligible
    # source quote recorded by the original M03 collector BEFORE M05 claim.
    research_rows = defaultdict(list)
    research_status = "NOT_REQUESTED"
    if research_evidence_db is not None:
        evidence = Path(research_evidence_db).expanduser()
        if (not evidence.is_absolute() or not _owned_private_regular(evidence)):
            raise ValueError("RESEARCH_EVIDENCE_DB_UNTRUSTED")
        try:
            parent = evidence.parent.lstat()
        except OSError:
            raise ValueError("RESEARCH_EVIDENCE_PARENT_UNTRUSTED") from None
        if (not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != os.geteuid()
                or stat.S_IMODE(parent.st_mode) != 0o700):
            raise ValueError("RESEARCH_EVIDENCE_PARENT_UNTRUSTED")
        with sqlite3.connect(evidence.as_uri() + "?mode=ro",
                             uri=True, timeout=3) as archive:
            archive.execute("PRAGMA query_only=ON")
            tables = {row[0] for row in archive.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if not {"ws3_source_evidence", "ws3_source_cycles"} <= tables:
                raise ValueError("RESEARCH_EVIDENCE_SCHEMA_UNAVAILABLE")
            rows = archive.execute(
                "SELECT e.pair,e.minute_epoch,e.source_exchange_epoch,"
                "e.last_idr,e.source_status,c.recorded_local_epoch "
                "FROM ws3_source_evidence AS e "
                "JOIN ws3_source_cycles AS c USING(minute_epoch) "
                f"WHERE e.pair IN ({placeholders}) "
                "AND e.minute_epoch BETWEEN ? AND ? "
                "ORDER BY e.minute_epoch,e.pair LIMIT 20001",
                (*pairs, start_epoch - 300,
                 min(now, end_epoch + max(HORIZONS) + 180)),
            ).fetchall()
        if len(rows) > 20000:
            raise ValueError("RESEARCH_EVIDENCE_RESULT_TOO_LARGE")
        for pair, minute, observed, price, status, recorded in rows:
            if status not in (
                "ELIGIBLE_SOURCE_OBSERVATION",
                "BELOW_150M_VOLUME_FLOOR",
            ):
                continue
            if (pair not in pairs or type(minute) is not int
                    or type(observed) is not int or type(recorded) is not int
                    or observed <= 0 or recorded < minute
                    or not minute - 90 <= observed <= minute + 59
                    or recorded > now or recorded < observed):
                raise ValueError("RESEARCH_EVIDENCE_SOURCE_INVALID")
            research_price = _positive_price(price)
            research_rows[pair].append(
                (observed, research_price, minute, recorded, status)
            )
            # These are public snapshot prices, not executed trades.
            quote_map[pair].append((observed, research_price))
        research_status = "EXPLICIT_READONLY_RESEARCH_SOURCE"
    # No lookahead in quote selection. Discard any epoch where two retained
    # records disagree on price, rather than choosing the favorable quote.
    conflicting_quote_epochs = defaultdict(set)
    for pair in pairs:
        values = defaultdict(set)
        for observed, price in quote_map[pair]:
            values[observed].add(price)
        conflicting_quote_epochs[pair] = {
            epoch for epoch, prices in values.items() if len(prices) != 1
        }
        quote_map[pair] = sorted(
            {(epoch, next(iter(prices))) for epoch, prices in values.items()
             if len(prices) == 1}
        )

    stages = {p["pair"]: p for p in timeline["pairs"]}
    report = []
    statuses = Counter()
    for pair, claimed, observed, kind, state, ack in events:
        if (type(claimed) is not int or type(observed) is not int
                or kind not in ("WS3", "REST")
                or state not in ("CLAIMED", "ACKED", "UNCERTAIN")
                or (state == "ACKED" and
                    (type(ack) is not int or
                     not observed <= claimed <= ack <= now))
                or (state != "ACKED" and ack is not None)):
            raise ValueError("INVALID_ORIGINAL_DELIVERY_EVENT")
        original_matched = (
            kind == "WS3" and observed in source_epochs[pair]
            and observed not in conflicting_quote_epochs[pair]
        )
        # The archived snapshot must have been stored before the claim.
        # A later snapshot repeating the same exchange epoch is insufficient
        # to independently establish the quote was available at alert time.
        archived_prices = {
            price for epoch, price, minute, recorded, source_status
            in research_rows[pair]
            if epoch == observed and source_status == "ELIGIBLE_SOURCE_OBSERVATION"
            and recorded <= claimed and minute <= claimed // 60 * 60
        }
        research_matched = (
            kind == "WS3" and len(archived_prices) == 1
            and observed not in conflicting_quote_epochs[pair]
        )
        provenance = (
            "ORIGINAL_M03_WS3_MINUTE_DB" if original_matched
            else "M03_WS3_PRIVATE_RESEARCH_ARCHIVE" if research_matched
            else "NONE"
        )
        matched = original_matched or research_matched
        status = (
            "MATCHED_WS3_ACK" if state == "ACKED" and original_matched
            else "MATCHED_WS3_ACK_RESEARCH_ARCHIVE"
            if state == "ACKED" and research_matched
            else "ACK_NOT_MATCHED_WS3_SOURCE" if state == "ACKED"
            else "NO_CONFIRMED_ACK"
        )
        statuses[status] += 1
        stage = stages[pair]
        pre = stage["first_m04_micro_prealert_top2_replay_minute_end_wib"]
        watch = stage["first_m05_top5_eligible_replay_minute_end_wib"]
        # These stage moments are source replay landmarks, not runtime latency.
        lead = None
        if pre is not None and watch is not None:
            p = datetime.strptime(pre.replace(" WIB", ""), "%Y-%m-%d %H:%M:%S")
            w = datetime.strptime(watch.replace(" WIB", ""), "%Y-%m-%d %H:%M:%S")
            delta = int((w - p).total_seconds())
            if delta > 0:
                lead = delta
        item = {
            "pair": pair, "source_kind": kind, "delivery_state": state,
            "status": status, "source_quote_matched": matched,
            "source_match_provenance": provenance if state == "ACKED" else "NONE",
            "source_replay_retained_original_quotes":
                stage.get("source_quote_rows_in_window", 0) > 0,
            "source_observed_wib": _wib(observed),
            "claim_wib": _wib(claimed), "ack_wib": _wib(ack),
            "source_to_ack_seconds": ack - observed
            if state == "ACKED" and matched else None,
            "claim_to_ack_seconds": ack - claimed if state == "ACKED" else None,
            "first_micro_prealert_replay_wib": pre,
            "first_m03_watch_replay_wib":
                stage["first_m03_watch_top40_replay_minute_end_wib"],
            "first_m05_eligible_replay_wib": watch,
            "first_m04_quality_replay_wib":
                stage["first_m04_quality_top2_replay_minute_end_wib"],
            "prealert_before_first_watch_replay_seconds": lead,
            "price_proxies": {},
        }
        if state == "ACKED" and matched:
            for horizon in HORIZONS:
                item["price_proxies"][str(horizon // 60) + "m"] = (
                    post_ack_price_proxy(
                        quote_map[pair], ack_epoch=ack,
                        horizon_seconds=horizon, now_epoch=now,
                        total_cost_bps=assumed_cost,
                    )
                )
        report.append(item)

    return {
        "status": "M05_ORIGINAL_WS3_POSTACK_QUALITY_AUDIT_READONLY",
        "window_start_wib": _wib(start_epoch),
        "window_end_wib": _wib(end_epoch + 59),
        "events_inspected": len(report),
        "status_counts": dict(sorted(statuses.items())),
        "fee_bps_assumed": str(fee) if fee is not None else None,
        "slippage_bps_assumed": str(slip) if slip is not None else None,
        "price_proxy_is_not_real_entry_or_exit_fill": True,
        "cost_adjusted_price_move_is_not_real_net_pnl": True,
        "source_quote_not_full_trade_tape_or_verified_spread": True,
        "replay_stage_milestones_not_production_trigger_latency": True,
        "historical_source_retention_may_hide_observations": True,
        "research_evidence_source_status": research_status,
        "research_archive_cannot_reconstruct_original_m04_micro_replay": True,
        "research_archive_partial_source_coverage": True,
        "market_opportunity_first_appearance_unknown": True,
        "execution_allowed": False,
        "exchange_requests": 0,
        "telegram_messages_sent": 0,
        "paid_provider_calls": 0,
        "orders_sent": 0,
        "events": report,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="WIB YYYY-MM-DD")
    parser.add_argument("--start", required=True, help="HH:MM WIB")
    parser.add_argument("--end", required=True, help="HH:MM WIB, inclusive")
    parser.add_argument("--pairs", required=True, help="Comma-separated IDR pairs")
    parser.add_argument("--radar-claims-db", type=Path, required=True)
    parser.add_argument("--source-db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--research-evidence-db", type=Path, default=None,
                        help="Explicit existing private M03 WS3 research DB; read only")
    parser.add_argument("--fee-bps", default=None,
                        help="Hypothetical total round-trip fees; no default")
    parser.add_argument("--slippage-bps", default=None,
                        help="Hypothetical round-trip slippage; no default")
    args = parser.parse_args()
    try:
        date = datetime.strptime(args.date, "%Y-%m-%d").date()
        start = datetime.combine(
            date, datetime.strptime(args.start, "%H:%M").time(), WIB)
        end = datetime.combine(
            date, datetime.strptime(args.end, "%H:%M").time(), WIB)
        result = inspect_postack_quality_readonly(
            start_epoch=int(start.timestamp()), end_epoch=int(end.timestamp()),
            pairs=tuple(x.strip().lower() for x in args.pairs.split(",")),
            radar_claims_db=args.radar_claims_db, db_path=args.source_db,
            research_evidence_db=args.research_evidence_db,
            fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
        )
    except (ValueError, OSError, sqlite3.Error, OverflowError) as exc:
        print(json.dumps({
            "status": "M05_POSTACK_QUALITY_AUDIT_BLOCKED",
            "reason": str(exc) if isinstance(exc, ValueError) else "SOURCE_IO_OR_SCHEMA",
            "execution_allowed": False, "orders_sent": 0,
        }, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
