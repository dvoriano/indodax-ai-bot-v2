"""WS3 original M03 archive to existing M05 owner radar: source-isolated nonlive."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.m05_radar_momentum_nonlive import (
    inspect_original_m03_ws3_watch_readonly,
    send_one_owner_radar_momentum_nonlive,
)
from indodax_ai_bot import telegram_service_tick

NOW = 1794330000


class FakeTelegram:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def call(self, method, payload):
        self.calls.append((method, payload))
        if self.fail:
            raise RuntimeError("uncertain synthetic transport")
        return {
            "message_id": 9,
            "chat": {"id": payload["chat_id"], "type": "private"},
        }


class WS3OriginalRadarBridgeTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        base = Path(t.name)
        self.db_path = base / "decisions.sqlite3"
        with sqlite3.connect(self.db_path) as db:
            db.execute("""
                CREATE TABLE m03_ws_summary_watch_minute(
                    pair TEXT NOT NULL, minute_epoch INTEGER NOT NULL,
                    exchange_epoch INTEGER NOT NULL, last_idr TEXT NOT NULL,
                    rolling_24h_idr TEXT NOT NULL, public_offset INTEGER NOT NULL,
                    PRIMARY KEY(pair,minute_epoch)
                )
            """)
        self.db_path.chmod(0o600)
        self.owner_path = base / "owner.sqlite3"
        self.ledger = OwnerDecisionLedger(
            self.owner_path, owner_user_id=456789123,
        )
        self.owner_path.chmod(0o600)

    def add(self, pair, minute_delta, price, turnover, offset,
            *, exchange_offset_seconds=42):
        minute = NOW + minute_delta * 60
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO m03_ws_summary_watch_minute VALUES (?,?,?,?,?,?)",
                (pair, minute, minute + exchange_offset_seconds,
                 str(price), str(turnover), offset),
            )

    def seed(self):
        self.add("uai_idr", -2, 100, 500000000, 1)
        self.add("uai_idr", -1, 101, 500030000, 2)

    def inspect(self, now=NOW):
        return inspect_original_m03_ws3_watch_readonly(
            db_path=self.db_path, now_epoch=now,
        )

    def send(self, api, ws3_enabled=True, now=NOW):
        return send_one_owner_radar_momentum_nonlive(
            api=api, ledger=self.ledger,
            db_path=self.db_path, now_epoch=now,
            ws3_enabled=ws3_enabled,
        )

    def test_real_schema_ws3_watch_candidate_no_fabricated_orderflow(self):
        self.seed()
        snap = self.inspect()
        self.assertEqual(snap["status"],
                         "M03_WS3_WATCH_RADAR_OBSERVED_UNATTESTED")
        self.assertEqual(snap["candidates"][0]["pair"], "uai_idr")
        self.assertEqual(snap["candidates"][0]["observed_rise_pct"], "1.0000")
        self.assertFalse(snap["candidates"][0]["trade_direction_verified"])
        self.assertFalse(snap["candidates"][0]["bid_ask_spread_verified"])
        self.assertFalse(snap["source_authenticated_for_g1"])
        self.assertFalse(snap["execution_allowed"])

    def test_live_exchange_timestamp_inside_minute_is_accepted(self):
        # A real WS3 20:52:42 event is later than the bucket start 20:52:00.
        self.seed()
        row = self.inspect()["candidates"][0]
        self.assertEqual(row["observed_epoch"], NOW - 60 + 42)
        self.assertEqual(row["source_elapsed_seconds"], 60)
        self.assertGreater(row["observed_epoch"], NOW - 60)

    def test_recent_previous_minute_publication_is_allowed(self):
        self.add("uai_idr", -2, 100, 500000000, 1,
                 exchange_offset_seconds=-20)
        self.add("uai_idr", -1, 101, 500030000, 2,
                 exchange_offset_seconds=-15)
        snap = self.inspect()
        self.assertEqual(len(snap["candidates"]), 1)
        self.assertEqual(snap["candidates"][0]["pair"], "uai_idr")

    def test_invalid_exchange_offset_after_minute_end_rejected(self):
        self.add("uai_idr", -2, 100, 500000000, 1)
        self.add("uai_idr", -1, 101, 500030000, 2,
                 exchange_offset_seconds=60)
        self.assertEqual(self.inspect()["candidates"], [])

    def test_unreasonably_old_exchange_offset_rejected(self):
        self.add("uai_idr", -2, 100, 500000000, 1)
        self.add("uai_idr", -1, 101, 500030000, 2,
                 exchange_offset_seconds=-91)
        self.assertEqual(self.inspect()["candidates"], [])


    def test_three_snapshot_acceleration_is_ranked_before_one_step_pump(self):
        for minute, price, vol in (
            (-3, "100", "500000000"),
            (-2, "100.1", "500020000"),
            (-1, "100.5", "500150000"),
        ):
            self.add("early_idr", minute, price, vol, minute + 30)
        self.add("pump_idr", -2, "100", "500000000", 20)
        self.add("pump_idr", -1, "110", "500250000", 21)
        candidates = self.inspect()["candidates"]
        self.assertEqual(candidates[0]["pair"], "early_idr")
        self.assertEqual(candidates[0]["momentum_phase"],
                         "EARLY_ACCELERATION_WATCH")
        self.assertEqual(candidates[1]["momentum_phase"],
                         "EXTENDED_MOVE_REVIEW")
        self.assertIsNone(candidates[0]["actual_1m_executed_volume_idr"])
        self.assertIsNone(candidates[0]["orderbook_spread_bps"])
        self.assertIsNone(candidates[0]["three_ai_role_responses"])

    def test_negative_rolling_turnover_never_becomes_buy_pressure(self):
        self.add("uai_idr", -2, 100, 500000000, 1)
        self.add("uai_idr", -1, 101, 499900000, 2)
        row = self.inspect()["candidates"][0]
        self.assertFalse(row["rolling_24h_delta_positive"])
        self.assertEqual(row["momentum_phase"],
                         "PRICE_RISE_ACTIVITY_UNCONFIRMED")
        self.assertIsNone(row["trade_buy_sell_counts"])

    def test_weak_single_step_not_called_early_ramp(self):
        self.add("uai_idr", -2, 100, 500000000, 1)
        self.add("uai_idr", -1, "100.3", 500050000, 2)
        self.assertEqual(self.inspect()["candidates"], [])


    def test_source_observed_trade_and_book_joins_ws3_watch_without_g1(self):
        self.seed()
        with sqlite3.connect(self.db_path) as db:
            db.execute("""
                CREATE TABLE m03_ws_microstructure_watch_minute(
                  pair TEXT NOT NULL, minute_epoch INTEGER NOT NULL,
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
                  PRIMARY KEY(pair,minute_epoch))
            """)
            db.execute("""
                INSERT INTO m03_ws_microstructure_watch_minute
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                "uai_idr", NOW - 60, NOW - 5, 1, 2, 1,
                "500000", "90000", NOW - 8, 42,
                0, 0, 51, NOW - 5, "100", "101", "99.5025",
                "7000000", "4000000", "1.7500", 0,
            ))
        row = self.inspect()["candidates"][0]
        micro = row["microstructure_observation"]
        self.assertTrue(micro["trade_activity_observed"])
        self.assertTrue(micro["orderbook_snapshot_observed"])
        self.assertEqual(micro["source_reported_buy_count"], 2)
        self.assertEqual(micro["source_reported_sell_count"], 1)
        self.assertEqual(micro["spread_bps"], "99.5025")
        self.assertFalse(micro["complete_trade_tape_verified"])
        self.assertFalse(row["trade_direction_verified"])
        self.assertFalse(row["g1_verified"])

    def test_old_micro_rows_cannot_upgrade_watch_or_fake_flow(self):
        self.seed()
        with sqlite3.connect(self.db_path) as db:
            db.execute("""
                CREATE TABLE m03_ws_microstructure_watch_minute(
                  pair TEXT NOT NULL, minute_epoch INTEGER NOT NULL,
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
                  PRIMARY KEY(pair,minute_epoch))
            """)
            db.execute("""
                INSERT INTO m03_ws_microstructure_watch_minute
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                "uai_idr", NOW - 60, NOW - 300, 1, 2, 1,
                "500000", "90000", NOW - 301, 42,
                0, 0, 51, NOW - 300, "100", "101", "99.5025",
                "7000000", "4000000", "1.7500", 0,
            ))
        micro = self.inspect()["candidates"][0]["microstructure_observation"]
        self.assertEqual(micro["status"], "NOT_OBSERVED")
        self.assertFalse(micro["trade_activity_observed"])


    def test_micro_discovery_uses_weak_positive_price_not_telegram_alert(self):
        self.add("uai_idr", -2, 100, 500000000, 1)
        self.add("uai_idr", -1, "100.20", 500050000, 2)
        self.assertEqual(self.inspect()["candidates"], [])
        from indodax_ai_bot.m05_radar_momentum_nonlive import (
            inspect_original_m03_ws3_micro_discovery_readonly,
        )
        report = inspect_original_m03_ws3_micro_discovery_readonly(
            db_path=self.db_path, now_epoch=NOW,
        )
        self.assertEqual(report["pairs"], ["uai_idr"])
        self.assertEqual(report["status"],
                         "M03_MICRO_DISCOVERY_PAIRS_SELECTED")
        self.assertEqual(report["weak_price_rise_pair_count"], 1)
        self.assertFalse(report["selection_is_m05_alert"])
        self.assertFalse(report["execution_allowed"])

    def test_micro_capture_second_slot_scans_real_weak_uai_before_alert(self):
        # Simulated three real minute-bucket observations: no future candles
        # or execution. Previously both stronger risers filled two slots.
        from indodax_ai_bot.m05_radar_momentum_nonlive import (
            inspect_original_m03_ws3_micro_discovery_readonly,
        )
        for pair, price in (("fast_idr", "102"),
                            ("later_idr", "101.6"),
                            ("uai_idr", "100.2")):
            self.add(pair, -2, "100", 500000000, 10)
            self.add(pair, -1, price, 500050000, 11)
        self.assertNotIn("uai_idr", [
            row["pair"] for row in self.inspect()["candidates"]
        ])
        report = inspect_original_m03_ws3_micro_discovery_readonly(
            db_path=self.db_path, now_epoch=NOW,
        )
        self.assertEqual(report["pairs"], ["fast_idr", "uai_idr"])
        self.assertEqual(report["weak_price_rise_pair_count"], 3)
        self.assertFalse(report["selection_is_m05_alert"])
        self.assertFalse(report["execution_allowed"])
        self.assertIn("EARLY_BELOW_ALERT_SCOUT", report["selection_policy"])
        one = inspect_original_m03_ws3_micro_discovery_readonly(
            db_path=self.db_path, now_epoch=NOW, max_pairs=1,
        )
        self.assertEqual(one["pairs"], ["fast_idr"])

    def test_micro_early_scout_rotates_without_promoting_negative_delta(self):
        from indodax_ai_bot.m05_radar_momentum_nonlive import (
            inspect_original_m03_ws3_micro_discovery_readonly,
        )
        for pair, pct, delta in (
            ("fast_idr", "102", 500050000),
            ("uai_idr", "100.2", 500050000),
            ("earlyb_idr", "100.3", 500050000),
            ("deceive_idr", "100.1", 499950000),
        ):
            self.add(pair, -2, "100", 500000000, 10)
            self.add(pair, -1, pct, delta, 11)
        first = inspect_original_m03_ws3_micro_discovery_readonly(
            db_path=self.db_path, now_epoch=NOW,
        )
        second = inspect_original_m03_ws3_micro_discovery_readonly(
            db_path=self.db_path, now_epoch=NOW+60,
        )
        self.assertEqual(first["pairs"][0], "fast_idr")
        self.assertEqual(second["pairs"][0], "fast_idr")
        self.assertEqual({first["pairs"][1], second["pairs"][1]},
                         {"uai_idr", "earlyb_idr"})
        self.assertNotIn("deceive_idr", first["pairs"] + second["pairs"])
        self.assertFalse(first["trade_activity_observed"])
        self.assertFalse(second["execution_allowed"])

    def test_micro_discovery_rotates_recent_liquid_baseline_without_rise(self):
        from indodax_ai_bot.m05_radar_momentum_nonlive import (
            inspect_original_m03_ws3_micro_discovery_readonly,
        )
        for i in range(3):
            pair = ("btc", "eth", "sol")[i] + "_idr"
            self.add(pair, -1, 100, 500000000 - i*10000000, i + 1)
        report = inspect_original_m03_ws3_micro_discovery_readonly(
            db_path=self.db_path, now_epoch=NOW,
        )
        self.assertEqual(len(report["pairs"]), 2)
        self.assertEqual(report["weak_price_rise_pair_count"], 0)
        self.assertEqual(report["fresh_liquid_pair_count"], 3)
        self.assertFalse(report["selection_is_m05_alert"])

    def test_micro_discovery_stale_low_turnover_and_manual_assets_excluded(self):
        from indodax_ai_bot.m05_radar_momentum_nonlive import (
            inspect_original_m03_ws3_micro_discovery_readonly,
        )
        self.add("uai_idr", -5, 101, 500000000, 1)
        self.add("thin_idr", -1, 100, 100000000, 2)
        self.add("floki_idr", -1, 100, 900000000, 3)
        self.add("pepe_idr", -1, 100, 900000000, 4)
        report = inspect_original_m03_ws3_micro_discovery_readonly(
            db_path=self.db_path, now_epoch=NOW,
        )
        self.assertEqual(report["pairs"], [])
        self.assertFalse(report["execution_allowed"])

    def test_existing_owner_poller_sender_reuses_durable_claim_and_no_buttons(self):
        self.seed()
        api = FakeTelegram()
        result = self.send(api)
        self.assertEqual(result["status"], "M05_RADAR_OWNER_PRIVATE_NOTICE_SENT")
        self.assertEqual(len(api.calls), 1)
        payload = api.calls[0][1]
        self.assertIn("WS3 WATCH", payload["text"])
        self.assertIn("NOT a closed 1m candle", payload["text"])
        self.assertIn("NOT A BUY RECOMMENDATION", payload["text"])
        self.assertIn("UNVERIFIED", payload["text"])
        self.assertNotIn("reply_markup", payload)
        self.assertEqual(self.send(api)["messages_sent"], 0)
        self.assertEqual(len(api.calls), 1)

    def test_default_off_stays_original_rest_only(self):
        self.seed()
        api = FakeTelegram()
        self.assertEqual(self.send(api, ws3_enabled=False)["messages_sent"], 0)
        self.assertFalse(api.calls)

    def test_stale_and_zero_price_delta_rejected(self):
        self.seed()
        self.assertEqual(self.inspect(now=NOW+240)["candidates"], [])
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "UPDATE m03_ws_summary_watch_minute SET last_idr='100' "
                "WHERE minute_epoch=?", (NOW-60,),
            )
        self.assertEqual(self.inspect()["candidates"], [])

    def test_liquidity_floor_and_reserved_pair_not_promoted(self):
        self.add("floki_idr", -2, 100, 999999999, 1)
        self.add("floki_idr", -1, 120, 999999999, 2)
        self.add("low_idr", -2, 100, 120000000, 1)
        self.add("low_idr", -1, 130, 120000000, 2)
        self.assertEqual(self.inspect()["candidates"], [])

    def test_claim_before_uncertain_send_never_retries(self):
        self.seed()
        failed = FakeTelegram(fail=True)
        self.assertEqual(self.send(failed)["messages_sent"], 0)
        retry = FakeTelegram()
        self.assertEqual(self.send(retry)["messages_sent"], 0)
        self.assertFalse(retry.calls)

    def test_ws3_new_flag_only_adds_source_to_existing_callback(self):
        cfg = {"M05_TELEGRAM_FAST_LONGPOLL_GO": "YES",
               "M05_RADAR_MOMENTUM_ALERT_GO": "YES",
               "M05_WS3_RADAR_WATCH_GO": "YES"}
        with (patch.object(
                telegram_service_tick, "_prepare_tick_runtime",
                return_value=("fake-api", "fake-ledger", 123, "fake-db", None)),
              patch.object(
                telegram_service_tick, "dedicated_owner_fast_loop_nonlive",
                return_value={"status": "OFFLINE"}) as run):
            telegram_service_tick.service_fast_loop_nonlive(
                env=cfg, max_batches=1,
            )
            callback = run.call_args.kwargs["auto_radar_alert"]
            self.assertTrue(callable(callback))
            self.assertTrue(callback.keywords["ws3_enabled"])
            cfg["M05_WS3_RADAR_WATCH_GO"] = "NO"
            telegram_service_tick.service_fast_loop_nonlive(
                env=cfg, max_batches=1,
            )
            self.assertIsNone(getattr(
                run.call_args.kwargs["auto_radar_alert"], "keywords", None
            ))


if __name__ == "__main__":
    unittest.main()
