"""CLI for non-live M01 foundation; no exchange connectivity."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import re
import sqlite3
import stat
import sys
import time
import os
from os import environ
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from .config import ConfigurationError, Settings
from .journal import DecisionJournal, DecisionConflictError
from .m03_egress_admission import M03SharedEgressAdmission, M03EgressAdmissionError
from .m03_ws_summary_live_watch import run_m03_ws3_summary_watch_once
from .public_market import IndodaxPublicMarket
from .m07_order_adapter import M07OrderDraft, M07OfflineStatusObservation
from .m07_limit_preflight import M07PreflightBlocked
from .m06_scoped_https import _owner_private_file
from .m07_private_order_readonly import M07ReadOnlyError
from .m06_owner_coverage import _existing_v2_credentials
from .m06_scoped_https import read_view_only_credentials
from .orchestrator import DailyScalpingOrchestrator
from .deep_analysis import OwnerEnteredAllInFeeSnapshot
from .m06_scoped_https import (M06RequestBudget, M06ScopeDenied, read_approval, read_view_only_credentials)
from .m06_account_coverage import read_coverage_approval, COVERAGE_ID
from .m06_account_coverage import read_account_diagnostic_approval, ACCOUNT_DIAG_ID
from .owner_control import OwnerDecisionLedger
from .telegram_service_tick import _private_file as _existing_private_owner_file
from .telegram_bot_api import TelegramBotApiClient, TelegramOwnerPoller, TelegramTransportError
from .telegram_readonly_preflight import inspect_telegram_readonly
from .telegram_owner_outbound import send_owner_safe_status_once
from .telegram_dedicated_inbound import (
    LEGACY_CAIPITI_BOT_ID, dedicated_owner_poll_once,
)
from .public_ws_health import public_ws_health
from .public_ws_continuous import supervised_public_ws_continuous
from .m11_04_release_risk_offline import review_m11_release_risk_offline
from .public_ws_unit_status import inspect_public_ws_systemd_unit
from .sumopod_provider import SumopodChatAdapter, SumopodProviderError, inspect_provider_configuration_offline
from .m04_owner_config import owner_provider_source
from .m10_07_original_mainpath_readonly import inspect_m10_07_original_mainpath_readonly


def _m03_compact_from_original(outcome) -> dict:
    """Only the original no-owner-Final DEEP20 outcome can be summarized."""
    if (outcome.final_decision is not None
            or outcome.stage_disposition != "ADVISORY_ONLY_NO_OWNER_DECISION"
            or outcome.execution_allowed is not False):
        raise ConfigurationError("M03_COMPACT_REQUIRES_PRE_FINAL_OBSERVATION")
    raw = outcome.bulk if isinstance(outcome.bulk, dict) else {}
    if (raw.get("g1_verified") is True
            or raw.get("execution_allowed") is True
            or raw.get("trade_authorized") is True):
        raise ConfigurationError("M03_COMPACT_SOURCE_UNSAFE")
    coverage = raw.get("coverage_status", "NO_BULK_EVIDENCE")
    if (type(coverage) is not str or not 1 <= len(coverage) <= 64
            or not all(c.isascii() and (c.isupper() or c.isdigit()
                                        or c == "_") for c in coverage)):
        coverage = "UNVERIFIED_COVERAGE"
    counts = {}
    for key in ("attempted_count", "analyzed_count"):
        val = raw.get(key)
        counts[key] = val if type(val) is int and 0 <= val <= 40 else None
    # Persist ONLY bounded, sanitized pre-Final candidate failure aggregates.
    # All values already exist in the same M03 bulk result: zero new GETs.
    # The legacy v1 compact rows remain valid and readable in the same DB.
    valid = raw.get("candidate_valid_count")
    rejected = raw.get("candidate_rejected_count")
    for value in (valid, rejected):
        if value is not None and (type(value) is not int or not 0 <= value <= 40):
            valid = rejected = None
            break
    if (type(valid) is int and type(rejected) is int
            and type(counts["analyzed_count"]) is int
            and valid + rejected != counts["analyzed_count"]):
        valid = rejected = None

    reason_counts = {}
    source_reasons = raw.get("candidate_rejection_reason_counts")
    if type(rejected) is int and rejected > 0:
        if isinstance(source_reasons, dict) and len(source_reasons) <= 40:
            for key, count in source_reasons.items():
                if type(count) is not int or count <= 0 or count > 40:
                    reason_counts = {}
                    break
                safe = (
                    key if type(key) is str and 3 <= len(key) <= 48
                    and all(c.isascii() and (c.isupper() or c.isdigit()
                                             or c == "_") for c in key)
                    else "UNCLASSIFIED_SOURCE_REASON"
                )
                reason_counts[safe] = reason_counts.get(safe, 0) + count
        if sum(reason_counts.values()) != rejected:
            reason_counts = {"UNCLASSIFIED_SOURCE_REASON": rejected}
        if len(reason_counts) > 6:
            frequent = sorted(
                reason_counts.items(), key=lambda row: (-row[1], row[0])
            )
            reason_counts = dict(frequent[:5])
            reason_counts["OTHER_REJECTIONS"] = sum(x[1] for x in frequent[5:])

    cohort = raw.get("eligible_cohort")
    cohort_status = (cohort.get("status") if isinstance(cohort, dict)
                     else None)
    if (type(cohort_status) is not str or not 1 <= len(cohort_status) <= 64
            or not all(c.isascii() and (c.isupper() or c.isdigit()
                                        or c == "_") for c in cohort_status)):
        cohort_status = "UNAVAILABLE"
    observed = raw.get("screening_observed_epoch")
    if type(observed) is not int or not 0 < observed < 4102444800:
        observed = None
    return {
        "schema_version": 2,
        "lane": "DEEP20_EVERY_FIVE_MINUTES",
        "coverage_status": coverage,
        **counts,
        "candidate_valid_count": valid,
        "candidate_rejected_count": rejected,
        "candidate_rejection_reason_counts": reason_counts,
        "eligible_cohort_status": cohort_status,
        "scan_observed_epoch": observed,
        "final_decision": None,
        "stage_disposition": "ADVISORY_ONLY_NO_OWNER_DECISION",
        "execution_allowed": False,
        "g1_verified": False,
        "paid_network_allowed": False,
    }


def _m06_fixed_budget_db() -> str:
    """Single V2 scope accounting path; cannot be reset via CLI arguments."""
    if os.environ.get("M06_OWNER_ONE_SHOT_PRIVATE_GO") == ACCOUNT_DIAG_ID:
        return str(Path.home() / ".local" / "state" /
                   "indodax-ai-bot-v2" / "m06-account-diagnostic-20261101.sqlite3")
    if os.environ.get("M06_OWNER_ONE_SHOT_PRIVATE_GO") == COVERAGE_ID:
        return str(Path.home() / ".local" / "state" /
                   "indodax-ai-bot-v2" / "m06-account-coverage-20261101.sqlite3")
    if (os.environ.get("M06_OWNER_ONE_SHOT_PRIVATE_GO")
            == "M06_PRIVATE_VIEW_ONLY_RETRY_20261031"):
        return str(Path.home() / ".local" / "state" /
                   "indodax-ai-bot-v2" / "m06-owner-viewonly-retry-20261031.sqlite3")
    return str(Path.home() / ".local" / "state" /
               "indodax-ai-bot-v2" / "m06-owner-viewonly-one-shot-20261031.sqlite3")


def _m04_original_provider_scope():
    """One existing V2 source rule for BOTH readiness and shadow adapter.

    Explicit process env wins as a complete scope. Never fill missing env
    roles or credentials with a protected file, and never copy secrets into
    a process-wide environment. Existing M04 owner file is read only when
    no Sumopod credential/model variables are set by the invoking process.
    """
    role_keys = ("SUMOPOD_API_KEY", "SUMOPOD_MODEL_MARKET",
                 "SUMOPOD_MODEL_RISK", "SUMOPOD_MODEL_STRATEGY")
    if any(k in environ for k in role_keys):
        return environ, "PROCESS_ENV", "NOT_USED"
    local = owner_provider_source()
    if local is None:
        return {}, "NO_V2_LOCAL_CONFIG", "NOT_PRESENT"
    source, key_status = local
    return source, "V2_PROTECTED_LOCAL_FILE", key_status


def _m06_redacted_error_code(exc: Exception) -> str:
    """Only show fixed M06 error identifiers; never exchange response/secret."""
    if type(exc) is M06ScopeDenied:
        code = str(exc)
        if re.fullmatch(r"M06_[A-Z0-9_]{1,95}", code):
            return code
    return "M06_COVERAGE_NOT_ACCEPTED"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Indodax V2 daily spot scalping (M01 safe foundation)")
    parser.add_argument("--db", default="runtime/decisions.sqlite3")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--health", action="store_true")
    group.add_argument("--m10-07-mainpath-readiness-once", action="store_true",
                       help="Original V2 M03 scanner + M04 three-role local config + M05 owner outbox READ ONLY; no extra worker, HTTP, orders or GO")
    group.add_argument("--m04-provider-readiness-once", action="store_true",
                       help="M04 original role/model/credential readiness only: no HTTP, provider billing, issuer or orders")
    group.add_argument("--m04-ws3-micro-readiness-once", action="store_true",
                       help="Original V2 M03 WS3 -> M04 immutable role slots and G1/paid blocking reasons READ ONLY; no network or journal")
    group.add_argument("--m11-release-risk-offline-once", action="store_true",
                       help="M11-04 V2 risk ceiling/kill-switch review only; no account IO, GO, trade or service update")
    group.add_argument("--m11-risk-halt-shadow-offline-once", action="store_true",
                       help="Original CLI→orchestrator→SAME DecisionJournal M11 halt review on preexisting private /tmp fixture, READ ONLY, never LIVE")
    group.add_argument("--m09-paper-offline-once", action="store_true",
                       help="Original journal PAPER-only BUY/SELL/SUMMARY simulation; no exchange orders or real profit")
    group.add_argument("--m09-recovery-shadow-once", action="store_true",
                       help="Original V2 M09 public shadow and M07/M08 journal recovery review, no post-SELL event claim or trade")
    group.add_argument("--m09-shadow-mainpath-once", action="store_true",
                       help="One original V2 public scanner→AI advisory→M05/M07/M08 shadow; isolated /tmp journal, no paid AI/trade")
    group.add_argument("--m08-settlement-admission-offline-once",
                       action="store_true",
                       help="M08-06 original V2 READ ONLY buy/sell fill and fee settlement admission, always BLOCK; isolated /tmp M11 fixture only")
    group.add_argument("--m08-post-sell-rediscovery-offline-once",
                       action="store_true",
                       help="One original M08-07 settled-IDR source gate to same public scanner; isolated /tmp journal only, no BUY")
    group.add_argument("--m08-sell-preflight-offline-once", action="store_true",
                       help="Original M08-05 tier+protective+ownership SELL REVIEW in isolated /tmp V2 SQLite only; no network, orders, ownership or LIVE")
    group.add_argument("--m07-origin-commitment-offline-once", action="store_true",
                       help="Original M07 local draft PREPARE/UNCERTAIN/REVIEW, same existing private synthetic /tmp V2 journal; no exchange issuer/order")
    group.add_argument("--m07-offline-intent-once", action="store_true",
                       help="EXPLICIT synthetic-only BUY reservation in original M07 journal; NO network, real position, or trade")
    group.add_argument("--m07-offline-unknown-once", action="store_true",
                       help="Simulate UNKNOWN for an already reserved synthetic M07 intent; permanent retry hold, no exchange GET")
    group.add_argument("--m07-offline-status-once", action="store_true",
                       help="Record UNTRUSTED synthetic normalized order status in original M07 journal, no exchange request")
    group.add_argument("--m07-private-fill-fees-once", action="store_true",
                       help="One owner View Only 14-day account/history/order/myTrades fill+fee check, 5 GET max; no bot-owned lots or trades")
    group.add_argument("--m07-private-14day-order-once", action="store_true",
                       help="Owner View Only 14-day account/history/order GET, max 4 GET; no trading")
    group.add_argument("--m07-private-latest-order-once", action="store_true",
                       help="One View Only account/history/latest-order GET chain, 3 GET max; no known client ID required")
    group.add_argument("--m07-private-order-status-once", action="store_true",
                       help="Explicit TAPI V2 View Only account + order GET, no trade/cancel/auto retry")
    group.add_argument("--m07-main-sizing-offline-once", action="store_true",
                       help="Original M03/M06/M07 source-shaped 0600 local preview only; no private GET or order")
    group.add_argument("--m07-main-readonly-cycle-once", action="store_true",
                       help="One real M03 bulk scan + original two signed M06 View Only GET + hardlocked M07 sizing; no order")
    parser.add_argument("--m07-owner-preview-m05", action="store_true",
                        help="Opt-in original source-bound M07 one-cycle READ ONLY to existing M05 owner BUY PREVIEW, never an order")
    group.add_argument("--m07-limit-preflight-offline-once", action="store_true",
                       help="Original V2 LIMIT BUY pair/min-step/fee/general IDR scenario preflight, 0600 JSON fixture, no API/trade")
    group.add_argument("--m07-order-adapter-offline-once", action="store_true",
                       help="Inspect ORIGINAL hardlocked TAPI V2 order/status/cancel contract without API keys, network or orders")
    group.add_argument("--m06-account-review-once", action="store_true",
                       help="Original V2 offline M06 account review, always REJECT; no private keys/requests")
    group.add_argument("--m06-private-readonly-once", action="store_true",
                       help="EXPLICIT scoped M06 GO: view-only four private HTTPS GETs only, never trade")
    group.add_argument("--m06-account-coverage-once", action="store_true",
                        help="Owner approved ONE 18-GET four-pair private READ ONLY coverage, never orders")
    group.add_argument("--m06-private-idr-budget-once", action="store_true",
                       help="Two official signed View Only GETs: live IDR free/locked and ALL-pair open orders, local UID-bound FLOKI/PEPE floor; no trading")
    group.add_argument("--m06-account-diagnostic-once", action="store_true",
                       help="Owner approved ONE signed GET account; original V2, no orders")
    group.add_argument("--once", action="store_true")
    parser.add_argument("--public-scanner-mainpath", action="store_true",
                        help="With --once only: opt-in same M03 bulk+G1+M04 no-trade pipeline using an existing BTC WS DB")
    group.add_argument("--public-once", action="store_true", help="Use official Indodax PUBLIC REST only; no private key/order")
    group.add_argument("--verify-public", action="store_true", help="One-shot public source verification: nonzero exit on public source errors")
    group.add_argument("--scan-once", action="store_true", help="PUBLIC all-IDR screening; no BUY signals, keys or trading")
    parser.add_argument("--m03-ws-microstructure-watch", action="store_true",
                        help="Opt-in same WS3 socket per-pair trade activity and orderbook for at most 2 recent original M03 watches, no REST/G1/AI/trade")
    parser.add_argument("--m03-ws-observe-seconds", type=int, default=12,
                        help="Bounded PUBLIC WS3 WATCH observation seconds 3..45; only with --m03-ws-summary-watch-once")
    group.add_argument("--m03-ws-summary-watch-once", action="store_true",
                       help="One bounded official WS3 market-summary WATCH into the original V2 SQLite; no REST, bid/ask, G1, AI or order")
    group.add_argument("--m03-ws-summary-replay-offline-once", action="store_true",
                       help="Two supplied PUBLIC WS summary frames in private /tmp JSON; original orchestrator WATCH-only, NO socket or DB writes")
    group.add_argument("--scheduled-public-scanner-once", action="store_true",
                       help="Opt-in ONE minute tick: lightweight all-IDR scan each minute; original 20-deep G1/M04 public reject path every fifth minute, no orders")
    parser.add_argument("--m03-light-only", action="store_true",
                        help="Explicit reversible original M03 canary: scheduled all-IDR LIGHT WATCH every aligned minute, never DEEP20 or G1/AI/trade")
    group.add_argument("--deep-once", action="store_true", help="PUBLIC bounded 2-pair 1m/5m/15m indicator pilot; no BUY orders")
    group.add_argument("--public-history-research-once", action="store_true",
                       help="One PUBLIC 1m OHLC 90-360m time-split price-only research; never trades")
    group.add_argument("--public-history-archive-once", action="store_true",
                       help="Persist 1-4 bounded PUBLIC 1m OHLC backfill windows in the existing SQLite journal; no trading")
    group.add_argument("--rolling-deep-once", action="store_true", help="PUBLIC 2-pair rotating deep scan, SQLite persisted and paced, never trades")
    group.add_argument("--bulk-deep-once", action="store_true", help="PUBLIC 20..40 same-cycle deep scan; default 20, never trades")
    group.add_argument("--public-ws-once", action="store_true", help="One public BTC trade-activity WS probe; persisted offset, no trades")
    group.add_argument("--public-ws-collect", action="store_true",
                       help="Bounded multi-session PUBLIC WS collection; durable SQLite, never trades")
    group.add_argument("--public-ws-supervised-once", action="store_true",
                       help="One public WS service batch with file lock and fail-stop; NO automatic deployment")
    group.add_argument("--public-ws-supervised-continuous", action="store_true",
                       help="One original BTC PUBLIC socket across rolling 2x30s reports; no G1/AI/order")
    group.add_argument("--public-ws-health", action="store_true",
                       help="READ-ONLY last-batch public WS SQLite/status diagnostic; no journal writes")
    group.add_argument("--public-ws-unit-status", action="store_true",
                       help="Read-only systemd user UNIT state; never installs or starts any service")
    group.add_argument("--m05-telegram-poll-once",
                       "--m05-telegram-dedicated-poll-once",
                       action="store_true",
                       help="One bounded inbound OWNER poll from a NEW dedicated V2 Telegram bot only; strict identity/webhook/lock and two GO gates; no trade")
    group.add_argument("--m05-telegram-readonly-preflight-once", action="store_true",
                       help="One getMe + getWebhookInfo identity and webhook status check ONLY; separate GO, no polling or messages")
    group.add_argument("--m05-telegram-owner-safe-status-once", action="store_true",
                       help="Opt-in one outbound status to Telegram owner, getMe + sendMessage ONLY; never polls or trades")
    parser.add_argument("--stream-seconds", type=int, default=12, help="Bounded public WS probe duration: 3..30 seconds")
    parser.add_argument("--collector-sessions", type=int, default=2,
                        help="PUBLIC WS collector: 2..10 sessions per bounded invocation")
    parser.add_argument("--collector-pause-seconds", type=int, default=1,
                        help="PUBLIC WS collector: 0..10 seconds between sessions")
    parser.add_argument("--public-ws-reconnect-until-recovered",
                        action="store_true",
                        help="Original supervised BTC PUBLIC only: reconnect every typed transport loss, immediate then bounded backoff; never trading")
    parser.add_argument("--supervisor-status-file", default=None,
                        help="Optional atomic status of last PUBLIC WS batch, not certified trade evidence")
    parser.add_argument("--health-max-age-seconds", type=int, default=3600,
                        help="Max age 60..86400s for operator last-batch check; does NOT prove service running")
    parser.add_argument("--pair", default="btc_idr", help="ticker_id e.g. btc_idr")
    parser.add_argument("--m03-ws-summary-replay-file", default=None,
                        help="Absolute owner-private 0600 /tmp JSON fixture with exactly two offline WS3 frames")
    parser.add_argument(
        "--deep-pair", default=None,
        help="Observe exactly one currently eligible IDR pair through original --deep-once mainpath; diagnostic only",
    )
    parser.add_argument("--history-minutes", type=int, default=180,
                        help="PUBLIC historical closed 1m bars for research only: 90..360")
    parser.add_argument("--history-windows", type=int, default=4,
                        help="Bounded M03-L archive windows per call, 1..4; future calls resume oldest archived minute")
    parser.add_argument("--bulk-deep-limit", type=int, default=20,
                        help="Opt-in PUBLIC 20..40-pair deep limit; default 20. Never trades")
    parser.add_argument("--bulk-min-volume-idr", default="0",
                        help="Optional PUBLIC bulk 24h IDR-volume floor; staged policy 150000000, 0 preserves existing behavior until rollout")
    parser.add_argument("--m08-settled-event-id", default=None,
                        help="M08-07 event identity in original /tmp DecisionJournal; cannot inject settlement evidence")
    parser.add_argument("--m09-paper-file", default=None,
                        help="Absolute /tmp 0600 exact-schema PAPER-only scenario; not exchange fill or settlement")
    parser.add_argument("--m09-paper-risk-halt-reference", action="store_true",
                        help="Opt-in M09 PAPER SUMMARY only: same existing private /tmp M11 fixture SQLite read-only risk-halt reference; never real account PnL/GO")
    parser.add_argument("--m09-recovery-file", default=None,
                        help="Absolute /tmp 0600 JSON with M09 recovery identities only, not settlement facts")
    parser.add_argument("--m08-sell-preflight-file", default=None,
                        help="Absolute 0600 JSON scenario in /tmp for original M08 offline non-executing SELL review only")
    parser.add_argument("--m08-settlement-admission-file", default=None,
                        help="Owner-private 0600 exact-schema /tmp JSON for future M08-06 issuer review only; never a verified wallet/issuer")
    parser.add_argument("--m07-origin-commitment-file", default=None,
                        help="0600 exact-schema local draft file in same private /tmp directory as original V2 journal; no keys, fills or GO")
    parser.add_argument("--m07-intent-id", default=None,
                        help="Synthetic-only immutable client order ID, required for M07 offline journal simulation")
    parser.add_argument("--m07-main-sizing-file", default=None,
                        help="Absolute owner-only 0600 JSON observations/inputs, untrusted OFFLINE sizing; no credentials")
    parser.add_argument("--m07-runtime-funding-file", default=None,
                        help="0600 JSON of explicit MAIN order budget assumptions; no market/account snapshots or keys")
    parser.add_argument("--m07-readonly-m11-source-observation", action="store_true",
                        help="Opt-in original M07 one-cycle source-bound M11 observation only: reuse existing 2 M06 signed GETs and M08 journal, no extra GET, Final or order")
    parser.add_argument("--m07-limit-preflight-file", default=None,
                        help="Absolute 0600 JSON of hypothetical rules, quote and IDR buckets, never exchange-verified or order authorization")
    parser.add_argument("--m07-preflight-risk-halt-reference", action="store_true",
                        help="Opt-in M07 synthetic LIMIT preflight: same existing private /tmp M11 SQLite fixture, offline read-only report, never GO/order")
    parser.add_argument("--m07-private-symbol", default=None)
    parser.add_argument("--m07-private-client-order-id", default=None)
    parser.add_argument("--m07-observation-id", default=None)
    parser.add_argument("--m07-observed-status", default=None)
    parser.add_argument("--m07-cumulative-base", default=None)
    parser.add_argument("--m07-cumulative-quote", default=None)
    parser.add_argument("--m03-owner-fee-snapshot-file", default=None,
                        help="Owner-entered 0600 IDR taker all-in fee JSON; NOT verified account evidence or permission to trade")
    parser.add_argument("--ws-db", default=None,
                        help="Existing BTC WS SQLite: required by --once --public-scanner-mainpath; optional for --bulk-deep-once, never G1 approval")
    parser.add_argument("--sumopod-shadow", action="store_true",
                        help="Connect configured Sumopod adapter to PUBLIC bulk shadow; paid network OFF, G1 still untrusted")
    parser.add_argument("--m05-shadow-db", default=None,
                        help="Opt-in OFFLINE owner REJECT audit DB (no Telegram transport or orders)")
    parser.add_argument("--m05-shadow-owner-id", type=int, default=None,
                        help="Opt-in numeric original V2 Telegram owner user ID")
    parser.add_argument("--m05-market-advisory-queue", action="store_true",
                        help="Only on scheduled M03: explicitly authorized no-order owner advisory queue, existing 0600 M05 ledger required")
    parser.add_argument("--m06-go-file", default=None,
                        help="Owner-created 0600 approval JSON, absolute path, <=15 min")
    parser.add_argument("--m06-credentials-file", default=None,
                        help="0600 view-only API key JSON, absolute path; never use trading key")
    parser.add_argument("--m06-ownership-db", default=None,
                        help="Optional existing UID-bound V2 ownership SQLite, read-only")
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()

        if not args.m03_ws_summary_watch_once and args.m03_ws_observe_seconds != 12:
            raise ConfigurationError("M03_WS_WATCH_DURATION_REQUIRES_WS3_MODE")
        if args.m03_ws_microstructure_watch and not args.m03_ws_summary_watch_once:
            raise ConfigurationError("M03_WS_MICRO_REQUIRES_WS3_SUMMARY_MODE")
        if args.m03_ws_summary_watch_once:
            # Explicit M03 public monitoring alternative, not a REST fix.
            # One bounded WS3 session and SAME original DecisionJournal DB.
            settings.require_m01_safety()
            if not 3 <= args.m03_ws_observe_seconds <= 45:
                raise ConfigurationError("M03_WS_WATCH_WINDOW_INVALID")
            report = run_m03_ws3_summary_watch_once(
                args.db, observe_seconds=args.m03_ws_observe_seconds,
                micro_enabled=args.m03_ws_microstructure_watch,
            )
            print(json.dumps({
                "m03_ws_summary_watch": report,
                "rest_scanner_recovered": False,
                "g1_verified": False,
                "live_trading_authorized": False,
                "execution_allowed": False,
            }, sort_keys=True, separators=(",", ":")))
            return 0

        if args.m03_ws_summary_replay_file is not None and not args.m03_ws_summary_replay_offline_once:
            raise ConfigurationError("M03_WS_REPLAY_FILE_REQUIRES_OFFLINE_ACTION")
        if args.m03_ws_summary_replay_offline_once:
            # Strictly offline: BEFORE constructing a DecisionJournal, HTTP
            # adapter, socket, provider, Telegram or market scanner.
            settings.require_m01_safety()
            if args.m03_ws_summary_replay_file is None:
                raise ConfigurationError("M03_WS_REPLAY_FIXTURE_REQUIRED")
            fixture = Path(args.m03_ws_summary_replay_file)
            if (not fixture.is_absolute() or fixture.is_symlink()
                    or not fixture.resolve().is_relative_to(Path("/tmp"))):
                raise ConfigurationError("M03_WS_REPLAY_FIXTURE_SCOPE_INVALID")
            try:
                fd = os.open(fixture, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                try:
                    info = os.fstat(fd)
                    if (not stat.S_ISREG(info.st_mode)
                            or info.st_uid != os.geteuid()
                            or info.st_mode & 0o077
                            or not 2 <= info.st_size <= 196608):
                        raise ConfigurationError("M03_WS_REPLAY_FIXTURE_UNTRUSTED")
                    with os.fdopen(fd, "rb", closefd=False) as stream:
                        raw = stream.read(196609)
                finally:
                    os.close(fd)
                data = json.loads(raw)
            except (OSError, UnicodeError, ValueError) as exc:
                if isinstance(exc, ConfigurationError):
                    raise
                raise ConfigurationError("M03_WS_REPLAY_FIXTURE_UNAVAILABLE") from None
            if type(data) is not dict or set(data) != {"frames"}:
                raise ConfigurationError("M03_WS_REPLAY_ENVELOPE_INVALID")
            review = DailyScalpingOrchestrator.review_m03_ws_summary_fixture_watch_offline(
                data["frames"], now_epoch=int(time.time()),
            )
            print(json.dumps({
                "m03_ws_summary_offline_review": review,
                "final_decision": None,
                "execution_allowed": False,
                "live_trading_authorized": False,
            }, sort_keys=True))
            return 0

        if args.public_ws_supervised_continuous:
            # A single owner-controlled, PUBLIC-only systemd service.
            # Dispatch BEFORE any DecisionJournal, scanner, AI or account API.
            settings.require_m01_safety()
            if (args.pair != "btc_idr" or args.collector_sessions != 2
                    or args.stream_seconds != 30
                    or args.collector_pause_seconds != 0
                    or not args.public_ws_reconnect_until_recovered
                    or not args.supervisor_status_file):
                raise ConfigurationError("M04W_TWO_30S_CONTINUOUS_BOUNDS_REQUIRED")
            report = supervised_public_ws_continuous(
                args.db, pair=args.pair, sessions=args.collector_sessions,
                seconds=args.stream_seconds,
                pause_seconds=args.collector_pause_seconds,
                status_file=args.supervisor_status_file,
            )
            print(json.dumps({"public_ws_continuous": report,
                              "execution_allowed": False,
                              "live_trading_authorized": False}, sort_keys=True))
            return 0

        if args.m10_07_mainpath_readiness_once:
            # M10-07 diagnostic at the ORIGINAL V2 CLI boundary, before
            # constructing any DecisionJournal, scanner or paid provider.
            settings.require_m01_safety()
            source, source_kind, key_status = _m04_original_provider_scope()
            provider_report = inspect_provider_configuration_offline(source)
            result = inspect_m10_07_original_mainpath_readonly(
                provider_report=provider_report,
            )
            result["m04_provider_source_kind"] = source_kind
            result["m04_key_file_status"] = key_status
            print(json.dumps({"m10_07_original_mainpath": result},
                             sort_keys=True))
            return 0
        if args.m04_ws3_micro_readiness_once:
            # ORIGINAL CLI -> SAME DailyScalpingOrchestrator static read-only
            # source inspection. Do not construct a DecisionJournal, socket,
            # Sumopod adapter, owner sender, second scanner or order.
            settings.require_m01_safety()
            local_source, source_type, key_file_status = (
                _m04_original_provider_scope()
            )
            provider_report = inspect_provider_configuration_offline(local_source)
            report = DailyScalpingOrchestrator.inspect_m04_ws3_micro_role_readiness_readonly(
                db_path=Path(args.db), now_epoch=int(time.time()),
                provider_report=provider_report,
            )
            report["provider_config_source"] = source_type
            report["provider_key_file_status"] = key_file_status
            print(json.dumps({
                "m04_ws3_micro_role_readiness": report,
                "final_decision": None,
                "execution_allowed": False,
                "live_trading_authorized": False,
            }, sort_keys=True))
            return 0
        if args.m04_provider_readiness_once:
            # Direct original V2 CLI, BEFORE creating a DecisionJournal or
            # scanning or instantiating the paid provider. This only checks
            # three already-existing MARKET/RISK/STRATEGY model IDs and
            # whether a credential is locally present. NOT provider auth.
            settings.require_m01_safety()
            local_source, source_type, key_file_status = (
                _m04_original_provider_scope()
            )
            report = inspect_provider_configuration_offline(local_source)
            report["config_source"] = source_type
            report["v2_key_file_status"] = key_file_status
            print(json.dumps({
                "m04_provider_readiness": report,
                "final_decision": None,
                "execution_allowed": False,
            }, sort_keys=True))
            return 0
        if args.m07_origin_commitment_file is not None and not args.m07_origin_commitment_offline_once:
            raise ConfigurationError("M07_ORIGIN_FILE_REQUIRES_OFFLINE_ACTION")
        if args.m07_origin_commitment_offline_once:
            settings.require_m01_safety()
            try:
                original_readonly = DecisionJournal.open_m11_offline_fixture_readonly(
                    args.db)
                path = Path(args.m07_origin_commitment_file)
                if (not path.is_absolute() or path.is_symlink()
                        or path.parent != original_readonly._db_path.parent
                        or not path.is_file() or path.stat().st_size > 4096):
                    raise ValueError("M07_ORIGIN_FIXTURE_NOT_PRIVATE")
                data = _owner_private_file(str(path))
                required = {
                    "action","account_uid","account_fingerprint",
                    "symbol","client_order_id","price_idr",
                    "quantity_base","observed_epoch",
                }
                if (type(data) is not dict or set(data) != required
                        or any(type(data[k]) is not str for k in required
                               - {"account_uid","observed_epoch"})
                        or type(data["account_uid"]) is not int
                        or type(data["observed_epoch"]) is not int):
                    raise ValueError("M07_ORIGIN_FIXTURE_SCHEMA_INVALID")
                draft = M07OrderDraft(
                    symbol=data["symbol"],side="BUY",order_type="LIMIT",
                    client_order_id=data["client_order_id"],
                    price=Decimal(data["price_idr"]),
                    quantity=Decimal(data["quantity_base"]),
                )
                action = data["action"]
                # PREPARE or UNCERTAIN writes ONLY a LOCAL, explicit
                # nonexecuting pre-submit commitment to the SAME original
                # isolated V2 journal. REVIEW opens it read-only.
                journal = (
                    original_readonly if action == "REVIEW"
                    else DecisionJournal(original_readonly._db_path)
                )
                result = DailyScalpingOrchestrator(
                    settings, journal,
                ).review_m07_original_bot_origin_local_once(
                    action=action, account_uid=data["account_uid"],
                    account_fingerprint=data["account_fingerprint"],
                    draft=draft, observed_epoch=data["observed_epoch"],
                )
            except (ValueError, TypeError, OSError, sqlite3.Error,
                    DecisionConflictError, InvalidOperation, M06ScopeDenied):
                raise ConfigurationError(
                    "M07_ORIGIN_OFFLINE_SCOPE_OR_SOURCE_UNVERIFIED") from None
            print(json.dumps({
                "m07_local_origin_commitment": result,
                "final_decision": None, "execution_allowed": False,
                "live_trading_authorized": False,
            }, sort_keys=True))
            return 0
        if args.m08_settlement_admission_file is not None and not args.m08_settlement_admission_offline_once:
            raise ConfigurationError("M08_SETTLEMENT_FIXTURE_ONLY_FOR_OFFLINE_ADMISSION")
        if args.m08_settlement_admission_offline_once:
            settings.require_m01_safety()
            # Exact original V2 private synthetic /tmp fixture, read-only
            # SAME DecisionJournal. Must reject before normal constructor.
            try:
                journal = DecisionJournal.open_m11_offline_fixture_readonly(args.db)
                fixture_path = Path(args.m08_settlement_admission_file)
                if (not fixture_path.is_absolute()
                        or fixture_path.is_symlink()
                        or fixture_path.parent != journal._db_path.parent
                        or not fixture_path.is_file()
                        or fixture_path.stat().st_size > 4096):
                    raise ValueError("M08_SETTLEMENT_FIXTURE_SCOPE_INVALID")
                raw = _owner_private_file(str(fixture_path))
                expected = {"account_fingerprint", "symbol",
                            "source_buy_client_order_id",
                            "sell_client_order_id", "observed_epoch"}
                if (type(raw) is not dict or set(raw) != expected
                        or type(raw["observed_epoch"]) is not int
                        or any(type(raw[k]) is not str for k in expected
                               - {"observed_epoch"})):
                    raise ValueError("M08_SETTLEMENT_FIXTURE_SCHEMA_INVALID")
                report = DailyScalpingOrchestrator(
                    settings, journal,
                ).review_m08_original_sell_settlement_admission_offline_once(**raw)
            except (sqlite3.Error, ValueError, OSError,
                    DecisionConflictError, M06ScopeDenied):
                raise ConfigurationError(
                    "M08_SETTLEMENT_OFFLINE_ADMISSION_UNVERIFIED") from None
            print(json.dumps({
                "m08_settlement_issuer_admission": report,
                "final_decision": None, "execution_allowed": False,
                "live_trading_authorized": False,
            }, sort_keys=True))
            return 0  # Review only; not a settlement event or BUY/SELL.
        if args.m11_release_risk_offline_once:
            settings.require_m01_safety()
            result = review_m11_release_risk_offline(settings=settings)
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            return 0  # Offline report completed, NOT M11 release approval.
        if args.m11_risk_halt_shadow_offline_once:
            settings.require_m01_safety()
            # An exclusively synthetic owner/time probe. Never accept the
            # process owner UID, runtime DB, private balances or real PnL.
            try:
                shadow_journal = DecisionJournal.open_m11_offline_fixture_readonly(
                    args.db)
                report = DailyScalpingOrchestrator(
                    settings, shadow_journal,
                ).review_m11_offline_shadow_safety_once(
                    owner_user_id=1, observed_epoch=1793781000,
                )
            except (sqlite3.Error, ValueError, OSError):
                raise ConfigurationError(
                    "M11_SHADOW_READONLY_FIXTURE_UNVERIFIED") from None
            print(json.dumps(report, sort_keys=True, separators=(",", ":")))
            return 0  # Synthetic SHADOW report only. Never LIVE release.
        if args.m03_light_only and not args.scheduled_public_scanner_once:
            raise ConfigurationError("M03_LIGHT_ONLY_REQUIRES_SCHEDULED_ORIGINAL")
        if args.scheduled_public_scanner_once:
            # Owner's user-systemd opt-in must be explicit and current.
            # Block before creating any decision or owner ledger, and BEFORE
            # scanner construction, public HTTP and paid adapters.
            settings.require_m01_safety()
            if environ.get("INDODAX_M03_REQUIRE_EGRESS_ADMISSION") != "YES":
                raise ConfigurationError("M03_EGRESS_ADMISSION_REQUIRED")
            try:
                # Public admission/REST quota is independent of BTC. The
                # DEEP20-specific BTC prerequisite is checked at dispatch.
                M03SharedEgressAdmission().allowed_local_limit(
                    require_btc_source=False,
                )
            except M03EgressAdmissionError as exc:
                raise ConfigurationError(str(exc)) from None
        if (args.m07_offline_intent_once or args.m07_offline_unknown_once
                or args.m07_offline_status_once):
            settings.require_m01_safety()
            if (type(args.m07_intent_id) is not str
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,36}", args.m07_intent_id)
                    or not Path(args.db).is_absolute()
                    or not Path(args.db).resolve().is_relative_to(Path("/tmp"))
                    or args.m07_intent_id == "LIVE"):
                raise ConfigurationError("M07_SYNTHETIC_ID_AND_EXPLICIT_ABSOLUTE_DB_REQUIRED")
        elif args.m07_intent_id is not None:
            raise ConfigurationError("M07_INTENT_ID_ONLY_FOR_OFFLINE_SIMULATION")
        if args.m09_paper_offline_once:
            settings.require_m01_safety()
            if (type(args.db) is not str
                    or not Path(args.db).is_absolute()
                    or not Path(args.db).resolve().is_relative_to(Path("/tmp"))
                    or type(args.m09_paper_file) is not str
                    or not Path(args.m09_paper_file).is_absolute()
                    or not Path(args.m09_paper_file).resolve().is_relative_to(Path("/tmp"))):
                raise ConfigurationError("M09_PAPER_ISOLATED_TMP_FILES_REQUIRED")
        elif args.m09_paper_file is not None:
            raise ConfigurationError("M09_PAPER_FILE_ONLY_FOR_OFFLINE_SIMULATION")
        if args.m09_paper_risk_halt_reference:
            if not args.m09_paper_offline_once:
                raise ConfigurationError("M09_PAPER_RISK_REFERENCE_SUMMARY_ONLY")
            # Block BEFORE the standard DecisionJournal constructor. The
            # selected file must already exist under a private /tmp folder
            # with the M11 offline fixture name and owner-only permissions.
            # It is THE SAME SQLite file for M09 PAPER and M11 halt tables.
            try:
                DecisionJournal.open_m11_offline_fixture_readonly(args.db)
            except (ValueError, OSError, sqlite3.Error):
                raise ConfigurationError(
                    "M09_PAPER_RISK_REFERENCE_PRIVATE_FIXTURE_REQUIRED") from None
        if args.m09_recovery_shadow_once:
            settings.require_m01_safety()
            if (type(args.db) is not str
                    or not Path(args.db).is_absolute()
                    or not Path(args.db).resolve().is_relative_to(Path("/tmp"))
                    or type(args.m09_recovery_file) is not str
                    or not Path(args.m09_recovery_file).is_absolute()
                    or not Path(args.m09_recovery_file).resolve().is_relative_to(Path("/tmp"))):
                raise ConfigurationError("M09_RECOVERY_ISOLATED_TMP_FILES_REQUIRED")
        elif args.m09_recovery_file is not None:
            raise ConfigurationError("M09_RECOVERY_FILE_ONLY_FOR_OFFLINE_SHADOW")
        if args.m09_shadow_mainpath_once:
            settings.require_m01_safety()
            if (not Path(args.db).is_absolute()
                    or not Path(args.db).resolve().is_relative_to(Path("/tmp"))):
                raise ConfigurationError("M09_SHADOW_TMP_ORIGINAL_JOURNAL_REQUIRED")
        if args.m08_post_sell_rediscovery_offline_once:
            settings.require_m01_safety()
            if (type(args.m08_settled_event_id) is not str
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,36}",
                                        args.m08_settled_event_id)
                    or type(args.db) is not str
                    or not Path(args.db).is_absolute()
                    or not Path(args.db).resolve().is_relative_to(Path("/tmp"))
                    or not Path(args.db).is_file()):
                raise ConfigurationError(
                    "M08_POST_SELL_ORIGINAL_TMP_EVENT_REQUIRED")
        elif args.m08_settled_event_id is not None:
            raise ConfigurationError(
                "M08_SETTLEMENT_EVENT_ID_ONLY_FOR_OFFLINE_HANDOFF")
        if args.m08_sell_preflight_offline_once:
            settings.require_m01_safety()
            if (type(args.m08_sell_preflight_file) is not str
                    or not Path(args.m08_sell_preflight_file).is_absolute()
                    or not Path(args.m08_sell_preflight_file).resolve().is_relative_to(Path("/tmp"))
                    or type(args.db) is not str
                    or not Path(args.db).is_absolute()
                    or not Path(args.db).resolve().is_relative_to(Path("/tmp"))
                    or not Path(args.db).is_file()
                    or type(args.m06_ownership_db) is not str
                    or not Path(args.m06_ownership_db).is_absolute()
                    or not Path(args.m06_ownership_db).resolve().is_relative_to(Path("/tmp"))
                    or not Path(args.m06_ownership_db).is_file()):
                raise ConfigurationError("M08_SELL_OFFLINE_ISOLATED_INPUT_REQUIRED")
        elif args.m08_sell_preflight_file is not None:
            raise ConfigurationError("M08_SELL_PREFLIGHT_FILE_ONLY_FOR_OFFLINE_REVIEW")
        if (args.m07_main_sizing_file is not None
                and not args.m07_main_sizing_offline_once):
            raise ConfigurationError("M07_MAIN_SIZING_FILE_ONLY_FOR_OFFLINE_REVIEW")
        if (args.m07_runtime_funding_file is not None
                and not args.m07_main_readonly_cycle_once):
            raise ConfigurationError("M07_RUNTIME_FUNDING_ONLY_FOR_REAL_READONLY_CYCLE")
        if args.m07_owner_preview_m05:
            settings.require_m01_safety()
            if (not args.m07_main_readonly_cycle_once
                    or type(args.m05_shadow_db) is not str
                    or not args.m05_shadow_db
                    or type(args.m05_shadow_owner_id) is not int
                    or args.m05_shadow_owner_id <= 0):
                raise ConfigurationError(
                    "M07_RUNTIME_M05_PREVIEW_REQUIRES_EXPLICIT_OWNER_SCOPE")
        if args.m07_readonly_m11_source_observation:
            settings.require_m01_safety()
            if not args.m07_main_readonly_cycle_once:
                raise ConfigurationError(
                    "M07_RUNTIME_M11_OBSERVATION_REQUIRES_ONE_CYCLE")
        if (args.m07_limit_preflight_file is not None
                and not args.m07_limit_preflight_offline_once):
            raise ConfigurationError("M07_PREFLIGHT_FILE_ONLY_FOR_OFFLINE_REVIEW")
        if args.m07_preflight_risk_halt_reference:
            settings.require_m01_safety()
            if (not args.m07_limit_preflight_offline_once
                    or type(args.m07_limit_preflight_file) is not str):
                raise ConfigurationError(
                    "M07_PREFLIGHT_RISK_REFERENCE_REQUIRES_OFFLINE_PREFLIGHT")
            # Deny BEFORE constructor would initialize an arbitrary runtime
            # journal. The scenario must share a private synthetic folder
            # with the SAME existing M11 fixture. Never deployed DB.
            try:
                refdb = DecisionJournal.open_m11_offline_fixture_readonly(args.db)
                p = Path(args.m07_limit_preflight_file)
                if (not p.is_absolute()
                        or p.parent != refdb._db_path.parent
                        or p.is_symlink() or not p.is_file()):
                    raise ValueError("M07_PREFLIGHT_RISK_REFERENCE_SCENARIO_SCOPE")
            except (OSError, ValueError, sqlite3.Error):
                raise ConfigurationError(
                    "M07_PREFLIGHT_RISK_REFERENCE_PRIVATE_FIXTURE_REQUIRED") from None
        status_fields = (
            args.m07_observation_id, args.m07_observed_status,
            args.m07_cumulative_base, args.m07_cumulative_quote,
        )
        if args.m07_offline_status_once:
            if (type(args.m07_observation_id) is not str
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,36}",
                                        args.m07_observation_id)
                    or args.m07_observed_status not in (
                        "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED",
                        "EXPIRED", "REJECTED", "UNKNOWN",
                    )
                    or any(type(n) is not str or not re.fullmatch(
                        r"(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,16})?", n
                    ) for n in (args.m07_cumulative_base,
                                args.m07_cumulative_quote))):
                raise ConfigurationError("M07_SYNTHETIC_STATUS_INPUT_INVALID")
        elif any(x is not None for x in status_fields):
            raise ConfigurationError("M07_STATUS_FIELDS_ONLY_FOR_SYNTHETIC_CLI")
        if (args.m07_private_order_status_once
                or args.m07_private_latest_order_once
                or args.m07_private_14day_order_once
                or args.m07_private_fill_fees_once):
            settings.require_m01_safety()
            if (type(args.m07_private_symbol) is not str
                    or not re.fullmatch(r"[A-Z0-9]{2,20}IDR",
                                        args.m07_private_symbol)):
                raise ConfigurationError("M07_READONLY_EXPLICIT_SYMBOL_REQUIRED")
            if args.m07_private_order_status_once and (
                    type(args.m07_private_client_order_id) is not str
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,36}",
                                        args.m07_private_client_order_id)):
                raise ConfigurationError("M07_READONLY_EXPLICIT_ORDER_ID_REQUIRED")
            if ((args.m07_private_latest_order_once
                    or args.m07_private_14day_order_once
                    or args.m07_private_fill_fees_once)
                    and args.m07_private_client_order_id is not None):
                raise ConfigurationError("M07_LATEST_REJECTS_CLIENT_ID_OVERRIDE")
        elif (args.m07_private_symbol is not None
                or args.m07_private_client_order_id is not None):
            raise ConfigurationError("M07_READONLY_FIELDS_REQUIRE_READONLY_COMMAND")
        m03_owner_fee_snapshot = None
        if args.m03_owner_fee_snapshot_file is not None:
            if not (
                args.deep_once or args.rolling_deep_once
                or args.bulk_deep_once or args.scheduled_public_scanner_once
                or (args.once and args.public_scanner_mainpath)
            ):
                raise ConfigurationError("M03_OWNER_FEE_REQUIRES_ORIGINAL_PUBLIC_DEEP_PATH")
            settings.require_m01_safety()
            try:
                private_path = _existing_private_owner_file(
                    args.m03_owner_fee_snapshot_file
                )
                if private_path.stat().st_size > 4096:
                    raise ValueError("FEE_SNAPSHOT_TOO_LARGE")
                m03_owner_fee_snapshot = OwnerEnteredAllInFeeSnapshot.from_mapping(
                    json.loads(private_path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError, UnicodeError, ConfigurationError):
                raise ConfigurationError("M03_OWNER_ENTERED_FEE_FILE_NOT_ACCEPTED") from None
        if args.m06_private_idr_budget_once:
            # Reuse ONLY the stored V2 owner-only View Only credential. No
            # separate app, external wallet and no POST/DELETE.
            settings.require_m01_safety()
            try:
                credentials_path = _existing_v2_credentials()
                if credentials_path is None:
                    raise M06ScopeDenied("M06_V2_VIEWONLY_FILE_NOT_FOUND")
                api_key, secret_key = read_view_only_credentials(
                    str(credentials_path),
                )
                report = DailyScalpingOrchestrator(
                    settings, DecisionJournal(args.db),
                ).inspect_m06_idr_budget_readonly_once(
                    api_key=api_key, secret_key=secret_key,
                    local_ownership_db=args.m06_ownership_db,
                )
            except (M06ScopeDenied, ValueError, OSError) as exc:
                print(json.dumps({
                    "stage": "M06_ACTUAL_IDR_VIEWONLY",
                    "status": "UNRESOLVED",
                    "reason": _m06_redacted_error_code(exc),
                    "execution_allowed": False,
                }, sort_keys=True), file=sys.stderr)
                return 3
            print(json.dumps({
                "stage": "M06_ACTUAL_IDR_VIEWONLY",
                **report,
            }, sort_keys=True))
            return 0
        if args.m06_account_diagnostic_once:
            settings.require_m01_safety()
            if not all((args.m06_go_file, args.m06_credentials_file)):
                raise ConfigurationError("M06_ACCOUNT_DIAG_FILES_REQUIRED")
            try:
                approval = read_account_diagnostic_approval(args.m06_go_file)
                api_key, secret_key = read_view_only_credentials(
                    args.m06_credentials_file
                )
                budget = M06RequestBudget(_m06_fixed_budget_db())
                journal = DecisionJournal(args.db)
                result = DailyScalpingOrchestrator(
                    settings, journal
                ).inspect_m06_account_diagnostic_once(
                    approval=approval, budget=budget,
                    api_key=api_key, secret_key=secret_key,
                )
                journal.record_stage_observation(
                    "M06_ACCOUNT_DIAG_" + uuid4().hex,
                    "M06_PRIVATE_ACCOUNT_DIAGNOSTIC", result.status,
                )
            except (M06ScopeDenied, ValueError) as exc:
                print(json.dumps({
                    "stage": "M06_ACCOUNT_DIAGNOSTIC_ONE_GET",
                    "status": "REJECT",
                    "reason": _m06_redacted_error_code(exc),
                    "execution_allowed": False,
                }, sort_keys=True), file=sys.stderr)
                return 3
            print(json.dumps({
                "stage": "M06_ACCOUNT_DIAGNOSTIC_ONE_GET",
                "status": result.status,
                "request_count": result.request_count,
                "view_only_permissions_verified":
                    result.view_only_permissions_verified,
                "balances_schema_observed": result.balances_schema_observed,
                "spendable_idr_verified": False,
                "manual_asset_ownership_verified": False,
                "external_peer_coverage_verified": False,
                "execution_allowed": False,
            }, sort_keys=True))
            return 0
        if args.m06_account_coverage_once:
            settings.require_m01_safety()
            if not all((args.m06_go_file, args.m06_credentials_file)):
                raise ConfigurationError("M06_COVERAGE_EXPLICIT_GO_FILES_REQUIRED")
            try:
                approval = read_coverage_approval(args.m06_go_file)
                api_key, secret_key = read_view_only_credentials(
                    args.m06_credentials_file
                )
                budget = M06RequestBudget(_m06_fixed_budget_db())
                journal = DecisionJournal(args.db)
                result = DailyScalpingOrchestrator(
                    settings, journal,
                ).inspect_m06_account_coverage_once(
                    approval=approval, budget=budget,
                    api_key=api_key, secret_key=secret_key,
                    local_ownership_db=args.m06_ownership_db,
                )
                journal.record_stage_observation(
                    "M06_COVERAGE_" + uuid4().hex,
                    "M06_ACCOUNT_COVERAGE", result.status,
                )
            except (M06ScopeDenied, ValueError) as exc:
                print(json.dumps({
                    "stage":"M06_ACCOUNT_COVERAGE_SCOPED",
                    "status":"REJECT",
                    "reason":_m06_redacted_error_code(exc),
                    "execution_allowed":False,
                },sort_keys=True), file=sys.stderr)
                return 3
            print(json.dumps({
                "stage":"M06_ACCOUNT_COVERAGE_SCOPED",
                "status":result.status,
                "request_count":result.request_count,
                "pairs_checked":result.checked_pairs,
                "windows_per_pair":result.checked_windows_per_pair,
                "open_orders_count":result.open_orders_count,
                "history_orders_count":result.history_orders_count,
                "trade_fills_count":result.trade_fills_count,
                "local_receipts_matched":result.local_receipts_matched,
                "local_receipts_unlinked":result.local_receipts_unlinked,
                "scoped_special_sell_gross_idr":dict(
                    result.scoped_special_sell_gross_idr),
                "scoped_special_sell_fill_count":dict(
                    result.scoped_special_sell_fill_count),
                "scoped_special_unresolved_fills":dict(
                    result.scoped_special_unresolved_fills),
                "special_sale_scope_not_full_history":True,
                "scoped_special_history_gaps":result.scoped_special_history_gaps,
                "general_idr_budget_from_this_scope":None,
                "unresolved_requirements":list(result.unresolved_requirements),
                "m06_02_balance_and_open_orders_observed":
                    result.m06_02_balance_and_open_orders_observed,
                "m06_03_special_pool_ratio_observed":
                    result.m06_03_special_pool_ratio_observed,
                "m06_04_local_fill_receipts_in_window_matched":
                    result.m06_04_local_fill_receipts_in_window_matched,
                "m06_05_local_account_conflict_free":
                    result.m06_05_local_account_conflict_free,
                "full_account_history_verified":result.full_account_history_verified,
                "external_peer_coverage_verified":result.external_peer_coverage_verified,
                "spendable_idr_verified":result.spendable_idr_verified,
                "bot_owned_sellable_verified":result.bot_owned_sellable_verified,
                "execution_allowed":False,
            },sort_keys=True))
            return 0
        if args.m06_private_readonly_once:
            # No implicit GO from branch/owner PASS or presence of environment keys.
            if not all((args.m06_go_file, args.m06_credentials_file)):
                raise ConfigurationError("M06_EXPLICIT_GO_FILES_REQUIRED")
            settings.require_m01_safety()
            try:
                approval = read_approval(args.m06_go_file)
                api_key, secret_key = read_view_only_credentials(
                    args.m06_credentials_file)
                budget = M06RequestBudget(_m06_fixed_budget_db())
                journal = DecisionJournal(args.db)
                orchestrator = DailyScalpingOrchestrator(settings, journal)
                result = orchestrator.inspect_m06_private_readonly_once(
                    approval=approval, budget=budget,
                    api_key=api_key, secret_key=secret_key,
                    local_ownership_db=args.db)
                journal.record_stage_observation(
                    "M06_PRIVATE_READONLY_" + uuid4().hex,
                    "M06_PRIVATE_READONLY", result.status,
                )
            except M06ScopeDenied as exc:
                # Fixed error codes only; NEVER emit API response/keys/balance.
                print(json.dumps({"status":"REJECT", "reason":str(exc),
                    "execution_allowed":False}), file=sys.stderr)
                return 3
            print(json.dumps({
                "status":result.status,
                "view_only_permissions_verified":
                    result.view_only_permissions_verified,
                "reconciliation_status":result.reconciliation_status,
                "capital_spendable_verified":result.capital_spendable_verified,
                "protected_rebuy_verified":result.protected_rebuy_verified,
                "special_asset_ownership_verified":
                    result.special_asset_ownership_verified,
                "local_ledger_account_bound":result.local_ledger_account_bound,
                "local_rebuy_hold_present":result.local_rebuy_hold_present,
                "local_manual_floor_present":result.local_manual_floor_present,
                "local_shadow_reservations_present":result.local_shadow_reservations_present,
                "local_shadow_coverage_complete":result.local_shadow_coverage_complete,
                "local_rebuy_receipts_linked":result.local_rebuy_receipts_linked,
                "local_exchange_receipts_linked":result.local_exchange_receipts_linked,
                "local_exchange_receipts_unlinked":result.local_exchange_receipts_unlinked,
                "unresolved_requirements":list(result.unresolved_requirements),
                "local_other_account_records_present":result.local_other_account_records_present,
                "local_orphan_fill_records_present":result.local_orphan_fill_records_present,
                "external_peer_coverage_verified":result.external_peer_coverage_verified,
                "special_pool_local_floors_complete":result.special_pool_local_floors_complete,
                "pending_buy_orders":result.pending_buy_orders,
                "unmatched_fills_in_window":result.unmatched_fills_in_window,
                "open_orders_count":result.open_orders_count,
                "order_history_count":result.order_history_count,
                "fills_count":result.fills_count,
                "history_complete":result.history_complete,
                "bot_owned_fills_verified":result.bot_owned_fills_verified,
                "cross_bot_ownership_verified":result.cross_bot_ownership_verified,
                "execution_allowed":result.execution_allowed,
            },sort_keys=True))
            return 0
        if args.m06_account_review_once:
            settings.require_m01_safety()
            journal = DecisionJournal(args.db)
            orchestrator = DailyScalpingOrchestrator(settings, journal)
            review = orchestrator.review_m06_account(now_epoch=int(time.time()))
            journal.record_stage_observation(
                "M06_ACCOUNT_READONLY_NO_GO",
                "M06_LOCAL_ACCOUNT_PREVIEW", review.reason,
            )
            print(json.dumps({
                "stage": "M06_ACCOUNT_READONLY_ORIGINAL_MAINPATH",
                "status": review.status,
                "reason": review.reason,
                "execution_allowed": review.execution_allowed,
                "account_authenticated": review.account_authenticated,
                "orders_complete": review.orders_complete,
                "fills_complete": review.fills_complete,
                "cross_bot_coexistence_verified": review.cross_bot_coexistence_verified,
            }, sort_keys=True))
            return 0
        if (args.m05_shadow_db is None) != (args.m05_shadow_owner_id is None):
            raise ConfigurationError("M05_REQUIRES_BOTH_SHADOW_OWNER_AND_DB")
        if args.m05_market_advisory_queue:
            # Explicitly opt in ONLY the original scheduled M03 -> M05
            # no-order queue. No Telegram call or owner-mode change here.
            if not args.scheduled_public_scanner_once:
                raise ConfigurationError("M05_QUEUE_REQUIRES_SCHEDULED_M03")
            if args.m05_shadow_db is None:
                raise ConfigurationError("M05_QUEUE_REQUIRES_EXISTING_OWNER_DB")
            if environ.get("M05_MARKET_ADVISORY_QUEUE_GO") != "YES":
                raise ConfigurationError("M05_QUEUE_SEPARATE_GO_REQUIRED")
            owner_path = _existing_private_owner_file(args.m05_shadow_db)
            try:
                with sqlite3.connect(owner_path.as_uri() + "?mode=ro",
                                     uri=True, timeout=5) as owner_readonly:
                    owner_readonly.execute("PRAGMA query_only=ON")
                    bound = owner_readonly.execute(
                        "SELECT owner_user_id FROM m05_config WHERE singleton=1"
                    ).fetchone()
                if bound != (args.m05_shadow_owner_id,):
                    raise ConfigurationError("M05_QUEUE_OWNER_ID_MISMATCH")
            except sqlite3.Error:
                raise ConfigurationError("M05_QUEUE_ORIGINAL_OWNER_DB_UNAVAILABLE") from None
        elif args.scheduled_public_scanner_once and args.m05_shadow_db is not None:
            raise ConfigurationError("M05_SCHEDULED_QUEUE_OPTIN_REQUIRED")
        if args.m05_shadow_db is not None and not (
                args.once or args.bulk_deep_once
                or args.m09_recovery_shadow_once or args.m09_shadow_mainpath_once
                or args.m05_telegram_poll_once
                or args.m05_telegram_readonly_preflight_once
                or args.m05_telegram_owner_safe_status_once
                or (args.scheduled_public_scanner_once
                    and args.m05_market_advisory_queue)
                or (args.m07_main_readonly_cycle_once
                    and args.m07_owner_preview_m05)):
            raise ConfigurationError("M05_SHADOW_ONLY_ON_MAINPATH")
        if ((args.m05_telegram_poll_once
             or args.m05_telegram_readonly_preflight_once
             or args.m05_telegram_owner_safe_status_once)
                and args.m05_shadow_db is None):
            raise ConfigurationError("M05_TELEGRAM_OWNER_DB_REQUIRED")
        if args.m05_shadow_db is not None and (
                Path(args.m05_shadow_db).resolve() == Path(args.db).resolve()
                or (args.ws_db is not None
                    and Path(args.m05_shadow_db).resolve() == Path(args.ws_db).resolve())):
            raise ConfigurationError("M05_SHADOW_DB_MUST_BE_SEPARATE")
        if args.m07_owner_preview_m05:
            # NEVER initialize a new owner DB for runtime preview. The same
            # existing dedicated Telegram service owns this 0600 SQLite,
            # its private owner identity and next update cursor.
            from .telegram_service_tick import _existing_owner_ledger
            owner_control = _existing_owner_ledger(
                Path(args.m05_shadow_db), args.m05_shadow_owner_id,
            )
        else:
            owner_control = (
                OwnerDecisionLedger(
                    args.m05_shadow_db, owner_user_id=args.m05_shadow_owner_id,
                )
                if args.m05_shadow_db is not None else None
            )
        try:
            bulk_min_volume_idr = Decimal(args.bulk_min_volume_idr)
        except (InvalidOperation, ValueError, TypeError):
            raise ConfigurationError("INVALID_BULK_MIN_VOLUME_IDR") from None
        if not bulk_min_volume_idr.is_finite() or bulk_min_volume_idr < 0:
            raise ConfigurationError("INVALID_BULK_MIN_VOLUME_IDR")
        if bulk_min_volume_idr != 0 and not (args.bulk_deep_once or args.scheduled_public_scanner_once or
                                                args.m07_main_readonly_cycle_once or
                                                (args.once and args.public_scanner_mainpath)):
            raise ConfigurationError("BULK_VOLUME_FLOOR_REQUIRES_BULK_DEEP_ONCE")
        if (type(args.bulk_deep_limit) is not int or not 20 <= args.bulk_deep_limit <= 40):
            raise ConfigurationError("BULK_DEEP_LIMIT_INVALID")
        # The scheduled timer is deliberately fixed at deep20; check this
        # before the generic bulk-only limit guard so an invalid scheduled40
        # is rejected with its precise, stable fail-closed reason.
        if args.scheduled_public_scanner_once and args.bulk_deep_limit != 20:
            raise ConfigurationError("M03_SCHEDULED_BULK_LIMIT_MUST_BE_20")
        if args.bulk_deep_limit != 20 and not (args.bulk_deep_once or
                                                  args.m07_main_readonly_cycle_once or
                                                  (args.once and args.public_scanner_mainpath)):
            raise ConfigurationError("BULK_DEEP_LIMIT_REQUIRES_BULK_DEEP_ONCE")
        if args.public_scanner_mainpath:
            if not args.once:
                raise ConfigurationError("M03_MAINPATH_REQUIRES_ONCE")
            if args.ws_db and Path(args.ws_db).resolve() == Path(args.db).resolve():
                raise ConfigurationError("M03_MAINPATH_WS_DB_MUST_BE_SEPARATE")
            # Optional public BTC WS source: missing/not-running WS becomes an
            # explicit diagnostic, not a pre-final owner REJECT. Do not invent
            # a source receipt or promote candidate trust.
        if args.scheduled_public_scanner_once:
            # Keep original path distinct, but the BTC SQLite is only
            # required by DEEP20. Never refuse a LIGHT WATCH tick because
            # an independent BTC collector has not produced a journal.
            if not args.ws_db:
                raise ConfigurationError("M03_SCHEDULED_REQUIRES_BTC_WS_DB")
            if Path(args.ws_db).resolve() == Path(args.db).resolve():
                raise ConfigurationError("M03_SCHEDULED_WS_DB_MUST_BE_SEPARATE")
            # Missing BTC journal is handled at DEEP20 dispatch by
            # downgrading to LIGHT; no fake source, G1 or buy permission.
        # M04-V: only explicit bulk CLI opt-in constructs the THREE-ROLE
        # Sumopod adapter from protected process environment. It is always
        # payment-disabled at this milestone and public G1 cannot qualify it.
        if args.sumopod_shadow and not args.bulk_deep_once:
            raise ConfigurationError("SUMOPOD_SHADOW_REQUIRES_BULK_DEEP_ONCE")
        if args.deep_pair is not None and not args.deep_once:
            raise ConfigurationError("DEEP_PAIR_REQUIRES_DEEP_ONCE")
        if args.m05_telegram_owner_safe_status_once:
            # SEND-ONLY: cannot fetch/ack updates, compete for polling
            # offsets, change webhooks, or generate a trade authorization.
            settings.require_m01_safety()
            if environ.get("M05_TELEGRAM_OWNER_STATUS_GO") != "YES":
                raise ConfigurationError("M05_TELEGRAM_OWNER_STATUS_GO_REQUIRED")
            bot_id = environ.get("M05_TELEGRAM_EXPECTED_BOT_ID", "")
            if (not bot_id.isascii() or not bot_id.isdecimal()
                    or not 1 <= len(bot_id) <= 19
                    or int(bot_id) <= 0):
                raise ConfigurationError("M05_OUTBOUND_EXPECTED_BOT_ID_REQUIRED")
            api = TelegramBotApiClient(
                token=environ.get("M05_TELEGRAM_BOT_TOKEN", ""),
                allow_network=True, timeout_seconds=6,
            )
            report = send_owner_safe_status_once(
                api=api, ledger=owner_control,
                expected_bot_id=int(bot_id),
            )
            print(json.dumps({
                "m05_telegram_owner_outbound": report,
                "final_decision": None,
                "execution_allowed": False,
                "live_trading_authorized": False,
            }, sort_keys=True))
            return 0
        if args.m05_telegram_readonly_preflight_once:
            # This branch may ONLY request getMe and getWebhookInfo. It
            # never obtains update_id, authenticates an owner or sends chat.
            settings.require_m01_safety()
            if environ.get("M05_TELEGRAM_PREFLIGHT_GO") != "YES":
                raise ConfigurationError("M05_TELEGRAM_PREFLIGHT_GO_REQUIRED")
            expected_text = environ.get("M05_TELEGRAM_EXPECTED_BOT_ID")
            if expected_text is not None and (
                    not expected_text.isascii()
                    or not expected_text.isdecimal()
                    or len(expected_text) > 19
                    or int(expected_text) <= 0):
                raise ConfigurationError("M05_PREFLIGHT_INVALID_EXPECTED_BOT_ID")
            expected = int(expected_text) if expected_text is not None else None
            api = TelegramBotApiClient(
                token=environ.get("M05_TELEGRAM_BOT_TOKEN", ""),
                allow_network=True, timeout_seconds=6,
            )
            report = inspect_telegram_readonly(
                api, expected_bot_id=expected,
            )
            print(json.dumps({
                "m05_telegram_readonly_preflight": report,
                "final_decision": None,
                "execution_allowed": False,
                "live_trading_authorized": False,
            }, sort_keys=True))
            # Discovery and webhook-conflict states are diagnostic only.
            return 0 if (
                report["bot_id_pinned_and_matched"] and
                report["polling_compatible_with_webhook_state"]
            ) else 3
        if args.m05_telegram_poll_once:
            # The old Caipiti bot serves the legacy gateway: NEVER poll it.
            # A fresh dedicated V2 bot, pinned to its true token prefix, has
            # separate operator GO and local file lock protecting its cursor.
            settings.require_m01_safety()
            if environ.get("M05_TELEGRAM_NETWORK_GO") != "YES":
                raise ConfigurationError("M05_TELEGRAM_NETWORK_GO_REQUIRED")
            if environ.get("M05_TELEGRAM_DEDICATED_GO") != "YES":
                raise ConfigurationError("M05_TELEGRAM_DEDICATED_GO_REQUIRED")
            raw_bot_id = environ.get("M05_TELEGRAM_DEDICATED_BOT_ID", "")
            if (not raw_bot_id.isascii() or not raw_bot_id.isdecimal()
                    or not 1 <= len(raw_bot_id) <= 19
                    or int(raw_bot_id) <= 0):
                raise ConfigurationError("M05_DEDICATED_BOT_ID_REQUIRED")
            bot_id = int(raw_bot_id)
            if bot_id == LEGACY_CAIPITI_BOT_ID:
                raise ConfigurationError("M05_DEDICATED_EXISTING_CAIPITI_DENIED")
            token = environ.get("M05_TELEGRAM_BOT_TOKEN", "")
            if token.split(":", 1)[0] != raw_bot_id:
                raise ConfigurationError("M05_DEDICATED_TOKEN_ID_MISMATCH")
            api = TelegramBotApiClient(
                token=token, allow_network=True, timeout_seconds=6,
            )
            summary = dedicated_owner_poll_once(
                api=api, ledger=owner_control, expected_bot_id=bot_id,
                lock_path=str(Path(args.m05_shadow_db).absolute()) +
                          ".dedicated-poller.lock",
                limit=10,
            )
            print(json.dumps({
                "m05_telegram_dedicated_poll": summary,
                "final_decision": None,
                "execution_allowed": False,
                "live_trading_authorized": False,
            }, sort_keys=True))
            return 0
        # Only explicit nonlive --bulk-deep-once --sumopod-shadow may
        # instantiate the already existing adapter. Reuse the EXACT same
        # owner-protected V2 config resolution as local readiness; never
        # start paid network, fabricate G1 receipts, or change scanner.
        provider = None
        if args.sumopod_shadow:
            local_source, _, _ = _m04_original_provider_scope()
            provider = SumopodChatAdapter.configured_from_env(local_source)
        if args.public_ws_unit_status:
            # No DecisionJournal, no systemctl state change, and no network.
            settings.require_m01_safety()
            report = inspect_public_ws_systemd_unit()
            print(json.dumps({
                "public_ws_unit_status": report,
                "final_decision": None,
                "execution_allowed": False,
                "live_trading_authorized": False,
            }, sort_keys=True))
            return 0 if report["status"] in (
                "STAGED_NOT_INSTALLED", "USER_UNIT_RUNNING_OBSERVED",
                "USER_UNIT_INACTIVE_OR_FAILED",
            ) else 3
        if args.public_ws_health:
            # Important: create NO DecisionJournal here. DB and status are
            # both read-only, and no G1/role provider/executor is contacted.
            settings.require_m01_safety()
            report = public_ws_health(
                args.db, args.supervisor_status_file or "",
                max_age_seconds=args.health_max_age_seconds,
            )
            print(json.dumps({
                "public_ws_health": report,
                "final_decision": None,
                "execution_allowed": False,
                "live_trading_authorized": False,
            }, sort_keys=True))
            return 0 if report["last_batch_recent_and_consistent"] is True else 3
        app = DailyScalpingOrchestrator(
            settings, DecisionJournal(args.db), ai_role_caller=provider,
            owner_control=owner_control,
            m03_owner_fee_snapshot=m03_owner_fee_snapshot,
        )
        if args.m09_paper_offline_once:
            settings.require_m01_safety()
            try:
                raw=_owner_private_file(args.m09_paper_file,max_bytes=4096)
                action=raw.get("action")
                fields={
                    "BUY":{
                        "paper_id","symbol","buy_qty","buy_price_idr",
                        "buy_all_in_fee_idr","buy_epoch",
                    },
                    "SELL":{
                        "paper_id","paper_exit_id","sell_qty",
                        "sell_price_idr","sell_all_in_fee_idr",
                        "incremental_slippage_idr","sell_epoch",
                    },
                    "REVIEW":{
                        "paper_id","observed_epoch","book_observed_epoch",
                        "tier_sell_qty","bid_levels","best_ask",
                        "taker_sell_fee_pct","maker_sell_fee_pct",
                        "taker_slippage_pct",
                    },
                    "SUMMARY":{"day_wib"},
                }
                if action not in fields or set(raw)!=(fields[action]|{"action"}):
                    raise ValueError("M09_PAPER_FIXTURE_SCHEMA_INVALID")
                typed={}
                money_keys={
                    "buy_qty","buy_price_idr","buy_all_in_fee_idr",
                    "sell_qty","sell_price_idr","sell_all_in_fee_idr",
                    "incremental_slippage_idr",
                }
                money_keys.update(("tier_sell_qty","best_ask",
                                   "taker_sell_fee_pct","maker_sell_fee_pct",
                                   "taker_slippage_pct"))
                epoch_keys={"buy_epoch","sell_epoch","observed_epoch",
                            "book_observed_epoch"}
                for k in fields[action]:
                    v=raw[k]
                    if k in money_keys:
                        if type(v) is not str or len(v)>45:
                            raise ValueError("M09_PAPER_DECIMAL_INPUT_INVALID")
                        typed[k]=Decimal(v)
                    elif k in epoch_keys:
                        if type(v) is not int:
                            raise ValueError("M09_PAPER_EPOCH_INPUT_INVALID")
                        typed[k]=v
                    elif k=="bid_levels":
                        if (type(v) is not list or len(v)>20
                                or any(type(level) is not list or len(level)!=2
                                       or any(type(x) is not str or len(x)>45
                                              for x in level) for level in v)):
                            raise ValueError("M09_PAPER_BOOK_INPUT_INVALID")
                        typed[k]=tuple(tuple(Decimal(x) for x in level)
                                       for level in v)
                    elif k=="day_wib":
                        if v is not None and type(v) is not str:
                            raise ValueError("M09_PAPER_DATE_INPUT_INVALID")
                        typed[k]=v
                    else:
                        if type(v) is not str or len(v)>45:
                            raise ValueError("M09_PAPER_IDENTITY_INPUT_INVALID")
                        typed[k]=v
                result=app.run_m09_original_paper_cycle_offline_once(
                    action=action,details=typed,
                    include_m11_risk_halt_readonly_reference=(
                        args.m09_paper_risk_halt_reference),
                )
            except (M06ScopeDenied,OSError,ValueError,TypeError,
                    ArithmeticError,DecisionConflictError,
                    sqlite3.Error) as exc:
                reason=str(exc)
                if re.fullmatch(r"M09_PAPER_[A-Z0-9_]{1,100}",reason) is None:
                    reason="M09_PAPER_SOURCE_OR_SIMULATION_UNAVAILABLE"
                print(json.dumps({
                    "m09_paper":{
                        "status":"UNAVAILABLE","reason":reason,
                        "order_sent":False,"execution_allowed":False,
                    },
                    "final_decision":None,"execution_allowed":False,
                    "live_trading_authorized":False,
                },sort_keys=True),file=sys.stderr)
                return 3
            print(json.dumps({
                "m09_paper":result,
                "final_decision":None,
                "execution_allowed":False,
                "live_trading_authorized":False,
            },sort_keys=True))
            return 0
        if args.m09_recovery_shadow_once:
            settings.require_m01_safety()
            try:
                source=_owner_private_file(args.m09_recovery_file)
                fields={
                    "account_fingerprint","symbol",
                    "source_buy_client_order_id","sell_client_order_id",
                    "requested_settlement_event_id",
                }
                if (type(source) is not dict or set(source)!=fields
                        or any(type(source[k]) is not str
                               or len(source[k])>70 for k in fields)):
                    raise ValueError("M09_RECOVERY_SOURCE_IDENTITY_INVALID")
                report=app.run_m09_original_recovery_shadow_once(
                    uuid4().hex,
                    account_fingerprint=source["account_fingerprint"],
                    symbol=source["symbol"],
                    source_buy_client_order_id=source[
                        "source_buy_client_order_id"],
                    sell_client_order_id=source["sell_client_order_id"],
                    requested_settlement_event_id=source[
                        "requested_settlement_event_id"],
                    ws_db_path=args.ws_db,
                    deep_limit=args.bulk_deep_limit,
                )
            except (ValueError,DecisionConflictError,sqlite3.Error,
                    OSError) as exc:
                reason=str(exc)
                if re.fullmatch(r"M09_[A-Z0-9_]{1,100}",reason) is None:
                    reason="M09_RECOVERY_SHADOW_SOURCE_UNAVAILABLE"
                print(json.dumps({
                    "m09_recovery_shadow":{
                        "status":reason,
                        "post_sell_scanner_invoked":False,
                        "signed_order_sent":False,
                        "execution_allowed":False,
                    },
                    "final_decision":None,"execution_allowed":False,
                    "live_trading_authorized":False,
                },sort_keys=True),file=sys.stderr)
                return 3
            print(json.dumps({
                "m09_recovery_shadow":report,
                "final_decision":None,"execution_allowed":False,
                "live_trading_authorized":False,
            },sort_keys=True))
            return 0
        if args.m09_shadow_mainpath_once:
            settings.require_m01_safety()
            report = app.run_m09_original_shadow_mainpath_once(
                uuid4().hex, ws_db_path=args.ws_db,
                deep_limit=args.bulk_deep_limit,
            )
            print(json.dumps({
                "m09_original_shadow":report,
                "final_decision":None,
                "execution_allowed":False,
                "live_trading_authorized":False,
            },sort_keys=True))
            return 0
        if args.m08_post_sell_rediscovery_offline_once:
            # No fabricated settlement, private key, stale proposal reuse,
            # previous-pair bias, or BUY. Only future canonical M08-06
            # event may claim and invoke original PUBLIC scanner once.
            settings.require_m01_safety()
            try:
                result = app.run_m08_post_sell_rediscovery_once(
                    settlement_event_id=args.m08_settled_event_id,
                    scan_trace_id=uuid4().hex,
                    observed_epoch=int(time.time()),
                    deep_limit=20,
                )
            except (ValueError,DecisionConflictError,PublicDataError,
                    sqlite3.Error) as exc:
                reason=str(exc)
                if re.fullmatch(r"M08_[A-Z0-9_]{1,100}",reason) is None:
                    reason="M08_POST_SELL_HANDOFF_UNAVAILABLE"
                print(json.dumps({
                    "m08_post_sell_rediscovery":{
                        "status":reason,
                        "settled_event_claimed":False,
                        "original_bulk_scanner_invoked":False,
                        "execution_allowed":False,
                    },
                    "final_decision":None,"execution_allowed":False,
                    "live_trading_authorized":False,
                },sort_keys=True),file=sys.stderr)
                return 3
            print(json.dumps({
                "m08_post_sell_rediscovery":result,
                "final_decision":None,"execution_allowed":False,
                "live_trading_authorized":False,
            },sort_keys=True))
            return 0
        if args.m08_sell_preflight_offline_once:
            # Original production CLI → ONE DailyScalpingOrchestrator →
            # original M07/M08 journal and M06 ownership guard. Fixture
            # isolated to /tmp, no credentials or network/API operations.
            settings.require_m01_safety()
            try:
                fixture_path=Path(args.m08_sell_preflight_file)
                if fixture_path.stat().st_size > 4096:
                    raise ValueError("M08_SELL_PREFLIGHT_FIXTURE_TOO_LARGE")
                raw=_owner_private_file(args.m08_sell_preflight_file)
                exact={
                    "account_fingerprint","account_uid",
                    "source_buy_client_order_id","sell",
                    "observed_epoch","expected_sell_notional_idr",
                    "estimated_sell_fees_idr","incremental_slippage_idr",
                    "observed_mark_price_idr","stop_loss_price_idr",
                    "high_watermark_price_idr","trailing_stop_price_idr",
                    "quote_fresh_unverified","pump_continuation_unverified",
                    "risk_review_clear_unverified",
                }
                if type(raw) is not dict or set(raw) != exact:
                    raise ValueError("M08_SELL_PREFLIGHT_FIXTURE_SCHEMA_INVALID")
                sell=raw["sell"]
                if (type(sell) is not dict
                        or set(sell) != {"symbol","client_order_id",
                                         "price","quantity"}
                        or any(type(sell[k]) is not str for k in sell)
                        or type(raw["account_uid"]) is not int
                        or type(raw["observed_epoch"]) is not int
                        or type(raw["account_fingerprint"]) is not str
                        or type(raw["source_buy_client_order_id"]) is not str
                        or any(type(raw[k]) is not bool for k in (
                            "quote_fresh_unverified",
                            "pump_continuation_unverified",
                            "risk_review_clear_unverified"))):
                    raise ValueError("M08_SELL_PREFLIGHT_FIXTURE_SCHEMA_INVALID")
                amounts={}
                for k in ("expected_sell_notional_idr",
                          "estimated_sell_fees_idr","incremental_slippage_idr",
                          "observed_mark_price_idr","stop_loss_price_idr",
                          "high_watermark_price_idr"):
                    if type(raw[k]) is not str or len(raw[k]) > 40:
                        raise ValueError("M08_SELL_PREFLIGHT_AMOUNT_INVALID")
                    amounts[k]=Decimal(raw[k])
                trail=raw["trailing_stop_price_idr"]
                if trail is not None and (type(trail) is not str
                                          or len(trail) > 40):
                    raise ValueError("M08_SELL_PREFLIGHT_AMOUNT_INVALID")
                draft=M07OrderDraft(
                    symbol=sell["symbol"],side="SELL",order_type="LIMIT",
                    client_order_id=sell["client_order_id"],
                    price=Decimal(sell["price"]),
                    quantity=Decimal(sell["quantity"]),
                )
                report=app.review_m08_sell_preflight_offline_once(
                    sell_draft=draft,
                    source_buy_client_order_id=raw["source_buy_client_order_id"],
                    account_fingerprint=raw["account_fingerprint"],
                    account_uid=raw["account_uid"],
                    local_m06_ledger_db=args.m06_ownership_db,
                    observed_epoch=raw["observed_epoch"],
                    expected_sell_notional_idr=amounts[
                        "expected_sell_notional_idr"],
                    estimated_sell_fees_idr=amounts[
                        "estimated_sell_fees_idr"],
                    incremental_slippage_idr=amounts[
                        "incremental_slippage_idr"],
                    observed_mark_price_idr=amounts[
                        "observed_mark_price_idr"],
                    stop_loss_price_idr=amounts[
                        "stop_loss_price_idr"],
                    high_watermark_price_idr=amounts[
                        "high_watermark_price_idr"],
                    trailing_stop_price_idr=(
                        Decimal(trail) if trail is not None else None),
                    quote_fresh_unverified=raw["quote_fresh_unverified"],
                    pump_continuation_unverified=raw[
                        "pump_continuation_unverified"],
                    risk_review_clear_unverified=raw[
                        "risk_review_clear_unverified"],
                )
            except (OSError,ValueError,TypeError,KeyError,ArithmeticError,
                    InvalidOperation,DecisionConflictError,M06ScopeDenied,
                    sqlite3.Error) as exc:
                reason=str(exc)
                if re.fullmatch(r"M08_[A-Z0-9_]{1,100}", reason) is None:
                    reason="M08_SELL_PREFLIGHT_OFFLINE_UNAVAILABLE"
                print(json.dumps({
                    "m08_sell_preflight_offline":{
                        "status":"UNRESOLVED","reason":reason,
                        "order_sent":False,"execution_allowed":False,
                    },
                    "final_decision":None,
                    "execution_allowed":False,
                    "live_trading_authorized":False,
                },sort_keys=True),file=sys.stderr)
                return 3
            print(json.dumps({
                "m08_sell_preflight_offline":report,
                "final_decision":None,"execution_allowed":False,
                "live_trading_authorized":False,
            },sort_keys=True))
            return 0
        if args.m07_main_readonly_cycle_once:
            # Unlike offline sizing fixtures, this path runs the ORIGINAL
            # M03 scanner once, then EXACT two M06 signed View Only GETs.
            # Only operator-entered future liabilities / ticket cap may be
            # supplied; no public market or wallet data can be injected.
            settings.require_m01_safety()
            try:
                if (type(args.ws_db) is not str
                        or not Path(args.ws_db).is_absolute()
                        or not Path(args.ws_db).is_file()
                        or Path(args.ws_db).resolve() == Path(args.db).resolve()):
                    raise M07PreflightBlocked("M07_RUNTIME_BTC_WS_DB_REQUIRED")
                if (type(args.m07_runtime_funding_file) is not str
                        or not Path(args.m07_runtime_funding_file).is_absolute()):
                    raise M07PreflightBlocked("M07_RUNTIME_FUNDING_FILE_REQUIRED")
                raw = _owner_private_file(args.m07_runtime_funding_file)
                from .m07_main_idr_sizing import valid_main_funding_schema
                if not valid_main_funding_schema(raw):
                    raise M07PreflightBlocked("M07_RUNTIME_FUNDING_SCHEMA_INVALID")
                keys = set(raw)
                if any(type(raw[k]) is not str or len(raw[k]) > 40
                       or not raw[k].isascii() for k in keys):
                    raise M07PreflightBlocked("M07_RUNTIME_FUNDING_AMOUNT_INVALID")
                funding = {k: Decimal(raw[k]) for k in keys}
                if (any(not v.is_finite() or v < 0 for v in funding.values())
                        or ("requested_ticket_ceiling_idr" in funding
                            and funding["requested_ticket_ceiling_idr"] <= 0)
                        or funding["max_spread_bps"] > 500):
                    raise M07PreflightBlocked("M07_RUNTIME_FUNDING_AMOUNT_INVALID")
                credentials_path = _existing_v2_credentials()
                if credentials_path is None:
                    raise M07PreflightBlocked("M07_RUNTIME_VIEWONLY_CREDENTIALS_NOT_FOUND")
                api_key, secret_key = read_view_only_credentials(
                    str(credentials_path),
                )
                report = app.run_m07_original_source_join_readonly_once(
                    uuid4().hex, api_key=api_key, secret_key=secret_key,
                    funding=funding, local_ownership_db=args.m06_ownership_db,
                    ws_db_path=args.ws_db,
                    min_volume_idr=max(bulk_min_volume_idr,
                                       Decimal("150000000")),
                    deep_limit=args.bulk_deep_limit,
                    include_m11_source_observation=(
                        args.m07_readonly_m11_source_observation),
                    owner_preview_user_id=(
                        args.m05_shadow_owner_id if
                        args.m07_owner_preview_m05 else None),
                )
            except (M07PreflightBlocked, M06ScopeDenied, ValueError,
                    OSError, ArithmeticError, TypeError, KeyError,
                    sqlite3.Error) as exc:
                reason = str(exc)
                if not re.fullmatch(r"M07_RUNTIME_[A-Z0-9_]{1,90}", reason):
                    reason = "M07_RUNTIME_SOURCE_OR_ACCOUNT_UNRESOLVED"
                print(json.dumps({
                    "stage": "M07_ORIGINAL_LIVE_SOURCE_JOIN_READONLY",
                    "status": reason,
                    "final_decision": None,
                    "execution_allowed": False,
                    "order_sent": False,
                }, sort_keys=True), file=sys.stderr)
                return 3
            print(json.dumps({
                "m07_original_live_source_join": report,
                "final_decision": None,
                "execution_allowed": False,
                "live_trading_authorized": False,
            }, sort_keys=True))
            # Exit zero means the scoped ORIGINAL View Only read succeeded,
            # NOT that an order or M07 sizing was permitted. Public fee/rules
            # can remain UNVERIFIED, with executor still hardlocked.
            return 0 if report.get("m06_authenticated_viewonly_get_count") == 2 else 3
        if (args.m07_private_order_status_once
                or args.m07_private_latest_order_once
                or args.m07_private_14day_order_once
                or args.m07_private_fill_fees_once):
            # Reuse only the preexisting V2 owner-only 0600 credentials.
            # No discovery outside V2, no secrets in logs or JSON result.
            try:
                credentials_path = _existing_v2_credentials()
                if credentials_path is None:
                    raise M07ReadOnlyError("M07_V2_VIEWONLY_FILE_NOT_FOUND")
                api_key, secret_key = read_view_only_credentials(
                    str(credentials_path)
                )
                if args.m07_private_order_status_once:
                    report = app.inspect_m07_private_readonly_order_once(
                        symbol=args.m07_private_symbol,
                        client_order_id=args.m07_private_client_order_id,
                        api_key=api_key, secret_key=secret_key,
                    )
                elif args.m07_private_latest_order_once:
                    report = app.inspect_m07_private_latest_order_once(
                        symbol=args.m07_private_symbol,
                        api_key=api_key, secret_key=secret_key,
                    )
                elif args.m07_private_14day_order_once:
                    report = app.inspect_m07_private_14day_order_once(
                        symbol=args.m07_private_symbol,
                        api_key=api_key, secret_key=secret_key,
                    )
                else:
                    report = app.inspect_m07_private_fill_fees_once(
                        symbol=args.m07_private_symbol,
                        api_key=api_key, secret_key=secret_key,
                    )
            except (ValueError, OSError, DecisionConflictError,
                    M06ScopeDenied) as exc:
                # Only fixed diagnostic classes, never raw exchange payload,
                # raw file path, HTTP bodies, IDs or credential material.
                code = str(exc)
                if not re.fullmatch(r"M07_[A-Z0-9_]{1,100}", code):
                    code = "M07_READONLY_STATUS_UNAVAILABLE"
                print(json.dumps({
                    "m07_private_readonly_order": {
                        "status": "UNRESOLVED", "reason": code,
                        "execution_allowed": False,
                    }, "final_decision": None,
                    "execution_allowed": False,
                }), file=sys.stderr)
                return 3
            print(json.dumps({
                "m07_private_readonly_order": report,
                "final_decision": None,
                "execution_allowed": False,
                "live_trading_authorized": False,
            }, sort_keys=True))
            return 0
        if (args.m07_offline_intent_once or args.m07_offline_unknown_once
                or args.m07_offline_status_once):
            try:
                if args.m07_offline_intent_once:
                    result = app.reserve_m07_offline_intent_once(
                        intent_id=args.m07_intent_id,
                        draft=M07OrderDraft(
                            "BTCIDR", "BUY", "MARKET",
                            args.m07_intent_id, quote_order_qty=10000,
                        ),
                        observed_epoch=int(time.time()),
                    )
                elif args.m07_offline_unknown_once:
                    result = app.simulate_m07_unknown_timeout_once(
                        intent_id=args.m07_intent_id,
                        observed_epoch=int(time.time()),
                    )
                else:
                    result = app.observe_m07_offline_status_once(
                        M07OfflineStatusObservation(
                            intent_id=args.m07_intent_id,
                            client_order_id=args.m07_intent_id,
                            symbol="BTCIDR",
                            observation_id=args.m07_observation_id,
                            status=args.m07_observed_status,
                            cumulative_base=Decimal(args.m07_cumulative_base),
                            cumulative_quote=Decimal(args.m07_cumulative_quote),
                            observed_epoch=int(time.time()),
                        ),
                    )
            except DecisionConflictError as exc:
                print(json.dumps({
                    "m07_offline_intent": {
                        "status": "BLOCKED",
                        "reason": str(exc),
                        "execution_allowed": False,
                        "order_sent": False,
                    },
                    "final_decision": None,
                    "execution_allowed": False,
                }, sort_keys=True), file=sys.stderr)
                return 3
            print(json.dumps({
                "m07_offline_intent": result,
                "final_decision": None,
                "execution_allowed": False,
                "live_trading_authorized": False,
            }, sort_keys=True))
            return 0
        if args.m07_main_sizing_offline_once:
            # Deliberately accepts *untrusted* local observations only.
            # This is the original CLI->orchestrator M07 preview, not a
            # private API poll, owner Final, journal intent, or new executor.
            settings.require_m01_safety()
            try:
                if (type(args.m07_main_sizing_file) is not str
                        or not args.m07_main_sizing_file.startswith("/")):
                    raise M07PreflightBlocked("M07_MAIN_SIZING_FILE_REQUIRED")
                raw = _owner_private_file(args.m07_main_sizing_file)
                from .m07_main_idr_sizing import valid_main_funding_schema
                required = {"public_candidate", "account_snapshot",
                            "owner_main_source_classified"}
                if (type(raw) is not dict or not required.issubset(raw)
                        or not valid_main_funding_schema(
                            {k:v for k,v in raw.items() if k not in required})
                        or type(raw["public_candidate"]) is not dict
                        or type(raw["account_snapshot"]) is not dict
                        or type(raw["owner_main_source_classified"]) is not bool):
                    raise M07PreflightBlocked("M07_MAIN_SIZING_FILE_SCHEMA_INVALID")
                numeric = set(raw) - required
                if any(type(raw[k]) is not str or len(raw[k]) > 40
                       or not raw[k].isascii() for k in numeric):
                    raise M07PreflightBlocked("M07_MAIN_SIZING_AMOUNT_INVALID")
                sized = app.review_m07_owner_main_idr_sizing_offline(
                    public_candidate=raw["public_candidate"],
                    account_snapshot=raw["account_snapshot"],
                    owner_main_source_classified=raw["owner_main_source_classified"],
                    **{k: Decimal(raw[k]) for k in numeric},
                    now_epoch=int(time.time()),
                )
            except (M07PreflightBlocked, M06ScopeDenied,
                    ArithmeticError, ValueError, TypeError, KeyError) as exc:
                reason = str(exc)
                if not re.fullmatch(r"M07_MAIN_SIZING_[A-Z0-9_]{1,90}", reason):
                    reason = "M07_MAIN_SIZING_INVALID_UNTRUSTED_INPUT"
                print(json.dumps({
                    "m07_main_sizing": {"status":"BLOCKED", "reason":reason,
                                         "execution_allowed":False,
                                         "order_sent":False},
                    "final_decision":None, "execution_allowed":False,
                }, sort_keys=True), file=sys.stderr)
                return 3
            print(json.dumps({
                "m07_main_sizing":sized,
                "source_scope":"UNTRUSTED_LOCAL_FIXTURE_OFFLINE_ONLY",
                "final_decision":None, "execution_allowed":False,
                "live_trading_authorized":False,
            }, sort_keys=True))
            return 0
        if args.m07_limit_preflight_offline_once:
            settings.require_m01_safety()
            try:
                if (type(args.m07_limit_preflight_file) is not str
                        or not args.m07_limit_preflight_file.startswith("/")):
                    raise M07PreflightBlocked("M07_PREFLIGHT_FILE_REQUIRED")
                raw = _owner_private_file(args.m07_limit_preflight_file)
                required = {
                    "symbol", "client_order_id", "limit_price_idr",
                    "quantity_base", "pair_rules",
                    "best_bid_idr", "best_ask_idr",
                    "free_idr", "locked_idr", "floki_rebuy_idr",
                    "pepe_rebuy_idr", "commitments_not_locked_idr",
                    "buy_all_in_fee_bps", "sell_all_in_fee_bps",
                    "max_spread_bps",
                }
                if set(raw) != required:
                    raise M07PreflightBlocked("M07_PREFLIGHT_FILE_SCHEMA_INVALID")
                fields = required - {"symbol","client_order_id","pair_rules"}
                if any(type(raw[k]) is not str or len(raw[k]) > 48
                       for k in fields):
                    raise M07PreflightBlocked("M07_PREFLIGHT_FILE_AMOUNT_INVALID")
                draft = M07OrderDraft(
                    raw["symbol"], "BUY", "LIMIT", raw["client_order_id"],
                    price=Decimal(raw["limit_price_idr"]),
                    quantity=Decimal(raw["quantity_base"]),
                )
                preview = app.assess_m07_limit_buy_preflight_offline(
                    draft=draft, pair_rules=raw["pair_rules"],
                    **{k: Decimal(raw[k]) for k in (
                        "best_bid_idr", "best_ask_idr", "free_idr",
                        "locked_idr", "floki_rebuy_idr", "pepe_rebuy_idr",
                        "commitments_not_locked_idr", "buy_all_in_fee_bps",
                        "sell_all_in_fee_bps", "max_spread_bps",
                    )}
                )
            except (M07PreflightBlocked, M06ScopeDenied,
                    ArithmeticError, ValueError, TypeError) as exc:
                reason = str(exc)
                if not re.fullmatch(r"M07_PREFLIGHT_[A-Z0-9_]{1,90}", reason):
                    reason = "M07_PREFLIGHT_INVALID_UNTRUSTED_INPUT"
                print(json.dumps({
                    "m07_limit_preflight": {
                        "status": "BLOCKED", "reason": reason,
                        "execution_allowed": False, "order_sent": False,
                    },
                    "final_decision": None, "execution_allowed": False,
                }, sort_keys=True), file=sys.stderr)
                return 3
            # Same original M07 journal/owner/account gate; synthetic CLI
            # fixture never becomes trusted M06 or M03 evidence.
            late_gate = app.review_m07_last_moment_gate_offline(
                limit_preview=preview, now_epoch=int(time.time()),
            )
            result = {
                "m07_last_moment_gate": late_gate,
                "m07_limit_preflight": asdict(preview),
                "final_decision": None,
                "execution_allowed": False,
                "live_trading_authorized": False,
            }
            if args.m07_preflight_risk_halt_reference:
                try:
                    result["m07_risk_halt_readonly_reference"] = (
                        app.review_m07_preflight_m11_risk_halt_offline_once(
                            limit_preview=preview, last_moment=late_gate,
                        )
                    )
                except (ValueError, OSError, sqlite3.Error):
                    print(json.dumps({
                        "m07_limit_preflight": {
                            "status": "BLOCKED",
                            "reason": "M07_PREFLIGHT_RISK_REFERENCE_UNAVAILABLE",
                            "order_sent": False, "execution_allowed": False,
                        },
                        "final_decision": None, "execution_allowed": False,
                        "live_trading_authorized": False,
                    }, sort_keys=True), file=sys.stderr)
                    return 3
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.m07_order_adapter_offline_once:
            settings.require_m01_safety()
            report = app.inspect_m07_official_order_adapter_once()
            print(json.dumps({
                "m07_official_order_adapter": report,
                "final_decision": None,
                "execution_allowed": False,
                "live_trading_authorized": False,
            }, sort_keys=True))
            return 0
        if args.health:
            result = app.health()
        else:
            if args.public_ws_supervised_once:
                outcome = app.run_public_ws_supervised_cycle(
                    uuid4().hex, db_path=args.db, pair=args.pair,
                    sessions=args.collector_sessions,
                    seconds=args.stream_seconds,
                    pause_seconds=args.collector_pause_seconds,
                    status_file=args.supervisor_status_file,
                    reconnect_until_recovered=args.public_ws_reconnect_until_recovered,
                )
            elif args.public_ws_collect:
                outcome = app.run_public_ws_collect_cycle(
                    uuid4().hex, db_path=args.db, pair=args.pair,
                    sessions=args.collector_sessions,
                    seconds=args.stream_seconds,
                    pause_seconds=args.collector_pause_seconds,
                )
            elif args.public_ws_once:
                outcome = app.run_public_ws_cycle(uuid4().hex, db_path=args.db,
                                                  pair=args.pair, seconds=args.stream_seconds)
            elif args.bulk_deep_once:
                if bulk_min_volume_idr > 0:
                    outcome = app.run_bulk_deep_cycle(
                        uuid4().hex, ws_db_path=args.ws_db,
                        min_volume_idr=bulk_min_volume_idr,
                        deep_limit=args.bulk_deep_limit,
                    )
                else:
                    if args.bulk_deep_limit == 20:
                        outcome = app.run_bulk_deep_cycle(uuid4().hex, ws_db_path=args.ws_db)
                    else:
                        outcome = app.run_bulk_deep_cycle(
                            uuid4().hex, ws_db_path=args.ws_db,
                            deep_limit=args.bulk_deep_limit,
                        )
            elif args.rolling_deep_once:
                outcome = app.run_rolling_deep_cycle(uuid4().hex)
            elif args.deep_once:
                outcome = app.run_deep_cycle(
                    uuid4().hex, target_pair=args.deep_pair,
                )
            elif args.public_history_archive_once:
                outcome = app.run_public_history_archive_cycle(
                    uuid4().hex, pair=args.pair,
                    minutes=args.history_minutes,
                    windows=args.history_windows,
                )
            elif args.public_history_research_once:
                outcome = app.run_public_history_research_cycle(
                    uuid4().hex, pair=args.pair, minutes=args.history_minutes,
                )
            elif args.scan_once:
                outcome = app.run_scan_cycle(uuid4().hex)
            elif args.public_once or args.verify_public:
                outcome = app.run_public_cycle(uuid4().hex, pair=args.pair)
            elif args.scheduled_public_scanner_once:
                # Schedule is chosen once using UTC minute epoch, not guessed
                # from historical ticker/WS samples. 5-minute deep uses the
                # EXACT SAME run_cycle/original bulk path. Other minutes
                # gather ticker/casebook observations via run_scan_cycle.
                # A delayed scheduler invocation beyond second 20 is skipped:
                # don't use stale same-minute data or make a catch-up burst.
                tick_epoch = int(time.time())
                tick_second = tick_epoch % 60
                m03_deep_deferred_btc = False
                if not 5 <= tick_second <= 20:
                    schedule_lane = "SKIPPED_LATE_OR_EARLY_REJECT"
                    outcome = app.run_cycle(uuid4().hex)
                elif (tick_epoch // 60) % 5 == 0 and not args.m03_light_only:
                    # BTC is mandatory for DEEP20, never for the LIGHT
                    # observation-only radar. Preserve PUBLIC policy/quotas
                    # on BOTH paths, and never promote degraded LIGHT to G1.
                    if not Path(args.ws_db).is_file():
                        # Required source is absent: do not construct a
                        # separate database or fake a successful BTC join.
                        m03_deep_deferred_btc = True
                    else:
                        try:
                            M03SharedEgressAdmission().allowed_local_limit()
                        except M03EgressAdmissionError as exc:
                            if str(exc) != "M03_STANDING_BTC_SOURCE_UNHEALTHY":
                                raise ConfigurationError(str(exc)) from None
                            m03_deep_deferred_btc = True
                    if m03_deep_deferred_btc:
                        schedule_lane = "LIGHT_ALL_IDR_EVERY_MINUTE"
                        outcome = app.run_scan_cycle(
                            uuid4().hex,
                            market=IndodaxPublicMarket(
                                m03_light_only_public_watch=True),
                        )
                    else:
                        schedule_lane = "DEEP20_EVERY_FIVE_MINUTES"
                        outcome = app.run_cycle(
                            uuid4().hex, public_scanner=True,
                            ws_db_path=args.ws_db,
                            min_volume_idr=max(
                                bulk_min_volume_idr, Decimal("150000000")),
                            deep_limit=20,
                        )
                else:
                    schedule_lane = "LIGHT_ALL_IDR_EVERY_MINUTE"
                    outcome = app.run_scan_cycle(
                        uuid4().hex,
                        market=IndodaxPublicMarket(
                            m03_light_only_public_watch=True),
                    )
            elif args.once and args.public_scanner_mainpath:
                # Exactly ONE original orchestrator and one original scanner.
                # No new provider, no issuer, no order. Mandatory BTC WS join
                # observes the live collector's SQLite in read-only mode.
                outcome = app.run_cycle(
                    uuid4().hex, public_scanner=True,
                    ws_db_path=args.ws_db,
                    min_volume_idr=max(
                        bulk_min_volume_idr, Decimal("150000000")),
                    deep_limit=args.bulk_deep_limit,
                )
            else:
                outcome = app.run_cycle(uuid4().hex)
            result = {
                "trace_id": outcome.trace_id,
                "final_decision": (
                    outcome.final_decision.value
                    if outcome.final_decision is not None else None
                ),
                "stage_disposition": outcome.stage_disposition,
                "reason": outcome.reason,
                "execution_allowed": outcome.execution_allowed,
                "public_market": outcome.market,
            }
            if args.public_ws_once or args.public_ws_collect or args.public_ws_supervised_once:
                result["public_ws"] = outcome.public_ws
                result["live_trading_authorized"] = False
            if args.scheduled_public_scanner_once:
                result["scheduled_m03_lane"] = schedule_lane
                result["m03_light_only_canary_active"] = bool(args.m03_light_only)
                if m03_deep_deferred_btc:
                    result["m03_deep_deferred_reason"] = (
                        "BTC_PUBLIC_SOURCE_UNHEALTHY_LIGHT_WATCH_ONLY"
                    )
                if schedule_lane == "DEEP20_EVERY_FIVE_MINUTES":
                    # One compact record in the EXISTING V2 journal.
                    # Observations are not owner Final decisions, and the
                    # summary cannot authorize either G1 or an order.
                    compact = _m03_compact_from_original(outcome)
                    app.journal.record_m03_compact_tick(
                        outcome.trace_id, compact,
                    )
                    result["m03_compact_tick"] = compact
                result["public_scanner"] = outcome.screening
                result["public_bulk"] = outcome.bulk
                result["ai_shadow"] = outcome.ai_shadow
                if args.m05_market_advisory_queue:
                    result["owner_market_advisory"] = outcome.owner_advisory
                result["g1_source_policy"] = "A_STRICT_NO_PUBLIC_PROMOTION"
                result["paid_network_allowed"] = False
                result["live_trading_authorized"] = False
            if args.bulk_deep_once or args.public_scanner_mainpath:
                result["public_bulk"] = outcome.bulk
                if args.public_scanner_mainpath:
                    result["mainpath_mode"] = "M03_PUBLIC_SCANNER_TO_M04_OWNER_ADVISORY"
                    result["public_24h_volume_floor_idr"] = str(max(
                        bulk_min_volume_idr, Decimal("150000000")))
                    result["btc_ws_existing_journal_readonly"] = bool(
                        args.ws_db and Path(args.ws_db).is_file()
                    )
                result["ai_shadow"] = outcome.ai_shadow
                if owner_control is not None:
                    # Safe summary only: callback secrets remain in the
                    # persistent M05 ledger and are sent via Telegram sender.
                    result["owner_market_advisory"] = outcome.owner_advisory
                # Never show API credentials, request/response bodies or
                # transport internals; readiness is not provider inference.
                result["sumopod_shadow"] = {
                    "configured": provider is not None,
                    "paid_network_allowed": False,
                    "g1_issuer_connected": False,
                    "role_usage_events": len(provider.usage_events) if provider else 0,
                }
                result["bulk_probe_status"] = (
                    "PUBLIC_BULK_BLOCKED" if outcome.bulk is None
                    else "PUBLIC_BULK_UNATTESTED"
                    if outcome.bulk["coverage_status"] == "ALL_SELECTED_ANALYZED_UNATTESTED"
                    else "PUBLIC_BULK_INCOMPLETE_UNATTESTED"
                )
                result["live_trading_authorized"] = False
            if args.rolling_deep_once:
                result["public_rolling"] = outcome.rolling
                result["rolling_probe_status"] = ("PUBLIC_ROLLING_UNATTESTED" if outcome.rolling is not None
                                                  else "PUBLIC_ROLLING_BLOCKED")
                result["live_trading_authorized"] = False
            if args.public_history_archive_once:
                result["public_history_archive"] = outcome.research_archive
                result["research_is_trade_signal"] = False
                result["live_trading_authorized"] = False
            if args.public_history_research_once:
                result["public_history_research"] = outcome.public_research
                result["research_is_trade_signal"] = False
                result["live_trading_authorized"] = False
            if args.deep_once:
                result["deep_target_pair"] = args.deep_pair
                result["public_deep"] = outcome.deep
                result["deep_probe_status"] = "PUBLIC_DEEP_UNATTESTED" if outcome.deep is not None else "PUBLIC_DEEP_ERROR"
                result["live_trading_authorized"] = False
            if args.scan_once:
                result["public_scanner"] = outcome.screening
                result["scanner_probe_status"] = "PUBLIC_SCREENED_UNATTESTED" if outcome.screening is not None else "PUBLIC_SCANNER_ERROR"
                result["live_trading_authorized"] = False
        if args.verify_public:
            # Exit zero means only public payload parsing succeeded, never
            # market-source authorization, Risk ALLOW or LIVE trading readiness.
            observed = outcome.market is not None
            result["public_probe_status"] = "PUBLIC_OBSERVED_G1_UNATTESTED" if observed else "PUBLIC_SOURCE_ERROR"
            result["verified_public_payload_parsed"] = observed
            result["live_trading_authorized"] = False
        print(json.dumps(result, sort_keys=True))
        if args.verify_public and not result["verified_public_payload_parsed"]:
            return 3
        if args.scan_once and result["public_scanner"] is None:
            return 3
        if args.deep_once and result["public_deep"] is None:
            return 3
        if args.public_history_research_once and result["public_history_research"] is None:
            return 3
        if args.public_history_archive_once and (
                result["public_history_archive"] is None or
                result["public_history_archive"]["source_failure"] is not None):
            return 3
        if args.rolling_deep_once and result["public_rolling"] is None:
            return 3
        if (args.bulk_deep_once or args.public_scanner_mainpath) and result["public_bulk"] is None:
            return 3
        if args.scheduled_public_scanner_once and (
                schedule_lane == "SKIPPED_LATE_OR_EARLY_REJECT"
                or (schedule_lane == "DEEP20_EVERY_FIVE_MINUTES"
                    and result["public_bulk"] is None)
                or (schedule_lane == "LIGHT_ALL_IDR_EVERY_MINUTE"
                    and result["public_scanner"] is None)):
            return 3
        if args.public_ws_once and outcome.reason.startswith("PUBLIC_WS_PROBE_REJECTED:"):
            return 3
        if args.public_ws_collect and outcome.reason == "PUBLIC_WS_COLLECTION_INTERRUPTED":
            return 3
        if args.public_ws_supervised_once and outcome.reason == "PUBLIC_WS_SUPERVISION_INTERRUPTED":
            return 3
        return 0
    except (ConfigurationError, OSError, ValueError, SumopodProviderError, TelegramTransportError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
