"""M03 original historical caller: OOS assumed-cost scenario, NO TRADE.

Synthetic closed OHLC only; no HTTP, account, paid AI, Telegram, orders or
source attestation. Tests economic sensitivity not exchange-executed fills.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.contracts import FinalDecision
from indodax_ai_bot.deep_analysis import (
    _m03_assumed_friction_holdout,
    evaluate_source_backed_candle_research,
)
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.public_market import Candle, PublicCandleHistory, PublicDataError


LABEL = "SURGE_BREAKOUT_OBSERVED"
PULLBACK = "PULLBACK_REBOUND_OBSERVED"
START = 1800000000


def fake_history(*, lose_holdout=False):
    """Fixed training setup events with forward 3m realized OHLC outcomes."""
    bars = []
    surges = {50, 80, 110, 140, 170, 200, 230, 260, 290, 320}
    for i in range(360):
        op, cl, hi, lo, vol = (Decimal("100"), Decimal("100"),
                               Decimal("101"), Decimal("99"), Decimal("10"))
        if i in surges:
            op, cl, hi, lo, vol = (Decimal("100"), Decimal("110"),
                                   Decimal("111"), Decimal("99"), Decimal("100"))
        elif any(i in (s+1, s+2, s+3) for s in surges):
            if lose_holdout and i >= 252:
                op, cl, hi, lo = (Decimal("100"), Decimal("100"),
                                   Decimal("101"), Decimal("99"))
            else:
                op, cl, hi, lo = (Decimal("110"), Decimal("115"),
                                   Decimal("116"), Decimal("109"))
        bars.append(Candle(START + 60*i, 60, op, hi, lo, cl, vol))
    return PublicCandleHistory(
        pair="btc_idr",
        from_epoch=START,
        to_epoch=START + len(bars)*60,
        observed_epoch=START + len(bars)*60+10,
        exchange_epoch=START + len(bars)*60+10,
        candles=tuple(bars),
    )


class M03HoldoutFrictionMainpathTests(unittest.TestCase):
    def test_friction_costs_holdout_are_train_selected_and_never_trade(self):
        report = evaluate_source_backed_candle_research(fake_history())
        self.assertEqual(report["status"],
                         "HOLDOUT_DESCRIPTIVE_ONLY_NOT_VALIDATED")
        economics = report["modeled_after_cost_holdout"]
        self.assertEqual(economics["setup_selected_on_training_only"], LABEL)
        self.assertGreaterEqual(economics["setup_train_nonoverlapping_count"], 5)
        self.assertGreater(economics["setup_holdout_nonoverlapping_count"], 0)
        self.assertEqual(economics["scenario_costs"]["LOW_ASSUMED"][
            "total_friction_bps_assumed"], "75")
        self.assertEqual(economics["scenario_costs"]["BASE_ASSUMED"][
            "total_friction_bps_assumed"], "140")
        self.assertEqual(economics["scenario_costs"]["STRESS_ASSUMED"][
            "total_friction_bps_assumed"], "260")
        self.assertEqual(economics["min_net_profit_idr_policy"], "10000")
        self.assertEqual(economics["max_active_main_pairs_policy"], 2)
        self.assertEqual(economics["eligible_live_candidates"], 0)
        self.assertTrue(economics["assumptions_not_actual_exchange_fees_or_spread"])
        self.assertFalse(economics["out_of_sample_net_edge_validated"])
        self.assertFalse(economics["source_g1_verified"])
        self.assertFalse(economics["execution_allowed"])
        self.assertFalse(report["g1_verified"])
        self.assertFalse(report["net_roi_verified"])
        self.assertFalse(report["execution_allowed"])

    def test_holdout_outcomes_never_select_or_fit_training_setup(self):
        good = evaluate_source_backed_candle_research(fake_history())
        bad = evaluate_source_backed_candle_research(
            fake_history(lose_holdout=True)
        )
        a, b = good["modeled_after_cost_holdout"], bad[
            "modeled_after_cost_holdout"]
        self.assertEqual(a["setup_selected_on_training_only"],
                         b["setup_selected_on_training_only"])
        self.assertEqual(a["setup_train_nonoverlapping_count"],
                         b["setup_train_nonoverlapping_count"])
        self.assertNotEqual(
            a["holdout_sensitivity"]["BASE_ASSUMED"][
                "mean_forward_3m_close_to_close_after_assumed_cost_bps"],
            b["holdout_sensitivity"]["BASE_ASSUMED"][
                "mean_forward_3m_close_to_close_after_assumed_cost_bps"],
        )
        self.assertFalse(a["holdout_used_to_select_setup"])
        self.assertFalse(b["holdout_used_to_select_setup"])

    def test_insufficient_evidence_is_unselected_not_fabricated_edge(self):
        observations = [(3*i, LABEL, Decimal("1000")) for i in range(3)]
        future = [(100 + i*3, LABEL, Decimal("9999")) for i in range(9)]
        result = _m03_assumed_friction_holdout(observations, future)
        self.assertIsNone(result["setup_selected_on_training_only"])
        self.assertEqual(result["eligible_live_candidates"], 0)
        self.assertEqual(result["holdout_sensitivity"]["BASE_ASSUMED"][
            "holdout_events"], 0)
        self.assertFalse(result["execution_allowed"])

    def test_original_orchestrator_records_no_trade_research_and_costs(self):
        class LocalPublicHistory:
            def closed_1m_research_history(self, pair, *, minutes):
                self_args = (pair, minutes)
                if self_args != ("btc_idr", 360):
                    raise AssertionError("UNEXPECTED_PUBLIC_HISTORY_CALL")
                return fake_history()

        with TemporaryDirectory() as tmp:
            original = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(Path(tmp)/"journal.sqlite3")
            )
            result = original.run_public_history_research_cycle(
                "m03-cost-oos-1", pair="btc_idr", minutes=360,
                market=LocalPublicHistory(),
            )
        self.assertEqual(result.final_decision, FinalDecision.REJECT)
        self.assertFalse(result.execution_allowed)
        self.assertEqual(
            result.public_research["modeled_after_cost_holdout"][
                "setup_selected_on_training_only"], LABEL,
        )
        self.assertFalse(
            result.public_research["modeled_after_cost_holdout"][
                "execution_allowed"]
        )


if __name__ == "__main__":
    unittest.main()
