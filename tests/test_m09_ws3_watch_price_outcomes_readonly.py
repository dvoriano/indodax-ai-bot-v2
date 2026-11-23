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


    def test_capture_coverage_observed_nearby_and_absent_source_are_distinct(self):
        from scripts.m09_ws3_watch_coverage_audit_readonly import (
            audit_saved_ws3_watch_coverage_readonly,
        )
        self.seed("tmg_idr")
        self.seed("drx_idr")
        captured = capture_original_ws3_watch_readonly(
            source_db=self.ws, capture_epoch=NOW)
        self.assertEqual(captured["candidate_count"], 2)
        saved = self.root / "natural-two-watch.json"
        saved.write_text(json.dumps(captured))
        saved.chmod(0o600)

        target5 = OBS + 300
        target15 = OBS + 900
        self.add_price("drx_idr", target5 + 19, "103.02", receipt=target5 + 20)
        # A sample AFTER the 30-second matching window is nearby only.
        self.add_price("drx_idr", target15 + 35, "103.50",
                       receipt=target15 + 36)

        with patch(
            "scripts.m09_ws3_watch_price_history_readonly."
            "inspect_original_ws3_three_role_handoff_readonly",
            side_effect=AssertionError("NO_RESELECT_OR_HISTORICAL_CAPTURE"),
        ) as recapture:
            result = audit_saved_ws3_watch_coverage_readonly(
                capture_file=saved, source_db=self.ws,
                evaluation_epoch=target15 + 45)
        recapture.assert_not_called()

        self.assertEqual(result["status"],
                         "LOCAL_CAPTURE_COVERAGE_RESEARCH_ONLY")
        self.assertEqual(result["matured_horizons"], 4)
        self.assertEqual(result["observed_matured_horizons"], 1)
        self.assertEqual(result["unknown_matured_horizons"], 3)
        self.assertEqual(result["observed_coverage_percent"], "25.0")
        by_pair = {row["pair"]: row for row in result["observations"]}
        self.assertEqual(set(by_pair), {"drx_idr", "tmg_idr"})
        drx = by_pair["drx_idr"]["coverage"]
        tmg = by_pair["tmg_idr"]["coverage"]
        self.assertEqual(drx["5m"]["coverage_class"],
                         "OBSERVED_ON_TARGET_PRICE")
        self.assertEqual(drx["5m"]["exact_horizon_receipts"], 1)
        self.assertEqual(D(drx["5m"]["observed_gross_price_return_percent"]),
                         D("2"))
        self.assertEqual(drx["15m"]["coverage_class"],
                         "NO_ON_TARGET_WITH_NEARBY_LOCAL_SAMPLES")
        self.assertEqual(drx["15m"]["exact_horizon_receipts"], 0)
        self.assertEqual(drx["15m"]["nearby_120s_local_receipts"], 1)
        self.assertEqual(tmg["5m"]["coverage_class"],
                         "NO_LOCAL_PRICE_WITHIN_120S")
        self.assertEqual(tmg["15m"]["coverage_class"],
                         "NO_LOCAL_PRICE_WITHIN_120S")
        self.assertEqual(drx["30m"]["coverage_class"], "NOT_YET_MATURED")
        self.assertEqual(by_pair["drx_idr"]["m04_evidence_digest_sha256"],
                         next(row["evidence_digest_sha256"] for row in
                              captured["handoff"]["candidates"]
                              if row["pair"] == "drx_idr"))
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["paper_fill_created"])
        self.assertEqual(result["provider_calls"], 0)
        with sqlite3.connect(self.paper) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m09_paper_simulated_buys").fetchone()[0], 0)

    def test_capture_coverage_no_future_receipt_and_late_arrival(self):
        from scripts.m09_ws3_watch_coverage_audit_readonly import (
            audit_saved_ws3_watch_coverage_readonly,
        )
        saved = self.save_real_capture(pair="drx_idr")
        five = OBS + 300
        self.add_price("drx_idr", five + 10, "103.02", receipt=five + 75)

        early = audit_saved_ws3_watch_coverage_readonly(
            capture_file=saved, source_db=self.ws, evaluation_epoch=five + 5)
        self.assertEqual(early["matured_horizons"], 0)
        self.assertEqual(early["observations"][0]["coverage"]["5m"]
                         ["coverage_class"], "HORIZON_30S_WINDOW_STILL_OPEN")
        pending = audit_saved_ws3_watch_coverage_readonly(
            capture_file=saved, source_db=self.ws, evaluation_epoch=five + 40)
        five_pending = pending["observations"][0]["coverage"]["5m"]
        self.assertEqual(five_pending["coverage_class"],
                         "NO_LOCAL_PRICE_WITHIN_120S")
        self.assertEqual(five_pending["exact_horizon_receipts"], 0)
        self.assertEqual(five_pending["nearby_120s_local_receipts"], 0)
        self.assertEqual(pending["observed_matured_horizons"], 0)

        later = audit_saved_ws3_watch_coverage_readonly(
            capture_file=saved, source_db=self.ws, evaluation_epoch=five + 76)
        five_later = later["observations"][0]["coverage"]["5m"]
        self.assertEqual(five_later["coverage_class"], "OBSERVED_ON_TARGET_PRICE")
        self.assertEqual(five_later["exact_horizon_receipts"], 1)
        self.assertEqual(later["observed_coverage_percent"], "100.0")

        # Adversarial skew: a future exchange time must never appear in
        # an earlier as-of coverage query, even if its recorded time is old.
        self.add_price("drx_idr", five + 25, "120", receipt=five + 2)
        skewed = audit_saved_ws3_watch_coverage_readonly(
            capture_file=saved, source_db=self.ws, evaluation_epoch=five + 5)
        sample = skewed["observations"][0]["coverage"]["5m"]
        self.assertEqual(sample["exact_horizon_receipts"], 0)
        self.assertEqual(sample["nearby_120s_local_receipts"], 0)

    def test_capture_coverage_rejects_world_readable_and_tampered_files(self):
        from scripts.m09_ws3_watch_coverage_audit_readonly import (
            audit_saved_ws3_watch_coverage_readonly,
        )
        saved = self.save_real_capture(pair="vine_idr")
        saved.chmod(0o644)
        denied = audit_saved_ws3_watch_coverage_readonly(
            capture_file=saved, source_db=self.ws, evaluation_epoch=OBS + 350)
        self.assertEqual(denied["status"], "CAPTURE_OR_OUTCOMES_CAPTURE_FILE_UNTRUSTED")
        self.assertEqual(denied["candidate_count"], 0)
        self.assertFalse(denied["execution_allowed"])
        saved.chmod(0o600)
        tampered = json.loads(saved.read_text())
        tampered["handoff"]["candidates"][0]["evidence_digest_sha256"] = "0" * 64
        saved.write_text(json.dumps(tampered))
        denied2 = audit_saved_ws3_watch_coverage_readonly(
            capture_file=saved, source_db=self.ws, evaluation_epoch=OBS + 350)
        self.assertEqual(denied2["candidate_count"], 0)
        self.assertFalse(denied2["paper_fill_created"])


    def test_precapture_retained_source_counts_do_not_use_future_rows(self):
        from scripts.m04_ws3_precapture_coverage_audit_readonly import (
            inspect_m04_retained_precapture_coverage_readonly,
        )
        saved = self.save_real_capture(pair="drx_idr")
        # Historical local rows retained with receipt already before capture.
        for epoch in (NOW-700, NOW-250, NOW-30):
            self.add_price("drx_idr", epoch, "103", receipt=epoch+1)
        # Price from before the capture but received after it is not
        # evidence that the price was locally available at capture time.
        self.add_price("drx_idr", NOW-100, "104", receipt=NOW+5)
        # A future exchange timestamp is excluded even if its receipt
        # timestamp is artificially earlier than the capture.
        self.add_price("drx_idr", NOW+10, "105", receipt=NOW-1)
        with patch(
            "scripts.m09_ws3_watch_price_history_readonly."
            "inspect_original_ws3_three_role_handoff_readonly",
            side_effect=AssertionError("NO_RECONSTRUCTED_M04_WATCH"),
        ) as rescan:
            result = inspect_m04_retained_precapture_coverage_readonly(
                capture_file=saved, source_db=self.ws, evaluation_epoch=NOW+20)
        rescan.assert_not_called()
        self.assertEqual(result["status"],
                         "RETAINED_PRECAPTURE_SOURCE_RESEARCH_ONLY")
        self.assertFalse(result["historical_db_immutability_verified"])
        self.assertFalse(result["m04_selection_modified"])
        self.assertFalse(result["lookahead_rows_included"])
        self.assertEqual(result["role_decisions_observed"], 0)
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["paper_fill_created"])
        self.assertEqual(result["provider_calls"], 0)
        only = result["observations"][0]
        self.assertEqual(only["pair"], "drx_idr")
        windows = only["pre_capture_retained_windows"]
        self.assertEqual(windows["5m"]["retained_rows_by_capture_cutoff"], 2)
        self.assertEqual(windows["5m"]["latest_exchange_age_seconds"], 30)
        self.assertEqual(windows["5m"]["largest_observed_inter_sample_gap_seconds"], 220)
        self.assertEqual(windows["15m"]["retained_rows_by_capture_cutoff"], 3)
        self.assertEqual(windows["15m"]["largest_observed_inter_sample_gap_seconds"], 450)
        self.assertFalse(only["price_coverage_decision_gate"])
        with sqlite3.connect(self.paper) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m09_paper_simulated_buys").fetchone()[0], 0)

    def test_precapture_retained_missing_and_invalid_capture_fail_closed(self):
        from scripts.m04_ws3_precapture_coverage_audit_readonly import (
            inspect_m04_retained_precapture_coverage_readonly,
        )
        saved = self.save_real_capture(pair="tmg_idr")
        missing = inspect_m04_retained_precapture_coverage_readonly(
            capture_file=saved, source_db=self.ws, evaluation_epoch=NOW+2)
        self.assertEqual(missing["status"],
                         "RETAINED_PRECAPTURE_SOURCE_RESEARCH_ONLY")
        for value in missing["observations"][0]["pre_capture_retained_windows"].values():
            self.assertEqual(value["retained_rows_by_capture_cutoff"], 0)
            self.assertEqual(value["status"], "NO_RETAINED_LOCAL_PRICE_ROWS")
            self.assertIsNone(value["latest_exchange_age_seconds"])
        self.assertFalse(missing["execution_allowed"])
        saved.chmod(0o644)
        denied = inspect_m04_retained_precapture_coverage_readonly(
            capture_file=saved, source_db=self.ws, evaluation_epoch=NOW+2)
        self.assertEqual(denied["candidate_count"], 0)
        self.assertFalse(denied["execution_allowed"])
        self.assertEqual(denied["role_decisions_observed"], 0)


    def test_public_closed_1m_candle_reference_keeps_original_ws3_unknown(self):
        from scripts.m09_indodax_public_candle_reference_readonly import (
            compare_m09_with_indodax_candles_readonly,
        )
        saved = self.save_real_capture(pair="drx_idr")
        def feed(pair, *, from_epoch, to_epoch):
            self.assertEqual(pair, "drx_idr")
            self.assertLessEqual(from_epoch, to_epoch)
            return [
                {"Time": (OBS + minutes*60)//60*60,
                 "Open": "101", "High": "105", "Low": "100",
                 "Close": "103.02", "Volume": "150"}
                for minutes in (5, 15, 30, 60)
            ]
        with patch(
            "scripts.m09_ws3_watch_price_history_readonly."
            "inspect_original_ws3_three_role_handoff_readonly",
            side_effect=AssertionError("NO_RECONSTRUCTED_WATCH"),
        ) as rescan:
            result = compare_m09_with_indodax_candles_readonly(
                capture_file=saved, source_db=self.ws, evaluation_epoch=OBS+3700,
                fetch_public_ohlcv=True, fetcher=feed)
        rescan.assert_not_called()
        self.assertEqual(result["status"],
                         "PUBLIC_1M_CANDLE_REFERENCE_RESEARCH_ONLY")
        self.assertEqual(result["original_ws3_unknown_horizons"], 4)
        self.assertEqual(result["candle_references_observed"], 4)
        self.assertEqual(result["public_rest_gets_attempted"], 1)
        self.assertEqual(result["public_rest_gets_validated"], 1)
        self.assertFalse(result["original_ws3_results_preserved"] is False)
        self.assertFalse(result["historical_candle_immutability_verified"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["paper_fill_created"])
        self.assertEqual(result["provider_calls"], 0)
        pair = result["observations"][0]
        self.assertEqual(pair["api_symbol"], "DRXIDR")
        for h in ("5m", "15m", "30m", "60m"):
            row = pair["horizons"][h]
            self.assertEqual(row["ws3_original_coverage_class"],
                             "NO_LOCAL_PRICE_WITHIN_120S")
            self.assertEqual(row["candle_status"],
                             "CLOSED_1M_CANDLE_REFERENCE_OBSERVED")
            self.assertEqual(
                D(row["candle_close_gross_price_change_from_original_ws3_baseline_percent"]),
                D("2"),
            )
            self.assertEqual(row["candle_minute_close_epoch"],
                             row["candle_minute_open_epoch"]+60)
            self.assertFalse(row["candle_is_quote_at_exact_target_second"])
        with sqlite3.connect(self.paper) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m09_paper_simulated_buys").fetchone()[0], 0)

    def test_public_candle_reference_one_minute_must_close_before_use(self):
        from scripts.m09_indodax_public_candle_reference_readonly import (
            compare_m09_with_indodax_candles_readonly,
        )
        saved = self.save_real_capture(pair="drx_idr")
        minute = (OBS+300)//60*60
        def feed(pair, *, from_epoch, to_epoch):
            return [{"Time": minute, "Open": "100", "High": "110",
                     "Low": "99", "Close": "108", "Volume": "10"}]
        before = compare_m09_with_indodax_candles_readonly(
            capture_file=saved, source_db=self.ws,
            evaluation_epoch=minute+59, fetch_public_ohlcv=True, fetcher=feed)
        self.assertEqual(before["observations"][0]["horizons"]["5m"]
                         ["candle_status"], "CANDLE_MINUTE_NOT_YET_CLOSED")
        self.assertEqual(before["candle_references_observed"], 0)
        after = compare_m09_with_indodax_candles_readonly(
            capture_file=saved, source_db=self.ws,
            evaluation_epoch=minute+60, fetch_public_ohlcv=True, fetcher=feed)
        self.assertEqual(after["observations"][0]["horizons"]["5m"]
                         ["candle_status"], "CLOSED_1M_CANDLE_REFERENCE_OBSERVED")
        self.assertEqual(after["candle_references_observed"], 1)
        self.assertFalse(after["execution_allowed"])

    def test_public_zero_volume_and_missing_are_not_confirmed_trade_price(self):
        from scripts.m09_indodax_public_candle_reference_readonly import (
            compare_m09_with_indodax_candles_readonly,
        )
        saved = self.save_real_capture(pair="tmg_idr")
        minute = (OBS+300)//60*60
        def feed(pair, *, from_epoch, to_epoch):
            return [{"Time": minute, "Open": "101", "High": "101",
                     "Low": "101", "Close": "101", "Volume": "0"}]
        result = compare_m09_with_indodax_candles_readonly(
            capture_file=saved, source_db=self.ws,
            evaluation_epoch=OBS+3700, fetch_public_ohlcv=True, fetcher=feed)
        self.assertEqual(result["candle_references_observed"], 0)
        self.assertEqual(result["price_reference_available_horizons"], 1)
        self.assertEqual(result["price_reference_unknown_horizons"], 3)
        self.assertEqual(result["price_reference_pending_horizons"], 0)
        self.assertEqual(result["price_reference_coverage_percent"], "25.0")
        self.assertEqual(result["candle_minutes_present"], 1)
        self.assertEqual(result["candle_zero_volume_references"], 1)
        bar = result["observations"][0]["horizons"]["5m"]
        self.assertEqual(bar["candle_status"], "CANDLE_ZERO_VOLUME_UNVERIFIED")
        self.assertTrue(bar["candle_ohlc_returned_and_validated"])
        self.assertIsNone(bar["candle_close_idr"])
        self.assertEqual(bar["candle_zero_volume_reference_close_idr"], "101")
        self.assertEqual(bar["price_reference_status"], "PRICE_REFERENCE_AVAILABLE")
        self.assertEqual(bar["price_reference_close_idr"], "101")
        self.assertEqual(bar["price_reference_source"], "INDODAX_CLOSED_1M_CANDLE_CLOSE")
        self.assertTrue(bar["price_reference_volume_zero"])
        self.assertFalse(bar["price_reference_trade_verified"])
        self.assertEqual(
            D(bar["price_reference_gross_change_from_ws3_baseline_percent"]), D("0"))
        self.assertEqual(
            D(bar["candle_zero_volume_reference_gross_price_change_from_original_ws3_baseline_percent"]),
            D("0"))
        self.assertTrue(bar["candle_zero_volume_reference_is_not_trade_evidence"])
        self.assertEqual(bar["ws3_original_coverage_class"],
                         "NO_LOCAL_PRICE_WITHIN_120S")
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(result["observations"][0]["horizons"]["15m"]
                         ["candle_status"], "CANDLE_MINUTE_NOT_RETURNED")

    def test_public_malformed_candle_duplicate_and_network_failure_are_unknown(self):
        from scripts.m09_indodax_public_candle_reference_readonly import (
            compare_m09_with_indodax_candles_readonly,
        )
        saved = self.save_real_capture(pair="drx_idr")
        minute = (OBS+300)//60*60
        candle = {"Time": minute, "Open": "101", "High": "101",
                  "Low": "101", "Close": "101", "Volume": "1"}
        def duplicate(pair, *, from_epoch, to_epoch):
            return [candle, dict(candle)]
        bad = compare_m09_with_indodax_candles_readonly(
            capture_file=saved, source_db=self.ws,
            evaluation_epoch=OBS+3700, fetch_public_ohlcv=True, fetcher=duplicate)
        self.assertEqual(bad["observations"][0]["fetch_status"],
                         "PUBLIC_CANDLES_UNAVAILABLE_OR_INVALID")
        self.assertEqual(bad["candle_references_observed"], 0)
        def unavailable(pair, *, from_epoch, to_epoch):
            raise OSError("AUTHORIZATION_DENIED")
        down = compare_m09_with_indodax_candles_readonly(
            capture_file=saved, source_db=self.ws,
            evaluation_epoch=OBS+3700, fetch_public_ohlcv=True, fetcher=unavailable)
        self.assertEqual(down["observations"][0]["fetch_status"],
                         "PUBLIC_CANDLES_UNAVAILABLE_OR_INVALID")
        self.assertFalse(down["execution_allowed"])

    def test_public_candle_http_is_fixed_get_and_invalid_capture_never_fetches(self):
        from scripts.m09_indodax_public_candle_reference_readonly import (
            compare_m09_with_indodax_candles_readonly,
            fetch_indodax_public_1m_candles,
        )
        from urllib.parse import urlsplit, parse_qs
        saved = self.save_real_capture(pair="drx_idr")
        minute = (OBS+300)//60*60
        candle = {"Time": minute, "Open": "101", "High": "101",
                  "Low": "101", "Close": "101", "Volume": "1"}
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def read(self, size=-1):
                self_size = size
                if self_size <= 0:
                    raise AssertionError("BOUNDED_RESPONSE_REQUIRED")
                return json.dumps([candle]).encode()
        def fake_urlopen(request, *, timeout):
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(timeout, 10)
            u = urlsplit(request.full_url)
            self.assertEqual(u.scheme, "https")
            self.assertEqual(u.hostname, "indodax.com")
            self.assertEqual(u.path, "/tradingview/history_v2")
            params = parse_qs(u.query)
            self.assertEqual(params["symbol"], ["DRXIDR"])
            self.assertEqual(params["tf"], ["1"])
            self.assertTrue("Key" not in request.headers)
            return Response()
        with patch(
            "scripts.m09_indodax_public_candle_reference_readonly.urlopen",
            side_effect=fake_urlopen
        ) as wire:
            payload = fetch_indodax_public_1m_candles(
                "drx_idr", from_epoch=minute, to_epoch=minute)
        self.assertEqual(len(payload), 1)
        wire.assert_called_once()

        saved.chmod(0o644)
        def must_not_fetch(*args, **kwargs):
            raise AssertionError("NO_NETWORK_FOR_INVALID_CAPTURE")
        denied = compare_m09_with_indodax_candles_readonly(
            capture_file=saved, source_db=self.ws,
            evaluation_epoch=OBS+3700,
            fetch_public_ohlcv=True, fetcher=must_not_fetch)
        self.assertTrue(denied["status"].startswith("ORIGINAL_WS3_"))
        self.assertEqual(denied["candidate_count"], 0)
        self.assertFalse(denied["execution_allowed"])
        no_optin = compare_m09_with_indodax_candles_readonly(
            capture_file=saved, source_db=self.ws,
            evaluation_epoch=OBS+3700, fetch_public_ohlcv=False,
            fetcher=must_not_fetch)
        self.assertEqual(no_optin["status"],
                         "EXPLICIT_PUBLIC_CANDLE_FETCH_OPT_IN_REQUIRED")



    def test_eight_zero_volume_candles_preserve_all_ws3_unknowns(self):
        from scripts.m09_indodax_public_candle_reference_readonly import (
            compare_m09_with_indodax_candles_readonly,
        )
        for pair in ("tmg_idr", "drx_idr"):
            self.seed(pair)
        captured = capture_original_ws3_watch_readonly(
            source_db=self.ws, capture_epoch=NOW)
        self.assertEqual(captured["candidate_count"], 2)
        saved = self.root / "two_watch_zero_volume.json"
        saved.write_text(json.dumps(captured), encoding="utf-8")
        saved.chmod(0o600)
        calls = []
        def history(pair, *, from_epoch, to_epoch):
            calls.append(pair)
            return [
                {
                    "Time": (OBS + minutes * 60)//60*60,
                    "Open": "101", "High": "101",
                    "Low": "101", "Close": "101",
                    "Volume": "0",
                }
                for minutes in (5, 15, 30, 60)
            ]

        result = compare_m09_with_indodax_candles_readonly(
            capture_file=saved, source_db=self.ws,
            evaluation_epoch=OBS + 3800,
            fetch_public_ohlcv=True, fetcher=history)
        self.assertEqual(set(calls), {"tmg_idr", "drx_idr"})
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["public_rest_gets_attempted"], 2)
        self.assertEqual(result["public_rest_gets_validated"], 2)
        self.assertEqual(result["original_ws3_unknown_horizons"], 8)
        self.assertEqual(result["candle_minutes_present"], 8)
        self.assertEqual(result["candle_zero_volume_references"], 8)
        self.assertEqual(result["candle_references_observed"], 0)
        self.assertEqual(result["price_reference_available_horizons"], 8)
        self.assertEqual(result["price_reference_unknown_horizons"], 0)
        self.assertEqual(result["price_reference_coverage_percent"], "100.0")
        for item in result["observations"]:
            for horizon in ("5m", "15m", "30m", "60m"):
                bar = item["horizons"][horizon]
                self.assertEqual(bar["candle_status"], "CANDLE_ZERO_VOLUME_UNVERIFIED")
                self.assertEqual(bar["candle_zero_volume_reference_close_idr"], "101")
                self.assertEqual(bar["price_reference_status"], "PRICE_REFERENCE_AVAILABLE")
                self.assertEqual(bar["price_reference_close_idr"], "101")
                self.assertTrue(bar["price_reference_volume_zero"])
                self.assertTrue(bar["price_reference_is_not_executable"])
                self.assertIsNone(bar["candle_close_idr"])
                self.assertEqual(bar["ws3_original_coverage_class"],
                                 "NO_LOCAL_PRICE_WITHIN_120S")
                self.assertFalse(bar["fee_spread_slippage_or_fill_verified"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["paper_fill_created"])
        self.assertEqual(result["paper_orders_created"], 0)
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["telegram_messages_sent"], 0)
        with sqlite3.connect(self.paper) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m09_paper_simulated_buys"
            ).fetchone()[0], 0)



    def test_mixed_candle_eight_references_while_seven_ws3_unknown(self):
        from scripts.m09_indodax_public_candle_reference_readonly import (
            compare_m09_with_indodax_candles_readonly,
        )
        for pair in ("tmg_idr", "drx_idr"):
            self.seed(pair)
        captured = capture_original_ws3_watch_readonly(
            source_db=self.ws, capture_epoch=NOW)
        self.assertEqual(captured["candidate_count"], 2)
        saved = self.root / "mixed_watch.json"
        saved.write_text(json.dumps(captured), encoding="utf-8")
        saved.chmod(0o600)
        self.add_price("drx_idr", OBS + 305, "103.02", receipt=OBS + 306)

        def candles(pair, *, from_epoch, to_epoch):
            return [{
                "Time": (OBS + minutes * 60) // 60 * 60,
                "Open": "101", "High": "103.02", "Low": "101",
                "Close": "103.02" if pair == "drx_idr" and minutes == 5 else "101",
                "Volume": "25372" if pair == "drx_idr" and minutes == 5 else "0",
            } for minutes in (5, 15, 30, 60)]

        result = compare_m09_with_indodax_candles_readonly(
            capture_file=saved, source_db=self.ws, evaluation_epoch=OBS + 3800,
            fetch_public_ohlcv=True, fetcher=candles)
        self.assertEqual(result["status"], "PUBLIC_1M_CANDLE_REFERENCE_RESEARCH_ONLY")
        self.assertEqual(result["original_ws3_observed_horizons"], 1)
        self.assertEqual(result["original_ws3_unknown_horizons"], 7)
        self.assertEqual(result["original_ws3_coverage_percent"], "12.5")
        self.assertEqual(result["candle_minutes_present"], 8)
        self.assertEqual(result["candle_zero_volume_references"], 7)
        self.assertEqual(result["candle_references_observed"], 1)
        self.assertEqual(result["price_reference_available_horizons"], 8)
        self.assertEqual(result["price_reference_unknown_horizons"], 0)
        self.assertEqual(result["price_reference_coverage_percent"], "100.0")
        for item in result["observations"]:
            for h in ("5m", "15m", "30m", "60m"):
                bar = item["horizons"][h]
                self.assertEqual(bar["price_reference_status"], "PRICE_REFERENCE_AVAILABLE")
                self.assertIsNotNone(bar["price_reference_close_idr"])
                self.assertIsNotNone(
                    bar["price_reference_gross_change_from_ws3_baseline_percent"])
                self.assertFalse(bar["price_reference_trade_verified"])
                self.assertTrue(bar["price_reference_is_not_exact_target_second"])
                self.assertTrue(bar["price_reference_is_not_executable"])
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["telegram_messages_sent"], 0)
        self.assertEqual(result["paper_orders_created"], 0)
        self.assertFalse(result["paper_fill_created"])
        self.assertFalse(result["execution_allowed"])
        with sqlite3.connect(self.paper) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m09_paper_simulated_buys").fetchone()[0], 0)



    def test_watch_channel_current_request_membership_is_not_pair_ack(self):
        from scripts.m04_ws3_watch_channel_coverage_readonly import (
            inspect_m04_watch_trade_channels_readonly,
        )
        self.seed("humanity_idr")
        self.seed("ace_idr")
        with sqlite3.connect(self.ws) as db:
            db.execute("""CREATE TABLE ws_stream_state(
                pair TEXT PRIMARY KEY, epoch TEXT,
                last_offset INTEGER, status TEXT)""")
            db.execute("INSERT INTO ws_stream_state VALUES(?,?,?,?)",
                       ("humanity_idr", "111", 33, "CONTIGUOUS_OBSERVED"))
        checkpoint = {
            "status": "M03_WS3_CONTINUOUS_CHECKPOINT",
            "observed_epoch": NOW - 10,
            "indexed_trade_pairs": ["humanity_idr"],
            "indexed_trade_channels_acknowledged": 1,
        }
        report = inspect_m04_watch_trade_channels_readonly(
            source_db=self.ws, evaluation_epoch=NOW,
            checkpoint_records=[checkpoint])
        self.assertEqual(report["status"],
                         "M04_WATCH_TRADE_CHANNEL_COVERAGE_RESEARCH_ONLY")
        self.assertEqual(report["candidate_count"], 2)
        self.assertEqual(report["capture_mode"], "CURRENT_OPERATOR_ONE_SHOT")
        self.assertEqual(report["checkpoint_count"], 1)
        self.assertFalse(report["pair_specific_ack_verified"])
        self.assertFalse(report["historical_source_immutability_verified"])
        self.assertFalse(report["execution_allowed"])
        self.assertFalse(report["m04_selection_modified"])
        samples = {r["pair"]: r for r in report["observations"]}
        self.assertEqual(set(samples), {"humanity_idr", "ace_idr"})
        self.assertTrue(samples["humanity_idr"]["checkpoint_request_membership"])
        self.assertFalse(samples["ace_idr"]["checkpoint_request_membership"])
        self.assertEqual(samples["humanity_idr"]["pair_specific_ack_evidence"],
                         "NOT_AVAILABLE_IN_CHECKPOINT")
        self.assertEqual(
            samples["humanity_idr"]["current_cursor_status_not_timestamped_ack"],
            "CONTIGUOUS_OBSERVED")
        self.assertFalse(
            samples["humanity_idr"]["cursor_status_proves_active_subscription"])
        self.assertGreaterEqual(
            samples["humanity_idr"]["source_local_retained_trade_windows"]
                ["15m"]["retained_local_trade_rows_by_capture_cutoff"], 1)
        self.assertEqual(report["paper_orders_created"], 0)
        self.assertEqual(report["provider_calls"], 0)
        with sqlite3.connect(self.paper) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m09_paper_simulated_buys").fetchone()[0], 0)

    def test_watch_channel_saved_capture_excludes_postcapture_rows_and_is_retro(self):
        from scripts.m04_ws3_watch_channel_coverage_readonly import (
            inspect_m04_watch_trade_channels_readonly,
        )
        saved = self.save_real_capture(pair="tmg_idr")
        # A past exchange time received AFTER capture is not proof at WATCH.
        with sqlite3.connect(self.ws) as db:
            db.execute("INSERT INTO m03_ws3_trade_samples VALUES(?,?,?,?,?,?)",
                       ("tmg_idr", NOW-100, 89, "102", "50", NOW+30))
            db.execute("INSERT INTO m03_ws3_trade_samples VALUES(?,?,?,?,?,?)",
                       ("tmg_idr", NOW+10, 90, "103", "50", NOW+11))
        old = {
            "status": "M03_WS3_CONTINUOUS_CHECKPOINT",
            "observed_epoch": NOW - 45,
            "indexed_trade_pairs": ["tmg_idr"],
        }
        future = dict(old, observed_epoch=NOW+1, indexed_trade_pairs=[])
        report = inspect_m04_watch_trade_channels_readonly(
            source_db=self.ws, capture_file=saved,
            evaluation_epoch=NOW+180,
            checkpoint_records=[old, future])
        self.assertEqual(report["status"],
                         "M04_WATCH_TRADE_CHANNEL_COVERAGE_RESEARCH_ONLY")
        self.assertEqual(report["capture_mode"], "SAVED_CAPTURE_RETROSPECTIVE")
        self.assertEqual(report["checkpoint_count"], 1)
        self.assertEqual(report["latest_checkpoint_age_seconds"], 45)
        item = report["observations"][0]
        self.assertTrue(item["checkpoint_request_membership"])
        self.assertIsNone(item["current_cursor_status_not_timestamped_ack"])
        self.assertEqual(
            item["source_local_retained_trade_windows"]["15m"]
                ["retained_local_trade_rows_by_capture_cutoff"], 1)
        self.assertFalse(report["pair_specific_ack_verified"])
        self.assertFalse(report["capture_and_source_db_read_are_atomic"])
        self.assertFalse(report["independent_g1_verified"])
        self.assertFalse(report["execution_allowed"])

    def test_watch_channel_no_checkpoint_is_unknown_request_not_false(self):
        from scripts.m04_ws3_watch_channel_coverage_readonly import (
            inspect_m04_watch_trade_channels_readonly,
        )
        saved = self.save_real_capture(pair="drx_idr")
        stale = {
            "status": "M03_WS3_CONTINUOUS_CHECKPOINT",
            "observed_epoch": NOW - 150,
            "indexed_trade_pairs": ["drx_idr"],
        }
        report = inspect_m04_watch_trade_channels_readonly(
            source_db=self.ws, capture_file=saved,
            evaluation_epoch=NOW+200, checkpoint_records=[stale])
        self.assertEqual(report["checkpoint_count"], 0)
        item = report["observations"][0]
        self.assertIsNone(item["checkpoint_request_membership"])
        self.assertEqual(item["checkpoint_request_evidence_status"],
                         "RECENT_CHECKPOINT_UNAVAILABLE")
        self.assertTrue(item["zero_rows_do_not_prove_zero_exchange_trades"])
        self.assertFalse(item["qualified_for_g1_or_market_decision"])

    def test_watch_channel_invalid_original_capture_fails_before_checkpoint_read(self):
        from scripts.m04_ws3_watch_channel_coverage_readonly import (
            inspect_m04_watch_trade_channels_readonly,
        )
        saved = self.save_real_capture(pair="drx_idr")
        saved.chmod(0o644)
        with patch(
            "scripts.m04_ws3_watch_channel_coverage_readonly._journal_checkpoints",
            side_effect=AssertionError("INVALID_CAPTURE_MUST_NOT_READ_JOURNAL"),
        ) as journal:
            report = inspect_m04_watch_trade_channels_readonly(
                source_db=self.ws, capture_file=saved, evaluation_epoch=NOW+200)
        journal.assert_not_called()
        self.assertTrue(report["status"].startswith("ORIGINAL_M04_"))
        self.assertEqual(report["candidate_count"], 0)
        self.assertFalse(report["execution_allowed"])

    def test_watch_channel_conflicting_checkpoint_epoch_fails_closed(self):
        from scripts.m04_ws3_watch_channel_coverage_readonly import (
            inspect_m04_watch_trade_channels_readonly,
        )
        saved = self.save_real_capture(pair="tmg_idr")
        base = {
            "status": "M03_WS3_CONTINUOUS_CHECKPOINT",
            "observed_epoch": NOW-20,
            "indexed_trade_pairs": ["tmg_idr"],
        }
        conflicted = dict(base, indexed_trade_pairs=[])
        report = inspect_m04_watch_trade_channels_readonly(
            source_db=self.ws, capture_file=saved,
            evaluation_epoch=NOW+100,
            checkpoint_records=[base, conflicted])
        self.assertEqual(report["status"], "ORIGINAL_OR_CHECKPOINT_INVALID")
        self.assertFalse(report["execution_allowed"])


if __name__=="__main__":
    unittest.main()
