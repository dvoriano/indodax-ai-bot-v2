"""M03-A: deterministic PUBLIC-IDR discovery and preliminary scalping shortlist.

PUBLIC observations are not G1 provenance, entry signals, or order authorization.
Rate budget: one /api/pairs, one /api/server_time, one /api/ticker_all per scan.
Deep 1m/5m/15m + order-flow scoring remains M03-B.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from .public_market import IndodaxPublicMarket, PublicDataError, PublicScanContext, _number, _seconds

# MAIN wallet must never use FLOKI/PEPE reserved-strategy buckets; stablecoins
# are settlement/quote instruments, not intended MAIN daily momentum scalps.
MAIN_EXCLUDED_PAIRS = frozenset({"floki_idr", "pepe_idr", "usdt_idr", "usdc_idr"})


@dataclass(frozen=True)
class ScreenedPair:
    pair: str
    volume_idr_24h: Decimal
    bid_idr: Decimal
    ask_idr: Decimal
    spread_bps: Decimal
    day_range_percent: Decimal
    ticker_epoch: int
    last_idr: Decimal | None = None
    early_momentum: dict | None = None
    # Historical *descriptive* similarity, never a predictive BUY score.
    surge_pattern: dict | None = None

    def summary(self) -> dict[str, str | int]:
        return {
            "pair": self.pair,
            "volume_idr_24h": str(self.volume_idr_24h),
            "spread_bps": str(self.spread_bps.quantize(Decimal("0.01"))),
            "day_range_percent": str(self.day_range_percent.quantize(Decimal("0.01"))),
            "ticker_epoch": self.ticker_epoch,
            "early_momentum": self.early_momentum,
            "surge_pattern": self.surge_pattern,
        }


@dataclass(frozen=True)
class ScannerResult:
    observed_epoch: int
    all_idr_pairs: int
    eligible_pairs: int
    excluded_pairs: int
    shortlist: tuple[ScreenedPair, ...]
    watchlist: tuple[ScreenedPair, ...]
    provenance: str = "INDODAX_PUBLIC_TICKER_ALL_UNATTESTED"
    g1_verified: bool = False
    scan_context: PublicScanContext | None = None
    universe: tuple[ScreenedPair, ...] = ()
    momentum_status: str = "NOT_RECORDED"
    surge_experience: dict | None = None

    def summary(self) -> dict:
        return {
            "observed_epoch": self.observed_epoch,
            "all_idr_pairs": self.all_idr_pairs,
            "eligible_pairs": self.eligible_pairs,
            "excluded_pairs": self.excluded_pairs,
            "shortlist_count": len(self.shortlist),
            "shortlist_top10": [x.summary() for x in self.shortlist[:10]],
            "watchlist_max2": [x.pair for x in self.watchlist],
            "source": self.provenance,
            "g1_issuer_verified": self.g1_verified,
            "trade_authorized": False,
            "ranking_basis": (
                "first-minute ticker 1m/5m acceleration WATCH priority, "
                "then 5m return and 24h IDR turnover; observational only"
                if self.momentum_status == "PUBLIC_MINUTE_SAMPLES_UNATTESTED"
                else "24h IDR turnover desc; bid-ask spread asc; 24h range desc; pair asc"
            ),
            "ranking_scope": "PRELIMINARY_PUBLIC_SCREEN_NOT_ENTRY_SIGNAL",
            "main_excluded_pairs": sorted(MAIN_EXCLUDED_PAIRS),
            "momentum_status": self.momentum_status,
            "momentum_provenance": "PUBLIC_TICKER_ALL_FIRST_MINUTE_SAMPLE_UNATTESTED",
            "momentum_buy_sell_flow_is_complete": False,
            "surge_experience": self.surge_experience,
            "early_watch_count": sum(
                x.early_momentum is not None and
                x.early_momentum.get("status") == "EARLY_WATCH_UNCALIBRATED"
                for x in self.universe
            ),
        }


class DailyPairScanner:
    """Initial all-IDR filter/rank only. Intentionally not executable trading analysis."""

    def __init__(self, market: IndodaxPublicMarket, *, shortlist_size: int = 30,
                 max_spread_bps: Decimal = Decimal("500")) -> None:
        if not 20 <= shortlist_size <= 40:
            raise ValueError("shortlist_size must be 20-40")
        if not max_spread_bps.is_finite() or max_spread_bps <= 0:
            raise ValueError("invalid spread guard")
        self.market = market
        self.shortlist_size = shortlist_size
        self.max_spread_bps = max_spread_bps

    def scan(self) -> ScannerResult:
        # Explicitly reuse existing official public adapter, no parallel HTTP client.
        pairs = self.market.idr_pairs()
        now = int(self.market._clock())
        server = self.market._get("/api/server_time")
        if not isinstance(server, dict):
            raise PublicDataError("INVALID_SERVER_TIME")
        server_epoch = _seconds(server.get("server_time"), "SERVER_TIME")
        if abs(now - server_epoch) > 30:
            raise PublicDataError("EXCHANGE_CLOCK_SKEW")
        all_tickers = self.market._get("/api/ticker_all")
        if not isinstance(all_tickers, dict) or not isinstance(all_tickers.get("tickers"), dict):
            raise PublicDataError("INVALID_TICKER_ALL")

        eligible: list[ScreenedPair] = []
        for pair in sorted(pairs):
            if pair in MAIN_EXCLUDED_PAIRS:
                continue
            ticker = all_tickers["tickers"].get(pair)
            if not isinstance(ticker, dict):
                continue
            try:
                ts = _seconds(ticker.get("server_time"), "TICKER_TIME")
                if ts > now + 3 or now - ts > 90:
                    continue
                bid = _number(ticker.get("buy"), "BID")
                ask = _number(ticker.get("sell"), "ASK")
                _number(ticker.get("last"), "LAST")
                volume = _number(ticker.get("vol_idr"), "VOLUME_IDR")
                high = _number(ticker.get("high"), "HIGH")
                low = _number(ticker.get("low"), "LOW")
                last = _number(ticker.get("last"), "LAST")
                if ask < bid or high < low or last < low or last > high:
                    continue
                spread = (ask - bid) * Decimal("10000") / ask
                if spread > self.max_spread_bps:
                    continue
                day_range = (high - low) * Decimal("100") / last
                eligible.append(ScreenedPair(pair, volume, bid, ask, spread, day_range, ts,
                                             last_idr=last))
            except PublicDataError:
                continue

        if not eligible:
            raise PublicDataError("NO_VALID_SCALPING_PAIRS")
        # This is a discovery order, NOT performance, buy signal or AI approval.
        eligible.sort(key=lambda x: (-x.volume_idr_24h, x.spread_bps, -x.day_range_percent, x.pair))
        shortlisted = tuple(eligible[: self.shortlist_size])
        return ScannerResult(
            observed_epoch=now,
            all_idr_pairs=len(pairs),
            eligible_pairs=len(eligible),
            excluded_pairs=len(pairs) - len(eligible),
            shortlist=shortlisted,
            watchlist=shortlisted[:2],
            universe=tuple(eligible),
            scan_context=PublicScanContext(
                observed_epoch=now, exchange_epoch=server_epoch,
                pairs=pairs, tickers=all_tickers["tickers"],
            ),
        )


def surge_pattern_signature(momentum: dict | None, spread_bps: Decimal) -> str | None:
    """Timestamp-locked coarse pattern shared by current and past cases.

    Derived solely from observed 5-minute return, change from previous 5m,
    and current spread. Not a classifier fitted to future outcomes.
    """
    if not isinstance(momentum, dict) or not spread_bps.is_finite() or spread_bps < 0:
        return None
    r5, delta = momentum.get("return_5m_pct"), momentum.get(
        "acceleration_current_5m_vs_prior_5m_pct_points"
    )
    if not isinstance(r5, str) or not isinstance(delta, str):
        return None
    try:
        value, acceleration = Decimal(r5), Decimal(delta)
    except (ArithmeticError, ValueError):
        return None
    if not value.is_finite() or not acceleration.is_finite():
        return None
    level = (
        "FALLING" if value <= 0 else
        "EARLY_UP_0_2" if value < 2 else
        "RISING_2_5" if value < 5 else
        "EXTENDED_UP_5_PLUS"
    )
    return ("__".join((level,
            "ACCELERATING" if acceleration > 0 else "SLOWING",
            "TIGHT_SPREAD" if spread_bps <= Decimal("80") else "WIDE_SPREAD")))


def rank_scanner_with_early_momentum(
    screening: ScannerResult, observations: dict[str, dict],
    *, casebook: dict | None = None,
) -> ScannerResult:
    """Reprioritize same-cycle DISCOVERY with history from existing journal.

    No lookahead, no deep request, no provider, no G1 or BUY authorization.
    Candidates with insufficient minute samples retain turnover ranking.
    Status is a *watch*, NEVER a trade signal or certified 1m candle.
    """
    if not isinstance(screening, ScannerResult) or not isinstance(observations, dict):
        raise ValueError("INVALID_EARLY_MOMENTUM_RANK_INPUT")
    pool = screening.universe
    if not pool:
        return replace(screening, momentum_status="NO_REAL_UNIVERSE_TO_RANK")
    patterns = (casebook.get("pattern_library") or {}) if isinstance(casebook, dict) else {}
    rows = []
    for item in pool:
        momentum = observations.get(item.pair)
        signature = surge_pattern_signature(momentum, item.spread_bps)
        sample = patterns.get(signature) if signature is not None else None
        similarity = {
            "signature": signature,
            # Forward 60m endpoint cohort is descriptive for a 30-60m
            # scalper. It is NEVER the peak, executable price or net ROI
            # since bot-owned BUY; ranking and entry authorization unchanged.
            "historical_matured_60m_cases": (
                sample.get("matured_60m", 0) if isinstance(sample, dict) else 0
            ),
            "historical_up_2pct_gross_60m_cases": (
                sample.get("up_2pct_60m", 0) if isinstance(sample, dict) else 0
            ),
            "historical_up_8pct_gross_60m_cases": (
                sample.get("up_8pct_60m", 0) if isinstance(sample, dict) else 0
            ),
            "historical_up_11pct_gross_60m_cases": (
                sample.get("up_11pct_60m", 0) if isinstance(sample, dict) else 0
            ),
            "historical_down_2pct_gross_60m_cases": (
                sample.get("down_2pct_60m", 0) if isinstance(sample, dict) else 0
            ),
            "historical_mean_return_60m_gross_pct": (
                sample.get("mean_return_60m_pct") if isinstance(sample, dict) else None
            ),
            "historical_60m_status": (
                sample.get("scalp_60m_descriptive_status", "NO_MATURED_60M_OUTCOMES")
                if isinstance(sample, dict) else "NO_MATCHED_60M_HISTORY"
            ),
            "historical_60m_endpoint_not_executable_exit": True,
            "historical_matured_24h_cases": (
                sample.get("matured_24h", 0) if isinstance(sample, dict) else 0
            ),
            "historical_up_20pct_24h_cases": (
                sample.get("up_20pct_24h", 0) if isinstance(sample, dict) else 0
            ),
            "historical_down_10pct_24h_cases": (
                sample.get("down_10pct_24h", 0) if isinstance(sample, dict) else 0
            ),
            "status": ("NO_MATCHED_MATURED_HISTORY" if not isinstance(sample, dict)
                      else sample.get("status", "DESCRIPTIVE_ONLY")),
            "predictive_model_validated": False,
            "future_outcome_known_now": False,
            "buy_authorized": False,
        }
        rows.append(replace(item, early_momentum=momentum,
                            surge_pattern=similarity))
    rows = tuple(rows)
    def sort_key(x: ScreenedPair) -> tuple:
        report = x.early_momentum or {}
        label = report.get("status")
        priority = {
            "EARLY_WATCH_UNCALIBRATED": 0,
            "CONFIRMATION_PENDING": 1,
            "OBSERVED_NO_ACCELERATION": 2,
            "EXHAUSTION_CAUTION": 3,
        }.get(label, 4)
        # Only compare momentum within non-stale, explicitly sampled cohorts.
        # No 24h return inference or bonus for raw unverified 1m flow.
        change = report.get("return_5m_pct")
        momentum = Decimal(change) if isinstance(change, str) else Decimal("-1000000")
        return (priority, -momentum, -x.volume_idr_24h, x.spread_bps, x.pair)
    ranked = tuple(sorted(rows, key=sort_key))
    size = len(screening.shortlist)
    return replace(
        screening,
        shortlist=ranked[:size], watchlist=ranked[:2],
        universe=ranked,
        momentum_status="PUBLIC_MINUTE_SAMPLES_UNATTESTED",
    )
