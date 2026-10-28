"""Hard, deterministic daily scalping constraints, independent of AI prompts."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal



@dataclass(frozen=True)
class SellTargetAssessment:
    """Quote-based position-relative EXIT diagnostic, never execution authority.

    Cost basis includes allocated BUY fees. Net proceeds deduct future SELL
    fees and incremental slippage once. Not realized PnL before settled fills.
    """
    allocated_cost_basis_idr: Decimal
    expected_net_proceeds_idr: Decimal
    expected_net_profit_idr: Decimal
    expected_net_roi_percent: Decimal
    reached_tiers: tuple[str, ...]
    highest_reached_tier: str | None
    reference: str = "BOT_OWNED_ACTUAL_BUY_FILL_COST_BASIS"
    price_change_24h_used: bool = False
    realized: bool = False
    execution_allowed: bool = False


@dataclass(frozen=True)
class ScalpingPolicy:
    min_net_profit_idr: Decimal = Decimal("10000")
    min_net_roi_percent: Decimal = Decimal("2")
    sell_targets_net_percent: tuple[Decimal, ...] = (
        Decimal("2"), Decimal("8"), Decimal("11")
    )
    max_active_main_pairs: int = 2
    manual_proposal_ttl_seconds: int = 90
    reporting_timezone: str = "Asia/Jakarta"

    def eligible_buy(self, estimated_net_profit_idr: Decimal, estimated_net_roi_percent: Decimal) -> bool:
        return (
            estimated_net_profit_idr.is_finite()
            and estimated_net_roi_percent.is_finite()
            and estimated_net_profit_idr >= self.min_net_profit_idr
            and estimated_net_roi_percent >= self.min_net_roi_percent
        )

    def assess_position_exit_targets(
        self, *,
        filled_buy_quantity: Decimal,
        filled_buy_notional_idr: Decimal,
        allocated_buy_fees_idr: Decimal,
        quantity_to_sell: Decimal,
        expected_sell_notional_idr: Decimal,
        estimated_sell_fees_idr: Decimal,
        incremental_slippage_idr: Decimal,
    ) -> SellTargetAssessment:
        """Assess T1/T2/T3 using SAME BUY cost basis across all candles.

        Strict Decimal inputs; do not infer an exchange fee or trade fill from
        ticker/24h change. Supports partial exit via prorated cost basis.
        Ownership/freshness/execution preflight remains future M08 work.
        """
        values = (
            filled_buy_quantity, filled_buy_notional_idr, allocated_buy_fees_idr,
            quantity_to_sell, expected_sell_notional_idr,
            estimated_sell_fees_idr, incremental_slippage_idr,
        )
        if any(not isinstance(v, Decimal) or not v.is_finite() for v in values):
            raise ValueError("EXIT_REQUIRES_EXPLICIT_FINITE_DECIMALS")
        if (filled_buy_quantity <= 0 or filled_buy_notional_idr <= 0
                or quantity_to_sell <= 0 or quantity_to_sell > filled_buy_quantity
                or allocated_buy_fees_idr < 0
                or expected_sell_notional_idr < 0
                or estimated_sell_fees_idr < 0
                or incremental_slippage_idr < 0):
            raise ValueError("INVALID_EXIT_POSITION_OR_COST")
        if (len(self.sell_targets_net_percent) != 3
                or self.sell_targets_net_percent != (
                    Decimal("2"), Decimal("8"), Decimal("11")
                )):
            raise ValueError("EXIT_TIERS_POLICY_MISMATCH")
        cost = (filled_buy_notional_idr + allocated_buy_fees_idr) * (
            quantity_to_sell / filled_buy_quantity
        )
        proceeds = (
            expected_sell_notional_idr - estimated_sell_fees_idr
            - incremental_slippage_idr
        )
        profit = proceeds - cost
        net_roi = Decimal("100") * profit / cost
        tier_ids = ("T1", "T2", "T3")
        reached = tuple(
            tier for tier, target in zip(tier_ids, self.sell_targets_net_percent)
            if net_roi >= target
        )
        return SellTargetAssessment(
            allocated_cost_basis_idr=cost,
            expected_net_proceeds_idr=proceeds,
            expected_net_profit_idr=profit,
            expected_net_roi_percent=net_roi,
            reached_tiers=reached,
            highest_reached_tier=reached[-1] if reached else None,
        )
