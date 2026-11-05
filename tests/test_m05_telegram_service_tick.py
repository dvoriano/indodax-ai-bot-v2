"""M05 systemd tick runner tests: fake Bot API ONLY, no real credentials/network."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_bot_api import TelegramBotApiClient, TelegramTransportError
from indodax_ai_bot.telegram_dedicated_inbound import dedicated_poller_lock
from indodax_ai_bot.telegram_service_tick import (
    ConfigurationError, service_tick_once, main,
)

BOT_ID = 9241907991
OWNER = 198108814
TOKEN = str(BOT_ID) + ":" + ("test_only_secret_for_v2_" * 2)


class M05SystemdTickTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        root = Path(t.name)
        self.state = root / "state"
        self.state.mkdir(mode=0o700)
        self.state.chmod(0o700)
        self.secrets = root / "secrets"
        self.secrets.mkdir(mode=0o700)
        self.secrets.chmod(0o700)
        self.db = self.state / "owner.sqlite3"
        self.ledger = OwnerDecisionLedger(self.db, owner_user_id=OWNER)
        self.db.chmod(0o600)
        self.token_file = self.secrets / "token"
        self.token_file.write_text(TOKEN + "\n", encoding="ascii")
        self.token_file.chmod(0o600)
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
            "M05_TELEGRAM_TOKEN_FILE": str(self.token_file),
        }
        self.calls = []

    def fake_api(self, method, payload):
        self.calls.append((method, dict(payload)))
        if method == "getMe":
            return {"id": BOT_ID, "is_bot": True, "username": "pitidenbot"}
        if method == "getWebhookInfo":
            return {"url": "", "pending_update_count": 0}
        if method == "getUpdates":
            return [{
                "update_id": 100,
                "message": {
                    "from": {"id": OWNER},
                    "chat": {"id": OWNER, "type": "private"},
                    "date": int(time.time()),
                    "text": "/status",
                },
            }] if payload["offset"] <= 100 else []
        if method == "sendMessage":
            return {"message_id": 4, "chat": {"id": OWNER, "type": "private"}}
        raise AssertionError("UNEXPECTED_API_METHOD")

    def run_tick(self, changes=None, *, fake=None):
        cfg = {**self.env, **(changes or {})}
        with patch.object(TelegramBotApiClient, "call",
                          side_effect=fake or self.fake_api):
            return service_tick_once(env=cfg)

    def test_existing_owner_status_one_tick_and_durable_cursor(self):
        result = self.run_tick()
        self.assertEqual([x[0] for x in self.calls],
                         ["getMe", "getWebhookInfo", "getUpdates", "sendMessage"])
        self.assertEqual(result["status"], "M05_TICK_COMPLETED")
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["next_offset"], 101)
        self.assertIsNone(result["final_decision"])
        self.assertFalse(result["execution_allowed"])
        self.assertNotIn(TOKEN, str(result))
        self.assertEqual(self.ledger.telegram_next_offset(), 101)

    def test_restart_reuses_cursor_and_does_not_send_twice(self):
        self.run_tick()
        self.calls.clear()
        report = self.run_tick()
        self.assertEqual([x[0] for x in self.calls],
                         ["getMe", "getWebhookInfo", "getUpdates"])
        self.assertEqual(report["processed"], 0)
        self.assertEqual(report["next_offset"], 101)

    def test_go_flags_prevent_any_network(self):
        for key in (
            "M05_TELEGRAM_TICK_GO", "M05_TELEGRAM_NETWORK_GO",
            "M05_TELEGRAM_DEDICATED_GO",
        ):
            with self.subTest(key=key), self.assertRaisesRegex(
                    ConfigurationError, "M05_TICK_REQUIRED_"):
                self.run_tick({key: "NO"})
            self.assertEqual(self.calls, [])

    def test_trading_flags_block_all_requests(self):
        for cfg in (
            {"INDODAX_TRADING_ENABLED": "YES"},
            {"INDODAX_ALLOW_LIVE_EXECUTION": "YES"},
            {"INDODAX_EXECUTION_MODE": "LIVE"},
        ):
            with self.subTest(cfg=cfg), self.assertRaisesRegex(
                    ConfigurationError, "M01_LIVE_NOT_IMPLEMENTED"):
                self.run_tick(cfg)
            self.assertEqual(self.calls, [])

    def test_old_bot_and_wrong_token_rejected_before_network(self):
        with self.assertRaisesRegex(
                ConfigurationError, "LEGACY_CAIPITI_DENIED"):
            self.run_tick({"M05_TELEGRAM_DEDICATED_BOT_ID": "8827061554"})
        with self.assertRaisesRegex(
                ConfigurationError, "TOKEN_ID_MISMATCH"):
            self.run_tick({"M05_TELEGRAM_DEDICATED_BOT_ID": str(BOT_ID + 1)})
        self.assertEqual(self.calls, [])

    def test_wrong_owner_id_denied_before_network(self):
        with self.assertRaisesRegex(
                ConfigurationError, "OWNER_DB_MISMATCH"):
            self.run_tick({"M05_TELEGRAM_OWNER_ID": str(OWNER + 1)})
        self.assertEqual(self.calls, [])

    def test_missing_db_fails_closed_no_cursor_reset(self):
        self.db.unlink()
        with self.assertRaises(ConfigurationError):
            self.run_tick()
        self.assertFalse(self.db.exists())
        self.assertEqual(self.calls, [])

    def test_incomplete_claim_blocks_before_any_api_call(self):
        self.ledger.claim_telegram_update(
            update_id=41, callback_query_id=None,
            now_epoch=int(time.time()),
        )
        with self.assertRaisesRegex(
                ConfigurationError, "INCOMPLETE_REVIEW_REQUIRED"):
            self.run_tick()
        self.assertEqual(self.calls, [])

    def test_missing_token_file_denied(self):
        self.token_file.unlink()
        with self.assertRaises(ConfigurationError):
            self.run_tick()
        self.assertEqual(self.calls, [])

    def test_token_file_unsafe_permissions_denied(self):
        self.token_file.chmod(0o644)
        with self.assertRaisesRegex(
                ConfigurationError, "UNSAFE_PRIVATE_FILE"):
            self.run_tick()
        self.assertEqual(self.calls, [])

    def test_symlink_secret_denied(self):
        original = self.secrets / "real-token"
        self.token_file.rename(original)
        self.token_file.symlink_to(original)
        with self.assertRaisesRegex(
                ConfigurationError, "INVALID_PRIVATE_PATH"):
            self.run_tick()
        self.assertEqual(self.calls, [])

    def test_state_dir_insecure_mode_rejected(self):
        self.state.chmod(0o755)
        with self.assertRaisesRegex(
                ConfigurationError, "UNSAFE_PRIVATE_FILE"):
            self.run_tick()
        self.assertEqual(self.calls, [])

    def test_token_never_appears_in_error(self):
        self.token_file.write_text("invalid:" + TOKEN)
        with self.assertRaises(ConfigurationError) as caught:
            self.run_tick()
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertEqual(self.calls, [])

    def test_local_lock_contention_denied_pre_network(self):
        lock = str(self.db) + ".dedicated-poller.lock"
        with dedicated_poller_lock(lock), self.assertRaisesRegex(
                TelegramTransportError, "ALREADY_RUNNING"):
            self.run_tick()
        self.assertEqual(self.calls, [])

    def test_webhook_blocks_before_poll(self):
        def api(method, payload):
            self.calls.append((method, dict(payload)))
            if method == "getMe":
                # getMe must first pass the real preflight identity schema.
                return {"id": BOT_ID, "is_bot": True,
                        "username": "pitidenbot"}
            if method == "getWebhookInfo":
                # Only a well-formed webhook envelope may test the block.
                return {"url": "https://private.example/token",
                        "pending_update_count": 0}
            raise AssertionError("getUpdates MUST NOT HAPPEN")
        with self.assertRaisesRegex(
                TelegramTransportError, "WEBHOOK_PRESENT"):
            self.run_tick(fake=api)
        self.assertEqual([x[0] for x in self.calls],
                         ["getMe", "getWebhookInfo"])

    def test_real_network_disabled_when_not_explicitly_configured(self):
        self.assertEqual(self.calls, [])
        with self.assertRaisesRegex(
                ConfigurationError, "M05_TICK_REQUIRED_"):
            service_tick_once(env={})
        self.assertEqual(self.calls, [])

    def test_cli_output_redacted_and_safe_status_only(self):
        out, err = StringIO(), StringIO()
        with patch.dict(os.environ, self.env, clear=True), patch.object(
                TelegramBotApiClient, "call", side_effect=self.fake_api):
            with redirect_stdout(out), redirect_stderr(err):
                rc = main()
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn("M05_TICK_COMPLETED", out.getvalue())
        self.assertIn('"execution_allowed": false', out.getvalue())
        self.assertNotIn(TOKEN, out.getvalue())
        self.assertNotIn(str(OWNER), out.getvalue())
        self.assertEqual(err.getvalue(), "")

    def test_isolated_systemd_entrypoint_imports_checkout_not_editable_site(self):
        """Simulate exact -I unit ExecStart with unrelated working directory.

        Intentionally omit token/GO. The correct result is a stable safety
        denial from service_tick_once, NOT 'No module named ...'.
        """
        root = Path(__file__).resolve().parents[1]
        entry = root / "scripts" / "m05_telegram_service_tick_entry.py"
        self.assertTrue(entry.is_file())
        env = {
            "HOME": str(Path.home()),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
            "M05_TELEGRAM_TICK_GO": "NO",
            "M05_TELEGRAM_NETWORK_GO": "NO",
            "M05_TELEGRAM_DEDICATED_GO": "NO",
        }
        run = subprocess.run(
            [sys.executable, "-I", "-B", str(entry)],
            cwd="/", env=env,
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn("M05_TICK_REQUIRED_M05_TELEGRAM_TICK_GO", run.stderr)
        self.assertEqual(run.stdout, "")
        self.assertNotIn("ModuleNotFoundError", run.stderr)
        self.assertNotIn(TOKEN, run.stderr)

    def test_systemd_files_are_opt_in_and_no_token_embedded(self):
        base = Path(__file__).resolve().parents[1] / "systemd" / "user"
        unit = (base / "indodax-v2-m05-telegram.service").read_text()
        timer = (base / "indodax-v2-m05-telegram.timer").read_text()
        self.assertIn("Type=oneshot", unit)
        self.assertIn("Restart=no", unit)
        self.assertIn("scripts/m05_telegram_service_tick_entry.py", unit)
        self.assertIn("-I -B", unit)
        self.assertNotIn("-m indodax_ai_bot.telegram_service_tick", unit)
        self.assertIn("OnUnitInactiveSec=3s", timer)
        self.assertIn("OnStartupSec=3s", timer)
        self.assertIn("AccuracySec=1s", timer)
        self.assertIn("Persistent=false", timer)
        self.assertIn("Unit=indodax-v2-m05-telegram.service", timer)
        self.assertNotIn("OnUnitInactiveSec=10s", timer)
        self.assertNotIn("OnUnitInactiveSec=30s", timer)
        self.assertIn("WantedBy=timers.target", timer)
        self.assertNotIn("M05_TELEGRAM_BOT_TOKEN=", unit)
        self.assertNotIn(TOKEN, unit + timer)
        self.assertNotIn("8827061554", unit + timer)
        self.assertNotIn("ExecStart=python", unit)


    def test_m07_optin_missing_secure_sources_fails_before_bot_http(self):
        with self.assertRaisesRegex(
                ConfigurationError, "M05_TICK_INVALID_PRIVATE_PATH"):
            self.run_tick({"M07_MANUAL_RECHECK_NONLIVE_GO": "YES"})
        self.assertEqual(self.calls, [])

    def test_m07_optin_uses_original_service_callback_only_for_owner_buy(self):
        import json
        from decimal import Decimal
        from indodax_ai_bot.journal import DecisionJournal
        from indodax_ai_bot.m07_order_adapter import M07OrderDraft
        from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
        now = int(time.time())
        decisions = self.state / "decisions.sqlite3"
        DecisionJournal(decisions)
        decisions.chmod(0o600)
        funding_file = self.secrets / "funding.json"
        funding_file.write_text(json.dumps({
            "requested_ticket_ceiling_idr": "100000",
            "new_floki_sell_rebuy_hold_idr": "0",
            "new_pepe_sell_rebuy_hold_idr": "0",
            "commitments_not_locked_idr": "0",
            "max_spread_bps": "150",
        }))
        funding_file.chmod(0o600)
        key_file = self.secrets / "viewonly.json"
        key_file.write_text(json.dumps({
            "api_key": "viewonly_test", "secret_key": "viewonly_test",
        }))
        key_file.chmod(0o600)
        draft = M07OrderDraft(
            symbol="BTCIDR", side="BUY", order_type="LIMIT",
            client_order_id="tick_owner_click_test", price=Decimal("100"),
            quantity=Decimal("100"),
        )
        row = self.ledger.queue_manual_trade_preview_offline(
            user_id=OWNER, chat_id=OWNER, draft=draft,
            account_fingerprint="a" * 64, now_epoch=now,
        )
        self.assertTrue(row["accepted"])
        card = self.ledger.claim_manual_trade_preview_delivery(
            now_epoch=now)
        self.ledger.confirm_manual_trade_preview_delivery(
            preview_id=row["preview_id"], message_id=88,
        )
        update = {
            "update_id": 77,
            "callback_query": {
                "id": "service-m07-click", "from": {"id": OWNER},
                "data": card["confirm_callback"],
                "message": {
                    "message_id": 88, "date": now,
                    "from": {"id": BOT_ID, "is_bot": True},
                    "chat": {"id": OWNER, "type": "private"},
                },
            },
        }
        def fake_api(method, payload):
            self.calls.append((method, dict(payload)))
            if method == "getMe":
                return {"id": BOT_ID, "is_bot": True, "username": "pitidenbot"}
            if method == "getWebhookInfo":
                return {"url": "", "pending_update_count": 0}
            if method == "getUpdates":
                return [update] if payload["offset"] <= 77 else []
            if method in ("answerCallbackQuery", "editMessageReplyMarkup"):
                return True
            if method == "sendMessage":
                return {"message_id": 101,
                        "chat": {"id": OWNER, "type": "private"}}
            raise AssertionError("UNEXPECTED_TELEGRAM")
        source_calls = []
        def fake_nonlive(self_app, **kwargs):
            source_calls.append(kwargs)
            return {"status": "M07_CLICK_SETUP_NO_LONGER_QUALIFIED",
                    "order_sent": False, "execution_allowed": False}
        cfg = {
            "M07_MANUAL_RECHECK_NONLIVE_GO": "YES",
            "M07_MANUAL_RECHECK_DECISION_DB": str(decisions),
            "M07_MANUAL_RECHECK_FUNDING_FILE": str(funding_file),
            "M07_MANUAL_RECHECK_VIEWONLY_KEY_FILE": str(key_file),
        }
        with patch.object(DailyScalpingOrchestrator,
                          "recheck_m07_clicked_manual_buy_from_sources_nonlive_once",
                          autospec=True, side_effect=fake_nonlive):
            result = self.run_tick(cfg, fake=fake_api)
            repeated = self.run_tick(cfg, fake=fake_api)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(repeated["processed"], 0)
        self.assertEqual(len(source_calls), 1)
        self.assertEqual(source_calls[0]["preview_id"], row["preview_id"])
        self.assertEqual(source_calls[0]["owner_user_id"], OWNER)
        self.assertEqual(source_calls[0]["api_key"], "viewonly_test")
        self.assertEqual(source_calls[0]["funding"]["max_spread_bps"],
                         Decimal("150"))
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(self.ledger.telegram_next_offset(), 78)
        self.assertNotIn("viewonly_test", str(result))
        with sqlite3.connect(decisions) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM m07_manual_sender_nonlive"
                           ).fetchone()[0], 0)



if __name__ == "__main__":
    unittest.main()
