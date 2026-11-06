"""Original same-candidate scalping evidence preserves watch and veto split."""
from decimal import Decimal
import unittest

from indodax_ai_bot.deep_analysis import DeepCandidate
from indodax_ai_bot.scalping_evidence import project_scalping_evidence_nonlive


class OriginalScalpingEvidenceTests(unittest.TestCase):
    def candidate(self, **changes):
        row = dict(
            pair="uai_idr", score=75, state="PUBLIC_EVIDENCE_UNATTESTED",
            rejection_reason=None, rsi14=Decimal("68"),
            volume_ratio=Decimal("3.2"), spread_bps=Decimal("25"),
            visible_buy_idr=Decimal("25000000"),
            visible_sell_idr=Decimal("9000000"),
            visible_buy_trade_count_sampled=42,
            visible_sell_trade_count_sampled=23,
            book_imbalance=Decimal("0.21"),
            market_structure_diagnostic={
                "label": "SURGE_BREAKOUT_OBSERVED"},
        )
        row.update(changes)
        return DeepCandidate(**row)

    def test_continuation_stays_watched_without_claiming_buy(self):
        row = self.candidate().summary()
        evidence = row["scalping_evidence"]
        self.assertEqual(evidence["watch_classification"],
                         "MOMENTUM_CONTINUATION_WATCH")
        self.assertTrue(evidence["already_pumped_not_automatic_exclusion"])
        self.assertEqual(evidence["trade_flow"]["visible_buy_count_sampled"], 42)
        self.assertEqual(evidence["trade_flow"]["visible_sell_count_sampled"], 23)
        self.assertFalse(evidence["trade_flow"]["complete_trade_tape_verified"])
        self.assertFalse(evidence["trade_flow"]["verified_aggressor_buy_dominance"])
        self.assertFalse(evidence["execution_allowed"])
        self.assertFalse(row["trade_authorized"])

    def test_high_rsi_continuation_is_still_visible_but_cautioned(self):
        row = self.candidate(rsi14=Decimal("84")).summary()
        self.assertEqual(row["scalp_setup_stage"]["stage"],
                         "EXHAUSTED_PUMP_CAUTION")
        self.assertEqual(row["scalping_evidence"]["watch_classification"],
                         "MOMENTUM_CONTINUATION_CAUTION_WATCH")
        self.assertTrue(row["scalping_evidence"]["anomaly_review_required"])
        self.assertFalse(row["scalping_evidence"]["execution_allowed"])

    def test_early_setup_remains_separate_from_continuation(self):
        row = self.candidate(
            market_structure_diagnostic=None,
            early_momentum={"status": "EARLY_WATCH_UNCALIBRATED"},
        ).summary()
        self.assertEqual(row["scalping_evidence"]["watch_classification"],
                         "EARLY_MOMENTUM_WATCH")

    def test_unknown_inputs_never_become_verified_flow_news_or_profit(self):
        row = self.candidate(
            visible_buy_idr=Decimal("0"), visible_sell_idr=Decimal("0"),
            visible_buy_trade_count_sampled=0,
            visible_sell_trade_count_sampled=0,
            volume_ratio=None, spread_bps=None, book_imbalance=None,
        ).summary()["scalping_evidence"]
        self.assertEqual(row["relative_volume_coverage"], "UNKNOWN")
        self.assertFalse(row["news_source_and_published_at_verified"])
        self.assertFalse(row["profit_after_fees_and_slippage_proven"])
        self.assertFalse(row["trade_flow"]["complete_trade_tape_verified"])
        self.assertFalse(row["execution_allowed"])

    def test_reject_non_mapping_or_nonfinite_values(self):
        with self.assertRaises(ValueError):
            project_scalping_evidence_nonlive(None)
        row = project_scalping_evidence_nonlive({
            "scalp_setup_stage": {"stage": "BREAKOUT_CONTINUATION_WATCH"},
            "visible_buy_idr": "NaN", "visible_sell_idr": "Infinity",
            "volume_ratio": "-2", "spread_bps": "NaN",
        })
        self.assertIsNone(row["trade_flow"]["sample_buy_minus_sell_value_idr"])
        self.assertIsNone(row["relative_closed_candle_volume"])
        self.assertIsNone(row["order_book"]["spread_bps"])
        self.assertFalse(row["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
