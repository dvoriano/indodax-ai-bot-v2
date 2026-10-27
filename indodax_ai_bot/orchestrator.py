"""SINGLE app entrypoint: safe vertical slice; real G1/G2/Indodax deferred."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import time
import sqlite3

from .config import Settings
from .contracts import FinalDecision
from .journal import DecisionJournal
from .public_market import IndodaxPublicMarket, PublicDataError
from .scanner import DailyPairScanner
from .deep_analysis import deep_scan, bulk_public_deep_scan
from .three_role_ai import ThreeRoleShadow
from .source_coverage import assess_public_ws_btc_for_g1
from .g1_receipt import G1ReceiptVerifier
from .trade_stream import PublicTradeStreamJournal, probe_official_public_ws, collect_official_public_ws, StreamEvidenceError
from .public_ws_supervisor import supervised_public_ws_batch, PublicWSSupervisorError


def _btc_rest_ws_observability(rest: dict | None, ws: dict | None) -> dict:
    """M04-O: compare two PUBLIC partial views of the same BTC closed 1m.

    Matching values are NOT proof of exhaustive market trade coverage,
    authenticated upstream origin or permission to consult paid AI.
    """
    base = {
        "stage": "M04O_PUBLIC_BTC_REST_WS_OBSERVABILITY",
        "status": "REJECT",
        "reason": "OBSERVATION_NOT_YET_ALIGNED",
        "pair": "btc_idr",
        "public_rest_recent_trades_exhaustive": False,
        "public_ws_complete_trade_tape_proven": False,
        "independent_exchange_origin_verified": False,
        "observed_values_cannot_establish_complete_coverage": True,
        "g1_receipt_issuance_allowed": False,
        "g2_provider_call_allowed": False,
        "execution_allowed": False,
    }
    if not isinstance(rest, dict):
        return {**base, "reason": "NO_ACTUAL_BTC_REST_MINUTE_SAMPLE"}
    if not isinstance(ws, dict) or ws.get("status") not in (
            "PUBLIC_SAMPLE_OBSERVED", "PUBLIC_SAMPLE_EMPTY"):
        ws_reason = ws.get("reason") if isinstance(ws, dict) else None
        # Expose specifically a known local discontinuity rejection to G1's
        # existing REST-vs-WS source quality diagnostic; never grant trust.
        gap_reason = ws_reason if ws_reason in (
            "REJECT_WS_MINUTE_CROSSES_RECORDED_GAP",
            "REJECT_WS_REBASELINE_IN_CLOSED_MINUTE",
            "REJECT_WS_GAP_LEDGER_INVALID",
            "REJECT_WS_GAP_LEDGER_LIMIT_EXCEEDED",
            "REJECT_WS_GAP_LEDGER_INCONSISTENT",
        ) else None
        return {
            **base, "reason": "NO_VALID_SAME_MINUTE_WS_SAMPLE",
            "public_ws_gap_rejection_reason": gap_reason,
            "public_ws_gap_in_minute": gap_reason is not None,
        }
    minute = rest.get("closed_1m_start_epoch")
    end = rest.get("closed_1m_end_epoch")
    if (type(minute) is not int or minute <= 0
            or type(end) is not int or end != minute + 60
            or ws.get("closed_1m_epoch") != minute
            or ws.get("closed_1m_end_epoch") != end
            or ws.get("minute_trade_coverage_complete") is not False
            or ws.get("g1_receipt_issuance_allowed") is not False
            or rest.get("g1_receipt_issuance_allowed") is not False):
        return {**base, "reason": "REST_WS_CLOSED_MINUTE_OR_TRUST_MISMATCH"}
    rest_count = rest.get("visible_trade_rows")
    ws_count = ws.get("trade_rows_visible_in_minute")
    if (type(rest_count) is not int or rest_count < 0
            or type(ws_count) is not int or ws_count < 0):
        return {**base, "reason": "INVALID_PUBLIC_REST_WS_TRADE_COUNT"}
    try:
        r_buy = Decimal(rest["visible_buy_idr"])
        r_sell = Decimal(rest["visible_sell_idr"])
        w_buy = Decimal(ws["buy_idr_volume_visible"])
        w_sell = Decimal(ws["sell_idr_volume_visible"])
        if any(not x.is_finite() or x < 0
               for x in (r_buy, r_sell, w_buy, w_sell)):
            raise ValueError("invalid flow")
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return {**base, "reason": "INVALID_PUBLIC_REST_WS_FLOW"}
    values_match = (rest_count == ws_count and r_buy == w_buy
                    and r_sell == w_sell)
    return {
        **base,
        "status": ("PUBLIC_VISIBLE_SAMPLES_MATCH_UNATTESTED" if values_match
                   else "PUBLIC_VISIBLE_SAMPLES_DIFFER_UNATTESTED"),
        "reason": "REST_AND_WS_ARE_NONEXHAUSTIVE_OBSERVATIONS",
        "closed_1m_start_epoch": minute,
        "closed_1m_end_epoch": end,
        "rest_visible_trade_rows": rest_count,
        "ws_visible_trade_rows": ws_count,
        "trade_row_delta_ws_minus_rest": ws_count - rest_count,
        "rest_visible_buy_idr": str(r_buy),
        "ws_visible_buy_idr": str(w_buy),
        "buy_idr_delta_ws_minus_rest": str(w_buy - r_buy),
        "rest_visible_sell_idr": str(r_sell),
        "ws_visible_sell_idr": str(w_sell),
        "sell_idr_delta_ws_minus_rest": str(w_sell - r_sell),
        "rest_visible_total_idr": str(r_buy + r_sell),
        "ws_visible_total_idr": str(w_buy + w_sell),
        "total_idr_delta_ws_minus_rest": str(w_buy + w_sell - r_buy - r_sell),
        "public_samples_match_without_completeness_proof": values_match,
    }


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
                 ai_role_caller=None,
                 source_verifier: G1ReceiptVerifier | None = None) -> None:
        settings.require_m01_safety()
        self.settings = settings
        self.journal = journal
        # Production CLI supplies None for BOTH dependencies. A provider
        # callback alone cannot bypass signed G1 receipt verification.
        # Injecting a verifier does not authenticate public REST/WS evidence.
        if source_verifier is not None and not isinstance(source_verifier, G1ReceiptVerifier):
            raise TypeError("INVALID_G1_SOURCE_VERIFIER")
        self._ai_role_caller = ai_role_caller
        self._source_verifier = source_verifier

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
                            market: IndodaxPublicMarket | None = None,
                            ws_db_path: str | None = None) -> CycleResult:
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
        # M04-H: optional read-only bridge to the EXISTING public WS SQLite
        # journal, strictly diagnostic. No promotions to g1_verified/issuer
        # flags. REST and WS may be from DIFFERENT temporal snapshots.
        if ws_db_path is not None and result is not None:
            cohort = result.get("eligible_cohort") or {}
            # M04-N: prefer actual PUBLIC BTC REST observation even if other
            # candidates were rejected. A cohort rejection must not suppress
            # factual BTC minute diagnostics. Never infer from wall clock.
            minute = result.get("btc_public_rest_closed_1m_epoch_diagnostic")
            cohort_minute = cohort.get("closed_1m_epoch")
            result["btc_ws_minute_reference"] = (
                "ACTUAL_PUBLIC_BTC_REST_CLOSED_CANDLE_UNATTESTED"
                if type(minute) is int else "NO_ACTUAL_BTC_REST_CLOSED_CANDLE"
            )
            # Cohort has REST receipt's END; minute is WS query START.
            if (type(cohort_minute) is int and type(minute) is int
                    and cohort_minute != minute + 60):
                result["btc_ws_closed_minute_sample"] = {
                    "status": "REJECT",
                    "reason": "BTC_REST_COHORT_CLOSED_MINUTE_MISMATCH",
                    "g1_receipt_issuance_allowed": False,
                    "g2_provider_call_allowed": False,
                    "execution_allowed": False,
                }
            elif type(minute) is not int:
                result["btc_ws_closed_minute_sample"] = {
                    "status": "REJECT",
                    "reason": "NO_ALIGNED_BTC_CLOSED_MINUTE",
                    "g1_receipt_issuance_allowed": False,
                    "g2_provider_call_allowed": False,
                    "execution_allowed": False,
                }
            elif not Path(ws_db_path).is_file():
                result["btc_ws_closed_minute_sample"] = {
                    "status": "REJECT",
                    "reason": "PUBLIC_WS_SQLITE_NOT_FOUND",
                    "g1_receipt_issuance_allowed": False,
                    "g2_provider_call_allowed": False,
                    "execution_allowed": False,
                }
            else:
                try:
                    result["btc_ws_closed_minute_sample"] = (
                        PublicTradeStreamJournal(ws_db_path).closed_minute_sample(
                            "btc_idr", closed_1m_epoch=minute,
                            now_epoch=int(time.time()),
                        )
                    )
                except (StreamEvidenceError, sqlite3.Error, OSError, ValueError):
                    result["btc_ws_closed_minute_sample"] = {
                        "status": "REJECT",
                        "reason": "PUBLIC_WS_SAMPLE_UNAVAILABLE_OR_INVALID",
                        "g1_receipt_issuance_allowed": False,
                        "g2_provider_call_allowed": False,
                        "execution_allowed": False,
                    }
            # These are two observed, NON-exhaustive public data views.
            # Explicitly preserve difference/mismatch instead of promoting
            # a public subset to signed G1 evidence or calling paid models.
            result["btc_rest_ws_observability"] = _btc_rest_ws_observability(
                result.get("btc_public_rest_visible_minute_sample"),
                result.get("btc_ws_closed_minute_sample"),
            )
            # M04-R: attach the observed WS gap/coverage rejection to the
            # EXISTING G1 source admission rather than a separate scanner.
            # All source/provenance, G2 and execution authority stays false.
            result["g1_source_admission"]["btc_ws_source_quality"] = (
                assess_public_ws_btc_for_g1(
                    result.get("btc_ws_closed_minute_sample"),
                    result.get("btc_rest_ws_observability"),
                )
            )
        # One G2 boundary, in the already accepted bulk mainpath. No provider
        # object is configured, no billable requests, no trading permission.
        ai_shadow = ThreeRoleShadow(
            caller=self._ai_role_caller,
            source_verifier=self._source_verifier,
        ).run(trace_id, result)
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

    def run_public_ws_collect_cycle(self, trace_id: str, *, db_path: str,
                                    pair: str = "btc_idr", sessions: int = 2,
                                    seconds: int = 30, pause_seconds: int = 1) -> CycleResult:
        """Bounded PUBLIC collector through same durable journal, Final REJECT."""
        journal = PublicTradeStreamJournal(db_path)
        try:
            report = collect_official_public_ws(
                journal, pair=pair, sessions=sessions,
                session_seconds=seconds, pause_seconds=pause_seconds,
            )
            reason = (
                "PUBLIC_WS_COLLECTION_UNATTESTED" if report["status"] ==
                "PUBLIC_SAMPLE_COLLECTED_UNATTESTED"
                else "PUBLIC_WS_COLLECTION_INTERRUPTED"
            )
        except StreamEvidenceError:
            report = {
                "status": "REJECT",
                "reason": "INVALID_PUBLIC_WS_COLLECTOR_REQUEST",
                "g1_receipt_issuance_allowed": False,
                "g2_provider_call_allowed": False,
                "execution_allowed": False,
            }
            reason = "PUBLIC_WS_COLLECTION_INTERRUPTED"
        self.journal.record_reject(trace_id, reason)
        return CycleResult(
            trace_id, FinalDecision.REJECT, reason, False,
            public_ws=report,
        )

    def run_public_ws_supervised_cycle(
        self, trace_id: str, *, db_path: str, pair: str = "btc_idr",
        sessions: int = 10, seconds: int = 30, pause_seconds: int = 1,
        status_file: str | None = None,
    ) -> CycleResult:
        """One lock-protected PUBLIC service batch, no paid AI or orders."""
        try:
            report = supervised_public_ws_batch(
                db_path, pair=pair, sessions=sessions, seconds=seconds,
                pause_seconds=pause_seconds, status_file=status_file,
            )
        except PublicWSSupervisorError:
            report = {
                "stage": "M04J_PUBLIC_WS_SUPERVISION_SLICE",
                "status": "REJECT",
                "reason": "PUBLIC_WS_SUPERVISOR_LOCK_OR_BOUNDS_BLOCKED",
                "g1_receipt_issuance_allowed": False,
                "g2_provider_call_allowed": False,
                "execution_allowed": False,
                "restart_only_after_clean_batch": False,
            }
        reason = ("PUBLIC_WS_SUPERVISION_UNATTESTED"
                  if report["status"] == "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED"
                  else "PUBLIC_WS_SUPERVISION_INTERRUPTED")
        self.journal.record_reject(trace_id, reason)
        return CycleResult(
            trace_id, FinalDecision.REJECT, reason, False,
            public_ws=report,
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
