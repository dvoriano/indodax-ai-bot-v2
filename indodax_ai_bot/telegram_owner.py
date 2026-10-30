"""M05 offline Telegram command/callback contract, NO BOT NETWORK.

A future Telegram HTTPS/polling adapter must authenticate update origin,
user ID and private chat from the actual Telegram API before calling this
interface. Raw JSON sent by a user is NOT authenticated by these functions.
"""
from __future__ import annotations
from typing import Any

from .owner_control import OwnerDecisionLedger, _blocked
from .m05_operator_views import readonly_operator_view


class OfflineTelegramOwnerInterface:
    def __init__(self, ledger: OwnerDecisionLedger):
        if not isinstance(ledger, OwnerDecisionLedger):
            raise TypeError("M05_REQUIRES_OWNER_LEDGER")
        self.ledger = ledger

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
        if command in ("/health", "/scanner") and len(parts) == 1:
            # Original real Telegram owner gate precedes this branch.
            # Read local V2 collector/systemd evidence only, no HTTP/AI/order.
            return readonly_operator_view(command, now_epoch=now_epoch)
        # Commands exist as a real UI contract, not fabricated account data.
        known = {
            "/market", "/ai", "/buy", "/positions", "/sell",
            "/balance", "/profit", "/history", "/risk",
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
