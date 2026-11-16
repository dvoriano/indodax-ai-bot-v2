"""SINGLE app entrypoint: safe vertical slice; real G1/G2/Indodax deferred."""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
import time
import hashlib
import json
import re
import sqlite3
import os
import stat

from .config import Settings
from .contracts import FinalDecision
from .journal import DecisionJournal
from .public_market import IndodaxPublicMarket, PublicDataError
from .scanner import DailyPairScanner, rank_scanner_with_early_momentum, MAIN_EXCLUDED_PAIRS
from .m03_ws_summary_offline_contract import review_documented_market_summary_sequence_offline
from .volume_screening import apply_deep_volume_floor
from .deep_analysis import deep_scan, bulk_public_deep_scan, evaluate_source_backed_candle_research, observe_public_quote_friction, OwnerEnteredAllInFeeSnapshot
from .three_role_ai import ThreeRoleShadow
from .source_coverage import assess_public_ws_btc_for_g1
from .g1_receipt import G1ReceiptVerifier
from .m06_account_review import review_unverified_account, M06AccountDecision
from .m06_shadow_account import AccountObservation
from .m06_private_payloads import parse_account, parse_orders, parse_fills, parse_open_orders
from .m06_ownership_ledger import M06OwnershipLedger, OwnershipReview, ProtectedRebuyReview
from .m06_capital_reconcile import reconcile_unverified_capital, CapitalReconciliation
from .policy import ScalpingPolicy, IDRFundingRouteReview
from .m06_scoped_https import M06ScopedApproval, M06RequestBudget, M06PrivateProbeResult, run_scoped_private_probe
from .m06_account_coverage import M06CoverageApproval, M06CoverageResult, run_coverage_readonly_once
from .m07_order_adapter import OfficialV2OrderAdapter, M07OrderDraft, M07OfflineStatusObservation
from .m07_limit_preflight import assess_m07_limit_buy_preflight_offline, M07LimitBuyPreflightPreview, M07PreflightBlocked
from .m07_same_candidate_public import assess_m07_same_candidate_public_boundary
from .m07_main_idr_sizing import (
    assess_m07_owner_main_idr_sizing_offline, valid_main_funding_schema,
)
from .m07_last_moment_sources import review_m07_last_moment_sources_offline
from .m07_private_order_readonly import read_m07_order_status_once, read_m07_latest_order_status_once, read_m07_14day_order_status_once
from .m06_account_coverage import M06AccountDiagnosticResult, run_account_diagnostic_readonly_once
from .m06_idr_live_readonly import inspect_idr_budget_readonly_once
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
    final_decision: FinalDecision | None
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
    stage_disposition: str = "LEGACY_DIAGNOSTIC"
    owner_advisory: dict | None = None


class DailyScalpingOrchestrator:
    @staticmethod
    def review_strategy_a_nonlive_once(*, review_type: str, **inputs) -> dict:
        """Original V2 orchestrator OFFLINE entrypoint, never an order action.

        The four bounded reference reviews use no network, model, Telegram,
        balance, journal mutation or any trade execution. The official M07
        adapter remains hardlocked even if review status appears favorable.
        """
        from .m08_strategy_a_nonlive import (
            assess_ws3_priority_alert_offline,
            assess_maker_entry_offline,
            assess_exit_strategy_a_offline,
            assess_rebound_watch_offline,
        )
        inspectors = {
            "PRIORITY_ALERT": assess_ws3_priority_alert_offline,
            "BUY_MAKER": assess_maker_entry_offline,
            "POSITION_EXIT": assess_exit_strategy_a_offline,
            "REBOUND_WATCH": assess_rebound_watch_offline,
        }
        if type(review_type) is not str or review_type not in inspectors:
            raise ValueError("STRATEGY_A_REVIEW_TYPE_INVALID")
        result = inspectors[review_type](**inputs)
        if (result.get("execution_allowed") is not False
                or result.get("order_sent") is not False):
            raise RuntimeError("STRATEGY_A_NONLIVE_HARDLOCK_VIOLATED")
        return result

    def review_m08_strategy_a_persisted_stop_shadow_offline_once(
            self, *, account_fingerprint: str, symbol: str,
            source_buy_client_order_id: str, observed_epoch: int,
            exit_inputs: dict) -> dict:
        """Original V2 offline journal + Strategy A review, never an order.

        Scope is a caller-supplied SYNTHETIC position identity, not exchange
        bot-owned provenance. A latched review survives restarts but neither
        this code nor an M05 Telegram approval may dispatch a SELL.
        """
        if (type(exit_inputs) is not dict
                or "stop_latched" in exit_inputs):
            raise ValueError("M08_STRATEGY_A_STOP_LATCH_INPUT_INVALID")
        scope = dict(
            account_fingerprint=account_fingerprint, symbol=symbol,
            source_buy_client_order_id=source_buy_client_order_id,
        )
        existing = self.journal.read_m08_strategy_a_stop_shadow_nonlive(
            **scope)
        assessed = self.review_strategy_a_nonlive_once(
            review_type="POSITION_EXIT", stop_latched=existing["latched"],
            **exit_inputs)
        if (not existing["latched"]
                and assessed.get("status") in (
                    "STOP_TAKER_PREVIEW_ONLY",
                    "STOP_CANCEL_RECONCILE_FIRST",
                )
                and assessed.get("stop_latched") is True):
            state = self.journal.latch_m08_strategy_a_stop_shadow_nonlive_once(
                **scope, observed_epoch=observed_epoch,
                assessed_exit=assessed)
        else:
            state = existing
        return {
            **assessed,
            "offline_stop_journal_latched": state["latched"],
            "offline_stop_journal_first_observed_epoch":
                state["first_observed_epoch"],
            "offline_stop_journal_execution_allowed": False,
            "stop_source_bot_owned_verified": False,
            "no_live_sell_or_cancel": True,
        }

    @staticmethod
    def review_m03_ws_summary_fixture_watch_offline(
        frames: object, *, now_epoch: int,
    ) -> dict:
        """Research-only review through the existing original V2 mainpath.

        No constructed journal, REST/WS client, paid AI, G1 source issuer,
        Telegram owner ledger or trade. Reuse the original M03 exclusions
        without pretending a summary is a trusted REST scanner result.
        """
        return review_documented_market_summary_sequence_offline(
            frames,
            now_epoch=now_epoch,
            excluded_pairs=MAIN_EXCLUDED_PAIRS,
        )

    @staticmethod
    def inspect_m04_ws3_micro_role_readiness_readonly(
        *, db_path: Path, now_epoch: int, provider_report: dict,
    ) -> dict:
        """Original WS3 -> M04 source integrity and three-role readiness.

        Inspection only. Partial WS3 is NOT complete trade tape, closed
        candle, independent G1 issuance or approval for a provider request.
        Does not instantiate a journal, market client, model or owner sender.
        """
        from .m04_radar_three_role_handoff_readonly import (
            inspect_original_ws3_micro_prealert_three_role_handoff_readonly,
        )
        from .three_role_ai import ROLES

        if (type(now_epoch) is not int or now_epoch <= 0
                or not isinstance(provider_report, dict)):
            raise ValueError("M04_WS3_READINESS_INVALID_INPUT")
        source = inspect_original_ws3_micro_prealert_three_role_handoff_readonly(
            db_path=db_path, now_epoch=now_epoch,
        )
        status = source.get("status")
        candidates = source.get("candidates")
        integrity_ok = (
            status == "M04_WS3_MICRO_PREALERT_ROLE_INPUTS_PREPARED"
            and type(candidates) is list and 1 <= len(candidates) <= 2
            and source.get("candidate_count") == len(candidates)
            and source.get("three_role_input_slots_prepared") == 3 * len(candidates)
            and source.get("actual_three_role_ai_calls") == 0
            and source.get("provider_http_calls") == 0
            and source.get("execution_allowed") is False
            and source.get("g1_independent_issuer_verified") is False
        )
        reports = []
        if integrity_ok:
            seen = set()
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    integrity_ok = False
                    break
                packet = candidate.get("public_evidence")
                pair = candidate.get("pair")
                digest = candidate.get("evidence_digest_sha256")
                slots = candidate.get("role_slots")
                try:
                    computed = hashlib.sha256(json.dumps(
                        packet, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=True, allow_nan=False,
                    ).encode("ascii")).hexdigest()
                except (ValueError, TypeError, UnicodeError):
                    integrity_ok = False
                    break
                if (type(pair) is not str or pair in seen
                        or not isinstance(packet, dict)
                        or packet.get("pair") != pair
                        or packet.get("source_kind") !=
                        "ORIGINAL_V2_WS3_MICRO_PREALERT_SOURCE_UNATTESTED"
                        or packet.get("execution_allowed") is not False
                        or packet.get("complete_trade_tape_verified") is not False
                        or packet.get("independent_g1_issuer_verified") is not False
                        or packet.get("closed_candles") != "UNVERIFIED"
                        or type(digest) is not str or digest != computed
                        or type(slots) is not list or len(slots) != len(ROLES)
                        or any(
                            type(slot) is not dict
                            or slot.get("role") != role
                            or slot.get("evidence_digest_sha256") != digest
                            or slot.get("model_output") is not None
                            or slot.get("dispatch") !=
                            "NOT_CALLED_G1_UNVERIFIED_AND_OWNER_PAID_AI_GO_ABSENT"
                            for slot, role in zip(slots, ROLES)
                        )):
                    integrity_ok = False
                    break
                score = candidate.get("quality_score_out_of_100")
                if type(score) is not int or not 0 <= score <= 100:
                    integrity_ok = False
                    break
                seen.add(pair)
                reports.append({
                    "pair": pair, "evidence_digest_sha256": digest,
                    "quality_score_observational_only": score,
                    "role_input_order": list(ROLES), "roles_called": [],
                    "missing_evidence": list(candidate.get("missing_evidence") or []),
                    "candidate_buy_qualified": False,
                })
        if status == "M04_WS3_MICRO_PREALERT_NO_CURRENT_SOURCE":
            readiness_status = "M04_WS3_READINESS_NO_CURRENT_PREALERT"
        elif integrity_ok:
            readiness_status = "M04_WS3_READINESS_ROLES_WAITING_G1_AND_PAID_GO"
        elif status == "M04_WS3_MICRO_PREALERT_ROLE_INPUTS_PREPARED":
            readiness_status = "M04_WS3_READINESS_PACKET_INTEGRITY_FAILED"
            reports = []
        else:
            readiness_status = "M04_WS3_READINESS_SOURCE_NOT_ACCEPTED"
        return {
            "stage": "M04_ORIGINAL_V2_WS3_MICRO_THREE_ROLE_READINESS",
            "status": readiness_status,
            "upstream_m04_status": status,
            "candidate_count": len(reports),
            "candidates": reports,
            "role_contract": list(ROLES),
            "role_slots_validated": 3 * len(reports),
            "provider_local_config_status": provider_report.get("status", "UNKNOWN"),
            "provider_models_present_local_only": (
                provider_report.get("status") ==
                "M04_PROVIDER_LOCAL_CONFIG_PRESENT_UNVERIFIED"
            ),
            "market_source_partial_only": True,
            "closed_1m_5m_15m_verified": False,
            "complete_15m_1h_tape_verified": False,
            "g1_independent_issuer_verified": False,
            "owner_paid_ai_go_authorized": False,
            "model_dispatch_allowed": False,
            "actual_three_role_responses": 0,
            "paid_provider_calls": 0,
            "owner_final_decision": None,
            "telegram_messages_sent": 0,
            "order_sent": False,
            "execution_allowed": False,
            "blockers": [
                "M04_G1_INDEPENDENT_ISSUER_AND_COMPLETE_TAPE_NOT_VERIFIED",
                "M04_CLOSED_1M_5M_15M_NOT_VERIFIED",
                "M04_OWNER_PAID_AI_GO_NOT_GRANTED",
                "M04_REAL_THREE_ROLE_RESPONSES_NOT_OBSERVED",
            ],
        }

    def __init__(self, settings: Settings, journal: DecisionJournal, *,
                 ai_role_caller=None,
                 source_verifier: G1ReceiptVerifier | None = None,
                 owner_control: OwnerDecisionLedger | None = None,
                 m03_owner_fee_snapshot: OwnerEnteredAllInFeeSnapshot | None = None) -> None:
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
        if m03_owner_fee_snapshot is not None and type(m03_owner_fee_snapshot) is not OwnerEnteredAllInFeeSnapshot:
            raise TypeError("M03_INVALID_OWNER_ENTERED_FEE_SNAPSHOT")
        self._m03_owner_fee_snapshot = m03_owner_fee_snapshot

    def review_m11_offline_shadow_safety_once(
        self, *, owner_user_id: int, observed_epoch: int,
        proposed_buy_cost_idr: Decimal | None = None,
        existing_exposure_idr: Decimal | None = None,
        open_order_count: int | None = None,
        daily_realized_net_idr: Decimal | None = None,
        realized_pnl_day_wib: str | None = None,
    ) -> dict:
        """CLI → ORIGINAL orchestrator → SAME M11 journal read-only review.

        Only explicitly isolated synthetic /tmp fixtures are allowed.
        Neither public M03/M04 data, a provider nor a Telegram owner source
        may become an M11 release credential or an executable Risk verdict.
        """
        self.settings.require_m01_safety()
        if not getattr(self.journal, "_m11_readonly_fixture", False):
            raise ValueError("M11_SHADOW_READONLY_FIXTURE_REQUIRED")
        if (self._ai_role_caller is not None
                or self._source_verifier is not None
                or self._owner_control is not None):
            raise ValueError("M11_SHADOW_EXTERNAL_AUTHORITY_FORBIDDEN")
        snapshot = self.journal.review_m11_offline_risk_halt(
            owner_user_id=owner_user_id, observed_epoch=observed_epoch,
            proposed_buy_cost_idr=proposed_buy_cost_idr,
            existing_exposure_idr=existing_exposure_idr,
            open_order_count=open_order_count,
            daily_realized_net_idr=daily_realized_net_idr,
            realized_pnl_day_wib=realized_pnl_day_wib,
        )
        if (snapshot.get("status") !=
                "REJECT_OFFLINE_RISK_HALT_AND_UNVERIFIED_SOURCES"
                or snapshot.get("execution_allowed") is not False
                or snapshot.get("m11_live_release_authorized") is not False
                or snapshot.get("scenario_is_real_exchange_evidence") is not False
                or snapshot.get("owner_identity_authenticated") is not False):
            raise ValueError("M11_SHADOW_UNEXPECTED_AUTHORITY_DENIED")
        return {
            "stage": "M11_ORIGINAL_V2_SHADOW_RISK_HALT_READONLY",
            "status": "REJECT_SHADOW_OFFLINE_ONLY",
            "source": "ISOLATED_SYNTHETIC_FIXTURE_NOT_EXCHANGE",
            "original_orchestrator_invoked": True,
            "same_original_decision_journal_review_invoked": True,
            "risk_halt_review": snapshot,
            "owner_final_decision": None,
            "g1_independent_source_verified": False,
            "account_or_real_pnl_verified": False,
            "risk_release_live_gate_connected": False,
            "telegram_or_paid_ai_called": False,
            "order_sent": False,
            "execution_allowed": False,
            "m11_live_release_authorized": False,
        }

    def inspect_m07_official_order_adapter_once(self) -> dict:
        """Original V2 mainpath, bounded offline signed-contract status ONLY.

        No caller can turn this into a submit/status/cancel request. A real
        exchange order and any permission require future independent GO,
        funds/ownership, Risk, Final and atomic intent reconciliation.
        """
        return OfficialV2OrderAdapter().contract_status()

    def assess_m07_limit_buy_preflight_offline(
            self, *, draft: M07OrderDraft, pair_rules: dict,
            best_bid_idr: Decimal, best_ask_idr: Decimal,
            free_idr: Decimal, locked_idr: Decimal,
            floki_rebuy_idr: Decimal, pepe_rebuy_idr: Decimal,
            commitments_not_locked_idr: Decimal,
            buy_all_in_fee_bps: Decimal, sell_all_in_fee_bps: Decimal,
            max_spread_bps: Decimal,
    ) -> M07LimitBuyPreflightPreview:
        """Same original M07 caller, preliminary hypothetical order gates.

        Nothing here is G1, actual exchange funds, live executable quote,
        owner PASS or permission to send an order.
        """
        self.settings.require_m01_safety()
        return assess_m07_limit_buy_preflight_offline(
            draft=draft, pair_rules=pair_rules,
            best_bid_idr=best_bid_idr, best_ask_idr=best_ask_idr,
            free_idr=free_idr, locked_idr=locked_idr,
            floki_rebuy_idr=floki_rebuy_idr,
            pepe_rebuy_idr=pepe_rebuy_idr,
            commitments_not_locked_idr=commitments_not_locked_idr,
            buy_all_in_fee_bps=buy_all_in_fee_bps,
            sell_all_in_fee_bps=sell_all_in_fee_bps,
            max_spread_bps=max_spread_bps,
        )

    def review_m07_preflight_m11_risk_halt_offline_once(
            self, *, limit_preview: M07LimitBuyPreflightPreview,
            last_moment: dict) -> dict:
        """Original M07 synthetic preflight → SAME original M11 journal.

        An opt-in REPORT, never a trading gate, Final or Risk approval.
        This does not feed hypothetical limit price, quantity, balances,
        fees, commitments, PAPER PnL or timestamps into M11 account caps
        or daily realized loss; those facts require authenticated sources.
        """
        self.settings.require_m01_safety()
        if (type(limit_preview) is not M07LimitBuyPreflightPreview
                or type(last_moment) is not dict
                or last_moment.get("execution_allowed") is not False
                or last_moment.get("order_sent") is not False
                or limit_preview.execution_allowed is not False
                or limit_preview.order_sent is not False):
            raise ValueError("M07_PREFLIGHT_RISK_REFERENCE_UNTRUSTED_INPUT")
        if (self._owner_control is not None
                or self._ai_role_caller is not None
                or self._source_verifier is not None
                or self._m03_owner_fee_snapshot is not None):
            raise ValueError("M07_PREFLIGHT_RISK_REFERENCE_EXTERNAL_SOURCE_DENIED")
        # Do not create a new ledger. The same original DecisionJournal file
        # is opened read-only with a private synthetic /tmp scope hardcheck.
        readonly = DecisionJournal.open_m11_offline_fixture_readonly(
            self.journal._db_path)
        if readonly._db_path != self.journal._db_path:
            raise ValueError("M07_PREFLIGHT_RISK_REFERENCE_NOT_SAME_JOURNAL")
        report = DailyScalpingOrchestrator(
            self.settings, readonly,
        ).review_m11_offline_shadow_safety_once(
            owner_user_id=1, observed_epoch=1793781000,
        )
        snap = report["risk_halt_review"]
        if (report["status"] != "REJECT_SHADOW_OFFLINE_ONLY"
                or snap["realized_pnl_settlement_verified"] is not False
                or snap["scenario_daily_loss_below_limit"] is not None
                or snap["scenario_proposed_total_capital_within"] is not None
                or snap["scenario_order_slots_within"] is not None
                or snap["owner_identity_authenticated"] is not False
                or report["owner_final_decision"] is not None
                or report["m11_live_release_authorized"] is not False
                or report["execution_allowed"] is not False):
            raise ValueError("M07_PREFLIGHT_RISK_REFERENCE_AUTHORITY_DENIED")
        return {
            "stage": "M07_M11_ORIGINAL_OFFLINE_PREFLIGHT_RISK_OBSERVATION",
            "status": "REJECT_OFFLINE_UNVERIFIED_RELEASE",
            "source": "SAME_ORIGINAL_V2_PRIVATE_SYNTHETIC_JOURNAL",
            "m07_limit_math_reference_passed_unverified":
                limit_preview.checks_passed_unverified,
            "m07_last_moment_status_reference": last_moment.get("status"),
            "m11_risk_halt": report,
            "owner_identity_status": "UNKNOWN_SYNTHETIC_FIXTURE_ONLY",
            "existing_exposure_status": "UNKNOWN_NO_AUTHENTICATED_ACCOUNT",
            "open_orders_status": "UNKNOWN_NO_AUTHENTICATED_EXCHANGE_STATE",
            "realized_daily_loss_status":
                "UNKNOWN_NO_EXCHANGE_SETTLED_IDR_EVIDENCE",
            "g1_source_status": "UNVERIFIED_NO_INDEPENDENT_ISSUER",
            "m07_order_intent_created": False,
            "owner_final_decision": None,
            "order_sent": False,
            "execution_allowed": False,
            "m11_live_release_authorized": False,
        }

    def review_m07_owner_main_idr_sizing_offline(
            self, *, public_candidate: dict, account_snapshot: dict,
            owner_main_source_classified: bool,
            requested_ticket_ceiling_idr: Decimal | None = None,
            new_floki_sell_rebuy_hold_idr: Decimal | None = None,
            new_pepe_sell_rebuy_hold_idr: Decimal | None = None,
            commitments_not_locked_idr: Decimal,
            max_spread_bps: Decimal, now_epoch: int) -> dict:
        """One original orchestrator: M03 quote + M06 View Only + M07 math.

        Owner classification admits general IDR as a funding SOURCE only.
        Account and public observations, ticket, risk and consent remain
        unverified for execution. NO network, keys, new order or GO.
        """
        self.settings.require_m01_safety()
        return assess_m07_owner_main_idr_sizing_offline(
            public_candidate=public_candidate,
            account_snapshot=account_snapshot,
            owner_main_source_classified=owner_main_source_classified,
            requested_ticket_ceiling_idr=requested_ticket_ceiling_idr,
            new_floki_sell_rebuy_hold_idr=new_floki_sell_rebuy_hold_idr,
            new_pepe_sell_rebuy_hold_idr=new_pepe_sell_rebuy_hold_idr,
            commitments_not_locked_idr=commitments_not_locked_idr,
            max_spread_bps=max_spread_bps, now_epoch=now_epoch,
        )


    def observe_m11_m07_sourcebound_account_and_loss_readonly_once(
            self, *, account: dict | None, now_epoch: int) -> dict:
        """M11 observation on already-collected M06 data, NEVER an M11 gate.

        Reuses EXACT original DecisionJournal M08 source inspection; no
        second REST/private GET, synthetic/PAPER PnL, owner attestation,
        risk-limit release, new ledger, order intent or executable verdict.
        A signed View Only response can observe free/locked IDR and all-pair
        openOrders but cannot prove all peer liabilities or M08 settlement.
        """
        self.settings.require_m01_safety()
        if type(now_epoch) is not int or now_epoch <= 0:
            raise ValueError("M11_M07_OBSERVATION_EPOCH_INVALID")
        m08 = self.journal.review_m09_original_m08_position_and_idr_readonly()
        if (type(m08) is not dict
                or m08.get("status") !=
                    "M08_NO_AUTHENTICATED_BOT_OWNED_SETTLEMENT_ISSUER"
                or m08.get("wallet_settled_idr_verified") is not False
                or m08.get("realized_net_pnl_wib_verified") is not False
                or m08.get("execution_allowed") is not False):
            raise ValueError("M11_M07_UNVERIFIED_M08_ISSUER_OR_SETTLEMENT")
        observed = None
        if (type(account) is dict
                and account.get("status") ==
                    "M06_IDR_BALANCE_OBSERVED_SOURCE_RESERVE_UNVERIFIED"
                and account.get("source_currency") == "IDR"
                and account.get("requests_completed") == 2
                and type(account.get("requests_completed")) is int
                and account.get("view_only_permissions_verified") is True
                and account.get("source_authenticated") is False
                and account.get("execution_allowed") is False
                and account.get("order_sent") is False
                and type(account.get("account_observed_epoch")) is int
                and 0 <= now_epoch - account["account_observed_epoch"] <= 30):
            try:
                free_raw = account.get("exchange_idr_free_observed")
                locked_raw = account.get("exchange_idr_locked_observed")
                orders = account.get("all_pair_open_orders_observed")
                if (type(free_raw) is not str or not 1 <= len(free_raw) <= 45
                        or type(locked_raw) is not str
                        or not 1 <= len(locked_raw) <= 45
                        or type(orders) is not int or not 0 <= orders < 1000):
                    raise ValueError("M11_M07_VIEWONLY_NUMERIC_SCOPE")
                free = Decimal(free_raw)
                locked = Decimal(locked_raw)
                if (not free.is_finite() or not locked.is_finite()
                        or min(free, locked) < 0
                        or max(free, locked) >= Decimal("1e25")):
                    raise ValueError("M11_M07_VIEWONLY_NUMERIC_INVALID")
            except (ValueError, TypeError, ArithmeticError):
                pass
            else:
                observed = {
                    "m06_free_idr_observed": format(free, "f"),
                    "m06_locked_idr_observed": format(locked, "f"),
                    "m06_all_pair_open_order_count_observed": orders,
                    "m06_viewonly_observed_epoch": account["account_observed_epoch"],
                    "m06_snapshot_age_seconds": (
                        now_epoch - account["account_observed_epoch"]),
                }
        # If M06 GET did not happen, was stale, malformed, spoofed, or
        # returned an unauthorized source, disclose NO apparent balance.
        # 'observed' means the existing M06 contract reported its own
        # signed 2-GET result, NOT independently certified whole-account
        # funding or a live execution authority.
        return {
            "stage": "M11_M07_ORIGINAL_SOURCEBOUND_READONLY_OBSERVATION",
            "status": "REJECT_NONLIVE_UNVERIFIED_RISK_RELEASE",
            "m06_viewonly_status": (
                "M06_TWO_SIGNED_GETS_REPORTED_RECENT_NOT_FULLY_ATTESTED"
                if observed is not None else "UNKNOWN_NO_VALID_RECENT_M06_2GET"),
            "m06_transport_observed": observed is not None,
            "m06_full_account_funding_authenticated": False,
            "m06_balance_and_open_orders_observation": observed,
            "all_peer_liabilities_verified": False,
            "account_exposure_idr_verified": False,
            "order_slots_completely_reconciled": False,
            "risk_owner_identity_authenticated": False,
            "m11_owner_risk_limits_authenticated": False,
            "m11_operational_halt_release_verified": False,
            "m11_limit_scenario_evaluated": False,
            "m08_original_journal_source_status": m08["status"],
            "m08_future_event_rows_untrusted": m08["future_event_db_rows_untrusted"],
            "m08_bot_owned_settlement_verified": False,
            "m08_realized_net_pnl_wib_verified": False,
            "realized_daily_net_loss_idr_verified": None,
            "realized_pnl_day_wib_verified": None,
            "g1_independent_issuer_verified": False,
            "owner_final_decision": None,
            "paid_ai_called": False,
            "private_gets_added_by_m11": 0,
            "m07_order_intent_created_by_m11": False,
            "order_sent": False,
            "execution_allowed": False,
            "m11_live_release_authorized": False,
        }

    def run_m07_original_source_join_readonly_once(
            self, trace_id: str, *, api_key: str, secret_key: str,
            funding: dict, local_ownership_db: str | None = None,
            market: IndodaxPublicMarket | None = None,
            ws_db_path: str | None = None,
            min_volume_idr: Decimal = Decimal("150000000"),
            deep_limit: int = 20,
            include_m11_source_observation: bool = False,
            owner_preview_user_id: int | None = None) -> dict:
        """One ORIGINAL M03 bulk -> fresh M06 View Only -> M07 sizing.

        The M03 public scanner and M06 signed account reader are called here,
        not replayed from owner JSON. Funding inputs cover ONLY the owner's
        ticket cap and explicitly known NEW special sale holds / commitments.
        No AI provider, Telegram decision, order transport, or LIVE permission.
        """
        self.settings.require_m01_safety()
        if type(include_m11_source_observation) is not bool:
            raise ValueError("M11_M07_OBSERVATION_FLAG_INVALID")
        if owner_preview_user_id is not None:
            if (type(owner_preview_user_id) is not int
                    or self._owner_control is None
                    or owner_preview_user_id !=
                        self._owner_control.owner_user_id):
                raise ValueError("M07_RUNTIME_M05_OWNER_IDENTITY_REQUIRED")
            state = self._owner_control.status(
                user_id=owner_preview_user_id,
                chat_id=owner_preview_user_id,
                chat_type="private",
            )
            if (state.get("accepted") is not True
                    or state.get("owner_mode") != "MANUAL"):
                raise ValueError("M07_RUNTIME_M05_MANUAL_MODE_REQUIRED")
        if not valid_main_funding_schema(funding):
            raise ValueError("M07_RUNTIME_FUNDING_SCHEMA_INVALID")
        if type(trace_id) is not str or not trace_id:
            raise ValueError("M07_RUNTIME_TRACE_INVALID")
        cycle = self.run_bulk_deep_cycle(
            trace_id, market=market, ws_db_path=ws_db_path,
            min_volume_idr=min_volume_idr, deep_limit=deep_limit,
        )
        bulk = cycle.bulk if type(cycle.bulk) is dict else {}
        cohort = bulk.get("eligible_cohort")
        cohort = cohort if type(cohort) is dict else {}
        allowed_pairs = cohort.get("eligible_pair_ids")
        allowed_pairs = allowed_pairs if type(allowed_pairs) is list else []
        rows = bulk.get("provisional_market_evidence")
        rows = rows if type(rows) is list else []
        ranked = bulk.get("ranked_diagnostics_top10")
        ranked = ranked if type(ranked) is list else []
        watchlist = cohort.get("provisional_watchlist_preview")
        watchlist = watchlist if type(watchlist) is list else []
        quality = cohort.get("provisional_quality_reviews")
        quality = quality if type(quality) is list else []
        selected = None
        selected_lane = None
        selected_quality_clean = False
        selected_public_status = None
        public_boundary_status_counts = {}
        # The REAL M03 deep result can have aligned candles without all
        # optional public order constraints. Count the status by source,
        # without logging pair/UID or making an extra market request.
        for row in ranked:
            if type(row) is not dict or row.get("pair") not in allowed_pairs:
                continue
            public = row.get("m07_same_candidate_public_preflight")
            status = (public.get("status") if type(public) is dict
                      else "M07_PUBLIC_PREFLIGHT_NOT_AVAILABLE")
            if type(status) is not str or len(status) > 90:
                status = "M07_PUBLIC_PREFLIGHT_INVALID_STATUS"
            public_boundary_status_counts[status] = (
                public_boundary_status_counts.get(status, 0) + 1)
        rejected = {
            "not_eligible_cohort_member": 0,
            "no_valid_source_bound_public_rules_or_fee": 0,
            "invalid_or_rejected_deep_candidate": 0,
            "stale_public_candidate": 0,
        }
        now = int(time.time())
        # A synchronized cohort is DIFFERENT from a quality-clean watchlist.
        # M03 may produce aligned valid observations yet zero clean watchlist
        # candidates. The previous bridge only inspected the watchlist and
        # incorrectly reported NO_ELIGIBLE even when M03 had good diagnostics.
        # Read the original same-batch top10 as a READ-ONLY fallback. These
        # are NOT promoted to quality-clean, G1, Risk, M05 or trading.
        synchronized = (
            bulk.get("mode") == "M03G_BULK_PUBLIC_SHARED_SOURCE_ONLY"
            and bulk.get("shared_public_scan_context") is True
            and cohort.get("status") == "ALIGNED_PUBLIC_UNATTESTED"
        )
        if synchronized:
            sources = (
                ("M03_QUALITY_WATCHLIST_REFERENCE_ONLY", rows),
                ("M03_RANKED_CAUTION_DIAGNOSTIC_ONLY", ranked),
            )
            for lane, source_rows in sources:
                for row in source_rows:
                    if type(row) is not dict:
                        rejected["invalid_or_rejected_deep_candidate"] += 1
                        continue
                    pair = row.get("pair")
                    if pair not in allowed_pairs:
                        rejected["not_eligible_cohort_member"] += 1
                        continue
                    if (row.get("eligible_for_same_window_comparison") is not True
                            or row.get("trade_authorized") is not False):
                        rejected["invalid_or_rejected_deep_candidate"] += 1
                        continue
                    public = row.get("m07_same_candidate_public_preflight")
                    # READ ONLY M06 observation requires an authentic,
                    # same-batch candidate, NOT market-order tradability.
                    # Missing public pair flags/fees remain M07 sizing vetoes
                    # but cannot prevent an authorized 2-GET account read.
                    if (type(public) is not dict
                            or public.get("stage") !=
                                "M07_SAME_CANDIDATE_PUBLIC_PREFLIGHT"
                            or public.get("candidate_pair") != pair
                            or public.get("observed_pair") != pair
                            or pair[:-4].upper() in
                                ("FLOKI", "PEPE", "USDT", "USDC")
                            or public.get("same_candidate_and_closed_minute") is not True
                            or public.get("public_fee_source_only") is not True
                            or public.get("per_account_fee_verified") is not False
                            or public.get("execution_allowed") is not False
                            or public.get("order_sent") is not False):
                        rejected["no_valid_source_bound_public_rules_or_fee"] += 1
                        continue
                    epoch = public.get("observed_epoch")
                    if (type(epoch) is not int
                            or not 0 <= now - epoch <= 30):
                        rejected["stale_public_candidate"] += 1
                        continue
                    selected = public
                    selected_public_status = public.get("status")
                    selected_lane = lane
                    selected_quality_clean = (
                        lane == "M03_QUALITY_WATCHLIST_REFERENCE_ONLY"
                        and pair in watchlist
                        and any(type(q) is dict and q.get("pair") == pair
                                and q.get("clear_of_provisional_cautions") is True
                                for q in quality)
                    )
                    break
                if selected is not None:
                    break
        base = {
            "stage": "M07_ORIGINAL_LIVE_SOURCE_JOIN_READONLY",
            "status": "M07_NO_ELIGIBLE_M03_SAME_CYCLE_CANDIDATE",
            "trace_id": trace_id,
            "m03_bulk_coverage_status": bulk.get("coverage_status"),
            "m03_eligible_cohort_status": cohort.get("status"),
            "m03_eligible_cohort_pair_count": len(allowed_pairs),
            "m03_quality_clean_watchlist_count": len(watchlist),
            "m03_source_bound_watchlist_rows": len(rows),
            "m03_ranked_diagnostic_rows": len(ranked),
            "m03_candidate_rejection_counts": rejected,
            "m03_public_boundary_status_counts": public_boundary_status_counts,
            "m03_selected_public_boundary_status": selected_public_status,
            "m03_selected_order_reference_ready": (
                selected_public_status ==
                "M07_PUBLIC_SOURCE_BOUND_RULES_REFERENCE_ONLY"),
            "m03_candidate_selection_lane": selected_lane,
            "m03_selected_quality_clean": selected_quality_clean,
            "m03_cautionary_fallback_cannot_authorize_trade": True,
            "m03_g1_independently_verified": False,
            "m04_risk_three_role_executable_verified": False,
            "m05_order_specific_final_verified": False,
            "account_all_in_fee_verified": False,
            "m07_atomic_intent_exchange_reconciled": False,
            "m06_authenticated_viewonly_get_count": 0,
            "m07_main_sizing": None,
            "candidate_pair": None,
            "order_sent": False,
            "execution_allowed": False,
            "live_trading_authorized": False,
        }
        if selected is None:
            if not synchronized and bulk:
                status = "M07_M03_COHORT_NOT_ALIGNED_NO_PRIVATE_GET"
            elif rejected["stale_public_candidate"] > 0:
                status = "M07_M03_PUBLIC_ALREADY_STALE_NO_PRIVATE_GET"
            elif rejected["no_valid_source_bound_public_rules_or_fee"] > 0:
                status = "M07_NO_READY_PUBLIC_PAIR_RULES_OR_FEE_REFERENCE"
            else:
                status = "M07_NO_ELIGIBLE_M03_SAME_CYCLE_CANDIDATE"
            self.journal.record_stage_observation(
                trace_id + "_m07", "M07_READONLY_SOURCE_JOIN", status,
            )
            report = dict(base, status=status)
            if include_m11_source_observation:
                report["m11_sourcebound_risk_observation"] = (
                    self.observe_m11_m07_sourcebound_account_and_loss_readonly_once(
                        account=None, now_epoch=int(time.time()),
                    )
                )
            return report
        # The selected same-batch source passed freshness before M06 GET.
        # The original M07 sizing ALSO rechecks both epoch TTLs afterwards.
        # EXACT original M06 two signed View Only GETs, no duplicate HTTP
        # path and no custom credential source. External/unknown commitments
        # cannot be silently inferred as zero by the reader.
        account = self.inspect_m06_idr_budget_readonly_once(
            api_key=api_key, secret_key=secret_key,
            local_ownership_db=local_ownership_db,
        )
        sized = self.review_m07_owner_main_idr_sizing_offline(
            public_candidate=selected, account_snapshot=account,
            owner_main_source_classified=True,
            now_epoch=int(time.time()), **funding,
        )
        ready_reference = sized.get("status") == (
            "M07_MAIN_SIZING_RULES_MATCH_REFERENCE_ONLY")
        # A View Only source join can be proven even if public fee,
        # suspension flag, or precision was not supplied. NEVER infer
        # these missing properties, and never call that a sizing PASS.
        status = (
            "M07_REAL_M03_M06_REFERENCE_JOINED_NO_ORDER"
            if ready_reference
            else "M07_REAL_M03_M06_READONLY_JOINED_PUBLIC_RULES_UNVERIFIED"
            if selected_public_status !=
                "M07_PUBLIC_SOURCE_BOUND_RULES_REFERENCE_ONLY"
            else "M07_REAL_M03_M06_JOIN_BLOCKED"
        )
        self.journal.record_stage_observation(
            trace_id + "_m07", "M07_READONLY_SOURCE_JOIN", status,
        )
        report = dict(
            base, status=status,
            candidate_pair=selected["candidate_pair"],
            m06_authenticated_viewonly_get_count=account.get(
                "requests_completed", 0),
            m06_account_observed_epoch=account.get("account_observed_epoch"),
            m06_open_orders_observed=account.get("all_pair_open_orders_observed"),
            m06_idr_free_observed=account.get("exchange_idr_free_observed"),
            m06_idr_locked_observed=account.get("exchange_idr_locked_observed"),
            m07_main_sizing=sized,
        )
        if owner_preview_user_id is not None:
            # Dedicated M05 service/timer owns Telegram delivery, never this
            # scanner. One original source-bound, quality-clean candidate
            # + exact real View Only UID/key pseudonym may queue a no-order
            # MANUAL preview. Caution-only ranked fallback is NOT qualified.
            if not selected_quality_clean:
                preview = {
                    "status": "M07_RUNTIME_M05_CAUTION_ONLY_NO_PREVIEW",
                    "owner_card_queued": False, "execution_allowed": False,
                }
            elif not ready_reference:
                preview = {
                    "status": "M07_RUNTIME_M05_SIZING_NOT_READY_NO_PREVIEW",
                    "owner_card_queued": False, "execution_allowed": False,
                }
            else:
                fingerprint = account.get("viewonly_key_uid_fingerprint")
                if (type(fingerprint) is not str
                        or re.fullmatch(r"[a-f0-9]{64}", fingerprint) is None):
                    preview = {
                        "status": "M07_RUNTIME_M05_UID_BINDING_NOT_VERIFIED",
                        "owner_card_queued": False, "execution_allowed": False,
                    }
                else:
                    # Client IDs stay deterministic per scan trace/pair/minute
                    # and already have replay/duplicate checks in original
                    # M05 ledger. No key/UID is embedded in any order ID.
                    client_id = "m7p" + hashlib.sha256(
                        (trace_id + ":" + selected["candidate_pair"] + ":"
                         + str(selected["closed_1m_epoch"])).encode("utf-8")
                    ).hexdigest()[:24]
                    preview = self.queue_m07_same_source_main_buy_preview_nonlive(
                        user_id=owner_preview_user_id,
                        chat_id=owner_preview_user_id,
                        account_fingerprint=fingerprint,
                        client_order_id=client_id,
                        public_candidate=selected,
                        account_snapshot=account,
                        funding=funding, now_epoch=int(time.time()),
                    )
            report["m07_owner_preview"] = preview
        if include_m11_source_observation:
            report["m11_sourcebound_risk_observation"] = (
                self.observe_m11_m07_sourcebound_account_and_loss_readonly_once(
                    account=account, now_epoch=int(time.time()),
                )
            )
        return report

    def review_m07_last_moment_gate_offline(
            self, *, limit_preview: M07LimitBuyPreflightPreview,
            public_candidate: dict | None = None,
            account_snapshot: dict | None = None,
            owner_advisory_proposal_id: str | None = None,
            source_digest: str | None = None,
            closed_1m_epoch: int | None = None,
            now_epoch: int,
    ) -> dict:
        """Same M03/M06/M05/M07 caller, read-only, never exchange order.

        A View Only balance or a genuine M05 advisory callback cannot
        authorize an execution; original journal still blocks UNKNOWNs.
        Source-bound M05 inspection uses original owner SQLite if injected.
        """
        self.settings.require_m01_safety()
        advisory = None
        if (self._owner_control is not None
                and owner_advisory_proposal_id is not None
                and source_digest is not None
                and closed_1m_epoch is not None):
            pair = limit_preview.symbol[:-3].lower() + "_idr"
            advisory = self._owner_control.inspect_market_advisory_preflight_readonly(
                proposal_id=owner_advisory_proposal_id, pair=pair,
                source_digest=source_digest,
                closed_1m_epoch=closed_1m_epoch, now_epoch=now_epoch,
            )
        return review_m07_last_moment_sources_offline(
            limit_preview=limit_preview,
            public_candidate=public_candidate,
            account_snapshot=account_snapshot,
            owner_advisory=advisory,
            offline_intent_guard=self.journal.m07_offline_intent_summary(),
            now_epoch=now_epoch,
        )

    def queue_m07_owner_manual_telegram_preview_offline(
            self, *, user_id: int, chat_id: int,
            account_fingerprint: str, draft: M07OrderDraft,
            now_epoch: int,
            expected_buy_total_idr: Decimal | None = None) -> dict:
        """One original M07 draft to SAME M05 owner ledger, preview ONLY.

        No fake market, account, AI, owner Final, preflight, execution or
        signal is accepted as true. The existing M07 adapter stays HARDLOCKED.
        """
        self.settings.require_m01_safety()
        if self._owner_control is None:
            raise ValueError("M07_MANUAL_OWNER_LEDGER_NOT_CONNECTED")
        report = OfficialV2OrderAdapter.contract_status()
        if (report.get("network_allowed") is not False
                or report.get("execution_allowed") is not False):
            raise ValueError("M07_MANUAL_PREVIEW_EXECUTOR_NOT_HARDLOCKED")
        result = self._owner_control.queue_manual_trade_preview_offline(
            user_id=user_id,chat_id=chat_id,
            account_fingerprint=account_fingerprint,
            draft=draft,now_epoch=now_epoch,
            expected_buy_total_idr=expected_buy_total_idr,
        )
        if result.get("accepted") is True and (
                result.get("execution_allowed") is not False
                or result.get("order_sent") is not False
                or result.get("owner_order_execution_authorized") is not False):
            raise ValueError("M07_MANUAL_PREVIEW_UNEXPECTED_AUTHORITY")
        return result

    def queue_m07_same_source_main_buy_preview_nonlive(
            self, *, user_id: int, chat_id: int,
            account_fingerprint: str, client_order_id: str,
            public_candidate: dict, account_snapshot: dict,
            funding: dict, now_epoch: int,
            owner_main_source_classified: bool = True,
    ) -> dict:
        """EXISTING M03/M06 -> M07 sizing -> EXISTING M05 private card.

        This intentionally joins the previously disconnected original V2
        source-bound reference sizing and owner Telegram PREVIEW. It never
        accepts a rank-only candidate, guess of quantity/price from a
        Telegram message, market BUY, or a live position as authority.

        M03 PUBLIC and M06 View Only *references* do not prove G1, Risk,
        per-account all-in fees, owner execution consent or spendable IDR.
        The resulting M05 card explicitly remains SIMULASI/NO ORDER. A
        future manually authorized release MUST recheck all live sources
        and receive a separate authenticated order-specific Final.
        """
        self.settings.require_m01_safety()
        if (self._owner_control is None
                or type(now_epoch) is not int or now_epoch <= 0
                or not valid_main_funding_schema(funding)):
            raise ValueError("M07_MAIN_M05_REFERENCE_SCOPE_INVALID")
        # Every monetary input must be explicit and typed. The original
        # sizing function evaluates source freshness, account View Only
        # permissions, open orders, pair rules, quote precision and funds.
        if any(type(value) is not Decimal for value in funding.values()):
            raise ValueError("M07_MAIN_M05_FUNDING_DECIMAL_REQUIRED")
        sized = self.review_m07_owner_main_idr_sizing_offline(
            public_candidate=public_candidate,
            account_snapshot=account_snapshot,
            owner_main_source_classified=owner_main_source_classified,
            now_epoch=now_epoch,
            **funding,
        )
        blocked = {
            "stage": "M07_MAIN_SAME_SOURCE_M05_OWNER_PREVIEW_NONLIVE",
            "status": "M07_MAIN_SOURCE_NOT_READY_FOR_OWNER_PREVIEW",
            "sizing_status": sized.get("status"),
            "owner_card_queued": False,
            "g1_issuer_verified": False,
            "ai_risk_verified": False,
            "live_account_spendable_verified": False,
            "owner_order_execution_authorized": False,
            "order_sent": False,
            "execution_allowed": False,
        }
        if sized.get("status") != "M07_MAIN_SIZING_RULES_MATCH_REFERENCE_ONLY":
            return blocked
        # The size report can be constructed from untrusted local scenario
        # mappings. Recheck all identity/precision/economic relations before
        # copying it to the durable original M05 draft and Telegram card.
        if (type(public_candidate) is not dict
                or type(account_snapshot) is not dict
                or type(client_order_id) is not str
                or public_candidate.get("status") !=
                    "M07_PUBLIC_SOURCE_BOUND_RULES_REFERENCE_ONLY"
                or public_candidate.get("same_candidate_and_closed_minute") is not True
                or public_candidate.get("public_fee_source_only") is not True
                or public_candidate.get("per_account_fee_verified") is not False
                or public_candidate.get("execution_allowed") is not False
                or account_snapshot.get("execution_allowed") is not False
                or sized.get("price_step_and_minimum_reference_met") is not True
                or sized.get("spread_ceiling_reference_met") is not True
                or sized.get("source_independent_g1_verified") is not False
                or sized.get("owner_m05_order_final_verified") is not False
                or sized.get("wallet_order_spendable_verified") is not False):
            return dict(blocked, status="M07_MAIN_SOURCE_REFERENCE_IDENTITY_UNVERIFIED")
        pair = public_candidate.get("candidate_pair")
        if (type(pair) is not str or pair != public_candidate.get("observed_pair")
                or not re.fullmatch(r"[a-z0-9]{2,20}_idr", pair)
                or pair[:-4].upper() in ("FLOKI", "PEPE", "USDT", "USDC")
                or account_snapshot.get("all_pair_open_orders_observed") != 0):
            return dict(blocked, status="M07_MAIN_SOURCE_PAIR_OR_ACCOUNT_MISMATCH")
        try:
            price = Decimal(public_candidate["public_best_ask_idr_reference"])
            qty = Decimal(sized["sized_qty_base_reference"])
            quoted_cost = Decimal(sized["buy_max_cost_idr_reference"])
            available = Decimal(sized["main_idr_pool_scenario"])
            precision = Decimal(public_candidate["public_pair_rules_reference"]["price_precision"])
            increment = Decimal(public_candidate["public_pair_rules_reference"]["quantity_increment"])
            gross = price * qty
            ticket_cap = funding.get("requested_ticket_ceiling_idr", available)
            if (not all(v.is_finite() for v in (
                    price, qty, quoted_cost, available, precision, increment,
                    gross, ticket_cap))
                    or any(v <= 0 for v in (
                        price, qty, quoted_cost, available, precision,
                        increment, gross, ticket_cap))
                    or gross > quoted_cost
                    or quoted_cost > min(ticket_cap, available)
                    or price % precision != 0
                    or qty % increment != 0):
                return dict(blocked, status="M07_MAIN_SOURCE_QUOTE_OR_BUDGET_INVALID")
            draft = M07OrderDraft(
                symbol=pair[:-4].upper() + "IDR",
                side="BUY", order_type="LIMIT", client_order_id=client_order_id,
                price=price, quantity=qty,
            )
            draft.parameters()
        except (KeyError, ValueError, TypeError, InvalidOperation,
                ArithmeticError):
            return dict(blocked, status="M07_MAIN_SOURCE_QUOTE_OR_BUDGET_INVALID")
        queued = self.queue_m07_owner_manual_telegram_preview_offline(
            user_id=user_id, chat_id=chat_id,
            account_fingerprint=account_fingerprint,
            draft=draft, now_epoch=now_epoch,
            expected_buy_total_idr=quoted_cost,
        )
        if queued.get("accepted") is not True:
            return dict(
                blocked, status="M07_MAIN_M05_OWNER_PREVIEW_NOT_QUEUED",
                owner_rejection_reason=queued.get("reason"),
            )
        return {
            **blocked,
            "status": "M07_MAIN_REFERENCE_BUY_QUEUED_M05_PREVIEW_NO_ORDER",
            "owner_card_queued": True,
            "preview_id": queued["preview_id"],
            "client_order_id": client_order_id,
            "pair": pair,
            "buy_limit_price_idr_reference": format(price, "f"),
            "base_quantity_reference": format(qty, "f"),
            "buy_max_cost_idr_reference": format(quoted_cost, "f"),
            "future_sell_vwap_idr_reference": sized.get(
                "future_sell_vwap_idr_reference"),
            "owner_card_preview_only": True,
            "existing_m05_owner_telegram_delivery": True,
        }

    def recheck_m07_clicked_manual_buy_from_sources_nonlive_once(
            self, *, owner_user_id: int, preview_id: str,
            api_key: str, secret_key: str, funding: dict,
            local_ownership_db: str | None = None,
            market: IndodaxPublicMarket | None = None) -> dict:
        """Owner callback -> SAME M03 deep+BTC, M06 View Only, M07 NO-POST.

        This bounded *single-pair* refresh is explicitly opt-in. It is not
        a second scanner: reuse the original M03 preliminary universe,
        deep_scan and its original candidate source-bound quote callback.
        The clicked pair comes exclusively from the original M05 ledger,
        never from Telegram callback text or the current highest-ranked coin.
        """
        self.settings.require_m01_safety()
        blocked = {
            "stage": "M07_ORIGINAL_CLICK_SOURCE_REFRESH_NONLIVE",
            "status": "M07_CLICK_SOURCE_UNAVAILABLE",
            "m03_m06_reference_rechecked": False,
            "m07_manual_sender_hold_staged": False,
            "g1_issuer_verified": False,
            "ai_risk_verified": False,
            "owner_order_final_authenticated": False,
            "order_sent": False,
            "execution_allowed": False,
        }
        if self._owner_control is None:
            return dict(blocked, status="M07_CLICK_OWNER_LEDGER_MISSING")
        clicked = self._owner_control.load_clicked_manual_buy_draft_nonlive(
            preview_id=preview_id, owner_user_id=owner_user_id,
            observed_epoch=int(time.time()),
        )
        if clicked is None:
            return dict(blocked, status="M07_CLICK_NOT_BOUND_TO_OWNER_DRAFT")
        draft = clicked["draft"]
        pair = draft.symbol[:-3].lower() + "_idr"
        # No private account access until the unchanged original scanner
        # proves the exact clicked pair is currently in the 20-40 shortlist
        # AND still has a clean observed entry setup in aligned BTC context.
        client = market if market is not None else IndodaxPublicMarket()
        try:
            screening = self._scan_with_early_momentum(client)
            shortlist = next(
                (row for row in screening.shortlist if row.pair == pair),
                None,
            )
            if shortlist is None:
                return dict(blocked, status="M07_CLICK_PAIR_NO_LONGER_SHORTLISTED")
            scoped = replace(screening, shortlist=(shortlist,),
                             watchlist=(shortlist,))
            evidence = deep_scan(
                client, scoped, limit=1, require_btc=True,
                on_candidate=self._attach_public_wall_experience,
            )
            same = evidence.same_window or {}
            summaries = [c.summary() for c in evidence.analyzed]
            candidate = summaries[0] if len(summaries) == 1 else {}
            reviewed = next((
                row for row in same.get("provisional_quality_reviews", [])
                if row.get("pair") == pair
            ), None)
            if (same.get("status") != "ALIGNED_PUBLIC_UNATTESTED"
                    or pair not in same.get(
                        "provisional_watchlist_preview", [])
                    or type(reviewed) is not dict
                    or reviewed.get("clear_of_provisional_cautions") is not True
                    or candidate.get("eligible_for_same_window_comparison") is not True
                    or candidate.get("trade_authorized") is not False):
                return dict(blocked, status="M07_CLICK_SETUP_NO_LONGER_QUALIFIED")
            public = candidate.get("m07_same_candidate_public_preflight")
            if (type(public) is not dict
                    or public.get("status") !=
                        "M07_PUBLIC_SOURCE_BOUND_RULES_REFERENCE_ONLY"
                    or public.get("candidate_pair") != pair
                    or public.get("same_candidate_and_closed_minute") is not True
                    or public.get("execution_allowed") is not False):
                return dict(blocked, status="M07_CLICK_PUBLIC_RULES_NOT_READY")
            # Existing V2 signed View Only M06 source is the ONLY private
            # reader; no trading/withdrawal key, paid AI or exchange POST.
            account = self.inspect_m06_idr_budget_readonly_once(
                api_key=api_key, secret_key=secret_key,
                local_ownership_db=local_ownership_db,
            )
            return self.review_m07_manual_owner_last_moment_nonlive_once(
                owner_user_id=owner_user_id, preview_id=preview_id,
                account_fingerprint=clicked["account_fingerprint"],
                draft=draft, public_candidate=public,
                account_snapshot=account,
                funding=funding, owner_main_source_classified=True,
                now_epoch=int(time.time()),
            )
        except (PublicDataError, M07PreflightBlocked, ValueError, TypeError,
                ArithmeticError, OSError, sqlite3.Error):
            return dict(blocked, status="M07_CLICK_REFRESH_READONLY_FAILED")

    def review_m07_manual_owner_last_moment_nonlive_once(
            self, *, owner_user_id: int, preview_id: str,
            account_fingerprint: str, draft: M07OrderDraft,
            public_candidate: dict, account_snapshot: dict,
            funding: dict, owner_main_source_classified: bool,
            now_epoch: int) -> dict:
        """Real M05 owner press -> fresh M03/M06 references -> SAME M07 journal.

        This is a NONLIVE sender rehearsal, not a true preflight or Final.
        An earlier queued preview is deliberately insufficient: the clicked
        LIMIT BUY price, quantized qty, account fingerprint, IDR funding and
        existing unresolved intents must still match at this moment. Public
        fees and View Only account remain unverified for actual execution.
        """
        self.settings.require_m01_safety()
        if self._owner_control is None:
            raise ValueError("M07_MANUAL_LASTMOMENT_OWNER_LEDGER_REQUIRED")
        blocked = {
            "stage": "M07_MANUAL_OWNER_LASTMOMENT_NONLIVE",
            "status": "M07_MANUAL_LASTMOMENT_BLOCKED",
            "owner_button_source_bound": False,
            "m03_m06_reference_rechecked": False,
            "m07_manual_sender_hold_staged": False,
            "g1_issuer_verified": False,
            "ai_risk_verified": False,
            "account_spendable_verified": False,
            "owner_order_final_authenticated": False,
            "order_sent": False,
            "execution_allowed": False,
        }
        if (type(now_epoch) is not int or now_epoch <= 0
                or type(draft) is not M07OrderDraft
                or draft.side != "BUY" or draft.order_type != "LIMIT"
                or type(owner_main_source_classified) is not bool
                or owner_main_source_classified is not True
                or type(public_candidate) is not dict
                or type(account_snapshot) is not dict
                or not valid_main_funding_schema(funding)
                or any(type(v) is not Decimal for v in funding.values())):
            return dict(blocked, status="M07_MANUAL_LASTMOMENT_INPUT_DENIED")
        press = self._owner_control.inspect_manual_trade_press_for_sender_nonlive(
            preview_id=preview_id, owner_user_id=owner_user_id,
            account_fingerprint=account_fingerprint, draft=draft,
            observed_epoch=now_epoch,
        )
        if (press.get("status") != "M05_MANUAL_CLICK_RECORDED_PREVIEW_ONLY"
                or press.get("owner_button_recorded") is not True
                or press.get("execution_allowed") is not False
                or press.get("owner_final_execution_authenticated") is not False):
            return dict(
                blocked, status="M07_MANUAL_LASTMOMENT_OWNER_PRESS_DENIED",
                owner_press_status=press.get("status"),
            )
        matched_owner = dict(blocked, owner_button_source_bound=True)
        pair = draft.symbol[:-3].lower() + "_idr"
        if (account_snapshot.get("viewonly_key_uid_fingerprint") !=
                account_fingerprint
                or type(account_fingerprint) is not str
                or not re.fullmatch(r"[a-f0-9]{64}", account_fingerprint)
                or public_candidate.get("candidate_pair") != pair
                or public_candidate.get("observed_pair") != pair
                or public_candidate.get("same_candidate_and_closed_minute") is not True
                or public_candidate.get("execution_allowed") is not False
                or account_snapshot.get("execution_allowed") is not False):
            return dict(
                matched_owner, status="M07_MANUAL_LASTMOMENT_SOURCE_IDENTITY_DENIED",
            )
        outstanding = self.journal.m07_offline_intent_summary()
        if (outstanding.get("stage") != "M07_ORIGINAL_OFFLINE_INTENT_GUARD"
                or outstanding.get("unresolved_simulated_intents") != 0
                or outstanding.get("local_origin_prepared_no_submit_count") != 0
                or outstanding.get("manual_sender_nonlive_holds") != 0
                or outstanding.get("additional_buy_reservations_blocked") is not False):
            return dict(
                matched_owner, status="M07_MANUAL_LASTMOMENT_OUTSTANDING_HOLD",
            )
        # Re-run the SAME original M07 math with the current source-shaped
        # observations, instead of trusting a stale Telegram message.
        try:
            sized = self.review_m07_owner_main_idr_sizing_offline(
                public_candidate=public_candidate,
                account_snapshot=account_snapshot,
                owner_main_source_classified=owner_main_source_classified,
                now_epoch=now_epoch, **funding,
            )
        except (M07PreflightBlocked, ValueError, TypeError, ArithmeticError):
            return dict(
                matched_owner, status="M07_MANUAL_LASTMOMENT_SIZING_DENIED",
            )
        if sized.get("status") != "M07_MAIN_SIZING_RULES_MATCH_REFERENCE_ONLY":
            return dict(
                matched_owner, status="M07_MANUAL_LASTMOMENT_SIZING_DENIED",
                sizing_status=sized.get("status"),
            )
        try:
            expected_price = Decimal(
                public_candidate["public_best_ask_idr_reference"])
            expected_qty = Decimal(sized["sized_qty_base_reference"])
            expected_cost = Decimal(sized["buy_max_cost_idr_reference"])
            if (not all(x.is_finite() and x > 0 for x in (
                    expected_price, expected_qty, expected_cost))
                    or draft.price != expected_price
                    or draft.quantity != expected_qty
                    or expected_cost > Decimal(sized["main_idr_pool_scenario"])
                    or expected_cost > funding.get("requested_ticket_ceiling_idr",
                                                   expected_cost)
                    or draft.price * draft.quantity > expected_cost):
                raise ValueError("M07_MANUAL_LASTMOMENT_CHANGED_ORDER")
        except (KeyError, ValueError, TypeError, ArithmeticError,
                InvalidOperation):
            return dict(
                matched_owner, status="M07_MANUAL_LASTMOMENT_DRAFT_CHANGED",
            )
        # M08 uses the SAME ORIGINAL journal and the exact clicked M07
        # clientOrderId. There is no bot-issued BUY order here, so even an
        # account-only FILLED order with complete fee/cost basis must NOT
        # mint a bot-owned lot or a SELL entitlement. Corrupted account
        # evidence blocks even staging the NONLIVE receipt.
        from .journal import DecisionConflictError
        try:
            m08 = self.review_m08_buy_position_admission_readonly(
                account_fingerprint=account_fingerprint,
                symbol=draft.symbol,
                client_order_id=draft.client_order_id,
            )
        except DecisionConflictError:
            return dict(
                matched_owner, status="M07_MANUAL_LASTMOMENT_M08_SOURCE_CONFLICT",
            )
        if (m08.get("stage") !=
                "M08_ORIGINAL_BUY_FILL_POSITION_ADMISSION_READONLY"
                or m08.get("bot_owned_buy_fill_proven") is not False
                or m08.get("position_manager_admission") is not False
                or m08.get("verified_bot_owned_lots") != 0
                or m08.get("sellable_bot_owned_base_qty") != "0"
                or m08.get("order_sent") is not False
                or m08.get("execution_allowed") is not False):
            return dict(
                matched_owner,
                status="M07_MANUAL_LASTMOMENT_M08_OWNERSHIP_HARDLOCK",
            )
        # The preflight observations are NOT signed Risk, G1 or spendable
        # attestations. Stage only a durable NONLIVE no-submit hold in the
        # EXISTING original DecisionJournal, never an exchange POST.
        staged = self.review_m07_manual_owner_sender_nonlive_once(
            owner_user_id=owner_user_id, preview_id=preview_id,
            account_fingerprint=account_fingerprint, draft=draft,
            now_epoch=now_epoch,
        )
        hold = staged.get("staged")
        if (type(hold) is not dict
                or hold.get("status") != "OFFLINE_CLICK_BOUND_NO_SUBMIT"
                or staged.get("order_sent") is not False
                or staged.get("execution_allowed") is not False):
            raise ValueError("M07_MANUAL_LASTMOMENT_HARDLOCK_CHANGED")
        return dict(
            matched_owner,
            status="M07_MANUAL_LASTMOMENT_REFERENCE_HOLD_NONLIVE_ONLY",
            m03_m06_reference_rechecked=True,
            m07_manual_sender_hold_staged=True,
            client_order_id=draft.client_order_id,
            draft_sha256=hold["draft_sha256"],
            sender_status=hold["status"],
            m08_original_buy_admission_checked=True,
            m08_account_buy_source_status=m08["status"],
            m08_account_fill_cost_basis_reference_only=(
                m08["account_buy_fills_and_fees_reconciled"]
            ),
            m08_bot_owned_position_verified=False,
            m08_verified_bot_owned_lots=0,
            m08_sellable_bot_owned_base_qty="0",
            m08_position_manager_admitted=False,
        )

    def review_m07_manual_owner_sender_nonlive_once(
            self, *, owner_user_id: int, preview_id: str,
            account_fingerprint: str, draft: M07OrderDraft,
            now_epoch: int, mock_result: dict | None = None,
            mock_result_provided: bool = False,
            mock_transport_method: str = "POST") -> dict:
        """M05 original owner click → M07 original journal → fake-only POST.

        An optional mock result can validate future POST parsing and
        UNKNOWN replay without network. No true order/Final/lot issuer.
        """
        self.settings.require_m01_safety()
        if self._owner_control is None:
            raise ValueError("M07_MANUAL_SENDER_OWNER_REQUIRED")
        if (self._ai_role_caller is not None
                or self._source_verifier is not None
                or OfficialV2OrderAdapter.contract_status().get(
                    "network_allowed") is not False):
            raise ValueError("M07_MANUAL_SENDER_NETWORK_MUST_BE_HARDLOCKED")
        button = self._owner_control.inspect_manual_trade_press_for_sender_nonlive(
            preview_id=preview_id,owner_user_id=owner_user_id,
            account_fingerprint=account_fingerprint,draft=draft,
            observed_epoch=now_epoch,
        )
        if (button.get("owner_button_recorded") is not True
                or button.get("execution_allowed") is not False
                or button.get("owner_final_execution_authenticated") is not False):
            return {
                "status": "M07_MANUAL_OWNER_CLICK_NOT_ELIGIBLE",
                "source_status": button.get("status"),
                "order_sent": False, "execution_allowed": False,
                "live_trading_authorized": False,
            }
        staged = self.journal.record_m07_manual_sender_nonlive_once(
            preview_id=preview_id,owner_user_id=owner_user_id,
            account_fingerprint=account_fingerprint,draft=draft,
            owner_review=button,recorded_epoch=now_epoch,
        )
        if mock_result_provided:
            if mock_transport_method == "POST":
                fake = OfficialV2OrderAdapter.inspect_nonlive_post_result_shape(
                    draft=draft,response=mock_result,
                    submitted_at_ms=now_epoch*1000,
                )
            elif mock_transport_method == "GET":
                fake = OfficialV2OrderAdapter.inspect_nonlive_get_order_status_shape(
                    draft=draft,response=mock_result,
                    observed_at_ms=now_epoch*1000,
                )
            else:
                raise ValueError("M07_MANUAL_MOCK_ENDPOINT_METHOD_DENIED")
            observed = self.journal.observe_m07_manual_sender_mock_result_once(
                client_order_id=draft.client_order_id,
                preview_id=preview_id,draft=draft,
                result=fake,observed_epoch=now_epoch,
            )
            return {
                "stage": "M07_MANUAL_BUTTON_BOUND_FUTURE_SENDER_OFFLINE",
                "staged": staged,
                "sender_simulation": observed,
                "owner_final_decision": None,
                "real_sender_issuer_verified": False,
                "order_sent": False, "execution_allowed": False,
                "live_trading_authorized": False,
            }
        return {
            "stage": "M07_MANUAL_BUTTON_BOUND_FUTURE_SENDER_OFFLINE",
            "staged": staged,
            "sender_simulation": None,
            "owner_final_decision": None,
            "real_sender_issuer_verified": False,
            "order_sent": False, "execution_allowed": False,
            "live_trading_authorized": False,
        }

    def review_m07_original_bot_origin_local_once(
            self, *, action: str, account_uid: int,
            account_fingerprint: str, draft: M07OrderDraft,
            observed_epoch: int) -> dict:
        """Original M07 adapter → SAME V2 journal, no real order issuer.

        PREPARE/UNCERTAIN only mutate local synthetic commitment rows.
        REVIEW uses an independent read-only handle to the same original
        journal. No provider/owner/G1 injection may promote local evidence.
        """
        self.settings.require_m01_safety()
        if action not in ("PREPARE","UNCERTAIN","REVIEW") or type(action) is not str:
            raise ValueError("M07_ORIGIN_ACTION_INVALID")
        adapter = OfficialV2OrderAdapter.contract_status()
        if (adapter.get("network_allowed") is not False
                or adapter.get("execution_allowed") is not False
                or self._ai_role_caller is not None
                or self._source_verifier is not None
                or self._owner_control is not None):
            raise ValueError("M07_ORIGIN_HARDLOCK_OR_AUTHORITY_INVALID")
        kwargs = {
            "account_uid": account_uid,
            "account_fingerprint": account_fingerprint,
            "draft": draft, "observed_epoch": observed_epoch,
        }
        if action == "PREPARE":
            result = self.journal.prepare_m07_order_origin_commitment_offline(
                **kwargs)
        elif action == "UNCERTAIN":
            result = self.journal.mark_m07_order_origin_uncertain_offline(
                **kwargs)
        else:
            result = self.journal.review_m07_order_origin_handoff_readonly(
                **kwargs)
        if (result.get("execution_allowed") is not False
                or result.get("order_sent") is not False
                or result.get("live_trading_authorized") is not False
                or result.get("bot_exchange_submit_receipt_verified") is True
                or result.get("m08_position_manager_admission") is True):
            raise ValueError("M07_ORIGIN_MUST_NEVER_GRANT_AUTHORITY")
        return result

    def reserve_m07_offline_intent_once(self, *,
            intent_id: str, draft: M07OrderDraft,
            observed_epoch: int) -> dict:
        """Only original V2 journal simulation, never exchange permission."""
        return self.journal.reserve_m07_offline_order_intent(
            intent_id=intent_id, draft=draft, recorded_epoch=observed_epoch,
        )

    def simulate_m07_unknown_timeout_once(self, *,
            intent_id: str, observed_epoch: int) -> dict:
        """Persistent UNKNOWN under original journal, no network retry."""
        return self.journal.mark_m07_offline_unknown_hold(
            intent_id=intent_id, observed_epoch=observed_epoch,
        )

    def observe_m07_offline_status_once(
            self, observation: M07OfflineStatusObservation) -> dict:
        """Original V2 caller: untrusted synthetic status; never exchange proof."""
        self.settings.require_m01_safety()
        return self.journal.record_m07_offline_status_observation(observation)

    def inspect_m07_private_readonly_order_once(
            self, *, symbol: str, client_order_id: str,
            api_key: str, secret_key: str) -> dict:
        """One original V2 mainpath READ ONLY account+order GET; no trade."""
        self.settings.require_m01_safety()
        return read_m07_order_status_once(
            journal=self.journal, symbol=symbol,
            client_order_id=client_order_id,
            api_key=api_key, secret_key=secret_key,
        )

    def inspect_m07_private_latest_order_once(
            self, *, symbol: str, api_key: str,
            secret_key: str) -> dict:
        """Owner View Only account→history→order through SAME journal."""
        self.settings.require_m01_safety()
        return read_m07_latest_order_status_once(
            journal=self.journal, symbol=symbol,
            api_key=api_key, secret_key=secret_key,
        )

    def inspect_m07_private_14day_order_once(
            self, *, symbol: str, api_key: str,
            secret_key: str) -> dict:
        """One V2 View Only retrospective, max four GET; no trade."""
        self.settings.require_m01_safety()
        return read_m07_14day_order_status_once(
            journal=self.journal, symbol=symbol,
            api_key=api_key, secret_key=secret_key,
        )

    def inspect_m07_private_fill_fees_once(
            self, *, symbol: str, api_key: str, secret_key: str) -> dict:
        """Account-only 14d discovery + matching myTrades, max 5 GET."""
        self.settings.require_m01_safety()
        return read_m07_14day_order_status_once(
            journal=self.journal, symbol=symbol,
            api_key=api_key, secret_key=secret_key,
            include_fills=True,
        )

    def inspect_m07_offline_intent_guard_once(self) -> dict:
        return self.journal.m07_offline_intent_summary()

    def review_m08_buy_position_admission_readonly(self, *,
            account_fingerprint: str, symbol: str,
            client_order_id: str) -> dict:
        """Original V2 position admission from existing M07 View Only ledger.

        Genuine account fills can establish a quoted cost-basis observation,
        never that this bot executed the BUY. M07's network hardlock has
        not produced a bot-authored submit receipt, so the sellable lot is
        ALWAYS empty. This call performs zero network requests.
        """
        self.settings.require_m01_safety()
        evidence = self.journal.review_m08_account_buy_position_admission_readonly(
            account_fingerprint=account_fingerprint,
            symbol=symbol,client_order_id=client_order_id,
        )
        intent = self.journal.m07_offline_intent_summary()
        return {
            **evidence,
            "original_m07_unknown_holds": intent["offline_unknown_holds"],
            "original_m07_unresolved_simulated_intents":
                intent["unresolved_simulated_intents"],
            "owned_position_manager_registered": False,
            "position_exit_decision": "BLOCKED_NO_BOT_OWNED_LOT",
            "order_sent": False,
            "execution_allowed": False,
        }

    def review_m08_holding_time_reference_offline(self, *,
            account_fingerprint: str, symbol: str, client_order_id: str,
            observed_epoch: int,
            pump_continuation_unverified: bool = False,
            trailing_protection_unverified: bool = False,
            market_source_fresh_unverified: bool = False,
            risk_review_clear_unverified: bool = False,
    ) -> dict:
        """Original M08-02: earliest scoped myTrades BUY fill -> holding time.

        Caller CANNOT supply a fake fill time. Only matching authenticated
        account myTrades rows in the original M07 journal supply the start.
        Not bot-owned until independently attested V2 order execution.
        The observed clock/flag values are unverified offline arguments.
        """
        self.settings.require_m01_safety()
        source = self.review_m08_buy_position_admission_readonly(
            account_fingerprint=account_fingerprint, symbol=symbol,
            client_order_id=client_order_id,
        )
        base = {
            "stage": "M08_ORIGINAL_HOLDING_TIME_SOURCE_OFFLINE",
            "status": "M08_HOLDING_TIME_BUY_FILL_TIMESTAMP_UNVERIFIED",
            "buy_source_status": source["status"],
            "trade_time_source": source["account_buy_fill_timestamp_source"],
            "holding_time_reference": None,
            "account_buy_fill_epoch_bot_owned": False,
            "bot_owned_position_verified": False,
            "time_exit_order_authorized": False,
            "sellable_bot_owned_base_qty": "0",
            "owner_order_final_verified": False,
            "order_sent": False,
            "execution_allowed": False,
        }
        first = source["account_first_buy_fill_epoch_reference"]
        last = source["account_last_buy_fill_epoch_reference"]
        if (source["account_buy_fills_and_fees_reconciled"] is not True
                or type(first) is not int or type(last) is not int):
            return base
        if type(observed_epoch) is not int or observed_epoch < last:
            raise ValueError("M08_OBSERVATION_PRECEDES_LAST_BUY_FILL")
        result = ScalpingPolicy().assess_position_holding_time(
            actual_buy_fill_epoch=first,
            observed_epoch=observed_epoch,
            pump_continuation_confirmed=pump_continuation_unverified,
            trailing_protection_active=trailing_protection_unverified,
            market_source_fresh=market_source_fresh_unverified,
            risk_review_clear=risk_review_clear_unverified,
        )
        journal_state = self.journal.m07_offline_intent_summary()
        return {
            **base,
            "status": "M08_ACCOUNT_BUY_HOLDING_TIME_REFERENCE_NOT_BOT_OWNED",
            "account_first_buy_fill_epoch_reference": first,
            "account_last_buy_fill_epoch_reference": last,
            "elapsed_seconds_from_first_buy_fill_reference":
                result.elapsed_seconds,
            "holding_phase_reference": result.phase,
            "risk_review_priority_reference": result.risk_review_priority,
            "next_review_interval_seconds_reference":
                result.review_interval_seconds,
            "pump_exception_scenario_only":
                result.pump_exception_observed,
            "observation_epoch_external_unverified": True,
            "market_risk_flags_scenario_only": True,
            "original_m07_unknown_holds":
                journal_state["offline_unknown_holds"],
        }

    def review_m08_net_tier_exit_reference_offline(self, *,
            account_fingerprint: str, symbol: str, client_order_id: str,
            sell_base_qty_reference: Decimal,
            expected_sell_gross_idr_reference: Decimal,
            estimated_sell_all_in_fee_idr_reference: Decimal,
            incremental_execution_slippage_idr_reference: Decimal,
    ) -> dict:
        """Original M08-01 T1/T2/T3 policy joined to real account BUY receipt.

        Uses source-bound BUY cost incl actual tax/fees in the SAME original
        M07 journal, not a 24h candle or owner-submitted BUY cost fixture.
        SELL price, SELL fee and slippage are *unverified* explicit inputs.
        Local synthetic M07 client ID and account fills are NOT proof of V2
        ownership; report can never reserve/sell any base or create a lot.
        """
        self.settings.require_m01_safety()
        source = self.review_m08_buy_position_admission_readonly(
            account_fingerprint=account_fingerprint, symbol=symbol,
            client_order_id=client_order_id,
        )
        if source["account_buy_fills_and_fees_reconciled"] is not True:
            return {
                "stage": "M08_ORIGINAL_NET_TIER_EXIT_OFFLINE",
                "status": "M08_NET_TIER_BUY_COST_SOURCE_UNAVAILABLE",
                "buy_source_status": source["status"],
                "net_exit_reference": None,
                "bot_owned_position_verified": False,
                "sellable_bot_owned_base_qty": "0",
                "order_sent": False,
                "execution_allowed": False,
            }
        outcome = ScalpingPolicy().assess_position_exit_targets(
            filled_buy_quantity=Decimal(
                source["account_buy_qty_reference"]),
            filled_buy_notional_idr=Decimal(
                source["account_buy_gross_idr_reference"]),
            allocated_buy_fees_idr=Decimal(
                source["account_buy_fees_including_tax_idr_reference"]),
            quantity_to_sell=sell_base_qty_reference,
            expected_sell_notional_idr=expected_sell_gross_idr_reference,
            estimated_sell_fees_idr=estimated_sell_all_in_fee_idr_reference,
            incremental_slippage_idr=incremental_execution_slippage_idr_reference,
        )
        unresolved = self.journal.m07_offline_intent_summary()
        return {
            "stage": "M08_ORIGINAL_NET_TIER_EXIT_OFFLINE",
            "status": "M08_ACCOUNT_COST_BASIS_TIER_MATH_REFERENCE_ONLY",
            "buy_source_status": source["status"],
            "sell_base_qty_reference": format(sell_base_qty_reference, "f"),
            "allocated_buy_cost_including_tax_idr_reference": format(
                outcome.allocated_cost_basis_idr,"f"),
            "expected_sell_net_proceeds_idr_reference": format(
                outcome.expected_net_proceeds_idr,"f"),
            "expected_sell_net_profit_idr_reference": format(
                outcome.expected_net_profit_idr,"f"),
            "expected_sell_net_roi_percent_reference": format(
                outcome.expected_net_roi_percent,"f"),
            "reached_net_tiers_reference": list(outcome.reached_tiers),
            "highest_reached_net_tier_reference": outcome.highest_reached_tier,
            "buy_cost_basis_from_account_mytrades": True,
            "sell_fee_and_slippage_exchange_verified": False,
            "position_change_24h_used": False,
            "original_m07_unknown_holds": unresolved["offline_unknown_holds"],
            "original_m07_unresolved_simulated_intents":
                unresolved["unresolved_simulated_intents"],
            "bot_owned_position_verified": False,
            "sellable_bot_owned_base_qty": "0",
            "tier_order_or_qty_reserved": False,
            "owner_m05_order_final_verified": False,
            "realized_pnl_recorded": False,
            "order_sent": False,
            "execution_allowed": False,
        }

    def review_m08_protective_exit_reference_offline(self, *,
            account_fingerprint: str, symbol: str, client_order_id: str,
            reference_buy_fill_epoch: int, observed_epoch: int,
            observed_mark_price_idr: Decimal,
            stop_loss_price_idr: Decimal,
            high_watermark_price_idr: Decimal,
            trailing_stop_price_idr: Decimal | None,
            quote_fresh_unverified: bool,
            pump_continuation_unverified: bool = False,
            risk_clear_unverified: bool = False,
    ) -> dict:
        """Original M08 owned-position admission -> protective policy preview.

        Fail closed when same original M07 journal lacks full account BUY
        fill+tax fee evidence. Even if account evidence is complete, it
        cannot promote an arbitrary filled account order or synthetic M07
        intent into a V2 bot-owned SELL lot or authorize a loss exit.
        """
        self.settings.require_m01_safety()
        source = self.review_m08_buy_position_admission_readonly(
            account_fingerprint=account_fingerprint, symbol=symbol,
            client_order_id=client_order_id,
        )
        if source["account_buy_fills_and_fees_reconciled"] is not True:
            return {
                "stage": "M08_ORIGINAL_PROTECTIVE_EXIT_OFFLINE",
                "status": "M08_PROTECTIVE_MISSING_ACCOUNT_BUY_FILLED_COST_SOURCE",
                "account_buy_source_status": source["status"],
                "protection_review": None,
                "bot_owned_position_verified": False,
                "protective_sell_quantity_authorized": "0",
                "owner_order_final_verified": False,
                "order_sent": False, "execution_allowed": False,
            }
        report = ScalpingPolicy().assess_protective_sell_reference_offline(
            account_buy_base_qty=Decimal(source["account_buy_qty_reference"]),
            account_buy_cost_basis_idr=Decimal(
                source["account_buy_cost_basis_idr_reference"]),
            reference_buy_fill_epoch=reference_buy_fill_epoch,
            now_epoch=observed_epoch,
            observed_mark_price_idr=observed_mark_price_idr,
            stop_loss_price_idr=stop_loss_price_idr,
            high_watermark_price_idr=high_watermark_price_idr,
            trailing_stop_price_idr=trailing_stop_price_idr,
            quote_fresh_unverified=quote_fresh_unverified,
            pump_continuation_unverified=pump_continuation_unverified,
            risk_clear_unverified=risk_clear_unverified,
        )
        original_intents = self.journal.m07_offline_intent_summary()
        return {
            "stage": "M08_ORIGINAL_PROTECTIVE_EXIT_OFFLINE",
            "status": "M08_PROTECTIVE_REVIEW_ACCOUNT_ONLY_NOT_BOT_OWNED",
            "account_buy_source_status": source["status"],
            "protective_trigger_reference": report.trigger,
            "stop_loss_breached_reference": report.stop_loss_breached_reference,
            "trailing_stop_breached_reference":
                report.trailing_stop_breached_reference,
            "time_stop_review_reference": report.time_stop_review_reference,
            "review_urgency": report.review_urgency,
            "position_age_seconds_reference":
                report.position_age_seconds_reference,
            "gross_unrealized_idr_reference": format(
                report.estimated_gross_unrealized_idr_reference,"f"),
            "gross_unrealized_roi_percent_reference": format(
                report.estimated_gross_unrealized_roi_percent_reference,"f"),
            "protective_loss_allowed_by_policy":
                report.protective_loss_allowed_by_policy,
            "source_quote_fresh_unverified": report.quote_fresh_unverified,
            "reference_buy_fill_epoch_independently_attested": False,
            "m07_unknown_holds_still_blocked":
                original_intents["offline_unknown_holds"],
            "bot_owned_position_verified": False,
            "protective_sell_quantity_authorized": "0",
            "owner_order_final_verified": False,
            "risk_executable_verified": False,
            "realized_pnl_recorded": False,
            "order_sent": False,"execution_allowed": False,
        }

    def review_m08_sell_preflight_offline_once(self, *,
            sell_draft: M07OrderDraft,
            source_buy_client_order_id: str,
            account_fingerprint: str,
            account_uid: int,
            local_m06_ledger_db: str,
            observed_epoch: int,
            expected_sell_notional_idr: Decimal,
            estimated_sell_fees_idr: Decimal,
            incremental_slippage_idr: Decimal,
            observed_mark_price_idr: Decimal,
            stop_loss_price_idr: Decimal,
            high_watermark_price_idr: Decimal,
            trailing_stop_price_idr: Decimal | None,
            quote_fresh_unverified: bool,
            pump_continuation_unverified: bool = False,
            risk_review_clear_unverified: bool = False,
    ) -> dict:
        """M08-05 one ORIGINAL V2 source→tier/protective→SELL guard chain.

        The supplied SELL draft is a REVIEW identity only, not an exchange
        order intent. No bot-owned BUY exists yet, so no SELL can execute.
        An atomic durable review in the SAME journal prevents conflicting
        replay/reuse of this client ID, including across restart. An
        exchange order/state change between read and write aborts the review.
        """
        self.settings.require_m01_safety()
        if (type(sell_draft) is not M07OrderDraft
                or sell_draft.side != "SELL"
                or sell_draft.order_type != "LIMIT"
                or sell_draft.quantity is None
                or type(observed_epoch) is not int
                or not 1 <= observed_epoch <= 9999999999
                or any(type(flag) is not bool for flag in (
                    quote_fresh_unverified,pump_continuation_unverified,
                    risk_review_clear_unverified))):
            raise ValueError("M08_SELL_PREFLIGHT_REVIEW_INPUT_INVALID")
        # Official adapter format validation, but NEVER prepare/dispatch an
        # order. Signed create/status/cancel remain hardlocked globally.
        params = sell_draft.parameters()
        quantity = sell_draft.quantity
        state = self.journal.review_m08_sell_account_liabilities_offline(
            account_fingerprint=account_fingerprint,
            symbol=sell_draft.symbol,
            source_buy_client_order_id=source_buy_client_order_id,
            sell_client_order_id=sell_draft.client_order_id,
        )
        owned = self.review_m07_sell_quantity_ownership_offline(
            local_m06_ledger_db=local_m06_ledger_db,
            account_uid=account_uid, draft=sell_draft,
        )
        buy = self.review_m08_buy_position_admission_readonly(
            account_fingerprint=account_fingerprint,
            symbol=sell_draft.symbol,
            client_order_id=source_buy_client_order_id,
        )
        tier = self.review_m08_net_tier_exit_reference_offline(
            account_fingerprint=account_fingerprint,
            symbol=sell_draft.symbol,
            client_order_id=source_buy_client_order_id,
            sell_base_qty_reference=quantity,
            expected_sell_gross_idr_reference=expected_sell_notional_idr,
            estimated_sell_all_in_fee_idr_reference=estimated_sell_fees_idr,
            incremental_execution_slippage_idr_reference=incremental_slippage_idr,
        )
        holding = self.review_m08_holding_time_reference_offline(
            account_fingerprint=account_fingerprint,
            symbol=sell_draft.symbol,
            client_order_id=source_buy_client_order_id,
            observed_epoch=observed_epoch,
            pump_continuation_unverified=pump_continuation_unverified,
            trailing_protection_unverified=trailing_stop_price_idr is not None,
            market_source_fresh_unverified=quote_fresh_unverified,
            risk_review_clear_unverified=risk_review_clear_unverified,
        )
        protective = None
        if holding["status"] == (
                "M08_ACCOUNT_BUY_HOLDING_TIME_REFERENCE_NOT_BOT_OWNED"):
            # CRITICAL: use genuine matched myTrades first BUY fill time,
            # NEVER a user-provided fake position start.
            protective = self.review_m08_protective_exit_reference_offline(
                account_fingerprint=account_fingerprint,
                symbol=sell_draft.symbol,
                client_order_id=source_buy_client_order_id,
                reference_buy_fill_epoch=holding[
                    "account_first_buy_fill_epoch_reference"],
                observed_epoch=observed_epoch,
                observed_mark_price_idr=observed_mark_price_idr,
                stop_loss_price_idr=stop_loss_price_idr,
                high_watermark_price_idr=high_watermark_price_idr,
                trailing_stop_price_idr=trailing_stop_price_idr,
                quote_fresh_unverified=quote_fresh_unverified,
                pump_continuation_unverified=pump_continuation_unverified,
                risk_clear_unverified=risk_review_clear_unverified,
            )
        trigger = (protective["protective_trigger_reference"]
                   if protective is not None else None)
        if trigger == "STALE_QUOTE_PROTECTION_NEEDS_FRESH_SOURCE":
            exit_hint = "RECHECK_STALE_REFERENCE_NO_ORDER"
        elif trigger in ("STOP_LOSS_REVIEW","TRAILING_STOP_REVIEW",
                         "TIME_STOP_URGENT_REVIEW"):
            exit_hint = "PROTECTIVE_RISK_REVIEW_ONLY"
        elif tier.get("highest_reached_net_tier_reference") is not None:
            exit_hint = "NET_TIER_REVIEW_ONLY"
        else:
            exit_hint = "MONITOR_OR_UNVERIFIED_ONLY"
        if state["global_m07_unknown_holds"]:
            reason = "M08_SELL_PREFLIGHT_UNKNOWN_HOLD_BLOCKED"
        elif state["sell_client_id_collision"]:
            reason = "M08_SELL_PREFLIGHT_CLIENT_ID_COLLISION"
        elif state["account_seen_open_sell_count"]:
            reason = "M08_SELL_PREFLIGHT_PARTIAL_OR_PENDING_SELL"
        elif buy["account_buy_fills_and_fees_reconciled"] is not True:
            reason = "M08_SELL_PREFLIGHT_BUY_SOURCE_UNVERIFIED"
        elif holding["holding_time_reference"] is None and (
                "elapsed_seconds_from_first_buy_fill_reference"
                not in holding):
            reason = "M08_SELL_PREFLIGHT_BUY_TIME_UNVERIFIED"
        else:
            reason = "M08_SELL_PREFLIGHT_BOT_OWNED_LOT_UNVERIFIED"
        scenario = {
            "uid": account_uid, "source_buy_client_order_id":
                source_buy_client_order_id,
            "local_m06_ledger_db": local_m06_ledger_db,
            "observed_epoch": observed_epoch,
            "expected_sell_notional_idr": format(
                expected_sell_notional_idr,"f"),
            "estimated_sell_fees_idr": format(estimated_sell_fees_idr,"f"),
            "incremental_slippage_idr": format(incremental_slippage_idr,"f"),
            "observed_mark_price_idr": format(observed_mark_price_idr,"f"),
            "stop_loss_price_idr": format(stop_loss_price_idr,"f"),
            "high_watermark_price_idr": format(
                high_watermark_price_idr,"f"),
            "trailing_stop_price_idr": (
                format(trailing_stop_price_idr,"f")
                if trailing_stop_price_idr is not None else None),
            "quote_fresh_unverified": quote_fresh_unverified,
            "pump_continuation_unverified": pump_continuation_unverified,
            "risk_review_clear_unverified": risk_review_clear_unverified,
            "liability_status": state["status"],
            "local_quantity_status": owned["status"],
            "local_net_qty_unverified": owned["local_net_qty_unverified"],
            "local_pending_sell_intents": owned["local_pending_sell_intents"],
            "buy_source_status": buy["status"],
            "tier_reference_status": tier["status"],
            "reached_tier": tier.get("highest_reached_net_tier_reference"),
            "protective_trigger": trigger,
            "holding_status": holding["status"],
            "exit_hint": exit_hint,"reason": reason,
        }
        draft_digest = hashlib.sha256(json.dumps(
            params,separators=(",",":"),ensure_ascii=True
        ).encode("ascii")).hexdigest()
        scenario_digest = hashlib.sha256(json.dumps(
            scenario,sort_keys=True,separators=(",",":"),ensure_ascii=True
        ).encode("ascii")).hexdigest()
        receipt = self.journal.record_m08_sell_preflight_denied_once(
            account_fingerprint=account_fingerprint,
            symbol=sell_draft.symbol,
            source_buy_client_order_id=source_buy_client_order_id,
            sell_client_order_id=sell_draft.client_order_id,
            draft_digest=draft_digest,scenario_digest=scenario_digest,
            exchange_state_digest=state["snapshot_digest"],
            observed_epoch=observed_epoch,
        )
        return {
            "stage": "M08_ORIGINAL_SELL_PREFLIGHT_OFFLINE",
            "status": reason,
            "exit_hint_reference": exit_hint,
            "tier_reference_status": tier["status"],
            "highest_net_tier_reference":
                tier.get("highest_reached_net_tier_reference"),
            "protective_trigger_reference": trigger,
            "holding_time_source_status": holding["status"],
            "owned_qty_guard_status": owned["status"],
            "locally_observed_buy_minus_sell_base_reference":
                owned["local_net_qty_unverified"],
            "local_pending_sell_intents": owned["local_pending_sell_intents"],
            "exchange_sell_viewonly_status": state["status"],
            "exchange_partial_sell_count_reference":
                state["account_seen_partial_sell_count"],
            "exchange_cancelled_sell_count_reference":
                state["account_seen_cancelled_sell_count"],
            "exchange_filled_sell_count_reference":
                state["account_seen_filled_sell_count"],
            "exchange_pending_sell_base_reference":
                state["account_seen_pending_sell_qty_reference"],
            "exchange_status_coverage_complete": False,
            "original_m07_unknown_holds": state["global_m07_unknown_holds"],
            "preflight_review_replay": receipt["replay"],
            "preflight_only_not_order_intent": True,
            "first_buy_fill_time_from_account_trades_not_bot": (
                holding["status"] ==
                "M08_ACCOUNT_BUY_HOLDING_TIME_REFERENCE_NOT_BOT_OWNED"),
            "bot_owned_buy_fill_verified": False,
            "verified_bot_owned_base_qty": "0",
            "maximum_executable_sell_base_qty": "0",
            "owner_order_specific_final_verified": False,
            "risk_executable_verified": False,
            "account_free_asset_verified": False,
            "sell_reservation_created": False,
            "unknown_order_released": False,
            "cancelled_order_qty_recredited": False,
            "retry_allowed": False,
            "realized_pnl_recorded": False,
            "order_sent": False,
            "execution_allowed": False,
        }

    def run_m09_original_shadow_mainpath_once(self, trace_id: str, *,
            market: IndodaxPublicMarket | None = None,
            ws_db_path: str | None = None,
            deep_limit: int = 20) -> dict:
        """Original M09-01 M03→G1/M04→M05→M07→M08, always NO ORDER.

        Invokes the SAME public bulk scanner, G1 source quality, M04
        three-role shadow and optional preexisting M05 advisory source.
        Original M07 adapter and M08 journal supply downstream hardlocks.
        Neither a fake Final, fresh private GET nor settled IDR is minted.
        """
        self.settings.require_m01_safety()
        if self._ai_role_caller is not None:
            raise ValueError("M09_SHADOW_PAID_OR_INJECTED_AI_FORBIDDEN")
        if type(trace_id) is not str or not trace_id.strip() or len(trace_id) > 64:
            raise ValueError("M09_ORIGINAL_TRACE_INVALID")
        if type(deep_limit) is not int or not 20 <= deep_limit <= 40:
            raise ValueError("M09_SHADOW_BULK_LIMIT_INVALID")
        cycle = self.run_bulk_deep_cycle(
            trace_id, market=market, ws_db_path=ws_db_path,
            min_volume_idr=Decimal("150000000"), deep_limit=deep_limit,
        )
        if (cycle.final_decision is not None
                or cycle.execution_allowed is not False
                or cycle.stage_disposition !=
                "ADVISORY_ONLY_NO_OWNER_DECISION"):
            raise ValueError("M09_ORIGINAL_BULK_AUTHORITY_CONFLICT")
        source = cycle.bulk if isinstance(cycle.bulk, dict) else None
        ai = cycle.ai_shadow if isinstance(cycle.ai_shadow, dict) else {}
        owner = (cycle.owner_advisory
                 if isinstance(cycle.owner_advisory, dict) else None)
        adapter = self.inspect_m07_official_order_adapter_once()
        if (adapter.get("network_allowed") is not False
                or adapter.get("execution_allowed") is not False):
            raise ValueError("M09_M07_HARDLOCK_INACTIVE")
        m07 = self.journal.m07_offline_intent_summary()
        m08 = self.journal.review_m09_original_m08_position_and_idr_readonly()
        # M09-02: NEVER trust booleans self-asserted by PUBLIC bulk or
        # model-shaped advisory as proof of an independent signed G1/Risk.
        if source is not None and (
                source.get("g1_verified") is True
                or source.get("execution_allowed") is True
                or source.get("trade_authorized") is True):
            raise ValueError("M09_PUBLIC_CANNOT_PROMOTE_G1_OR_EXECUTION")
        if (ai.get("execution_allowed") is True
                or ai.get("live_trading_authorized") is True
                or ai.get("final_decision") is not None):
            raise ValueError("M09_RISK_SHADOW_CANNOT_AUTHORIZE_FINAL")
        cohort = source.get("eligible_cohort") if source is not None else None
        cohort = cohort if isinstance(cohort, dict) else {}
        g1 = source.get("g1_source_admission") if source is not None else None
        if isinstance(g1, dict) and (
                g1.get("g1_receipt_issuance_allowed") is True
                or g1.get("execution_allowed") is True):
            raise ValueError("M09_PUBLIC_G1_ISSUER_NOT_ATTESTED")
        now = int(time.time())
        observed = source.get("screening_observed_epoch") if source else None
        minute = cohort.get("closed_1m_epoch")
        public_time_aligned = (
            type(observed) is int and type(minute) is int
            and 0 <= observed - minute <= 120
            and 0 <= now - observed <= 60
        )
        m05_mode = "M05_OWNER_LEDGER_NOT_CONNECTED"
        if self._owner_control is not None:
            owner_uid = self._owner_control.owner_user_id
            # Same ORIGINAL owner ledger, status only. No callback, update,
            # Telegram transport or implicit permission when mode=AUTO.
            snapshot = self._owner_control.status(
                user_id=owner_uid,chat_id=owner_uid,chat_type="private",
            )
            m05_mode = snapshot.get(
                "owner_mode","M05_OWNER_MODE_UNAVAILABLE")
        return {
            "stage": "M09_ORIGINAL_V2_SHADOW_END_TO_END",
            "status": "M09_SHADOW_CHAIN_NO_ORDER_NO_OWNER_FINAL",
            "m03_original_bulk_invoked": True,
            "m03_scan_reason": cycle.reason,
            "m03_public_coverage_status": (
                source.get("coverage_status") if source is not None else None),
            "m03_cohort_status": cohort.get("status"),
            "m03_provisional_watchlist_count": (
                len(cohort.get("provisional_watchlist_preview"))
                if isinstance(cohort.get("provisional_watchlist_preview"),list)
                else 0),
            "m04_original_g1_admission_observed": isinstance(g1,dict),
            "m09_original_public_candle_time_fresh_reference": public_time_aligned,
            "m09_public_failure_or_rate_blocked": (
                cycle.reason.startswith("PUBLIC_BULK_BLOCKED:")),
            "m09_public_rate_budget_ip_wide_verified": False,
            "m09_unattested_source_never_clears_risk": True,
            "m09_risk_veto_or_source_unavailable": (
                not ai or ai.get("status") != "PASS"
                or "RISK_VETO" in str(ai.get("reason",""))),
            "m09_m05_observed_mode_reference": m05_mode,
            "m09_auto_mode_cannot_issue_order_final": True,
            "m09_quote_fee_precision_balance_authenticated": False,
            "m09_original_m07_order_preview_authorized": False,
            "independent_g1_issuer_verified": False,
            "g1_source_not_promoted_from_public": True,
            "m04_original_three_role_shadow_invoked": True,
            "m04_expected_roles": ["MARKET","RISK","STRATEGY"],
            "m04_completed_role_count": (
                len(ai.get("completed_roles"))
                if isinstance(ai.get("completed_roles"),list) else 0),
            "m04_three_role_reason": ai.get("reason"),
            "m04_paid_ai_provider_called": False,
            "m04_role_veto_cannot_be_bypassed": True,
            "m05_original_advisory_status": (
                owner.get("status") if owner is not None
                else "M05_NO_BOUND_OWNER_ADVISORY"),
            "m05_order_specific_final_decision": None,
            "m05_original_telegram_transport_invoked": False,
            "m07_original_adapter_status": adapter.get("status"),
            "m07_hardlock_active": True,
            "m07_unknown_holds": m07["offline_unknown_holds"],
            "m07_order_intents_created_by_shadow": False,
            "m08_original_position_source_status": m08["status"],
            "m08_account_only_buy_receipts_observed":
                m08["account_only_buy_receipt_count"],
            "m08_account_only_sell_receipts_observed":
                m08["account_only_sell_receipt_count"],
            "m08_position_manager_bot_owned_verified": False,
            "m08_wallet_settled_idr_verified": False,
            "m08_realized_net_pnl_wib_verified": False,
            "paper_pnl_recorded_as_real_idr": False,
            "verified_spendable_capital_idr": False,
            "new_buy_order_or_sell_order_created": False,
            "owner_final_decision": None,
            "paid_network_allowed": False,
            "private_account_get_invoked": False,
            "live_trading_authorized": False,
            "order_sent": False,
            "execution_allowed": False,
        }

    def review_m09_paper_position_policy_offline(self, *, paper_id: str,
            observed_epoch: int, book_observed_epoch: int,
            tier_sell_qty: Decimal, bid_levels: tuple,
            best_ask: Decimal, taker_sell_fee_pct: Decimal,
            maker_sell_fee_pct: Decimal, taker_slippage_pct: Decimal) -> dict:
        """Original PAPER lot -> existing M08 Strategy A and holding clock.

        Review only: the supplied book and fee values are hypothetical,
        including maker prices. Never claim a resting maker order filled
        or publish a real BUY/SELL. Record a PAPER fill via existing SELL
        only when simulated fill evidence is supplied separately.
        """
        self.settings.require_m01_safety()
        position=self.journal.read_m09_open_paper_position_offline(
            paper_id=paper_id,observed_epoch=observed_epoch)
        if (type(book_observed_epoch) is not int
                or not position["buy_epoch"]<=book_observed_epoch<=observed_epoch):
            raise ValueError("M09_PAPER_BOOK_TIME_INVALID")
        result=self.review_strategy_a_nonlive_once(
            review_type="POSITION_EXIT",
            remaining_bot_qty_reference=position["remaining_qty"],
            remaining_cost_including_buy_fee_idr=position["remaining_cost_idr"],
            tier_sell_qty_reference=tier_sell_qty,
            bid_levels=bid_levels,best_ask=best_ask,
            taker_sell_fee_pct=taker_sell_fee_pct,
            maker_sell_fee_pct=maker_sell_fee_pct,
            taker_slippage_pct=taker_slippage_pct,
            book_age_seconds=observed_epoch-book_observed_epoch)
        time_review=ScalpingPolicy().assess_position_holding_time(
            actual_buy_fill_epoch=position["buy_epoch"],
            observed_epoch=observed_epoch,
            market_source_fresh=False)
        return {
            **self.journal._m09_paper_source_only(),
            "status":"M09_PAPER_POSITION_POLICY_REVIEW_ONLY",
            "paper_id":paper_id,"paper_symbol":position["symbol"],
            "paper_remaining_qty":format(position["remaining_qty"],"f"),
            "paper_remaining_cost_idr":format(position["remaining_cost_idr"],"f"),
            "paper_holding_seconds":time_review.elapsed_seconds,
            "paper_holding_phase":time_review.phase,
            "paper_time_review_urgency":time_review.risk_review_priority,
            "paper_strategy_a_reference":result,
            "paper_book_is_hypothetical":True,
            "paper_sell_fill_recorded":False,
        }

    def discover_m09_paper_entry_reference_once(self, *,
            now_epoch: int | None = None, source_db=None,
            require_m04_review: bool = False,
            m04_shadow_decision: dict | None = None) -> dict:
        """Reuse the original early WS3 radar, never a second scanner.

        This is a maker entry reference, not a simulated fill. Existing
        aggregate depth cannot prove our passive order got executed.
        """
        from .m05_radar_momentum_nonlive import (
            DEFAULT_DB, inspect_original_m03_ws3_watch_readonly,
        )
        self.settings.require_m01_safety()
        now=int(time.time()) if now_epoch is None else now_epoch
        if type(now) is not int or now<=0:
            raise ValueError("M09_PAPER_CLOCK_INVALID")
        base={
            **self.journal._m09_paper_source_only(),
            "status":"M09_PAPER_WAIT_FOR_ENTRY_SOURCE",
            "paper_entry_reference":None,"paper_buy_fill_recorded":False,
            "paper_source_g1_verified":False,"paper_ai_review_verified":False,
        }
        if self.journal.review_m09_paper_pnl_offline()[
                "paper_open_or_partial_positions"]!=0:
            return {**base,"status":"M09_PAPER_MONITOR_EXISTING_POSITION"}
        if type(require_m04_review) is not bool or (
                not require_m04_review and m04_shadow_decision is not None):
            raise ValueError("M09_PAPER_M04_MODE_INVALID")
        selection = None
        if require_m04_review:
            x = m04_shadow_decision
            if (not isinstance(x, dict)
                    or x.get("stage") != "M04_CANDIDATE_DECISION_LOOP_SHADOW"
                    or x.get("status") != "SELECTED_SYNTHETIC_REVIEW_ONLY"
                    or x.get("final_decision") is not None
                    or x.get("execution_allowed") is not False
                    or x.get("paper_fill_authorized") is not False):
                return {**base, "status":"M09_PAPER_M04_RESCAN_REQUIRED"}
            pair = x.get("selected_pair")
            digest = x.get("selected_evidence_digest_sha256")
            source_epoch = x.get("source_observed_epoch")
            decision_epoch = x.get("decision_observed_epoch")
            if (type(pair) is not str or not re.fullmatch(r"[a-z0-9]{2,20}_idr", pair)
                    or pair in ("floki_idr", "pepe_idr")
                    or type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or type(source_epoch) is not int or type(decision_epoch) is not int
                    or not 0 < source_epoch <= decision_epoch <= now
                    or now-source_epoch > 60):
                return {**base, "status":"M09_PAPER_M04_STALE_OR_INVALID_SELECTION"}
            rows = x.get("candidates")
            if (not isinstance(rows, list) or not 1 <= len(rows) <= 2
                    or any(not isinstance(row, dict) for row in rows)
                    or sum(row.get("pair") == pair for row in rows) != 1
                    or not any(row.get("pair") == pair
                               and row.get("evidence_digest_sha256") == digest
                               and row.get("disposition") == "QUALIFIED_SYNTHETIC_SCENARIO"
                               for row in rows)):
                return {**base, "status":"M09_PAPER_M04_INVALID_SELECTION"}
            # Explicitly synthetic caller-supplied reference, NOT verification
            # of real AI, signed provenance or account/order authority.
            selection = {"pair":pair, "evidence_digest_sha256":digest,
                         "source_observed_epoch":source_epoch,
                         "decision_observed_epoch":decision_epoch}
        path=Path(DEFAULT_DB if source_db is None else source_db).expanduser()
        if path.resolve()==Path(self.journal._db_path).resolve():
            return base
        report=inspect_original_m03_ws3_watch_readonly(
            db_path=path,now_epoch=now)
        for candidate in report.get("micro_prealert_candidates_top40",[]):
            if selection is not None and candidate.get("pair") != selection["pair"]:
                continue
            micro=candidate.get("microstructure_observation",{})
            if selection is not None and (
                    type(candidate.get("observed_epoch")) is not int
                    or candidate["observed_epoch"] < selection["source_observed_epoch"]
                    or type(micro.get("orderbook_locally_observed_epoch")) is not int
                    or micro["orderbook_locally_observed_epoch"] < selection["decision_observed_epoch"]):
                continue
            if (micro.get("orderbook_snapshot_observed") is not True
                    or micro.get("source_malformed") is not False
                    or micro.get("trade_activity_observed") is not True
                    or micro.get("trade_source_gap_or_truncation") is not False
                    or not 0<=now-micro["orderbook_locally_observed_epoch"]<=30
                    or not 0<=now-candidate["observed_epoch"]<=90
                    or Decimal(candidate["volume_24h_idr"])<Decimal("400000000")):
                continue
            bid=Decimal(micro["best_bid_idr"])
            assessment=self.review_strategy_a_nonlive_once(
                review_type="BUY_MAKER",proposed_buy_price=bid,
                best_bid=bid,best_ask=Decimal(micro["best_ask_idr"]))
            if assessment.get("maker_buy_eligible") is not True:
                continue
            return {**base,"status":"M09_PAPER_MAKER_ENTRY_REFERENCE_NO_FILL",
                    "paper_entry_reference":{
                        "pair":candidate["pair"],
                        "observed_epoch":micro["orderbook_locally_observed_epoch"],
                        "maker_limit_price_idr":str(bid),
                        "rolling_24h_idr":candidate["volume_24h_idr"],
                        "observed_rise_pct":candidate["observed_rise_pct"],
                        "maker_fill_verified":False,
                        "entry_policy":assessment,
                        **({"m04_synthetic_selection_reference":selection}
                           if selection is not None else {})}}
        return base

    def _read_m09_observed_book(self, *, pair: str, now: int,
            not_before: int, source_db=None):
        """Reuse original M03 minute book read, with no connection to exchange."""
        from .m05_radar_momentum_nonlive import (
            DEFAULT_DB, _owned_private_regular, _ws3_micro_observation,
        )
        path=Path(DEFAULT_DB if source_db is None else source_db).expanduser()
        if (not _owned_private_regular(path)
                or path.resolve()==Path(self.journal._db_path).resolve()):
            return None
        try:
            with sqlite3.connect(path.absolute().as_uri()+"?mode=ro",
                                 uri=True,timeout=2) as con:
                con.execute("PRAGMA query_only=ON")
                row=con.execute(
                    "SELECT pair,minute_epoch,observed_local_epoch,"
                    "trade_frames,source_buy_count,source_sell_count,"
                    "source_buy_idr,source_sell_idr,last_trade_epoch,"
                    "trade_offset,trade_gap_observed,trade_truncated,"
                    "book_offset,book_local_epoch,best_bid_idr,best_ask_idr,"
                    "spread_bps,bid_depth_top5_idr,ask_depth_top5_idr,"
                    "book_imbalance_top5_ratio,source_malformed "
                    "FROM m03_ws_microstructure_watch_minute "
                    "WHERE pair=? ORDER BY minute_epoch DESC LIMIT 1",
                    (pair,),
                ).fetchone()
        except (sqlite3.Error,OSError,ValueError):
            return None
        if (not row or type(row[1]) is not int or row[1]%60
                or type(row[2]) is not int or type(row[13]) is not int
                or not not_before<=row[13]<=row[2]<=now
                or not 0<=now-row[13]<=30
                or not row[1]<=row[2]<row[1]+60):
            return None
        obs=_ws3_micro_observation(row,now=now,source_minute=row[1])
        if (obs.get("orderbook_snapshot_observed") is not True
                or obs.get("source_malformed") is not False):
            return None
        return obs

    def review_m09_paper_observed_market_once(self, *, paper_id: str,
            sell_fee_pct: Decimal, slippage_pct: Decimal,
            now_epoch: int | None = None, source_db=None) -> dict:
        """One source-backed PAPER mark review; no market requests or fills.

        M03 stores top-of-book prices and aggregate depth, not a price ladder
        or maker queue/fill receipt. A bid mark times quantity is an estimate,
        not a realizable VWAP. Never synthesize a bid level or a PAPER SELL.
        """
        from .m08_strategy_a_nonlive import STOP_NET_PCT, MIN_PROFIT_IDR
        self.settings.require_m01_safety()
        now=int(time.time()) if now_epoch is None else now_epoch
        lot=self.journal.read_m09_open_paper_position_offline(
            paper_id=paper_id,observed_epoch=now)
        for value in (sell_fee_pct,slippage_pct):
            if (type(value) is not Decimal or not value.is_finite()
                    or not 0<=value<5):
                raise ValueError("M09_PAPER_MODELED_FRICTION_INVALID")
        holding=ScalpingPolicy().assess_position_holding_time(
            actual_buy_fill_epoch=lot["buy_epoch"],observed_epoch=now)
        pair=lot["symbol"][:-3].lower()+"_idr"
        base={
            **self.journal._m09_paper_source_only(),
            "status":"M09_PAPER_WAIT_FOR_FRESH_OBSERVED_BOOK",
            "paper_id":paper_id,"paper_symbol":lot["symbol"],
            "paper_remaining_qty":format(lot["remaining_qty"],"f"),
            "paper_remaining_cost_idr":format(lot["remaining_cost_idr"],"f"),
            "paper_holding_phase":holding.phase,
            "paper_holding_seconds":holding.elapsed_seconds,
            "paper_market_book_observed":False,
            "paper_fill_depth_verified":False,
            "paper_sell_fill_recorded":False,
            "paper_source_g1_verified":False,
            "paper_net_mark_profit_idr":None,
            "paper_net_mark_roi_percent":None,
            "paper_exit_reference":None,
        }
        obs=self._read_m09_observed_book(
            pair=pair,now=now,not_before=lot["buy_epoch"],source_db=source_db)
        if obs is None:
            return base
        bid=Decimal(obs["best_bid_idr"])
        gross=lot["remaining_qty"]*bid
        fee=gross*sell_fee_pct/100
        slip=gross*slippage_pct/100
        target=ScalpingPolicy().assess_position_exit_targets(
            filled_buy_quantity=lot["remaining_qty"],
            filled_buy_notional_idr=lot["remaining_cost_idr"],
            allocated_buy_fees_idr=Decimal(0),
            quantity_to_sell=lot["remaining_qty"],
            expected_sell_notional_idr=gross,
            estimated_sell_fees_idr=fee,
            incremental_slippage_idr=slip)
        if target.expected_net_roi_percent<=STOP_NET_PCT:
            trigger="STOP_LOSS_MARK_REVIEW_NO_FILL"
        elif holding.phase in ("TIME_STOP_PROTECTIVE_REVIEW",
                               "EXTENDED_POSITION_RISK_REVIEW"):
            trigger="TIME_STOP_MARK_REVIEW_NO_FILL"
        elif (target.highest_reached_tier is not None
                and target.expected_net_profit_idr>=MIN_PROFIT_IDR):
            trigger=target.highest_reached_tier+"_NET_MARK_REVIEW_NO_FILL"
        else:
            trigger="HOLD_NO_QUALIFIED_NET_MARK_EXIT"
        return {
            **base,"status":"M09_PAPER_OBSERVED_BOOK_MARK_REVIEW",
            "paper_market_book_observed":True,
            "paper_book_epoch":obs["orderbook_locally_observed_epoch"],
            "paper_book_age_seconds":now-obs["orderbook_locally_observed_epoch"],
            "paper_best_bid_idr":str(bid),
            "paper_best_ask_idr":obs["best_ask_idr"],
            "paper_bid_depth_top5_idr":obs["bid_depth_top5_idr"],
            "paper_net_mark_profit_idr":str(target.expected_net_profit_idr),
            "paper_net_mark_roi_percent":str(target.expected_net_roi_percent),
            "paper_exit_reference":trigger,
            "paper_friction_is_modeled_not_account_fee":True,
            "paper_mark_is_not_executable_vwap":True,
        }

    def step_m09_managed_paper_cycle_once(self, *, seed_idr: Decimal,
            buy_fee_pct: Decimal, maker_sell_fee_pct: Decimal,
            taker_sell_fee_pct: Decimal, slippage_pct: Decimal,
            depth_fraction_pct: Decimal, trailing_pct: Decimal,
            now_epoch: int | None = None, source_db=None,
            require_m04_review: bool = False,
            m04_shadow_decision: dict | None = None) -> dict:
        """One atomic virtual order/position step on the existing PAPER journal.

        Explicit quote-cross + aggregate-depth haircut MODEL, not a fill
        simulator calibrated against real orders. Pending maker order waits
        for a later quote crossing the limit; partial BUY cancels remainder.
        Partial SELL retains the position. No exchange or AI transport.
        """
        from decimal import ROUND_DOWN
        from .m05_radar_momentum_nonlive import DEFAULT_DB
        from .m08_strategy_a_nonlive import STOP_NET_PCT, MIN_PROFIT_IDR
        from .journal import DecisionConflictError
        self.settings.require_m01_safety()
        now=int(time.time()) if now_epoch is None else now_epoch
        self.journal._m09_paper_epoch(now)
        source=Path(DEFAULT_DB if source_db is None else source_db).expanduser()
        if source.resolve()==Path(self.journal._db_path).resolve():
            raise ValueError("M09_PAPER_SOURCE_WALLET_COLLISION")
        amounts=(seed_idr,buy_fee_pct,maker_sell_fee_pct,taker_sell_fee_pct,
                 slippage_pct,depth_fraction_pct,trailing_pct)
        if (any(type(v) is not Decimal or not v.is_finite() for v in amounts)
                or not 0<seed_idr<Decimal("1e18")
                or any(not 0<=v<5 for v in amounts[1:5])
                or not 0<depth_fraction_pct<=10 or not 0<trailing_pct<=10):
            raise ValueError("M09_PAPER_AUTO_CONFIG_INVALID")
        if type(require_m04_review) is not bool or (
                not require_m04_review and m04_shadow_decision is not None):
            raise ValueError("M09_PAPER_M04_MODE_INVALID")
        config=[str(v.normalize()) for v in amounts]+[str(source.absolute())]
        # Preserve default legacy PAPER config identity. Opt-in AI-shadow mode
        # cannot be toggled to radar-only after wallet creation.
        if require_m04_review:
            config.append("M04_SYNTHETIC_SELECTION_REQUIRED")
        quantum=Decimal("0.00000001")
        def size_at_most(amount,price):
            return (amount/price).quantize(quantum,rounding=ROUND_DOWN)
        def book(pair,after):
            return self._read_m09_observed_book(
                pair=pair,now=now,not_before=after+1,source_db=source)
        with self.journal._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            # Lazy creation ONLY in an isolated managed PAPER wallet.
            con.execute("CREATE TABLE IF NOT EXISTS m09_paper_auto_state ("
                        "id INTEGER PRIMARY KEY CHECK(id=1), state_json TEXT NOT NULL)")
            row=con.execute("SELECT state_json FROM m09_paper_auto_state WHERE id=1").fetchone()
            buys=con.execute("SELECT paper_id,buy_qty,buy_price_idr,buy_all_in_fee_idr "
                             "FROM m09_paper_simulated_buys").fetchall()
            sells=con.execute("SELECT paper_id,sell_qty,sell_price_idr,sell_all_in_fee_idr,"
                              "incremental_slippage_idr FROM m09_paper_simulated_sells").fetchall()
            if row is None:
                if buys or sells:
                    raise DecisionConflictError("M09_PAPER_AUTO_REQUIRES_EMPTY_WALLET")
                state=dict(config=config,position=None,pending=None,last_tick=0,
                           last_book=0,high_roi=None,protective=False)
            else:
                state=json.loads(row[0])
                if state.get("config")!=config:
                    raise DecisionConflictError("M09_PAPER_AUTO_CONFIG_CHANGED")
            if type(state["last_tick"]) is not int or now<state["last_tick"]:
                raise DecisionConflictError("M09_PAPER_AUTO_CLOCK_REVERSED")
            cash=seed_idr
            remaining={}
            for pid,q,p,f in buys:
                qty,price,fee=map(Decimal,(q,p,f))
                if (not pid.startswith("paper_auto_") or any(not x.is_finite()
                        for x in (qty,price,fee)) or qty<=0 or price<=0 or fee<0):
                    raise DecisionConflictError("M09_PAPER_AUTO_RECEIPT_INVALID")
                cash-=qty*price+fee
                remaining[pid]=qty
            for pid,q,p,f,sl in sells:
                qty,price,fee,slip=map(Decimal,(q,p,f,sl))
                if (pid not in remaining or any(not x.is_finite()
                        for x in (qty,price,fee,slip)) or qty<=0 or price<=0
                        or fee<0 or slip<0 or fee+slip>qty*price):
                    raise DecisionConflictError("M09_PAPER_AUTO_RECEIPT_INVALID")
                remaining[pid]-=qty
                cash+=qty*price-fee-slip
            open_ids=[pid for pid,q in remaining.items() if q>0]
            pid=state["position"]
            if (cash<0 or any(q<0 for q in remaining.values())
                    or open_ids!=([pid] if pid else [])):
                raise DecisionConflictError("M09_PAPER_AUTO_WALLET_MISMATCH")
            status="M09_PAPER_AUTO_WAIT"
            receipt=None
            note=None
            pending=state["pending"]
            if now==state["last_tick"]:
                status="M09_PAPER_AUTO_TICK_REPLAY"
            elif pid is None:
                if pending is not None:
                    if pending["side"]!="BUY":
                        raise DecisionConflictError("M09_PAPER_AUTO_PENDING_MISMATCH")
                    if now-pending["created"]>=300:
                        state["pending"]=None
                        status="M09_PAPER_AUTO_BUY_EXPIRED"
                    else:
                        obs=book(pending["pair"],pending["created"])
                        limit=Decimal(pending["limit"])
                        if obs and Decimal(obs["best_ask_idr"])<=limit:
                            # Haircut aggregate ask depth, never infer L1 size.
                            cap=Decimal(obs["ask_depth_top5_idr"])*depth_fraction_pct/100
                            desired=size_at_most(cash,limit*(1+buy_fee_pct/100))
                            qty=min(desired,size_at_most(cap,limit))
                            if qty>0:
                                fee=qty*limit*buy_fee_pct/100
                                stamp=obs["orderbook_locally_observed_epoch"]
                                pid="paper_auto_"+str(pending["created"])
                                receipt=self.journal.record_m09_paper_buy_offline(
                                    paper_id=pid,symbol=pending["pair"][:-4].upper()+"IDR",
                                    buy_qty=qty,buy_price_idr=limit,buy_all_in_fee_idr=fee,
                                    buy_epoch=stamp,_paper_con=con)
                                cash-=qty*limit+fee
                                state.update(position=pid,pending=None,last_book=stamp,
                                             high_roi=None,protective=False)
                                status=("M09_PAPER_AUTO_BUY_PARTIAL_REMAINDER_CANCELLED"
                                        if qty<desired else "M09_PAPER_AUTO_BUY_FILLED")
                        if receipt is None:
                            status="M09_PAPER_AUTO_BUY_PENDING"
                elif cash>0:
                    opts = ({"require_m04_review": True,
                             "m04_shadow_decision": m04_shadow_decision}
                            if require_m04_review else {})
                    found=self.discover_m09_paper_entry_reference_once(
                        now_epoch=now,source_db=source,**opts)
                    entry=found.get("paper_entry_reference")
                    selection=entry.get("m04_synthetic_selection_reference") if entry else None
                    new_selection = (not require_m04_review or (
                        isinstance(selection, dict)
                        and selection["source_observed_epoch"]>state.get("last_m04_source_epoch",0)))
                    if entry and entry["observed_epoch"]>state["last_book"] and new_selection:
                        state["pending"]=dict(side="BUY",pair=entry["pair"],
                                              limit=entry["maker_limit_price_idr"],
                                              created=now)
                        if require_m04_review:
                            state["last_m04_source_epoch"]=selection["source_observed_epoch"]
                            state["pending"]["m04_synthetic_selection_reference"]=selection
                        status="M09_PAPER_AUTO_BUY_PENDING"
                    else:
                        status=("M09_PAPER_AUTO_M04_RESCAN_REQUIRED" if require_m04_review
                                else "M09_PAPER_AUTO_DISCOVER_WAIT")
            else:
                # Existing read helper uses a separate committed snapshot;
                # this transaction serializes writes before policy review.
                lot=self.journal.read_m09_open_paper_position_offline(
                    paper_id=pid,observed_epoch=now)
                pair=lot["symbol"][:-3].lower()+"_idr"
                obs=book(pair,max(state["last_book"],lot["buy_epoch"]))
                status="M09_PAPER_AUTO_POSITION_WAIT_BOOK"
                if obs:
                    stamp=obs["orderbook_locally_observed_epoch"]
                    state["last_book"]=stamp
                    qty=lot["remaining_qty"]
                    cost=lot["remaining_cost_idr"]
                    bid,ask=Decimal(obs["best_bid_idr"]),Decimal(obs["best_ask_idr"])
                    taker_factor=1-(taker_sell_fee_pct+slippage_pct)/100
                    maker_factor=1-maker_sell_fee_pct/100
                    full_profit=qty*bid*taker_factor-cost
                    roi=100*full_profit/cost
                    high=roi if state["high_roi"] is None else max(roi,Decimal(state["high_roi"]))
                    state["high_roi"]=str(high)
                    phase=ScalpingPolicy().assess_position_holding_time(
                        actual_buy_fill_epoch=lot["buy_epoch"],observed_epoch=now).phase
                    if roi<=STOP_NET_PCT:
                        state["protective"]=True
                        note="STOP_LOSS"
                    elif high>=2 and high-roi>=trailing_pct:
                        state["protective"]=True
                        note="TRAILING_STOP"
                    elif phase in ("TIME_STOP_PROTECTIVE_REVIEW","EXTENDED_POSITION_RISK_REVIEW"):
                        state["protective"]=True
                        note="TIME_STOP"
                    if state["protective"]:
                        note=note or "PROTECTIVE_LATCH"
                        state["pending"]=None
                    spread=Decimal(obs["spread_bps"])
                    price=None
                    is_maker=False
                    if state["protective"] or (roi>=8 and full_profit>=MIN_PROFIT_IDR
                                               and spread<=50):
                        price=bid
                        if not state["protective"]:
                            note="T3" if roi>=11 else "T2"
                        state["pending"]=None
                    elif pending and pending["side"]=="SELL" and stamp>pending["created"] \
                            and bid>=Decimal(pending["limit"]):
                        price=Decimal(pending["limit"])
                        is_maker=True
                        note="T1"
                    elif pending is None and (qty*ask*maker_factor-cost>=MIN_PROFIT_IDR
                                               and qty*ask*maker_factor>=cost*Decimal("1.02")):
                        state["pending"]=dict(side="SELL",pair=pair,limit=str(ask),created=now)
                        status="M09_PAPER_AUTO_T1_PENDING"
                    else:
                        status="M09_PAPER_AUTO_HOLD"
                    if price is not None:
                        cap=Decimal(obs["bid_depth_top5_idr"])*depth_fraction_pct/100
                        exit_qty=min(qty,size_at_most(cap,price))
                        gross=exit_qty*price
                        fee=gross*(maker_sell_fee_pct if is_maker else taker_sell_fee_pct)/100
                        slip=Decimal(0) if is_maker else gross*slippage_pct/100
                        profit=gross-fee-slip-cost*exit_qty/qty
                        minimum_roi=Decimal(11 if note=="T3" else 8 if note=="T2" else 2)
                        if exit_qty>0 and (state["protective"] or (
                                profit>=MIN_PROFIT_IDR
                                and profit>=cost*exit_qty/qty*minimum_roi/100)):
                            receipt=self.journal.record_m09_paper_sell_offline(
                                paper_id=pid,paper_exit_id="pexit_auto_"+str(stamp),
                                sell_qty=exit_qty,sell_price_idr=price,
                                sell_all_in_fee_idr=fee,incremental_slippage_idr=slip,
                                sell_epoch=stamp,_paper_con=con)
                            cash+=gross-fee-slip
                            closed=receipt["status"]=="M09_PAPER_CLOSED"
                            if closed:
                                state.update(position=None,pending=None,high_roi=None,protective=False)
                            status="M09_PAPER_AUTO_SELL_CLOSED" if closed else "M09_PAPER_AUTO_SELL_PARTIAL"
                        else:
                            status="M09_PAPER_AUTO_EXIT_WAIT_DEPTH_OR_NET_TARGET"
            state["last_tick"]=now
            con.execute("INSERT INTO m09_paper_auto_state(id,state_json) VALUES(1,?) "
                        "ON CONFLICT(id) DO UPDATE SET state_json=excluded.state_json",
                        (json.dumps(state,sort_keys=True),))
            return {
                **self.journal._m09_paper_source_only(),"status":status,
                "paper_virtual_free_idr":str(cash),
                "paper_position_id":state["position"],
                "paper_pending_order":state["pending"],
                "paper_modeled_receipt":receipt,"paper_exit_reason":note,
                "paper_fill_model":"QUOTE_CROSS_AND_AGGREGATE_DEPTH_HAIRCUT",
                "paper_fill_depth_verified":False,
                "paper_ai_review_verified":False,"paper_source_g1_verified":False,
                "paper_m04_synthetic_selection_required":require_m04_review,
                "paper_quantity_precision_is_modeled":True,
            }

    def run_m09_original_paper_cycle_offline_once(self, *,
            action: str, details: dict,
            include_m11_risk_halt_readonly_reference: bool = False) -> dict:
        """M09-04: original V2 journal only, never exchange execution.

        Details are user-entered hypothetical PAPER quantities, prices
        and fees. No M03/G1/RISK/M05 proposal or real account IDR is
        minted. Uses distinct paper-only namespace in original
        DecisionJournal; projected PAPER realization is a SIMULATED
        SELL receipt only, not wallet-confirmed realized PnL.
        """
        self.settings.require_m01_safety()
        if type(details) is not dict or type(action) is not str:
            raise ValueError("M09_PAPER_SCENARIO_INVALID")
        if type(include_m11_risk_halt_readonly_reference) is not bool:
            raise ValueError("M09_PAPER_RISK_REFERENCE_FLAG_INVALID")
        if include_m11_risk_halt_readonly_reference:
            # This extra view is explicitly SUMMARY-only and only when no
            # owner/provider/verifier is connected. Never an M05 Final or
            # an executable M07/M11 Risk gate.
            if action != "SUMMARY":
                raise ValueError("M09_PAPER_RISK_REFERENCE_SUMMARY_ONLY")
            if (self._ai_role_caller is not None
                    or self._source_verifier is not None
                    or self._owner_control is not None
                    or self._m03_owner_fee_snapshot is not None):
                raise ValueError("M09_PAPER_RISK_REFERENCE_EXTERNAL_SOURCE_DENIED")
            # Same original SQLite file, no second ledger: both connections
            # use the original DecisionJournal class, M11 opened query_only.
            m11_readonly = DecisionJournal.open_m11_offline_fixture_readonly(
                self.journal._db_path)
            if m11_readonly._db_path != self.journal._db_path:
                raise ValueError("M09_PAPER_RISK_REFERENCE_NOT_SAME_JOURNAL")
        if action == "BUY":
            result=self.journal.record_m09_paper_buy_offline(**details)
        elif action == "SELL":
            result=self.journal.record_m09_paper_sell_offline(**details)
        elif action == "REVIEW":
            result=self.review_m09_paper_position_policy_offline(**details)
        elif action == "OBSERVE":
            result=self.review_m09_paper_observed_market_once(**details)
        elif action == "DISCOVER":
            result=self.discover_m09_paper_entry_reference_once(**details)
        elif action == "AUTO_STEP":
            result=self.step_m09_managed_paper_cycle_once(**details)
        elif action == "SUMMARY":
            result=self.journal.review_m09_paper_pnl_offline(**details)
        else:
            raise ValueError("M09_PAPER_ACTION_INVALID")
        actual=self.journal.review_m09_original_m08_position_and_idr_readonly()
        if (actual["wallet_settled_idr_verified"] is not False
                or actual["realized_net_pnl_wib_verified"] is not False
                or result["execution_allowed"] is not False
                or result["real_exchange_realized_pnl_idr"] is not None):
            raise ValueError("M09_PAPER_EXCHANGE_REALITY_CROSSING")
        report = {
            "stage":"M09_ORIGINAL_PAPER_ACCOUNTING_OFFLINE",
            "action":action,"paper":result,
            "m08_original_settlement_source":actual["status"],
            "paper_can_be_spendable_capital":False,
            "paper_may_publish_position_settled_idr":False,
            "owner_order_final":None,
            "paid_ai_called":False,
            "private_exchange_get_called":False,
            "order_sent":False,
            "execution_allowed":False,
        }
        if include_m11_risk_halt_readonly_reference:
            # DO NOT pass PAPER net PnL, PAPER capital or SELL timestamps
            # into M11 exposure/loss. There is no exchange/owner receipt.
            # A fixed synthetic identity/time is ONLY an untrusted fixture
            # lookup, never authentication of a human owner.
            shadow = DailyScalpingOrchestrator(
                self.settings, m11_readonly,
            ).review_m11_offline_shadow_safety_once(
                owner_user_id=1, observed_epoch=1793781000,
            )
            if (shadow["status"] != "REJECT_SHADOW_OFFLINE_ONLY"
                    or shadow["risk_halt_review"]["realized_pnl_settlement_verified"] is not False
                    or shadow["risk_halt_review"]["scenario_daily_loss_below_limit"] is not None
                    or shadow["owner_final_decision"] is not None
                    or shadow["m11_live_release_authorized"] is not False):
                raise ValueError("M09_PAPER_RISK_REFERENCE_AUTHORITY_DENIED")
            report.update({
                "m11_risk_halt_readonly_reference": shadow,
                "m11_risk_reference_owner_identity": "FIXTURE_ONLY_UNAUTHENTICATED",
                "m11_risk_reference_real_account_loss_status":
                    "UNKNOWN_NO_EXCHANGE_SETTLED_IDR_EVIDENCE",
                "m11_paper_pnl_used_as_realized_loss": False,
                "m11_live_release_authorized": False,
            })
        return report

    def run_m09_original_recovery_shadow_once(self, trace_id: str, *,
            account_fingerprint: str, symbol: str,
            source_buy_client_order_id: str,
            sell_client_order_id: str,
            requested_settlement_event_id: str,
            market: IndodaxPublicMarket | None = None,
            ws_db_path: str | None = None,
            deep_limit: int = 20) -> dict:
        """M09-03: SAME original M09 shadow plus M07/M08 recovery facts.

        Account-only PARTIAL/CANCELLED/FILLED, synthetic UNKNOWN and a
        client-ID collision cannot release bot inventory or create IDR.
        There is NO authoritative M08-06 settled-IDR publisher. Therefore
        DO NOT call claim_m08_post_sell_rediscovery_once or rescan as a
        post-SELL action, even if a synthetic future-event table row exists.
        A normal M09 PUBLIC scan is still allowed as a separate, non-order
        research step, with fresh trace ID and G1/owner hardlocked.
        """
        self.settings.require_m01_safety()
        if (type(requested_settlement_event_id) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{1,36}",
                                requested_settlement_event_id) is None):
            raise ValueError("M09_RECOVERY_SETTLEMENT_EVENT_ID_INVALID")
        before=self.journal.review_m08_sell_account_liabilities_offline(
            account_fingerprint=account_fingerprint,
            symbol=symbol,
            source_buy_client_order_id=source_buy_client_order_id,
            sell_client_order_id=sell_client_order_id,
        )
        guard_before=self.journal.m07_offline_intent_summary()
        shadow=self.run_m09_original_shadow_mainpath_once(
            trace_id,market=market,ws_db_path=ws_db_path,
            deep_limit=deep_limit,
        )
        after=self.journal.review_m08_sell_account_liabilities_offline(
            account_fingerprint=account_fingerprint,
            symbol=symbol,
            source_buy_client_order_id=source_buy_client_order_id,
            sell_client_order_id=sell_client_order_id,
        )
        guard_after=self.journal.m07_offline_intent_summary()
        # If a different process modifies order/UNKNOWN evidence between
        # admission and delivery, the diagnostic cannot claim stable state.
        if (before["snapshot_digest"] != after["snapshot_digest"]
                or guard_before != guard_after):
            raise ValueError("M09_RECOVERY_JOURNAL_CHANGED_DURING_SHADOW")
        m08=self.journal.review_m09_original_m08_position_and_idr_readonly()
        if after["global_m07_unknown_holds"]:
            reason="M09_RECOVERY_UNKNOWN_HOLD_BLOCKED"
        elif after["sell_client_id_collision"]:
            reason="M09_RECOVERY_DUPLICATE_ID_BLOCKED"
        elif after["account_seen_partial_sell_count"]:
            reason="M09_RECOVERY_PARTIAL_SELL_UNSETTLED"
        elif after["account_seen_open_sell_count"]:
            reason="M09_RECOVERY_OPEN_SELL_UNSETTLED"
        elif after["account_seen_cancelled_sell_count"]:
            reason="M09_RECOVERY_CANCELLED_NO_RELEASE"
        elif after["account_seen_filled_sell_count"]:
            reason="M09_RECOVERY_ACCOUNT_SELL_NOT_SETTLED_IDR"
        else:
            reason="M09_RECOVERY_ACCOUNT_ORDER_COVERAGE_UNVERIFIED"
        return {
            "stage": "M09_ORIGINAL_RECOVERY_TO_REDISCOVERY_SHADOW",
            "status": reason,
            "original_m09_public_shadow_invoked": True,
            "original_m09_source_reason": shadow["m03_scan_reason"],
            "original_m09_owner_final": None,
            "original_m09_hardlocked": shadow["m07_hardlock_active"],
            "m07_unknown_holds": guard_after["offline_unknown_holds"],
            "m07_readonly_recovery_partial":
                guard_after["viewonly_recovery_partial_status_observed"],
            "m07_readonly_recovery_terminal":
                guard_after["viewonly_recovery_terminal_status_observed"],
            "m07_outstanding_synthetic_intents":
                guard_after["unresolved_simulated_intents"],
            "m08_account_seen_partial_sells":
                after["account_seen_partial_sell_count"],
            "m08_account_seen_cancelled_sells":
                after["account_seen_cancelled_sell_count"],
            "m08_account_seen_filled_sells":
                after["account_seen_filled_sell_count"],
            "m08_account_seen_pending_base_reference":
                after["account_seen_pending_sell_qty_reference"],
            "m08_duplicate_sell_client_id":
                after["sell_client_id_collision"],
            "m08_status_all_exchange_orders_covered": False,
            "m08_future_settlement_rows_untrusted":
                m08["future_event_db_rows_untrusted"],
            "m08_genuine_settlement_issuer_exists": False,
            "post_sell_rediscovery_eligible": False,
            "post_sell_scanner_invoked": False,
            "original_post_sell_event_claimed": False,
            "prior_pair_automatically_rebought": False,
            "m07_unknown_hold_released": False,
            "sell_unsettled_inventory_recredited": False,
            "exchange_sell_bot_owned_verified": False,
            "exchange_wallet_idr_settled_verified": False,
            "realized_pnl_recorded": False,
            "spendable_idr_authorized": False,
            "order_intent_created_by_recovery": False,
            "signed_order_sent": False,
            "execution_allowed": False,
        }

    def review_m09_original_fault_preflight_offline(self, *,
            draft: M07OrderDraft, pair_rules: dict,
            best_bid_idr: Decimal, best_ask_idr: Decimal,
            free_idr: Decimal, locked_idr: Decimal,
            floki_rebuy_idr: Decimal, pepe_rebuy_idr: Decimal,
            commitments_not_locked_idr: Decimal,
            buy_all_in_fee_bps: Decimal, sell_all_in_fee_bps: Decimal,
            max_spread_bps: Decimal,
            public_candidate: dict | None = None,
            account_snapshot: dict | None = None,
            owner_advisory_proposal_id: str | None = None,
            source_digest: str | None = None,
            closed_1m_epoch: int | None = None,
            now_epoch: int) -> dict:
        """M09-02 original M07 LIMIT and last-moment fault-chain check.

        ALL inputs are scenario/read-only references, NOT independently
        authenticated exchange facts or a G1 source. Calls the SAME M07
        preview and last-moment adapter already reachable in V2.
        Possible aligned price/math, owner advisory button or account free
        balance can NEVER become order-specific M05 Final or a BUY intent.
        """
        self.settings.require_m01_safety()
        try:
            limit=self.assess_m07_limit_buy_preflight_offline(
                draft=draft,pair_rules=pair_rules,
                best_bid_idr=best_bid_idr,best_ask_idr=best_ask_idr,
                free_idr=free_idr,locked_idr=locked_idr,
                floki_rebuy_idr=floki_rebuy_idr,
                pepe_rebuy_idr=pepe_rebuy_idr,
                commitments_not_locked_idr=commitments_not_locked_idr,
                buy_all_in_fee_bps=buy_all_in_fee_bps,
                sell_all_in_fee_bps=sell_all_in_fee_bps,
                max_spread_bps=max_spread_bps,
            )
        except (M07PreflightBlocked,ValueError) as exc:
            code=str(exc)
            if not code.startswith("M07_PREFLIGHT_"):
                code="M09_SCENARIO_INPUT_INVALID"
            return {
                "stage":"M09_ORIGINAL_FAULT_PREFLIGHT_NONEXECUTING",
                "status":"M09_LIMIT_PREVIEW_FAIL_CLOSED",
                "preview_reason":code,
                "m07_limit_math_reference_passed":False,
                "m07_last_moment_source_bound":False,
                "m05_order_specific_final_verified":False,
                "m07_order_intent_created":False,
                "order_sent":False,"execution_allowed":False,
            }
        last=self.review_m07_last_moment_gate_offline(
            limit_preview=limit,
            public_candidate=public_candidate,
            account_snapshot=account_snapshot,
            owner_advisory_proposal_id=owner_advisory_proposal_id,
            source_digest=source_digest,
            closed_1m_epoch=closed_1m_epoch,
            now_epoch=now_epoch,
        )
        return {
            "stage":"M09_ORIGINAL_FAULT_PREFLIGHT_NONEXECUTING",
            "status":"M09_LAST_MOMENT_UNVERIFIED_NO_ORDER",
            "preview_reason":limit.status,
            "m07_limit_math_reference_passed":
                limit.checks_passed_unverified,
            "m07_last_moment_source_bound":
                last["public_same_candidate_fresh_observed"],
            "m07_fresh_spendable_capital_verified":False,
            "m07_rate_egress_all_peers_verified":False,
            "m07_unresolved_or_unknown_hold":
                last["m07_unresolved_or_unknown_local_intent"],
            "m05_owner_advisory_review_pass_reference":
                last["m05_same_source_advisory_review_pass"],
            "m05_order_specific_final_verified":False,
            "m07_missing_requirements":last["missing_requirements"],
            "m07_order_intent_created":False,
            "order_sent":False,"execution_allowed":False,
        }

    def review_m08_original_sell_settlement_admission_offline_once(
            self, *, account_fingerprint: str, symbol: str,
            source_buy_client_order_id: str, sell_client_order_id: str,
            observed_epoch: int) -> dict:
        """Original V2 orchestrator → SAME original journal, read-only.

        Account-only fee arithmetic can be inspected, but no synthetic or
        View Only record is allowed to become a real settled-IDR issuer.
        """
        self.settings.require_m01_safety()
        result = self.journal.review_m08_sell_settlement_issuer_admission_offline(
            account_fingerprint=account_fingerprint, symbol=symbol,
            source_buy_client_order_id=source_buy_client_order_id,
            sell_client_order_id=sell_client_order_id,
            observed_epoch=observed_epoch,
        )
        if (result["status"] !=
                "M08_06_BLOCK_UNVERIFIED_BOT_ORIGIN_AND_IDR_SETTLEMENT"
                or result["settlement_event_issued"] is not False
                or result["canonical_m08_settlement_issuer_connected"] is not False
                or result["realized_wib_net_idr_verified"] is not False
                or result["owner_final_decision"] is not None
                or result["execution_allowed"] is not False
                or result["order_sent"] is not False):
            raise ValueError("M08_SETTLEMENT_REVIEW_AUTHORITY_DENIED")
        return result

    def run_m08_post_sell_rediscovery_once(self, *,
            settlement_event_id: str, scan_trace_id: str,
            observed_epoch: int,
            market: IndodaxPublicMarket | None = None,
            deep_limit: int = 20) -> dict:
        """M08-07 one-time settlement handoff to SAME M03 public scanner.

        SOURCE CONTRACT: M08-06 (not implemented/authorized yet) must
        first emit a genuine reconciled bot-owned POSITION_SETTLED_IDR
        in THIS DecisionJournal. Account-only fills or hypothetical
        local PnL cannot create it. Scanning uses NEW trace/candles,
        NO priority/exclusion for previous symbol, NEVER a previous
        order/owner Final or an automatic BUY. Atomic claim is consumed
        before scanning so replay, timeout and restart cannot churn.
        """
        self.settings.require_m01_safety()
        if type(deep_limit) is not int or not 20 <= deep_limit <= 40:
            raise ValueError("M08_POST_SELL_SCAN_DEEP_LIMIT_INVALID")
        claim = self.journal.claim_m08_post_sell_rediscovery_once(
            event_id=settlement_event_id, scan_trace_id=scan_trace_id,
            observed_epoch=observed_epoch,
        )
        if not claim["rediscovery_claimed"]:
            return {
                "stage": "M08_ORIGINAL_POST_SELL_REDISCOVERY",
                "status": claim["status"],
                "settled_event_claimed": False,
                "original_bulk_scanner_invoked": False,
                "previous_pair_preferred_or_blacklisted": False,
                "new_buy_proposal_created": False,
                "prior_owner_final_reused": False,
                "new_idr_spendable_confirmed": False,
                "new_buy_order_or_reservation_sent": False,
                "order_sent": False,
                "execution_allowed": False,
            }
        # This is precisely the original scanner used by --bulk-deep-once.
        # No token from the previous sale is passed, filtered, or preferred.
        # Market and BTC evidence must be newly fetched by the same source
        # and G1/M04/Risk/Final gates remain unverified.
        outcome = self.run_bulk_deep_cycle(
            scan_trace_id, market=market,
            min_volume_idr=Decimal("150000000"),
            deep_limit=deep_limit,
        )
        return {
            "stage": "M08_ORIGINAL_POST_SELL_REDISCOVERY",
            "status": "M08_SETTLED_EVENT_CONSUMED_ORIGINAL_SCANNER_OBSERVED",
            "settled_event_claimed": True,
            "original_bulk_scanner_invoked": True,
            "scanner_trace_id": scan_trace_id,
            "scanner_reason": outcome.reason,
            "scanner_stage_disposition": outcome.stage_disposition,
            "scanner_execution_allowed": outcome.execution_allowed,
            "previous_pair_preferred_or_blacklisted": False,
            "same_pair_reentry_requires_fresh_proposal": True,
            "new_buy_proposal_created": False,
            "prior_owner_final_reused": False,
            "new_idr_spendable_confirmed": False,
            "new_buy_order_or_reservation_sent": False,
            "source_claim_does_not_authorize_trade": True,
            "order_sent": False,
            "execution_allowed": False,
        }

    def review_m06_account(self, *, observation: AccountObservation | None = None,
                           now_epoch: int) -> M06AccountDecision:
        """One original orchestrator, local offline account diagnostic only."""
        self.settings.require_m01_safety()
        return review_unverified_account(observation, now_epoch=now_epoch)

    def inspect_m06_offline_private_fixtures(self, *, account_payload,
            open_orders_payload, orders_payload, fills_payload,
            symbol: str, now_epoch: int) -> dict:
        """Original orchestrator parser path, untrusted fixtures, NO network."""
        self.settings.require_m01_safety()
        if type(now_epoch) is not int or now_epoch <= 0:
            raise ValueError("M06_REVIEW_EPOCH_INVALID")
        account = parse_account(account_payload)
        open_orders = parse_open_orders(open_orders_payload, expected_symbol=symbol)
        orders = parse_orders(orders_payload, expected_symbol=symbol)
        fills = parse_fills(fills_payload, expected_symbol=symbol)
        return {
            "status": "REJECT",
            "reason": "M06_PRIVATE_FIXTURE_UNTRUSTED_OR_INCOMPLETE",
            "observed_uid_present": account.uid > 0,
            "free_idr_unverified": str(account.free_idr),
            "locked_idr_unverified": str(account.locked_idr),
            "open_orders_seen": len(open_orders),
            "historical_orders_seen": len(orders),
            "fills_seen": len(fills),
            "account_authenticated": False,
            "orders_complete": False,
            "fills_complete": False,
            "bot_ownership_verified": False,
            "cross_bot_coexistence_verified": False,
            "execution_allowed": False,
        }

    def review_m06_local_intent_ownership(self, *, db_path: str,
            account_key: str, order, fills) -> OwnershipReview:
        """One original V2 orchestrator: persisted local evidence, never authenticated."""
        self.settings.require_m01_safety()
        return M06OwnershipLedger(db_path).observe(
            account_key=account_key, order=order, fills=fills)

    def review_m07_sell_quantity_ownership_offline(self, *,
            local_m06_ledger_db: str, account_uid: int,
            draft: M07OrderDraft) -> dict:
        """Original V2 M07-05: READ ONLY guard before any hypothetical SELL.

        Uses existing M06 local receipt ledger plus same DecisionJournal
        original intent state. A wallet holding or synthetic/unattributed
        fill can NEVER be presented as an executable bot-owned SELL lot.
        No order transport, order reservation, or token ownership upgrade.
        """
        self.settings.require_m01_safety()
        if (type(draft) is not M07OrderDraft or draft.side != "SELL"
                or draft.quantity is None):
            raise ValueError("M07_SELL_OWNERSHIP_LIMIT_DRAFT_INVALID")
        draft.parameters()
        reviewed = M06OwnershipLedger.inspect_m07_sell_quantity_readonly(
            db_path=local_m06_ledger_db, account_uid=account_uid,
            symbol=draft.symbol[:-3].lower()+"_idr",
            requested_base=draft.quantity,
        )
        guard = self.journal.m07_offline_intent_summary()
        return {
            **reviewed,
            "stage": "M07_ORIGINAL_SELL_OWNERSHIP_LAST_MOMENT_GUARD",
            "original_journal_unresolved_intents": guard[
                "unresolved_simulated_intents"],
            "original_journal_unknown_holds": guard["offline_unknown_holds"],
            "original_journal_recovery_reference_count": guard[
                "viewonly_recovery_references_not_bot_owned"],
            "owned_position_manager_actual_fills_connected": False,
            "risk_executable_veto_cleared": False,
            "owner_order_specific_final_verified": False,
            "sell_reservation_created": False,
            "order_sent": False,
            "execution_allowed": False,
        }

    def protect_m06_manual_holding_floor_offline(self, *, db_path: str,
            account_key: str, asset: str,
            minimum_qty: Decimal) -> ProtectedRebuyReview:
        """Original V2 caller protects manual FLOKI/PEPE local floor."""
        self.settings.require_m01_safety()
        return M06OwnershipLedger(db_path).protect_manual_asset_floor(
            account_key=account_key, asset=asset, minimum_qty=minimum_qty,
        )

    def review_m06_protected_rebuy_offline(self, *, db_path: str,
            account_key: str) -> ProtectedRebuyReview:
        """Same M06 ownership database; local gross SELL proceeds stay locked."""
        self.settings.require_m01_safety()
        return M06OwnershipLedger(db_path).review_protected_rebuy(
            account_key=account_key,
        )

    def review_m06_idr_funding_route_offline(
            self, *, db_path: str, account_key: str, account,
            funding_source: str, buy_asset: str, requested_idr: Decimal,
            commitments_not_locked_idr: Decimal,
            local_shadow_commitments_idr: Decimal,
    ) -> IDRFundingRouteReview:
        """Original V2 M06 ledger + source-split funding route, PREVIEW only.

        MAIN recycles any available general IDR; special SELL proceeds
        remain exclusively earmarked for repurchasing their SAME asset.
        """
        self.settings.require_m01_safety()
        from .m06_private_payloads import AccountBalances
        if type(account) is not AccountBalances:
            raise ValueError("M06_IDR_ACCOUNT_INPUT_INVALID")
        if (type(commitments_not_locked_idr) is not Decimal
                or type(local_shadow_commitments_idr) is not Decimal
                or not commitments_not_locked_idr.is_finite()
                or not local_shadow_commitments_idr.is_finite()
                or commitments_not_locked_idr < 0
                or local_shadow_commitments_idr < 0):
            raise ValueError("M06_IDR_COMMITMENTS_INVALID")
        rebuy = self.review_m06_protected_rebuy_offline(
            db_path=db_path, account_key=account_key,
        )
        return ScalpingPolicy().review_idr_buy_funding_route(
            free_idr=account.free_idr, locked_idr=account.locked_idr,
            floki_rebuy_idr=rebuy.floki_idr_local_protected,
            pepe_rebuy_idr=rebuy.pepe_idr_local_protected,
            commitments_not_locked_idr=(
                commitments_not_locked_idr + local_shadow_commitments_idr
            ),
            funding_source=funding_source, buy_asset=buy_asset,
            requested_idr=requested_idr,
        )

    def reconcile_m06_capital_with_local_rebuy_offline(self, *,
            db_path: str, account_key: str, account, open_orders,
            unlocked_commitments_idr: Decimal,
            local_shadow_idr: Decimal) -> CapitalReconciliation:
        """Original V2 capital calculator fed by local rebuy ledger, never LIVE.

        A local-only ledger cannot prove exchange-wide coverage, prior sales
        or external commitments. Always force all_pairs complete=False.
        """
        self.settings.require_m01_safety()
        rebuy = self.review_m06_protected_rebuy_offline(
            db_path=db_path, account_key=account_key,
        )
        return reconcile_unverified_capital(
            account=account, open_orders=open_orders,
            rebuy_idr=rebuy.total_idr_local_protected,
            unlocked_commitments_idr=unlocked_commitments_idr,
            local_shadow_idr=local_shadow_idr,
            all_pairs_open_orders_complete=False,
        )

    def reconcile_m06_capital_offline(self, *, account, open_orders,
            rebuy_idr: Decimal, unlocked_commitments_idr: Decimal,
            local_shadow_idr: Decimal,
            all_pairs_open_orders_complete: bool = False) -> CapitalReconciliation:
        """One original orchestrator boundary; NEVER authenticated account authority."""
        self.settings.require_m01_safety()
        return reconcile_unverified_capital(
            account=account, open_orders=open_orders, rebuy_idr=rebuy_idr,
            unlocked_commitments_idr=unlocked_commitments_idr,
            local_shadow_idr=local_shadow_idr,
            all_pairs_open_orders_complete=all_pairs_open_orders_complete,
        )

    def inspect_m06_private_readonly_once(self, *, approval: M06ScopedApproval,
            budget: M06RequestBudget, api_key: str,
            secret_key: str,
            local_ownership_db: str | None = None) -> M06PrivateProbeResult:
        """Original orchestrator; explicit M06 GO, four GETs, no trade authority."""
        self.settings.require_m01_safety()
        return run_scoped_private_probe(
            approval=approval, budget=budget,
            api_key=api_key, secret_key=secret_key,
            local_ownership_db=local_ownership_db,
        )

    def store_m06_scoped_special_sell_observation_offline(
            self, *, db_path: str, account_uid: int, symbol: str,
            orders: tuple, fills: tuple,
    ) -> dict:
        """Original V2 ledger LOCAL source hold, never private GET/order.

        The caller must already possess a scoped View Only window; this
        offline method does not attest it and cannot release capital.
        Unlike bot-owned fills, manual/external sells have NO V2 intent.
        """
        self.settings.require_m01_safety()
        # Constructor of the existing M06 ledger initializes tables. Guard
        # BEFORE construction, otherwise a missing file would be created
        # and a shared/symlink path might be mutated prematurely.
        if type(db_path) is not str or not db_path.startswith("/"):
            raise ValueError("M06_EXTERNAL_LEDGER_ABSOLUTE_PATH_REQUIRED")
        p = Path(db_path)
        try:
            directory = p.parent.lstat()
            file = p.lstat()
        except OSError:
            raise ValueError("M06_EXTERNAL_EXISTING_LEDGER_REQUIRED") from None
        if not (stat.S_ISDIR(directory.st_mode)
                and stat.S_ISREG(file.st_mode)
                and directory.st_uid == os.getuid()
                and file.st_uid == os.getuid()
                and directory.st_mode & 0o077 == 0
                and file.st_mode & 0o077 == 0):
            raise ValueError("M06_EXTERNAL_EXISTING_LEDGER_INSECURE")
        report = M06OwnershipLedger(
            db_path,
        ).observe_scoped_external_special_sells_offline(
            account_uid=account_uid, symbol=symbol,
            orders=orders, fills=fills,
        )
        return {
            "stage": "M06_SCOPED_EXTERNAL_SELL_LOCAL_OBSERVATION",
            "status": "LOCAL_SCOPED_RECEIPT_HOLD_ONLY",
            "floki_gross_proceeds_local_hold_idr": str(
                report.floki_idr_local_protected),
            "pepe_gross_proceeds_local_hold_idr": str(
                report.pepe_idr_local_protected),
            "distinct_local_sell_fills": report.distinct_sell_fills,
            "locally_observed_external_special_sell_fills":
                report.locally_observed_external_special_sell_fills,
            "all_time_sale_history_verified": False,
            "all_rebuy_consumption_verified": False,
            "peer_liabilities_verified": False,
            "spendable_idr_verified": False,
            "execution_allowed": False,
            "order_sent": False,
        }

    def store_m06_same_token_rebuy_buy_observation_offline(
            self, *, db_path: str, account_uid: int,
            symbol: str, orders: tuple, fills: tuple) -> dict:
        """Original V2 offline BUY receipt evidence, never reserve release.

        Same existing M06 UID-scoped SQLite, not a new ledger. This does
        NOT run a private GET, not an M05 order permission and never
        decreases any previous protected FLOKI/PEPE SELL proceeds.
        """
        self.settings.require_m01_safety()
        # Preserve the secure existing-file check BEFORE ledger constructor,
        # which initializes legacy schema. Avoid implicit database creation.
        if type(db_path) is not str or not db_path.startswith("/"):
            raise ValueError("M06_REBUY_BUY_EXISTING_DB_PATH_REQUIRED")
        p = Path(db_path)
        try:
            d=p.parent.lstat()
            f=p.lstat()
        except OSError:
            raise ValueError("M06_REBUY_BUY_EXISTING_DB_REQUIRED") from None
        if (not stat.S_ISDIR(d.st_mode) or d.st_uid != os.getuid()
                or d.st_mode & 0o077
                or not stat.S_ISREG(f.st_mode) or f.st_uid != os.getuid()
                or f.st_mode & 0o077):
            raise ValueError("M06_REBUY_BUY_DB_INSECURE")
        result = M06OwnershipLedger(db_path).observe_scoped_same_token_rebuy_buy_offline(
            account_uid=account_uid,symbol=symbol,orders=orders,fills=fills)
        return {
            "stage":"M06_SAME_TOKEN_REBUY_BUY_OFFLINE",
            "status":"OBSERVED_LOCAL_BUY_FILL_NOT_SETTLED",
            "floki_sell_proceeds_protected_idr":str(
                result.floki_idr_local_protected),
            "pepe_sell_proceeds_protected_idr":str(
                result.pepe_idr_local_protected),
            "floki_rebuy_buy_observed_idr":str(
                result.floki_rebuy_buy_observed_outflow_idr),
            "pepe_rebuy_buy_observed_idr":str(
                result.pepe_rebuy_buy_observed_outflow_idr),
            "locally_observed_rebuy_fill_count":
                result.local_rebuy_buy_observation_count,
            "rebuy_consumption_settled_verified":False,
            "all_time_special_sale_history_verified":False,
            "all_peer_liabilities_verified":False,
            "spendable_idr_verified":False,
            "execution_allowed":False,
            "order_sent":False,
        }

    def review_m06_special_source_receipts_offline(
            self, *, db_path: str, account_uid: int,
            symbol: str, orders: tuple, fills: tuple) -> dict:
        """Original orchestrator -> existing M06 RO UID-scoped ledger.

        This accepts bounded parsed test/source records only; caller cannot
        attest that origin, history, settlement or money is spendable.
        No private GET, ledger write, or real order.
        """
        self.settings.require_m01_safety()
        return M06OwnershipLedger.review_scoped_special_receipts_readonly_offline(
            db_path=db_path, account_uid=account_uid,
            symbol=symbol,orders=orders,fills=fills,
        ).safe_summary()

    def inspect_m06_idr_budget_readonly_once(
            self, *, api_key: str, secret_key: str,
            local_ownership_db: str | None = None) -> dict:
        """Original V2: 2 signed owner View Only GETs + existing local ledger.

        Observed real free/locked IDR and known local rebuy protection, never
        LIVE sizing or account-wide special sale provenance.
        """
        self.settings.require_m01_safety()
        return inspect_idr_budget_readonly_once(
            api_key=api_key, secret_key=secret_key,
            local_ownership_db=local_ownership_db,
        )

    def inspect_m06_account_coverage_once(self, *,
            approval: M06CoverageApproval, budget: M06RequestBudget,
            api_key: str, secret_key: str,
            local_ownership_db: str | None = None) -> M06CoverageResult:
        """Original V2 caller, single bounded READ ONLY, never a trade."""
        self.settings.require_m01_safety()
        return run_coverage_readonly_once(
            approval=approval, budget=budget, api_key=api_key,
            secret_key=secret_key, local_ownership_db=local_ownership_db,
        )

    def inspect_m06_account_diagnostic_once(self, *,
            approval: M06ScopedApproval, budget: M06RequestBudget,
            api_key: str, secret_key: str) -> M06AccountDiagnosticResult:
        """Original V2 orchestrator, ONE owner-approved account GET only."""
        self.settings.require_m01_safety()
        return run_account_diagnostic_readonly_once(
            approval=approval, budget=budget,
            api_key=api_key, secret_key=secret_key,
        )

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
        return cycle

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
        # Same original observed REST quote and bounded top-book depth. An
        # illustrative fixed ticket is NOT owner spendable IDR; the per-user
        # all-in fee remains unknown. No extra REST or provider call.
        cost = observe_public_quote_friction(
            observation, owner_fee_snapshot=self._m03_owner_fee_snapshot,
        )
        # One extra deterministic M07 check from the EXACT observed M03
        # candidate, original /api/pairs fields, and same friction source.
        # The public-only gate never reads account IDR, M05 decision or
        # private keys and cannot fabricate an executable LIMIT draft.
        m07 = assess_m07_same_candidate_public_boundary(
            candidate_pair=candidate.pair, observation=observation,
            observed_friction=cost,
        )
        return replace(
            candidate, wall_temporal_evidence=wall,
            public_quote_friction_diagnostic=cost,
            m07_same_candidate_public_preflight=m07,
        )

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
            # Missing BTC WS is a recorded source-quality warning, not
            # permission for M03/M04 to issue a Final REJECT for the owner.
            # No WS evidence is fabricated; any actual order remains disabled.
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
        # M03/M04 observations must not claim that the owner rejected a
        # proposal. This path has no executable proposal or owner callback yet.
        # Three-role provider permissions stay unchanged; no invented AI data.
        ai_shadow = ThreeRoleShadow(
            caller=self._ai_role_caller,
            source_verifier=self._source_verifier,
            review_claim=self.journal.claim_m04_candidate_evidence_once,
        ).run(trace_id, result)
        # Best-effort retrospective logging; never reclassify an AI result
        # or stop the original scanner if the history store is unavailable.
        if self._ai_role_caller is not None:
            try:
                ai_shadow["historical_reviews_recorded"] = self.journal.record_m04_history_reviews(
                    ai_shadow=ai_shadow,
                    source_epoch=result.get("screening_observed_epoch"),
                    decision_epoch=int(time.time()),
                )
            except (sqlite3.Error, OSError, ValueError, TypeError):
                ai_shadow["historical_reviews_recorded"] = None
        self.journal.record_stage_observation(
            trace_id, "M03_M04_PRE_FINAL", reason,
        )
        owner_advisory = None
        if self._owner_control is not None:
            # Single original M03/M04 bulk evidence. No extra REST/AI call,
            # no phantom size/price or executable owner permission.
            from .m05_market_advisory import queue_scanner_owner_advisory
            owner_advisory = queue_scanner_owner_advisory(
                bulk=result, ai_shadow=ai_shadow,
                owner=self._owner_control, now_epoch=int(time.time()),
            )
        return CycleResult(
            trace_id, None, reason, False,
            None, None, None, None, result, ai_shadow,
            stage_disposition="ADVISORY_ONLY_NO_OWNER_DECISION",
            owner_advisory=owner_advisory,
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
        reconnect_until_recovered: bool = False,
    ) -> CycleResult:
        """One original PUBLIC service batch; optional transient transport recovery."""
        try:
            report = supervised_public_ws_batch(
                db_path, pair=pair, sessions=sessions, seconds=seconds,
                pause_seconds=pause_seconds, status_file=status_file,
                **({"reconnect_until_recovered": True}
                   if reconnect_until_recovered else {}),
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
