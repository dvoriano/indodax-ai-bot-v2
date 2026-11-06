"""No-lookahead original V2 public M03/M05 radar candidate triage and replay.

Single actual SQLite fixture shape, source-only. No network, owner Telegram
messages, AI, restarts, order buttons, or provider call.
"""
from datetime import datetime
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest
from zoneinfo import ZoneInfo

from indodax_ai_bot.m05_radar_momentum_nonlive import (
    inspect_original_m03_radar_readonly, classify_public_watch_pre_ai_nonlive,
)
from scripts.m05_radar_owner_quality_replay_readonly import (
    replay_original_public_watch_readonly,
)

WIB = ZoneInfo("Asia/Jakarta")
BASE = int(datetime(2026, 11, 6, 20, 0, tzinfo=WIB).timestamp())


class OriginalPublicRadarReviewReplayTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.path = Path(t.name) / "decisions.sqlite3"
        with sqlite3.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE public_ticker_minute_samples(
                    pair TEXT NOT NULL,
                    minute_epoch INTEGER NOT NULL,
                    observed_epoch INTEGER NOT NULL,
                    ticker_epoch INTEGER NOT NULL,
                    last_idr TEXT NOT NULL,
                    volume_24h_idr TEXT NOT NULL,
                    spread_bps TEXT NOT NULL,
                    PRIMARY KEY(pair, minute_epoch)
                );
                CREATE TABLE public_surge_cases(
                    pair TEXT, minute_epoch INTEGER, cohort TEXT
                );
            """)
        self.path.chmod(0o600)

    def sample(self, pair, minute, price, volume="600000000", spread="25",
               *, observed_delay=7):
        epoch = BASE + minute * 60
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO public_ticker_minute_samples VALUES (?,?,?,?,?,?,?)",
                (pair, epoch, epoch + observed_delay, epoch + observed_delay,
                 str(price), str(volume), str(spread)),
            )

    def inspect(self, minute, **kw):
        return inspect_original_m03_radar_readonly(
            db_path=self.path, now_epoch=BASE + minute * 60 + 59, **kw
        )

    def replay(self, pairs=("uai_idr",), end=8):
        return replay_original_public_watch_readonly(
            start_epoch=BASE, end_epoch=BASE + end * 60, pairs=pairs,
            db_path=self.path,
        )

    def test_price_up_and_real_positive_rolling_delta_review_not_buy(self):
        for minute, price, volume in (
            (0, 3151, 1000000000),
            (1, 3180, 1010000000),
            (2, 3250, 1030000000),
            (3, 3411, 1100000000),
        ):
            self.sample("uai_idr", minute, price, volume)
        snap = self.inspect(3)
        self.assertEqual(snap["status"], "M03_ORIGINAL_PUBLIC_RADAR_OBSERVED_UNATTESTED")
        row = snap["candidates"][0]
        self.assertEqual(row["pair"], "uai_idr")
        triage = row["pre_ai_review"]
        self.assertEqual(triage["status"], "PUBLIC_WATCH_PRE_AI_DIAGNOSTIC_ONLY")
        self.assertEqual(triage["review_priority"], "PRICE_AND_ROLLING_DELTA_REVIEW")
        self.assertEqual(triage["priority_rank_observational"], 1)
        self.assertFalse(triage["delta_24h_is_actual_1m_trade_volume"])
        self.assertFalse(triage["price_is_actual_closed_candle"])
        self.assertFalse(triage["trade_direction_buy_dominance_verified"])
        self.assertFalse(triage["g1_verified"])
        self.assertEqual(triage["market_risk_strategy_ai_calls"], 0)
        self.assertEqual(triage["strategy"], "WATCH_NO_TRADE")
        self.assertFalse(triage["execution_allowed"])
        self.assertEqual(len(triage["public_sample_fingerprint_sha256"]), 64)

    def test_price_down_large_rolling_delta_is_low_priority_not_buy(self):
        for minute, price, volume in (
            (0, 12325, 830000000),
            (1, 12310, 850000000),
            (2, 12300, 900000000),
            (3, 12294, 1010000000),
        ):
            self.sample("aster_idr", minute, price, volume, spread="70")
        row = self.inspect(3)["candidates"][0]
        triage = row["pre_ai_review"]
        self.assertEqual(row["watch_type"], "ROLLING_TURNOVER_CHANGE_WATCH")
        self.assertEqual(triage["review_priority"], "ACTIVITY_ONLY_PRICE_NOT_RISING")
        self.assertEqual(triage["priority_rank_observational"], 4)
        self.assertIn("PRICE_NONPOSITIVE_DO_NOT_INFER_BUY_PRESSURE",
                      triage["caution_codes"])
        self.assertIn("SPREAD_OVER_60_BPS_OBSERVED", triage["caution_codes"])
        self.assertIsNone(triage["ai_role_results"])

    def test_explain_low_turnover_spread_missing_reference_and_below_threshold(self):
        self.sample("low_idr", 0, 100, volume="140000000")
        self.sample("low_idr", 3, 150, volume="140000000")
        self.sample("wide_idr", 0, 100, spread="120")
        self.sample("wide_idr", 3, 150, spread="120")
        self.sample("solo_idr", 3, 100)
        self.sample("slow_idr", 0, 100)
        self.sample("slow_idr", 3, 101)
        snap = self.inspect(3, explain_pairs=(
            "low_idr", "wide_idr", "solo_idr", "slow_idr", "absent_idr",
        ))
        facts = snap["pair_diagnostics"]
        self.assertEqual(facts["low_idr"]["reason"], "BELOW_DAILY_TURNOVER_FLOOR")
        self.assertEqual(facts["wide_idr"]["reason"], "SPREAD_OVER_WATCH_LIMIT")
        self.assertEqual(facts["solo_idr"]["reason"], "MISSING_2_TO_5M_REFERENCE")
        self.assertEqual(facts["slow_idr"]["reason"],
                         "BELOW_PRICE_AND_ROLLING_DELTA_THRESHOLDS")
        self.assertFalse(facts["slow_idr"]["rolling_24h_delta_observable"])
        self.assertEqual(facts["absent_idr"]["reason"],
                         "NO_VALID_SAMPLE_IN_REPLAY_WINDOW")

    def test_replay_uses_earlier_observation_only_and_no_casebook_hindsight(self):
        for minute, price in ((0, 100), (1, 101), (2, 103), (3, 106)):
            self.sample("uai_idr", minute, price, observed_delay=35)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO public_surge_cases VALUES (?,?,?)",
                ("uai_idr", BASE + 3 * 60, "EARLY_WATCH"),
            )
        self.assertEqual(self.inspect(3)["candidates"][0]["pair"], "uai_idr")
        result = self.replay(end=4)
        row = result["pairs"][0]
        self.assertIsNone(row["first_top5_watch_wib"])
        self.assertGreater(
            row["asof_minute_counts_by_reason"].get(
                "BELOW_PRICE_AND_ROLLING_DELTA_THRESHOLDS", 0), 0
        )
        self.assertTrue(result["historical_casebook_excluded_to_prevent_lookahead"])
        self.assertEqual(result["exchange_requests"], 0)
        self.assertEqual(result["actual_three_role_ai_calls"], 0)
        self.assertFalse(result["execution_allowed"])

    def test_replay_observed_first_eligible_before_later_price_pump(self):
        for minute, price in ((0, 100), (1, 101), (2, 105),
                              (3, 110), (4, 115), (5, 119)):
            self.sample("uai_idr", minute, price)
        old = self.path.stat().st_mtime_ns
        result = self.replay(end=5)
        row = result["pairs"][0]
        self.assertEqual(row["first_top5_watch_wib"],
                         "2026-11-06 20:03:59 WIB")
        self.assertEqual(row["first_eligible_any_rank_wib"],
                         "2026-11-06 20:03:59 WIB")
        self.assertEqual(row["first_top5_observed_evidence"]["priority"],
                         "PRICE_MOMENTUM_REVIEW")
        self.assertEqual(row["asof_minute_counts_by_reason"]["IN_TOP5"], 3)
        self.assertEqual(self.path.stat().st_mtime_ns, old)

    def test_qualified_but_outside_top5_is_separately_explained(self):
        for i, pair in enumerate(("aa_idr","bb_idr","cc_idr",
                                  "dd_idr","ee_idr","ff_idr")):
            self.sample(pair, 0, 100)
            self.sample(pair, 3, 120 + i)
        report = self.inspect(3, explain_pairs=("aa_idr","ff_idr"))
        self.assertEqual(report["pair_diagnostics"]["aa_idr"]["reason"],
                         "QUALIFIED_OUTSIDE_TOP5")
        self.assertEqual(report["pair_diagnostics"]["aa_idr"]["rank"], 6)
        self.assertEqual(report["pair_diagnostics"]["ff_idr"]["reason"], "IN_TOP5")
        self.assertEqual(report["pair_diagnostics"]["ff_idr"]["rank"], 1)

    def test_volume_down_price_then_later_price_momentum_transition(self):
        # Historical UAI-like shape: volume NET up while sampled price is
        # negative, later price accelerates. Never project later price backward.
        values = (
            (100, "1000000000"),
            (90, "1010000000"),
            (85, "1020000000"),
            (80, "1300000000"),
            (80, "1300000000"),
            (80, "1300000000"),
            (80, "1300000000"),
            (80, "1300000000"),
            (80, "1300000000"),
            (90, "1300000000"),
        )
        for minute, (price, volume) in enumerate(values):
            self.sample("uai_idr", minute, price, volume)

        before_price_rise = self.replay(end=8)["pairs"][0]
        self.assertEqual(
            before_price_rise["first_top5_falling_price_volume_only_wib"],
            "2026-11-06 20:03:59 WIB",
        )
        self.assertIsNone(before_price_rise["first_top5_price_momentum_wib"])
        self.assertEqual(
            before_price_rise["top5_minute_counts_by_watch_type"].get(
                "PRICE_MOMENTUM_WATCH", 0), 0,
        )

        result = self.replay(end=9)
        row = result["pairs"][0]
        self.assertEqual(
            row["first_top5_price_momentum_wib"],
            "2026-11-06 20:09:59 WIB",
        )
        self.assertEqual(
            row["first_top5_by_watch_type_wib"]["PRICE_MOMENTUM_WATCH"],
            "2026-11-06 20:09:59 WIB",
        )
        self.assertEqual(
            row["first_top5_by_watch_type_wib"]["ROLLING_TURNOVER_CHANGE_WATCH"],
            "2026-11-06 20:03:59 WIB",
        )
        self.assertEqual(
            [event["phase"] for event in row["top5_observed_phase_transitions"]],
            [
                "ROLLING_TURNOVER_CHANGE_WATCH:PRICE_DOWN",
                "PRICE_MOMENTUM_WATCH:PRICE_NONNEGATIVE",
            ],
        )
        self.assertFalse(row["phase_transitions_are_actual_messages"])
        self.assertTrue(
            result["possible_top5_watch_not_proof_of_actual_telegram_send"]
        )
        self.assertTrue(
            result["cooldowns_delivery_and_exact_process_poll_not_replayed"]
        )
        self.assertEqual(result["actual_three_role_ai_calls"], 0)
        self.assertFalse(result["execution_allowed"])

    def test_no_lookahead_first_price_rise_must_follow_actual_sample(self):
        self.sample("fun_idr", 0, 100)
        self.sample("fun_idr", 3, 120)
        before = self.replay(pairs=("fun_idr",), end=2)["pairs"][0]
        self.assertIsNone(before["first_top5_price_momentum_wib"])
        after = self.replay(pairs=("fun_idr",), end=3)["pairs"][0]
        self.assertEqual(
            after["first_top5_price_momentum_wib"],
            "2026-11-06 20:03:59 WIB"
        )
        self.assertEqual(after["top5_minute_counts_by_watch_type"], {
            "PRICE_MOMENTUM_WATCH": 1,
        })

    def test_replay_scope_restricts_large_or_invalid_windows(self):
        for kwargs in (
            {"end_epoch": BASE + 121 * 60, "start_epoch": BASE},
            {"end_epoch": BASE, "start_epoch": BASE + 60},
            {"end_epoch": BASE, "start_epoch": BASE, "pairs": ("../secret",)},
            {"end_epoch": BASE, "start_epoch": BASE, "pairs": ()},
            {"end_epoch": BASE, "start_epoch": BASE, "pairs": ("uai_idr",) * 2},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(
                    ValueError, "M05_RADAR_REPLAY_INVALID_SCOPE"):
                replay_original_public_watch_readonly(
                    db_path=self.path, **kwargs
                )


if __name__ == "__main__":
    unittest.main()
