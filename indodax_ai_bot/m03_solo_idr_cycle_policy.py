"""Original V2 solo-position IDR scalping policy, deterministic, offline-only.

This is a non-executable decision/planning contract. It cannot submit trades,
infer asset ownership, turn snapshots into a closed candle, or call paid AI.

Trading universe: IDR quote only, FLOKI and PEPE manual-only. Existing old
special asset ledger remains historical/read-only; it is not a new allocation
or a bot trading permit. Any free IDR is a potential budget, but no positive
amount guarantees that an exchange-valid/profitable order exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re

_IDR_PAIR = re.compile(r"^[a-z0-9]{2,24}_idr$")
_MANUAL_ONLY = frozenset({"floki_idr", "pepe_idr"})
_ROLLING_MIN_IDR = Decimal("150000000")
_NET_PROFIT_FLOOR_IDR = Decimal("10000")
_NET_ROI_FLOOR_PCT = Decimal("2")
_MAX_CONCURRENT_BOT_POSITIONS = 1


class SoloIDRPolicyInvalid(ValueError):
    """Typed deterministic validation error, no credentials/source payloads."""


def _number(raw, *, allow_zero: bool = False) -> Decimal:
    if type(raw) not in (str, int, Decimal):
        raise SoloIDRPolicyInvalid("SOLO_IDR_INVALID_MONEY_OR_METRIC")
    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        raise SoloIDRPolicyInvalid("SOLO_IDR_INVALID_MONEY_OR_METRIC") from None
    if (not value.is_finite()
            or (value < 0 if allow_zero else value <= 0)
            or value != 0 and abs(value.adjusted()) > 20):
        raise SoloIDRPolicyInvalid("SOLO_IDR_INVALID_MONEY_OR_METRIC")
    return value


def main_idr_pair_status(pair: str) -> str:
    """All IDR pairs, other than the two owner-managed manual assets.

    No hidden hard exclusion for USDT/USDC: trend, fee, spread, market rules
    and net edge determine whether an IDR quote market is worth considering.
    """
    if type(pair) is not str or not _IDR_PAIR.fullmatch(pair):
        return "NOT_AN_IDR_SPOT_PAIR"
    if pair in _MANUAL_ONLY:
        return "MANUAL_ONLY_ASSET"
    return "IDR_PAIR_ELIGIBLE_FOR_PUBLIC_WATCH"


def main_available_idr_review(
    *, exchange_free_idr, unaccounted_commitments_idr,
    all_pair_open_orders_verified: bool, account_snapshot_fresh: bool,
) -> dict:
    """Free IDR minus separately un-locked commitments, never locked twice.

    Historical FLOKI/PEPE rebuy sub-buckets are NOT deducted. Do not erase
    their old ledger. Free IDR is only a calculated PREVIEW until all
    committed/open order scope, account access and exchange rules are proven.
    """
    free = _number(exchange_free_idr, allow_zero=True)
    commitments = _number(unaccounted_commitments_idr, allow_zero=True)
    if type(all_pair_open_orders_verified) is not bool or (
            type(account_snapshot_fresh) is not bool):
        raise SoloIDRPolicyInvalid("SOLO_IDR_INVALID_ACCOUNT_FLAGS")
    usable = max(Decimal(0), free - commitments)
    return {
        "status": (
            "PREVIEW_BALANCE_FRESH_NOT_EXECUTION_AUTHORITY"
            if all_pair_open_orders_verified and account_snapshot_fresh
            else "ACCOUNT_OR_OPEN_ORDERS_UNVERIFIED"
        ),
        "free_idr_observed": format(free, "f"),
        "unaccounted_commitments_idr": format(commitments, "f"),
        "computed_uncommitted_idr_preview": format(usable, "f")
        if all_pair_open_orders_verified and account_snapshot_fresh else None,
        "fixed_ticket_ceiling_idr": None,
        "minimum_main_idr_capital_internal": "0",
        "manual_asset_reserve_deducted_idr": "0",
        "exchange_minimum_and_fee_still_apply": True,
        "execution_allowed": False,
    }


@dataclass(frozen=True)
class CycleFacts:
    """One exact source-bound snapshot, never persisted execution state.

    One bot-owned position at most. Existing manual/external holdings are
    excluded from bot-owned position counts, and cannot be sold by this policy.
    """
    bot_owned_open_position_count: int
    bot_owned_base_quantity: Decimal
    pending_buy: bool
    pending_sell: bool
    sell_fills_unsettled: bool
    order_status_unknown: bool
    risk_halt_new_entries: bool
    all_open_orders_verified: bool
    candidate_count: int
    has_fresh_idr_account_snapshot: bool
    owner_mode: str = "MANUAL"


def next_solo_idr_cycle(facts: CycleFacts) -> dict:
    """Prioritize managed SELL over any new BUY; never force a transaction."""
    if type(facts) is not CycleFacts:
        raise SoloIDRPolicyInvalid("SOLO_IDR_CYCLE_FACTS_REQUIRED")
    if (type(facts.bot_owned_open_position_count) is not int
            or not 0 <= facts.bot_owned_open_position_count <= 1
            or type(facts.candidate_count) is not int
            or not 0 <= facts.candidate_count <= 40
            or facts.owner_mode not in ("MANUAL", "AUTO")
            or any(type(x) is not bool for x in (
                facts.pending_buy, facts.pending_sell, facts.sell_fills_unsettled,
                facts.order_status_unknown, facts.risk_halt_new_entries,
                facts.all_open_orders_verified,
                facts.has_fresh_idr_account_snapshot,
            ))):
        raise SoloIDRPolicyInvalid("SOLO_IDR_CYCLE_FACTS_INVALID")
    qty = _number(facts.bot_owned_base_quantity, allow_zero=True)
    if ((facts.bot_owned_open_position_count == 0) != (qty == 0)
            or facts.pending_buy and (facts.pending_sell or qty > 0)
            or facts.sell_fills_unsettled and qty > 0
            or facts.pending_sell and qty == 0 and not facts.sell_fills_unsettled):
        return {
            "phase": "SOURCE_OR_POSITION_CONFLICT_RECONCILE",
            "new_buy_admitted": False, "focus": "RECONCILE_POSITION_OR_ORDER",
            "execution_allowed": False,
        }
    if facts.order_status_unknown:
        phase, focus = "UNKNOWN_ORDER_RECONCILIATION", "RECONCILE_WITH_EXCHANGE"
    elif qty > 0:
        phase, focus = (
            "SELL_FOCUS", "MANAGE_BOT_OWNED_EXIT_PROTECTIVE_AND_TARGET"
        )
    elif facts.pending_sell or facts.sell_fills_unsettled:
        phase, focus = "WAIT_SELL_SETTLEMENT_IDR", "RECONCILE_FILLS_AND_FREE_IDR"
    elif facts.pending_buy:
        phase, focus = "WAIT_BUY_FILL", "RECONCILE_BUY_FILL_AND_OWNERSHIP"
    elif facts.risk_halt_new_entries:
        phase, focus = "HALT_NEW_BUYS", "READONLY_RISK_REVIEW"
    elif not facts.has_fresh_idr_account_snapshot or (
            not facts.all_open_orders_verified):
        phase, focus = "SCAN_WITH_CAPITAL_UNVERIFIED", "PUBLIC_SCAN_NO_NEW_ORDER"
    elif facts.candidate_count > 0:
        phase, focus = "SCAN_AND_AI_REVIEW", "REVIEW_AT_MOST_TWO_BEST"
    else:
        phase, focus = "SCAN_NEW_IDR_PAIRS", "AWAIT_FRESH_MARKET_EVIDENCE"
    return {
        "phase": phase,
        "focus": focus,
        "new_buy_admitted": False,
        "scan_public_allowed": phase in (
            "SCAN_NEW_IDR_PAIRS", "SCAN_AND_AI_REVIEW",
            "SCAN_WITH_CAPITAL_UNVERIFIED", "HALT_NEW_BUYS",
        ),
        "new_buy_limit_while_position_open": 0,
        "max_bot_owned_positions": _MAX_CONCURRENT_BOT_POSITIONS,
        "max_deep_candidates_per_review": 2,
        "owner_mode": facts.owner_mode,
        "risk_veto_can_be_bypassed": False,
        "manually_held_assets_tradeable": False,
        "execution_allowed": False,
    }


def candidate_quality_watch(
    *, candidate: dict, now_epoch: int,
    current_order_budget_idr=None,
) -> dict:
    """Evidence-quality ranking (0..100), NOT probability or an entry signal.

    Uses actual source flags and metrics from original M05 WS3 inspector.
    Sources missing trade activity, real book or closed candles get no points
    for those features. This function NEVER authorizes an AI spend/order.
    """
    if type(candidate) is not dict or type(now_epoch) is not int or (
            now_epoch <= 0):
        raise SoloIDRPolicyInvalid("SOLO_IDR_QUALITY_INPUT_INVALID")
    pair = candidate.get("pair")
    pair_status = main_idr_pair_status(pair)
    base = {
        "pair": pair if type(pair) is str else None,
        "pair_status": pair_status,
        "status": "REJECT_PUBLIC_WATCH",
        "review_score_out_of_100": 0,
        "score_is_predicted_roi": False,
        "source_complete": False,
        "real_ai_responses_observed": False,
        "buy_recommended": False,
        "execution_allowed": False,
        "signals": {},
        "missing_evidence": [],
        "reject_reasons": [],
    }
    if pair_status != "IDR_PAIR_ELIGIBLE_FOR_PUBLIC_WATCH":
        base["reject_reasons"].append(pair_status)
        return base
    observed = candidate.get("observed_epoch")
    if (type(observed) is not int
            or not 0 <= now_epoch - observed <= 150):
        base["reject_reasons"].append("SOURCE_STALE_OR_FUTURE")
        return base
    turnover = _number(candidate.get("volume_24h_idr"),
                       allow_zero=True)
    if turnover < _ROLLING_MIN_IDR:
        base["reject_reasons"].append("BELOW_24H_IDR_TURNOVER_FLOOR")
        return base
    rise = _number(candidate.get("observed_rise_pct"))
    if rise > Decimal("500"):
        base["reject_reasons"].append("UNBOUNDED_PRICE_SOURCE")
        return base
    score = 15
    phase = candidate.get("momentum_phase")
    if phase == "EARLY_ACCELERATION_WATCH":
        score += 25
    elif phase == "PRICE_RISE_ROLLING_DELTA_POSITIVE":
        score += 16
    elif phase == "PRICE_RISE_ACTIVITY_UNCONFIRMED":
        score += 8
    elif phase == "EXTENDED_MOVE_REVIEW":
        score += 2
        base["reject_reasons"].append("EXTENDED_MOVE_DO_NOT_CHASE")
    else:
        base["reject_reasons"].append("MOMENTUM_NOT_QUALIFIED")
        return base
    if candidate.get("rolling_24h_delta_positive") is True:
        score += 5
    micro = candidate.get("microstructure_observation")
    if not isinstance(micro, dict):
        micro = {}
    trades = micro.get("trade_activity_observed") is True
    book = micro.get("orderbook_snapshot_observed") is True
    if trades:
        count = micro.get("captured_trade_count")
        if type(count) is int and count > 0:
            score += 12
            if (type(micro.get("source_reported_buy_count")) is int
                    and type(micro.get("source_reported_sell_count")) is int
                    and micro["source_reported_buy_count"] >
                    micro["source_reported_sell_count"]):
                score += 3  # Labeled sides only, NOT maker/taker pressure.
    else:
        base["missing_evidence"].append("SOURCE_TRADE_ACTIVITY_MISSING")
    if book:
        spread = _number(micro.get("spread_bps"), allow_zero=True)
        if spread <= 30:
            score += 16
        elif spread <= 70:
            score += 10
        elif spread <= 120:
            score += 3
        else:
            base["reject_reasons"].append("WIDE_SPREAD_REVIEW")
        bid_depth = _number(micro.get("bid_depth_top5_idr"),
                            allow_zero=True)
        ask_depth = _number(micro.get("ask_depth_top5_idr"),
                            allow_zero=True)
        if (current_order_budget_idr is not None and
                _number(current_order_budget_idr, allow_zero=True) > 0
                and bid_depth >= _number(
                    current_order_budget_idr, allow_zero=True) * 3
                and ask_depth >= _number(
                    current_order_budget_idr, allow_zero=True) * 3):
            score += 12
    else:
        base["missing_evidence"].append("FRESH_ORDER_BOOK_MISSING")
    tech = candidate.get("closed_candle_technical_evidence")
    if isinstance(tech, dict) and tech.get("1m_closed_verified") is True:
        if (tech.get("ema8_above_ema21") is True
                and tech.get("price_above_vwap") is True):
            score += 12
        if (tech.get("5m_trend_confirmed") is True
                and tech.get("15m_regime_not_adverse") is True):
            score += 8
    else:
        base["missing_evidence"].append("CLOSED_1M_5M_15M_NOT_VERIFIED")
    if micro.get("trade_gap_or_truncation") is True:
        base["reject_reasons"].append("SOURCE_TRADE_GAP_OR_TRUNCATED")
    base["missing_evidence"].extend((
        "COMPLETE_15M_1H_TRADE_TAPE_AND_G1_NOT_VERIFIED",
        "ACCOUNT_FEE_TAX_SLIPPAGE_NET_EDGE_UNVERIFIED",
        "THREE_INDEPENDENT_AI_RESPONSES_NOT_VERIFIED",
    ))
    base.update({
        "status": "PUBLIC_WATCH_QUALITY_RANKED_NOT_BUY_READY",
        "review_score_out_of_100": min(score, 100),
        "signals": {
            "momentum_phase": phase,
            "rolling_24h_turnover_idr": format(turnover, "f"),
            "price_rise_pct": format(rise, "f"),
            "trade_activity_source_observed": trades,
            "book_source_observed": book,
            "source_trade_side_is_aggressor_verified": False,
            "source_trade_tape_complete": False,
        },
    })
    return base


def candidate_fingerprint(pair: str, source_epoch: int, evidence: dict) -> str:
    """Stable deduplication key for re-evaluating only genuinely new evidence."""
    if (main_idr_pair_status(pair) != "IDR_PAIR_ELIGIBLE_FOR_PUBLIC_WATCH"
            or type(source_epoch) is not int or source_epoch <= 0
            or type(evidence) is not dict):
        raise SoloIDRPolicyInvalid("SOLO_IDR_FINGERPRINT_INPUT_INVALID")
    try:
        raw = json.dumps(
            {"pair": pair, "epoch": source_epoch, "evidence": evidence},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, UnicodeError, ValueError):
        raise SoloIDRPolicyInvalid("SOLO_IDR_FINGERPRINT_INPUT_INVALID") from None
    if len(raw) > 16000:
        raise SoloIDRPolicyInvalid("SOLO_IDR_FINGERPRINT_INPUT_INVALID")
    return sha256(raw).hexdigest()


def net_edge_policy_preview(
    *, ticket_idr, forecast_net_profit_idr, forecast_net_roi_pct,
    all_in_fees_and_slippage_verified: bool,
) -> dict:
    """Both floors, never force entry because a ticket is available."""
    ticket = _number(ticket_idr, allow_zero=True)
    net = _number(forecast_net_profit_idr, allow_zero=True)
    roi = _number(forecast_net_roi_pct, allow_zero=True)
    if type(all_in_fees_and_slippage_verified) is not bool:
        raise SoloIDRPolicyInvalid("SOLO_IDR_FEE_VALIDATION_INVALID")
    return {
        "ticket_idr": format(ticket, "f"),
        "profit_idr_floor": format(_NET_PROFIT_FLOOR_IDR, "f"),
        "net_roi_pct_floor": format(_NET_ROI_FLOOR_PCT, "f"),
        "both_net_thresholds_met_on_scenario": (
            ticket > 0 and net >= _NET_PROFIT_FLOOR_IDR
            and roi >= _NET_ROI_FLOOR_PCT
        ),
        "fee_scope_verified": all_in_fees_and_slippage_verified,
        "source_verified_for_g1": False,
        "buy_authorized": False, "execution_allowed": False,
    }
