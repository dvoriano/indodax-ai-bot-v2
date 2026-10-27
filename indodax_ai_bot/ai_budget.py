"""M04-V: durable pre-request AI cost ceiling reservations, not payment GO.

One SQLite-backed ledger shared by processes/restarts. Reservations represent
operator-supplied WORST-CASE USD MICRO-UNITS, not invoices or actual spend.
A transport timeout or crash may have been billable, so reservations are
never auto-refunded. Provider prices/model IDs must be verified separately
before any authorized live paid operation.

No credentials, source data, prompts, or private account state are stored.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import sqlite3
from typing import Callable
from zoneinfo import ZoneInfo


JAKARTA = ZoneInfo("Asia/Jakarta")
_PAIR_RE = re.compile(r"^[a-z0-9]+_idr$")
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
_ROLES = frozenset(("MARKET", "RISK", "STRATEGY"))


class AiBudgetError(RuntimeError):
    """Fixed error identifiers, never leaked SQLite paths/market inputs."""


class PersistentAiBudget:
    """Conservative daily limit using micro-USD (1 USD = 1,000,000 units).

    The caller must supply an operator-approved upper bound on the expected
    charge PER REQUEST and a daily ceiling. This alone does not authorize
    any network request, validate a source or verify vendor invoice rates.
    """

    def __init__(
        self, db_path: str | Path, *,
        daily_limit_micro_usd: int,
        per_request_reservation_micro_usd: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (type(daily_limit_micro_usd) is not int
                or type(per_request_reservation_micro_usd) is not int
                or not 1 <= per_request_reservation_micro_usd <= daily_limit_micro_usd
                or daily_limit_micro_usd > 100_000_000):
            raise AiBudgetError("INVALID_AI_BUDGET_LIMIT")
        self._path = Path(db_path)
        self._daily = daily_limit_micro_usd
        self._ceiling = per_request_reservation_micro_usd
        self._clock = clock or (lambda: datetime.now(JAKARTA))
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self._path, timeout=10) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS ai_paid_reservations (
                        jakarta_day TEXT NOT NULL,
                        role TEXT NOT NULL,
                        pair TEXT NOT NULL,
                        evidence_digest TEXT NOT NULL,
                        reserved_micro_usd INTEGER NOT NULL CHECK(reserved_micro_usd > 0),
                        PRIMARY KEY (jakarta_day, role, pair, evidence_digest)
                    )
                """)
        except (sqlite3.Error, OSError):
            raise AiBudgetError("AI_BUDGET_DB_UNAVAILABLE") from None

    def reserve(self, role: str, pair: str, evidence_digest: str) -> int:
        """Atomic, durable, unique charge ceiling reservation before network.

        A blocked duplicate is never silently replaced by a new reservation.
        With SQLite BEGIN IMMEDIATE two processes cannot spend past one cap.
        """
        if (role not in _ROLES or not isinstance(pair, str)
                or not _PAIR_RE.fullmatch(pair)
                or not isinstance(evidence_digest, str)
                or not _DIGEST_RE.fullmatch(evidence_digest)):
            raise AiBudgetError("INVALID_AI_BUDGET_SOURCE")
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise AiBudgetError("INVALID_AI_BUDGET_CLOCK")
        day = now.astimezone(JAKARTA).date().isoformat()
        try:
            with sqlite3.connect(self._path, timeout=10) as conn:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    "SELECT 1 FROM ai_paid_reservations "
                    "WHERE jakarta_day=? AND role=? AND pair=? AND evidence_digest=?",
                    (day, role, pair, evidence_digest),
                ).fetchone()
                if existing:
                    raise AiBudgetError("AI_BUDGET_SOURCE_ALREADY_RESERVED")
                spent = int(conn.execute(
                    "SELECT COALESCE(SUM(reserved_micro_usd), 0) "
                    "FROM ai_paid_reservations WHERE jakarta_day=?", (day,),
                ).fetchone()[0])
                if spent > self._daily - self._ceiling:
                    raise AiBudgetError("AI_DAILY_COST_CAP_REACHED")
                conn.execute(
                    "INSERT INTO ai_paid_reservations "
                    "(jakarta_day, role, pair, evidence_digest, reserved_micro_usd) "
                    "VALUES (?,?,?,?,?)",
                    (day, role, pair, evidence_digest, self._ceiling),
                )
                return spent + self._ceiling
        except (sqlite3.Error, OSError):
            raise AiBudgetError("AI_BUDGET_DB_UNAVAILABLE") from None
