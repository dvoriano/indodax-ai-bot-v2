"""M03-B closed-candle indicators, source fault and no-trade integration tests."""
from __future__ import annotations
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.contracts import MarketSnapshot, SourceReceipt
from indodax_ai_bot.deep_analysis import analyze_observation, deep_scan
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.public_market import Candle, PublicDataError, PublicObservation
from indodax_ai_bot.scanner import ScreenedPair, ScannerResult, MAIN_EXCLUDED_PAIRS, DailyPairScanner


def observation(pair="btc_idr", gap=False, no_trade=False, wide=False):
    end = 1800000120
    minute = []
    for i in range(31):
        start = end - (31-i)*60
        x = D("100") + D(i)/D("100")
        minute.append(Candle(start,60,x,x+1,x-1,x+D(".005"),D("40") if i==30 else D("10")))
    if gap:
        minute.pop(-3)
    base = (end//900-2)*900
    fifteen = (Candle(base,900,D("98"),D("101"),D("97"),D("99"),D("100")),
               Candle(base+900,900,D("99"),D("102"),D("98"),D("100"),D("120")))
    receipt=SourceReceipt(pair,end,end+10,"PUBLIC_UNATTESTED",False)
    snap=MarketSnapshot(receipt,D("95") if wide else D("100.29"),D("100.30"))
    return PublicObservation(pair,snap,minute[-1],
        Candle(end-300,300,D("99.7"),D("101"),D("99"),D("100.3"),D("100")),
        fifteen[-1],500,False,300,False,"UNTRUSTED",
        tuple(minute),fifteen,D("350"),D("100"),0 if no_trade else 6,D("100000"),D("50000"))


def screening():
    names=("btc_idr","eth_idr","sol_idr")
    xs=tuple(ScreenedPair(p,D("30000000"),D("100"),D("101"),D("50"),D("6"),1800000115) for p in names)
    return ScannerResult(1800000120,398,300,98,xs,xs[:2])


class MockMarket:
    def __init__(self):
        self.calls=[]
    def observe(self,pair):
        self.calls.append(pair)
        if pair=="eth_idr":
            raise PublicDataError("STALE_CLOSED_1M")
        return observation(pair)


class M03BDeepTest(unittest.TestCase):
    def test_indicators_diagnostic_not_trade(self):
        result=analyze_observation(observation())
        self.assertEqual(result.state,"PUBLIC_EVIDENCE_UNATTESTED")
        self.assertGreater(result.score,50)
        self.assertLessEqual(result.score,100)
        self.assertEqual(result.rsi14,D("100"))
        self.assertGreater(result.ema8,result.ema21)
        self.assertGreater(result.volume_ratio,D("1"))
        self.assertGreater(result.book_imbalance,D("0"))
        self.assertFalse(result.summary()["trade_history_complete"])
        self.assertFalse(result.summary()["trade_authorized"])

    def test_missing_minute_gap_denies_score(self):
        result=analyze_observation(observation(gap=True))
        self.assertEqual(result.state,"REJECT_INCOMPLETE_EVIDENCE")
        self.assertIsNone(result.score)
        self.assertIn("GAPPED_60S_HISTORY",result.rejection_reason)

    def test_candle_interval_checks_both_neighbor_timeframes(self):
        obs=observation()
        for index in (-1,-2):
            with self.subTest(index=index):
                minute=list(obs.candles_1m)
                minute[index]=replace(minute[index],interval=300)
                changed=replace(obs,candles_1m=tuple(minute))
                result=analyze_observation(changed)
                self.assertEqual(result.state,"REJECT_INCOMPLETE_EVIDENCE")
                self.assertEqual(result.rejection_reason,"GAPPED_60S_HISTORY")
        fifteen=list(obs.candles_15m)
        fifteen[-1]=replace(fifteen[-1],interval=60)
        changed=replace(obs,candles_15m=tuple(fifteen))
        self.assertEqual(analyze_observation(changed).rejection_reason,"GAPPED_900S_HISTORY")

    def test_sparse_trade_and_wide_spread_fail_closed(self):
        self.assertEqual(analyze_observation(observation(no_trade=True)).rejection_reason,"NO_RECENT_TRADE_SAMPLE")
        self.assertEqual(analyze_observation(observation(wide=True)).rejection_reason,"SCALPING_SPREAD_TOO_WIDE")

    def test_pilot_budget_and_individual_source_fault(self):
        market=MockMarket()
        results=deep_scan(market,screening())
        self.assertEqual(market.calls,["btc_idr","eth_idr"])
        self.assertEqual(results.summary()["deep_examined_count"],2)
        self.assertEqual(results.analyzed[1].state,"REJECT_SOURCE_ERROR")
        self.assertFalse(results.summary()["trade_authorized"])
        with self.assertRaises(ValueError):
            deep_scan(market,screening(),limit=3)

    def test_deep_is_wired_to_journal_but_never_executes(self):
        with TemporaryDirectory() as td:
            app=DailyScalpingOrchestrator(Settings.from_env({}),DecisionJournal(Path(td)/"journal.sqlite3"))
            with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scan:
                scan.return_value.scan.return_value=screening()
                result=app.run_deep_cycle("cycle1",market=MockMarket())
            self.assertEqual(result.final_decision.value,"REJECT")
            self.assertEqual(result.reason,"G1_TRADE_COVERAGE_AND_RISK_NOT_CONNECTED")
            self.assertFalse(result.execution_allowed)
            self.assertEqual(result.deep["deep_examined_count"],2)
            self.assertEqual(app.journal.count(),1)

    def test_main_excludes_pepe_floki_usdt_usdc(self):
        from test_m03a_scanner import FakeIndodaxAll
        f=FakeIndodaxAll()
        self.assertTrue({"pepe_idr","floki_idr","usdt_idr","usdc_idr"}.issubset(MAIN_EXCLUDED_PAIRS))
        for p in MAIN_EXCLUDED_PAIRS:
            f.pairs.append({"ticker_id":p,"id":p.replace("_",""),
                            "symbol":p.replace("_","").upper(),"base_currency":"idr"})
            f.tickers[p]={"server_time":f.now-1,"buy":"100","sell":"101",
                          "last":"100","low":"99","high":"101","vol_idr":"9999999999999"}
        rank=DailyPairScanner(f.client()).scan()
        self.assertEqual(rank.all_idr_pairs,49)
        self.assertEqual(rank.excluded_pairs,4)
        self.assertEqual(rank.watchlist[0].pair,"m000_idr")
        self.assertTrue(all(x.pair not in MAIN_EXCLUDED_PAIRS for x in rank.shortlist))


if __name__=="__main__":
    unittest.main()
