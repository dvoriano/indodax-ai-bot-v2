"""Offline-only tests for the V2 M05 one-way Telegram owner status route."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_bot_api import (
    TelegramBotApiClient, TelegramTransportError,
)
from indodax_ai_bot.telegram_owner_outbound import send_owner_safe_status_once

TOKEN = "123456:" + "synthetic_owner_status_token_" * 2
BOT_ID = 8827061554
OWNER_ID = 123456789
ME = {"id": BOT_ID, "is_bot": True, "username": "FakeV2Bot"}
SENT = {"message_id": 777, "chat": {"id": OWNER_ID, "type": "private"}}


class FakeBot:
    def __init__(self, *, identity=None, sent=None, error_method=None):
        self.identity = ME.copy() if identity is None else identity
        self.sent = SENT.copy() if sent is None else sent
        self.error_method = error_method
        self.calls = []

    def __call__(self, method, payload, timeout):
        self.calls.append((method, dict(payload)))
        if method == self.error_method:
            return {
                "ok": False,
                "description": "SECRET_INTERNAL_" + TOKEN,
            }
        return {"ok": True, "result":
                self.identity if method == "getMe" else self.sent}


class M05OutboundOwnerStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owner_db = str(Path(self.tmp.name) / "owner.sqlite3")
        self.main_db = str(Path(self.tmp.name) / "main.sqlite3")
        self.ledger = OwnerDecisionLedger(self.owner_db, owner_user_id=OWNER_ID)

    def api(self, fake):
        return TelegramBotApiClient(token=TOKEN, transport=fake)

    def run_send(self, fake, *, expected_id=BOT_ID):
        return send_owner_safe_status_once(
            api=self.api(fake), ledger=self.ledger,
            expected_bot_id=expected_id,
        )

    def cli_args(self):
        return [
            "--db", self.main_db,
            "--m05-shadow-db", self.owner_db,
            "--m05-shadow-owner-id", str(OWNER_ID),
            "--m05-telegram-owner-safe-status-once",
        ]

    def cli_env(self, **changes):
        return {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
            "M05_TELEGRAM_OWNER_STATUS_GO": "YES",
            "M05_TELEGRAM_EXPECTED_BOT_ID": str(BOT_ID),
            "M05_TELEGRAM_BOT_TOKEN": TOKEN,
            **changes,
        }

    def test_single_status_only_getme_sendmessage_no_poll(self):
        fake = FakeBot()
        res = self.run_send(fake)
        self.assertEqual([x[0] for x in fake.calls], ["getMe", "sendMessage"])
        self.assertEqual(fake.calls[0][1], {})
        payload = fake.calls[1][1]
        self.assertEqual(payload["chat_id"], OWNER_ID)
        self.assertTrue(payload["disable_notification"])
        self.assertIn("Indodax AI Bot V2", payload["text"])
        self.assertIn("Mode owner: MANUAL", payload["text"])
        self.assertIn("Eksekusi trading: OFF", payload["text"])
        self.assertNotIn(TOKEN, json.dumps(res))
        self.assertEqual(res["messages_sent"], 1)
        self.assertEqual(res["updates_fetched"], 0)
        self.assertEqual(res["callbacks_processed"], 0)
        self.assertFalse(res["execution_allowed"])
        self.assertFalse(res["inbound_polling_authorized"])
        self.assertEqual(res["final_decision"], "REJECT")

    def test_existing_auto_owner_mode_still_cannot_trade(self):
        updated = self.ledger.switch_mode(
            user_id=OWNER_ID, chat_id=OWNER_ID, mode="AUTO",
            now_epoch=1800000000,
        )
        self.assertFalse(updated["execution_allowed"])
        fake = FakeBot()
        result = self.run_send(fake)
        self.assertIn("Mode owner: AUTO", fake.calls[1][1]["text"])
        self.assertEqual(result["owner_mode"], "AUTO")
        self.assertFalse(result["execution_allowed"])

    def test_mismatched_pinned_bot_id_blocks_before_message(self):
        fake = FakeBot(identity={"id": BOT_ID + 1, "is_bot": True})
        with self.assertRaisesRegex(
                TelegramTransportError, "BOT_ID_MISMATCH"):
            self.run_send(fake)
        self.assertEqual([x[0] for x in fake.calls], ["getMe"])

    def test_invalid_bot_identity_blocks_before_message(self):
        for identity in (
            {"id": BOT_ID, "is_bot": False}, {"id": True, "is_bot": True},
            {"id": 0, "is_bot": True}, {},
        ):
            with self.subTest(identity=identity):
                fake = FakeBot(identity=identity)
                with self.assertRaises(TelegramTransportError):
                    self.run_send(fake)
                self.assertEqual([x[0] for x in fake.calls], ["getMe"])

    def test_invalid_expected_id_blocks_without_network(self):
        for value in (None, True, False, 0, -3, "8827061554", 2.5):
            fake = FakeBot()
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                        ValueError, "EXPECTED_BOT_ID_REQUIRED"):
                    self.run_send(fake, expected_id=value)
                self.assertEqual(fake.calls, [])

    def test_ledger_rejects_malformed_or_executable_state_before_send(self):
        for state in (
            {"accepted": False, "owner_mode": "MANUAL",
             "final_decision": "REJECT", "execution_allowed": False},
            {"accepted": True, "owner_mode": "LIVE",
             "final_decision": "REJECT", "execution_allowed": False},
            {"accepted": True, "owner_mode": "MANUAL",
             "final_decision": "PASS", "execution_allowed": False},
            {"accepted": True, "owner_mode": "MANUAL",
             "final_decision": "REJECT", "execution_allowed": True},
        ):
            fake = FakeBot()
            with self.subTest(state=state), patch.object(
                    self.ledger, "status", return_value=state):
                with self.assertRaisesRegex(
                        TelegramTransportError, "OWNER_STATE_UNSAFE"):
                    self.run_send(fake)
                self.assertEqual([x[0] for x in fake.calls], ["getMe"])

    def test_wrong_chat_or_unconfirmed_message_response_fails_closed(self):
        for sent in (
            {"message_id": 777, "chat": {"id": OWNER_ID + 1, "type": "private"}},
            {"message_id": 777, "chat": {"id": OWNER_ID, "type": "group"}},
            {"message_id": True, "chat": {"id": OWNER_ID, "type": "private"}},
            {"message_id": 0, "chat": {"id": OWNER_ID, "type": "private"}},
            {"message_id": 777, "chat": {}},
            {},
        ):
            fake = FakeBot(sent=sent)
            with self.subTest(sent=sent), self.assertRaisesRegex(
                    TelegramTransportError, "DO_NOT_RETRY"):
                self.run_send(fake)
            self.assertEqual([x[0] for x in fake.calls], ["getMe", "sendMessage"])

    def test_bot_api_negative_send_does_not_leak_token_and_has_no_retry(self):
        fake = FakeBot(error_method="sendMessage")
        with self.assertRaises(TelegramTransportError) as caught:
            self.run_send(fake)
        self.assertEqual(str(caught.exception), "M05_TELEGRAM_API_REJECTED")
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertEqual([x[0] for x in fake.calls], ["getMe", "sendMessage"])

    def test_network_defaults_off_even_for_outbound(self):
        with self.assertRaisesRegex(
                TelegramTransportError, "NETWORK_DISABLED"):
            TelegramBotApiClient(token=TOKEN).call("sendMessage", {
                "chat_id": OWNER_ID, "text": "unsafe"
            })

    def test_cli_without_independent_go_never_invokes_bot(self):
        for env in (
            self.cli_env(M05_TELEGRAM_OWNER_STATUS_GO="NO"),
            self.cli_env(M05_TELEGRAM_OWNER_STATUS_GO=""),
        ):
            with self.subTest(env=env):
                out, err = StringIO(), StringIO()
                with patch.dict(os.environ, env, clear=True), patch.object(
                        TelegramBotApiClient, "call",
                        side_effect=AssertionError("UNAUTHORIZED_NETWORK")):
                    with redirect_stdout(out), redirect_stderr(err):
                        code = main(self.cli_args())
                self.assertEqual(code, 2)
                self.assertIn("M05_TELEGRAM_OWNER_STATUS_GO_REQUIRED",
                              err.getvalue())
                self.assertEqual(out.getvalue(), "")

    def test_cli_pinned_status_sends_once_only_and_redacts_owner_id(self):
        calls = []
        def fake(method, payload):
            calls.append((method, dict(payload)))
            return ME.copy() if method == "getMe" else SENT.copy()
        out, err = StringIO(), StringIO()
        with patch.dict(os.environ, self.cli_env(), clear=True), patch.object(
                TelegramBotApiClient, "call", side_effect=fake):
            with redirect_stdout(out), redirect_stderr(err):
                code = main(self.cli_args())
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual([x[0] for x in calls], ["getMe", "sendMessage"])
        report = json.loads(out.getvalue())
        self.assertFalse(report["execution_allowed"])
        self.assertEqual(report["final_decision"], "REJECT")
        self.assertEqual(report["m05_telegram_owner_outbound"]["messages_sent"], 1)
        self.assertNotIn(TOKEN, out.getvalue())
        self.assertNotIn(str(OWNER_ID), out.getvalue())
        self.assertEqual(err.getvalue(), "")

    def test_cli_invalid_pin_or_live_settings_never_send(self):
        for env in (
            self.cli_env(M05_TELEGRAM_EXPECTED_BOT_ID=""),
            self.cli_env(M05_TELEGRAM_EXPECTED_BOT_ID="９９９９９"),
            self.cli_env(INDODAX_TRADING_ENABLED="YES"),
            self.cli_env(INDODAX_EXECUTION_MODE="LIVE"),
        ):
            out, err = StringIO(), StringIO()
            with self.subTest(env=list(env)), patch.dict(
                    os.environ, env, clear=True), patch.object(
                    TelegramBotApiClient, "call",
                    side_effect=AssertionError("UNAUTHORIZED_NETWORK")):
                with redirect_stdout(out), redirect_stderr(err):
                    code = main(self.cli_args())
            self.assertEqual(code, 2)
            self.assertEqual(out.getvalue(), "")
            self.assertNotIn(TOKEN, err.getvalue())


if __name__ == "__main__":
    unittest.main()
