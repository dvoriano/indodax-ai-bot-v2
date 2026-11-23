"""One existing M03 WS3 DB to existing two M04 three-role INPUT packets.

Offline synthetic SQLite only. No network, Telegram, paid AI, G1 or orders.
"""
from __future__ import annotations

from decimal import Decimal as D
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.m04_radar_three_role_handoff_readonly import (
    inspect_original_ws3_three_role_handoff_readonly,
)
from indodax_ai_bot.m04_ws3_indexed_observation_nonlive import (
    inspect_ws3_indexed_trade_asof_nonlive,
)

NOW=1794330000
OBS=NOW-25


class OriginalWS3M04IndexedAsOfTests(unittest.TestCase):
    def setUp(self):
        td=TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.db=Path(td.name)/"decisions.sqlite3"
        with sqlite3.connect(self.db) as con:
            con.executescript("""
                CREATE TABLE m03_ws_summary_watch_minute(
                    pair TEXT,minute_epoch INTEGER,exchange_epoch INTEGER,
                    last_idr TEXT,rolling_24h_idr TEXT,public_offset INTEGER,
                    PRIMARY KEY(pair,minute_epoch));
            """)
        self.db.chmod(0o600)

    def seed(self, pair):
        with sqlite3.connect(self.db) as con:
            con.executemany(
                "INSERT INTO m03_ws_summary_watch_minute VALUES(?,?,?,?,?,?)",(
                (pair,NOW-120,NOW-85,"100","750000000",11),
                (pair,NOW-60,OBS,"101","760000000",12),
            ))

    def install_index(self):
        with sqlite3.connect(self.db) as con:
            con.executescript("""
                CREATE TABLE m03_ws3_trade_samples(
                    pair TEXT NOT NULL,exchange_epoch INTEGER NOT NULL,
                    sequence INTEGER NOT NULL,price_idr TEXT NOT NULL,
                    quote_idr TEXT NOT NULL,recorded_epoch INTEGER NOT NULL,
                    PRIMARY KEY(pair,exchange_epoch,sequence));
                CREATE TABLE m03_ws3_price_samples_5s(
                    pair TEXT,bucket_epoch INTEGER,exchange_epoch INTEGER,
                    last_idr TEXT,public_offset INTEGER,recorded_epoch INTEGER,
                    PRIMARY KEY(pair,bucket_epoch));
                CREATE TABLE ws_stream_state(
                    pair TEXT PRIMARY KEY,epoch TEXT,last_offset INTEGER,status TEXT);
            """)

    def add_trade(self,pair,ts,seq,price,quote,receipt=None):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO m03_ws3_trade_samples VALUES(?,?,?,?,?,?)",
                        (pair,ts,seq,price,quote,ts if receipt is None else receipt))

    def test_original_two_role_packets_bind_same_asof_trade_digest(self):
        self.seed("uai_idr")
        self.seed("cst_idr")
        self.install_index()
        close=OBS//60*60
        self.add_trade("uai_idr",close-55,1,"100","1000")
        self.add_trade("uai_idr",close-5,2,"102","2000")
        # A trade with exchange timestamp inside the window but RECEIPT
        # later than the original alert source is forbidden lookahead.
        self.add_trade("uai_idr",close-10,3,"999","9000",OBS+1)
        self.add_trade("cst_idr",close-30,1,"50","700")
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO ws_stream_state VALUES(?,?,?,?)",
                        ("uai_idr","123",10,"CONTIGUOUS_OBSERVED"))
            con.executemany("INSERT INTO m03_ws3_price_samples_5s VALUES(?,?,?,?,?,?)",(
                ("uai_idr",OBS,OBS,"101",1,OBS),
                ("uai_idr",OBS-5,OBS-5,"999",2,OBS+2),
            ))
        report=inspect_original_ws3_three_role_handoff_readonly(
            db_path=self.db,now_epoch=NOW)
        self.assertEqual(report["status"],"M04_WS3_HANDOFF_ROLE_INPUTS_PREPARED")
        self.assertEqual(report["candidate_count"],2)
        self.assertEqual(report["indexed_asof_candidates_attached"],2)
        self.assertFalse(report["indexed_trade_source_is_complete"])
        self.assertEqual(report["actual_three_role_ai_calls"],0)
        by_pair={r["pair"]:r for r in report["candidates"]}
        self.assertEqual(set(by_pair),{"uai_idr","cst_idr"})
        uai=by_pair["uai_idr"]
        packet=uai["public_evidence"]
        evidence=packet["indexed_trade_window_evidence"]
        self.assertEqual(evidence["candidate_observed_epoch"],OBS)
        self.assertEqual(evidence["closed_windows"]["1m"]["locally_observed_trade_rows"],2)
        self.assertEqual(evidence["closed_windows"]["1m"]["observed_quote_idr_only"],"3000")
        self.assertEqual(evidence["closed_windows"]["1m"]["observed_high_price_idr"],"102")
        self.assertFalse(evidence["closed_windows"]["1m"]["full_window_coverage_verified"])
        self.assertEqual(evidence["local_price_5s_asof"]["last_idr"],"101")
        self.assertEqual(evidence["journal_state_at_inspection_not_historical"],
                         "CONTIGUOUS_OBSERVED")
        self.assertFalse(packet["complete_trade_tape_verified"])
        self.assertFalse(packet["independent_g1_issuer_verified"])
        self.assertFalse(uai["execution_allowed"])
        canonical=sha256(json.dumps(packet,sort_keys=True,separators=(",",":"),
                                    ensure_ascii=True).encode("ascii")).hexdigest()
        self.assertEqual(uai["evidence_digest_sha256"],canonical)
        self.assertEqual([slot["role"] for slot in uai["role_slots"]],
                         ["MARKET","RISK","STRATEGY"])
        self.assertEqual({x["evidence_digest_sha256"] for x in uai["role_slots"]},
                         {canonical})
        self.assertEqual(
            by_pair["cst_idr"]["public_evidence"][
                "indexed_trade_window_evidence"]["closed_windows"]["1m"]["observed_quote_idr_only"],
            "700")

    def test_one_corrupt_local_tape_does_not_impersonate_other_pair(self):
        self.seed("uai_idr")
        self.seed("cst_idr")
        self.install_index()
        close=OBS//60*60
        self.add_trade("uai_idr",close-30,1,"100","1000")
        self.add_trade("cst_idr",close-30,1,"NaN","500")
        result=inspect_original_ws3_three_role_handoff_readonly(
            db_path=self.db,now_epoch=NOW)
        by_pair={r["pair"]:r for r in result["candidates"]}
        self.assertEqual(by_pair["cst_idr"]["public_evidence"][
            "indexed_trade_window_evidence"]["status"],
            "INDEXED_LOCAL_EVIDENCE_INVALID_OR_UNREADABLE")
        self.assertEqual(by_pair["uai_idr"]["public_evidence"][
            "indexed_trade_window_evidence"]["status"],
            "OBSERVED_LOCAL_TRADE_SAMPLES_ONLY")
        for row in result["candidates"]:
            self.assertFalse(row["execution_allowed"])
            self.assertIsNone(row["role_slots"][0]["model_output"])

    def test_without_original_trade_index_original_m04_remains_available(self):
        self.seed("uai_idr")
        report=inspect_original_ws3_three_role_handoff_readonly(
            db_path=self.db,now_epoch=NOW)
        self.assertEqual(report["candidate_count"],1)
        self.assertEqual(report["indexed_trade_window_source"],
                         "INDEX_NOT_INSTALLED_OR_NOT_READABLE")
        self.assertNotIn("indexed_trade_window_evidence",
                         report["candidates"][0]["public_evidence"])

    def test_bad_scope_fails_closed_no_snapshot_or_writes(self):
        self.seed("uai_idr")
        self.install_index()
        self.assertIsNone(inspect_ws3_indexed_trade_asof_nonlive(
            db_path=self.db.parent/"absent.sqlite3",
            candidate_epochs={"uai_idr":OBS},now_epoch=NOW))
        with self.assertRaisesRegex(ValueError,"M04_WS3_INDEXED_INVALID_SCOPE"):
            inspect_ws3_indexed_trade_asof_nonlive(
                db_path=self.db,candidate_epochs={"uai_idr":NOW+3},
                now_epoch=NOW)


if __name__=="__main__":
    unittest.main()
