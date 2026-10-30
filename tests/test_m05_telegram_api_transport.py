"""M05 Bot API one-shot polling boundary, completely OFFLINE mock transport.

No Telegram network, owner credential, Indodax private request, paid AI or
orders. Test Telegram envelopes are fake and do not prove live identity.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_bot_api import (
    TelegramBotApiClient, TelegramOwnerPoller, TelegramTransportError,
)

OWNER = 123456789
NOW = 1800000140
TOKEN = "123456:" + ("synthetic_private_test_token_" * 2)


def message(update_id, text="/status", *, user_id=OWNER, chat_id=OWNER,
            chat_type="private", date=NOW):
    return {
        "update_id": update_id, "message": {
            "from": {"id": user_id},
            "chat": {"id": chat_id, "type": chat_type},
            "date": date, "text": text,
        },
    }


def callback(update_id, callback_id, data, *, user_id=OWNER,
             chat_id=OWNER, chat_type="private"):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": callback_id, "from": {"id": user_id},
            "data": data,
            "message": {
                "message_id": 777, "date": NOW - 10,
                "from": {"id": 999999888, "is_bot": True},
                "chat": {"id": chat_id, "type": chat_type},
            },
        },
    }


class FakeBotApi:
    def __init__(self, updates=None, *, fail_on=None):
        self.updates = list(updates or [])
        self.fail_on = fail_on
        self.calls = []

    def __call__(self, method, payload, timeout):
        self.calls.append((method, dict(payload), timeout))
        if method == self.fail_on:
            raise OSError("SECRET_OR_UPSTREAM_EXCEPTION_NEVER_EXPOSE")
        if method == "getUpdates":
            # Simulate Bot API offset acknowledging past updates.
            data = [x for x in self.updates
                    if x["update_id"] >= payload["offset"]]
            return {"ok": True, "result": data[:payload["limit"]]}
        return {"ok": True, "result": True}


class M05TelegramApiTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owner_db = Path(self.tmp.name) / "owner.sqlite3"
        self.ledger = OwnerDecisionLedger(self.owner_db, owner_user_id=OWNER)

    def poller(self, fake):
        api = TelegramBotApiClient(
            token=TOKEN, transport=fake, timeout_seconds=3,
        )
        return TelegramOwnerPoller(
            api=api, ledger=self.ledger, clock=lambda: NOW,
        )

    def proposal(self, *, source_expires_epoch=NOW + 25):
        return self.ledger.issue_offline_shadow_proposal(
            pair="btc_idr", digest="a"*64,
            closed_1m_epoch=NOW-20,
            entry_price_idr="100000", quantity="0.2",
            now_epoch=NOW,
            source_expires_epoch=source_expires_epoch,
        )

    def test_real_network_disabled_by_default_no_secret_in_representation(self):
        api = TelegramBotApiClient(token=TOKEN)
        self.assertNotIn(TOKEN, repr(api))
        self.assertIn("REDACTED", repr(api))
        with self.assertRaisesRegex(
                TelegramTransportError, "M05_TELEGRAM_NETWORK_DISABLED"):
            api.call("getUpdates", {"offset": 0})
        with self.assertRaisesRegex(
                TelegramTransportError, "M05_TELEGRAM_METHOD_DENIED"):
            api.call("deleteWebhook", {})

    def test_owner_private_status_from_bot_api_and_durable_offset(self):
        fake = FakeBotApi([message(100, "/status")])
        report = self.poller(fake).poll_once()
        self.assertEqual(report["handled"], 1)
        self.assertEqual(report["final_decision"], "REJECT")
        self.assertFalse(report["execution_allowed"])
        self.assertEqual(report["next_offset"], 101)
        self.assertEqual([call[0] for call in fake.calls],
                         ["getUpdates", "sendMessage"])
        self.assertEqual(fake.calls[0][1]["allowed_updates"],
                         ["message", "callback_query"])
        sent = fake.calls[1][1]
        self.assertEqual(sent["chat_id"], OWNER)
        self.assertIn("MANUAL", sent["text"])
        restarted = OwnerDecisionLedger(self.owner_db, owner_user_id=OWNER)
        self.assertEqual(restarted.telegram_next_offset(), 101)
        self.assertEqual(self.poller(fake).poll_once()["handled"], 0)

    def test_non_owner_or_group_or_spoofed_identity_never_reaches_ledger(self):
        fake = FakeBotApi([
            message(1, "/kontrol auto", user_id=1, chat_id=1),
            message(2, "/kontrol auto", user_id=OWNER, chat_id=1),
            message(3, "/kontrol auto", chat_type="group"),
        ])
        result = self.poller(fake).poll_once()
        self.assertEqual(result["handled"], 3)
        self.assertEqual(result["next_offset"], 4)
        self.assertEqual([c[0] for c in fake.calls], ["getUpdates"])
        self.assertEqual(self.ledger.status(
            user_id=OWNER, chat_id=OWNER)["owner_mode"], "MANUAL")
        self.assertEqual(self.ledger.audit_count(), 0)

    def test_stale_owner_command_cannot_activate_auto(self):
        fake = FakeBotApi([message(5, "/kontrol auto", date=NOW-91)])
        self.poller(fake).poll_once()
        self.assertEqual(self.ledger.status(
            user_id=OWNER, chat_id=OWNER)["owner_mode"], "MANUAL")
        self.assertEqual(len(fake.calls), 1)

    def test_owner_auto_switch_via_mock_telegram_persists_but_never_trades(self):
        fake = FakeBotApi([message(17, "/kontrol auto")])
        self.poller(fake).poll_once()
        self.assertEqual(OwnerDecisionLedger(
            self.owner_db, owner_user_id=OWNER
        ).status(user_id=OWNER, chat_id=OWNER)["owner_mode"], "AUTO")
        self.assertEqual(self.ledger.audit_count(), 1)
        self.assertIn("AUTO tidak mengaktifkan order",
                      fake.calls[-1][1]["text"])
        self.assertEqual(self.ledger.telegram_next_offset(), 18)

    def test_callback_pass_through_real_owner_envelope_stays_final_reject(self):
        p = self.proposal()
        fake = FakeBotApi([callback(200, "cq-pass", p["callbacks"]["PASS"])])
        report = self.poller(fake).poll_once()
        self.assertEqual(report["next_offset"], 201)
        self.assertEqual(report["final_decision"], "REJECT")
        self.assertFalse(report["execution_allowed"])
        self.assertEqual([c[0] for c in fake.calls],
                         ["getUpdates", "answerCallbackQuery"])
        self.assertEqual(fake.calls[-1][1]["callback_query_id"], "cq-pass")
        self.assertEqual(self.ledger.audit_count(), 2)
        replay = self.ledger.apply_callback(
            user_id=OWNER, chat_id=OWNER,
            callback_data=p["callbacks"]["REJECT"], now_epoch=NOW+1,
        )
        self.assertEqual(replay["reason"], "CALLBACK_ALREADY_CONSUMED")

    def test_duplicate_callback_id_across_different_updates_is_not_replayed(self):
        p = self.proposal()
        fake = FakeBotApi([
            callback(1, "cq-once", p["callbacks"]["PASS"]),
            callback(2, "cq-once", p["callbacks"]["REJECT"]),
        ])
        result = self.poller(fake).poll_once()
        self.assertEqual(result["handled"], 1)
        self.assertEqual(result["ignored"], 1)
        self.assertEqual(result["next_offset"], 3)
        self.assertEqual(sum(c[0]=="answerCallbackQuery" for c in fake.calls), 1)
        self.assertEqual(self.ledger.audit_count(), 2)

    def test_expired_callback_acks_without_reopening_trade(self):
        p = self.proposal(source_expires_epoch=NOW+1)
        fake = FakeBotApi([callback(8, "cq-expired", p["callbacks"]["PASS"])])
        poller = TelegramOwnerPoller(
            api=TelegramBotApiClient(token=TOKEN, transport=fake),
            ledger=self.ledger, clock=lambda: NOW+1,
        )
        out = poller.poll_once()
        self.assertEqual(out["final_decision"], "REJECT")
        self.assertFalse(out["execution_allowed"])
        self.assertEqual(fake.calls[-1][0], "answerCallbackQuery")
        self.assertEqual(self.ledger.telegram_next_offset(), 9)

    def test_callback_from_other_chat_or_inaccessible_message_is_rejected(self):
        p = self.proposal()
        broken = callback(6, "cq-wrong", p["callbacks"]["PASS"], chat_id=10)
        no_message = callback(7, "cq-no-message", p["callbacks"]["PASS"])
        del no_message["callback_query"]["message"]
        fake = FakeBotApi([broken, no_message])
        self.poller(fake).poll_once()
        self.assertEqual([c[0] for c in fake.calls], ["getUpdates"])
        self.assertEqual(self.ledger.audit_count(), 1)
        self.assertEqual(self.ledger.telegram_next_offset(), 8)

    def test_network_failure_does_not_advance_cursor_or_leak_error_detail(self):
        fake = FakeBotApi([message(33)], fail_on="getUpdates")
        with self.assertRaises(TelegramTransportError) as error:
            self.poller(fake).poll_once()
        self.assertEqual(str(error.exception),
                         "M05_TELEGRAM_API_TRANSPORT_FAILED")
        self.assertEqual(self.ledger.telegram_next_offset(), 0)
        self.assertNotIn(TOKEN, str(error.exception))
        self.assertNotIn("SECRET_OR_UPSTREAM", str(error.exception))

    def test_reply_failure_keeps_mode_change_consumed_and_never_replays(self):
        fake = FakeBotApi([message(43, "/kontrol auto")],
                          fail_on="sendMessage")
        with self.assertRaisesRegex(
                TelegramTransportError, "M05_TELEGRAM_API_TRANSPORT_FAILED"):
            self.poller(fake).poll_once()
        self.assertEqual(self.ledger.telegram_next_offset(), 44)
        self.assertEqual(self.ledger.status(
            user_id=OWNER, chat_id=OWNER)["owner_mode"], "AUTO")
        self.assertEqual(self.ledger.audit_count(), 1)
        self.assertEqual(self.poller(fake).poll_once()["handled"], 0)
        self.assertEqual(self.ledger.audit_count(), 1)

    def test_callback_ack_failure_never_reruns_consumed_consent(self):
        p = self.proposal()
        fake = FakeBotApi(
            [callback(50, "cq-ack", p["callbacks"]["PASS"])],
            fail_on="answerCallbackQuery",
        )
        with self.assertRaises(TelegramTransportError):
            self.poller(fake).poll_once()
        self.assertEqual(self.ledger.telegram_next_offset(), 51)
        self.assertEqual(self.ledger.audit_count(), 2)
        self.assertEqual(self.poller(fake).poll_once()["handled"], 0)
        self.assertEqual(self.ledger.audit_count(), 2)

    def test_incomplete_claim_after_crash_blocks_retry_for_operator_review(self):
        self.assertEqual(self.ledger.claim_telegram_update(
            update_id=77, callback_query_id=None, now_epoch=NOW
        ), "CLAIMED")
        fake = FakeBotApi([message(77, "/kontrol auto")])
        with self.assertRaisesRegex(
                TelegramTransportError, "INCOMPLETE_REVIEW_REQUIRED"):
            self.poller(fake).poll_once()
        self.assertEqual(self.ledger.telegram_update_status(77), "CLAIMED")
        self.assertEqual(self.ledger.telegram_next_offset(), 0)
        self.assertEqual(self.ledger.audit_count(), 0)

    def test_malformed_duplicate_or_oversized_batches_rejected_pre_dispatch(self):
        for updates in (
            [{"update_id": 1}, {"update_id": 1}],
            [{"update_id": 2}, {"update_id": 1}],
            [{"update_id": True}],
        ):
            with self.subTest(updates=updates):
                fake = FakeBotApi(updates)
                with self.assertRaises(TelegramTransportError):
                    self.poller(fake).poll_once()
                self.assertEqual(self.ledger.telegram_next_offset(), 0)
        with self.assertRaisesRegex(ValueError, "INVALID_M05_POLL_LIMIT"):
            self.poller(FakeBotApi()).poll_once(limit=100)

    def test_unsupported_update_ignored_and_not_sent_to_chat(self):
        fake = FakeBotApi([{"update_id": 4, "my_chat_member": {"id": 100}}])
        out = self.poller(fake).poll_once()
        self.assertEqual(out["handled"], 1)
        self.assertEqual(self.ledger.telegram_next_offset(), 5)
        self.assertEqual(len(fake.calls), 1)

    def test_bot_api_negative_response_is_sanitized_without_description(self):
        class Refused:
            def __call__(self, method, payload, timeout):
                return {"ok": False, "description": "secret " + TOKEN}
        with self.assertRaises(TelegramTransportError) as ctx:
            self.poller(Refused()).poll_once()
        self.assertEqual(str(ctx.exception), "M05_TELEGRAM_API_REJECTED")
        self.assertNotIn(TOKEN, str(ctx.exception))
        self.assertEqual(self.ledger.telegram_next_offset(), 0)

    def test_callback_inaccessible_message_and_non_bot_sender_never_consume(self):
        p = self.proposal()
        inaccessible = callback(1, "cq-old", p["callbacks"]["PASS"])
        inaccessible["callback_query"]["message"]["date"] = 0
        fake_author = callback(2, "cq-fake", p["callbacks"]["PASS"])
        fake_author["callback_query"]["message"]["from"]["is_bot"] = False
        fake = FakeBotApi([inaccessible, fake_author])
        out = self.poller(fake).poll_once()
        self.assertEqual(out["next_offset"], 3)
        self.assertEqual([c[0] for c in fake.calls], ["getUpdates"])
        self.assertEqual(self.ledger.audit_count(), 1)
        later = self.ledger.apply_callback(
            user_id=OWNER, chat_id=OWNER,
            callback_data=p["callbacks"]["REJECT"], now_epoch=NOW+1,
        )
        self.assertTrue(later["accepted"])

    def test_mock_cli_integration_and_explicit_network_owner_go(self):
        from indodax_ai_bot.telegram_bot_api import TelegramBotApiClient
        args = [
            "--db", str(Path(self.tmp.name)/"main.sqlite3"),
            "--m05-telegram-poll-once",
            "--m05-shadow-db", str(self.owner_db),
            "--m05-shadow-owner-id", str(OWNER),
        ]
        output, errors = StringIO(), StringIO()
        with patch.dict(os.environ, {
                "M05_TELEGRAM_NETWORK_GO": "NO",
                "M05_TELEGRAM_BOT_TOKEN": TOKEN,
                "INDODAX_TRADING_ENABLED": "NO",
                "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
                "INDODAX_EXECUTION_MODE": "DRY_RUN",
        }), patch.object(TelegramBotApiClient, "call",
                         side_effect=AssertionError("MUST_NOT_CALL")):
            with redirect_stdout(output), redirect_stderr(errors):
                code = main(args)
        self.assertEqual(code, 2)
        self.assertIn("M05_TELEGRAM_NETWORK_GO_REQUIRED", errors.getvalue())
        self.assertNotIn(TOKEN, errors.getvalue())
        output, errors = StringIO(), StringIO()
        with patch.dict(os.environ, {
                "M05_TELEGRAM_NETWORK_GO": "YES",
                "M05_TELEGRAM_BOT_TOKEN": TOKEN,
                "INDODAX_TRADING_ENABLED": "NO",
                "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
                "INDODAX_EXECUTION_MODE": "DRY_RUN",
        }), patch.object(TelegramBotApiClient, "call",
                         return_value=[]):
            with redirect_stdout(output), redirect_stderr(errors):
                code = main(args)
        self.assertEqual(code, 0, errors.getvalue())
        result = json.loads(output.getvalue())
        self.assertEqual(result["final_decision"], "REJECT")
        self.assertEqual(result["m05_telegram_poll"]["next_offset"], 0)
        self.assertFalse(result["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
