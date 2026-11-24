"""Offline post400 collector tests; no network, service actions or orders."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import m04_ws3_watch_post400_collector_nonlive as collector
from tests.test_m04_ws3_watch_multicohort_readonly import _report, EPOCH


def current(epoch=EPOCH, pair="anoa_idr", digest="a"*64, volume="400000000"):
    r = _report(epoch, (pair, digest, True, 3))
    r["current_m04_ws3_handoff_min_rolling_24h_idr"] = "400000000"
    for row in r["observations"]:
        row["current_m04_source_rolling_24h_turnover_idr"] = volume
    return r


class Post400CollectorTests(unittest.TestCase):
    def setUp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        self.directory = self.root / "post400"
        self.db = self.root / "unused.sqlite3"
        env = patch.dict(os.environ, collector.LOCKS)
        env.start()
        self.addCleanup(env.stop)

    def collect(self, report, epoch=EPOCH):
        with patch.object(collector, "inspect_m04_watch_trade_channels_readonly",
                          return_value=report) as inspect:
            result = collector.collect_once(
                source_db=self.db, evidence_dir=self.directory,
                now_epoch=epoch)
        return result, inspect.call_count

    def test_saves_private_file_and_summarizes_current_policy(self):
        result, count = self.collect(current())
        self.assertEqual(count, 1)
        self.assertEqual(result["status"], "POST400_CURRENT_WATCH_SAVED")
        files = list(self.directory.glob("watch-*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertFalse(self.db.exists())
        summary = collector.summarize(evidence_dir=self.directory)
        self.assertEqual(summary["status"], "POST400_SUMMARY_RESEARCH_ONLY")
        self.assertTrue(summary["summary"]["post400_policy_only"])
        self.assertEqual(summary["summary"]["distinct_candidate_evidence"], 1)
        self.assertFalse(summary["execution_allowed"])
        self.assertFalse(summary["independent_g1_verified"])

    def test_same_source_epoch_changed_digest_is_not_new_watch(self):
        self.collect(current())
        changed = current(EPOCH+10, digest="b"*64)
        changed["observations"][0]["m04_source_observed_epoch"] = EPOCH-25
        result, _ = self.collect(changed, EPOCH+10)
        self.assertEqual(result["status"], "POST400_DUPLICATE_SOURCE_OBSERVATIONS")
        self.assertEqual(result["saved_reports"], 1)
        self.assertEqual(len(list(self.directory.glob("watch-*.json"))), 1)

    def test_different_source_epoch_is_collected(self):
        self.collect(current())
        result, _ = self.collect(current(EPOCH+180, digest="b"*64), EPOCH+180)
        self.assertEqual(result["status"], "POST400_CURRENT_WATCH_SAVED")
        self.assertEqual(result["saved_reports"], 2)
        summary = collector.summarize(evidence_dir=self.directory)["summary"]
        self.assertEqual(summary["distinct_candidate_evidence"], 2)

    def test_no_valid_watch_writes_no_report(self):
        result, _ = self.collect({"status": "ORIGINAL_M04_NO_ELIGIBLE_UNCALLED_WATCH"})
        self.assertEqual(result["status"], "POST400_NO_VALID_CURRENT_WATCH")
        self.assertEqual(list(self.directory.glob("watch-*.json")), [])
        self.assertEqual(collector.summarize(evidence_dir=self.directory)["status"],
                         "POST400_NO_SAVED_REPORTS")

    def test_malformed_or_pre400_report_rejected(self):
        old = current()
        del old["current_m04_ws3_handoff_min_rolling_24h_idr"]
        for report in (old, current(volume="399999999"), current(volume="NaN")):
            with self.subTest(report=report):
                result, _ = self.collect(report)
                self.assertEqual(result["status"], "POST400_STORAGE_OR_CONTRACT_BLOCKED")
        self.assertEqual(list(self.directory.glob("watch-*.json")), [])

    def test_cap_preserves_existing_file_and_does_not_capture(self):
        self.collect(current())
        original = list(self.directory.glob("watch-*.json"))[0]
        before = original.read_bytes()
        with patch.object(collector, "MAX_REPORTS", 1):
            result, count = self.collect(current(EPOCH+180), EPOCH+180)
        self.assertEqual(result["status"], "POST400_STORAGE_CAP_REACHED")
        self.assertEqual(count, 0)
        self.assertEqual(original.read_bytes(), before)

    def test_explicit_nonlive_locks_mandatory(self):
        with patch.dict(os.environ, {"INDODAX_TRADING_ENABLED": "YES"}):
            result, count = self.collect(current())
        self.assertEqual(result["status"], "POST400_EXPLICIT_NONLIVE_LOCKS_REQUIRED")
        self.assertEqual(count, 0)
        self.assertFalse(self.directory.exists())

    def test_symlink_directory_rejected(self):
        other = self.root / "other"
        other.mkdir(mode=0o700)
        self.directory.symlink_to(other, target_is_directory=True)
        result, count = self.collect(current())
        self.assertEqual(result["status"], "POST400_STORAGE_OR_CONTRACT_BLOCKED")
        self.assertEqual(count, 0)
        self.assertEqual(list(other.iterdir()), [])

    def test_old_policy_file_in_new_directory_is_not_mixed(self):
        self.directory.mkdir(mode=0o700)
        old = _report(EPOCH, ("icnt_idr", "a"*64, False, 0))
        p = self.directory / "watch-old.json"
        p.write_text(json.dumps(old))
        p.chmod(0o600)
        result, count = self.collect(current())
        self.assertEqual(result["status"], "POST400_STORAGE_OR_CONTRACT_BLOCKED")
        self.assertEqual(count, 0)
        self.assertEqual(collector.summarize(evidence_dir=self.directory)["status"],
                         "POST400_STORAGE_OR_CONTRACT_BLOCKED")

    def test_post400_summary_dedup_overlapping_source_observations(self):
        self.collect(current())
        later = current(EPOCH+30, digest="b"*64)
        later["observations"][0]["m04_source_observed_epoch"] = EPOCH-25
        second = current(EPOCH+30, pair="cst_idr", digest="c"*64)["observations"][0]
        later["observations"].append(second)
        later["candidate_count"] = 2
        result, _ = self.collect(later, EPOCH+30)
        self.assertEqual(result["status"], "POST400_CURRENT_WATCH_SAVED")
        self.assertEqual(result["new_source_observations"], 1)
        summary = collector.summarize(evidence_dir=self.directory)["summary"]
        self.assertEqual(summary["distinct_candidate_evidence"], 2)
        self.assertEqual(summary["duplicate_candidate_evidence_ignored"], 1)
        self.assertEqual(summary["candidate_identity_basis"], "PAIR_AND_ORIGINAL_SOURCE_EPOCH")


if __name__ == "__main__":
    unittest.main()
