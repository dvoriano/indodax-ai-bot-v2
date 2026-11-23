"""One existing M03 WS3 DB to existing two M04 three-role INPUT packets.

Offline synthetic SQLite only. No network, Telegram, paid AI, G1 or orders.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal as D
from indodax_ai_bot.m04_ws3_candidate_review_nonlive import (
    assess_uncalled_ws3_candidate_loop,
)
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

    def test_no_watch_returns_rescan_not_a_model_reject(self):
        self.install_index()
        report=inspect_original_ws3_three_role_handoff_readonly(
            db_path=self.db,now_epoch=NOW)
        ready=report["candidate_review_readiness"]
        self.assertEqual(ready["status"],"RESCAN_NO_CURRENT_WATCH")
        self.assertEqual(ready["watch_candidate_count"],0)
        self.assertEqual(ready["qualified_role_decision_count"],0)
        self.assertIsNone(ready["selected_pair"])
        self.assertFalse(ready["paper_fill_authorized"])
        self.assertFalse(ready["execution_allowed"])

    def test_one_real_watch_with_one_sample_waits_without_fake_ai(self):
        self.seed("vine_idr")
        self.install_index()
        close=OBS//60*60
        self.add_trade("vine_idr",close-30,1,"101","95248")
        report=inspect_original_ws3_three_role_handoff_readonly(
            db_path=self.db,now_epoch=NOW)
        ready=report["candidate_review_readiness"]
        self.assertEqual(ready["status"],"WAIT_ONE_WATCH_NO_QUALIFIED_AI_REVIEW")
        self.assertEqual(ready["watch_candidate_count"],1)
        self.assertEqual(ready["candidate_reviews"][0]["pair"],"vine_idr")
        self.assertEqual(ready["candidate_reviews"][0]["observed_trades_1m"],1)
        self.assertEqual(ready["candidate_reviews"][0]["observed_quote_5m_idr_only"],"95248")
        self.assertEqual(ready["candidate_reviews"][0]["reason"],
                         "WAIT_SPARSE_OBSERVED_1M_TRADES")
        self.assertEqual(ready["candidate_reviews"][0]["role_outputs_missing"],
                         ["MARKET","RISK","STRATEGY"])
        self.assertEqual(ready["role_calls_observed"],0)
        self.assertFalse(ready["telegram_trade_preview_authorized"])
        self.assertFalse(ready["paper_fill_authorized"])

    def test_two_watch_inputs_never_claim_qualified_risk_comparison(self):
        self.seed("uai_idr")
        self.seed("cst_idr")
        self.install_index()
        close=OBS//60*60
        self.add_trade("uai_idr",close-30,1,"100","1000")
        self.add_trade("uai_idr",close-10,2,"101","1200")
        self.add_trade("cst_idr",close-30,1,"NaN","700")
        report=inspect_original_ws3_three_role_handoff_readonly(
            db_path=self.db,now_epoch=NOW)
        ready=report["candidate_review_readiness"]
        self.assertEqual(ready["status"],
                         "WAIT_TWO_WATCHES_NO_COMPARABLE_AI_REVIEWS")
        by_pair={r["pair"]:r for r in ready["candidate_reviews"]}
        self.assertEqual(by_pair["uai_idr"]["reason"],
                         "WAIT_PARTIAL_SOURCE_AND_UNCALLED_ROLES")
        self.assertEqual(by_pair["cst_idr"]["reason"],
                         "WAIT_CORRUPT_LOCAL_INDEX")
        self.assertIsNone(ready["selected_pair"])
        self.assertEqual(ready["qualified_role_decision_count"],0)
        self.assertEqual(ready["provider_calls"],0)
        self.assertFalse(ready["execution_allowed"])

    def test_mutated_source_digest_or_role_slot_cannot_be_reviewed(self):
        self.seed("uai_idr")
        self.install_index()
        original=inspect_original_ws3_three_role_handoff_readonly(
            db_path=self.db,now_epoch=NOW)
        for mutation in ("digest","slot","g1","expired"):
            with self.subTest(mutation=mutation):
                changed=deepcopy(original)
                if mutation=="digest":
                    changed["candidates"][0]["evidence_digest_sha256"]="0"*64
                if mutation=="slot":
                    changed["candidates"][0]["role_slots"][0]["model_output"]={"BUY":True}
                if mutation=="g1":
                    changed["g1_independent_issuer_verified"]=True
                seen=assess_uncalled_ws3_candidate_loop(
                    handoff=changed,
                    now_epoch=NOW+200 if mutation=="expired" else NOW)
                self.assertEqual(seen["watch_candidate_count"],0)
                self.assertIsNone(seen["selected_pair"])
                self.assertFalse(seen["execution_allowed"])
                self.assertFalse(seen["paper_fill_authorized"])
                self.assertTrue(seen["status"].startswith("RESCAN_"))

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


    def _set_turnover_pair(self, pair, *, previous, latest):
        with sqlite3.connect(self.db) as con:
            con.execute("""
                UPDATE m03_ws_summary_watch_minute
                SET rolling_24h_idr=?
                WHERE pair=? AND minute_epoch=?
            """, (str(previous), pair, NOW-120))
            con.execute("""
                UPDATE m03_ws_summary_watch_minute
                SET rolling_24h_idr=?
                WHERE pair=? AND minute_epoch=?
            """, (str(latest), pair, NOW-60))

    def test_below_400m_ws3_ticker_remains_source_but_not_m04_handoff(self):
        from indodax_ai_bot.m05_radar_momentum_nonlive import (
            inspect_original_m03_ws3_watch_readonly,
        )
        self.seed("anoa_idr")
        self.seed("icnt_idr")
        self._set_turnover_pair(
            "anoa_idr", previous=506480736, latest=506356556)
        self._set_turnover_pair(
            "icnt_idr", previous=282977955, latest=283015485)
        source = inspect_original_m03_ws3_watch_readonly(
            db_path=self.db, now_epoch=NOW)
        self.assertIn("icnt_idr", {
            c["pair"] for c in source["ranked_review_shortlist_top40"]})
        self.assertIn("anoa_idr", {
            c["pair"] for c in source["ranked_review_shortlist_top40"]})

        handoff = inspect_original_ws3_three_role_handoff_readonly(
            db_path=self.db, now_epoch=NOW)
        self.assertEqual(
            handoff["status"], "M04_WS3_HANDOFF_ROLE_INPUTS_PREPARED")
        self.assertEqual(handoff["candidate_count"], 1)
        self.assertEqual(handoff["candidates"][0]["pair"], "anoa_idr")
        self.assertEqual(handoff["m04_ws3_handoff_min_rolling_24h_idr"],
                         "400000000")
        self.assertEqual(
            handoff["source_watches_below_400m_excluded_from_m04_handoff"], 1)
        self.assertTrue(handoff["source_watch_price_observation_is_not_deleted"])
        self.assertTrue(handoff["m03_trade_channel_allocator_unchanged"])
        self.assertTrue(handoff["m05_original_telegram_top5_unchanged"])
        self.assertEqual(handoff["actual_three_role_ai_calls"], 0)
        self.assertFalse(handoff["execution_allowed"])
        self.assertFalse(handoff["g1_independent_issuer_verified"])

    def test_400m_exact_boundary_is_allowed_in_m04_ws3_handoff(self):
        self.seed("boundary_idr")
        self._set_turnover_pair(
            "boundary_idr", previous=390000000, latest=400000000)
        report = inspect_original_ws3_three_role_handoff_readonly(
            db_path=self.db, now_epoch=NOW)
        self.assertEqual(report["status"], "M04_WS3_HANDOFF_ROLE_INPUTS_PREPARED")
        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(report["candidates"][0]["pair"], "boundary_idr")
        self.assertEqual(report[
            "source_watches_below_400m_excluded_from_m04_handoff"], 0)
        self.assertFalse(report["execution_allowed"])

    def test_below_400m_only_original_watch_is_not_global_data_unknown(self):
        from indodax_ai_bot.m05_radar_momentum_nonlive import (
            inspect_original_m03_ws3_watch_readonly,
        )
        self.seed("icnt_idr")
        self._set_turnover_pair(
            "icnt_idr", previous=282977955, latest=283015485)
        original = inspect_original_m03_ws3_watch_readonly(
            db_path=self.db, now_epoch=NOW)
        self.assertEqual(
            original["ranked_review_shortlist_top40"][0]["pair"], "icnt_idr")
        report = inspect_original_ws3_three_role_handoff_readonly(
            db_path=self.db, now_epoch=NOW)
        self.assertEqual(report["status"], "M04_WS3_HANDOFF_NO_CURRENT_WATCH")
        self.assertEqual(report["candidate_count"], 0)
        self.assertEqual(
            report["source_watches_below_400m_excluded_from_m04_handoff"], 1)
        self.assertTrue(report["source_watch_price_observation_is_not_deleted"])
        self.assertEqual(report["actual_three_role_ai_calls"], 0)
        self.assertFalse(report["execution_allowed"])
        self.assertFalse(report["paper_fill_created"] if
                         "paper_fill_created" in report else report["order_sent"])


if __name__=="__main__":
    unittest.main()
