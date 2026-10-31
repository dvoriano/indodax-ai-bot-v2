"""M06 conservative reconciliation of offline account and open order evidence.

All inputs are untrusted until separately authenticated by the Indodax
read-only transport; this module never sends requests or authorizes trading.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from .m06_private_payloads import AccountBalances, OrderRecord
from .m06_shadow_account import _amount


class M06ReconciliationBlocked(ValueError):
    pass


@dataclass(frozen=True)
class CapitalReconciliation:
    free_idr: Decimal
    exchange_locked_idr: Decimal
    protected_rebuy_idr: Decimal
    other_unlocked_commitments_idr: Decimal
    local_shadow_commitments_idr: Decimal
    calculated_available_idr: Decimal
    missing_open_order_notional: bool
    source_authenticated: bool = False
    cross_bot_verified: bool = False
    execution_allowed: bool = False
    status: str = "REJECT_UNVERIFIED_CAPITAL"


def reconcile_unverified_capital(*, account: AccountBalances,
        open_orders: tuple[OrderRecord, ...], rebuy_idr: Decimal,
        unlocked_commitments_idr: Decimal, local_shadow_idr: Decimal,
        all_pairs_open_orders_complete: bool = False) -> CapitalReconciliation:
    """Conservative local arithmetic; never double subtract exchange locked.

    We do not assume remaining BUY order quantity can be valued at last trade
    price. Unpriced open BUYs require separate exchange order notional/holds.
    """
    if type(account) is not AccountBalances or type(open_orders) is not tuple:
        raise M06ReconciliationBlocked("M06_RECONCILE_SOURCE_INVALID")
    for value in (rebuy_idr,unlocked_commitments_idr,local_shadow_idr):
        try:
            _amount(value)
        except ValueError:
            raise M06ReconciliationBlocked("M06_RECONCILE_AMOUNT_INVALID") from None
    if any(type(order) is not OrderRecord for order in open_orders):
        raise M06ReconciliationBlocked("M06_RECONCILE_ORDER_INVALID")
    if any(order.executed_qty>order.original_qty for order in open_orders):
        raise M06ReconciliationBlocked("M06_RECONCILE_OVERFILLED")
    pending_unpriced=any(order.side=="BUY" and order.executed_qty<order.original_qty
                         for order in open_orders)
    # Free IDR already excludes locked IDR. Subtract only independent holds.
    available=max(Decimal("0"),account.free_idr-rebuy_idr
                  -unlocked_commitments_idr-local_shadow_idr)
    return CapitalReconciliation(
        account.free_idr, account.locked_idr,rebuy_idr,
        unlocked_commitments_idr,local_shadow_idr,available,
        pending_unpriced or not all_pairs_open_orders_complete,
    )
