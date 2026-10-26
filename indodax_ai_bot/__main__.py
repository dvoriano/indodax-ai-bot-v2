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
    parser.add_argument("--pair", default="btc_idr", help="ticker_id e.g. btc_idr")
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
        app = DailyScalpingOrchestrator(settings, DecisionJournal(args.db))
        if args.health:
            result = app.health()
        else:
            if args.rolling_deep_once:
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
        return 0
    except (ConfigurationError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
