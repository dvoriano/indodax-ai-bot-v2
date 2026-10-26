"""M03-G public scan context sharing, 20–40 budget and fail-closed bulk path.

Tests use mock public sources; not proof of real 20–40-pair HTTP latency.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.deep_analysis import bulk_public_deep_scan
from indodax_ai_bot.public_market import (
    IndodaxPublicMarket, PublicDataError, PublicScanContext,
)
from indodax_ai_bot.scanner import DailyPairScanner, ScannerResult, ScreenedPair
from test_m03a_scanner import FakeIndodaxAll
from test_m03b_deep import observation

from decimal import Decimal


def fake_screen(count=20, *, contains_btc=True):
    names = ["btc_idr"] if contains_btc else []
    names += [f"token{i:03d}_idr" for i in range(count - len(names))]
    epoch = 1800000130
    rows = tuple(ScreenedPair(p, Decimal("30000000"), Decimal("100"),
                              Decimal("101"), Decimal("50"), Decimal("5"),
                              epoch-1) for p in names)
    ctx = PublicScanContext(
        observed_epoch=epoch, exchange_epoch=epoch,
        pairs={p: (p.replace("_", ""), p.replace("_", "").upper())
               for p in names + ["btc_idr"]},
        tickers={p: {
            "server_time": epoch-1,
            "buy": "100", "sell": "101", "last": "100",
            "high": "110", "low": "90",
        } for p in names + ["btc_idr"]},
    )
    return ScannerResult(epoch, 398, 300, 98, rows, rows[:2],
                         scan_context=ctx)


class FakeBulkMarket:
    def __init__(self, *, invalid_pair=None, expire_after=None):
        self.calls = []
        self.now = 1800000130
        self.invalid_pair = invalid_pair
        self.expire_after = expire_after
        self._clock = lambda: self.now

    def observe(self, pair, *, scan_context=None):
        self.calls.append((pair, scan_context))
        if self.expire_after is not None and len(self.calls) >= self.expire_after:
            self.now += 31
        if pair == self.invalid_pair:
            return observation(pair, no_trade=True)
        return observation(pair)


class M03GBulkPublicTests(unittest.TestCase):
    def test_scanner_attaches_three_public_response_context(self):
        fake = FakeIndodaxAll()
        scan = DailyPairScanner(fake.client()).scan()
        self.assertIsNotNone(scan.scan_context)
        self.assertEqual(scan.scan_context.observed_epoch, fake.now)
        self.assertEqual(len(scan.scan_context.pairs), 45)
        self.assertIn("m000_idr", scan.scan_context.tickers)
        self.assertEqual(fake.calls, ["/api/pairs", "/api/server_time", "/api/ticker_all"])
        self.assertNotIn("scan_context", scan.summary())

    def test_shared_context_avoids_duplicate_pair_server_and_ticker_requests(self):
        fake = FakeIndodaxAll()
        paths = []
        def fetch(path):
            paths.append(path)
            if path.startswith("/tradingview/history_v2?"):
                return []
            return fake.fetch(path)
        market = IndodaxPublicMarket(fetch=fetch, clock=lambda: fake.now)
        scan = DailyPairScanner(market).scan()
        with self.assertRaisesRegex(PublicDataError, "MISSING_CLOSED_MULTITIMEFRAME"):
            market.observe("m000_idr", scan_context=scan.scan_context)
        self.assertEqual(
            paths[:3], ["/api/pairs", "/api/server_time", "/api/ticker_all"])
        self.assertEqual(sum(p.startswith("/tradingview/history_v2?") for p in paths), 2)
        self.assertEqual(len(paths), 5)
        self.assertFalse(any(p.startswith("/api/ticker/") for p in paths))

    def test_expired_scan_context_fails_before_any_http(self):
        fake = FakeIndodaxAll()
        first = DailyPairScanner(fake.client()).scan()
        requests = []
        market = IndodaxPublicMarket(fetch=lambda p: requests.append(p),
                                     clock=lambda: fake.now + 31)
        with self.assertRaisesRegex(PublicDataError, "EXPIRED_PUBLIC_SCAN_CONTEXT"):
            market.observe("m000_idr", scan_context=first.scan_context)
        self.assertEqual(requests, [])

    def test_twenty_pairs_bounded_and_aligned_still_not_authorized(self):
        scan = fake_screen()
        market = FakeBulkMarket()
        result = bulk_public_deep_scan(market, scan, limit=20)
        self.assertEqual(result["attempted_count"], 20)
        self.assertEqual(result["analyzed_count"], 20)
        self.assertEqual(result["estimated_requests_including_discovery"], 83)
        self.assertEqual(result["same_window"]["status"], "ALIGNED_PUBLIC_UNATTESTED")
        self.assertEqual(len(result["same_window"]["ranked_same_window_diagnostics"]), 20)
        self.assertFalse(result["full_universe_deep_verified"])
        self.assertFalse(result["g2_candidate_authorized"])
        self.assertFalse(result["trade_authorized"])
        self.assertTrue(all(c is scan.scan_context for _, c in market.calls))

    def test_40_pairs_with_btc_outside_need_167_requests_with_headroom(self):
        scan = fake_screen(40, contains_btc=False)
        market = FakeBulkMarket()
        result = bulk_public_deep_scan(market, scan, limit=40)
        self.assertEqual(result["estimated_requests_including_discovery"], 167)
        self.assertEqual(len(market.calls), 41)
        self.assertEqual(market.calls[-1][0], "btc_idr")
        self.assertEqual(result["same_window"]["status"], "ALIGNED_PUBLIC_UNATTESTED")
        self.assertFalse(result["g1_verified"])

    def test_fails_closed_when_one_of_twenty_candles_has_no_trade_evidence(self):
        scan = fake_screen()
        bad = scan.shortlist[10].pair
        result = bulk_public_deep_scan(FakeBulkMarket(invalid_pair=bad), scan)
        self.assertEqual(result["candidate_rejected_count"], 1)
        self.assertEqual(result["same_window"]["status"],
                         "REJECT_INCOMPLETE_OR_EXPIRED_BULK_COVERAGE")
        self.assertEqual(result["same_window"]["ranked_same_window_diagnostics"], [])
        self.assertEqual(result["same_window"]["provisional_watchlist_preview"], [])
        self.assertFalse(result["trade_authorized"])

    def test_expiry_stops_remaining_queries_and_clears_rankings(self):
        result = bulk_public_deep_scan(FakeBulkMarket(expire_after=2), fake_screen())
        self.assertTrue(result["window_expired"])
        self.assertLess(result["attempted_count"], 20)
        self.assertEqual(result["same_window"]["provisional_watchlist_preview"], [])
        self.assertFalse(result["execution_allowed"])

    def test_invalid_bulk_count_and_missing_context_fail_closed(self):
        scan = fake_screen(40)
        m = FakeBulkMarket()
        for size in (19, 41, True):
            with self.subTest(size=size):
                with self.assertRaises(PublicDataError):
                    bulk_public_deep_scan(m, scan, limit=size)
        with self.assertRaisesRegex(PublicDataError, "BULK_REQUIRES_SHARED"):
            bulk_public_deep_scan(m, replace(scan, scan_context=None))
        with self.assertRaisesRegex(PublicDataError, "BULK_SHORTLIST_BELOW"):
            bulk_public_deep_scan(m, fake_screen(19), limit=20)

    def test_cli_same_orchestrator_always_journals_reject(self):
        with TemporaryDirectory() as td:
            output = StringIO()
            with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner:
                scanner.return_value.scan.return_value = fake_screen()
                with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                           return_value=FakeBulkMarket()):
                    with redirect_stdout(output):
                        rc = main([
                            "--bulk-deep-once", "--db", str(Path(td) / "safe.sqlite3")
                        ])
            printed = json.loads(output.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(printed["final_decision"], "REJECT")
            self.assertFalse(printed["execution_allowed"])
            self.assertFalse(printed["live_trading_authorized"])
            self.assertEqual(printed["bulk_probe_status"], "PUBLIC_BULK_UNATTESTED")
            self.assertFalse(printed["public_bulk"]["trade_authorized"])


if __name__ == "__main__":
    unittest.main()
