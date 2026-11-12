"""Small strict heartbeat recognizer for one original M05 PID.

Reads journalctl JSON lines from stdin. Never emits message content, tokens,
systemd environment, chat IDs, order data, or raw exceptions.
This helper does NOT call journalctl or operate systemd itself.
"""
from __future__ import annotations

import json
import os
import re
import sys


HEARTBEAT = "M05_FAST_LOOP_HEARTBEAT_NONLIVE"
BAD_STATES = frozenset((
    "M05_RADAR_CALLBACK_FAILED", "M05_RADAR_UNTRUSTED_RESULT",
))


def inspect_heartbeat_lines(lines: list[str], *, pid: str) -> str:
    """READY / WAITING / FAILED, all non-executing and strict PID matched."""
    if type(pid) is not str or re.fullmatch(r"[1-9][0-9]{0,9}", pid) is None:
        return "FAILED"
    seen = False
    for raw in lines[-1200:]:
        try:
            record = json.loads(raw)
            if not isinstance(record, dict) or str(record.get("_PID")) != pid:
                continue
            payload = json.loads(record.get("MESSAGE", ""))
            if (not isinstance(payload, dict)
                    or payload.get("status") != HEARTBEAT):
                continue
            if (payload.get("radar_enabled") is not True
                    or payload.get("execution_allowed") is not False):
                return "FAILED"
            count = payload.get("radar_invocations")
            if type(count) is not int or count < 0:
                return "FAILED"
            status = payload.get("radar_last_status")
            if status in BAD_STATES:
                return "FAILED"
            if count >= 1:
                # Other source status categories are observational and not
                # proof that a Telegram message was actually sent.
                seen = True
        except (ValueError, TypeError, UnicodeError):
            continue
    return "READY" if seen else "WAITING"


def main() -> int:
    pid = os.environ.get("M05_CHECK_PID", "")
    verdict = inspect_heartbeat_lines(
        sys.stdin.read(2_000_000).splitlines(), pid=pid)
    print("M05_PRIORITY_HEARTBEAT_" + verdict)
    return {"READY": 0, "WAITING": 2, "FAILED": 3}[verdict]


if __name__ == "__main__":
    sys.exit(main())
