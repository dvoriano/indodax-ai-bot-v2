"""M04-N same-path BTC REST closed-minute -> persistent PUBLIC WS evidence.

Only public source diagnostics. These tests cannot authorize Market/Risk/Strategy,
a signed G1 issuer, fee-bearing API execution, or any BUY/SELL.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.deep_analysis import bulk_public_deep_scan
from indodax_ai_bot.trade_stream import PublicTradeStreamJournal
from test_m03g_bulk import FakeBulkMarket, fake_screen
from test_m04h_closed_minute_join import MINUTE, prepared_journal


def blocked_cohort(*args, **kwargs):
    return {
        "status": "REJECT_TEST_COHORT",
        "closed_1m_epoch": None,
        "g2_candidate_authorized": False,
        "live_trading_authorized": False,
    }


class M04NPersistentBTCMinuteTests(unittest.TestCase):
    def test_real_observed_btc_minute_available_even_if_cohort_rejected(self):
        with patch("indodax_ai_bot.deep_analysis._same_window_gate",
                   side_effect=blocked_cohort):
            r = bulk_public_deep_scan(FakeBulkMarket(), fake_screen())
        self.assertEqual(r["eligible_cohort"]["status"], "REJECT_TEST_COHORT")
        self.assertIsNone(r["eligible_cohort"]["closed_1m_epoch"])
        self.assertEqual(r["btc_public_rest_closed_1m_epoch_diagnostic"], MINUTE)
        self.assertTrue(r["btc_rest_observation_not_source_authentication"])
        self.assertFalse(r["g1_verified"])
        self.assertFalse(r["g1_source_admission"]["g2_provider_call_allowed"])

    def test_real_mainpath_join_even_when_candidate_cohort_rejects(self):
        with TemporaryDirectory() as td:
            ws = Path(td) / "public.sqlite3"
            prepared_journal(ws)
            out = StringIO()
            with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner:
                scanner.return_value.scan.return_value = fake_screen()
                with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                           return_value=FakeBulkMarket()) as market:
                    with patch("indodax_ai_bot.deep_analysis._same_window_gate",
                               side_effect=blocked_cohort):
                        with patch("indodax_ai_bot.orchestrator.time.time",
                                   return_value=MINUTE + 90):
                            with redirect_stdout(out):
                                code = main([
                                    "--bulk-deep-once", "--ws-db", str(ws),
                                    "--db", str(Path(td)/"decisions.sqlite3"),
                                ])
            obj = json.loads(out.getvalue())
            data = obj["public_bulk"]
            self.assertEqual(code, 0)
            self.assertEqual(data["btc_ws_minute_reference"],
                             "ACTUAL_PUBLIC_BTC_REST_CLOSED_CANDLE_UNATTESTED")
            self.assertEqual(data["eligible_cohort"]["status"], "REJECT_TEST_COHORT")
            self.assertEqual(data["btc_ws_closed_minute_sample"]["closed_1m_epoch"],
                             MINUTE)
            self.assertEqual(data["btc_ws_closed_minute_sample"]["trade_rows_visible_in_minute"],2)
            self.assertFalse(data["btc_ws_closed_minute_sample"]["minute_trade_coverage_complete"])
            self.assertFalse(data["g1_verified"])
            self.assertFalse(data["g1_source_admission"]["g1_receipt_issuance_allowed"])
            self.assertEqual(obj["ai_shadow"]["completed_roles"], [])
            self.assertEqual(obj["final_decision"], "REJECT")
            self.assertFalse(obj["execution_allowed"])

    def test_cohort_btc_rest_clock_disagreement_stops_ws_join(self):
        with TemporaryDirectory() as td:
            ws = Path(td) / "public.sqlite3"
            prepared_journal(ws)
            out = StringIO()
            def bad_cohort(*a, **kw):
                return {
                    "status": "ALIGNED_PUBLIC_UNATTESTED",
                    "closed_1m_epoch": MINUTE + 60,
                }
            with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner:
                scanner.return_value.scan.return_value = fake_screen()
                with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                           return_value=FakeBulkMarket()):
                    with patch("indodax_ai_bot.deep_analysis._same_window_gate",
                               side_effect=bad_cohort):
                        with redirect_stdout(out):
                            code = main([
                                "--bulk-deep-once","--ws-db",str(ws),
                                "--db",str(Path(td)/"decisions.sqlite3"),
                            ])
            obj=json.loads(out.getvalue())
            self.assertEqual(code,0)
            self.assertEqual(
                obj["public_bulk"]["btc_ws_closed_minute_sample"]["reason"],
                "BTC_REST_COHORT_CLOSED_MINUTE_MISMATCH",
            )
            self.assertFalse(obj["execution_allowed"])
            self.assertEqual(obj["final_decision"],"REJECT")

    def test_old_publications_do_not_hide_recent_closed_minute(self):
        with TemporaryDirectory() as td:
            ws = Path(td) / "public.sqlite3"
            j = prepared_journal(ws)
            chosen = MINUTE + 120
            pair = "btc_idr"
            epoch = "1633996541"
            prepared = []
            for offset in range(104, 2210):
                timestamp = (MINUTE - 300 if offset < 2208 else chosen + offset - 2208)
                row = [["btcidr",timestamp, offset,"buy","1","100","1"]]
                raw = json.dumps(row, separators=(",",":"), ensure_ascii=True)
                prepared.append((pair,epoch,offset,sha256(raw.encode()).hexdigest(),raw))
            with sqlite3.connect(ws) as con:
                con.executemany("INSERT INTO ws_trade_publications VALUES (?,?,?,?,?)",prepared)
                con.execute("UPDATE ws_stream_state SET last_offset=2209 WHERE pair=?", (pair,))
            report = j.closed_minute_sample(
                pair, closed_1m_epoch=chosen, now_epoch=chosen+90,
                max_publications=2,
            )
            self.assertEqual(report["status"], "PUBLIC_SAMPLE_OBSERVED")
            self.assertEqual(report["trade_rows_visible_in_minute"], 2)
            self.assertEqual(report["sampled_publication_count_total_epoch"],2209-100)
            self.assertEqual(report["offset_minimum_in_minute"],2208)
            self.assertEqual(report["offset_maximum_in_minute"],2209)
            self.assertFalse(report["g2_provider_call_allowed"])
            self.assertFalse(report["minute_trade_coverage_complete"])

    def test_excess_target_minute_rows_still_rejected_without_partial_volume(self):
        with TemporaryDirectory() as td:
            ws=Path(td)/"public.sqlite3"
            j=prepared_journal(ws)
            sample=j.closed_minute_sample("btc_idr",closed_1m_epoch=MINUTE,
                                          now_epoch=MINUTE+65,
                                          max_publications=1)
            self.assertEqual(sample["status"],"REJECT")
            self.assertEqual(sample["reason"],"REJECT_WS_SNAPSHOT_LIMIT_EXCEEDED")
            self.assertNotIn("total_idr_volume_visible",sample)
            self.assertFalse(sample["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
