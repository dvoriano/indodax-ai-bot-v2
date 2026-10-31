"""Strict Indodax V2 private payload parsing, offline and never authority.

Official TAPI v2 schemas. Parsing does NOT authenticate origin, establish
pagination completeness, identify bot-owned lots or authorize expenditure.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from collections.abc import Mapping
from .m06_shadow_account import _amount


class M06PayloadError(ValueError):
    pass


@dataclass(frozen=True)
class AccountBalances:
    uid: int
    assets: tuple[tuple[str, Decimal, Decimal], ...]
    free_idr: Decimal
    locked_idr: Decimal
    exchange_authenticated: bool = False
    source_complete: bool = False
    execution_allowed: bool = False


@dataclass(frozen=True)
class OrderRecord:
    order_id: str
    symbol: str
    side: str
    status: str
    original_qty: Decimal
    executed_qty: Decimal
    client_order_id: str | None = None
    bot_owned_verified: bool = False


@dataclass(frozen=True)
class FillRecord:
    trade_id: str
    order_id: str
    symbol: str
    quantity: Decimal
    price: Decimal
    commission: Decimal
    bot_owned_verified: bool = False


def _decimal(value, field: str, *, positive: bool = False) -> Decimal:
    if type(value) not in (str, int):
        raise M06PayloadError("M06_" + field + "_INVALID")
    try:
        amount = Decimal(value)
        _amount(amount)
    except (InvalidOperation, ValueError, TypeError):
        raise M06PayloadError("M06_" + field + "_INVALID") from None
    if positive and amount <= 0:
        raise M06PayloadError("M06_" + field + "_NONPOSITIVE")
    return amount


def _records(payload, path: str) -> list:
    if isinstance(payload, dict) and "code" in payload and "msg" in payload:
        raise M06PayloadError("M06_EXCHANGE_API_ERROR")
    if type(payload) is not dict or type(payload.get("data")) is not list:
        raise M06PayloadError("M06_" + path + "_SCHEMA_INVALID")
    rows = payload["data"]
    if len(rows) > 1000 or any(type(x) is not dict for x in rows):
        raise M06PayloadError("M06_" + path + "_ROWS_INVALID")
    return rows


def parse_account(payload) -> AccountBalances:
    if type(payload) is not dict or "code" in payload:
        raise M06PayloadError("M06_ACCOUNT_SCHEMA_INVALID")
    uid = payload.get("uid")
    items = payload.get("balances")
    if type(uid) is not int or uid <= 0 or type(items) is not list or len(items) > 2000:
        raise M06PayloadError("M06_ACCOUNT_SCHEMA_INVALID")
    seen = set()
    result = []
    for record in items:
        if type(record) is not dict:
            raise M06PayloadError("M06_BALANCE_RECORD_INVALID")
        asset=record.get("asset")
        if (type(asset) is not str or not 2 <= len(asset) <= 20
                or not asset.isascii() or not asset.isalnum()
                or asset != asset.upper() or asset in seen):
            raise M06PayloadError("M06_ASSET_DUPLICATE_OR_INVALID")
        seen.add(asset)
        free=_decimal(record.get("free"), "FREE")
        locked=_decimal(record.get("locked"), "LOCKED")
        result.append((asset,free,locked))
    if "IDR" not in seen:
        raise M06PayloadError("M06_IDR_BALANCE_MISSING")
    idr=next(x for x in result if x[0]=="IDR")
    return AccountBalances(uid,tuple(result),idr[1],idr[2])


def parse_orders(payload, *, expected_symbol: str) -> tuple[OrderRecord, ...]:
    if type(expected_symbol) is not str or not expected_symbol.endswith("idr"):
        raise M06PayloadError("M06_SYMBOL_INVALID")
    rows=_records(payload, "ORDERS")
    out=[]
    seen=set()
    for r in rows:
        oid=r.get("orderId")
        symbol=r.get("symbol")
        side=r.get("side")
        status=r.get("status")
        if (type(oid) not in (str,int) or not str(oid)
                or type(symbol) is not str or symbol.lower()!=expected_symbol
                or side not in ("BUY","SELL") or type(status) is not str
                or not status or len(status)>32 or str(oid) in seen):
            raise M06PayloadError("M06_ORDER_IDENTITY_INVALID")
        seen.add(str(oid))
        orig=_decimal(r.get("oriQty", r.get("origQty")),"ORIG_QTY")
        executed=_decimal(r.get("executedQty"),"EXEC_QTY")
        if executed>orig:
            raise M06PayloadError("M06_ORDER_OVERFILLED")
        cid=r.get("clientOrderId")
        if cid is not None and (type(cid) is not str or not 1<=len(cid)<=128):
            raise M06PayloadError("M06_CLIENT_ORDER_ID_INVALID")
        out.append(OrderRecord(str(oid),symbol.lower(),side,status,orig,executed,cid))
    return tuple(out)


def parse_fills(payload, *, expected_symbol: str) -> tuple[FillRecord, ...]:
    if type(expected_symbol) is not str or not expected_symbol.endswith("idr"):
        raise M06PayloadError("M06_SYMBOL_INVALID")
    rows=_records(payload,"FILLS")
    out=[]
    seen=set()
    for r in rows:
        trade_id=r.get("tradeId")
        order_id=r.get("orderId")
        symbol=r.get("symbol")
        if (type(trade_id) not in (str,int) or not str(trade_id)
                or type(order_id) not in (str,int) or not str(order_id)
                or type(symbol) is not str or symbol.lower()!=expected_symbol
                or str(trade_id) in seen):
            raise M06PayloadError("M06_FILL_IDENTITY_INVALID")
        seen.add(str(trade_id))
        out.append(FillRecord(str(trade_id),str(order_id),symbol.lower(),
             _decimal(r.get("qty"),"FILL_QTY",positive=True),
             _decimal(r.get("price"),"FILL_PRICE",positive=True),
             _decimal(r.get("commission"),"FILL_COMMISSION")))
    return tuple(out)

def parse_open_orders(payload, *, expected_symbol: str | None = None) -> tuple[OrderRecord, ...]:
    """Official openOrders is a direct list, unlike order histories {data:[]}."""
    if type(payload) is not list or len(payload)>1000:
        raise M06PayloadError("M06_OPEN_ORDERS_SCHEMA_INVALID")
    if expected_symbol is None:
        if payload:
            raise M06PayloadError("M06_ALL_PAIR_OPEN_ORDER_RECONCILIATION_REQUIRED")
        return ()
    if any(type(row) is not dict for row in payload):
        raise M06PayloadError("M06_OPEN_ORDER_RECORD_INVALID")
    return parse_orders({"data": payload}, expected_symbol=expected_symbol)
