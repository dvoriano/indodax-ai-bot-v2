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
    if any(c.interval != interval or (b.start - a.start) != interval for a, b in zip(last, last[1:])):
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
        }


def deep_scan(market: IndodaxPublicMarket, screening: ScannerResult, *, limit: int = 2) -> DeepScan:
    """Bound deep calls; never fetch 30 x 7 requests in a single minute."""
    if not 1 <= limit <= 2:
        raise ValueError("M03B pilot limits deep HTTP work to 1-2 pairs")
    found: list[DeepCandidate] = []
    for item in screening.shortlist[:limit]:
        try:
            obs = market.observe(item.pair)
            if abs(obs.snapshot.source.observed_epoch - screening.observed_epoch) > 120:
                raise PublicDataError("OUT_OF_SYNC_SCREENING")
            found.append(analyze_observation(obs))
        except PublicDataError as exc:
            found.append(DeepCandidate(item.pair, None, "REJECT_SOURCE_ERROR", str(exc)))
    return DeepScan(screening.all_idr_pairs, len(screening.shortlist), tuple(found), limit)
