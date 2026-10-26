"""SINGLE app entrypoint: safe vertical slice; real G1/G2/Indodax deferred."""
from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .contracts import FinalDecision
from .journal import DecisionJournal


@dataclass(frozen=True)
class CycleResult:
    trace_id: str
    final_decision: FinalDecision
    reason: str
    execution_allowed: bool = False


class DailyScalpingOrchestrator:
    def __init__(self, settings: Settings, journal: DecisionJournal) -> None:
        settings.require_m01_safety()
        self.settings = settings
        self.journal = journal

    def run_cycle(self, trace_id: str) -> CycleResult:
        # No synthetic candles, mock AI authority, private API or phantom orders.
        # Even a configured AUTO policy cannot authorize M01 trade intents.
        reason = "G1_SOURCE_NOT_CONNECTED"
        self.journal.record_reject(trace_id, reason)
        return CycleResult(trace_id, FinalDecision.REJECT, reason)

    def health(self) -> dict[str, str | int | bool]:
        return {
            "release": "M01_FOUNDATION",
            "core_strategy": "DAILY_SPOT_SCALPING",
            "decision_mode": self.settings.decision_mode,
            "execution_mode": self.settings.execution_mode,
            "execution_allowed": False,
            "accepted_decision_records": self.journal.count(),
            "next_stage": "M02_PUBLIC_MARKET_SOURCE",
        }
