"""M06 read-only cross-source sanity checks; no ownership or completeness claim.

Indodax openOrders uses numeric orderId plus fullOrderId; history and
myTrades use the full ID. Matching records is diagnostic ONLY. The endpoints
are sampled at different moments and history defaults to 24 hours.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal

from .m06_private_payloads import OrderRecord, FillRecord


class M06ConsistencyBlocked(ValueError):
    pass


@dataclass(frozen=True)
class M06CrossSourceReview:
    status: str
    matched_fill_count: int
    unmatched_fill_count: int
    conflicting_record_count: int
    history_complete: bool = False
    bot_owned_fills_verified: bool = False
    cross_bot_ownership_verified: bool = False
    execution_allowed: bool = False


def inspect_cross_source_consistency(*, open_orders: tuple[OrderRecord, ...],
        closed_orders: tuple[OrderRecord, ...],
        fills: tuple[FillRecord, ...], symbol: str) -> M06CrossSourceReview:
    """Conservative matching; refuse contradictions, never infer bot ownership."""
    if (type(open_orders) is not tuple or type(closed_orders) is not tuple
            or type(fills) is not tuple or type(symbol) is not str
            or not symbol.endswith("idr")):
        raise M06ConsistencyBlocked("M06_CROSS_SOURCE_INVALID")
    if (any(type(x) is not OrderRecord for x in open_orders+closed_orders)
            or any(type(x) is not FillRecord for x in fills)):
        raise M06ConsistencyBlocked("M06_CROSS_SOURCE_TYPES_INVALID")
    index: dict[str, OrderRecord] = {}
    for order in open_orders:
        if order.symbol != symbol:
            continue
        if not order.full_order_id:
            raise M06ConsistencyBlocked("M06_OPEN_FULL_ORDER_ID_REQUIRED")
        key = order.full_order_id
        if key in index:
            raise M06ConsistencyBlocked("M06_ORDER_ID_COLLISION")
        index[key] = order
    for order in closed_orders:
        if order.symbol != symbol:
            raise M06ConsistencyBlocked("M06_CLOSED_PAIR_MISMATCH")
        if order.order_id in index:
            # A time-drifting snapshot can show same order as OPEN and CLOSED.
            # Do not pick one version or infer it is settled.
            raise M06ConsistencyBlocked("M06_ORDER_SNAPSHOT_TIME_DRIFT")
        index[order.order_id] = order
    seen: set[str] = set()
    per_order_qty: dict[str, Decimal] = {}
    matched = 0
    unmatched = 0
    for fill in fills:
        if fill.symbol != symbol or fill.trade_id in seen:
            raise M06ConsistencyBlocked("M06_FILL_PAIR_OR_DUPLICATE")
        seen.add(fill.trade_id)
        order = index.get(fill.order_id)
        if order is None:
            unmatched += 1  # 24h window / other prior lifecycle; not bot-owned.
            continue
        if (fill.client_order_id is not None and order.client_order_id is not None
                and fill.client_order_id != order.client_order_id):
            raise M06ConsistencyBlocked("M06_FILL_CLIENT_ID_CONFLICT")
        if (fill.is_buyer is not None and
                fill.is_buyer != (order.side == "BUY")):
            raise M06ConsistencyBlocked("M06_FILL_SIDE_CONFLICT")
        per_order_qty[fill.order_id] = (
            per_order_qty.get(fill.order_id, Decimal("0")) + fill.quantity
        )
        if per_order_qty[fill.order_id] > order.executed_qty:
            raise M06ConsistencyBlocked("M06_FILL_QTY_EXCEEDS_SNAPSHOT")
        matched += 1
    return M06CrossSourceReview(
        status="REJECT_PARTIAL_PRIVATE_HISTORY_UNATTESTED",
        matched_fill_count=matched,
        unmatched_fill_count=unmatched,
        conflicting_record_count=0,
    )
