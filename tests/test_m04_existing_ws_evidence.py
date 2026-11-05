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
        self.assertTrue(out["last_persisted_stream_status_contiguous"])

    def test_disconnected_stream_remains_non_live_even_if_recent(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE ws_stream_state SET status='DISCONNECTED' WHERE pair='btc_idr'")
        out = inspect_existing_btc_ws(str(self.path), now_epoch=1800000071)
        self.assertTrue(out["latest_observed_trade_recent_120s"])
        self.assertFalse(out["last_persisted_stream_status_contiguous"])
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

    def test_rolling_15_and_60_share_exact_end_and_locate_recorded_gap(self):
        anchor = 1800000300  # 5 minutes into the next wall-clock hour
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM ws_trade_publications")
            for offset in range(1, 62):
                ts = anchor - 3600 + (offset - 1) * 60 + 12
                rows = json.dumps([
                    ["btcidr", ts, offset, "buy", "100000", "10000", "0.1"]
                ], separators=(",", ":"))
                db.execute(
                    "INSERT INTO ws_trade_publications VALUES(?,?,?,?,?)",
                    ("btc_idr", "77", offset,
                     sha256(rows.encode()).hexdigest(), rows),
                )
            db.execute("UPDATE ws_stream_state SET last_offset=61")
            db.execute(
                "INSERT INTO ws_stream_discontinuities("
                "pair,prior_epoch,prior_offset,new_epoch,new_offset,"
                "reason,observed_at_epoch) VALUES(?,?,?,?,?,?,?)",
                ("btc_idr", "77", 2, "77", 3, "LOCAL_GAP", anchor - 10000),
            )
            db.execute(
                "INSERT INTO ws_stream_discontinuities("
                "pair,prior_epoch,prior_offset,new_epoch,new_offset,"
                "reason,observed_at_epoch) VALUES(?,?,?,?,?,?,?)",
                ("btc_idr", "77", 40, "77", 41, "LOCAL_GAP", anchor - 30),
            )
        out = inspect_existing_btc_ws(str(self.path), now_epoch=anchor + 15)
        w15 = out["windows"]["rolling_last_15_closed_minutes"]
        w60 = out["windows"]["rolling_last_60_closed_minutes"]
        aligned = out["windows"]["last_closed_1h"]
        self.assertEqual(w15["end_epoch"], anchor)
        self.assertEqual(w60["end_epoch"], anchor)
        self.assertEqual(w15["minutes_with_observed_trades"], 15)
        self.assertEqual(w60["minutes_with_observed_trades"], 60)
        self.assertEqual(w15["recorded_gap_events_observed_during_window"], 1)
        self.assertEqual(w60["recorded_gap_events_observed_during_window"], 1)
        self.assertEqual(aligned["end_epoch"], 1800000000)
        self.assertEqual(aligned["recorded_gap_events_observed_during_window"], 0)
        self.assertEqual(out["discontinuities_recorded"], 2)
        self.assertEqual(out["latest_recorded_gap_observed_at_epoch"], anchor-30)
        self.assertFalse(out["same_end_rolling_windows_are_g1_attested"])
        self.assertFalse(w60["complete_exchange_trade_tape_proven"])
        self.assertFalse(w60["independent_issuer_attested"])
        self.assertTrue(w60["no_gap_event_observed_does_not_prove_trade_completeness"])
        self.assertFalse(out["execution_allowed"])

    def test_gap_absence_and_missing_minute_do_not_promote_g1(self):
        anchor = 1800000300
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM ws_trade_publications")
            for offset in range(1, 62):
                if offset == 59:
                    continue
                ts = anchor - 3600 + (offset - 1) * 60 + 12
                rows = json.dumps([
                    ["btcidr", ts, offset, "buy", "100000", "10000", "0.1"]
                ], separators=(",", ":"))
                db.execute(
                    "INSERT INTO ws_trade_publications VALUES(?,?,?,?,?)",
                    ("btc_idr", "77", offset,
                     sha256(rows.encode()).hexdigest(), rows),
                )
            db.execute("UPDATE ws_stream_state SET last_offset=61")
        out = inspect_existing_btc_ws(str(self.path), now_epoch=anchor+15)
        win = out["windows"]["rolling_last_60_closed_minutes"]
        self.assertEqual(win["minutes_with_observed_trades"], 59)
        self.assertEqual(win["minutes_without_observed_trades_or_missing_data"], 1)
        self.assertEqual(win["recorded_gap_events_observed_during_window"], 0)
        self.assertFalse(win["complete_exchange_trade_tape_proven"])
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
