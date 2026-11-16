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
from time import monotonic
from typing import Any, Iterator

from .owner_control import OwnerDecisionLedger
from .telegram_bot_api import (
    TelegramBotApiClient, TelegramOwnerPoller, TelegramTransportError,
    TelegramGetUpdatesTransportError,
)
from .telegram_readonly_preflight import inspect_telegram_readonly

LEGACY_CAIPITI_BOT_ID = 8827061554
RADAR_CHECK_INTERVAL_SECONDS = 30


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


def _send_one_manual_trade_preview(
        *, api: TelegramBotApiClient,
        ledger: OwnerDecisionLedger) -> dict[str, Any]:
    """One M05 owner-private BUY/SELL preview card. NO trade transport."""
    try:
        claimed = ledger.claim_manual_trade_preview_delivery(
            now_epoch=int(time.time()),
        )
    except ValueError:
        raise TelegramTransportError(
            "M05_MANUAL_TRADE_DELIVERY_REVIEW_REQUIRED") from None
    if claimed is None:
        return {"delivery": "NOT_QUEUED", "messages_sent": 0}
    msg = api.call("sendMessage", {
        "chat_id": ledger.owner_user_id,
        "text": claimed["message_text"],
        "reply_markup": {"inline_keyboard": [[
            {"text": claimed["side"] + " (uji, tanpa order)",
             "callback_data": claimed["confirm_callback"]},
            {"text": "BATAL", "callback_data": claimed["reject_callback"]},
        ]]},
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
            "M05_MANUAL_TRADE_SEND_UNCERTAIN_REVIEW_REQUIRED")
    try:
        ledger.confirm_manual_trade_preview_delivery(
            preview_id=claimed["preview_id"],
            message_id=msg["message_id"],
        )
    except ValueError:
        raise TelegramTransportError(
            "M05_MANUAL_TRADE_ACK_UNCERTAIN_REVIEW_REQUIRED") from None
    return {"delivery": "SENT_OWNER_PRIVATE_REVIEW_ONLY", "messages_sent": 1}


def dedicated_owner_fast_loop_nonlive(
    *, api: TelegramBotApiClient, ledger: OwnerDecisionLedger,
    expected_bot_id: int, lock_path: str | Path,
    on_manual_buy_preview_nonlive=None,
    stop_requested=None, max_batches: int | None = None,
    wait_seconds: int = 2,
    heartbeat=None, auto_ops_alert=None, auto_radar_alert=None,
) -> dict[str, Any]:
    """Single original owner poller/ledger, always waiting at Telegram.

    A *separate explicitly opted-in* service process owns the EXISTING
    dedicated-poller lock continuously. The old M05 3-second timer MUST
    be disabled before deployment. Per-update audit/offset, delivery claims
    and callback authorization are unchanged. No background thread, new
    Telegram bot, exchange order, paid AI or competing SQLite owner.
    """
    if (not isinstance(api, TelegramBotApiClient)
            or not isinstance(ledger, OwnerDecisionLedger)
            or type(expected_bot_id) is not int or expected_bot_id <= 0
            or expected_bot_id == LEGACY_CAIPITI_BOT_ID):
        raise TelegramTransportError("M05_FAST_LOOP_DEDICATED_IDENTITY_INVALID")
    if type(wait_seconds) is not int or not 1 <= wait_seconds <= 5:
        raise ValueError("M05_FAST_LOOP_WAIT_INVALID")
    if (max_batches is not None
            and (type(max_batches) is not int
                 or not 1 <= max_batches <= 200)):
        raise ValueError("M05_FAST_LOOP_BATCH_LIMIT_INVALID")
    if stop_requested is not None and not callable(stop_requested):
        raise ValueError("M05_FAST_LOOP_STOP_HOOK_INVALID")
    if heartbeat is not None and not callable(heartbeat):
        raise ValueError("M05_FAST_LOOP_HEARTBEAT_INVALID")
    if auto_ops_alert is not None and not callable(auto_ops_alert):
        raise ValueError("M10_AUTO_OPS_ALERT_NOT_CALLABLE")
    if auto_radar_alert is not None and not callable(auto_radar_alert):
        raise ValueError("M05_AUTO_RADAR_ALERT_NOT_CALLABLE")
    batches = handled = ignored = notices = 0
    radar_invocations = radar_send_acks = radar_uncertain = 0
    ops_alert_failures = 0
    # A previously CLAIMED owner advisory has an uncertain delivery outcome.
    # Quarantine ONLY this outbox for this worker lifetime, preserving the
    # existing ledger's no-retry rule and keeping owner ingress/radar alive.
    advisory_delivery_review_required = False
    advisory_delivery_reason = "NONE"
    getupdates_transport_failures = 0
    getupdates_consecutive_failures = 0
    readonly_preflight_transport_failures = 0
    readonly_preflight_outage_bursts = 0
    radar_next_due = None
    radar_last_status = ("M05_RADAR_PENDING_FIRST_POLL"
                         if auto_radar_alert is not None else "M05_RADAR_OPTIN_OFF")
    radar_status_allowlist = frozenset((
        "M05_RADAR_OWNER_PRIVATE_NOTICE_SENT",
        "M05_RADAR_DELIVERY_UNCERTAIN_NO_RETRY",
        "M05_RADAR_NO_CURRENT_QUALIFIED_WATCH",
        "M05_RADAR_PREMIUM_SIX_GREEN_NOT_MET",
        "M05_RADAR_THROTTLED",
        "M05_RADAR_ORIGINAL_SOURCE_UNAVAILABLE",
        "M05_RADAR_OWNER_LEDGER_UNVERIFIED",
    ))
    # No new network behavior unless owner GO was granted upstream.
    with dedicated_poller_lock(lock_path):
        def _preflight() -> None:
            nonlocal readonly_preflight_transport_failures
            # getMe/getWebhookInfo are read-only. Retry ONLY network
            # transport failures before owner updates or send effects.
            for attempt in range(3):
                try:
                    result = inspect_telegram_readonly(
                        api, expected_bot_id=expected_bot_id,
                    )
                except TelegramTransportError as error:
                    if str(error) != "M05_TELEGRAM_API_TRANSPORT_FAILED":
                        raise
                    readonly_preflight_transport_failures += 1
                    if attempt == 2:
                        raise TelegramTransportError(
                            "M05_READONLY_PREFLIGHT_TRANSPORT_RETRY_EXHAUSTED"
                        ) from None
                    time.sleep(attempt + 1)
                    continue
                if result.get("status") == "WEBHOOK_PRESENT_POLLING_BLOCKED":
                    raise TelegramTransportError("M05_DEDICATED_WEBHOOK_PRESENT")
                if result.get("status") != (
                        "READONLY_PREFLIGHT_OK_POLLING_NOT_AUTHORIZED"):
                    raise TelegramTransportError("M05_DEDICATED_PREFLIGHT_UNSAFE")
                return

        def _verified_preflight_or_stop() -> bool:
            nonlocal readonly_preflight_outage_bursts
            # Retry only read-only network failures. Identity, webhook and
            # malformed API responses remain fatal before any owner effects.
            first_attempt = True
            while True:
                # Preserve original startup identity verification even when
                # shutdown is already requested; after a failed transport
                # attempt, respect shutdown before another network call.
                if (not first_attempt and stop_requested is not None
                        and stop_requested()):
                    return False
                first_attempt = False
                try:
                    _preflight()
                    readonly_preflight_outage_bursts = 0
                    return True
                except TelegramTransportError as error:
                    if str(error) != (
                            "M05_READONLY_PREFLIGHT_TRANSPORT_RETRY_EXHAUSTED"):
                        raise
                    readonly_preflight_outage_bursts += 1
                    # Same exclusive original poller lock; no getUpdates,
                    # outbound, or cursor changes until preflight succeeds.
                    time.sleep(min(
                        30, 5 * (2 ** min(readonly_preflight_outage_bursts - 1, 3))
                    ))

        if not _verified_preflight_or_stop():
            return {
                "status": "M05_FAST_LOOP_STOPPED_NONLIVE",
                "batches": 0, "handled": 0, "ignored": 0,
                "readonly_preflight_outage_bursts":
                    readonly_preflight_outage_bursts,
                "radar_invocations": 0, "radar_enabled": auto_radar_alert is not None,
                "single_original_poller": True, "execution_allowed": False,
                "order_sent": False,
            }
        poller = TelegramOwnerPoller(
            api=api, ledger=ledger, expected_bot_id=expected_bot_id,
            on_manual_buy_preview_nonlive=on_manual_buy_preview_nonlive,
        )
        while True:
            if stop_requested is not None and stop_requested():
                break
            # Recheck bot identity and webhook periodically, not on
            # every owner press. Upstream getUpdates also rejects a webhook.
            if batches > 0 and batches % 15 == 0:
                if not _verified_preflight_or_stop():
                    break
            try:
                result = poller.poll_once(limit=10, wait_seconds=wait_seconds)
            except TelegramGetUpdatesTransportError:
                # Only a failed, non-mutating getUpdates read is retried.
                # Owner sends and callbacks retain uncertain/no-replay rules.
                getupdates_transport_failures += 1
                getupdates_consecutive_failures += 1
                # Read-only getUpdates may recover from network outages.
                # Never ack/replay a cursor or owner send on uncertain IO.
                # API rejected and unsafe state errors remain fatal.
                time.sleep(
                    getupdates_consecutive_failures
                    if getupdates_consecutive_failures < 3
                    else min(30, 2 ** min(getupdates_consecutive_failures - 1, 5))
                )
                continue
            getupdates_consecutive_failures = 0
            if (result.get("execution_allowed") is not False
                    or result.get("final_decision") is not None):
                raise TelegramTransportError("M05_FAST_LOOP_EXECUTOR_CHANGED")
            # Flush the same original owner outboxes on each wakeup. During
            # idle periods getUpdates returns after <= wait_seconds.
            if advisory_delivery_review_required:
                outbound = {"delivery": "REVIEW_REQUIRED_NO_RETRY",
                            "messages_sent": 0}
            else:
                try:
                    outbound = _send_one_queued_market_advisory(
                        api=api, ledger=ledger,
                    )
                except TelegramTransportError as error:
                    code = str(error)
                    if code not in (
                        "M05_ADVISORY_DELIVERY_REVIEW_REQUIRED",
                        "M05_ADVISORY_SEND_UNCERTAIN_REVIEW_REQUIRED",
                        "M05_ADVISORY_ACK_UNCERTAIN_REVIEW_REQUIRED",
                        "M05_TELEGRAM_API_TRANSPORT_FAILED",
                    ):
                        raise
                    # The original SQLite CLAIMED row MUST NOT be reset,
                    # resent, marked SENT or silently expired. Only this
                    # advisory delivery lane pauses; owner Telegram updates,
                    # manual preview and public radar remain available.
                    advisory_delivery_review_required = True
                    advisory_delivery_reason = code
                    outbound = {"delivery": "REVIEW_REQUIRED_NO_RETRY",
                                "messages_sent": 0}
            manual_preview = (
                {"delivery": "DEFERRED_ONE_OWNER_CARD_PER_TICK",
                 "messages_sent": 0}
                if outbound["messages_sent"] != 0
                else _send_one_manual_trade_preview(api=api, ledger=ledger)
            )
            batches += 1
            # Reuse the SAME original owner receiver and durable SQLite.
            # Check after the first successful poll, then by elapsed time:
            # Ten completed longpoll batches are NOT a 30-second clock.
            # No catch-up bursts after slow calls. The sender's persistent
            # per-pair/global cooldown and uncertain-delivery claims still
            # govern notices, including after process restart.
            radar_checked = False
            if auto_radar_alert is not None and (
                    radar_next_due is None or monotonic() >= radar_next_due):
                radar_checked = True
                radar_invocations += 1
                try:
                    radar = auto_radar_alert(api=api, ledger=ledger)
                    status = (radar.get("status")
                              if isinstance(radar, dict) else None)
                    sent = (radar.get("messages_sent")
                            if isinstance(radar, dict) else None)
                    if (status in radar_status_allowlist
                            and type(sent) is int and sent in (0, 1)
                            and (sent == 1) ==
                            (status == "M05_RADAR_OWNER_PRIVATE_NOTICE_SENT")):
                        radar_last_status = status
                        radar_send_acks += sent
                        radar_uncertain += (
                            status == "M05_RADAR_DELIVERY_UNCERTAIN_NO_RETRY"
                        )
                    else:
                        radar_last_status = "M05_RADAR_UNTRUSTED_RESULT"
                        radar_uncertain += 1
                except Exception:
                    # Radar opt-in cannot kill original owner callback poller.
                    # Never place exception text/token/raw Bot API body in log.
                    radar_last_status = "M05_RADAR_CALLBACK_FAILED"
                    radar_uncertain += 1
                finally:
                    radar_next_due = monotonic() + RADAR_CHECK_INTERVAL_SECONDS
            # Ops retains its existing batch cadence, after the radar check.
            # These synchronous callbacks cannot guarantee a wall-time SLA
            # during network stalls; no extra thread/poller is introduced.
            if auto_ops_alert is not None and batches % 30 == 0:
                try:
                    auto_ops_alert(api=api, ledger=ledger)
                except Exception:
                    ops_alert_failures += 1
            handled += result["handled"]
            ignored += result["ignored"]
            notices += (outbound["messages_sent"]
                        + manual_preview["messages_sent"])
            if heartbeat is not None and (radar_checked or batches % 10 == 0):
                heartbeat({
                    "status": "M05_FAST_LOOP_HEARTBEAT_NONLIVE",
                    "batches": batches, "handled": handled,
                    "ignored": ignored, "outbound_sent": notices,
                    "ops_alert_failures": ops_alert_failures,
                    "advisory_delivery_review_required": advisory_delivery_review_required,
                    "advisory_delivery_reason": advisory_delivery_reason,
                    "getupdates_transport_failures": getupdates_transport_failures,
                    "readonly_preflight_transport_failures": readonly_preflight_transport_failures,
                    "readonly_preflight_outage_bursts": readonly_preflight_outage_bursts,
                    "getupdates_consecutive_failures": getupdates_consecutive_failures,
                    "radar_enabled": auto_radar_alert is not None,
                    "radar_invocations": radar_invocations,
                    "radar_send_acks": radar_send_acks,
                    "radar_delivery_uncertain": radar_uncertain,
                    "radar_last_status": radar_last_status,
                    "execution_allowed": False,
                })
            if max_batches is not None and batches >= max_batches:
                break
    return {
        "status": "M05_FAST_LOOP_EXITED_NONLIVE",
        "batches": batches, "handled": handled,
        "ignored": ignored, "outbound_sent": notices,
        "next_offset": ledger.telegram_next_offset(),
        "single_original_poller": True,
        "ops_alert_failures": ops_alert_failures,
        "advisory_delivery_review_required": advisory_delivery_review_required,
        "advisory_delivery_reason": advisory_delivery_reason,
        "getupdates_transport_failures": getupdates_transport_failures,
        "readonly_preflight_transport_failures": readonly_preflight_transport_failures,
        "readonly_preflight_outage_bursts": readonly_preflight_outage_bursts,
        "getupdates_consecutive_failures": getupdates_consecutive_failures,
        "radar_enabled": auto_radar_alert is not None,
        "radar_invocations": radar_invocations,
        "radar_send_acks": radar_send_acks,
        "radar_delivery_uncertain": radar_uncertain,
        "radar_last_status": radar_last_status,
        "bot_identity_pinned_and_matched": True,
        "owner_ledger_original": True,
        "order_sent": False, "execution_allowed": False,
        "final_decision": None,
    }


def dedicated_owner_poll_once(
    *, api: TelegramBotApiClient, ledger: OwnerDecisionLedger,
    expected_bot_id: int, lock_path: str | Path, limit: int = 10,
    on_manual_buy_preview_nonlive=None,
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

        # Receive command/callback first, then flush ONE owner outbox item
        # in this SAME systemd tick. Previously new /reviewtest items waited
        # until the NEXT timer tick and lost half their 90-second TTL.
        result = TelegramOwnerPoller(
            api=api, ledger=ledger, expected_bot_id=expected_bot_id,
            on_manual_buy_preview_nonlive=on_manual_buy_preview_nonlive,
        ).poll_once(limit=limit)
        outbound = _send_one_queued_market_advisory(
            api=api, ledger=ledger,
        )
        manual_preview = (
            {"delivery": "DEFERRED_ONE_OWNER_CARD_PER_TICK",
             "messages_sent": 0}
            if outbound["messages_sent"] != 0
            else _send_one_manual_trade_preview(api=api, ledger=ledger)
        )
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
        "owner_manual_trade_preview_delivery": manual_preview,
        "automatic_polling_enabled": False,
        "live_execution_authorized": False,
        "final_decision": None,
        "execution_allowed": False,
    }

