"""M05 dedicated-bot owner ingress, exclusively one bounded poll.

The existing Caipiti bot is owned by another gateway. Its bot ID is DENIED
here. Operator must provision a DIFFERENT Telegram bot, verify the exact Bot
API identity and empty webhook, and explicitly enable this one-shot process.

No systemd scheduler, incoming updates on old Caipiti, paid AI or execution.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
import time
from typing import Any, Iterator

from .owner_control import OwnerDecisionLedger
from .telegram_bot_api import (
    TelegramBotApiClient, TelegramOwnerPoller, TelegramTransportError,
)
from .telegram_readonly_preflight import inspect_telegram_readonly

LEGACY_CAIPITI_BOT_ID = 8827061554


@contextmanager
def dedicated_poller_lock(path: str | Path) -> Iterator[None]:
    """Protect one persistent owner DB from concurrent local poll processes.

    Never unlink a lock file: doing so could permit overlapping lock inodes.
    A lock cannot prove a remote process isn't using the same Telegram bot.
    """
    p = Path(path)
    if not p.is_absolute() or not p.parent.is_dir():
        raise TelegramTransportError("M05_DEDICATED_LOCK_PATH_INVALID")
    fd = None
    try:
        fd = os.open(
            p, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), 0o600,
        )
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode)
                or st.st_uid != os.getuid()
                or st.st_nlink != 1
                or stat.S_IMODE(st.st_mode) != 0o600):
            raise TelegramTransportError("M05_DEDICATED_LOCK_UNSAFE")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise TelegramTransportError("M05_DEDICATED_POLLER_ALREADY_RUNNING") from None
        yield
    except TelegramTransportError:
        raise
    except OSError:
        raise TelegramTransportError("M05_DEDICATED_LOCK_IO_FAILED") from None
    finally:
        if fd is not None:
            os.close(fd)


def _send_one_queued_market_advisory(
    *, api: TelegramBotApiClient, ledger: OwnerDecisionLedger,
) -> dict[str, Any]:
    """Owner-private inline buttons, original M05 DB and Bot API transport.

    A CLAIMED message must never be retried on uncertain network delivery.
    No order or private exchange request exists on this sender path.
    """
    try:
        claim = ledger.claim_market_advisory_delivery(
            now_epoch=int(time.time()),
        )
    except ValueError:
        raise TelegramTransportError(
            "M05_ADVISORY_DELIVERY_REVIEW_REQUIRED"
        ) from None
    if claim is None:
        return {"delivery": "NOT_QUEUED", "messages_sent": 0}
    msg = api.call("sendMessage", {
        "chat_id": ledger.owner_user_id,
        "text": claim["message_text"],
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "PASS (review only)",
                 "callback_data": claim["pass_callback"]},
                {"text": "REJECT",
                 "callback_data": claim["reject_callback"]},
            ]],
        },
        "disable_notification": False,
    })
    if (not isinstance(msg, dict)
            or type(msg.get("message_id")) is not int
            or msg["message_id"] <= 0
            or not isinstance(msg.get("chat"), dict)
            or type(msg["chat"].get("id")) is not int
            or msg["chat"]["id"] != ledger.owner_user_id
            or msg["chat"].get("type") != "private"):
        raise TelegramTransportError(
            "M05_ADVISORY_SEND_UNCERTAIN_REVIEW_REQUIRED"
        )
    try:
        ledger.confirm_market_advisory_delivery(
            proposal_id=claim["proposal_id"],
            message_id=msg["message_id"],
        )
    except ValueError:
        raise TelegramTransportError(
            "M05_ADVISORY_ACK_UNCERTAIN_REVIEW_REQUIRED"
        ) from None
    return {"delivery": "SENT_OWNER_PRIVATE", "messages_sent": 1}


def dedicated_owner_poll_once(
    *, api: TelegramBotApiClient, ledger: OwnerDecisionLedger,
    expected_bot_id: int, lock_path: str | Path, limit: int = 10,
) -> dict[str, Any]:
    """Get pinned identity, reject webhook conflict, poll ONCE, then release lock.

    The old Caipiti credential is prohibited. getUpdates is called only after
    a locally exclusive lock and TWO Bot API preflight checks succeed.
    """
    if not isinstance(api, TelegramBotApiClient):
        raise TypeError("M05_DEDICATED_REQUIRES_API_CLIENT")
    if not isinstance(ledger, OwnerDecisionLedger):
        raise TypeError("M05_DEDICATED_REQUIRES_OWNER_LEDGER")
    if type(expected_bot_id) is not int or expected_bot_id <= 0:
        raise ValueError("M05_DEDICATED_BOT_ID_INVALID")
    if expected_bot_id == LEGACY_CAIPITI_BOT_ID:
        raise TelegramTransportError("M05_DEDICATED_EXISTING_CAIPITI_DENIED")
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("M05_DEDICATED_POLL_LIMIT_INVALID")

    with dedicated_poller_lock(lock_path):
        preflight = inspect_telegram_readonly(
            api, expected_bot_id=expected_bot_id,
        )
        if preflight["status"] == "WEBHOOK_PRESENT_POLLING_BLOCKED":
            raise TelegramTransportError("M05_DEDICATED_WEBHOOK_PRESENT")
        if (preflight["status"] !=
                "READONLY_PREFLIGHT_OK_POLLING_NOT_AUTHORIZED"):
            raise TelegramTransportError("M05_DEDICATED_PREFLIGHT_UNSAFE")

        outbound = _send_one_queued_market_advisory(
            api=api, ledger=ledger,
        )
        result = TelegramOwnerPoller(
            api=api, ledger=ledger, expected_bot_id=expected_bot_id,
        ).poll_once(limit=limit)
    # The poll summary is not an owner trade proposal. Each individual
    # callback is durably recorded in the owner ledger; batch has no Final.
    if result.get("final_decision") is not None or (
            result.get("execution_allowed") is not False):
        raise TelegramTransportError("M05_DEDICATED_OWNER_GATE_CHANGED")
    return {
        "status": "M05_DEDICATED_OWNER_ONE_POLL_DONE",
        "bot_id_pinned_and_matched": True,
        "old_caipiti_bot_used": False,
        "webhook_present": False,
        "local_exclusive_lock": True,
        "inbound_owner_identity": "BOT_API_ENVELOPE_FILTERED_ONLY",
        "human_owner_verified": False,
        "handler": result,
        "owner_market_advisory_delivery": outbound,
        "automatic_polling_enabled": False,
        "live_execution_authorized": False,
        "final_decision": None,
        "execution_allowed": False,
    }
