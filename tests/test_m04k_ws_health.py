"""M04-K PUBLIC last-batch health: status+SQLite checks, NO authority or writes."""
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
from indodax_ai_bot.public_ws_supervisor import supervised_public_ws_batch
from test_m04e_trade_stream import ack, pub

CLOCK = 1793063147


def make_batch(directory: Path):
    db = directory / "ws.sqlite3"
    status = directory / "state.json"
    def collector(journal, *, pair, sessions, session_seconds, pause_seconds):
        journal.acknowledge(pair, ack(100))
        journal.ingest(pair, pub(101))
        journal.mark_disconnected(pair)
        return {
            "status": "PUBLIC_SAMPLE_COLLECTED_UNATTESTED",
            "requested_sessions": sessions,
            "completed_safe_sessions": sessions,
            "attempted_sessions": sessions,
            "g1_receipt_issuance_allowed": False,
            "g2_provider_call_allowed": False,
            "execution_allowed": False,
        }
    result = supervised_public_ws_batch(
        db, sessions=2, seconds=3, pause_seconds=0,
        status_file=status, collector=collector, now=lambda: CLOCK,
    )
    return db, status, result


class M04KPublicWSHealthTests(unittest.TestCase):
    def test_recent_valid_checkpoint_and_journal_are_last_batch_only(self):
        with TemporaryDirectory() as td:
            db, status, previous = make_batch(Path(td))
            result = public_ws_health(db, status, now_epoch=CLOCK+10)
            self.assertEqual(result["status"], "RECENT_COMPLETED_PUBLIC_BATCH_UNATTESTED")
            self.assertTrue(result["last_batch_recent_and_consistent"])
            self.assertEqual(result["batch_age_seconds"], 10)
            self.assertEqual(result["persisted_publications"], 1)
            self.assertEqual(result["stream_epoch"], "1633996541")
            self.assertEqual(result["last_publication_offset"], 101)
            self.assertIsNone(result["systemd_service_active"])
            self.assertIsNone(result["live_stream_currently_connected"])
            self.assertFalse(result["g1_receipt_issuance_allowed"])
            self.assertFalse(result["g2_provider_call_allowed"])
            self.assertFalse(result["execution_allowed"])
            self.assertFalse(result["indodax_trade_tape_completeness_proven"])
            self.assertFalse(result["full_closed_minute_coverage_proven"])

    def test_stale_checkpoint_rejected_even_if_stored_offset_exists(self):
        with TemporaryDirectory() as td:
            db, status, _ = make_batch(Path(td))
            result = public_ws_health(db, status, now_epoch=CLOCK+1201,
                                      max_age_seconds=1200)
            self.assertEqual(result["reason"], "WS_HEALTH_LAST_BATCH_STALE")
            self.assertFalse(result["last_batch_recent_and_consistent"])

    def test_future_dated_checkpoint_is_rejected(self):
        with TemporaryDirectory() as td:
            db, status, _ = make_batch(Path(td))
            result = public_ws_health(db, status, now_epoch=CLOCK-1)
            self.assertEqual(result["reason"], "WS_HEALTH_FUTURE_BATCH_TIME")

    def test_mutated_owner_status_cannot_claim_trusted_g1(self):
        with TemporaryDirectory() as td:
            db, status, _ = make_batch(Path(td))
            item = json.loads(status.read_text())
            item["g1_receipt_issuance_allowed"] = True
            status.write_text(json.dumps(item))
            result = public_ws_health(db, status, now_epoch=CLOCK+1)
            self.assertEqual(result["reason"], "WS_HEALTH_UNTRUSTED_BATCH_STATUS")
            self.assertFalse(result["execution_allowed"])

    def test_rollback_or_epoch_change_never_claims_healthy(self):
        with TemporaryDirectory() as td:
            db, status, _ = make_batch(Path(td))
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE ws_stream_state SET last_offset=100 WHERE pair='btc_idr'")
            out = public_ws_health(db, status, now_epoch=CLOCK+1)
            self.assertEqual(out["reason"], "WS_HEALTH_JOURNAL_MISMATCH_OR_ROLLBACK")
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE ws_stream_state SET last_offset=101,epoch='changed' WHERE pair='btc_idr'")
            out = public_ws_health(db, status, now_epoch=CLOCK+1)
            self.assertEqual(out["reason"], "WS_HEALTH_JOURNAL_MISMATCH_OR_ROLLBACK")

    def test_missing_files_create_nothing(self):
        with TemporaryDirectory() as td:
            db = Path(td)/"missing.sqlite3"
            status = Path(td)/"missing.status"
            out=public_ws_health(db,status,now_epoch=CLOCK+1)
            self.assertEqual(out["reason"], "WS_HEALTH_FILES_MISSING")
            self.assertFalse(db.exists())
            self.assertFalse(status.exists())

    def test_oversized_or_invalid_json_status_fails_closed(self):
        with TemporaryDirectory() as td:
            db,status,_ = make_batch(Path(td))
            status.write_text("{" + " " * 33000 + "}")
            result=public_ws_health(db,status,now_epoch=CLOCK+1)
            self.assertEqual(result["reason"], "WS_HEALTH_STATUS_OVERSIZED")
            status.write_text("{bad")
            result=public_ws_health(db,status,now_epoch=CLOCK+1)
            self.assertEqual(result["reason"], "WS_HEALTH_STATUS_INVALID")

    def test_invalid_age_bounds_cannot_claim_healthy(self):
        with TemporaryDirectory() as td:
            db,status,_ = make_batch(Path(td))
            for bound in (0, 1, 86401, False):
                with self.subTest(bound=bound):
                    out=public_ws_health(db,status,now_epoch=CLOCK+1,
                                         max_age_seconds=bound)
                    self.assertEqual(out["reason"], "INVALID_PUBLIC_WS_HEALTH_BOUNDS")

    def test_cli_operator_health_does_not_create_decision_journal_or_trade(self):
        with TemporaryDirectory() as td:
            db,status,_=make_batch(Path(td))
            before_db=db.read_bytes()
            out=StringIO()
            with patch("indodax_ai_bot.public_ws_health.time.time",
                       return_value=CLOCK+10):
                with redirect_stdout(out):
                    rc=main([
                        "--public-ws-health",
                        "--db",str(db),
                        "--supervisor-status-file",str(status),
                        "--health-max-age-seconds","3600",
                    ])
            parsed=json.loads(out.getvalue())
            self.assertEqual(rc,0)
            self.assertEqual(parsed["final_decision"],"REJECT")
            self.assertFalse(parsed["execution_allowed"])
            self.assertFalse(parsed["live_trading_authorized"])
            self.assertTrue(parsed["public_ws_health"]["last_batch_recent_and_consistent"])
            self.assertEqual(db.read_bytes(),before_db)
            with sqlite3.connect(db) as con:
                tables={row[0] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}
            self.assertNotIn("decisions", tables)

    def test_cli_stale_status_returns_nonzero_without_order(self):
        with TemporaryDirectory() as td:
            db,status,_=make_batch(Path(td))
            out=StringIO()
            with patch("indodax_ai_bot.public_ws_health.time.time",
                       return_value=CLOCK+5000):
                with redirect_stdout(out):
                    rc=main([
                        "--public-ws-health","--db",str(db),
                        "--supervisor-status-file",str(status),
                    ])
            parsed=json.loads(out.getvalue())
            self.assertEqual(rc,3)
            self.assertEqual(parsed["final_decision"],"REJECT")
            self.assertEqual(parsed["public_ws_health"]["reason"],
                             "WS_HEALTH_LAST_BATCH_STALE")


if __name__ == "__main__":
    unittest.main()
