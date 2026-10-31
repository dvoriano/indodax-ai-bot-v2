"""One canonical V2 position-review owner, fail-closed until exchange-owned fills.

M08 reuses EXISTING ScalpingPolicy T1/T2/T3 and 30/60/90 minute logic.
This module never fetches private data, places an order, or creates a new
ledger. A caller-provided lot is NOT authenticated by merely naming it 'owned'.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .policy import ScalpingPolicy


@dataclass(frozen=True)
class PositionReview:
    status: str
    reason: str
    highest_net_tier: str | None = None
    hold_phase: str | None = None
    protective_review_required: bool = False
    source_authenticated: bool = False
    bot_ownership_verified: bool = False
    execution_allowed: bool = False
    realized: bool = False


class PositionManager:
    """Single existing V2 orchestrator's future owner for managed SELL reviews.

    Mathematical diagnostics are intentionally separate from authenticated
    exchange fill/position/settlement. No positive order authority exists.
    """
    def __init__(self, policy: ScalpingPolicy | None = None):
        if policy is not None and not isinstance(policy, ScalpingPolicy):
            raise TypeError("M08_POSITION_POLICY_INVALID")
        self.policy = policy or ScalpingPolicy()

    def review(self) -> PositionReview:
        """The current V2 has NO authenticated buy fill source/owned lots."""
        return PositionReview(
            status="REJECT",
            reason="M08_EXCHANGE_OWNED_BUY_FILLS_NOT_CONNECTED",
        )

    def review_unverified_quote(
        self, *, actual_buy_fill_epoch: int, observed_epoch: int,
        filled_buy_quantity: Decimal, filled_buy_notional_idr: Decimal,
        allocated_buy_fees_idr: Decimal, quantity_to_sell: Decimal,
        expected_sell_notional_idr: Decimal, estimated_sell_fees_idr: Decimal,
        incremental_slippage_idr: Decimal,
        pump_continuation_confirmed: bool = False,
        trailing_protection_active: bool = False,
        market_source_fresh: bool = False,
        risk_review_clear: bool = False,
    ) -> PositionReview:
        """Quote math for integration/PAPER only, never an owned SELL signal.

        Reuse immutable original policy's fill-relative net tiers and hold-time
        rules. No arbitrary caller can attest trade origin, quantity ownership,
        settled proceeds or authorization by passing this function arguments.
        """
        time = self.policy.assess_position_holding_time(
            actual_buy_fill_epoch=actual_buy_fill_epoch,
            observed_epoch=observed_epoch,
            pump_continuation_confirmed=pump_continuation_confirmed,
            trailing_protection_active=trailing_protection_active,
            market_source_fresh=market_source_fresh,
            risk_review_clear=risk_review_clear,
        )
        net = self.policy.assess_position_exit_targets(
            filled_buy_quantity=filled_buy_quantity,
            filled_buy_notional_idr=filled_buy_notional_idr,
            allocated_buy_fees_idr=allocated_buy_fees_idr,
            quantity_to_sell=quantity_to_sell,
            expected_sell_notional_idr=expected_sell_notional_idr,
            estimated_sell_fees_idr=estimated_sell_fees_idr,
            incremental_slippage_idr=incremental_slippage_idr,
        )
        return PositionReview(
            status="REJECT",
            reason="M08_UNVERIFIED_LOT_AND_SETTLEMENT",
            highest_net_tier=net.highest_reached_tier,
            hold_phase=time.phase,
            protective_review_required=time.risk_review_priority == "URGENT",
        )
