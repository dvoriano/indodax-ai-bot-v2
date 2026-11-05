"""Original V2 M05 private MANUAL/AUTO inline control (NONLIVE).

Telegram is a fake Bot API. The original OwnerDecisionLedger, poller, and
cursor are real SQLite. No Indodax, Sumopod, or execution transport.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_bot_api import (
    TelegramBotApiClient, TelegramOwnerPoller, TelegramTransportError,
)

OWNER = 27511008
BOT = 9876543210
NOW = 1800000140


class OriginalM05OwnerModeInlineTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix="m05-inline-mode-")
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "original-owner.sqlite3"
        self.owner = OwnerDecisionLedger(self.db, owner_user_id=OWNER)
        self.sent = []
        self.calls = []
        self.updates = []
        self.epoch = NOW
        self.invalid_delivery = False
        self.api = TelegramBotApiClient(
            token=str(BOT) + ":" + "X" * 30,
            transport=self.telegram,
        )
        self.poller = TelegramOwnerPoller(
            api=self.api, ledger=self.owner,
            expected_bot_id=BOT, clock=lambda: self.epoch,
        )

    def telegram(self, method, payload, timeout):
        self.calls.append((method, payload))
        if method == "getUpdates":
            data = [x for x in self.updates
                    if x["update_id"] >= payload["offset"]]
        elif method == "sendMessage":
            self.sent.append(payload)
            data = {
                "message_id": (
                    0 if self.invalid_delivery else 700 + len(self.sent)
                ),
                "chat": {"id": OWNER, "type": "private"},
            }
        elif method in ("answerCallbackQuery", "editMessageReplyMarkup"):
            data = True
        else:
            raise AssertionError("UNEXPECTED_TELEGRAM_METHOD")
        return {"ok": True, "result": data}

    def command(self, *, update_id, text="/kontrol",
                user=OWNER, chat=OWNER, chat_type="private"):
        self.updates.append({
            "update_id": update_id,
            "message": {
                "from": {"id": user},
                "chat": {"id": chat, "type": chat_type},
                "date": self.epoch, "text": text,
            },
        })
        return self.poller.poll_once()

    def choose(self, *, update_id, button, sent_at=None,
               message_id=None, user=OWNER, origin=BOT):
        self.updates.append({
            "update_id": update_id,
            "callback_query": {
                "id": f"inline-choice-{update_id}",
                "from": {"id": user},
                "data": button,
                "message": {
                    "message_id": message_id if message_id is not None
                    else 700 + 1,
                    "date": self.epoch if sent_at is None else sent_at,
                    "from": {"id": origin, "is_bot": True},
                    "chat": {"id": OWNER, "type": "private"},
                },
            },
        })
        return self.poller.poll_once()

    def get_buttons(self):
        payload = self.sent[0]
        self.assertEqual(payload["chat_id"], OWNER)
        buttons = payload["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([x["text"] for x in buttons],
                         ["MANUAL", "AUTO"])
        for button in buttons:
            self.assertLessEqual(
                len(button["callback_data"].encode("utf-8")), 64)
        return buttons[0]["callback_data"], buttons[1]["callback_data"]

    def mode(self):
        return self.owner.status(
            user_id=OWNER, chat_id=OWNER)["owner_mode"]

    def test_private_kontrol_button_can_switch_auto_and_manual_without_order(self):
        first = self.command(update_id=10)
        self.assertEqual(first["handled"], 1)
        manual, auto = self.get_buttons()
        self.assertEqual(self.mode(), "MANUAL")
        self.assertIn("order tetap OFF", self.sent[0]["text"])
        self.assertFalse(first["execution_allowed"])
        self.epoch = NOW + 1
        result = self.choose(update_id=11, button=auto)
        self.assertEqual(result["handled"], 1)
        self.assertEqual(self.mode(), "AUTO")
        self.assertIn("Trading tetap OFF", self.calls[-3][1].get("text", "")
                      + self.calls[-2][1].get("text", ""))
        self.assertEqual(self.sent[-1]["reply_markup"] if
                         "reply_markup" in self.sent[-1] else None, None)
        self.assertFalse(result["execution_allowed"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m05_mode_menus "
                "WHERE chosen_mode='AUTO'").fetchone()[0], 1)
        self.epoch = NOW + 2
        self.command(update_id=12)
        button_manual, _ = self.sent[-1]["reply_markup"]["inline_keyboard"][0]
        self.choose(update_id=13, button=button_manual["callback_data"],
                    message_id=700 + len(self.sent))
        self.assertEqual(self.mode(), "MANUAL")
        self.assertEqual(self.owner.telegram_next_offset(), 14)

    def test_replay_other_button_does_not_flip_mode(self):
        self.command(update_id=1)
        manual, auto = self.get_buttons()
        self.epoch += 1
        self.choose(update_id=2, button=auto)
        self.assertEqual(self.mode(), "AUTO")
        self.epoch += 1
        self.choose(update_id=3, button=manual)
        self.assertEqual(self.mode(), "AUTO")
        self.assertEqual(len([
            x for x in self.calls if x[0] == "editMessageReplyMarkup"
        ]), 1)

    def test_wrong_owner_and_wrong_bot_cannot_use_mode_button(self):
        self.command(update_id=1)
        manual, auto = self.get_buttons()
        self.epoch += 1
        self.choose(update_id=2, button=auto, user=OWNER + 1)
        self.choose(update_id=3, button=auto, origin=BOT + 1)
        self.assertEqual(self.mode(), "MANUAL")
        self.choose(update_id=4, button=auto)
        self.assertEqual(self.mode(), "AUTO")

    def test_expired_menu_wrong_message_and_direct_mode_change_denied(self):
        menu = self.owner.issue_mode_switch_menu(
            user_id=OWNER, chat_id=OWNER, now_epoch=NOW)
        self.owner.confirm_mode_switch_menu_delivery(
            menu_id=menu["menu_id"], message_id=55)
        self.assertEqual(
            self.owner.apply_mode_switch_menu_callback(
                user_id=OWNER, chat_id=OWNER,
                callback_data=menu["callbacks"]["AUTO"],
                now_epoch=NOW + 1, telegram_message_id=56)["reason"],
            "M05_MODE_MENU_NOT_DELIVERED",
        )
        self.assertEqual(
            self.owner.apply_mode_switch_menu_callback(
                user_id=OWNER, chat_id=OWNER,
                callback_data=menu["callbacks"]["AUTO"],
                now_epoch=NOW + 90, telegram_message_id=55)["reason"],
            "M05_MODE_MENU_EXPIRED_OR_CHANGED",
        )
        other = self.owner.issue_mode_switch_menu(
            user_id=OWNER, chat_id=OWNER, now_epoch=NOW + 91)
        self.owner.confirm_mode_switch_menu_delivery(
            menu_id=other["menu_id"], message_id=60)
        self.owner.switch_mode(
            user_id=OWNER, chat_id=OWNER,
            mode="AUTO", now_epoch=NOW + 92)
        self.assertEqual(
            self.owner.apply_mode_switch_menu_callback(
                user_id=OWNER, chat_id=OWNER,
                callback_data=other["callbacks"]["MANUAL"],
                now_epoch=NOW + 93, telegram_message_id=60)["reason"],
            "M05_MODE_MENU_EXPIRED_OR_CHANGED",
        )
        self.assertEqual(self.mode(), "AUTO")

    def test_new_menu_invalidates_old_menu_without_recording_fake_manual(self):
        first = self.owner.issue_mode_switch_menu(
            user_id=OWNER, chat_id=OWNER, now_epoch=NOW)
        self.owner.confirm_mode_switch_menu_delivery(
            menu_id=first["menu_id"], message_id=51)
        second = self.owner.issue_mode_switch_menu(
            user_id=OWNER, chat_id=OWNER, now_epoch=NOW + 1)
        self.owner.confirm_mode_switch_menu_delivery(
            menu_id=second["menu_id"], message_id=52)
        old = self.owner.apply_mode_switch_menu_callback(
            user_id=OWNER, chat_id=OWNER,
            callback_data=first["callbacks"]["AUTO"],
            now_epoch=NOW + 2, telegram_message_id=51)
        self.assertFalse(old["accepted"])
        new = self.owner.apply_mode_switch_menu_callback(
            user_id=OWNER, chat_id=OWNER,
            callback_data=second["callbacks"]["AUTO"],
            now_epoch=NOW + 2, telegram_message_id=52)
        self.assertTrue(new["accepted"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT chosen_mode FROM m05_mode_menus "
                "WHERE menu_id=?", (first["menu_id"],)
            ).fetchone()[0], None)

    def test_concurrent_mode_click_atomic_and_durable(self):
        menu = self.owner.issue_mode_switch_menu(
            user_id=OWNER, chat_id=OWNER, now_epoch=NOW)
        self.owner.confirm_mode_switch_menu_delivery(
            menu_id=menu["menu_id"], message_id=98)
        def click(which):
            other = OwnerDecisionLedger(self.db, owner_user_id=OWNER)
            return other.apply_mode_switch_menu_callback(
                user_id=OWNER, chat_id=OWNER,
                callback_data=menu["callbacks"][which],
                telegram_message_id=98, now_epoch=NOW + 1,
            )
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(click, ("AUTO", "MANUAL")))
        self.assertEqual(sum(x["accepted"] for x in results), 1)
        self.assertEqual(
            OwnerDecisionLedger(self.db, owner_user_id=OWNER)
            .status(user_id=OWNER, chat_id=OWNER)["owner_mode"],
            next(x["owner_mode"] for x in results if x["accepted"]),
        )
        self.assertTrue(all(x["execution_allowed"] is False for x in results))

    def test_uncertain_telegram_menu_send_never_arms_mode(self):
        self.invalid_delivery = True
        with self.assertRaisesRegex(
                TelegramTransportError,
                "M05_MODE_MENU_SEND_UNCERTAIN_NO_RETRY"):
            self.command(update_id=1)
        self.assertEqual(self.owner.telegram_next_offset(), 2)
        self.invalid_delivery = False
        repeated = self.poller.poll_once()
        self.assertEqual(repeated["handled"], 0)
        self.assertEqual(self.mode(), "MANUAL")
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m05_mode_menus WHERE "
                "delivery_state='SENT'").fetchone()[0], 0)

    def test_original_text_mode_commands_remain_available_nonlive(self):
        self.command(update_id=5, text="/kontrol auto")
        self.assertEqual(self.mode(), "AUTO")
        self.assertNotIn("reply_markup", self.sent[-1])
        self.command(update_id=6, text="/kontrol manual")
        self.assertEqual(self.mode(), "MANUAL")
        status = self.command(update_id=7, text="/status")
        self.assertFalse(status["execution_allowed"])
        self.assertNotIn("reply_markup", self.sent[-1])


if __name__ == "__main__":
    unittest.main()
