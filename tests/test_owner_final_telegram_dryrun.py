"""Test the original M05 Bot API poller and owner ledger, with NO network or orders."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_bot_api import TelegramBotApiClient, TelegramOwnerPoller


class OwnerFinalTelegramDryRunTest(TestCase):
    OWNER = 31001
    BOT = 41001
    NOW = 1800000000

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = OwnerDecisionLedger(
            Path(self.temp.name) / "owner.sqlite3", owner_user_id=self.OWNER,
        )

    def create_proposal(self):
        proposal = self.ledger.issue_offline_shadow_proposal(
            pair="inj_idr", digest="a" * 64,
            closed_1m_epoch=self.NOW - 60,
            entry_price_idr="15000", quantity="1",
            now_epoch=self.NOW, source_expires_epoch=self.NOW + 80,
        )
        self.assertTrue(proposal["accepted"])
        self.assertIsNone(proposal["final_decision"])
        self.assertFalse(proposal["execution_allowed"])
        return proposal

    def test_real_poller_path_owner_pass_preserved_no_exchange_order(self):
        proposal = self.create_proposal()
        api_calls = []
        update = {
            "update_id": 1,
            "callback_query": {
                "id": "cb-pass", "from": {"id": self.OWNER},
                "data": proposal["callbacks"]["PASS"],
                "message": {
                    "message_id": 1,
                    "date": self.NOW,
                    "chat": {"id": self.OWNER, "type": "private"},
                    "from": {"id": self.BOT, "is_bot": True},
                },
            },
        }

        def transport(method, payload, timeout):
            api_calls.append((method, payload))
            if method == "getUpdates":
                return {"ok": True, "result": [update]}
            if method == "answerCallbackQuery":
                return {"ok": True, "result": True}
            raise AssertionError("Unexpected method " + method)

        api = TelegramBotApiClient(token="123456:abcdefghijklmnopqrstuvwxyzABCD",
                                   transport=transport)
        result = TelegramOwnerPoller(
            api=api, ledger=self.ledger, clock=lambda: self.NOW + 3,
            expected_bot_id=self.BOT,
        ).poll_once(limit=1)
        self.assertEqual(result["handled"], 1)
        self.assertIsNone(result["final_decision"])
        self.assertFalse(result["execution_allowed"])
        self.assertEqual([x[0] for x in api_calls],
                         ["getUpdates", "answerCallbackQuery"])
        self.assertIn("Owner PASS recorded", api_calls[1][1]["text"])
        duplicate = self.ledger.apply_callback(
            user_id=self.OWNER, chat_id=self.OWNER,
            callback_data=proposal["callbacks"]["PASS"],
            now_epoch=self.NOW + 4,
        )
        self.assertFalse(duplicate["accepted"])
        self.assertIsNone(duplicate["final_decision"])
        self.assertFalse(duplicate["execution_allowed"])

    def test_owner_reject_is_the_actual_final_decision(self):
        proposal = self.create_proposal()
        result = self.ledger.apply_callback(
            user_id=self.OWNER, chat_id=self.OWNER,
            callback_data=proposal["callbacks"]["REJECT"],
            now_epoch=self.NOW + 1,
        )
        self.assertTrue(result["accepted"])
        self.assertEqual(result["owner_decision"], "REJECT")
        self.assertEqual(result["final_decision"], "REJECT")
        self.assertFalse(result["execution_allowed"])

    def test_owner_pass_is_not_replaced_by_module_reject(self):
        proposal = self.create_proposal()
        result = self.ledger.apply_callback(
            user_id=self.OWNER, chat_id=self.OWNER,
            callback_data=proposal["callbacks"]["PASS"],
            now_epoch=self.NOW + 1,
        )
        self.assertTrue(result["accepted"])
        self.assertEqual(result["owner_decision"], "PASS")
        self.assertEqual(result["final_decision"], "PASS")
        self.assertFalse(result["execution_allowed"])

    def test_foreign_chat_cannot_decide(self):
        proposal = self.create_proposal()
        result = self.ledger.apply_callback(
            user_id=self.OWNER, chat_id=self.OWNER + 1,
            callback_data=proposal["callbacks"]["PASS"],
            now_epoch=self.NOW + 1,
        )
        self.assertFalse(result["accepted"])
        self.assertIsNone(result["owner_decision"])
        self.assertIsNone(result["final_decision"])
        self.assertFalse(result["execution_allowed"])
