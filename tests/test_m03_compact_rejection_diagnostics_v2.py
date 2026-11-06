"""Original M03 pre-Final DEEP20 compact rejection-only observability.

No HTTP, paid provider, Telegram, service change, private key or exchange
order. Compatibility with already persisted v1 rows is mandatory.
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from indodax_ai_bot.__main__ import _m03_compact_from_original
from indodax_ai_bot.journal import DecisionJournal, DecisionConflictError


def fake_outcome(bulk=None):
    return SimpleNamespace(
        final_decision=None,
        stage_disposition="ADVISORY_ONLY_NO_OWNER_DECISION",
        execution_allowed=False,
        bulk=bulk,
    )


class M03OriginalCompactV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "decision.sqlite3"
        self.journal = DecisionJournal(self.path)

    def real_partial(self):
        return {
            "coverage_status": "PARTIAL_INVALID_PAIR_EVIDENCE",
            "attempted_count": 20,
            "analyzed_count": 20,
            "candidate_valid_count": 17,
            "candidate_rejected_count": 3,
            "candidate_rejection_reason_counts": {
                "NO_CLOSED_CANDLE": 2,
                "REJECT_SOURCE_ERROR": 1,
            },
            "eligible_cohort": {"status": "ALIGNED_PUBLIC_UNATTESTED"},
            "screening_observed_epoch": 1793934300,
            "g1_verified": False, "execution_allowed": False,
            "trade_authorized": False,
        }

    def test_partial_v2_persists_existing_count_and_reason_fields(self):
        summary = _m03_compact_from_original(fake_outcome(self.real_partial()))
        self.assertEqual(summary["schema_version"], 2)
        self.assertEqual(summary["attempted_count"], 20)
        self.assertEqual(summary["analyzed_count"], 20)
        self.assertEqual(summary["candidate_valid_count"], 17)
        self.assertEqual(summary["candidate_rejected_count"], 3)
        self.assertEqual(summary["candidate_rejection_reason_counts"], {
            "NO_CLOSED_CANDLE": 2, "REJECT_SOURCE_ERROR": 1,
        })
        self.assertFalse(summary["g1_verified"])
        self.assertFalse(summary["execution_allowed"])
        self.journal.record_m03_compact_tick("test-original", summary)
        with sqlite3.connect(self.path) as db:
            saved = db.execute(
                "SELECT summary_json FROM m03_compact_tick WHERE trace_id=?",
                ("test-original",),
            ).fetchone()
        self.assertEqual(json.loads(saved[0]), summary)
        self.journal.record_m03_compact_tick("test-original", summary)
        self.assertEqual(self.journal.m03_compact_count(), 1)

    def test_unknown_untrusted_reason_does_not_leak_source_text(self):
        original = self.real_partial()
        original["candidate_rejection_reason_counts"] = {
            "error: SECRET my_key=xyz": 3,
        }
        summary = _m03_compact_from_original(fake_outcome(original))
        self.assertEqual(
            summary["candidate_rejection_reason_counts"],
            {"UNCLASSIFIED_SOURCE_REASON": 3},
        )
        self.assertNotIn("SECRET", json.dumps(summary))
        self.journal.record_m03_compact_tick("untrusted", summary)

    def test_too_many_reason_categories_are_aggregated_without_loss(self):
        original = self.real_partial()
        original.update(candidate_valid_count=13, candidate_rejected_count=7)
        original["candidate_rejection_reason_counts"] = {
            "REASON_" + str(i): 1 for i in range(7)
        }
        summary = _m03_compact_from_original(fake_outcome(original))
        self.assertEqual(
            sum(summary["candidate_rejection_reason_counts"].values()), 7
        )
        self.assertLessEqual(len(summary["candidate_rejection_reason_counts"]), 6)
        self.journal.record_m03_compact_tick("bounded", summary)

    def test_v1_legacy_rows_still_accepted_in_same_original_journal(self):
        full = _m03_compact_from_original(fake_outcome(self.real_partial()))
        legacy_keys = {
            "schema_version", "lane", "coverage_status",
            "attempted_count", "analyzed_count",
            "final_decision", "stage_disposition",
            "execution_allowed", "g1_verified", "paid_network_allowed",
        }
        legacy = {k: v for k, v in full.items() if k in legacy_keys}
        legacy["schema_version"] = 1
        self.journal.record_m03_compact_tick("legacy", legacy)
        self.journal.record_m03_compact_tick("new", full)
        self.assertEqual(self.journal.m03_compact_count(), 2)

    def test_tampered_v2_counts_fail_closed(self):
        s = _m03_compact_from_original(fake_outcome(self.real_partial()))
        s["candidate_rejected_count"] = 4
        with self.assertRaisesRegex(ValueError, "V2_COUNT_MISMATCH"):
            self.journal.record_m03_compact_tick("bad-count", s)
        self.assertEqual(self.journal.m03_compact_count(), 0)

    def test_tampered_v2_reason_total_fails_closed(self):
        s = _m03_compact_from_original(fake_outcome(self.real_partial()))
        s["candidate_rejection_reason_counts"]["REJECT_SOURCE_ERROR"] = 20
        with self.assertRaisesRegex(ValueError, "V2_REJECTIONS_INVALID"):
            self.journal.record_m03_compact_tick("bad-reasons", s)

    def test_never_promote_source_to_final_or_execution(self):
        source = self.real_partial()
        source["trade_authorized"] = True
        with self.assertRaisesRegex(ValueError, "M03_COMPACT_SOURCE_UNSAFE"):
            _m03_compact_from_original(fake_outcome(source))
        source["trade_authorized"] = False
        summary = _m03_compact_from_original(fake_outcome(source))
        summary["g1_verified"] = True
        with self.assertRaisesRegex(ValueError, "M03_COMPACT_PRE_FINAL_ONLY"):
            self.journal.record_m03_compact_tick("bad-authority", summary)

    def test_conflicting_replay_refused_same_trace(self):
        s = _m03_compact_from_original(fake_outcome(self.real_partial()))
        self.journal.record_m03_compact_tick("same-trace", s)
        s["coverage_status"] = "TIME_EXPIRED_OR_TRUNCATED"
        with self.assertRaises(DecisionConflictError):
            self.journal.record_m03_compact_tick("same-trace", s)

    def test_no_bulk_records_no_fabricated_candidate_counts(self):
        s = _m03_compact_from_original(fake_outcome(None))
        self.assertIsNone(s["candidate_valid_count"])
        self.assertIsNone(s["candidate_rejected_count"])
        self.assertEqual(s["candidate_rejection_reason_counts"], {})
        self.assertIsNone(s["scan_observed_epoch"])
        self.journal.record_m03_compact_tick("none", s)


if __name__ == "__main__":
    unittest.main()
