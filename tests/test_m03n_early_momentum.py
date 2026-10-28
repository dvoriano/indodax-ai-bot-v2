"""M03-N early momentum from timestamped ticker_all, ONE existing scanner.

PUBLIC 1m samples are first-seen within a minute, NOT a true close,
not a volume delta, and never evidence of complete BUY trade flow or G1.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.deep_analysis import deep_scan
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.scanner import (
    DailyPairScanner, rank_scanner_with_early_momentum,
)
from test_m03a_scanner import FakeIndodaxAll
from test_m03d_same_window import MarketFixture


def fake_minute(fake: FakeIndodaxAll, i: int) -> None:
    fake.now = 1800000065 + i * 60
    for pair, ticker in fake.tickers.items():
        base = D("100")
        if pair == "m044_idr":
            base += D("0.08") * D(min(i, 50))
            base += D("0.4") * D(max(0, i-50))
        ticker.update({
            "server_time":fake.now-1,
            "last":str(base),
            "buy":str(base * D("0.999")),
            "sell":str(base * D("1.001")),
            "low":str(base*D("0.98")),
            "high":str(base*D("1.02")),
        })


class M03NEarlyMomentumTests(unittest.TestCase):
    def setUp(self):
        self.f=FakeIndodaxAll()

    def _populate(self, journal, *, start=0, stop=62, skip=()):
        latest=None
        for i in range(start,stop):
            if i in skip:
                continue
            fake_minute(self.f,i)
            initial=DailyPairScanner(self.f.client()).scan()
            reports=journal.record_and_assess_public_ticker_momentum(initial)
            latest=rank_scanner_with_early_momentum(initial,reports)
        return latest

    def test_closed_minute_buy_sell_counts_are_visible_sample_only(self):
        from indodax_ai_bot.public_market import IndodaxPublicMarket
        from test_m02_public_market import OfficialPublicFixtures

        class PartialTrades(OfficialPublicFixtures):
            def fetch(self,path):
                if path=="/api/trades/btcidr":
                    minute=(self.now//60)*60-60
                    return [
                        {"date":str(minute+12),"price":"100","amount":"2","type":"buy"},
                        {"date":str(minute+30),"price":"100","amount":"1","type":"sell"},
                        {"date":str(minute+45),"price":"100","amount":"1","type":"buy"},
                        {"date":str(self.now-3),"price":"100","amount":"5","type":"sell"},
                    ]
                return super().fetch(path)
        fixture=PartialTrades()
        obs=IndodaxPublicMarket(
            fetch=fixture.fetch,clock=lambda:fixture.now,
        ).observe("btc_idr")
        self.assertEqual(obs.visible_closed_1m_buy_count,2)
        self.assertEqual(obs.visible_closed_1m_sell_count,1)
        self.assertEqual(obs.visible_closed_1m_trade_count,3)
        self.assertEqual(obs.visible_closed_1m_buy_idr,D("300"))
        self.assertEqual(obs.visible_closed_1m_sell_idr,D("100"))
        self.assertFalse(obs.summary()["visible_trade_side_counts_complete"])
        self.assertFalse(obs.trade_history_complete)
        self.assertFalse(obs.ready)

    def test_startup_never_invents_5m_15m_60m_data(self):
        with TemporaryDirectory() as td:
            j=DecisionJournal(Path(td)/"db.sqlite3")
            fake_minute(self.f,0)
            raw=DailyPairScanner(self.f.client()).scan()
            reports=j.record_and_assess_public_ticker_momentum(raw)
            p=reports["m044_idr"]
            self.assertEqual(p["status"],"WARMUP_OR_GAPPED_MINUTE_SAMPLES")
            for horizon in ("1m","5m","15m","60m"):
                self.assertIsNone(p[f"return_{horizon}_pct"])
            self.assertFalse(p["buy_sell_flow_complete"])
            self.assertFalse(p["ticker_is_closed_candle"])
            self.assertFalse(p["trade_signal"])

    def test_rising_altcoin_prioritized_over_high_24h_turnover(self):
        with TemporaryDirectory() as td:
            ranked=self._populate(DecisionJournal(Path(td)/"db.sqlite3"))
            self.assertEqual(ranked.shortlist[0].pair,"m044_idr")
            self.assertEqual(ranked.watchlist[0].pair,"m044_idr")
            m=ranked.shortlist[0].early_momentum
            self.assertEqual(m["status"],"EARLY_WATCH_UNCALIBRATED")
            self.assertIsNotNone(m["return_60m_pct"])
            self.assertGreater(D(m["acceleration_current_5m_vs_prior_5m_pct_points"]),0)
            self.assertFalse(ranked.summary()["trade_authorized"])
            self.assertFalse(ranked.g1_verified)

    def test_restart_preserves_first_per_minute_quote_no_fake_acceleration(self):
        with TemporaryDirectory() as td:
            db=Path(td)/"d.sqlite3"
            j=DecisionJournal(db)
            fake_minute(self.f,0)
            first=DailyPairScanner(self.f.client()).scan()
            a=j.record_and_assess_public_ticker_momentum(first)["m044_idr"]
            self.f.tickers["m044_idr"].update({
                "last":"125", "buy":"124.9", "sell":"125.1",
                "high":"130", "low":"120",
            })
            second=DailyPairScanner(self.f.client()).scan()
            b=DecisionJournal(db).record_and_assess_public_ticker_momentum(second)["m044_idr"]
            self.assertEqual(b["first_observed_epoch_this_minute"],
                             a["first_observed_epoch_this_minute"])
            self.assertEqual(b["return_1m_pct"],None)
            with j._connect() as con:
                x=con.execute(
                    "SELECT last_idr FROM public_ticker_minute_samples "
                    "WHERE pair='m044_idr'",
                ).fetchone()[0]
            self.assertEqual(x,"100")

    def test_missing_minute_cannot_yield_fabricated_60m_return(self):
        with TemporaryDirectory() as td:
            j=DecisionJournal(Path(td)/"db.sqlite3")
            rank=self._populate(j,skip=(31,))
            m=next(x for x in rank.universe if x.pair=="m044_idr").early_momentum
            self.assertIsNone(m["return_60m_pct"])
            self.assertLess(m["consecutive_minute_samples"],61)
            self.assertFalse(m["source_trust_verified"])

    def test_stale_ticker_epoch_is_not_recorded_as_new_minute(self):
        with TemporaryDirectory() as td:
            j=DecisionJournal(Path(td)/"db.sqlite3")
            fake_minute(self.f,2)
            self.f.tickers["m044_idr"]["server_time"]=self.f.now-65
            sc=DailyPairScanner(self.f.client()).scan()
            rep=j.record_and_assess_public_ticker_momentum(sc)
            self.assertEqual(rep["m044_idr"]["status"],"WARMUP_OR_STALE_TICKER")
            with j._connect() as con:
                count=con.execute(
                    "SELECT COUNT(*) FROM public_ticker_minute_samples "
                    "WHERE pair='m044_idr'",
                ).fetchone()[0]
            self.assertEqual(count,0)

    def test_no_last_price_does_not_fabricate_midpoint(self):
        with TemporaryDirectory() as td:
            j=DecisionJournal(Path(td)/"db.sqlite3")
            fake_minute(self.f,0)
            s=DailyPairScanner(self.f.client()).scan()
            rows=tuple(replace(x,last_idr=None) if x.pair=="m044_idr" else x
                       for x in s.universe)
            repl=replace(s,universe=rows)
            rep=j.record_and_assess_public_ticker_momentum(repl)
            self.assertEqual(rep["m044_idr"]["status"],"WARMUP_OR_STALE_TICKER")

    def test_deep_analysis_binds_early_watch_but_not_trade_authority(self):
        with TemporaryDirectory() as td:
            rank=self._populate(DecisionJournal(Path(td)/"db.sqlite3"))
            class SameMinuteFixture(MarketFixture):
                def observe(self, pair):
                    raw=super().observe(pair)
                    # Preserve each source and candle timestamp relation.
                    shifted_close=rank.observed_epoch//60*60
                    delta=shifted_close-raw.snapshot.source.closed_1m_epoch
                    receipt=replace(
                        raw.snapshot.source,
                        closed_1m_epoch=shifted_close,
                        observed_epoch=rank.observed_epoch,
                    )
                    from indodax_ai_bot.contracts import MarketSnapshot
                    snap=MarketSnapshot(receipt,raw.snapshot.best_bid_idr,
                                        raw.snapshot.best_ask_idr)
                    minute=tuple(replace(c,start=c.start+delta)
                                 for c in raw.candles_1m)
                    fifteen=tuple(replace(c,start=c.start+delta)
                                  for c in raw.candles_15m)
                    return replace(
                        raw,snapshot=snap,candle_1m=minute[-1],
                        candle_5m=replace(raw.candle_5m,start=raw.candle_5m.start+delta),
                        candle_15m=fifteen[-1],
                        candles_1m=minute,candles_15m=fifteen,
                    )
            deep=deep_scan(SameMinuteFixture(),rank,limit=2)
            candidate=deep.analyzed[0].summary()
            self.assertEqual(candidate["pair"],"m044_idr")
            self.assertEqual(candidate["early_momentum"]["status"],
                             "EARLY_WATCH_UNCALIBRATED")
            self.assertFalse(candidate["trade_history_complete"])
            self.assertFalse(candidate["g1_verified"])
            self.assertFalse(candidate["trade_authorized"])

    def test_real_orchestrator_scan_path_persists_no_buy_ever(self):
        with TemporaryDirectory() as td:
            j=DecisionJournal(Path(td)/"real.sqlite3")
            app=DailyScalpingOrchestrator(Settings.from_env({}),j)
            fake_minute(self.f,0)
            first=app.run_scan_cycle("initial",market=self.f.client())
            self.assertEqual(first.final_decision.value,"REJECT")
            self.assertEqual(first.screening["momentum_status"],
                             "PUBLIC_MINUTE_SAMPLES_UNATTESTED")
            for i in range(1,62):
                fake_minute(self.f,i)
                last=app.run_scan_cycle("cycle"+str(i),market=self.f.client())
            self.assertEqual(last.screening["shortlist_top10"][0]["pair"],"m044_idr")
            self.assertEqual(last.screening["shortlist_top10"][0]["early_momentum"]["status"],
                             "EARLY_WATCH_UNCALIBRATED")
            self.assertEqual(last.final_decision.value,"REJECT")
            self.assertFalse(last.execution_allowed)
            self.assertEqual(j.count(),62)


if __name__ == "__main__":
    unittest.main()
