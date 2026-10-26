"""M03-B: source-bound *observational* scalp analysis. Never authorizes BUY.

Indicators use only fully closed candles returned by the public adapter.
Visible public trades may be truncated, so momentum and book signals are
research diagnostics until G1 provenance/coverage gates are implemented.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .public_market import Candle, IndodaxPublicMarket, PublicDataError, PublicObservation
from .scanner import ScannerResult

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

    def summary(self) -> dict:
        return {
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
            "book_imbalance": None if self.book_imbalance is None else str(self.book_imbalance.quantize(Decimal("0.001"))),
            "anomaly_flags": list(self.anomaly_flags),
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


def analyze_observation(obs: PublicObservation, *, max_spread_bps: Decimal = Decimal("80")) -> DeepCandidate:
    """One pair; reject incomplete/stale evidence rather than create synthetic candles."""
    pair = obs.pair
    try:
        if obs.snapshot.source.pair != pair or obs.snapshot.source.observed_epoch - obs.snapshot.source.closed_1m_epoch > 120:
            raise PublicDataError("STALE_OR_WRONG_SOURCE")
        minute = _last_continuous(obs.candles_1m, 60, 22)
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
                             volume_ratio, spread_bps, buy, sell, imbalance)
    except PublicDataError as exc:
        return DeepCandidate(pair, None, "REJECT_INCOMPLETE_EVIDENCE", str(exc))


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
    return dict(
        base,
        status="ALIGNED_PUBLIC_UNATTESTED",
        closed_1m_epoch=btc_epoch,
        btc_regime=regime,
        ranked_same_window_diagnostics=[
            {"pair": c.pair, "score": c.score} for c in ranked
        ],
    )


def deep_scan(market: IndodaxPublicMarket, screening: ScannerResult, *, limit: int = 2,
              offset: int = 0, require_btc: bool = False) -> DeepScan:
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
            found.append(analyze_observation(obs))
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
