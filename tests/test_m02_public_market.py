"""M02 targeted tests for official public payloads, complete candles and mainpath safe reject."""
from __future__ import annotations

from decimal import Decimal
from contextlib import redirect_stdout
from io import StringIO
import json
from unittest.mock import patch
from indodax_ai_bot.__main__ import main
from pathlib import Path
import tempfile
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.public_market import IndodaxPublicMarket, PublicDataError


class OfficialPublicFixtures:
    def __init__(self):
        self.now = 1_800_000_062
        self.paths: list[str] = []
        self.candles = []
        for start in range((self.now // 60) * 60 - 34 * 60, (self.now // 60) * 60, 60):
            self.candles.append({"Time": start, "Open": "100", "High": "101", "Low": "99", "Close": "100", "Volume": "0" if start % 300 == 0 else "2"})
        fifteen_start = ((self.now // 900) - 1) * 900
        self.fifteen = [{"Time": fifteen_start, "Open": "100", "High": "101", "Low": "99", "Close": "100", "Volume": "0"}]
        self.fail_path = ""

    def fetch(self, path):
        self.paths.append(path)
        if self.fail_path and self.fail_path in path:
            raise OSError("fixture public outage")
        if path == "/api/pairs":
            return [{"id": "btcidr", "ticker_id": "btc_idr", "symbol": "BTCIDR", "base_currency": "idr", "traded_currency": "btc"},
                    {"id": "badidr", "ticker_id": "bad_idr", "symbol": "BADIDR", "base_currency": "idr", "is_market_suspended": 1},
                    {"id": "ethusdt", "ticker_id": "eth_usdt", "symbol": "ETHUSDT", "base_currency": "usdt"}]
        if path == "/api/server_time":
            return {"timezone": "UTC", "server_time": self.now * 1000}
        if path == "/api/ticker/btcidr":
            return {"ticker": {"buy": "100", "sell": "101", "server_time": self.now - 1}}
        if path.startswith("/tradingview/history_v2?"):
            if "tf=1&" in path or path.endswith("tf=1"):
                return self.candles
            if "tf=15&" in path or path.endswith("tf=15"):
                return self.fifteen
            raise AssertionError(path)
        if path == "/api/trades/btcidr":
            return [{"date": str(self.now - 3), "price": "100", "amount": "0.1", "tid": "1", "type": "buy"}]
        if path == "/api/depth/btcidr":
            return {"buy": [["100", "2"]], "sell": [["101", "3"]]}
        raise AssertionError("Unexpected public endpoint " + path)


class M02PublicTests(unittest.TestCase):
    def setUp(self):
        self.f = OfficialPublicFixtures()
        self.client = IndodaxPublicMarket(fetch=self.f.fetch, clock=lambda: self.f.now)

    def test_official_pair_mapping_id_vs_ticker_and_symbols(self):
        self.assertEqual(self.client.idr_pairs(), {"btc_idr": ("btcidr", "BTCIDR")})
        self.assertEqual(self.f.paths, ["/api/pairs"])

    def test_valid_closed_1m_native_15m_and_aggregated_real_5m(self):
        obs = self.client.observe("btc_idr")
        self.assertEqual(obs.candle_5m.interval, 300)
        self.assertEqual(obs.candle_5m.volume, Decimal("8"))  # one genuine zero-volume minute
        self.assertEqual(obs.candle_15m.interval, 900)
        self.assertEqual(obs.candle_1m.start % 60, 0)
        self.assertLessEqual(obs.candle_1m.start + 60, self.f.now)
        self.assertFalse(obs.snapshot.source.verified)
        self.assertFalse(obs.ready)
        self.assertFalse(obs.trade_history_complete)
        self.assertEqual(obs.trade_count_visible, 1)
        self.assertEqual(obs.snapshot.best_ask_idr, Decimal("101"))
        self.assertEqual(len([p for p in self.f.paths if "history_v2" in p]), 2)
        self.assertFalse(any("/private" in p or "trade_api" in p for p in self.f.paths))

    def test_partial_live_candle_is_discarded(self):
        current = (self.f.now // 60) * 60
        self.f.candles.append({"Time": current, "Open": "900", "High": "900", "Low": "900", "Close": "900", "Volume": "12"})
        obs = self.client.observe()
        self.assertLess(obs.candle_1m.start, current)

    def test_candle_gaps_rejected_not_faked(self):
        latest = self.f.candles[-1]["Time"]
        self.f.candles = [x for x in self.f.candles if x["Time"] != latest]
        # Next closed minute is still fresh, but no consecutive latest proof; force missing 5m block.
        self.f.candles = self.f.candles[-3:]
        with self.assertRaisesRegex(PublicDataError, "INCOMPLETE_5M"):
            self.client.observe()

    def test_stale_exchange_clock_fail_closed(self):
        self.client = IndodaxPublicMarket(fetch=self.f.fetch, clock=lambda: self.f.now + 61)
        with self.assertRaisesRegex(PublicDataError, "EXCHANGE_CLOCK_SKEW"):
            self.client.observe()

    def test_invalid_pair_fails_before_network(self):
        with self.assertRaisesRegex(PublicDataError, "INVALID_IDR_PAIR"):
            self.client.observe("btc/idr")
        self.assertEqual(self.f.paths, [])

    def test_public_error_fail_closed(self):
        self.f.fail_path = "/api/trades/"
        with self.assertRaisesRegex(PublicDataError, "PUBLIC_REQUEST_FAILED"):
            self.client.observe()

    def test_mainpath_reads_real_source_envelope_but_never_allows_buy(self):
        with tempfile.TemporaryDirectory() as td:
            app = DailyScalpingOrchestrator(Settings.from_env({}), DecisionJournal(Path(td) / "events.sqlite3"))
            result = app.run_public_cycle("m02-public", market=self.client)
            self.assertEqual(result.final_decision.value, "REJECT")
            self.assertEqual(result.reason, "G1_PROVENANCE_NOT_VERIFIED")
            self.assertFalse(result.execution_allowed)
            self.assertEqual(result.market["source"], "INDODAX_PUBLIC_OHLC_V2_UNATTESTED")
            self.assertEqual(app.journal.count(), 1)

    def test_mainpath_fault_journal_is_reject(self):
        self.f.fail_path = "/api/pairs"
        with tempfile.TemporaryDirectory() as td:
            app = DailyScalpingOrchestrator(Settings.from_env({}), DecisionJournal(Path(td) / "events.sqlite3"))
            result = app.run_public_cycle("m02-failure", market=self.client)
            self.assertEqual(result.reason, "PUBLIC_SOURCE_INVALID:PUBLIC_REQUEST_FAILED")
            self.assertIsNone(result.market)
            self.assertFalse(result.execution_allowed)

    def test_public_probe_ok_reports_untrusted_not_live(self):
        with tempfile.TemporaryDirectory() as td:
            output = StringIO()
            with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket", return_value=self.client), patch.dict("os.environ", {"INDODAX_TRADING_ENABLED": "NO", "INDODAX_ALLOW_LIVE_EXECUTION": "NO", "INDODAX_EXECUTION_MODE": "DRY_RUN"}, clear=True), redirect_stdout(output):
                code = main(["--verify-public", "--pair", "btc_idr", "--db", str(Path(td) / "probe.sqlite3")])
            data = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(data["verified_public_payload_parsed"])
            self.assertEqual(data["public_probe_status"], "PUBLIC_OBSERVED_G1_UNATTESTED")
            self.assertEqual(data["final_decision"], "REJECT")
            self.assertFalse(data["execution_allowed"])
            self.assertFalse(data["live_trading_authorized"])

    def test_public_probe_error_is_nonzero_and_not_live(self):
        self.f.fail_path = "/api/pairs"
        with tempfile.TemporaryDirectory() as td:
            output = StringIO()
            with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket", return_value=self.client), patch.dict("os.environ", {"INDODAX_TRADING_ENABLED": "NO", "INDODAX_ALLOW_LIVE_EXECUTION": "NO", "INDODAX_EXECUTION_MODE": "DRY_RUN"}, clear=True), redirect_stdout(output):
                code = main(["--verify-public", "--pair", "btc_idr", "--db", str(Path(td) / "probe.sqlite3")])
            data = json.loads(output.getvalue())
            self.assertEqual(code, 3)
            self.assertFalse(data["verified_public_payload_parsed"])
            self.assertEqual(data["public_probe_status"], "PUBLIC_SOURCE_ERROR")
            self.assertIsNone(data["public_market"])
            self.assertFalse(data["execution_allowed"])
            self.assertFalse(data["live_trading_authorized"])

if __name__ == "__main__":
    unittest.main()
