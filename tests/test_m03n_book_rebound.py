"""M03-N book-wall and panic-rebound diagnostics on the SAME G1 deep path.

Public resting orders are cancelable. NO forecast, no attested full tape,
no provider calls, no G2/FInal order authority from any of these flags.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D
import unittest

from indodax_ai_bot.contracts import MarketSnapshot
from indodax_ai_bot.deep_analysis import (
    analyze_observation, assess_orderbook_rebound,
)
from indodax_ai_bot.public_market import IndodaxPublicMarket, PublicDataError
from test_m02_public_market import OfficialPublicFixtures
from test_m03b_deep import observation


def with_wall(obs=None, *, bid_total="1000000", ask_total="200000"):
    obs = obs if obs is not None else observation()
    bid, ask = obs.snapshot.best_bid_idr, obs.snapshot.best_ask_idr
    return replace(
        obs,
        bid_book_levels=((bid,D(bid_total)/bid),),
        ask_book_levels=((ask,D(ask_total)/ask),),
    )


def panic(obs=None, *, bouncing=True):
    obs=with_wall(obs)
    history=list(obs.candles_1m)
    history[-3]=replace(
        history[-3],open=D("97"),high=D("98"),low=D("89"),
        close=D("90"),volume=D("20"),
    )
    history[-2]=replace(
        history[-2],open=D("90"),high=D("91"),low=D("87"),
        close=D("89"),volume=D("20"),
    )
    history[-1]=replace(
        history[-1],open=D("89"),high=D("91"),low=D("88"),
        close=D("90.5") if bouncing else D("88.5"),
        volume=D("40"),
    )
    # Keep current quote and depth near the actual rebound close.
    receipt=obs.snapshot.source
    bid,ask=D("90.49"),D("90.50")
    return replace(
        obs, candles_1m=tuple(history),candle_1m=history[-1],
        snapshot=MarketSnapshot(receipt,bid,ask),
        bid_book_levels=((bid,D("1000000")/bid),),
        ask_book_levels=((ask,D("200000")/ask),),
    )


class M03NBookWallTests(unittest.TestCase):
    def test_100m_bid_against_20m_ask_reads_five_to_one_but_wide_spread(self):
        obs=observation(wide=False)
        quote=MarketSnapshot(obs.snapshot.source,D("10000"),D("10500"))
        x=replace(
            obs,snapshot=quote,
            bid_book_levels=((D("10000"),D("10000")),),
            ask_book_levels=((D("10500"),D("20000000")/D("10500")),),
        )
        result=assess_orderbook_rebound(x)
        self.assertAlmostEqual(
            float(result["near_bid_ask_notional_ratio"]),5.0,places=9
        )
        self.assertEqual(result["status"],"WIDE_SPREAD_WALL_UNACTIONABLE")
        self.assertEqual(result["largest_bid_wall_notional_idr"],"100000000")
        self.assertFalse(result["resting_orders_are_executed_buys"])
        self.assertFalse(result["wall_persistence_proven"])
        report=analyze_observation(x).summary()
        self.assertEqual(report["rejection_reason"],"SCALPING_SPREAD_TOO_WIDE")
        self.assertEqual(
            report["book_wall_diagnostic"]["status"],
            "WIDE_SPREAD_WALL_UNACTIONABLE",
        )
        self.assertFalse(report["trade_authorized"])

    def test_wide_selloff_plus_close_rebound_and_wall_is_only_unconfirmed_watch(self):
        obs=panic()
        result=assess_orderbook_rebound(obs)
        self.assertEqual(result["status"],"PANIC_REBOUND_WATCH_UNCONFIRMED")
        self.assertLessEqual(D(result["observed_22m_peak_to_low_pct"]),D("-7"))
        self.assertGreaterEqual(D(result["recovery_from_visible_low_pct"]),D("0.3"))
        self.assertEqual(
            result["continuation_hypothesis"],
            "NEEDS_NEXT_CLOSED_CANDLE_AND_FLOW_CONFIRMATION",
        )
        self.assertEqual(result["fade_hypothesis"],
                         "RETEST_OR_SECOND_LEG_DOWN_REMAINS_POSSIBLE")
        self.assertFalse(result["trade_signal"])
        out=analyze_observation(obs).summary()
        self.assertEqual(out["book_wall_diagnostic"]["status"],
                         "PANIC_REBOUND_WATCH_UNCONFIRMED")
        self.assertEqual(out["state"],"PUBLIC_EVIDENCE_UNATTESTED")
        self.assertFalse(out["g1_verified"])

    def test_second_leg_down_reported_even_with_very_large_bid_wall(self):
        diag=assess_orderbook_rebound(panic(bouncing=False))
        self.assertEqual(diag["status"],"SECOND_LEG_DOWN_RISK")
        self.assertEqual(diag["continuation_hypothesis"],"NOT_CONFIRMED")
        self.assertFalse(diag["execution_allowed"])

    def test_large_bid_wall_without_panic_is_not_rebound_signal(self):
        res=assess_orderbook_rebound(with_wall())
        self.assertEqual(res["status"],"BID_WALL_WATCH_UNCONFIRMED")
        self.assertFalse(res["spoofing_excluded"])
        self.assertFalse(res["trade_signal"])

    def test_total_near_wall_excludes_huge_far_away_bid_order(self):
        x=with_wall(bid_total="1000",ask_total="2000")
        bid=x.bid_book_levels[0][0]
        x=replace(x,bid_book_levels=(
            x.bid_book_levels[0],
            (bid*D("0.90"),D("100000000")),
        ))
        res=assess_orderbook_rebound(x)
        self.assertEqual(res["near_bid_ask_notional_ratio"],"0.5")
        self.assertEqual(res["bid_near_total_idr"],"1000")
        self.assertEqual(res["status"],"NO_STRONG_NEAR_BID_WALL")

    def test_incomplete_and_wrong_order_book_evidence_is_never_approved(self):
        obs=observation()
        empty=assess_orderbook_rebound(obs)
        self.assertEqual(empty["status"],"BOOK_LEVELS_NOT_OBSERVED")
        self.assertFalse(empty["g1_verified"])
        x=with_wall()
        x=replace(x,bid_book_levels=x.bid_book_levels+x.bid_book_levels)
        with self.assertRaisesRegex(PublicDataError,"UNORDERED_BOOK_WALL_LEVELS"):
            assess_orderbook_rebound(x)
        self.assertEqual(analyze_observation(x).state,"REJECT_INCOMPLETE_EVIDENCE")

    def test_async_depth_quote_price_mismatch_does_not_claim_support(self):
        x=with_wall()
        x=replace(
            x,
            bid_book_levels=((D("120"),D("10000")),),
            ask_book_levels=((D("121"),D("1000")),),
        )
        res=assess_orderbook_rebound(x)
        self.assertEqual(res["status"],"BOOK_TICKER_MISMATCH_NO_WATCH")
        self.assertFalse(res["trade_signal"])

    def test_rest_api_parses_ten_depth_levels_without_extra_endpoint(self):
        class WithLadder(OfficialPublicFixtures):
            def fetch(self,path):
                if path=="/api/depth/btcidr":
                    return {
                        "buy":[[str(100-i),"2"] for i in range(12)],
                        "sell":[[str(101+i),"1"] for i in range(12)],
                    }
                return super().fetch(path)
        fixture=WithLadder()
        obs=IndodaxPublicMarket(
            fetch=fixture.fetch,clock=lambda:fixture.now,
        ).observe("btc_idr")
        self.assertEqual(len(obs.bid_book_levels),10)
        self.assertEqual(len(obs.ask_book_levels),10)
        self.assertEqual(obs.bid_book_levels[0],(D("100"),D("2")))
        self.assertEqual(obs.ask_book_levels[0],(D("101"),D("1")))
        self.assertEqual(len([
            p for p in fixture.paths if "/tradingview/history_v2?" in p
        ]),2)  # reuses existing closed-candle GETs, no new call
        self.assertFalse(obs.ready)
        self.assertFalse(obs.trade_history_complete)

    def test_depth_endpoint_unsorted_levels_refused_before_book_analysis(self):
        class BadDepth(OfficialPublicFixtures):
            def fetch(self,path):
                if path=="/api/depth/btcidr":
                    return {"buy":[["99","2"],["100","1"]],
                            "sell":[["101","1"],["102","2"]]}
                return super().fetch(path)
        f=BadDepth()
        with self.assertRaisesRegex(PublicDataError,"UNSORTED_OR_DUPLICATE_BOOK_LEVEL"):
            IndodaxPublicMarket(fetch=f.fetch,clock=lambda:f.now).observe("btc_idr")


if __name__=="__main__":
    unittest.main()
