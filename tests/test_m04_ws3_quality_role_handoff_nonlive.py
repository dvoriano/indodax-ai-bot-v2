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
