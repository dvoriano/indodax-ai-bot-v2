"""M06 original scoped private coverage: explicit FLOKI/PEPE provenance gaps.

No private exchange requests. Consumes ALREADY parsed M06 signed GET scope.
A pair/time window that returns zero SELL fills does not prove that zero
IDR from past FLOKI/PEPE sales is still earmarked for rebuy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from .m06_special_sale_window import M06SpecialSaleWindowObservation


@dataclass(frozen=True, repr=False)
class M06SpecialHistoryGapReport:
    first_observed_ms: int
    last_observed_ms: int
    checked_at_ms: int
    window_count: int
    scoped_pair_sell_gross_idr: tuple[tuple[str, str], ...]
    scoped_pair_sell_fill_count: tuple[tuple[str, int], ...]
    observed_pair_records_unresolved: bool
    special_sale_before_first_window_unverified: bool = True
    special_sale_after_last_window_unverified: bool = True
    special_initial_liability_unverified: bool = True
    rebuy_consumption_provenance_unverified: bool = True
    external_peer_liabilities_unverified: bool = True
    full_history_covered: bool = False
    balance_source_funds_verified: bool = False
    execution_allowed: bool = False

    def __repr__(self):
        return "<M06SpecialHistoryGapReport REDACTED; no account authority>"

    def safe_summary(self) -> dict:
        return {
            "status": "M06_SPECIAL_SALE_HISTORY_GAPS_UNRESOLVED",
            "observed_interval_utc": {
                "start": _utc(self.first_observed_ms),
                "end": _utc(self.last_observed_ms),
            },
            "checked_at_utc": _utc(self.checked_at_ms),
            "queried_consecutive_windows": self.window_count,
            "scoped_sell_gross_idr_observed": dict(self.scoped_pair_sell_gross_idr),
            "scoped_sell_fill_count_observed": dict(self.scoped_pair_sell_fill_count),
            "observed_pair_records_unresolved": self.observed_pair_records_unresolved,
            "special_sale_before_first_window_unverified": True,
            "special_sale_after_last_window_unverified": True,
            "special_initial_liability_unverified": True,
            "rebuy_consumption_provenance_unverified": True,
            "external_peer_liabilities_unverified": True,
            "full_history_covered": False,
            "balance_source_funds_verified": False,
            "safe_general_idr_from_this_report": None,
            "execution_allowed": False,
        }


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def review_m06_special_history_gaps(*,
        windows: tuple[tuple[int, int], ...],
        assets: Sequence[M06SpecialSaleWindowObservation],
        checked_at_ms: int,
) -> M06SpecialHistoryGapReport:
    """Reject partial scopes; show unknown prior/after/initial conditions.

    A validated contiguous finite window is *observed coverage only*.
    It is never an authenticated all-time source-of-funds liability report.
    All input amounts are from the original official View Only fill parsers.
    """
    if (type(windows) is not tuple or not windows
            or len(windows) > 32
            or type(checked_at_ms) is not int
            or not 1_000_000_000_000 <= checked_at_ms < 9_999_999_999_999):
        raise ValueError("M06_SPECIAL_GAP_SCOPE_INVALID")
    previous = None
    for a,b in windows:
        if (type(a) is not int or type(b) is not int
                or a < 1_000_000_000_000 or a > b
                or b >= checked_at_ms
                or (previous is not None and a != previous + 1)):
            raise ValueError("M06_SPECIAL_GAP_DISCONTIGUOUS_OR_FUTURE")
        previous = b
    if (not isinstance(assets, (tuple, list)) or len(assets) != 2
            or {x.asset for x in assets
                if type(x) is M06SpecialSaleWindowObservation}
            != {"FLOKI","PEPE"}
            or any(type(x) is not M06SpecialSaleWindowObservation
                   for x in assets)):
        raise ValueError("M06_SPECIAL_GAP_PAIR_COVERAGE_INVALID")
    data = sorted(assets, key=lambda x:x.asset)
    if any(x.scoped_sell_gross_idr < 0 or
           not x.scoped_sell_gross_idr.is_finite() or
           x.scoped_sell_fill_count < 0 or
           x.scoped_unlinked_fill_count < 0 or
           x.scoped_unknown_side_fill_count < 0
           for x in data):
        raise ValueError("M06_SPECIAL_GAP_VALUES_INVALID")
    return M06SpecialHistoryGapReport(
        first_observed_ms=windows[0][0],
        last_observed_ms=windows[-1][1],
        checked_at_ms=checked_at_ms,
        window_count=len(windows),
        scoped_pair_sell_gross_idr=tuple(
            (x.asset, format(x.scoped_sell_gross_idr,"f")) for x in data),
        scoped_pair_sell_fill_count=tuple(
            (x.asset,x.scoped_sell_fill_count) for x in data),
        observed_pair_records_unresolved=any(
            x.scope_has_unresolved_fills for x in data),
    )
