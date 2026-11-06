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
import time
from zoneinfo import ZoneInfo

from indodax_ai_bot.m05_radar_momentum_nonlive import (
    DEFAULT_DB, PAIR_ID, inspect_original_m03_radar_readonly,
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
            if reason == "IN_TOP5" and first_top5[pair] is None:
                first_top5[pair] = asof
                found = present[pair]
                example[pair] = {
                    "watch_type": found["watch_type"],
                    "observed_rise_pct": found["observed_rise_pct"],
                    "priority": found["pre_ai_review"]["review_priority"],
                    "sample_age_seconds": found["sample_age_seconds"],
                    "spread_bps": found["spread_bps"],
                    "g1_verified": False,
                    "market_risk_strategy_ai_calls": 0,
                }

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
            "asof_minute_counts_by_reason": dict(sorted(counts[pair].items())),
            "first_top5_observed_evidence": example[pair],
        } for pair in pairs],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay ONLY original local PUBLIC market observations; NO orders."
    )
    parser.add_argument("--date", required=True, help="WIB date YYYY-MM-DD")
    parser.add_argument("--start", required=True, help="HH:MM WIB")
    parser.add_argument("--end", required=True, help="HH:MM WIB (inclusive)")
    parser.add_argument("--pairs", default=",".join(DEFAULT_PAIRS))
    args = parser.parse_args()
    try:
        date = datetime.strptime(args.date, "%Y-%m-%d").date()
        start = datetime.combine(date, datetime.strptime(args.start, "%H:%M").time(), WIB)
        end = datetime.combine(date, datetime.strptime(args.end, "%H:%M").time(), WIB)
        pairs = tuple(p.strip().lower() for p in args.pairs.split(","))
        if end.timestamp() > time.time() + 59:
            raise ValueError("FUTURE_REPLAY_RANGE")
        result = replay_original_public_watch_readonly(
            start_epoch=int(start.timestamp()),
            end_epoch=int(end.timestamp()),
            pairs=pairs,
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
