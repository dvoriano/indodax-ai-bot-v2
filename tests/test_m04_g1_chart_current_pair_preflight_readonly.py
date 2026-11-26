"""Offline original M03 ACK + recent trade pair preflight."""
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from scripts import m04_g1_chart_current_pair_preflight_readonly as m

NOW=1795659600


def checkpoint(t, pairs, *, pid="42", invocation="a"*32, legacy=False):
    obj=dict(status="M03_WS3_CONTINUOUS_CHECKPOINT",observed_epoch=t,
             execution_allowed=False,indexed_trade_pairs=pairs)
    if not legacy:
        obj.update(indexed_trade_pairs_acknowledged=pairs,
                   indexed_trade_channels_acknowledged=len(pairs))
    return dict(MESSAGE=json.dumps(obj),_PID=pid,
                _SYSTEMD_INVOCATION_ID=invocation,
                __REALTIME_TIMESTAMP=str(t*1000000))


class CurrentPairPreflightTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db=Path(tmp.name)/"decisions.sqlite3"
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE ws_trade_publications(x)")
            db.execute("CREATE TABLE m03_ws3_trade_publication_times(x)")
            db.execute("CREATE TABLE m03_ws3_trade_samples("
                       "pair TEXT,exchange_epoch INTEGER,sequence INTEGER,recorded_epoch INTEGER)")

    def add(self,pair,seconds,seq,receipt=None):
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO m03_ws3_trade_samples VALUES(?,?,?,?)",
                       (pair,NOW-seconds,seq,NOW-seconds+1 if receipt is None else receipt))

    def check(self,records):
        before=self.db.read_bytes()
        r=m.choose_pair(records=records,source_db=self.db,now=NOW)
        self.assertEqual(before,self.db.read_bytes())
        self.assertEqual(r["rest_requests"],0)
        self.assertEqual(r["websocket_connections"],0)
        self.assertFalse(r["independent_g1_verified"])
        self.assertFalse(r["execution_allowed"])
        return r

    def test_rank_only_current_ack_intersection_and_on_time_activity(self):
        for i in range(4):
            self.add("enj_idr",10+i,i+1)
        self.add("cst_idr",20,1)
        self.add("btc_idr",30,1)
        self.add("btc_idr",40,2)
        self.add("cst_idr",10,2,NOW+1)
        self.add("floki_idr",10,1)
        r=self.check([checkpoint(NOW-50,["btc_idr","cst_idr","floki_idr"]),
                      checkpoint(NOW-10,["btc_idr","cst_idr","enj_idr","floki_idr"])])
        self.assertEqual(r["selected_pair"],"btc_idr")
        self.assertEqual(r["ack_intersection_pairs"],["btc_idr","cst_idr"])
        self.assertEqual(r["active_ack_pair_candidates"][0]["local_trade_rows_120s"],2)

    def test_process_transition_stale_and_legacy_block(self):
        for a,b,status in (
            (checkpoint(NOW-50,["btc_idr"]),checkpoint(NOW-10,["btc_idr"],pid="43"),
             "PREFLIGHT_RECENT_CHECKPOINT_TRANSITION_OR_STALE"),
            (checkpoint(NOW-110,["btc_idr"]),checkpoint(NOW-70,["btc_idr"]),
             "PREFLIGHT_RECENT_CHECKPOINT_TRANSITION_OR_STALE"),
            (checkpoint(NOW-50,["btc_idr"],legacy=True),checkpoint(NOW-10,["btc_idr"]),
             "PREFLIGHT_ACK_UNKNOWN"),
        ):
            with self.subTest(status=status):
                r=self.check([a,b])
                self.assertEqual(r["status"],status)
                self.assertIsNone(r["selected_pair"])

    def test_empty_recent_activity_not_a_pair(self):
        self.add("btc_idr",200,1)
        r=self.check([checkpoint(NOW-50,["btc_idr"]),
                      checkpoint(NOW-10,["btc_idr"])])
        self.assertEqual(r["status"],"PREFLIGHT_NO_RECENT_LOCAL_ACTIVITY")
        self.assertIsNone(r["selected_pair"])

    def test_failure_subtype_only_no_remote_message(self):
        rows=[{"MESSAGE":"M03_WS_WATCH_JSON_INVALID:OBSERVE:STRUCTURED_EXTRA_DATA secret"},
              {"MESSAGE":"M03_WS_WATCH_JSON_INVALID:OBSERVE:STRUCTURED_EXTRA_DATA twice"},
              {"MESSAGE":"M03_WS_WATCH_JSON_INVALID unknown text"}]
        self.assertEqual(m.failure_counts(rows),{
            "M03_WS_WATCH_JSON_INVALID:OBSERVE:STRUCTURED_EXTRA_DATA":2,
            "M03_WS_WATCH_JSON_INVALID_SUBTYPE_UNAVAILABLE":1})


if __name__=="__main__":
    unittest.main()
