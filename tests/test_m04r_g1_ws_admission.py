"""M04-R: attach BTC WS gap/coverage to the existing G1 source admission.

All fixtures are public-only and synthetic. No full tape, issuer, paid AI or
order may be inferred from a passing unit test.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.source_coverage import assess_public_ws_btc_for_g1
from test_m03g_bulk import FakeBulkMarket, fake_screen
from test_m04q_ws_gap_minute_source import MINUTE, TARGET, make_gap_journal


class M04RExistingG1WSAdmissionTests(unittest.TestCase):
    def test_historic_two_gaps_remain_visible_when_minute_is_empty(self):
        result = assess_public_ws_btc_for_g1(
            {"status": "PUBLIC_SAMPLE_EMPTY",
             "closed_1m_epoch": TARGET,
             "reason": "FULL_MINUTE_TRADE_TAPE_NOT_PROVEN",
             "unrecoverable_gap_rebaseline_count": 2},
            {"status": "PUBLIC_VISIBLE_SAMPLES_MATCH_UNATTESTED"},
        )
        self.assertEqual(result["known_unrecoverable_ws_discontinuities"], 2)
        self.assertEqual(result["reason"], "HISTORIC_WS_GAP_AND_TRADE_TAPE_UNATTESTED")
        self.assertTrue(result["rest_ws_comparable_public_minute"])
        self.assertFalse(result["full_closed_minute_trade_coverage_proven"])
        self.assertFalse(result["g1_receipt_issuance_allowed"])
        self.assertFalse(result["g2_provider_call_allowed"])
        self.assertFalse(result["execution_allowed"])

    def test_no_historic_gap_still_cannot_prove_source_or_call_ai(self):
        result = assess_public_ws_btc_for_g1(
            {"status": "PUBLIC_SAMPLE_OBSERVED",
             "closed_1m_epoch": TARGET,
             "reason": "FULL_MINUTE_TRADE_TAPE_NOT_PROVEN",
             "unrecoverable_gap_rebaseline_count": 0,
             "g1_receipt_issuance_allowed": True},
            {"status": "PUBLIC_VISIBLE_SAMPLES_MATCH_UNATTESTED"},
        )
        self.assertEqual(result["reason"], "PUBLIC_WS_TRADE_TAPE_AND_ORIGIN_UNATTESTED")
        self.assertFalse(result["independent_exchange_origin_verified"])
        self.assertFalse(result["g1_receipt_issuance_allowed"])
        self.assertFalse(result["g2_provider_call_allowed"])

    def test_crossgap_minute_rejects_before_g1_even_when_observations_exist(self):
        with TemporaryDirectory() as td:
            wsdb = Path(td) / "btc.sqlite3"
            make_gap_journal(wsdb, marker_time=TARGET - 20)
            output = StringIO()
            with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner:
                scanner.return_value.scan.return_value = fake_screen()
                with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                           return_value=FakeBulkMarket()):
                    with patch("indodax_ai_bot.orchestrator.time.time",
                               return_value=MINUTE + 90):
                        with redirect_stdout(output):
                            rc = main([
                                "--bulk-deep-once", "--ws-db", str(wsdb),
                                "--db", str(Path(td) / "decision.sqlite3"),
                            ])
            self.assertEqual(rc, 0)
            data = json.loads(output.getvalue())
            admission = data["public_bulk"]["g1_source_admission"]
            self.assertEqual(admission["status"], "REJECT_SOURCE_NOT_QUALIFIED")
            quality = admission["btc_ws_source_quality"]
            self.assertEqual(quality["reason"], "RECORDED_GAP_IN_BTC_CLOSED_MINUTE")
            self.assertEqual(quality["known_unrecoverable_ws_discontinuities"], 1)
            self.assertTrue(quality["current_minute_recorded_gap"])
            self.assertFalse(quality["g1_receipt_issuance_allowed"])
            self.assertFalse(data["public_bulk"]["g1_verified"])
            self.assertEqual(data["ai_shadow"]["completed_roles"], [])
            self.assertIsNone(data["final_decision"])
            self.assertFalse(data["execution_allowed"])

    def test_missing_ws_file_still_reports_specific_g1_source_rejection(self):
        with TemporaryDirectory() as td:
            missing = Path(td) / "missing.sqlite3"
            output = StringIO()
            with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner:
                scanner.return_value.scan.return_value = fake_screen()
                with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                           return_value=FakeBulkMarket()):
                    with redirect_stdout(output):
                        rc = main([
                            "--bulk-deep-once", "--ws-db", str(missing),
                            "--db", str(Path(td) / "decision.sqlite3"),
                        ])
            self.assertEqual(rc, 0)
            data = json.loads(output.getvalue())
            quality = data["public_bulk"]["g1_source_admission"]["btc_ws_source_quality"]
            self.assertEqual(quality["reason"], "NO_VALID_BTC_WS_CLOSED_MINUTE")
            self.assertIsNone(quality["known_unrecoverable_ws_discontinuities"])
            self.assertFalse(missing.exists())
            self.assertIsNone(data["final_decision"])
            self.assertEqual(data["ai_shadow"]["completed_roles"], [])


if __name__ == "__main__":
    unittest.main()
