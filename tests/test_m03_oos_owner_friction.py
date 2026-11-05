"""M03 historical closed-OHLC OOS owner friction scenarios: NO LIVE evidence."""
from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.calibration_replay import (
    MAX_CALIBRATION_1M_ROWS, evaluate_public_ohlc_proxy_ablation,
)
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.public_market import (
    Candle, PublicCandleHistory, PublicDataError,
)
from ops.m03_calibration_replay_once import evaluate_archive, main


def seven_day_proxy(n: int = 7200) -> PublicCandleHistory:
    """Deterministic, explicitly SYNTHETIC minute bars, never market tape.

    Every hourly signal at minute 30 is a 100-bps surge with a volume
    spike; the subsequent next-hour minute 0 has a 300-bps hypothetical
    30m forward close. The actual next-bar open is 100, not a fill.
    """
    start = 1800000000 - (1800000000 % 60)
    rows = []
    for i in range(n):
        minute = i % 60
        close = Decimal("103") if minute == 0 else (
            Decimal("101") if minute == 30 else Decimal("100"))
        rows.append(Candle(
            start + 60 * i, 60, Decimal("100"),
            max(Decimal("100.2"), close + Decimal("0.2")),
            Decimal("99.8"), close,
            Decimal("300") if minute == 30 else Decimal("20"),
        ))
    return PublicCandleHistory(
        pair="btc_idr", observed_epoch=start + 60*n,
        exchange_epoch=start + 60*n,
        from_epoch=start, to_epoch=start + 60*n,
        candles=tuple(rows),
    )


class M03OwnerAssumedFrictionOOS(unittest.TestCase):
    def test_seven_days_can_meet_30_holdout_and_costs_keep_no_trade(self):
        self.assertEqual(MAX_CALIBRATION_1M_ROWS, 20160)
        src = seven_day_proxy()
        r = evaluate_public_ohlc_proxy_ablation(
            src,
            owner_assumed_roundtrip_friction_bps=Decimal("30"),
            hypothetical_ticket_idr=Decimal("500000"),
        )
        self.assertGreater(r["closed_1m_rows_visible"], 4320)
        self.assertTrue(r["holdout_proxy_signal_count_minimum_met"])
        cohort = r["holdout"]["cohorts"]["SURGE_WITH_VOLUME_PROXY"]
        self.assertGreaterEqual(cohort["independent_hourly_samples"], 30)
        self.assertEqual(
            cohort["owner_assumed_joint_idr10000_and_roi2pct_30m_samples"],
            cohort["independent_hourly_samples"],
        )
        self.assertEqual(
            cohort["owner_assumed_joint_idr10000_and_roi2pct_60m_samples"], 0,
        )
        self.assertEqual(r["owner_assumed_roundtrip_friction_bps"], "30")
        self.assertEqual(r["hypothetical_ticket_idr"], "500000")
        self.assertFalse(r["owner_assumed_joint_threshold_is_qualification"])
        self.assertFalse(r["out_of_sample_strategy_edge_validated"])
        self.assertFalse(r["actual_indodax_fee_verified"])
        self.assertFalse(r["market_source_g1_verified"])
        self.assertFalse(r["g2_provider_call_allowed"])
        self.assertFalse(r["execution_allowed"])
        self.assertEqual(r["final_decision"], "REJECT")

    def test_roi_and_rupiah_checks_are_both_required_for_same_ticket(self):
        src = seven_day_proxy()
        small_ticket = evaluate_public_ohlc_proxy_ablation(
            src, owner_assumed_roundtrip_friction_bps=Decimal("30"),
            hypothetical_ticket_idr=Decimal("100000"),
        )
        x = small_ticket["holdout"]["cohorts"]["SURGE_WITH_VOLUME_PROXY"]
        # 300bps gross minus 30bps hypothetical fee/spread/slippage
        # = 270bps (2.70%) but only IDR 2700 on a IDR 100k ticket.
        self.assertEqual(x["owner_assumed_net_30m_bps_average"], "270.0000")
        self.assertEqual(
            x["owner_assumed_joint_idr10000_and_roi2pct_30m_samples"], 0,
        )
        high_friction = evaluate_public_ohlc_proxy_ablation(
            src, owner_assumed_roundtrip_friction_bps=Decimal("120"),
            hypothetical_ticket_idr=Decimal("1000000"),
        )
        y = high_friction["holdout"]["cohorts"]["SURGE_WITH_VOLUME_PROXY"]
        # IDR 18k would pass the Rupiah threshold, but 1.8% fails ROI.
        self.assertEqual(y["owner_assumed_net_30m_bps_average"], "180.0000")
        self.assertEqual(
            y["owner_assumed_joint_idr10000_and_roi2pct_30m_samples"], 0,
        )

    def test_owner_scenarios_never_change_signals_or_observed_returns(self):
        src = seven_day_proxy()
        first = evaluate_public_ohlc_proxy_ablation(src)
        stressed = evaluate_public_ohlc_proxy_ablation(
            src, owner_assumed_roundtrip_friction_bps=Decimal("200"),
            hypothetical_ticket_idr=Decimal("400000"),
        )
        self.assertEqual(first["source_digest_sha256_local"],
                         stressed["source_digest_sha256_local"])
        for bucket in ("train", "holdout"):
            for label, original in first[bucket]["cohorts"].items():
                scenario = stressed[bucket]["cohorts"][label]
                for field in (
                    "independent_hourly_samples", "avg_gross_30m_bps",
                    "avg_gross_60m_bps",
                    "illustrative_net_30m_bps_by_roundtrip_cost",
                ):
                    self.assertEqual(original[field], scenario[field])
                self.assertIsNone(original[
                    "owner_assumed_joint_idr10000_and_roi2pct_30m_samples"
                ])
        self.assertIsNone(first["owner_assumed_roundtrip_friction_bps"])

    def test_reject_nonpaired_nan_negative_bogus_scenario(self):
        src = seven_day_proxy(360)
        invalid = [
            (None, Decimal("500000")),
            (Decimal("30"), None),
            (Decimal("-1"), Decimal("100000")),
            (Decimal("NaN"), Decimal("100000")),
            (Decimal("3001"), Decimal("100000")),
            (Decimal("1"), Decimal("0")),
            (Decimal("1"), Decimal("Infinity")),
            (Decimal("1"), Decimal("1000000000001")),
            (30, Decimal("100000")),
        ]
        for cost, ticket in invalid:
            with self.subTest(cost=cost, ticket=ticket):
                with self.assertRaisesRegex(PublicDataError, "CALIBRATION"):
                    evaluate_public_ohlc_proxy_ablation(
                        src, owner_assumed_roundtrip_friction_bps=cost,
                        hypothetical_ticket_idr=ticket,
                    )

    def test_original_archive_over_4320_rows_readonly_and_cli_redacted(self):
        src = seven_day_proxy(4680)
        with TemporaryDirectory() as td:
            db = Path(td) / "v2_existing.sqlite3"
            journal = DecisionJournal(db)
            for i in range(0, len(src.candles), 360):
                rows = src.candles[i:i+360]
                section = replace(
                    src, from_epoch=rows[0].start,
                    to_epoch=rows[-1].start + 60,
                    candles=rows,
                    observed_epoch=rows[-1].start + 60,
                    exchange_epoch=rows[-1].start + 60,
                )
                journal.archive_public_ohlc_history(section)
            original_bytes = db.stat().st_size
            out = evaluate_archive(
                db,
                owner_assumed_roundtrip_friction_bps=Decimal("30"),
                hypothetical_ticket_idr=Decimal("500000"),
            )
            self.assertEqual(out["closed_1m_rows_visible"], 4680)
            self.assertFalse(out["execution_allowed"])
            self.assertEqual(db.stat().st_size, original_bytes)
            output = StringIO()
            with redirect_stdout(output):
                rc = main([
                    "--db", str(db), "--pair", "btc_idr",
                    "--roundtrip-friction-bps", "30", "--ticket-idr", "500000",
                ])
            self.assertEqual(rc, 0)
            parsed = json.loads(output.getvalue())
            self.assertEqual(parsed["closed_1m_rows_visible"], 4680)
            self.assertFalse(parsed["g2_provider_call_allowed"])
            self.assertFalse(parsed["execution_allowed"])
            self.assertEqual(db.stat().st_size, original_bytes)
            output = StringIO()
            with redirect_stdout(output):
                rc = main([
                    "--db", str(db), "--roundtrip-friction-bps", "abc",
                    "--ticket-idr", "500000",
                ])
            self.assertEqual(rc, 3)
            self.assertEqual(json.loads(output.getvalue())["status"], "REJECT")


if __name__ == "__main__":
    unittest.main()
