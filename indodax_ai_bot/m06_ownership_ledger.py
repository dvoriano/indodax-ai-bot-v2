"""M06 durable, fail-closed ownership evidence for bot-originated order intents.

Stores only a locally generated order intent. Exchange fills MUST be independently
reconciled before an owned position can be declared. No network or trading.
"""
from __future__ import annotations
import hashlib
import sqlite3
import os
import stat
from contextlib import closing
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

from .m06_private_payloads import OrderRecord, FillRecord
from .m06_shadow_account import _amount


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
    protected_rebuy: ProtectedRebuyReview | None = None


@dataclass(frozen=True)
class ProtectedRebuyReview:
    """Account-scoped LOCAL observation only; never proof of exchange funds."""
    floki_idr_local_protected: Decimal
    pepe_idr_local_protected: Decimal
    total_idr_local_protected: Decimal
    distinct_sell_fills: int
    floki_manual_qty_local_floor: Decimal = Decimal("0")
    pepe_manual_qty_local_floor: Decimal = Decimal("0")
    manual_holding_verified: bool = False
    exchange_proceeds_verified: bool = False
    rebuy_spending_allowed: bool = False
    main_capital_spending_allowed: bool = False
    execution_allowed: bool = False
    status: str = "LOCAL_SPECIAL_SELL_PROCEEDS_REBUY_HOLD_UNVERIFIED"
    # Additional account-specific LOCAL SQLite observations, never wallet proof.
    local_shadow_commitments_idr: Decimal = Decimal("0")
    local_shadow_coverage_complete: bool = False
    floki_manual_floor_recorded: bool = False
    pepe_manual_floor_recorded: bool = False


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
            db.execute("""CREATE TABLE IF NOT EXISTS m06_special_rebuy_observations (
                trade_id TEXT PRIMARY KEY,
                account_hash TEXT NOT NULL,
                asset TEXT NOT NULL CHECK(asset IN ('FLOKI','PEPE')),
                order_id TEXT NOT NULL,
                gross_idr TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS m06_manual_asset_floors (
                account_hash TEXT NOT NULL,
                asset TEXT NOT NULL CHECK(asset IN ('FLOKI','PEPE')),
                minimum_qty TEXT NOT NULL,
                PRIMARY KEY (account_hash, asset)
            )""")

    def _db(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @staticmethod
    def account_scope_from_exchange_uid(uid: int) -> str:
        """Account identifier from parsed Indodax account ONLY, not API key."""
        if type(uid) is not int or uid <= 0:
            raise OwnershipConflict("M06_ACCOUNT_UID_INVALID")
        return "INDODAX_V2_UID:" + str(uid)

    @classmethod
    def inspect_existing_account_readonly(cls, *, db_path: str,
                                          account_uid: int) -> ProtectedRebuyReview:
        """Read existing local holds using SQLite RO; never create/migrate DB.

        An account's locally recorded intents must use the canonical uid
        binding; no caller-supplied account label or API key can substitute.
        A missing, insecure, or mismatched ledger fails closed.
        """
        key = cls.account_scope_from_exchange_uid(account_uid)
        account_hash = cls._identity(key)
        if type(db_path) is not str or not db_path:
            raise OwnershipConflict("M06_LOCAL_LEDGER_PATH_INVALID")
        p = Path(os.path.abspath(db_path))
        parent = p.parent
        try:
            owner = os.getuid()
            d = parent.lstat()
            f = p.lstat()
            if (not stat.S_ISDIR(d.st_mode) or d.st_uid != owner
                    or d.st_mode & 0o077
                    or not stat.S_ISREG(f.st_mode) or f.st_uid != owner
                    or f.st_mode & 0o077):
                raise OwnershipConflict("M06_LOCAL_LEDGER_INSECURE")
            # Explicit URI mode=ro never creates tables, DB, or journal rows.
            with closing(sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                bound = any(
                    db.execute(
                        "SELECT 1 FROM " + table
                        + " WHERE account_hash=? LIMIT 1",
                        (account_hash,),
                    ).fetchone() is not None
                    for table in (
                        "m06_intents",
                        "m06_special_rebuy_observations",
                        "m06_manual_asset_floors",
                    )
                )
                if not bound:
                    raise OwnershipConflict("M06_LOCAL_ACCOUNT_UNBOUND")
                protected = cls._read_rebuy_in_transaction(db, account_hash)
                # Read existing shadow holds in the SAME immutable SQLite
                # snapshot. The table may be absent on older V2 installs.
                # Absence means UNKNOWN, never an assumed zero liability.
                has_shadow_table = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='m06_shadow_reserve'"
                ).fetchone() is not None
                if not has_shadow_table:
                    return protected
                rows = db.execute(
                    "SELECT amount_idr FROM m06_shadow_reserve "
                    "WHERE account_key_hash=? AND released=0 LIMIT 10001",
                    (account_hash,),
                ).fetchall()
                if len(rows) > 10000:
                    raise OwnershipConflict("M06_SHADOW_LEDGER_LIMIT_EXCEEDED")
                try:
                    pending = sum(
                        (_amount(Decimal(raw)) for (raw,) in rows),
                        Decimal("0"),
                    )
                    _amount(pending)
                except (ValueError, ArithmeticError, TypeError):
                    raise OwnershipConflict("M06_SHADOW_LEDGER_CORRUPT") from None
                return replace(
                    protected,
                    local_shadow_commitments_idr=pending,
                    local_shadow_coverage_complete=True,
                )
        except OwnershipConflict:
            raise
        except (OSError, sqlite3.Error, ValueError):
            raise OwnershipConflict("M06_LOCAL_LEDGER_UNAVAILABLE") from None

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

    def protect_manual_asset_floor(self, *, account_key: str,
                                   asset: str,
                                   minimum_qty: Decimal) -> ProtectedRebuyReview:
        """Monotonic LOCAL exclusion. No exchange proof and no floor release."""
        account = self._identity(account_key)
        if asset not in ("FLOKI", "PEPE") or type(asset) is not str:
            raise OwnershipConflict("M06_MANUAL_ASSET_INVALID")
        try:
            qty = _amount(minimum_qty)
        except ValueError:
            raise OwnershipConflict("M06_MANUAL_FLOOR_INVALID") from None
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT minimum_qty FROM m06_manual_asset_floors "
                "WHERE account_hash=? AND asset=?", (account, asset),
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO m06_manual_asset_floors VALUES(?,?,?)",
                    (account, asset, str(qty)),
                )
            else:
                try:
                    previous = _amount(Decimal(row[0]))
                except (ValueError, ArithmeticError):
                    raise OwnershipConflict("M06_MANUAL_FLOOR_CORRUPT") from None
                if qty < previous:
                    raise OwnershipConflict("M06_MANUAL_FLOOR_REDUCTION_DENIED")
                if qty > previous:
                    db.execute(
                        "UPDATE m06_manual_asset_floors SET minimum_qty=? "
                        "WHERE account_hash=? AND asset=?",
                        (str(qty), account, asset),
                    )
            return self._read_rebuy_in_transaction(db, account)

    @staticmethod
    def _read_rebuy_in_transaction(db, account_hash: str) -> ProtectedRebuyReview:
        rows = db.execute(
            "SELECT asset,gross_idr FROM m06_special_rebuy_observations "
            "WHERE account_hash=? ORDER BY trade_id LIMIT 10001",
            (account_hash,),
        ).fetchall()
        if len(rows) > 10000:
            raise OwnershipConflict("M06_REBUY_LEDGER_LIMIT_EXCEEDED")
        floki, pepe = Decimal("0"), Decimal("0")
        for asset, raw in rows:
            try:
                value = _amount(Decimal(raw))
            except (ValueError, ArithmeticError):
                raise OwnershipConflict("M06_REBUY_LEDGER_AMOUNT_CORRUPT") from None
            if asset == "FLOKI":
                floki += value
            elif asset == "PEPE":
                pepe += value
            else:
                raise OwnershipConflict("M06_REBUY_LEDGER_ASSET_CORRUPT")
        try:
            _amount(floki)
            _amount(pepe)
            _amount(floki + pepe)
        except ValueError:
            raise OwnershipConflict("M06_REBUY_LEDGER_TOTAL_OUT_OF_RANGE") from None
        floors = dict(db.execute(
            "SELECT asset,minimum_qty FROM m06_manual_asset_floors "
            "WHERE account_hash=?", (account_hash,),
        ).fetchall())
        if any(asset not in ("FLOKI", "PEPE") for asset in floors):
            raise OwnershipConflict("M06_MANUAL_FLOOR_ASSET_CORRUPT")
        try:
            floki_floor = _amount(Decimal(floors.get("FLOKI", "0")))
            pepe_floor = _amount(Decimal(floors.get("PEPE", "0")))
        except (ValueError, ArithmeticError):
            raise OwnershipConflict("M06_MANUAL_FLOOR_CORRUPT") from None
        return ProtectedRebuyReview(
            floki, pepe, floki + pepe, len(rows),
            floki_manual_qty_local_floor=floki_floor,
            pepe_manual_qty_local_floor=pepe_floor,
            floki_manual_floor_recorded="FLOKI" in floors,
            pepe_manual_floor_recorded="PEPE" in floors,
        )

    def review_protected_rebuy(self, *, account_key: str) -> ProtectedRebuyReview:
        """Read persisted conservative holds; NEVER claim actual wallet proceeds."""
        account = self._identity(account_key)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._read_rebuy_in_transaction(db, account)

    @staticmethod
    def _maybe_protect_special_sell(db, *, account_hash: str,
                                    order: OrderRecord,
                                    fill: FillRecord) -> None:
        asset = {"flokiidr": "FLOKI", "pepeidr": "PEPE"}.get(order.symbol)
        if asset is None or order.side != "SELL":
            return
        if fill.is_buyer is not None and fill.is_buyer is not False:
            raise OwnershipConflict("M06_SPECIAL_FILL_SIDE_CONFLICT")
        if (fill.client_order_id is not None
                and fill.client_order_id != order.client_order_id):
            raise OwnershipConflict("M06_SPECIAL_FILL_CLIENT_ID_CONFLICT")
        try:
            _amount(fill.quantity)
            _amount(fill.price)
            _amount(fill.commission)
            if fill.quantity <= 0 or fill.price <= 0:
                raise ValueError("M06_SPECIAL_FILL_NONPOSITIVE")
            gross = _amount(fill.quantity * fill.price)
            if fill.quote_quantity is not None:
                gross = max(gross, _amount(fill.quote_quantity))
            if gross <= 0:
                raise ValueError("M06_SPECIAL_FILL_GROSS_NONPOSITIVE")
        except ValueError:
            raise OwnershipConflict("M06_SPECIAL_FILL_AMOUNT_INVALID") from None
        # Conservatively hold GROSS quote IDR (no fee deduction); a locally
        # observed rebuy BUY is not allowed to reduce this hold.
        observed = (account_hash, asset, order.order_id, str(gross))
        prior = db.execute(
            "SELECT account_hash,asset,order_id,gross_idr "
            "FROM m06_special_rebuy_observations WHERE trade_id=?",
            (fill.trade_id,),
        ).fetchone()
        if prior is not None:
            if prior != observed:
                raise OwnershipConflict("M06_REBUY_TRADE_REPLAY_CONFLICT")
        else:
            db.execute(
                "INSERT INTO m06_special_rebuy_observations "
                "(trade_id,account_hash,asset,order_id,gross_idr) "
                "VALUES(?,?,?,?,?)",
                (fill.trade_id, *observed),
            )

    def observe(self, *, account_key: str, order: OrderRecord,
                fills: tuple[FillRecord, ...]) -> OwnershipReview:
        account=self._identity(account_key)
        if type(order) is not OrderRecord or type(fills) is not tuple:
            raise OwnershipConflict("M06_OWNERSHIP_INPUT_INVALID")
        if any(type(f) is not FillRecord or f.order_id!=order.order_id
               or f.symbol!=order.symbol for f in fills):
            raise OwnershipConflict("M06_FOREIGN_FILL")
        if (order.bot_owned_verified is not False
                or any(f.bot_owned_verified is not False for f in fills)):
            raise OwnershipConflict("M06_FORGED_OWNERSHIP_AUTHORITY")
        if any(f.client_order_id is not None
               and f.client_order_id != order.client_order_id for f in fills):
            raise OwnershipConflict("M06_FOREIGN_CLIENT_FILL")
        if any(f.is_buyer is not None
               and f.is_buyer != (order.side == "BUY") for f in fills):
            raise OwnershipConflict("M06_FILL_SIDE_MISMATCH")
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
                if matched:
                    self._maybe_protect_special_sell(
                        db, account_hash=account, order=order, fill=fill,
                    )
            if matched and order.symbol in ("flokiidr", "pepeidr") and fills:
                # A partial snapshot cannot justify more observed fills than
                # the reported cumulative exchange executed quantity.
                prior_quantities = db.execute(
                    "SELECT qty FROM m06_observed_fills WHERE order_id=?",
                    (order.order_id,),
                ).fetchall()
                try:
                    observed_quantity = sum(
                        (_amount(Decimal(qty)) for (qty,) in prior_quantities),
                        Decimal("0"),
                    )
                except ValueError:
                    raise OwnershipConflict("M06_OBSERVED_FILL_QTY_INVALID") from None
                if observed_quantity > order.executed_qty:
                    raise OwnershipConflict("M06_SELL_FILL_EXCEEDS_ORDER")
            protected = self._read_rebuy_in_transaction(db, account)
            return OwnershipReview(order.order_id,row is not None,matched,
                                   tuple(f.trade_id for f in fills),
                                   protected_rebuy=protected)
