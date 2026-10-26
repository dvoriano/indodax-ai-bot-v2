"""M03-I: rank only a fresh, valid public cohort; NEVER upgrade G1/AI/trade.

The 20-pair population guard must still REJECT if any member is invalid.
"""
from __future__ import annotations

import json
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.contracts import MarketSnapshot
from indodax_ai_bot.deep_analysis import bulk_public_deep_scan
from test_m03b_deep import observation
from test_m03g_bulk import FakeBulkMarket, fake_screen
from test_m03h_coverage import EightInvalid, SourceError


class BrokenBTC(FakeBulkMarket):
    def observe(self, pair, *, scan_context=None):
        self.calls.append((pair, scan_context))
        return observation(pair, no_trade=(pair == "btc_idr"))


class MisalignedValidPair(FakeBulkMarket):
    def observe(self, pair, *, scan_context=None):
        self.calls.append((pair, scan_context))
        obs = observation(pair, no_trade=(pair == "token015_idr"))
        if pair == "token004_idr":
            # Shift an entire closed 1m series BACK one minute: the source
            # receipt stays internally valid (observed_epoch >= close), while
            # the pair remains deliberately misaligned with BTC. Shifting it
            # into the future would violate SourceReceipt before testing G1.
            shifted = tuple(replace(c, start=c.start - 60)
                            for c in obs.candles_1m)
            receipt = replace(obs.snapshot.source,
                              closed_1m_epoch=obs.snapshot.source.closed_1m_epoch - 60)
            obs = replace(
                obs, candles_1m=shifted, candle_1m=shifted[-1],
                snapshot=MarketSnapshot(
                    receipt, obs.snapshot.best_bid_idr, obs.snapshot.best_ask_idr
                ),
            )
        return obs


class AllInvalid(FakeBulkMarket):
    def observe(self, pair, *, scan_context=None):
        self.calls.append((pair, scan_context))
        return observation(pair, no_trade=True)


class M03IValidCohortTests(unittest.TestCase):
    def test_partial_population_keeps_population_rejected_but_ranks_valid_same_minute_cohort(self):
        market = EightInvalid()
        data = bulk_public_deep_scan(market, fake_screen(), limit=20)
        self.assertEqual(data["candidate_valid_count"], 12)
        self.assertEqual(data["candidate_rejected_count"], 8)
        self.assertEqual(data["coverage_status"], "PARTIAL_INVALID_PAIR_EVIDENCE")
        self.assertEqual(data["same_window"]["status"], "REJECT_INCOMPLETE_OR_EXPIRED_BULK_COVERAGE")
        self.assertEqual(data["same_window"]["ranked_same_window_diagnostics"], [])
        self.assertFalse(data["synchronized_pair_coverage_complete"])
        cohort = data["eligible_cohort"]
        self.assertEqual(cohort["status"], "ALIGNED_PUBLIC_UNATTESTED")
        self.assertEqual(cohort["closed_1m_epoch"], 1800000120)
        self.assertEqual(cohort["eligible_pair_count"], 12)
        self.assertEqual(len(cohort["eligible_pair_ids"]), 12)
        self.assertEqual(len(cohort["ranked_same_window_diagnostics"]), 12)
        self.assertNotIn("token015_idr", cohort["eligible_pair_ids"])
        self.assertTrue(cohort["partial_population"])
        self.assertFalse(cohort["population_coverage_complete"])
        self.assertFalse(cohort["source_trust_verified"])
        self.assertFalse(cohort["trade_history_complete"])
        self.assertFalse(cohort["g1_verified"])
        self.assertFalse(cohort["g2_candidate_authorized"])
        self.assertFalse(cohort["cohort_is_trade_signal"])
        self.assertFalse(cohort["trade_authorized"])
        self.assertFalse(cohort["execution_allowed"])
        self.assertEqual(len(market.calls), 20)  # No additional REST work

    def test_faulty_pair_kept_out_of_eligible_cohort(self):
        data = bulk_public_deep_scan(SourceError(), fake_screen())
        self.assertEqual(data["candidate_rejected_count"], 1)
        self.assertNotIn("token011_idr", data["eligible_cohort"]["eligible_pair_ids"])
        self.assertEqual(data["eligible_cohort"]["eligible_pair_count"], 19)
        self.assertEqual(data["eligible_cohort"]["status"], "ALIGNED_PUBLIC_UNATTESTED")
        self.assertEqual(data["same_window"]["ranked_same_window_diagnostics"], [])

    def test_untrusted_or_incomplete_btc_cannot_be_used_as_regime_proof(self):
        market = BrokenBTC()
        data = bulk_public_deep_scan(market, fake_screen())
        self.assertEqual(data["eligible_cohort"]["status"], "REJECT_BTC_CONTEXT_INCOMPLETE")
        self.assertEqual(data["eligible_cohort"]["ranked_same_window_diagnostics"], [])
        self.assertEqual(data["eligible_cohort"]["provisional_watchlist_preview"], [])
        self.assertEqual([p for p, _ in market.calls].count("btc_idr"), 1)
        self.assertFalse(data["g2_candidate_authorized"])

    def test_mismatched_valid_candidate_cannot_join_sync_cohort(self):
        data = bulk_public_deep_scan(MisalignedValidPair(), fake_screen())
        self.assertEqual(data["candidate_rejected_count"], 1)
        self.assertEqual(data["eligible_cohort"]["eligible_pair_count"], 19)
        self.assertEqual(data["eligible_cohort"]["status"], "REJECT_CANDLE_OR_SNAPSHOT_MISALIGNMENT")
        self.assertEqual(data["eligible_cohort"]["ranked_same_window_diagnostics"], [])
        self.assertFalse(data["eligible_cohort"]["cohort_is_trade_signal"])

    def test_partial_timewindow_never_promotes_early_eligible_pairs(self):
        data = bulk_public_deep_scan(FakeBulkMarket(expire_after=2), fake_screen())
        self.assertEqual(data["eligible_cohort"]["status"], "REJECT_TRUNCATED_BULK_COHORT")
        self.assertEqual(data["eligible_cohort"]["ranked_same_window_diagnostics"], [])
        self.assertFalse(data["execution_allowed"])

    def test_no_eligible_candidates_never_produces_public_watchlist(self):
        data = bulk_public_deep_scan(AllInvalid(), fake_screen())
        self.assertEqual(data["eligible_cohort"]["eligible_pair_count"], 0)
        self.assertEqual(data["eligible_cohort"]["status"], "REJECT_NO_VALID_COHORT")
        self.assertEqual(data["eligible_cohort"]["provisional_watchlist_preview"], [])
        self.assertEqual(data["eligible_cohort"]["ranked_same_window_diagnostics"], [])
        self.assertFalse(data["eligible_cohort"]["g2_candidate_authorized"])

    def test_full_coverage_public_cohort_still_not_trade_authorized(self):
        data = bulk_public_deep_scan(FakeBulkMarket(), fake_screen())
        self.assertTrue(data["synchronized_pair_coverage_complete"])
        self.assertTrue(data["eligible_cohort"]["population_coverage_complete"])
        self.assertFalse(data["eligible_cohort"]["partial_population"])
        self.assertEqual(data["eligible_cohort"]["eligible_pair_count"], 20)
        self.assertFalse(data["eligible_cohort"]["source_trust_verified"])
        self.assertFalse(data["eligible_cohort"]["g2_candidate_authorized"])
        self.assertFalse(data["trade_authorized"])

    def test_cli_returns_public_partial_cohort_and_persistent_reject(self):
        output = StringIO()
        with TemporaryDirectory() as td:
            with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner:
                scanner.return_value.scan.return_value = fake_screen()
                with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                           return_value=EightInvalid()):
                    with redirect_stdout(output):
                        code = main(["--bulk-deep-once", "--db", str(Path(td) / "safe.sqlite3")])
        parsed = json.loads(output.getvalue())
        self.assertEqual(code, 0)  # Diagnostic reported, NOT strategy pass.
        self.assertEqual(parsed["bulk_probe_status"], "PUBLIC_BULK_INCOMPLETE_UNATTESTED")
        self.assertEqual(parsed["public_bulk"]["eligible_cohort"]["eligible_pair_count"], 12)
        self.assertEqual(parsed["final_decision"], "REJECT")
        self.assertFalse(parsed["execution_allowed"])
        self.assertFalse(parsed["live_trading_authorized"])


if __name__ == "__main__":
    unittest.main()
