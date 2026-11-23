"""Active roadmap M03 WS3 -> M04 WAIT -> ORIGINAL M09 no-fill + outcomes.

All data and purchases are nonlive local fixtures. No private GET, paid AI,
Telegram, real order, real fill, or spendable IDR.
"""
from __future__ import annotations

from copy import deepcopy
import json
from decimal import Decimal as D
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal, DecisionConflictError
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.m04_radar_three_role_handoff_readonly import (
    inspect_original_ws3_three_role_handoff_readonly,
)
from indodax_ai_bot.m09_ws3_watch_price_outcomes_readonly import (
    inspect_m09_ws3_watch_price_outcomes_readonly,
)
from scripts.m09_ws3_watch_price_history_readonly import (
    capture_original_ws3_watch_readonly,
    evaluate_saved_ws3_watch_readonly,
)

NOW=1794330000
OBS=NOW-25

class M09OriginalWS3NoFillAndPriceOutcomeTests(unittest.TestCase):
    def setUp(self):
        td=TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root=Path(td.name)
        self.ws=self.root/"m03-original.sqlite3"
        self.paper=self.root/"original-v2-paper.sqlite3"
        self.app=DailyScalpingOrchestrator(Settings(),DecisionJournal(self.paper))
        with sqlite3.connect(self.ws) as db:
            db.executescript("""
                CREATE TABLE m03_ws_summary_watch_minute(
                    pair TEXT,minute_epoch INTEGER,exchange_epoch INTEGER,
                    last_idr TEXT,rolling_24h_idr TEXT,public_offset INTEGER,
                    PRIMARY KEY(pair,minute_epoch));
                CREATE TABLE m03_ws3_trade_samples(
                    pair TEXT,exchange_epoch INTEGER,sequence INTEGER,
                    price_idr TEXT,quote_idr TEXT,recorded_epoch INTEGER,
                    PRIMARY KEY(pair,exchange_epoch,sequence));
                CREATE TABLE m03_ws3_price_samples_5s(
                    pair TEXT,bucket_epoch INTEGER,exchange_epoch INTEGER,
                    last_idr TEXT,public_offset INTEGER,recorded_epoch INTEGER,
                    PRIMARY KEY(pair,bucket_epoch));
            """)
        self.ws.chmod(0o600)

    def seed(self,pair):
        with sqlite3.connect(self.ws) as db:
            db.executemany("INSERT INTO m03_ws_summary_watch_minute VALUES(?,?,?,?,?,?)",[
                (pair,NOW-120,NOW-85,"100","750000000",11),
                (pair,NOW-60,OBS,"101","760000000",12),
            ])
            start=OBS//60*60
            db.execute("INSERT INTO m03_ws3_trade_samples VALUES(?,?,?,?,?,?)",
                       (pair,start-20,1,"100","500",start-19))

    def handoff(self):
        return inspect_original_ws3_three_role_handoff_readonly(
            db_path=self.ws,now_epoch=NOW)

    def add_price(self,pair,target,price,receipt=None):
        with sqlite3.connect(self.ws) as db:
            db.execute("INSERT INTO m03_ws3_price_samples_5s VALUES(?,?,?,?,?,?)",
                (pair,target//5*5,target,str(price),20,target if receipt is None else receipt))

    def test_original_m09_gate_never_promotes_two_uncalled_candidates_to_fill(self):
        self.seed("humanity_idr")
        self.seed("ace_idr")
        with (
            patch.object(self.app,"run_bulk_deep_cycle",
                side_effect=AssertionError("SCANNER_MUST_NOT_RUN")) as scan,
            patch("indodax_ai_bot.m07_order_adapter.OfficialV2OrderAdapter.create_order",
                side_effect=AssertionError("ORDER_MUST_NOT_SEND")) as order,
        ):
            result=self.app.review_m09_ws3_to_paper_admission_readonly_once(
                source_db=self.ws,now_epoch=NOW)
        self.assertEqual(result["m04_watch_candidate_count"],2)
        self.assertEqual(result["m04_uncalled_readiness"]["status"],
                         "WAIT_TWO_WATCHES_NO_COMPARABLE_AI_REVIEWS")
        self.assertEqual(result["m09_status"],"M09_PAPER_M04_RESCAN_REQUIRED")
        self.assertIsNone(result["paper_entry_reference"])
        self.assertFalse(result["paper_fill_created"])
        self.assertFalse(result["execution_allowed"])
        scan.assert_not_called()
        order.assert_not_called()
        with sqlite3.connect(self.paper) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m09_paper_simulated_buys").fetchone()[0],0)
        self.assertEqual(self.app.journal.review_m09_paper_pnl_offline()[
            "paper_buy_count"],0)

    def test_original_single_and_zero_watch_no_fill(self):
        for pairs,expected in (
            ((),"RESCAN_NO_CURRENT_WATCH"),
            (("vine_idr",),"WAIT_ONE_WATCH_NO_QUALIFIED_AI_REVIEW"),
        ):
            with self.subTest(pairs=pairs):
                with sqlite3.connect(self.ws) as db:
                    db.execute("DELETE FROM m03_ws_summary_watch_minute")
                    db.execute("DELETE FROM m03_ws3_trade_samples")
                for pair in pairs:
                    self.seed(pair)
                result=self.app.review_m09_ws3_to_paper_admission_readonly_once(
                    source_db=self.ws,now_epoch=NOW)
                self.assertEqual(result["m04_uncalled_readiness"]["status"],expected)
                self.assertEqual(result["m09_status"],"M09_PAPER_M04_RESCAN_REQUIRED")
                self.assertFalse(result["paper_fill_created"])
                self.assertIsNone(result["paper_entry_reference"])

    def test_historical_horizon_available_after_actual_price_receipt_only(self):
        self.seed("humanity_idr")
        report=self.handoff()
        self.assertEqual(report["candidate_count"],1)
        self.add_price("humanity_idr",OBS+5*60+5,"103.02")
        # Would be a price return of +2% compared with last source 101;
        # it is NOT profit or proof of an executable entry/exit.
        result=inspect_m09_ws3_watch_price_outcomes_readonly(
            handoff=report,db_path=self.ws,evaluation_epoch=OBS+5*60+6)
        self.assertEqual(result["status"],"LOCAL_PRICE_PATH_RESEARCH_ONLY")
        self.assertEqual(result["candidate_count"],1)
        path=result["observations"][0]["observed_outcomes"]
        self.assertEqual(path["5m"]["status"],
                         "OBSERVED_LOCAL_GROSS_PRICE_CHANGE_ONLY")
        self.assertEqual(D(path["5m"]["observed_gross_price_return_percent"]),D("2.00"))
        self.assertEqual(path["5m"]["sample_delay_seconds"],5)
        self.assertEqual(path["15m"]["status"],"NOT_YET_MATURED")
        self.assertEqual(path["30m"]["status"],"NOT_YET_MATURED")
        self.assertEqual(path["60m"]["status"],"NOT_YET_MATURED")
        self.assertFalse(result["paper_fill_authorized"])
        self.assertFalse(result["virtual_pnl_calculated"])
        self.assertFalse(result["actual_pnl_calculated"])

    def test_missing_future_data_and_delayed_receipt_are_unknown(self):
        self.seed("humanity_idr")
        report=self.handoff()
        five=OBS+300
        self.add_price("humanity_idr",five+1,"110",receipt=five+100)
        report_one=inspect_m09_ws3_watch_price_outcomes_readonly(
            handoff=report,db_path=self.ws,evaluation_epoch=five+50)
        first=report_one["observations"][0]["observed_outcomes"]["5m"]
        self.assertEqual(first["status"],"UNKNOWN_NO_LOCAL_PRICE_NEAR_HORIZON")
        later=inspect_m09_ws3_watch_price_outcomes_readonly(
            handoff=report,db_path=self.ws,evaluation_epoch=five+101)
        self.assertEqual(later["observations"][0]["observed_outcomes"]["5m"]["status"],
                         "OBSERVED_LOCAL_GROSS_PRICE_CHANGE_ONLY")
        self.assertEqual(later["observations"][0]["observed_outcomes"]["15m"]["status"],
                         "NOT_YET_MATURED")

    def test_sparse_gaps_never_infer_return_or_fill(self):
        self.seed("ace_idr")
        report=self.handoff()
        self.add_price("ace_idr",OBS+5*60+35,"125")
        out=inspect_m09_ws3_watch_price_outcomes_readonly(
            handoff=report,db_path=self.ws,evaluation_epoch=OBS+5*60+100)
        self.assertEqual(out["observations"][0]["observed_outcomes"]["5m"]["status"],
                         "UNKNOWN_NO_LOCAL_PRICE_NEAR_HORIZON")
        self.assertEqual(out["paper_orders_created"],0)

    def test_forged_digest_role_or_future_clock_denied(self):
        self.seed("humanity_idr")
        report=self.handoff()
        for how in ("digest","role","clock","g1"):
            with self.subTest(how=how):
                tampered=deepcopy(report)
                if how=="digest":
                    tampered["candidates"][0]["evidence_digest_sha256"]="0"*64
                elif how=="role":
                    tampered["candidates"][0]["role_slots"][0]["model_output"]={"BUY":True}
                elif how=="clock":
                    tampered["candidates"][0]["public_evidence"]["observed_epoch"]=OBS+1000
                else:
                    tampered["g1_independent_issuer_verified"]=True
                outcome=inspect_m09_ws3_watch_price_outcomes_readonly(
                    handoff=tampered,db_path=self.ws,evaluation_epoch=NOW)
                self.assertEqual(outcome["status"],"SOURCE_UNTRUSTED")
                self.assertEqual(outcome["candidate_count"],0)
                self.assertFalse(outcome["execution_allowed"])

    def test_paper_usdt_usdc_only_hypothetical_and_manual_assets_still_denied(self):
        for symbol in ("USDTIDR","USDCIDR"):
            with self.subTest(symbol=symbol):
                with TemporaryDirectory() as tmp:
                    app=DailyScalpingOrchestrator(
                        Settings(),DecisionJournal(Path(tmp)/"paper.sqlite3"))
                    buy=app.run_m09_original_paper_cycle_offline_once(
                        action="BUY",details={
                            "paper_id":"paper_stablecoin", "symbol":symbol,
                            "buy_qty":D("1"),"buy_price_idr":D("10000"),
                            "buy_all_in_fee_idr":D("20"),"buy_epoch":OBS,
                        })
                    self.assertEqual(buy["paper"]["status"],"M09_SIMULATED_BUY_OPEN")
                    self.assertFalse(buy["order_sent"])
                    self.assertFalse(buy["execution_allowed"])
        for symbol in ("FLOKIIDR","PEPEIDR"):
            with self.subTest(symbol=symbol):
                with self.assertRaises(DecisionConflictError):
                    self.app.run_m09_original_paper_cycle_offline_once(
                        action="BUY",details={
                            "paper_id":"paper_manual", "symbol":symbol,
                            "buy_qty":D("1"),"buy_price_idr":D("10000"),
                            "buy_all_in_fee_idr":D("20"),"buy_epoch":OBS,
                        })


    def save_real_capture(self, *, pair="humanity_idr"):
        self.seed(pair)
        captured=capture_original_ws3_watch_readonly(
            source_db=self.ws,capture_epoch=NOW)
        self.assertEqual(captured["status"],"LOCAL_UNCALLED_WATCH_CAPTURED")
        path=self.root/"captured-watch.json"
        path.write_text(json.dumps(captured),encoding="utf-8")
        path.chmod(0o600)
        return path

    def test_one_shot_capture_to_observed_horizon_no_paper_or_model(self):
        saved=self.save_real_capture()
        self.add_price("humanity_idr",OBS+300+5,"103.02")
        # A later look at the *same* stored original packet, not replayed
        # with future prices during a fabricated historical M04 selection.
        with patch(
            "scripts.m09_ws3_watch_price_history_readonly."
            "inspect_original_ws3_three_role_handoff_readonly",
            side_effect=AssertionError("NO_HISTORICAL_M04_RECONSTRUCTION")
        ) as recapture:
            observed=evaluate_saved_ws3_watch_readonly(
                capture_file=saved,source_db=self.ws,
                evaluation_epoch=OBS+306)
        recapture.assert_not_called()
        self.assertEqual(observed["status"],
                         "LOCAL_CAPTURED_WATCH_PRICE_RESEARCH_ONLY")
        price=observed["observations"][0]["observed_outcomes"]
        self.assertEqual(price["5m"]["status"],
                         "OBSERVED_LOCAL_GROSS_PRICE_CHANGE_ONLY")
        self.assertEqual(D(price["5m"]["observed_gross_price_return_percent"]),D("2"))
        self.assertEqual(price["15m"]["status"],"NOT_YET_MATURED")
        self.assertFalse(observed["net_profit_or_fills_inferred"])
        self.assertFalse(observed["execution_allowed"])
        with sqlite3.connect(self.paper) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m09_paper_simulated_buys").fetchone()[0],0)

    def test_capture_evaluation_unknown_for_late_and_missing_prices(self):
        saved=self.save_real_capture(pair="ace_idr")
        self.add_price("ace_idr",OBS+300+35,"120")
        result=evaluate_saved_ws3_watch_readonly(
            capture_file=saved,source_db=self.ws,
            evaluation_epoch=OBS+300+50)
        self.assertEqual(result["status"],
                         "LOCAL_CAPTURED_WATCH_PRICE_RESEARCH_ONLY")
        self.assertEqual(result["observations"][0]["observed_outcomes"]["5m"]["status"],
                         "UNKNOWN_NO_LOCAL_PRICE_NEAR_HORIZON")
        self.assertFalse(result["paper_fill_created"])

    def test_capture_privacy_clock_and_tamper_fail_closed(self):
        saved=self.save_real_capture(pair="vine_idr")
        original=json.loads(saved.read_text())
        tests=("mode","source","clock","digest","role","future","duplicate")
        for how in tests:
            with self.subTest(how=how):
                data=deepcopy(original)
                if how=="source":
                    data["source_db_path"]="/unrelated/source.sqlite3"
                elif how=="clock":
                    data["captured_epoch"]=OBS-500
                elif how=="future":
                    data["captured_epoch"]=OBS+350
                elif how=="digest":
                    data["handoff"]["candidates"][0]["evidence_digest_sha256"]="0"*64
                elif how=="role":
                    data["handoff"]["candidates"][0]["role_slots"][0]["model_output"]={"BUY":True}
                elif how=="duplicate":
                    saved.write_text('{"stage":"a","stage":"b"}')
                if how!="duplicate":
                    saved.write_text(json.dumps(data))
                saved.chmod(0o644 if how=="mode" else 0o600)
                result=evaluate_saved_ws3_watch_readonly(
                    capture_file=saved,source_db=self.ws,
                    evaluation_epoch=OBS+301)
                self.assertNotEqual(result["status"],
                                    "LOCAL_CAPTURED_WATCH_PRICE_RESEARCH_ONLY")
                self.assertFalse(result["execution_allowed"])
        saved.write_text(json.dumps(original))
        saved.chmod(0o600)
        linked=self.root/"capture-link.json"
        linked.symlink_to(saved)
        result=evaluate_saved_ws3_watch_readonly(
            capture_file=linked,source_db=self.ws,
            evaluation_epoch=OBS+301)
        self.assertEqual(result["status"],"CAPTURE_FILE_UNTRUSTED")

    def test_no_watch_cannot_be_captured_as_historical_fact(self):
        result=capture_original_ws3_watch_readonly(
            source_db=self.ws,capture_epoch=NOW)
        self.assertEqual(result["status"],"NO_ELIGIBLE_UNCALLED_WATCH")
        self.assertFalse(result["execution_allowed"])


if __name__=="__main__":
    unittest.main()
