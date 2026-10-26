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
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
        app = DailyScalpingOrchestrator(settings, DecisionJournal(args.db))
        if args.health:
            result = app.health()
        else:
            outcome = app.run_cycle(uuid4().hex)
            result = {
                "trace_id": outcome.trace_id,
                "final_decision": outcome.final_decision.value,
                "reason": outcome.reason,
                "execution_allowed": outcome.execution_allowed,
            }
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ConfigurationError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
