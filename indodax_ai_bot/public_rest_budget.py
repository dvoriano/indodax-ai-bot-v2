"""M03 public REST quota shared by cooperating V2 processes on ONE VPS.

A rolling 60-second SQLite quota is reserved BEFORE each outgoing GET.
It is not exchange IP telemetry and cannot count non-V2 apps/other hosts,
NAT peers, or manual curl. A lower declared peer cap sticks across V2
processes using the SAME DB, including callers without the M03 policy.
Fail-closed on exhausted/unavailable storage.
No exchange credentials, response bodies, or market data stored.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import time
from typing import Callable


class SharedRestBudgetError(RuntimeError):
    """Fixed safe reason; never disclose a filesystem path or request body."""


class SharedPublicRestBudget:
    """Local-host cross-process reservation ledger, conservative 168/60s.

    The 12-request headroom below the documented 180 public GET/minute is
    NOT a guarantee if other consumers share the outward-facing public IP.
    V2 processes must use the SAME state file and user/permissions.
    """

    def __init__(self, path: str | Path | None = None, *,
                 limit: int = 168,
                 clock: Callable[[], float] | None = None) -> None:
        if type(limit) is not int or not 1 <= limit <= 168:
            raise SharedRestBudgetError("INVALID_PUBLIC_SHARED_RATE_LIMIT")
        self.limit = limit
        self.path = Path(path) if path is not None else (
            Path(os.environ.get("INDODAX_PUBLIC_REST_RATE_DB", ""))
            if os.environ.get("INDODAX_PUBLIC_REST_RATE_DB") else
            Path.home() / ".cache" / "indodax-ai-bot-v2" / "rest-rate.sqlite3"
        )
        self.clock = clock or time.time

    def reserve(self, *, limit_override: int | None = None) -> int:
        """Reserve one public GET with a shared, sticky conservative peer cap.

        A lowered cap is durable in the SAME V2 cross-process SQLite file.
        V2 clients without an egress policy cannot silently revert to 168
        after another V2 client declares a smaller external headroom.
        The cap only decreases; removing the policy does NOT raise it.
        An explicit separately reviewed reset procedure is required before
        any increase. This still does not measure traffic outside V2.
        """
        if (limit_override is not None
                and (type(limit_override) is not int
                     or not 1 <= limit_override <= 168)):
            raise SharedRestBudgetError("INVALID_PUBLIC_SHARED_RATE_LIMIT")
        now = self.clock()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not (0 < now < 1e12):
            raise SharedRestBudgetError("INVALID_PUBLIC_SHARED_RATE_CLOCK")
        # Every cooperating real V2 transport uses this SAME DB, including
        # transports without M03 admission enabled.
        blocked = False
        used_next = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with sqlite3.connect(self.path, timeout=2.0) as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute(
                    "CREATE TABLE IF NOT EXISTS rest_get_reservations "
                    "(id INTEGER PRIMARY KEY, requested_at REAL NOT NULL)"
                )
                con.execute(
                    "CREATE TABLE IF NOT EXISTS rest_shared_egress_cap "
                    "(id INTEGER PRIMARY KEY CHECK(id=1), "
                    "max_local_requests_60s INTEGER NOT NULL "
                    "CHECK(max_local_requests_60s BETWEEN 1 AND 168))"
                )
                saved_row = con.execute(
                    "SELECT max_local_requests_60s "
                    "FROM rest_shared_egress_cap WHERE id=1"
                ).fetchone()
                if saved_row is None:
                    durable_cap = 168
                elif (type(saved_row[0]) is not int
                      or not 1 <= saved_row[0] <= 168):
                    raise SharedRestBudgetError("PUBLIC_SHARED_REST_CAP_INVALID")
                else:
                    durable_cap = saved_row[0]
                if limit_override is not None and limit_override < durable_cap:
                    durable_cap = limit_override
                    con.execute(
                        "INSERT INTO rest_shared_egress_cap "
                        "(id,max_local_requests_60s) VALUES (1,?) "
                        "ON CONFLICT(id) DO UPDATE SET "
                        "max_local_requests_60s=excluded.max_local_requests_60s",
                        (durable_cap,),
                    )
                effective_limit = min(self.limit, durable_cap)
                con.execute(
                    "DELETE FROM rest_get_reservations WHERE requested_at <= ?",
                    (now - 60.0,),
                )
                used = int(con.execute(
                    "SELECT COUNT(*) FROM rest_get_reservations"
                ).fetchone()[0])
                if used >= effective_limit:
                    # Do not raise before context-manager COMMIT: an exhausted
                    # request must still durably propagate the lowered cap.
                    blocked = True
                else:
                    con.execute(
                        "INSERT INTO rest_get_reservations (requested_at) VALUES (?)",
                        (now,),
                    )
                    used_next = used + 1
            if blocked:
                raise SharedRestBudgetError(
                    "PUBLIC_SHARED_REST_RATE_BUDGET_EXCEEDED"
                )
            return used_next
        except SharedRestBudgetError:
            raise
        except (sqlite3.Error, OSError, ValueError):
            raise SharedRestBudgetError("PUBLIC_SHARED_REST_RATE_DB_UNAVAILABLE") from None
