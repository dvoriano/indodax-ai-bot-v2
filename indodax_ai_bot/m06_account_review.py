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
class M06SpecialPartitionPreview:
    """Untrusted wallet ratio arithmetic only: no bot-owned SELL quantity."""
    asset: str
    observed_wallet_qty: Decimal
    theoretical_trading_cap_qty: Decimal
    minimum_protected_qty: Decimal
    local_manual_floor_qty: Decimal
    local_manual_floor_recorded: bool
    bot_owned_sellable_qty: Decimal = Decimal("0")
    wallet_ownership_verified: bool = False
    execution_allowed: bool = False


def review_m06_special_partitions(*, account: AccountBalances,
                                  local_rebuy: ProtectedRebuyReview | None
                                  ) -> tuple[M06SpecialPartitionPreview, ...]:
    """Two fixed owner pools, always reserve 75% FLOKI / 70% PEPE.

    A local manual floor may *increase* protected quantity, never reduce it.
    Neither observed wallet quantity nor the local floor can establish
    bot-owned lots, so authenticated SELL availability remains exactly ZERO.
    """
    if type(account) is not AccountBalances:
        raise ValueError("M06_SPECIAL_WALLET_SOURCE_INVALID")
    if local_rebuy is not None and type(local_rebuy) is not ProtectedRebuyReview:
        raise ValueError("M06_SPECIAL_LOCAL_SOURCE_INVALID")
    wallet = {asset: _amount(free) + _amount(locked)
              for asset, free, locked in account.assets}
    if len(wallet) != len(account.assets):
        raise ValueError("M06_SPECIAL_ASSET_DUPLICATE")
    out = []
    for asset, fraction, reserve, recorded in (
        ("FLOKI", Decimal("0.25"), (
            local_rebuy.floki_manual_qty_local_floor
            if local_rebuy is not None else Decimal("0")
        ), (local_rebuy.floki_manual_floor_recorded
            if local_rebuy is not None else False)),
        ("PEPE", Decimal("0.30"), (
            local_rebuy.pepe_manual_qty_local_floor
            if local_rebuy is not None else Decimal("0")
        ), (local_rebuy.pepe_manual_floor_recorded
            if local_rebuy is not None else False)),
    ):
        total = _amount(wallet.get(asset, Decimal("0")))
        floor = _amount(reserve)
        if type(recorded) is not bool:
            raise ValueError("M06_SPECIAL_MANUAL_COVERAGE_INVALID")
        if floor > total:
            raise ValueError("M06_SPECIAL_MANUAL_FLOOR_EXCEEDS_WALLET")
        minimum = max(total * (Decimal("1") - fraction), floor)
        cap = min(total * fraction, total - minimum)
        out.append(M06SpecialPartitionPreview(
            asset=asset,
            observed_wallet_qty=total,
            theoretical_trading_cap_qty=_amount(cap),
            minimum_protected_qty=_amount(minimum),
            local_manual_floor_qty=floor,
            local_manual_floor_recorded=recorded,
        ))
    return tuple(out)


@dataclass(frozen=True, repr=False)
class M06SingleAccountPreview:
    """One consolidated observation; never an exchange-spendable budget.

    'upper_bound' can be LESS than free IDR, but unknown external commitments
    may reduce true spendable IDR further. It is NOT safe BUY sizing.
    """
    observed_free_idr: Decimal
    observed_locked_idr: Decimal
    known_local_protected_idr: Decimal
    known_local_pending_idr: Decimal
    candidate_budget_upper_bound_idr: Decimal
    pending_buy_orders: int
    local_ledger_present: bool
    external_obligations_complete: bool = False
    spendable_verified: bool = False
    execution_allowed: bool = False

    def __repr__(self) -> str:
        return "<M06SingleAccountPreview REDACTED>"


def observe_m06_account_preview(*, account: AccountBalances,
        open_orders: tuple[OrderRecord, ...],
        local_rebuy: ProtectedRebuyReview | None) -> M06SingleAccountPreview:
    """One automatic M06 observation, no second owner decision and no I/O.

    Current free IDR ALREADY EXCLUDES exchange-locked IDR. Only subtract
    independently recorded local rebuy holds and pending shadow liabilities.
    A missing local ledger or outside-account debts are UNKNOWN, not zero.
    The result is diagnostic only and can never grant BUY or SELL.
    """
    if type(account) is not AccountBalances or type(open_orders) is not tuple:
        raise ValueError("M06_PREVIEW_SOURCE_INVALID")
    if any(type(o) is not OrderRecord for o in open_orders):
        raise ValueError("M06_PREVIEW_ORDERS_INVALID")
    if local_rebuy is not None and type(local_rebuy) is not ProtectedRebuyReview:
        raise ValueError("M06_PREVIEW_LEDGER_INVALID")
    free = _amount(account.free_idr)
    locked = _amount(account.locked_idr)
    protected = _amount(
        local_rebuy.total_idr_local_protected if local_rebuy is not None
        else Decimal("0")
    )
    pending = _amount(
        local_rebuy.local_shadow_commitments_idr if local_rebuy is not None
        else Decimal("0")
    )
    cap = max(Decimal("0"), free - protected - pending)
    _amount(cap)
    return M06SingleAccountPreview(
        observed_free_idr=free,
        observed_locked_idr=locked,
        known_local_protected_idr=protected,
        known_local_pending_idr=pending,
        candidate_budget_upper_bound_idr=cap,
        pending_buy_orders=sum(
            order.side == "BUY" and order.executed_qty < order.original_qty
            for order in open_orders
        ),
        local_ledger_present=local_rebuy is not None,
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
    local_shadow_reservations_present: bool = False
    local_shadow_coverage_complete: bool = False
    local_rebuy_receipts_linked: bool = False
    local_other_account_records_present: bool = False
    local_orphan_fill_records_present: bool = False
    external_peer_coverage_verified: bool = False
    special_pool_local_floors_complete: bool = False
    protected_rebuy_ledger_verified: bool = False
    manual_asset_ownership_verified: bool = False
    account_wide_order_history_complete: bool = False
    cross_bot_ownership_verified: bool = False
    spendable_capital_verified: bool = False
    execution_allowed: bool = False
    # One diagnostic observation, not a second authorization gate.
    account_preview: M06SingleAccountPreview | None = None


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
                or local_rebuy.main_capital_spending_allowed is not False
                or local_rebuy.external_peer_coverage_verified is not False
                or type(local_rebuy.local_other_account_records_present) is not bool
                or type(local_rebuy.local_orphan_fill_records_present) is not bool):

            raise ValueError("M06_LOCAL_REBUY_AUTHORITY_FORGED")
        for number in (
                local_rebuy.floki_idr_local_protected,
                local_rebuy.pepe_idr_local_protected,
                local_rebuy.total_idr_local_protected,
                local_rebuy.floki_manual_qty_local_floor,
                local_rebuy.pepe_manual_qty_local_floor,
                local_rebuy.local_shadow_commitments_idr):
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
        local_shadow_idr=(local_rebuy.local_shadow_commitments_idr
                          if local_rebuy is not None else Decimal("0")),
        all_pairs_open_orders_complete=False,
    )
    if calculated.calculated_available_idr != Decimal("0"):
        raise ValueError("M06_UNVERIFIED_CAPITAL_NONZERO")
    # One local diagnostic view only. Owner PASS/REJECT remains downstream;
    # M06 preview can NEVER be sent to the order executor as spendable cash.
    account_preview = observe_m06_account_preview(
        account=account, open_orders=open_orders, local_rebuy=local_rebuy,
    )
    pool_previews = review_m06_special_partitions(
        account=account, local_rebuy=local_rebuy,
    )
    special_unknown = any(
        p.observed_wallet_qty > 0 for p in pool_previews
    )
    return M06ReadOnlyReconciliation(
        status="REJECT",
        reason="M06_UNVERIFIED_REBUY_OWNERSHIP_AND_ACCOUNT_COVERAGE",
        account_preview=account_preview,
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
        local_shadow_reservations_present=(local_rebuy is not None
            and local_rebuy.local_shadow_commitments_idr > 0),
        local_shadow_coverage_complete=(local_rebuy is not None
            and local_rebuy.local_shadow_coverage_complete is True),
        local_rebuy_receipts_linked=(local_rebuy is not None
            and local_rebuy.local_rebuy_receipts_linked is True),
        local_other_account_records_present=(local_rebuy is not None
            and local_rebuy.local_other_account_records_present is True),
        local_orphan_fill_records_present=(local_rebuy is not None
            and local_rebuy.local_orphan_fill_records_present is True),
        # Only local evidence is observed; absence never implies exclusive
        # ownership of exchange wallet or other processes' balances.
        external_peer_coverage_verified=False,
        special_pool_local_floors_complete=all(
            p.local_manual_floor_recorded for p in pool_previews
        ),
    )
