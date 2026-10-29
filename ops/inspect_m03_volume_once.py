"""One-shot read-only Indodax volume distribution; three public REST GETs.

Usage (from repository root):
    python -B -m ops.inspect_m03_volume_once

No private credentials, AI provider, order, WebSocket, or SQLite writes.
"""
from __future__ import annotations

import json
import sys

from indodax_ai_bot.public_market import IndodaxPublicMarket, PublicDataError
from indodax_ai_bot.scanner import DailyPairScanner
from indodax_ai_bot.volume_screening import public_volume_distribution


def main() -> int:
    try:
        # One public scanner; this calls /api/pairs, /api/server_time,
        # and /api/ticker_all once each. No journal or network retry loop.
        market = IndodaxPublicMarket(timeout=8)
        scan = DailyPairScanner(market).scan()
        report = public_volume_distribution(scan)
        report["public_get_attempt_count_this_client"] = (
            market.public_get_attempt_count
        )
        # The 180/min allowance is shared by callers using the same IP;
        # this report does NOT assert spare global-IP request budget.
        report["other_process_requests_accounted_for"] = False
        print(json.dumps(report, sort_keys=True))
        return 0
    except (PublicDataError, OSError, ValueError):
        # Avoid printing unexpected HTTP response bodies or request internals.
        print(json.dumps({
            "status": "PUBLIC_VOLUME_PROBE_FAILED",
            "trading_allowed": False,
        }), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
