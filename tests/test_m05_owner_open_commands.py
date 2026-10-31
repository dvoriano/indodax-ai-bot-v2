"""M05 original V2 owner commands, no made-up account/AI/position claims."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_owner import OfflineTelegramOwnerInterface


OWNER = 123456789
NOW = 1800000140
EXPECTED = {
    "/market": "M05_MARKET_G1_UNVERIFIED",
    "/ai": "M05_PAID_THREE_ROLE_RESULTS_UNAVAILABLE",
    "/buy": "M05_BUY_EXECUTOR_NOT_AUTHORIZED",
    "/positions": "M05_OWNED_POSITION_SOURCE_UNAVAILABLE",
    "/sell": "M05_SELL_EXECUTOR_NOT_AUTHORIZED",
    "/balance": "M05_AUTHENTICATED_BALANCE_UNAVAILABLE",
    "/profit": "M05_SETTLED_IDR_PNL_UNAVAILABLE",
    "/history": "M05_EXCHANGE_TRADE_HISTORY_UNAVAILABLE",
    "/risk": "M05_RISK_SOURCE_NOT_QUALIFIED",
}


class M05OriginalOwnerCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = OwnerDecisionLedger(
            Path(self.temp.name) / "owner.sqlite3", owner_user_id=OWNER
        )
        self.ui = OfflineTelegramOwnerInterface(self.ledger)

    def request(self, command, *, user_id=OWNER, chat_id=OWNER, chat_type="private"):
        return self.ui.handle_command(
            user_id=user_id, chat_id=chat_id, chat_type=chat_type,
            text=command, now_epoch=NOW,
        )

    def test_every_unfinished_command_distinguishes_its_real_v2_missing_source(self):
        for command, reason in EXPECTED.items():
            with self.subTest(command=command):
                response = self.request(command)
                self.assertEqual(response["reason"], reason)
                self.assertEqual(response["final_decision"], "REJECT")
                self.assertFalse(response["accepted"])
                self.assertFalse(response["execution_allowed"])
                self.assertTrue(response["text"].endswith("Final REJECT. Trading OFF."))
                self.assertNotIn("Rp 0", response["text"])
                self.assertNotIn("profit: 0", response["text"].lower())

    def test_foreign_or_group_identity_cannot_read_owner_state(self):
        for command in EXPECTED:
            for caller in (
                {"user_id": 5, "chat_id": 5},
                {"user_id": OWNER, "chat_id": 6},
                {"user_id": OWNER, "chat_id": OWNER, "chat_type": "group"},
            ):
                with self.subTest(command=command, caller=caller):
                    response = self.request(command, **caller)
                    self.assertEqual(response["reason"], "OWNER_NOT_AUTHORIZED")
                    self.assertNotIn("text", response)

    def test_existing_owner_shadow_mode_is_not_trade_authorization(self):
        proposal = self.ledger.issue_offline_shadow_proposal(
            pair="btc_idr", digest="a" * 64,
            closed_1m_epoch=NOW-20, entry_price_idr="100000",
            quantity="1", now_epoch=NOW, source_expires_epoch=NOW+60,
        )
        self.assertTrue(proposal["accepted"])
        reply = self.request("/buy")
        self.assertIn("Proposal shadow tertunda: 1.", reply["text"])
        self.assertIn("Mode: MANUAL.", reply["text"])
        self.assertFalse(reply["execution_allowed"])
        self.assertEqual(self.request("/buy extra")["reason"], "UNKNOWN_TELEGRAM_COMMAND")
        self.ledger.switch_mode(user_id=OWNER, chat_id=OWNER,
                                mode="AUTO", now_epoch=NOW+1)
        response = self.request("/risk")
        self.assertIn("Mode: AUTO.", response["text"])
        self.assertIn("Proposal shadow tertunda: 0.", response["text"])
        self.assertFalse(response["execution_allowed"])

    def test_status_and_kontrol_remain_original_durable_ledger_commands(self):
        self.assertEqual(self.request("/status")["owner_mode"], "MANUAL")
        self.assertTrue(self.request("/kontrol auto")["accepted"])
        self.assertEqual(self.request("/status")["owner_mode"], "AUTO")
        self.assertEqual(self.request("/health bogus")["reason"], "UNKNOWN_TELEGRAM_COMMAND")
        self.assertFalse(self.request("/sell")["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
