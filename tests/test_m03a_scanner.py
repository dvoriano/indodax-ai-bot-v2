"""M03-A: preliminary all-IDR public scanner, not a BUY signal."""
from __future__ import annotations

import json
import os
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.public_market import IndodaxPublicMarket, PublicDataError
from indodax_ai_bot.scanner import DailyPairScanner


class FakeIndodaxAll:
    def __init__(self) -> None:
        self.now = 1800000065
        self.calls = []
        self.tickers = {}
        self.pairs = []
        for n in range(45):
            pair = f"m{n:03d}_idr"
            self.pairs.append({
                "ticker_id": pair, "id": pair.replace("_", ""),
                "symbol": pair.replace("_", "").upper(),
                "base_currency": "idr", "is_market_suspended": 0,
            })
            self.tickers[pair] = {
                "server_time": self.now - 1,
                "buy": "100", "sell": "101", "last": "100",
                "low": "90", "high": "110",
                "vol_idr": str((45 - n) * 1000000),
            }

    def fetch(self, path):
        self.calls.append(path)
        if path == "/api/pairs":
            return self.pairs
        if path == "/api/server_time":
            return {"server_time": self.now * 1000}
        if path == "/api/ticker_all":
            return {"tickers": self.tickers}
        raise AssertionError(f"not an allowed public scanner endpoint: {path}")

    def client(self):
        return IndodaxPublicMarket(fetch=self.fetch, clock=lambda: self.now)


class M03AScannerTests(unittest.TestCase):
    def setUp(self):
        self.f = FakeIndodaxAll()
        self.client = self.f.client()

    def test_scan_all_idr_shortlist_30_and_watchlist_at_most_2(self):
        result = DailyPairScanner(self.client).scan()
        self.assertEqual(result.all_idr_pairs, 45)
        self.assertEqual(result.eligible_pairs, 45)
        self.assertEqual(len(result.shortlist), 30)
        self.assertEqual(len(result.watchlist), 2)
        self.assertEqual(result.shortlist[0].pair, "m000_idr")
        self.assertEqual(result.shortlist[-1].pair, "m029_idr")
        self.assertFalse(result.g1_verified)
        self.assertFalse(result.summary()["trade_authorized"])
        self.assertEqual(self.f.calls, ["/api/pairs", "/api/server_time", "/api/ticker_all"])

    def test_deterministic_rank_equal_metrics_tiebreak_pair_ascending(self):
        for item in self.f.tickers.values():
            item["vol_idr"] = "1000000"
        result = DailyPairScanner(self.client).scan()
        self.assertEqual([c.pair for c in result.watchlist], ["m000_idr", "m001_idr"])

    def test_stale_crossed_zero_volume_and_invalid_payload_skipped(self):
        self.f.tickers["m000_idr"]["server_time"] = self.f.now - 150
        self.f.tickers["m001_idr"]["sell"] = "50"
        self.f.tickers["m002_idr"]["vol_idr"] = "0"
        self.f.tickers["m003_idr"]["last"] = "NaN"
        result = DailyPairScanner(self.client).scan()
        self.assertEqual(result.eligible_pairs, 41)
        self.assertEqual(result.excluded_pairs, 4)
        self.assertEqual(result.watchlist[0].pair, "m004_idr")

    def test_bad_clock_rejects_entire_scan(self):
        client = IndodaxPublicMarket(fetch=self.f.fetch, clock=lambda: self.f.now + 40)
        with self.assertRaisesRegex(PublicDataError, "EXCHANGE_CLOCK_SKEW"):
            DailyPairScanner(client).scan()

    def test_all_missing_volume_fail_closed(self):
        for t in self.f.tickers.values():
            t.pop("vol_idr")
        with self.assertRaisesRegex(PublicDataError, "NO_VALID_SCALPING_PAIRS"):
            DailyPairScanner(self.client).scan()

    def test_invalid_shortlist_size_rejected(self):
        for size in (0, 19, 41, 200):
            with self.assertRaises(ValueError):
                DailyPairScanner(self.client, shortlist_size=size)

    def test_mainpath_never_approves_trade_on_ranked_candidates(self):
        with TemporaryDirectory() as td:
            app = DailyScalpingOrchestrator(Settings.from_env({}), DecisionJournal(Path(td) / "db.sqlite3"))
            result = app.run_scan_cycle("scan-one", market=self.client)
            self.assertEqual(result.final_decision.value, "REJECT")
            self.assertEqual(result.reason, "G1_DEEP_ANALYSIS_AND_RISK_NOT_CONNECTED")
            self.assertFalse(result.execution_allowed)
            self.assertEqual(result.screening["shortlist_count"], 30)
            self.assertFalse(result.screening["g1_issuer_verified"])
            self.assertEqual(app.journal.count(), 1)

    def test_cli_scan_once_exits_success_only_on_public_screen_payload(self):
        with TemporaryDirectory() as td:
            output = StringIO()
            with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket", return_value=self.client):
                with patch.dict(os.environ, {"INDODAX_TRADING_ENABLED": "NO",
                    "INDODAX_ALLOW_LIVE_EXECUTION": "NO", "INDODAX_EXECUTION_MODE": "DRY_RUN"}):
                    with redirect_stdout(output):
                        rc = main(["--scan-once", "--db", str(Path(td) / "db.sqlite3")])
            parsed = json.loads(output.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(parsed["scanner_probe_status"], "PUBLIC_SCREENED_UNATTESTED")
            self.assertFalse(parsed["live_trading_authorized"])
            self.assertEqual(parsed["final_decision"], "REJECT")
            self.assertFalse(parsed["execution_allowed"])

    def test_cli_scan_once_empty_market_is_nonzero(self):
        for t in self.f.tickers.values():
            t.pop("vol_idr")
        with TemporaryDirectory() as td:
            output = StringIO()
            with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket", return_value=self.client):
                with patch.dict(os.environ, {"INDODAX_TRADING_ENABLED": "NO",
                    "INDODAX_ALLOW_LIVE_EXECUTION": "NO", "INDODAX_EXECUTION_MODE": "DRY_RUN"}):
                    with redirect_stdout(output):
                        rc = main(["--scan-once", "--db", str(Path(td) / "db.sqlite3")])
            parsed = json.loads(output.getvalue())
            self.assertEqual(rc, 3)
            self.assertEqual(parsed["scanner_probe_status"], "PUBLIC_SCANNER_ERROR")
            self.assertEqual(parsed["final_decision"], "REJECT")
            self.assertFalse(parsed["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
