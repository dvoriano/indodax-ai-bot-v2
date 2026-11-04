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
from .scanner import DailyPairScanner, rank_scanner_with_early_momentum
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
from .m07_main_idr_sizing import assess_m07_owner_main_idr_sizing_offline
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
            requested_ticket_ceiling_idr: Decimal,
            new_floki_sell_rebuy_hold_idr: Decimal,
            new_pepe_sell_rebuy_hold_idr: Decimal,
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
            include_m11_source_observation: bool = False) -> dict:
        """One ORIGINAL M03 bulk -> fresh M06 View Only -> M07 sizing.

        The M03 public scanner and M06 signed account reader are called here,
        not replayed from owner JSON. Funding inputs cover ONLY the owner's
        ticket cap and explicitly known NEW special sale holds / commitments.
        No AI provider, Telegram decision, order transport, or LIVE permission.
        """
        self.settings.require_m01_safety()
        if type(include_m11_source_observation) is not bool:
            raise ValueError("M11_M07_OBSERVATION_FLAG_INVALID")
        required = {
            "requested_ticket_ceiling_idr",
            "new_floki_sell_rebuy_hold_idr",
            "new_pepe_sell_rebuy_hold_idr",
            "commitments_not_locked_idr",
            "max_spread_bps",
        }
        if type(funding) is not dict or set(funding) != required:
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
            now_epoch: int) -> dict:
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
        )
        if result.get("accepted") is True and (
                result.get("execution_allowed") is not False
                or result.get("order_sent") is not False
                or result.get("owner_order_execution_authorized") is not False):
            raise ValueError("M07_MANUAL_PREVIEW_UNEXPECTED_AUTHORITY")
        return result

    def review_m07_manual_owner_sender_nonlive_once(
            self, *, owner_user_id: int, preview_id: str,
            account_fingerprint: str, draft: M07OrderDraft,
            now_epoch: int, mock_result: dict | None = None,
            mock_result_provided: bool = False) -> dict:
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
            fake = OfficialV2OrderAdapter.inspect_nonlive_post_result_shape(
                draft=draft,response=mock_result,
                submitted_at_ms=now_epoch*1000,
            )
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
        ).run(trace_id, result)
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
