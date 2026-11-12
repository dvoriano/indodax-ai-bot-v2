"""Owner Strategy A: deterministic, research-only scalping decision references.

No exchange transport, database writes, Telegram send, AI call, order, or
execution authority. Every result is an OFFLINE reference, not a trade intent.
The actual M07/M08 runtime remains hardlocked.
"""
from __future__ import annotations

from decimal import Decimal


ZERO = Decimal("0")
HUNDRED = Decimal("100")
STOP_NET_PCT = Decimal("-5")
STOP_WARN_NET_PCT = Decimal("-3")
T1_NET_PCT = Decimal("2")
T2_NET_PCT = Decimal("8")
T3_NET_PCT = Decimal("11")
MIN_PROFIT_IDR = Decimal("10000")
MAX_MAKER_ENTRY_SPREAD_PCT = Decimal("1.5")
MAX_TAKER_PROFIT_SPREAD_PCT = Decimal("0.5")
PRIORITY_RISE_PCT = Decimal("1.5")
PRIORITY_24H_IDR = Decimal("400000000")
REBOUND_DROP_PCT = Decimal("-7")
REBOUND_RECOVERY_PCT = Decimal("1.5")
REBOUND_COOLDOWN_SECONDS = 900
MAX_BOOK_AGE_SECONDS = 30

OPEN_SELL_STATES = frozenset((
    "NONE", "OPEN", "PARTIAL", "CANCEL_PENDING", "UNKNOWN",
))


def _d(value: Decimal, label: str, *, strictly_positive: bool = False) -> Decimal:
    if (type(value) is not Decimal or not value.is_finite()
            or abs(value) >= Decimal("1e28")
            or (value <= ZERO if strictly_positive else value < ZERO)):
        raise ValueError("STRATEGY_A_INVALID_" + label)
    return value


def _readonly(status: str, **details) -> dict:
    return {
        "status": status,
        "strategy": "A_STOP_5_REBOUND_WATCH",
        "source_verified_for_execution": False,
        "execution_allowed": False,
        "order_sent": False,
        "model_calls": 0,
        **details,
    }


def assess_ws3_priority_alert_offline(*, rise_pct: Decimal,
                                      rolling_24h_idr: Decimal,
                                      early_acceleration: bool) -> dict:
    """A 0.35% acceleration remains a scout, NEVER bypasses Telegram 1.5%."""
    if type(rise_pct) is not Decimal or not rise_pct.is_finite():
        raise ValueError("STRATEGY_A_INVALID_PRICE_MOVE")
    _d(abs(rise_pct), "PRICE_MOVE")
    _d(rolling_24h_idr, "TURNOVER")
    if type(early_acceleration) is not bool:
        raise ValueError("STRATEGY_A_INVALID_ACCELERATION_FLAG")
    priority = (rise_pct >= PRIORITY_RISE_PCT
                and rolling_24h_idr >= PRIORITY_24H_IDR)
    return _readonly(
        "PRIORITY_ALERT_ELIGIBLE_OFFLINE" if priority
        else "SCOUT_ONLY_NO_PRIORITY_TELEGRAM",
        priority_alert_eligible=priority,
        discovery_early_acceleration_retained=early_acceleration,
        minimum_priority_rise_pct=str(PRIORITY_RISE_PCT),
        minimum_priority_rolling_24h_idr=str(PRIORITY_24H_IDR),
        rolling_24h_not_executed_1m_volume=True,
    )


def assess_maker_entry_offline(*, proposed_buy_price: Decimal,
                               best_bid: Decimal, best_ask: Decimal) -> dict:
    """Only a resting BUY LIMIT/MOC; wide spread still has a hard risk cap."""
    for label, value in (
        ("BUY_PRICE", proposed_buy_price),
        ("BID", best_bid),
        ("ASK", best_ask),
    ):
        _d(value, label, strictly_positive=True)
    if best_ask <= best_bid:
        return _readonly("INVALID_OR_CROSSED_BOOK", maker_buy_eligible=False)
    spread = (best_ask - best_bid) * HUNDRED / (
        (best_ask + best_bid) / Decimal("2")
    )
    eligible = (
        proposed_buy_price <= best_bid
        and proposed_buy_price < best_ask
        and spread <= MAX_MAKER_ENTRY_SPREAD_PCT
    )
    return _readonly(
        "BUY_MAKER_MOC_REFERENCE" if eligible else "BUY_MAKER_REJECT",
        maker_buy_eligible=eligible,
        order_type_reference="LIMIT",
        time_in_force_reference="MOC",
        spread_pct=str(spread),
        max_maker_spread_pct=str(MAX_MAKER_ENTRY_SPREAD_PCT),
        no_fallback_to_taker=True,
    )


def _gross_if_taker(bids: tuple[tuple[Decimal, Decimal], ...],
                    quantity: Decimal) -> Decimal | None:
    remaining = quantity
    gross = ZERO
    last_price: Decimal | None = None
    for price, size in bids:
        _d(price, "BID_PRICE", strictly_positive=True)
        _d(size, "BID_SIZE", strictly_positive=True)
        if last_price is not None and price >= last_price:
            raise ValueError("STRATEGY_A_BIDS_NOT_DESCENDING")
        last_price = price
        take = min(size, remaining)
        gross += price * take
        remaining -= take
        # Validate the entire visible ladder, not only levels needed to fill.
        # Hidden unsorted tail must never be silently accepted as a valid book.
    return gross if remaining == ZERO else None


def _net_result(gross: Decimal, cost: Decimal, fee_pct: Decimal,
                slippage_pct: Decimal) -> tuple[Decimal, Decimal]:
    net = gross * (Decimal("1") - (fee_pct + slippage_pct) / HUNDRED)
    profit = net - cost
    return profit, (profit / cost) * HUNDRED


def assess_exit_strategy_a_offline(
    *, remaining_bot_qty_reference: Decimal,
    remaining_cost_including_buy_fee_idr: Decimal,
    tier_sell_qty_reference: Decimal,
    bid_levels: tuple[tuple[Decimal, Decimal], ...],
    best_ask: Decimal,
    taker_sell_fee_pct: Decimal,
    maker_sell_fee_pct: Decimal,
    taker_slippage_pct: Decimal,
    book_age_seconds: int,
    stop_latched: bool = False,
    open_sell_state: str = "NONE",
) -> dict:
    """STOP before tiers, with actual sellable bid-depth arithmetic only.

    All quantities/costs are caller-supplied reference values: NEVER an
    attestation that the bot owns those assets. Order lifecycle is NOT mutated.
    Caller must persist STOP latch and reconcile UNKNOWN/partial before SELL.
    """
    qty = _d(remaining_bot_qty_reference, "REMAINING_QTY", strictly_positive=True)
    cost = _d(remaining_cost_including_buy_fee_idr, "REMAINING_COST",
              strictly_positive=True)
    tier_qty = _d(tier_sell_qty_reference, "TIER_QTY", strictly_positive=True)
    _d(best_ask, "BEST_ASK", strictly_positive=True)
    for label, val in (("TAKER_FEE", taker_sell_fee_pct),
                       ("MAKER_FEE", maker_sell_fee_pct),
                       ("SLIPPAGE", taker_slippage_pct)):
        _d(val, label)
        if val >= Decimal("5"):
            raise ValueError("STRATEGY_A_FEE_OR_SLIPPAGE_TOO_HIGH")
    if (tier_qty > qty or type(book_age_seconds) is not int
            or type(stop_latched) is not bool
            or open_sell_state not in OPEN_SELL_STATES
            or type(bid_levels) is not tuple
            or len(bid_levels) > 20
            or any(type(level) is not tuple or len(level) != 2
                   for level in bid_levels)):
        raise ValueError("STRATEGY_A_EXIT_INPUT_INVALID")
    base = {
        "stop_latched": stop_latched,
        "open_sell_state": open_sell_state,
        "required_cancel_before_new_sell": open_sell_state in (
            "OPEN", "PARTIAL", "CANCEL_PENDING", "UNKNOWN"),
        "net_stop_threshold_pct": str(STOP_NET_PCT),
        "min_profit_idr_per_exit": str(MIN_PROFIT_IDR),
        "bot_owned_inventory_attested": False,
    }
    if not 0 <= book_age_seconds <= MAX_BOOK_AGE_SECONDS:
        return _readonly("STOP_LATCHED_NEEDS_FRESH_BOOK" if stop_latched
                         else "STALE_BOOK_RISK_ALERT_NO_ORDER", **base)
    if not bid_levels:
        return _readonly("STOP_LATCHED_NEEDS_BOOK" if stop_latched
                         else "NO_BID_DEPTH_RISK_ALERT_NO_ORDER", **base)
    full_gross = _gross_if_taker(bid_levels, qty)
    tier_gross = _gross_if_taker(bid_levels, tier_qty)
    if full_gross is None or tier_gross is None:
        return _readonly(
            "STOP_LATCHED_INSUFFICIENT_DEPTH" if stop_latched
            else "INSUFFICIENT_BID_DEPTH_RISK_ALERT_NO_ORDER",
            **base)
    best_bid = bid_levels[0][0]
    if best_ask <= best_bid:
        return _readonly("INVALID_OR_CROSSED_BOOK", **base)
    spread = ((best_ask - best_bid) * HUNDRED
              / ((best_ask + best_bid) / Decimal("2")))
    full_profit, full_roi = _net_result(
        full_gross, cost, taker_sell_fee_pct, taker_slippage_pct)
    tier_cost = cost * tier_qty / qty
    tier_profit, tier_roi = _net_result(
        tier_gross, tier_cost, taker_sell_fee_pct, taker_slippage_pct)
    maker_profit, maker_roi = _net_result(
        best_ask * tier_qty, tier_cost, maker_sell_fee_pct, ZERO)
    stop = stop_latched or full_roi <= STOP_NET_PCT
    details = dict(
        base, stop_latched=stop,
        projected_full_exit_net_roi_pct=str(full_roi),
        projected_full_exit_net_profit_idr=str(full_profit),
        projected_tier_taker_net_roi_pct=str(tier_roi),
        projected_tier_taker_profit_idr=str(tier_profit),
        projected_tier_maker_roi_if_filled_pct=str(maker_roi),
        spread_pct=str(spread),
        assumed_bid_depth_covers_full_qty=True,
        price_only_not_realized_pnl=True,
    )
    if stop:
        if open_sell_state != "NONE":
            return _readonly("STOP_CANCEL_RECONCILE_FIRST", **details)
        return _readonly("STOP_TAKER_PREVIEW_ONLY", **details)
    if open_sell_state in ("CANCEL_PENDING", "UNKNOWN"):
        return _readonly("ORDER_UNKNOWN_RECONCILE_ONLY", **details)
    if (tier_roi >= T3_NET_PCT and tier_profit >= MIN_PROFIT_IDR
            and spread <= MAX_TAKER_PROFIT_SPREAD_PCT):
        return _readonly(
            "T3_TAKER_PREVIEW_ONLY" if open_sell_state == "NONE"
            else "T3_CANCEL_T1_AND_RECONCILE_FIRST", **details)
    if (tier_roi >= T2_NET_PCT and tier_profit >= MIN_PROFIT_IDR
            and spread <= MAX_TAKER_PROFIT_SPREAD_PCT):
        return _readonly(
            "T2_TAKER_PREVIEW_ONLY" if open_sell_state == "NONE"
            else "T2_CANCEL_T1_AND_RECONCILE_FIRST", **details)
    if maker_roi >= T1_NET_PCT and maker_profit >= MIN_PROFIT_IDR:
        return _readonly(
            "T1_MAKER_MOC_PREVIEW_ONLY" if open_sell_state == "NONE"
            else "T1_MAKER_ALREADY_RESTING_REVIEW",
            **dict(details, t1_price_is_resting_ask_not_executable_bid=True))
    return _readonly(
        "LOSS_WARNING_HOLD" if full_roi <= STOP_WARN_NET_PCT
        else "HOLD_NO_PROFIT_TIER", **details)


def assess_rebound_watch_offline(
    *, settled_idr_verified: bool, closed_epoch: int, now_epoch: int,
    pre_drop_reference_price: Decimal,
    closed_1m_quotes: tuple[tuple[int, Decimal], ...],
    rolling_24h_idr: Decimal,
    trade_activity_verified: bool,
    orderbook_fresh_verified: bool,
    spread_pct: Decimal,
) -> dict:
    """As-of closed observations, cooldown, 7% drop, 1.5% rebound, NO BUY.

    This never uses future lows or assumes sparse WS3 last prices constitute
    closed candles. Boolean verification flags are NOT issuer attestation.
    """
    if (type(settled_idr_verified) is not bool
            or type(closed_epoch) is not int
            or type(now_epoch) is not int
            or closed_epoch <= 0 or now_epoch < closed_epoch
            or type(trade_activity_verified) is not bool
            or type(orderbook_fresh_verified) is not bool
            or type(closed_1m_quotes) is not tuple
            or len(closed_1m_quotes) > 120):
        raise ValueError("STRATEGY_A_REBOUND_INPUT_INVALID")
    ref = _d(pre_drop_reference_price, "PRE_DROP", strictly_positive=True)
    _d(rolling_24h_idr, "REBOUND_TURNOVER")
    _d(spread_pct, "REBOUND_SPREAD")
    if not settled_idr_verified:
        return _readonly("REBOUND_BLOCKED_UNSETTLED_POSITION",
                         rebound_entry_allowed=False)
    if now_epoch - closed_epoch < REBOUND_COOLDOWN_SECONDS:
        return _readonly("REBOUND_REENTRY_COOLDOWN",
                         rebound_entry_allowed=False)
    if len(closed_1m_quotes) < 3:
        return _readonly("REBOUND_SOURCE_INCOMPLETE",
                         rebound_entry_allowed=False)
    prior_epoch: int | None = None
    prices: list[Decimal] = []
    for epoch, price in closed_1m_quotes:
        if (type(epoch) is not int or epoch % 60 != 0
                or epoch > now_epoch or
                (prior_epoch is not None and epoch != prior_epoch + 60)):
            return _readonly("REBOUND_SOURCE_INCOMPLETE",
                             rebound_entry_allowed=False)
        prices.append(_d(price, "CLOSED_PRICE", strictly_positive=True))
        prior_epoch = epoch
    if prior_epoch is None or now_epoch - prior_epoch > 90:
        return _readonly("REBOUND_SOURCE_STALE",
                         rebound_entry_allowed=False)
    low = min(prices)
    latest = prices[-1]
    drop_pct = (low / ref - Decimal("1")) * HUNDRED
    recovery_pct = (latest / low - Decimal("1")) * HUNDRED
    recovering = prices[-1] > prices[-2] > prices[-3]
    liquid = (rolling_24h_idr >= PRIORITY_24H_IDR
              and spread_pct <= MAX_MAKER_ENTRY_SPREAD_PCT)
    confirmed_inputs = trade_activity_verified and orderbook_fresh_verified
    candidate = (drop_pct <= REBOUND_DROP_PCT
                 and recovery_pct >= REBOUND_RECOVERY_PCT
                 and recovering and liquid and confirmed_inputs)
    return _readonly(
        "REBOUND_CANDIDATE_REQUIRES_NEW_AI_OWNER_FINAL" if candidate
        else "REBOUND_WATCH_UNCONFIRMED",
        rebound_entry_allowed=False,
        rebound_candidate_for_review=candidate,
        low_asof=str(low),
        observed_drop_pct=str(drop_pct),
        observed_recovery_pct=str(recovery_pct),
        closed_minute_recovery_confirmed=recovering,
        independently_verified_g1=False,
        fresh_three_role_ai_verified=False,
        new_owner_final_verified=False,
        no_averaging_down=True,
    )
