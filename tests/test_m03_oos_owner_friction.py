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


def nine_day_proxy(n: int = 12000) -> PublicCandleHistory:
    """Deterministic, explicitly SYNTHETIC minute bars, never market tape.

    Every hourly signal at minute 30 has 100-bps 5m acceleration and a
    volume spike. Alternate hours have a prior order-independent high
    at minute 25, yielding a distinct PULLBACK (not SURGE) proxy.
    Each subsequent next-hour minute 0 has a 300-bps hypothetical 30m
    forward close. The actual next-bar open is 100, not a fill.
    >8 days are needed to supply 30 independent signals in EACH
    mutually exclusive holdout cohort at a 30% holdout fraction.
    """
    start = 1800000000 - (1800000000 % 60)
    rows = []
    for i in range(n):
        minute = i % 60
        close = Decimal("103") if minute == 0 else (
            Decimal("101") if minute == 30 else Decimal("100"))
        high = max(Decimal("100.2"), close + Decimal("0.2"))
        if minute == 25 and (i // 60) % 2:
            high = Decimal("103")
        rows.append(Candle(
            start + 60 * i, 60, Decimal("100"),
            high, Decimal("99.8"), close,
            Decimal("300") if minute == 30 else Decimal("20"),
        ))
    return PublicCandleHistory(
        pair="btc_idr", observed_epoch=start + 60*n,
        exchange_epoch=start + 60*n,
        from_epoch=start, to_epoch=start + 60*n,
        candles=tuple(rows),
    )


class M03OwnerAssumedFrictionOOS(unittest.TestCase):
    def test_readonly_gate_diagnostic_preserves_original_results(self):
        src = nine_day_proxy()
        original = evaluate_public_ohlc_proxy_ablation(src)
        report = evaluate_public_ohlc_proxy_ablation(
            src, diagnose_signal_gates=True,
        )
        self.assertNotIn("signal_gate_diagnostic", original)
        self.assertEqual(report["holdout"], original["holdout"])
        self.assertEqual(report["train"], original["train"])
        self.assertEqual(report["source_digest_sha256_local"],
                         original["source_digest_sha256_local"])
        d = report["signal_gate_diagnostic"]
        hourly = d["holdout_hourly_fixed_anchor"]
        minute = d["holdout_all_eligible_minutes_not_independent_trades"]
        self.assertEqual(hourly["candidate_minutes_examined"],
                         report["holdout"]["independent_hourly_windows"])
        self.assertEqual(hourly["gate_true_counts"]["surge_final"],
                         report["holdout"]["cohorts"][
                             "SURGE_WITH_VOLUME_PROXY"]["independent_hourly_samples"])
        self.assertEqual(hourly["gate_true_counts"]["pullback_final"],
                         report["holdout"]["cohorts"][
                             "PULLBACK_WITH_VOLUME_PROXY"]["independent_hourly_samples"])
        self.assertGreater(minute["candidate_minutes_examined"],
                           hourly["candidate_minutes_examined"])
        self.assertGreaterEqual(minute["gate_true_counts"]["surge_final"],
                                hourly["gate_true_counts"]["surge_final"])
        self.assertTrue(d["same_original_predeclared_thresholds"])
        self.assertFalse(d["threshold_tuning_performed"])
        self.assertFalse(report["execution_allowed"])
        self.assertFalse(report["market_source_g1_verified"])

    def test_hourly_anchor_can_miss_actual_synthetic_minute_signals(self):
        src = nine_day_proxy()
        # Controlled timing shift in fully synthetic OHLC, NOT market tuning.
        rotated = []
        for base in range(0, len(src.candles), 60):
            hour = src.candles[base:base + 60]
            for minute, candle in enumerate(hour):
                observed = hour[(minute - 7) % 60]
                rotated.append(replace(observed, start=candle.start))
        shifted = replace(src, candles=tuple(rotated))
        d = evaluate_public_ohlc_proxy_ablation(
            shifted, diagnose_signal_gates=True,
        )["signal_gate_diagnostic"]
        hourly = d["holdout_hourly_fixed_anchor"]["gate_true_counts"]
        minute = d["holdout_all_eligible_minutes_not_independent_trades"][
            "gate_true_counts"]
        self.assertEqual(hourly["surge_final"], 0)
        self.assertEqual(hourly["pullback_final"], 0)
        self.assertGreater(minute["surge_final"], 0)
        self.assertGreater(minute["pullback_final"], 0)

    def test_diagnostic_rejects_invalid_nonboolean_flag(self):
        with self.assertRaisesRegex(PublicDataError, "DIAGNOSTIC_MODE"):
            evaluate_public_ohlc_proxy_ablation(
                nine_day_proxy(360), diagnose_signal_gates="YES",
            )

    def test_nine_days_can_meet_both_holdout_cohorts_without_trading(self):
        self.assertEqual(MAX_CALIBRATION_1M_ROWS, 20160)
        src = nine_day_proxy()
        r = evaluate_public_ohlc_proxy_ablation(
            src,
            owner_assumed_roundtrip_friction_bps=Decimal("30"),
            hypothetical_ticket_idr=Decimal("500000"),
        )
        self.assertGreater(r["closed_1m_rows_visible"], 4320)
        self.assertTrue(r["holdout_proxy_signal_count_minimum_met"])
        cohort = r["holdout"]["cohorts"]["SURGE_WITH_VOLUME_PROXY"]
        self.assertGreaterEqual(cohort["independent_hourly_samples"], 30)
        self.assertGreaterEqual(
            r["holdout"]["cohorts"]["PULLBACK_WITH_VOLUME_PROXY"][
                "independent_hourly_samples"], 30,
        )
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
        src = nine_day_proxy()
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
        src = nine_day_proxy()
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
        src = nine_day_proxy(360)
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
        src = nine_day_proxy(4680)
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
