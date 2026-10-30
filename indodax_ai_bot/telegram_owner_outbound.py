"""M05 one-way owner-status notification. No Telegram polling or trading.

This is a strictly opt-in, single send. It cannot receive callbacks, alter the
owner ledger mode or grant execution. Keep the Bot API token outside this repo.
"""
from __future__ import annotations

from typing import Any

from .owner_control import OwnerDecisionLedger
from .telegram_bot_api import TelegramBotApiClient, TelegramTransportError


def send_owner_safe_status_once(
    *, api: TelegramBotApiClient, ledger: OwnerDecisionLedger,
    expected_bot_id: int,
) -> dict[str, Any]:
    """Verify bot ID, read owner shadow state, send one non-actionable status.

    Only calls getMe, then sendMessage. Never uses getUpdates, webhook methods,
    callback answers or private exchange data. This does not verify a human
    owner or establish exclusive polling ownership.
    """
    if not isinstance(api, TelegramBotApiClient):
        raise TypeError("M05_OUTBOUND_REQUIRES_BOT_API_CLIENT")
    if not isinstance(ledger, OwnerDecisionLedger):
        raise TypeError("M05_OUTBOUND_REQUIRES_OWNER_LEDGER")
    if type(expected_bot_id) is not int or expected_bot_id <= 0:
        raise ValueError("M05_OUTBOUND_EXPECTED_BOT_ID_REQUIRED")

    me = api.call("getMe", {})
    if (not isinstance(me, dict)
            or type(me.get("id")) is not int
            or me["id"] != expected_bot_id
            or me.get("is_bot") is not True):
        raise TelegramTransportError("M05_OUTBOUND_BOT_ID_MISMATCH")

    owner_id = ledger.owner_user_id
    if type(owner_id) is not int or owner_id <= 0:
        raise TelegramTransportError("M05_OUTBOUND_OWNER_ID_INVALID")
    state = ledger.status(
        user_id=owner_id, chat_id=owner_id, chat_type="private",
    )
    if (state.get("accepted") is not True
            or state.get("owner_mode") not in ("MANUAL", "AUTO")
            or state.get("final_decision") != "REJECT"
            or state.get("execution_allowed") is not False):
        raise TelegramTransportError("M05_OUTBOUND_OWNER_STATE_UNSAFE")

    mode = state["owner_mode"]
    # Deliberately no balance, signal, profit, order, or private account data.
    message = (
        "Indodax AI Bot V2 | Status M05\n"
        "Mode owner: " + mode + "\n"
        "Telegram: notifikasi satu arah; balasan/tombol belum terhubung.\n"
        "Final Decision: REJECT. Eksekusi trading: OFF.\n"
        "Tidak ada proposal BUY/SELL atau data akun live terverifikasi."
    )
    sent = api.call("sendMessage", {
        "chat_id": owner_id,
        "text": message,
        "disable_notification": True,
    })
    if (not isinstance(sent, dict)
            or type(sent.get("message_id")) is not int
            or sent["message_id"] <= 0
            or not isinstance(sent.get("chat"), dict)
            or type(sent["chat"].get("id")) is not int
            or sent["chat"]["id"] != owner_id
            or sent["chat"].get("type") != "private"):
        # Delivery outcome is uncertain. Do not retry automatically.
        raise TelegramTransportError(
            "M05_OUTBOUND_RESPONSE_UNVERIFIED_DO_NOT_RETRY"
        )
    return {
        "status": "M05_OUTBOUND_SAFE_STATUS_SUBMITTED",
        "bot_id_pinned_and_matched": True,
        "recipient_private_chat_matched": True,
        "message_id": sent["message_id"],
        "messages_sent": 1,
        "updates_fetched": 0,
        "callbacks_processed": 0,
        "webhook_modified": False,
        "owner_mode": mode,
        "owner_identity_verified_by_incoming_update": False,
        "inbound_polling_authorized": False,
        "account_data_connected": False,
        "final_decision": "REJECT",
        "execution_allowed": False,
    }
