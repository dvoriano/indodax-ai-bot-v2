"""M05 Telegram identity/webhook READ-ONLY release preflight.

Never polls/acknowledges getUpdates, never sends a message or changes a webhook.
The ONLY API methods used are getMe and getWebhookInfo. Live authority and
Telegram private owner validation are NOT inferred from bot identity alone.
"""
from __future__ import annotations

import re
from typing import Any

from .telegram_bot_api import TelegramBotApiClient, TelegramTransportError

_USERNAME = re.compile(r"^[A-Za-z0-9_]{5,32}$")


def inspect_telegram_readonly(
    api: TelegramBotApiClient, *, expected_bot_id: int | None = None,
) -> dict[str, Any]:
    """At most two HTTPS read-only requests and a minimal redacted report.

    Unpinned discovered bot IDs are diagnostic only. This never verifies
    which human controls a Telegram account or grants polling permission.
    """
    if not isinstance(api, TelegramBotApiClient):
        raise TypeError("M05_PREFLIGHT_REQUIRES_BOT_CLIENT")
    if (expected_bot_id is not None and
            (type(expected_bot_id) is not int or expected_bot_id <= 0)):
        raise ValueError("M05_PREFLIGHT_INVALID_EXPECTED_BOT_ID")

    me = api.call("getMe", {})
    if not isinstance(me, dict):
        raise TelegramTransportError("M05_PREFLIGHT_INVALID_BOT_IDENTITY")
    identity = me.get("id")
    username = me.get("username")
    if (type(identity) is not int or identity <= 0
            or me.get("is_bot") is not True
            or not isinstance(username, str)
            or _USERNAME.fullmatch(username) is None):
        raise TelegramTransportError("M05_PREFLIGHT_INVALID_BOT_IDENTITY")
    if expected_bot_id is not None and identity != expected_bot_id:
        # Identity mismatch must stop BEFORE even the second HTTPS call.
        raise TelegramTransportError("M05_PREFLIGHT_BOT_ID_MISMATCH")

    info = api.call("getWebhookInfo", {})
    if not isinstance(info, dict):
        raise TelegramTransportError("M05_PREFLIGHT_INVALID_WEBHOOK_INFO")
    webhook_url = info.get("url")
    count = info.get("pending_update_count")
    if (not isinstance(webhook_url, str)
            or type(count) is not int or count < 0):
        raise TelegramTransportError("M05_PREFLIGHT_INVALID_WEBHOOK_INFO")

    webhook_present = bool(webhook_url)
    pinned = expected_bot_id is not None
    state = (
        "WEBHOOK_PRESENT_POLLING_BLOCKED" if webhook_present
        else "BOT_ID_DISCOVERY_ONLY_UNPINNED" if not pinned
        else "READONLY_PREFLIGHT_OK_POLLING_NOT_AUTHORIZED"
    )
    return {
        "stage": "M05_TELEGRAM_READONLY_PREFLIGHT",
        "status": state,
        "bot_id": identity,
        "bot_username": username,
        "bot_id_pinned_and_matched": pinned,
        "webhook_present": webhook_present,
        "polling_compatible_with_webhook_state": not webhook_present,
        "pending_update_count": count,
        "webhook_url_disclosed": False,
        "webhook_changed": False,
        "updates_fetched": 0,
        "messages_sent": 0,
        "callbacks_acknowledged": 0,
        "private_owner_identity_verified": False,
        "verified_owner_update_received": False,
        "g1_source_qualified": False,
        "trading_authorized": False,
        "paid_ai_authorized": False,
        "final_decision": "REJECT",
        "execution_allowed": False,
    }
