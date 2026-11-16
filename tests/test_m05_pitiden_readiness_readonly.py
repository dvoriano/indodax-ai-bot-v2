"""Offline verification of the original M03 read-only source audit."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.m05_pitiden_readiness_readonly import inspect_readiness

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
