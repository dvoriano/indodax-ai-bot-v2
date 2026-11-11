"""One bounded historical replay of the ORIGINAL M03->M05 public radar.

Single canonical original V2 SQLite, mode=ro/query_only. Replays first-seen
ticker sample availability AS OF each minute, without future observations.
Historical casebook labels are disabled (they may be recorded later).
No new scanner, Indodax request, Telegram sender, AI call, owner DB or order.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path
import json
import sqlite3
import time
from zoneinfo import ZoneInfo

from indodax_ai_bot.m05_radar_momentum_nonlive import (
    DEFAULT_DB, PAIR_ID, _owned_private_regular,
    inspect_original_m03_radar_readonly,
    inspect_original_m03_ws3_watch_readonly,
)
from indodax_ai_bot.m04_radar_three_role_handoff_readonly import (
    inspect_original_ws3_three_role_handoff_readonly,
    inspect_original_ws3_micro_prealert_three_role_handoff_readonly,
)

WIB = ZoneInfo("Asia/Jakarta")
DEFAULT_PAIRS = (
    "uai_idr", "cyber_idr", "pieverse_idr",
    "fun_idr", "rvn_idr", "aster_idr", "icnt_idr",
)


def replay_original_public_watch_readonly(
    *, start_epoch: int, end_epoch: int,
    pairs: tuple[str, ...] = DEFAULT_PAIRS,
    db_path: Path = DEFAULT_DB,
) -> dict:
    """Bounded no-lookahead replay; never infer hypothetical BUY or profit."""
    if (type(start_epoch) is not int or type(end_epoch) is not int
            or start_epoch <= 0 or end_epoch < start_epoch
            or start_epoch % 60 != 0 or end_epoch % 60 != 0
            or end_epoch - start_epoch > 120 * 60
            or type(pairs) is not tuple or not 1 <= len(pairs) <= 10
            or len(set(pairs)) != len(pairs)
            or any(type(p) is not str or not PAIR_ID.fullmatch(p) for p in pairs)):
        raise ValueError("M05_RADAR_REPLAY_INVALID_SCOPE")
    counts = {p: Counter() for p in pairs}
    first_top5 = {p: None for p in pairs}
    first_eligible = {p: None for p in pairs}
    example = {p: None for p in pairs}
    first_top5_price = {p: None for p in pairs}
    first_top5_falling_volume = {p: None for p in pairs}
    first_by_type = {p: {} for p in pairs}
    type_minute_counts = {p: Counter() for p in pairs}
    phase_transitions = {p: [] for p in pairs}
    previous_phase = {p: None for p in pairs}
    for minute in range(start_epoch, end_epoch + 1, 60):
        asof = minute + 59
        snap = inspect_original_m03_radar_readonly(
            db_path=db_path, now_epoch=asof, explain_pairs=pairs,
            replay_no_casebook=True,
        )
        if snap.get("status") != "M03_ORIGINAL_PUBLIC_RADAR_OBSERVED_UNATTESTED":
            raise ValueError("M05_RADAR_REPLAY_ORIGINAL_SOURCE_NOT_VERIFIED")
        present = {item["pair"]: item for item in snap["candidates"]}
        for pair, info in snap["pair_diagnostics"].items():
            reason = info["reason"]
            counts[pair][reason] += 1
            if reason in ("IN_TOP5", "QUALIFIED_OUTSIDE_TOP5") and first_eligible[pair] is None:
                first_eligible[pair] = asof
            if reason == "IN_TOP5":
                found = present[pair]
                watch_type = found["watch_type"]
                price = found["observed_rise_pct"]
                type_minute_counts[pair][watch_type] += 1
                first_by_type[pair].setdefault(watch_type, asof)
                if first_top5[pair] is None:
                    first_top5[pair] = asof
                    example[pair] = {
                        "watch_type": watch_type,
                        "observed_rise_pct": price,
                        "priority": found["pre_ai_review"]["review_priority"],
                        "sample_age_seconds": found["sample_age_seconds"],
                        "spread_bps": found["spread_bps"],
                        "g1_verified": False,
                        "market_risk_strategy_ai_calls": 0,
                    }
                if found["price_watch"] and first_top5_price[pair] is None:
                    first_top5_price[pair] = asof
                if (found["volume_watch"] and not found["price_watch"]
                        and price.startswith("-")
                        and first_top5_falling_volume[pair] is None):
                    first_top5_falling_volume[pair] = asof
                # Phase = observed WATCH type + sign of sampled price.
                # This is not a risk score, closed candle, or Telegram send.
                phase = (
                    watch_type + (":PRICE_DOWN" if price.startswith("-")
                                  else ":PRICE_NONNEGATIVE")
                )
                if phase != previous_phase[pair]:
                    if len(phase_transitions[pair]) < 12:
                        phase_transitions[pair].append({
                            "asof_epoch": asof,
                            "phase": phase,
                            "observed_rise_pct": price,
                            "spread_bps": found["spread_bps"],
                        })
                    previous_phase[pair] = phase

    def wib_time(epoch: int | None) -> str | None:
        return (datetime.fromtimestamp(epoch, WIB).strftime("%Y-%m-%d %H:%M:%S WIB")
                if epoch is not None else None)

    return {
        "status": "M05_ORIGINAL_PUBLIC_RADAR_CHRONOLOGICAL_REPLAY",
        "window_start_wib": wib_time(start_epoch),
        "window_end_wib": wib_time(end_epoch + 59),
        "step_seconds": 60,
        "source": "EXISTING_ORIGINAL_M03_MINUTE_TICKER_SQLITE_ONLY",
        "historical_casebook_excluded_to_prevent_lookahead": True,
        "volume_24h_delta_not_actual_intraminute_trade_volume": True,
        "not_a_calibrated_profitability_or_trade_backtest": True,
        "possible_top5_watch_not_proof_of_actual_telegram_send": True,
        "cooldowns_delivery_and_exact_process_poll_not_replayed": True,
        "independent_issuer_verified": False,
        "actual_three_role_ai_calls": 0,
        "paid_provider_calls": 0,
        "exchange_requests": 0,
        "order_sent": False,
        "execution_allowed": False,
        "pairs": [{
            "pair": pair,
            "first_eligible_any_rank_wib": wib_time(first_eligible[pair]),
            "first_top5_watch_wib": wib_time(first_top5[pair]),
            "first_top5_price_momentum_wib": wib_time(first_top5_price[pair]),
            "first_top5_falling_price_volume_only_wib": (
                wib_time(first_top5_falling_volume[pair])
            ),
            "first_top5_by_watch_type_wib": {
                kind: wib_time(epoch)
                for kind, epoch in sorted(first_by_type[pair].items())
            },
            "top5_minute_counts_by_watch_type": dict(
                sorted(type_minute_counts[pair].items())
            ),
            "top5_observed_phase_transitions": [{
                **{k: v for k, v in item.items() if k != "asof_epoch"},
                "asof_wib": wib_time(item["asof_epoch"]),
            } for item in phase_transitions[pair]],
            "phase_transitions_are_actual_messages": False,
            "asof_minute_counts_by_reason": dict(sorted(counts[pair].items())),
            "first_top5_observed_evidence": example[pair],
        } for pair in pairs],
    }



def replay_original_ws3_quality_timeline_readonly(
    *, start_epoch: int, end_epoch: int,
    pairs: tuple[str, ...] = DEFAULT_PAIRS,
    db_path: Path = DEFAULT_DB,
    radar_claims_db: Path | None = None,
) -> dict:
    """Replay ORIGINAL WS3/M04 observed source, not a reconstructed send.

    Outputs earliest minute-end replay eligibility and raw archive events.
    Only the last retained M05 radar claim is available per pair; a claim is
    never proof of a Telegram send/ACK or even that WS3 won REST fallback.
    """
    if (type(start_epoch) is not int or type(end_epoch) is not int
            or start_epoch <= 0 or end_epoch < start_epoch
            or start_epoch % 60 != 0 or end_epoch % 60 != 0
            or end_epoch - start_epoch > 120 * 60
            or type(pairs) is not tuple or not 1 <= len(pairs) <= 10
            or len(set(pairs)) != len(pairs)
            or any(type(p) is not str or not PAIR_ID.fullmatch(p)
                   for p in pairs)):
        raise ValueError("M03_WS3_TIMELINE_INVALID_SCOPE")
    path = Path(db_path).expanduser()
    if not _owned_private_regular(path):
        raise ValueError("M03_WS3_TIMELINE_SOURCE_UNTRUSTED")
    path = path.absolute()
    raw = {pair: {"first_quote": None, "first_micro": None,
                  "quote_rows": 0, "micro_rows": 0,
                  "quote_exchange_epochs": set()} for pair in pairs}
    placeholders = ",".join("?" for _ in pairs)
    with sqlite3.connect(path.as_uri() + "?mode=ro",
                         uri=True, timeout=3) as db:
        db.execute("PRAGMA query_only=ON")
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "m03_ws_summary_watch_minute" not in tables:
            raise ValueError("M03_WS3_TIMELINE_SOURCE_ABSENT")
        for pair, minute, exchange, price, volume in db.execute(
            "SELECT pair,minute_epoch,exchange_epoch,last_idr,"
            "rolling_24h_idr FROM m03_ws_summary_watch_minute "
            f"WHERE pair IN ({placeholders}) AND minute_epoch BETWEEN ? AND ? "
            "ORDER BY minute_epoch, pair",
            (*pairs, start_epoch, end_epoch),
        ):
            item = raw[pair]
            item["quote_rows"] += 1
            item["quote_exchange_epochs"].add(exchange)
            if item["first_quote"] is None:
                item["first_quote"] = {
                    "minute_epoch": minute, "exchange_epoch": exchange,
                    "price_idr": price, "rolling_24h_idr": volume,
                }
        if "m03_ws_microstructure_watch_minute" in tables:
            for pair, captured, frames, book, gap, truncated, malformed in db.execute(
                "SELECT pair,observed_local_epoch,trade_frames,book_offset,"
                "trade_gap_observed,trade_truncated,source_malformed "
                "FROM m03_ws_microstructure_watch_minute "
                f"WHERE pair IN ({placeholders}) "
                "AND observed_local_epoch BETWEEN ? AND ? "
                "ORDER BY observed_local_epoch,pair",
                (*pairs, start_epoch, end_epoch + 59),
            ):
                item = raw[pair]
                item["micro_rows"] += 1
                if item["first_micro"] is None:
                    item["first_micro"] = {
                        "captured_epoch": captured,
                        "trade_frames": frames,
                        "book_snapshot_recorded": book is not None,
                        "trade_gap": bool(gap),
                        "truncated": bool(truncated),
                        "malformed": bool(malformed),
                    }

    claim_status = "NOT_REQUESTED"
    claims = {}
    if radar_claims_db is not None:
        owner_path = Path(radar_claims_db).expanduser()
        if not _owned_private_regular(owner_path):
            raise ValueError("M03_WS3_TIMELINE_OWNER_DB_UNTRUSTED")
        with sqlite3.connect(owner_path.absolute().as_uri() + "?mode=ro",
                             uri=True, timeout=3) as owner:
            owner.execute("PRAGMA query_only=ON")
            table = owner.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='m05_radar_momentum_attempts'"
            ).fetchone()
            claim_status = ("LAST_CLAIM_ROWS_ONLY_NOT_TELEGRAM_ACK"
                            if table else "RADAR_CLAIM_TABLE_ABSENT")
            if table:
                for pair, claimed, observed in owner.execute(
                    "SELECT pair,claimed_epoch,source_observed_epoch "
                    "FROM m05_radar_momentum_attempts "
                    f"WHERE pair IN ({placeholders}) AND claimed_epoch BETWEEN ? AND ?",
                    (*pairs, start_epoch, end_epoch + 59),
                ):
                    claims[pair] = (claimed, observed)

    first = {pair: {
        "m03_watch_any_rank": None,
        "m05_watch_top5": None,
        "micro_joined_fresh": None,
        "m04_watch_quality_top2": None,
        "m04_micro_prealert_top2": None,
    } for pair in pairs}
    stats = {pair: Counter() for pair in pairs}
    first_quality = {pair: None for pair in pairs}
    for minute in range(start_epoch, end_epoch + 1, 60):
        # Earliest point that can include ALL actual records in this minute.
        # Not the time the live scanner/owner sender actually ran.
        asof = minute + 59
        snap = inspect_original_m03_ws3_watch_readonly(
            db_path=path, now_epoch=asof,
        )
        if snap.get("status") != "M03_WS3_WATCH_RADAR_OBSERVED_UNATTESTED":
            raise ValueError("M03_WS3_TIMELINE_SOURCE_INSPECT_FAILED")
        top40 = {x["pair"]: x
                 for x in snap["ranked_review_shortlist_top40"]}
        # Micro PREALERT can have truthful fresh trade/book data before the
        # 0.5% M05 Telegram WATCH gate. Count that stage independently.
        prealert_source = {x["pair"]: x
                           for x in snap["micro_prealert_candidates_top40"]}
        top5 = {x["pair"] for x in snap["candidates"]}
        m04 = inspect_original_ws3_three_role_handoff_readonly(
            db_path=path, now_epoch=asof,
        )
        if m04.get("status") not in (
                "M04_WS3_HANDOFF_ROLE_INPUTS_PREPARED",
                "M04_WS3_HANDOFF_NO_CURRENT_WATCH"):
            raise ValueError("M03_WS3_TIMELINE_M04_SOURCE_UNAVAILABLE")
        m04_top2 = {
            x["pair"]: x for x in m04.get("candidates", [])
        }
        pre = inspect_original_ws3_micro_prealert_three_role_handoff_readonly(
            db_path=path, now_epoch=asof,
        )
        if pre.get("status") not in (
                "M04_WS3_MICRO_PREALERT_ROLE_INPUTS_PREPARED",
                "M04_WS3_MICRO_PREALERT_NO_CURRENT_SOURCE"):
            raise ValueError("M03_WS3_TIMELINE_PREALERT_SOURCE_UNAVAILABLE")
        pre_top2 = {x["pair"] for x in pre.get("candidates", [])}
        for pair in pairs:
            item = first[pair]
            if pair in top40:
                stats[pair]["m03_watch_top40_minute_ends"] += 1
                if item["m03_watch_any_rank"] is None:
                    item["m03_watch_any_rank"] = asof
            joined = top40.get(pair) or prealert_source.get(pair)
            if joined is not None:
                micro = joined.get("microstructure_observation", {})
                if (micro.get("status") == "PARTIAL_SOURCE_OBSERVATION"
                        and not micro.get("source_malformed")
                        and not micro.get("trade_source_gap_or_truncation")
                        and (micro.get("trade_activity_observed") or
                             micro.get("orderbook_snapshot_observed"))):
                    stats[pair]["fresh_micro_joined_minute_ends"] += 1
                    if pair in prealert_source and pair not in top40:
                        stats[pair]["micro_prealert_before_telegram_minute_ends"] += 1
                    if item["micro_joined_fresh"] is None:
                        item["micro_joined_fresh"] = asof
            if pair in top5:
                stats[pair]["m05_top5_eligible_minute_ends"] += 1
                if item["m05_watch_top5"] is None:
                    item["m05_watch_top5"] = asof
            if pair in m04_top2:
                stats[pair]["m04_top2_minute_ends"] += 1
                if item["m04_watch_quality_top2"] is None:
                    item["m04_watch_quality_top2"] = asof
                    first_quality[pair] = m04_top2[pair][
                        "quality_score_out_of_100"]
            if pair in pre_top2:
                stats[pair]["m04_prealert_top2_minute_ends"] += 1
                if item["m04_micro_prealert_top2"] is None:
                    item["m04_micro_prealert_top2"] = asof

    def wib_time(epoch: int | None) -> str | None:
        return (datetime.fromtimestamp(epoch, WIB).strftime(
            "%Y-%m-%d %H:%M:%S WIB") if epoch is not None else None)

    result_pairs = []
    for pair in pairs:
        src = raw[pair]
        marks = first[pair]
        initial = src["first_quote"]
        captured = src["first_micro"]
        claim = claims.get(pair)
        result_pairs.append({
            "pair": pair,
            "source_quote_rows_in_window": src["quote_rows"],
            "source_micro_rows_in_window": src["micro_rows"],
            "first_persisted_quote_minute_wib": wib_time(
                initial["minute_epoch"] if initial else None),
            "first_quote_exchange_epoch_wib": wib_time(
                initial["exchange_epoch"] if initial else None),
            "first_quote_price_idr": initial["price_idr"] if initial else None,
            "first_raw_micro_capture_wib": wib_time(
                captured["captured_epoch"] if captured else None),
            "first_raw_micro_type": (
                {"trade_frames": captured["trade_frames"],
                 "book_snapshot_recorded": captured["book_snapshot_recorded"],
                 "gap": captured["trade_gap"],
                 "truncated": captured["truncated"],
                 "malformed": captured["malformed"]}
                if captured else None),
            "first_m03_watch_top40_replay_minute_end_wib": wib_time(
                marks["m03_watch_any_rank"]),
            "first_m05_top5_eligible_replay_minute_end_wib": wib_time(
                marks["m05_watch_top5"]),
            "first_fresh_micro_joined_replay_minute_end_wib": wib_time(
                marks["micro_joined_fresh"]),
            "first_m04_quality_top2_replay_minute_end_wib": wib_time(
                marks["m04_watch_quality_top2"]),
            "first_m04_quality_top2_score_observational": first_quality[pair],
            "first_m04_micro_prealert_top2_replay_minute_end_wib": wib_time(
                marks["m04_micro_prealert_top2"]),
            "m04_quality_top2_lag_after_first_m03_watch_seconds": (
                marks["m04_watch_quality_top2"] - marks["m03_watch_any_rank"]
                if marks["m04_watch_quality_top2"] is not None
                and marks["m03_watch_any_rank"] is not None else None),
            "last_retained_m05_radar_claim_wib": wib_time(
                claim[0] if claim else None),
            "last_retained_claim_source_quote_exchange_wib": wib_time(
                claim[1] if claim else None),
            "last_claim_source_epoch_matches_ws3_quote": (
                claim[1] in src["quote_exchange_epochs"]
                if claim else None),
            "telegram_actual_send_ack_wib": None,
            "telegram_delivery_delay_seconds": None,
            "minute_end_replay_counts": dict(sorted(stats[pair].items())),
        })

    return {
        "status": "M03_WS3_ORIGINAL_SOURCE_CHRONOLOGICAL_QUALITY_TIMELINE",
        "window_start_wib": wib_time(start_epoch),
        "window_end_wib": wib_time(end_epoch + 59),
        "source": "SAME_ORIGINAL_M03_WS3_SQLITE_AND_M04_READONLY_INSPECTORS",
        "first_detection_means_first_RETAINED_source_minute_not_listing": True,
        "replay_minute_end_is_not_real_process_trigger_epoch": True,
        "micro_capture_does_not_prove_complete_trade_tape": True,
        "owner_radar_claim_source_status": claim_status,
        "owner_radar_claim_last_row_only_and_not_send_ack": True,
        "telegram_real_delivery_lag_not_measurable_without_M05_ACK_logs": True,
        "m05_original_telegram_top5_threshold_and_cooldowns_unchanged": True,
        "source_retention_can_hide_earlier_pairs_and_events": True,
        "rolling_24h_delta_not_actual_1m_trade_flow": True,
        "historical_lookahead_forbidden": True,
        "exchange_requests": 0,
        "telegram_messages_sent": 0,
        "paid_provider_calls": 0,
        "orders_sent": 0,
        "execution_allowed": False,
        "pairs": result_pairs,
    }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay ONLY original local PUBLIC market observations; NO orders."
    )
    parser.add_argument("--date", required=True, help="WIB date YYYY-MM-DD")
    parser.add_argument("--start", required=True, help="HH:MM WIB")
    parser.add_argument("--end", required=True, help="HH:MM WIB (inclusive)")
    parser.add_argument("--pairs", default=",".join(DEFAULT_PAIRS))
    parser.add_argument("--source", choices=("rest", "ws3"), default="rest",
                        help="Default unchanged REST ticker replay; ws3 is original WS3 source-only timeline")
    parser.add_argument("--radar-claims-db", type=Path, default=None,
                        help="Optional existing original M05 private ledger, last claim only, NOT send ACK; WS3 mode only")
    args = parser.parse_args()
    try:
        date = datetime.strptime(args.date, "%Y-%m-%d").date()
        start = datetime.combine(date, datetime.strptime(args.start, "%H:%M").time(), WIB)
        end = datetime.combine(date, datetime.strptime(args.end, "%H:%M").time(), WIB)
        pairs = tuple(p.strip().lower() for p in args.pairs.split(","))
        if end.timestamp() > time.time() + 59:
            raise ValueError("FUTURE_REPLAY_RANGE")
        if args.source == "rest":
            if args.radar_claims_db is not None:
                raise ValueError("REST_REPLAY_CLAIMS_OPTION_NOT_SUPPORTED")
            result = replay_original_public_watch_readonly(
                start_epoch=int(start.timestamp()),
                end_epoch=int(end.timestamp()),
                pairs=pairs,
            )
        else:
            result = replay_original_ws3_quality_timeline_readonly(
                start_epoch=int(start.timestamp()),
                end_epoch=int(end.timestamp()),
                pairs=pairs, radar_claims_db=args.radar_claims_db,
            )
    except (ValueError, OverflowError):
        print(json.dumps({
            "status": "M05_RADAR_REPLAY_BLOCKED",
            "reason": "INVALID_WINDOW_OR_UNTRUSTED_ORIGINAL_SOURCE",
            "execution_allowed": False,
        }, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
