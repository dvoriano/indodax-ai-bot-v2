"""CLI for non-live M01 foundation; no exchange connectivity."""
from __future__ import annotations

import argparse
import json
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from .config import ConfigurationError, Settings
from .journal import DecisionJournal
from .orchestrator import DailyScalpingOrchestrator
from .owner_control import OwnerDecisionLedger
from .public_ws_health import public_ws_health
from .public_ws_unit_status import inspect_public_ws_systemd_unit
from .sumopod_provider import SumopodChatAdapter, SumopodProviderError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Indodax V2 daily spot scalping (M01 safe foundation)")
    parser.add_argument("--db", default="runtime/decisions.sqlite3")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--health", action="store_true")
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
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
        if (args.m05_shadow_db is None) != (args.m05_shadow_owner_id is None):
            raise ConfigurationError("M05_REQUIRES_BOTH_SHADOW_OWNER_AND_DB")
        if args.m05_shadow_db is not None and not (args.once or args.bulk_deep_once):
            raise ConfigurationError("M05_SHADOW_ONLY_ON_MAINPATH")
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
            # Require BTC source even on light minutes so the same service
            # cannot silently run without its planned M04 read-only join.
            if not args.ws_db:
                raise ConfigurationError("M03_SCHEDULED_REQUIRES_BTC_WS_DB")
            if Path(args.ws_db).resolve() == Path(args.db).resolve():
                raise ConfigurationError("M03_SCHEDULED_WS_DB_MUST_BE_SEPARATE")
            if not Path(args.ws_db).is_file():
                raise ConfigurationError("M03_SCHEDULED_WS_DB_NOT_FOUND")
        # M04-V: only explicit bulk CLI opt-in constructs the THREE-ROLE
        # Sumopod adapter from protected process environment. It is always
        # payment-disabled at this milestone and public G1 cannot qualify it.
        if args.sumopod_shadow and not args.bulk_deep_once:
            raise ConfigurationError("SUMOPOD_SHADOW_REQUIRES_BULK_DEEP_ONCE")
        if args.deep_pair is not None and not args.deep_once:
            raise ConfigurationError("DEEP_PAIR_REQUIRES_DEEP_ONCE")
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
        app = DailyScalpingOrchestrator(
            settings, DecisionJournal(args.db), ai_role_caller=provider,
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
    except (ConfigurationError, OSError, ValueError, SumopodProviderError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
