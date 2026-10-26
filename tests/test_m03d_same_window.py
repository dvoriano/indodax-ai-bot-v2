"""M03-D: source-aligned BTC context on the SAME existing public rolling path.

These tests prove only fail-closed public diagnostic comparison. Not a BUY gate.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import unittest

from indodax_ai_bot.contracts import MarketSnapshot
from indodax_ai_bot.deep_analysis import deep_scan
from indodax_ai_bot.public_market import PublicDataError
from test_m03b_deep import observation, screening


class MarketFixture:
    def __init__(self, *, missing_btc=False, bad_eth=False, misalign_eth=False):
        self.missing_btc = missing_btc
        self.bad_eth = bad_eth
        self.misalign_eth = misalign_eth
        self.calls = []

    def observe(self, pair):
        self.calls.append(pair)
        if pair == "btc_idr" and self.missing_btc:
            raise PublicDataError("BTC_ENDPOINT_UNAVAILABLE")
        obs = observation(pair, no_trade=(pair == "eth_idr" and self.bad_eth))
        if pair == "eth_idr" and self.misalign_eth:
            delta = 60
            shifted_candles = tuple(replace(c, start=c.start + delta)
                                    for c in obs.candles_1m)
            receipt = replace(
                obs.snapshot.source,
                closed_1m_epoch=obs.snapshot.source.closed_1m_epoch + delta,
                observed_epoch=obs.snapshot.source.observed_epoch + delta,
            )
            obs = replace(
                obs,
                snapshot=MarketSnapshot(receipt, obs.snapshot.best_bid_idr,
                                        obs.snapshot.best_ask_idr),
                candle_1m=shifted_candles[-1],
                candles_1m=shifted_candles,
            )
        return obs


class M03DSameWindowTests(unittest.TestCase):
    def test_existing_btc_observation_reused_for_same_minute(self):
        market = MarketFixture()
        out = deep_scan(market, screening(), require_btc=True)
        self.assertEqual(market.calls, ["btc_idr", "eth_idr"])
        proof = out.summary()["same_window"]
        self.assertEqual(proof["status"], "ALIGNED_PUBLIC_UNATTESTED")
        self.assertEqual(proof["closed_1m_epoch"], 1800000120)
        self.assertEqual(proof["btc_regime"], "BTC_UPTREND_DIAGNOSTIC")
        self.assertEqual(
            sorted([x["pair"] for x in proof["ranked_same_window_diagnostics"]]),
            ["btc_idr", "eth_idr"],
        )
        self.assertFalse(proof["source_trust_verified"])
        self.assertFalse(proof["trade_history_complete"])
        self.assertFalse(proof["g2_candidate_authorized"])
        self.assertFalse(proof["live_trading_authorized"])
        self.assertFalse(proof["cross_cycle_ranking_allowed"])

    def test_btc_context_fetched_once_if_not_in_rotated_pair_batch(self):
        market = MarketFixture()
        out = deep_scan(market, screening(), offset=1, limit=2, require_btc=True)
        self.assertEqual(market.calls, ["eth_idr", "sol_idr", "btc_idr"])
        self.assertEqual(out.summary()["same_window"]["status"], "ALIGNED_PUBLIC_UNATTESTED")
        self.assertEqual(len(out.summary()["same_window"]["ranked_same_window_diagnostics"]), 2)
        self.assertEqual(out.summary()["deep_examined_count"], 2)

    def test_missing_btc_context_does_not_create_candidates(self):
        market = MarketFixture(missing_btc=True)
        out = deep_scan(market, screening(), offset=1, require_btc=True)
        gate = out.summary()["same_window"]
        self.assertEqual(gate["status"], "REJECT_BTC_SOURCE_UNAVAILABLE")
        self.assertEqual(gate["ranked_same_window_diagnostics"], [])
        self.assertIsNone(gate["closed_1m_epoch"])
        self.assertFalse(gate["g2_candidate_authorized"])

    def test_incomplete_candidate_does_not_rank_other_candidate(self):
        market = MarketFixture(bad_eth=True)
        out = deep_scan(market, screening(), require_btc=True)
        gate = out.summary()["same_window"]
        self.assertEqual(gate["status"], "REJECT_CANDIDATE_EVIDENCE_INCOMPLETE")
        self.assertEqual(gate["ranked_same_window_diagnostics"], [])
        self.assertIsNone(out.analyzed[1].score)
        self.assertEqual(out.analyzed[1].rejection_reason, "NO_RECENT_TRADE_SAMPLE")

    def test_different_closed_minute_cannot_be_compared(self):
        market = MarketFixture(misalign_eth=True)
        out = deep_scan(market, screening(), require_btc=True)
        self.assertIsNotNone(out.analyzed[1].score)
        gate = out.summary()["same_window"]
        self.assertEqual(gate["status"], "REJECT_CANDLE_OR_SNAPSHOT_MISALIGNMENT")
        self.assertEqual(gate["ranked_same_window_diagnostics"], [])
        self.assertFalse(gate["g2_candidate_authorized"])

    def test_screening_older_than_public_observations_blocks_rank(self):
        old = replace(screening(), observed_epoch=1800000060)
        market = MarketFixture()
        out = deep_scan(market, old, require_btc=True)
        gate = out.summary()["same_window"]
        self.assertEqual(gate["status"], "REJECT_CANDLE_OR_SNAPSHOT_MISALIGNMENT")
        self.assertEqual(gate["ranked_same_window_diagnostics"], [])

    def test_public_legacy_deep_diagnostics_remain_without_btc_context(self):
        market = MarketFixture()
        out = deep_scan(market, screening(), require_btc=False)
        self.assertEqual(market.calls, ["btc_idr", "eth_idr"])
        self.assertNotIn("same_window", out.summary())
        self.assertFalse(out.summary()["trade_authorized"])


if __name__ == "__main__":
    unittest.main()
