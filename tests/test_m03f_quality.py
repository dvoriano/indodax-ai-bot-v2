"""M03-F: observational quality review; no G2 candidate issuer, no trading."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch
import unittest

from indodax_ai_bot.deep_analysis import DeepCandidate, _quality_review, deep_scan
from test_m03b_deep import screening
from test_m03d_same_window import MarketFixture


def diagnostic(*, pair="btc_idr", score=30, rsi="48.67", volume="0.47",
               imbalance="-0.989", buy="100", sell="0"):
    return DeepCandidate(
        pair=pair, score=score, state="PUBLIC_EVIDENCE_UNATTESTED",
        rejection_reason=None,
        closed_1m_epoch=1800000120,
        rsi14=Decimal(rsi),
        volume_ratio=Decimal(volume),
        book_imbalance=Decimal(imbalance),
        visible_buy_idr=Decimal(buy),
        visible_sell_idr=Decimal(sell),
    )


class M03FPublicQualityReviewTests(unittest.TestCase):
    def test_real_btc_like_downtrend_weak_volume_ask_heavy_not_promoted(self):
        r = _quality_review(diagnostic(), "BTC_DOWNTREND_DIAGNOSTIC")
        self.assertEqual(r["pair"], "btc_idr")
        self.assertFalse(r["clear_of_provisional_cautions"])
        self.assertIn("BTC_DOWNTREND_CAUTION", r["review_flags"])
        self.assertIn("BELOW_BASELINE_OR_MISSING_VOLUME", r["review_flags"])
        self.assertIn("ASK_DOMINANT_OR_MISSING_BOOK", r["review_flags"])
        self.assertFalse(r["risk_ai_evaluated"])
        self.assertFalse(r["g2_candidate_authorized"])
        self.assertFalse(r["trade_authorized"])

    def test_eth_like_oversold_book_weak(self):
        r = _quality_review(diagnostic(
            pair="eth_idr", rsi="20.73", volume="1.08", imbalance="-0.586"
        ), "BTC_DOWNTREND_DIAGNOSTIC")
        self.assertIn("OVERSOLD_RSI_REVIEW", r["review_flags"])
        self.assertIn("ASK_DOMINANT_OR_MISSING_BOOK", r["review_flags"])
        self.assertFalse(r["clear_of_provisional_cautions"])

    def test_visible_sample_selling_gets_label_not_claim_of_full_history(self):
        r = _quality_review(diagnostic(
            pair="inj_idr", score=78, rsi="60", volume="2",
            imbalance="0.2", buy="100", sell="200"
        ), "BTC_UPTREND_DIAGNOSTIC")
        self.assertEqual(r["review_flags"], ["VISIBLE_SELL_SAMPLE_DOMINANT"])
        self.assertFalse(r["trade_authorized"])

    def test_quality_cleared_is_not_risk_authorization(self):
        r = _quality_review(diagnostic(
            pair="inj_idr", score=85, rsi="55", volume="2",
            imbalance="0.2", buy="200", sell="100"
        ), "BTC_UPTREND_DIAGNOSTIC")
        self.assertEqual(r["review_flags"], [])
        self.assertTrue(r["clear_of_provisional_cautions"])
        self.assertFalse(r["risk_ai_evaluated"])
        self.assertFalse(r["g2_candidate_authorized"])
        self.assertFalse(r["trade_authorized"])

    def test_aligned_public_two_pair_watchlist_is_only_preview(self):
        with patch("indodax_ai_bot.deep_analysis._rsi14", return_value=Decimal("60")):
            report = deep_scan(MarketFixture(), screening(), require_btc=True).summary()
        gate = report["same_window"]
        self.assertEqual(gate["status"], "ALIGNED_PUBLIC_UNATTESTED")
        self.assertEqual(gate["provisional_watchlist_preview"], ["btc_idr", "eth_idr"])
        self.assertEqual(len(gate["provisional_quality_reviews"]), 2)
        self.assertFalse(gate["watchlist_is_trade_signal"])
        self.assertFalse(gate["provisional_thresholds_backtested"])
        self.assertFalse(gate["g2_candidate_authorized"])
        self.assertFalse(gate["live_trading_authorized"])

    def test_failed_same_window_clears_preview_explicitly(self):
        gate = deep_scan(
            MarketFixture(misalign_eth=True), screening(), require_btc=True
        ).summary()["same_window"]
        self.assertEqual(gate["status"], "REJECT_CANDLE_OR_SNAPSHOT_MISALIGNMENT")
        self.assertEqual(gate["ranked_same_window_diagnostics"], [])
        self.assertEqual(gate["provisional_quality_reviews"], [])
        self.assertEqual(gate["provisional_watchlist_preview"], [])
        self.assertFalse(gate["g2_candidate_authorized"])

    def test_public_bearish_context_may_rank_diagnostics_but_preview_empty(self):
        with patch("indodax_ai_bot.deep_analysis._quality_review") as review:
            # Patch labels only, never model-generated or trusted provenance.
            def bearish_flags(candidate, btc_regime):
                return {
                    "pair": candidate.pair, "score": candidate.score,
                    "review_flags": ["BTC_DOWNTREND_CAUTION"],
                    "clear_of_provisional_cautions": False,
                    "risk_ai_evaluated": False, "g2_candidate_authorized": False,
                    "trade_authorized": False,
                }
            review.side_effect = bearish_flags
            gate = deep_scan(MarketFixture(), screening(), require_btc=True).summary()["same_window"]
        self.assertEqual(gate["status"], "ALIGNED_PUBLIC_UNATTESTED")
        self.assertEqual(len(gate["ranked_same_window_diagnostics"]), 2)
        self.assertEqual(gate["provisional_watchlist_preview"], [])
        self.assertFalse(gate["watchlist_is_trade_signal"])


if __name__ == "__main__":
    unittest.main()
