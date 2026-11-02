"""M05 offline Telegram command/callback contract, NO BOT NETWORK.

A future Telegram HTTPS/polling adapter must authenticate update origin,
user ID and private chat from the actual Telegram API before calling this
interface. Raw JSON sent by a user is NOT authenticated by these functions.
"""
from __future__ import annotations
from typing import Any

from .owner_control import OwnerDecisionLedger, _blocked


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
        if command == "/reviewtest" and len(parts) == 1:
            demo = self.ledger.queue_owner_review_button_test(
                user_id=user_id, chat_id=chat_id, now_epoch=now_epoch,
                chat_type=chat_type,
            )
            if demo.get("accepted") is True:
                return {
                    **demo,
                    "text": (
                        "SIMULASI M05: tombol PASS/REJECT review-only "
                        "diantrekan untuk tick Telegram berikutnya. "
                        "Berlaku maksimal 90 detik, bukan sinyal pasar, "
                        "tidak memakai AI/G1/saldo. Trading OFF."
                    ),
                }
            return {
                **demo,
                "text": (
                    "SIMULASI M05 tidak diantrekan: "
                    + demo.get("reason", "UNAVAILABLE")
                    + ". Tidak ada order. Trading OFF."
                ),
            }
        # Original M05 SQLite review records are genuine local evidence,
        # never exchange trade/fill evidence. Read the SAME owner ledger.
        if command in ("/market", "/history") and len(parts) == 1:
            review = self.ledger.owner_review_snapshot(
                user_id=user_id, chat_id=chat_id, now_epoch=now_epoch,
                chat_type=chat_type,
            )
            if review.get("accepted") is not True:
                return review
            if command == "/market":
                details = (
                    "M05 market advisory lokal: antre " +
                    str(review["fresh_queued_market_advisories"]) +
                    ", terkirim & belum kedaluwarsa " +
                    str(review["fresh_sent_market_advisories"]) +
                    ", tercatat total " +
                    str(review["market_advisories_recorded"]) + ". "
                    "Sumber G1 independen belum terverifikasi; "
                    "ini BUKAN sinyal BUY. Trading OFF."
                )
            else:
                details = (
                    "M05 riwayat tombol review owner (bukan transaksi): "
                    "PASS " + str(review["owner_review_pass_count"]) +
                    ", REJECT " + str(review["owner_review_reject_count"]) +
                    ". Riwayat order/fill Indodax belum diverifikasi. "
                    "Trading OFF."
                )
            return {
                **_blocked("LIVE_DATA_OR_EXECUTOR_NOT_CONNECTED"),
                "observed_owner_review": review,
                "text": details,
            }
        # Every other original V2 owner command gives a precise, safe
        # unavailable status without pretending to access private APIs.
        unavailable = {
            "/scanner": "Scanner M03 terjadwal belum berizin aktif; /market hanya menampilkan ledger M05. Trading OFF.",
            "/ai": "MARKET, RISK, STRATEGY belum mempunyai respons provider berbayar dan sumber G1 tervalidasi. Trading OFF.",
            "/buy": "BUY tidak tersedia: G1, risk, saldo akun, izin LIVE dan executor belum terverifikasi. Tidak mengirim order.",
            "/positions": "Posisi milik bot belum diverifikasi dari fill Indodax. Tidak mengarang posisi atau saldo.",
            "/sell": "SELL tidak tersedia: kepemilikan lot/fill dan executor belum terverifikasi. Tidak mengirim order.",
            "/balance": "Saldo IDR spendable belum terverifikasi dari private Indodax View Only. Tidak menampilkan saldo perkiraan.",
            "/profit": "Profit bersih realisasi WIB belum terverifikasi dari BUY/SELL fill serta settlement. Tidak menampilkan PnL fiktif.",
            "/risk": "RISK belum mempunyai sumber G1, respons AI dan preflight saldo terverifikasi. Trading tetap OFF.",
            "/health": "Layanan perintah M05 tersedia; kesehatan pasar G1 dan account trading belum diverifikasi. Trading OFF.",
        }
        if command in unavailable and len(parts) == 1:
            return {
                **_blocked("LIVE_DATA_OR_EXECUTOR_NOT_CONNECTED"),
                "text": unavailable[command],
            }
        return _blocked("UNKNOWN_TELEGRAM_COMMAND")

    def handle_callback(self, *, user_id: int, chat_id: int,
                        data: str, now_epoch: int,
                        chat_type: str = "private",
                        telegram_message_id: int | None = None) -> dict[str, Any]:
        return self.ledger.apply_callback(
            user_id=user_id, chat_id=chat_id, callback_data=data,
            now_epoch=now_epoch, chat_type=chat_type,
            telegram_message_id=telegram_message_id,
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
