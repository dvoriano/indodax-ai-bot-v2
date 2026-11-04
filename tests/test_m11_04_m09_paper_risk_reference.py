"""M11-04 report inside ORIGINAL M09 PAPER SUMMARY, same SQLite file.

Opt-in synthetic-only CLI → original orchestrator → original
DecisionJournal M11 read-only query. Never turn PAPER net PnL into
realized account loss or a Final, execution/Risk or LIVE approval.
"""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator

EPOCH = 1793781000
BUY = {
    "action": "BUY", "paper_id": "paper_m11_ref_001",
    "symbol": "XRPIDR", "buy_qty": "4", "buy_price_idr": "25000",
    "buy_all_in_fee_idr": "200", "buy_epoch": 1793694000,
}
SELL = {
    "action": "SELL", "paper_id": "paper_m11_ref_001",
    "paper_exit_id": "pexit_m11_ref_001", "sell_qty": "4",
    "sell_price_idr": "28000", "sell_all_in_fee_idr": "500",
    "incremental_slippage_idr": "100", "sell_epoch": 1793695800,
}
SUMMARY = {"action": "SUMMARY", "day_wib": None}


class M11M09OriginalPaperRiskReferenceTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix="m11-m09-paper-", dir="/tmp")
        self.addCleanup(tmp.cleanup)
        self.parent = Path(tmp.name)
        self.db = self.parent / "m11_offline_fixture.sqlite3"
        self.paper_file = self.parent / "paper.json"
        self.journal = DecisionJournal(self.db)
        os.chmod(self.db, 0o600)

    def cli(self, action=SUMMARY, *, reference=False, db=None, env=None):
        self.paper_file.write_text(json.dumps(action))
        self.paper_file.chmod(0o600)
        args = [
            "--m09-paper-offline-once",
            "--m09-paper-file", str(self.paper_file),
            "--db", str(self.db if db is None else db),
        ]
        if reference:
            args += ["--m09-paper-risk-halt-reference"]
        flags = {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
        }
        flags.update(env or {})
        out, err = StringIO(), StringIO()
        with patch.dict(os.environ, flags, clear=True):
            with redirect_stdout(out), redirect_stderr(err):
                code = main(args)
        return code, out.getvalue(), err.getvalue()

    def check_rejected(self, raw):
        self.assertIsNone(raw["final_decision"])
        self.assertFalse(raw["execution_allowed"])
        self.assertFalse(raw["live_trading_authorized"])
        report = raw["m09_paper"]
        self.assertIsNone(report["owner_order_final"])
        self.assertFalse(report["execution_allowed"])
        self.assertFalse(report["order_sent"])
        self.assertFalse(report["m11_live_release_authorized"])
        self.assertFalse(report["m11_paper_pnl_used_as_realized_loss"])
        self.assertEqual(report["m11_risk_reference_owner_identity"],
                         "FIXTURE_ONLY_UNAUTHENTICATED")
        self.assertEqual(report["m11_risk_reference_real_account_loss_status"],
                         "UNKNOWN_NO_EXCHANGE_SETTLED_IDR_EVIDENCE")
        risk = report["m11_risk_halt_readonly_reference"]
        self.assertEqual(risk["status"], "REJECT_SHADOW_OFFLINE_ONLY")
        self.assertTrue(risk["same_original_decision_journal_review_invoked"])
        self.assertFalse(risk["m11_live_release_authorized"])
        self.assertFalse(risk["execution_allowed"])
        self.assertIsNone(risk["owner_final_decision"])
        self.assertFalse(risk["risk_halt_review"]["realized_pnl_settlement_verified"])
        self.assertFalse(risk["risk_halt_review"]["owner_identity_authenticated"])
        self.assertFalse(risk["risk_halt_review"]["scenario_is_real_exchange_evidence"])
        self.assertIsNone(risk["risk_halt_review"]["scenario_daily_loss_below_limit"])
        self.assertIn("RISK_OBSERVATION_MISSING",
                      risk["risk_halt_review"]["reason_codes"])

    def test_original_cli_to_original_paper_and_same_m11_journal_missing_policy(self):
        rc, out, err = self.cli(reference=True)
        self.assertEqual((rc, err), (0, ""))
        raw = json.loads(out)
        self.assertEqual(raw["m09_paper"]["action"], "SUMMARY")
        self.assertEqual(raw["m09_paper"]["paper"]["source"],
                         "M09_PAPER_SCENARIO_ONLY")
        self.assertIn("OWNER_OFFLINE_POLICY_UNVERIFIED_OR_MISSING",
                      raw["m09_paper"]["m11_risk_halt_readonly_reference"][
                          "risk_halt_review"]["reason_codes"])
        self.check_rejected(raw)

    def test_original_cli_owner_entered_policy_not_approval(self):
        self.journal.record_m11_offline_risk_limits_once(
            owner_user_id=1, policy_id="a" * 24,
            capital_ceiling_idr=Decimal("300000"),
            max_open_orders=3, daily_loss_ceiling_idr=Decimal("40000"),
            recorded_epoch=EPOCH,
        )
        rc, out, err = self.cli(reference=True)
        self.assertEqual((rc, err), (0, ""))
        risk = json.loads(out)["m09_paper"]["m11_risk_halt_readonly_reference"]
        self.assertTrue(risk["risk_halt_review"]["offline_policy_present_for_caller"])
        self.assertTrue(risk["risk_halt_review"]["persistent_halt_engaged_observed"])
        self.check_rejected(json.loads(out))

    def test_profitable_paper_pnl_not_real_m11_daily_loss(self):
        self.assertEqual(self.cli(action=BUY)[0], 0)
        self.assertEqual(self.cli(action=SELL)[0], 0)
        rc, out, err = self.cli(reference=True)
        self.assertEqual((rc, err), (0, ""))
        raw = json.loads(out)
        self.assertEqual(raw["m09_paper"]["paper"]["paper_net_pnl_idr_reference"],
                         "11200")
        self.assertIsNone(raw["m09_paper"]["paper"]["real_exchange_realized_pnl_idr"])
        self.check_rejected(raw)

    def test_losing_paper_pnl_not_real_m11_daily_loss(self):
        self.assertEqual(self.cli(action=BUY)[0], 0)
        sell = dict(SELL, sell_price_idr="24000", sell_all_in_fee_idr="150",
                    incremental_slippage_idr="50")
        self.assertEqual(self.cli(action=sell)[0], 0)
        rc, out, err = self.cli(reference=True)
        self.assertEqual((rc, err), (0, ""))
        raw = json.loads(out)
        self.assertEqual(raw["m09_paper"]["paper"]["paper_net_pnl_idr_reference"],
                         "-4400")
        self.check_rejected(raw)

    def test_summary_reference_does_not_mutate_either_namespace(self):
        self.journal.record_m11_offline_risk_limits_once(
            owner_user_id=1, policy_id="a" * 24,
            capital_ceiling_idr=Decimal("100"), max_open_orders=1,
            daily_loss_ceiling_idr=Decimal("50"), recorded_epoch=EPOCH,
        )
        before = sha256(self.db.read_bytes()).hexdigest()
        for _ in range(2):
            rc, out, err = self.cli(reference=True)
            self.assertEqual((rc, err), (0, ""))
            self.check_rejected(json.loads(out))
            self.assertEqual(sha256(self.db.read_bytes()).hexdigest(), before)
            for suffix in ("-wal", "-shm", "-journal"):
                self.assertFalse(Path(str(self.db) + suffix).exists())
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM m11_offline_risk_limits").fetchone()[0], 1)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM m09_paper_simulated_sells").fetchone()[0], 0)

    def test_default_paper_summary_remains_unchanged(self):
        rc, out, err = self.cli()
        self.assertEqual((rc, err), (0, ""))
        report = json.loads(out)["m09_paper"]
        self.assertNotIn("m11_risk_halt_readonly_reference", report)
        self.assertNotIn("m11_live_release_authorized", report)
        self.assertFalse(report["execution_allowed"])

    def test_optin_rejects_buy_sell_without_paper_mutation(self):
        for case in (BUY, SELL):
            rc, out, err = self.cli(action=case, reference=True)
            self.assertEqual((rc, out), (3, ""))
            self.assertIn("M09_PAPER_RISK_REFERENCE_SUMMARY_ONLY", err)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM m09_paper_simulated_buys").fetchone()[0], 0)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM m09_paper_simulated_sells").fetchone()[0], 0)

    def test_optin_rejects_missing_runtime_and_wrong_name_before_constructor(self):
        missing = self.parent / "missing.sqlite3"
        for db in (missing, self.parent / "decisions.sqlite3",
                   Path("runtime/decisions.sqlite3"),
                   Path("/tmp/m11_offline_fixture.sqlite3")):
            with self.subTest(db=db):
                rc, out, err = self.cli(reference=True, db=db)
                self.assertEqual((rc, out), (2, ""))
                self.assertIn("M09_PAPER_", err)
                if db == missing:
                    self.assertFalse(db.exists())

    def test_optin_rejects_world_readable_file_or_directory(self):
        os.chmod(self.db, 0o644)
        rc, out, err = self.cli(reference=True)
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("M09_PAPER_RISK_REFERENCE_PRIVATE_FIXTURE_REQUIRED", err)
        os.chmod(self.db, 0o600)
        os.chmod(self.parent, 0o755)
        rc, out, err = self.cli(reference=True)
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("M09_PAPER_RISK_REFERENCE_PRIVATE_FIXTURE_REQUIRED", err)

    def test_optin_rejects_symlink_and_sidecar(self):
        alias_dir = self.parent / "alias"
        alias_dir.symlink_to(self.parent, target_is_directory=True)
        fake = alias_dir / "m11_offline_fixture.sqlite3"
        rc, out, err = self.cli(reference=True, db=fake)
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("M09_PAPER_RISK_REFERENCE_PRIVATE_FIXTURE_REQUIRED", err)
        wal = Path(str(self.db) + "-wal")
        wal.write_bytes(b"unverified")
        rc, out, err = self.cli(reference=True)
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("M09_PAPER_RISK_REFERENCE_PRIVATE_FIXTURE_REQUIRED", err)

    def test_optin_with_unrelated_cli_action_refused(self):
        out, err = StringIO(), StringIO()
        with patch.dict(os.environ, {}, clear=True):
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["--health", "--m09-paper-risk-halt-reference"])
        self.assertEqual((code, out.getvalue()), (2, ""))
        self.assertIn("M09_PAPER_RISK_REFERENCE_SUMMARY_ONLY", err.getvalue())

    def test_corrupted_halt_stays_reject_not_owner_final(self):
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE m11_offline_risk_halt SET event_count=100")
        rc, out, err = self.cli(reference=True)
        self.assertEqual((rc, err), (0, ""))
        report = json.loads(out)
        self.assertFalse(report["m09_paper"]["m11_risk_halt_readonly_reference"][
            "risk_halt_review"]["persistent_halt_engaged_observed"])
        self.assertIn("HALT_STATE_OR_EVENT_COUNT_UNVERIFIED",
                      report["m09_paper"]["m11_risk_halt_readonly_reference"][
                          "risk_halt_review"]["reason_codes"])
        self.check_rejected(report)

    def test_live_env_denies_before_reading_or_writing(self):
        for flags in ({"INDODAX_TRADING_ENABLED": "YES"},
                      {"INDODAX_ALLOW_LIVE_EXECUTION": "YES"},
                      {"INDODAX_EXECUTION_MODE": "LIVE"}):
            with self.subTest(flags=flags):
                with patch.object(
                    DecisionJournal, "open_m11_offline_fixture_readonly",
                    side_effect=AssertionError("MUST_NOT_OPEN"),
                ):
                    code, out, err = self.cli(reference=True, env=flags)
                self.assertEqual((code, out), (2, ""))
                self.assertIn("M01_LIVE_NOT_IMPLEMENTED", err)

    def test_no_exchange_telegram_paid_ai_or_order_calls(self):
        with patch("indodax_ai_bot.__main__.TelegramBotApiClient") as telegram, \
             patch("indodax_ai_bot.__main__.SumopodChatAdapter") as provider, \
             patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket") as market, \
             patch("indodax_ai_bot.orchestrator.OfficialV2OrderAdapter") as order:
            rc, out, err = self.cli(reference=True)
        self.assertEqual((rc, err), (0, ""))
        self.check_rejected(json.loads(out))
        telegram.assert_not_called()
        provider.assert_not_called()
        market.assert_not_called()
        order.assert_not_called()

    def test_direct_original_orchestrator_cannot_accept_fake_owner_or_provider(self):
        from indodax_ai_bot.owner_control import OwnerDecisionLedger
        owner_db = self.parent / "owner.sqlite3"
        owner = OwnerDecisionLedger(owner_db, owner_user_id=88773)
        direct = DailyScalpingOrchestrator(
            Settings(), DecisionJournal(self.db), owner_control=owner,
        )
        with self.assertRaisesRegex(ValueError, "EXTERNAL_SOURCE_DENIED"):
            direct.run_m09_original_paper_cycle_offline_once(
                action="SUMMARY", details={"day_wib": None},
                include_m11_risk_halt_readonly_reference=True,
            )
        with self.assertRaisesRegex(ValueError, "SUMMARY_ONLY"):
            direct.run_m09_original_paper_cycle_offline_once(
                action="BUY", details={},
                include_m11_risk_halt_readonly_reference=True,
            )


if __name__ == "__main__":
    unittest.main()
