"""M07-02 same M03 + M06 owner-classified MAIN IDR sizing, OFFLINE ONLY.

Never infers an old FLOKI/PEPE IDR reserve contrary to the owner decision.
New actual special-token sale holds and un-locked commitments are explicit
inputs; they must not silently default to zero. Nothing authorizes an order.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from typing import Any

from .m07_limit_preflight import (
    M07PreflightBlocked, assess_m07_limit_buy_preflight_offline,
)
from .m07_order_adapter import M07OrderDraft


def _money(value: Any, field: str, *, allow_zero: bool = True) -> Decimal:
    if type(value) not in (str, int, Decimal):
        raise M07PreflightBlocked("M07_MAIN_SIZING_" + field + "_INVALID")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError, ArithmeticError):
        raise M07PreflightBlocked("M07_MAIN_SIZING_" + field + "_INVALID") from None
    if (not amount.is_finite() or amount < 0
            or not allow_zero and amount == 0
            or amount != 0 and not -18 <= amount.adjusted() <= 20):
        raise M07PreflightBlocked("M07_MAIN_SIZING_" + field + "_INVALID")
    return amount


def assess_m07_owner_main_idr_sizing_offline(*,
        public_candidate: dict[str, Any],
        account_snapshot: dict[str, Any],
        owner_main_source_classified: bool,
        requested_ticket_ceiling_idr: Decimal,
        new_floki_sell_rebuy_hold_idr: Decimal,
        new_pepe_sell_rebuy_hold_idr: Decimal,
        commitments_not_locked_idr: Decimal,
        max_spread_bps: Decimal,
        now_epoch: int) -> dict[str, Any]:
    """Show maximum quantized hypothetical BUY from the MAIN pool.

    A caller-supplied classification is the owner's funding policy, NOT an
    API attestation or a Final PASS. The wallet and public summary are source
    observations used only for a short-lived non-executable calculation.
    This requires explicit known new-sale holds, never imaginary old holds.
    """
    blocked = {
        "stage": "M07_OWNER_MAIN_IDR_SIZING_OFFLINE",
        "status": "M07_MAIN_SIZING_SOURCE_UNVERIFIED",
        "owner_main_source_classification_accepted_for_preview": False,
        "main_idr_pool_scenario": None,
        "requested_ticket_ceiling_idr": None,
        "sized_qty_base_reference": None,
        "buy_max_cost_idr_reference": None,
        "price_step_and_minimum_reference_met": False,
        "spread_ceiling_reference_met": False,
        "slippage_and_depth_executable_verified": False,
        "per_account_all_in_fee_verified": False,
        "source_independent_g1_verified": False,
        "owner_m05_order_final_verified": False,
        "wallet_order_spendable_verified": False,
        "order_sent": False,
        "execution_allowed": False,
    }
    if (type(public_candidate) is not dict or
            type(account_snapshot) is not dict or
            type(owner_main_source_classified) is not bool or
            type(now_epoch) is not int or now_epoch <= 0):
        raise M07PreflightBlocked("M07_MAIN_SIZING_INPUT_INVALID")
    p, a = public_candidate, account_snapshot
    pair = p.get("candidate_pair")
    public_epoch = p.get("observed_epoch")
    account_epoch = a.get("account_observed_epoch")
    if (p.get("stage") != "M07_SAME_CANDIDATE_PUBLIC_PREFLIGHT"
            or p.get("status") != "M07_PUBLIC_SOURCE_BOUND_RULES_REFERENCE_ONLY"
            or p.get("same_candidate_and_closed_minute") is not True
            or p.get("observed_pair") != pair
            or type(pair) is not str or not pair.endswith("_idr")
            or pair[:-4].upper() in ("PEPE", "FLOKI", "USDT", "USDC")
            or type(public_epoch) is not int
            or type(p.get("closed_1m_epoch")) is not int
            or not 0 <= public_epoch - p["closed_1m_epoch"] <= 120
            or not 0 <= now_epoch - public_epoch <= 30):
        return dict(blocked, status="M07_MAIN_SIZING_PUBLIC_SOURCE_STALE_OR_MISMATCH")
    if (a.get("status") != "M06_IDR_BALANCE_OBSERVED_SOURCE_RESERVE_UNVERIFIED"
            or a.get("view_only_permissions_verified") is not True
            or a.get("requests_completed") != 2
            or a.get("execution_allowed") is not False
            or type(account_epoch) is not int
            or not 0 <= now_epoch - account_epoch <= 30
            or type(a.get("all_pair_open_orders_observed")) is not int
            or a["all_pair_open_orders_observed"] != 0):
        return dict(blocked, status="M07_MAIN_SIZING_ACCOUNT_STALE_OR_OPEN_ORDER")
    if owner_main_source_classified is not True:
        return dict(blocked, status="M07_MAIN_SIZING_OWNER_SOURCE_CLASSIFICATION_REQUIRED")
    cap = _money(requested_ticket_ceiling_idr, "TICKET", allow_zero=False)
    floki = _money(new_floki_sell_rebuy_hold_idr, "NEW_FLOKI_HOLD")
    pepe = _money(new_pepe_sell_rebuy_hold_idr, "NEW_PEPE_HOLD")
    commitments = _money(commitments_not_locked_idr, "COMMITMENTS")
    spread_cap = _money(max_spread_bps, "SPREAD")
    free = _money(a.get("exchange_idr_free_observed"), "FREE")
    locked = _money(a.get("exchange_idr_locked_observed"), "LOCKED")
    budget = max(Decimal(0), free - floki - pepe - commitments)
    base = dict(blocked,
        owner_main_source_classification_accepted_for_preview=True,
        main_idr_pool_scenario=format(budget, "f"),
        requested_ticket_ceiling_idr=format(cap, "f"),
    )
    if budget <= 0:
        return dict(base, status="M07_MAIN_SIZING_NO_UNCOMMITTED_IDR")
    rules = p.get("public_pair_rules_reference")
    if (type(rules) is not dict or
            p.get("public_market_explicitly_active") is not True or
            p.get("public_quote_price_step_match") is not True):
        return dict(base, status="M07_MAIN_SIZING_PUBLIC_PAIR_RULES_BLOCK")
    try:
        ask = _money(p["public_best_ask_idr_reference"], "ASK", allow_zero=False)
        bid = _money(p["public_best_bid_idr_reference"], "BID", allow_zero=False)
        tick = _money(rules["price_precision"], "TICK", allow_zero=False)
        step = _money(rules["quantity_increment"], "STEP", allow_zero=False)
        buy_bps = _money(p["all_in_buy_taker_bps_public_reference_only"], "BUY_FEE")
        sell_bps = _money(p["all_in_sell_taker_bps_public_reference_only"], "SELL_FEE")
        if ask <= bid or ask % tick != 0 or bid % tick != 0:
            return dict(base, status="M07_MAIN_SIZING_INVALID_QUOTE_GRID")
        if buy_bps > 500 or sell_bps > 500 or spread_cap > 500:
            return dict(base, status="M07_MAIN_SIZING_FEE_OR_SPREAD_INVALID")
        ceiling = min(cap, budget)
        qty = ((ceiling / (ask * (1 + buy_bps / 10000))) /
               step).to_integral_value(rounding=ROUND_FLOOR) * step
        if qty <= 0:
            return dict(base, status="M07_MAIN_SIZING_BELOW_QUANTITY_STEP")
        draft = M07OrderDraft(
            pair[:-4].upper() + "IDR", "BUY", "LIMIT",
            "m07_main_sizing_preview_only", price=ask, quantity=qty,
        )
        preview = assess_m07_limit_buy_preflight_offline(
            draft=draft, pair_rules=rules,
            best_bid_idr=bid, best_ask_idr=ask,
            free_idr=free, locked_idr=locked,
            floki_rebuy_idr=floki, pepe_rebuy_idr=pepe,
            commitments_not_locked_idr=commitments,
            buy_all_in_fee_bps=buy_bps, sell_all_in_fee_bps=sell_bps,
            max_spread_bps=spread_cap,
        )
    except (KeyError, ArithmeticError, InvalidOperation,
            M07PreflightBlocked, ValueError):
        return dict(base, status="M07_MAIN_SIZING_RULES_OR_PRECISION_UNRESOLVED")
    return dict(base,
        status=("M07_MAIN_SIZING_RULES_MATCH_REFERENCE_ONLY"
                if preview.checks_passed_unverified else
                "M07_MAIN_SIZING_RULES_BLOCKED_REFERENCE_ONLY"),
        sized_qty_base_reference=format(qty, "f"),
        buy_max_cost_idr_reference=preview.estimated_buy_max_cost_idr,
        future_sell_vwap_idr_reference=preview.required_future_sell_vwap_idr_unverified,
        price_step_and_minimum_reference_met=(
            preview.price_step_valid and preview.quantity_step_valid and
            preview.public_min_order_met and preview.public_market_active),
        spread_ceiling_reference_met=preview.within_spread_ceiling,
        used_public_all_in_buy_bps=format(buy_bps, "f"),
        used_public_all_in_sell_bps=format(sell_bps, "f"),
    )
