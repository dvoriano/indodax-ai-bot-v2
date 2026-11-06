"""M10-06 opt-in owner-only operational alert, in the EXISTING M05 receiver.

No worker, order, account access, service mutation or paid AI. Claim BEFORE
Telegram send so uncertain responses are never replayed after restart.
"""
from __future__ import annotations

from hashlib import sha256
import json
import sqlite3
import time

from .m10_ops_health_readonly import (
    assess_v2_operations_readonly, format_v2_ops_owner_message,
)

_MIN_SPACING_SECONDS = 3600
_ALLOWED_CODES = frozenset((
    "BTC_WS_BATCH_INCOMPLETE_OR_INCONSISTENT", "BTC_WS_BATCH_REJECTED",
    "BTC_WS_BATCH_STALE", "BTC_WS_BATCH_FUTURE_CLOCK",
    "BTC_WS_BATCH_STATUS_UNAVAILABLE", "BTC_WS_BATCH_FIELDS_UNTRUSTED",
    "BTC_WS_REJECT_RESTART_FLAG_CONFLICT", "BTC_WS_UNIT_NOT_ACTIVE",
    "BTC_WS_UNIT_STATUS_UNVERIFIED", "M05_TIMER_NOT_ACTIVE",
    "M05_TIMER_STATUS_UNVERIFIED", "M03_TIMER_NOT_CONFIRMED_DISABLED",
    "M03_TIMER_STATUS_UNVERIFIED",
))


def send_one_owner_ops_alert_nonlive(*, api, ledger, report=None,
                                     now_epoch=None):
    """Durably at-most-once alert attempt per hour, no automatic retry.

    Only call under the already-held dedicated M05 poller process lock.
    Never turn alert snapshot into a release/GO signal.
    """
    now = int(time.time()) if now_epoch is None else now_epoch
    if type(now) is not int or now <= 0:
        raise ValueError("M10_ALERT_INVALID_CLOCK")
    snap = assess_v2_operations_readonly() if report is None else report
    if (not isinstance(snap, dict)
            or snap.get("status") != "V2_OPERATIONS_READONLY_OBSERVATION"
            or snap.get("scope") !=
                "ONE_V2_ORIGINAL_WS_SIDECAR_AND_FIXED_USER_UNITS"
            or snap.get("live_trading_authorized") is not False
            or snap.get("m10_06_full_acceptance") is not False):
        return {"status": "M10_ALERT_UNTRUSTED_SNAPSHOT", "messages_sent": 0}
    codes = snap.get("alert_codes")
    if (not isinstance(codes, list) or len(codes) > 32
            or any(type(c) is not str or c not in _ALLOWED_CODES
                   for c in codes)):
        return {"status": "M10_ALERT_UNTRUSTED_CODES", "messages_sent": 0}
    if not codes:
        return {"status": "M10_ALERT_NO_WARNINGS", "messages_sent": 0}
    batch = snap.get("btc_ws_last_batch")
    batch = batch if isinstance(batch, dict) else {}
    epoch = batch.get("last_completed_batch_epoch")
    if type(epoch) is not int or not 0 <= epoch <= now:
        epoch = 0
    digest = sha256(json.dumps(
        [sorted(set(codes)), epoch],
        separators=(",", ":")).encode("ascii")).hexdigest()
    # The same original owner SQLite; never create another bot or DB.
    with sqlite3.connect(ledger.db_path, timeout=5) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""
            CREATE TABLE IF NOT EXISTS m10_owner_ops_alert_claim (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                last_epoch INTEGER NOT NULL,
                digest TEXT NOT NULL
            )
        """)
        row = db.execute(
            "SELECT last_epoch,digest FROM m10_owner_ops_alert_claim "
            "WHERE singleton=1"
        ).fetchone()
        if row and (row[1] == digest or now - row[0] < _MIN_SPACING_SECONDS
                    or now < row[0]):
            return {"status": "M10_ALERT_ALREADY_CLAIMED_OR_THROTTLED",
                    "messages_sent": 0}
        db.execute("""
            INSERT INTO m10_owner_ops_alert_claim(singleton,last_epoch,digest)
            VALUES(1,?,?)
            ON CONFLICT(singleton) DO UPDATE SET
                last_epoch=excluded.last_epoch,digest=excluded.digest
        """, (now, digest))
    # Claim is persisted BEFORE network I/O. A failed/uncertain send is
    # never automatically replayed. Owner can still request /ops.
    message = format_v2_ops_owner_message(snap)
    if "ALERT_DATA_UNVERIFIED" in message:
        return {"status": "M10_ALERT_FORMAT_UNVERIFIED", "messages_sent": 0}
    message = message.replace(
        "Peringatan belum terbukti terkirim otomatis; ",
        "Pengiriman otomatis sedang dicoba; penerimaan owner belum terverifikasi; ",
    )
    from .telegram_bot_api import TelegramTransportError
    try:
        api.call("sendMessage", {
            "chat_id": ledger.owner_user_id,
            "text": "ALERT OTOMATIS M10-06 (READ ONLY)\n" + message,
        })
    except TelegramTransportError:
        return {"status": "M10_ALERT_DELIVERY_UNCERTAIN_NO_RETRY",
                "messages_sent": 0}
    return {"status": "M10_ALERT_SENT_UNVERIFIED_DELIVERY",
            "messages_sent": 1}
