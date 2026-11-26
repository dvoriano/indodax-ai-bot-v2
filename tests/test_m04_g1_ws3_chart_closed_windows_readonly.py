"""Offline prospective closed-window chart research tests."""
from decimal import Decimal as D
import os
import unittest
from unittest.mock import patch

from scripts import m04_g1_ws3_chart_closed_windows_readonly as m
from scripts import m04_g1_ws3_chart_tick_probe_readonly as probe
from tests.test_m04_g1_ws3_chart_tick_probe_readonly import (
    FakeWS, prefix, pub, NOW, PAIR,
)

INV="a"*32


class ChartClosedWindowTests(unittest.TestCase):
    def test_explicit_long_collector_receipt_and_health_checks(self):
        ws=FakeWS(prefix()+[(2,pub(11,2,21)),(8,pub(12,10,22))])
        checks=[]
        cap=probe.collect_chart_ticks(pair=PAIR,seconds=1020,
            ws_module=ws,monotonic=ws.clock,epoch=ws.epoch,
            closed_window_research=True,health_check=lambda:checks.append(ws.t))
        self.assertEqual(ws.attempts,1)
        self.assertTrue(ws.closed)
        self.assertEqual(cap["chart_stream_epoch"],"123")
        self.assertEqual(cap["tick_receipts"],{(NOW+2,21):NOW+2,(NOW+10,22):NOW+10})
        self.assertEqual(cap["finish_epoch"],NOW+1020)
        self.assertGreater(len(checks),90)
        with self.assertRaisesRegex(ValueError,"CHART_SCOPE_INVALID"):
            probe.collect_chart_ticks(pair=PAIR,seconds=1020,ws_module=ws,
                monotonic=ws.clock,epoch=ws.epoch)

    def test_health_change_closes_socket_without_reconnect(self):
        ws=FakeWS(prefix()+[(2,pub(11,2,21))])
        def health():
            if ws.t>=10:
                raise ValueError("CHART_ORIGINAL_SERVICE_CHANGED")
        with self.assertRaisesRegex(ValueError,"CHART_ORIGINAL_SERVICE_CHANGED"):
            probe.collect_chart_ticks(pair=PAIR,seconds=1020,
                ws_module=ws,monotonic=ws.clock,epoch=ws.epoch,
                closed_window_research=True,health_check=health)
        self.assertTrue(ws.closed)
        self.assertEqual(ws.attempts,1)

    def test_closed_windows_cutoff_and_interior_are_separate(self):
        end=NOW+3720
        ticks=[(end-3600-1,1,D("11"),D("2")),
               (end-3500,2,D("11"),D("2")),
               (end-100,3,D("11"),D("2")),
               (end-90,4,D("11"),D("2")),
               (end-1,5,D("11"),D("2"))]
        receipts={t[:2]:t[0] for t in ticks}
        receipts[ticks[3][:2]]=end+1
        cap=dict(ack_epoch=NOW,finish_epoch=end+5,ticks=ticks,tick_receipts=receipts)
        def raw(path,pair,start,stop,cutoff,**kw):
            self.assertEqual(stop,end)
            self.assertEqual(cutoff,end)
            self.assertEqual(kw["max_identities"],100000)
            return [(t,s,p,D("22"),v) for t,s,p,v in ticks
                    if start<=t<stop and s!=4],0,1
        with patch.object(m,"read_raw_overlap",side_effect=raw):
            windows=m.compare_closed_windows(capture=cap,pair=PAIR,horizon="1h",
                source_db="fixture",records=[],expected_invocation=INV)
        self.assertEqual(set(windows),{"15m","1h"})
        self.assertEqual(windows["1h"]["chart_rows_by_cutoff"],3)
        self.assertEqual(windows["15m"]["chart_rows_by_cutoff"],2)
        self.assertEqual(windows["15m"]["chart_late_rows"],1)
        self.assertEqual(windows["1h"]["local_raw_late_receipts"],1)
        self.assertTrue(windows["1h"]["closed_window_observed_comparison"]
                        ["nonempty_base_signature_agreement"])
        self.assertEqual(windows["1h"]["interior_comparison"]["chart_comparable_rows"],2)
        self.assertFalse(windows["1h"]["chart_exchange_interior_spans_full_window"])
        self.assertFalse(windows["1h"]["all_sampled_m03_invocations_match"])
        self.assertFalse(windows["1h"]["complete_trade_tape_verified"])

    def test_empty_ticks_unknown_and_insufficient_duration_blocked(self):
        cap=dict(ack_epoch=NOW,finish_epoch=NOW+1020,ticks=[],tick_receipts={})
        with patch.object(m,"read_raw_overlap",return_value=([],0,0)):
            w=m.compare_closed_windows(capture=cap,pair=PAIR,horizon="15m",
                source_db="fixture",records=[],expected_invocation=INV)["15m"]
        self.assertFalse(w["closed_window_observed_comparison"]
                         ["nonempty_base_signature_agreement"])
        self.assertEqual(w["interior_comparison"]["comparable_seconds"],0)
        with self.assertRaisesRegex(ValueError,"CHART_CLOSED_WINDOW_NOT_SPANNED"):
            m.compare_closed_windows(capture=cap,pair=PAIR,horizon="1h",
                source_db="fixture",records=[],expected_invocation=INV)

    def test_preflight_blocks_without_locks_or_stable_original_services(self):
        ws=FakeWS(prefix())
        with patch.dict(os.environ,{},clear=True):
            r=m.run(horizon="15m",ws_module=ws)
        self.assertEqual(r["connection_attempts"],0)
        with patch.dict(os.environ,probe._LOCKS,clear=True):
            r=m.run(horizon="15m",ws_module=ws,service=lambda unit:{
                "ActiveState":"active","SubState":"running","MainPID":"1",
                "InvocationID":INV,"NRestarts":"1"})
        self.assertEqual(r["connection_attempts"],0)
        self.assertEqual(ws.attempts,0)
        self.assertFalse(r["execution_allowed"])
        self.assertEqual(r["rest_requests"],0)


if __name__=="__main__":
    unittest.main()
