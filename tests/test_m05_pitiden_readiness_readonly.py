"""Offline verification of the original M03 read-only source audit."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.m05_pitiden_readiness_readonly import inspect_readiness, inspect_named_pairs, probe_recent_trade_coverage
from indodax_ai_bot.public_market import (IndodaxPublicV2RecentTradesResearch, PublicDataError,
                                         summarize_v2_trade_page_research)

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


class PitidenTradeCoverageTests(unittest.TestCase):
    STOP=NOW//60*60
    FLAGS={"INDODAX_TRADING_ENABLED":"NO","INDODAX_ALLOW_LIVE_EXECUTION":"NO",
           "INDODAX_EXECUTION_MODE":"DRY_RUN","INDODAX_M03_REQUIRE_EGRESS_ADMISSION":"NO"}

    def item(self,tid,ms,amount="125.25",price="100"):
        return {"id":tid,"time":ms,"quoteQty":amount,"price":price,
                "qty":"1.25","isBestMatch":True}

    def page(self):
        b=self.STOP*1000
        a=b-60000
        return [self.item(4,b),self.item(3,b-1,"20.75","103"),
                self.item(2,a,"10.25","101"),self.item(1,a-1)]

    def evaluate(self,page):
        return summarize_v2_trade_page_research(page,requested_epoch=NOW,received_epoch=NOW+1)

    def test_closed_window_exact_boundary_quote_amount_and_no_full_tape_claim(self):
        out=self.evaluate(self.page())
        row=out["timeframes"][0]
        self.assertEqual(row["tf"],"1m")
        self.assertEqual(row["sample_quote_idr"],"31.00")
        self.assertEqual(row["trade_count_observed"],2)
        self.assertEqual(row["first_observed_price_idr"],"101")
        self.assertEqual(row["last_observed_price_idr"],"103")
        self.assertTrue(row["page_bracketed"])
        self.assertEqual(row["coverage"],"PAGE_BRACKETED_UNATTESTED")
        self.assertFalse(row["full_volume_verified"])
        self.assertFalse(out["complete_trade_tape_verified"])
        self.assertEqual(len(out["timeframes"]),7)
        self.assertEqual(out["timeframes"][-1]["coverage"],"NO_TRADES_OBSERVED_UNVERIFIED")
        self.assertIsNone(out["timeframes"][-1]["sample_quote_idr"])

    def test_truncated_page_and_tied_timestamp_never_certify_full_volume(self):
        b=self.STOP*1000
        page=[self.item(i+1,b-1000-i) for i in range(499)]+[self.item(500,b)]
        out=self.evaluate(page)
        self.assertTrue(out["page_at_limit"])
        self.assertEqual(out["timeframes"][0]["coverage"],"PARTIAL_PAGE")
        self.assertFalse(out["timeframes"][0]["page_bracketed"])
        self.assertFalse(out["timeframes"][0]["full_volume_verified"])
        p=self.page()
        p.append(self.item(5,p[1]["time"]))
        self.assertTrue(self.evaluate(p)["timeframes"][0]["boundary_price_order_unverified"])

    def test_empty_duplicate_malformed_future_and_invalid_clock(self):
        self.assertTrue(all(r["sample_quote_idr"] is None for r in self.evaluate([])["timeframes"]))
        p=self.page()
        for page in (p+[p[0]], [dict(p[0],time=NOW)], [dict(p[0],time=(NOW+3)*1000)],
                     [dict(p[0],quoteQty="NaN")], [dict(p[0],id=True)],{"code":-1121}):
            with self.assertRaises(PublicDataError):
                self.evaluate(page)
        with self.assertRaises(PublicDataError):
            summarize_v2_trade_page_research(p,requested_epoch=NOW,received_epoch=NOW+31)

    def test_owner_probe_path_bounds_and_no_retry_after_failure(self):
        calls=[]
        def fetch(path):
            calls.append(path)
            if len(calls)>1:
                raise PublicDataError("PUBLIC_REST_HTTP_403_FORBIDDEN")
            return self.page()
        with patch.dict("os.environ",self.FLAGS,clear=False):
            client=IndodaxPublicV2RecentTradesResearch(fetch=fetch)
            out=probe_recent_trade_coverage(pairs=("vine_idr","rare_idr","cst_idr"),
                                             client=client,clock=lambda:NOW)
            self.assertEqual(out["public_get_invocations"],2)
            self.assertEqual(calls,["/api/v2/trades?symbol=VINEIDR&limit=500",
                                    "/api/v2/trades?symbol=RAREIDR&limit=500"])
            self.assertEqual(out["pairs"][1]["reason"],"PUBLIC_REST_HTTP_403_FORBIDDEN")
            self.assertEqual(out["unattempted_pairs"],["cst_idr"])
            self.assertFalse(out["runtime_market_data_written"])
            self.assertFalse(out["execution_allowed"])
            self.assertEqual(out["provider_calls"],0)
            for pairs in (("floki_idr",),("btc_usdt",),("vine_idr","vine_idr"),
                          ("a_idr","b_idr","c_idr","d_idr","e_idr")):
                self.assertEqual(probe_recent_trade_coverage(pairs=pairs,client=client)["public_get_invocations"],0)
            with patch.dict("os.environ",{"INDODAX_TRADING_ENABLED":"YES"}):
                self.assertEqual(probe_recent_trade_coverage(pairs=("vine_idr",),client=client)["public_get_invocations"],0)
            self.assertEqual(len(calls),2)

    def test_real_probe_always_requires_existing_admission_before_transport(self):
        with patch.dict("os.environ",self.FLAGS,clear=False):
            with patch("indodax_ai_bot.public_market.M03SharedEgressAdmission") as admission, \
                 patch("indodax_ai_bot.public_market.IndodaxPublicMarket._http_json") as transport:
                from indodax_ai_bot.m03_egress_admission import M03EgressAdmissionError
                admission.return_value.allowed_local_limit.side_effect=M03EgressAdmissionError(
                    "M03_EGRESS_POLICY_UNAVAILABLE")
                client=IndodaxPublicV2RecentTradesResearch()
                out=probe_recent_trade_coverage(pairs=("vine_idr",),client=client,clock=lambda:NOW)
                self.assertEqual(out["pairs"][0]["reason"],"M03_EGRESS_POLICY_UNAVAILABLE")
                transport.assert_not_called()
                admission.return_value.allowed_local_limit.assert_called_once_with(require_btc_source=True)
