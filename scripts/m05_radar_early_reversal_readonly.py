"""Review original V2 M03 ticker minute observations before +8% radar WATCH.

READ ONLY local SQLite, exact minute adjacency and original public quote
conditions. A sampled price uptick after a decline is NOT a BUY or evidence
of order flow, closed candles, G1, paid AI or profitability.

Never sends Indodax REST, Telegram messages, order commands, or DB writes.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo

from indodax_ai_bot.m05_radar_momentum_nonlive import (
    DEFAULT_DB, MAX_SPREAD_BPS, MIN_TURNOVER_IDR, PAIR_ID,
    _finite_number, _owned_private_regular,
)
from indodax_ai_bot.scanner import MAIN_EXCLUDED_PAIRS

WIB = ZoneInfo("Asia/Jakarta")


def _time(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, WIB).strftime("%Y-%m-%d %H:%M:%S WIB")


def _pct(a: Decimal, b: Decimal) -> str:
    return format(((a / b) - Decimal(1)) * Decimal(100), ".2f")


def examine_early_reversal_readonly(
    *, start_epoch: int, end_epoch: int, pairs: tuple[str, ...],
    db_path: Path = DEFAULT_DB,
) -> dict:
    """Observed 1m + optional 3m return, never inferred missing candles."""
    if (type(start_epoch) is not int or type(end_epoch) is not int
            or start_epoch <= 0 or start_epoch % 60 != 0
            or end_epoch % 60 != 0 or end_epoch < start_epoch
            or end_epoch - start_epoch > 30 * 60
            or type(pairs) is not tuple or not 1 <= len(pairs) <= 5
            or len(set(pairs)) != len(pairs)
            or any(type(p) is not str or not PAIR_ID.fullmatch(p)
                   or p in MAIN_EXCLUDED_PAIRS for p in pairs)):
        raise ValueError("M05_EARLY_REVERSAL_INVALID_SCOPE")
    path = Path(db_path)
    if not _owned_private_regular(path):
        raise ValueError("M05_EARLY_REVERSAL_SOURCE_NOT_TRUSTED")

    placeholders = ",".join("?" for _ in pairs)
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
            db.execute("PRAGMA query_only=ON")
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='public_ticker_minute_samples'"
            ).fetchone():
                raise ValueError("M05_EARLY_REVERSAL_SOURCE_ABSENT")
            raw = db.execute(
                "SELECT pair,minute_epoch,observed_epoch,last_idr,"
                "volume_24h_idr,spread_bps "
                "FROM public_ticker_minute_samples "
                f"WHERE pair IN ({placeholders}) "
                "AND minute_epoch BETWEEN ? AND ? "
                "ORDER BY pair,minute_epoch",
                (*pairs, start_epoch - 2 * 60, end_epoch),
            ).fetchall()
    except (sqlite3.Error, OSError):
        raise ValueError("M05_EARLY_REVERSAL_SOURCE_UNAVAILABLE") from None

    by_pair = {pair: {} for pair in pairs}
    for pair, minute, observed, price_raw, volume_raw, spread_raw in raw:
        if (pair not in by_pair or type(minute) is not int
                or minute % 60 != 0 or type(observed) is not int
                or not minute <= observed < minute + 60):
            continue
        price = _finite_number(price_raw, positive=True)
        volume = _finite_number(volume_raw, positive=True)
        spread = _finite_number(spread_raw)
        if price is None or volume is None or spread is None:
            continue
        by_pair[pair][minute] = (
            observed, price, volume, spread,
        )

    output = []
    for pair in pairs:
        observations = by_pair[pair]
        timeline = []
        first_reversal = None
        first_percent_tiers = {tier: None for tier in ("1", "2", "3")}
        missing_minutes = 0
        for minute in range(start_epoch, end_epoch + 1, 60):
            latest = observations.get(minute)
            if latest is None:
                missing_minutes += 1
                continue
            prev = observations.get(minute - 60)
            prev2 = observations.get(minute - 120)
            prev3 = observations.get(minute - 180)
            price = latest[1]
            current_change = (_pct(price, prev[1]) if prev is not None else None)
            three_min_change = (_pct(price, prev3[1])
                                if prev3 is not None else None)
            previous_change = (
                (prev[1] / prev2[1] - 1) * 100
                if prev is not None and prev2 is not None else None
            )
            current_price_change = (
                (price / prev[1] - 1) * 100 if prev is not None else None
            )
            turnover_pass = latest[2] >= MIN_TURNOVER_IDR
            spread_pass = latest[3] <= MAX_SPREAD_BPS
            reversal = (
                turnover_pass and spread_pass
                and prev is not None and prev2 is not None
                and current_price_change is not None
                and previous_change is not None
                and previous_change < 0 and current_price_change > 0
            )
            sample = {
                "observed_at_wib": _time(latest[0]),
                "minute_wib": _time(minute),
                "last_idr": str(price),
                "return_1m_sample_pct": current_change,
                "return_3m_sample_pct": three_min_change,
                "rolling_24h_idr": str(latest[2]),
                "spread_bps": str(latest[3]),
                "turnover_floor_pass": turnover_pass,
                "spread_limit_pass": spread_pass,
                "exact_previous_minute_sample": prev is not None,
                "positive_1m_after_negative_1m_observed": reversal,
            }
            timeline.append(sample)
            if reversal:
                if first_reversal is None:
                    first_reversal = sample
                for tier in first_percent_tiers:
                    if first_percent_tiers[tier] is None and (
                        current_price_change >= Decimal(tier)):
                        first_percent_tiers[tier] = sample

        output.append({
            "pair": pair,
            "first_positive_1m_after_negative_1m": first_reversal,
            "first_reversal_by_1m_sample_pct_threshold": first_percent_tiers,
            "observed_minute_count": len(timeline),
            "missing_minute_count": missing_minutes,
            "timeline": timeline,
        })

    return {
        "status": "M05_EARLY_REVERSAL_PUBLIC_OBSERVATION_ONLY",
        "window_start_wib": _time(start_epoch),
        "window_end_wib": _time(end_epoch + 59),
        "asof_lookahead": False,
        "source": "ORIGINAL_M03_TICKER_MINUTE_SQLITE_ONLY",
        "sample_returns_are_not_closed_candles": True,
        "rolling_24h_volume_is_not_executed_1m_flow": True,
        "reversal_is_not_predictive_or_buy_signal": True,
        "threshold_tiers_are_retrospective_diagnostics_only": True,
        "source_issuer_g1_verified": False,
        "actual_market_risk_strategy_ai_calls": 0,
        "exchange_requests": 0,
        "telegram_messages_sent": 0,
        "order_sent": False,
        "execution_allowed": False,
        "pairs": output,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=(
        "Early public sampled-price reversal observations ONLY, never BUY"
    ))
    parser.add_argument("--date", required=True, help="YYYY-MM-DD WIB")
    parser.add_argument("--start", required=True, help="HH:MM WIB")
    parser.add_argument("--end", required=True, help="HH:MM WIB")
    parser.add_argument("--pairs", default="uai_idr,fun_idr,aster_idr")
    args = parser.parse_args()
    try:
        date = datetime.strptime(args.date, "%Y-%m-%d").date()
        start = datetime.combine(date, datetime.strptime(args.start, "%H:%M").time(), WIB)
        end = datetime.combine(date, datetime.strptime(args.end, "%H:%M").time(), WIB)
        if end.timestamp() > time.time() + 59:
            raise ValueError("FUTURE_WINDOW")
        pairs = tuple(s.strip().lower() for s in args.pairs.split(","))
        result = examine_early_reversal_readonly(
            start_epoch=int(start.timestamp()),
            end_epoch=int(end.timestamp()), pairs=pairs,
        )
    except (ValueError, OverflowError):
        print(json.dumps({
            "status": "M05_EARLY_REVERSAL_BLOCKED",
            "reason": "INVALID_OR_UNTRUSTED_SOURCE",
            "execution_allowed": False,
        }, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
