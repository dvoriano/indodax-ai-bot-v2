"""M05 offline owner UI and persistent one-shot callback tests only.

NO Telegram API, Indodax private API, provider, or money is accessed.
A synthetic shadow PASS is consent logging, Final stays REJECT.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import subprocess
import sys
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_owner import OfflineTelegramOwnerInterface
from test_m03g_bulk import FakeBulkMarket, fake_screen

OWNER = 123456789
NOW = 1800000140
DIGEST = "a" * 64


class M05OwnerOfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "m05.sqlite3"
        self.store = OwnerDecisionLedger(self.db, owner_user_id=OWNER)

    def proposal(self, **changes):
        fields = {
            "pair": "btc_idr", "digest": DIGEST,
            "closed_1m_epoch": NOW - 20,
            "entry_price_idr": "100000", "quantity": "0.25",
            "now_epoch": NOW, "source_expires_epoch": NOW + 30,
        }
        fields.update(changes)
        return self.store.issue_offline_shadow_proposal(**fields)

    def test_default_manual_mode_survives_restart_and_owner_cannot_change(self):
        state = self.store.status(user_id=OWNER, chat_id=OWNER)
        self.assertEqual(state["owner_mode"], "MANUAL")
        self.assertFalse(state["execution_allowed"])
        self.assertFalse(state["telegram_network_connected"])
        next_store = OwnerDecisionLedger(self.db, owner_user_id=OWNER)
        self.assertEqual(next_store.status(
            user_id=OWNER, chat_id=OWNER)["owner_mode"], "MANUAL")
        with self.assertRaisesRegex(ValueError, "M05_OWNER_IDENTITY_MISMATCH"):
            OwnerDecisionLedger(self.db, owner_user_id=999)

    def test_reject_non_owner_group_chat_and_malformed_identity(self):
        ui = OfflineTelegramOwnerInterface(self.store)
        for actor, chat, typ in [(8, 8, "private"), (OWNER, 8, "private"),
                                 (OWNER, OWNER, "group"), (True, OWNER, "private")]:
            with self.subTest(actor=actor, chat=chat, typ=typ):
                self.assertEqual(ui.handle_command(
                    user_id=actor, chat_id=chat, chat_type=typ,
                    text="/kontrol auto", now_epoch=NOW)["reason"],
                    "OWNER_NOT_AUTHORIZED")
        self.assertEqual(self.store.status(
            user_id=OWNER, chat_id=OWNER)["owner_mode"], "MANUAL")
        self.assertEqual(self.store.audit_count(), 0)

    def test_shadow_proposal_ttl_capped_and_buttons_within_telegram_limit(self):
        p = self.proposal(source_expires_epoch=NOW + 500)
        self.assertTrue(p["accepted"])
        self.assertTrue(p["shadow_only"])
        self.assertEqual(p["expires_epoch"], NOW + 90)
        self.assertFalse(p["execution_allowed"])
        for label, token in p["callbacks"].items():
            self.assertLessEqual(len(token.encode()), 64)
            self.assertIn(":P:" if label == "PASS" else ":R:", token)
        keyboard = OfflineTelegramOwnerInterface.keyboard_for_shadow_proposal(p)
        self.assertFalse(keyboard["network_sent"])
        self.assertEqual(len(keyboard["inline_keyboard"][0]), 2)

    def test_source_expiry_shorter_than_90_seconds(self):
        p = self.proposal()
        self.assertEqual(p["expires_epoch"], NOW + 30)
        response = self.store.apply_callback(
            user_id=OWNER, chat_id=OWNER,
            callback_data=p["callbacks"]["PASS"], now_epoch=NOW + 30)
        self.assertEqual(response["reason"], "EXPIRED_OR_MODE_CHANGED")
        self.assertFalse(response["accepted"])
        self.assertIsNone(response["final_decision"])

    def test_pass_consumes_once_but_cannot_authorize_execution(self):
        p = self.proposal()
        result = self.store.apply_callback(
            user_id=OWNER, chat_id=OWNER, now_epoch=NOW+2,
            callback_data=p["callbacks"]["PASS"])
        self.assertTrue(result["accepted"])
        self.assertEqual(result["owner_decision"], "PASS")
        self.assertEqual(result["final_decision"], "PASS")
        self.assertFalse(result["execution_allowed"])
        replay = self.store.apply_callback(
            user_id=OWNER, chat_id=OWNER, now_epoch=NOW+3,
            callback_data=p["callbacks"]["REJECT"])
        self.assertEqual(replay["reason"], "CALLBACK_ALREADY_CONSUMED")
        self.assertFalse(replay["accepted"])
        self.assertEqual(self.store.audit_count(), 2)

    def test_reject_consume_is_final_even_across_restart(self):
        p = self.proposal()
        first = self.store.apply_callback(
            user_id=OWNER, chat_id=OWNER, now_epoch=NOW+1,
            callback_data=p["callbacks"]["REJECT"])
        self.assertEqual(first["owner_decision"], "REJECT")
        restarted = OwnerDecisionLedger(self.db, owner_user_id=OWNER)
        after = restarted.apply_callback(
            user_id=OWNER, chat_id=OWNER, now_epoch=NOW+2,
            callback_data=p["callbacks"]["PASS"])
        self.assertEqual(after["reason"], "CALLBACK_ALREADY_CONSUMED")

    def test_forged_or_wrong_owner_callback_rejected_without_token_consumption(self):
        p = self.proposal()
        fake = p["callbacks"]["PASS"][:-1] + (
            "a" if p["callbacks"]["PASS"][-1] != "a" else "b")
        forged = self.store.apply_callback(
            user_id=OWNER, chat_id=OWNER, now_epoch=NOW+1,
            callback_data=fake)
        self.assertEqual(forged["reason"], "UNKNOWN_OR_FORGED_CALLBACK")
        other = self.store.apply_callback(
            user_id=888, chat_id=888, now_epoch=NOW+1,
            callback_data=p["callbacks"]["PASS"])
        self.assertEqual(other["reason"], "OWNER_NOT_AUTHORIZED")
        allowed = self.store.apply_callback(
            user_id=OWNER, chat_id=OWNER, now_epoch=NOW+2,
            callback_data=p["callbacks"]["PASS"])
        self.assertTrue(allowed["accepted"])

    def test_two_concurrent_callbacks_resolve_exactly_one_final_choice(self):
        p = self.proposal()
        data = (p["callbacks"]["PASS"], p["callbacks"]["REJECT"])
        def call(token):
            other = OwnerDecisionLedger(self.db, owner_user_id=OWNER)
            return other.apply_callback(
                user_id=OWNER, chat_id=OWNER,
                callback_data=token, now_epoch=NOW+1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(call, data))
        self.assertEqual(sum(x["accepted"] for x in result), 1)
        self.assertEqual(
            sorted(x["reason"] for x in result),
            ["CALLBACK_ALREADY_CONSUMED", "SHADOW_CONSENT_RECORDED_NO_EXECUTOR"])
        self.assertEqual(self.store.audit_count(), 2)

    def test_auto_switch_invalidates_pending_and_does_not_create_order(self):
        p = self.proposal()
        ui = OfflineTelegramOwnerInterface(self.store)
        result = ui.handle_command(
            user_id=OWNER, chat_id=OWNER,
            text="/kontrol auto", now_epoch=NOW+1)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["owner_mode"], "AUTO")
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["auto_order_authorized"])
        self.assertEqual(self.proposal()["reason"], "AUTO_NO_VERIFIED_PROPOSAL_ENGINE")
        old = ui.handle_callback(
            user_id=OWNER, chat_id=OWNER, now_epoch=NOW+2,
            data=p["callbacks"]["PASS"])
        self.assertEqual(old["reason"], "CALLBACK_ALREADY_CONSUMED")
        self.assertEqual(OwnerDecisionLedger(
            self.db, owner_user_id=OWNER).status(
            user_id=OWNER, chat_id=OWNER)["owner_mode"], "AUTO")
        ui.handle_command(user_id=OWNER, chat_id=OWNER,
                          text="/kontrol manual", now_epoch=NOW+3)
        self.assertTrue(self.proposal()["accepted"])

    def test_all_named_telegram_commands_fail_closed_on_unconnected_data(self):
        ui = OfflineTelegramOwnerInterface(self.store)
        for cmd in ("/scanner", "/market", "/ai", "/buy", "/positions",
                    "/sell", "/balance", "/profit", "/history",
                    "/risk", "/health"):
            with self.subTest(command=cmd):
                response = ui.handle_command(
                    user_id=OWNER, chat_id=OWNER, text=cmd, now_epoch=NOW)
                self.assertEqual(response["reason"],
                                 "LIVE_DATA_OR_EXECUTOR_NOT_CONNECTED")
                self.assertFalse(response["execution_allowed"])
        self.assertIn("MANUAL", ui.handle_command(
            user_id=OWNER, chat_id=OWNER,
            text="/start", now_epoch=NOW)["text"])

    def test_reject_invalid_pair_digest_price_or_stale_source(self):
        for changes in (
            {"pair": "btc_usdt"}, {"digest": "f"}, {"quantity": "NaN"},
            {"entry_price_idr": "-1"},
            {"source_expires_epoch": NOW},
            {"closed_1m_epoch": NOW + 60},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(self.proposal(**changes)["accepted"])
        self.assertEqual(self.store.audit_count(), 0)

    def test_mainpath_once_records_reject_not_owner_proposal(self):
        journal = DecisionJournal(Path(self.temp.name)/"main.sqlite3")
        app = DailyScalpingOrchestrator(
            Settings.from_env({}), journal, owner_control=self.store)
        result = app.run_cycle("m05-once")
        self.assertEqual(result.final_decision.value, "REJECT")
        self.assertFalse(result.execution_allowed)
        self.assertEqual(self.store.observed_cycle("m05-once"), {
            "reason": "G1_SOURCE_NOT_CONNECTED",
            "final_decision": "REJECT", "execution_allowed": False,
        })
        again = app.run_cycle("m05-once")
        self.assertEqual(again.final_decision.value, "REJECT")
        self.assertEqual(self.store.audit_count(), 0)
        with self.assertRaisesRegex(ValueError, "M05_MAINPATH_TRACE_REPLAY_CONFLICT"):
            self.store.record_orchestrator_reject(
                trace_id="m05-once", reason="FORGED", now_epoch=NOW)

    def test_real_bulk_mainpath_uses_existing_scanner_and_persists_reject(self):
        journal = DecisionJournal(Path(self.temp.name)/"bulk.sqlite3")
        app = DailyScalpingOrchestrator(
            Settings.from_env({}), journal, owner_control=self.store)
        with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scan:
            scan.return_value.scan.return_value = fake_screen()
            response = app.run_bulk_deep_cycle(
                "m05-bulk", market=FakeBulkMarket()
            )
        self.assertIsNone(response.final_decision)
        self.assertFalse(response.execution_allowed)
        self.assertIsNone(self.store.observed_cycle("m05-bulk"))
        self.assertEqual(journal.stage_observation_count(), 1)
        self.assertEqual(self.store.audit_count(), 0)

    def test_cli_optin_wires_original_mainpath_without_telegram_or_orders(self):
        db = Path(self.temp.name)/"cli-state.sqlite3"
        env = {**os.environ,
               "INDODAX_TRADING_ENABLED": "NO",
               "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
               "INDODAX_EXECUTION_MODE": "DRY_RUN"}
        result = subprocess.run(
            [sys.executable, "-m", "indodax_ai_bot",
             "--db", str(db), "--m05-shadow-db", str(self.db),
             "--m05-shadow-owner-id", str(OWNER), "--once"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        message = json.loads(result.stdout)
        self.assertEqual(message["final_decision"], "REJECT")
        self.assertFalse(message["execution_allowed"])
        self.assertIsNotNone(self.store.observed_cycle(message["trace_id"]))


if __name__ == "__main__":
    unittest.main()
