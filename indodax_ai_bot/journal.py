"""Persistent and idempotent REJECT audit log. No private/account information."""
from __future__ import annotations

import sqlite3
import hashlib
import json
from dataclasses import dataclass
from contextlib import contextmanager
from typing import Iterator
from pathlib import Path

from .contracts import FinalDecision


class DecisionConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeepWindow:
    pair_ids: tuple[str, ...]
    starting_index: int
    next_index: int
    universe_digest: str
    replayed: bool = False


class DecisionJournal:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS decisions (
                    trace_id TEXT PRIMARY KEY,
                    final_decision TEXT NOT NULL CHECK(final_decision IN ('PASS', 'REJECT')),
                    reason TEXT NOT NULL,
                    schema_version INTEGER NOT NULL
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS deep_scan_cursor (
                    lane TEXT PRIMARY KEY,
                    next_index INTEGER NOT NULL,
                    last_started_epoch INTEGER NOT NULL
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS deep_scan_reservation (
                    trace_id TEXT PRIMARY KEY,
                    universe_digest TEXT NOT NULL,
                    selected_json TEXT NOT NULL,
                    starting_index INTEGER NOT NULL,
                    next_index INTEGER NOT NULL
                )"""
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self._db_path, timeout=10)
        try:
            with con:
                yield con
        finally:
            con.close()

    def record_reject(self, trace_id: str, reason: str) -> None:
        if not trace_id or not trace_id.strip() or not reason:
            raise ValueError("trace_id and reason required")
        # A real transaction with one UNIQUE trace prevents conflicting replay.
        with self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO decisions(trace_id, final_decision, reason, schema_version) VALUES (?, ?, ?, 1)",
                (trace_id, FinalDecision.REJECT.value, reason),
            )
            row = con.execute(
                "SELECT final_decision, reason FROM decisions WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
            if row != (FinalDecision.REJECT.value, reason):
                raise DecisionConflictError("conflicting replay for trace_id")

    def count(self) -> int:
        with self._connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0])

    def reserve_deep_window(
        self, trace_id: str, ranked_pairs: tuple[str, ...],
        *, observed_epoch: int, batch_size: int = 2, min_interval_seconds: int = 12,
    ) -> DeepWindow:
        """Atomically rotate public-only deep candidates across restarts.

        The cursor tracks scheduling, NOT evidence freshness or authorization.
        Reusing a trace id with a changed universe fails closed.
        """
        if not isinstance(trace_id, str) or not trace_id.strip():
            raise ValueError("INVALID_SCAN_TRACE")
        if (not ranked_pairs or len(set(ranked_pairs)) != len(ranked_pairs)
                or any(not isinstance(p, str) or not p.endswith("_idr") for p in ranked_pairs)):
            raise ValueError("INVALID_SCAN_UNIVERSE")
        if isinstance(observed_epoch, bool) or not isinstance(observed_epoch, int) or observed_epoch <= 0:
            raise ValueError("INVALID_SCAN_EPOCH")
        if not 1 <= batch_size <= 2 or min_interval_seconds < 1:
            raise ValueError("INVALID_SCAN_BUDGET")
        digest = hashlib.sha256(json.dumps(ranked_pairs, separators=(",", ":")).encode()).hexdigest()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute(
                "SELECT universe_digest, selected_json, starting_index, next_index "
                "FROM deep_scan_reservation WHERE trace_id = ?", (trace_id,)
            ).fetchone()
            if existing is not None:
                if existing[0] != digest:
                    raise DecisionConflictError("DEEP_TRACE_UNIVERSE_CHANGED")
                return DeepWindow(tuple(json.loads(existing[1])), existing[2], existing[3], digest, True)
            cursor = con.execute(
                "SELECT next_index, last_started_epoch FROM deep_scan_cursor WHERE lane='MAIN'"
            ).fetchone()
            if cursor is None:
                start = 0
            else:
                last_start = cursor[1]
                if observed_epoch - last_start < min_interval_seconds:
                    raise ValueError("DEEP_SCAN_COOLDOWN")
                start = cursor[0] % len(ranked_pairs)
            count = min(batch_size, len(ranked_pairs))
            chosen = tuple(ranked_pairs[(start + i) % len(ranked_pairs)] for i in range(count))
            nxt = (start + count) % len(ranked_pairs)
            con.execute(
                "INSERT INTO deep_scan_reservation(trace_id, universe_digest, selected_json, starting_index, next_index) "
                "VALUES (?, ?, ?, ?, ?)",
                (trace_id, digest, json.dumps(chosen), start, nxt),
            )
            con.execute(
                "INSERT INTO deep_scan_cursor(lane, next_index, last_started_epoch) "
                "VALUES ('MAIN', ?, ?) ON CONFLICT(lane) DO UPDATE SET "
                "next_index=excluded.next_index, last_started_epoch=excluded.last_started_epoch",
                (nxt, observed_epoch),
            )
            return DeepWindow(chosen, start, nxt, digest)
