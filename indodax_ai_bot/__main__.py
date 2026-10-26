"""CLI for non-live M01 foundation; no exchange connectivity."""
from __future__ import annotations

import argparse
import json
import sys
from uuid import uuid4

from .config import ConfigurationError, Settings
from .journal import DecisionJournal
from .orchestrator import DailyScalpingOrchestrator


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Indodax V2 daily spot scalping (M01 safe foundation)")
    parser.add_argument("--db", default="runtime/decisions.sqlite3")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--health", action="store_true")
    group.add_argument("--once", action="store_true")
    group.add_argument("--public-once", action="store_true", help="Use official Indodax PUBLIC REST only; no private key/order")
    group.add_argument("--verify-public", action="store_true", help="One-shot public source verification: nonzero exit on public source errors")
    group.add_argument("--scan-once", action="store_true", help="PUBLIC all-IDR screening; no BUY signals, keys or trading")
    group.add_argument("--deep-once", action="store_true", help="PUBLIC bounded 2-pair 1m/5m/15m indicator pilot; no BUY orders")
    group.add_argument("--rolling-deep-once", action="store_true", help="PUBLIC 2-pair rotating deep scan, SQLite persisted and paced, never trades")
    group.add_argument("--bulk-deep-once", action="store_true", help="PUBLIC up-to-20 same-cycle deep scan; bounded requests and never trades")
    group.add_argument("--public-ws-once", action="store_true", help="One public BTC trade-activity WS probe; persisted offset, no trades")
    group.add_argument("--public-ws-collect", action="store_true",
                       help="Bounded multi-session PUBLIC WS collection; durable SQLite, never trades")
    parser.add_argument("--stream-seconds", type=int, default=12, help="Bounded public WS probe duration: 3..30 seconds")
    parser.add_argument("--collector-sessions", type=int, default=2,
                        help="PUBLIC WS collector: 2..10 sessions per bounded invocation")
    parser.add_argument("--collector-pause-seconds", type=int, default=1,
                        help="PUBLIC WS collector: 0..10 seconds between sessions")
    parser.add_argument("--pair", default="btc_idr", help="ticker_id e.g. btc_idr")
    parser.add_argument("--ws-db", default=None,
                        help="Opt-in READ-ONLY diagnostic join of V2 public WS SQLite to --bulk-deep-once; no G1 approval")
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
        app = DailyScalpingOrchestrator(settings, DecisionJournal(args.db))
        if args.health:
            result = app.health()
        else:
            if args.public_ws_collect:
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
                outcome = app.run_bulk_deep_cycle(uuid4().hex, ws_db_path=args.ws_db)
            elif args.rolling_deep_once:
                outcome = app.run_rolling_deep_cycle(uuid4().hex)
            elif args.deep_once:
                outcome = app.run_deep_cycle(uuid4().hex)
            elif args.scan_once:
                outcome = app.run_scan_cycle(uuid4().hex)
            elif args.public_once or args.verify_public:
                outcome = app.run_public_cycle(uuid4().hex, pair=args.pair)
            else:
                outcome = app.run_cycle(uuid4().hex)
            result = {
                "trace_id": outcome.trace_id,
                "final_decision": outcome.final_decision.value,
                "reason": outcome.reason,
                "execution_allowed": outcome.execution_allowed,
                "public_market": outcome.market,
            }
            if args.public_ws_once or args.public_ws_collect:
                result["public_ws"] = outcome.public_ws
                result["live_trading_authorized"] = False
            if args.bulk_deep_once:
                result["public_bulk"] = outcome.bulk
                result["ai_shadow"] = outcome.ai_shadow
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
            if args.deep_once:
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
        if args.rolling_deep_once and result["public_rolling"] is None:
            return 3
        if args.bulk_deep_once and result["public_bulk"] is None:
            return 3
        if args.public_ws_once and outcome.reason.startswith("PUBLIC_WS_PROBE_REJECTED:"):
            return 3
        if args.public_ws_collect and outcome.reason == "PUBLIC_WS_COLLECTION_INTERRUPTED":
            return 3
        return 0
    except (ConfigurationError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
