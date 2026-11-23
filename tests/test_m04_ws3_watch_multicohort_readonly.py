"""Pure offline multi-WATCH channel-coverage regression fixtures.

Never opens a WS/REST connection, calls a model, sends Telegram or orders.
Fixture reports mimic already-captured original M04 operator output; they
do not independently establish real exchange provenance.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.m04_ws3_watch_multicohort_readonly import (
    summarize_original_current_watch_reports_readonly,
)

EPOCH = 1795420000


def _row(pair: str, digest: str, epoch: int,
         membership: bool | None = True, n: int = 3):
    membership_status = {
        True: "REQUEST_LISTED_IN_LATEST_RECENT_CHECKPOINT",
        False: "NOT_LISTED_IN_LATEST_RECENT_CHECKPOINT",
        None: "RECENT_CHECKPOINT_UNAVAILABLE",
    }
    return {
        "pair": pair,
        "m04_original_evidence_digest_sha256": digest,
        "m04_source_observed_epoch": epoch - 25,
        "checkpoint_request_membership": membership,
        "checkpoint_request_evidence_status": membership_status[membership],
        "pair_specific_ack_evidence": "NOT_AVAILABLE_IN_CHECKPOINT",
        "cursor_status_proves_active_subscription": False,
        "current_cursor_status_not_timestamped_ack": None,
        "zero_rows_do_not_prove_zero_exchange_trades": True,
        "qualified_for_g1_or_market_decision": False,
        "source_local_retained_trade_windows": {
            "1m": {
                "retained_local_trade_rows_by_capture_cutoff": n,
                "local_rows_are_not_complete_trade_tape_proof": True,
            },
            "15m": {
                "retained_local_trade_rows_by_capture_cutoff": n * 4,
                "local_rows_are_not_complete_trade_tape_proof": True,
            },
            "60m": {
                "retained_local_trade_rows_by_capture_cutoff": n * 9,
                "local_rows_are_not_complete_trade_tape_proof": True,
            },
        },
        "local_trade_publications_60m_by_receipt": n,
        "recorded_discontinuities_60m": 0,
    }


def _report(epoch: int, *pairs: tuple[str, str, bool | None, int]):
    items = [_row(pair, digest, epoch, membership, count)
             for pair, digest, membership, count in pairs]
    any_checkpoint = any(item["checkpoint_request_membership"] is not None
                         for item in items)
    return {
        "stage": "M04_ORIGINAL_WATCH_TRADE_CHANNEL_COVERAGE_READONLY",
        "status": "M04_WATCH_TRADE_CHANNEL_COVERAGE_RESEARCH_ONLY",
        "captured_epoch": epoch,
        "evaluated_epoch": epoch,
        "capture_mode": "CURRENT_OPERATOR_ONE_SHOT",
        "capture_and_source_db_read_are_atomic": False,
        "historical_source_immutability_verified": False,
        "journal_status": "JOURNAL_READ_OK",
        "checkpoint_count": 1 if any_checkpoint else 0,
        "latest_checkpoint_epoch": epoch - 15 if any_checkpoint else None,
        "latest_checkpoint_age_seconds": 15 if any_checkpoint else None,
        "checkpoint_membership_is_only_subscription_request_evidence": True,
        "checkpoint_not_listed_is_not_proof_of_exchange_inactivity": True,
        "pair_specific_ack_verified": False,
        "no_pair_specific_ack_timestamp_in_existing_checkpoint": True,
        "trade_index_not_complete_exchange_tape": True,
        "independent_g1_verified": False,
        "m04_selection_modified": False,
        "real_ai_role_responses_observed": False,
        "candidate_count": len(items),
        "observations": items,
        "paper_fill_created": False,
        "paper_orders_created": 0,
        "provider_calls": 0,
        "telegram_messages_sent": 0,
        "order_sent": False,
        "execution_allowed": False,
    }


class OriginalWatchChannelMultiCohortResearchTests(unittest.TestCase):
    def setUp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        self.index = 0

    def _file(self, report, *, name=None):
        self.index += 1
        p = self.root / (name or f"m04-current-{self.index}.json")
        p.write_text(json.dumps(report), encoding="utf-8")
        p.chmod(0o600)
        return p

    def _digest(self, text):
        return sha256(text.encode()).hexdigest()

    def test_three_distinct_packets_and_two_pairs_are_not_ack_or_g1(self):
        reports = [
            _report(EPOCH, ("tmg_idr", self._digest("TMG-A"), True, 4),
                    ("drx_idr", self._digest("DRX-A"), False, 0)),
            _report(EPOCH+180, ("tmg_idr", self._digest("TMG-B"), True, 2),
                    ("drx_idr", self._digest("DRX-B"), False, 0)),
            _report(EPOCH+360, ("tmg_idr", self._digest("TMG-C"), None, 0),
                    ("drx_idr", self._digest("DRX-C"), None, 0)),
        ]
        result = summarize_original_current_watch_reports_readonly(
            evidence_files=[self._file(r) for r in reports])
        self.assertEqual(result["status"], "M04_CURRENT_WATCH_MULTICOHORT_RESEARCH_ONLY")
        self.assertEqual(result["distinct_watch_packet_fingerprints"], 3)
        self.assertEqual(result["distinct_candidate_evidence"], 6)
        self.assertTrue(result["enough_distinct_packet_fingerprints_for_repeat_pattern"])
        self.assertEqual(result["duplicate_watch_packet_reports_ignored"], 0)
        rows = {r["pair"]: r for r in result["pair_summaries"]}
        self.assertEqual(set(rows), {"tmg_idr", "drx_idr"})
        self.assertEqual(rows["tmg_idr"]["request_listed"], 2)
        self.assertEqual(rows["tmg_idr"]["checkpoint_unavailable"], 1)
        self.assertEqual(rows["drx_idr"]["not_listed_latest_checkpoint"], 2)
        self.assertEqual(rows["drx_idr"]["checkpoint_unavailable"], 1)
        self.assertEqual(rows["drx_idr"]["without_retained_local_trades_60m"], 3)
        self.assertEqual(rows["tmg_idr"]["with_retained_local_trades_1m"], 2)
        for field in ("pair_specific_ack_verified", "independent_g1_verified",
                      "complete_trade_tape_verified", "execution_allowed"):
            self.assertFalse(result[field])
        self.assertFalse(result["ranking_cause_of_missed_trade_channel_verified"])
        self.assertEqual(result["paper_orders_created"], 0)
        self.assertEqual(result["telegram_messages_sent"], 0)
        self.assertEqual(result["provider_calls"], 0)

    def test_identical_candidate_packet_across_captures_deduplicates(self):
        old = _report(EPOCH, ("drx_idr", self._digest("same-source"), False, 0))
        again = _report(EPOCH+60, ("drx_idr", self._digest("same-source"), True, 5))
        result = summarize_original_current_watch_reports_readonly(
            evidence_files=[self._file(again), self._file(old)])
        self.assertEqual(result["accepted_input_reports"], 2)
        self.assertEqual(result["distinct_watch_packet_fingerprints"], 1)
        self.assertEqual(result["distinct_candidate_evidence"], 1)
        self.assertEqual(result["duplicate_watch_packet_reports_ignored"], 1)
        self.assertEqual(result["duplicate_candidate_evidence_ignored"], 1)
        row = result["pair_summaries"][0]
        self.assertEqual(row["not_listed_latest_checkpoint"], 1)
        self.assertEqual(row["request_listed"], 0)
        self.assertFalse(result["enough_distinct_packet_fingerprints_for_repeat_pattern"])

    def test_overlapping_two_candidate_packets_do_not_double_count_pair_digest(self):
        shared = self._digest("shared")
        packet_a = _report(EPOCH, ("tmg_idr", shared, True, 3),
                         ("drx_idr", self._digest("first"), False, 0))
        packet_b = _report(EPOCH+180, ("tmg_idr", shared, False, 0),
                         ("drx_idr", self._digest("second"), True, 2))
        result = summarize_original_current_watch_reports_readonly(
            evidence_files=[self._file(packet_a), self._file(packet_b)])
        self.assertEqual(result["distinct_watch_packet_fingerprints"], 2)
        self.assertEqual(result["distinct_candidate_evidence"], 3)
        self.assertEqual(result["duplicate_candidate_evidence_ignored"], 1)
        rows = {r["pair"]: r for r in result["pair_summaries"]}
        self.assertEqual(rows["tmg_idr"]["distinct_candidate_evidence"], 1)
        self.assertEqual(rows["drx_idr"]["distinct_candidate_evidence"], 2)

    def test_saved_retrospective_is_not_current_watch_evidence(self):
        report = _report(EPOCH, ("drx_idr", self._digest("retro"), False, 0))
        report["capture_mode"] = "SAVED_CAPTURE_RETROSPECTIVE"
        result = summarize_original_current_watch_reports_readonly(
            evidence_files=[self._file(report)])
        self.assertEqual(result["status"], "M04_MULTICOHORT_INPUT_BLOCKED")
        self.assertEqual(result["accepted_input_reports"], 0)
        self.assertFalse(result["execution_allowed"])

    def test_nonprivate_symlink_and_duplicate_path_fail_closed(self):
        report = _report(EPOCH, ("tmg_idr", self._digest("source"), True, 4))
        file = self._file(report)
        duplicate = summarize_original_current_watch_reports_readonly(
            evidence_files=[file, file])
        self.assertEqual(duplicate["status"], "M04_MULTICOHORT_INPUT_BLOCKED")
        file.chmod(0o644)
        bad_mode = summarize_original_current_watch_reports_readonly(
            evidence_files=[file])
        self.assertEqual(bad_mode["status"], "M04_MULTICOHORT_INPUT_BLOCKED")
        file.chmod(0o600)
        alias = self.root / "symlink.json"
        alias.symlink_to(file)
        bad_link = summarize_original_current_watch_reports_readonly(
            evidence_files=[alias])
        self.assertEqual(bad_link["status"], "M04_MULTICOHORT_INPUT_BLOCKED")

    def test_tampered_flags_and_inconsistent_counts_fail_closed(self):
        base = _report(EPOCH, ("tmg_idr", self._digest("src"), True, 3))
        edits = [
            lambda x: x.update(execution_allowed=True),
            lambda x: x.update(pair_specific_ack_verified=True),
            lambda x: x.update(independent_g1_verified=True),
            lambda x: x.update(captured_epoch=x["captured_epoch"]-1),
            lambda x: x["observations"][0].update(
                checkpoint_request_evidence_status="RECENT_CHECKPOINT_UNAVAILABLE"),
            lambda x: x["observations"][0].update(
                checkpoint_request_membership=1),
            lambda x: x["observations"][0]["source_local_retained_trade_windows"]
                     ["60m"].update(retained_local_trade_rows_by_capture_cutoff=1),
            lambda x: x.update(candidate_count=2),
        ]
        for i, mutate in enumerate(edits):
            with self.subTest(i=i):
                report = deepcopy(base)
                mutate(report)
                result = summarize_original_current_watch_reports_readonly(
                    evidence_files=[self._file(report)])
                self.assertEqual(result["status"], "M04_MULTICOHORT_INPUT_BLOCKED")
                self.assertFalse(result["execution_allowed"])

    def test_duplicate_json_property_and_bad_collection_size_fail_closed(self):
        p = self.root / "duplicate.json"
        p.write_text('{"stage":"x","stage":"y"}', encoding="utf-8")
        p.chmod(0o600)
        bad_json = summarize_original_current_watch_reports_readonly(
            evidence_files=[p])
        self.assertEqual(bad_json["status"], "M04_MULTICOHORT_INPUT_BLOCKED")
        invalid_scope = summarize_original_current_watch_reports_readonly(
            evidence_files=[])
        self.assertEqual(invalid_scope["status"], "M04_MULTICOHORT_INPUT_BLOCKED")
        excessive = summarize_original_current_watch_reports_readonly(
            evidence_files=[p] * 33)
        self.assertEqual(excessive["status"], "M04_MULTICOHORT_INPUT_BLOCKED")

    def test_bad_missing_checkpoint_indeterminate_membership(self):
        missing = _report(EPOCH, ("drx_idr", self._digest("missing"), None, 0))
        result = summarize_original_current_watch_reports_readonly(
            evidence_files=[self._file(missing)])
        self.assertEqual(result["status"], "M04_CURRENT_WATCH_MULTICOHORT_RESEARCH_ONLY")
        self.assertEqual(result["pair_summaries"][0]["checkpoint_unavailable"], 1)
        self.assertEqual(result["pair_summaries"][0]["not_listed_latest_checkpoint"], 0)
        self.assertFalse(result["enough_distinct_packet_fingerprints_for_repeat_pattern"])


if __name__ == "__main__":
    unittest.main()
