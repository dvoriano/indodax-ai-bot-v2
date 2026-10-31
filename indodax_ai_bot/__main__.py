"""CLI for non-live M01 foundation; no exchange connectivity."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from os import environ
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from .config import ConfigurationError, Settings
from .m03_egress_admission import M03SharedEgressAdmission
from .journal import DecisionJournal
from .orchestrator import DailyScalpingOrchestrator
from .m06_scoped_https import (M06RequestBudget, M06ScopeDenied, read_approval, read_view_only_credentials)
from .owner_control import OwnerDecisionLedger
from .telegram_bot_api import TelegramBotApiClient, TelegramOwnerPoller, TelegramTransportError
from .telegram_readonly_preflight import inspect_telegram_readonly
from .telegram_owner_outbound import send_owner_safe_status_once
from .telegram_dedicated_inbound import (
    LEGACY_CAIPITI_BOT_ID, dedicated_owner_poll_once,
)
from .public_ws_health import public_ws_health
from .public_ws_unit_status import inspect_public_ws_systemd_unit
from .sumopod_provider import SumopodChatAdapter, SumopodProviderError


def _m06_fixed_budget_db() -> str:
    """Single V2 scope accounting path; cannot be reset via CLI arguments."""
    if (os.environ.get("M06_OWNER_ONE_SHOT_PRIVATE_GO")
            == "M06_PRIVATE_VIEW_ONLY_RETRY_20261031"):
        return str(Path.home() / ".local" / "state" /
                   "indodax-ai-bot-v2" / "m06-owner-viewonly-retry-20261031.sqlite3")
    return str(Path.home() / ".local" / "state" /
               "indodax-ai-bot-v2" / "m06-owner-viewonly-one-shot-20261031.sqlite3")


def _m03_deep_compact_from_original(outcome) -> dict:
    """Only the original orchestrator's DEEP20 outcome can feed this view."""
    from .contracts import FinalDecision
    if (outcome.final_decision != FinalDecision.REJECT
            or outcome.execution_allowed is not False):
        raise ConfigurationError("M03_DEEP_COMPACT_REQUIRES_FINAL_REJECT")
    raw = outcome.bulk if isinstance(outcome.bulk, dict) else {}
    if (raw.get("g1_verified") is True
            or raw.get("execution_allowed") is True
            or raw.get("trade_authorized") is True):
        raise ConfigurationError("M03_DEEP_COMPACT_SOURCE_UNSAFE")
    coverage = raw.get("coverage_status", "NO_BULK_EVIDENCE")
    if (not isinstance(coverage, str) or not 1 <= len(coverage) <= 64
            or not all(c.isascii() and (c.isupper() or c.isdigit() or c == "_")
                       for c in coverage)):
        coverage = "UNVERIFIED_COVERAGE"
    counts = {}
    for name in ("attempted_count", "analyzed_count"):
        value = raw.get(name)
        counts[name] = value if type(value) is int and 0 <= value <= 40 else None
    return {
        "schema_version": 1,
        "lane": "DEEP20_EVERY_FIVE_MINUTES",
        "coverage_status": coverage,
        **counts,
        "final_decision": "REJECT",
        "execution_allowed": False,
        "g1_issuer_connected": False,
        "paid_network_allowed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Indodax V2 daily spot scalping (M01 safe foundation)")
    parser.add_argument("--db", default="runtime/decisions.sqlite3")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--health", action="store_true")
    group.add_argument("--m06-account-review-once", action="store_true",
                       help="Original V2 offline M06 account review, always REJECT; no private keys/requests")
    group.add_argument("--m06-private-readonly-once", action="store_true",
                       help="EXPLICIT scoped M06 GO: view-only four private HTTPS GETs only, never trade")
    group.add_argument("--once", action="store_true")
    parser.add_argument("--public-scanner-mainpath", action="store_true",
                        help="With --once only: opt-in same M03 bulk+G1+M04 no-trade pipeline using an existing BTC WS DB")
    group.add_argument("--public-once", action="store_true", help="Use official Indodax PUBLIC REST only; no private key/order")
    group.add_argument("--verify-public", action="store_true", help="One-shot public source verification: nonzero exit on public source errors")
    group.add_argument("--scan-once", action="store_true", help="PUBLIC all-IDR screening; no BUY signals, keys or trading")
    group.add_argument("--scheduled-public-scanner-once", action="store_true",
                       help="Opt-in ONE minute tick: lightweight all-IDR scan each minute; original 20-deep G1/M04 public reject path every fifth minute, no orders")
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
    parser.add_argument("--supervisor-status-file", default=None,
                        help="Optional atomic status of last PUBLIC WS batch, not certified trade evidence")
    parser.add_argument("--health-max-age-seconds", type=int, default=3600,
                        help="Max age 60..86400s for operator last-batch check; does NOT prove service running")
    parser.add_argument("--pair", default="btc_idr", help="ticker_id e.g. btc_idr")
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
    parser.add_argument("--ws-db", default=None,
                        help="Existing BTC WS SQLite: required by --once --public-scanner-mainpath; optional for --bulk-deep-once, never G1 approval")
    parser.add_argument("--sumopod-shadow", action="store_true",
                        help="Connect configured Sumopod adapter to PUBLIC bulk shadow; paid network OFF, G1 still untrusted")
    parser.add_argument("--m05-shadow-db", default=None,
                        help="Opt-in OFFLINE owner REJECT audit DB (no Telegram transport or orders)")
    parser.add_argument("--m05-shadow-owner-id", type=int, default=None,
                        help="Opt-in numeric Telegram owner user ID, OFFLINE only")
    parser.add_argument("--m06-go-file", default=None,
                        help="Owner-created 0600 approval JSON, absolute path, <=15 min")
    parser.add_argument("--m06-credentials-file", default=None,
                        help="0600 view-only API key JSON, absolute path; never use trading key")
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
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
                journal.record_reject("M06_PRIVATE_READONLY_" + uuid4().hex,
                                      result.status)
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
            journal.record_reject("M06_ACCOUNT_READONLY_NO_GO", review.reason)
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
        if args.m05_shadow_db is not None and not (
                args.once or args.bulk_deep_once or args.m05_telegram_poll_once
                or args.m05_telegram_readonly_preflight_once
                or args.m05_telegram_owner_safe_status_once):
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
                                                  (args.once and args.public_scanner_mainpath)):
            raise ConfigurationError("BULK_DEEP_LIMIT_REQUIRES_BULK_DEEP_ONCE")
        if args.public_scanner_mainpath:
            if not args.once:
                raise ConfigurationError("M03_MAINPATH_REQUIRES_ONCE")
            if not args.ws_db:
                raise ConfigurationError("M03_MAINPATH_REQUIRES_BTC_WS_DB")
            if Path(args.ws_db).resolve() == Path(args.db).resolve():
                raise ConfigurationError("M03_MAINPATH_WS_DB_MUST_BE_SEPARATE")
            if not Path(args.ws_db).is_file():
                raise ConfigurationError("M03_MAINPATH_WS_DB_NOT_FOUND")
        if args.scheduled_public_scanner_once:
            # A unit opt-in MUST be admitted before journal creation, public
            # source discovery, or calls into the scanner. Rechecked per GET.
            admission_mode = os.environ.get("INDODAX_M03_REQUIRE_EGRESS_ADMISSION", "NO")
            if admission_mode not in ("NO", "YES"):
                raise ConfigurationError("M03_EGRESS_ADMISSION_MODE_INVALID")
            # Require BTC source even on light minutes so the same service
            # cannot silently run without its planned M04 read-only join.
            if not args.ws_db:
                raise ConfigurationError("M03_SCHEDULED_REQUIRES_BTC_WS_DB")
            if Path(args.ws_db).resolve() == Path(args.db).resolve():
                raise ConfigurationError("M03_SCHEDULED_WS_DB_MUST_BE_SEPARATE")
            if not Path(args.ws_db).is_file():
                raise ConfigurationError("M03_SCHEDULED_WS_DB_NOT_FOUND")
            # Production scheduled CLI cannot silently bypass the original
            # fail-closed egress policy, even when called outside systemd.
            # A fresh owner-only policy is checked again before EACH HTTP GET.
            if admission_mode != "YES":
                raise ConfigurationError("M03_EGRESS_ADMISSION_REQUIRED")
            M03SharedEgressAdmission().allowed_local_limit()
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
                "final_decision": "REJECT",
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
                "final_decision": "REJECT",
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
                "final_decision": "REJECT",
                "execution_allowed": False,
                "live_trading_authorized": False,
            }, sort_keys=True))
            return 0
        provider = (
            SumopodChatAdapter.configured_from_env()
            if args.sumopod_shadow else None
        )
        if args.public_ws_unit_status:
            # No DecisionJournal, no systemctl state change, and no network.
            settings.require_m01_safety()
            report = inspect_public_ws_systemd_unit()
            print(json.dumps({
                "public_ws_unit_status": report,
                "final_decision": "REJECT",
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
                "final_decision": "REJECT",
                "execution_allowed": False,
                "live_trading_authorized": False,
            }, sort_keys=True))
            return 0 if report["last_batch_recent_and_consistent"] is True else 3
        journal = DecisionJournal(args.db)
        app = DailyScalpingOrchestrator(
            settings, journal, ai_role_caller=provider,
            owner_control=owner_control,
        )
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
                if not 5 <= tick_second <= 20:
                    schedule_lane = "SKIPPED_LATE_OR_EARLY_REJECT"
                    outcome = app.run_cycle(uuid4().hex)
                elif (tick_epoch // 60) % 5 == 0:
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
                    outcome = app.run_scan_cycle(uuid4().hex)
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
                "final_decision": outcome.final_decision.value,
                "reason": outcome.reason,
                "execution_allowed": outcome.execution_allowed,
                "public_market": outcome.market,
            }
            # Original V2 CLI surfaces ONLY bounded hard-deny diagnostics.
            # No buy lot, wallet estimate or simulated PnL is fabricated.
            if outcome.position_review is not None:
                result["position_review"] = outcome.position_review
            if outcome.preflight_review is not None:
                result["preflight_review"] = outcome.preflight_review
            if args.public_ws_once or args.public_ws_collect or args.public_ws_supervised_once:
                result["public_ws"] = outcome.public_ws
                result["live_trading_authorized"] = False
            if args.scheduled_public_scanner_once:
                result["scheduled_m03_lane"] = schedule_lane
                result["public_scanner"] = outcome.screening
                result["public_bulk"] = outcome.bulk
                result["ai_shadow"] = outcome.ai_shadow
                result["g1_source_policy"] = "A_STRICT_NO_PUBLIC_PROMOTION"
                result["paid_network_allowed"] = False
                result["live_trading_authorized"] = False
            if args.bulk_deep_once or args.public_scanner_mainpath:
                result["public_bulk"] = outcome.bulk
                if args.public_scanner_mainpath:
                    result["mainpath_mode"] = "M03_PUBLIC_SCANNER_TO_M04_REJECT_ONLY"
                    result["public_24h_volume_floor_idr"] = str(max(
                        bulk_min_volume_idr, Decimal("150000000")))
                    result["btc_ws_existing_journal_readonly"] = True
                result["ai_shadow"] = outcome.ai_shadow
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
        if args.scheduled_public_scanner_once and (
                schedule_lane == "DEEP20_EVERY_FIVE_MINUTES"):
            # One original decision trace -> one durable bounded M03 record.
            # The bulk evidence may be huge; never rely on journalctl stdout
            # fragmentation/reassembly for operational counts or REJECT state.
            compact = _m03_deep_compact_from_original(outcome)
            journal.record_m03_compact_tick(outcome.trace_id, compact)
            result["m03_compact_telemetry"] = compact
            # The original scheduled DEEP20 CycleResult intentionally
            # leaves screening=None; its scanner already ran internally.
            # Never fabricate a scanner summary from unrelated bulk fields.
            # public_scanner retains its original None value.
            result["public_bulk"] = (
                None if outcome.bulk is None else {
                    "coverage_status": compact["coverage_status"],
                    "attempted_count": compact["attempted_count"],
                    "analyzed_count": compact["analyzed_count"],
                    "g1_verified": False,
                    "execution_allowed": False,
                }
            )
            result["ai_shadow"] = {
                "completed_roles": [],
                "execution_allowed": False,
            }
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
