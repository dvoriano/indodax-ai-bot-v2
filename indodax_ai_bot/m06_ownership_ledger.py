"""M06 durable, fail-closed ownership evidence for bot-originated order intents.

Stores only a locally generated order intent. Exchange fills MUST be independently
reconciled before an owned position can be declared. No network or trading.
"""
from __future__ import annotations
import hashlib
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .m06_private_payloads import OrderRecord, FillRecord


class OwnershipConflict(ValueError):
    pass


@dataclass(frozen=True)
class OwnershipReview:
    order_id: str
    intent_found: bool
    account_match: bool
    fill_ids_seen: tuple[str, ...]
    exchange_order_attested: bool = False
    exchange_fills_attested: bool = False
    bot_owned_verified: bool = False
    execution_allowed: bool = False
    status: str = "UNVERIFIED_LOCAL_INTENT_ONLY"


class M06OwnershipLedger:
    """Persist deterministic local intents, reject foreign/replayed fill identities.

    This is an evidence ledger, NOT an exchange ownership oracle. A caller
    cannot supply an authenticated=True flag to change the resulting trust.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS m06_intents (
                order_id TEXT PRIMARY KEY, account_hash TEXT NOT NULL,
                symbol TEXT NOT NULL, side TEXT NOT NULL,
                client_id TEXT NOT NULL UNIQUE
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS m06_observed_fills (
                trade_id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
                symbol TEXT NOT NULL, qty TEXT NOT NULL,
                price TEXT NOT NULL, fee TEXT NOT NULL
            )""")

    def _db(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @staticmethod
    def _identity(account_key: str) -> str:
        if type(account_key) is not str or not 1<=len(account_key)<=128:
            raise OwnershipConflict("M06_ACCOUNT_SCOPE_INVALID")
        return hashlib.sha256(account_key.encode()).hexdigest()

    def record_local_intent(self, *, account_key: str, order_id: str,
                            client_id: str, symbol: str, side: str) -> None:
        account=self._identity(account_key)
        if (any(type(v) is not str or not 1<=len(v)<=128 for v in (order_id,client_id,symbol))
                or side not in ("BUY","SELL") or not symbol.endswith("idr")):
            raise OwnershipConflict("M06_INTENT_INVALID")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT account_hash,symbol,side,client_id FROM m06_intents WHERE order_id=?",
                           (order_id,)).fetchone()
            value=(account,symbol,side,client_id)
            if row is not None:
                if row!=value:
                    raise OwnershipConflict("M06_INTENT_REPLAY_CONFLICT")
                return
            try:
                db.execute("INSERT INTO m06_intents VALUES(?,?,?,?,?)",(order_id,*value))
            except sqlite3.IntegrityError:
                raise OwnershipConflict("M06_CLIENT_ID_REUSE_CONFLICT") from None

    def observe(self, *, account_key: str, order: OrderRecord,
                fills: tuple[FillRecord, ...]) -> OwnershipReview:
        account=self._identity(account_key)
        if type(order) is not OrderRecord or type(fills) is not tuple:
            raise OwnershipConflict("M06_OWNERSHIP_INPUT_INVALID")
        if any(type(f) is not FillRecord or f.order_id!=order.order_id
               or f.symbol!=order.symbol for f in fills):
            raise OwnershipConflict("M06_FOREIGN_FILL")
        if len(set(f.trade_id for f in fills))!=len(fills):
            raise OwnershipConflict("M06_DUPLICATE_FILL_ID")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT account_hash,symbol,side,client_id FROM m06_intents WHERE order_id=?",
                           (order.order_id,)).fetchone()
            matched=(row is not None and row==(account,order.symbol,order.side,order.client_order_id))
            if not matched and fills:
                raise OwnershipConflict("M06_UNATTRIBUTED_FILL")
            for fill in fills:
                payload=(fill.order_id,fill.symbol,str(fill.quantity),str(fill.price),str(fill.commission))
                existing=db.execute("SELECT order_id,symbol,qty,price,fee FROM m06_observed_fills WHERE trade_id=?",
                                    (fill.trade_id,)).fetchone()
                if existing is not None and existing!=payload:
                    raise OwnershipConflict("M06_FILL_REPLAY_CONFLICT")
                if existing is None:
                    db.execute("INSERT INTO m06_observed_fills VALUES(?,?,?,?,?,?)",(fill.trade_id,*payload))
            return OwnershipReview(order.order_id,row is not None,matched,
                                   tuple(f.trade_id for f in fills))
