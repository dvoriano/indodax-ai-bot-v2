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
