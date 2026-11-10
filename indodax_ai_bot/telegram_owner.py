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
        if command in ("/start", "/status") and len(parts) == 1:
            state = self.ledger.status(
                user_id=user_id, chat_id=chat_id, chat_type=chat_type
            )
            return {
                **state,
                "text": (
                    "Indodax V2 M05 OFFLINE SHADOW. Mode "
                    + state["owner_mode"]
                    + ". No executable proposal has been authorized."
                ),
            }
        if command in ("/kontrol", "/control") and len(parts) == 1:
            menu = self.ledger.issue_mode_switch_menu(
                user_id=user_id, chat_id=chat_id,
                now_epoch=now_epoch, chat_type=chat_type,
            )
            if menu.get("accepted") is not True:
                return menu
            return {
                **menu,
                "text": (
                    "Select the V2 bot mode. Current mode: "
                    + menu["owner_mode"]
                    + ".\\nMANUAL: BUY/SELL requires the owner order button."
                    + "\\nAUTO: automated bot execution is permitted only AFTER"
                    + " LIVE, AI Risk, and capital checks are authorized."
                    + "\\nOrders remain OFF. Scanner operation is not"
                    + " disabled by the selected mode."
                ).replace("\\n", "\n"),
            }
        if command in ("/kontrol", "/control") and len(parts) == 2:
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
                    + " saved. AUTO does not enable order execution."
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
                        "M05 SIMULATION: review-only PASS/REJECT buttons "
                        "queued for the next Telegram cycle. "
                        "Valid for up to 90 seconds; not a market signal. "
                        "No AI/G1/balance lookup. Trading OFF."
                    ),
                }
            return {
                **demo,
                "text": (
                    "M05 simulation not queued: "
                    + demo.get("reason", "UNAVAILABLE")
                    + ". No order sent. Trading OFF."
                ),
            }
        # M10-06: ORIGINAL owner-only M05 read-only operations view.
        # Authenticated owner/private-chat gate above MUST run before this.
        # Never enqueue/send a notification, call paid AI or issue a trade.
        if command == "/ops" and len(parts) == 1:
            from .m10_ops_health_readonly import (
                assess_v2_operations_readonly, format_v2_ops_owner_message,
            )
            try:
                review = assess_v2_operations_readonly(now_epoch=now_epoch)
                reply = format_v2_ops_owner_message(review)
            except (OSError, TypeError, ValueError):
                reply = (
                    "M10-06 V2 READ-ONLY operations. STATUS_UNVERIFIED. "
                    "No GO authorization. Trading OFF."
                )
            return {
                **_blocked("M10_OPS_OWNER_READONLY_NO_EXECUTION"),
                "text": reply,
            }
        if command == "/ai" and len(parts) == 1:
            # Only inspect original V2 protected owner configuration. This
            # does not call Sumopod or prove model access/paid GO/G1.
            from .m04_owner_config import (
                owner_provider_source, M04OwnerConfigDenied,
            )
            from .sumopod_provider import inspect_provider_configuration_offline
            import os
            try:
                keys = (
                    "SUMOPOD_API_KEY", "SUMOPOD_MODEL_MARKET",
                    "SUMOPOD_MODEL_RISK", "SUMOPOD_MODEL_STRATEGY",
                )
                if any(key in os.environ for key in keys):
                    source = {key: os.environ.get(key, "") for key in keys}
                    origin = "PROCESS_ENV"
                else:
                    protected = owner_provider_source()
                    source = protected[0] if protected is not None else {}
                    origin = (
                        "V2_PROTECTED_LOCAL_FILE" if protected is not None
                        else "M04_CONFIG_NOT_FOUND"
                    )
                report = inspect_provider_configuration_offline(source)
                # Model IDs are locally schema-validated, but no key value,
                # billing data or raw error is ever put on Telegram.
                models = report["configured_models_by_role"]
                fields = "; ".join(
                    role + "=" + (models.get(role) or "NOT_CONFIGURED")
                    for role in ("MARKET", "RISK", "STRATEGY")
                )
                key_state = (
                    "LOCALLY_CONFIGURED"
                    if report["sumopod_key_present_nonempty"]
                    else "NOT_DETECTED"
                )
                status = report["status"]
                reply = (
                    "M04 AI readiness (READ-ONLY): MARKET, RISK, STRATEGY\\nSource: " + origin
                    + "\\n" + fields
                    + "\\nAPI key: " + key_state
                    + "\\nStatus: " + status
                    + "\\nModel/provider online: UNVERIFIED"
                    + "\\nIndependent G1: UNVERIFIED"
                    + "\\nThree live AI responses: UNVERIFIED"
                    + "\\nPaid AI invoked: NO. Orders: OFF."
                ).replace("\\n", "\n")
            except (M04OwnerConfigDenied, ValueError, OSError):
                reply = (
                    "M04 AI readiness: CONFIGURATION_UNVERIFIED. "
                    "No provider access, paid AI call, or order. OFF."
                )
            return {
                **_blocked("M04_PROVIDER_READINESS_DIAGNOSTIC_ONLY"),
                "text": reply,
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
                    "M05 local market advisories: queued " +
                    str(review["fresh_queued_market_advisories"]) +
                    ", sent and unexpired " +
                    str(review["fresh_sent_market_advisories"]) +
                    ", total recorded " +
                    str(review["market_advisories_recorded"]) + ". "
                    "Independent G1 source is unverified; "
                    "this is NOT a BUY signal. Trading OFF."
                )
            else:
                details = (
                    "M05 owner review history (not exchange trades): "
                    "PASS " + str(review["owner_review_pass_count"]) +
                    ", REJECT " + str(review["owner_review_reject_count"]) +
                    ". Indodax order/fill history is unverified. "
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
            "/scanner": "Scheduled M03 scanner is not confirmed active; /market displays only M05 ledger data. Trading OFF.",

            "/buy": "BUY unavailable: G1, risk, account balance, LIVE permission, and executor are unverified. No order sent.",
            "/positions": "Bot-owned positions are not verified against Indodax fills. No positions or balances are inferred.",
            "/sell": "SELL unavailable: owned lots/fills and executor are unverified. No order sent.",
            "/balance": "Spendable IDR balance is not verified against private Indodax View Only data. No estimated balance shown.",
            "/profit": "Realized net profit (WIB) is not verified against BUY/SELL fills and settlement. No estimated PnL shown.",
            "/risk": "RISK lacks verified G1 source, AI responses, and balance preflight. Trading OFF.",
            "/health": "M05 command receiver available; G1 market health and trading account are unverified. Trading OFF.",
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
        if isinstance(data, str) and data.startswith("m5mode:"):
            return self.ledger.apply_mode_switch_menu_callback(
                user_id=user_id, chat_id=chat_id,
                callback_data=data, now_epoch=now_epoch,
                telegram_message_id=telegram_message_id,
                chat_type=chat_type,
            )
        if isinstance(data, str) and data.startswith("m5t:"):
            return self.ledger.apply_manual_trade_preview_callback(
                user_id=user_id, chat_id=chat_id, callback_data=data,
                now_epoch=now_epoch, chat_type=chat_type,
                telegram_message_id=telegram_message_id,
            )
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
                {"text": "PASS (test)", "callback_data": callbacks["PASS"]},
                {"text": "REJECT", "callback_data": callbacks["REJECT"]},
            ]],
            "network_sent": False,
            "execution_allowed": False,
        }
