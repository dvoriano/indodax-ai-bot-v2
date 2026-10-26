"""M03-E: extreme public momentum anomaly gate; no trade authorization.

Safety-only provisional thresholds; must be validated with replay/ablation.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import unittest
from unittest.mock import patch

from indodax_ai_bot.deep_analysis import (
    analyze_observation, deep_scan, EXTREME_RSI, EXTREME_VOLUME_RATIO,
)
from test_m03b_deep import observation, screening
from test_m03d_same_window import MarketFixture


def surge(pair="br_idr", *, buy="76325.769204", sell="1645919.9999506",
          volume="829"):
    old = observation(pair)
    candle = replace(old.candles_1m[-1], volume=Decimal(volume))
    minutes = old.candles_1m[:-1] + (candle,)
    return replace(
        old, candle_1m=candle, candles_1m=minutes,
        visible_closed_1m_buy_idr=Decimal(buy),
        visible_closed_1m_sell_idr=Decimal(sell),
    )


class M03EAnomalyTests(unittest.TestCase):
    def test_br_like_extreme_volume_overbought_and_selling_suppresses_score(self):
        result = analyze_observation(surge())
        self.assertEqual(result.state, "REJECT_MOMENTUM_ANOMALY")
        self.assertEqual(result.rejection_reason, "EXTREME_PUBLIC_MOMENTUM_REVIEW_REQUIRED")
        self.assertIsNone(result.score)
        self.assertEqual(result.rsi14, Decimal("100"))
        self.assertGreater(result.volume_ratio, Decimal("80"))
        self.assertEqual(result.anomaly_flags, (
            "EXTREME_VOLUME_WITH_OVERBOUGHT_RSI",
            "EXTREME_VOLUME_WITH_VISIBLE_SELL_DOMINANCE",
        ))
        report = result.summary()
        self.assertFalse(report["eligible_for_same_window_comparison"])
        self.assertFalse(report["trade_authorized"])
        self.assertFalse(report["g1_verified"])
        self.assertEqual(len(report["anomaly_flags"]), 2)

    def test_unusual_volume_alone_is_not_sufficient_to_label_combined_anomaly(self):
        with patch("indodax_ai_bot.deep_analysis._rsi14", return_value=Decimal("60")):
            result = analyze_observation(surge(buy="1000", sell="100"))
        self.assertEqual(result.state, "PUBLIC_EVIDENCE_UNATTESTED")
        self.assertIsInstance(result.score, int)
        self.assertEqual(result.anomaly_flags, ())
        self.assertTrue(result.summary()["eligible_for_same_window_comparison"])
        self.assertFalse(result.summary()["trade_authorized"])

    def test_rsi_volume_boundary_is_explicit_and_inclusive(self):
        self.assertEqual(EXTREME_RSI, Decimal("90"))
        self.assertEqual(EXTREME_VOLUME_RATIO, Decimal("10"))
        with patch("indodax_ai_bot.deep_analysis._rsi14", return_value=Decimal("90")):
            result = analyze_observation(surge(buy="1000", sell="100", volume="100"))
        self.assertEqual(result.anomaly_flags, ("EXTREME_VOLUME_WITH_OVERBOUGHT_RSI",))
        self.assertIsNone(result.score)

    def test_extreme_volume_visible_sell_dominance_independently_flags(self):
        with patch("indodax_ai_bot.deep_analysis._rsi14", return_value=Decimal("60")):
            result = analyze_observation(surge(buy="100", sell="300", volume="100"))
        self.assertEqual(result.anomaly_flags,
                         ("EXTREME_VOLUME_WITH_VISIBLE_SELL_DOMINANCE",))
        self.assertIsNone(result.score)

    def test_ordinary_momentum_fixture_still_diagnostic_and_no_trade(self):
        result = analyze_observation(observation())
        self.assertIsNotNone(result.score)
        self.assertEqual(result.anomaly_flags, ())
        self.assertFalse(result.summary()["trade_history_complete"])
        self.assertFalse(result.summary()["trade_authorized"])

    def test_same_window_comparison_rejects_suspicious_altcoin(self):
        class SuspiciousAlt(MarketFixture):
            def observe(self, pair):
                return surge(pair) if pair == "eth_idr" else super().observe(pair)
        out = deep_scan(SuspiciousAlt(), screening(), require_btc=True)
        self.assertEqual(out.analyzed[1].state, "REJECT_MOMENTUM_ANOMALY")
        window = out.summary()["same_window"]
        self.assertEqual(window["status"], "REJECT_CANDIDATE_ANOMALY_REVIEW_REQUIRED")
        self.assertEqual(window["ranked_same_window_diagnostics"], [])
        self.assertFalse(window["g2_candidate_authorized"])
        self.assertFalse(out.summary()["trade_authorized"])

    def test_btc_anomaly_blocks_all_candidate_rankings(self):
        class SuspiciousBTC(MarketFixture):
            def observe(self, pair):
                return surge(pair) if pair == "btc_idr" else super().observe(pair)
        out = deep_scan(SuspiciousBTC(), screening(), require_btc=True)
        window = out.summary()["same_window"]
        self.assertEqual(window["status"], "REJECT_BTC_ANOMALY_REVIEW_REQUIRED")
        self.assertEqual(window["ranked_same_window_diagnostics"], [])
        self.assertFalse(window["g2_candidate_authorized"])


if __name__ == "__main__":
    unittest.main()
