"""Original M11-04 CLI → orchestrator → SAME journal READ-ONLY SHADOW.

Tests only synthetic, owner-private TemporaryDirectory SQLite fixtures.
No real V2 runtime DB, Telegram, private/exchange API, order or GO.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import hashlib
import json
import os
import sqlite3
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


EPOCH = 1793781000
OWNER = 1


class M11OriginalShadowReadonlyTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="m11-shadow-original-", dir="/tmp")
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "m11_offline_fixture.sqlite3"
        self.write_journal = DecisionJournal(self.db)
        os.chmod(self.db, 0o600)

    def policy(self):
        self.write_journal.record_m11_offline_risk_limits_once(
            owner_user_id=OWNER, policy_id="a" * 24,
            capital_ceiling_idr=Decimal("300000"),
            max_open_orders=3,
            daily_loss_ceiling_idr=Decimal("40000"),
            recorded_epoch=EPOCH,
        )

    def readonly(self):
        return DecisionJournal.open_m11_offline_fixture_readonly(self.db)

    def call(self, db=None, env=None):
        out, err = StringIO(), StringIO()
        flags = {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
        }
        flags.update(env or {})
        with patch.dict(os.environ, flags, clear=True):
            with redirect_stdout(out), redirect_stderr(err):
                code = main([
                    "--m11-risk-halt-shadow-offline-once",
                    "--db", str(db or self.db),
                ])
        return code, out.getvalue(), err.getvalue()

    def assert_denied(self, r):
        self.assertEqual(r["status"], "REJECT_SHADOW_OFFLINE_ONLY")
        self.assertIsNone(r["owner_final_decision"])
        self.assertFalse(r["execution_allowed"])
        self.assertFalse(r["m11_live_release_authorized"])
        self.assertFalse(r["risk_halt_review"]["execution_allowed"])
        self.assertFalse(r["risk_halt_review"]["m11_live_release_authorized"])
        self.assertFalse(r["risk_halt_review"]["owner_identity_authenticated"])
        self.assertFalse(r["risk_halt_review"]["scenario_is_real_exchange_evidence"])
        self.assertFalse(r["order_sent"])

    def test_cli_original_mainpath_reads_missing_policy_and_never_go(self):
        code, out, err = self.call()
        self.assertEqual((code, err), (0, ""))
        r = json.loads(out)
        self.assertTrue(r["original_orchestrator_invoked"])
        self.assertTrue(r["same_original_decision_journal_review_invoked"])
        self.assertIn("OWNER_OFFLINE_POLICY_UNVERIFIED_OR_MISSING",
                      r["risk_halt_review"]["reason_codes"])
        self.assertIn("RISK_OBSERVATION_MISSING",
                      r["risk_halt_review"]["reason_codes"])
        self.assert_denied(r)

    def test_owner_entered_policy_remains_unverified_and_halt_engaged(self):
        self.policy()
        code, out, err = self.call()
        self.assertEqual((code, err), (0, ""))
        r = json.loads(out)
        self.assertTrue(r["risk_halt_review"]["offline_policy_present_for_caller"])
        self.assertTrue(r["risk_halt_review"]["persistent_halt_engaged_observed"])
        self.assert_denied(r)

    def test_original_orchestrator_scenario_arithmetic_clear_still_rejects(self):
        self.policy()
        before = self.db.read_bytes()
        orchestrator = DailyScalpingOrchestrator(Settings(), self.readonly())
        r = orchestrator.review_m11_offline_shadow_safety_once(
            owner_user_id=OWNER, observed_epoch=EPOCH,
            proposed_buy_cost_idr=Decimal("120000"),
            existing_exposure_idr=Decimal("130000"),
            open_order_count=1,
            daily_realized_net_idr=Decimal("-30000"),
            realized_pnl_day_wib="2026-11-04",
        )
        snap = r["risk_halt_review"]
        self.assertTrue(snap["scenario_proposed_total_capital_within"])
        self.assertTrue(snap["scenario_order_slots_within"])
        self.assertTrue(snap["scenario_daily_loss_below_limit"])
        self.assert_denied(r)
        self.assertEqual(self.db.read_bytes(), before)

    def test_all_three_breaches_and_wrong_day_still_reject(self):
        self.policy()
        r = DailyScalpingOrchestrator(
            Settings(), self.readonly(),
        ).review_m11_offline_shadow_safety_once(
            owner_user_id=OWNER, observed_epoch=EPOCH,
            proposed_buy_cost_idr=Decimal("350000"),
            existing_exposure_idr=Decimal("1"),
            open_order_count=3,
            daily_realized_net_idr=Decimal("-40000"),
            realized_pnl_day_wib="2026-11-03",
        )
        reasons = r["risk_halt_review"]["reason_codes"]
        for reason in ("CAPITAL_SCENARIO_CEILING_BREACHED",
                       "ORDER_SCENARIO_CEILING_BREACHED",
                       "DAILY_LOSS_SCENARIO_CEILING_OR_DAY_BREACHED",
                       "WIB_DAY_UNVERIFIED"):
            self.assertIn(reason, reasons)
        self.assert_denied(r)

    def test_cli_read_does_not_modify_sqlite_or_create_sidecars(self):
        self.policy()
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        code, out, _ = self.call()
        self.assertEqual(code, 0)
        self.assert_denied(json.loads(out))
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse(Path(str(self.db) + suffix).exists())

    def test_cannot_open_missing_or_default_runtime_path(self):
        missing = self.db.with_name("m11_offline_fixture_missing.sqlite3")
        code, _, err = self.call(missing)
        self.assertEqual(code, 2)
        self.assertIn("M11_SHADOW_READONLY_FIXTURE_UNVERIFIED", err)
        self.assertFalse(missing.exists())
        with patch.dict(os.environ, {}, clear=True):
            with redirect_stdout(StringIO()) as stdout, redirect_stderr(StringIO()) as stderr:
                code = main(["--m11-risk-halt-shadow-offline-once"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("M11_SHADOW_READONLY_FIXTURE_UNVERIFIED", stderr.getvalue())

    def test_external_checkout_path_and_wrong_filename_denied(self):
        for value in (Path("/home/ubuntu/indodax-ai-bot-v2/runtime/decisions.sqlite3"),
                      self.db.with_name("decisions.sqlite3"),
                      Path("/tmp/m11_offline_fixture.sqlite3")):
            with self.subTest(path=value):
                code, out, err = self.call(value)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("M11_SHADOW_READONLY_FIXTURE_UNVERIFIED", err)

    def test_symlink_and_world_readable_fixture_denied(self):
        link = self.db.parent / "alias.sqlite3"
        link.symlink_to(self.db)
        with self.assertRaises(ValueError):
            DecisionJournal.open_m11_offline_fixture_readonly(link)
        os.chmod(self.db, 0o644)
        code, out, err = self.call()
        self.assertEqual((code, out), (2, ""))
        self.assertIn("M11_SHADOW_READONLY_FIXTURE_UNVERIFIED", err)

    def test_wal_or_journal_sidecar_denied(self):
        sidecar = Path(str(self.db) + "-wal")
        sidecar.write_bytes(b"unverified")
        code, out, err = self.call()
        self.assertEqual((code, out), (2, ""))
        self.assertIn("M11_SHADOW_READONLY_FIXTURE_UNVERIFIED", err)

    def test_corrupt_halt_does_not_promote_authority(self):
        self.policy()
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE m11_offline_risk_halt SET event_count=99 WHERE singleton=1")
        code, out, err = self.call()
        self.assertEqual((code, err), (0, ""))
        r = json.loads(out)
        self.assertIn("HALT_STATE_OR_EVENT_COUNT_UNVERIFIED",
                      r["risk_halt_review"]["reason_codes"])
        self.assertFalse(r["risk_halt_review"]["persistent_halt_engaged_observed"])
        self.assert_denied(r)

    def test_no_writable_journal_or_external_dependencies(self):
        with self.assertRaisesRegex(ValueError, "READONLY_FIXTURE_REQUIRED"):
            DailyScalpingOrchestrator(
                Settings(), self.write_journal,
            ).review_m11_offline_shadow_safety_once(
                owner_user_id=OWNER, observed_epoch=EPOCH)
        fake_ai_caller = lambda *a, **kw: {"status": "PASS"}
        with self.assertRaisesRegex(ValueError, "EXTERNAL_AUTHORITY_FORBIDDEN"):
            DailyScalpingOrchestrator(
                Settings(), self.readonly(), ai_role_caller=fake_ai_caller,
            ).review_m11_offline_shadow_safety_once(
                owner_user_id=OWNER, observed_epoch=EPOCH)

    def test_m01_go_env_denied_before_open_fixture(self):
        for env in ({"INDODAX_TRADING_ENABLED": "YES"},
                    {"INDODAX_ALLOW_LIVE_EXECUTION": "YES"},
                    {"INDODAX_EXECUTION_MODE": "LIVE"}):
            with self.subTest(env=env):
                with patch.object(
                    DecisionJournal, "open_m11_offline_fixture_readonly",
                    side_effect=AssertionError("MUST NOT OPEN"),
                ):
                    code, out, err = self.call(env=env)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("M01_LIVE_NOT_IMPLEMENTED", err)

    def test_no_http_telegram_or_order_calls(self):
        with patch("indodax_ai_bot.__main__.TelegramBotApiClient") as api, \
                patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket") as market, \
                patch("indodax_ai_bot.orchestrator.OfficialV2OrderAdapter") as order:
            code, out, err = self.call()
        self.assertEqual((code, err), (0, ""))
        self.assert_denied(json.loads(out))
        api.assert_not_called()
        market.assert_not_called()
        order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
