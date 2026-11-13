"""M04 G1 15m/1h real-source feasibility: bounded, untrusted, no trading."""
from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from indodax_ai_bot import m04_g1_15m_1h_source_inventory_nonlive as m
from scripts import m04_g1_15m_1h_source_inventory_readonly as cli

END = int(datetime(2026, 11, 12, 16, 6,
                   tzinfo=ZoneInfo("Asia/Jakarta")).timestamp())
PAIR = "tmg_idr"


def micro_schema(db):
    db.executescript("""
        CREATE TABLE ws3_micro_research_evidence (
            pair TEXT, minute_epoch INTEGER,
            recorded_local_epoch INTEGER, trade_frames INTEGER,
            last_trade_epoch INTEGER, trade_offset INTEGER,
            trade_gap_observed INTEGER, trade_truncated INTEGER,
            source_malformed INTEGER, book_local_epoch INTEGER,
            book_offset INTEGER, source_complete INTEGER,
            aggressor_verified INTEGER, g1_verified INTEGER,
            PRIMARY KEY(pair,minute_epoch)
        );
    """)


def add_micro(db, *, minute=None, frames=1, book=True,
              flags=(0, 0, 0), authority=(0, 0, 0)):
    minute = END - 60 if minute is None else minute
    captured = minute + 18
    db.execute("""
        INSERT INTO ws3_micro_research_evidence VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        PAIR, minute, captured, frames,
        captured - 3 if frames else None,
        123 if frames else None,
        *flags, captured - 1 if book else None,
        125 if book else None, *authority,
    ))


def journal_schema(db):
    db.executescript("""
        CREATE TABLE ws_trade_publications (
            pair TEXT, epoch TEXT, offset INTEGER,
            payload_sha256 TEXT, rows_json TEXT,
            PRIMARY KEY(pair,epoch,offset)
        );
        CREATE TABLE ws_stream_state (
            pair TEXT PRIMARY KEY, epoch TEXT,
            last_offset INTEGER, status TEXT
        );
        CREATE TABLE ws_stream_discontinuities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair TEXT, prior_epoch TEXT, prior_offset INTEGER,
            new_epoch TEXT, new_offset INTEGER, reason TEXT,
            observed_at_epoch INTEGER
        );
    """)
    db.execute(
        "INSERT INTO ws_stream_state VALUES (?,?,?,?)",
        (PAIR, "123456", 103, "CONTIGUOUS_OBSERVED"),
    )


def add_journal(db, *, offset=101, when=None, epoch="123456",
                digest_override=None, raw_override=None):
    when = END - 90 if when is None else when
    raw = json.dumps([[
        "tmgidr", when, offset, "buy", "100", "2000", "20",
    ]], separators=(",", ":"))
    encoded = raw if raw_override is None else raw_override
    digest = (sha256(raw.encode("utf-8")).hexdigest()
              if digest_override is None else digest_override)
    db.execute(
        "INSERT INTO ws_trade_publications VALUES (?,?,?,?,?)",
        (PAIR, epoch, offset, digest, encoded),
    )


class G1InventoryPureTests(unittest.TestCase):
    def setUp(self):
        self.micro = sqlite3.connect(":memory:")
        self.journal = sqlite3.connect(":memory:")
        self.addCleanup(self.micro.close)
        self.addCleanup(self.journal.close)
        micro_schema(self.micro)
        journal_schema(self.journal)

    def check(self, *, journal=None, pairs=None):
        return m.inventory_g1_windows_nonlive(
            micro_db=self.micro,
            journal_db=self.journal if journal is None else journal,
            end_epoch=END,
            pairs=[PAIR] if pairs is None else pairs,
        )

    def test_empty_samples_are_unknown_not_zero_market_trades(self):
        report = self.check()
        self.assertFalse(report["g1_independent_issuer_verified"])
        self.assertFalse(report["g1_coverage_manifest_issuance_allowed"])
        self.assertFalse(report["execution_allowed"])
        self.assertEqual(report["orders_sent"], 0)
        self.assertEqual(report["provider_http_calls"], 0)
        self.assertEqual(report["additional_public_ws_connections"], 0)
        row = report["results"][0]
        self.assertEqual(row["rotating_micro_archive"]["windows"]["15m"]
                         ["missing_minutes_unknown"], 15)
        self.assertEqual(row["rotating_micro_archive"]["windows"]["1h"]
                         ["missing_minutes_unknown"], 60)
        self.assertEqual(row["separate_existing_trade_journal"]["status"],
                         "JOURNAL_PAIR_UNOBSERVED")
        self.assertFalse(row["full_15m_tape_verified"])

    def test_two_micro_rows_do_not_prove_15m_or_1h(self):
        add_micro(self.micro, minute=END-60)
        add_micro(self.micro, minute=END-120, frames=0)
        result = self.check()["results"][0]["rotating_micro_archive"]
        self.assertEqual(result["status"], "BOUNDED_ROTATING_MICRO_SAMPLE_ONLY")
        q15 = result["windows"]["15m"]
        q60 = result["windows"]["1h"]
        self.assertEqual(q15["micro_observed_minutes"], 2)
        self.assertEqual(q15["missing_minutes_unknown"], 13)
        self.assertEqual(q15["trade_activity_observed_minutes"], 1)
        self.assertEqual(q15["book_observed_minutes"], 2)
        self.assertEqual(q15["maximum_consecutive_observed_minutes"], 2)
        self.assertEqual(q60["missing_minutes_unknown"], 58)
        self.assertFalse(q60["trade_publication_cursor_continuity_verified"])
        self.assertFalse(q60["zero_trade_minutes_proven"])

    def test_one_hour_of_minute_rows_still_not_full_issuer_tape(self):
        for i in range(60):
            add_micro(self.micro, minute=END - (i+1)*60)
        windows = self.check()["results"][0]["rotating_micro_archive"]["windows"]
        self.assertEqual(windows["15m"]["micro_observed_minutes"], 15)
        self.assertEqual(windows["1h"]["micro_observed_minutes"], 60)
        self.assertEqual(windows["1h"]["maximum_consecutive_observed_minutes"],
                         60)
        self.assertFalse(windows["15m"]["complete_tape_verified"])
        self.assertFalse(windows["1h"]["complete_tape_verified"])

    def test_gaps_truncation_malformed_and_fake_g1_never_promote(self):
        add_micro(self.micro, flags=(1, 1, 1),
                  authority=(1, 1, 1))
        q = self.check()["results"][0]["rotating_micro_archive"]["windows"]["15m"]
        self.assertEqual(q["flagged_or_invalid_minutes"], 1)
        self.assertEqual(q["observed_gap_flags"], 1)
        self.assertEqual(q["observed_truncation_flags"], 1)
        self.assertEqual(q["observed_malformed_flags"], 1)
        self.assertEqual(q["trade_activity_observed_minutes"], 0)
        self.assertFalse(q["complete_tape_verified"])

    def test_straddling_end_excluded_and_start_included(self):
        add_micro(self.micro, minute=END-900)
        add_micro(self.micro, minute=END)
        q = self.check()["results"][0]["rotating_micro_archive"]["windows"]["15m"]
        self.assertEqual(q["micro_observed_minutes"], 1)
        self.assertEqual(q["missing_minutes_unknown"], 14)

    def test_partial_streak_and_corrupted_timestamp_are_flagged(self):
        add_micro(self.micro, minute=END-60)
        add_micro(self.micro, minute=END-180)
        self.micro.execute(
            "UPDATE ws3_micro_research_evidence "
            "SET last_trade_epoch=recorded_local_epoch+1 "
            "WHERE minute_epoch=?", (END-180,),
        )
        q = self.check()["results"][0]["rotating_micro_archive"]["windows"]["15m"]
        self.assertEqual(q["maximum_consecutive_observed_minutes"], 1)
        self.assertEqual(q["flagged_or_invalid_minutes"], 1)
        self.assertEqual(q["trade_activity_observed_minutes"], 1)

    def test_missing_table_is_unavailable_not_crash(self):
        self.micro.execute("DROP TABLE ws3_micro_research_evidence")
        q = self.check()["results"][0]["rotating_micro_archive"]
        self.assertEqual(q["status"], "MICRO_TABLE_ABSENT")
        self.assertEqual(q["windows"]["1h"]["missing_minutes_unknown"], 60)

    def test_missing_required_column_rejected(self):
        self.micro.execute("DROP TABLE ws3_micro_research_evidence")
        self.micro.execute("CREATE TABLE ws3_micro_research_evidence(pair TEXT)")
        with self.assertRaisesRegex(ValueError, "SCHEMA_MISMATCH"):
            self.check()

    def test_bad_scope_pair_duplicate_bool_end_and_over_cap(self):
        for end, pairs in (
            (True, [PAIR]), (END+1, [PAIR]), (END, []),
            (END, [PAIR, PAIR]), (END, ["../evil"]),
            (END, ["éth_idr"]), (END, [f"x{i}_idr" for i in range(9)]),
        ):
            with self.subTest(end=end, pairs=pairs):
                with self.assertRaises(ValueError):
                    m.inventory_g1_windows_nonlive(
                        micro_db=self.micro, journal_db=None,
                        end_epoch=end, pairs=pairs,
                    )

    def test_two_journal_publications_offset_contiguous_but_unattested(self):
        for offset in (101, 102):
            add_journal(self.journal, offset=offset, when=END-60)
        q = self.check()["results"][0]["separate_existing_trade_journal"]
        self.assertEqual(q["status"],
                         "JOURNAL_LOCAL_INTEGRITY_CHECKED_SAMPLE_ONLY")
        self.assertEqual(q["total_pair_publications_inspected"], 2)
        self.assertEqual(q["windows"]["15m"]["observed_trade_rows"], 2)
        self.assertEqual(q["windows"]["1h"]["observed_trade_rows"], 2)
        self.assertEqual(q["windows"]["15m"][
            "observed_offset_jumps_in_pair_journal"], 0)
        self.assertFalse(q["offset_recovery_reconciled_independently"])
        self.assertFalse(q["windows"]["1h"]["complete_tape_verified"])

    def test_journal_offset_jump_and_rebaseline_marker_reported(self):
        add_journal(self.journal, offset=101)
        add_journal(self.journal, offset=103, when=END-70)
        self.journal.execute("""
            INSERT INTO ws_stream_discontinuities
            (pair,prior_epoch,prior_offset,new_epoch,new_offset,
             reason,observed_at_epoch) VALUES (?,?,?,?,?,?,?)
        """, (PAIR, "123456", 101, "123456", 103,
              "UNRECOVERABLE_WS_GAP_REBASELINED", END-80))
        q = self.check()["results"][0]["separate_existing_trade_journal"]
        self.assertEqual(q["windows"]["15m"][
            "observed_offset_jumps_in_pair_journal"], 1)
        self.assertEqual(q["windows"]["15m"][
            "explicit_gap_rebaseline_markers_in_window"], 1)
        self.assertEqual(q["all_pair_recorded_gap_markers"], 1)
        self.assertFalse(q["upstream_origin_authenticated"])

    def test_multiple_epochs_never_claim_continuous_tape(self):
        add_journal(self.journal, offset=101)
        add_journal(self.journal, offset=9, epoch="123457", when=END-100)
        q = self.check()["results"][0]["separate_existing_trade_journal"]
        self.assertEqual(q["windows"]["15m"]["source_epoch_count"], 2)
        self.assertTrue(q["has_multiple_publication_epochs"])
        self.assertFalse(q["windows"]["15m"]["complete_tape_verified"])

    def test_digest_corrupt_invalid_json_and_oversize_rejected(self):
        add_journal(self.journal, offset=101)
        self.journal.execute(
            "UPDATE ws_trade_publications SET rows_json='tampered'")
        q = self.check()["results"][0]["separate_existing_trade_journal"]
        self.assertEqual(q["status"], "JOURNAL_DIGEST_OR_ROW_INVALID")
        self.assertFalse(q["windows"]["15m"]["complete_tape_verified"])

    def test_duplicate_trade_identity_in_two_publications_rejected(self):
        add_journal(self.journal, offset=101, when=END-90)
        raw = json.dumps([[
            "tmgidr", END-90, 101, "buy", "100", "2000", "20",
        ]], separators=(",", ":"))
        self.journal.execute(
            "INSERT INTO ws_trade_publications VALUES (?,?,?,?,?)",
            (PAIR, "123456", 102, sha256(raw.encode()).hexdigest(), raw),
        )
        q = self.check()["results"][0]["separate_existing_trade_journal"]
        self.assertEqual(q["status"], "JOURNAL_DUPLICATE_TRADE_IDENTITY")

    def test_journal_with_missing_tables_remains_unavailable(self):
        self.journal.execute("DROP TABLE ws_stream_discontinuities")
        q = self.check()["results"][0]["separate_existing_trade_journal"]
        self.assertEqual(q["status"], "JOURNAL_REQUIRED_TABLES_ABSENT")

    def test_optional_journal_not_supplied_is_explicit_unknown(self):
        q = m.inventory_g1_windows_nonlive(
            micro_db=self.micro, journal_db=None,
            end_epoch=END, pairs=[PAIR],
        )["results"][0]["separate_existing_trade_journal"]
        self.assertEqual(q["status"], "INDEPENDENT_JOURNAL_NOT_SUPPLIED")

    def test_publications_outside_both_windows_not_miscounted(self):
        add_journal(self.journal, offset=101, when=END-3601)
        add_journal(self.journal, offset=102, when=END)
        q = self.check()["results"][0]["separate_existing_trade_journal"]
        self.assertEqual(q["windows"]["1h"]["observed_trade_rows"], 0)
        self.assertEqual(q["windows"]["15m"][
            "observed_publications_with_window_trades"], 0)


class G1InventoryOwnerCliTests(unittest.TestCase):
    def test_missing_private_archive_denied_no_created_paths(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "missing.sqlite3"
            out = cli.inspect_original_g1_window_source_inventory_readonly(
                pairs=[PAIR], end_epoch=END,
                research_path=p, now_epoch=END+10,
            )
            self.assertEqual(
                out["status"], "M04_G1_15M_1H_SOURCE_INVENTORY_BLOCKED_READONLY")
            self.assertFalse(p.exists())
            self.assertEqual(out["orders_sent"], 0)
            self.assertFalse(out["execution_allowed"])

    def test_readonly_private_archive_with_no_optional_journal(self):
        with TemporaryDirectory() as td:
            parent = Path(td) / "private"
            parent.mkdir(mode=0o700)
            os.chmod(parent, 0o700)
            p = parent / "observations.sqlite3"
            db = sqlite3.connect(p)
            micro_schema(db)
            add_micro(db)
            db.commit()
            db.close()
            p.chmod(0o600)
            before = p.stat().st_size
            out = cli.inspect_original_g1_window_source_inventory_readonly(
                pairs=[PAIR], end_epoch=END,
                research_path=p, now_epoch=END+10,
            )
            self.assertEqual(
                out["status"], "M04_G1_15M_1H_INDEPENDENT_SOURCE_FEASIBILITY_READONLY")
            self.assertEqual(out["results"][0]["rotating_micro_archive"]["windows"]
                             ["15m"]["micro_observed_minutes"], 1)
            self.assertEqual(p.stat().st_size, before)
            self.assertEqual(out["provider_http_calls"], 0)
            self.assertEqual(out["service_restarts"], 0)

    def test_future_and_outside_retention_rejected(self):
        with TemporaryDirectory() as td:
            for now in (END-1, END+72*3600+1):
                result = cli.inspect_original_g1_window_source_inventory_readonly(
                    pairs=[PAIR], end_epoch=END,
                    research_path=Path(td)/"no-file",
                    now_epoch=now,
                )
                self.assertEqual(result["reason"],
                                 "WINDOW_FUTURE_OR_OUTSIDE_RETENTION")

    def test_invalid_end_wib_returns_no_io_and_sanitized_output(self):
        with patch.object(cli, "inspect_original_g1_window_source_inventory_readonly") as scan:
            with redirect_stdout(StringIO()) as stream:
                rc = cli.main(["--end-wib", "invalid"])
        self.assertEqual(rc, 2)
        self.assertIn("WINDOW_WIB_FORMAT_INVALID", stream.getvalue())
        self.assertNotIn("token", stream.getvalue())
        scan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
