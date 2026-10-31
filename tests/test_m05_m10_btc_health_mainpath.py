"""M05 official one-shot tick -> authenticated owner -> M10 original public health.

No external HTTP: the Bot API transport is injected. BTC health provider is
patched only at the systemd/SQLite boundary; logic and real M05 caller chain
remain production modules. LIVE GO, account APIs and order execution are OFF.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_bot_api import TelegramBotApiClient
from indodax_ai_bot.telegram_service_tick import service_tick_once, ConfigurationError

BOT_ID = 9241907991
OWNER = 198108814
TOKEN = str(BOT_ID) + ":" + "synthetic_m05_only_secret_" * 2


def healthy():
    return {
        "stage": "M10_ORIGINAL_BTC_PUBLIC_OPERATIONS_HEALTH",
        "status": "HEALTHY_LAST_BATCH_UNATTESTED",
        "reason": "M10_RECENT_PUBLIC_BATCH_CONSISTENT",
        "pair": "btc_idr",
        "read_only": True,
        "publications": 10112,
        "historical_gaps": 5,
        "gap_budget": 5,
        "last_batch_age_seconds": 364,
        "go_remaining_seconds": 85532,
        "timer_active": True,
        "restart_disabled": True,
        "notification_sent": False,
        "source_authenticated_for_g1": False,
        "full_trade_tape_proven": False,
        "g1_receipt_issuance_allowed": False,
        "g2_provider_call_allowed": False,
        "execution_allowed": False,
        "final_decision": "REJECT",
    }


class M05OriginalTelegramToM10HealthTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        state = self.root / "m05"
        state.mkdir(mode=0o700)
        self.token_dir = self.root / "token"
        self.token_dir.mkdir(mode=0o700)
        self.db = state / "owner.sqlite3"
        self.ledger = OwnerDecisionLedger(self.db, owner_user_id=OWNER)
        self.db.chmod(0o600)
        self.token = self.token_dir / "secret"
        self.token.write_text(TOKEN + "\n", encoding="ascii")
        self.token.chmod(0o600)
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
            "M05_TELEGRAM_TOKEN_FILE": str(self.token),
        }
        self.calls = []
        self.command = "/health"
        self.from_user = OWNER
        self.chat_id = OWNER
        self.chat_type = "private"
        self.date_delta = 0

    def fake_api(self, method, payload):
        self.calls.append((method, dict(payload)))
        if method == "getMe":
            return {"id": BOT_ID, "is_bot": True}
        if method == "getWebhookInfo":
            return {"url": "", "pending_update_count": 0}
        if method == "getUpdates":
            return [{
                "update_id": 7,
                "message": {
                    "from": {"id": self.from_user},
                    "chat": {"id": self.chat_id, "type": self.chat_type},
                    "text": self.command,
                    "date": int(time.time()) - self.date_delta,
                },
            }] if payload["offset"] <= 7 else []
        if method == "sendMessage":
            return {"message_id": 5, "chat": {"id": OWNER, "type": "private"}}
        raise AssertionError("UNEXPECTED_BOT_API_METHOD")

    def tick(self, env_overrides=None, health_result=None):
        cfg = {**self.env, **(env_overrides or {})}
        if health_result is None:
            health_result = healthy()
        with patch.object(TelegramBotApiClient, "call",
                          side_effect=self.fake_api), patch(
                "indodax_ai_bot.btc_ops_health.health",
                return_value=health_result) as sensor:
            result = service_tick_once(env=cfg)
        return result, sensor

    def test_owner_private_real_m05_tick_routes_existing_m10_sensor_to_health_reply(self):
        result, sensor = self.tick({"M05_BTC_PUBLIC_HEALTH_GO": "YES"})
        self.assertEqual([x[0] for x in self.calls],
                         ["getMe", "getWebhookInfo", "getUpdates", "sendMessage"])
        sensor.assert_called_once()
        self.assertEqual(result["status"], "M05_TICK_COMPLETED")
        self.assertEqual(result["final_decision"], "REJECT")
        self.assertFalse(result["execution_allowed"])
        msg = self.calls[-1][1]["text"]
        self.assertIn("HEALTHY_LAST_BATCH_UNATTESTED", msg)
        self.assertIn("Publikasi: 10112", msg)
        self.assertIn("Gap tercatat/batas: 5/5", msg)
        self.assertIn("G1: TIDAK TERBUKTI. Trading: OFF.", msg)
        self.assertNotIn(TOKEN, msg)
        self.assertEqual(self.ledger.telegram_next_offset(), 8)

    def test_default_off_preserves_old_unavailable_health_and_never_reads_btc(self):
        _, sensor = self.tick()
        sensor.assert_not_called()
        self.assertIn("Belum terhubung", self.calls[-1][1]["text"])

    def test_foreign_owner_or_group_get_no_reply_and_no_btc_read(self):
        self.from_user = OWNER + 1
        _, sensor = self.tick({"M05_BTC_PUBLIC_HEALTH_GO": "YES"})
        sensor.assert_not_called()
        self.assertEqual([x[0] for x in self.calls],
                         ["getMe", "getWebhookInfo", "getUpdates"])

    def test_stale_owner_health_does_not_read_btc(self):
        self.date_delta = 120
        _, sensor = self.tick({"M05_BTC_PUBLIC_HEALTH_GO": "YES"})
        sensor.assert_not_called()
        self.assertEqual([x[0] for x in self.calls],
                         ["getMe", "getWebhookInfo", "getUpdates"])

    def test_alert_readout_exposes_only_static_safe_reason(self):
        bad = healthy()
        bad["status"] = "ALERT_REVIEW"
        bad["reason"] = "M10_PUBLIC_GAP_BUDGET_EXCEEDED"
        _, sensor = self.tick({"M05_BTC_PUBLIC_HEALTH_GO": "YES"}, bad)
        sensor.assert_called_once()
        msg = self.calls[-1][1]["text"]
        self.assertIn("ALERT_REVIEW", msg)
        self.assertIn("M10_PUBLIC_GAP_BUDGET_EXCEEDED", msg)
        self.assertNotIn(TOKEN, msg)
        self.assertIn("Trading: OFF", msg)

    def test_malicious_source_status_never_echoed_or_granted(self):
        bad = healthy()
        bad["status"] = "HEALTHY_LAST_BATCH_UNATTESTED"
        bad["reason"] = "SECRET_TOKEN_FROM_UNTRUSTED_SIDE_CAR\n"
        bad["execution_allowed"] = True
        _, sensor = self.tick({"M05_BTC_PUBLIC_HEALTH_GO": "YES"}, bad)
        sensor.assert_called_once()
        msg = self.calls[-1][1]["text"]
        self.assertIn("ALERT_REVIEW", msg)
        self.assertNotIn("SECRET_TOKEN", msg)
        self.assertIn("Trading: OFF", msg)

    def test_syntactically_valid_secret_like_reason_not_sent_to_owner(self):
        untrusted = healthy()
        untrusted["status"] = "ALERT_REVIEW"
        untrusted["reason"] = "PRIVATE_TOKEN_SECRET_VALUE"
        _, sensor = self.tick({"M05_BTC_PUBLIC_HEALTH_GO": "YES"}, untrusted)
        sensor.assert_called_once()
        msg = self.calls[-1][1]["text"]
        self.assertIn("ALERT_REVIEW", msg)
        self.assertNotIn("PRIVATE_TOKEN_SECRET_VALUE", msg)
        self.assertIn("Status kesehatan belum dapat diverifikasi", msg)

    def test_invalid_go_denied_before_any_telegram_transport(self):
        cfg = {**self.env, "M05_BTC_PUBLIC_HEALTH_GO": "MAYBE"}
        with patch.object(TelegramBotApiClient, "call",
                          side_effect=AssertionError("NETWORK_CALLED")):
            with self.assertRaisesRegex(ConfigurationError, "BTC_HEALTH_GO_INVALID"):
                service_tick_once(env=cfg)
        self.assertEqual(self.calls, [])

    def test_new_owner_control_command_unaffected_by_health_integration(self):
        self.command = "/kontrol manual"
        _, sensor = self.tick({"M05_BTC_PUBLIC_HEALTH_GO": "YES"})
        sensor.assert_not_called()
        self.assertIn("AUTO tidak mengaktifkan order",
                      self.calls[-1][1]["text"])


if __name__ == "__main__":
    unittest.main()
