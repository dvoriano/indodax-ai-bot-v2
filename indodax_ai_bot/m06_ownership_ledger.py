"""M06 durable, fail-closed ownership evidence for bot-originated order intents.

Stores only a locally generated order intent. Exchange fills MUST be independently
reconciled before an owned position can be declared. No network or trading.
"""
from __future__ import annotations
import hashlib
import json
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
    # Local evidence of coexistence conflicts, never complete peer coverage.
    local_other_account_records_present: bool = False
    local_orphan_fill_records_present: bool = False
    external_peer_coverage_verified: bool = False
    # Local immutability coverage only, NEVER exchange provenance.
    local_rebuy_receipts_linked: bool = False
    local_fill_receipt_count: int = 0
    # Special manual/external SELL fills imported as LOCAL offline evidence.
    # Does not certify complete history or exchange-attested ownership.
    locally_observed_external_special_sell_fills: int = 0
    # Tentative same-token BUY fills. Do NOT subtract from SELL rebuy hold.
    floki_rebuy_buy_observed_outflow_idr: Decimal = Decimal("0")
    pepe_rebuy_buy_observed_outflow_idr: Decimal = Decimal("0")
    local_rebuy_buy_observation_count: int = 0
    rebuy_consumption_settled_verified: bool = False


@dataclass(frozen=True, repr=False)
class M06ReceiptWindowReview:
    """Independent exchange-shaped window vs immutable V2 local receipts.

    A matching hash confirms internal consistency, NOT account-wide coverage,
    settled fills or ownership authority. This never emits wallet identifiers.
    """
    checked_fills: int
    matched_local_receipts: int
    unlinked_fills: int
    account_wide_history_complete: bool = False
    bot_owned_verified: bool = False
    execution_allowed: bool = False

    def __repr__(self) -> str:
        return "<M06ReceiptWindowReview REDACTED>"


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
            db.execute("""CREATE TABLE IF NOT EXISTS m06_local_fill_receipts (
                trade_id TEXT PRIMARY KEY,
                account_hash TEXT NOT NULL,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
                client_id TEXT NOT NULL,
                content_sha256 TEXT NOT NULL
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
                # Older V2 databases may not yet have the OPTIONAL manual
                # SELL receipt table. A readonly inspection must not mutate
                # its schema or incorrectly claim a missing table is a fill.
                present = {
                    row[0] for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                bound = any(
                    db.execute(
                        "SELECT 1 FROM " + table
                        + " WHERE account_hash=? LIMIT 1",
                        (account_hash,),
                    ).fetchone() is not None
                    for table in (
                        "m06_intents",
                        "m06_special_rebuy_observations",
                        "m06_scoped_external_sell_receipts",
                        "m06_scoped_rebuy_buy_observations",
                        "m06_manual_asset_floors",
                    )
                    if table in present
                )
                if not bound:
                    raise OwnershipConflict("M06_LOCAL_ACCOUNT_UNBOUND")
                protected = cls._read_rebuy_in_transaction(db, account_hash)
                foreign, orphan = cls._inspect_local_coexistence(
                    db, account_hash=account_hash,
                )
                protected = replace(
                    protected,
                    local_other_account_records_present=foreign,
                    local_orphan_fill_records_present=orphan,
                    # Local SQLite cannot prove V1, other hosts or outside
                    # clients have stopped accessing this Indodax account.
                    external_peer_coverage_verified=False,
                )
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
    def _inspect_local_coexistence(db, *, account_hash: str) -> tuple[bool, bool]:
        """Local conflict hints inside the current immutable read-only snapshot.

        An empty result is NOT evidence no other process uses the exchange
        account. Never use this method to grant LIVE or exclusive ownership.
        """
        local_sources = (
            "m06_intents",
            "m06_special_rebuy_observations",
            "m06_scoped_external_sell_receipts",
            "m06_scoped_rebuy_buy_observations",
            "m06_manual_asset_floors",
            "m06_local_fill_receipts",
            "m06_shadow_reserve",
        )
        foreign = False
        for table in local_sources:
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if exists is None:
                continue
            column = ("account_key_hash" if table == "m06_shadow_reserve"
                      else "account_hash")
            # Both the table and column come from fixed source constants.
            found = db.execute(
                "SELECT 1 FROM " + table + " WHERE " + column
                + " != ? LIMIT 1", (account_hash,),
            ).fetchone()
            foreign = foreign or found is not None

        # Local fill tables do not carry an account column. A fill without a
        # receipt cannot be attributed from the current V2 SQLite alone.
        have_fills = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='m06_observed_fills'",
        ).fetchone() is not None
        have_receipts = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='m06_local_fill_receipts'",
        ).fetchone() is not None
        orphan = False
        if have_fills:
            if not have_receipts:
                orphan = db.execute(
                    "SELECT 1 FROM m06_observed_fills LIMIT 1",
                ).fetchone() is not None
            else:
                orphan = db.execute(
                    "SELECT 1 FROM m06_observed_fills AS f "
                    "WHERE NOT EXISTS ("
                    "SELECT 1 FROM m06_local_fill_receipts AS r "
                    "WHERE r.trade_id=f.trade_id"
                    ") LIMIT 1",
                ).fetchone() is not None
        return foreign, orphan

    @staticmethod
    def _identity(account_key: str) -> str:
        if type(account_key) is not str or not 1<=len(account_key)<=128:
            raise OwnershipConflict("M06_ACCOUNT_SCOPE_INVALID")
        return hashlib.sha256(account_key.encode()).hexdigest()

    @classmethod
    def match_exchange_window_to_local_receipts_readonly(cls, *,
            db_path: str, account_uid: int,
            orders: tuple[OrderRecord, ...],
            fills: tuple[FillRecord, ...], symbol: str) -> M06ReceiptWindowReview:
        """Compare existing V2 intent+receipt to one bounded order/fill window.

        No writes, new private GET, backfilling missing receipts or ownership
        promotion. Foreign/missing records remain unlinked. Conflicting
        immutable receipts deny the whole read-only review.
        """
        if (type(orders) is not tuple or type(fills) is not tuple
                or type(symbol) is not str or not symbol.endswith("idr")
                or any(type(x) is not OrderRecord for x in orders)
                or any(type(x) is not FillRecord for x in fills)):
            raise OwnershipConflict("M06_WINDOW_INPUT_INVALID")
        if (any(x.symbol != symbol or x.bot_owned_verified is not False
                for x in orders)
                or any(x.symbol != symbol or x.bot_owned_verified is not False
                       for x in fills)):
            raise OwnershipConflict("M06_WINDOW_SCOPE_INVALID")
        # Reuse the existing owner-UID and mode=ro permission checks.
        cls.inspect_existing_account_readonly(
            db_path=db_path, account_uid=account_uid,
        )
        account = cls._identity(cls.account_scope_from_exchange_uid(account_uid))
        by_id = {}
        for order in orders:
            if order.order_id in by_id:
                raise OwnershipConflict("M06_WINDOW_DUPLICATE_ORDER")
            by_id[order.order_id] = order
        unique_fills = set()
        found = 0
        try:
            p = Path(os.path.abspath(db_path))
            with closing(sqlite3.connect(p.as_uri() + "?mode=ro",
                                         uri=True, timeout=2)) as db:
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                for fill in fills:
                    if fill.trade_id in unique_fills:
                        raise OwnershipConflict("M06_WINDOW_DUPLICATE_FILL")
                    unique_fills.add(fill.trade_id)
                    order = by_id.get(fill.order_id)
                    if (order is None or order.client_order_id is None
                            or fill.client_order_id != order.client_order_id
                            or fill.is_buyer is None
                            or fill.is_buyer != (order.side == "BUY")):
                        continue
                    intent = db.execute(
                        "SELECT account_hash,symbol,side,client_id "
                        "FROM m06_intents WHERE order_id=?",
                        (order.order_id,),
                    ).fetchone()
                    if intent != (account, symbol, order.side, order.client_order_id):
                        continue
                    try:
                        for amount in (fill.quantity, fill.price, fill.commission):
                            _amount(amount)
                        if fill.quantity <= 0 or fill.price <= 0:
                            raise ValueError("M06_WINDOW_NONPOSITIVE")
                        if fill.quote_quantity is not None:
                            _amount(fill.quote_quantity)
                    except ValueError:
                        raise OwnershipConflict("M06_WINDOW_ECONOMICS_INVALID") from None
                    evidence = [
                        account, order.order_id, order.symbol, order.side,
                        order.client_order_id, fill.trade_id, fill.order_id,
                        fill.symbol, str(fill.quantity), str(fill.price),
                        str(fill.commission), fill.is_buyer,
                        str(fill.quote_quantity)
                        if fill.quote_quantity is not None else None,
                        fill.commission_asset,
                    ]
                    digest = hashlib.sha256(json.dumps(
                        evidence, separators=(",", ":"), ensure_ascii=True,
                    ).encode("ascii")).hexdigest()
                    receipt = db.execute(
                        "SELECT account_hash,order_id,symbol,side,client_id,"
                        "content_sha256 FROM m06_local_fill_receipts "
                        "WHERE trade_id=?",
                        (fill.trade_id,),
                    ).fetchone()
                    if receipt is None:
                        continue
                    if receipt != (account, order.order_id, symbol, order.side,
                                   order.client_order_id, digest):
                        raise OwnershipConflict("M06_EXCHANGE_WINDOW_RECEIPT_CONFLICT")
                    observed = db.execute(
                        "SELECT order_id,symbol,qty,price,fee "
                        "FROM m06_observed_fills WHERE trade_id=?",
                        (fill.trade_id,),
                    ).fetchone()
                    if observed != (order.order_id, symbol, str(fill.quantity),
                                    str(fill.price), str(fill.commission)):
                        raise OwnershipConflict("M06_EXCHANGE_WINDOW_FILL_CONFLICT")
                    found += 1
        except OwnershipConflict:
            raise
        except (OSError, sqlite3.Error):
            raise OwnershipConflict("M06_WINDOW_LEDGER_UNAVAILABLE") from None
        return M06ReceiptWindowReview(
            checked_fills=len(fills), matched_local_receipts=found,
            unlinked_fills=len(fills) - found,
        )

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
        # Union local V2-intent SELL receipts and external/manual SELL
        # observations by trade ID so the SAME fill never counts twice.
        # External rows are a conservative hold only, NOT owned positions.
        rows = db.execute(
            "SELECT trade_id,asset,order_id,gross_idr "
            "FROM m06_special_rebuy_observations "
            "WHERE account_hash=? ORDER BY trade_id LIMIT 10001",
            (account_hash,),
        ).fetchall()
        if len(rows) > 10000:
            raise OwnershipConflict("M06_REBUY_LEDGER_LIMIT_EXCEEDED")
        by_trade = {tid: (asset, oid, raw) for tid,asset,oid,raw in rows}
        external_count = 0
        external_only_count = 0
        has_external = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='m06_scoped_external_sell_receipts'"
        ).fetchone() is not None
        if has_external:
            external = db.execute(
                "SELECT trade_id,asset,order_id,gross_idr "
                "FROM m06_scoped_external_sell_receipts "
                "WHERE account_hash=? ORDER BY trade_id LIMIT 10001",
                (account_hash,),
            ).fetchall()
            if len(external) > 10000:
                raise OwnershipConflict("M06_SCOPED_EXTERNAL_LEDGER_LIMIT")
            external_count = len(external)
            for tid,asset,oid,raw in external:
                prior = by_trade.get(tid)
                if prior is not None:
                    try:
                        compatible = (
                            prior[0] == asset and prior[1] == oid
                            and _amount(Decimal(prior[2])) ==
                                _amount(Decimal(raw))
                        )
                    except (ValueError, ArithmeticError):
                        raise OwnershipConflict(
                            "M06_DUPLICATE_SPECIAL_GROSS_CORRUPT") from None
                    if not compatible:
                        raise OwnershipConflict(
                            "M06_DUPLICATE_SPECIAL_SELL_SOURCE_CONFLICT")
                else:
                    by_trade[tid] = (asset,oid,raw)
                    external_only_count += 1
        if len(by_trade) > 10000:
            raise OwnershipConflict("M06_REBUY_LEDGER_LIMIT_EXCEEDED")
        # Read optional tentative BUY observations from the SAME UID-bound
        # V2 ledger. These are diagnostics ONLY: not authenticated settlement
        # or permission to decrease the original SELL rebuy protection.
        rebuy_buy_count = 0
        floki_buy = Decimal("0")
        pepe_buy = Decimal("0")
        has_rebuys = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='m06_scoped_rebuy_buy_observations'"
        ).fetchone() is not None
        if has_rebuys:
            buy_rows = db.execute(
                "SELECT asset,observed_outflow_idr "
                "FROM m06_scoped_rebuy_buy_observations "
                "WHERE account_hash=? LIMIT 10001",
                (account_hash,),
            ).fetchall()
            if len(buy_rows) > 10000:
                raise OwnershipConflict("M06_REBUY_BUY_LEDGER_LIMIT")
            rebuy_buy_count = len(buy_rows)
            for asset,raw in buy_rows:
                try:
                    amount = _amount(Decimal(raw))
                except (ValueError, TypeError, ArithmeticError):
                    raise OwnershipConflict("M06_REBUY_BUY_VALUE_CORRUPT") from None
                if asset == "FLOKI":
                    floki_buy += amount
                elif asset == "PEPE":
                    pepe_buy += amount
                else:
                    raise OwnershipConflict("M06_REBUY_BUY_ASSET_CORRUPT")
            try:
                _amount(floki_buy + pepe_buy)
            except (ValueError, TypeError, ArithmeticError):
                raise OwnershipConflict("M06_REBUY_BUY_OVERFLOW") from None
        floki, pepe = Decimal("0"), Decimal("0")
        for asset, oid, raw in by_trade.values():
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
        # Legacy local fill rows do not acquire provenance retroactively.
        # Read-only inspection of older SQLite must still preserve their
        # conservative rebuy holds, but flag receipt linkage as unproven.
        has_receipts = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='m06_local_fill_receipts'"
        ).fetchone() is not None
        # External/manual fills do not have a bot-intent receipt. A table
        # existing for other fills must not certify these as V2 bot-owned.
        linked = has_receipts and external_only_count == 0
        receipt_count = 0
        if has_receipts:
            receipt_rows = db.execute(
                "SELECT trade_id,order_id,symbol,side,client_id,content_sha256 "
                "FROM m06_local_fill_receipts WHERE account_hash=? LIMIT 10001",
                (account_hash,),
            ).fetchall()
            if len(receipt_rows) > 10000:
                raise OwnershipConflict("M06_LOCAL_RECEIPTS_LIMIT_EXCEEDED")
            receipt_count = len(receipt_rows)
            receipt_map = {row[0]: row for row in receipt_rows}
            for asset, tid, oid in db.execute(
                "SELECT asset,trade_id,order_id FROM "
                "m06_special_rebuy_observations WHERE account_hash=?",
                (account_hash,),
            ):
                linked_row = receipt_map.get(tid)
                if linked_row is None:
                    linked = False
                elif (linked_row[1] != oid
                        or linked_row[2] != asset.lower() + "idr"
                        or linked_row[3] != "SELL"):
                    raise OwnershipConflict("M06_SPECIAL_RECEIPT_CONFLICT")
        return ProtectedRebuyReview(
            floki, pepe, floki + pepe, len(by_trade),
            floki_manual_qty_local_floor=floki_floor,
            pepe_manual_qty_local_floor=pepe_floor,
            floki_manual_floor_recorded="FLOKI" in floors,
            pepe_manual_floor_recorded="PEPE" in floors,
            local_rebuy_receipts_linked=linked,
            local_fill_receipt_count=receipt_count,
            locally_observed_external_special_sell_fills=external_count,
            floki_rebuy_buy_observed_outflow_idr=floki_buy,
            pepe_rebuy_buy_observed_outflow_idr=pepe_buy,
            local_rebuy_buy_observation_count=rebuy_buy_count,
            rebuy_consumption_settled_verified=False,
        )

    def observe_scoped_external_special_sells_offline(
            self, *, account_uid: int, symbol: str,
            orders: tuple[OrderRecord, ...],
            fills: tuple[FillRecord, ...],
    ) -> ProtectedRebuyReview:
        """Atomic idempotent LOCAL record of same-pair View Only SELL fills.

        Deliberate OFFLINE-only method. Its inputs can be mocked and DO NOT
        independently attest authenticated exchange origin or all-time
        coverage. Caller MUST obtain any future real fills from an explicitly
        owner-approved View Only source. No real M06 GET is made here.

        Missing order, unknown direction or ambiguous client ID DENIES the
        whole batch. Never interprets absence of a fill as no historic hold.
        """
        from .m06_special_sale_window import (
            observe_m06_special_sale_window, M06SpecialSaleWindowRejected,
        )
        account_hash = self._identity(
            self.account_scope_from_exchange_uid(account_uid)
        )
        # This OPTIONAL local import must never create an unchecked DB,
        # follow a symlink or persist manual order IDs in a shared file.
        try:
            p = self.path
            parent = p.parent.lstat()
            file = p.lstat()
            owner = os.getuid()
            secure = (
                stat.S_ISDIR(parent.st_mode)
                and stat.S_ISREG(file.st_mode)
                and parent.st_uid == owner and file.st_uid == owner
                and parent.st_mode & 0o077 == 0
                and file.st_mode & 0o077 == 0
            )
        except (OSError, ValueError):
            secure = False
        if not secure:
            raise OwnershipConflict("M06_EXTERNAL_LOCAL_LEDGER_INSECURE")
        if (type(orders) is not tuple or type(fills) is not tuple
                or len(orders) > 1000 or len(fills) > 1000):
            raise OwnershipConflict("M06_EXTERNAL_SCOPED_BATCH_INVALID")
        try:
            checked = observe_m06_special_sale_window(
                symbol=symbol, orders=orders, fills=fills,
            )
        except M06SpecialSaleWindowRejected:
            raise OwnershipConflict(
                "M06_EXTERNAL_SCOPED_FILL_INVALID") from None
        if checked.scope_has_unresolved_fills:
            raise OwnershipConflict("M06_EXTERNAL_SCOPED_COVERAGE_AMBIGUOUS")
        by_id = {row.order_id: row for row in orders}
        prepared = []
        for fill in fills:
            order = by_id.get(fill.order_id)
            if (order is None or fill.is_buyer is None
                    or order.client_order_id is None
                    or fill.client_order_id != order.client_order_id):
                raise OwnershipConflict("M06_EXTERNAL_SOURCE_LINK_UNVERIFIED")
            # BUY fills are not SELL proceeds. BUY rebuy consumption is a
            # separate unsettled problem and CANNOT reduce this source hold.
            if order.side != "SELL":
                continue
            if (fill.is_buyer is not False or order.symbol != symbol):
                raise OwnershipConflict("M06_EXTERNAL_SELL_SIDE_INVALID")
            try:
                gross = _amount(fill.quantity * fill.price)
                if fill.quote_quantity is not None:
                    gross = max(gross, _amount(fill.quote_quantity))
                if gross <= 0:
                    raise ValueError("M06_EXTERNAL_SELL_GROSS_NONPOSITIVE")
            except (ValueError, ArithmeticError, TypeError):
                raise OwnershipConflict("M06_EXTERNAL_SELL_AMOUNT_INVALID") from None
            asset = symbol[:-3].upper()
            content = [
                account_hash, asset, order.order_id, order.client_order_id,
                fill.trade_id, fill.order_id, fill.client_order_id,
                fill.symbol, str(fill.quantity), str(fill.price),
                str(fill.quote_quantity) if fill.quote_quantity is not None else None,
                str(fill.commission), fill.commission_asset, fill.is_buyer,
            ]
            digest = hashlib.sha256(json.dumps(
                content, separators=(",", ":"), ensure_ascii=True
            ).encode("ascii")).hexdigest()
            prepared.append((fill.trade_id, account_hash, asset,
                             order.order_id, str(gross), digest))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            # Explicit offline ingestion only. Normal M06 startup/read-only
            # and existing historical ledgers never create this new table.
            db.execute("""CREATE TABLE IF NOT EXISTS m06_scoped_external_sell_receipts (
                trade_id TEXT PRIMARY KEY,
                account_hash TEXT NOT NULL,
                asset TEXT NOT NULL CHECK(asset IN ('FLOKI','PEPE')),
                order_id TEXT NOT NULL,
                gross_idr TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL
            )""")
            for entry in prepared:
                prior = db.execute(
                    "SELECT account_hash,asset,order_id,gross_idr,receipt_sha256 "
                    "FROM m06_scoped_external_sell_receipts WHERE trade_id=?",
                    (entry[0],),
                ).fetchone()
                if prior is not None:
                    if prior != entry[1:]:
                        raise OwnershipConflict("M06_EXTERNAL_SCOPED_REPLAY_CONFLICT")
                    continue
                local = db.execute(
                    "SELECT account_hash,asset,order_id,gross_idr "
                    "FROM m06_special_rebuy_observations WHERE trade_id=?",
                    (entry[0],),
                ).fetchone()
                if local is not None:
                    if (local[:3] != entry[1:4]
                            or _amount(Decimal(local[3])) !=
                               _amount(Decimal(entry[4]))):
                        raise OwnershipConflict("M06_EXTERNAL_OVERLAP_CONFLICT")
                    # A matching gross amount alone is NOT enough. A replay
                    # could alter qty/price/fee while preserving total IDR.
                    # Compare against the original locally immutable fill.
                    original = db.execute(
                        "SELECT order_id,symbol,qty,price,fee "
                        "FROM m06_observed_fills WHERE trade_id=?",
                        (entry[0],),
                    ).fetchone()
                    raw_fill = next(
                        (x for x in fills if x.trade_id == entry[0]),
                        None,
                    )
                    if raw_fill is None or original != (
                            raw_fill.order_id, raw_fill.symbol,
                            str(raw_fill.quantity), str(raw_fill.price),
                            str(raw_fill.commission)):
                        raise OwnershipConflict(
                            "M06_EXTERNAL_OVERLAP_FILL_CONFLICT")
                db.execute(
                    "INSERT INTO m06_scoped_external_sell_receipts "
                    "(trade_id,account_hash,asset,order_id,gross_idr,receipt_sha256) "
                    "VALUES(?,?,?,?,?,?)", entry,
                )
            return self._read_rebuy_in_transaction(db, account_hash)

    def observe_scoped_same_token_rebuy_buy_offline(
            self, *, account_uid: int, symbol: str,
            orders: tuple[OrderRecord, ...],
            fills: tuple[FillRecord, ...],
    ) -> ProtectedRebuyReview:
        """Persist bounded same-token BUY observation, never release SELL hold.

        Synthetic/owner-provided parsed rows do not prove authenticated
        private history, full rebuy provenance, exchange settlement, or
        available main IDR. Rebuy consumption is OBSERVED, not CREDITED.
        This original M06 ledger method makes NO private request or order.
        """
        account_hash = self._identity(
            self.account_scope_from_exchange_uid(account_uid)
        )
        if (type(symbol) is not str
                or symbol not in ("flokiidr", "pepeidr")
                or type(orders) is not tuple or type(fills) is not tuple
                or len(orders) > 1000 or len(fills) > 1000):
            raise OwnershipConflict("M06_REBUY_BUY_SCOPE_INVALID")
        # Same physical owner-only existing ledger as M06 special SELL.
        # Reject missing/symlink/world-accessible file before any write.
        try:
            d=self.path.parent.lstat()
            f=self.path.lstat()
            uid=os.getuid()
            if (not stat.S_ISDIR(d.st_mode) or d.st_uid != uid
                    or d.st_mode & 0o077
                    or not stat.S_ISREG(f.st_mode) or f.st_uid != uid
                    or f.st_mode & 0o077):
                raise OwnershipConflict("M06_REBUY_BUY_DB_INSECURE")
        except OSError:
            raise OwnershipConflict("M06_REBUY_BUY_DB_INSECURE") from None
        by_id = {}
        for order in orders:
            if (type(order) is not OrderRecord or order.symbol != symbol
                    or order.side not in ("BUY","SELL")
                    or order.bot_owned_verified is not False
                    or order.order_id in by_id):
                raise OwnershipConflict("M06_REBUY_BUY_ORDER_INVALID")
            by_id[order.order_id] = order
        sums = {}
        prepared = []
        seen = set()
        for fill in fills:
            if (type(fill) is not FillRecord or fill.symbol != symbol
                    or fill.bot_owned_verified is not False
                    or fill.trade_id in seen):
                raise OwnershipConflict("M06_REBUY_BUY_FILL_INVALID")
            seen.add(fill.trade_id)
            order=by_id.get(fill.order_id)
            if (order is None or fill.client_order_id is None
                    or order.client_order_id != fill.client_order_id
                    or fill.is_buyer is None
                    or fill.is_buyer != (order.side == "BUY")):
                raise OwnershipConflict("M06_REBUY_BUY_SOURCE_UNLINKED")
            if order.side != "BUY":
                # A paired SELL may be present in the bounded window; it
                # stays in the separate existing SELL source observation.
                continue
            try:
                for amount in (fill.quantity,fill.price,fill.commission):
                    _amount(amount)
                if fill.quantity <= 0 or fill.price <= 0:
                    raise ValueError("M06_REBUY_BUY_AMOUNT_NONPOSITIVE")
                if (type(order.executed_qty) is not Decimal
                        or not order.executed_qty.is_finite()
                        or order.executed_qty < 0):
                    raise ValueError("M06_REBUY_BUY_ORDER_EXEC_INVALID")
                fill_sum=sums.get(fill.order_id,Decimal("0"))+fill.quantity
                if fill_sum>order.executed_qty:
                    raise ValueError("M06_REBUY_BUY_FILL_OVER_EXEC")
                sums[fill.order_id]=fill_sum
                outflow = _amount(fill.quantity*fill.price)
                if fill.quote_quantity is not None:
                    outflow=max(outflow,_amount(fill.quote_quantity))
                if fill.commission_asset is not None and (
                        type(fill.commission_asset) is not str
                        or fill.commission_asset.lower() not in (
                            "idr", "floki", "pepe")):
                    raise ValueError("M06_REBUY_BUY_FEE_ASSET_UNKNOWN")
                if fill.commission_asset == "idr":
                    outflow=_amount(outflow+fill.commission)
                if outflow<=0:
                    raise ValueError("M06_REBUY_BUY_SPEND_INVALID")
            except (ValueError, TypeError, ArithmeticError):
                raise OwnershipConflict("M06_REBUY_BUY_ECONOMICS_INVALID") from None
            asset=symbol[:-3].upper()
            evidence=[
                account_hash,asset,order.order_id,order.client_order_id,
                fill.trade_id,fill.order_id,fill.client_order_id,fill.symbol,
                str(fill.quantity),str(fill.price),
                str(fill.quote_quantity) if fill.quote_quantity is not None else None,
                str(fill.commission),fill.commission_asset,fill.is_buyer,
            ]
            digest=hashlib.sha256(json.dumps(
                evidence,separators=(",",":"),ensure_ascii=True,
            ).encode("ascii")).hexdigest()
            prepared.append((fill.trade_id,account_hash,asset,order.order_id,
                             str(outflow),digest))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            # Optional schema, never created on regular ledger startup.
            db.execute("""CREATE TABLE IF NOT EXISTS m06_scoped_rebuy_buy_observations (
                trade_id TEXT PRIMARY KEY,
                account_hash TEXT NOT NULL,
                asset TEXT NOT NULL CHECK(asset IN ('FLOKI','PEPE')),
                order_id TEXT NOT NULL,
                observed_outflow_idr TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL
            )""")
            for entry in prepared:
                collision=db.execute(
                    "SELECT 1 FROM m06_scoped_external_sell_receipts "
                    "WHERE trade_id=?", (entry[0],)
                ).fetchone() if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='m06_scoped_external_sell_receipts'"
                ).fetchone() else None
                if collision is not None:
                    raise OwnershipConflict("M06_REBUY_BUY_COLLIDES_WITH_SELL")
                # A bot-owned receipt with the same trade ID must match
                # immutable raw fill economics; otherwise hard conflict.
                local=db.execute(
                    "SELECT order_id,symbol,qty,price,fee "
                    "FROM m06_observed_fills WHERE trade_id=?",
                    (entry[0],),
                ).fetchone()
                if local is not None:
                    raw=next((x for x in fills
                              if x.trade_id==entry[0]),None)
                    if raw is None or local!=(
                            raw.order_id,raw.symbol,str(raw.quantity),
                            str(raw.price),str(raw.commission)):
                        raise OwnershipConflict("M06_REBUY_BUY_LOCAL_FILL_CONFLICT")
                prior=db.execute(
                    "SELECT account_hash,asset,order_id,observed_outflow_idr,"
                    "receipt_sha256 FROM m06_scoped_rebuy_buy_observations "
                    "WHERE trade_id=?", (entry[0],)
                ).fetchone()
                if prior is not None:
                    if tuple(prior)!=entry[1:]:
                        raise OwnershipConflict("M06_REBUY_BUY_REPLAY_CONFLICT")
                    continue
                db.execute(
                    "INSERT INTO m06_scoped_rebuy_buy_observations "
                    "(trade_id,account_hash,asset,order_id,"
                    "observed_outflow_idr,receipt_sha256) VALUES(?,?,?,?,?,?)",
                    entry,
                )
            return self._read_rebuy_in_transaction(db, account_hash)

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
                # Immutable local provenance receipt ties a previously
                # recorded V2 intent, specific UID, full fill economics and
                # direction. It does NOT attest actual exchange ownership.
                _amount(fill.quantity)
                _amount(fill.price)
                _amount(fill.commission)
                if fill.quantity <= 0 or fill.price <= 0:
                    raise OwnershipConflict("M06_FILL_NONPOSITIVE")
                if fill.quote_quantity is not None:
                    _amount(fill.quote_quantity)
                if fill.commission_asset is not None and (
                        type(fill.commission_asset) is not str
                        or not 2 <= len(fill.commission_asset) <= 20):
                    raise OwnershipConflict("M06_FILL_FEE_ASSET_INVALID")
                evidence = [
                    account, order.order_id, order.symbol, order.side,
                    order.client_order_id, fill.trade_id, fill.order_id,
                    fill.symbol, str(fill.quantity), str(fill.price),
                    str(fill.commission), fill.is_buyer,
                    str(fill.quote_quantity) if fill.quote_quantity is not None else None,
                    fill.commission_asset,
                ]
                digest = hashlib.sha256(json.dumps(
                    evidence, separators=(",", ":"), ensure_ascii=True,
                ).encode("ascii")).hexdigest()
                receipt = (account, order.order_id, order.symbol,
                           order.side, order.client_order_id, digest)
                receipt_row = db.execute(
                    "SELECT account_hash,order_id,symbol,side,client_id,content_sha256 "
                    "FROM m06_local_fill_receipts WHERE trade_id=?",
                    (fill.trade_id,),
                ).fetchone()
                payload=(fill.order_id,fill.symbol,str(fill.quantity),str(fill.price),str(fill.commission))
                existing=db.execute("SELECT order_id,symbol,qty,price,fee FROM m06_observed_fills WHERE trade_id=?",
                                    (fill.trade_id,)).fetchone()
                if existing is not None and existing!=payload:
                    raise OwnershipConflict("M06_FILL_REPLAY_CONFLICT")
                if receipt_row is not None and tuple(receipt_row) != receipt:
                    raise OwnershipConflict("M06_FILL_RECEIPT_REPLAY_CONFLICT")
                if existing is not None and receipt_row is None:
                    # Legacy row is not silently claimed as original V2
                    # provenance by attaching new metadata after the fact.
                    raise OwnershipConflict("M06_LEGACY_FILL_RECEIPT_ABSENT")
                if existing is None:
                    db.execute("INSERT INTO m06_observed_fills VALUES(?,?,?,?,?,?)",(fill.trade_id,*payload))
                if matched and receipt_row is None:
                    db.execute(
                        "INSERT INTO m06_local_fill_receipts VALUES(?,?,?,?,?,?,?)",
                        (fill.trade_id, *receipt),
                    )
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
