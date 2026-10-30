"""M05 dedicated-bot inbound release gate offline tests; NO real Telegram.

All messages and callbacks are fake Bot API envelopes. These tests cannot
prove Telegram credential ownership or delivery on a genuine dedicated bot.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_bot_api import (
    TelegramBotApiClient, TelegramOwnerPoller, TelegramTransportError,
)
from indodax_ai_bot.telegram_dedicated_inbound import (
    LEGACY_CAIPITI_BOT_ID, dedicated_owner_poll_once,
    dedicated_poller_lock,
)

OWNER = 198108814
BOT_ID = 999123456
TOKEN = str(BOT_ID) + ":" + "synthetic_new_dedicated_token_" * 2
NOW = int(time.time())


def owner_msg(update_id=1, *, text="/status", who=OWNER,
              chat=OWNER, chat_type="private", delta=0):
    return {
        "update_id": update_id,
        "message": {
            "from": {"id": who},
            "chat": {"id": chat, "type": chat_type},
            "date": NOW - delta, "text": text,
        },
    }


def callback(update_id=1, *, bot_id=BOT_ID, data="invalid",
             user=OWNER, chat=OWNER):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": "callback-"+str(update_id),
            "data": data,
            "from": {"id": user},
            "message": {
                "message_id": 55,
                "date": NOW - 5,
                "from": {"id": bot_id, "is_bot": True},
                "chat": {"id": chat, "type": "private"},
            },
        },
    }


class FakeTelegram:
    def __init__(self, *, messages=(), bot_id=BOT_ID,
                 webhook_url="", fail=None):
        self.messages = list(messages)
        self.bot_id = bot_id
        self.webhook_url = webhook_url
        self.fail = fail
        self.calls = []

    def __call__(self, method, payload, timeout):
        self.calls.append((method, dict(payload)))
        if method == self.fail:
            raise OSError("SENSITIVE_TRANSPORT_URL_NO_LEAK")
        if method == "getMe":
            result = {"id": self.bot_id, "is_bot": True,
                      "username": "V2DedicatedBot"}
        elif method == "getWebhookInfo":
            result = {"url": self.webhook_url,
                      "pending_update_count": 0}
        elif method == "getUpdates":
            result = [
                x for x in self.messages if x["update_id"] >= payload["offset"]
            ][:payload["limit"]]
        else:
            result = True
        return {"ok": True, "result": result}


class M05DedicatedInboundTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.db = self.tmp / "owner.sqlite3"
        self.main_db = self.tmp / "main.sqlite3"
        self.lock = self.tmp / "owner.sqlite3.dedicated-poller.lock"
        self.ledger = OwnerDecisionLedger(self.db, owner_user_id=OWNER)

    def api(self, fake):
        return TelegramBotApiClient(token=TOKEN, transport=fake)

    def run_poll(self, fake, *, expected=BOT_ID):
        return dedicated_owner_poll_once(
            api=self.api(fake), ledger=self.ledger,
            expected_bot_id=expected, lock_path=self.lock,
        )

    def test_pinned_new_bot_owner_status_two_way_and_durable_cursor(self):
        fake = FakeTelegram(messages=[owner_msg(15)])
        result = self.run_poll(fake)
        self.assertEqual([c[0] for c in fake.calls],
                         ["getMe", "getWebhookInfo", "getUpdates", "sendMessage"])
        self.assertEqual(fake.calls[-1][1]["chat_id"], OWNER)
        self.assertIn("Mode MANUAL", fake.calls[-1][1]["text"])
        self.assertEqual(result["handler"]["next_offset"], 16)
        self.assertTrue(result["local_exclusive_lock"])
        self.assertFalse(result["old_caipiti_bot_used"])
        self.assertFalse(result["human_owner_verified"])
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(result["final_decision"], "REJECT")
        self.assertEqual(self.ledger.telegram_next_offset(), 16)

    def test_mode_auto_command_is_persisted_but_not_order(self):
        fake = FakeTelegram(messages=[owner_msg(15, text="/kontrol auto")])
        result = self.run_poll(fake)
        self.assertEqual(self.ledger.status(
            user_id=OWNER, chat_id=OWNER
        )["owner_mode"], "AUTO")
        self.assertIn("AUTO tidak mengaktifkan order", fake.calls[-1][1]["text"])
        self.assertFalse(result["execution_allowed"])

    def test_old_caipiti_id_denied_before_any_api_call(self):
        fake = FakeTelegram()
        with self.assertRaisesRegex(
                TelegramTransportError, "EXISTING_CAIPITI_DENIED"):
            self.run_poll(fake, expected=LEGACY_CAIPITI_BOT_ID)
        self.assertEqual(fake.calls, [])

    def test_wrong_getme_id_blocks_webhook_and_updates(self):
        fake = FakeTelegram(bot_id=BOT_ID+1)
        with self.assertRaisesRegex(
                TelegramTransportError, "BOT_ID_MISMATCH"):
            self.run_poll(fake)
        self.assertEqual([c[0] for c in fake.calls], ["getMe"])

    def test_webhook_present_denied_before_getupdates(self):
        fake = FakeTelegram(webhook_url="https://secret.example/" + TOKEN)
        with self.assertRaisesRegex(
                TelegramTransportError, "DEDICATED_WEBHOOK_PRESENT"):
            self.run_poll(fake)
        self.assertEqual([c[0] for c in fake.calls],
                         ["getMe", "getWebhookInfo"])

    def test_other_local_poller_lock_denies_without_api_calls(self):
        fake = FakeTelegram()
        with dedicated_poller_lock(self.lock):
            with self.assertRaisesRegex(
                    TelegramTransportError, "ALREADY_RUNNING"):
                self.run_poll(fake)
        self.assertEqual(fake.calls, [])

    def test_lock_file_insecure_mode_symlink_and_nonabsolute_path_denied(self):
        self.lock.touch(mode=0o644)
        self.lock.chmod(0o644)
        fake = FakeTelegram()
        with self.assertRaisesRegex(
                TelegramTransportError, "LOCK_UNSAFE"):
            self.run_poll(fake)
        self.assertEqual(fake.calls, [])
        self.lock.unlink()
        target = self.tmp / "target"
        target.write_text("safe")
        self.lock.symlink_to(target)
        with self.assertRaisesRegex(
                TelegramTransportError, "LOCK_IO_FAILED"):
            self.run_poll(fake)
        self.assertEqual(fake.calls, [])
        with self.assertRaisesRegex(
                TelegramTransportError, "LOCK_PATH_INVALID"):
            dedicated_owner_poll_once(
                api=self.api(fake), ledger=self.ledger,
                expected_bot_id=BOT_ID,
                lock_path="relative.lock",
            )

    def test_wrong_bot_origin_in_callback_ignored_without_ack(self):
        fake = FakeTelegram(messages=[callback(7, bot_id=BOT_ID+1)])
        result = self.run_poll(fake)
        self.assertEqual([c[0] for c in fake.calls],
                         ["getMe", "getWebhookInfo", "getUpdates"])
        self.assertEqual(result["handler"]["handled"], 1)
        self.assertEqual(self.ledger.telegram_next_offset(), 8)
        self.assertFalse(result["execution_allowed"])

    def test_correct_bot_origin_callback_rejected_safely_and_acknowledged(self):
        fake = FakeTelegram(messages=[callback(7)])
        result = self.run_poll(fake)
        self.assertEqual([c[0] for c in fake.calls],
                         ["getMe", "getWebhookInfo", "getUpdates",
                          "answerCallbackQuery"])
        self.assertEqual(self.ledger.telegram_next_offset(), 8)
        self.assertEqual(result["final_decision"], "REJECT")
        self.assertFalse(result["execution_allowed"])

    def test_foreign_chat_and_group_get_no_reply(self):
        fake = FakeTelegram(messages=[
            owner_msg(1, who=OWNER+1, chat=OWNER+1),
            owner_msg(2, chat=-444, chat_type="group"),
        ])
        result = self.run_poll(fake)
        self.assertEqual([c[0] for c in fake.calls],
                         ["getMe", "getWebhookInfo", "getUpdates"])
        self.assertEqual(result["handler"]["next_offset"], 3)

    def test_stale_command_no_reply_and_no_mode_change(self):
        fake = FakeTelegram(messages=[
            owner_msg(5, text="/kontrol auto", delta=130),
        ])
        result = self.run_poll(fake)
        self.assertEqual([c[0] for c in fake.calls],
                         ["getMe", "getWebhookInfo", "getUpdates"])
        self.assertEqual(self.ledger.status(
            user_id=OWNER, chat_id=OWNER,
        )["owner_mode"], "MANUAL")
        self.assertEqual(result["handler"]["next_offset"], 6)

    def test_restart_dedup_does_not_repeat_owner_status(self):
        fake = FakeTelegram(messages=[owner_msg(20)])
        self.run_poll(fake)
        second_fake = FakeTelegram(messages=[owner_msg(20)])
        second = self.run_poll(second_fake)
        self.assertEqual([c[0] for c in second_fake.calls],
                         ["getMe", "getWebhookInfo", "getUpdates"])
        self.assertEqual(second_fake.calls[-1][1]["offset"], 21)
        self.assertEqual(second["handler"]["handled"], 0)

    def test_preflight_transport_failure_raises_redacted_code(self):
        fake = FakeTelegram(fail="getMe")
        with self.assertRaises(TelegramTransportError) as caught:
            self.run_poll(fake)
        self.assertEqual(str(caught.exception),
                         "M05_TELEGRAM_API_TRANSPORT_FAILED")
        self.assertNotIn("SENSITIVE_TRANSPORT_URL", str(caught.exception))
        self.assertEqual([c[0] for c in fake.calls], ["getMe"])

    def test_invalid_parameters_deny_without_api(self):
        for bot_id in (None, False, True, 0, -1, str(BOT_ID)):
            fake = FakeTelegram()
            with self.subTest(bot_id=bot_id), self.assertRaises(ValueError):
                self.run_poll(fake, expected=bot_id)
            self.assertEqual(fake.calls, [])

    def test_owner_reply_failure_does_not_retry_processed_update(self):
        fake = FakeTelegram(messages=[owner_msg(15)], fail="sendMessage")
        with self.assertRaisesRegex(
                TelegramTransportError, "API_TRANSPORT_FAILED"):
            self.run_poll(fake)
        self.assertEqual(self.ledger.telegram_next_offset(), 16)
        fresh = FakeTelegram(messages=[owner_msg(15)])
        self.run_poll(fresh)
        self.assertEqual([x[0] for x in fresh.calls],
                         ["getMe", "getWebhookInfo", "getUpdates"])

    def cli_args(self):
        return [
            "--db", str(self.main_db),
            "--m05-telegram-dedicated-poll-once",
            "--m05-shadow-db", str(self.db),
            "--m05-shadow-owner-id", str(OWNER),
        ]

    def cli_env(self, **extra):
        return {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
            "M05_TELEGRAM_NETWORK_GO": "YES",
            "M05_TELEGRAM_DEDICATED_GO": "YES",
            "M05_TELEGRAM_DEDICATED_BOT_ID": str(BOT_ID),
            "M05_TELEGRAM_BOT_TOKEN": TOKEN,
            **extra,
        }

    def test_cli_no_network_or_dedicated_go_denies_before_api(self):
        for env in (
            self.cli_env(M05_TELEGRAM_NETWORK_GO="NO"),
            self.cli_env(M05_TELEGRAM_DEDICATED_GO="NO"),
        ):
            out, err = StringIO(), StringIO()
            with patch.dict(os.environ, env, clear=True), patch.object(
                    TelegramBotApiClient, "call",
                    side_effect=AssertionError("NO_NET")):
                with redirect_stdout(out), redirect_stderr(err):
                    rc = main(self.cli_args())
            self.assertEqual(rc, 2)
            self.assertIn("_GO_REQUIRED", err.getvalue())
            self.assertEqual(out.getvalue(), "")

    def test_cli_legacy_bot_and_token_mismatch_denied_pre_network(self):
        for env, reason in (
            (self.cli_env(M05_TELEGRAM_DEDICATED_BOT_ID=str(LEGACY_CAIPITI_BOT_ID)),
             "EXISTING_CAIPITI_DENIED"),
            (self.cli_env(M05_TELEGRAM_BOT_TOKEN="8827061554:" + "legacy_fake_secret_"*2),
             "TOKEN_ID_MISMATCH"),
            (self.cli_env(M05_TELEGRAM_DEDICATED_BOT_ID=""),
             "DEDICATED_BOT_ID_REQUIRED"),
        ):
            out, err = StringIO(), StringIO()
            with self.subTest(reason=reason), patch.dict(
                    os.environ, env, clear=True), patch.object(
                    TelegramBotApiClient, "call",
                    side_effect=AssertionError("NO_NET")):
                with redirect_stdout(out), redirect_stderr(err):
                    rc = main(self.cli_args())
            self.assertEqual(rc, 2)
            self.assertIn(reason, err.getvalue())
            self.assertEqual(out.getvalue(), "")
            self.assertNotIn(TOKEN, err.getvalue())

    def test_cli_mock_two_way_status_only_and_redacted_result(self):
        fake = FakeTelegram(messages=[owner_msg(30)])
        def fake_call(method, payload):
            reply = fake(method, payload, 6)
            return reply["result"]
        out, err = StringIO(), StringIO()
        with patch.dict(os.environ, self.cli_env(), clear=True), patch.object(
                TelegramBotApiClient, "call", side_effect=fake_call):
            with redirect_stdout(out), redirect_stderr(err):
                rc = main(self.cli_args())
        self.assertEqual(rc, 0, err.getvalue())
        self.assertEqual([c[0] for c in fake.calls],
                         ["getMe", "getWebhookInfo", "getUpdates", "sendMessage"])
        data = json.loads(out.getvalue())
        self.assertFalse(data["execution_allowed"])
        self.assertEqual(data["final_decision"], "REJECT")
        self.assertFalse(data["m05_telegram_dedicated_poll"]["human_owner_verified"])
        self.assertNotIn(TOKEN, out.getvalue())
        self.assertNotIn(str(OWNER), out.getvalue())
        self.assertEqual(err.getvalue(), "")

    def test_cli_live_env_never_queries_telegram(self):
        out, err = StringIO(), StringIO()
        with patch.dict(os.environ, self.cli_env(
                INDODAX_ALLOW_LIVE_EXECUTION="YES",
        ), clear=True), patch.object(
                TelegramBotApiClient, "call",
                side_effect=AssertionError("NO_NET")):
            with redirect_stdout(out), redirect_stderr(err):
                rc = main(self.cli_args())
        self.assertEqual(rc, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("M01_LIVE_NOT_IMPLEMENTED", err.getvalue())


if __name__ == "__main__":
    unittest.main()
