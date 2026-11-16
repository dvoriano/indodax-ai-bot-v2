"""Original WS3 observation capture, retention and premium boundary use."""
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from indodax_ai_bot.m03_ws3_local_observations_nonlive import (
    LocalObservationCapture, archive_local_observations,
    read_observed_minute_trade_ohlc,
)
from indodax_ai_bot.m03_ws_summary_live_watch import collect_ws3_market_summary_once
from indodax_ai_bot.m05_pitiden_premium_layout import (
    read_price_references, build_timeframes,
)
from tests.test_m03_ws_summary_live_watch import FakeWS, Clock, frames

START=1794330000

def summary(stamp,price):
    return {"pair":"btc_idr","observed_exchange_epoch":stamp,
            "last_idr":price,"volume_24h_idr":"500000000"}

def trade(stamp,seq,price,amount):
    return {"exchange_epoch":stamp,"sequence":seq,"price_idr":price,
            "source_reported_idr_amount":amount}

def source(capture, micro):
    return {"status":"M03_WS3_PUBLIC_SUMMARY_OBSERVED",
            "public_ws_authentication_observed":True,
            "local_observations":capture.snapshot(micro)}

def state():
    return {"btc_idr":{"trade_gap_observed":False,"trade_truncated":False,
                       "source_malformed":False}}

class WS3LocalObservationTests(unittest.TestCase):
    def test_same_original_socket_no_new_subscription(self):
        fs=frames(START+2,110)
        fs[2]["result"]["data"]["data"][0][6]="500000000"
        more=frames(START+20,120,offset=125)[2]
        more["result"]["data"]["data"][0][6]="500000000"
        provider=FakeWS(fs+[more])
        out=collect_ws3_market_summary_once(
            ws_module=provider,monotonic=Clock(),observe_seconds=5,
            epoch_fn=lambda:START+30,capture_local_observations=True)
        self.assertEqual(len(provider.sock.sent),2)
        self.assertEqual(len(out["local_observations"]["summary"]),4)
        self.assertEqual(len(out["pairs"]),2)
        self.assertEqual(out["local_observations"]["trades"],[])

    def test_asof_summary_edges_and_observed_trade_ohlc(self):
        with TemporaryDirectory() as root:
            p=Path(root)/"observations.sqlite3"
            cap=LocalObservationCapture()
            cap.observe_summary([summary(START+2,"100")],1,START+3)
            cap.observe_summary([summary(START+55,"110")],2,START+56)
            cap.observe_trades("btc_idr",[
                trade(START+3,1,"100","200"),
                trade(START+30,2,"120","360"),
                trade(START+55,3,"110","220"),
                trade(START+55,3,"110","220"),
            ],START+56)
            s=source(cap,state())
            result=archive_local_observations(s,now_epoch=START+61,evidence_db=p)
            self.assertEqual(result["summary_added"],2)
            self.assertEqual(result["trades_added"],3)
            self.assertEqual(archive_local_observations(
                s,now_epoch=START+62,evidence_db=p)["trades_added"],0)
            candle=read_observed_minute_trade_ohlc(p,"btc_idr",START,
                                                 now_epoch=START+65)
            self.assertEqual(candle["open"],"100")
            self.assertEqual(candle["high"],"120")
            self.assertEqual(candle["close"],"110")
            self.assertEqual(candle["observed_turnover_idr"],"780")
            self.assertFalse(candle["complete"])
            c={"pair":"btc_idr","observed_epoch":START+65,
               "latest_price_idr":"110","volume_24h_idr":"500000000"}
            refs=read_price_references(c,db_path=Path(root)/"absent",
                now_epoch=START+65,source_kind="WS3",evidence_path=p)
            self.assertEqual(refs,[(START+2,Decimal("100")),(START+55,Decimal("110"))])
            r=build_timeframes(c,refs)[0]
            self.assertEqual(r["change"],Decimal("10"))
            self.assertTrue(r["approx"])
            self.assertIsNone(r["volume"])
            # No future archived evidence allowed.
            self.assertEqual(read_price_references(c,db_path=Path(root)/"absent",
                now_epoch=START+60,source_kind="WS3",evidence_path=p),[])
            self.assertFalse((Path(root)/"absent").exists())

    def test_conflicting_trade_sequence_and_gap_are_not_promoted(self):
        with TemporaryDirectory() as root:
            p=Path(root)/"observations.sqlite3"
            cap=LocalObservationCapture()
            cap.observe_trades("btc_idr",[trade(START+3,1,"100","200")],START+5)
            bad=state()
            bad["btc_idr"]["trade_gap_observed"]=True
            self.assertEqual(cap.snapshot(bad)["trades"],[])
            cap.observe_trades("btc_idr",[trade(START+3,1,"101","201")],START+6)
            archive_local_observations(source(cap,state()),now_epoch=START+61,evidence_db=p)
            self.assertIsNone(read_observed_minute_trade_ohlc(
                p,"btc_idr",START,now_epoch=START+65))
