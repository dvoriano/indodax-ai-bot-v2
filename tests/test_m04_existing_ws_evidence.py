"""Read-only evidence inventory contract; no network/credentials/trading."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.m04_existing_ws_evidence import inspect_existing_btc_ws


class ExistingWsExternalEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "public_ws.sqlite3"
        with sqlite3.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE ws_stream_state(
                    pair TEXT PRIMARY KEY,epoch TEXT,
                    last_offset INTEGER,status TEXT
                );
                CREATE TABLE ws_trade_publications(
                    pair TEXT,epoch TEXT,offset INTEGER,
                    payload_sha256 TEXT,rows_json TEXT,
                    PRIMARY KEY(pair,epoch,offset)
                );
                CREATE TABLE ws_stream_discontinuities(
                    id INTEGER PRIMARY KEY,pair TEXT,prior_epoch TEXT,
                    prior_offset INTEGER,new_epoch TEXT,new_offset INTEGER,
                    reason TEXT,observed_at_epoch INTEGER
                );
            """)
            db.execute(
                "INSERT INTO ws_stream_state VALUES('btc_idr','77',12,'CONTIGUOUS_OBSERVED')"
            )
            for off, ts, seq in ((11, 1800000020, 400), (12, 1800000070, 401)):
                rows = json.dumps([
                    ["btcidr", ts, seq, "buy", "100000", "10000", "0.1"]
                ], separators=(",", ":"))
                db.execute(
                    "INSERT INTO ws_trade_publications VALUES(?,?,?,?,?)",
                    ("btc_idr", "77", off, sha256(rows.encode()).hexdigest(), rows)
                )

    def test_original_publication_inventory_has_no_g1_promotion(self):
        out = inspect_existing_btc_ws(str(self.path))
        self.assertEqual(out["status"], "LOCAL_PUBLIC_WS_EVIDENCE_INVENTORY_ONLY")
        self.assertEqual(out["total_publications"], 2)
        self.assertEqual(out["observed_trade_rows"], 2)
        self.assertEqual(out["offset_jumps_between_stored_publications"], 0)
        self.assertEqual(out["trade_sequence_jumps_between_observed_rows"], 0)
        self.assertFalse(out["g1_authenticated"])
        self.assertFalse(out["complete_public_15m_1h_tape_proven"])
        self.assertFalse(out["execution_allowed"])
        self.assertFalse(
            out["windows"]["last_closed_1h"]["complete_exchange_trade_tape_proven"]
        )

    def test_historical_stream_never_counts_as_current(self):
        out = inspect_existing_btc_ws(str(self.path), now_epoch=1800009000)
        self.assertEqual(out["latest_observed_trade_lag_seconds"], 8930)
        self.assertFalse(out["latest_observed_trade_recent_120s"])
        self.assertFalse(out["source_eligible_for_live_decision"])
        self.assertTrue(out["stream_is_currently_connected"])

    def test_disconnected_stream_remains_non_live_even_if_recent(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE ws_stream_state SET status='DISCONNECTED' WHERE pair='btc_idr'")
        out = inspect_existing_btc_ws(str(self.path), now_epoch=1800000071)
        self.assertTrue(out["latest_observed_trade_recent_120s"])
        self.assertFalse(out["stream_is_currently_connected"])
        self.assertFalse(out["source_eligible_for_live_decision"])

    def test_readonly_never_modifies_database(self):
        before = self.path.read_bytes()
        inspect_existing_btc_ws(str(self.path))
        self.assertEqual(self.path.read_bytes(), before)

    def test_checksum_mismatch_not_accepted(self):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE ws_trade_publications SET payload_sha256=? WHERE offset=12",
                ("0" * 64,)
            )
        out = inspect_existing_btc_ws(str(self.path))
        self.assertEqual(out["publication_hash_mismatches"], 1)
        self.assertEqual(out["observed_trade_rows"], 1)
        self.assertFalse(out["g1_authenticated"])

    def test_nonexistent_db_does_not_get_created(self):
        missing = Path(self.tmp.name) / "absent.sqlite3"
        with self.assertRaisesRegex(ValueError, "WS_EVIDENCE_DB_NOT_FOUND_ABSOLUTE"):
            inspect_existing_btc_ws(str(missing))
        self.assertFalse(missing.exists())

    def test_publication_cap_is_explicit(self):
        out = inspect_existing_btc_ws(str(self.path), max_publications=1)
        self.assertEqual(
            out["status"], "PUBLIC_WS_DATASET_EXCEEDS_BOUNDED_INSPECTION"
        )
        self.assertFalse(out["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
