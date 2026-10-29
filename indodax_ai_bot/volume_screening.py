"""M03 public 24h-volume gate; never hides low-volume pairs from the radar.

No new market API requests and no inference of true minute volume from
the rolling 24h field. This is a pre-deep liquidity screen, NOT a BUY signal.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from .public_market import PublicDataError
from .scanner import ScannerResult

DEFAULT_DEEP_VOLUME_FLOOR_IDR = Decimal("100000000")
VOLUME_PROBE_THRESHOLDS_IDR = (
    Decimal("50000000"),
    Decimal("100000000"),
    Decimal("250000000"),
    Decimal("500000000"),
)


def apply_deep_volume_floor(
    screening: ScannerResult, *,
    minimum_idr: Decimal = DEFAULT_DEEP_VOLUME_FLOOR_IDR,
    max_deep_pairs: int = 20,
) -> ScannerResult:
    """Choose a current-scan, ranked deep subset without dropping the radar.

    The existing, already momentum-ranked full universe is retained as-is.
    No fallback to earlier batches, stale scores or below-floor candidates.
    This does not establish orderbook depth or minimum executable notional.
    """
    if not isinstance(screening, ScannerResult) or not screening.universe:
        raise PublicDataError("INVALID_VOLUME_SCREENING_UNIVERSE")
    if (not isinstance(minimum_idr, Decimal) or not minimum_idr.is_finite()
            or minimum_idr < 0):
        raise PublicDataError("INVALID_DEEP_VOLUME_FLOOR")
    if (type(max_deep_pairs) is not int or
            not 20 <= max_deep_pairs <= 40):
        raise PublicDataError("INVALID_DEEP_PAIR_LIMIT")
    if len({item.pair for item in screening.universe}) != len(screening.universe):
        raise PublicDataError("DUPLICATE_VOLUME_SCREENING_PAIR")

    chosen = tuple(
        pair for pair in screening.universe
        if pair.volume_idr_24h.is_finite()
        and pair.volume_idr_24h >= minimum_idr
    )[:max_deep_pairs]
    return replace(
        screening,
        shortlist=chosen,
        watchlist=chosen[:2],
    )


def public_volume_distribution(screening: ScannerResult) -> dict:
    """Snapshot counts from the same 3-GET public scanner observation."""
    if not isinstance(screening, ScannerResult) or not screening.universe:
        raise PublicDataError("INVALID_VOLUME_SCREENING_UNIVERSE")
    universe = screening.universe
    counts = {
        str(int(threshold)): sum(
            item.volume_idr_24h.is_finite()
            and item.volume_idr_24h >= threshold
            for item in universe
        )
        for threshold in VOLUME_PROBE_THRESHOLDS_IDR
    }
    above_floor = counts[str(int(DEFAULT_DEEP_VOLUME_FLOOR_IDR))]
    return {
        "stage": "M03_PUBLIC_VOLUME_DISTRIBUTION_READONLY",
        "observed_epoch": screening.observed_epoch,
        "all_idr_pairs": screening.all_idr_pairs,
        "eligible_initial_scanner_pairs": len(universe),
        "ineligible_or_excluded_initial_pairs": screening.all_idr_pairs - len(universe),
        "volume_basis": "PUBLIC_TICKER_ALL_ROLLING_24H_VOL_IDR",
        "minimum_deep_volume_idr": str(int(DEFAULT_DEEP_VOLUME_FLOOR_IDR)),
        "at_or_above_volume_idr_counts": counts,
        "below_100m_but_still_in_radar": len(universe) - above_floor,
        "at_least_20_deep_candidates_by_volume_only": above_floor >= 20,
        "at_least_40_deep_candidates_by_volume_only": above_floor >= 40,
        "all_initially_eligible_pairs_still_monitored": True,
        "volume_alone_does_not_validate_orderbook_or_trade": True,
        "public_source_unattested": True,
        "g1_verified": False,
        "trade_authorized": False,
        "execution_allowed": False,
    }
