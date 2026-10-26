"""M04-D public source coverage, with mock exchange data and NO LIVE permission."""
from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.deep_analysis import bulk_public_deep_scan
from indodax_ai_bot.source_coverage import public_coverage_report

from test_m03b_deep import observation
from test_m03g_bulk import FakeBulkMarket, fake_screen
from test_m03h_coverage import EightInvalid


class InflatedCoverage(FakeBulkMarket):
    def observe(self, pair, *, scan_context=None):
        self.calls.append((pair, scan_context))
        return replace(observation(pair), trade_history_complete=True)


class M04DPublicCoverageTests(unittest.TestCase):
    def test_twenty_public_pairs_never_claim_exhaustive_trade_data(self):
        fake = FakeBulkMarket()
        data = bulk_public_deep_scan(fake, fake_screen())
        admission = data["g1_source_admission"]
        self.assertEqual(admission["stage"], "M04D_PUBLIC_SOURCE_COVERAGE")
        self.assertEqual(admission["status"], "REJECT_SOURCE_NOT_QUALIFIED")
        self.assertEqual(admission["pairs_requested"], 20)
        self.assertEqual(admission["pairs_observed"], 20)
        self.assertEqual(len(admission["pair_coverage"]), 20)
        self.assertEqual(len(fake.calls), 20)
        self.assertTrue(admission["public_trades_are_recent_sample"])
        self.assertFalse(admission["public_trade_history_complete"])
        self.assertFalse(admission["independently_authenticated_market_origin"])
        self.assertFalse(admission["g1_receipt_issuance_allowed"])
        self.assertFalse(admission["g2_provider_call_allowed"])
        self.assertFalse(admission["execution_allowed"])
        self.assertTrue(all(not row["can_prove_complete_trade_tape"]
                            for row in admission["pair_coverage"]))

    def test_per_pair_record_preserves_actual_visible_counts_and_epoch(self):
        data = bulk_public_deep_scan(FakeBulkMarket(), fake_screen())
        item = data["g1_source_admission"]["pair_coverage"][0]
        actual = observation("btc_idr")
        self.assertEqual(item["pair"], "btc_idr")
        self.assertEqual(item["recent_trade_rows_visible"], actual.trade_count_visible)
        self.assertEqual(item["closed_1m_trades_visible"],
                         actual.visible_closed_1m_trade_count)
        self.assertEqual(item["closed_1m_epoch"],
                         actual.snapshot.source.closed_1m_epoch)
        self.assertEqual(item["reason"], "PUBLIC_RECENT_TRADE_WINDOW_NOT_EXHAUSTIVELY_PROVEN")

    def test_invalid_indicators_do_not_make_observed_tape_exhaustive(self):
        data = bulk_public_deep_scan(EightInvalid(), fake_screen())
        admission = data["g1_source_admission"]
        self.assertEqual(data["candidate_rejected_count"], 8)
        self.assertEqual(admission["pairs_observed"], 20)
        self.assertEqual(admission["pairs_requested"], 20)
        self.assertFalse(admission["g1_receipt_issuance_allowed"])
        self.assertEqual(data["same_window"]["ranked_same_window_diagnostics"], [])

    def test_deadline_reports_not_seen_pairs_as_missing(self):
        data = bulk_public_deep_scan(FakeBulkMarket(expire_after=2), fake_screen())
        report = data["g1_source_admission"]
        self.assertTrue(report["batch_window_expired"])
        self.assertFalse(report["batch_complete"])
        self.assertEqual(report["pairs_observed"], 1)
        self.assertEqual(sum(not x["observation_present"] for x in report["pair_coverage"]), 19)
        self.assertEqual(report["pair_coverage"][-1]["reason"], "NO_PUBLIC_OBSERVATION")
        self.assertFalse(report["g2_provider_call_allowed"])

    def test_forged_completeness_boolean_is_rejected_not_promoted(self):
        sample = InflatedCoverage()
        data = bulk_public_deep_scan(sample, fake_screen())
        report = data["g1_source_admission"]
        self.assertEqual(report["pairs_observed"], 20)
        self.assertEqual(report["pair_coverage"][0]["reason"],
                         "UNVERIFIABLE_PUBLIC_COMPLETENESS_OR_TRUST_CLAIM")
        self.assertFalse(report["g1_receipt_issuance_allowed"])
        self.assertFalse(report["public_trade_history_complete"])

    def test_missing_bulk_never_produces_source_qualification(self):
        result = public_coverage_report(("btc_idr",), {}, screening_epoch=1800000130,
                                        complete_batch=False, window_expired=True)
        self.assertEqual(result["pairs_observed"], 0)
        self.assertEqual(result["pair_coverage"][0]["reason"], "NO_PUBLIC_OBSERVATION")
        self.assertFalse(result["g1_receipt_issuance_allowed"])
        self.assertFalse(result["execution_allowed"])

    def test_same_minute_candidate_cohort_is_not_source_issuer(self):
        data = bulk_public_deep_scan(FakeBulkMarket(), fake_screen())
        self.assertEqual(data["eligible_cohort"]["status"], "ALIGNED_PUBLIC_UNATTESTED")
        self.assertFalse(data["g1_source_admission"]["g1_receipt_issuance_allowed"])
        self.assertFalse(data["g1_verified"])
        self.assertFalse(data["trade_authorized"])

    def test_cli_public_g1_coverage_is_observable_and_final_remains_reject(self):
        with TemporaryDirectory() as td:
            output = StringIO()
            with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner:
                scanner.return_value.scan.return_value = fake_screen()
                with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                           return_value=FakeBulkMarket()):
                    with redirect_stdout(output):
                        rc = main(["--bulk-deep-once", "--db",
                                   str(Path(td) / "journal.sqlite3")])
        parsed = json.loads(output.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual(parsed["final_decision"], "REJECT")
        self.assertFalse(parsed["execution_allowed"])
        self.assertFalse(parsed["live_trading_authorized"])
        self.assertEqual(parsed["public_bulk"]["g1_source_admission"]["pairs_observed"], 20)
        self.assertFalse(parsed["public_bulk"]["g1_source_admission"]["g2_provider_call_allowed"])
        self.assertEqual(parsed["ai_shadow"]["completed_roles"], [])
        self.assertFalse(parsed["ai_shadow"]["provider_connected"])


if __name__ == "__main__":
    unittest.main()
