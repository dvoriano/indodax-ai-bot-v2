"""M06 offline account reconciliation and atomic shadow reservations.

No HTTP, credentials, private API, order submission or execution authority.
An authenticated exchange reader MUST be separately implemented and authorized.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import sqlite3
import time
import hashlib
import json

from .policy import ScalpingPolicy


def _amount(value: Decimal) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        raise ValueError("M06_INVALID_AMOUNT")
    if value != 0 and (value.adjusted() > 24 or value.adjusted() < -24):
        raise ValueError("M06_AMOUNT_OUT_OF_RANGE")
    return value


@dataclass(frozen=True)
class AccountObservation:
    """Unverified account-shaped inputs, NEVER an exchange certificate."""
    observed_epoch: int
    free_idr: Decimal
    locked_idr: Decimal
    floki_rebuy_idr: Decimal
    pepe_rebuy_idr: Decimal
    other_unlocked_commitments_idr: Decimal
    account_key: str
    orders_complete: bool = False
    fills_complete: bool = False
    exchange_authenticated: bool = False
    execution_allowed: bool = False


@dataclass(frozen=True)
class ReserveReview:
    trace_id: str
    amount_idr: Decimal
    remaining_idr: Decimal
    observation_digest: str
    replayed: bool
    status: str = "SHADOW_RESERVED_NOT_SPENDABLE"
    execution_allowed: bool = False


class M06ShadowCapital:
    """Single SQLite transaction for offline *shadow* commitments.

    Do not present this store as ownership of funds or bot-owned exchange fills.
    A cross-process/cross-bot account-wide reservation lock is NOT established.
    """
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS m06_shadow_reserve (
                trace_id TEXT PRIMARY KEY, account_key_hash TEXT NOT NULL,
                snapshot_digest TEXT NOT NULL, amount_idr TEXT NOT NULL,
                created_epoch INTEGER NOT NULL, released INTEGER NOT NULL DEFAULT 0
            )""")

    def _connection(self):
        db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @staticmethod
    def _validate(observation: AccountObservation, now_epoch: int):
        if type(observation) is not AccountObservation:
            raise ValueError("M06_OBSERVATION_TYPE")
        if (type(now_epoch) is not int or type(observation.observed_epoch) is not int
                or now_epoch <= 0 or not 0 <= now_epoch-observation.observed_epoch <= 15):
            raise ValueError("M06_STALE_OR_FUTURE_SNAPSHOT")
        if not isinstance(observation.account_key, str) or not 1 <= len(observation.account_key) <= 128:
            raise ValueError("M06_ACCOUNT_SCOPE_UNKNOWN")
        for v in (observation.free_idr, observation.locked_idr,
                  observation.floki_rebuy_idr, observation.pepe_rebuy_idr,
                  observation.other_unlocked_commitments_idr):
            _amount(v)
        # Deliberately never promote caller-provided booleans to trust.
        if (observation.exchange_authenticated is not False
                or observation.execution_allowed is not False):
            raise ValueError("M06_UNTRUSTED_AUTH_CLAIM")
        if type(observation.orders_complete) is not bool or type(observation.fills_complete) is not bool:
            raise ValueError("M06_INVALID_COVERAGE_FLAGS")

    def reserve_shadow(self, *, observation: AccountObservation,
                       trace_id: str, requested_idr: Decimal,
                       now_epoch: int) -> ReserveReview:
        self._validate(observation, now_epoch)
        _amount(requested_idr)
        if requested_idr <= 0 or not isinstance(trace_id, str) or not 1 <= len(trace_id) <= 128:
            raise ValueError("M06_INVALID_SHADOW_REQUEST")
        account_hash = hashlib.sha256(observation.account_key.encode()).hexdigest()
        fields = [observation.observed_epoch, *(str(x) for x in (
            observation.free_idr, observation.locked_idr,
            observation.floki_rebuy_idr, observation.pepe_rebuy_idr,
            observation.other_unlocked_commitments_idr)),
            observation.orders_complete, observation.fills_complete, account_hash]
        digest = hashlib.sha256(json.dumps(fields, separators=(",", ":")).encode()).hexdigest()
        db = self._connection()
        try:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT account_key_hash,snapshot_digest,amount_idr,released "
                "FROM m06_shadow_reserve WHERE trace_id=?", (trace_id,)
            ).fetchone()
            if existing is not None:
                if existing != (account_hash, digest, str(requested_idr), 0):
                    raise ValueError("M06_TRACE_REPLAY_CONFLICT")
                replayed = True
            else:
                replayed = False
            rows = db.execute(
                "SELECT amount_idr FROM m06_shadow_reserve "
                "WHERE account_key_hash=? AND released=0", (account_hash,)
            ).fetchall()
            pending = sum((_amount(Decimal(r[0])) for r in rows), Decimal("0"))
            capital = ScalpingPolicy().review_main_capital(
                free_idr=observation.free_idr,
                locked_idr=observation.locked_idr,
                floki_rebuy_idr=observation.floki_rebuy_idr,
                pepe_rebuy_idr=observation.pepe_rebuy_idr,
                commitments_not_locked_idr=observation.other_unlocked_commitments_idr,
            )
            remaining = capital.spendable_idr - pending
            if not replayed:
                if requested_idr > remaining:
                    raise ValueError("M06_SHADOW_INSUFFICIENT_FREE_IDR")
                db.execute(
                    "INSERT INTO m06_shadow_reserve "
                    "(trace_id,account_key_hash,snapshot_digest,amount_idr,created_epoch) "
                    "VALUES (?,?,?,?,?)",
                    (trace_id,account_hash,digest,str(requested_idr),now_epoch),
                )
                remaining -= requested_idr
            db.execute("COMMIT")
            return ReserveReview(trace_id, requested_idr, remaining, digest, replayed)
        except Exception:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def release_shadow(self, *, trace_id: str) -> None:
        """Only releases a local hypothetical reservation, NOT an exchange hold."""
        if not isinstance(trace_id, str) or not 1 <= len(trace_id) <= 128:
            raise ValueError("M06_INVALID_TRACE")
        db = self._connection()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT released FROM m06_shadow_reserve WHERE trace_id=?",
                (trace_id,),
            ).fetchone()
            if row is None or row[0] != 0:
                raise ValueError("M06_RELEASE_UNKNOWN_OR_REPLAY")
            db.execute("UPDATE m06_shadow_reserve SET released=1 WHERE trace_id=?", (trace_id,))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()
