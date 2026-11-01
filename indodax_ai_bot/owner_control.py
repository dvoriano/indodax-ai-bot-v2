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
                db.execute("UPDATE m05_config SET mode=? WHERE singleton=1", (mode,))
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

    def apply_callback(self, *, user_id: int, chat_id: int, callback_data: str,
                       now_epoch: int, chat_type: str = "private") -> dict[str, Any]:
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
