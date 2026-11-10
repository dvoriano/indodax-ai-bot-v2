"""WS3 original V2 evidence to MARKET/RISK/STRATEGY uncalled role slots."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from indodax_ai_bot.m04_radar_three_role_handoff_readonly import (
    inspect_original_ws3_three_role_handoff_readonly,
    prepare_ws3_three_role_watch_handoff_readonly,
)
from indodax_ai_bot.m05_radar_momentum_nonlive import (
    inspect_original_m03_ws3_watch_readonly,
)
NOW = 1794330000


class M04WS3QualityHandoffTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "decisions.sqlite3"
        with sqlite3.connect(self.path) as db:
            db.execute("""
                CREATE TABLE m03_ws_summary_watch_minute(
                    pair TEXT NOT NULL, minute_epoch INTEGER NOT NULL,
                    exchange_epoch INTEGER NOT NULL, last_idr TEXT NOT NULL,
                    rolling_24h_idr TEXT NOT NULL, public_offset INTEGER NOT NULL,
                    PRIMARY KEY(pair,minute_epoch))
            """)
        self.path.chmod(0o600)

    def row(self, pair, offset, price, turnover):
        minute = NOW + 60 * offset
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO m03_ws_summary_watch_minute VALUES(?,?,?,?,?,?)",
                (pair, minute, minute + 35, str(price), str(turnover), 100 + offset),
            )

    def seed(self):
        self.row("uai_idr", -2, 100, 700000000)
        self.row("uai_idr", -1, 101, 701000000)

    def test_same_digest_three_roles_unexecuted(self):
        self.seed()
        r = inspect_original_ws3_three_role_handoff_readonly(
            db_path=self.path, now_epoch=NOW)
        self.assertEqual(r["status"], "M04_WS3_HANDOFF_ROLE_INPUTS_PREPARED")
        self.assertEqual(r["candidate_count"], 1)
        self.assertEqual(r["three_role_input_slots_prepared"], 3)
        self.assertEqual(r["actual_three_role_ai_calls"], 0)
        self.assertEqual(r["provider_http_calls"], 0)
        self.assertFalse(r["g1_independent_issuer_verified"])
        self.assertFalse(r["execution_allowed"])
        row = r["candidates"][0]
        self.assertEqual([x["role"] for x in row["role_slots"]],
                         ["MARKET", "RISK", "STRATEGY"])
        self.assertTrue(all(x["model_output"] is None for x in row["role_slots"]))
        self.assertTrue(all(x["evidence_digest_sha256"] ==
                            row["evidence_digest_sha256"]
                            for x in row["role_slots"]))
        self.assertEqual(row["public_evidence"]["orderbook"], "NOT_OBSERVED")
        self.assertEqual(row["public_evidence"]["trade_activity"], "NOT_OBSERVED")

    def test_no_source_no_ai_slots(self):
        r = inspect_original_ws3_three_role_handoff_readonly(
            db_path=self.path, now_epoch=NOW)
        self.assertEqual(r["candidate_count"], 0)


    def test_existing_three_ai_slots_include_only_observed_micro_sample(self):
        self.seed()
        with sqlite3.connect(self.path) as db:
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
                "uai_idr", NOW - 60, NOW - 5, 1, 3, 1,
                "500000", "100000", NOW - 8, 40,
                0, 0, 51, NOW - 5, "100", "101", "99.5025",
                "9000000", "4000000", "2.2500", 0,
            ))
        report = inspect_original_ws3_three_role_handoff_readonly(
            db_path=self.path, now_epoch=NOW,
        )
        row = report["candidates"][0]
        packet = row["public_evidence"]
        self.assertEqual(packet["trade_activity"], "PARTIAL_WS3_CAPTURE")
        self.assertEqual(packet["orderbook"], "PARTIAL_LOCAL_WS3_BOOK_SNAPSHOT")
        self.assertFalse(packet["trade_side_aggressor_qualified"])
        self.assertFalse(packet["complete_trade_tape_verified"])
        self.assertTrue(all(x["model_output"] is None
                            for x in row["role_slots"]))
        self.assertEqual(report["actual_three_role_ai_calls"], 0)

    def test_rejects_tampered_pair_price_or_g1(self):
        self.seed()
        for key, invalid in (("latest_price_idr", "150"),
                             ("g1_verified", True)):
            snap = inspect_original_m03_ws3_watch_readonly(
                db_path=self.path, now_epoch=NOW)
            snap["candidates"][0][key] = invalid
            r = prepare_ws3_three_role_watch_handoff_readonly(
                snapshot=snap, now_epoch=NOW)
            self.assertEqual(r["status"], "M04_WS3_INVALID_CANDIDATE_PACKET")
            self.assertEqual(r["candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
