"""Pure M07 TAPI V2 READ ONLY myTrades to order fill and fee reconciliation.

Commission already includes fees AND taxes in the official Indodax V2
response. Never tax twice, never turn a historical account SELL into a
bot-owned position, and never approve a new order.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import re

from .m07_private_order_readonly import M07ReadOnlyOrder, M07ReadOnlyError

_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,20})(?:\.[0-9]{1,18})?\Z")
_ID = re.compile(r"[A-Za-z0-9_-]{1,36}\Z")
_DIGEST_ID = re.compile(r"[0-9]{1,30}\Z")


def _number(text: object, field: str) -> Decimal:
    if type(text) is not str or not _DECIMAL.fullmatch(text):
        raise M07ReadOnlyError("M07_FILLS_" + field + "_INVALID")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise M07ReadOnlyError("M07_FILLS_" + field + "_INVALID") from None
    if not value.is_finite() or value < 0 or value >= Decimal("1e24"):
        raise M07ReadOnlyError("M07_FILLS_" + field + "_INVALID")
    return value


@dataclass(frozen=True, repr=False)
class M07VerifiedReadOnlyFills:
    """Matched account fill summary; deliberately NO V2 bot ownership."""
    account_symbol: str
    client_order_id: str
    full_order_id: str
    side: str
    executed_base: Decimal
    gross_quote: Decimal
    total_commission_including_tax_quote: Decimal
    net_quote: Decimal
    count: int
    sha256: str

    def __repr__(self) -> str:
        return "<M07VerifiedReadOnlyFills REDACTED account evidence only>"


def reconcile_m07_mytrades(
    payload: object, *, order: M07ReadOnlyOrder,
    window_start_ms: int, window_end_ms: int,
) -> M07VerifiedReadOnlyFills:
    """Reject missing/partial/truncated/duplicated fills and any fee ambiguity."""
    if (type(order) is not M07ReadOnlyOrder
            or order.status != "FILLED"
            or type(window_start_ms) is not int
            or type(window_end_ms) is not int
            or not window_start_ms <= order.created_ms <= window_end_ms
            or not 0 < window_end_ms - window_start_ms <= 7 * 86400 * 1000):
        raise M07ReadOnlyError("M07_FILLS_ORDER_OR_WINDOW_UNSUPPORTED")
    if (type(payload) is not dict or type(payload.get("data")) is not list
            or type(payload.get("code")) is int):
        raise M07ReadOnlyError("M07_FILLS_RESPONSE_INVALID")
    records = payload["data"]
    if not records or len(records) >= 1000:
        raise M07ReadOnlyError("M07_FILLS_EMPTY_OR_TRUNCATED")
    ids = set()
    entries = []
    with localcontext() as ctx:
        ctx.prec = 64
        base = Decimal(0)
        gross = Decimal(0)
        commission = Decimal(0)
        for row in records:
            if type(row) is not dict:
                raise M07ReadOnlyError("M07_FILLS_ROW_INVALID")
            ident = row.get("tradeId")
            if type(ident) is not str or not _DIGEST_ID.fullmatch(ident) or ident in ids:
                raise M07ReadOnlyError("M07_FILLS_TRADE_ID_DUPLICATED")
            ids.add(ident)
            if (row.get("symbol") != order.symbol.lower()
                    or row.get("clientOrderId") != order.client_order_id
                    or row.get("orderId") != order.full_order_id
                    or type(row.get("isBuyer")) is not bool
                    or row["isBuyer"] != (order.side == "BUY")
                    or type(row.get("isMaker")) is not bool):
                raise M07ReadOnlyError("M07_FILLS_ORDER_IDENTITY_MISMATCH")
            t = row.get("time")
            if (type(t) is not int
                    or not window_start_ms <= t <= window_end_ms):
                raise M07ReadOnlyError("M07_FILLS_TIME_OUTSIDE_RANGE")
            qty = _number(row.get("qty"), "QTY")
            quote = _number(row.get("quoteQty"), "QUOTE")
            fee = _number(row.get("commission"), "COMMISSION")
            price = _number(row.get("price"), "PRICE")
            if (qty <= 0 or quote <= 0 or price <= 0
                    or fee > quote or row.get("commissionAsset") != "idr"):
                raise M07ReadOnlyError("M07_FILLS_NON_IDR_FEE_OR_AMOUNT_INVALID")
            base += qty
            gross += quote
            commission += fee
            entries.append((
                ident, str(qty), str(quote), str(fee), str(price),
                t, bool(row["isMaker"]),
            ))
        if base != order.executed_qty or base <= 0 or commission > gross:
            raise M07ReadOnlyError("M07_FILLS_EXECUTED_QUANTITY_MISMATCH")
        net = gross - commission if order.side == "SELL" else gross + commission
        if net <= 0 or net >= Decimal("1e28"):
            raise M07ReadOnlyError("M07_FILLS_AGGREGATE_INVALID")
    entries.sort(key=lambda x: x[0])
    digest = hashlib.sha256(json.dumps(
        (order.full_order_id, order.client_order_id, order.side,
         entries), separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")).hexdigest()
    return M07VerifiedReadOnlyFills(
        account_symbol=order.symbol,
        client_order_id=order.client_order_id,
        full_order_id=order.full_order_id,
        side=order.side,
        executed_base=base, gross_quote=gross,
        total_commission_including_tax_quote=commission,
        net_quote=net, count=len(entries), sha256=digest,
    )
