"""M04-Q: real G1 minute rejects known WS discontinuity boundaries.

These only test existing journal+orchestrator. Synthetic public fixtures
cannot attest full trade tape, source origin, paid AI or trading.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.trade_stream import PublicTradeStreamJournal
from test_m03g_bulk import FakeBulkMarket, fake_screen
from test_m04e_trade_stream import ack, replay_pub
from test_m04h_closed_minute_join import MINUTE, sample_frame

TARGET = MINUTE - 60


def make_gap_journal(path: Path, *, marker_time: int, first: bool = True,
                     last: bool = True) -> PublicTradeStreamJournal:
    j = PublicTradeStreamJournal(path)
    j.acknowledge("btc_idr", ack(100))
    if first:
        j.ingest("btc_idr", sample_frame(101,TARGET+5,"buy","1000"))
    else:
        j.ingest("btc_idr", sample_frame(101,TARGET-80,"buy","1000"))
    j.mark_disconnected("btc_idr")
    # Server replay loses publication 102; old offset101 remains intact.
    partial=j.acknowledge("btc_idr",ack(105,publications=[
        replay_pub(103),replay_pub(105),
    ]))
    assert partial["stream_status"] == "GAP"
    with patch("indodax_ai_bot.trade_stream.time.time",
               return_value=marker_time):
        j.acknowledge("btc_idr",ack(110),fresh_segment=True)
    if last:
        j.ingest("btc_idr",sample_frame(111,TARGET+40,"sell","2500"))
    j.mark_disconnected("btc_idr")
    return j


class M04QPublicGapAwareMinuteTests(unittest.TestCase):
    def test_same_epoch_trades_across_gap_are_rejected_without_partial_volume(self):
        with TemporaryDirectory() as td:
            j=make_gap_journal(Path(td)/"ws.sqlite3",
                               marker_time=TARGET-20)
            result=j.closed_minute_sample(
                "btc_idr",closed_1m_epoch=TARGET,now_epoch=MINUTE+90,
            )
            self.assertEqual(result["status"],"REJECT")
            self.assertEqual(result["reason"],
                             "REJECT_WS_MINUTE_CROSSES_RECORDED_GAP")
            self.assertTrue(result["recorded_gap_crosses_trade_offsets"])
            self.assertFalse(result["rebaseline_observed_in_minute"])
            self.assertEqual(result["unrecoverable_gap_rebaseline_count"],1)
            self.assertFalse(result["observed_rows_aggregated"])
            self.assertNotIn("total_idr_volume_visible",result)
            self.assertFalse(result["minute_trade_coverage_complete"])
            self.assertFalse(result["g1_receipt_issuance_allowed"])
            self.assertFalse(result["g2_provider_call_allowed"])
            self.assertFalse(result["execution_allowed"])

    def test_rebaseline_inside_minute_rejected_even_without_both_sides(self):
        with TemporaryDirectory() as td:
            j=make_gap_journal(Path(td)/"ws.sqlite3",
                               marker_time=TARGET+25,last=False)
            result=j.closed_minute_sample(
                "btc_idr",closed_1m_epoch=TARGET,now_epoch=MINUTE+90,
            )
            self.assertEqual(result["status"],"REJECT")
            self.assertEqual(result["reason"],
                             "REJECT_WS_REBASELINE_IN_CLOSED_MINUTE")
            self.assertTrue(result["rebaseline_observed_in_minute"])
            self.assertFalse(result["recorded_gap_crosses_trade_offsets"])
            self.assertNotIn("trade_rows_visible_in_minute",result)
            self.assertFalse(result["execution_allowed"])

    def test_epoch_change_marker_inside_minute_rejected_without_ws_trades(self):
        with TemporaryDirectory() as td:
            j=PublicTradeStreamJournal(Path(td)/"ws.sqlite3")
            j.acknowledge("btc_idr",ack(100))
            j.mark_disconnected("btc_idr")
            old=j.acknowledge("btc_idr",ack(3,epoch="1633996542"))
            self.assertEqual(old["stream_status"],"GAP")
            with patch("indodax_ai_bot.trade_stream.time.time",
                       return_value=TARGET+25):
                j.acknowledge("btc_idr",ack(4,epoch="1633996542"),
                              fresh_segment=True)
            report=j.closed_minute_sample(
                "btc_idr",closed_1m_epoch=TARGET,now_epoch=MINUTE+90,
            )
            self.assertEqual(report["reason"],
                             "REJECT_WS_REBASELINE_IN_CLOSED_MINUTE")
            self.assertEqual(report["unrecoverable_gap_rebaseline_count"],1)
            self.assertNotIn("total_idr_volume_visible",report)
            self.assertFalse(report["execution_allowed"])

    def test_valid_postgap_sample_keeps_historic_warning_and_no_auth(self):
        with TemporaryDirectory() as td:
            j=make_gap_journal(Path(td)/"ws.sqlite3",
                               marker_time=TARGET-75,first=False)
            report=j.closed_minute_sample(
                "btc_idr",closed_1m_epoch=TARGET,now_epoch=MINUTE+90,
            )
            self.assertEqual(report["status"],"PUBLIC_SAMPLE_OBSERVED")
            self.assertEqual(report["trade_rows_visible_in_minute"],1)
            self.assertEqual(report["sell_idr_volume_visible"],"2500")
            self.assertTrue(report["has_explicit_unrecoverable_ws_discontinuity"])
            self.assertEqual(report["unrecoverable_gap_rebaseline_count"],1)
            self.assertEqual(report["minute_segment_continuity_status"],
                             "NO_RECORDED_GAP_IN_MINUTE_UNATTESTED")
            self.assertFalse(report["historical_gap_start_time_verified"])
            self.assertFalse(report["minute_trade_coverage_complete"])
            self.assertFalse(report["g1_receipt_issuance_allowed"])

    def test_invalid_recorded_gap_ledger_rejected_before_aggregation(self):
        with TemporaryDirectory() as td:
            db=Path(td)/"ws.sqlite3"
            j=make_gap_journal(db,marker_time=TARGET-75,first=False)
            with sqlite3.connect(db) as con:
                con.execute(
                    "UPDATE ws_stream_discontinuities "
                    "SET new_offset=prior_offset WHERE pair='btc_idr'"
                )
            report=j.closed_minute_sample(
                "btc_idr",closed_1m_epoch=TARGET,now_epoch=MINUTE+90,
            )
            self.assertEqual(report["status"],"REJECT")
            self.assertEqual(report["reason"],"REJECT_WS_GAP_LEDGER_INVALID")
            self.assertNotIn("total_idr_volume_visible",report)
            self.assertFalse(report["g1_receipt_issuance_allowed"])

    def test_same_existing_bulk_mainpath_carries_gap_reject_no_ai_trade(self):
        with TemporaryDirectory() as td:
            wsdb=Path(td)/"ws.sqlite3"
            make_gap_journal(wsdb,marker_time=TARGET-20)
            out=StringIO()
            with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner:
                scanner.return_value.scan.return_value=fake_screen()
                with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                           return_value=FakeBulkMarket()):
                    with patch("indodax_ai_bot.orchestrator.time.time",
                               return_value=MINUTE+90):
                        with redirect_stdout(out):
                            exitcode=main([
                                "--bulk-deep-once","--ws-db",str(wsdb),
                                "--db",str(Path(td)/"decision.sqlite3"),
                            ])
            data=json.loads(out.getvalue())
            b=data["public_bulk"]
            sample=b["btc_ws_closed_minute_sample"]
            self.assertEqual(exitcode,0)
            self.assertEqual(sample["reason"],
                             "REJECT_WS_MINUTE_CROSSES_RECORDED_GAP")
            self.assertEqual(b["btc_rest_ws_observability"]["reason"],
                             "NO_VALID_SAME_MINUTE_WS_SAMPLE")
            self.assertEqual(
                b["btc_rest_ws_observability"]["public_ws_gap_rejection_reason"],
                "REJECT_WS_MINUTE_CROSSES_RECORDED_GAP",
            )
            self.assertTrue(
                b["btc_rest_ws_observability"]["public_ws_gap_in_minute"]
            )
            self.assertFalse(b["g1_verified"])
            self.assertEqual(data["ai_shadow"]["completed_roles"],[])
            self.assertIsNone(data["final_decision"])
            self.assertFalse(data["execution_allowed"])


if __name__=="__main__":
    unittest.main()
