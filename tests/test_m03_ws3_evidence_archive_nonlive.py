"""Offline contract for opt-in M03 WS3 source research archive.

No sockets, REST calls, Telegram sender or order path.
"""
from pathlib import Path
import os
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

from indodax_ai_bot.m03_ws3_evidence_archive_nonlive import (
    archive_ws3_evidence_nonlive, RETENTION_SECONDS,
)
from indodax_ai_bot.m03_ws_summary_live_watch import (
    run_m03_ws3_summary_watch_once,
)

NOW = 1794330000


def source(rows=None):
    return {
        "status": "M03_WS3_PUBLIC_SUMMARY_OBSERVED",
        "frames_observed": 4,
        "pairs": rows if rows is not None else [
            {"pair": "uai_idr", "observed_exchange_epoch": NOW - 15,
             "last_idr": "102", "volume_24h_idr": "250000000",
             "source_public_offset": 12},
            {"pair": "thin_idr", "observed_exchange_epoch": NOW - 25,
             "last_idr": "500", "volume_24h_idr": "40000000",
             "source_public_offset": 13},
            {"pair": "stale_idr", "observed_exchange_epoch": NOW - 110,
             "last_idr": "100", "volume_24h_idr": "200000000",
             "source_public_offset": 14},
        ],
    }


class EvidenceArchiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.directory = self.base / "private"
        self.db = self.directory / "observations.sqlite3"

    def run_archive(self, snapshot=None, now=NOW):
        return archive_ws3_evidence_nonlive(
            source() if snapshot is None else snapshot,
            now_epoch=now, evidence_db=self.db,
        )

    def test_proof_rows_and_filter_reasons_are_real_source_only(self):
        result = self.run_archive()
        self.assertEqual(result["status"], "M03_WS3_RESEARCH_ARCHIVE_RECORDED")
        self.assertEqual(result["source_eligible_count"], 1)
        self.assertFalse(result["source_coverage_complete"])
        self.assertEqual(result["exchange_requests_added"], 0)
        self.assertEqual(result["orders_sent"], 0)
        self.assertEqual(stat.S_IMODE(self.db.lstat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.directory.lstat().st_mode), 0o700)
        with sqlite3.connect(self.db) as db:
            rows = db.execute(
                "SELECT pair,source_status,last_idr "
                "FROM ws3_source_evidence ORDER BY pair"
            ).fetchall()
            cycle = db.execute(
                "SELECT source_pairs_seen,source_eligible_count,"
                "stale_count,low_turnover_count "
                "FROM ws3_source_cycles"
            ).fetchone()
        self.assertEqual(rows, [
            ("stale_idr", "STALE_OR_INVALID_EPOCH", None),
            ("thin_idr", "BELOW_150M_VOLUME_FLOOR", "500"),
            ("uai_idr", "ELIGIBLE_SOURCE_OBSERVATION", "102"),
        ])
        self.assertEqual(cycle, (3, 1, 1, 1))

    def test_same_minute_does_not_rewrite_first_quote(self):
        self.run_archive()
        variant = source()
        variant["pairs"][0]["last_idr"] = "999"
        self.run_archive(variant)
        with sqlite3.connect(self.db) as db:
            price = db.execute(
                "SELECT last_idr FROM ws3_source_evidence "
                "WHERE pair='uai_idr'"
            ).fetchone()[0]
            count = db.execute(
                "SELECT COUNT(*) FROM ws3_source_cycles"
            ).fetchone()[0]
        self.assertEqual(price, "102")
        self.assertEqual(count, 1)

    def test_additive_native_prev24h_archive_immutable_and_no_backfill(self):
        self.run_archive()
        # Simulate prior deployed schema, then migrate on next real cycle.
        with sqlite3.connect(self.db) as db:
            db.execute("ALTER TABLE ws3_source_evidence DROP COLUMN previous_24h_idr")
        snap=source()
        snap["pairs"][0]["previous_24h_idr"]="98"
        self.run_archive(snap)
        with sqlite3.connect(self.db) as db:
            self.assertIsNone(db.execute(
                "SELECT previous_24h_idr FROM ws3_source_evidence "
                "WHERE pair='uai_idr'").fetchone()[0])
        future=NOW+60
        snap["pairs"][0]["observed_exchange_epoch"]=future-15
        self.run_archive(snap,now=future)
        snap["pairs"][0]["previous_24h_idr"]="99"
        self.run_archive(snap,now=future)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT previous_24h_idr FROM ws3_source_evidence "
                "WHERE pair='uai_idr' ORDER BY minute_epoch").fetchall(),
                [(None,),("98",)])

    def test_retention_is_72h_for_new_shadow_only(self):
        self.run_archive()
        future = NOW + RETENTION_SECONDS + 120
        snap = source([{
            "pair":"uai_idr", "observed_exchange_epoch":future - 8,
            "last_idr":"110", "volume_24h_idr":"300000000",
            "source_public_offset":22,
        }])
        self.run_archive(snap, now=future)
        with sqlite3.connect(self.db) as db:
            stored = db.execute(
                "SELECT COUNT(*),MIN(minute_epoch) "
                "FROM ws3_source_evidence"
            ).fetchone()
            cycles = db.execute(
                "SELECT COUNT(*) FROM ws3_source_cycles"
            ).fetchone()[0]
        self.assertEqual(stored[0], 1)
        self.assertEqual(cycles, 1)

    def test_untrusted_symlink_and_world_readable_db_denied(self):
        self.directory.mkdir(mode=0o700)
        other = self.base / "other.sqlite3"
        other.write_text("not db")
        self.db.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "EVIDENCE_DB_UNTRUSTED"):
            self.run_archive()
        self.db.unlink()
        self.db.write_text("")
        self.db.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "EVIDENCE_DB_UNTRUSTED"):
            self.run_archive()

    def test_source_absence_not_reported_as_no_trade(self):
        self.run_archive(source([]))
        with sqlite3.connect(self.db) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM ws3_source_evidence"
            ).fetchone()[0]
            cycle = db.execute(
                "SELECT source_pairs_seen FROM ws3_source_cycles"
            ).fetchone()[0]
        self.assertEqual((count, cycle), (0, 0))

    def test_pair_cap_is_explicit_not_completeness_claim(self):
        rows = [{
            "pair":f"token{i:03d}_idr", "observed_exchange_epoch":NOW - 1,
            "last_idr":"5", "volume_24h_idr":"200000000",
            "source_public_offset":i,
        } for i in range(260)]
        result = self.run_archive(source(rows))
        self.assertEqual(result["source_pairs_saved_cap"], 250)
        self.assertEqual(result["dropped_by_cap"], 10)
        self.assertFalse(result["source_coverage_complete"])

    def test_total_row_cap_limits_storage_without_pretending_full_coverage(self):
        with patch(
            "indodax_ai_bot.m03_ws3_evidence_archive_nonlive."
            "MAX_TOTAL_SOURCE_ROWS", 2
        ):
            report = self.run_archive()
        self.assertEqual(report["max_stored_source_rows"], 2)
        self.assertFalse(report["source_coverage_complete"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM ws3_source_evidence"
            ).fetchone()[0], 2)

    def test_off_by_default_no_shadow_write_in_original_runner(self):
        fake = source()
        with patch(
            "indodax_ai_bot.m03_ws_summary_live_watch."
            "collect_ws3_market_summary_once", return_value=fake
        ), patch(
            "indodax_ai_bot.m03_ws_summary_live_watch."
            "persist_ws3_summary_watch", return_value={"status":"OK"}
        ), patch.dict(os.environ, {"M03_WS3_EVIDENCE_ARCHIVE_GO":"NO"}), patch(
            "indodax_ai_bot.m03_ws3_evidence_archive_nonlive."
            "archive_ws3_evidence_nonlive"
        ) as archive:
            result = run_m03_ws3_summary_watch_once(
                str(self.base / "decisions.sqlite3"))
        archive.assert_not_called()
        self.assertNotIn("research_archive", result)

    def test_optin_same_snapshot_without_new_ws_or_extra_scanner(self):
        fake = source()
        with patch(
            "indodax_ai_bot.m03_ws_summary_live_watch."
            "collect_ws3_market_summary_once", return_value=fake
        ) as collector, patch(
            "indodax_ai_bot.m03_ws_summary_live_watch."
            "persist_ws3_summary_watch", return_value={"status":"OK"}
        ) as original, patch.dict(
            os.environ, {"M03_WS3_EVIDENCE_ARCHIVE_GO":"YES"}
        ), patch(
            "indodax_ai_bot.m03_ws3_evidence_archive_nonlive."
            "archive_ws3_evidence_nonlive",
            return_value={"status":"RESEARCH_OK"}
        ) as archive:
            report = run_m03_ws3_summary_watch_once(
                str(self.base / "decisions.sqlite3"))
        collector.assert_called_once()
        original.assert_called_once()
        archive.assert_called_once()
        self.assertIs(archive.call_args.args[0], fake)
        self.assertEqual(report["research_archive"]["status"], "RESEARCH_OK")

    def test_no_fresh_liquid_cycle_keeps_source_evidence_but_still_rejects(self):
        from indodax_ai_bot.m03_ws_summary_live_watch import M03WSWatchError
        fake=source()
        with patch(
            "indodax_ai_bot.m03_ws_summary_live_watch."
            "collect_ws3_market_summary_once",return_value=fake
        ) as collector, patch(
            "indodax_ai_bot.m03_ws_summary_live_watch."
            "persist_ws3_summary_watch",side_effect=M03WSWatchError(
                "M03_WS_WATCH_NO_FRESH_LIQUID_IDR_PAIR")
        ), patch.dict(os.environ, {"M03_WS3_EVIDENCE_ARCHIVE_GO":"YES"}), patch(
            "indodax_ai_bot.m03_ws3_evidence_archive_nonlive."
            "archive_ws3_evidence_nonlive",return_value={}
        ) as archive:
            with self.assertRaisesRegex(M03WSWatchError,
                                       "M03_WS_WATCH_NO_FRESH_LIQUID_IDR_PAIR"):
                run_m03_ws3_summary_watch_once(str(self.base/"decisions.sqlite3"))
            collector.assert_called_once()
            archive.assert_called_once()
            self.assertIs(archive.call_args.args[0],fake)

    def test_shadow_failure_never_prevents_original_watch(self):
        with patch(
            "indodax_ai_bot.m03_ws_summary_live_watch."
            "collect_ws3_market_summary_once", return_value=source()
        ), patch(
            "indodax_ai_bot.m03_ws_summary_live_watch."
            "persist_ws3_summary_watch", return_value={
                "status":"M03_WS3_SUMMARY_WATCH_ONLY_OK",
                "watch_candidate_count": 1,
            }
        ), patch.dict(os.environ, {"M03_WS3_EVIDENCE_ARCHIVE_GO":"YES"}), patch(
            "indodax_ai_bot.m03_ws3_evidence_archive_nonlive."
            "archive_ws3_evidence_nonlive",
            side_effect=OSError("shadow unavailable")
        ):
            report = run_m03_ws3_summary_watch_once(
                str(self.base / "decisions.sqlite3"))
        self.assertEqual(report["watch_candidate_count"], 1)
        self.assertEqual(report["research_archive"]["status"],
                         "M03_WS3_RESEARCH_ARCHIVE_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
