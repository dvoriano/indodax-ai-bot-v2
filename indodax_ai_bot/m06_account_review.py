"""M06 original V2 offline account review: reject-only, no exchange access.

This is not a substitute for an authenticated private source. No pretend fills,
ownership attribution, free-wallet promotion, private API, or LIVE authority.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from .policy import ScalpingPolicy
from .m06_shadow_account import AccountObservation, M06ShadowCapital, _amount
from .m06_private_payloads import AccountBalances, OrderRecord, FillRecord
from .m06_capital_reconcile import reconcile_unverified_capital
from .m06_source_consistency import inspect_cross_source_consistency
from .m06_ownership_ledger import ProtectedRebuyReview


@dataclass(frozen=True)
class M06AccountDecision:
    status: str
    reason: str
    free_idr_unverified: Decimal
    shadow_spendable_idr: Decimal
    orders_complete: bool = False
    fills_complete: bool = False
    account_authenticated: bool = False
    ownership_verified: bool = False
    cross_bot_coexistence_verified: bool = False
    execution_allowed: bool = False


def review_unverified_account(observation: AccountObservation | None,
                              *, now_epoch: int) -> M06AccountDecision:
    """Immutable offline preview, explicitly NOT spendable exchange authority."""
    if type(now_epoch) is not int or now_epoch <= 0:
        raise ValueError("M06_REVIEW_EPOCH_INVALID")
    if observation is None:
        return M06AccountDecision("REJECT", "M06_PRIVATE_ACCOUNT_GO_ABSENT",
                                  Decimal("0"), Decimal("0"))
    M06ShadowCapital._validate(observation,now_epoch)
    capital=ScalpingPolicy().review_main_capital(
        free_idr=observation.free_idr,
        locked_idr=observation.locked_idr,
        floki_rebuy_idr=observation.floki_rebuy_idr,
        pepe_rebuy_idr=observation.pepe_rebuy_idr,
        commitments_not_locked_idr=observation.other_unlocked_commitments_idr,
    )
    return M06AccountDecision(
        "REJECT", "M06_ACCOUNT_OBSERVATION_UNAUTHENTICATED",
        observation.free_idr,capital.spendable_idr,
        orders_complete=False, fills_complete=False,
    )

@dataclass(frozen=True)
class M06ReadOnlyReconciliation:
    """Original V2 M06: diagnostics are not source or wallet authority."""
    status: str
    reason: str
    observed_idr_balance: bool
    all_pair_open_orders_seen: int
    pending_buy_orders: int
    matched_fills_in_window: int
    unmatched_fills_in_window: int
    flo_pepe_holdings_unattributed: bool
    local_ledger_account_bound: bool = False
    local_rebuy_hold_present: bool = False
    local_manual_floor_present: bool = False
    protected_rebuy_ledger_verified: bool = False
    manual_asset_ownership_verified: bool = False
    account_wide_order_history_complete: bool = False
    cross_bot_ownership_verified: bool = False
    spendable_capital_verified: bool = False
    execution_allowed: bool = False


def reconcile_readonly_private_snapshot(*, account: AccountBalances,
        open_orders: tuple[OrderRecord, ...],
        order_history: tuple[OrderRecord, ...],
        fills: tuple[FillRecord, ...], symbol: str,
        local_rebuy: ProtectedRebuyReview | None = None) -> M06ReadOnlyReconciliation:
    """Join four parsed endpoints through existing M06 policy/consistency.

    No locally certified rebuy ledger, account-wide history or cross-bot
    coordinator is available in this source. Hence *no* positive spendable
    balance can be issued, even after a successful view-only HTTPS response.
    """
    if type(account) is not AccountBalances:
        raise ValueError("M06_ACCOUNT_RECONCILIATION_SOURCE_INVALID")
    if (type(open_orders) is not tuple
            or type(order_history) is not tuple or type(fills) is not tuple):
        raise ValueError("M06_ACCOUNT_RECONCILIATION_COVERAGE_INVALID")
    consistency = inspect_cross_source_consistency(
        open_orders=open_orders, closed_orders=order_history,
        fills=fills, symbol=symbol,
    )
    if any(type(order) is not OrderRecord for order in open_orders):
        raise ValueError("M06_ACCOUNT_RECONCILIATION_ORDER_INVALID")
    if local_rebuy is not None:
        if (type(local_rebuy) is not ProtectedRebuyReview
                or local_rebuy.exchange_proceeds_verified is not False
                or local_rebuy.manual_holding_verified is not False
                or local_rebuy.execution_allowed is not False
                or local_rebuy.rebuy_spending_allowed is not False
                or local_rebuy.main_capital_spending_allowed is not False):
            raise ValueError("M06_LOCAL_REBUY_AUTHORITY_FORGED")
        for number in (
                local_rebuy.floki_idr_local_protected,
                local_rebuy.pepe_idr_local_protected,
                local_rebuy.total_idr_local_protected,
                local_rebuy.floki_manual_qty_local_floor,
                local_rebuy.pepe_manual_qty_local_floor):
            _amount(number)
        if (local_rebuy.floki_idr_local_protected
                + local_rebuy.pepe_idr_local_protected
                != local_rebuy.total_idr_local_protected):
            raise ValueError("M06_LOCAL_REBUY_TOTAL_CONFLICT")
    pending_buy = sum(1 for order in open_orders
                      if order.side == "BUY"
                      and order.executed_qty < order.original_qty)
    # Unknown protected rebuy proceeds, manual quantities and other bot
    # obligations MUST NOT silently be interpreted as zero.
    # This existing conservative calculator always returns zero while
    # all-pair commitment evidence is incomplete.
    calculated = reconcile_unverified_capital(
        account=account, open_orders=open_orders,
        rebuy_idr=(local_rebuy.total_idr_local_protected
                   if local_rebuy is not None else Decimal("0")),
        unlocked_commitments_idr=Decimal("0"),
        local_shadow_idr=Decimal("0"),
        all_pairs_open_orders_complete=False,
    )
    if calculated.calculated_available_idr != Decimal("0"):
        raise ValueError("M06_UNVERIFIED_CAPITAL_NONZERO")
    holdings = {asset: free + locked for asset, free, locked in account.assets}
    special_unknown = any(
        holdings.get(asset, Decimal("0")) > 0
        for asset in ("FLOKI", "PEPE")
    )
    return M06ReadOnlyReconciliation(
        status="REJECT",
        reason="M06_UNVERIFIED_REBUY_OWNERSHIP_AND_ACCOUNT_COVERAGE",
        observed_idr_balance=True,
        all_pair_open_orders_seen=len(open_orders),
        pending_buy_orders=pending_buy,
        matched_fills_in_window=consistency.matched_fill_count,
        unmatched_fills_in_window=consistency.unmatched_fill_count,
        flo_pepe_holdings_unattributed=special_unknown,
        local_ledger_account_bound=local_rebuy is not None,
        local_rebuy_hold_present=(local_rebuy is not None
            and local_rebuy.total_idr_local_protected > 0),
        local_manual_floor_present=(local_rebuy is not None
            and (local_rebuy.floki_manual_qty_local_floor > 0
                 or local_rebuy.pepe_manual_qty_local_floor > 0)),
    )
