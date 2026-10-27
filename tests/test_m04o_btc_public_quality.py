"""M04-O: PUBLIC BTC REST+WS same-minute observability in existing G1.

Unattested, nonexhaustive partial views can support diagnostics only, NEVER
certify source origin, full minute coverage, model calls or an order.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.orchestrator import _btc_rest_ws_observability
from indodax_ai_bot.deep_analysis import bulk_public_deep_scan
from test_m03g_bulk import FakeBulkMarket, fake_screen
from test_m04h_closed_minute_join import MINUTE, prepared_rest_matched_journal


def rest():
    return {
        "pair":"btc_idr",
        "closed_1m_start_epoch":MINUTE-60,
        "closed_1m_end_epoch":MINUTE,
        "visible_trade_rows":2,
        "visible_buy_idr":"1000",
        "visible_sell_idr":"2500",
        "g1_receipt_issuance_allowed":False,
    }


def ws():
    return {
        "status":"PUBLIC_SAMPLE_OBSERVED",
        "pair":"btc_idr",
        "closed_1m_epoch":MINUTE-60,
        "closed_1m_end_epoch":MINUTE,
        "trade_rows_visible_in_minute":2,
        "buy_idr_volume_visible":"1000",
        "sell_idr_volume_visible":"2500",
        "minute_trade_coverage_complete":False,
        "g1_receipt_issuance_allowed":False,
    }


class M04OBTCPublicSourceQualityTests(unittest.TestCase):
    def test_equal_visible_samples_never_upgrade_source_trust(self):
        report=_btc_rest_ws_observability(rest(),ws())
        self.assertEqual(report["status"],"PUBLIC_VISIBLE_SAMPLES_MATCH_UNATTESTED")
        self.assertEqual(report["trade_row_delta_ws_minus_rest"],0)
        self.assertEqual(report["total_idr_delta_ws_minus_rest"],"0")
        self.assertTrue(report["public_samples_match_without_completeness_proof"])
        self.assertTrue(report["observed_values_cannot_establish_complete_coverage"])
        self.assertFalse(report["g1_receipt_issuance_allowed"])
        self.assertFalse(report["g2_provider_call_allowed"])
        self.assertFalse(report["execution_allowed"])

    def test_different_visible_samples_report_deltas_not_complete_tape(self):
        a=rest()
        b=ws()
        a["visible_trade_rows"]=6
        a["visible_buy_idr"]="350"
        a["visible_sell_idr"]="100"
        report=_btc_rest_ws_observability(a,b)
        self.assertEqual(report["status"],"PUBLIC_VISIBLE_SAMPLES_DIFFER_UNATTESTED")
        self.assertEqual(report["trade_row_delta_ws_minus_rest"],-4)
        self.assertEqual(report["buy_idr_delta_ws_minus_rest"],"650")
        self.assertEqual(report["sell_idr_delta_ws_minus_rest"],"2400")
        self.assertEqual(report["total_idr_delta_ws_minus_rest"],"3050")
        self.assertFalse(report["public_samples_match_without_completeness_proof"])
        self.assertFalse(report["execution_allowed"])

    def test_different_minute_or_forged_auth_rejected(self):
        for change in (
            {"closed_1m_epoch":MINUTE},
            {"minute_trade_coverage_complete":True},
            {"g1_receipt_issuance_allowed":True},
        ):
            b={**ws(),**change}
            with self.subTest(change=change):
                report=_btc_rest_ws_observability(rest(),b)
                self.assertEqual(report["status"],"REJECT")
                self.assertEqual(report["reason"],"REST_WS_CLOSED_MINUTE_OR_TRUST_MISMATCH")
                self.assertFalse(report["g2_provider_call_allowed"])

    def test_missing_ws_and_corrupt_flows_fail_closed(self):
        self.assertEqual(
            _btc_rest_ws_observability(rest(),None)["reason"],
            "NO_VALID_SAME_MINUTE_WS_SAMPLE",
        )
        self.assertEqual(
            _btc_rest_ws_observability(None,ws())["reason"],
            "NO_ACTUAL_BTC_REST_MINUTE_SAMPLE",
        )
        b=ws()
        b["sell_idr_volume_visible"]="NaN"
        self.assertEqual(_btc_rest_ws_observability(rest(),b)["reason"],
                         "INVALID_PUBLIC_REST_WS_FLOW")

    def test_existing_bulk_retains_actual_rest_count_and_flow_no_extra_http(self):
        market=FakeBulkMarket()
        result=bulk_public_deep_scan(market,fake_screen())
        rest_result=result["btc_public_rest_visible_minute_sample"]
        self.assertEqual(rest_result["closed_1m_start_epoch"],MINUTE-60)
        self.assertEqual(rest_result["closed_1m_end_epoch"],MINUTE)
        self.assertEqual(rest_result["visible_trade_rows"],6)
        self.assertEqual(rest_result["visible_buy_idr"],"350")
        self.assertEqual(rest_result["visible_sell_idr"],"100")
        self.assertEqual(rest_result["visible_total_idr"],"450")
        self.assertEqual(len(market.calls),20)
        self.assertFalse(result["g1_verified"])
        self.assertFalse(rest_result["execution_allowed"])

    def test_real_mainpath_cli_joins_same_minute_and_still_final_reject(self):
        with TemporaryDirectory() as td:
            ws_path=Path(td)/"ws.sqlite3"
            prepared_rest_matched_journal(ws_path)
            output=StringIO()
            with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner:
                scanner.return_value.scan.return_value=fake_screen()
                with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                           return_value=FakeBulkMarket()):
                    with patch("indodax_ai_bot.orchestrator.time.time",
                               return_value=MINUTE+90):
                        with redirect_stdout(output):
                            rc=main(["--bulk-deep-once",
                                     "--ws-db",str(ws_path),
                                     "--db",str(Path(td)/"decision.sqlite3")])
            obj=json.loads(output.getvalue())
            quality=obj["public_bulk"]["btc_rest_ws_observability"]
            self.assertEqual(rc,0)
            self.assertEqual(quality["status"],
                             "PUBLIC_VISIBLE_SAMPLES_DIFFER_UNATTESTED")
            self.assertEqual(quality["closed_1m_start_epoch"],MINUTE-60)
            self.assertFalse(quality["g1_receipt_issuance_allowed"])
            self.assertFalse(quality["g2_provider_call_allowed"])
            self.assertFalse(quality["execution_allowed"])
            self.assertFalse(obj["public_bulk"]["g1_verified"])
            self.assertEqual(obj["ai_shadow"]["completed_roles"],[])
            self.assertEqual(obj["final_decision"],"REJECT")
            self.assertFalse(obj["execution_allowed"])

if __name__ == "__main__":
    unittest.main()
