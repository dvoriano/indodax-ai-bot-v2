"""M08 original mainpath SELL-policy review without authenticated positions."""
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.position_manager import PositionManager


class M08OriginalPositionMainpathTests(unittest.TestCase):
    def test_existing_mainpath_blocks_missing_exchange_ownership(self):
        with TemporaryDirectory() as td:
            orchestrator = DailyScalpingOrchestrator(
                Settings.from_env({
                    "INDODAX_TRADING_ENABLED": "NO",
                    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
                    "INDODAX_EXECUTION_MODE": "DRY_RUN",
                }), DecisionJournal(Path(td) / "decisions.sqlite3"),
            )
            result = orchestrator.run_cycle("m08-scoped-missing-fill")
            self.assertEqual(result.final_decision.value, "REJECT")
            self.assertFalse(result.execution_allowed)
            self.assertEqual(
                result.position_review["reason"],
                "M08_EXCHANGE_OWNED_BUY_FILLS_NOT_CONNECTED",
            )
            self.assertFalse(result.position_review["execution_allowed"])
            self.assertFalse(result.position_review["bot_ownership_verified"])
            self.assertFalse(result.position_review["realized"])

    def test_position_manager_reuses_existing_net_and_hold_math_without_order(self):
        m = PositionManager()
        out = m.review_unverified_quote(
            actual_buy_fill_epoch=100, observed_epoch=3700,
            filled_buy_quantity=D("10"),
            filled_buy_notional_idr=D("1000"),
            allocated_buy_fees_idr=D("2"),
            quantity_to_sell=D("4"),
            expected_sell_notional_idr=D("440"),
            estimated_sell_fees_idr=D("1"),
            incremental_slippage_idr=D("1"),
        )
        self.assertEqual(out.status, "REJECT")
        self.assertEqual(out.reason, "M08_UNVERIFIED_LOT_AND_SETTLEMENT")
        self.assertEqual(out.highest_net_tier, "T2")
        self.assertEqual(out.hold_phase, "TIME_STOP_PROTECTIVE_REVIEW")
        self.assertTrue(out.protective_review_required)
        self.assertFalse(out.bot_ownership_verified)
        self.assertFalse(out.execution_allowed)

    def test_partial_quantity_exceeding_candidate_buy_is_rejected(self):
        m = PositionManager()
        with self.assertRaises(ValueError):
            m.review_unverified_quote(
                actual_buy_fill_epoch=100, observed_epoch=200,
                filled_buy_quantity=D("1"),
                filled_buy_notional_idr=D("100"),
                allocated_buy_fees_idr=D("0"),
                quantity_to_sell=D("2"),
                expected_sell_notional_idr=D("120"),
                estimated_sell_fees_idr=D("0"),
                incremental_slippage_idr=D("0"),
            )

    def test_no_buy_fill_is_never_an_owned_position(self):
        m = PositionManager()
        out = m.review()
        self.assertEqual(out.status, "REJECT")
        self.assertFalse(out.execution_allowed)
        self.assertIsNone(out.highest_net_tier)
        self.assertIsNone(out.hold_phase)


if __name__ == "__main__":
    unittest.main()
