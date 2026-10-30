"""M04 public source consistency on the EXISTING CLI→orchestrator→G1 boundary.

Exactly one synthetic HTTP transport boundary. Real public adapter, scanner,
bulk deep G1 source admission, ThreeRoleShadow and DecisionJournal run as-is.
No paid model, private Indodax request, trusted issuer or order.
"""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from decimal import Decimal
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.public_market import Candle, IndodaxPublicMarket
from indodax_ai_bot.source_coverage import public_coverage_report
from test_m03a_scanner import FakeIndodaxAll
from test_m03b_deep import observation

NOW = 1800000130
LAST_1M = (NOW // 60) * 60 - 60
LAST_15M = (NOW // 900) * 900 - 900


def ohlc_row(start):
    return {
        "Time": start, "Open": "100", "High": "101",
        "Low": "99", "Close": "100", "Volume": "10",
    }


def good_observation():
    obs = observation("btc_idr")
    bars = obs.candles_1m
    start5 = (obs.snapshot.source.closed_1m_epoch // 300) * 300 - 300
    start15 = (obs.snapshot.source.closed_1m_epoch // 900) * 900 - 900
    def aggregate(start, interval):
        included = sorted((b for b in bars
                           if start <= b.start < start + interval),
                          key=lambda b: b.start)
        assert len(included) == interval // 60
        return Candle(start, interval, included[0].open,
                      max(b.high for b in included), min(b.low for b in included),
                      included[-1].close, sum((b.volume for b in included), Decimal(0)))
    return replace(
        obs, candle_5m=aggregate(start5, 300),
        candle_15m=aggregate(start15, 900),
        exchange_server_epoch=NOW, ticker_observed_epoch=NOW - 1,
        bid_book_levels=((Decimal("100"), Decimal("2")), (Decimal("99"), Decimal("1"))),
        ask_book_levels=((Decimal("101"), Decimal("2")), (Decimal("102"), Decimal("1"))),
        depth_levels_visible=4,
    )


class M04SourceConsistencyOriginalTests(unittest.TestCase):
    def test_real_bulk_cli_to_source_coverage_and_sqlite_reject(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            quota = root / "rest.sqlite3"
            db = root / "decisions.sqlite3"
            fake = FakeIndodaxAll()
            fake.now = NOW
            for item in fake.tickers.values():
                item["server_time"] = NOW - 1
                item["vol_idr"] = "300000000"
            calls = []
            def transport(_client, path):
                calls.append(path)
                if path.startswith("/tradingview/history_v2?"):
                    url = urlsplit(path)
                    tf = parse_qs(url.query).get("tf", [""])[0]
                    if tf == "1":
                        return [ohlc_row(LAST_1M - n * 60) for n in reversed(range(34))]
                    if tf == "15":
                        return [{
                            **ohlc_row(LAST_15M - n * 900),
                            "Volume": "150",
                        } for n in (1, 0)]
                    raise AssertionError("UNEXPECTED_TIMEFRAME")
                if path.startswith("/api/trades/"):
                    return [{"date": NOW - 70, "price": "100",
                             "amount": "1", "type": "buy"}]
                if path.startswith("/api/depth/"):
                    return {"buy": [["100", "5"], ["99", "4"]],
                            "sell": [["101", "5"], ["102", "4"]]}
                return fake.fetch(path)
            original = IndodaxPublicMarket.__init__
            def clock_constructor(client, *a, **kw):
                kw.setdefault("clock", lambda: NOW)
                original(client, *a, **kw)
            stdout, stderr = StringIO(), StringIO()
            env = {
                "INDODAX_TRADING_ENABLED": "NO",
                "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
                "INDODAX_EXECUTION_MODE": "DRY_RUN",
                "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "NO",
                "INDODAX_PUBLIC_REST_RATE_DB": str(quota),
            }
            with (
                patch.dict(os.environ, env),
                patch.object(IndodaxPublicMarket, "__init__", new=clock_constructor),
                patch.object(IndodaxPublicMarket, "_http_json",
                             autospec=True, side_effect=transport),
                redirect_stdout(stdout), redirect_stderr(stderr),
            ):
                code = main(["--bulk-deep-once", "--db", str(db)])
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(code, 0)
            self.assertEqual(len(calls), 83)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["final_decision"], "REJECT")
            self.assertFalse(result["execution_allowed"])
            self.assertEqual(result["ai_shadow"]["completed_roles"], [])
            admission = result["public_bulk"]["g1_source_admission"]
            self.assertEqual(admission["pairs_observed"], 20)
            first = admission["pair_coverage"][0]["timeframe_diagnostic"]
            self.assertEqual(first["exchange_to_local_clock_skew_seconds"], 0)
            self.assertEqual(first["exchange_clock_status"],
                             "PUBLIC_SERVER_CLOCK_WITHIN_30S_UNATTESTED")
            self.assertEqual(first["ticker_age_seconds"], 1)
            self.assertEqual(first["closed_5m_ohlc_consistency"],
                             "MATCHED_CLOSED_OHLC_UNATTESTED")
            self.assertEqual(first["closed_15m_ohlc_consistency"],
                             "MATCHED_CLOSED_OHLC_UNATTESTED")
            self.assertEqual(first["book_ladder_consistency"],
                             "PUBLIC_TWO_SIDED_DEPTH_OBSERVED_NONATOMIC")
            self.assertFalse(first["book_and_quote_atomic_snapshot_proven"])
            self.assertFalse(admission["g1_receipt_issuance_allowed"])
            self.assertFalse(admission["g2_provider_call_allowed"])
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
                self.assertEqual(
                    con.execute("SELECT final_decision FROM decisions WHERE trace_id=?",
                                (result["trace_id"],)).fetchone(),
                    ("REJECT",),
                )

    def diagnostics(self, obs):
        return public_coverage_report(
            ("btc_idr",), {"btc_idr": obs},
            screening_epoch=NOW, complete_batch=True, window_expired=False,
        )

    def test_missing_clock_and_depth_is_explicitly_unknown_not_validated(self):
        obs = good_observation()
        obs = replace(obs, exchange_server_epoch=None, ticker_observed_epoch=None,
                      bid_book_levels=(), ask_book_levels=())
        report = self.diagnostics(obs)
        diag = report["pair_coverage"][0]["timeframe_diagnostic"]
        self.assertEqual(diag["exchange_clock_status"], "UNKNOWN_NO_EXCHANGE_EPOCH")
        self.assertIsNone(diag["exchange_to_local_clock_skew_seconds"])
        self.assertEqual(diag["ticker_freshness_status"], "UNKNOWN_NO_TICKER_EPOCH")
        self.assertEqual(diag["book_ladder_consistency"], "UNKNOWN_NO_BOTH_BOOK_LADDERS")
        self.assertFalse(report["g1_receipt_issuance_allowed"])

    def test_15m_ohlc_conflict_denies_timeframe_consistency(self):
        obs = good_observation()
        different = replace(obs.candle_15m, close=obs.candle_15m.close + Decimal("0.01"))
        report = self.diagnostics(replace(obs, candle_15m=different))
        diag = report["pair_coverage"][0]["timeframe_diagnostic"]
        self.assertEqual(diag["closed_15m_ohlc_consistency"],
                         "REJECT_CLOSED_OHLC_MISMATCH")
        self.assertEqual(diag["status"], "REJECT_PUBLIC_TIMEFRAME_OR_AGE")
        self.assertFalse(report["g2_provider_call_allowed"])

    def test_stale_ticker_and_server_clock_skew_denied(self):
        obs = good_observation()
        for broken in (
            replace(obs, ticker_observed_epoch=NOW - 61),
            replace(obs, exchange_server_epoch=NOW + 31),
        ):
            with self.subTest(broken=broken):
                report = self.diagnostics(broken)
                self.assertEqual(
                    report["pair_coverage"][0]["timeframe_diagnostic"]["status"],
                    "REJECT_PUBLIC_TIMEFRAME_OR_AGE",
                )
                self.assertFalse(report["g1_receipt_issuance_allowed"])

    def test_crossed_book_and_wrong_depth_levels_denied(self):
        obs = good_observation()
        bad = replace(obs, bid_book_levels=((Decimal("102"), Decimal("2")),))
        report = self.diagnostics(bad)
        self.assertEqual(
            report["pair_coverage"][0]["timeframe_diagnostic"]["book_ladder_consistency"],
            "REJECT_BOOK_LADDER_CONSISTENCY",
        )
        self.assertFalse(report["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
