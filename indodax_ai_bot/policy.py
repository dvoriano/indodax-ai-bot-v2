"""Hard, deterministic daily scalping constraints, independent of AI prompts."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re



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
class ScalpingTimeAssessment:
    """Position holding-time advice, never a SELL/BUY authorization."""
    elapsed_seconds: int
    phase: str
    risk_review_priority: str
    review_interval_seconds: int
    pump_exception_observed: bool
    forced_sell: bool = False
    execution_allowed: bool = False
    reference: str = "ACTUAL_BOT_OWNED_BUY_FILL_EPOCH"


@dataclass(frozen=True)
class SpecialPoolReview:
    """FLOKI/PEPE mathematical quota, NOT authenticated token ownership."""
    asset: str
    maximum_trading_quantity: Decimal
    minimum_reserved_quantity: Decimal
    manually_excluded_quantity: Decimal
    reviewed: bool
    wallet_authenticated: bool = False
    execution_allowed: bool = False




@dataclass(frozen=True)
class MainCapitalReview:
    """Read-only arithmetic. Only a separately verified exchange source can attest funds."""
    free_idr: Decimal
    locked_idr_observed: Decimal
    reserved_rebuy_idr: Decimal
    commitments_not_locked_idr: Decimal
    spendable_idr: Decimal
    overcommitted: bool
    source_authenticated: bool = False
    execution_allowed: bool = False


@dataclass(frozen=True)
class IDRFundingRouteReview:
    """A funding-source preview, NOT permission to dispatch an exchange order.

    All GENERAL IDR can revolve into the next eligible MAIN scalp, whatever
    its origin or size. Only FLOKI/PEPE SELL proceeds are earmarked to
    repurchase their SAME asset. Read-only local reservations never
    authenticate actual proceeds or account-wide availability.
    """
    funding_source: str
    buy_asset: str
    requested_idr: Decimal
    main_available_idr: Decimal
    floki_rebuy_available_idr: Decimal
    pepe_rebuy_available_idr: Decimal
    selected_source_budget_idr: Decimal
    source_and_amount_match: bool
    all_buckets_funded_by_free_balance: bool
    source_authenticated: bool = False
    exchange_order_authorized: bool = False
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
    scalp_fast_window_seconds: int = 30 * 60
    scalp_preferred_exit_seconds: int = 60 * 60
    scalp_pump_extension_review_seconds: int = 90 * 60

    def assess_position_holding_time(
        self, *,
        actual_buy_fill_epoch: int,
        observed_epoch: int,
        pump_continuation_confirmed: bool = False,
        trailing_protection_active: bool = False,
        market_source_fresh: bool = False,
        risk_review_clear: bool = False,
    ) -> ScalpingTimeAssessment:
        """30/60m scalping review clock starts at an ACTUAL owned BUY fill.

        A strong pump may justify bounded trailing observation after 60m,
        but must be fresh, protected and Risk-cleared, and 90m triggers
        escalated review even then. Never fabricate fills or authorize SELL.
        Price returns and T1/T2/T3 still refer to actual BUY cost basis.
        """
        if (type(actual_buy_fill_epoch) is not int or
                type(observed_epoch) is not int or
                actual_buy_fill_epoch <= 0 or
                observed_epoch < actual_buy_fill_epoch):
            raise ValueError("INVALID_ACTUAL_BUY_HOLDING_TIME")
        flags = (pump_continuation_confirmed, trailing_protection_active,
                 market_source_fresh, risk_review_clear)
        if any(type(flag) is not bool for flag in flags):
            raise ValueError("INVALID_PUMP_EXCEPTION_EVIDENCE")
        age = observed_epoch - actual_buy_fill_epoch
        exception = all(flags)
        if age < self.scalp_fast_window_seconds:
            phase, priority = "FAST_SCALP_MONITOR", "NORMAL"
        elif age < self.scalp_preferred_exit_seconds:
            phase, priority = "EXIT_PRIORITY_REVIEW", "ELEVATED"
        elif (exception and
              age < self.scalp_pump_extension_review_seconds):
            phase, priority = "PUMP_TRAILING_EXCEPTION_REVIEW", "ELEVATED"
        elif age >= self.scalp_pump_extension_review_seconds:
            phase, priority = "EXTENDED_POSITION_RISK_REVIEW", "URGENT"
        else:
            phase, priority = "TIME_STOP_PROTECTIVE_REVIEW", "URGENT"
        return ScalpingTimeAssessment(
            elapsed_seconds=age,
            phase=phase,
            risk_review_priority=priority,
            review_interval_seconds=60,
            pump_exception_observed=exception,
        )

    def review_main_capital(
        self, *, free_idr: Decimal, locked_idr: Decimal,
        floki_rebuy_idr: Decimal, pepe_rebuy_idr: Decimal,
        commitments_not_locked_idr: Decimal,
    ) -> MainCapitalReview:
        """M06 budget arithmetic on UNVERIFIED, caller-supplied values.

        Free IDR already excludes exchange-locked IDR. Never subtract locked
        twice. Only subtract additional commitments not already reflected in
        locked balance, plus the protected FLOKI/PEPE rebuy reserves. The
        resulting number is NOT spendable exchange authority until a future
        authenticated/account reconciler proves ownership, freshness and
        outstanding order coverage.
        """
        values = (
            free_idr, locked_idr, floki_rebuy_idr, pepe_rebuy_idr,
            commitments_not_locked_idr,
        )
        if any(
            type(v) is not Decimal or not v.is_finite() or v < 0
            or (v != 0 and (v.adjusted() > 24 or v.adjusted() < -24))
            for v in values
        ):
            raise ValueError("M06_INVALID_CAPITAL_DECIMAL")
        rebuy = floki_rebuy_idr + pepe_rebuy_idr
        commitments = commitments_not_locked_idr
        remainder = free_idr - rebuy - commitments
        return MainCapitalReview(
            free_idr=free_idr,
            locked_idr_observed=locked_idr,
            reserved_rebuy_idr=rebuy,
            commitments_not_locked_idr=commitments,
            spendable_idr=max(Decimal("0"), remainder),
            overcommitted=remainder < 0,
        )

    def review_idr_buy_funding_route(
        self, *, free_idr: Decimal, locked_idr: Decimal,
        floki_rebuy_idr: Decimal, pepe_rebuy_idr: Decimal,
        commitments_not_locked_idr: Decimal,
        funding_source: str, buy_asset: str,
        requested_idr: Decimal,
    ) -> IDRFundingRouteReview:
        """Route a proposed IDR BUY by SOURCE, not original principal amount.

        MAIN source: any presently FREE GENERAL IDR (deposits, past trading,
        or settled MAIN scalp proceeds) after two protected rebuy reserves
        and not-yet-locked commitments. NO fixed capital baseline or ceiling.
        Special sale proceeds: FLOKI_REBUY -> FLOKI only; PEPE_REBUY -> PEPE
        only. Never pool these proceeds with MAIN, even if all coins share
        one exchange IDR wallet. No wallet evidence or LIVE authorization.
        """
        if (type(funding_source) is not str or funding_source not in (
                "MAIN", "FLOKI_REBUY", "PEPE_REBUY")):
            raise ValueError("M06_IDR_FUNDING_SOURCE_INVALID")
        if (type(buy_asset) is not str
                or re.fullmatch(r"[A-Z0-9]{2,20}", buy_asset) is None
                or buy_asset == "IDR"):
            raise ValueError("M06_IDR_BUY_ASSET_INVALID")
        if (type(requested_idr) is not Decimal
                or not requested_idr.is_finite()
                or requested_idr <= 0
                or requested_idr >= Decimal("1e24")):
            raise ValueError("M06_IDR_BUY_AMOUNT_INVALID")
        budget = self.review_main_capital(
            free_idr=free_idr, locked_idr=locked_idr,
            floki_rebuy_idr=floki_rebuy_idr,
            pepe_rebuy_idr=pepe_rebuy_idr,
            commitments_not_locked_idr=commitments_not_locked_idr,
        )
        sufficient_all = not budget.overcommitted
        sources = {
            "MAIN": budget.spendable_idr,
            "FLOKI_REBUY": floki_rebuy_idr,
            "PEPE_REBUY": pepe_rebuy_idr,
        }
        if funding_source == "MAIN":
            # Trading exclusions remain separate from historical read-only.
            asset_matches = buy_asset not in (
                "FLOKI", "PEPE", "USDT", "USDC")
        else:
            asset_matches = (
                buy_asset == ("FLOKI" if funding_source == "FLOKI_REBUY"
                              else "PEPE")
            )
        chosen = sources[funding_source]
        return IDRFundingRouteReview(
            funding_source=funding_source, buy_asset=buy_asset,
            requested_idr=requested_idr,
            main_available_idr=budget.spendable_idr,
            floki_rebuy_available_idr=floki_rebuy_idr,
            pepe_rebuy_available_idr=pepe_rebuy_idr,
            selected_source_budget_idr=chosen,
            source_and_amount_match=(
                sufficient_all and asset_matches and requested_idr <= chosen
            ),
            all_buckets_funded_by_free_balance=sufficient_all,
        )

    def review_special_asset_pool(
        self, *, asset: str, total_quantity: Decimal,
        manual_quantity: Decimal, bot_owned_quantity: Decimal,
    ) -> SpecialPoolReview:
        """M06 token reserve and manually-owned exclusions, PURE PREVIEW.

        Never infer wallet provenance or spending rights from caller inputs.
        FLOKI token trading cap 25%, PEPE token trading cap 30%; the other
        75%/70% are reserved. Manually held tokens do not belong to the bot,
        regardless of available balance. No SELL/BALANCE/private endpoint.
        """
        if asset not in ("FLOKI", "PEPE"):
            raise ValueError("M06_SPECIAL_ASSET_UNSUPPORTED")
        values = (total_quantity, manual_quantity, bot_owned_quantity)
        if any(type(v) is not Decimal or not v.is_finite() or v < 0
               for v in values):
            raise ValueError("M06_SPECIAL_ASSET_QUANTITY_INVALID")
        if manual_quantity + bot_owned_quantity > total_quantity:
            raise ValueError("M06_SPECIAL_ASSET_OWNERSHIP_OVERLAP")
        # Owner's 50% manual holdings remain entirely outside bot control.
        # This is a conservative allocation invariant, never wallet proof.
        if manual_quantity < total_quantity * Decimal("0.50"):
            raise ValueError("M06_MANUAL_HALF_NOT_RESERVED")
        portion = Decimal("0.25") if asset == "FLOKI" else Decimal("0.30")
        cap = total_quantity * portion
        available = min(cap, bot_owned_quantity)
        return SpecialPoolReview(
            asset=asset,
            maximum_trading_quantity=available,
            minimum_reserved_quantity=total_quantity - cap,
            manually_excluded_quantity=manual_quantity,
            reviewed=True,
        )

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
