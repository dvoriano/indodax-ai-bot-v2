"""Original M03 candidate -> M07-02 observed-public contract (NOT an order).

The earlier strict M07 offline LIMIT draft parser is retained. This bridge
attaches only same-candidate public quote, metadata, and already calculated
fee-sensitive book walk evidence; it NEVER creates an order, assumes a
quantity, reads/reuses a historic balance, or grants authority from M05.
"""
from __future__ import annotations

from decimal import Decimal
from math import isfinite
from typing import Any

from .m07_limit_preflight import M07PreflightBlocked, _rule
from .public_market import PublicObservation
from .scanner import MAIN_EXCLUDED_PAIRS



def _observed_public_rule(value: object, field: str) -> Decimal:
    """Parse official /api/pairs JSON numerics without changing M07 policy.

    The official API documents floating-point JSON numbers for
    price_precision, quantity_increment and traded-currency minimum. The
    general hypothetical-order _rule intentionally rejects Python floats.
    Here only, convert a finite *already observed* JSON numeric into its
    decimal text representation, then apply the ORIGINAL strict M07 bounds.
    This is not a price-grid repair, implicit rounding or executable rule.
    """
    if type(value) is float:
        if not isfinite(value):
            raise M07PreflightBlocked("M07_PUBLIC_RULE_" + field + "_INVALID")
        value = str(value)
    return _rule(value, field)


def assess_m07_same_candidate_public_boundary(*,
        candidate_pair: str, observation: PublicObservation,
        observed_friction: dict[str, Any]) -> dict[str, Any]:
    """Fail-closed exact pair/minute/metadata/fee joining without extra GETs.

    The return value is safe for use inside existing M03 candidate.summary.
    Real M07 full preflight still REQUIRES separately verified full-account
    MAIN IDR (including special proceeds) and a fresh M05 owner decision.
    """
    if type(candidate_pair) is not str or not candidate_pair.endswith("_idr"):
        raise M07PreflightBlocked("M07_PUBLIC_CANDIDATE_PAIR_INVALID")
    if type(observation) is not PublicObservation:
        raise M07PreflightBlocked("M07_PUBLIC_OBSERVATION_TYPE_INVALID")
    source = observation.snapshot.source
    values = {
        "stage": "M07_SAME_CANDIDATE_PUBLIC_PREFLIGHT",
        "candidate_pair": candidate_pair,
        "observed_pair": observation.pair,
        "closed_1m_epoch": source.closed_1m_epoch,
        "observed_epoch": source.observed_epoch,
        "status": "M07_PUBLIC_PREFLIGHT_SOURCE_UNRESOLVED",
        "same_candidate_and_closed_minute": False,
        "same_observation_public_rule_batch": False,
        "public_quote_price_step_match": False,
        "public_buy_minimum_known": False,
        "public_market_explicitly_active": False,
        "public_quote_spread_bps_observed": None,
        "all_in_buy_taker_bps_public_reference_only": None,
        "all_in_sell_taker_bps_public_reference_only": None,
        "public_fee_source_only": True,
        "per_account_fee_verified": False,
        "m06_actual_spendable_idr_verified": False,
        "m06_special_rebuy_exclusions_verified": False,
        "g1_independent_source_verified": False,
        "m05_fresh_owner_final_verified": False,
        "m07_atomic_order_intent_preflight_verified": False,
        "limit_buy_draft_created": False,
        "execution_allowed": False,
        "order_sent": False,
    }
    # M03's caller already gave a *single* observation to its friction
    # evaluator; reject a stale or cross-pair injected friction result.
    aligned = (
        candidate_pair == observation.pair == source.pair
        and source.closed_1m_epoch > 0
        and 0 <= source.observed_epoch - source.closed_1m_epoch <= 120
        and type(observed_friction) is dict
        and observed_friction.get("pair") == candidate_pair
        and observed_friction.get("closed_1m_epoch") == source.closed_1m_epoch
        and observed_friction.get("observed_epoch") == source.observed_epoch
    )
    values["same_candidate_and_closed_minute"] = aligned
    if not aligned:
        return values

    rules = observation.pair_rule_fields
    required = ("price_precision", "quantity_increment",
                "trade_min_base_currency", "trade_min_traded_currency",
                "is_market_suspended", "is_maintenance")
    if (type(rules) is not dict or any(k not in rules for k in required)
            or candidate_pair in MAIN_EXCLUDED_PAIRS):
        values["status"] = "M07_PUBLIC_PAIR_RULES_MISSING_OR_EXCLUDED"
        return values
    values["same_observation_public_rule_batch"] = True
    # The public pair metadata is not a LIVE exchange order acceptance check.
    # A JSON float from /api/pairs must not be mistaken for missing rules.
    # Invalid, negative, nonfinite and out-of-bounds numerics remain blocked.
    try:
        tick = _observed_public_rule(rules["price_precision"], "PRICE_STEP")
        increment = _observed_public_rule(rules["quantity_increment"], "QTY_STEP")
        minimum_idr = _observed_public_rule(rules["trade_min_base_currency"], "MIN_IDR")
        minimum_base = _observed_public_rule(rules["trade_min_traded_currency"], "MIN_QTY")
    except (ValueError, ArithmeticError):
        values["status"] = "M07_PUBLIC_NUMERIC_PAIR_RULE_INVALID"
        return values
    bid = observation.snapshot.best_bid_idr
    ask = observation.snapshot.best_ask_idr
    if (type(bid) is not Decimal or type(ask) is not Decimal
            or not bid.is_finite() or not ask.is_finite()
            or bid <= 0 or ask <= bid):
        values["status"] = "M07_PUBLIC_TICKER_QUOTE_OR_SPREAD_INVALID"
        return values
    try:
        steps = bid % tick == 0 and ask % tick == 0
        spread = (ask - bid) / ask * Decimal("10000")
    except ArithmeticError:
        values["status"] = "M07_PUBLIC_QUOTE_GRID_ARITHMETIC_UNRESOLVED"
        return values
    values["public_quote_price_step_match"] = steps
    values["public_buy_minimum_known"] = minimum_idr > 0 and minimum_base > 0 and increment > 0
    values["public_quote_spread_bps_observed"] = format(spread, "f")
    # Exact M03 observation values are exported solely for bounded M07
    # sizing RESEARCH. They are neither order fill prices nor authenticated
    # account/fee evidence; never copy from another pair or observation.
    values["public_best_bid_idr_reference"] = format(bid, "f")
    values["public_best_ask_idr_reference"] = format(ask, "f")
    values["public_pair_rules_reference"] = {
        "price_precision": format(tick, "f"),
        "quantity_increment": format(increment, "f"),
        "trade_min_base_currency": format(minimum_idr, "f"),
        "trade_min_traded_currency": format(minimum_base, "f"),
        "is_market_suspended": rules["is_market_suspended"],
        "is_maintenance": rules["is_maintenance"],
    }
    # Explicit false/zero ONLY. Maintenance or unknown flags always block.
    values["public_market_explicitly_active"] = (
        rules["is_market_suspended"] in (0, False, "0")
        and rules["is_maintenance"] in (0, False, "0")
        and type(rules["is_market_suspended"]) in (bool, int, str)
        and type(rules["is_maintenance"]) in (bool, int, str)
    )
    schedule = observed_friction.get("published_idr_pro_all_in_fee_table")
    if (type(schedule) is dict and schedule.get("market") == "IDR"
            and schedule.get("mode") == "PRO"
            and schedule.get("account_specific_fee_verified") is False
            and schedule.get("service_tax_cfx_already_included") is True):
        try:
            buy_pct = _rule(schedule["buy_taker_percent_used"], "BUY_TAKER_PCT")
            sell_pct = _rule(schedule["sell_taker_percent_used"], "SELL_TAKER_PCT")
            if buy_pct > 5 or sell_pct > 5:
                raise ValueError("M07_PUBLIC_FEE_BOUND_EXCEEDED")
        except (ValueError, ArithmeticError, KeyError):
            values["status"] = "M07_PUBLIC_FEE_REFERENCE_INVALID"
            return values
        values["all_in_buy_taker_bps_public_reference_only"] = format(buy_pct * 100, "f")
        values["all_in_sell_taker_bps_public_reference_only"] = format(sell_pct * 100, "f")
    if (steps and values["public_buy_minimum_known"]
            and values["public_market_explicitly_active"]
            and values["all_in_buy_taker_bps_public_reference_only"] is not None):
        values["status"] = "M07_PUBLIC_SOURCE_BOUND_RULES_REFERENCE_ONLY"
    else:
        values["status"] = "M07_PUBLIC_RULES_OR_FEES_NOT_READY"
    return values
