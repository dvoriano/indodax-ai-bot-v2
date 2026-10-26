"""SINGLE app entrypoint: safe vertical slice; real G1/G2/Indodax deferred."""
from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .contracts import FinalDecision
from .journal import DecisionJournal
from .public_market import IndodaxPublicMarket, PublicDataError
from .scanner import DailyPairScanner


@dataclass(frozen=True)
class CycleResult:
    trace_id: str
    final_decision: FinalDecision
    reason: str
    execution_allowed: bool = False
    market: dict[str, str | int | bool] | None = None
    screening: dict | None = None


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

    def run_public_cycle(self, trace_id: str, *, pair: str = "btc_idr",
                         market: IndodaxPublicMarket | None = None) -> CycleResult:
        """Real public-only observations; never issues executable trade intents."""
        client = market if market is not None else IndodaxPublicMarket()
        try:
            observation = client.observe(pair)
        except PublicDataError as exc:
            reason = f"PUBLIC_SOURCE_INVALID:{exc}"
            payload = None
        else:
            reason = "G1_SCANNER_NOT_CONNECTED" if observation.snapshot.source.verified else "G1_PROVENANCE_NOT_VERIFIED"
            payload = observation.summary()
        self.journal.record_reject(trace_id, reason)
        return CycleResult(trace_id, FinalDecision.REJECT, reason, False, payload)

    def run_scan_cycle(self, trace_id: str, *,
                       market: IndodaxPublicMarket | None = None) -> CycleResult:
        """Public scan into same mainpath; no G1 evidence or trading approval."""
        client = market if market is not None else IndodaxPublicMarket()
        try:
            ranking = DailyPairScanner(client).scan()
        except PublicDataError as exc:
            reason = f"PUBLIC_SCANNER_INVALID:{exc}"
            summary = None
        else:
            reason = "G1_DEEP_ANALYSIS_AND_RISK_NOT_CONNECTED"
            summary = ranking.summary()
        self.journal.record_reject(trace_id, reason)
        return CycleResult(trace_id, FinalDecision.REJECT, reason, False, None, summary)

    def health(self) -> dict[str, str | int | bool]:
        return {
            "release": "M03A_PUBLIC_SCREEN_CODED",
            "core_strategy": "DAILY_SPOT_SCALPING",
            "decision_mode": self.settings.decision_mode,
            "execution_mode": self.settings.execution_mode,
            "execution_allowed": False,
            "accepted_decision_records": self.journal.count(),
            "next_stage": "M03B_DEEP_SCALPING_EVIDENCE",
        }
