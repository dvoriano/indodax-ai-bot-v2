"""M04 public 1m/5m/15m temporal diagnostics on the original source path.

Public timestamps and sampled book/trade activity NEVER certify source
provenance, exhaustive tape, paid AI admission, or order execution.
"""
from __future__ import annotations

from dataclasses import replace
import unittest

from indodax_ai_bot.source_coverage import public_coverage_report
from indodax_ai_bot.deep_analysis import bulk_public_deep_scan
from test_m03b_deep import observation
from test_m03g_bulk import FakeBulkMarket, fake_screen


class M04PublicTimeframeFreshnessTests(unittest.TestCase):
    def _aligned(self, pair="btc_idr"):
        obs = observation(pair)
        end = obs.snapshot.source.closed_1m_epoch
        five = replace(obs.candle_5m, start=(end // 300 - 1) * 300)
        return replace(obs, candle_5m=five)

    def test_timestamp_and_context_fields_on_same_realistic_public_window(self):
        btc = self._aligned("btc_idr")
        eth = self._aligned("eth_idr")
        end = btc.snapshot.source.closed_1m_epoch
        report = public_coverage_report(
            ("btc_idr", "eth_idr"), {"btc_idr": btc, "eth_idr": eth},
            screening_epoch=end - 5, complete_batch=True, window_expired=False
        )
        self.assertEqual(report["pairs_with_aligned_public_timestamps"], 2)
        self.assertEqual(report["pairs_with_missing_or_invalid_public_timestamps"], 0)
        for row in report["pair_coverage"]:
            diag = row["timeframe_diagnostic"]
            self.assertEqual(diag["status"], "ALIGNED_PUBLIC_TIMESTAMPS_UNATTESTED")
            self.assertEqual(diag["closed_1m_start_epoch"] + 60, end)
            self.assertEqual(diag["snapshot_age_after_1m_close_seconds"], 10)
            self.assertEqual(diag["screen_to_observation_seconds"], 15)
            self.assertIsNone(diag["exchange_to_local_clock_skew_seconds"])
            self.assertGreater(diag["orderbook_depth_levels_visible"], 0)
            self.assertFalse(diag["full_trade_tape_proven"])
            self.assertFalse(diag["independent_source_origin_verified"])
        self.assertFalse(report["g1_receipt_issuance_allowed"])
        self.assertFalse(report["g2_provider_call_allowed"])
        self.assertFalse(report["execution_allowed"])

    def test_stale_public_snapshot_is_reported_not_promoted(self):
        obs = self._aligned()
        end = obs.snapshot.source.closed_1m_epoch
        bad_source = replace(obs.snapshot.source, observed_epoch=end + 200)
        bad = replace(obs, snapshot=replace(obs.snapshot, source=bad_source))
        report = public_coverage_report(
            ("btc_idr",), {"btc_idr": bad}, screening_epoch=end - 5,
            complete_batch=True, window_expired=False
        )
        row = report["pair_coverage"][0]
        self.assertEqual(row["reason"], "PUBLIC_SOURCE_CLOCK_MISMATCH")
        self.assertEqual(row["timeframe_diagnostic"]["status"],
                         "REJECT_PUBLIC_TIMEFRAME_OR_AGE")
        self.assertEqual(report["pairs_with_aligned_public_timestamps"], 0)
        self.assertFalse(report["g1_receipt_issuance_allowed"])

    def test_bad_5m_alignment_and_absent_pair_have_distinct_diagnostics(self):
        obs = self._aligned()
        broken = replace(obs, candle_5m=replace(obs.candle_5m, interval=60))
        end = obs.snapshot.source.closed_1m_epoch
        report = public_coverage_report(
            ("btc_idr", "eth_idr"), {"btc_idr": broken},
            screening_epoch=end - 5, complete_batch=False,
            window_expired=True
        )
        self.assertEqual(report["pair_coverage"][0]["timeframe_diagnostic"]["status"],
                         "REJECT_PUBLIC_TIMEFRAME_OR_AGE")
        self.assertIsNone(report["pair_coverage"][1]["timeframe_diagnostic"])
        self.assertEqual(report["pair_coverage"][1]["reason"], "NO_PUBLIC_OBSERVATION")
        self.assertEqual(report["pairs_with_missing_or_invalid_public_timestamps"], 2)
        self.assertFalse(report["public_trade_history_complete"])

    def test_existing_bulk_mainpath_carries_diagnostic_without_extra_requests(self):
        fake = FakeBulkMarket()
        data = bulk_public_deep_scan(fake, fake_screen())
        report = data["g1_source_admission"]
        self.assertEqual(report["pairs_requested"], 20)
        self.assertEqual(len(fake.calls), 20)
        self.assertEqual(len(report["pair_coverage"]), 20)
        self.assertTrue(all("timeframe_diagnostic" in row
                            for row in report["pair_coverage"]))
        self.assertEqual(report["status"], "REJECT_SOURCE_NOT_QUALIFIED")
        self.assertFalse(data["g1_verified"])
        self.assertFalse(report["g2_provider_call_allowed"])


if __name__ == "__main__":
    unittest.main()
