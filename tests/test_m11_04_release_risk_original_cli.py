"""M11-04 owner limits/kill-switch release casebook, only original V2 safe CLI.

All values are synthetic, no Bot API, Indodax GET, paid AI or service IO.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.m11_04_release_risk_offline import (
    M11ReleaseRiskScenario, review_m11_release_risk_offline,
)


def sample(**changes):
    d = {
        "capital_ceiling_idr": Decimal("250000"),
        "order_count_ceiling": 3,
        "daily_realized_loss_ceiling_idr": Decimal("30000"),
        "proposed_order_cost_idr": Decimal("120000"),
        "open_order_count": 1,
        "day_realized_net_pnl_idr": Decimal("-20000"),
        "kill_switch_engaged": False,
    }
    d.update(changes)
    return M11ReleaseRiskScenario(**d)


class M11OriginalV2RiskReleaseOfflineTests(unittest.TestCase):
    def safe(self, r):
        self.assertEqual(r["status"], "REJECT_UNVERIFIED_RELEASE_EVIDENCE")
        self.assertFalse(r["m11_live_release_authorized"])
        self.assertFalse(r["m11_04_full_acceptance"])
        self.assertFalse(r["exchange_request_or_order_sent"])
        self.assertFalse(r["owner_live_go_explicitly_verified"])
        self.assertFalse(r["kill_switch_installed_runtime_verified"])
        self.assertFalse(r["account_spendable_and_owner_fills_verified"])
        self.assertFalse(r["credentials_least_privilege_audited"])
        self.assertFalse(r["scenario_is_exchange_or_owner_evidence"])

    def test_absent_owner_limits_never_generate_default_budget_or_go(self):
        r = review_m11_release_risk_offline(settings=Settings())
        self.assertIn("OWNER_LIMITS_NOT_YET_VERIFIED", r["reason_codes"])
        self.assertIsNone(r["capital_ceiling_scenario_within"])
        self.assertIsNone(r["daily_loss_scenario_below_cap"])
        self.assertFalse(r["scenario_arithmetic_clear_unverified"])
        self.safe(r)

    def test_synthetic_good_caps_do_not_make_real_release(self):
        r = review_m11_release_risk_offline(settings=Settings(), scenario=sample())
        self.assertEqual(r["reason_codes"],
                         ["SCENARIO_ARITHMETIC_CLEAR_NOT_REAL_AUTHORITY"])
        self.assertTrue(r["scenario_arithmetic_clear_unverified"])
        self.safe(r)

    def test_capital_breach_requires_fail_closed(self):
        r = review_m11_release_risk_offline(
            settings=Settings(), scenario=sample(
                proposed_order_cost_idr=Decimal("250001")))
        self.assertIn("CAPITAL_CEILING_SCENARIO_BREACHED", r["reason_codes"])
        self.assertFalse(r["scenario_arithmetic_clear_unverified"])
        self.safe(r)

    def test_capital_equal_ceiling_is_only_theoretical(self):
        r = review_m11_release_risk_offline(
            settings=Settings(), scenario=sample(
                proposed_order_cost_idr=Decimal("250000")))
        self.assertTrue(r["capital_ceiling_scenario_within"])
        self.safe(r)

    def test_max_order_count_rejects_equal_or_over_limit(self):
        for n in (3, 4):
            with self.subTest(n=n):
                r = review_m11_release_risk_offline(
                    settings=Settings(), scenario=sample(open_order_count=n))
                self.assertIn("ORDER_COUNT_CEILING_SCENARIO_BREACHED",
                              r["reason_codes"])
                self.safe(r)

    def test_loss_at_daily_cap_or_beyond_rejects(self):
        for n in ("-30000", "-30001"):
            with self.subTest(loss=n):
                r = review_m11_release_risk_offline(
                    settings=Settings(),
                    scenario=sample(day_realized_net_pnl_idr=Decimal(n)))
                self.assertIn("DAILY_REALIZED_LOSS_SCENARIO_BREACHED",
                              r["reason_codes"])
                self.safe(r)

    def test_kill_switch_engaged_always_vetoes(self):
        r = review_m11_release_risk_offline(
            settings=Settings(),
            scenario=sample(kill_switch_engaged=True,
                            day_realized_net_pnl_idr=Decimal("100000")))
        self.assertIn("KILL_SWITCH_SCENARIO_ENGAGED", r["reason_codes"])
        self.assertFalse(r["scenario_arithmetic_clear_unverified"])
        self.safe(r)

    def test_multiple_simultaneous_breaches_are_all_recorded(self):
        r = review_m11_release_risk_offline(
            settings=Settings(),
            scenario=sample(
                proposed_order_cost_idr=Decimal("400000"),
                open_order_count=3,
                day_realized_net_pnl_idr=Decimal("-50000"),
                kill_switch_engaged=True,
            ))
        self.assertEqual(set(r["reason_codes"]), {
            "CAPITAL_CEILING_SCENARIO_BREACHED",
            "ORDER_COUNT_CEILING_SCENARIO_BREACHED",
            "DAILY_REALIZED_LOSS_SCENARIO_BREACHED",
            "KILL_SWITCH_SCENARIO_ENGAGED",
        })
        self.safe(r)

    def test_untrusted_numeric_nan_bool_and_negative_are_not_accepted(self):
        for alteration in (
            {"capital_ceiling_idr": Decimal("NaN")},
            {"capital_ceiling_idr": Decimal("0")},
            {"capital_ceiling_idr": Decimal("Infinity")},
            {"proposed_order_cost_idr": Decimal("-1")},
            {"order_count_ceiling": True},
            {"open_order_count": 1.0},
            {"day_realized_net_pnl_idr": "secret"},
            {"kill_switch_engaged": "false"},
            {"daily_realized_loss_ceiling_idr": Decimal("0")},
        ):
            with self.subTest(alt=alteration):
                r = review_m11_release_risk_offline(
                    settings=Settings(), scenario=sample(**alteration))
                self.assertEqual(r["reason_codes"],
                                 ["RISK_LIMIT_SCENARIO_UNTRUSTED"])
                self.assertFalse(r["scenario_arithmetic_clear_unverified"])
                self.safe(r)

    def test_live_settings_never_turn_report_into_go(self):
        for change in (
            {"trading_enabled": True},
            {"allow_live_execution": True},
            {"execution_mode": "LIVE"},
        ):
            with self.subTest(change=change), self.assertRaisesRegex(
                    ValueError, "M11_RELEASE_LIVE_SETTINGS_NOT_AUTHORIZED"):
                review_m11_release_risk_offline(
                    settings=Settings(**change), scenario=sample())

    def test_original_v2_cli_is_no_io_no_ledger_and_no_go(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "must_not_exist.sqlite3"
            out = StringIO()
            with (patch.dict(os.environ, {
                        "INDODAX_TRADING_ENABLED": "NO",
                        "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
                        "INDODAX_EXECUTION_MODE": "DRY_RUN",
                    }, clear=True),
                  patch("indodax_ai_bot.__main__.DecisionJournal",
                        side_effect=AssertionError("NO_LEDGER")),
                  patch("indodax_ai_bot.__main__.DailyScalpingOrchestrator",
                        side_effect=AssertionError("NO_ORCHESTRATOR")),
                  redirect_stdout(out)):
                code = main(["--m11-release-risk-offline-once", "--db", str(db)])
            self.assertEqual(code, 0)
            self.assertFalse(db.exists())
            report = json.loads(out.getvalue())
            self.assertEqual(report["stage"],
                             "M11_04_ORIGINAL_V2_RELEASE_RISK_OFFLINE")
            self.safe(report)

    def test_original_cli_go_env_denied_before_ledger_and_no_output_secrets(self):
        for key, value in (
            ("INDODAX_TRADING_ENABLED", "YES"),
            ("INDODAX_ALLOW_LIVE_EXECUTION", "YES"),
            ("INDODAX_EXECUTION_MODE", "LIVE"),
        ):
            with self.subTest(key=key):
                out, err = StringIO(), StringIO()
                with (patch.dict(os.environ, {
                          "INDODAX_TRADING_ENABLED": "NO",
                          "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
                          "INDODAX_EXECUTION_MODE": "DRY_RUN",
                          key: value, "ANY_SECRET": "CANARY_NEVER_ECHO",
                      }, clear=True),
                      patch("indodax_ai_bot.__main__.DecisionJournal",
                            side_effect=AssertionError("NO_LEDGER")),
                      redirect_stdout(out), redirect_stderr(err)):
                    rc = main(["--m11-release-risk-offline-once"])
                self.assertEqual(rc, 2)
                self.assertEqual(out.getvalue(), "")
                self.assertNotIn("CANARY_NEVER_ECHO", err.getvalue())
                self.assertNotIn("KEY", err.getvalue())
                self.assertIn("M01_LIVE_NOT_IMPLEMENTED", err.getvalue())


if __name__ == "__main__":
    unittest.main()
