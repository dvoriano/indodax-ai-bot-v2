"""OFFLINE research parser for officially documented Indodax WS market summary.

Only parses supplied dict fixtures. No socket, HTTP, fallback activation,
SQLite writer, modified scanner, order or G1/AI authority. This summary
contains price and rolling 24h turnover, NOT best bid/ask, trades or 1m candles.
Ref: btcid/indodax-official-api-docs/Marketdata-websocket.md
channel "market:summary-24h", data.data records.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

_CHANNEL = "market:summary-24h"
_PAIR = re.compile(r"[a-z0-9]{2,24}idr\Z")
_MAX_ROWS = 800


class MarketSummaryContractError(ValueError):
    """Not an authorization/transport error; invalid offline input only."""


def _decimal(raw, *, positive: bool) -> str:
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
        raise MarketSummaryContractError("WS_SUMMARY_NUMBER_INVALID")
    try:
        n = Decimal(str(raw))
    except (ValueError, InvalidOperation):
        raise MarketSummaryContractError("WS_SUMMARY_NUMBER_INVALID") from None
    if not n.is_finite() or (n <= 0 if positive else n < 0):
        raise MarketSummaryContractError("WS_SUMMARY_NUMBER_INVALID")
    return format(n, "f")


def normalize_documented_market_summary_offline(payload: dict) -> dict:
    """Strictly validate documented pair/time/price/volume shape, no I/O.

    Output is deliberately NOT a DailyPairScanner ScreeningResult and must
    never be written into original ticker_all/closed candle or G1 tables.
    """
    if not isinstance(payload, dict) or set(payload) != {"result"}:
        raise MarketSummaryContractError("WS_SUMMARY_ENVELOPE_INVALID")
    result = payload["result"]
    if not isinstance(result, dict) or result.get("channel") != _CHANNEL:
        raise MarketSummaryContractError("WS_SUMMARY_CHANNEL_INVALID")
    data = result.get("data")
    if not isinstance(data, dict):
        raise MarketSummaryContractError("WS_SUMMARY_DATA_INVALID")
    rows = data.get("data")
    offset = data.get("offset")
    if (not isinstance(rows, list)
            or not 1 <= len(rows) <= _MAX_ROWS
            or type(offset) is not int or offset < 0):
        raise MarketSummaryContractError("WS_SUMMARY_FRAME_INVALID")
    seen = set()
    normalized = []
    for row in rows:
        # Official format:
        # pair, epoch-second, last, low24h, high24h, prev24h,
        # turnover24h_IDR, volume24h_coin
        if not isinstance(row, list) or len(row) != 8:
            raise MarketSummaryContractError("WS_SUMMARY_ROW_SHAPE_INVALID")
        pair, stamp, last, low, high, previous, idr, coin = row
        if (type(pair) is not str or not _PAIR.fullmatch(pair)
                or type(stamp) is not int or stamp <= 0):
            raise MarketSummaryContractError("WS_SUMMARY_PAIR_OR_TIME_INVALID")
        if pair in seen:
            raise MarketSummaryContractError("WS_SUMMARY_DUPLICATE_PAIR")
        seen.add(pair)
        last, low, high, previous = (
            _decimal(x, positive=True) for x in (last, low, high, previous)
        )
        vol_idr = _decimal(idr, positive=False)
        vol_coin = _decimal(coin, positive=False)
        if not Decimal(low) <= Decimal(last) <= Decimal(high):
            raise MarketSummaryContractError("WS_SUMMARY_PRICE_ORDER_INVALID")
        normalized.append({
            "pair": pair[:-3] + "_idr",
            "observed_exchange_epoch": stamp,
            "last_idr": last,
            "low_24h_idr": low,
            "high_24h_idr": high,
            "previous_24h_idr": previous,
            "volume_24h_idr": vol_idr,
            "volume_24h_coin": vol_coin,
        })
    return {
        "status": "OFFLINE_PUBLIC_WS_MARKET_SUMMARY_UNATTESTED",
        "channel": _CHANNEL,
        "offset": offset,
        "pairs_parsed": len(normalized),
        "samples": normalized,
        "official_reference_only": True,
        "not_a_live_source": True,
        "missing_best_bid_ask": True,
        "missing_spread_and_depth": True,
        "missing_trade_tape": True,
        "not_verified_closed_candle": True,
        "not_authorized_for_scanner": True,
        "not_authorized_for_g1_or_ai": True,
        "orders_sent": 0,
        "network_requests": 0,
    }

def review_documented_market_summary_sequence_offline(
    frames: object, *, now_epoch: int,
    excluded_pairs: frozenset[str] = frozenset(),
    max_candidates: int = 30,
) -> dict:
    """Compare exactly two supplied WS3 summary fixtures for WATCH research.

    No WS subscription, REST fallback, persistence, trade tape, bid/ask,
    true minute turnover, G1 receipt, AI provider, Telegram or execution.
    Offset growth only rejects obvious replay; it does NOT prove no gaps.
    An exchange timestamp only describes a supplied untrusted fixture.
    """
    if (type(now_epoch) is not int or now_epoch <= 0
            or type(max_candidates) is not int
            or not 20 <= max_candidates <= 40
            or not isinstance(excluded_pairs, frozenset)
            or any(type(p) is not str for p in excluded_pairs)):
        raise MarketSummaryContractError("WS_SUMMARY_REVIEW_OPTIONS_INVALID")
    if type(frames) is not list or len(frames) != 2:
        raise MarketSummaryContractError("WS_SUMMARY_REVIEW_TWO_FRAMES_REQUIRED")
    first = normalize_documented_market_summary_offline(frames[0])
    second = normalize_documented_market_summary_offline(frames[1])
    previous_offset = first["offset"]
    latest_offset = second["offset"]
    if latest_offset <= previous_offset:
        raise MarketSummaryContractError("WS_SUMMARY_REPLAY_OR_OFFSET_REWIND")
    before = {row["pair"]: row for row in first["samples"]}
    candidates = []
    skipped = {
        "no_previous_pair": 0,
        "excluded_pair": 0,
        "stale_or_future_exchange_time": 0,
        "pair_time_gap_or_replay": 0,
        "below_rolling_turnover_floor": 0,
        "nonpositive_observed_price_change": 0,
    }
    for row in second["samples"]:
        pair = row["pair"]
        if pair in excluded_pairs:
            skipped["excluded_pair"] += 1
            continue
        old = before.get(pair)
        if old is None:
            skipped["no_previous_pair"] += 1
            continue
        stamp = row["observed_exchange_epoch"]
        prev_stamp = old["observed_exchange_epoch"]
        if not 0 <= now_epoch - stamp <= 90:
            # Conservative: even +1s future timestamps are not trustworthy
            # for this offline candidate comparison.
            skipped["stale_or_future_exchange_time"] += 1
            continue
        if not 0 < stamp - prev_stamp <= 300:
            skipped["pair_time_gap_or_replay"] += 1
            continue
        turnover = Decimal(row["volume_24h_idr"])
        if turnover < Decimal("150000000"):
            skipped["below_rolling_turnover_floor"] += 1
            continue
        first_last = Decimal(old["last_idr"])
        last = Decimal(row["last_idr"])
        if last <= first_last:
            skipped["nonpositive_observed_price_change"] += 1
            continue
        pct = (last - first_last) * Decimal(100) / first_last
        rolling_delta = turnover - Decimal(old["volume_24h_idr"])
        candidates.append({
            "pair": pair,
            "earlier_exchange_epoch": prev_stamp,
            "later_exchange_epoch": stamp,
            "observed_seconds": stamp - prev_stamp,
            "price_change_percent_between_two_samples": format(pct, "f"),
            "rolling_24h_turnover_idr": format(turnover, "f"),
            "change_in_rolling_24h_turnover_idr": format(rolling_delta, "f"),
            "actual_minute_buy_sell_flow_verified": False,
            "bid_ask_spread_verified": False,
            "trade_authorized": False,
        })
    candidates.sort(key=lambda r: (
        -Decimal(r["price_change_percent_between_two_samples"]),
        -Decimal(r["change_in_rolling_24h_turnover_idr"]),
        -Decimal(r["rolling_24h_turnover_idr"]),
        r["pair"],
    ))
    return {
        "status": "OFFLINE_WS_SUMMARY_M03_WATCH_RESEARCH_UNATTESTED",
        "source": "INDODAX_WS3_MARKET_SUMMARY_24H_OFFLINE_FIXTURES",
        "channel": _CHANNEL,
        "first_offset": previous_offset,
        "last_offset": latest_offset,
        "offset_increased_without_sequence_proof": True,
        "frame_gap_completeness_verified": False,
        "first_frame_pairs": first["pairs_parsed"],
        "last_frame_pairs": second["pairs_parsed"],
        "qualified_watch_candidate_count": min(len(candidates), max_candidates),
        "candidate_research_rows": candidates[:max_candidates],
        "skipped_pair_counts": skipped,
        "watch_only_not_buy_signal": True,
        "rolling_24h_turnover_delta_is_not_minute_traded_volume": True,
        "missing_bid_ask_spread_depth_and_trade_tape": True,
        "no_verified_closed_1m_5m_15m_candles": True,
        "untrusted_fixture_timestamps_not_live_source": True,
        "not_authorized_for_original_rest_scanner": True,
        "g1_receipt_issuance_allowed": False,
        "g2_provider_call_allowed": False,
        "owner_final_decision": None,
        "execution_allowed": False,
        "orders_sent": 0,
        "telegram_messages_sent": 0,
        "network_requests": 0,
        "sqlite_writes": 0,
    }
