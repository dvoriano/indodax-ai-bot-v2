"""SINGLE app entrypoint: safe vertical slice; real G1/G2/Indodax deferred."""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
import time
import sqlite3

from .config import Settings
from .contracts import FinalDecision
from .journal import DecisionJournal
from .public_market import IndodaxPublicMarket, PublicDataError
from .scanner import DailyPairScanner, rank_scanner_with_early_momentum
from .volume_screening import apply_deep_volume_floor
from .deep_analysis import deep_scan, bulk_public_deep_scan, evaluate_source_backed_candle_research
from .three_role_ai import ThreeRoleShadow
from .position_manager import PositionManager
from .execution_preflight import ExecutionPreflight
from .m06_account_review import review_unverified_account, M06AccountDecision
from .m06_shadow_account import AccountObservation
from .source_coverage import assess_public_ws_btc_for_g1
from .g1_receipt import G1ReceiptVerifier
from .owner_control import OwnerDecisionLedger
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
    public_research: dict | None = None
    research_archive: dict | None = None
    position_review: dict | None = None
    preflight_review: dict | None = None


class DailyScalpingOrchestrator:
    def __init__(self, settings: Settings, journal: DecisionJournal, *,
                 ai_role_caller=None,
                 source_verifier: G1ReceiptVerifier | None = None,
                 owner_control: OwnerDecisionLedger | None = None) -> None:
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
        if owner_control is not None and not isinstance(
                owner_control, OwnerDecisionLedger):
            raise TypeError("INVALID_M05_OWNER_CONTROL")
        self._owner_control = owner_control
        # One V2 position review: no second trading loop, no exchange GET.
        # Until verified owned fills are integrated, review() is fail-closed.
        self._position_manager = PositionManager()
        self._execution_preflight = ExecutionPreflight()

    def review_m06_account(self, *, observation: AccountObservation | None = None,
                           now_epoch: int) -> M06AccountDecision:
        """One original orchestrator, local offline account diagnostic only."""
        self.settings.require_m01_safety()
        return review_unverified_account(observation, now_epoch=now_epoch)

    def _record_m05_reject(self, cycle: CycleResult) -> CycleResult:
        """One original orchestrator result, NO Telegram/order-side effects."""
        if self._owner_control is not None:
            if (cycle.final_decision is not FinalDecision.REJECT
                    or cycle.execution_allowed is not False):
                raise ValueError("M05_CANNOT_PROMOTE_MAINPATH_AUTHORITY")
            self._owner_control.record_orchestrator_reject(
                trace_id=cycle.trace_id, reason=cycle.reason,
                now_epoch=int(time.time()),
            )
        position = self._position_manager.review()
        # Never invent fills, PnL or executable SELL from a public scanner.
        preflight = self._execution_preflight.assess_buy()
        return replace(
            cycle,
            position_review={
                "status": position.status,
                "reason": position.reason,
                "bot_ownership_verified": position.bot_ownership_verified,
                "execution_allowed": position.execution_allowed,
                "realized": position.realized,
            },
            preflight_review={
                "status": preflight.status,
                "reason": preflight.reason,
                "risk_veto": preflight.risk_veto,
                "execution_allowed": preflight.execution_allowed,
                "exchange_order_request_created": preflight.exchange_order_request_created,
            },
        )

    def _scan_with_early_momentum(self, client: IndodaxPublicMarket):
        """One existing scanner. Persist first minute ticker quotes across cycles.

        This produces a PUBLIC watchlist only; no G1 or AI role promotion.
        No extra HTTP requests and no fake completed one-minute candles.
        """
        screening = DailyPairScanner(client).scan()
        if not screening.universe:
            # Legacy mocked M03 scanner fixture, no historical price source.
            return screening
        reports = self.journal.record_and_assess_public_ticker_momentum(screening)
        # One source-bound prospective casebook, read ONCE for this cycle.
        # Annotates each new scanner candidate, never overrides BUY gates.
        experience = self.journal.public_surge_casebook_summary()
        ranked = rank_scanner_with_early_momentum(
            screening, reports, casebook=experience,
        )
        return replace(ranked, surge_experience=experience)

    def _attach_public_wall_experience(self, candidate, observation):
        """Same original deep path, no extra fetch, provider or trade."""
        wall = self.journal.record_and_compare_public_wall(candidate, observation)
        return replace(candidate, wall_temporal_evidence=wall)

    def run_cycle(self, trace_id: str, *, public_scanner: bool = False,
                  ws_db_path: str | None = None,
                  min_volume_idr: Decimal = Decimal("150000000"),
                  deep_limit: int = 20) -> CycleResult:
        """Default single entrypoint; PUBLIC M03 path only by explicit opt-in.

        Reuse the original bulk orchestrator with its journal, same-window
        evidence, source/G1 checks and M04 shadow. No second scanner or
        executable proposal. Legacy --once performs ZERO public GETs.
        """
        if public_scanner:
            if not ws_db_path:
                reason = "M03_MAINPATH_REQUIRES_BTC_WS_SOURCE"
                self.journal.record_reject(trace_id, reason)
                return self._record_m05_reject(
                    CycleResult(trace_id, FinalDecision.REJECT, reason)
                )
            return self.run_bulk_deep_cycle(
                trace_id, ws_db_path=ws_db_path,
                min_volume_idr=min_volume_idr, deep_limit=deep_limit,
            )
        # No synthetic candles, mock AI authority, private API or phantom orders.
        # Even a configured AUTO policy cannot authorize M01 trade intents.
        reason = "G1_SOURCE_NOT_CONNECTED"
        self.journal.record_reject(trace_id, reason)
        return self._record_m05_reject(
            CycleResult(trace_id, FinalDecision.REJECT, reason)
        )

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

    def run_public_history_research_cycle(
        self, trace_id: str, *, pair: str = "btc_idr",
        minutes: int = 180,
        market: IndodaxPublicMarket | None = None,
    ) -> CycleResult:
        """M03-K original orchestrator PUBLIC research, Final always REJECT.

        Fetches at most three public REST endpoints, no source issuer,
        provider, balance check, Telegram, or trade. Journal records REJECT.
        """
        client = market if market is not None else IndodaxPublicMarket()
        try:
            history = client.closed_1m_research_history(pair, minutes=minutes)
            report = evaluate_source_backed_candle_research(history)
        except PublicDataError as exc:
            reason = f"PUBLIC_HISTORY_RESEARCH_BLOCKED:{exc}"
            report = None
        else:
            reason = "PUBLIC_OHLC_RESEARCH_UNATTESTED_NO_TRADE"
        self.journal.record_reject(trace_id, reason)
        return CycleResult(
            trace_id, FinalDecision.REJECT, reason, False,
            public_research=report,
        )

    def run_public_history_archive_cycle(
        self, trace_id: str, *, pair: str = "btc_idr",
        minutes: int = 360, windows: int = 4,
        market: IndodaxPublicMarket | None = None,
    ) -> CycleResult:
        """M03-L: bounded sequential PUBLIC backfill, same journal, NO orders.

        First invocation archives the latest windows. The next invocation
        automatically continues from the OLDEST committed minute, not a
        misleading new disconnected daily snapshot. Each exact 1m window
        is checked and committed atomically; incomplete source stops safely.
        """
        if (not isinstance(pair,str) or not pair.endswith("_idr")
                or type(minutes) is not int or not 90 <= minutes <= 360
                or type(windows) is not int or not 1 <= windows <= 4):
            raise ValueError("INVALID_PUBLIC_ARCHIVE_OPTIONS")
        client = market if market is not None else IndodaxPublicMarket()
        committed = []
        failure = None
        for _ in range(windows):
            # Recompute from durable state after each successful commit.
            # Replay after restart naturally resumes the first missing window.
            anchor = self.journal.earliest_public_ohlc_epoch(pair)
            try:
                history = client.closed_1m_research_history(
                    pair, minutes=minutes, end_epoch=anchor,
                )
                committed.append(self.journal.archive_public_ohlc_history(history))
            except (PublicDataError, ValueError) as exc:
                failure = str(exc)
                break
        summary = self.journal.public_ohlc_archive_summary(pair)
        archive_history = None
        try:
            archive_history = self.journal.recent_contiguous_public_ohlc_history(
                pair,max_minutes=4320,
            )
            research = (
                evaluate_source_backed_candle_research(archive_history)
                if archive_history is not None else None
            )
        except (PublicDataError, ValueError) as exc:
            research = None
            failure = failure or ("ARCHIVE_EVALUATION_INVALID:" + str(exc))
        report = {
            "stage": "M03L_DURABLE_PUBLIC_OHLC_HISTORY",
            "status": ("PUBLIC_ARCHIVE_BACKFILL_INCOMPLETE"
                       if failure else "PUBLIC_ARCHIVE_BATCH_ACCEPTED_UNATTESTED"),
            "pair": pair,
            "windows_requested": windows,
            "windows_committed_this_call": len(committed),
            "per_window_minutes": minutes,
            "individual_batches": committed,
            "archive_summary": summary,
            "latest_contiguous_research": research,
            "latest_contiguous_1m_rows": (
                len(archive_history.candles) if archive_history is not None else 0
            ),
            "source_failure": failure,
            "max_public_get_estimate": windows*3,
            "other_ip_clients_rate_accounted_for": False,
            "historical_ohlc_immutable_against_accidental_conflicts": True,
            "exchange_signed_tape_or_origin_proven": False,
            "g1_verified": False,
            "out_of_sample_edge_validated": False,
            "net_roi_verified": False,
            "g2_candidate_authorized": False,
            "execution_allowed": False,
        }
        reason = (
            "PUBLIC_OHLC_ARCHIVE_BACKFILL_BLOCKED:" + failure
            if failure else "PUBLIC_OHLC_ARCHIVE_UNATTESTED_NO_TRADE"
        )
        self.journal.record_reject(trace_id, reason)
        return CycleResult(
            trace_id, FinalDecision.REJECT, reason, False,
            research_archive=report,
        )

    def run_scan_cycle(self, trace_id: str, *,
                       market: IndodaxPublicMarket | None = None) -> CycleResult:
        """Public scan into same mainpath; no G1 evidence or trading approval."""
        client = market if market is not None else IndodaxPublicMarket()
        try:
            ranking = self._scan_with_early_momentum(client)
        except PublicDataError as exc:
            reason = f"PUBLIC_SCANNER_INVALID:{exc}"
            summary = None
        else:
            reason = "G1_DEEP_ANALYSIS_AND_RISK_NOT_CONNECTED"
            summary = ranking.summary()
        self.journal.record_reject(trace_id, reason)
        return CycleResult(trace_id, FinalDecision.REJECT, reason, False, None, summary)

    def run_deep_cycle(self, trace_id: str, *,
                       market: IndodaxPublicMarket | None = None,
                       target_pair: str | None = None) -> CycleResult:
        """Same M03-B mainpath; optional bounded, source-eligible pair re-observation.

        Targeting is PUBLIC observation only. It cannot add ineligible pairs,
        shortcut the full-market scanner, G1 attestation, or BUY/RISK gates.
        The existing no-target two-pair scan remains unchanged.
        """
        client = market if market is not None else IndodaxPublicMarket()
        try:
            preliminary = self._scan_with_early_momentum(client)
            limit = 2
            if target_pair is not None:
                from .scanner import MAIN_EXCLUDED_PAIRS
                if (not isinstance(target_pair, str)
                        or not target_pair.endswith("_idr")
                        or target_pair in MAIN_EXCLUDED_PAIRS
                        or not preliminary.universe):
                    raise PublicDataError("TARGET_DEEP_PAIR_NOT_ELIGIBLE")
                matching = tuple(
                    x for x in preliminary.universe if x.pair == target_pair
                )
                if len(matching) != 1:
                    raise PublicDataError("TARGET_DEEP_PAIR_NOT_ELIGIBLE")
                # Preserve full all-pair observation context, but observe
                # exactly ONE source-screened pair with original deep_scan.
                preliminary = replace(
                    preliminary, shortlist=matching, watchlist=matching,
                )
                limit = 1
            evidence = deep_scan(
                client, preliminary, limit=limit,
                on_candidate=self._attach_public_wall_experience,
            )
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
            preliminary = self._scan_with_early_momentum(client)
            selected = self.journal.reserve_deep_window(
                trace_id, tuple(x.pair for x in preliminary.shortlist),
                observed_epoch=preliminary.observed_epoch,
            )
            evidence = deep_scan(
                client, preliminary, limit=len(selected.pair_ids),
                offset=selected.starting_index, require_btc=True,
                on_candidate=self._attach_public_wall_experience,
            )
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
                            ws_db_path: str | None = None,
                            min_volume_idr: Decimal = Decimal("0"),
                            deep_limit: int = 20) -> CycleResult:
        """One bounded 20–40-pair public attempt; optional 24h IDR liquidity floor.

        The full IDR universe is still sampled by the original scanner/journal.
        A positive filter only changes the same-cycle deep shortlist, never
        the radar, source trust, AI permissions, or order authorization.
        """
        client = market if market is not None else IndodaxPublicMarket()
        try:
            if type(deep_limit) is not int or not 20 <= deep_limit <= 40:
                raise PublicDataError("BULK_LIMIT_MUST_BE_20_TO_40")
            preliminary = self._scan_with_early_momentum(client)
            if min_volume_idr != Decimal("0") or deep_limit > 20:
                # Keep one original scanner; 40 reads the SAME ranked universe
                # rather than incorrectly trying to extend the default top30.
                preliminary = apply_deep_volume_floor(
                    preliminary, minimum_idr=min_volume_idr,
                    max_deep_pairs=deep_limit,
                )
            evidence = bulk_public_deep_scan(
                client, preliminary, limit=deep_limit,
                on_candidate=self._attach_public_wall_experience,
            )
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
                        PublicTradeStreamJournal.readonly(ws_db_path).closed_minute_sample(
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
        return self._record_m05_reject(CycleResult(
            trace_id, FinalDecision.REJECT, reason, False,
            None, None, None, None, result, ai_shadow,
        ))

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
