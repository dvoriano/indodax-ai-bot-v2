"""M07-02 original V2 LIMIT BUY preflight preview: strict rules and IDR buckets.

Pure, offline and explicitly UNTRUSTED input. Actual /api/pairs freshness,
live orderbook, fee tier, verified M06 funds, M05 Final and G1 receipt are
separate necessary gates. Positive arithmetic here NEVER authorizes trading.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from collections.abc import Mapping

from .m07_order_adapter import M07OrderDraft
from .policy import ScalpingPolicy


class M07PreflightBlocked(ValueError):
    """Fixed-code only error for an invalid preview input."""


@dataclass(frozen=True)
class M07LimitBuyPreflightPreview:
    status: str
    symbol: str
    price_step_valid: bool
    quantity_step_valid: bool
    public_min_order_met: bool
    public_market_active: bool
    within_spread_ceiling: bool
    general_idr_scenario_funded: bool
    estimated_buy_max_cost_idr: str
    general_idr_scenario_budget: str
    required_future_sell_vwap_idr_unverified: str | None
    checks_passed_unverified: bool
    public_pair_rule_provenance_verified: bool = False
    actual_account_fee_verified: bool = False
    wallet_spendable_verified: bool = False
    market_source_g1_verified: bool = False
    owner_m05_final_pass_verified: bool = False
    order_sent: bool = False
    execution_allowed: bool = False


_REQUIRED = frozenset((
    "price_precision", "quantity_increment", "trade_min_base_currency",
    "trade_min_traded_currency", "is_market_suspended", "is_maintenance",
))


def _dec(value: object, field: str, *, zero_ok: bool = False) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise M07PreflightBlocked("M07_PREFLIGHT_" + field + "_INVALID")
    if (value < 0 or (value == 0 and not zero_ok)
            or (value != 0 and not -18 <= value.adjusted() <= 20)):
        raise M07PreflightBlocked("M07_PREFLIGHT_" + field + "_INVALID")
    return value


def _rule(value: object, field: str) -> Decimal:
    if type(value) not in (str, int, Decimal):
        raise M07PreflightBlocked("M07_PREFLIGHT_RULE_" + field + "_INVALID")
    try:
        x = Decimal(value)
        return _dec(x, "RULE_" + field)
    except (InvalidOperation, ValueError, ArithmeticError):
        raise M07PreflightBlocked("M07_PREFLIGHT_RULE_" + field + "_INVALID") from None


def assess_m07_limit_buy_preflight_offline(*,
        draft: M07OrderDraft, pair_rules: Mapping[str, object],
        best_bid_idr: Decimal, best_ask_idr: Decimal,
        free_idr: Decimal, locked_idr: Decimal,
        floki_rebuy_idr: Decimal, pepe_rebuy_idr: Decimal,
        commitments_not_locked_idr: Decimal,
        buy_all_in_fee_bps: Decimal, sell_all_in_fee_bps: Decimal,
        max_spread_bps: Decimal,
        ) -> M07LimitBuyPreflightPreview:
    """Check same-pair rule/quote and hypothetical GENERAL IDR source budget.

    LIMIT BUY worst-case quote spend is price * quantity plus modeled all-in
    fee; do not add spread again on top of the limit price. SELL target
    sensitivity includes a *single* whole sell fee, never separate tax/CFX.
    """
    if type(draft) is not M07OrderDraft:
        raise M07PreflightBlocked("M07_PREFLIGHT_DRAFT_INVALID")
    # Reuse ORIGINAL M07 adapter identity/shape rules. It is hardlocked.
    try:
        draft.parameters()
    except ValueError:
        raise M07PreflightBlocked("M07_PREFLIGHT_DRAFT_INVALID") from None
    if (draft.side != "BUY" or draft.order_type != "LIMIT"
            or draft.price is None or draft.quantity is None):
        raise M07PreflightBlocked("M07_PREFLIGHT_LIMIT_BUY_ONLY")
    if (not isinstance(pair_rules, Mapping)
            or set(pair_rules) != _REQUIRED):
        raise M07PreflightBlocked("M07_PREFLIGHT_PAIR_RULES_INCOMPLETE")
    bid = _dec(best_bid_idr, "BID")
    ask = _dec(best_ask_idr, "ASK")
    if ask <= bid:
        raise M07PreflightBlocked("M07_PREFLIGHT_CROSSED_BOOK")
    for name in ("is_market_suspended", "is_maintenance"):
        if type(pair_rules[name]) not in (str, int, bool) or (
                pair_rules[name] not in (0, 1, False, True, "0", "1")):
            raise M07PreflightBlocked("M07_PREFLIGHT_PUBLIC_MARKET_FLAG_UNKNOWN")
    active = pair_rules["is_market_suspended"] in (0, False, "0")
    active = active and pair_rules["is_maintenance"] in (0, False, "0")
    price_step = _rule(pair_rules["price_precision"], "PRICE_STEP")
    qty_step = _rule(pair_rules["quantity_increment"], "QTY_STEP")
    min_idr = _rule(pair_rules["trade_min_base_currency"], "MIN_IDR")
    min_qty = _rule(pair_rules["trade_min_traded_currency"], "MIN_QTY")
    for val, field in (
        (buy_all_in_fee_bps, "BUY_FEE"),
        (sell_all_in_fee_bps, "SELL_FEE"),
        (max_spread_bps, "SPREAD_CEILING"),
    ):
        _dec(val, field, zero_ok=(field == "SPREAD_CEILING"))
        if val > Decimal("500") or (
                field != "SPREAD_CEILING" and val >= Decimal("10000")):
            raise M07PreflightBlocked("M07_PREFLIGHT_" + field + "_INVALID")
    # Real execution must bind these to the same pair snapshot; the preview
    # does NOT let a CLI-supplied symbol or quoted price attest a live book.
    price_step_valid = draft.price % price_step == 0
    quantity_step_valid = draft.quantity % qty_step == 0
    gross = draft.price * draft.quantity
    public_min_met = gross >= min_idr and draft.quantity >= min_qty
    spread_bps = (ask - bid) / ask * Decimal("10000")
    within_spread = spread_bps <= max_spread_bps
    policy = ScalpingPolicy()
    capital = policy.review_idr_buy_funding_route(
        free_idr=free_idr, locked_idr=locked_idr,
        floki_rebuy_idr=floki_rebuy_idr, pepe_rebuy_idr=pepe_rebuy_idr,
        commitments_not_locked_idr=commitments_not_locked_idr,
        funding_source="MAIN", buy_asset=draft.symbol[:-3],
        requested_idr=gross * (
            Decimal("1") + buy_all_in_fee_bps / Decimal("10000")
        ),
    )
    cost = gross * (Decimal("1") + buy_all_in_fee_bps / Decimal("10000"))
    profit_target = max(
        policy.min_net_profit_idr,
        cost * policy.min_net_roi_percent / Decimal("100"),
    )
    sell_after_fee_factor = (
        Decimal("1") - sell_all_in_fee_bps / Decimal("10000")
    )
    future_vwap = (
        (cost + profit_target) / sell_after_fee_factor / draft.quantity
    )
    passes = (price_step_valid and quantity_step_valid and public_min_met
              and active and within_spread and capital.source_and_amount_match)
    return M07LimitBuyPreflightPreview(
        status=("M07_DRAFT_RULES_MATCH_UNTRUSTED"
                if passes else "M07_DRAFT_RULES_BLOCKED_UNTRUSTED"),
        symbol=draft.symbol,
        price_step_valid=price_step_valid,
        quantity_step_valid=quantity_step_valid,
        public_min_order_met=public_min_met,
        public_market_active=active,
        within_spread_ceiling=within_spread,
        general_idr_scenario_funded=capital.source_and_amount_match,
        estimated_buy_max_cost_idr=format(cost, "f"),
        general_idr_scenario_budget=format(capital.main_available_idr, "f"),
        required_future_sell_vwap_idr_unverified=format(future_vwap, "f"),
        checks_passed_unverified=passes,
    )
