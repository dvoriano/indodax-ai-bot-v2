"""M05 offline Telegram command/callback contract, NO BOT NETWORK.

A future Telegram HTTPS/polling adapter must authenticate update origin,
user ID and private chat from the actual Telegram API before calling this
interface. Raw JSON sent by a user is NOT authenticated by these functions.
"""
from __future__ import annotations
from typing import Any, Callable
import re

from .owner_control import OwnerDecisionLedger, _blocked


_PUBLIC_HEALTH_STATES = frozenset((
    "HEALTHY_LAST_BATCH_UNATTESTED", "COLLECTING_UNATTESTED", "ALERT_REVIEW",
))
_PUBLIC_REASON = re.compile(r"^[A-Z][A-Z0-9_]{0,80}$")


def _btc_owner_health_text(document: Any) -> str:
    """Only owner-safe public data, never raw provider/systemd/JSON content."""
    fallback = (
        "BTC PUBLIC | ALERT_REVIEW\n"
        "Status kesehatan belum dapat diverifikasi.\n"
        "G1: TIDAK TERBUKTI. Trading: OFF."
    )
    if (not isinstance(document, dict)
            or document.get("stage") != "M10_ORIGINAL_BTC_PUBLIC_OPERATIONS_HEALTH"
            or document.get("pair") != "btc_idr"
            or document.get("read_only") is not True
            or document.get("execution_allowed") is not False
            or document.get("g1_receipt_issuance_allowed") is not False
            or document.get("g2_provider_call_allowed") is not False
            or document.get("source_authenticated_for_g1") is not False
            or document.get("full_trade_tape_proven") is not False
            or document.get("notification_sent") is not False
            or document.get("final_decision") != "REJECT"
            or document.get("status") not in _PUBLIC_HEALTH_STATES):
        return fallback
    state = document["status"]
    reason = document.get("reason")
    if not isinstance(reason, str) or not _PUBLIC_REASON.fullmatch(reason):
        return fallback
    if state == "ALERT_REVIEW":
        return (
            "BTC PUBLIC | ALERT_REVIEW\n"
            "Alasan: " + reason + "\n"
            "G1: TIDAK TERBUKTI. Trading: OFF."
        )
    if reason not in (
            "M10_RECENT_PUBLIC_BATCH_CONSISTENT",
            "M10_BOUNDED_BATCH_IN_PROGRESS"):
        return fallback
    required = ("publications", "historical_gaps", "gap_budget",
                "last_batch_age_seconds", "go_remaining_seconds")
    if any(type(document.get(k)) is not int or not 0 <= document[k] <= 1000000000
           for k in required):
        return fallback
    if (document.get("timer_active") is not True
            or document.get("restart_disabled") is not True):
        return fallback
    return (
        "BTC PUBLIC | " + state + "\n"
        "Publikasi: " + str(document["publications"]) + "\n"
        "Gap tercatat/batas: " + str(document["historical_gaps"]) +
        "/" + str(document["gap_budget"]) + "\n"
        "Umur batch: " + str(document["last_batch_age_seconds"]) + " detik\n"
        "Izin publik tersisa: " + str(document["go_remaining_seconds"] // 60) +
        " menit\n"
        "G1: TIDAK TERBUKTI. Trading: OFF."
    )


class OfflineTelegramOwnerInterface:
    def __init__(self, ledger: OwnerDecisionLedger, *,
                 btc_health_provider: Callable[[], dict[str, Any]] | None = None):
        if not isinstance(ledger, OwnerDecisionLedger):
            raise TypeError("M05_REQUIRES_OWNER_LEDGER")
        if btc_health_provider is not None and not callable(btc_health_provider):
            raise TypeError("M05_BTC_HEALTH_PROVIDER_INVALID")
        self.ledger = ledger
        self._btc_health_provider = btc_health_provider

    def handle_command(self, *, user_id: int, chat_id: int,
                       text: str, now_epoch: int,
                       chat_type: str = "private") -> dict[str, Any]:
        # The future authenticated Telegram transport supplies these fields.
        if not self.ledger._is_owner(user_id, chat_id, chat_type):
            return _blocked("OWNER_NOT_AUTHORIZED")
        if not isinstance(text, str) or len(text) > 180:
            return _blocked("INVALID_TELEGRAM_COMMAND")
        parts = text.lower().strip().split()
        if not parts:
            return _blocked("INVALID_TELEGRAM_COMMAND")
        command = parts[0].split("@")[0]
        if command == "/health" and len(parts) == 1 and self._btc_health_provider:
            try:
                readout = self._btc_health_provider()
            except Exception:
                readout = None
            return {
                **_blocked("M10_BTC_PUBLIC_READONLY"),
                "text": _btc_owner_health_text(readout),
            }
        if command in ("/start", "/status", "/kontrol") and len(parts) == 1:
            state = self.ledger.status(
                user_id=user_id, chat_id=chat_id, chat_type=chat_type
            )
            return {
                **state,
                "text": (
                    "Indodax V2 M05 OFFLINE SHADOW. Mode "
                    + state["owner_mode"]
                    + ". Tidak ada proposal eksekusi yang disahkan."
                ),
            }
        if command == "/kontrol" and len(parts) == 2:
            if parts[1] not in ("manual", "auto"):
                return _blocked("INVALID_TELEGRAM_MODE")
            updated = self.ledger.switch_mode(
                user_id=user_id, chat_id=chat_id,
                mode=parts[1].upper(), now_epoch=now_epoch,
                chat_type=chat_type,
            )
            return {
                **updated,
                "text": (
                    "Mode " + updated["owner_mode"]
                    + " tersimpan. AUTO tidak mengaktifkan order."
                ),
            }
        # Commands exist as a real UI contract, not fabricated account data.
        known = {
            "/scanner", "/market", "/ai", "/buy", "/positions", "/sell",
            "/balance", "/profit", "/history", "/risk", "/health",
        }
        if command in known and len(parts) == 1:
            return {
                **_blocked("LIVE_DATA_OR_EXECUTOR_NOT_CONNECTED"),
                "text": (
                    "Belum terhubung ke sumber live tepercaya atau akun; "
                    "tidak ada data transaksi yang akan direkayasa."
                ),
            }
        return _blocked("UNKNOWN_TELEGRAM_COMMAND")

    def handle_callback(self, *, user_id: int, chat_id: int,
                        data: str, now_epoch: int,
                        chat_type: str = "private") -> dict[str, Any]:
        return self.ledger.apply_callback(
            user_id=user_id, chat_id=chat_id, callback_data=data,
            now_epoch=now_epoch, chat_type=chat_type,
        )

    @staticmethod
    def keyboard_for_shadow_proposal(item: dict[str, Any]) -> dict[str, Any]:
        if (item.get("accepted") is not True or item.get("shadow_only") is not True
                or item.get("execution_allowed") is not False):
            return _blocked("NO_VALID_SHADOW_PROPOSAL")
        callbacks = item.get("callbacks")
        if not isinstance(callbacks, dict) or set(callbacks) != {"PASS", "REJECT"}:
            return _blocked("INVALID_SHADOW_CALLBACKS")
        return {
            "inline_keyboard": [[
                {"text": "PASS (uji)", "callback_data": callbacks["PASS"]},
                {"text": "REJECT", "callback_data": callbacks["REJECT"]},
            ]],
            "network_sent": False,
            "execution_allowed": False,
        }
