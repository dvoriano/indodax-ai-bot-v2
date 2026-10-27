"""M04-H: one closed-minute WS sample joined to the existing public bulk G1 path.

Never authorizes a trusted G1 issuer, paid AI or trading. Pure test frames.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.trade_stream import PublicTradeStreamJournal, StreamEvidenceError
from test_m04e_trade_stream import ack, pub
from test_m03g_bulk import FakeBulkMarket, fake_screen

MINUTE = 1800000120


def sample_frame(offset: int, timestamp: int, side="buy", idr="1000",
                 seq: int | None = None):
    result = pub(offset, seq=seq)
    trade = result["result"]["data"]["data"][0]
    trade[1] = timestamp
    trade[3] = side
    trade[5] = idr
    return result


def prepared_journal(path: Path):
    journal = PublicTradeStreamJournal(path)
    journal.acknowledge("btc_idr", ack(100))
    journal.ingest("btc_idr", sample_frame(101, MINUTE + 4, "buy", "1000"))
    journal.ingest("btc_idr", sample_frame(102, MINUTE + 30, "sell", "2500"))
    journal.ingest("btc_idr", sample_frame(103, MINUTE + 60, "buy", "9000"))
    journal.mark_disconnected("btc_idr")
    return journal


class M04HClosedMinuteTests(unittest.TestCase):
    def test_closed_minute_aggregates_only_matching_trade_rows(self):
        with TemporaryDirectory() as td:
            j = prepared_journal(Path(td) / "ws.sqlite3")
            report = j.closed_minute_sample(
                "btc_idr", closed_1m_epoch=MINUTE, now_epoch=MINUTE+65,
            )
            self.assertEqual(report["status"], "PUBLIC_SAMPLE_OBSERVED")
            self.assertEqual(report["trade_rows_visible_in_minute"], 2)
            self.assertEqual(report["buy_trade_rows_visible"], 1)
            self.assertEqual(report["sell_trade_rows_visible"], 1)
            self.assertEqual(report["buy_idr_volume_visible"], "1000")
            self.assertEqual(report["sell_idr_volume_visible"], "2500")
            self.assertEqual(report["total_idr_volume_visible"], "3500")
            self.assertEqual(report["sampled_publications_in_minute"], 2)
            self.assertEqual(report["offset_minimum_in_minute"], 101)
            self.assertEqual(report["offset_maximum_in_minute"], 102)
            self.assertEqual(report["stream_status"], "DISCONNECTED")
            self.assertFalse(report["minute_trade_coverage_complete"])
            self.assertFalse(report["independent_indodax_origin_verified"])
            self.assertFalse(report["g1_receipt_issuance_allowed"])
            self.assertFalse(report["g2_provider_call_allowed"])
            self.assertFalse(report["execution_allowed"])

    def test_unclosed_minute_rejected_without_trade_inference(self):
        with TemporaryDirectory() as td:
            j = prepared_journal(Path(td) / "ws.sqlite3")
            report = j.closed_minute_sample(
                "btc_idr", closed_1m_epoch=MINUTE, now_epoch=MINUTE+30,
            )
            self.assertEqual(report["reason"], "REJECT_MINUTE_NOT_CLOSED")
            self.assertEqual(report["status"], "REJECT")
            self.assertFalse(report["g1_receipt_issuance_allowed"])

    def test_other_minute_empty_sample_cannot_be_called_full_coverage(self):
        with TemporaryDirectory() as td:
            j = prepared_journal(Path(td) / "ws.sqlite3")
            report = j.closed_minute_sample(
                "btc_idr", closed_1m_epoch=MINUTE-120, now_epoch=MINUTE+100,
            )
            self.assertEqual(report["status"], "PUBLIC_SAMPLE_EMPTY")
            self.assertEqual(report["trade_rows_visible_in_minute"], 0)
            self.assertEqual(report["sampled_publications_in_minute"], 0)
            self.assertEqual(report["reason"], "FULL_MINUTE_TRADE_TAPE_NOT_PROVEN")

    def test_modified_stored_publication_digest_detected_before_aggregation(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "ws.sqlite3"
            j = prepared_journal(path)
            with sqlite3.connect(path) as con:
                con.execute(
                    "UPDATE ws_trade_publications SET rows_json='[]' "
                    "WHERE pair=? AND offset=?", ("btc_idr", 101),
                )
            report = j.closed_minute_sample(
                "btc_idr", closed_1m_epoch=MINUTE, now_epoch=MINUTE+65,
            )
            self.assertEqual(report["reason"], "REJECT_WS_JOURNAL_DIGEST_MISMATCH")
            self.assertFalse(report["g1_receipt_issuance_allowed"])

    def test_same_trade_identity_cross_publication_rejected(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "ws.sqlite3"
            j = PublicTradeStreamJournal(path)
            j.acknowledge("btc_idr", ack(100))
            j.ingest("btc_idr", sample_frame(101, MINUTE + 4, seq=999))
            j.ingest("btc_idr", sample_frame(102, MINUTE + 4, seq=999))
            report = j.closed_minute_sample(
                "btc_idr", closed_1m_epoch=MINUTE, now_epoch=MINUTE+65,
            )
            self.assertEqual(report["reason"], "REJECT_WS_DUPLICATE_TRADE_IDENTITY")
            self.assertFalse(report["execution_allowed"])

    def test_bounded_snapshot_does_not_emit_partial_statistics(self):
        with TemporaryDirectory() as td:
            j = prepared_journal(Path(td)/"ws.sqlite3")
            report = j.closed_minute_sample(
                "btc_idr", closed_1m_epoch=MINUTE, now_epoch=MINUTE+65,
                max_publications=1,
            )
            self.assertEqual(report["reason"], "REJECT_WS_SNAPSHOT_LIMIT_EXCEEDED")
            self.assertNotIn("total_idr_volume_visible", report)
            self.assertFalse(report["g2_provider_call_allowed"])

    def test_bulk_mainpath_opt_in_ws_join_preserves_final_reject_and_g1_untrusted(self):
        with TemporaryDirectory() as td:
            ws_db = Path(td) / "ws.sqlite3"
            prepared_journal(ws_db)
            output = StringIO()
            with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner:
                scanner.return_value.scan.return_value = fake_screen()
                with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                           return_value=FakeBulkMarket()):
                    with patch("indodax_ai_bot.orchestrator.time.time",
                               return_value=MINUTE + 70):
                        with redirect_stdout(output):
                            rc = main([
                                "--bulk-deep-once",
                                "--ws-db", str(ws_db),
                                "--db", str(Path(td) / "decision.sqlite3"),
                            ])
            result = json.loads(output.getvalue())
            self.assertEqual(rc, 0)
            joined = result["public_bulk"]["btc_ws_closed_minute_sample"]
            self.assertEqual(joined["closed_1m_epoch"], MINUTE)
            self.assertEqual(joined["trade_rows_visible_in_minute"], 2)
            self.assertFalse(joined["minute_trade_coverage_complete"])
            self.assertFalse(result["public_bulk"]["g1_verified"])
            self.assertFalse(result["public_bulk"]["g1_source_admission"][
                "g1_receipt_issuance_allowed"
            ])
            self.assertEqual(result["ai_shadow"]["completed_roles"], [])
            self.assertEqual(result["final_decision"], "REJECT")
            self.assertFalse(result["execution_allowed"])
            self.assertFalse(result["live_trading_authorized"])

    def test_bulk_mainpath_missing_ws_file_is_safe_observation_rejection(self):
        with TemporaryDirectory() as td:
            missing = Path(td) / "not-created.sqlite3"
            output = StringIO()
            with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner:
                scanner.return_value.scan.return_value = fake_screen()
                with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                           return_value=FakeBulkMarket()):
                    with redirect_stdout(output):
                        rc = main([
                            "--bulk-deep-once",
                            "--ws-db", str(missing),
                            "--db", str(Path(td) / "decision.sqlite3"),
                        ])
            result = json.loads(output.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(result["public_bulk"]["btc_ws_closed_minute_sample"][
                "reason"
            ], "PUBLIC_WS_SQLITE_NOT_FOUND")
            self.assertFalse(missing.exists())
            self.assertEqual(result["final_decision"], "REJECT")


if __name__ == "__main__":
    unittest.main()
