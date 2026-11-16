"""Offline verification of the original M03 read-only source audit."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.m05_pitiden_readiness_readonly import inspect_readiness, inspect_named_pairs

NOW=1794800005
MINUTE=NOW//60*60-60

class PitidenReadinessTests(unittest.TestCase):
    def test_stale_and_fresh_source_are_not_confused_with_send(self):
        with tempfile.TemporaryDirectory() as root:
            db=Path(root)/"original.db"
            archive=Path(root)/"not-present.db"
            with sqlite3.connect(db) as con:
                con.execute("""CREATE TABLE m03_ws_summary_watch_minute(
                    pair TEXT,minute_epoch INTEGER,exchange_epoch INTEGER,
                    last_idr TEXT,rolling_24h_idr TEXT,public_offset INTEGER,
                    PRIMARY KEY(pair,minute_epoch))""")
                con.executemany("INSERT INTO m03_ws_summary_watch_minute VALUES (?,?,?,?,?,?)",[
                    ("btc_idr",MINUTE-120,MINUTE-71,"99","500000000",0),
                    ("btc_idr",MINUTE-60,MINUTE-11,"100","500000000",1),
                    ("btc_idr",MINUTE,MINUTE+49,"103","500000000",2),
                    ("thin_idr",MINUTE,MINUTE+49,"100","399999999",3),
                ])
            db.chmod(0o600)
            before=db.read_bytes()
            result=inspect_readiness(now=NOW,db_path=db,evidence_path=archive)
            self.assertEqual(result["status"],"PITIDEN_READONLY_SOURCE_AUDIT")
            self.assertEqual(result["liquid_pairs_latest_bucket"],1)
            self.assertEqual(result["fresh_tested_pairs"],1)
            self.assertEqual(result["coverage"]["1m"],[1,1])
            self.assertEqual(result["tested_pairs"][0]["green"],1)
            self.assertEqual(result["telegram_messages_sent"],0)
            self.assertTrue(result["gate_only_is_not_send_authority"])
            stale=inspect_readiness(now=NOW+200,db_path=db,evidence_path=archive)
            self.assertEqual(stale["fresh_tested_pairs"],0)
            self.assertEqual(stale["fresh_premium_gate_only"],0)
            self.assertEqual(db.read_bytes(),before)
            self.assertFalse(archive.exists())

    def test_absent_original_db_not_created(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/"absent.db"
            result=inspect_readiness(now=NOW,db_path=p)
            self.assertEqual(result["status"],"ORIGINAL_DB_UNTRUSTED")
            self.assertFalse(p.exists())

    def test_named_pair_gate_reconstruction_price_volume_missing_and_readonly(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/"original.db"
            archive=Path(root)/"absent.db"
            with sqlite3.connect(p) as db:
                db.execute("""CREATE TABLE m03_ws_summary_watch_minute(
                    pair TEXT,minute_epoch INTEGER,exchange_epoch INTEGER,
                    last_idr TEXT,rolling_24h_idr TEXT,public_offset INTEGER,
                    PRIMARY KEY(pair,minute_epoch))""")
                db.executemany("INSERT INTO m03_ws_summary_watch_minute VALUES (?,?,?,?,?,?)",[
                    ("btc_idr",MINUTE-60,MINUTE-11,"100","500000000",1),
                    ("btc_idr",MINUTE,MINUTE+49,"103","500000000",2),
                    ("thin_idr",MINUTE,MINUTE+49,"100","399999999",3),
                ])
            p.chmod(0o600)
            before=p.read_bytes()
            out=inspect_named_pairs(pairs=("btc_idr","thin_idr","rare_idr"),
                                    now=NOW,db_path=p,evidence_path=archive)
            rows={r["pair"]:r for r in out["pairs"]}
            btc=rows["btc_idr"]
            self.assertEqual(btc["last_idr"],"103")
            self.assertEqual(btc["rolling_24h_idr"],"500000000")
            self.assertEqual(btc["reason"],"BELOW_FOUR_GREEN")
            self.assertEqual(rows["thin_idr"]["reason"],"BELOW_PREMIUM_VOLUME_FLOOR")
            self.assertEqual(rows["rare_idr"]["reason"],"NO_RETAINED_WATCH_SAMPLE")
            self.assertEqual(len(btc["timeframes"]),8)
            self.assertIsNone(btc["timeframes"][0]["volume_idr"])
            self.assertFalse(out["execution_allowed"])
            self.assertEqual(before,p.read_bytes())
            self.assertFalse(archive.exists())
