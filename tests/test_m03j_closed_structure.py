"""M03-J scoped research: closed-candle structure and bounded gross-price replay.

No real orders, fees, provider requests, synthetic G1 issuer, or trade approval.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D
import unittest

from indodax_ai_bot.deep_analysis import (
    analyze_observation, bulk_public_deep_scan,
    classify_closed_market_structure, replay_closed_market_structure,
)
from indodax_ai_bot.public_market import Candle, PublicDataError
from test_m03b_deep import observation
from test_m03g_bulk import FakeBulkMarket, fake_screen


def flats(count=30, end=1800000120):
    return tuple(Candle(
        start=end-(count-i)*60, interval=60,
        open=D("100"), high=D("101"), low=D("99"),
        close=D("100"), volume=D("10"),
    ) for i in range(count))


class M03JClosedStructureTests(unittest.TestCase):
    def test_surge_breakout_requires_closed_high_volume_body(self):
        xs=flats()
        surge=replace(xs[-1], open=D("100"), high=D("105"),
                      low=D("99"), close=D("104"), volume=D("40"))
        out=classify_closed_market_structure(xs[:-1]+(surge,))
        self.assertEqual(out["label"], "SURGE_BREAKOUT_OBSERVED")
        self.assertEqual(out["closed_1m_epoch"], 1800000120)
        self.assertEqual(out["reference_prior_10m_high"], "101")
        self.assertFalse(out["trade_signal"])
        self.assertFalse(out["g1_verified"])
        weak=replace(surge,volume=D("10"))
        self.assertEqual(
            classify_closed_market_structure(xs[:-1]+(weak,))["label"],
            "NO_CLOSED_CANDLE_SETUP",
        )

    def test_false_breakout_precedes_buy_looking_surge(self):
        xs=flats()
        trap=replace(xs[-1], open=D("100"), high=D("105"),
                     low=D("99"), close=D("100.5"), volume=D("40"))
        res=classify_closed_market_structure(xs[:-1]+(trap,))
        self.assertEqual(res["label"], "FALSE_BREAKOUT_RISK")
        self.assertFalse(res["trade_signal"])

    def test_pullback_rebound_and_reversal_are_distinct_candle_diagnostics(self):
        xs=list(flats())
        for i in range(29):
            v=D("100") + D(i)/D(10)
            xs[i]=replace(xs[i],open=v,high=v+D("0.9"),
                          low=v-D("0.9"),close=v)
        xs[-1]=replace(xs[-1],open=D("102.0"),high=D("103.0"),
                       low=D("101.0"),close=D("102.8"))
        self.assertEqual(
            classify_closed_market_structure(tuple(xs))["label"],
            "PULLBACK_REBOUND_OBSERVED",
        )
        xs=list(flats())
        for idx, close, op in [(-4,"102","102"),(-3,"101","102"),(-2,"100","101")]:
            xs[idx]=replace(xs[idx], open=D(op), high=D("103"),
                             low=D("99"), close=D(close))
        xs[-1]=replace(xs[-1],open=D("99.5"),high=D("103"),
                       low=D("99"),close=D("102"))
        self.assertEqual(
            classify_closed_market_structure(tuple(xs))["label"],
            "REVERSAL_ATTEMPT_OBSERVED",
        )

    def test_discontinuous_or_invalid_history_rejected_before_classification(self):
        xs=flats()
        gap=list(xs)
        gap[-7]=replace(gap[-7],start=gap[-7].start+30)
        with self.assertRaisesRegex(PublicDataError,"GAPPED_60S_HISTORY"):
            classify_closed_market_structure(tuple(gap))
        with self.assertRaisesRegex(PublicDataError,"GAPPED_STRUCTURE_REPLAY_HISTORY"):
            replay_closed_market_structure(tuple(gap))
        with self.assertRaisesRegex(PublicDataError,"INVALID_STRUCTURE_CANDLE"):
            classify_closed_market_structure(xs[:-1]+(
                replace(xs[-1],high=D("99")),))

    def test_replay_has_no_lookahead_on_signal_and_no_profit_claim(self):
        xs=list(flats(29))
        impulse=replace(xs[22],open=D("100"),high=D("105"),
                        low=D("99"),close=D("104"),volume=D("40"))
        xs[22]=impulse
        out=replay_closed_market_structure(tuple(xs))
        self.assertEqual(out["status"],"CANDLE_ONLY_REPLAY_UNCALIBRATED")
        target=impulse.start+60
        matched=[x for x in out["events"] if x["closed_1m_epoch"]==target]
        self.assertEqual(len(matched),1)
        self.assertEqual(matched[0]["label"],"SURGE_BREAKOUT_OBSERVED")
        self.assertTrue(out["price_only_not_net_roi"])
        self.assertFalse(out["ablation_validated"])
        self.assertFalse(out["out_of_sample_validated"])
        modified=list(xs)
        modified[25]=replace(modified[25],close=D("101"))
        changed=replay_closed_market_structure(tuple(modified))
        again=[x for x in changed["events"] if x["closed_1m_epoch"]==target]
        self.assertEqual(again[0]["label"],matched[0]["label"])
        self.assertNotEqual(
            again[0]["forward_3m_close_to_close_gross_bps"],
            matched[0]["forward_3m_close_to_close_gross_bps"],
        )

    def test_mainpath_preserves_original_score_veto_and_marks_research_only(self):
        obs=observation()
        result=analyze_observation(obs)
        self.assertEqual(result.state,"PUBLIC_EVIDENCE_UNATTESTED")
        self.assertIsNotNone(result.score)
        summary=result.summary()
        self.assertIsInstance(summary["market_structure_diagnostic"],dict)
        self.assertEqual(
            summary["market_structure_diagnostic"]["closed_1m_epoch"],
            obs.snapshot.source.closed_1m_epoch,
        )
        self.assertEqual(summary["structure_replay"]["status"],
                         "CANDLE_ONLY_REPLAY_UNCALIBRATED")
        self.assertFalse(summary["market_structure_diagnostic"]["trade_signal"])
        self.assertFalse(summary["trade_authorized"])
        self.assertFalse(summary["g1_verified"])

    def test_existing_20_pair_bulk_mainpath_exposes_new_diagnostic_but_no_ai(self):
        out=bulk_public_deep_scan(FakeBulkMarket(),fake_screen(),limit=20)
        self.assertEqual(out["attempted_count"],20)
        self.assertEqual(out["estimated_requests_including_discovery"],83)
        row=out["ranked_diagnostics_top10"][0]
        self.assertIn("market_structure_diagnostic",row)
        self.assertIn("structure_replay",row)
        self.assertFalse(row["market_structure_diagnostic"]["trade_signal"])
        self.assertFalse(out["g1_verified"])
        self.assertFalse(out["g2_candidate_authorized"])
        self.assertFalse(out["execution_allowed"])
        self.assertEqual(out["eligible_cohort"]["status"],
                         "ALIGNED_PUBLIC_UNATTESTED")


if __name__=="__main__":
    unittest.main()
