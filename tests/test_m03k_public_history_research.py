"""M03-K scoped public source-backed OHLC time split, no real API in tests.

The live read-only probe is a separate owner VPS action, only after tests.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.deep_analysis import evaluate_source_backed_candle_research
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.public_market import (
    IndodaxPublicMarket, PublicDataError,
)


class PublicHistoryFixture:
    now = 1800000120

    def __init__(self, *, omitted=None, exchange_lag=0):
        self.paths = []
        self.omitted = omitted
        self.exchange_lag = exchange_lag

    def fetch(self, path):
        self.paths.append(path)
        if path == "/api/pairs":
            return [{"id": "btcidr", "ticker_id": "btc_idr",
                     "symbol": "BTCIDR", "base_currency": "idr"}]
        if path == "/api/server_time":
            return {"server_time": (self.now-self.exchange_lag)*1000}
        if path.startswith("/tradingview/history_v2?"):
            query = parse_qs(urlparse(path).query)
            assert query["symbol"] == ["BTCIDR"], query
            assert query["tf"] == ["1"], query
            first, last = (int(query[k][0]) for k in ("from","to"))
            result = []
            for i, stamp in enumerate(range(first, last, 60)):
                if i == self.omitted:
                    continue
                price = Decimal("100") + Decimal(i)/Decimal("100")
                volume = "40" if i % 17 == 0 else "10"
                result.append({
                    "Time": stamp,
                    "Open": str(price),
                    "High": str(price+Decimal("2")),
                    "Low": str(price-Decimal("2")),
                    "Close": str(price+Decimal("0.2")),
                    "Volume": volume,
                })
            return result
        raise AssertionError("Unexpected HTTP endpoint "+path)

    def market(self):
        return IndodaxPublicMarket(fetch=self.fetch, clock=lambda: self.now)


class M03KPublicHistoryTests(TestCase):
    def test_exact_three_public_gets_contiguous_closed_only_no_full_tape(self):
        api=PublicHistoryFixture()
        hist=api.market().closed_1m_research_history(minutes=180)
        self.assertEqual(len(api.paths),3)
        self.assertEqual(api.paths[:2],["/api/pairs","/api/server_time"])
        self.assertTrue(api.paths[2].startswith("/tradingview/history_v2?"))
        self.assertEqual(len(hist.candles),180)
        self.assertEqual(hist.candles[0].start,hist.from_epoch)
        self.assertEqual(hist.candles[-1].start+60,hist.to_epoch)
        self.assertFalse(hist.source_trust_verified)
        self.assertFalse(hist.trade_history_complete)
        self.assertFalse(any("/trade_api" in x or "/api/trades/" in x
                             for x in api.paths))

    def test_time_split_train_holdout_and_embargo_are_descriptive(self):
        hist=PublicHistoryFixture().market().closed_1m_research_history()
        result=evaluate_source_backed_candle_research(hist)
        self.assertEqual(result["status"],"HOLDOUT_DESCRIPTIVE_ONLY_NOT_VALIDATED")
        self.assertEqual(len(result["source_digest_sha256"]),64)
        self.assertGreater(result["train"]["decision_windows"],0)
        self.assertGreater(result["holdout"]["decision_windows"],0)
        self.assertEqual(result["purged_boundary_windows"],3)
        self.assertEqual(result["closed_1m_rows_visible"],180)
        self.assertFalse(result["g1_verified"])
        self.assertFalse(result["net_roi_verified"])
        self.assertFalse(result["out_of_sample_validated"])
        self.assertFalse(result["execution_allowed"])

    def test_gap_one_closed_candle_rejects_no_synthetic_fill(self):
        api=PublicHistoryFixture(omitted=47)
        with self.assertRaisesRegex(PublicDataError,
                                    "RESEARCH_HISTORY_1M_INCOMPLETE"):
            api.market().closed_1m_research_history()
        self.assertEqual(len(api.paths),3)

    def test_clock_skew_rejected_before_ohlc_and_bad_param_before_any_get(self):
        api=PublicHistoryFixture(exchange_lag=61)
        with self.assertRaisesRegex(PublicDataError,"EXCHANGE_CLOCK_SKEW"):
            api.market().closed_1m_research_history()
        self.assertEqual(api.paths,["/api/pairs","/api/server_time"])
        api=PublicHistoryFixture()
        with self.assertRaisesRegex(PublicDataError,"INVALID_RESEARCH_HISTORY_MINUTES"):
            api.market().closed_1m_research_history(minutes=361)
        with self.assertRaisesRegex(PublicDataError,"INVALID_IDR_PAIR"):
            api.market().closed_1m_research_history(pair="btc/usdt")
        self.assertEqual(api.paths,[])

    def test_future_last_close_changes_outcome_not_prior_signal_features(self):
        hist=PublicHistoryFixture().market().closed_1m_research_history()
        original=evaluate_source_backed_candle_research(hist)
        rows=list(hist.candles)
        # Only last three forward returns see the final bar; labels for every
        # eligible decision are computed at least three bars before this bar.
        rows[-1]=replace(rows[-1], close=Decimal("190"), high=Decimal("195"))
        changed=evaluate_source_backed_candle_research(
            replace(hist,candles=tuple(rows))
        )
        self.assertNotEqual(original["source_digest_sha256"],
                            changed["source_digest_sha256"])
        self.assertEqual(original["train"],changed["train"])
        self.assertEqual(
            {k:v["count"] for k,v in original["holdout"]["labels"].items()},
            {k:v["count"] for k,v in changed["holdout"]["labels"].items()},
        )
        self.assertNotEqual(
            original["holdout"]["all_windows_mean_forward_3m_gross_bps"],
            changed["holdout"]["all_windows_mean_forward_3m_gross_bps"],
        )

    def test_forged_trust_flags_and_modified_window_fail_closed(self):
        hist=PublicHistoryFixture().market().closed_1m_research_history()
        with self.assertRaisesRegex(PublicDataError,"INVALID_RESEARCH_SOURCE"):
            evaluate_source_backed_candle_research(replace(hist,trade_history_complete=True))
        with self.assertRaisesRegex(PublicDataError,"INVALID_RESEARCH_CANDLE_WINDOW"):
            evaluate_source_backed_candle_research(replace(hist,to_epoch=hist.to_epoch+60))

    def test_existing_orchestrator_records_reject_with_research_report(self):
        with TemporaryDirectory() as td:
            j=DecisionJournal(Path(td)/"jr.sqlite3")
            app=DailyScalpingOrchestrator(Settings.from_env({}),j)
            report=app.run_public_history_research_cycle(
                "m03k-trace",market=PublicHistoryFixture().market()
            )
            self.assertEqual(report.final_decision.value,"REJECT")
            self.assertEqual(report.reason,"PUBLIC_OHLC_RESEARCH_UNATTESTED_NO_TRADE")
            self.assertFalse(report.execution_allowed)
            self.assertEqual(report.public_research["train"]["decision_windows"]>0,True)
            self.assertEqual(j.count(),1)

    def test_real_cli_flag_reuses_orchestrator_no_provider_no_order(self):
        with TemporaryDirectory() as td:
            output=StringIO()
            with (patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                        return_value=PublicHistoryFixture().market()),
                  patch.dict("os.environ",{
                      "INDODAX_TRADING_ENABLED":"NO",
                      "INDODAX_ALLOW_LIVE_EXECUTION":"NO",
                      "INDODAX_EXECUTION_MODE":"DRY_RUN",
                  },clear=True),
                  redirect_stdout(output)):
                code=main(["--public-history-research-once","--pair","btc_idr",
                           "--history-minutes","180",
                           "--db",str(Path(td)/"runtime.sqlite3")])
            data=json.loads(output.getvalue())
            self.assertEqual(code,0)
            self.assertEqual(data["final_decision"],"REJECT")
            self.assertFalse(data["execution_allowed"])
            self.assertFalse(data["live_trading_authorized"])
            self.assertFalse(data["research_is_trade_signal"])
            self.assertEqual(data["public_history_research"]["closed_1m_rows_visible"],180)


if __name__ == "__main__":
    import unittest
    unittest.main()
