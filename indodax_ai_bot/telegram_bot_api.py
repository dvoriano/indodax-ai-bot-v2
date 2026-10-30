"""M05 Telegram Bot API polling boundary, bounded and OFF by default.

Only the OFFICIAL HTTPS Bot API getUpdates response is a trusted update
transport when explicitly enabled. Injected transport is TEST-ONLY and is NOT
proof of authenticity. Webhooks are deliberately unsupported here; mixing
webhook and polling causes Telegram updates to be unavailable to getUpdates.

No private Indodax API, strategy execution, Telegram token in logs, or orders.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler, Request, build_opener,
)

from .owner_control import OwnerDecisionLedger
from .telegram_owner import OfflineTelegramOwnerInterface

BOT_API_HOST = "https://api.telegram.org"
TOKEN_RE = re.compile(r"^[0-9]{5,20}:[A-Za-z0-9_-]{20,128}$")
METHODS = frozenset(("getUpdates", "sendMessage", "answerCallbackQuery"))
Transport = Callable[[str, dict[str, Any], float], Any]


class TelegramTransportError(RuntimeError):
    """Stable error codes only, never print Bot API URLs or bearer tokens."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # The secret token appears in the request URL. Never follow redirects.
        return None


class TelegramBotApiClient:
    def __init__(self, *, token: str, transport: Transport | None = None,
                 allow_network: bool = False, timeout_seconds: float = 6.0):
        if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
            raise ValueError("INVALID_M05_TELEGRAM_TOKEN")
        if (type(timeout_seconds) not in (int, float)
                or not 1 <= timeout_seconds <= 12):
            raise ValueError("INVALID_M05_TELEGRAM_TIMEOUT")
        self._token = token
        self._transport = transport
        self._allow_network = allow_network is True
        self._timeout = float(timeout_seconds)

    def __repr__(self) -> str:
        return "<TelegramBotApiClient token=REDACTED network_default_off>"

    def call(self, method: str, payload: dict[str, Any]) -> Any:
        if method not in METHODS or not isinstance(payload, dict):
            raise TelegramTransportError("M05_TELEGRAM_METHOD_DENIED")
        if self._transport is None and not self._allow_network:
            raise TelegramTransportError("M05_TELEGRAM_NETWORK_DISABLED")
        if self._transport is not None:
            try:
                response = self._transport(
                    method, dict(payload), self._timeout
                )
            except Exception:
                raise TelegramTransportError(
                    "M05_TELEGRAM_API_TRANSPORT_FAILED"
                ) from None
        else:
            url = BOT_API_HOST + "/bot" + self._token + "/" + method
            req = Request(
                url, method="POST",
                data=json.dumps(
                    payload, separators=(",", ":"), allow_nan=False
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            try:
                # No redirects. Enforce bounded timeout and response size.
                with build_opener(_NoRedirect()).open(
                        req, timeout=self._timeout) as res:
                    raw = res.read(131073)
                if len(raw) > 131072:
                    raise TelegramTransportError("M05_TELEGRAM_RESPONSE_TOO_LARGE")
                response = json.loads(raw)
            except TelegramTransportError:
                raise
            except (HTTPError, URLError, OSError, TimeoutError,
                    ValueError, TypeError):
                raise TelegramTransportError(
                    "M05_TELEGRAM_API_TRANSPORT_FAILED"
                ) from None
        if (not isinstance(response, Mapping)
                or response.get("ok") is not True
                or "result" not in response):
            raise TelegramTransportError("M05_TELEGRAM_API_REJECTED")
        return response["result"]


class TelegramOwnerPoller:
    """One bounded Bot API poll, reusing existing M05 owner ledger and UI.

    Caller must not invoke poll_once concurrently or while webhooks are active.
    The SQLite cursor is persisted only after a safe handler outcome. A crash
    during a claimed update halts for operator review, never retries consent.
    """

    def __init__(self, *, api: TelegramBotApiClient,
                 ledger: OwnerDecisionLedger,
                 clock: Callable[[], float] = time.time):
        if not isinstance(api, TelegramBotApiClient):
            raise TypeError("M05_REQUIRES_TELEGRAM_API_CLIENT")
        if not isinstance(ledger, OwnerDecisionLedger):
            raise TypeError("M05_REQUIRES_OWNER_LEDGER")
        self._api = api
        self._ledger = ledger
        self._owner = OfflineTelegramOwnerInterface(ledger)
        self._clock = clock

    def _dispatch(self, update: Mapping[str, Any],
                  now_epoch: int) -> tuple[str, str | None, str | None]:
        """Return fixed safe outcome, reply method, reply target/message.

        Never trust identities supplied outside the fetched Bot API envelope.
        """
        msg = update.get("message")
        callback = update.get("callback_query")
        if (msg is None) == (callback is None):
            return ("IGNORED_UNSUPPORTED", None, None)
        if msg is not None:
            if not isinstance(msg, dict):
                return ("REJECT_MALFORMED_MESSAGE", None, None)
            user = msg.get("from")
            chat = msg.get("chat")
            if not isinstance(user, dict) or not isinstance(chat, dict):
                return ("REJECT_MISSING_IDENTITY", None, None)
            user_id, chat_id = user.get("id"), chat.get("id")
            if not self._ledger._is_owner(
                    user_id, chat_id, chat.get("type")):
                return ("REJECT_NOT_OWNER_PRIVATE", None, None)
            sent_epoch = msg.get("date")
            if (type(sent_epoch) is not int
                    or not 0 <= now_epoch - sent_epoch <= 90):
                return ("REJECT_STALE_COMMAND", None, None)
            text = msg.get("text")
            if not isinstance(text, str):
                return ("IGNORED_NON_TEXT_MESSAGE", None, None)
            result = self._owner.handle_command(
                user_id=user_id, chat_id=chat_id, text=text,
                now_epoch=now_epoch, chat_type="private",
            )
            # No source/account data is fabricated: UI gives NOT_CONNECTED.
            reply = result.get("text", "Perintah belum tersedia. Trading OFF.")
            if not isinstance(reply, str) or len(reply) > 1024:
                raise TelegramTransportError("M05_TELEGRAM_UNSAFE_REPLY")
            return ("OWNER_COMMAND_HANDLED", "sendMessage", reply)
        if not isinstance(callback, dict):
            return ("REJECT_MALFORMED_CALLBACK", None, None)
        from_user = callback.get("from")
        message = callback.get("message")
        if not isinstance(from_user, dict) or not isinstance(message, dict):
            # Inline mode/inaccessible callback cannot prove private owner chat.
            return ("REJECT_CALLBACK_WITHOUT_PRIVATE_CHAT", None, None)
        chat = message.get("chat")
        if not isinstance(chat, dict) or not self._ledger._is_owner(
                from_user.get("id"), chat.get("id"), chat.get("type")):
            return ("REJECT_CALLBACK_NOT_OWNER", None, None)
        data = callback.get("data")
        if not isinstance(data, str) or len(data.encode("utf-8")) > 64:
            return ("REJECT_CALLBACK_DATA", None, None)
        result = self._owner.handle_callback(
            user_id=from_user["id"], chat_id=chat["id"],
            data=data, now_epoch=now_epoch, chat_type="private",
        )
        # Even a successful shadow PASS has Final REJECT.
        if result.get("execution_allowed") is not False or (
                result.get("final_decision") != "REJECT"):
            raise TelegramTransportError("M05_TELEGRAM_OWNER_GATE_CHANGED")
        return (
            "OWNER_CALLBACK_HANDLED" if result.get("accepted") else
            "OWNER_CALLBACK_REJECTED",
            "answerCallbackQuery", "Simulasi tercatat. Trading tetap OFF.",
        )

    def poll_once(self, *, limit: int = 10) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("INVALID_M05_POLL_LIMIT")
        offset = self._ledger.telegram_next_offset()
        updates = self._api.call("getUpdates", {
            "offset": offset, "limit": limit, "timeout": 0,
            "allowed_updates": ["message", "callback_query"],
        })
        if not isinstance(updates, list) or len(updates) > limit:
            raise TelegramTransportError("M05_TELEGRAM_INVALID_BATCH")
        ids = []
        for update in updates:
            if (not isinstance(update, dict)
                    or type(update.get("update_id")) is not int
                    or update["update_id"] < 0):
                raise TelegramTransportError("M05_TELEGRAM_INVALID_BATCH")
            ids.append(update["update_id"])
        if ids != sorted(set(ids)):
            raise TelegramTransportError("M05_TELEGRAM_UNSORTED_OR_DUPLICATE")
        handled = ignored = 0
        for update in updates:
            uid = update["update_id"]
            if uid < self._ledger.telegram_next_offset():
                ignored += 1
                continue
            callback = update.get("callback_query")
            callback_id = callback.get("id") if isinstance(callback, dict) else None
            now_epoch = int(self._clock())
            try:
                claimed = self._ledger.claim_telegram_update(
                    update_id=uid, callback_query_id=callback_id,
                    now_epoch=now_epoch,
                )
            except ValueError:
                raise TelegramTransportError(
                    "M05_TELEGRAM_INVALID_UPDATE_CLAIM"
                ) from None
            if claimed == "INCOMPLETE_REVIEW_REQUIRED":
                raise TelegramTransportError(
                    "M05_TELEGRAM_INCOMPLETE_REVIEW_REQUIRED"
                )
            if claimed != "CLAIMED":
                ignored += 1
                continue
            try:
                outcome, method, reply = self._dispatch(update, now_epoch)
                # Only after immutable ledger outcome may the next offset
                # acknowledge this update to Telegram.
                self._ledger.complete_telegram_update(
                    update_id=uid, outcome=outcome,
                )
            except Exception:
                # A CLAIMED row survives. Do not silently retry any owner
                # command or callback following an uncertain interrupted write.
                raise TelegramTransportError(
                    "M05_TELEGRAM_HANDLER_INCOMPLETE_REVIEW_REQUIRED"
                ) from None
            handled += 1
            if method is not None:
                if method == "sendMessage":
                    payload = {
                        "chat_id": self._ledger.owner_user_id,
                        "text": reply,
                    }
                elif method == "answerCallbackQuery":
                    payload = {
                        "callback_query_id": callback["id"],
                        "text": reply,
                        "show_alert": False,
                    }
                else:
                    raise TelegramTransportError("M05_TELEGRAM_UNEXPECTED_REPLY")
                # If sending fails, consent remains consumed/durable. It is
                # not re-executed on retries or on a later process restart.
                self._api.call(method, payload)
        return {
            "handled": handled, "ignored": ignored,
            "next_offset": self._ledger.telegram_next_offset(),
            "final_decision": "REJECT", "execution_allowed": False,
            "telegram_network_default_off": True,
        }
