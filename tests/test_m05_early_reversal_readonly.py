"""Validate bounded original M03 public ticker early-reversal diagnostics."""
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest
from zoneinfo import ZoneInfo

from scripts.m05_radar_early_reversal_readonly import (
    examine_early_reversal_readonly,
)

BASE = int(datetime(2026, 11, 6, 20, 50, tzinfo=ZoneInfo("Asia/Jakarta")).timestamp())


class OriginalPublicEarlyReversalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "decisions.sqlite3"
        with sqlite3.connect(self.db) as conn:
            conn.execute("""
                CREATE TABLE public_ticker_minute_samples(
                    pair TEXT NOT NULL,
                    minute_epoch INTEGER NOT NULL,
                    observed_epoch INTEGER NOT NULL,
                    ticker_epoch INTEGER NOT NULL,
                    last_idr TEXT NOT NULL,
                    volume_24h_idr TEXT NOT NULL,
                    spread_bps TEXT NOT NULL,
                    PRIMARY KEY(pair,minute_epoch)
                )
            """)
        self.db.chmod(0o600)

    def sample(self, minute, price, *, pair="uai_idr",
               volume="1000000000", spread="22",
               observed_delay=7):
        epoch = BASE + minute * 60
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO public_ticker_minute_samples VALUES(?,?,?,?,?,?,?)",
                (pair, epoch, epoch + observed_delay, epoch + observed_delay,
                 str(price), volume, spread)
            )

    def replay(self, end, *, pairs=("uai_idr",), start=0):
        return examine_early_reversal_readonly(
            start_epoch=BASE + start * 60,
            end_epoch=BASE + end * 60,
            pairs=pairs, db_path=self.db,
        )

    def test_negative_then_positive_public_sample_is_diagnostic_only(self):
        self.sample(0, 100)
        self.sample(1, 96)
        self.sample(2, 94)
        self.sample(3, 96)
        report = self.replay(3)
        uai = report["pairs"][0]
        first = uai["first_positive_1m_after_negative_1m"]
        self.assertEqual(first["minute_wib"], "2026-11-06 20:53:00 WIB")
        self.assertEqual(first["return_1m_sample_pct"], "2.13")
        self.assertTrue(first["spread_limit_pass"])
        self.assertTrue(first["turnover_floor_pass"])
        self.assertEqual(
            uai["first_reversal_by_1m_sample_pct_threshold"]["2"]["minute_wib"],
            "2026-11-06 20:53:00 WIB",
        )
        self.assertIsNone(
            uai["first_reversal_by_1m_sample_pct_threshold"]["3"]
        )
        self.assertTrue(report["sample_returns_are_not_closed_candles"])
        self.assertFalse(report["source_issuer_g1_verified"])
        self.assertEqual(report["actual_market_risk_strategy_ai_calls"], 0)
        self.assertEqual(report["exchange_requests"], 0)
        self.assertEqual(report["telegram_messages_sent"], 0)
        self.assertFalse(report["order_sent"])
        self.assertFalse(report["execution_allowed"])

    def test_only_past_samples_and_exact_adjacent_minutes(self):
        self.sample(0, 100)
        self.sample(1, 90)
        self.sample(3, 94)
        now = self.replay(1)["pairs"][0]
        self.assertIsNone(now["first_positive_1m_after_negative_1m"])
        later = self.replay(3)["pairs"][0]
        self.assertIsNone(later["first_positive_1m_after_negative_1m"])
        self.assertFalse(later["timeline"][-1]["exact_previous_minute_sample"])
        self.assertEqual(later["missing_minute_count"], 1)

    def test_bad_liquidity_and_spread_do_not_form_reversal(self):
        for pair,vol,spread in (
            ("thin_idr", "149999999", "22"),
            ("wide_idr", "800000000", "101"),
        ):
            self.sample(0, 100, pair=pair, volume=vol, spread=spread)
            self.sample(1, 90, pair=pair, volume=vol, spread=spread)
            self.sample(2, 93, pair=pair, volume=vol, spread=spread)
        report = self.replay(2,pairs=("thin_idr","wide_idr"))
        for row in report["pairs"]:
            self.assertIsNone(row["first_positive_1m_after_negative_1m"])

    def test_token_subrupiah_precision_and_no_db_write(self):
        self.sample(0, "0.00000012")
        self.sample(1, "0.00000010")
        self.sample(2, "0.00000011")
        before = self.db.stat().st_mtime_ns
        result = self.replay(2)
        self.assertEqual(self.db.stat().st_mtime_ns, before)
        self.assertEqual(
            result["pairs"][0]["timeline"][-1]["last_idr"], "1.1E-7"
        )
        self.assertEqual(
            result["pairs"][0]["timeline"][-1]["return_1m_sample_pct"],
            "10.00"
        )

    def test_invalid_scopes_rejected(self):
        attempts = (
            (BASE, BASE + 31 * 60, ("uai_idr",)),
            (BASE+60, BASE, ("uai_idr",)),
            (BASE, BASE, ("../secret",)),
            (BASE, BASE, ()),
            (BASE, BASE, ("uai_idr",)*2),
            (BASE, BASE, ("aa_idr","bb_idr","cc_idr","dd_idr",
                          "ee_idr","ff_idr")),
        )
        for start, end, pairs in attempts:
            with self.subTest(start=start,end=end,pairs=pairs):
                with self.assertRaisesRegex(
                    ValueError, "M05_EARLY_REVERSAL_INVALID_SCOPE"
                ):
                    examine_early_reversal_readonly(
                        start_epoch=start,end_epoch=end,
                        pairs=pairs,db_path=self.db
                    )


if __name__ == "__main__":
    unittest.main()
