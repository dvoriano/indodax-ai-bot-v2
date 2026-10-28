"""M03-N causal wall persistence and prospective surge casebook mainpath tests.

Only observed prior snapshots may inform a current diagnostic. Same-minute
replays, weak walls, missing future labels and illiquid pumps cannot authorize
trading. Uses the original SQLite journal, scanner and orchestrator modules.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.contracts import MarketSnapshot
from indodax_ai_bot.deep_analysis import analyze_observation
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.scanner import (
    DailyPairScanner,rank_scanner_with_early_momentum,surge_pattern_signature,
)
from test_m03a_scanner import FakeIndodaxAll
from test_m03b_deep import observation,screening
from test_m03n_book_rebound import with_wall


def advanced(obs, candidate, *, step=60, bid_total=None, ask_total=None,
             quote_shift=D("0")):
    source=replace(
        obs.snapshot.source,
        observed_epoch=obs.snapshot.source.observed_epoch+step,
        closed_1m_epoch=obs.snapshot.source.closed_1m_epoch+step,
    )
    bid=obs.snapshot.best_bid_idr+quote_shift
    ask=obs.snapshot.best_ask_idr+quote_shift
    changed=replace(
        obs,
        snapshot=MarketSnapshot(source,bid,ask),
        bid_book_levels=((bid,(bid_total or D("1000000"))/bid),),
        ask_book_levels=((ask,(ask_total or D("200000"))/ask),),
    )
    buy_total=bid_total or D("1000000")
    sell_total=ask_total or D("200000")
    diagnostic=dict(candidate.book_wall_diagnostic or {})
    diagnostic.update({
        "status":("BID_WALL_WATCH_UNCONFIRMED" if buy_total/sell_total>=D("3")
                  else "NO_STRONG_NEAR_BID_WALL"),
        "largest_bid_wall_price_idr":str(bid),
        "largest_bid_wall_notional_idr":str(buy_total),
        "bid_near_total_idr":str(buy_total),
        "ask_near_total_idr":str(sell_total),
    })
    current=replace(
        candidate,
        closed_1m_epoch=source.closed_1m_epoch,
        book_wall_diagnostic=diagnostic,
    )
    return changed,current


class M03NWallSurgeMainpathTest(unittest.TestCase):
    def setUp(self):
        self.td=TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.j=DecisionJournal(Path(self.td.name)/"journal.sqlite3")

    def test_wall_two_distinct_minutes_dominant_support_observed_not_proven(self):
        first=with_wall()
        candidate=analyze_observation(first)
        result=self.j.record_and_compare_public_wall(candidate,first)
        self.assertEqual(result["status"],"BID_WALL_PERSISTENCE_UNPROVEN")
        second,c2=advanced(first,candidate)
        report=self.j.record_and_compare_public_wall(c2,second)
        self.assertEqual(report["status"],"BID_WALL_RETAINED_OBSERVED")
        self.assertEqual(report["observations_compared"],2)
        self.assertTrue(report["bid_wall_retained_observed"])
        self.assertFalse(report["bid_wall_persistence_proven"])
        self.assertFalse(report["cancel_vs_fill_distinguishable"])
        self.assertFalse(report["execution_allowed"])

    def test_same_minute_rescan_is_not_a_new_wall_observation(self):
        first=with_wall()
        candidate=analyze_observation(first)
        self.j.record_and_compare_public_wall(candidate,first)
        second,c2=advanced(first,candidate)
        self.j.record_and_compare_public_wall(c2,second)
        repeat=replace(
            second,
            snapshot=MarketSnapshot(
                replace(second.snapshot.source,
                        observed_epoch=second.snapshot.source.observed_epoch+7),
                second.snapshot.best_bid_idr,second.snapshot.best_ask_idr,
            ),
        )
        replay=self.j.record_and_compare_public_wall(c2,repeat)
        self.assertEqual(replay["status"],"WALL_SAME_MINUTE_REPLAY")
        with self.j._connect() as con:
            count=con.execute(
                "SELECT COUNT(*) FROM public_deep_wall_samples WHERE pair='btc_idr'"
            ).fetchone()[0]
        self.assertEqual(count,2)

    def test_weak_bid_cannot_be_called_persistent_buy_support(self):
        first=with_wall(bid_total="1000",ask_total="50000")
        candidate=analyze_observation(first)
        self.assertEqual(candidate.book_wall_diagnostic["status"],
                         "NO_STRONG_NEAR_BID_WALL")
        self.j.record_and_compare_public_wall(candidate,first)
        second,c2=advanced(first,candidate,
                           bid_total=D("1000"),ask_total=D("50000"))
        result=self.j.record_and_compare_public_wall(c2,second)
        self.assertEqual(result["status"],
                         "NO_DOMINANT_BID_WALL_IN_EITHER_SNAPSHOT")
        self.assertFalse(result["bid_wall_retained_observed"])

    def test_wall_withdrawn_or_shifted_not_called_persistent(self):
        first=with_wall()
        c=analyze_observation(first)
        self.j.record_and_compare_public_wall(c,first)
        second,c2=advanced(first,c,quote_shift=D("1.00"))
        diag=self.j.record_and_compare_public_wall(c2,second)
        self.assertEqual(diag["status"],
                         "BID_WALL_DISAPPEARED_OR_MOVED_UNKNOWN")
        self.assertGreater(D(diag["wall_price_shift_bps"]),D("50"))
        self.assertFalse(diag["cancel_vs_fill_distinguishable"])

    def test_three_minute_gap_no_fabricated_continuity(self):
        first=with_wall()
        c=analyze_observation(first)
        self.j.record_and_compare_public_wall(c,first)
        later,c2=advanced(first,c,step=360)
        result=self.j.record_and_compare_public_wall(c2,later)
        self.assertEqual(result["status"],"WALL_OBSERVATION_GAP_OR_REPLAY")

    def test_actual_original_deep_orchestrator_receives_wall_field(self):
        class Market:
            def observe(self,pair):
                return with_wall(observation(pair))
        app=DailyScalpingOrchestrator(Settings.from_env({}),self.j)
        with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner:
            scanner.return_value.scan.return_value=screening()
            result=app.run_deep_cycle("wall-integrated",market=Market())
        self.assertEqual(result.final_decision.value,"REJECT")
        self.assertFalse(result.execution_allowed)
        self.assertEqual(result.deep["deep_examined_count"],2)
        for item in result.deep["ranked_diagnostics"]:
            self.assertEqual(item["wall_temporal_evidence"]["status"],
                             "BID_WALL_PERSISTENCE_UNPROVEN")
            self.assertFalse(item["trade_authorized"])

    def test_surge_pattern_signature_never_reads_future_price(self):
        data={
            "return_5m_pct":"2.5",
            "acceleration_current_5m_vs_prior_5m_pct_points":"1.0",
        }
        code=surge_pattern_signature(data,D("35"))
        self.assertEqual(code,"RISING_2_5__ACCELERATING__TIGHT_SPREAD")
        self.assertIsNone(surge_pattern_signature(
            {"return_5m_pct":None},D("35")))
        self.assertIsNone(surge_pattern_signature(
            {"return_5m_pct":"NaN",
             "acceleration_current_5m_vs_prior_5m_pct_points":"1"},D("35")))

    def test_surge_control_cohort_and_missing_future_are_not_winners(self):
        fake=FakeIndodaxAll()
        start=1800000000
        fake.now=start+10
        for ticker in fake.tickers.values():
            ticker["server_time"]=fake.now-1
        screen=DailyPairScanner(fake.client()).scan()
        coins=[x.pair for x in screen.universe[:5]]
        with self.j._connect() as con:
            for p in coins:
                con.execute(
                    """INSERT INTO public_ticker_minute_samples
                       (pair,minute_epoch,observed_epoch,ticker_epoch,
                        last_idr,volume_24h_idr,spread_bps)
                       VALUES (?,?,?,?,?,?,?)""",
                    (p,start,start+10,start+9,"100","10000000","50"),
                )
        features={
            "consecutive_minute_samples":11,
            "return_1m_pct":"0.4","return_5m_pct":"2.5",
            "acceleration_current_5m_vs_prior_5m_pct_points":"1",
        }
        reports={
            coins[0]:dict(features,status="EARLY_WATCH_UNCALIBRATED"),
            **{p:dict(features,status="OBSERVED_NO_ACCELERATION")
               for p in coins[1:]},
        }
        self.j.record_public_surge_experience(screen,reports)
        current=self.j.public_surge_casebook_summary()
        self.assertEqual(current["cohorts"]["EARLY_WATCH"]["cases"],1)
        self.assertEqual(current["cohorts"]["CONTROL"]["cases"],3)
        self.assertTrue(current["control_cohort_available"])
        future=start+86400
        # Only FIRST early winner and ONE losing control have a real
        # follow-up quote. The other controls remain UNKNOWN, not losses.
        with self.j._connect() as con:
            for p,value in ((coins[0],"135"),(coins[1],"91")):
                con.execute(
                    """INSERT INTO public_ticker_minute_samples
                       (pair,minute_epoch,observed_epoch,ticker_epoch,
                        last_idr,volume_24h_idr,spread_bps)
                       VALUES (?,?,?,?,?,?,?)""",
                    (p,future,future+10,future+9,value,"10000000","50"),
                )
        self.j.record_public_surge_experience(
            replace(screen,observed_epoch=future+10),{},
        )
        result=self.j.public_surge_casebook_summary()
        self.assertEqual(result["cohorts"]["EARLY_WATCH"]["up_20pct_24h"],1)
        self.assertEqual(result["cohorts"]["CONTROL"]["matured_24h"],1)
        self.assertEqual(result["cohorts"]["CONTROL"]["cases"],3)
        self.assertEqual(result["cohorts"]["CONTROL"]["up_20pct_24h"],0)
        self.assertFalse(result["trainable_model_authorized"])
        self.assertFalse(result["trade_authorized"])

    def test_surge_case_created_before_outcome_and_matures_only_after_future_snapshot(self):
        fake=FakeIndodaxAll()
        start=1800000000 # 5m boundary
        fake.now=start+10
        for ticker in fake.tickers.values():
            ticker["server_time"]=fake.now-1
        scanner=DailyPairScanner(fake.client()).scan()
        pair=scanner.universe[0].pair
        with self.j._connect() as con:
            con.execute(
                """INSERT INTO public_ticker_minute_samples
                   (pair,minute_epoch,observed_epoch,ticker_epoch,
                    last_idr,volume_24h_idr,spread_bps)
                   VALUES (?,?,?,?,?,?,?)""",
                (pair,start,start+10,start+9,"100","10000000","50"),
            )
        report={
            pair:{
                "status":"EARLY_WATCH_UNCALIBRATED",
                "consecutive_minute_samples":11,
                "return_1m_pct":"0.4","return_5m_pct":"2.5",
                "return_15m_pct":None,"return_60m_pct":None,
                "acceleration_current_5m_vs_prior_5m_pct_points":"1",
            }
        }
        self.j.record_public_surge_experience(scanner,report)
        early=self.j.public_surge_casebook_summary()
        self.assertEqual(early["cohorts"]["EARLY_WATCH"]["cases"],1)
        self.assertEqual(early["cohorts"]["EARLY_WATCH"]["matured_24h"],0)
        self.assertEqual(early["pattern_library"],{
            "RISING_2_5__ACCELERATING__WIDE_SPREAD":{
                "cases":1,"matured_60m":0,"matured_24h":0,
                "up_20pct_24h":0,"up_50pct_24h":0,
                "up_100pct_24h":0,"down_10pct_24h":0,
                "up_2pct_60m":0,"up_8pct_60m":0,
                "up_11pct_60m":0,"down_2pct_60m":0,
                "mean_return_60m_pct":None,
                "scalp_60m_descriptive_status":"NO_MATURED_60M_OUTCOMES",
                "distinct_pairs":1,"distinct_days":1,
                "matured_distinct_pairs":0,"matured_distinct_days":0,
                "status":"INSUFFICIENT_MATURED_COMPARABLE_CASES",
                "out_of_sample_validated":False,
                "net_fee_adjusted_edge_proven":False,
            }
        })
        future60=start+3600
        future24=start+86400
        with self.j._connect() as con:
            for epoch,value in ((future60,"104"),(future24,"125")):
                con.execute(
                    """INSERT INTO public_ticker_minute_samples
                       (pair,minute_epoch,observed_epoch,ticker_epoch,
                        last_idr,volume_24h_idr,spread_bps)
                       VALUES (?,?,?,?,?,?,?)""",
                    (pair,epoch,epoch+10,epoch+9,value,"10000000","50"),
                )
        later=replace(scanner,observed_epoch=future60+10)
        self.j.record_public_surge_experience(later,{})
        after60=self.j.public_surge_casebook_summary()
        self.assertEqual(after60["cohorts"]["EARLY_WATCH"]["matured_60m"],1)
        self.assertEqual(after60["cohorts"]["EARLY_WATCH"]["matured_24h"],0)
        self.assertEqual(after60["cohorts"]["EARLY_WATCH"]["up_2pct_60m"],1)
        self.assertEqual(after60["cohorts"]["EARLY_WATCH"]["up_8pct_60m"],0)
        self.assertEqual(after60["cohorts"]["EARLY_WATCH"]["mean_return_60m_pct"],"4.0000")
        early60=rank_scanner_with_early_momentum(scanner,report,casebook=after60)
        early60_pattern=next(x for x in early60.universe if x.pair==pair).surge_pattern
        self.assertEqual(early60_pattern["historical_matured_60m_cases"],1)
        self.assertEqual(early60_pattern["historical_up_2pct_gross_60m_cases"],1)
        self.assertEqual(early60_pattern["historical_mean_return_60m_gross_pct"],"4.0000")
        self.assertEqual(early60_pattern["historical_matured_24h_cases"],0)
        self.assertFalse(early60_pattern["buy_authorized"])
        future=replace(scanner,observed_epoch=future24+10)
        self.j.record_public_surge_experience(future,{})
        mature=self.j.public_surge_casebook_summary()
        self.assertEqual(mature["cohorts"]["EARLY_WATCH"]["matured_24h"],1)
        self.assertEqual(mature["cohorts"]["EARLY_WATCH"]["up_20pct_24h"],1)
        self.assertEqual(mature["cohorts"]["EARLY_WATCH"]["up_50pct_24h"],0)
        current=rank_scanner_with_early_momentum(
            scanner,report,casebook=mature)
        annot=next(x for x in current.universe if x.pair==pair).surge_pattern
        self.assertEqual(annot["historical_matured_24h_cases"],1)
        self.assertEqual(annot["historical_up_20pct_24h_cases"],1)
        self.assertFalse(annot["predictive_model_validated"])
        self.assertFalse(annot["buy_authorized"])
        self.assertFalse(current.summary()["trade_authorized"])


if __name__=="__main__":
    unittest.main()
