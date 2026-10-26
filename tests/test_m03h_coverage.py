"""M03-H: transparent PUBLIC bulk rejection diagnostics, no extra requests/order."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.deep_analysis import bulk_public_deep_scan
from indodax_ai_bot.public_market import IndodaxPublicMarket, PublicDataError
from indodax_ai_bot.scanner import DailyPairScanner

from test_m03a_scanner import FakeIndodaxAll
from test_m03b_deep import observation
from test_m03g_bulk import FakeBulkMarket, fake_screen


class EightInvalid(FakeBulkMarket):
    def observe(self, pair, *, scan_context=None):
        self.calls.append((pair, scan_context))
        bad = {f"token{i:03d}_idr" for i in range(11, 19)}
        return observation(pair, no_trade=(pair in bad))


class SourceError(FakeBulkMarket):
    def observe(self, pair, *, scan_context=None):
        self.calls.append((pair, scan_context))
        if pair == "token011_idr":
            raise PublicDataError("OHLC_ENDPOINT_TEMPORARILY_UNAVAILABLE")
        return observation(pair)


class M03HCoverageTests(unittest.TestCase):
    def test_eight_unqualified_pairs_report_all_reasons_not_just_top10(self):
        result = bulk_public_deep_scan(EightInvalid(), fake_screen(), limit=20)
        self.assertEqual(result["attempted_count"], 20)
        self.assertEqual(result["analyzed_count"], 20)
        self.assertEqual(result["candidate_valid_count"], 12)
        self.assertEqual(result["candidate_rejected_count"], 8)
        self.assertEqual(result["coverage_status"], "PARTIAL_INVALID_PAIR_EVIDENCE")
        self.assertEqual(len(result["candidate_rejection_reasons"]), 8)
        self.assertEqual(result["candidate_rejection_reason_counts"], {"NO_RECENT_TRADE_SAMPLE": 8})
        self.assertEqual(result["not_attempted_pairs"], [])
        self.assertEqual(result["attempted_without_result_count"], 0)
        self.assertEqual(result["same_window"]["ranked_same_window_diagnostics"], [])
        self.assertEqual(result["same_window"]["provisional_watchlist_preview"], [])
        self.assertFalse(result["synchronized_pair_coverage_complete"])
        self.assertFalse(result["g2_candidate_authorized"])
        self.assertFalse(result["trade_authorized"])

    def test_source_http_fault_not_misreported_as_volume_deficiency(self):
        result = bulk_public_deep_scan(SourceError(), fake_screen())
        self.assertEqual(result["candidate_rejection_reason_counts"],
                         {"OHLC_ENDPOINT_TEMPORARILY_UNAVAILABLE": 1})
        self.assertEqual(result["candidate_rejection_reasons"][0], {
            "pair": "token011_idr", "state": "REJECT_SOURCE_ERROR",
            "reason": "OHLC_ENDPOINT_TEMPORARILY_UNAVAILABLE",
        })
        self.assertEqual(result["coverage_status"], "PARTIAL_INVALID_PAIR_EVIDENCE")

    def test_expired_window_reports_unattempted_and_unfinished_distinctly(self):
        result = bulk_public_deep_scan(FakeBulkMarket(expire_after=2), fake_screen())
        self.assertEqual(result["coverage_status"], "TIME_EXPIRED_OR_TRUNCATED")
        self.assertEqual(result["attempted_count"], 2)
        self.assertEqual(result["attempted_without_result_count"], 1)
        self.assertEqual(len(result["not_attempted_pairs"]), 18)
        self.assertTrue(result["window_expired"])
        self.assertFalse(result["synchronized_pair_coverage_complete"])
        self.assertEqual(result["candidate_rejection_reasons"], [])

    def test_full_diagnostic_coverage_still_untrusted_and_never_trades(self):
        result = bulk_public_deep_scan(FakeBulkMarket(), fake_screen())
        self.assertEqual(result["coverage_status"], "ALL_SELECTED_ANALYZED_UNATTESTED")
        self.assertEqual(result["candidate_rejection_reasons"], [])
        self.assertTrue(result["synchronized_pair_coverage_complete"])
        self.assertIsNone(result["public_get_attempt_count_this_client"])
        self.assertFalse(result["same_window"]["g2_candidate_authorized"])
        self.assertFalse(result["full_universe_deep_verified"])
        self.assertFalse(result["execution_allowed"])

    def test_public_request_counter_counts_shared_scan_and_actual_attempts(self):
        f = FakeIndodaxAll()
        paths = []
        def fetch(path):
            paths.append(path)
            if path.startswith("/tradingview/history_v2?"):
                return []
            return f.fetch(path)
        market = IndodaxPublicMarket(fetch=fetch, clock=lambda: f.now)
        scan = DailyPairScanner(market).scan()
        self.assertEqual(market.public_get_attempt_count, 3)
        with self.assertRaisesRegex(PublicDataError, "MISSING_CLOSED_MULTITIMEFRAME"):
            market.observe("m000_idr", scan_context=scan.scan_context)
        self.assertEqual(market.public_get_attempt_count, 5)
        self.assertEqual(len(paths), 5)
        self.assertFalse(any(p.startswith("/api/ticker/") for p in paths))

    def test_cli_incomplete_bulk_status_distinct_from_successful_payload_parse(self):
        with TemporaryDirectory() as td:
            buf = StringIO()
            with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner:
                scanner.return_value.scan.return_value = fake_screen()
                with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                           return_value=EightInvalid()):
                    with redirect_stdout(buf):
                        code = main([
                            "--bulk-deep-once", "--db", str(Path(td) / "journal.sqlite3")
                        ])
        output = json.loads(buf.getvalue())
        self.assertEqual(code, 0)  # Probe executed, not a BUY authorization!
        self.assertEqual(output["bulk_probe_status"], "PUBLIC_BULK_INCOMPLETE_UNATTESTED")
        self.assertEqual(output["public_bulk"]["candidate_rejected_count"], 8)
        self.assertEqual(output["final_decision"], "REJECT")
        self.assertFalse(output["execution_allowed"])
        self.assertFalse(output["live_trading_authorized"])


if __name__ == "__main__":
    unittest.main()
