"""M04-M focused regression: historical health while the NEXT WS batch runs.

Only newly affected health-state behavior is tested. No exchange calls, paid AI,
orders, systemd edits, or reruns of the accepted ten M04-K test cases.
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
from indodax_ai_bot.public_ws_health import public_ws_health
from indodax_ai_bot.trade_stream import PublicTradeStreamJournal
from test_m04e_trade_stream import ack, pub
from test_m04k_ws_health import CLOCK, make_batch


class M04MActiveNextBatchHealthTests(unittest.TestCase):
    def test_subscribed_next_batch_is_not_falsely_diagnosed_as_rollback(self):
        with TemporaryDirectory() as td:
            db, checkpoint, completed = make_batch(Path(td))
            self.assertEqual(completed["stream_status"], "DISCONNECTED")
            journal = PublicTradeStreamJournal(db)
            journal.acknowledge("btc_idr", ack(101))
            result = public_ws_health(db, checkpoint, now_epoch=CLOCK + 15)
            self.assertEqual(result["status"], "RECENT_COMPLETED_PUBLIC_BATCH_UNATTESTED")
            self.assertTrue(result["last_batch_recent_and_consistent"])
            self.assertEqual(result["current_journal_stream_status"], "BASELINE_ONLY")
            self.assertEqual(result["completed_batch_stream_status"], "DISCONNECTED")
            self.assertFalse(result["execution_allowed"])
            self.assertFalse(result["g2_provider_call_allowed"])
            self.assertIsNone(result["live_stream_currently_connected"])
            self.assertIsNone(result["systemd_service_active"])

    def test_next_batch_new_trade_still_preserves_last_completed_batch(self):
        with TemporaryDirectory() as td:
            db, checkpoint, _ = make_batch(Path(td))
            journal = PublicTradeStreamJournal(db)
            journal.acknowledge("btc_idr", ack(101))
            journal.ingest("btc_idr", pub(102))
            result = public_ws_health(db, checkpoint, now_epoch=CLOCK + 20)
            self.assertEqual(result["status"], "RECENT_COMPLETED_PUBLIC_BATCH_UNATTESTED")
            self.assertEqual(result["current_journal_stream_status"], "CONTIGUOUS_OBSERVED")
            self.assertEqual(result["last_publication_offset"], 102)
            self.assertEqual(result["persisted_publications"], 2)
            self.assertTrue(result["last_batch_recent_and_consistent"])
            self.assertFalse(result["full_closed_minute_coverage_proven"])
            self.assertFalse(result["g1_receipt_issuance_allowed"])

    def test_gap_or_recovering_remains_rejected_even_after_valid_batch(self):
        with TemporaryDirectory() as td:
            db, checkpoint, _ = make_batch(Path(td))
            for state in ("GAP", "RECOVERING", "CORRUPT"):
                with self.subTest(state=state):
                    with sqlite3.connect(db) as connection:
                        connection.execute(
                            "UPDATE ws_stream_state SET status=? WHERE pair='btc_idr'",
                            (state,),
                        )
                    result = public_ws_health(db, checkpoint, now_epoch=CLOCK + 12)
                    self.assertEqual(result["status"], "REJECT")
                    self.assertEqual(result["reason"],
                                     "WS_HEALTH_JOURNAL_MISMATCH_OR_ROLLBACK")
                    self.assertFalse(result["last_batch_recent_and_consistent"])

    def test_count_regression_is_not_masked_by_accepted_live_state(self):
        with TemporaryDirectory() as td:
            db, checkpoint, _ = make_batch(Path(td))
            with sqlite3.connect(db) as connection:
                connection.execute(
                    "UPDATE ws_stream_state SET status='CONTIGUOUS_OBSERVED' "
                    "WHERE pair='btc_idr'"
                )
                connection.execute(
                    "DELETE FROM ws_trade_publications WHERE pair='btc_idr'"
                )
            result = public_ws_health(db, checkpoint, now_epoch=CLOCK + 13)
            self.assertEqual(result["status"], "REJECT")
            self.assertEqual(result["reason"],
                             "WS_HEALTH_JOURNAL_MISMATCH_OR_ROLLBACK")
            self.assertFalse(result["execution_allowed"])

    def test_cli_live_next_batch_health_read_only_no_order(self):
        with TemporaryDirectory() as td:
            db, checkpoint, _ = make_batch(Path(td))
            journal = PublicTradeStreamJournal(db)
            journal.acknowledge("btc_idr", ack(101))
            out = StringIO()
            with patch("indodax_ai_bot.public_ws_health.time.time",
                       return_value=CLOCK + 30):
                with redirect_stdout(out):
                    code = main([
                        "--public-ws-health",
                        "--db", str(db),
                        "--supervisor-status-file", str(checkpoint),
                        "--health-max-age-seconds", "3600",
                    ])
            report = json.loads(out.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(report["final_decision"], "REJECT")
            self.assertFalse(report["live_trading_authorized"])
            self.assertFalse(report["execution_allowed"])
            self.assertEqual(
                report["public_ws_health"]["current_journal_stream_status"],
                "BASELINE_ONLY",
            )


if __name__ == "__main__":
    unittest.main()
