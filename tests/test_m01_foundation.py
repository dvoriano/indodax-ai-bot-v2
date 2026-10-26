"""Changed-scope tests for the first real (non-executing) runtime vertical slice."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from indodax_ai_bot.config import ConfigurationError, Settings
from indodax_ai_bot.contracts import FinalDecision, MarketSnapshot, SourceReceipt
from indodax_ai_bot.journal import DecisionConflictError, DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.policy import ScalpingPolicy


class M01FoundationTests(unittest.TestCase):
    def test_default_flags_fail_closed(self):
        settings = Settings.from_env({})
        self.assertEqual(settings.decision_mode, "MANUAL")
        self.assertFalse(settings.live_allowed)
        settings.require_m01_safety()

    def test_cannot_enable_live_by_env_or_auto(self):
        for override in (
            {"INDODAX_TRADING_ENABLED": "YES"},
            {"INDODAX_ALLOW_LIVE_EXECUTION": "YES"},
            {"INDODAX_EXECUTION_MODE": "LIVE"},
        ):
            with self.subTest(override=override):
                settings = Settings.from_env(override)
                with self.assertRaises(ConfigurationError):
                    settings.require_m01_safety()
        auto = Settings.from_env({"BOT_DECISION_MODE": "AUTO"})
        self.assertFalse(auto.live_allowed)

    def test_unknown_yes_no_rejected(self):
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"INDODAX_TRADING_ENABLED": "MAYBE"})

    def test_daily_scalp_policy_minimums_and_tiers(self):
        p = ScalpingPolicy()
        self.assertTrue(p.eligible_buy(Decimal("10000"), Decimal("2")))
        self.assertFalse(p.eligible_buy(Decimal("9999"), Decimal("5")))
        self.assertFalse(p.eligible_buy(Decimal("20000"), Decimal("1.9")))
        self.assertEqual(p.sell_targets_net_percent, (Decimal("2"), Decimal("8"), Decimal("11")))
        self.assertEqual(p.reporting_timezone, "Asia/Jakarta")
        self.assertEqual(p.max_active_main_pairs, 2)

    def test_market_receipt_rejects_invalid_data(self):
        with self.assertRaises(ValueError):
            SourceReceipt("btc_usdt", 60, 60, "unknown")
        receipt = SourceReceipt("btc_idr", 60, 61, "public", False)
        with self.assertRaises(ValueError):
            MarketSnapshot(receipt, Decimal("105"), Decimal("99"))

    def test_one_mainpath_cycle_is_persistent_reject(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "state.sqlite3"
            app = DailyScalpingOrchestrator(Settings.from_env({}), DecisionJournal(db))
            response = app.run_cycle("m01-cycle")
            self.assertEqual(response.final_decision, FinalDecision.REJECT)
            self.assertEqual(response.reason, "G1_SOURCE_NOT_CONNECTED")
            self.assertFalse(response.execution_allowed)
            app.run_cycle("m01-cycle")  # same trace is idempotent
            self.assertEqual(app.journal.count(), 1)
            self.assertEqual(DecisionJournal(db).count(), 1)  # restart
            with self.assertRaises(DecisionConflictError):
                app.journal.record_reject("m01-cycle", "OTHER_REASON")

    def test_cli_is_runnable_without_secrets_network_or_trade(self):
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td) / "bot.sqlite3")
            env = {**os.environ, "INDODAX_TRADING_ENABLED": "NO",
                   "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
                   "INDODAX_EXECUTION_MODE": "DRY_RUN",
                   "BOT_DECISION_MODE": "MANUAL"}
            for command in ("--health", "--once"):
                result = subprocess.run(
                    [sys.executable, "-m", "indodax_ai_bot", "--db", db, command],
                    env=env, capture_output=True, text=True, timeout=10, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('"execution_allowed": false', result.stdout)


if __name__ == "__main__":
    unittest.main()
