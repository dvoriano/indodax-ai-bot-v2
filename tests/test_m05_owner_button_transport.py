"""One original dedicated M05 owner service: /reviewtest -> inline callback.

Real original owner SQLite, original service tick, synthetic Telegram HTTP only.
Never accesses Indodax, paid AI, actual Telegram, exchange orders or credentials.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_bot_api import TelegramBotApiClient
from indodax_ai_bot.telegram_owner import OfflineTelegramOwnerInterface
from indodax_ai_bot.telegram_service_tick import service_tick_once

BOT = 9241907991
OWNER = 174401
TOKEN = str(BOT) + ":" + "fake_local_test_token_123456789012345678"
TEST_PAIR = "reviewtest_idr"


class OriginalM05ButtonTransportTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        root = Path(t.name)
        state = root / "state"
        state.mkdir(mode=0o700)
        state.chmod(0o700)
        self.db = state / "owner.sqlite3"
        self.ledger = OwnerDecisionLedger(self.db, owner_user_id=OWNER)
        self.db.chmod(0o600)
        keydir = root / "secrets"
        keydir.mkdir(mode=0o700)
        keydir.chmod(0o700)
        self.token = keydir / "token"
        self.token.write_text(TOKEN + "\n")
        self.token.chmod(0o600)
        self.now = int(time.time())
        self.calls = []
        self.incoming = []
        self.sent_buttons = None
        self.env = {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
            "M05_TELEGRAM_TICK_GO": "YES",
            "M05_TELEGRAM_NETWORK_GO": "YES",
            "M05_TELEGRAM_DEDICATED_GO": "YES",
            "M05_TELEGRAM_DEDICATED_BOT_ID": str(BOT),
            "M05_TELEGRAM_OWNER_ID": str(OWNER),
            "M05_TELEGRAM_OWNER_DB": str(self.db),
            "M05_TELEGRAM_TOKEN_FILE": str(self.token),
        }

    def fake_api(self, method, payload):
        self.calls.append((method, dict(payload)))
        if method == "getMe":
            return {"id": BOT, "is_bot": True, "username": "SyntheticOwner"}
        if method == "getWebhookInfo":
            return {"url": "", "pending_update_count": 0}
        if method == "getUpdates":
            return [u for u in self.incoming
                    if u["update_id"] >= payload["offset"]]
        if method == "sendMessage":
            if "reply_markup" in payload:
                self.sent_buttons = payload
            return {
                "message_id": 42 if "reply_markup" in payload else 41,
                "chat": {"id": OWNER, "type": "private"},
            }
        if method == "answerCallbackQuery":
            return True
        raise AssertionError("UNEXPECTED_METHOD")

    def tick(self):
        with patch.object(TelegramBotApiClient, "call", side_effect=self.fake_api):
            return service_tick_once(env=self.env)

    def command(self, name="/reviewtest", *, user=OWNER, chat=OWNER, update=101):
        return {"update_id": update, "message": {
            "from": {"id": user},
            "chat": {"id": chat, "type": "private"},
            "date": self.now, "text": name,
        }}

    def callback(self, data, *, user=OWNER, chat=OWNER, update=102):
        return {"update_id": update, "callback_query": {
            "id": "test-callback-" + str(update),
            "from": {"id": user}, "data": data,
            "message": {
                "message_id": 42, "date": self.now,
                "from": {"id": BOT, "is_bot": True},
                "chat": {"id": chat, "type": "private"},
            },
        }}

    def test_owner_command_to_original_outbox_to_real_callback_caller(self):
        self.incoming = [self.command()]
        first = self.tick()
        self.assertEqual(first["processed"], 1)
        self.assertFalse(first["execution_allowed"])
        self.assertIsNone(self.sent_buttons)
        self.assertEqual(self.ledger.telegram_next_offset(), 102)
        with sqlite3.connect(self.db) as db:
            p = db.execute(
                "SELECT pair,entry_price_idr,quantity,state,execution_allowed "
                "FROM m05_proposals"
            ).fetchone()
            self.assertEqual(p, (TEST_PAIR, "0", "0", "PENDING", 0))

        self.incoming = []
        second = self.tick()
        self.assertEqual(second["processed"], 0)
        self.assertIsNotNone(self.sent_buttons)
        self.assertEqual(self.sent_buttons["chat_id"], OWNER)
        self.assertIn("SIMULASI, BUKAN SINYAL PASAR", self.sent_buttons["text"])
        self.assertIn("Trading OFF", self.sent_buttons["text"])
        keyboard = self.sent_buttons["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([b["text"] for b in keyboard],
                         ["PASS (review only)", "REJECT"])
        cb = keyboard[0]["callback_data"]

        self.incoming = [self.callback(cb)]
        third = self.tick()
        self.assertEqual(third["processed"], 1)
        self.assertFalse(third["execution_allowed"])
        self.assertIn("answerCallbackQuery", [m for m, _ in self.calls])
        self.assertEqual(self.ledger.telegram_next_offset(), 103)
        with sqlite3.connect(self.db) as db:
            row = db.execute(
                "SELECT state,reason,execution_allowed FROM m05_proposals"
            ).fetchone()
            self.assertEqual(row, ("PASS", "OWNER_BUTTON", 0))
            self.assertEqual(db.execute(
                "SELECT delivery_state FROM m05_market_advisory_outbox"
            ).fetchone()[0], "SENT")

        view = OfflineTelegramOwnerInterface(self.ledger)
        market = view.handle_command(
            user_id=OWNER, chat_id=OWNER, text="/market", now_epoch=self.now,
        )["observed_owner_review"]
        hist = view.handle_command(
            user_id=OWNER, chat_id=OWNER, text="/history", now_epoch=self.now,
        )["observed_owner_review"]
        self.assertEqual(market["market_advisories_recorded"], 0)
        self.assertEqual(hist["owner_review_pass_count"], 0)
        self.assertEqual(hist["owner_review_reject_count"], 0)

        self.incoming = [self.callback(
            keyboard[1]["callback_data"], update=103,
        )]
        self.tick()
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT state FROM m05_proposals"
            ).fetchone()[0], "PASS")
        self.assertTrue(all(m in {
            "getMe", "getWebhookInfo", "getUpdates",
            "sendMessage", "answerCallbackQuery",
        } for m, _ in self.calls))

    def test_untrusted_chat_and_nonowner_cannot_queue(self):
        view = OfflineTelegramOwnerInterface(self.ledger)
        for user, chat in ((OWNER + 1, OWNER + 1), (OWNER, -123)):
            result = view.handle_command(
                user_id=user, chat_id=chat, text="/reviewtest",
                now_epoch=self.now,
            )
            self.assertFalse(result["accepted"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m05_proposals"
            ).fetchone()[0], 0)

    def test_manual_required_duplicate_and_timeout_without_send(self):
        view = OfflineTelegramOwnerInterface(self.ledger)
        self.ledger.switch_mode(
            user_id=OWNER, chat_id=OWNER, mode="AUTO", now_epoch=self.now,
        )
        deny = view.handle_command(
            user_id=OWNER, chat_id=OWNER, text="/reviewtest",
            now_epoch=self.now,
        )
        self.assertFalse(deny["accepted"])
        self.assertEqual(deny["reason"], "M05_TEST_REQUIRES_MANUAL_MODE")
        self.ledger.switch_mode(
            user_id=OWNER, chat_id=OWNER, mode="MANUAL",
            now_epoch=self.now + 1,
        )
        accepted = view.handle_command(
            user_id=OWNER, chat_id=OWNER, text="/reviewtest",
            now_epoch=self.now + 2,
        )
        self.assertTrue(accepted["accepted"])
        duplicate = view.handle_command(
            user_id=OWNER, chat_id=OWNER, text="/reviewtest",
            now_epoch=self.now + 2,
        )
        self.assertFalse(duplicate["accepted"])
        self.assertEqual(duplicate["reason"], "M05_TEST_ALREADY_PENDING")
        self.assertIsNone(self.ledger.claim_market_advisory_delivery(
            now_epoch=self.now + 92,
        ))
        self.assertEqual(self.ledger.status(
            user_id=OWNER, chat_id=OWNER,
        )["pending_shadow_proposals"], 0)

    def test_reserved_test_pair_is_not_real_market_candidate(self):
        rejected = self.ledger.issue_market_advisory_proposal(
            pair=TEST_PAIR, digest="a" * 64,
            closed_1m_epoch=1800000000,
            now_epoch=1800000010,
            source_expires_epoch=1800000090,
            setup="PULLBACK",
            market_status="MARKET_UNVERIFIED",
            risk_status="RISK_UNKNOWN",
        )
        self.assertFalse(rejected["accepted"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m05_proposals"
            ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
