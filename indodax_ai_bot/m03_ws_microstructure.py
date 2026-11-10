"""Documented Indodax PUBLIC WS3 per-pair trade and book contracts.

Strict observational facts only. These snapshots cannot certify complete
trade tape, aggressor classification, independent G1, fills or orders.
No socket, network, secrets, secondary DB, or paid model access here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any

_PAIR = re.compile(r"[a-z0-9]{2,24}_idr\Z")
_MAX_TRADE_ROWS = 200
_MAX_BOOK_LEVELS = 100


class WS3MicrostructureInvalid(ValueError):
    """Redacted fixed public-source validation code."""


def _decimal(value: Any, *, zero: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(
            value, (str, int, float, Decimal)):
        raise WS3MicrostructureInvalid("WS3_MICRO_NUMBER_INVALID")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise WS3MicrostructureInvalid("WS3_MICRO_NUMBER_INVALID") from None
    if (not number.is_finite()
            or (number < 0 if zero else number <= 0)
            or len(number.as_tuple().digits) > 40
            or not -24 <= number.adjusted() <= 24):
        raise WS3MicrostructureInvalid("WS3_MICRO_NUMBER_INVALID")
    return number


def _frame(payload: dict, *, channel: str) -> tuple[Any, int]:
    if not isinstance(payload, dict):
        raise WS3MicrostructureInvalid("WS3_MICRO_ENVELOPE_INVALID")
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("channel") != channel:
        raise WS3MicrostructureInvalid("WS3_MICRO_CHANNEL_INVALID")
    wrapper = result.get("data")
    if not isinstance(wrapper, dict):
        raise WS3MicrostructureInvalid("WS3_MICRO_DATA_INVALID")
    offset = wrapper.get("offset")
    if type(offset) is not int or offset < 0:
        raise WS3MicrostructureInvalid("WS3_MICRO_OFFSET_INVALID")
    return wrapper.get("data"), offset


def _channel(pair: str, kind: str) -> str:
    if (type(pair) is not str or not _PAIR.fullmatch(pair)
            or kind not in ("trade-activity", "order-book")):
        raise WS3MicrostructureInvalid("WS3_MICRO_PAIR_INVALID")
    return "market:" + kind + "-" + pair.replace("_", "")


def trade_channel(pair: str) -> str:
    return _channel(pair, "trade-activity")


def book_channel(pair: str) -> str:
    return _channel(pair, "order-book")


def parse_trade_activity_frame(payload: dict, *, pair: str) -> dict:
    """Only actual published transactions, not one-minute market totals.

    Official [pair, epoch_s, sequence, side, price, idr_amount, base_amount].
    Published 'buy'/'sell' is a feed SIDE, not maker/taker or proof of
    aggressive buyers/sellers. Offset continuity is a separate issue.
    """
    raw, offset = _frame(payload, channel=trade_channel(pair))
    if (not isinstance(raw, list) or len(raw) > _MAX_TRADE_ROWS):
        raise WS3MicrostructureInvalid("WS3_MICRO_TRADE_ROWS_INVALID")
    trades = []
    base = pair[:-4] + "idr"
    for row in raw:
        if not isinstance(row, list) or len(row) != 7:
            raise WS3MicrostructureInvalid("WS3_MICRO_TRADE_ROW_INVALID")
        market, epoch, seq, side, price, idr_amount, base_amount = row
        if (market != base or type(epoch) is not int or epoch <= 0
                or type(seq) is not int or seq < 0
                or side not in ("buy", "sell")):
            raise WS3MicrostructureInvalid("WS3_MICRO_TRADE_FIELDS_INVALID")
        price = _decimal(price)
        idr_amount = _decimal(idr_amount)
        base_amount = _decimal(base_amount)
        trades.append({
            "pair": pair, "exchange_epoch": epoch,
            "sequence": seq, "source_side": side,
            "price_idr": format(price, "f"),
            "source_reported_idr_amount": format(idr_amount, "f"),
            "source_reported_base_amount": format(base_amount, "f"),
        })
    return {"channel": trade_channel(pair), "offset": offset,
            "trades": trades, "complete_trade_tape_verified": False,
            "aggressor_side_verified": False, "orders_sent": 0}


def parse_orderbook_frame(payload: dict, *, pair: str) -> dict:
    """Source book snapshot with locally inferred best bid/ask and depth.

    Official book has NO exchange timestamp, so freshness is local receive
    time and the snapshot cannot prove absence of subsequent order changes.
    """
    raw, offset = _frame(payload, channel=book_channel(pair))
    if (not isinstance(raw, dict)
            or raw.get("pair") != pair.replace("_", "")):
        raise WS3MicrostructureInvalid("WS3_MICRO_BOOK_PAIR_INVALID")

    def side(name: str) -> list[tuple[Decimal, Decimal]]:
        levels = raw.get(name)
        if (not isinstance(levels, list)
                or not 1 <= len(levels) <= _MAX_BOOK_LEVELS):
            raise WS3MicrostructureInvalid("WS3_MICRO_BOOK_SIDE_INVALID")
        seen_prices = set()
        out = []
        for level in levels:
            if not isinstance(level, dict):
                raise WS3MicrostructureInvalid("WS3_MICRO_BOOK_LEVEL_INVALID")
            price = _decimal(level.get("price"))
            volume = _decimal(level.get("idr_volume"), zero=True)
            if price in seen_prices:
                raise WS3MicrostructureInvalid("WS3_MICRO_BOOK_DUPLICATE_PRICE")
            seen_prices.add(price)
            out.append((price, volume))
        out.sort(key=lambda x: x[0], reverse=(name == "bid"))
        return out

    bids, asks = side("bid"), side("ask")
    bid, ask = bids[0][0], asks[0][0]
    if bid >= ask:
        raise WS3MicrostructureInvalid("WS3_MICRO_BOOK_CROSSED_OR_LOCKED")
    spread = (ask - bid) / ((ask + bid) / 2) * 10000
    bid5 = sum((v for _, v in bids[:5]), Decimal(0))
    ask5 = sum((v for _, v in asks[:5]), Decimal(0))
    return {
        "pair": pair, "offset": offset,
        "best_bid_idr": format(bid, "f"),
        "best_ask_idr": format(ask, "f"),
        "spread_bps": format(spread, ".4f"),
        "bid_depth_top5_idr": format(bid5, "f"),
        "ask_depth_top5_idr": format(ask5, "f"),
        "book_imbalance_top5_ratio": (
            format(bid5 / ask5, ".4f") if ask5 > 0 else None
        ),
        "depth_levels_bid_observed": len(bids),
        "depth_levels_ask_observed": len(asks),
        "exchange_timestamp_available": False,
        "independent_g1_verified": False,
        "execution_allowed": False,
    }
