"""SINGLE app entrypoint: safe vertical slice; real G1/G2/Indodax deferred."""
from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .contracts import FinalDecision
from .journal import DecisionJournal
from .public_market import IndodaxPublicMarket, PublicDataError
from .scanner import DailyPairScanner
from .deep_analysis import deep_scan, bulk_public_deep_scan
from .three_role_ai import ThreeRoleShadow
from .trade_stream import PublicTradeStreamJournal, probe_official_public_ws, StreamEvidenceError


@dataclass(frozen=True)
class CycleResult:
    trace_id: str
    final_decision: FinalDecision
    reason: str
    execution_allowed: bool = False
    market: dict[str, str | int | bool] | None = None
    screening: dict | None = None
    deep: dict | None = None
    rolling: dict | None = None
    bulk: dict | None = None
    ai_shadow: dict | None = None
    public_ws: dict | None = None


class DailyScalpingOrchestrator:
    def __init__(self, settings: Settings, journal: DecisionJournal, *,
                 ai_role_caller=None) -> None:
        settings.require_m01_safety()
        self.settings = settings
        self.journal = journal
        # Explicit dependency injection seam only. Production CLI supplies
        # None: paid model calls remain unconfigured and unreachable.
        self._ai_role_caller = ai_role_caller

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

    def run_deep_cycle(self, trace_id: str, *,
                       market: IndodaxPublicMarket | None = None) -> CycleResult:
        """One bounded M03-B public-only discovery→closed-candle analysis mainpath."""
        client = market if market is not None else IndodaxPublicMarket()
        try:
            preliminary = DailyPairScanner(client).scan()
            evidence = deep_scan(client, preliminary, limit=2)
        except PublicDataError as exc:
            reason = f"PUBLIC_DEEP_SOURCE_INVALID:{exc}"
            result = None
        else:
            reason = "G1_TRADE_COVERAGE_AND_RISK_NOT_CONNECTED"
            result = evidence.summary()
        self.journal.record_reject(trace_id, reason)
        return CycleResult(trace_id, FinalDecision.REJECT, reason, False, None, None, result)

    def run_rolling_deep_cycle(self, trace_id: str, *,
                               market: IndodaxPublicMarket | None = None) -> CycleResult:
        """Persistently rotate a bounded 2-pair deep inspection, never trade.

        No cross-minute scores are merged: each cycle is an isolated diagnostic.
        The SQLite reservation is atomic and restart-safe, with 12s pacing.
        """
        client = market if market is not None else IndodaxPublicMarket()
        try:
            preliminary = DailyPairScanner(client).scan()
            selected = self.journal.reserve_deep_window(
                trace_id, tuple(x.pair for x in preliminary.shortlist),
                observed_epoch=preliminary.observed_epoch,
            )
            evidence = deep_scan(client, preliminary, limit=len(selected.pair_ids),
                                 offset=selected.starting_index, require_btc=True)
            data = evidence.summary()
            data.update({
                "reserved_pairs": list(selected.pair_ids),
                "starting_index": selected.starting_index,
                "next_index": selected.next_index,
                "reservation_replayed": selected.replayed,
                "universe_digest": selected.universe_digest,
                "full_universe_deep_verified": False,
                "cross_cycle_ranking_allowed": False,
            })
            reason = "G1_ROTATING_PUBLIC_EVIDENCE_UNTRUSTED"
        except (PublicDataError, ValueError) as exc:
            reason = f"PUBLIC_ROLLING_SCAN_BLOCKED:{exc}"
            data = None
        self.journal.record_reject(trace_id, reason)
        return CycleResult(trace_id, FinalDecision.REJECT, reason, False, None, None, None, data)

    def run_bulk_deep_cycle(self, trace_id: str, *,
                            market: IndodaxPublicMarket | None = None) -> CycleResult:
        """One bounded 20-pair public attempt; never use stale/replayed ranks."""
        client = market if market is not None else IndodaxPublicMarket()
        try:
            preliminary = DailyPairScanner(client).scan()
            evidence = bulk_public_deep_scan(client, preliminary, limit=20)
        except PublicDataError as exc:
            reason = f"PUBLIC_BULK_BLOCKED:{exc}"
            result = None
        else:
            reason = "G1_PUBLIC_BULK_SOURCE_UNVERIFIED"
            result = evidence
        # One G2 boundary, in the already accepted bulk mainpath. No provider
        # object is configured, no billable requests, no trading permission.
        ai_shadow = ThreeRoleShadow(caller=self._ai_role_caller).run(trace_id, result)
        self.journal.record_reject(trace_id, reason)
        return CycleResult(
            trace_id, FinalDecision.REJECT, reason, False,
            None, None, None, None, result, ai_shadow,
        )

    def run_public_ws_cycle(self, trace_id: str, *, db_path: str,
                            pair: str = "btc_idr",
                            seconds: int = 12) -> CycleResult:
        """Official public trade stream probe, same orchestrator, always REJECT."""
        journal = PublicTradeStreamJournal(db_path)
        try:
            ws_data = probe_official_public_ws(
                journal, pair=pair, seconds=seconds,
            )
            reason = "PUBLIC_WS_TRADE_SAMPLE_UNATTESTED"
        except StreamEvidenceError as exc:
            reason = "PUBLIC_WS_PROBE_REJECTED:" + str(exc)
            ws_data = journal.status(pair) if pair.endswith("_idr") else None
        self.journal.record_reject(trace_id, reason)
        return CycleResult(
            trace_id, FinalDecision.REJECT, reason, False,
            public_ws=ws_data,
        )

    def health(self) -> dict[str, str | int | bool]:
        return {
            "release": "M04E_OFFICIAL_PUBLIC_WS_PROBE_CODED",
            "core_strategy": "DAILY_SPOT_SCALPING",
            "decision_mode": self.settings.decision_mode,
            "execution_mode": self.settings.execution_mode,
            "execution_allowed": False,
            "accepted_decision_records": self.journal.count(),
            "next_stage": "M04E_VPS_PUBLIC_WS_OFFSET_RECOVERY_PROOF",
        }
