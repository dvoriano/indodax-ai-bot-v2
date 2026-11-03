"""M03 public REST quota shared by cooperating V2 processes on ONE VPS.

A rolling 60-second SQLite quota is reserved BEFORE each outgoing GET.
It is not exchange IP telemetry and cannot count non-V2 apps/other hosts,
NAT peers, or manual curl. Fail-closed on exhausted/unavailable storage.
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
        """Atomic reservation with BEGIN IMMEDIATE; no automatic refunds.

        A request that times out may still reach the exchange, so its
        reservation remains until the 60-second rolling window expires.
        """
        if (limit_override is not None
                and (type(limit_override) is not int
                     or not 1 <= limit_override <= 168)):
            raise SharedRestBudgetError("INVALID_PUBLIC_SHARED_RATE_LIMIT")
        effective_limit = min(self.limit, limit_override) if limit_override is not None else self.limit
        now = self.clock()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not (0 < now < 1e12):
            raise SharedRestBudgetError("INVALID_PUBLIC_SHARED_RATE_CLOCK")
        # Any V2 reader/writer running under the same service account must
        # share this file. No implicit remote IP-wide coordination is claimed.
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with sqlite3.connect(self.path, timeout=2.0) as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute(
                    "CREATE TABLE IF NOT EXISTS rest_get_reservations "
                    "(id INTEGER PRIMARY KEY, requested_at REAL NOT NULL)"
                )
                con.execute("DELETE FROM rest_get_reservations WHERE requested_at <= ?",
                            (now - 60.0,))
                used = int(con.execute(
                    "SELECT COUNT(*) FROM rest_get_reservations"
                ).fetchone()[0])
                if used >= effective_limit:
                    raise SharedRestBudgetError("PUBLIC_SHARED_REST_RATE_BUDGET_EXCEEDED")
                con.execute(
                    "INSERT INTO rest_get_reservations (requested_at) VALUES (?)",
                    (now,),
                )
            return used + 1
        except SharedRestBudgetError:
            raise
        except (sqlite3.Error, OSError, ValueError):
            raise SharedRestBudgetError("PUBLIC_SHARED_REST_RATE_DB_UNAVAILABLE") from None
