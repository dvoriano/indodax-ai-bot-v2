"""M11-04 risk-halt observation on ORIGINAL V2 M07 LIMIT preflight CLI.

Only synthetic owner-private /tmp fixture; one and the same existing
original DecisionJournal SQLite. Never real risk release, Final, order,
private exchange/AI/Telegram, or M09 PAPER realized PnL promotion.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
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
from indodax_ai_bot.m07_order_adapter import OfficialV2OrderAdapter, M07Hardlocked
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from tests.test_m07_limit_preflight_original import fixture, args

EPOCH = 1793781000


class M11OriginalM07PreflightRiskReferenceTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix="m11-m07-risk-ref-", dir="/tmp")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.db = self.dir / "m11_offline_fixture.sqlite3"
        self.input = self.dir / "m07-preflight-input.json"
        self.journal = DecisionJournal(self.db)
        self.db.chmod(0o600)

    def run_cli(self, *, reference=True, db=None, data=None, env=None,
                file=None, command=None):
        path = self.input if file is None else file
        if data is None:
            data = fixture()
        if path == self.input:
            path.write_text(json.dumps(data))
            path.chmod(0o600)
        call = (["--m07-limit-preflight-offline-once",
                 "--m07-limit-preflight-file", str(path),
                 "--db", str(self.db if db is None else db)]
                if command is None else command)
        if reference:
            call.append("--m07-preflight-risk-halt-reference")
        state = {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
        }
        state.update(env or {})
        out, err = StringIO(), StringIO()
        with patch.dict(os.environ, state, clear=True):
            with redirect_stdout(out), redirect_stderr(err):
                code = main(call)
        return code, out.getvalue(), err.getvalue()

    def assert_reject(self, reply):
        self.assertIsNone(reply["final_decision"])
        self.assertFalse(reply["execution_allowed"])
        self.assertFalse(reply["live_trading_authorized"])
        self.assertFalse(reply["m07_limit_preflight"]["execution_allowed"])
        self.assertFalse(reply["m07_last_moment_gate"]["execution_allowed"])
        r = reply["m07_risk_halt_readonly_reference"]
        self.assertEqual(r["status"], "REJECT_OFFLINE_UNVERIFIED_RELEASE")
        self.assertEqual(r["stage"], "M07_M11_ORIGINAL_OFFLINE_PREFLIGHT_RISK_OBSERVATION")
        self.assertEqual(r["owner_identity_status"], "UNKNOWN_SYNTHETIC_FIXTURE_ONLY")
        self.assertEqual(r["existing_exposure_status"], "UNKNOWN_NO_AUTHENTICATED_ACCOUNT")
        self.assertEqual(r["open_orders_status"],
                         "UNKNOWN_NO_AUTHENTICATED_EXCHANGE_STATE")
        self.assertEqual(r["realized_daily_loss_status"],
                         "UNKNOWN_NO_EXCHANGE_SETTLED_IDR_EVIDENCE")
        self.assertEqual(r["g1_source_status"], "UNVERIFIED_NO_INDEPENDENT_ISSUER")
        self.assertFalse(r["m07_order_intent_created"])
        self.assertFalse(r["order_sent"])
        self.assertFalse(r["execution_allowed"])
        self.assertFalse(r["m11_live_release_authorized"])
        self.assertIsNone(r["owner_final_decision"])
        m11 = r["m11_risk_halt"]
        self.assertEqual(m11["status"], "REJECT_SHADOW_OFFLINE_ONLY")
        self.assertTrue(m11["same_original_decision_journal_review_invoked"])
        self.assertFalse(m11["m11_live_release_authorized"])
        self.assertFalse(m11["execution_allowed"])
        self.assertIsNone(m11["owner_final_decision"])
        snap = m11["risk_halt_review"]
        self.assertIsNone(snap["scenario_proposed_total_capital_within"])
        self.assertIsNone(snap["scenario_order_slots_within"])
        self.assertIsNone(snap["scenario_daily_loss_below_limit"])
        self.assertFalse(snap["owner_identity_authenticated"])
        self.assertFalse(snap["realized_pnl_settlement_verified"])
        self.assertFalse(snap["scenario_is_real_exchange_evidence"])

    def policy(self):
        self.journal.record_m11_offline_risk_limits_once(
            owner_user_id=1, policy_id="a" * 24,
            capital_ceiling_idr=Decimal("200000"),
            max_open_orders=2, daily_loss_ceiling_idr=Decimal("10000"),
            recorded_epoch=EPOCH,
        )

    def test_original_cli_to_m07_math_late_gate_m11_same_journal_no_policy(self):
        rc, out, err = self.run_cli()
        self.assertEqual((rc, err), (0, ""))
        reply = json.loads(out)
        self.assertTrue(reply["m07_limit_preflight"]["checks_passed_unverified"])
        self.assertEqual(reply["m07_last_moment_gate"]["status"],
                         "BLOCKED_UNVERIFIED_FULL_ORDER_PRECONDITIONS")
        self.assertIn("OWNER_OFFLINE_POLICY_UNVERIFIED_OR_MISSING",
                      reply["m07_risk_halt_readonly_reference"][
                          "m11_risk_halt"]["risk_halt_review"]["reason_codes"])
        self.assert_reject(reply)

    def test_owner_entered_policy_engaged_halt_not_release(self):
        self.policy()
        rc, out, err = self.run_cli()
        self.assertEqual((rc, err), (0, ""))
        self.assert_reject(json.loads(out))
        snap = json.loads(out)["m07_risk_halt_readonly_reference"][
            "m11_risk_halt"]["risk_halt_review"]
        self.assertTrue(snap["offline_policy_present_for_caller"])
        self.assertTrue(snap["persistent_halt_engaged_observed"])

    def test_clean_hypothetical_preflight_price_qty_fee_never_real_exposure(self):
        self.policy()
        r = json.loads(self.run_cli()[1])
        self.assertTrue(r["m07_limit_preflight"]["checks_passed_unverified"])
        self.assert_reject(r)
        self.assertIn("RISK_OBSERVATION_MISSING", r[
            "m07_risk_halt_readonly_reference"]["m11_risk_halt"][
                "risk_halt_review"]["reason_codes"])

    def test_bad_hypothetical_spread_still_reports_m11_reject(self):
        self.policy()
        data = fixture()
        data["best_bid_idr"] = "50"
        rc, out, err = self.run_cli(data=data)
        self.assertEqual((rc, err), (0, ""))
        reply = json.loads(out)
        self.assertFalse(reply["m07_limit_preflight"]["checks_passed_unverified"])
        self.assertFalse(reply["m07_risk_halt_readonly_reference"][
            "m07_limit_math_reference_passed_unverified"])
        self.assert_reject(reply)

    def test_missing_bad_schema_never_calls_m11_as_approval(self):
        rc, out, err = self.run_cli(data={"symbol": "BTCIDR"})
        self.assertEqual((rc, out), (3, ""))
        self.assertIn("M07_PREFLIGHT_FILE_SCHEMA_INVALID", err)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM m11_offline_risk_limits").fetchone()[0], 0)

    def test_readonly_m11_reference_leaves_whole_original_journal_bytes_untouched(self):
        self.policy()
        before = sha256(self.db.read_bytes()).hexdigest()
        for _ in range(2):
            rc, out, err = self.run_cli()
            self.assertEqual((rc, err), (0, ""))
            self.assert_reject(json.loads(out))
            self.assertEqual(sha256(self.db.read_bytes()).hexdigest(), before)
            for suffix in ("-wal", "-shm", "-journal"):
                self.assertFalse(Path(str(self.db) + suffix).exists())

    def test_no_new_m07_order_intents_and_existing_unknown_unchanged(self):
        self.policy()
        before = self.journal.m07_offline_intent_summary()
        self.assert_reject(json.loads(self.run_cli()[1]))
        after = self.journal.m07_offline_intent_summary()
        self.assertEqual(before, after)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM m07_offline_order_intents").fetchone()[0], 0)

    def test_original_cli_default_without_flag_is_unchanged(self):
        rc, out, err = self.run_cli(reference=False)
        self.assertEqual((rc, err), (0, ""))
        r = json.loads(out)
        self.assertNotIn("m07_risk_halt_readonly_reference", r)
        self.assertFalse(r["execution_allowed"])

    def test_flag_not_usable_for_unrelated_cli_action(self):
        rc, out, err = self.run_cli(
            command=["--health"], reference=True)
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("M07_PREFLIGHT_RISK_REFERENCE_REQUIRES_OFFLINE_PREFLIGHT", err)

    def test_missing_and_other_named_or_runtime_db_denied_before_creation(self):
        for p in (self.dir / "missing.sqlite3",
                  self.dir / "decisions.sqlite3",
                  Path("runtime/m11_offline_fixture.sqlite3"),
                  Path("/tmp/m11_offline_fixture.sqlite3")):
            with self.subTest(db=str(p)):
                rc, out, err = self.run_cli(db=p)
                self.assertEqual((rc, out), (2, ""))
                self.assertIn(
                    "M07_PREFLIGHT_RISK_REFERENCE_PRIVATE_FIXTURE_REQUIRED", err)
        self.assertFalse((self.dir / "missing.sqlite3").exists())

    def test_private_scenario_file_must_live_in_same_private_fixture_directory(self):
        other = TemporaryDirectory(prefix="m11-m07-outside-", dir="/tmp")
        self.addCleanup(other.cleanup)
        p = Path(other.name) / "other.json"
        p.write_text(json.dumps(fixture()))
        p.chmod(0o600)
        rc, out, err = self.run_cli(file=p)
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("M07_PREFLIGHT_RISK_REFERENCE_PRIVATE_FIXTURE_REQUIRED", err)

    def test_world_readable_database_and_directory_denied(self):
        self.db.chmod(0o644)
        self.assertEqual(self.run_cli()[0], 2)
        self.db.chmod(0o600)
        self.dir.chmod(0o755)
        self.assertEqual(self.run_cli()[0], 2)

    def test_symlink_path_and_wal_sidecars_denied(self):
        alias_dir = self.dir / "alias"
        alias_dir.symlink_to(self.dir, target_is_directory=True)
        badpath = alias_dir / "m11_offline_fixture.sqlite3"
        rc, out, err = self.run_cli(db=badpath)
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("M07_PREFLIGHT_RISK_REFERENCE_PRIVATE_FIXTURE_REQUIRED", err)
        sidecar = Path(str(self.db) + "-wal")
        sidecar.write_bytes(b"unverified")
        self.assertEqual(self.run_cli()[0], 2)

    def test_corrupted_persistent_halt_never_mints_owner_final(self):
        self.policy()
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE m11_offline_risk_halt SET event_count=100")
        rc, out, err = self.run_cli()
        self.assertEqual((rc, err), (0, ""))
        r = json.loads(out)
        self.assert_reject(r)
        reasons = r["m07_risk_halt_readonly_reference"][
            "m11_risk_halt"]["risk_halt_review"]["reason_codes"]
        self.assertIn("HALT_STATE_OR_EVENT_COUNT_UNVERIFIED", reasons)

    def test_live_env_denied_before_fixture_open_or_constructor(self):
        for env in ({"INDODAX_TRADING_ENABLED": "YES"},
                    {"INDODAX_ALLOW_LIVE_EXECUTION": "YES"},
                    {"INDODAX_EXECUTION_MODE": "LIVE"}):
            with self.subTest(env=env):
                with patch.object(
                    DecisionJournal, "open_m11_offline_fixture_readonly",
                    side_effect=AssertionError("MUST_NOT_OPEN"),
                ):
                    rc, out, err = self.run_cli(env=env)
                self.assertEqual((rc, out), (2, ""))
                self.assertIn("M01_LIVE_NOT_IMPLEMENTED", err)

    def test_no_exchange_telegram_paid_provider_or_order_call(self):
        with (patch("indodax_ai_bot.__main__.TelegramBotApiClient") as tg,
              patch("indodax_ai_bot.__main__.SumopodChatAdapter") as ai,
              patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket") as mk,
              patch("indodax_ai_bot.orchestrator.OfficialV2OrderAdapter") as od):
            rc, out, err = self.run_cli()
        self.assertEqual((rc, err), (0, ""))
        self.assert_reject(json.loads(out))
        for mock in (tg, ai, mk, od):
            mock.assert_not_called()

    def test_direct_original_orchestrator_rejects_owner_source_injection(self):
        from indodax_ai_bot.owner_control import OwnerDecisionLedger
        owner = OwnerDecisionLedger(self.dir / "owner.sqlite3",
                                     owner_user_id=76543)
        app = DailyScalpingOrchestrator(
            Settings(), self.journal, owner_control=owner)
        v = app.assess_m07_limit_buy_preflight_offline(**args())
        last = app.review_m07_last_moment_gate_offline(
            limit_preview=v, now_epoch=EPOCH)
        with self.assertRaisesRegex(ValueError, "EXTERNAL_SOURCE_DENIED"):
            app.review_m07_preflight_m11_risk_halt_offline_once(
                limit_preview=v, last_moment=last)

    def test_direct_forged_preflight_cannot_turn_into_risk_authority(self):
        app = DailyScalpingOrchestrator(Settings(), self.journal)
        v = app.assess_m07_limit_buy_preflight_offline(**args())
        with self.assertRaisesRegex(ValueError, "UNTRUSTED_INPUT"):
            app.review_m07_preflight_m11_risk_halt_offline_once(
                limit_preview=v,
                last_moment={"execution_allowed": True, "order_sent": False})
        with self.assertRaisesRegex(ValueError, "UNTRUSTED_INPUT"):
            app.review_m07_preflight_m11_risk_halt_offline_once(
                limit_preview="forged",
                last_moment={"execution_allowed": False, "order_sent": False})

    def test_original_executor_create_status_cancel_remain_hardlocked(self):
        with self.assertRaises(M07Hardlocked):
            OfficialV2OrderAdapter().dispatch()
        with self.assertRaises(M07Hardlocked):
            OfficialV2OrderAdapter().create_order()

    def test_m09_paper_realized_profit_not_used_as_m11_risk_input(self):
        self.policy()
        app = DailyScalpingOrchestrator(Settings(), self.journal)
        app.run_m09_original_paper_cycle_offline_once(
            action="BUY", details={
                "paper_id": "paper_m11_m07", "symbol": "XRPIDR",
                "buy_qty": Decimal("4"), "buy_price_idr": Decimal("25000"),
                "buy_all_in_fee_idr": Decimal("200"),
                "buy_epoch": 1793694000,
            })
        app.run_m09_original_paper_cycle_offline_once(
            action="SELL", details={
                "paper_id": "paper_m11_m07",
                "paper_exit_id": "pexit_m11_m07",
                "sell_qty": Decimal("4"),
                "sell_price_idr": Decimal("28000"),
                "sell_all_in_fee_idr": Decimal("500"),
                "incremental_slippage_idr": Decimal("100"),
                "sell_epoch": 1793695800,
            })
        rc, out, err = self.run_cli()
        self.assertEqual((rc, err), (0, ""))
        self.assert_reject(json.loads(out))
        self.assertIn("RISK_OBSERVATION_MISSING", json.loads(out)[
            "m07_risk_halt_readonly_reference"]["m11_risk_halt"][
                "risk_halt_review"]["reason_codes"])


if __name__ == "__main__":
    unittest.main()
