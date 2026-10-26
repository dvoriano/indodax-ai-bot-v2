"""Hard, deterministic daily scalping constraints, independent of AI prompts."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ScalpingPolicy:
    min_net_profit_idr: Decimal = Decimal("10000")
    min_net_roi_percent: Decimal = Decimal("2")
    sell_targets_net_percent: tuple[Decimal, ...] = (
        Decimal("2"), Decimal("8"), Decimal("11")
    )
    max_active_main_pairs: int = 2
    manual_proposal_ttl_seconds: int = 90
    reporting_timezone: str = "Asia/Jakarta"

    def eligible_buy(self, estimated_net_profit_idr: Decimal, estimated_net_roi_percent: Decimal) -> bool:
        return (
            estimated_net_profit_idr.is_finite()
            and estimated_net_roi_percent.is_finite()
            and estimated_net_profit_idr >= self.min_net_profit_idr
            and estimated_net_roi_percent >= self.min_net_roi_percent
        )
