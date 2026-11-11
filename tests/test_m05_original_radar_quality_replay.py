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
    replay_original_ws3_quality_timeline_readonly,
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



class OriginalWS3QualityTimelineReplayTests(unittest.TestCase):
    """Original SQLite WS3+M04 source-only; no false Telegram ACK."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.db = base / "decisions.sqlite3"
        self.owner = base / "owner.sqlite3"
        with sqlite3.connect(self.db) as db:
            db.executescript("""
                CREATE TABLE m03_ws_summary_watch_minute(
                  pair TEXT NOT NULL,minute_epoch INTEGER NOT NULL,
                  exchange_epoch INTEGER NOT NULL,last_idr TEXT NOT NULL,
                  rolling_24h_idr TEXT NOT NULL,public_offset INTEGER NOT NULL,
                  PRIMARY KEY(pair,minute_epoch));
                CREATE TABLE m03_ws_microstructure_watch_minute(
                  pair TEXT NOT NULL,minute_epoch INTEGER NOT NULL,
                  observed_local_epoch INTEGER NOT NULL,
                  trade_frames INTEGER NOT NULL,
                  source_buy_count INTEGER,source_sell_count INTEGER,
                  source_buy_idr TEXT,source_sell_idr TEXT,
                  last_trade_epoch INTEGER,trade_offset INTEGER,
                  trade_gap_observed INTEGER NOT NULL,
                  trade_truncated INTEGER NOT NULL,
                  book_offset INTEGER,book_local_epoch INTEGER,
                  best_bid_idr TEXT,best_ask_idr TEXT,spread_bps TEXT,
                  bid_depth_top5_idr TEXT,ask_depth_top5_idr TEXT,
                  book_imbalance_top5_ratio TEXT,
                  source_malformed INTEGER NOT NULL,
                  PRIMARY KEY(pair,minute_epoch));
            """)
        self.db.chmod(0o600)
        self.zero = BASE

    def quote(self, pair, minute, price, volume="500000000"):
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO m03_ws_summary_watch_minute VALUES "
                       "(?,?,?,?,?,?)", (
                pair, BASE + minute * 60, BASE + minute * 60 + 20,
                str(price), str(volume), 100 + minute,
            ))

    def micro(self, pair, minute):
        stamp = BASE + minute * 60 + 30
        with sqlite3.connect(self.db) as db:
            db.execute(
                "INSERT INTO m03_ws_microstructure_watch_minute VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pair, BASE + minute * 60, stamp, 1, 2, 1,
                 "100000", "50000", stamp-2, 45,
                 0, 0, 51, stamp, "100", "100.2", "19.9800",
                 "9000000", "7000000", "1.2857", 0),
            )

    def replay(self, end=4, pairs=("uai_idr", "thin_idr"), owner=False):
        return replay_original_ws3_quality_timeline_readonly(
            start_epoch=BASE,
            end_epoch=BASE + 60 * end, pairs=pairs, db_path=self.db,
            radar_claims_db=self.owner if owner else None,
        )

    def test_uai_like_first_quote_micro_watch_m04_times_and_negative_control(self):
        self.quote("uai_idr", 1, 100)
        self.quote("uai_idr", 2, 101, "500050000")
        self.micro("uai_idr", 2)
        self.quote("thin_idr", 1, 100, "100000000")
        self.quote("thin_idr", 2, 105, "100050000")
        r = self.replay()
        self.assertEqual(r["status"],
            "M03_WS3_ORIGINAL_SOURCE_CHRONOLOGICAL_QUALITY_TIMELINE")
        good, thin = r["pairs"]
        self.assertEqual(good["pair"], "uai_idr")
        self.assertEqual(good["first_persisted_quote_minute_wib"],
                         "2026-11-06 20:01:00 WIB")
        self.assertEqual(good["first_raw_micro_capture_wib"],
                         "2026-11-06 20:02:30 WIB")
        self.assertEqual(good["first_m03_watch_top40_replay_minute_end_wib"],
                         "2026-11-06 20:02:59 WIB")
        self.assertEqual(good["first_m05_top5_eligible_replay_minute_end_wib"],
                         "2026-11-06 20:02:59 WIB")
        self.assertEqual(good["first_fresh_micro_joined_replay_minute_end_wib"],
                         "2026-11-06 20:02:59 WIB")
        self.assertEqual(good["first_m04_quality_top2_replay_minute_end_wib"],
                         "2026-11-06 20:02:59 WIB")
        self.assertEqual(good["first_m04_micro_prealert_top2_replay_minute_end_wib"],
                         "2026-11-06 20:02:59 WIB")
        self.assertGreater(
            good["first_m04_quality_top2_score_observational"], 0)
        self.assertIsNone(thin["first_m05_top5_eligible_replay_minute_end_wib"])
        self.assertIsNone(thin["first_m04_quality_top2_replay_minute_end_wib"])
        self.assertEqual(thin["source_quote_rows_in_window"], 2)
        self.assertIsNone(good["telegram_actual_send_ack_wib"])
        self.assertIsNone(good["telegram_delivery_delay_seconds"])
        self.assertEqual(r["telegram_messages_sent"], 0)
        self.assertEqual(r["paid_provider_calls"], 0)
        self.assertFalse(r["execution_allowed"])

    def test_later_micro_must_not_be_used_in_earlier_replayed_minute(self):
        self.quote("uai_idr", 1, 100)
        self.quote("uai_idr", 2, 101, "500050000")
        self.quote("uai_idr", 3, "101.2", "500060000")
        self.micro("uai_idr", 3)
        before = self.replay(end=2, pairs=("uai_idr",))["pairs"][0]
        after = self.replay(end=3, pairs=("uai_idr",))["pairs"][0]
        self.assertIsNone(before["first_raw_micro_capture_wib"])
        self.assertIsNone(before["first_fresh_micro_joined_replay_minute_end_wib"])
        self.assertEqual(before["source_micro_rows_in_window"], 0)
        self.assertEqual(after["first_raw_micro_capture_wib"],
                         "2026-11-06 20:03:30 WIB")
        self.assertEqual(after["first_fresh_micro_joined_replay_minute_end_wib"],
                         "2026-11-06 20:03:59 WIB")
        self.assertEqual(before["first_m03_watch_top40_replay_minute_end_wib"],
                         "2026-11-06 20:02:59 WIB")

    def test_last_owner_radar_claim_is_not_telegram_send_ack(self):
        self.quote("uai_idr", 1, 100)
        self.quote("uai_idr", 2, 101, "500050000")
        with sqlite3.connect(self.owner) as db:
            db.execute(
                "CREATE TABLE m05_radar_momentum_attempts("
                "pair TEXT PRIMARY KEY,claimed_epoch INTEGER,"
                "source_observed_epoch INTEGER)"
            )
            db.execute(
                "INSERT INTO m05_radar_momentum_attempts VALUES(?,?,?)",
                ("uai_idr", BASE + 2*60 + 45, BASE + 2*60 + 20),
            )
        self.owner.chmod(0o600)
        r = self.replay(pairs=("uai_idr",), owner=True)
        p = r["pairs"][0]
        self.assertEqual(r["owner_radar_claim_source_status"],
                         "LAST_CLAIM_ROWS_ONLY_NOT_TELEGRAM_ACK")
        self.assertEqual(p["last_retained_m05_radar_claim_wib"],
                         "2026-11-06 20:02:45 WIB")
        self.assertTrue(p["last_claim_source_epoch_matches_ws3_quote"])
        self.assertIsNone(p["telegram_actual_send_ack_wib"])
        self.assertIsNone(p["telegram_delivery_delay_seconds"])
        self.assertEqual(r["telegram_messages_sent"], 0)

    def test_rejects_bad_scope_untrusted_db_and_absent_archive(self):
        self.quote("uai_idr", 1, 100)
        for kwargs in (
            {"start_epoch": BASE, "end_epoch": BASE + 121 * 60},
            {"start_epoch": BASE + 60, "end_epoch": BASE},
            {"start_epoch": BASE, "end_epoch": BASE,
             "pairs": ("../bad",)},
            {"start_epoch": BASE, "end_epoch": BASE, "pairs": ()},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(
                    ValueError, "M03_WS3_TIMELINE_INVALID_SCOPE"):
                replay_original_ws3_quality_timeline_readonly(
                    db_path=self.db, **kwargs)
        no_db = self.db.with_name("not_present.db")
        with self.assertRaisesRegex(
                ValueError, "M03_WS3_TIMELINE_SOURCE_UNTRUSTED"):
            replay_original_ws3_quality_timeline_readonly(
                db_path=no_db, start_epoch=BASE, end_epoch=BASE)
        empty = self.db.with_name("no_summary.db")
        with sqlite3.connect(empty) as db:
            db.execute("CREATE TABLE unrelated(x INTEGER)")
            db.execute("INSERT INTO unrelated VALUES(1)")
        empty.chmod(0o600)
        with self.assertRaisesRegex(
                ValueError, "M03_WS3_TIMELINE_SOURCE_ABSENT"):
            replay_original_ws3_quality_timeline_readonly(
                db_path=empty, start_epoch=BASE, end_epoch=BASE)


if __name__ == "__main__":
    unittest.main()
