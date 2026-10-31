"""M07 one V2 deterministic BUY preflight, permanently non-executable.

This is not a second Final Gate or exchange adapter. The original
DailyScalpingOrchestrator calls it before any hypothetical executor.
Until authenticated upstream/owner/account/order identity sources are wired,
even numerically profitable caller-supplied claims are NEVER an order permit.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .policy import ScalpingPolicy, MainCapitalReview


@dataclass(frozen=True)
class PreflightReview:
    status: str
    reason: str
    estimated_net_profit_idr: Decimal | None = None
    estimated_net_roi_percent: Decimal | None = None
    risk_veto: bool = True
    exchange_order_request_created: bool = False
    execution_allowed: bool = False


class ExecutionPreflight:
    def __init__(self, policy: ScalpingPolicy | None = None):
        if policy is not None and not isinstance(policy, ScalpingPolicy):
            raise TypeError("M07_POLICY_INVALID")
        self.policy = policy or ScalpingPolicy()

    def assess_buy(
        self, *, pair: str | None = None,
        now_epoch: int | None = None,
        source_expires_epoch: int | None = None,
        risk_allow: bool = False,
        owner_pass: bool = False,
        issuer_authenticated: bool = False,
        wallet_authenticated: bool = False,
        order_identity_attested: bool = False,
        tradability_verified: bool = False,
        fees_verified: bool = False,
        quantity_verified: bool = False,
        buy_notional_idr: Decimal | None = None,
        projected_sell_notional_idr: Decimal | None = None,
        buy_all_in_fee_idr: Decimal | None = None,
        sell_all_in_fee_idr: Decimal | None = None,
        spread_slippage_idr: Decimal | None = None,
        spendable_idr: Decimal | None = None,
        capital_review: MainCapitalReview | None = None,
    ) -> PreflightReview:
        deny = lambda reason: PreflightReview("REJECT", reason)
        flags = (risk_allow, owner_pass, issuer_authenticated,
                 wallet_authenticated, order_identity_attested,
                 tradability_verified, fees_verified, quantity_verified)
        if any(type(f) is not bool for f in flags):
            return deny("M07_UNVERIFIED_FLAG_TYPE")
        if not risk_allow:
            return deny("M07_RISK_VETO")
        if not issuer_authenticated:
            return deny("M07_SOURCE_ISSUER_NOT_VERIFIED")
        if not owner_pass:
            return deny("M07_OWNER_PASS_NOT_VERIFIED")
        if not wallet_authenticated:
            return deny("M07_ACCOUNT_NOT_VERIFIED")
        if not order_identity_attested:
            return deny("M07_ORDER_IDENTITY_NOT_ATTESTED")
        if not tradability_verified or not fees_verified or not quantity_verified:
            return deny("M07_QUOTE_PRECISION_OR_FEE_UNVERIFIED")
        if (type(pair) is not str or not pair.endswith("_idr")
                or len(pair) > 32 or not pair[:-4].isalnum()
                or type(now_epoch) is not int
                or type(source_expires_epoch) is not int
                or now_epoch <= 0
                or not now_epoch < source_expires_epoch <= now_epoch + 90):
            return deny("M07_PAIR_OR_SOURCE_TTL_INVALID")
        if capital_review is not None:
            # This object is a mathematical budget review, not an Indodax
            # signed balance or proof of bot-owned unsettled commitments.
            # Never promote a caller-crafted source_authenticated=True flag.
            if type(capital_review) is not MainCapitalReview:
                return deny("M07_CAPITAL_REVIEW_TYPE_INVALID")
            if (capital_review.source_authenticated is not False
                    or capital_review.execution_allowed is not False):
                return deny("M07_CAPITAL_REVIEW_AUTHORITY_INVALID")
            # Dataclasses are caller-constructible. Recompute via the ONE
            # canonical policy before relying on the quoted spendable number.
            # Splitting the rebuy reserve into tokens is unnecessary for the
            # MAIN budget: both are protected IDR outflows.
            try:
                expected_review = self.policy.review_main_capital(
                    free_idr=capital_review.free_idr,
                    locked_idr=capital_review.locked_idr_observed,
                    floki_rebuy_idr=capital_review.reserved_rebuy_idr,
                    pepe_rebuy_idr=Decimal("0"),
                    commitments_not_locked_idr=(
                        capital_review.commitments_not_locked_idr),
                )
            except (ValueError, TypeError, ArithmeticError):
                return deny("M07_CAPITAL_REVIEW_INTEGRITY_INVALID")
            if capital_review != expected_review:
                return deny("M07_CAPITAL_REVIEW_INTEGRITY_INVALID")
            if (type(spendable_idr) is not Decimal
                    or spendable_idr != capital_review.spendable_idr):
                return deny("M07_CAPITAL_SPENDABLE_MISMATCH")
        values = (buy_notional_idr, projected_sell_notional_idr,
                  buy_all_in_fee_idr, sell_all_in_fee_idr,
                  spread_slippage_idr, spendable_idr)
        if any(type(x) is not Decimal or not x.is_finite() or x < 0
               for x in values):
            return deny("M07_UNVERIFIED_PRICE_FEE_OR_BALANCE")
        if buy_notional_idr <= 0 or projected_sell_notional_idr <= 0:
            return deny("M07_NONPOSITIVE_NOTIONAL")
        cost = buy_notional_idr + buy_all_in_fee_idr
        if cost <= 0 or cost > spendable_idr:
            return deny("M07_INSUFFICIENT_FREE_IDR")
        net = (projected_sell_notional_idr - sell_all_in_fee_idr
               - spread_slippage_idr - cost)
        roi = Decimal("100") * net / cost
        if not self.policy.eligible_buy(net, roi):
            return PreflightReview("REJECT", "M07_NET_PROFIT_GATE_FAILED",
                                   estimated_net_profit_idr=net,
                                   estimated_net_roi_percent=roi)
        # Inputs above are mere caller claims. A positive risk/wallet/source
        # flag cannot authenticate an exchange HTTP response or issuer.
        return PreflightReview(
            status="REJECT",
            reason="M07_EXCHANGE_BOUND_ORDER_ADAPTER_NOT_CONNECTED",
            estimated_net_profit_idr=net,
            estimated_net_roi_percent=roi,
            risk_veto=False,
        )
