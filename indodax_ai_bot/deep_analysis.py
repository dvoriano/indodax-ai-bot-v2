"""M03-B: source-bound *observational* scalp analysis. Never authorizes BUY.

Indicators use only fully closed candles returned by the public adapter.
Visible public trades may be truncated, so momentum and book signals are
research diagnostics until G1 provenance/coverage gates are implemented.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_CEILING
from hashlib import sha256
from typing import Callable

from .public_market import Candle, IndodaxPublicMarket, PublicCandleHistory, PublicDataError, PublicObservation
from .scanner import ScannerResult
from .source_coverage import public_coverage_report

ZERO = Decimal("0")
HUNDRED = Decimal("100")
# Conservative provisional review thresholds; NOT statistically fitted.
# Do not graduate this diagnostic to order permission without replay.
EXTREME_VOLUME_RATIO = Decimal("10")
EXTREME_RSI = Decimal("90")
VISIBLE_SELL_DOMINANCE_MULTIPLE = Decimal("3")


@dataclass(frozen=True)
class DeepCandidate:
    pair: str
    score: int | None
    state: str
    rejection_reason: str | None
    closed_1m_epoch: int | None = None
    rsi14: Decimal | None = None
    ema8: Decimal | None = None
    ema21: Decimal | None = None
    volume_ratio: Decimal | None = None
    spread_bps: Decimal | None = None
    visible_buy_idr: Decimal = ZERO
    visible_sell_idr: Decimal = ZERO
    book_imbalance: Decimal | None = None
    anomaly_flags: tuple[str, ...] = ()
    # M03-J closed-candle research only, never an entry or risk decision.
    market_structure_diagnostic: dict | None = None
    structure_replay: dict | None = None
    # M03-N sampled first-seen-minute ticker momentum, never closed OHLC.
    early_momentum: dict | None = None
    visible_buy_trade_count_sampled: int = 0
    visible_sell_trade_count_sampled: int = 0
    # M03-N extension: non-executable book-wall/rebound evidence.
    book_wall_diagnostic: dict | None = None
    wall_temporal_evidence: dict | None = None
    surge_experience_context: dict | None = None
    surge_pattern: dict | None = None
    public_maintenance_flag: bool = False
    # M03 observed PUBLIC quote+depth hypothetical ticket economics only.
    # Account-specific all-in fees are unknown. Never a tradable quote.
    public_quote_friction_diagnostic: dict | None = None
    # M07 public-source-bound gate, never capital/account or order authority.
    m07_same_candidate_public_preflight: dict | None = None

    def summary(self) -> dict:
        return {
            "scalp_setup_stage": classify_candidate_entry_stage(self),
            "pair": self.pair, "score": self.score, "state": self.state,
            "rejection_reason": self.rejection_reason,
            "closed_1m_epoch": self.closed_1m_epoch,
            "rsi14": None if self.rsi14 is None else str(self.rsi14.quantize(Decimal("0.01"))),
            "ema8": None if self.ema8 is None else str(self.ema8),
            "ema21": None if self.ema21 is None else str(self.ema21),
            "volume_ratio": None if self.volume_ratio is None else str(self.volume_ratio.quantize(Decimal("0.01"))),
            "spread_bps": None if self.spread_bps is None else str(self.spread_bps.quantize(Decimal("0.01"))),
            "visible_buy_idr": str(self.visible_buy_idr),
            "visible_sell_idr": str(self.visible_sell_idr),
            "visible_buy_trade_count_sampled": self.visible_buy_trade_count_sampled,
            "visible_sell_trade_count_sampled": self.visible_sell_trade_count_sampled,
            "visible_trade_side_counts_complete": False,
            "book_imbalance": None if self.book_imbalance is None else str(self.book_imbalance.quantize(Decimal("0.001"))),
            "anomaly_flags": list(self.anomaly_flags),
            "market_structure_diagnostic": self.market_structure_diagnostic,
            "structure_replay": self.structure_replay,
            "early_momentum": self.early_momentum,
            "book_wall_diagnostic": self.book_wall_diagnostic,
            "wall_temporal_evidence": self.wall_temporal_evidence,
            "surge_experience_context": self.surge_experience_context,
            "surge_pattern": self.surge_pattern,
            "public_maintenance_flag": self.public_maintenance_flag,
            "public_quote_friction_diagnostic":
                self.public_quote_friction_diagnostic,
            "m07_same_candidate_public_preflight":
                self.m07_same_candidate_public_preflight,
            "market_tradability_verified": False,
            "eligible_for_same_window_comparison": self.score is not None and not self.anomaly_flags,
            "trade_history_complete": False,
            "g1_verified": False,
            "trade_authorized": False,
        }


def _ema(values: tuple[Decimal, ...], period: int) -> Decimal:
    if len(values) < period:
        raise PublicDataError("SHORT_EMA_HISTORY")
    factor = Decimal(2) / Decimal(period + 1)
    result = sum(values[:period], ZERO) / Decimal(period)
    for value in values[period:]:
        result = (value - result) * factor + result
    return result


def _rsi14(closes: tuple[Decimal, ...]) -> Decimal:
    if len(closes) < 15:
        raise PublicDataError("SHORT_RSI_HISTORY")
    tail = closes[-15:]
    changes = [b - a for a, b in zip(tail, tail[1:])]
    gain = sum((max(ZERO, d) for d in changes), ZERO)
    loss = sum((max(ZERO, -d) for d in changes), ZERO)
    if gain == ZERO and loss == ZERO:
        return Decimal("50")
    if loss == ZERO:
        return HUNDRED
    return HUNDRED * gain / (gain + loss)


def _last_continuous(series: tuple[Candle, ...], interval: int, count: int) -> tuple[Candle, ...]:
    if len(series) < count:
        raise PublicDataError(f"INSUFFICIENT_{interval}S_COVERAGE")
    last = series[-count:]
    if any(a.interval != interval or b.interval != interval or (b.start - a.start) != interval
           for a, b in zip(last, last[1:])):
        raise PublicDataError(f"GAPPED_{interval}S_HISTORY")
    return last



# M03-J explicit provisional, UNCALIBRATED research definitions. These labels
# DO NOT change the score, source trust, eligibility, AI gate or Final policy.
# Use only closed, consecutive 1m bars. Any historical performance measured
# from these candles is PRICE-ONLY and never a realized net trade return.
_STRUCTURE_WINDOW = 22
_STRUCTURE_BASELINE_BARS = 10
_STRUCTURE_SURGE_VOLUME = Decimal("2")
_STRUCTURE_MIN_BODY_FRACTION = Decimal("0.5")


def classify_closed_market_structure(candles: tuple[Candle, ...]) -> dict:
    """Classify one closed 1m observation with NO future bars or public trade claims.

    Caller supplies only the historical window ending at the decision candle.
    Must be fully contiguous; 5m/15m/BTC and source trust remain external gates.
    """
    minute = _last_continuous(candles, 60, _STRUCTURE_WINDOW)
    current = minute[-1]
    earlier = minute[-11:-1]
    if (current.volume < ZERO or any(x.volume < ZERO for x in earlier)
            or any(x.high < max(x.open, x.close) or
                   x.low > min(x.open, x.close) or x.high < x.low for x in minute)):
        raise PublicDataError("INVALID_STRUCTURE_CANDLE")
    baseline = sum((x.volume for x in earlier), ZERO) / Decimal(len(earlier))
    if baseline <= ZERO:
        raise PublicDataError("NO_STRUCTURE_VOLUME_BASELINE")
    high = max(x.high for x in earlier)
    low = min(x.low for x in earlier)
    candle_range = current.high - current.low
    body_fraction = ((abs(current.close - current.open) / candle_range)
                     if candle_range > ZERO else ZERO)
    volume_ratio = current.volume / baseline
    label = "NO_CLOSED_CANDLE_SETUP"

    # Rejection must take precedence over breakout when a high briefly
    # exceeds visible resistance but the same CLOSED candle finishes below.
    if (current.high > high and current.close <= high
            and current.close < current.high):
        label = "FALSE_BREAKOUT_RISK"
    elif (current.close > high and current.close > current.open
          and volume_ratio >= _STRUCTURE_SURGE_VOLUME
          and body_fraction >= _STRUCTURE_MIN_BODY_FRACTION):
        label = "SURGE_BREAKOUT_OBSERVED"
    else:
        prev_closes = tuple(x.close for x in minute[:-1])
        ema8_before = _ema(prev_closes, 8)
        ema21_before = _ema(prev_closes, 21)
        previous = minute[-2]
        previous_down = (minute[-4].close > minute[-3].close >
                         minute[-2].close)
        if (previous_down and previous.close < previous.open
                and current.open <= previous.close
                and current.close > previous.open
                and current.close > current.open):
            label = "REVERSAL_ATTEMPT_OBSERVED"
        elif (ema8_before > ema21_before
              and current.low <= ema8_before
              and current.close >= ema8_before
              and current.close > current.open):
            label = "PULLBACK_REBOUND_OBSERVED"

    return {
        "label": label,
        "closed_1m_epoch": current.start + 60,
        "reference_prior_10m_high": str(high),
        "reference_prior_10m_low": str(low),
        "visible_1m_volume_vs_prior_10m": str(volume_ratio),
        "closed_1m_body_fraction": str(body_fraction),
        "lookback_closed_1m": _STRUCTURE_WINDOW,
        "full_trade_flow_proven": False,
        "g1_verified": False,
        "calibrated": False,
        "trade_signal": False,
    }


def replay_closed_market_structure(
    candles: tuple[Candle, ...], *, forward_bars: int = 3,
) -> dict:
    """Historical candle-only *diagnostic* replay, no fill/fee/ROI claims.

    For sample ending at index k, classifier sees ONLY candles[:k+1].
    Its outcome refers to close at k+forward_bars, never used in that label.
    Missing bar/time continuity refuses the WHOLE replay (no invented bars).
    """
    if type(forward_bars) is not int or forward_bars != 3:
        raise PublicDataError("STRUCTURE_REPLAY_REQUIRES_3_CLOSED_BARS")
    if len(candles) < _STRUCTURE_WINDOW + forward_bars:
        return {
            "status": "INSUFFICIENT_HISTORY_FOR_REPLAY",
            "sample_count": 0,
            "events": [],
            "g1_verified": False,
            "price_only_not_net_roi": True,
            "out_of_sample_validated": False,
        }
    if any(a.interval != 60 or b.interval != 60 or b.start - a.start != 60
           for a, b in zip(candles, candles[1:])):
        raise PublicDataError("GAPPED_STRUCTURE_REPLAY_HISTORY")
    if any(c.close <= ZERO for c in candles):
        raise PublicDataError("INVALID_STRUCTURE_REPLAY_PRICE")
    events = []
    # Replay at most a few dozen candles per snapshot; preserve every event
    # in this bounded history without selecting on its future performance.
    for end in range(_STRUCTURE_WINDOW, len(candles)-forward_bars+1):
        current = candles[end-1]
        label = classify_closed_market_structure(tuple(candles[end-_STRUCTURE_WINDOW:end]))["label"]
        if label != "NO_CLOSED_CANDLE_SETUP":
            future = candles[end+forward_bars-1]
            gross_bps = (future.close / current.close - Decimal(1)) * Decimal(10000)
            events.append({
                "label": label,
                "closed_1m_epoch": current.start + 60,
                "forward_3m_close_to_close_gross_bps": str(gross_bps),
            })
    return {
        "status": "CANDLE_ONLY_REPLAY_UNCALIBRATED",
        "sample_count": len(candles) - _STRUCTURE_WINDOW - forward_bars + 1,
        "labeled_event_count": len(events),
        "events": events,
        "g1_verified": False,
        "price_only_not_net_roi": True,
        "fees_slippage_fills_not_measured": True,
        "out_of_sample_validated": False,
        "ablation_validated": False,
        "trade_signal": False,
    }






# M03 research-only scenarios, explicitly assumed, NOT observed exchange fees,
# actual bid/ask spread, position-dependent depth, slippage or proven fills.
# Never pass modeled results into source eligibility, AI, G1 or BUY.
_M03_FRICTION_SCENARIOS = (
    ("LOW_ASSUMED", Decimal("20"), Decimal("10"), Decimal("15")),
    ("BASE_ASSUMED", Decimal("30"), Decimal("25"), Decimal("30")),
    ("STRESS_ASSUMED", Decimal("50"), Decimal("50"), Decimal("60")),
)
_M03_SETUP_LABELS = (
    "SURGE_BREAKOUT_OBSERVED",
    "PULLBACK_REBOUND_OBSERVED",
)
_M03_TRAIN_MIN_NONOVERLAP_EVENTS = 5


@dataclass(frozen=True)
class OwnerEnteredAllInFeeSnapshot:
    """Owner-transcribed IDR taker buy/sell fee, NEVER account-authenticated."""
    pair: str
    buy_all_in_bps: Decimal
    sell_all_in_bps: Decimal
    profile_seen_epoch: int
    market: str = "IDR"
    order_role: str = "TAKER"
    source_kind: str = "OWNER_ENTERED_INDODAX_PROFILE_UNATTESTED"

    @classmethod
    def from_mapping(cls, data: object) -> "OwnerEnteredAllInFeeSnapshot":
        if (type(data) is not dict or set(data) != {
            "pair", "buy_all_in_bps", "sell_all_in_bps",
            "profile_seen_epoch", "market", "order_role", "source_kind",
        }):
            raise ValueError("M03_OWNER_FEE_SNAPSHOT_SCHEMA_INVALID")
        pair = data["pair"]
        if (type(pair) is not str or not pair.endswith("_idr")
                or len(pair) > 30 or not pair[:-4].isalnum()
                or pair.lower() != pair):
            raise ValueError("M03_OWNER_FEE_PAIR_INVALID")
        if (data["market"] != "IDR"
                or data["order_role"] != "TAKER"
                or data["source_kind"] != "OWNER_ENTERED_INDODAX_PROFILE_UNATTESTED"):
            raise ValueError("M03_OWNER_FEE_UNSUPPORTED_SCOPE")
        epoch = data["profile_seen_epoch"]
        if type(epoch) is not int or epoch <= 0:
            raise ValueError("M03_OWNER_FEE_EPOCH_INVALID")
        vals = []
        for field in ("buy_all_in_bps", "sell_all_in_bps"):
            value = data[field]
            if (type(value) is not str or len(value) > 16
                    or not value.isascii()):
                raise ValueError("M03_OWNER_FEE_DECIMAL_INVALID")
            try:
                bps = Decimal(value)
            except (ArithmeticError, ValueError):
                raise ValueError("M03_OWNER_FEE_DECIMAL_INVALID") from None
            if not bps.is_finite() or not ZERO <= bps <= Decimal("500"):
                raise ValueError("M03_OWNER_FEE_RATE_OUT_OF_BOUNDS")
            vals.append(bps)
        return cls(pair, vals[0], vals[1], epoch)


# Published INDODAX PRO IDR total All-in Fees: service/tax/CFX are
# ALREADY included; never add them separately. User-provided screenshot
# from the official help article, verified 2 Nov 2026, post 1 Oct 2026
# published fee revision. Public schedule is NOT signed/account-attested.
# Market taker is the relevant role for crossing the visible ASK then BID.
_M03_IDR_PRO_PUBLISHED_ALL_IN_PERCENT = {
    "BUY_MAKER": Decimal("0.10555"),
    "BUY_TAKER": Decimal("0.20555"),
    "SELL_MAKER": Decimal("0.31555"),
    "SELL_TAKER": Decimal("0.41555"),
}
_M03_IDR_PRO_FEE_HELP_URL = (
    "https://help.indodax.com/hc/id/articles/"
    "4416646599705-Rincian-Biaya-Transaksi-di-INDODAX"
)


def _m03_all_in_total_fee_preview(
    buy_fee_bps: Decimal, sell_fee_bps: Decimal,
    illustrative_idr: Decimal, proceeds: Decimal, acquired: Decimal,
) -> dict:
    """Apply ONLY full fee totals. Public ASK/BID depth embeds spread already.

    No maker fill, per-account fee, actual exchange execution or settlement
    is inferred. Same calculator for public published and owner-entered
    sensitivity; do not double-count all-in components or book spread.
    """
    buy_cost = illustrative_idr * (Decimal(1) + buy_fee_bps / Decimal(10000))
    net_bid_proceeds = proceeds * (Decimal(1) - sell_fee_bps / Decimal(10000))
    modeled_pnl = net_bid_proceeds - buy_cost
    desired_pnl = max(Decimal("10000"), buy_cost * Decimal("0.02"))
    target_gross_sale = (buy_cost + desired_pnl) / (
        Decimal(1) - sell_fee_bps / Decimal(10000)
    )
    return {
        "modeled_immediate_net_profit_idr_unverified": str(
            modeled_pnl.quantize(Decimal("0.01"))
        ),
        "modeled_immediate_net_roi_percent_unverified": str(
            (modeled_pnl / buy_cost * Decimal(100)).quantize(Decimal("0.01"))
        ),
        "policy_required_future_sell_gross_idr_unverified": str(
            target_gross_sale.quantize(Decimal("0.01"))
        ),
        "policy_required_future_sell_vwap_idr_unverified": str(
            target_gross_sale / acquired
        ),
        "future_sell_liquidity_and_fill_unknown": True,
        "projected_or_realized_profit_verified": False,
        "execution_allowed": False,
    }


def assess_public_pair_order_constraints(
    obs: PublicObservation, *,
    illustrative_idr: Decimal, observed_book_acquired: Decimal,
) -> dict:
    """Same /api/pairs public metadata; no per-account EXECUTION authority.

    Official `price_precision` is a PRICE STEP in documented pair examples,
    not the number of decimal places. `quantity_increment` is the quantity
    step. Missing, ambiguous, invalid or rounded-down quantities are retained
    as UNKNOWN or public warnings, NEVER used for a tradable order.
    """
    result = {
        "stage": "M07_PUBLIC_PAIR_RULES_SAME_DISCOVERY_BATCH",
        "pair": obs.pair,
        "status": "PUBLIC_PAIR_RULES_UNAVAILABLE",
        "metadata_source": "INDODAX_PUBLIC_PAIRS_UNATTESTED",
        "metadata_observed_epoch": obs.snapshot.source.observed_epoch,
        "price_step_idr": None,
        "quantity_increment_base": None,
        "min_order_idr": None,
        "min_order_base_qty": None,
        "quoted_bid_ask_on_published_price_step": False,
        "illustrative_idr_meets_public_minimum": False,
        "illustrative_base_qty_meets_public_minimum": False,
        "illustrative_base_qty_rounded_down": None,
        "illustrative_qty_exactly_on_increment": False,
        "illustrative_rounded_qty_meets_public_minimum": False,
        "public_market_suspended_flag_explicit_zero": False,
        "public_wallet_maintenance_caution": None,
        "same_public_pair_rule_batch": bool(obs.pair_rule_fields),
        "exchange_order_tradability_verified": False,
        "actual_order_precision_and_balance_verified": False,
        "min_order_for_real_account_verified": False,
        "g1_verified": False,
        "execution_allowed": False,
    }
    rules = obs.pair_rule_fields
    if not isinstance(rules, dict):
        return dict(result, status="INVALID_PUBLIC_RULES_CONTAINER")
    names = (
        "price_precision", "quantity_increment",
        "trade_min_base_currency", "trade_min_traded_currency",
        "is_market_suspended",
    )
    if any(name not in rules for name in names):
        return dict(result, status="MISSING_PUBLIC_RULES_UNVERIFIED")
    if rules["is_market_suspended"] not in (0, False, "0"):
        return dict(result, status="PUBLIC_MARKET_SUSPENDED_OR_UNKNOWN")
    result["public_market_suspended_flag_explicit_zero"] = True
    maintenance = rules.get("is_maintenance")
    if maintenance in (0, False, "0"):
        result["public_wallet_maintenance_caution"] = False
    elif maintenance in (1, True, "1"):
        result["public_wallet_maintenance_caution"] = True
    try:
        parsed = []
        for name in names[:4]:
            raw = rules[name]
            if type(raw) not in (str, int, float, Decimal):
                raise ValueError("INVALID_PUBLIC_PRICE_OR_QTY_RULE")
            val = Decimal(str(raw))
            if (not val.is_finite() or val <= ZERO or
                    not -30 <= val.adjusted() <= 24):
                raise ValueError("INVALID_PUBLIC_PRICE_OR_QTY_RULE")
            parsed.append(val)
        tick, increment, min_idr, min_qty = parsed
        if (type(illustrative_idr) is not Decimal
                or type(observed_book_acquired) is not Decimal
                or not illustrative_idr.is_finite()
                or not observed_book_acquired.is_finite()
                or illustrative_idr <= ZERO or observed_book_acquired <= ZERO):
            raise ValueError("INVALID_ILLUSTRATIVE_QUANTITY")
    except (ArithmeticError, ValueError):
        return dict(result, status="INVALID_PUBLIC_PRICE_OR_QTY_RULE")
    bid = obs.snapshot.best_bid_idr
    ask = obs.snapshot.best_ask_idr
    # Huge ratios between dynamic price and step sizes can exceed Decimal
    # context precision. Unrepresentable book/precision stays UNKNOWN.
    try:
        on_step = bid % tick == ZERO and ask % tick == ZERO
        # For observed book-walk BUY quantities: market buys funded in IDR
        # are not assured to return exact base precision after fees.
        rounded_qty = (observed_book_acquired // increment) * increment
        notional_enough = illustrative_idr >= min_idr
        qty_enough = observed_book_acquired >= min_qty
        rounded_enough = rounded_qty >= min_qty
        increment_aligned = rounded_qty == observed_book_acquired
    except ArithmeticError:
        return dict(result, status="PUBLIC_RULES_DECIMAL_PRECISION_UNAVAILABLE")
    result.update({
        "status": (
            "PUBLIC_RULES_CONSISTENT_RESEARCH_ONLY"
            if on_step and notional_enough and qty_enough and rounded_enough
            and increment_aligned
            else "PUBLIC_RULES_QTY_ROUND_DOWN_REQUIRED"
            if on_step and notional_enough and qty_enough and rounded_enough
            else "PUBLIC_RULE_CONSTRAINT_WARNING"
        ),
        "price_step_idr": str(tick),
        "quantity_increment_base": str(increment),
        "min_order_idr": str(min_idr),
        "min_order_base_qty": str(min_qty),
        "quoted_bid_ask_on_published_price_step": on_step,
        "illustrative_idr_meets_public_minimum": notional_enough,
        "illustrative_base_qty_meets_public_minimum": qty_enough,
        "illustrative_base_qty_rounded_down": str(rounded_qty),
        "illustrative_qty_exactly_on_increment": increment_aligned,
        "illustrative_rounded_qty_meets_public_minimum": rounded_enough,
    })
    return result


def _m07_post_round_sell_research(
    obs: PublicObservation, *,
    pair_rules: dict, illustrative_idr: Decimal,
    acquired_before_real_buy_fees: Decimal,
    buy_all_in_bps: Decimal, sell_all_in_bps: Decimal,
) -> dict:
    """Original M07-02 read-only minimum SELL and price-grid sensitivity.

    Visible ASK→BID book arithmetic and public /api/pairs constraints only.
    The hypothetical asset amount is BEFORE any real BUY fill/fee/rounding.
    This is NOT a preflight authorization, a bot-owned lot, or a trade quote.
    """
    result = {
        "stage": "M07_POST_ROUND_SELL_MINIMUM_RESEARCH",
        "status": "PUBLIC_RULES_UNQUALIFIED",
        "pair": obs.pair,
        "sell_qty_rounded_down_before_real_buy_fees": None,
        "round_down_unallocated_base_qty": None,
        "visible_bid_book_sell_gross_idr": None,
        "visible_bid_book_sell_after_all_in_fee_idr": None,
        "sell_notional_meets_public_minimum": False,
        "minimum_idr_met_on_buy_and_sell": False,
        "required_future_sell_vwap_before_price_tick": None,
        "required_future_sell_limit_price_ceiled_to_tick": None,
        "published_fee_totals_counted_once": True,
        "spread_and_visible_book_impact_already_in_prices": True,
        "fee_deducted_base_asset_unknown": True,
        "actual_post_buy_sellable_quantity_verified": False,
        "future_sell_book_liquidity_verified": False,
        "exchange_order_acceptance_verified": False,
        "owner_free_spendable_idr_verified": False,
        "g1_verified": False,
        "execution_allowed": False,
    }
    if not isinstance(pair_rules, dict) or pair_rules.get("status") not in (
        "PUBLIC_RULES_CONSISTENT_RESEARCH_ONLY",
        "PUBLIC_RULES_QTY_ROUND_DOWN_REQUIRED",
    ):
        return result
    try:
        qty = Decimal(pair_rules["illustrative_base_qty_rounded_down"])
        tick = Decimal(pair_rules["price_step_idr"])
        min_idr = Decimal(pair_rules["min_order_idr"])
        min_qty = Decimal(pair_rules["min_order_base_qty"])
        if (not all(type(x) is Decimal and x.is_finite() and x >= ZERO
                    for x in (illustrative_idr, buy_all_in_bps,
                              sell_all_in_bps, qty, tick, min_idr, min_qty))
                or tick <= ZERO or qty <= ZERO or min_qty <= ZERO
                or min_idr <= ZERO or illustrative_idr < min_idr
                or buy_all_in_bps > Decimal("500")
                or sell_all_in_bps >= Decimal("10000")
                or qty < min_qty):
            return dict(result, status="INVALID_POST_ROUND_PUBLIC_CONSTRAINTS")
        if (type(acquired_before_real_buy_fees) is not Decimal
                or not acquired_before_real_buy_fees.is_finite()
                or acquired_before_real_buy_fees < qty):
            return dict(result, status="INVALID_ASK_BOOK_ACQUIRED_QUANTITY")
        if qty != (
            (qty // Decimal(pair_rules["quantity_increment_base"]))
            * Decimal(pair_rules["quantity_increment_base"])
        ):
            return dict(result, status="QUANTITY_NOT_ON_PUBLIC_INCREMENT")
        # Same bounded depth used by original M03 book walk. No second REST.
        remaining = qty
        sell_gross = ZERO
        for price, visible_qty in obs.bid_book_levels:
            if (type(price) is not Decimal or type(visible_qty) is not Decimal
                    or not price.is_finite() or not visible_qty.is_finite()
                    or price <= ZERO or visible_qty <= ZERO):
                return dict(result, status="INVALID_VISIBLE_BID_BOOK")
            sold = min(remaining, visible_qty)
            sell_gross += sold * price
            remaining -= sold
            if remaining == ZERO:
                break
        if remaining != ZERO:
            return dict(result, status="VISIBLE_BID_DEPTH_INSUFFICIENT_AFTER_ROUND")
        sell_net = sell_gross * (Decimal(1) - sell_all_in_bps / Decimal(10000))
        # Original BUY acquired units are not exchange-owned, and BUY fees
        # might be deducted from asset rather than IDR, making qty less.
        # Future price is a *mathematical requirement*, never a fill promise.
        buy_cost = illustrative_idr * (
            Decimal(1) + buy_all_in_bps / Decimal(10000)
        )
        target_net = buy_cost + max(Decimal("10000"), buy_cost * Decimal("0.02"))
        min_sell_gross = target_net / (
            Decimal(1) - sell_all_in_bps / Decimal(10000)
        )
        required_vwap = min_sell_gross / qty
        limit_price_on_grid = (
            (required_vwap / tick).to_integral_value(rounding=ROUND_CEILING)
            * tick
        )
        result.update({
            "status": (
                "PUBLIC_POST_ROUND_SELL_RESEARCH_ONLY"
                if sell_gross >= min_idr
                else "PUBLIC_POST_ROUND_SELL_BELOW_MIN_IDR"
            ),
            "sell_qty_rounded_down_before_real_buy_fees": str(qty),
            "round_down_unallocated_base_qty": str(
                acquired_before_real_buy_fees - qty
            ),
            "visible_bid_book_sell_gross_idr": str(sell_gross),
            "visible_bid_book_sell_after_all_in_fee_idr": str(sell_net),
            "sell_notional_meets_public_minimum": sell_gross >= min_idr,
            "minimum_idr_met_on_buy_and_sell": sell_gross >= min_idr,
            "required_future_sell_vwap_before_price_tick": str(required_vwap),
            "required_future_sell_limit_price_ceiled_to_tick": str(
                limit_price_on_grid
            ),
        })
        return result
    except (ArithmeticError, ValueError, KeyError, TypeError):
        return dict(result, status="POST_ROUND_PRECISION_OR_RULE_UNAVAILABLE")


def observe_public_quote_friction(
    obs: PublicObservation, *, illustrative_idr: Decimal = Decimal("100000"),
    owner_fee_snapshot: OwnerEnteredAllInFeeSnapshot | None = None,
) -> dict:
    """Bounded hypothetical roundtrip book walk on ONE existing M03 observation.

    Ticker and book originate in different REST responses and are never a
    simultaneous executable exchange quote. Fee schedule may vary by account
    and maker/taker and is NOT provided by these PUBLIC endpoints. Keep
    measured quote/depth observations apart from *assumed* fees/slippage.
    """
    src = obs.snapshot.source
    diagnostic = {
        "stage": "M03_PUBLIC_QUOTE_DEPTH_FRICTION_RESEARCH",
        "pair": obs.pair,
        "closed_1m_epoch": src.closed_1m_epoch,
        "observed_epoch": src.observed_epoch,
        "public_source_provenance": src.provenance,
        "status": "UNAVAILABLE",
        "illustrative_not_owned_idr": str(illustrative_idr),
        "observed_ticker_spread_bps": None,
        "book_walk_immediate_exit_before_fees_idr": None,
        "book_walk_immediate_exit_before_fees_bps": None,
        "public_book_ticker_cross_request_coherent": False,
        "public_book_depth_guaranteed": False,
        "fees_all_in_account_verified": False,
        "fees_source": "INDODAX_ACCOUNT_PROFILE_ALL_IN_FEES_NOT_CONNECTED",
        "fee_schedule_oct_2026_can_change": True,
        "tax_cfx_components_not_double_counted": True,
        "model_assumptions_not_actual_all_in_fee": True,
        "fee_and_slippage_scenarios": None,
        "owner_entered_fee_sensitivity": None,
        "owner_entered_fee_status": "NOT_PROVIDED",
        "published_idr_pro_all_in_fee_table": None,
        "published_idr_pro_taker_fee_sensitivity": None,
        "selected_all_in_fee_basis": "NO_VALID_BOOK_WALK",
        "public_post_round_sell_research": {
            "status": "NOT_EVALUATED_NO_VALID_BOOK_WALK",
            "execution_allowed": False,
        },
        "public_pair_order_constraints": {
            "status": "NOT_EVALUATED_NO_VALID_BOOK_WALK",
            "exchange_order_tradability_verified": False,
            "execution_allowed": False,
        },
        "net_roi_verified": False,
        "eligible_live_candidates": 0,
        "g1_verified": False,
        "execution_allowed": False,
    }
    if (type(illustrative_idr) is not Decimal
            or not illustrative_idr.is_finite()
            or illustrative_idr <= ZERO
            or illustrative_idr.adjusted() > 9):
        return dict(diagnostic, status="INVALID_HYPOTHETICAL_TICKET")
    bid, ask = obs.snapshot.best_bid_idr, obs.snapshot.best_ask_idr
    if (src.pair != obs.pair
            or src.observed_epoch < src.closed_1m_epoch
            or src.observed_epoch - src.closed_1m_epoch > 120
            or not bid.is_finite() or not ask.is_finite()
            or bid <= ZERO or ask <= bid):
        return dict(diagnostic, status="STALE_OR_INVALID_PUBLIC_QUOTE")
    spread_bps = (ask-bid)*Decimal(10000)/ask
    diagnostic["observed_ticker_spread_bps"] = str(spread_bps.quantize(Decimal("0.01")))
    bids, asks = obs.bid_book_levels, obs.ask_book_levels
    if not bids or not asks or len(bids) > 10 or len(asks) > 10:
        return dict(diagnostic, status="PUBLIC_BOOK_DEPTH_UNAVAILABLE")
    for side, levels in (("bid", bids), ("ask", asks)):
        for price, qty in levels:
            if (type(price) is not Decimal or type(qty) is not Decimal
                    or not price.is_finite() or not qty.is_finite()
                    or price <= ZERO or qty <= ZERO
                    or price.adjusted() > 24 or qty.adjusted() > 24):
                return dict(diagnostic, status="INVALID_PUBLIC_BOOK_LEVEL")
        if any((a[0] <= b[0] if side == "bid" else a[0] >= b[0])
               for a, b in zip(levels, levels[1:])):
            return dict(diagnostic, status="UNORDERED_PUBLIC_BOOK")
    if bids[0][0] >= asks[0][0]:
        return dict(diagnostic, status="CROSSED_PUBLIC_BOOK")
    # REST ticker and REST depth are NOT atomic. A large discrepancy blocks
    # this indicative arithmetic rather than combining two market epochs.
    if (abs(bids[0][0]-bid)*Decimal(10000)/bid > Decimal("30")
            or abs(asks[0][0]-ask)*Decimal(10000)/ask > Decimal("30")):
        return dict(diagnostic, status="TICKER_BOOK_QUOTE_MISMATCH")
    diagnostic["public_book_ticker_cross_request_coherent"] = True
    remaining_idr = illustrative_idr
    acquired = ZERO
    for price, qty in asks:
        spent = min(remaining_idr, qty*price)
        acquired += spent / price
        remaining_idr -= spent
        if remaining_idr == ZERO:
            break
    if remaining_idr > ZERO:
        return dict(diagnostic, status="VISIBLE_ASK_DEPTH_INSUFFICIENT")
    remaining_qty = acquired
    proceeds = ZERO
    for price, qty in bids:
        sold = min(remaining_qty, qty)
        proceeds += price*sold
        remaining_qty -= sold
        if remaining_qty == ZERO:
            break
    if remaining_qty > ZERO:
        return dict(diagnostic, status="VISIBLE_BID_DEPTH_INSUFFICIENT")
    gross = proceeds - illustrative_idr
    diagnostic.update({
        "status": "VISIBLE_BOOK_WALK_ILLUSTRATIVE_ONLY",
        "illustrative_base_qty_from_asks": str(acquired),
        "illustrative_immediate_bid_proceeds_idr": str(proceeds),
        "book_walk_immediate_exit_before_fees_idr": str(gross.quantize(Decimal("0.01"))),
        "book_walk_immediate_exit_before_fees_bps": str(
            (gross / illustrative_idr * Decimal(10000)).quantize(Decimal("0.01"))
        ),
        "fee_and_slippage_scenarios": {},
    })
    # Verify public precision/minimum constraints against the SAME
    # previously observed /api/pairs response, no extra HTTP or order.
    diagnostic["public_pair_order_constraints"] = (
        assess_public_pair_order_constraints(
            obs, illustrative_idr=illustrative_idr,
            observed_book_acquired=acquired,
        )
    )
    # The observed book walk ALREADY embeds spread and depth impact. Do not
    # subtract the PR46 hypothetical SPREAD again. Fees and additional slip
    # below are strictly illustrative, NEVER account/Indodax fee attestation.
    for name, fee_bps, extra_slip_bps, _unused_spread in _M03_FRICTION_SCENARIOS:
        buy_extra = illustrative_idr * (fee_bps + extra_slip_bps)/Decimal(10000)
        sell_extra = proceeds * (fee_bps + extra_slip_bps)/Decimal(10000)
        pnl = gross - buy_extra - sell_extra
        diagnostic["fee_and_slippage_scenarios"][name] = {
            "buy_fee_bps_assumed": str(fee_bps),
            "sell_fee_bps_assumed": str(fee_bps),
            "additional_slippage_each_side_bps_assumed": str(extra_slip_bps),
            "spread_or_book_impact_already_included": True,
            "hypothetical_immediate_exit_after_assumed_cost_idr": str(
                pnl.quantize(Decimal("0.01"))
            ),
            "hypothetical_immediate_exit_after_assumed_cost_bps": str(
                (pnl / illustrative_idr * Decimal(10000)).quantize(Decimal("0.01"))
            ),
            "realized_or_actionable_net_roi": False,
        }
    # Published public PRO fee totals, NOT components. This is the default
    # pricing REFERENCE for the observed immediate ASK→BID book walk even
    # without a manually supplied account fee file. Actual account fee
    # remains unverified. Maker fees are reference only, NOT applied to
    # this marketable book-walk's execution assumptions.
    pct = _M03_IDR_PRO_PUBLISHED_ALL_IN_PERCENT
    published_buy_bps = pct["BUY_TAKER"] * Decimal(100)
    published_sell_bps = pct["SELL_TAKER"] * Decimal(100)
    diagnostic["published_idr_pro_all_in_fee_table"] = {
        "market": "IDR",
        "mode": "PRO",
        "source": "INDODAX_OFFICIAL_HELP_PUBLIC_TABLE_NOT_ACCOUNT_ATTESTED",
        "source_url": _M03_IDR_PRO_FEE_HELP_URL,
        "official_help_verified_date": "2026-11-02",
        "published_effective_since": "2026-10-01",
        "buy_maker_percent_reference_only": str(pct["BUY_MAKER"]),
        "buy_taker_percent_used": str(pct["BUY_TAKER"]),
        "sell_maker_percent_reference_only": str(pct["SELL_MAKER"]),
        "sell_taker_percent_used": str(pct["SELL_TAKER"]),
        "fee_is_total_all_in": True,
        "service_tax_cfx_already_included": True,
        "component_fee_addition_allowed": False,
        "maker_execution_not_implied": True,
        "account_specific_fee_verified": False,
        "schedule_can_change": True,
    }
    diagnostic["published_idr_pro_taker_fee_sensitivity"] = {
        "buy_total_all_in_bps": str(published_buy_bps),
        "sell_total_all_in_bps": str(published_sell_bps),
        "roundtrip_taker_fee_percent_sum_reference_only": str(
            pct["BUY_TAKER"] + pct["SELL_TAKER"]
        ),
        "spread_book_impact_already_in_observed_walk": True,
        "additional_spread_fee_deduction": False,
        "individual_fee_components_added": False,
        "account_specific_fee_verified": False,
        **_m03_all_in_total_fee_preview(
            published_buy_bps, published_sell_bps,
            illustrative_idr, proceeds, acquired,
        ),
    }
    diagnostic["selected_all_in_fee_basis"] = (
        "INDODAX_PRO_IDR_PUBLIC_TAKER_ALL_IN_TOTAL_UNVERIFIED_ACCOUNT"
    )
    if owner_fee_snapshot is not None:
        fees = owner_fee_snapshot
        if type(fees) is not OwnerEnteredAllInFeeSnapshot:
            diagnostic["owner_entered_fee_status"] = "INVALID_TYPE"
        elif fees.pair != obs.pair:
            diagnostic["owner_entered_fee_status"] = "PAIR_MISMATCH"
        elif (type(fees.profile_seen_epoch) is not int
              or fees.profile_seen_epoch > src.observed_epoch
              or src.observed_epoch - fees.profile_seen_epoch > 86400):
            diagnostic["owner_entered_fee_status"] = "STALE_OR_FUTURE"
        elif not (fees.market == "IDR" and fees.order_role == "TAKER"
                  and fees.source_kind ==
                  "OWNER_ENTERED_INDODAX_PROFILE_UNATTESTED"
                  and all(type(v) is Decimal and v.is_finite()
                          and ZERO <= v <= Decimal("500")
                          for v in (fees.buy_all_in_bps,
                                    fees.sell_all_in_bps))):
            diagnostic["owner_entered_fee_status"] = "INVALID_SCOPE_OR_RATE"
        else:
            diagnostic["owner_entered_fee_status"] = (
                "FRESH_OWNER_ENTERED_NOT_EXCHANGE_AUTHENTICATED"
            )
            diagnostic["selected_all_in_fee_basis"] = (
                "OWNER_ENTERED_IDR_TAKER_ALL_IN_TOTAL_UNVERIFIED_ACCOUNT"
            )
            diagnostic["owner_entered_fee_sensitivity"] = {
                "source_kind": fees.source_kind,
                "profile_seen_epoch": fees.profile_seen_epoch,
                "pair": fees.pair,
                "buy_all_in_bps_owner_entered": str(fees.buy_all_in_bps),
                "sell_all_in_bps_owner_entered": str(fees.sell_all_in_bps),
                "market": fees.market,
                "order_role": fees.order_role,
                "tax_cfx_components_not_added_twice": True,
                "spread_book_impact_already_in_observed_walk": True,
                **_m03_all_in_total_fee_preview(
                    fees.buy_all_in_bps, fees.sell_all_in_bps,
                    illustrative_idr, proceeds, acquired,
                ),
                "owner_entered_is_not_authenticated_account_fee": True,
                "execution_allowed": False,
            }
    # ORIGINAL M07-02 observation mainpath: crossing buy/sell book has
    # populated both the public per-pair constraints and selected fee source.
    # Assess the post-increment SELL minimum on a hypothetical quantity,
    # and ceil any future target to the same observed public price step.
    # Neither amount is proven owned or order-accepted by the exchange.
    pair_rules = diagnostic["public_pair_order_constraints"]
    source_buy = published_buy_bps
    source_sell = published_sell_bps
    if diagnostic["owner_entered_fee_sensitivity"] is not None:
        source_buy = owner_fee_snapshot.buy_all_in_bps
        source_sell = owner_fee_snapshot.sell_all_in_bps
    diagnostic["public_post_round_sell_research"] = (
        _m07_post_round_sell_research(
            obs, pair_rules=pair_rules, illustrative_idr=illustrative_idr,
            acquired_before_real_buy_fees=acquired,
            buy_all_in_bps=source_buy, sell_all_in_bps=source_sell,
        )
    )
    return diagnostic


def _m03_assumed_friction_holdout(
    training: list[tuple[int, str, Decimal]],
    holdout: list[tuple[int, str, Decimal]],
) -> dict:
    """Predefined, disjoint 3m outcome windows and train-only setup choice.

    The two existing M03-J labels are frozen before the split. The holdout
    NEVER selects a setup or changes its thresholds. A cost scenario is
    merely gross OHLC close-to-close minus explicit *hypothetical* friction;
    it does not prove market orders, achievable fills or net PnL.
    """
    def disjoint(rows: list[tuple[int, str, Decimal]]) -> list[tuple[int, str, Decimal]]:
        selected = []
        last_index = -4
        for index, label, gross_bps in rows:
            if index - last_index >= 3:
                selected.append((index, label, gross_bps))
                last_index = index
        return selected

    train = disjoint(training)
    test = disjoint(holdout)
    costs = {
        tag: {
            "taker_fee_each_side_bps_assumed": str(fee),
            "slippage_each_side_bps_assumed": str(slip),
            "spread_roundtrip_bps_assumed": str(spread),
            "total_friction_bps_assumed": str(2 * fee + 2 * slip + spread),
        }
        for tag, fee, slip, spread in _M03_FRICTION_SCENARIOS
    }
    base_cost = Decimal(costs["BASE_ASSUMED"]["total_friction_bps_assumed"])
    counts = {
        label: sum(name == label for _, name, _ in train)
        for label in _M03_SETUP_LABELS
    }
    training_means = {
        label: (
            sum((ret - base_cost for _, name, ret in train if name == label), ZERO)
            / counts[label] if counts[label] else None
        )
        for label in _M03_SETUP_LABELS
    }
    # Train-only, frozen before reading any holdout outcome.
    possible = [
        label for label in _M03_SETUP_LABELS
        if counts[label] >= _M03_TRAIN_MIN_NONOVERLAP_EVENTS
        and training_means[label] is not None
        and training_means[label] > ZERO
    ]
    setup = sorted(
        possible, key=lambda name: (-training_means[name], name)
    )[0] if possible else None
    selected_holdout = [ret for _, name, ret in test if name == setup]
    sensitivity = {}
    for tag, _, _, _ in _M03_FRICTION_SCENARIOS:
        cost = Decimal(costs[tag]["total_friction_bps_assumed"])
        values = [ret - cost for ret in selected_holdout]
        count = len(values)
        sensitivity[tag] = {
            "holdout_events": count,
            "mean_forward_3m_close_to_close_after_assumed_cost_bps": (
                str((sum(values, ZERO) / count).quantize(Decimal("0.01")))
                if count else None
            ),
            "events_above_assumed_cost": sum(v > ZERO for v in values),
            "events_at_or_above_2pct_after_assumed_cost": sum(
                v >= Decimal("200") for v in values
            ),
        }
    return {
        "stage": "M03_HOLDOUT_FRICTION_SCENARIO_ONLY",
        "status": (
            "TRAIN_ONLY_SETUP_SELECTED_HOLDOUT_DESCRIPTIVE"
            if setup is not None and selected_holdout
            else "INSUFFICIENT_TRAIN_OR_HOLDOUT_NO_EDGE_CLAIM"
        ),
        "setup_selected_on_training_only": setup,
        "setup_train_nonoverlapping_count": (
            counts[setup] if setup is not None else 0
        ),
        "setup_holdout_nonoverlapping_count": len(selected_holdout),
        "all_train_nonoverlapping_events": len(train),
        "all_holdout_nonoverlapping_events": len(test),
        "holdout_used_to_select_setup": False,
        "forward_outcomes_overlap_within_sample": False,
        "training_min_events_per_setup": _M03_TRAIN_MIN_NONOVERLAP_EVENTS,
        "scenario_costs": costs,
        "holdout_sensitivity": sensitivity,
        "assumptions_not_actual_exchange_fees_or_spread": True,
        "unobserved_execution_and_slippage_fills": True,
        "capital_for_10000_idr_net_profit_verified": False,
        "min_net_roi_percent_policy": "2",
        "min_net_profit_idr_policy": "10000",
        "max_active_main_pairs_policy": 2,
        "eligible_live_candidates": 0,
        "source_g1_verified": False,
        "out_of_sample_net_edge_validated": False,
        "execution_allowed": False,
    }


def evaluate_source_backed_candle_research(history: PublicCandleHistory) -> dict:
    """M03-K deterministic time-split/embargo evaluation of M03-J labels.

    The input is public closed OHLC, NOT authenticated complete trade history.
    No optimized thresholds, order fills, fee model, net ROI or live signal.
    Training outcomes must end before the holdout period begins.
    """
    if (not isinstance(history, PublicCandleHistory)
            or history.source_trust_verified is not False
            or history.trade_history_complete is not False
            or history.provenance != "INDODAX_PUBLIC_OHLC_V2_UNATTESTED"
            or not isinstance(history.pair, str)
            or not history.pair.endswith("_idr")):
        raise PublicDataError("INVALID_RESEARCH_SOURCE")
    candles = history.candles
    n = len(candles)
    if (n < 90 or n > 4320 or history.to_epoch - history.from_epoch != 60 * n
            or candles[0].start != history.from_epoch
            or candles[-1].start + 60 != history.to_epoch
            or history.observed_epoch < history.to_epoch
            or abs(history.observed_epoch - history.exchange_epoch) > 30
            or any(c.interval != 60 or c.close <= ZERO
                   for c in candles)
            or any(b.start - a.start != 60
                   for a, b in zip(candles, candles[1:]))):
        raise PublicDataError("INVALID_RESEARCH_CANDLE_WINDOW")
    # Canonical digest binds the report to the exact observed OHLC payload,
    # not to exchange-signed provenance or a complete public trade tape.
    serialized = "\n".join(
        ",".join(map(str, (c.start, c.open, c.high, c.low, c.close, c.volume)))
        for c in candles
    )
    source_digest = sha256(serialized.encode("utf-8")).hexdigest()
    holdout_start_index = n * 7 // 10
    train: list[tuple[str, Decimal, bool]] = []
    holdout: list[tuple[str, Decimal, bool]] = []
    calibration_train: list[tuple[int, str, Decimal]] = []
    calibration_holdout: list[tuple[int, str, Decimal]] = []
    purged = 0
    for i in range(_STRUCTURE_WINDOW - 1, n - 3):
        prior = candles[i-10:i]
        current = candles[i]
        try:
            label = classify_closed_market_structure(
                tuple(candles[i-_STRUCTURE_WINDOW+1:i+1])
            )["label"]
        except PublicDataError:
            # Never silently drop a low-volume/invalid row and present a
            # selected surviving subset as an unbiased holdout.
            raise PublicDataError("RESEARCH_STRUCTURE_WINDOW_INVALID") from None
        body = (abs(current.close - current.open) /
                (current.high - current.low)
                if current.high > current.low else ZERO)
        breakout_without_volume = (
            current.close > max(c.high for c in prior)
            and current.close > current.open
            and body >= _STRUCTURE_MIN_BODY_FRACTION
        )
        outcome = ((candles[i+3].close / current.close)-Decimal(1))*Decimal(10000)
        record = (label, outcome, breakout_without_volume)
        if i + 3 < holdout_start_index:
            train.append(record)
            if label in _M03_SETUP_LABELS:
                calibration_train.append((i, label, outcome))
        elif i >= holdout_start_index:
            holdout.append(record)
            if label in _M03_SETUP_LABELS:
                calibration_holdout.append((i, label, outcome))
        else:
            # Embargo outcomes straddling train/holdout cutoff.
            purged += 1

    def stats(rows: list[tuple[str, Decimal, bool]]) -> dict:
        def mean(vals: list[Decimal]) -> str | None:
            return str((sum(vals, ZERO) / len(vals)).quantize(Decimal("0.01"))) if vals else None
        surge = [ret for name, ret, _ in rows
                 if name == "SURGE_BREAKOUT_OBSERVED"]
        unfiltered_breakout = [ret for _, ret, raw in rows if raw]
        return {
            "decision_windows": len(rows),
            "all_windows_mean_forward_3m_gross_bps": mean([r for _, r, _ in rows]),
            "surge_breakout_events": len(surge),
            "surge_breakout_mean_forward_3m_gross_bps": mean(surge),
            "no_volume_filter_breakout_events": len(unfiltered_breakout),
            "no_volume_filter_breakout_mean_forward_3m_gross_bps": mean(unfiltered_breakout),
            "labels": {
                tag: {
                    "count": sum(name == tag for name, _, _ in rows),
                    "mean_forward_3m_gross_bps": mean(
                        [r for name, r, _ in rows if name == tag]
                    ),
                }
                for tag in (
                    "SURGE_BREAKOUT_OBSERVED",
                    "PULLBACK_REBOUND_OBSERVED",
                    "FALSE_BREAKOUT_RISK",
                    "REVERSAL_ATTEMPT_OBSERVED",
                )
            },
        }

    return {
        "stage": "M03K_PUBLIC_OHLC_TIME_SPLIT_RESEARCH",
        "status": "HOLDOUT_DESCRIPTIVE_ONLY_NOT_VALIDATED",
        "pair": history.pair,
        "source": history.provenance,
        "source_digest_sha256": source_digest,
        "from_epoch": history.from_epoch,
        "to_epoch": history.to_epoch,
        "observed_epoch": history.observed_epoch,
        "closed_1m_rows_visible": n,
        "archive_research_window_multiday_eligible": n >= 1440,
        "holdout_start_epoch": candles[holdout_start_index].start,
        "train": stats(train),
        "holdout": stats(holdout),
        "modeled_after_cost_holdout": _m03_assumed_friction_holdout(
            calibration_train, calibration_holdout,
        ),
        "purged_boundary_windows": purged,
        "ablation": "surge_breakout WITH vs WITHOUT provisional volume >=2x condition, price-only",
        "rules_fixed_prior_to_observation": True,
        "forward_outcome_only_used_for_reporting": True,
        "overlapping_3m_forward_windows_not_independent": True,
        "full_exchange_trade_tape_proven": False,
        "source_trust_verified": False,
        "g1_verified": False,
        "out_of_sample_validated": False,
        "net_roi_verified": False,
        "fee_slippage_fills_measured": False,
        "g2_candidate_authorized": False,
        "execution_allowed": False,
    }



def assess_orderbook_rebound(
    obs: PublicObservation,
    *, near_bps: Decimal = Decimal("200"),
    spread_guard_bps: Decimal = Decimal("80"),
    early_momentum: dict | None = None,
) -> dict:
    """M03-N: source-bound *visible resting order* vs actual trade diagnostic.

    The depth REST endpoint is a single snapshot, so large BUY/SELL walls
    could be canceled, moved or spoofed. NEVER convert them to executed flow
    or to a probabilistic signal. Price drawdown uses only closed 1m candles.
    """
    bid = obs.snapshot.best_bid_idr
    ask = obs.snapshot.best_ask_idr
    if (bid <= ZERO or ask < bid or
            obs.snapshot.source.pair != obs.pair or
            obs.snapshot.source.observed_epoch < obs.snapshot.source.closed_1m_epoch or
            obs.snapshot.source.observed_epoch - obs.snapshot.source.closed_1m_epoch > 120):
        raise PublicDataError("STALE_OR_INVALID_BOOK_SOURCE")
    book = {
        "status": "BOOK_LEVELS_NOT_OBSERVED",
        "pair": obs.pair,
        "observed_epoch": obs.snapshot.source.observed_epoch,
        "closed_1m_epoch": obs.snapshot.source.closed_1m_epoch,
        "source": "INDODAX_PUBLIC_DEPTH_SNAPSHOT_UNATTESTED",
        "resting_orders_are_executed_buys": False,
        "full_orderbook_proven": False,
        "wall_persistence_proven": False,
        "spoofing_excluded": False,
        "visible_trade_flow_complete": False,
        "g1_verified": False,
        "trade_signal": False,
        "execution_allowed": False,
    }
    if not obs.bid_book_levels or not obs.ask_book_levels:
        return book
    if (not near_bps.is_finite() or near_bps <= ZERO or
            not spread_guard_bps.is_finite() or spread_guard_bps <= ZERO):
        raise PublicDataError("INVALID_BOOK_WALL_WINDOW")
    for side, levels in (("BID", obs.bid_book_levels),
                         ("ASK", obs.ask_book_levels)):
        for price, amount in levels:
            if (not price.is_finite() or not amount.is_finite()
                    or price <= ZERO or amount <= ZERO):
                raise PublicDataError("INVALID_BOOK_WALL_LEVEL")
        prices = [x[0] for x in levels]
        if any(
            (a <= b if side == "BID" else a >= b)
            for a, b in zip(prices,prices[1:])
        ):
            raise PublicDataError("UNORDERED_BOOK_WALL_LEVELS")
    # A distinct ticker/depth snapshot can be inconsistent. Refuse even a
    # large wall when the book best level is far from quoted best bid/ask.
    best_bid, best_ask = obs.bid_book_levels[0][0], obs.ask_book_levels[0][0]
    if best_bid >= best_ask:
        raise PublicDataError("CROSSED_BOOK_WALL_DEPTH")
    price_source_mismatch = (
        abs(best_bid-bid)*Decimal(10000)/bid > Decimal("100")
        or abs(best_ask-ask)*Decimal(10000)/ask > Decimal("100")
    )
    spread = (ask-bid)*Decimal(10000)/ask
    # Comparable price-proximate IDR notionals. Sum nearby notional AND
    # report the strongest single wall, not a misleading number of orders.
    near_bid = [
        (p,p*q) for p,q in obs.bid_book_levels
        if (best_bid-p)*Decimal(10000)/best_bid <= near_bps
    ]
    near_ask = [
        (p,p*q) for p,q in obs.ask_book_levels
        if (p-best_ask)*Decimal(10000)/best_ask <= near_bps
    ]
    bid_total = sum((notional for _,notional in near_bid), ZERO)
    ask_total = sum((notional for _,notional in near_ask), ZERO)
    # first book level always lies in near range when input is genuine.
    if bid_total <= ZERO or ask_total <= ZERO:
        raise PublicDataError("EMPTY_NEAR_BOOK_WALL")
    bid_wall_price,bid_wall_idr = max(near_bid,key=lambda x:x[1])
    ask_wall_price,ask_wall_idr = max(near_ask,key=lambda x:x[1])
    ratio = bid_total/ask_total
    # 22 candles are already required by primary analysis. No future bars
    # are read; compare last CLOSED close to an earlier observed high/low.
    minute = _last_continuous(obs.candles_1m,60,22)
    if minute[-1].start+60 != obs.snapshot.source.closed_1m_epoch:
        raise PublicDataError("BOOK_WALL_CANDLE_SOURCE_MISMATCH")
    if any(not all(x.is_finite() for x in (c.open,c.high,c.low,c.close,c.volume))
           or c.low <= ZERO or c.high < max(c.open,c.close)
           or c.low > min(c.open,c.close) or c.volume < ZERO
           for c in minute):
        raise PublicDataError("INVALID_BOOK_REBOUND_CANDLE")
    past_peak = max(c.high for c in minute[:-1])
    observed_low = min(c.low for c in minute)
    selloff = (observed_low/past_peak-1)*HUNDRED
    recovery = (minute[-1].close/observed_low-1)*HUNDRED
    last_change = (minute[-1].close/minute[-2].close-1)*HUNDRED
    # Only reference 60m ticker returns if SAME-minute, uninterrupted 61
    # first-observed quotes exist. This is not a certified closed 60m candle.
    ticker_60m = None
    if isinstance(early_momentum, dict):
        epoch = early_momentum.get("minute_epoch")
        count = early_momentum.get("consecutive_minute_samples")
        reported = early_momentum.get("return_60m_pct")
        if (type(epoch) is int and epoch ==
                (obs.snapshot.source.observed_epoch//60)*60
                and type(count) is int and count >= 61
                and isinstance(reported, str)):
            try:
                candidate = Decimal(reported)
                if candidate.is_finite():
                    ticker_60m = candidate
            except (ArithmeticError, ValueError):
                pass
    severe_selloff = selloff <= Decimal("-7") or (
        ticker_60m is not None and ticker_60m <= Decimal("-7")
    )
    fiveminute = (obs.candle_5m.close/obs.candle_5m.open-1)*HUNDRED
    last_close_vs_quote_bps = (
        abs(minute[-1].close-bid)*Decimal(10000)/bid
    )
    if price_source_mismatch:
        state = "BOOK_TICKER_MISMATCH_NO_WATCH"
    elif spread > spread_guard_bps:
        state = "WIDE_SPREAD_WALL_UNACTIONABLE"
    elif last_close_vs_quote_bps > Decimal("300"):
        state = "BOOK_LAST_CLOSE_DISLOCATION_NO_WATCH"
    elif (severe_selloff and recovery >= Decimal("0.3")
          and last_change > ZERO and ratio >= Decimal("3")):
        state = "PANIC_REBOUND_WATCH_UNCONFIRMED"
    elif severe_selloff and last_change <= ZERO:
        state = "SECOND_LEG_DOWN_RISK"
    elif ratio >= Decimal("3"):
        state = "BID_WALL_WATCH_UNCONFIRMED"
    else:
        state = "NO_STRONG_NEAR_BID_WALL"
    # Two possible scenarios, not probabilities or guaranteed prediction.
    continuation = (
        state == "PANIC_REBOUND_WATCH_UNCONFIRMED"
        and fiveminute > ZERO
        and obs.visible_closed_1m_buy_idr > obs.visible_closed_1m_sell_idr
    )
    book.update({
        "status": state,
        "near_book_window_bps": str(near_bps),
        "bid_near_total_idr": str(bid_total),
        "ask_near_total_idr": str(ask_total),
        "near_bid_ask_notional_ratio": str(ratio),
        "largest_bid_wall_price_idr": str(bid_wall_price),
        "largest_bid_wall_notional_idr": str(bid_wall_idr),
        "largest_ask_wall_price_idr": str(ask_wall_price),
        "largest_ask_wall_notional_idr": str(ask_wall_idr),
        "spread_bps": str(spread),
        "last_close_vs_bid_gap_bps": str(last_close_vs_quote_bps),
        "observed_22m_peak_to_low_pct": str(selloff),
        "sampled_60m_ticker_return_pct":
            None if ticker_60m is None else str(ticker_60m),
        "crash_evidence_horizon": (
            "22M_CLOSED_OHLC" if selloff <= Decimal("-7") else
            "60M_CONTIGUOUS_TICKER_SAMPLES" if ticker_60m is not None and
                ticker_60m <= Decimal("-7") else "NO_SEVERE_SELLOFF"
        ),
        "recovery_from_visible_low_pct": str(recovery),
        "last_closed_1m_return_pct": str(last_change),
        "closed_5m_return_pct": str(fiveminute),
        "continuation_hypothesis":
            "NEEDS_NEXT_CLOSED_CANDLE_AND_FLOW_CONFIRMATION" if continuation
            else "NOT_CONFIRMED",
        "fade_hypothesis": "RETEST_OR_SECOND_LEG_DOWN_REMAINS_POSSIBLE",
        "visible_buy_idr_sampled": str(obs.visible_closed_1m_buy_idr),
        "visible_sell_idr_sampled": str(obs.visible_closed_1m_sell_idr),
        "visible_buy_count_sampled": obs.visible_closed_1m_buy_count,
        "visible_sell_count_sampled": obs.visible_closed_1m_sell_count,
        "thresholds_backtested": False,
    })
    return book


def analyze_observation(
    obs: PublicObservation, *, max_spread_bps: Decimal = Decimal("80"),
    early_momentum: dict | None = None,
) -> DeepCandidate:
    """One pair; reject incomplete/stale evidence rather than create synthetic candles."""
    pair = obs.pair
    book_diagnostic: dict | None = None
    try:
        if obs.snapshot.source.pair != pair or obs.snapshot.source.observed_epoch - obs.snapshot.source.closed_1m_epoch > 120:
            raise PublicDataError("STALE_OR_WRONG_SOURCE")
        minute = _last_continuous(obs.candles_1m, 60, 22)
        book_diagnostic = assess_orderbook_rebound(
            obs, early_momentum=early_momentum
        )
        fifteen = _last_continuous(obs.candles_15m, 900, 2)
        if minute[-1].start + 60 != obs.snapshot.source.closed_1m_epoch:
            raise PublicDataError("CANDLE_RECEIPT_MISMATCH")
        if minute[-1].volume <= ZERO:
            raise PublicDataError("NO_LAST_1M_VOLUME")
        if obs.visible_closed_1m_trade_count <= 0:
            raise PublicDataError("NO_RECENT_TRADE_SAMPLE")
        if obs.bid_depth_top5_idr <= ZERO or obs.ask_depth_top5_idr <= ZERO:
            raise PublicDataError("NO_BOOK_LIQUIDITY")
        closes = tuple(c.close for c in minute)
        ema8, ema21, rsi = _ema(closes, 8), _ema(closes, 21), _rsi14(closes)
        avg_volume = sum((c.volume for c in minute[-11:-1]), ZERO) / Decimal("10")
        if avg_volume <= ZERO:
            raise PublicDataError("NO_BASELINE_VOLUME")
        volume_ratio = minute[-1].volume / avg_volume
        bid = obs.snapshot.best_bid_idr
        ask = obs.snapshot.best_ask_idr
        spread_bps = HUNDRED * HUNDRED * (ask - bid) / ask
        if spread_bps > max_spread_bps:
            raise PublicDataError("SCALPING_SPREAD_TOO_WIDE")
        imbalance = ((obs.bid_depth_top5_idr - obs.ask_depth_top5_idr)
                     / (obs.bid_depth_top5_idr + obs.ask_depth_top5_idr))
        buy, sell = obs.visible_closed_1m_buy_idr, obs.visible_closed_1m_sell_idr
        # Retain the exact approved score and veto logic. These are labels
        # for observational review, not a new executable ranking or Risk AI.
        structure = classify_closed_market_structure(tuple(minute))
        try:
            replay = replay_closed_market_structure(obs.candles_1m)
        except PublicDataError:
            replay = {
                "status": "REJECT_GAPPED_OR_INVALID_REPLAY_HISTORY",
                "sample_count": 0,
                "events": [],
                "g1_verified": False,
                "price_only_not_net_roi": True,
                "out_of_sample_validated": False,
            }
        # Public trade flow is a VISIBLE SAMPLE, never complete trade history.
        # Conservative review when volume explosions are combined with extreme
        # RSI or visible selling. These are anomaly flags, not proof of fraud.
        flags: list[str] = []
        if volume_ratio >= EXTREME_VOLUME_RATIO:
            if rsi >= EXTREME_RSI:
                flags.append("EXTREME_VOLUME_WITH_OVERBOUGHT_RSI")
            if sell > ZERO and sell >= VISIBLE_SELL_DOMINANCE_MULTIPLE * buy:
                flags.append("EXTREME_VOLUME_WITH_VISIBLE_SELL_DOMINANCE")
        if flags:
            return DeepCandidate(
                pair, None, "REJECT_MOMENTUM_ANOMALY",
                "EXTREME_PUBLIC_MOMENTUM_REVIEW_REQUIRED",
                obs.snapshot.source.closed_1m_epoch, rsi, ema8, ema21,
                volume_ratio, spread_bps, buy, sell, imbalance, tuple(flags),
                market_structure_diagnostic=structure, structure_replay=replay,
                visible_buy_trade_count_sampled=obs.visible_closed_1m_buy_count,
                visible_sell_trade_count_sampled=obs.visible_closed_1m_sell_count,
                book_wall_diagnostic=book_diagnostic,
            )

        # Deterministic indicators, NOT a trading probability or expected ROI.
        score = sum((
            20 if ema8 > ema21 else 0,
            10 if Decimal("48") <= rsi <= Decimal("72") else 0,
            10 if obs.candle_5m.close > obs.candle_5m.open else 0,
            10 if fifteen[-1].close > fifteen[-2].close else 0,
            15 if volume_ratio >= Decimal("1.3") else 0,
            10 if spread_bps <= Decimal("30") else 0,
            10 if imbalance >= Decimal("0") else 0,
            10 if buy > sell else 0,
            5 if minute[-1].close > minute[-1].open else 0,
        ))
        return DeepCandidate(pair, score, "PUBLIC_EVIDENCE_UNATTESTED", None,
                             obs.snapshot.source.closed_1m_epoch, rsi, ema8, ema21,
                             volume_ratio, spread_bps, buy, sell, imbalance,
                             market_structure_diagnostic=structure,
                             structure_replay=replay,
                             visible_buy_trade_count_sampled=obs.visible_closed_1m_buy_count,
                             visible_sell_trade_count_sampled=obs.visible_closed_1m_sell_count,
                             book_wall_diagnostic=book_diagnostic)
    except PublicDataError as exc:
        return DeepCandidate(pair, None, "REJECT_INCOMPLETE_EVIDENCE", str(exc),
                             book_wall_diagnostic=book_diagnostic)


@dataclass(frozen=True)
class DeepScan:
    screened_idr_pairs: int
    preliminary_shortlist: int
    analyzed: tuple[DeepCandidate, ...]
    budget_limit: int
    scope: str = "M03B_BOUNDED_PUBLIC_PILOT_NO_TRADE"
    same_window: dict | None = None

    def summary(self) -> dict:
        ranked = sorted(self.analyzed, key=lambda x: (
            x.score is None, -(x.score if x.score is not None else -1), x.pair))
        return {
            "all_idr_pairs": self.screened_idr_pairs,
            "shortlist_count": self.preliminary_shortlist,
            "deep_examined_count": len(self.analyzed),
            "deep_budget_limit": self.budget_limit,
            "ranked_diagnostics": [x.summary() for x in ranked],
            "ranking_scope": self.scope,
            "g1_verified": False,
            "trade_authorized": False,
            "risk_ai_connected": False,
            **({"same_window": self.same_window} if self.same_window is not None else {}),
        }


def classify_candidate_entry_stage(candidate: DeepCandidate) -> dict:
    """Unify existing EARLY / closed PULLBACK / pump exhaustion observations.

    This is a same-candidate review label, not a score bonus, predictive edge,
    complete trade flow, order authorization or a new strategy/scanner.
    Only available observed evidence is used; no future candle or imputed BUY.
    """
    if not isinstance(candidate, DeepCandidate):
        raise ValueError("INVALID_M03_CANDIDATE_STAGE")
    structure = candidate.market_structure_diagnostic
    label = structure.get("label") if isinstance(structure, dict) else None
    early = candidate.early_momentum
    early_status = early.get("status") if isinstance(early, dict) else None
    if label == "FALSE_BREAKOUT_RISK":
        # An uncalibrated structure high-tick alone may label ordinary
        # candles as false breakouts. Treat it as a hard caution only if
        # contemporaneous sampled early momentum corroborates an attempted
        # surge. Otherwise keep an explicit review label, not a fake veto.
        stage = (
            "FALSE_BREAKOUT_CAUTION" if early_status in (
                "EARLY_WATCH_UNCALIBRATED", "EXHAUSTION_CAUTION"
            ) else "FALSE_BREAKOUT_REVIEW_UNCONFIRMED"
        )
    elif (early_status == "EXHAUSTION_CAUTION"
          or candidate.rsi14 is not None
          and candidate.rsi14 >= Decimal("75")):
        stage = "EXHAUSTED_PUMP_CAUTION"
    elif label == "PULLBACK_REBOUND_OBSERVED":
        stage = "PULLBACK_REBOUND_WATCH"
    elif label == "SURGE_BREAKOUT_OBSERVED":
        stage = "BREAKOUT_CONTINUATION_WATCH"
    elif early_status == "EARLY_WATCH_UNCALIBRATED":
        stage = "EARLY_ACCELERATION_WATCH"
    elif label == "REVERSAL_ATTEMPT_OBSERVED":
        stage = "REVERSAL_ATTEMPT_REVIEW"
    else:
        stage = "NO_CONFIRMED_ENTRY_SETUP"
    return {
        "stage": stage,
        "structure_label": label,
        "early_momentum_status": early_status,
        "uses_closed_1m_structure": isinstance(structure, dict),
        "ticker_momentum_is_not_closed_candle": True,
        "entry_timing_confirmed": False,
        "calibrated": False,
        "risk_ai_evaluated": False,
        "g1_verified": False,
        "trade_authorized": False,
    }


def _quality_review(candidate: DeepCandidate, btc_regime: str) -> dict:
    """M03-F observational caution labels, NOT risk AI/veto or BUY qualification.

    Thresholds are conservative review triggers only. They are uncalibrated
    against historical PnL and MUST NOT serve as an executable trading rule.
    """
    flags: list[str] = []
    if btc_regime == "BTC_DOWNTREND_DIAGNOSTIC":
        flags.append("BTC_DOWNTREND_CAUTION")
    if candidate.score is None:
        flags.append("NO_VALID_PUBLIC_SCORE")
    if candidate.rsi14 is None:
        flags.append("MISSING_RSI")
    elif candidate.rsi14 >= Decimal("75"):
        flags.append("OVERBOUGHT_RSI_REVIEW")
    elif candidate.rsi14 <= Decimal("30"):
        flags.append("OVERSOLD_RSI_REVIEW")
    if candidate.volume_ratio is None or candidate.volume_ratio < Decimal("1"):
        flags.append("BELOW_BASELINE_OR_MISSING_VOLUME")
    if candidate.book_imbalance is None or candidate.book_imbalance < Decimal("-0.5"):
        flags.append("ASK_DOMINANT_OR_MISSING_BOOK")
    if candidate.visible_sell_idr > candidate.visible_buy_idr:
        flags.append("VISIBLE_SELL_SAMPLE_DOMINANT")
    timing = classify_candidate_entry_stage(candidate)
    # High indicator score never overrides an actually observed fakeout
    # or exhausted momentum. No threshold change, no trade authority.
    if timing["stage"] == "FALSE_BREAKOUT_CAUTION":
        flags.append("CLOSED_CANDLE_FALSE_BREAKOUT_CAUTION")
    if timing["stage"] == "EXHAUSTED_PUMP_CAUTION":
        flags.append("MOMENTUM_EXHAUSTION_CAUTION")
    book = candidate.book_wall_diagnostic or {}
    wall_status = book.get("status")
    if wall_status in ("BOOK_TICKER_MISMATCH_NO_WATCH",
                       "BOOK_LAST_CLOSE_DISLOCATION_NO_WATCH"):
        flags.append("UNALIGNED_DEPTH_QUOTE_OR_CANDLE")
    elif wall_status == "SECOND_LEG_DOWN_RISK":
        flags.append("SECOND_LEG_DOWN_RISK")
    elif wall_status == "WIDE_SPREAD_WALL_UNACTIONABLE":
        flags.append("WIDE_SPREAD_ORDERBOOK_WALL")
    elif wall_status == "PANIC_REBOUND_WATCH_UNCONFIRMED":
        flags.append("PANIC_REBOUND_NOT_CONFIRMED")
    return {
        "pair": candidate.pair,
        "score": candidate.score,
        "review_flags": flags,
        "clear_of_provisional_cautions": not flags,
        # Maintenance may be a wallet restriction; never claim tradability
        # merely because public OHLC and books can be observed.
        "maintenance_flag_requires_independent_order_preflight": (
            candidate.public_maintenance_flag
        ),
        "scalp_setup_stage": timing,
        "risk_ai_evaluated": False,
        "book_wall_status": wall_status,
        "wall_persistence_proven": False,
        "g2_candidate_authorized": False,
        "trade_authorized": False,
    }


def _same_window_gate(
    screening: ScannerResult,
    candidates: tuple[DeepCandidate, ...],
    observations: dict[str, PublicObservation],
    btc: PublicObservation | None,
) -> dict:
    """Compare only observations from a single closed 1m epoch with fresh BTC.

    A synchronized diagnostic is NOT a G1 receipt, AI candidate or permission.
    No scores are carried from earlier scan batches.
    """
    base = {
        "status": "REJECT_NO_SYNCHRONIZED_EVIDENCE",
        "closed_1m_epoch": None,
        "btc_regime": None,
        "ranked_same_window_diagnostics": [],
        "source_trust_verified": False,
        "trade_history_complete": False,
        "g2_candidate_authorized": False,
        "live_trading_authorized": False,
        "cross_cycle_ranking_allowed": False,
        "btc_context_requested": True,
        "provisional_quality_reviews": [],
        "provisional_watchlist_preview": [],
        "watchlist_is_trade_signal": False,
        "provisional_thresholds_backtested": False,
    }
    if btc is None:
        return dict(base, status="REJECT_BTC_SOURCE_UNAVAILABLE")
    btc_diagnostic = analyze_observation(btc)
    if btc_diagnostic.state == "REJECT_MOMENTUM_ANOMALY":
        return dict(base, status="REJECT_BTC_ANOMALY_REVIEW_REQUIRED")
    if btc_diagnostic.score is None:
        return dict(base, status="REJECT_BTC_CONTEXT_INCOMPLETE")
    if len(candidates) == 0 or any(
        c.score is None and c.state != "REJECT_MOMENTUM_ANOMALY"
        for c in candidates
    ):
        return dict(base, status="REJECT_CANDIDATE_EVIDENCE_INCOMPLETE")
    if any(c.state == "REJECT_MOMENTUM_ANOMALY" for c in candidates):
        return dict(base, status="REJECT_CANDIDATE_ANOMALY_REVIEW_REQUIRED")
    btc_epoch = btc.snapshot.source.closed_1m_epoch
    all_observations = [btc]
    for candidate in candidates:
        o = observations.get(candidate.pair)
        if o is None:
            return dict(base, status="REJECT_MISSING_PAIR_OBSERVATION")
        all_observations.append(o)
    # Guard against observation spanning a new closed minute or stale ranking
    # after the initial ticker_all sweep. Never interpret distinct 1m closes
    # as one synchronous market picture.
    epochs = {o.snapshot.source.closed_1m_epoch for o in all_observations}
    times = [o.snapshot.source.observed_epoch for o in all_observations]
    timeframe_contexts = {(o.candle_5m.start, o.candle_15m.start)
                          for o in all_observations}
    if (len(epochs) != 1 or next(iter(epochs)) != btc_epoch
            or len(timeframe_contexts) != 1
            or max(times) - min(times) > 45
            or abs(max(times) - screening.observed_epoch) > 60
            or any(o.snapshot.source.pair != o.pair for o in all_observations)):
        return dict(base, status="REJECT_CANDLE_OR_SNAPSHOT_MISALIGNMENT")
    # BTC is a context signal, not permission. Use closed 15m prices and
    # EMA direction together; neither a public quote nor indicator is verified.
    btc_latest = btc.candles_15m[-1]
    btc_prev = btc.candles_15m[-2]
    if btc_diagnostic.ema8 > btc_diagnostic.ema21 and btc_latest.close > btc_prev.close:
        regime = "BTC_UPTREND_DIAGNOSTIC"
    elif btc_diagnostic.ema8 < btc_diagnostic.ema21 and btc_latest.close < btc_prev.close:
        regime = "BTC_DOWNTREND_DIAGNOSTIC"
    else:
        regime = "BTC_MIXED_DIAGNOSTIC"
    ranked = sorted(candidates, key=lambda c: (-int(c.score), c.pair))
    reviews = [_quality_review(c, regime) for c in ranked]
    # This is a PUBLIC-ONLY PREVIEW of current-minute candidates, not a
    # qualified G1 receipt. Prior scan batch scores are never included.
    # Once baseline quality is clear, prefer real observed EARLY/PULLBACK/
    # breakout setups over a higher scalar score with no observed setup.
    # Same-minute diagnostics retain their original score order for audit.
    # These priorities are UNCALIBRATED and never authorize a provider call.
    stage_rank = {
        "PULLBACK_REBOUND_WATCH": 0,
        "EARLY_ACCELERATION_WATCH": 1,
        "BREAKOUT_CONTINUATION_WATCH": 2,
        "NO_CONFIRMED_ENTRY_SETUP": 3,
        "REVERSAL_ATTEMPT_REVIEW": 4,
    }
    clean = [item for item in reviews if item["clear_of_provisional_cautions"]]
    provisional_watchlist = [
        item["pair"] for item in sorted(
            clean,
            key=lambda item: (
                stage_rank.get(
                    (item.get("scalp_setup_stage") or {}).get("stage"), 5,
                ),
                -(item["score"] if type(item.get("score")) is int else -1),
                item["pair"],
            ),
        )[:2]
    ]
    return dict(
        base,
        status="ALIGNED_PUBLIC_UNATTESTED",
        closed_1m_epoch=btc_epoch,
        btc_regime=regime,
        ranked_same_window_diagnostics=[
            {"pair": c.pair, "score": c.score} for c in ranked
        ],
        provisional_quality_reviews=reviews,
        provisional_watchlist_preview=provisional_watchlist,
        watchlist_is_trade_signal=False,
        provisional_thresholds_backtested=False,
    )


def deep_scan(market: IndodaxPublicMarket, screening: ScannerResult, *, limit: int = 2,
              offset: int = 0, require_btc: bool = False,
              on_candidate: Callable[[DeepCandidate, PublicObservation], DeepCandidate] | None = None) -> DeepScan:
    """Bound deep calls; never fetch 30 x 7 requests in a single minute."""
    if not 1 <= limit <= 2:
        raise ValueError("M03B pilot limits deep HTTP work to 1-2 pairs")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("INVALID_DEEP_WINDOW_OFFSET")
    if not screening.shortlist:
        raise PublicDataError("NO_PRELIMINARY_CANDIDATES")
    found: list[DeepCandidate] = []
    observations: dict[str, PublicObservation] = {}
    count = min(limit, len(screening.shortlist))
    for i in range(count):
        item = screening.shortlist[(offset + i) % len(screening.shortlist)]
        try:
            obs = market.observe(item.pair)
            if abs(obs.snapshot.source.observed_epoch - screening.observed_epoch) > 120:
                raise PublicDataError("OUT_OF_SYNC_SCREENING")
            observations[item.pair] = obs
            candidate = replace(
                analyze_observation(obs, early_momentum=item.early_momentum),
                early_momentum=item.early_momentum,
                surge_experience_context=screening.surge_experience,
                surge_pattern=item.surge_pattern,
            )
            # Only the SAME source-bound observation becomes wall evidence;
            # journal callback is injected by the original orchestrator.
            if on_candidate is not None:
                candidate = on_candidate(candidate, obs)
            found.append(candidate)
        except PublicDataError as exc:
            found.append(DeepCandidate(item.pair, None, "REJECT_SOURCE_ERROR", str(exc)))
    synchronized: dict | None = None
    if require_btc:
        btc = observations.get("btc_idr")
        if btc is None:
            # Explicit third public observation only when BTC is absent from
            # the selected two, bounded by the 12s scheduler, no re-use of
            # older or cached snapshots.
            try:
                btc = market.observe("btc_idr")
            except PublicDataError:
                btc = None
        synchronized = _same_window_gate(
            screening, tuple(found), observations, btc,
        )
    return DeepScan(
        screening.all_idr_pairs, len(screening.shortlist),
        tuple(found), limit, same_window=synchronized,
    )


def bulk_public_deep_scan(market: IndodaxPublicMarket, screening: ScannerResult,
                          *, limit: int = 20,
                          on_candidate: Callable[[DeepCandidate, PublicObservation], DeepCandidate] | None = None) -> dict:
    """M03-G: source-efficient SAME-CYCLE 20–40 pair diagnostic attempt.

    This is NOT a multi-minute rank aggregator. Per-pair 4 public GET requests
    are used with the scanner's 3 already counted shared public responses.
    The 180/min published limit is per IP; this local budget is not a global
    cross-process limiter. Abort if observations cannot stay timely.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 20 <= limit <= 40:
        raise PublicDataError("BULK_LIMIT_MUST_BE_20_TO_40")
    context = screening.scan_context
    if context is None:
        raise PublicDataError("BULK_REQUIRES_SHARED_PUBLIC_SCAN_CONTEXT")
    if len(screening.shortlist) < limit:
        raise PublicDataError("BULK_SHORTLIST_BELOW_REQUESTED_LIMIT")
    selected = tuple(x.pair for x in screening.shortlist[:limit])
    if len(set(selected)) != len(selected):
        raise PublicDataError("BULK_DUPLICATE_PAIRS")
    # Scanner: pairs + server_time + ticker_all => 3 HTTP GET.
    # Each observe(pair, scan_context) fetches 1m/15m OHLC, trades and depth
    # => 4 GET. Fetch BTC context once if outside selected. Headroom 12 GET
    # reserves capacity for noise, but OTHER PROCESSES on the IP aren't tracked.
    extra_btc = "btc_idr" not in selected
    expected_gets = 3 + 4 * len(selected) + (4 if extra_btc else 0)
    if expected_gets > 168:
        raise PublicDataError("PUBLIC_RATE_BUDGET_EXCEEDED")
    if abs(context.observed_epoch - screening.observed_epoch) > 0:
        raise PublicDataError("BULK_SCAN_CONTEXT_MISMATCH")

    observations: dict[str, PublicObservation] = {}
    candidates: list[DeepCandidate] = []
    attempted = 0
    window_expired = False
    for pair in selected:
        if int(market._clock()) - screening.observed_epoch > 30:
            window_expired = True
            break
        attempted += 1
        try:
            obs = market.observe(pair, scan_context=context)
            if (obs.snapshot.source.observed_epoch - screening.observed_epoch > 30
                    or int(market._clock()) - screening.observed_epoch > 30):
                window_expired = True
                break
        except PublicDataError as exc:
            candidates.append(DeepCandidate(
                pair, None, "REJECT_SOURCE_ERROR", str(exc)))
            continue
        observations[pair] = obs
        history = next(
            (x.early_momentum for x in screening.shortlist[:limit]
             if x.pair == pair), None,
        )
        candidate = replace(
            analyze_observation(obs, early_momentum=history),
            early_momentum=history,
            surge_experience_context=screening.surge_experience,
            surge_pattern=next(
                (x.surge_pattern for x in screening.shortlist[:limit]
                 if x.pair == pair), None,
            ),
            public_maintenance_flag=(pair in context.maintenance_pair_ids),
        )
        if on_candidate is not None:
            candidate = on_candidate(candidate, obs)
        candidates.append(candidate)
    completed = attempted == len(selected) and len(candidates) == len(selected)
    valid = completed and not window_expired and all(c.score is not None for c in candidates)
    # Population-wide acceptance remains STRICT: 1 invalid pair blocks it.
    # But the pairs that DID have valid evidence can be inspected as a
    # separate same-minute observational cohort, never as a G1 proposal.
    guard = {
        "status": "REJECT_INCOMPLETE_OR_EXPIRED_BULK_COVERAGE",
        "ranked_same_window_diagnostics": [],
        "provisional_watchlist_preview": [],
        "cross_cycle_ranking_allowed": False,
        "g2_candidate_authorized": False,
        "live_trading_authorized": False,
        "trade_history_complete": False,
        "source_trust_verified": False,
    }
    eligible = tuple(c for c in candidates if c.score is not None)
    cohort_gate = dict(guard, status="REJECT_NO_VALID_COHORT")
    btc: PublicObservation | None = None
    if completed and not window_expired and eligible:
        btc = observations.get("btc_idr")
        if btc is None and "btc_idr" not in selected:
            # Only one extra BTC request, covered by the preflight estimate.
            # Never re-fetch a failed BTC source in the selected batch.
            try:
                if int(market._clock()) - screening.observed_epoch > 30:
                    raise PublicDataError("EXPIRED_BTC_CONTEXT_WINDOW")
                btc = market.observe("btc_idr", scan_context=context)
            except PublicDataError:
                btc = None
        # Existing gate checks exact 1m/5m/15m close, BTC, times and evidence.
        cohort_gate = _same_window_gate(screening, eligible, observations, btc)
        # No post-BTC overrun may be accepted as a synchronized cohort.
        if int(market._clock()) - screening.observed_epoch > 30:
            cohort_gate = dict(guard, status="REJECT_POST_CONTEXT_WINDOW_EXPIRED")
    elif not completed or window_expired:
        cohort_gate = dict(guard, status="REJECT_TRUNCATED_BULK_COHORT")
    if valid:
        guard = cohort_gate
    # Incomplete population may still have a VALID SUBSET DIAGNOSTIC, but
    # never upgrades population acceptance or execution/AI authorization.
    eligible_cohort = {
        **cohort_gate,
        "eligible_pair_count": len(eligible),
        "eligible_pair_ids": [c.pair for c in eligible],
        "population_pair_count": len(selected),
        "population_coverage_complete": valid,
        "partial_population": len(eligible) != len(selected),
        "cohort_is_trade_signal": False,
        "g1_verified": False,
        "g2_candidate_authorized": False,
        "trade_authorized": False,
        "execution_allowed": False,
        "cross_cycle_ranking_allowed": False,
    }

    diagnostic = sorted(candidates, key=lambda x: (
        x.score is None, -(x.score if x.score is not None else -1), x.pair
    ))
    # Report EVERY rejected pair; top-10 scores alone conceal failures.
    # No new network requests and no change in approval authority.
    rejected_pairs = [
        {"pair": c.pair, "state": c.state, "reason": c.rejection_reason}
        for c in candidates if c.score is None
    ]
    reason_counts: dict[str, int] = {}
    for item in rejected_pairs:
        key = item["reason"] or item["state"]
        reason_counts[key] = reason_counts.get(key, 0) + 1
    not_attempted = list(selected[attempted:])
    # "attempted" includes the pair that may have crossed the time deadline;
    # that pair may be absent from candidates if its payload expired post-fetch.
    attempted_without_result = max(0, attempted - len(candidates))
    if not completed or window_expired:
        coverage_state = "TIME_EXPIRED_OR_TRUNCATED"
    elif rejected_pairs:
        coverage_state = "PARTIAL_INVALID_PAIR_EVIDENCE"
    else:
        coverage_state = "ALL_SELECTED_ANALYZED_UNATTESTED"
    actual_requests = getattr(market, "public_get_attempt_count", None)
    g1_source_admission = public_coverage_report(
        selected, observations, screening_epoch=screening.observed_epoch,
        complete_batch=completed, window_expired=window_expired,
    )
    # M04-N: a *real observed* public BTC REST closed 1m candle is useful
    # for matching the persistent PUBLIC WS journal even if most altcoin
    # candidates fail the strict eligible-cohort gate. No invented minute,
    # additional HTTP GET, trusted G1 issuer, or G2 promotion.
    btc_rest = observations.get("btc_idr") or btc
    btc_rest_minute = None
    btc_rest_visible = None
    if btc_rest is not None and btc_rest.pair == "btc_idr":
        src = btc_rest.snapshot.source
        # SourceReceipt.closed_1m_epoch is candle END, but WS sampling
        # needs its START. Only accept a 60s OHLC candle proving that link.
        closed_end = src.closed_1m_epoch
        candle_start = btc_rest.candle_1m.start
        if (src.pair == "btc_idr"
                and type(closed_end) is int and closed_end > 60
                and closed_end % 60 == 0
                and type(candle_start) is int and candle_start > 0
                and candle_start % 60 == 0
                and candle_start + 60 == closed_end
                and 0 <= src.observed_epoch - screening.observed_epoch <= 30):
            btc_rest_minute = candle_start
            # Values have already been parsed/validated by the PUBLIC REST
            # adapter. Its recent-trades endpoint is page-limited, so count
            # and BUY/SELL notional describe only VISIBLE closed-minute rows.
            buy_visible = btc_rest.visible_closed_1m_buy_idr
            sell_visible = btc_rest.visible_closed_1m_sell_idr
            btc_rest_visible = {
                "stage": "M04O_BTC_REST_CLOSED_MINUTE_VISIBLE_SAMPLE",
                "pair": "btc_idr",
                "closed_1m_start_epoch": candle_start,
                "closed_1m_end_epoch": closed_end,
                "visible_trade_rows": btc_rest.visible_closed_1m_trade_count,
                "visible_buy_idr": str(buy_visible),
                "visible_sell_idr": str(sell_visible),
                "visible_total_idr": str(buy_visible + sell_visible),
                "rest_recent_trade_window_exhaustive": False,
                "g1_receipt_issuance_allowed": False,
                "g2_provider_call_allowed": False,
                "execution_allowed": False,
            }
    return {
        "mode": "M03G_BULK_PUBLIC_SHARED_SOURCE_ONLY",
        "screening_observed_epoch": screening.observed_epoch,
        "btc_public_rest_closed_1m_epoch_diagnostic": btc_rest_minute,
        "btc_public_rest_visible_minute_sample": btc_rest_visible,
        "btc_rest_observation_not_source_authentication": True,
        "source_trust_verified": False,
        "trade_history_complete": False,
        "shortlist_size": len(screening.shortlist),
        "requested_pairs": list(selected),
        "attempted_count": attempted,
        "analyzed_count": len(candidates),
        "candidate_valid_count": sum(c.score is not None for c in candidates),
        "candidate_rejected_count": sum(c.score is None for c in candidates),
        "candidate_rejection_reasons": rejected_pairs,
        "candidate_rejection_reason_counts": dict(sorted(reason_counts.items())),
        "not_attempted_pairs": not_attempted,
        "attempted_without_result_count": attempted_without_result,
        "coverage_status": coverage_state,
        "g1_source_admission": g1_source_admission,
        "synchronized_pair_coverage_complete": valid and guard["status"] == "ALIGNED_PUBLIC_UNATTESTED",
        "eligible_cohort": eligible_cohort,
        "window_expired": window_expired,
        "estimated_requests_including_discovery": expected_gets,
        # None for independently supplied market test doubles.
        "public_get_attempt_count_this_client": actual_requests,
        "public_limit_per_minute": 180,
        "local_budget_headroom": 12,
        "other_process_requests_accounted_for": False,
        "shared_public_scan_context": True,
        "ranked_diagnostics_top10": [x.summary() for x in diagnostic[:10]],
        # The quality-first preview can be outside the scalar score top-10.
        # Preserve exact selected per-pair evidence for M04 MARKET digest
        # without a second REST scan or mixing closed candle windows.
        "provisional_market_evidence": [
            c.summary() for pair in
            eligible_cohort.get("provisional_watchlist_preview", [])
            for c in candidates if c.pair == pair and c.score is not None
        ],
        "same_window": guard,
        "full_universe_deep_verified": False,
        "g1_verified": False,
        "g2_candidate_authorized": False,
        "trade_authorized": False,
        "execution_allowed": False,
    }
