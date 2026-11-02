"""M05 original-owner command contract, real SQLite + synthetic Bot API ONLY."""
from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_owner import OfflineTelegramOwnerInterface
from indodax_ai_bot.telegram_bot_api import TelegramBotApiClient
from indodax_ai_bot.telegram_service_tick import service_tick_once

OWNER = 5678012
BOT = 9241907991
TOKEN = str(BOT) + ":" + "synthetic_only_token_1234567890123456789"
NOW = 1800000010
CLOSED = 1800000000


class OriginalOwnerCommandTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.state = root / "owner"
        self.state.mkdir(mode=0o700)
        self.state.chmod(0o700)
        self.db = self.state / "owner.sqlite3"
        self.ledger = OwnerDecisionLedger(self.db, owner_user_id=OWNER)
        self.db.chmod(0o600)
        self.ui = OfflineTelegramOwnerInterface(self.ledger)
        self.secrets = root / "secrets"
        self.secrets.mkdir(mode=0o700)
        self.secrets.chmod(0o700)
        self.token_file = self.secrets / "token"
        self.token_file.write_text(TOKEN + "\n")
        self.token_file.chmod(0o600)

    def call_ui(self, cmd, *, owner=OWNER, chat=OWNER, now=NOW):
        return self.ui.handle_command(
            user_id=owner, chat_id=chat, text=cmd,
            now_epoch=now, chat_type="private",
        )

    def queue_one(self):
        proposal = self.ledger.issue_market_advisory_proposal(
            pair="inj_idr", digest="a" * 64,
            closed_1m_epoch=CLOSED, now_epoch=NOW,
            source_expires_epoch=CLOSED + 90,
            setup="PULLBACK_REBOUND_WATCH",
            market_status="M04_UNVERIFIED",
            risk_status="UNVERIFIED_ACCOUNT_AND_G1",
        )
        self.assertTrue(proposal["accepted"])
        return proposal

    def test_market_reports_only_actual_pending_ledger_evidence(self):
        empty = self.call_ui("/market")
        self.assertFalse(empty["accepted"])
        self.assertEqual(empty["observed_owner_review"][
            "market_advisories_recorded"], 0)
        self.assertIn("Trading OFF", empty["text"])
        self.assertNotIn(TOKEN, str(empty))

        p = self.queue_one()
        queued = self.call_ui("/market")
        self.assertEqual(queued["observed_owner_review"][
            "fresh_queued_market_advisories"], 1)
        self.assertEqual(queued["observed_owner_review"][
            "fresh_sent_market_advisories"], 0)

        claim = self.ledger.claim_market_advisory_delivery(now_epoch=NOW + 1)
        self.assertEqual(claim["proposal_id"], p["proposal_id"])
        self.ledger.confirm_market_advisory_delivery(
            proposal_id=p["proposal_id"], message_id=55,
        )
        sent = self.call_ui("/market", now=NOW + 2)
        self.assertEqual(sent["observed_owner_review"][
            "fresh_queued_market_advisories"], 0)
        self.assertEqual(sent["observed_owner_review"][
            "fresh_sent_market_advisories"], 1)

        callback = self.ledger.apply_callback(
            user_id=OWNER, chat_id=OWNER,
            callback_data=claim["pass_callback"], now_epoch=NOW + 3,
        )
        self.assertTrue(callback["accepted"])
        self.assertEqual(callback["final_decision"], "PASS")
        completed = self.call_ui("/market", now=NOW + 4)
        self.assertEqual(completed["observed_owner_review"][
            "fresh_sent_market_advisories"], 0)
        self.assertEqual(completed["observed_owner_review"][
            "market_advisories_recorded"], 1)
        self.assertFalse(completed["observed_owner_review"][
            "market_source_authenticated"])

    def test_history_is_review_not_exchange_trading(self):
        self.queue_one()
        claim = self.ledger.claim_market_advisory_delivery(now_epoch=NOW + 1)
        self.ledger.confirm_market_advisory_delivery(
            proposal_id=claim["proposal_id"], message_id=55,
        )
        self.ledger.apply_callback(
            user_id=OWNER, chat_id=OWNER,
            callback_data=claim["reject_callback"], now_epoch=NOW + 2,
        )
        h = self.call_ui("/history", now=NOW + 4)
        self.assertEqual(h["observed_owner_review"][
            "owner_review_pass_count"], 0)
        self.assertEqual(h["observed_owner_review"][
            "owner_review_reject_count"], 1)
        self.assertIn("bukan transaksi", h["text"])
        self.assertIn("Trading OFF", h["text"])
        self.assertFalse(h["execution_allowed"])
        self.assertIsNone(h["final_decision"])

    def test_all_nine_named_commands_explicit_failclosed(self):
        for command in (
            "/market", "/ai", "/buy", "/positions", "/sell",
            "/balance", "/profit", "/history", "/risk",
        ):
            with self.subTest(command=command):
                reply = self.call_ui(command)
                self.assertFalse(reply["accepted"])
                self.assertEqual(reply["reason"],
                                 "LIVE_DATA_OR_EXECUTOR_NOT_CONNECTED")
                self.assertIsNone(reply["final_decision"])
                self.assertFalse(reply["execution_allowed"])
                self.assertTrue(reply["text"])
                self.assertLess(len(reply["text"]), 1024)
        self.assertIn("saldo", self.call_ui("/balance")["text"].lower())
        self.assertIn("G1", self.call_ui("/ai")["text"])
        self.assertIn("tidak", self.call_ui("/buy")["text"].lower())

    def test_owner_only_private_and_restart_persistence(self):
        self.queue_one()
        foreign = self.call_ui("/market", owner=OWNER + 1, chat=OWNER + 1)
        self.assertFalse(foreign["accepted"])
        self.assertEqual(foreign["reason"], "OWNER_NOT_AUTHORIZED")
        another = OfflineTelegramOwnerInterface(
            OwnerDecisionLedger(self.db, owner_user_id=OWNER)
        )
        res = another.handle_command(
            user_id=OWNER, chat_id=OWNER, text="/market",
            now_epoch=NOW,
        )
        self.assertEqual(res["observed_owner_review"][
            "fresh_queued_market_advisories"], 1)
        self.assertFalse(res["execution_allowed"])

    def test_original_m05_service_tick_sends_one_message_per_real_command(self):
        recorded = []
        clock = int(time.time())
        updates = [
            {"update_id": 610 + i, "message": {
                "from": {"id": OWNER},
                "chat": {"id": OWNER, "type": "private"},
                "date": clock,
                "text": text,
            }}
            for i, text in enumerate(
                ("/market", "/history", "/ai", "/risk", "/balance")
            )
        ]
        def fake_call(_api, method, payload):
            recorded.append((method, dict(payload)))
            if method == "getMe":
                return {"id": BOT, "is_bot": True, "username": "TestV2"}
            if method == "getWebhookInfo":
                return {"url": "", "pending_update_count": 0}
            if method == "getUpdates":
                return [u for u in updates
                        if u["update_id"] >= payload["offset"]]
            if method == "sendMessage":
                return {"message_id": 100 + len(recorded),
                        "chat": {"id": OWNER, "type": "private"}}
            raise AssertionError("UNEXPECTED_METHOD")

        environment = {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
            "M05_TELEGRAM_TICK_GO": "YES",
            "M05_TELEGRAM_NETWORK_GO": "YES",
            "M05_TELEGRAM_DEDICATED_GO": "YES",
            "M05_TELEGRAM_DEDICATED_BOT_ID": str(BOT),
            "M05_TELEGRAM_OWNER_ID": str(OWNER),
            "M05_TELEGRAM_OWNER_DB": str(self.db),
            "M05_TELEGRAM_TOKEN_FILE": str(self.token_file),
        }
        with patch.object(TelegramBotApiClient, "call", new=fake_call):
            result = service_tick_once(env=environment)
        self.assertEqual(result["status"], "M05_TICK_COMPLETED")
        self.assertEqual(result["processed"], 5)
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(
            [x[0] for x in recorded],
            ["getMe", "getWebhookInfo", "getUpdates"] + ["sendMessage"] * 5,
        )
        responses = [p["text"] for method, p in recorded
                     if method == "sendMessage"]
        self.assertIn("M05 market advisory lokal", responses[0])
        self.assertIn("bukan transaksi", responses[1])
        self.assertIn("MARKET, RISK, STRATEGY", responses[2])
        self.assertIn("Trading", responses[3])
        self.assertIn("Saldo IDR", responses[4])
        self.assertEqual(self.ledger.telegram_next_offset(), 615)
        self.assertTrue(all("Trading OFF" in text or
                            "Tidak mengirim order" in text
                            for text in responses if "order" in text))
        recorded.clear()
        with patch.object(TelegramBotApiClient, "call", new=fake_call):
            again = service_tick_once(env=environment)
        self.assertEqual(again["processed"], 0)
        self.assertEqual([x[0] for x in recorded],
                         ["getMe", "getWebhookInfo", "getUpdates"])


if __name__ == "__main__":
    unittest.main()
