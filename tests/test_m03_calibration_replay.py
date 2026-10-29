"""M03 source-backed OHLC proxy ablation research safety, all offline."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from indodax_ai_bot.calibration_replay import evaluate_public_ohlc_proxy_ablation
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.public_market import Candle, PublicCandleHistory, PublicDataError
from ops.m03_calibration_replay_once import evaluate_archive


def history(n=360):
    start = 1800000000 - (1800000000 % 60)
    rows = []
    for i in range(n):
        price = Decimal("100") + Decimal(i) / Decimal("100")
        rows.append(Candle(
            start + 60*i, 60,
            price, price + Decimal("2"), price - Decimal("2"),
            price + Decimal("0.1"),
            Decimal("20") + (Decimal("100") if i % 27 == 0 else Decimal("0")),
        ))
    return PublicCandleHistory(
        pair="btc_idr", observed_epoch=start+60*n,
        exchange_epoch=start+60*n,
        from_epoch=start, to_epoch=start+60*n,
        candles=tuple(rows),
    )


class M03PublicCalibrationTests(unittest.TestCase):
    def test_two_split_purged_nonoverlapping_only_observational(self):
        out = evaluate_public_ohlc_proxy_ablation(history())
        self.assertEqual(out["status"], "DESCRIPTIVE_OOS_ONLY_INSUFFICIENT_TRADE_EVIDENCE")
        self.assertTrue(out["purged_boundary_hourly_windows"] >= 1)
        self.assertGreater(out["train"]["independent_hourly_windows"], 0)
        self.assertGreater(out["holdout"]["independent_hourly_windows"], 0)
        self.assertEqual(len(out["source_digest_sha256_local"]), 64)
        self.assertFalse(out["out_of_sample_strategy_edge_validated"])
        self.assertFalse(out["net_idr_10000_and_roi_2pct_verified"])
        self.assertFalse(out["actual_fill_verified"])
        self.assertFalse(out["market_source_g1_verified"])
        self.assertFalse(out["execution_allowed"])
        self.assertEqual(out["final_decision"], "REJECT")

    def test_future_bar_changes_return_not_previous_train_or_proxy_labels(self):
        source = history()
        original = evaluate_public_ohlc_proxy_ablation(source)
        bars = list(source.candles)
        # Index 330 is an exit price for a late holdout signal at 270;
        # the train policy and signal cohort counts cannot see it.
        bars[330] = replace(
            bars[330], close=Decimal("190"), high=Decimal("195"),
        )
        changed = evaluate_public_ohlc_proxy_ablation(
            replace(source, candles=tuple(bars))
        )
        self.assertEqual(original["train"], changed["train"])
        for label in original["holdout"]["cohorts"]:
            self.assertEqual(
                original["holdout"]["cohorts"][label]["independent_hourly_samples"],
                changed["holdout"]["cohorts"][label]["independent_hourly_samples"],
            )
        self.assertNotEqual(original["source_digest_sha256_local"],
                            changed["source_digest_sha256_local"])
        self.assertNotEqual(
            original["holdout"]["cohorts"]["ALL_HOURLY_CONTROL"]["avg_gross_60m_bps"],
            changed["holdout"]["cohorts"]["ALL_HOURLY_CONTROL"]["avg_gross_60m_bps"],
        )

    def test_forged_origin_and_gaps_rejected(self):
        src = history()
        for bad in (
            replace(src, source_trust_verified=True),
            replace(src, trade_history_complete=True),
            replace(src, to_epoch=src.to_epoch + 60),
            replace(src, candles=src.candles[:100]+src.candles[101:]),
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(PublicDataError):
                    evaluate_public_ohlc_proxy_ablation(bad)

    def test_archive_is_readonly_and_checks_local_digest(self):
        with TemporaryDirectory() as td:
            db=Path(td)/"archive.sqlite3"
            j=DecisionJournal(db)
            src=history()
            # Archive API constrains each immutable chunk <=360 bars.
            j.archive_public_ohlc_history(src)
            first=db.stat().st_size
            report=evaluate_archive(db)
            self.assertFalse(report["execution_allowed"])
            self.assertEqual(report["closed_1m_rows_visible"], 360)
            self.assertEqual(db.stat().st_size, first)
            with sqlite3.connect(db) as c:
                c.execute(
                    "UPDATE public_ohlc_1m SET close='9999' "
                    "WHERE pair='btc_idr' AND start_epoch=?",
                    (src.candles[12].start,),
                )
            with self.assertRaisesRegex(
                PublicDataError, "CALIBRATION_ARCHIVE_ROW_DIGEST_MISMATCH"
            ):
                evaluate_archive(db)


if __name__ == "__main__":
    unittest.main()
