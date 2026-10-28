"""M03-N to M04: 60-minute historical outcomes reach the ORIGINAL deep path.

No separate scanner or model caller; PUBLIC source, Final REJECT, and
trade-authorized=False are preserved. Gross ticker endpoints are not
filled-position net ROI or a prediction of an executable exit.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from test_m03b_deep import MockMarket, screening


PATTERN = "RISING_2_5__ACCELERATING__TIGHT_SPREAD"


class M03NM04ScalpExperienceWiringTest(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.j=DecisionJournal(Path(self.tmp.name)/"journal.sqlite3")
        self.app=DailyScalpingOrchestrator(Settings.from_env({}),self.j)
        base=screening()
        self.scan=replace(base, universe=base.shortlist)
        self.reports={
            "btc_idr": {
                "status":"EARLY_WATCH_UNCALIBRATED",
                "return_1m_pct":"0.5",
                "return_5m_pct":"2.5",
                "acceleration_current_5m_vs_prior_5m_pct_points":"1.0",
                "consecutive_minute_samples":11,
            },
            "eth_idr":{"status":"OBSERVED_NO_ACCELERATION"},
            "sol_idr":{"status":"OBSERVED_NO_ACCELERATION"},
        }

    def invoke(self, casebook):
        with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner:
            scanner.return_value.scan.return_value=self.scan
            with patch.object(
                self.j,"record_and_assess_public_ticker_momentum",
                return_value=self.reports,
            ) as measured:
                with patch.object(
                    self.j,"public_surge_casebook_summary",
                    return_value=casebook,
                ) as summary:
                    result=self.app.run_deep_cycle(
                        "net-g1-provenance-guard", market=MockMarket(),
                    )
                    self.assertEqual(measured.call_count,1)
                    self.assertEqual(summary.call_count,1)
                    return result

    def test_60m_pattern_reaches_existing_deep_evidence_no_trade(self):
        casebook={
            "status":"DESCRIPTIVE_EXPERIENCE_NOT_PREDICTIVE",
            "casebook_rows":4,
            "pattern_library":{
                PATTERN:{
                    "matured_60m":3,
                    "up_2pct_60m":1,
                    "up_8pct_60m":0,
                    "up_11pct_60m":0,
                    "down_2pct_60m":1,
                    "mean_return_60m_pct":"-0.4000",
                    "scalp_60m_descriptive_status":
                        "OBSERVED_ENDPOINT_HISTORY_NOT_TRADE_VALIDATED",
                    "matured_24h":0,
                },
            },
        }
        res=self.invoke(casebook)
        self.assertEqual(res.final_decision.value,"REJECT")
        self.assertFalse(res.execution_allowed)
        self.assertEqual(res.deep["deep_examined_count"],2)
        btc=next(
            x for x in res.deep["ranked_diagnostics"]
            if x["pair"]=="btc_idr"
        )
        context=btc["surge_pattern"]
        self.assertEqual(context["historical_matured_60m_cases"],3)
        self.assertEqual(context["historical_up_2pct_gross_60m_cases"],1)
        self.assertEqual(context["historical_up_8pct_gross_60m_cases"],0)
        self.assertEqual(context["historical_down_2pct_gross_60m_cases"],1)
        self.assertEqual(context["historical_mean_return_60m_gross_pct"],"-0.4000")
        self.assertTrue(context["historical_60m_endpoint_not_executable_exit"])
        self.assertEqual(context["historical_matured_24h_cases"],0)
        self.assertFalse(context["predictive_model_validated"])
        self.assertFalse(context["buy_authorized"])
        self.assertEqual(btc["surge_experience_context"],casebook)
        self.assertFalse(btc["trade_authorized"])
        self.assertFalse(btc["g1_verified"])

    def test_no_mature_outcome_cannot_fake_positive_60m_history(self):
        res=self.invoke({
            "status":"DESCRIPTIVE_EXPERIENCE_NOT_PREDICTIVE",
            "casebook_rows":1,
            "pattern_library":{},
        })
        self.assertFalse(res.execution_allowed)
        btc=next(x for x in res.deep["ranked_diagnostics"]
                 if x["pair"]=="btc_idr")
        history=btc["surge_pattern"]
        self.assertEqual(history["historical_matured_60m_cases"],0)
        self.assertEqual(history["historical_up_2pct_gross_60m_cases"],0)
        self.assertIsNone(history["historical_mean_return_60m_gross_pct"])
        self.assertEqual(history["historical_60m_status"],"NO_MATCHED_60M_HISTORY")
        self.assertFalse(history["buy_authorized"])


if __name__=="__main__":
    unittest.main()
