"""M05 V2 owner /scanner and /health through ONE existing service tick.

No real Telegram network, systemd mutation, Indodax call, private data or order.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from unittest.mock import patch
import time
import unittest

from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_bot_api import TelegramBotApiClient
from indodax_ai_bot.telegram_service_tick import service_tick_once
from indodax_ai_bot.m05_operator_views import readonly_operator_view

BOT_ID = 9241907991
OWNER = 198108814
TOKEN = str(BOT_ID) + ":" + "test_only_v2_token_chars_" * 2


class M05OperatorReadOnlyMainpathTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        state = self.root / "state"
        state.mkdir(mode=0o700)
        secrets = self.root / "secrets"
        secrets.mkdir(mode=0o700)
        self.db = state / "owner.sqlite3"
        self.ledger = OwnerDecisionLedger(self.db, owner_user_id=OWNER)
        self.db.chmod(0o600)
        token_path = secrets / "token"
        token_path.write_text(TOKEN + "\n")
        token_path.chmod(0o600)
        self.env = {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
            "M05_TELEGRAM_TICK_GO": "YES",
            "M05_TELEGRAM_NETWORK_GO": "YES",
            "M05_TELEGRAM_DEDICATED_GO": "YES",
            "M05_TELEGRAM_DEDICATED_BOT_ID": str(BOT_ID),
            "M05_TELEGRAM_OWNER_ID": str(OWNER),
            "M05_TELEGRAM_OWNER_DB": str(self.db),
            "M05_TELEGRAM_TOKEN_FILE": str(token_path),
        }
        self.api_calls = []
        self.systemd_calls = []
        self.mode = "inactive"
        self.owner_id = OWNER
        self.command = "/scanner"
        self.message_epoch = int(time.time())

    def fake_systemd(self, args, **kwargs):
        self.systemd_calls.append(list(args))
        self.assertEqual(args[:3], ["systemctl", "--user", "show"])
        self.assertEqual(kwargs["timeout"], 3)
        return subprocess.CompletedProcess(args, 0, stdout=(
            "LoadState=loaded\n"
            "ActiveState=" + self.mode + "\n"
            "UnitFileState=disabled\n"
        ))

    def fake_api(self, method, payload):
        self.api_calls.append((method, dict(payload)))
        if method == "getMe":
            return {"id": BOT_ID, "is_bot": True, "username": "pitidenbot"}
        if method == "getWebhookInfo":
            return {"url": "", "pending_update_count": 0}
        if method == "getUpdates":
            return [{
                "update_id": 50,
                "message": {
                    "from": {"id": self.owner_id},
                    "chat": {"id": self.owner_id, "type": "private"},
                    "date": self.message_epoch,
                    "text": self.command,
                },
            }] if payload["offset"] <= 50 else []
        if method == "sendMessage":
            return {"message_id": 22, "chat": {"id": OWNER, "type": "private"}}
        raise AssertionError("NOT_AN_ALLOWED_TELEGRAM_METHOD")

    def run_service(self):
        with (patch.object(TelegramBotApiClient, "call",
                           side_effect=self.fake_api),
              patch("indodax_ai_bot.m05_operator_views.subprocess.run",
                    side_effect=self.fake_systemd)):
            return service_tick_once(env=self.env)

    def test_original_service_owner_scanner_reports_unit_and_never_trades(self):
        report = self.run_service()
        self.assertEqual(report["processed"], 1)
        self.assertEqual(report["next_offset"], 51)
        self.assertFalse(report["execution_allowed"])
        self.assertEqual(report["final_decision"], "REJECT")
        self.assertEqual([x[0] for x in self.api_calls],
                         ["getMe", "getWebhookInfo", "getUpdates", "sendMessage"])
        message = self.api_calls[-1][1]["text"]
        self.assertIn("V2 scanner M03", message)
        self.assertIn("timer=INACTIVE", message)
        self.assertIn("enabled=DISABLED", message)
        self.assertIn("service=INACTIVE", message)
        self.assertIn("Trading OFF", message)
        self.assertEqual(self.ledger.telegram_next_offset(), 51)
        self.assertEqual(len(self.systemd_calls), 2)
        self.assertNotIn(TOKEN, str(report))

    def test_original_service_owner_health_reads_existing_v2_batch(self):
        self.command = "/health"
        with patch(
            "indodax_ai_bot.m05_operator_views.public_ws_health",
            return_value={"last_batch_recent_and_consistent": True},
        ) as health:
            report = self.run_service()
        self.assertEqual(report["processed"], 1)
        self.assertEqual([x[0] for x in self.api_calls][-1], "sendMessage")
        message = self.api_calls[-1][1]["text"]
        self.assertIn("BTC WS last batch=RECENT_COMPLETED_UNATTESTED", message)
        self.assertIn("bukan heartbeat/live socket", message)
        self.assertIn("Final REJECT. Trading OFF.", message)
        args, kw = health.call_args
        self.assertEqual(args[0].name, "public_ws_btc.sqlite3")
        self.assertEqual(args[1].name, "public_ws_btc.status.json")
        self.assertEqual(kw["max_age_seconds"], 3600)
        self.assertFalse(report["execution_allowed"])

    def test_health_missing_batch_is_unverified_and_no_files_created(self):
        report = readonly_operator_view(
            "/health", now_epoch=self.message_epoch,
            home=self.root, runner=self.fake_systemd,
        )
        self.assertIn("BTC WS last batch=UNVERIFIED_OR_STALE", report["text"])
        self.assertFalse(report["execution_allowed"])
        self.assertFalse((self.root / "indodax-ai-bot-v2").exists())

    def test_non_owner_private_update_never_reads_operator_evidence(self):
        self.owner_id = OWNER + 1
        with patch(
            "indodax_ai_bot.telegram_owner.readonly_operator_view",
            side_effect=AssertionError("UNAUTHORIZED_VIEW_USED"),
        ):
            report = self.run_service()
        self.assertEqual(report["processed"], 1)
        self.assertEqual([x[0] for x in self.api_calls],
                         ["getMe", "getWebhookInfo", "getUpdates"])
        self.assertEqual(self.systemd_calls, [])
        self.assertFalse(report["execution_allowed"])

    def test_auto_mode_uses_durable_owner_ledger_and_never_authorizes_order(self):
        self.command = "/kontrol auto"
        report = self.run_service()
        self.assertEqual(report["processed"], 1)
        status = self.ledger.status(user_id=OWNER, chat_id=OWNER)
        self.assertEqual(status["owner_mode"], "AUTO")
        self.assertFalse(status["execution_allowed"])
        self.assertIn("AUTO tidak mengaktifkan order",
                      self.api_calls[-1][1]["text"])
        self.assertEqual(self.systemd_calls, [])

    def test_buy_cannot_use_health_or_scanner_route(self):
        self.command = "/buy"
        report = self.run_service()
        self.assertEqual(report["processed"], 1)
        self.assertIn("Belum terhubung", self.api_calls[-1][1]["text"])
        self.assertEqual(self.systemd_calls, [])
        self.assertFalse(report["execution_allowed"])

    def test_systemd_missing_or_unexpected_output_never_claims_active(self):
        def untrusted(_args, **_kwargs):
            return subprocess.CompletedProcess(
                ["systemctl"], 0, stdout="LoadState=loaded\nActiveState=active\n",
            )
        report = readonly_operator_view(
            "/scanner", now_epoch=self.message_epoch, runner=untrusted,
        )
        self.assertIn("timer=UNKNOWN", report["text"])
        self.assertIn("service=UNKNOWN", report["text"])
        self.assertFalse(report["execution_allowed"])

    def test_restart_does_not_replay_owner_view(self):
        self.run_service()
        self.api_calls.clear()
        self.systemd_calls.clear()
        second = self.run_service()
        self.assertEqual(second["processed"], 0)
        self.assertEqual([x[0] for x in self.api_calls],
                         ["getMe", "getWebhookInfo", "getUpdates"])
        self.assertEqual(self.systemd_calls, [])
        self.assertEqual(self.ledger.telegram_next_offset(), 51)


if __name__ == "__main__":
    unittest.main()
