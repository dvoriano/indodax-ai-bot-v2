"""M05 owner PASS/REJECT durable shadow ledger, OFFLINE and non-executable.

This module is NOT a Telegram transport, exchange authenticator, issuer or
order authorizer. A real Telegram adapter must verify update origin before
passing identity into these functions. Nothing here can create an order.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import hmac
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Iterator, Any

PAIR = re.compile(r"^[a-z0-9]+_idr$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
CALLBACK = re.compile(r"^m5:([a-f0-9]{24}):([PR]):([a-f0-9]{32})$")
MANUAL_TRADE_CALLBACK = re.compile(
    r"^m5t:([a-f0-9]{24}):([BSR]):([a-f0-9]{32})$"
)
PROPOSAL_ID = re.compile(r"^[a-f0-9]{24}$")
MODES = ("MANUAL", "AUTO")
TTL_MAX = 90


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "accepted": False, "reason": reason,
        "owner_decision": None, "final_decision": None,
        "execution_allowed": False,
    }


def _decimal_positive(value: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str) or len(value) > 64:
        raise ValueError("INVALID_PRICE_OR_QUANTITY")
    try:
        d = Decimal(value)
    except InvalidOperation:
        raise ValueError("INVALID_PRICE_OR_QUANTITY") from None
    if not d.is_finite() or d <= 0 or d.adjusted() > 18 or d.adjusted() < -18:
        raise ValueError("INVALID_PRICE_OR_QUANTITY")
    return str(d)


class OwnerDecisionLedger:
    """Owner-only, atomic one-use decisions and audit, NEVER order rights."""

    def __init__(self, db_path: str | Path, *, owner_user_id: int) -> None:
        if type(owner_user_id) is not int or owner_user_id <= 0:
            raise ValueError("INVALID_M05_OWNER")
        self.owner_user_id = owner_user_id
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._tx() as db:
            schema = """
                CREATE TABLE IF NOT EXISTS m05_config (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    owner_user_id INTEGER NOT NULL,
                    mode TEXT NOT NULL CHECK(mode IN ('MANUAL','AUTO'))
                );
                CREATE TABLE IF NOT EXISTS m05_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    owner_user_id INTEGER NOT NULL,
                    pair TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    closed_1m_epoch INTEGER NOT NULL,
                    entry_price_idr TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    created_epoch INTEGER NOT NULL,
                    expires_epoch INTEGER NOT NULL,
                    token_hash TEXT NOT NULL,
                    mode_at_create TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('PENDING','PASS','REJECT')),
                    reason TEXT,
                    decision_epoch INTEGER,
                    execution_allowed INTEGER NOT NULL DEFAULT 0
                        CHECK(execution_allowed = 0)
                );
                CREATE TABLE IF NOT EXISTS m05_market_advisory_outbox (
                    proposal_id TEXT PRIMARY KEY,
                    pair TEXT NOT NULL,
                    closed_1m_epoch INTEGER NOT NULL,
                    expires_epoch INTEGER NOT NULL,
                    message_text TEXT NOT NULL,
                    pass_callback TEXT NOT NULL,
                    reject_callback TEXT NOT NULL,
                    delivery_state TEXT NOT NULL
                        CHECK(delivery_state IN ('QUEUED','CLAIMED','SENT','EXPIRED')),
                    sent_message_id INTEGER,
                    UNIQUE(pair,closed_1m_epoch),
                    FOREIGN KEY(proposal_id) REFERENCES m05_proposals(proposal_id)
                );
                CREATE TABLE IF NOT EXISTS m05_manual_trade_previews (
                    preview_id TEXT PRIMARY KEY,
                    owner_user_id INTEGER NOT NULL,
                    account_fingerprint TEXT NOT NULL,
                    client_order_id TEXT NOT NULL UNIQUE,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
                    price_idr TEXT NOT NULL,
                    quantity_base TEXT NOT NULL,
                    draft_sha256 TEXT NOT NULL,
                    created_epoch INTEGER NOT NULL,
                    expires_epoch INTEGER NOT NULL,
                    token_hash TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    confirm_callback TEXT NOT NULL,
                    reject_callback TEXT NOT NULL,
                    send_state TEXT NOT NULL CHECK(send_state IN
                        ('QUEUED','CLAIMED','SENT','EXPIRED')),
                    sent_message_id INTEGER,
                    owner_choice TEXT CHECK(owner_choice IN
                        ('BUY','SELL','REJECT')),
                    decided_epoch INTEGER,
                    execution_allowed INTEGER NOT NULL CHECK(execution_allowed=0)
                );
                CREATE TABLE IF NOT EXISTS m05_audit (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_epoch INTEGER NOT NULL,
                    owner_user_id INTEGER,
                    proposal_id TEXT,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS m05_cycle_observations (
                    trace_id TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    observed_epoch INTEGER NOT NULL,
                    final_decision TEXT NOT NULL CHECK(final_decision='REJECT'),
                    execution_allowed INTEGER NOT NULL CHECK(execution_allowed=0)
                );
                CREATE TABLE IF NOT EXISTS m05_telegram_cursor (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    next_offset INTEGER NOT NULL CHECK(next_offset>=0)
                );
                CREATE TABLE IF NOT EXISTS m05_telegram_updates (
                    update_id INTEGER PRIMARY KEY,
                    callback_query_id TEXT UNIQUE,
                    status TEXT NOT NULL CHECK(status IN ('CLAIMED','DONE')),
                    outcome TEXT,
                    event_epoch INTEGER NOT NULL
                );
            """
            # sqlite3.executescript() silently commits the surrounding BEGIN.
            # Execute DDL statements individually to preserve this transaction.
            for statement in schema.split(";"):
                if statement.strip():
                    db.execute(statement)
            db.execute("INSERT OR IGNORE INTO m05_config VALUES (1, ?, 'MANUAL')",
                       (owner_user_id,))
            row = db.execute(
                "SELECT owner_user_id FROM m05_config WHERE singleton=1"
            ).fetchone()
            if row is None or row[0] != owner_user_id:
                raise ValueError("M05_OWNER_IDENTITY_MISMATCH")
            db.execute(
                "INSERT OR IGNORE INTO m05_telegram_cursor VALUES (1,0)"
            )

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def _is_owner(self, user_id: int, chat_id: int, chat_type: str) -> bool:
        # Telegram transport authenticity is not established by this method.
        return (type(user_id) is int and type(chat_id) is int
                and user_id == self.owner_user_id
                and chat_id == user_id and chat_type == "private")

    def status(self, *, user_id: int, chat_id: int,
               chat_type: str = "private") -> dict[str, Any]:
        if not self._is_owner(user_id, chat_id, chat_type):
            return _blocked("OWNER_NOT_AUTHORIZED")
        with self._tx() as db:
            mode = db.execute("SELECT mode FROM m05_config WHERE singleton=1").fetchone()[0]
            pending = db.execute(
                "SELECT COUNT(*) FROM m05_proposals WHERE state='PENDING'"
            ).fetchone()[0]
        return {
            "accepted": True, "owner_mode": mode, "pending_shadow_proposals": pending,
            "final_decision": None, "execution_allowed": False,
            "telegram_network_connected": False,
            "live_execution_authorized": False,
        }

    def inspect_market_advisory_preflight_readonly(
        self, *, proposal_id: str, pair: str, source_digest: str,
        closed_1m_epoch: int, now_epoch: int,
    ) -> dict[str, Any]:
        """Exact original M05 source/TTL/owner ledger check, NEVER order consent.

        A real button PASS on a PUBLIC market advisory only acknowledged
        owner review. In this schema advisory rows carry ZERO price/quantity,
        and the immutable execution_allowed column is always 0. Read the
        same SQLite in mode=ro; do not change callback state or consume a
        second time. No Telegram network, crypto wallet or new M05 ledger.
        """
        result = {
            "stage": "M05_SOURCE_BOUND_ADVISORY_READONLY",
            "status": "M05_ADVISORY_NOT_VERIFIED",
            "source_bound_owner_review_pass": False,
            "advisory_only": True,
            "order_price_quantity_bound": False,
            "owner_order_execution_consent_verified": False,
            "final_decision_for_trading": None,
            "execution_allowed": False,
        }
        if (type(proposal_id) is not str or not PROPOSAL_ID.fullmatch(proposal_id)
                or type(pair) is not str or not PAIR.fullmatch(pair)
                or type(source_digest) is not str or not DIGEST.fullmatch(source_digest)
                or type(closed_1m_epoch) is not int or closed_1m_epoch <= 0
                or type(now_epoch) is not int or now_epoch <= 0):
            return dict(result, status="M05_ADVISORY_INPUT_INVALID")
        try:
            with sqlite3.connect(
                self.db_path.resolve().as_uri() + "?mode=ro",
                uri=True, timeout=2,
            ) as db:
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                config = db.execute(
                    "SELECT owner_user_id,mode FROM m05_config WHERE singleton=1"
                ).fetchone()
                row = db.execute(
                    """SELECT owner_user_id,pair,digest,closed_1m_epoch,
                              created_epoch,expires_epoch,mode_at_create,
                              state,reason,decision_epoch,execution_allowed,
                              entry_price_idr,quantity
                       FROM m05_proposals WHERE proposal_id=?""",
                    (proposal_id,),
                ).fetchone()
                outbound = db.execute(
                    """SELECT delivery_state,sent_message_id
                       FROM m05_market_advisory_outbox WHERE proposal_id=?""",
                    (proposal_id,),
                ).fetchone()
                # Even MANUAL→AUTO→MANUAL must not resurrect a previously
                # reviewed advisory after an owner-mode transition.
                mode_flipped = (
                    db.execute(
                        """SELECT 1 FROM m05_audit
                           WHERE event='OWNER_MODE_CHANGED'
                             AND event_epoch>=? AND event_epoch<=? LIMIT 1""",
                        (row[4],now_epoch),
                    ).fetchone() is not None
                    if row is not None and type(row[4]) is int
                    else True
                )
        except (OSError, sqlite3.Error, ValueError):
            return dict(result, status="M05_ADVISORY_LEDGER_UNAVAILABLE")
        if row is None or config is None:
            return dict(result, status="M05_ADVISORY_SOURCE_NOT_FOUND")
        (uid, row_pair, digest, minute, created, expires, original_mode,
         state, reason, decision, can_exec, price, quantity) = row
        if (config != (self.owner_user_id, "MANUAL")
                or uid != self.owner_user_id
                or (row_pair,digest,minute) !=
                   (pair,source_digest,closed_1m_epoch)
                or original_mode != "MANUAL"):
            return dict(result, status="M05_ADVISORY_SOURCE_OR_MODE_MISMATCH")
        if (outbound is None or outbound[0] != "SENT"
                or type(outbound[1]) is not int or outbound[1] <= 0
                or type(created) is not int or type(expires) is not int
                or not created <= now_epoch < expires
                or expires - created > TTL_MAX):
            return dict(result, status="M05_ADVISORY_EXPIRED_OR_UNDELIVERED")
        if mode_flipped:
            return dict(result, status="M05_ADVISORY_OWNER_MODE_TRANSITION")
        if can_exec != 0 or price != "0" or quantity != "0":
            return dict(result, status="M05_ADVISORY_SCHEMA_EXECUTION_CONFLICT")
        if (state == "PASS" and reason == "OWNER_BUTTON"
                and type(decision) is int and created <= decision < expires
                and decision <= now_epoch):
            return dict(result,
                        status="M05_OWNER_BUTTON_PASS_ADVISORY_ONLY",
                        source_bound_owner_review_pass=True)
        return dict(result, status="M05_NO_VALID_SOURCE_BOUND_PASS")

    def owner_review_snapshot(
        self, *, user_id: int, chat_id: int,
        now_epoch: int, chat_type: str = "private",
    ) -> dict[str, Any]:
        """Owner-private existing M05 facts, NOT trade/account/AI evidence.

        This intentionally reads only the original owner decision SQLite
        ledger. It does not call exchanges, generate a proposal or order,
        inspect foreign accounts, or promote public candidates to G1.
        """
        if not self._is_owner(user_id, chat_id, chat_type):
            return _blocked("OWNER_NOT_AUTHORIZED")
        if type(now_epoch) is not int or now_epoch <= 0:
            return _blocked("INVALID_M05_SNAPSHOT_CLOCK")
        with self._tx() as db:
            row = db.execute(
                """SELECT
                       COALESCE(SUM(o.delivery_state='QUEUED'
                                    AND o.expires_epoch>?
                                    AND p.state='PENDING'),0),
                       COALESCE(SUM(o.delivery_state='SENT'
                                    AND o.expires_epoch>?
                                    AND p.state='PENDING'),0),
                       COUNT(*)
                   FROM m05_market_advisory_outbox o
                   JOIN m05_proposals p ON p.proposal_id=o.proposal_id
                   WHERE o.pair!='reviewtest_idr'""",
                (now_epoch, now_epoch),
            ).fetchone()
            reviewed = db.execute(
                """SELECT
                       COALESCE(SUM(state='PASS'
                                    AND reason='OWNER_BUTTON'),0),
                       COALESCE(SUM(state='REJECT'
                                    AND reason='OWNER_BUTTON'),0)
                   FROM m05_proposals
                   WHERE pair!='reviewtest_idr'"""
            ).fetchone()
        return {
            "accepted": True,
            "fresh_queued_market_advisories": int(row[0]),
            "fresh_sent_market_advisories": int(row[1]),
            "market_advisories_recorded": int(row[2]),
            "owner_review_pass_count": int(reviewed[0]),
            "owner_review_reject_count": int(reviewed[1]),
            "market_source_authenticated": False,
            "exchange_fills_authenticated": False,
            "execution_allowed": False,
            "final_decision": None,
        }

    def switch_mode(self, *, user_id: int, chat_id: int, mode: str,
                    now_epoch: int, chat_type: str = "private") -> dict[str, Any]:
        if not self._is_owner(user_id, chat_id, chat_type):
            return _blocked("OWNER_NOT_AUTHORIZED")
        if mode not in MODES or type(now_epoch) is not int or now_epoch <= 0:
            return _blocked("INVALID_MODE_OR_TIME")
        with self._tx() as db:
            current = db.execute(
                "SELECT mode FROM m05_config WHERE singleton=1"
            ).fetchone()[0]
            if current != mode:
                closed = db.execute(
                    """UPDATE m05_proposals SET state='REJECT',
                           reason='MODE_CHANGED', decision_epoch=?
                       WHERE state='PENDING'""", (now_epoch,)
                ).rowcount
                # A MANUAL BUY/SELL preview cannot survive a MANUAL↔AUTO
                # round trip as a stale owner action. No order was sent.
                db.execute(
                    "UPDATE m05_manual_trade_previews "
                    "SET owner_choice='REJECT',decided_epoch=? "
                    "WHERE owner_choice IS NULL",
                    (now_epoch,),
                )
                db.execute(
                    "UPDATE m05_config SET mode=? WHERE singleton=1", (mode,))
                db.execute(
                    """INSERT INTO m05_audit
                       (event_epoch,owner_user_id,event,detail) VALUES (?,?,?,?)""",
                    (now_epoch, user_id, "OWNER_MODE_CHANGED",
                     f"{current}->{mode};cancelled={closed}"),
                )
        return {
            "accepted": True, "owner_mode": mode,
            "final_decision": None,
            "auto_order_authorized": False, "execution_allowed": False,
        }

    def queue_manual_trade_preview_offline(
            self, *, user_id: int, chat_id: int,
            account_fingerprint: str, draft, now_epoch: int,
            chat_type: str = "private") -> dict[str, Any]:
        """Original M07 draft → MANUAL owner Telegram preview, NO trade.

        A producer of real execution decisions MUST NOT reuse this method's
        rows as a source/fee/risk/owner order authorization. No exchange
        identity is asserted; source status and ownership are UNKNOWN.
        """
        from .m07_order_adapter import M07OrderDraft
        import json
        if not self._is_owner(user_id, chat_id, chat_type):
            return _blocked("OWNER_NOT_AUTHORIZED")
        if (type(draft) is not M07OrderDraft
                or draft.order_type != "LIMIT"
                or draft.side not in ("BUY", "SELL")
                or type(account_fingerprint) is not str
                or not DIGEST.fullmatch(account_fingerprint)
                or type(now_epoch) is not int or now_epoch <= 0):
            return _blocked("M05_MANUAL_TRADE_DRAFT_SCOPE_INVALID")
        try:
            params = draft.parameters()  # exact original V2 M07 adapter
        except ValueError:
            return _blocked("M05_MANUAL_TRADE_DRAFT_SCOPE_INVALID")
        digest = sha256(json.dumps(
            params, ensure_ascii=True, separators=(",", ":"),
        ).encode("ascii")).hexdigest()
        price, qty = str(draft.price), str(draft.quantity)
        preview_id, secret = secrets.token_hex(12), secrets.token_hex(16)
        action = "B" if draft.side == "BUY" else "S"
        confirm = f"m5t:{preview_id}:{action}:{secret}"
        reject = f"m5t:{preview_id}:R:{secret}"
        if max(len(confirm), len(reject)) > 64:
            return _blocked("M05_MANUAL_TRADE_CALLBACK_TOO_LONG")
        text = (
            "M05 MANUAL " + draft.side + " (SIMULASI TOMBOL, NO ORDER)\n" +
            draft.symbol + " | LIMIT harga IDR " + price +
            " | qty " + qty + "\n" +
            "ClientOrderID: " + draft.client_order_id + "\n" +
            "Akun, fee, G1, kepemilikan dan preflight BELUM TERVERIFIKASI.\n" +
            "Menekan tombol hanya mencatat pilihan owner. Tidak BUY/SELL."
        )
        with self._tx() as db:
            mode = db.execute(
                "SELECT mode FROM m05_config WHERE singleton=1"
            ).fetchone()[0]
            if mode != "MANUAL":
                return _blocked("M05_MANUAL_TRADE_MODE_REQUIRED")
            if db.execute(
                "SELECT 1 FROM m05_manual_trade_previews "
                "WHERE client_order_id=?",
                (draft.client_order_id,),
            ).fetchone():
                return _blocked("M05_MANUAL_TRADE_CLIENT_ID_ALREADY_USED")
            # Only one unexpired card can be in-flight; do not allow
            # multiple buttons to impersonate concurrent manual orders.
            if db.execute(
                "SELECT 1 FROM m05_manual_trade_previews "
                "WHERE expires_epoch>? AND owner_choice IS NULL "
                "AND send_state IN ('QUEUED','CLAIMED','SENT') LIMIT 1",
                (now_epoch,),
            ).fetchone():
                return _blocked("M05_MANUAL_TRADE_CARD_ALREADY_PENDING")
            db.execute(
                """INSERT INTO m05_manual_trade_previews(
                    preview_id,owner_user_id,account_fingerprint,
                    client_order_id,symbol,side,price_idr,quantity_base,
                    draft_sha256,created_epoch,expires_epoch,token_hash,
                    message_text,confirm_callback,reject_callback,
                    send_state,execution_allowed)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'QUEUED',0)""",
                (preview_id,self.owner_user_id,account_fingerprint,
                 draft.client_order_id,draft.symbol,draft.side,price,qty,
                 digest,now_epoch,now_epoch+TTL_MAX,
                 sha256(secret.encode()).hexdigest(),text,confirm,reject),
            )
            db.execute(
                "INSERT INTO m05_audit "
                "(event_epoch,owner_user_id,proposal_id,event,detail) "
                "VALUES (?,?,?,?,?)",
                (now_epoch,user_id,preview_id,"MANUAL_PREVIEW_QUEUED",
                 "DRAFT_ONLY_NO_EXCHANGE_AUTHORITY"),
            )
        return {
            "accepted": True, "stage": "M05_MANUAL_TRADE_BUTTON_PREVIEW",
            "preview_id": preview_id, "symbol": draft.symbol,
            "side": draft.side, "draft_sha256": digest,
            "owner_mode": "MANUAL",
            "source_attested": False,
            "owner_order_execution_authorized": False,
            "order_sent": False, "execution_allowed": False,
        }

    def inspect_manual_trade_press_for_sender_nonlive(
            self, *, preview_id: str, owner_user_id: int,
            account_fingerprint: str, draft, observed_epoch: int) -> dict:
        """Read existing M05 owner-button card, no execution authorization.

        Same original M05 owner ledger. A Telegram click is not a signed
        M07 POST receipt, spendable balance, account authentication or real
        owner Final. This read cannot mint such facts.
        """
        from .m07_order_adapter import M07OrderDraft
        import json
        if (type(preview_id) is not str
                or PROPOSAL_ID.fullmatch(preview_id) is None
                or type(owner_user_id) is not int
                or owner_user_id != self.owner_user_id
                or type(account_fingerprint) is not str
                or DIGEST.fullmatch(account_fingerprint) is None
                or type(draft) is not M07OrderDraft
                or draft.order_type != "LIMIT"
                or draft.side not in ("BUY", "SELL")
                or type(observed_epoch) is not int
                or observed_epoch <= 0):
            return _blocked("M05_MANUAL_SENDER_IDENTITY_INVALID")
        try:
            encoded = json.dumps(
                draft.parameters(), separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
            digest = sha256(encoded).hexdigest()
        except (ValueError, TypeError):
            return _blocked("M05_MANUAL_SENDER_DRAFT_INVALID")
        with self._tx() as db:
            mode = db.execute(
                "SELECT mode FROM m05_config WHERE singleton=1"
            ).fetchone()[0]
            row = db.execute(
                "SELECT owner_user_id,account_fingerprint,client_order_id,"
                "symbol,side,price_idr,quantity_base,draft_sha256,"
                "created_epoch,expires_epoch,send_state,sent_message_id,"
                "owner_choice,decided_epoch,execution_allowed "
                "FROM m05_manual_trade_previews WHERE preview_id=?",
                (preview_id,),
            ).fetchone()
            audit = db.execute(
                "SELECT COUNT(*) FROM m05_audit WHERE proposal_id=? "
                "AND owner_user_id=? AND event=? AND detail='NO_ORDER_NO_FINAL'",
                (preview_id, owner_user_id,
                 "MANUAL_PREVIEW_" + draft.side),
            ).fetchone()[0]
        matched = (
            mode == "MANUAL"
            and row is not None
            and row[:5] == (
                owner_user_id, account_fingerprint,
                draft.client_order_id, draft.symbol, draft.side)
            and row[5:8] == (
                str(draft.price), str(draft.quantity), digest)
            and type(row[8]) is int and type(row[9]) is int
            and row[8] <= row[13] < row[9]
            and row[13] <= observed_epoch < row[9]
            and row[10] == "SENT"
            and type(row[11]) is int and row[11] > 0
            and row[12] == draft.side
            and row[14] == 0
            and audit == 1
        )
        return {
            "stage": "M05_EXISTING_MANUAL_OWNER_PRESS_SOURCE_REVIEW",
            "status": (
                "M05_MANUAL_CLICK_RECORDED_PREVIEW_ONLY"
                if matched else "M05_MANUAL_PRESS_NOT_SOURCE_BOUND"),
            "preview_id": preview_id if matched else None,
            "draft_sha256": digest if matched else None,
            "owner_button_recorded": bool(matched),
            "owner_final_execution_authenticated": False,
            "telegram_https_origin_independently_attested_here": False,
            "account_spendable_idr_verified": False,
            "bot_owned_sellable_quantity_verified": False,
            "order_sent": False, "execution_allowed": False,
        }

    def claim_manual_trade_preview_delivery(
            self, *, now_epoch: int) -> dict[str, Any] | None:
        """One original M05 queue, no uncertain send retry."""
        if type(now_epoch) is not int or now_epoch <= 0:
            raise ValueError("M05_MANUAL_TRADE_CLAIM_TIME_INVALID")
        with self._tx() as db:
            if db.execute(
                "SELECT 1 FROM m05_manual_trade_previews "
                "WHERE send_state='CLAIMED' LIMIT 1"
            ).fetchone():
                raise ValueError("M05_MANUAL_TRADE_DELIVERY_UNCERTAIN")
            db.execute(
                "UPDATE m05_manual_trade_previews SET send_state='EXPIRED' "
                "WHERE send_state='QUEUED' AND expires_epoch<=?",
                (now_epoch,),
            )
            mode = db.execute(
                "SELECT mode FROM m05_config WHERE singleton=1"
            ).fetchone()[0]
            if mode != "MANUAL":
                return None
            row = db.execute(
                "SELECT preview_id,message_text,confirm_callback,"
                "reject_callback,side FROM m05_manual_trade_previews "
                "WHERE send_state='QUEUED' AND owner_choice IS NULL "
                "AND expires_epoch>? ORDER BY created_epoch,preview_id LIMIT 1",
                (now_epoch,),
            ).fetchone()
            if row is None:
                return None
            if db.execute(
                "UPDATE m05_manual_trade_previews SET send_state='CLAIMED' "
                "WHERE preview_id=? AND send_state='QUEUED'",
                (row[0],),
            ).rowcount != 1:
                raise ValueError("M05_MANUAL_TRADE_CLAIM_CONFLICT")
        return {
            "preview_id": row[0], "message_text": row[1],
            "confirm_callback": row[2], "reject_callback": row[3],
            "side": row[4],
        }

    def confirm_manual_trade_preview_delivery(
            self, *, preview_id: str, message_id: int) -> None:
        if (type(preview_id) is not str
                or not PROPOSAL_ID.fullmatch(preview_id)
                or type(message_id) is not int or message_id <= 0):
            raise ValueError("M05_MANUAL_TRADE_DELIVERY_ACK_INVALID")
        with self._tx() as db:
            if db.execute(
                "UPDATE m05_manual_trade_previews "
                "SET send_state='SENT',sent_message_id=? "
                "WHERE preview_id=? AND send_state='CLAIMED'",
                (message_id,preview_id),
            ).rowcount != 1:
                raise ValueError("M05_MANUAL_TRADE_DELIVERY_ACK_CONFLICT")

    def apply_manual_trade_preview_callback(
            self, *, user_id: int, chat_id: int, callback_data: str,
            now_epoch: int, telegram_message_id: int | None,
            chat_type: str = "private") -> dict[str, Any]:
        """One real bot-envelope-bound owner press, PREVIEW ONLY, NO Final.

        Caller must verify Telegram Bot API envelope and private bot message
        before calling this method. Never turn a raw caller's user_id into
        evidence of authenticated Telegram or actual order permission.
        """
        if not self._is_owner(user_id, chat_id, chat_type):
            return _blocked("OWNER_NOT_AUTHORIZED")
        match = (MANUAL_TRADE_CALLBACK.fullmatch(callback_data)
                 if isinstance(callback_data, str) else None)
        if (match is None or type(now_epoch) is not int or now_epoch <= 0
                or type(telegram_message_id) is not int
                or telegram_message_id <= 0):
            return _blocked("M05_MANUAL_TRADE_CALLBACK_INVALID")
        preview_id, letter, secret = match.groups()
        with self._tx() as db:
            row = db.execute(
                "SELECT owner_user_id,token_hash,side,expires_epoch,"
                "send_state,sent_message_id,owner_choice "
                "FROM m05_manual_trade_previews WHERE preview_id=?",
                (preview_id,),
            ).fetchone()
            if (row is None or row[0] != user_id
                    or not hmac.compare_digest(
                        row[1],sha256(secret.encode()).hexdigest()
                    )):
                return _blocked("M05_MANUAL_TRADE_CALLBACK_FORGED")
            if (row[4] != "SENT" or row[5] != telegram_message_id):
                return _blocked("M05_MANUAL_TRADE_MESSAGE_NOT_DELIVERED")
            if row[6] is not None:
                return _blocked("M05_MANUAL_TRADE_CALLBACK_ALREADY_USED")
            if (letter not in ("R", "B" if row[2] == "BUY" else "S")):
                return _blocked("M05_MANUAL_TRADE_SIDE_MISMATCH")
            mode = db.execute(
                "SELECT mode FROM m05_config WHERE singleton=1"
            ).fetchone()[0]
            if now_epoch >= row[3] or mode != "MANUAL":
                return _blocked("M05_MANUAL_TRADE_EXPIRED_OR_AUTO_MODE")
            choice = "REJECT" if letter == "R" else row[2]
            if db.execute(
                "UPDATE m05_manual_trade_previews SET owner_choice=?,"
                "decided_epoch=? WHERE preview_id=? AND owner_choice IS NULL",
                (choice,now_epoch,preview_id),
            ).rowcount != 1:
                return _blocked("M05_MANUAL_TRADE_CALLBACK_ALREADY_USED")
            db.execute(
                "INSERT INTO m05_audit "
                "(event_epoch,owner_user_id,proposal_id,event,detail) "
                "VALUES (?,?,?,?,?)",
                (now_epoch,user_id,preview_id,
                 "MANUAL_PREVIEW_" + choice,"NO_ORDER_NO_FINAL"),
            )
        return {
            "accepted": True,
            "manual_trade_preview": True,
            "preview_id": preview_id,
            "owner_decision": choice,
            "final_decision": None,
            "telegram_preview_press_recorded": True,
            "actual_trade_click_authorized": False,
            "order_sent": False, "execution_allowed": False,
        }

    def issue_offline_shadow_proposal(
        self, *, pair: str, digest: str, closed_1m_epoch: int,
        entry_price_idr: str, quantity: str, now_epoch: int,
        source_expires_epoch: int,
    ) -> dict[str, Any]:
        """Synthetic UI rehearsal only; NEVER called by live market admission.

        A future G1/M06/M07 producer must use a different verified adapter.
        This method cannot set execution_allowed and never returns an intent.
        """
        if (not isinstance(pair, str) or not PAIR.fullmatch(pair)
                or not isinstance(digest, str) or not DIGEST.fullmatch(digest)
                or type(closed_1m_epoch) is not int or closed_1m_epoch <= 0
                or type(now_epoch) is not int or now_epoch <= 0
                or type(source_expires_epoch) is not int
                or not closed_1m_epoch <= now_epoch < source_expires_epoch):
            return _blocked("INVALID_SHADOW_SOURCE_OR_TTL")
        try:
            entry, qty = _decimal_positive(entry_price_idr), _decimal_positive(quantity)
        except ValueError:
            return _blocked("INVALID_PRICE_OR_QUANTITY")
        expires = min(now_epoch + TTL_MAX, source_expires_epoch)
        proposal = secrets.token_hex(12)
        secret = secrets.token_hex(16)
        with self._tx() as db:
            mode = db.execute(
                "SELECT mode FROM m05_config WHERE singleton=1"
            ).fetchone()[0]
            if mode != "MANUAL":
                return _blocked("AUTO_NO_VERIFIED_PROPOSAL_ENGINE")
            db.execute(
                """INSERT INTO m05_proposals(
                   proposal_id,owner_user_id,pair,digest,closed_1m_epoch,
                   entry_price_idr,quantity,created_epoch,expires_epoch,
                   token_hash,mode_at_create,state,execution_allowed)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,'PENDING',0)""",
                (proposal, self.owner_user_id, pair, digest, closed_1m_epoch,
                 entry, qty, now_epoch, expires, sha256(secret.encode()).hexdigest(),
                 mode),
            )
            db.execute(
                """INSERT INTO m05_audit(event_epoch,owner_user_id,proposal_id,event,detail)
                   VALUES (?,?,?,?,?)""",
                (now_epoch, self.owner_user_id, proposal,
                 "SHADOW_PROPOSAL_CREATED", "NOT_EXCHANGE_AUTHENTICATED"),
            )
        return {
            "accepted": True, "proposal_id": proposal, "pair": pair,
            "source_digest_sha256": digest, "expires_epoch": expires,
            "owner_mode": "MANUAL", "shadow_only": True,
            "callbacks": {
                "PASS": f"m5:{proposal}:P:{secret}",
                "REJECT": f"m5:{proposal}:R:{secret}",
            },
            "final_decision": None, "execution_allowed": False,
        }

    def issue_market_advisory_proposal(
        self, *, pair: str, digest: str, closed_1m_epoch: int,
        now_epoch: int, source_expires_epoch: int, setup: str,
        market_status: str, risk_status: str,
    ) -> dict[str, Any]:
        """Original public M03/M04 observation -> owner MANUAL review, NO order.

        Zero price and quantity mean NO executable order was constructed.
        Only current, source-fingerprinted closed-minute observations are
        eligible for an inline-button review. This is NOT G1 authentication.
        """
        if (not isinstance(pair, str) or not PAIR.fullmatch(pair)
                or pair in ("usdt_idr", "usdc_idr", "floki_idr", "pepe_idr", "reviewtest_idr")
                or not isinstance(digest, str) or not DIGEST.fullmatch(digest)
                or type(closed_1m_epoch) is not int or closed_1m_epoch <= 0
                or closed_1m_epoch % 60 != 0
                or type(now_epoch) is not int or now_epoch <= 0
                or type(source_expires_epoch) is not int
                or not closed_1m_epoch <= now_epoch < source_expires_epoch
                or source_expires_epoch > closed_1m_epoch + TTL_MAX):
            return _blocked("INVALID_MARKET_ADVISORY_SOURCE_OR_TTL")
        labels = (setup, market_status, risk_status)
        if any(not isinstance(v, str) or not 1 <= len(v) <= 100
               or not v.isascii() or any(ch not in
                   "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_:-."
                   for ch in v) for v in labels):
            return _blocked("INVALID_MARKET_ADVISORY_LABEL")
        expires = min(now_epoch + TTL_MAX, source_expires_epoch)
        proposal_id = secrets.token_hex(12)
        secret = secrets.token_hex(16)
        pass_cb = f"m5:{proposal_id}:P:{secret}"
        reject_cb = f"m5:{proposal_id}:R:{secret}"
        message = (
            "Indodax V2 | M03/M04 public advisory\\n"
            "Pair: " + pair.upper() + "\\n"
            "Closed 1m (epoch): " + str(closed_1m_epoch) + "\\n"
            "Setup M03: " + setup + "\\n"
            "Analisis M04: " + market_status + "\\n"
            "RISK: " + risk_status + "\\n"
            "Fingerprint: " + digest[:16] + "\\n"
            "G1/account/order: UNVERIFIED. Harga dan jumlah order belum "
            "diotorisasi. PASS/REJECT hanya keputusan review owner; "
            "TIDAK mengirim BUY atau SELL."
        )
        with self._tx() as db:
            mode = db.execute(
                "SELECT mode FROM m05_config WHERE singleton=1"
            ).fetchone()[0]
            if mode != "MANUAL":
                return _blocked("M05_MANUAL_MODE_REQUIRED")
            if db.execute(
                """SELECT 1 FROM m05_market_advisory_outbox
                   WHERE pair=? AND closed_1m_epoch=?""",
                (pair, closed_1m_epoch),
            ).fetchone():
                return _blocked("M05_ADVISORY_ALREADY_QUEUED_THIS_CANDLE")
            db.execute(
                """INSERT INTO m05_proposals(
                   proposal_id,owner_user_id,pair,digest,closed_1m_epoch,
                   entry_price_idr,quantity,created_epoch,expires_epoch,
                   token_hash,mode_at_create,state,execution_allowed)
                   VALUES (?,?,?,?,?,'0','0',?,?,?,'MANUAL','PENDING',0)""",
                (proposal_id, self.owner_user_id, pair, digest,
                 closed_1m_epoch, now_epoch, expires,
                 sha256(secret.encode()).hexdigest()),
            )
            db.execute(
                """INSERT INTO m05_market_advisory_outbox(
                   proposal_id,pair,closed_1m_epoch,expires_epoch,
                   message_text,pass_callback,reject_callback,delivery_state)
                   VALUES (?,?,?,?,?,?,?,'QUEUED')""",
                (proposal_id, pair, closed_1m_epoch, expires,
                 message, pass_cb, reject_cb),
            )
            db.execute(
                """INSERT INTO m05_audit
                   (event_epoch,owner_user_id,proposal_id,event,detail)
                   VALUES (?,?,?,?,?)""",
                (now_epoch, self.owner_user_id, proposal_id,
                 "MARKET_ADVISORY_QUEUED", "NO_PRICE_NO_QUANTITY_NO_ORDER"),
            )
        return {
            "accepted": True, "proposal_id": proposal_id, "pair": pair,
            "source_digest_sha256": digest, "expires_epoch": expires,
            "outbox_delivery": "QUEUED", "owner_mode": "MANUAL",
            "shadow_only": True, "final_decision": None,
            "execution_allowed": False,
        }

    def queue_owner_review_button_test(
        self, *, user_id: int, chat_id: int, now_epoch: int,
        chat_type: str = "private",
    ) -> dict[str, Any]:
        """Explicit owner-only transport rehearsal via SAME original outbox.

        This is NOT an M03 candidate, M04 AI result, G1 receipt, order,
        or authorization. A synthetic reserved pair NEVER enters real scans.
        Owner invokes the command himself in his private dedicated chat.
        """
        if not self._is_owner(user_id, chat_id, chat_type):
            return _blocked("OWNER_NOT_AUTHORIZED")
        if type(now_epoch) is not int or now_epoch <= 0:
            return _blocked("INVALID_M05_TEST_CLOCK")
        pair = "reviewtest_idr"
        candle = now_epoch - (now_epoch % 60)
        expires = now_epoch + TTL_MAX
        proposal_id = secrets.token_hex(12)
        secret = secrets.token_hex(16)
        digest = sha256(
            ("M05_OWNER_BUTTON_TRANSPORT_TEST:" + proposal_id).encode()
        ).hexdigest()
        msg = (
            "M05 BUTTON TEST | SIMULASI, BUKAN SINYAL PASAR\n"
            "Hanya uji komunikasi Telegram owner PASS/REJECT.\n"
            "Tidak ada pair pasar, G1, AI berbayar, saldo, harga, "
            "jumlah maupun order.\n"
            "Tombol berlaku maksimal 90 detik dari perintah.\n"
            "PASS dan REJECT hanya menyimpan hasil uji di SQLite V2. "
            "BUY/SELL TIDAK AKAN DIKIRIM. Trading OFF."
        )
        with self._tx() as db:
            mode = db.execute(
                "SELECT mode FROM m05_config WHERE singleton=1"
            ).fetchone()[0]
            if mode != "MANUAL":
                return _blocked("M05_TEST_REQUIRES_MANUAL_MODE")
            if db.execute(
                """SELECT 1
                   FROM m05_market_advisory_outbox o
                   JOIN m05_proposals p ON p.proposal_id=o.proposal_id
                   WHERE o.pair=? AND p.state='PENDING'
                     AND o.expires_epoch>?""",
                (pair, now_epoch),
            ).fetchone():
                return _blocked("M05_TEST_ALREADY_PENDING")
            if db.execute(
                """SELECT 1 FROM m05_market_advisory_outbox
                   WHERE pair=? AND closed_1m_epoch=?""",
                (pair, candle),
            ).fetchone():
                return _blocked("M05_TEST_ONCE_PER_MINUTE")
            db.execute(
                """INSERT INTO m05_proposals(
                   proposal_id,owner_user_id,pair,digest,closed_1m_epoch,
                   entry_price_idr,quantity,created_epoch,expires_epoch,
                   token_hash,mode_at_create,state,execution_allowed)
                   VALUES (?,?,?,?,?,'0','0',?,?,?,'MANUAL','PENDING',0)""",
                (proposal_id, self.owner_user_id, pair, digest, candle,
                 now_epoch, expires, sha256(secret.encode()).hexdigest()),
            )
            db.execute(
                """INSERT INTO m05_market_advisory_outbox(
                   proposal_id,pair,closed_1m_epoch,expires_epoch,
                   message_text,pass_callback,reject_callback,delivery_state)
                   VALUES (?,?,?,?,?,?,?,'QUEUED')""",
                (proposal_id, pair, candle, expires, msg,
                 f"m5:{proposal_id}:P:{secret}",
                 f"m5:{proposal_id}:R:{secret}"),
            )
            db.execute(
                """INSERT INTO m05_audit(
                   event_epoch,owner_user_id,proposal_id,event,detail)
                   VALUES (?,?,?,?,?)""",
                (now_epoch, user_id, proposal_id,
                 "OWNER_BUTTON_TRANSPORT_TEST_QUEUED",
                 "SYNTHETIC_NO_MARKET_NO_ORDER"),
            )
        return {
            "accepted": True, "reason": "M05_OWNER_BUTTON_TEST_QUEUED",
            "shadow_only": True, "expires_epoch": expires,
            "final_decision": None, "execution_allowed": False,
        }

    def claim_market_advisory_delivery(self, *, now_epoch: int) -> dict[str, Any] | None:
        """At most one owner-private message, claimed BEFORE network send.

        A crashed or uncertain send is NEVER automatically retried.
        """
        if type(now_epoch) is not int or now_epoch <= 0:
            raise ValueError("M05_ADVISORY_INVALID_CLOCK")
        with self._tx() as db:
            if db.execute(
                """SELECT 1 FROM m05_market_advisory_outbox
                   WHERE delivery_state='CLAIMED' LIMIT 1"""
            ).fetchone():
                raise ValueError("M05_ADVISORY_INCOMPLETE_DELIVERY_REVIEW")
            # Expire unsent review-only items atomically in BOTH existing
            # M05 tables, so /status cannot report old PENDING proposals.
            # This is expiry metadata, NOT a human Final REJECT.
            db.execute(
                """UPDATE m05_proposals
                   SET state='REJECT',reason='EXPIRED_UNSENT',
                       decision_epoch=?
                   WHERE state='PENDING' AND proposal_id IN (
                       SELECT proposal_id FROM m05_market_advisory_outbox
                       WHERE delivery_state='QUEUED' AND expires_epoch<=?
                   )""",
                (now_epoch, now_epoch),
            )
            db.execute(
                """UPDATE m05_market_advisory_outbox SET delivery_state='EXPIRED'
                   WHERE delivery_state='QUEUED' AND expires_epoch<=?""",
                (now_epoch,),
            )
            mode = db.execute(
                "SELECT mode FROM m05_config WHERE singleton=1"
            ).fetchone()[0]
            if mode != "MANUAL":
                return None
            row = db.execute(
                """SELECT o.proposal_id,o.message_text,o.pass_callback,
                          o.reject_callback,o.expires_epoch
                   FROM m05_market_advisory_outbox o
                   JOIN m05_proposals p ON p.proposal_id=o.proposal_id
                   WHERE o.delivery_state='QUEUED' AND p.state='PENDING'
                     AND o.expires_epoch>? AND p.mode_at_create='MANUAL'
                   ORDER BY p.created_epoch,o.proposal_id LIMIT 1""",
                (now_epoch,),
            ).fetchone()
            if row is None:
                return None
            if db.execute(
                """UPDATE m05_market_advisory_outbox
                   SET delivery_state='CLAIMED'
                   WHERE proposal_id=? AND delivery_state='QUEUED'""",
                (row[0],),
            ).rowcount != 1:
                raise ValueError("M05_ADVISORY_CLAIM_CONFLICT")
        return {
            "proposal_id": row[0], "message_text": row[1],
            "pass_callback": row[2], "reject_callback": row[3],
            "expires_epoch": row[4],
        }

    def confirm_market_advisory_delivery(
        self, *, proposal_id: str, message_id: int,
    ) -> None:
        if (not isinstance(proposal_id, str)
                or not PROPOSAL_ID.fullmatch(proposal_id)
                or type(message_id) is not int or message_id <= 0):
            raise ValueError("M05_ADVISORY_INVALID_DELIVERY_ACK")
        with self._tx() as db:
            if db.execute(
                """UPDATE m05_market_advisory_outbox
                   SET delivery_state='SENT',sent_message_id=?
                   WHERE proposal_id=? AND delivery_state='CLAIMED'""",
                (message_id, proposal_id),
            ).rowcount != 1:
                raise ValueError("M05_ADVISORY_DELIVERY_ACK_CONFLICT")

    def market_advisory_delivery_status(self, proposal_id: str) -> str | None:
        if not isinstance(proposal_id, str) or not PROPOSAL_ID.fullmatch(proposal_id):
            raise ValueError("M05_ADVISORY_INVALID_ID")
        with self._tx() as db:
            row = db.execute(
                """SELECT delivery_state FROM m05_market_advisory_outbox
                   WHERE proposal_id=?""", (proposal_id,),
            ).fetchone()
        return row[0] if row else None

    def apply_callback(self, *, user_id: int, chat_id: int, callback_data: str,
                       now_epoch: int, chat_type: str = "private",
                       telegram_message_id: int | None = None) -> dict[str, Any]:
        """Requires outer Telegram adapter authentication, not raw user JSON."""
        if not self._is_owner(user_id, chat_id, chat_type):
            return _blocked("OWNER_NOT_AUTHORIZED")
        match = CALLBACK.fullmatch(callback_data) if isinstance(callback_data, str) else None
        if match is None or type(now_epoch) is not int or now_epoch <= 0:
            return _blocked("INVALID_CALLBACK")
        proposal, answer, secret = match.groups()
        choice = "PASS" if answer == "P" else "REJECT"
        with self._tx() as db:
            row = db.execute(
                """SELECT owner_user_id, token_hash, expires_epoch, state
                   FROM m05_proposals WHERE proposal_id=?""", (proposal,)
            ).fetchone()
            if row is None or row[0] != user_id or not hmac.compare_digest(
                    row[1], sha256(secret.encode()).hexdigest()):
                return _blocked("UNKNOWN_OR_FORGED_CALLBACK")
            if row[3] != "PENDING":
                return _blocked("CALLBACK_ALREADY_CONSUMED")
            # Market-advisory callbacks only become actionable as OWNER
            # review after the exact private Telegram message is acknowledged.
            # Legacy synthetic shadow proposal rows have no outbox entry.
            market_delivery = db.execute(
                """SELECT delivery_state,sent_message_id
                   FROM m05_market_advisory_outbox WHERE proposal_id=?""",
                (proposal,),
            ).fetchone()
            if market_delivery is not None:
                if market_delivery[0] != "SENT":
                    return _blocked("M05_ADVISORY_NOT_SENT_TO_OWNER")
                # The real Telegram transport binds the pressed button to
                # the exact message that M05 itself sent and acknowledged.
                # Legacy offline direct callers omit the transport message.
                if telegram_message_id is not None and (
                    type(telegram_message_id) is not int
                    or telegram_message_id != market_delivery[1]
                ):
                    return _blocked("M05_ADVISORY_MESSAGE_ID_MISMATCH")
            mode = db.execute(
                "SELECT mode FROM m05_config WHERE singleton=1"
            ).fetchone()[0]
            if now_epoch >= row[2] or mode != "MANUAL":
                db.execute(
                    """UPDATE m05_proposals SET state='REJECT', reason=?,
                       decision_epoch=? WHERE proposal_id=? AND state='PENDING'""",
                    ("EXPIRED" if now_epoch >= row[2] else "MODE_NOT_MANUAL",
                     now_epoch, proposal),
                )
                db.execute(
                    """INSERT INTO m05_audit
                       (event_epoch,owner_user_id,proposal_id,event,detail)
                       VALUES (?,?,?,?,?)""",
                    (now_epoch, user_id, proposal, "SHADOW_REJECT",
                     "EXPIRED_OR_MODE_CHANGED"),
                )
                return _blocked("EXPIRED_OR_MODE_CHANGED")
            changed = db.execute(
                """UPDATE m05_proposals SET state=?, reason='OWNER_BUTTON',
                   decision_epoch=? WHERE proposal_id=? AND state='PENDING'""",
                (choice, now_epoch, proposal),
            ).rowcount
            if changed != 1:
                return _blocked("CALLBACK_ALREADY_CONSUMED")
            db.execute(
                """INSERT INTO m05_audit
                   (event_epoch,owner_user_id,proposal_id,event,detail)
                   VALUES (?,?,?,?,?)""",
                (now_epoch, user_id, proposal, "OWNER_SHADOW_"+choice,
                 "CONSENT_ONLY_NO_ORDER"),
            )
        return {
            "accepted": True, "proposal_id": proposal,
            "owner_decision": choice, "final_decision": choice,
            "reason": "SHADOW_CONSENT_RECORDED_NO_EXECUTOR",
            "execution_allowed": False,
        }

    def claim_finished_review_button_notice(
        self, *, user_id: int, chat_id: int, callback_data: str,
        telegram_message_id: int, now_epoch: int,
        chat_type: str = "private",
    ) -> dict[str, Any]:
        """One safe cleanup notice for an already-final Telegram review card.

        Stale/expired callback cannot change Final. This verifies the
        *exact* original owner/private bot-delivered message, proposal token
        and durable final state before allowing one UI cleanup attempt.
        Called after an authenticated original owner decision or an invalid
        expired/replayed click, never before applying the owner ledger.
        """
        if not self._is_owner(user_id, chat_id, chat_type):
            return _blocked("OWNER_NOT_AUTHORIZED")
        match = CALLBACK.fullmatch(callback_data) if isinstance(callback_data, str) else None
        if (match is None or type(telegram_message_id) is not int
                or telegram_message_id <= 0
                or type(now_epoch) is not int or now_epoch <= 0):
            return _blocked("INVALID_CALLBACK_UI_NOTICE")
        proposal, _answer, secret = match.groups()
        with self._tx() as db:
            row = db.execute(
                """SELECT p.owner_user_id,p.token_hash,p.state,p.reason,
                          o.delivery_state,o.sent_message_id
                   FROM m05_proposals p
                   JOIN m05_market_advisory_outbox o
                     ON o.proposal_id=p.proposal_id
                   WHERE p.proposal_id=?""",
                (proposal,),
            ).fetchone()
            if (row is None or row[0] != user_id
                    or not hmac.compare_digest(
                        row[1], sha256(secret.encode()).hexdigest()
                    )
                    or row[4] != "SENT"
                    or row[5] != telegram_message_id
                    or row[2] not in ("PASS", "REJECT")):
                return _blocked("M05_UI_NOTICE_NOT_AUTHORIZED")
            prior = db.execute(
                """SELECT 1 FROM m05_audit
                   WHERE proposal_id=?
                     AND event='OWNER_REVIEW_UI_NOTICE_ATTEMPTED'
                   LIMIT 1""",
                (proposal,),
            ).fetchone()
            if prior:
                return _blocked("M05_UI_NOTICE_ALREADY_ATTEMPTED")
            db.execute(
                """INSERT INTO m05_audit
                   (event_epoch,owner_user_id,proposal_id,event,detail)
                   VALUES (?,?,?,?,?)""",
                (now_epoch, user_id, proposal,
                 "OWNER_REVIEW_UI_NOTICE_ATTEMPTED",
                 "EXPIRED_OR_CONSUMED_NO_REPLAY"),
            )
            state, reason = row[2], row[3]
        label = (
            "EXPIRED" if reason == "EXPIRED" or reason == "EXPIRED_UNSENT"
            else "MODE_CHANGED" if reason == "MODE_NOT_MANUAL"
            else state if reason == "OWNER_BUTTON"
            else "REVIEW_CLOSED"
        )
        return {
            "accepted": True, "ui_notice_label": label,
            "final_decision": None, "execution_allowed": False,
            "ui_only": True, "safe_to_remove_original_buttons": True,
        }

    def record_orchestrator_reject(self, *, trace_id: str, reason: str,
                                   now_epoch: int) -> None:
        """Attached to the ONE current V2 mainpath: no proposal creation."""
        if (not isinstance(trace_id, str) or not trace_id.strip()
                or len(trace_id) > 180 or not isinstance(reason, str)
                or not reason.strip() or len(reason) > 240
                or type(now_epoch) is not int or now_epoch <= 0):
            raise ValueError("INVALID_M05_MAINPATH_REJECT")
        with self._tx() as db:
            db.execute(
                """INSERT OR IGNORE INTO m05_cycle_observations
                   VALUES (?,?,?,'REJECT',0)""",
                (trace_id, reason, now_epoch),
            )
            found = db.execute(
                "SELECT reason FROM m05_cycle_observations WHERE trace_id=?",
                (trace_id,),
            ).fetchone()
            if found is None or found[0] != reason:
                raise ValueError("M05_MAINPATH_TRACE_REPLAY_CONFLICT")

    def audit_count(self) -> int:
        with self._tx() as db:
            return db.execute("SELECT COUNT(*) FROM m05_audit").fetchone()[0]

    def observed_cycle(self, trace_id: str) -> dict[str, Any] | None:
        with self._tx() as db:
            row = db.execute(
                """SELECT reason,final_decision,execution_allowed
                   FROM m05_cycle_observations WHERE trace_id=?""", (trace_id,)
            ).fetchone()
        return ({"reason": row[0], "final_decision": row[1],
                 "execution_allowed": bool(row[2])} if row else None)

    def telegram_next_offset(self) -> int:
        """Bot API getUpdates cursor; only durable processed updates advance it."""
        with self._tx() as db:
            row = db.execute(
                "SELECT next_offset FROM m05_telegram_cursor WHERE singleton=1"
            ).fetchone()
        if row is None:
            raise ValueError("M05_TELEGRAM_CURSOR_UNAVAILABLE")
        return row[0]

    def claim_telegram_update(self, *, update_id: int,
                              callback_query_id: str | None,
                              now_epoch: int) -> str:
        """Atomically reserve one Telegram update, without storing credentials.

        A crash after CLAIMED intentionally blocks future polls for operator
        review rather than rerunning potentially side-effectful owner commands.
        """
        if (type(update_id) is not int or update_id < 0
                or type(now_epoch) is not int or now_epoch <= 0
                or (callback_query_id is not None and (
                    not isinstance(callback_query_id, str)
                    or not 1 <= len(callback_query_id) <= 128
                    or not callback_query_id.isascii()))):
            raise ValueError("INVALID_TELEGRAM_UPDATE_ID")
        with self._tx() as db:
            cursor = db.execute(
                "SELECT next_offset FROM m05_telegram_cursor WHERE singleton=1"
            ).fetchone()[0]
            if update_id < cursor:
                return "ALREADY_ACKNOWLEDGED"
            previous = db.execute(
                "SELECT status FROM m05_telegram_updates WHERE update_id=?",
                (update_id,),
            ).fetchone()
            if previous:
                return ("ALREADY_ACKNOWLEDGED" if previous[0] == "DONE"
                        else "INCOMPLETE_REVIEW_REQUIRED")
            if db.execute(
                "SELECT 1 FROM m05_telegram_updates WHERE status='CLAIMED' LIMIT 1"
            ).fetchone():
                return "INCOMPLETE_REVIEW_REQUIRED"
            if callback_query_id is not None and db.execute(
                "SELECT 1 FROM m05_telegram_updates WHERE callback_query_id=?",
                (callback_query_id,),
            ).fetchone():
                db.execute(
                    """INSERT INTO m05_telegram_updates
                       VALUES (?,NULL,'DONE','DUPLICATE_CALLBACK',?)""",
                    (update_id, now_epoch),
                )
                db.execute(
                    """UPDATE m05_telegram_cursor SET next_offset=?
                       WHERE singleton=1 AND next_offset<?""",
                    (update_id + 1, update_id + 1),
                )
                return "DUPLICATE_CALLBACK"
            db.execute(
                """INSERT INTO m05_telegram_updates
                   VALUES (?,?,'CLAIMED',NULL,?)""",
                (update_id, callback_query_id, now_epoch),
            )
            return "CLAIMED"

    def complete_telegram_update(self, *, update_id: int,
                                 outcome: str) -> None:
        """Mark a safely handled update before acknowledging it to Telegram."""
        if (type(update_id) is not int or update_id < 0
                or not isinstance(outcome, str)
                or not 1 <= len(outcome) <= 100
                or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
                       for ch in outcome)):
            raise ValueError("INVALID_TELEGRAM_UPDATE_COMPLETION")
        with self._tx() as db:
            changed = db.execute(
                """UPDATE m05_telegram_updates SET status='DONE',outcome=?
                   WHERE update_id=? AND status='CLAIMED'""",
                (outcome, update_id),
            ).rowcount
            if changed != 1:
                raise ValueError("TELEGRAM_UPDATE_NOT_CLAIMED")
            db.execute(
                """UPDATE m05_telegram_cursor SET next_offset=?
                   WHERE singleton=1 AND next_offset<?""",
                (update_id + 1, update_id + 1),
            )

    def telegram_update_status(self, update_id: int) -> str | None:
        with self._tx() as db:
            row = db.execute(
                "SELECT status FROM m05_telegram_updates WHERE update_id=?",
                (update_id,),
            ).fetchone()
        return row[0] if row else None
