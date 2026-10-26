"""M03-A: deterministic PUBLIC-IDR discovery and preliminary scalping shortlist.

PUBLIC observations are not G1 provenance, entry signals, or order authorization.
Rate budget: one /api/pairs, one /api/server_time, one /api/ticker_all per scan.
Deep 1m/5m/15m + order-flow scoring remains M03-B.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .public_market import IndodaxPublicMarket, PublicDataError, _number, _seconds

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

    def summary(self) -> dict[str, str | int]:
        return {
            "pair": self.pair,
            "volume_idr_24h": str(self.volume_idr_24h),
            "spread_bps": str(self.spread_bps.quantize(Decimal("0.01"))),
            "day_range_percent": str(self.day_range_percent.quantize(Decimal("0.01"))),
            "ticker_epoch": self.ticker_epoch,
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
            "ranking_basis": "24h IDR turnover desc; bid-ask spread asc; 24h range desc; pair asc",
            "ranking_scope": "PRELIMINARY_PUBLIC_SCREEN_NOT_ENTRY_SIGNAL",
            "main_excluded_pairs": sorted(MAIN_EXCLUDED_PAIRS),
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
                eligible.append(ScreenedPair(pair, volume, bid, ask, spread, day_range, ts))
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
        )
