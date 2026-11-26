"""Offline original WS3 chart tick probe; real network never used."""
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import m04_g1_ws3_chart_tick_probe_readonly as m

NOW=1795508400
PAIR="enj_idr"
CHANNEL="chart:tick-enjidr"


class FakeWS:
    class WebSocketTimeoutException(Exception):
        pass

    def __init__(self, frames):
        self.frames=list(frames)
        self.t=0
        self.sent=[]
        self.attempts=0
        self.closed=False

    def create_connection(self,url,**kwargs):
        self.attempts+=1
        if url!=m.WS_URL:
            raise AssertionError("WRONG_ORIGIN")
        return self

    def settimeout(self,seconds):
        pass

    def send(self,payload):
        self.sent.append(json.loads(payload))

    def recv(self):
        if self.frames:
            delay,frame=self.frames.pop(0)
            self.t+=delay
            return json.dumps(frame) if not isinstance(frame,str) else frame
        self.t+=1
        raise self.WebSocketTimeoutException()

    def close(self,**kw):
        self.closed=True

    def clock(self):
        return self.t

    def epoch(self):
        return NOW+self.t


def prefix():
    return [(0,{"id":1,"result":{"client":"fixture"}}),
            (0,{"id":2,"result":{"recoverable":True,"epoch":"123","offset":10}})]


def pub(off,t,seq,volume="2",price="11",channel=CHANNEL):
    return {"result":{"channel":channel,"data":{
        "offset":off,"data":[[NOW+t,seq,price,volume]]}}}


class ChartTickProbeTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db=Path(tmp.name)/"decisions.sqlite3"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE ws_trade_publications("
                        "pair TEXT,epoch TEXT,offset INTEGER,payload_sha256 TEXT,rows_json TEXT)")
            con.execute("CREATE TABLE m03_ws3_trade_publication_times("
                        "pair TEXT,epoch TEXT,offset INTEGER,recorded_epoch INTEGER)")

    def insert_raw(self,off,t,seq,*,receipt=None,price="11",quote="22",base="2",bad=False):
        raw=json.dumps([["enjidr",NOW+t,seq,"buy",price,quote,base]],
                       separators=(",",":"))
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO ws_trade_publications VALUES(?,?,?,?,?)",
                        (PAIR,"123",off,"0"*64 if bad else sha256(raw.encode()).hexdigest(),raw))
            if receipt is not None:
                con.execute("INSERT INTO m03_ws3_trade_publication_times VALUES(?,?,?,?)",
                            (PAIR,"123",off,NOW+receipt))

    def run_probe(self,frames):
        ws=FakeWS(frames)
        before=self.db.read_bytes()
        with patch.dict(os.environ,m._LOCKS,clear=True):
            r=m.run(pair=PAIR,seconds=30,source_db=self.db,ws_module=ws,
                    monotonic=ws.clock,epoch=ws.epoch)
        self.assertEqual(before,self.db.read_bytes())
        self.assertEqual(ws.attempts,1)
        self.assertTrue(ws.closed)
        self.assertEqual(r["rest_requests"],0)
        self.assertFalse(r["execution_allowed"])
        self.assertFalse(r["independent_g1_verified"])
        return r,ws

    def test_ack_and_raw_base_volume_identity_agreement(self):
        self.insert_raw(40,10,22,receipt=11)
        self.insert_raw(41,2,21,receipt=3)
        self.insert_raw(42,20,23,receipt=21)
        r,ws=self.run_probe(prefix()+[
            (2,pub(11,2,21)),(8,pub(12,10,22)),(10,pub(13,20,23))])
        self.assertEqual(r["status"],"CHART_TICK_RAW_COMPARISON_RESEARCH_ONLY")
        self.assertEqual(r["publication_count"],3)
        self.assertEqual(r["chart_ticks_observed"],3)
        self.assertEqual(r["baseline_offset"],10)
        self.assertEqual(r["last_offset"],13)
        w=r["comparison"]
        self.assertEqual(w["comparable_seconds"],17)
        self.assertEqual(w["chart_comparable_rows"],1)
        self.assertEqual(w["local_comparable_rows"],1)
        self.assertEqual(w["shared_identity_price_base_matches"],1)
        self.assertEqual(w["time_price_volume_matches_if_chart_is_quote"],0)
        self.assertTrue(w["nonempty_base_signature_agreement"])
        self.assertFalse(r["chart_volume_unit_equivalence_verified"])
        self.assertEqual(ws.sent[1]["params"],{"channel":CHANNEL})

    def test_gap_and_denial_fail_stop_without_reconnect(self):
        for frames in (prefix()+[(2,pub(12,2,1))],
                       prefix()[:1]+[(0,{"id":2,"error":{"code":103}})],
                       prefix()+[(2,pub(11,2,1,channel="market:trade-activity-enjidr"))]):
            with self.subTest(frames=frames):
                r,_=self.run_probe(frames)
                self.assertEqual(r["status"],"CHART_PROBE_BLOCKED")
                self.assertEqual(r["connection_attempts"],1)

    def test_no_ticks_and_edge_only_are_unknown(self):
        r,_=self.run_probe(prefix())
        self.assertEqual(r["status"],"CHART_ACK_ONLY_NO_TICKS")
        r,_=self.run_probe(prefix()+[(2,pub(11,2,1))])
        self.assertEqual(r["status"],"CHART_NO_INTERIOR_OVERLAP")

    def test_ack_only_reports_same_window_raw_activity_without_claiming_outage(self):
        self.insert_raw(40,0,20,receipt=1)  # ACK second excluded.
        self.insert_raw(41,10,21,receipt=11)
        self.insert_raw(42,10,22,receipt=30)
        self.insert_raw(43,12,23,receipt=31)
        self.insert_raw(44,13,24)
        self.insert_raw(45,30,25,receipt=30)  # Final second excluded.
        r,_=self.run_probe(prefix())
        self.assertEqual(r["status"],"CHART_ACK_ONLY_NO_TICKS")
        self.assertEqual(r["local_raw_activity_status"],"LOCAL_TRADE_ACTIVITY_OBSERVED")
        self.assertEqual(r["local_raw_trade_rows_by_finish"],2)
        self.assertEqual(r["local_raw_trade_seconds_by_finish"],1)
        self.assertEqual(r["local_raw_unknown_receipts"],1)
        self.assertEqual(r["local_raw_late_receipts"],1)
        self.assertEqual(r["local_window_start_epoch"],NOW+1)
        self.assertEqual(r["local_window_end_epoch"],NOW+30)
        self.assertTrue(r["local_nonempty_does_not_prove_chart_outage"])
        self.assertFalse(r["complete_trade_tape_verified"])

    def test_ack_only_empty_local_unknown_and_invalid_source_explicit(self):
        r,_=self.run_probe(prefix())
        self.assertEqual(r["local_raw_activity_status"],
                         "NO_ON_TIME_LOCAL_TRADE_OBSERVED_UNVERIFIED")
        self.assertEqual(r["local_raw_trade_rows_by_finish"],0)
        self.assertTrue(r["local_empty_is_not_exchange_inactivity"])
        self.insert_raw(40,10,21,receipt=11,bad=True)
        r,_=self.run_probe(prefix())
        self.assertEqual(r["local_raw_activity_status"],
                         "LOCAL_ACK_WINDOW_SOURCE_UNAVAILABLE")
        self.assertNotIn("local_raw_trade_rows_by_finish",r)

    def test_retrospective_ack_context_rejects_future_and_expired_scope(self):
        with patch.object(m,"read_raw_overlap",
                          side_effect=AssertionError("invalid scope must not read")):
            for now in (NOW+29,NOW+86400):
                r=m.audit_ack_only_local_activity(
                    source_db=self.db,pair=PAIR,ack_epoch=NOW,
                    finish_epoch=NOW+30,now_epoch=now)
                self.assertEqual(r["local_raw_activity_status"],
                                 "LOCAL_ACK_WINDOW_SCOPE_INVALID")

    def test_unknown_late_and_conflicting_raw_not_credited(self):
        self.insert_raw(40,10,22,receipt=31)
        self.insert_raw(41,11,23)
        frames=prefix()+[(2,pub(11,2,21)),(8,pub(12,10,22)),
                         (10,pub(13,20,24))]
        r,_=self.run_probe(frames)
        self.assertEqual(r["local_raw_unknown_receipts"],1)
        self.assertEqual(r["local_raw_late_receipts"],1)
        self.assertEqual(r["comparison"]["local_comparable_rows"],0)
        self.assertEqual(r["comparison"]["chart_only_if_base"],1)
        self.insert_raw(42,10,22,receipt=11,base="3")
        r,_=self.run_probe(frames)
        self.assertEqual(r["status"],"CHART_LOCAL_COMPARISON_BLOCKED")

    def test_bad_digest_blocks_local_comparison(self):
        self.insert_raw(40,10,22,receipt=11,bad=True)
        r,_=self.run_probe(prefix()+[(2,pub(11,2,21)),
                            (8,pub(12,10,22)),(10,pub(13,20,23))])
        self.assertEqual(r["status"],"CHART_LOCAL_COMPARISON_BLOCKED")

    def test_signature_multiplicity_does_not_require_same_sequence(self):
        c={"ticks":[(NOW+10,1,Decimal("11"),Decimal("2")),
                    (NOW+10,2,Decimal("11"),Decimal("2"))]}
        rows=[(NOW+10,9,Decimal("11"),Decimal("22"),Decimal("2"))]
        w=m.compare_overlap(c,rows,start=NOW,end=NOW+20)
        self.assertEqual(w["time_price_volume_matches_if_chart_is_base"],1)
        self.assertEqual(w["chart_only_if_base"],1)
        self.assertEqual(w["shared_timestamp_sequence_identities"],0)
        self.assertFalse(w["nonempty_base_signature_agreement"])

    def test_no_connection_without_locks_or_original_db(self):
        ws=FakeWS(prefix())
        with patch.dict(os.environ,{},clear=True):
            r=m.run(pair=PAIR,seconds=30,source_db=self.db,ws_module=ws)
        self.assertEqual(r["connection_attempts"],0)
        with patch.dict(os.environ,m._LOCKS,clear=True):
            r=m.run(pair=PAIR,seconds=30,source_db=self.db.parent/"missing",ws_module=ws)
        self.assertEqual(r["connection_attempts"],0)
        self.assertEqual(ws.attempts,0)


if __name__=="__main__":
    unittest.main()
