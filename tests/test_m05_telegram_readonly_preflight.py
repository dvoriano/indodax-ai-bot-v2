"""M05 strict readonly Telegram connectivity release-gate offline tests.

Fake Bot API only. No secrets, Telegram requests, update consumption, or
exchange/AI calls. 'getMe' authentication is NOT owner identity proof.
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
from indodax_ai_bot.telegram_bot_api import (
    TelegramBotApiClient, TelegramTransportError,
)
from indodax_ai_bot.telegram_readonly_preflight import (
    inspect_telegram_readonly,
)

TOKEN = "123456:" + ("synthetic_readonly_bot_token_" * 2)
BOT_ID = 123456
BOT = {"id": BOT_ID, "is_bot": True, "username": "TestScalpGateBot"}
WEBHOOK_EMPTY = {"url": "", "pending_update_count": 2}


class FakeReadonlyTransport:
    def __init__(self, *, me=None, webhook=None, fail=None):
        self.me = BOT.copy() if me is None else me
        self.webhook = WEBHOOK_EMPTY.copy() if webhook is None else webhook
        self.fail = fail
        self.calls = []

    def __call__(self, method, body, timeout):
        self.calls.append((method, dict(body), timeout))
        if method == self.fail:
            raise OSError("SECRET_NETWORK_STACK_SHOULD_NOT_LEAK")
        result = self.me if method == "getMe" else self.webhook
        return {"ok": True, "result": result}


class M05TelegramReadonlyPreflightTests(unittest.TestCase):
    def client(self, transport):
        return TelegramBotApiClient(
            token=TOKEN, transport=transport, timeout_seconds=2.0,
        )

    def test_unpinned_discovery_diagnostic_but_not_owner_verified(self):
        fake = FakeReadonlyTransport()
        result = inspect_telegram_readonly(self.client(fake))
        self.assertEqual([c[0] for c in fake.calls], ["getMe", "getWebhookInfo"])
        self.assertEqual(result["status"], "BOT_ID_DISCOVERY_ONLY_UNPINNED")
        self.assertFalse(result["bot_id_pinned_and_matched"])
        self.assertEqual(result["bot_id"], BOT_ID)
        self.assertEqual(result["bot_username"], BOT["username"])
        self.assertFalse(result["private_owner_identity_verified"])
        self.assertFalse(result["verified_owner_update_received"])
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(result["updates_fetched"], 0)
        self.assertEqual(result["messages_sent"], 0)
        self.assertEqual(result["callbacks_acknowledged"], 0)

    def test_explicit_matching_bot_id_yields_polling_not_authorized(self):
        fake = FakeReadonlyTransport()
        result = inspect_telegram_readonly(
            self.client(fake), expected_bot_id=BOT_ID,
        )
        self.assertEqual(
            result["status"], "READONLY_PREFLIGHT_OK_POLLING_NOT_AUTHORIZED"
        )
        self.assertTrue(result["bot_id_pinned_and_matched"])
        self.assertTrue(result["polling_compatible_with_webhook_state"])
        self.assertFalse(result["trading_authorized"])
        self.assertEqual(result["final_decision"], "REJECT")
        self.assertEqual(result["pending_update_count"], 2)

    def test_wrong_pinned_identity_stops_before_webhook_info(self):
        fake = FakeReadonlyTransport()
        with self.assertRaisesRegex(
                TelegramTransportError, "M05_PREFLIGHT_BOT_ID_MISMATCH"):
            inspect_telegram_readonly(
                self.client(fake), expected_bot_id=BOT_ID+1,
            )
        self.assertEqual([c[0] for c in fake.calls], ["getMe"])

    def test_webhook_present_is_reported_without_leaking_its_url(self):
        secret_url = "https://example.test/hook/" + TOKEN
        fake = FakeReadonlyTransport(
            webhook={"url": secret_url, "pending_update_count": 12,
                     "last_error_message": "SECRET_INTERNAL_ENDPOINT"}
        )
        result = inspect_telegram_readonly(
            self.client(fake), expected_bot_id=BOT_ID,
        )
        self.assertEqual(result["status"], "WEBHOOK_PRESENT_POLLING_BLOCKED")
        self.assertTrue(result["webhook_present"])
        self.assertFalse(result["polling_compatible_with_webhook_state"])
        self.assertFalse(result["webhook_url_disclosed"])
        self.assertFalse(result["webhook_changed"])
        serialized = json.dumps(result)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn("example.test", serialized)
        self.assertNotIn("SECRET_INTERNAL_ENDPOINT", serialized)

    def test_fake_non_bot_identity_is_denied_without_webhook_request(self):
        for identity in (
            {"id": BOT_ID, "is_bot": False, "username": "FakeAdmin"},
            {"id": True, "is_bot": True, "username": "False123"},
            {"id": BOT_ID, "is_bot": True, "username": "../secrets"},
            {"id": 0, "is_bot": True, "username": "Short"},
            None, [], 5,
        ):
            with self.subTest(identity=identity):
                fake = FakeReadonlyTransport(me=identity)
                with self.assertRaisesRegex(
                        TelegramTransportError, "INVALID_BOT_IDENTITY"):
                    inspect_telegram_readonly(self.client(fake))
                self.assertEqual([c[0] for c in fake.calls], ["getMe"])

    def test_missing_or_invalid_webhook_status_is_rejected(self):
        for webhook in (
            None, [], {}, {"url": None, "pending_update_count": 0},
            {"url": "", "pending_update_count": True},
            {"url": "", "pending_update_count": -2},
            {"url": 5, "pending_update_count": 0},
        ):
            with self.subTest(webhook=webhook):
                fake = FakeReadonlyTransport(webhook=webhook)
                with self.assertRaisesRegex(
                        TelegramTransportError, "INVALID_WEBHOOK_INFO"):
                    inspect_telegram_readonly(
                        self.client(fake), expected_bot_id=BOT_ID,
                    )
                self.assertEqual(len(fake.calls), 2)

    def test_readonly_methods_reject_parameters_mutations_or_network_default(self):
        fake = FakeReadonlyTransport()
        client = self.client(fake)
        for method in ("getMe", "getWebhookInfo"):
            with self.assertRaisesRegex(
                    TelegramTransportError, "READONLY_ARGUMENTS_DENIED"):
                client.call(method, {"token": "forbidden"})
        for method in ("deleteWebhook", "setWebhook", "sendInvoice"):
            with self.assertRaisesRegex(
                    TelegramTransportError, "METHOD_DENIED"):
                client.call(method, {})
        self.assertEqual(fake.calls, [])
        with self.assertRaisesRegex(
                TelegramTransportError, "NETWORK_DISABLED"):
            TelegramBotApiClient(token=TOKEN).call("getMe", {})

    def test_network_transport_failure_is_sanitized_no_second_call(self):
        fake = FakeReadonlyTransport(fail="getMe")
        with self.assertRaises(TelegramTransportError) as err:
            inspect_telegram_readonly(self.client(fake))
        self.assertEqual(str(err.exception),
                         "M05_TELEGRAM_API_TRANSPORT_FAILED")
        self.assertNotIn(TOKEN, str(err.exception))
        self.assertNotIn("SECRET_NETWORK_STACK", str(err.exception))
        self.assertEqual([c[0] for c in fake.calls], ["getMe"])

    def test_webhook_transport_failure_does_not_invent_success(self):
        fake = FakeReadonlyTransport(fail="getWebhookInfo")
        with self.assertRaises(TelegramTransportError):
            inspect_telegram_readonly(
                self.client(fake), expected_bot_id=BOT_ID,
            )
        self.assertEqual([c[0] for c in fake.calls], ["getMe", "getWebhookInfo"])

    def test_invalid_expected_bot_id_rejected_pre_network(self):
        for bad in (True, False, -2, 0, "123456", 2.0):
            fake = FakeReadonlyTransport()
            with self.subTest(bad=bad), self.assertRaisesRegex(
                    ValueError, "INVALID_EXPECTED_BOT_ID"):
                inspect_telegram_readonly(
                    self.client(fake), expected_bot_id=bad,
                )
            self.assertEqual(fake.calls, [])

    def test_cli_requires_separate_preflight_go_and_never_calls_bot_by_default(self):
        with TemporaryDirectory() as tmp:
            args = ["--db", str(Path(tmp) / "v2.sqlite3"),
                    "--m05-telegram-readonly-preflight-once",
                    "--m05-shadow-db", str(Path(tmp)/"m05-owner.sqlite3"),
                    "--m05-shadow-owner-id", "123456789"]
            for env in ({}, {"M05_TELEGRAM_NETWORK_GO": "YES"}):
                sent, err = StringIO(), StringIO()
                variables = {
                    "INDODAX_TRADING_ENABLED": "NO",
                    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
                    "INDODAX_EXECUTION_MODE": "DRY_RUN",
                    "M05_TELEGRAM_PREFLIGHT_GO": "NO",
                    "M05_TELEGRAM_BOT_TOKEN": TOKEN,
                    **env,
                }
                with patch.dict(os.environ, variables), patch.object(
                        TelegramBotApiClient, "call",
                        side_effect=AssertionError("REAL_CALL_PROHIBITED")):
                    with redirect_stdout(sent), redirect_stderr(err):
                        rc = main(args)
                self.assertEqual(rc, 2)
                self.assertIn("M05_TELEGRAM_PREFLIGHT_GO_REQUIRED", err.getvalue())
                self.assertNotIn(TOKEN, err.getvalue())

    def test_cli_readonly_discovery_exit3_and_pinned_exit0_no_side_effect(self):
        with TemporaryDirectory() as tmp:
            args = ["--db", str(Path(tmp) / "v2.sqlite3"),
                    "--m05-telegram-readonly-preflight-once",
                    "--m05-shadow-db", str(Path(tmp)/"m05-owner.sqlite3"),
                    "--m05-shadow-owner-id", "123456789"]
            for expect, code in ((None, 3), (str(BOT_ID), 0)):
                calls = []
                def fake(method, payload):
                    calls.append(method)
                    return BOT.copy() if method=="getMe" else WEBHOOK_EMPTY.copy()
                vars = {
                    "INDODAX_TRADING_ENABLED": "NO",
                    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
                    "INDODAX_EXECUTION_MODE": "DRY_RUN",
                    "M05_TELEGRAM_PREFLIGHT_GO": "YES",
                    "M05_TELEGRAM_BOT_TOKEN": TOKEN,
                }
                if expect is not None:
                    vars["M05_TELEGRAM_EXPECTED_BOT_ID"] = expect
                output, errors = StringIO(), StringIO()
                with patch.dict(os.environ, vars, clear=True), patch.object(
                        TelegramBotApiClient, "call", side_effect=fake):
                    with redirect_stdout(output), redirect_stderr(errors):
                        rc = main(args)
                self.assertEqual(rc, code, errors.getvalue())
                self.assertEqual(calls, ["getMe", "getWebhookInfo"])
                report = json.loads(output.getvalue())
                self.assertEqual(report["final_decision"], "REJECT")
                self.assertFalse(report["execution_allowed"])
                self.assertEqual(
                    report["m05_telegram_readonly_preflight"]["bot_id_pinned_and_matched"],
                    expect is not None,
                )
                self.assertNotIn(TOKEN, output.getvalue())
                self.assertEqual(errors.getvalue(), "")

    def test_cli_webhook_blocks_and_omits_secret_url(self):
        with TemporaryDirectory() as tmp:
            args = ["--db", str(Path(tmp)/"main.db"),
                    "--m05-telegram-readonly-preflight-once",
                    "--m05-shadow-db", str(Path(tmp)/"owner.db"),
                    "--m05-shadow-owner-id", "123456789"]
            def fake(method, payload):
                if method == "getMe":
                    return BOT.copy()
                return {"url": "https://myprivate.tld/h/" + TOKEN,
                        "pending_update_count": 10}
            variables = {
                "INDODAX_TRADING_ENABLED": "NO",
                "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
                "INDODAX_EXECUTION_MODE": "DRY_RUN",
                "M05_TELEGRAM_PREFLIGHT_GO": "YES",
                "M05_TELEGRAM_EXPECTED_BOT_ID": str(BOT_ID),
                "M05_TELEGRAM_BOT_TOKEN": TOKEN,
            }
            stdout, stderr = StringIO(), StringIO()
            with patch.dict(os.environ, variables, clear=True), patch.object(
                    TelegramBotApiClient, "call", side_effect=fake):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    rc = main(args)
            self.assertEqual(rc, 3)
            self.assertIn("WEBHOOK_PRESENT_POLLING_BLOCKED", stdout.getvalue())
            self.assertNotIn("myprivate.tld", stdout.getvalue())
            self.assertNotIn(TOKEN, stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
