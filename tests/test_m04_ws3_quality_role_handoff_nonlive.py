"""WS3 original V2 evidence to MARKET/RISK/STRATEGY uncalled role slots."""
from pathlib import Path
from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator

from indodax_ai_bot.m04_radar_three_role_handoff_readonly import (
    inspect_original_ws3_three_role_handoff_readonly,
    inspect_original_ws3_micro_prealert_three_role_handoff_readonly,
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
        self.assertGreater(row["quality_score_out_of_100"], 0)
        self.assertFalse(row["quality_review_observational_only"]["buy_recommended"])
        self.assertFalse(row["public_evidence"]["quality_score_is_trade_probability"])
        self.assertEqual(r["quality_scored_candidates_before_top2"], 1)

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
        self.assertGreaterEqual(row["quality_score_out_of_100"], 50)
        self.assertEqual(
            row["quality_review_observational_only"]["signals"][
                "trade_activity_source_observed"], True,
        )
        self.assertNotIn(
            "fresh_orderbook_snapshot_not_observed", row["missing_evidence"]
        )
        self.assertIn(
            "complete_trade_tape_and_aggressor_flow_unverified",
            row["missing_evidence"],
        )
        self.assertFalse(row["public_evidence"]["quality_review"]["execution_allowed"])


    def test_two_watch_candidates_rank_actual_micro_quality_before_price_pump(self):
        """Same M03 source, more verified activity and narrow book outranks pump."""
        self.seed()
        self.row("eth_idr", -2, 100, 900000000)
        self.row("eth_idr", -1, 106, 901000000)
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
            db.execute(
                "INSERT INTO m03_ws_microstructure_watch_minute VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("uai_idr", NOW-60, NOW-5, 2, 3, 1,
                 "500000", "100000", NOW-8, 40,
                 0, 0, 51, NOW-5, "100", "100.2", "19.9800",
                 "9000000", "7000000", "1.2857", 0)
            )
        report = inspect_original_ws3_three_role_handoff_readonly(
            db_path=self.path, now_epoch=NOW,
        )
        self.assertEqual(report["candidate_count"], 2)
        first, second = report["candidates"]
        self.assertEqual(first["pair"], "uai_idr")
        self.assertEqual(second["pair"], "eth_idr")
        self.assertGreater(
            first["quality_score_out_of_100"],
            second["quality_score_out_of_100"]
        )
        self.assertFalse(first["quality_review_observational_only"]["buy_recommended"])
        self.assertEqual(report["actual_three_role_ai_calls"], 0)
        for row in report["candidates"]:
            digests = {s["evidence_digest_sha256"] for s in row["role_slots"]}
            self.assertEqual(digests, {row["evidence_digest_sha256"]})


    def test_owner_relative_db_path_handoff_is_not_misclassified_untrusted(self):
        """Real owner command passes Path('runtime/decisions.sqlite3')."""
        import os
        self.seed()
        relative_path = Path(os.path.relpath(self.path, start=Path.cwd()))
        self.assertFalse(relative_path.is_absolute())
        raw = inspect_original_m03_ws3_watch_readonly(
            db_path=relative_path, now_epoch=NOW,
        )
        self.assertEqual(raw["status"],
                         "M03_WS3_WATCH_RADAR_OBSERVED_UNATTESTED")
        self.assertEqual(len(raw["candidates"]), 1)
        report = inspect_original_ws3_three_role_handoff_readonly(
            db_path=relative_path, now_epoch=NOW,
        )
        self.assertEqual(report["status"],
                         "M04_WS3_HANDOFF_ROLE_INPUTS_PREPARED")
        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(
            [r["role"] for r in report["candidates"][0]["role_slots"]],
            ["MARKET", "RISK", "STRATEGY"],
        )
        self.assertEqual(report["actual_three_role_ai_calls"], 0)
        self.assertFalse(report["execution_allowed"])

    def test_empty_source_is_not_genuine_ai_reject(self):
        # A separate private SQLite without original M03 minute table.
        # setUp() intentionally DOES create that table in self.path.
        no_table = self.path.parent / "no-summary-table.sqlite3"
        with sqlite3.connect(no_table) as db:
            db.execute("CREATE TABLE unrelated_source_only(x TEXT)")
        no_table.chmod(0o600)
        absolute = inspect_original_ws3_three_role_handoff_readonly(
            db_path=no_table, now_epoch=NOW,
        )
        self.assertEqual(absolute["status"], "M04_WS3_SOURCE_TABLE_ABSENT")
        self.assertEqual(absolute["upstream_source_status"],
                         "M05_WS3_SOURCE_ABSENT")
        self.assertEqual(absolute["candidate_count"], 0)
        self.assertFalse(absolute["execution_allowed"])

    def test_untrusted_source_path_has_precise_no_order_diagnostic(self):
        bad = inspect_original_ws3_three_role_handoff_readonly(
            db_path=self.path.parent / "not-existing.sqlite3",
            now_epoch=NOW,
        )
        self.assertEqual(bad["status"], "M04_WS3_SOURCE_FILE_UNTRUSTED")
        self.assertEqual(bad["upstream_source_status"],
                         "M05_WS3_SOURCE_UNTRUSTED")
        self.assertFalse(bad["order_sent"])
        self.assertFalse(bad["execution_allowed"])

    def test_legitimate_source_without_current_price_rise_is_no_watch(self):
        self.row("btc_idr", -2, 100, 900000000)
        self.row("btc_idr", -1, 100, 900010000)
        import os
        relative_path = Path(os.path.relpath(self.path, start=Path.cwd()))
        report = inspect_original_ws3_three_role_handoff_readonly(
            db_path=relative_path, now_epoch=NOW,
        )
        self.assertEqual(report["status"],
                         "M04_WS3_HANDOFF_NO_CURRENT_WATCH")
        self.assertNotIn("upstream_source_status", report)
        self.assertEqual(report["candidate_count"], 0)
        self.assertFalse(report["execution_allowed"])


    def insert_micro_for_pair(self, pair, *, captured_epoch=None,
                              trade_frames=2, spread_bps="19.9800"):
        captured = NOW - 5 if captured_epoch is None else captured_epoch
        with sqlite3.connect(self.path) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS m03_ws_microstructure_watch_minute(
                    pair TEXT NOT NULL, minute_epoch INTEGER NOT NULL,
                    observed_local_epoch INTEGER NOT NULL,
                    trade_frames INTEGER NOT NULL,
                    source_buy_count INTEGER, source_sell_count INTEGER,
                    source_buy_idr TEXT, source_sell_idr TEXT,
                    last_trade_epoch INTEGER, trade_offset INTEGER,
                    trade_gap_observed INTEGER NOT NULL,
                    trade_truncated INTEGER NOT NULL,
                    book_offset INTEGER, book_local_epoch INTEGER,
                    best_bid_idr TEXT, best_ask_idr TEXT, spread_bps TEXT,
                    bid_depth_top5_idr TEXT, ask_depth_top5_idr TEXT,
                    book_imbalance_top5_ratio TEXT,
                    source_malformed INTEGER NOT NULL,
                    PRIMARY KEY(pair,minute_epoch))
            """)
            db.execute(
                "INSERT INTO m03_ws_microstructure_watch_minute VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pair, NOW-60, captured, trade_frames,
                 3 if trade_frames else None,
                 1 if trade_frames else None,
                 "500000" if trade_frames else None,
                 "100000" if trade_frames else None,
                 NOW-8 if trade_frames else None,
                 40 if trade_frames else None,
                 0, 0, 51, captured,
                 "100", "100.2", spread_bps,
                 "9000000", "7000000", "1.2857", 0)
            )

    def test_real_micro_prealert_passes_under_telegram_threshold(self):
        """A +0.2% move and real public book/trades can reach M04 early."""
        self.row("uai_idr", -2, 100, 700000000)
        self.row("uai_idr", -1, "100.2", 700050000)
        self.insert_micro_for_pair("uai_idr")
        watch = inspect_original_m03_ws3_watch_readonly(
            db_path=self.path, now_epoch=NOW,
        )
        self.assertEqual(watch["candidates"], [])
        self.assertEqual(watch["micro_prealert_observed_count"], 1)
        self.assertFalse(watch["micro_prealert_is_owner_telegram_alert"])
        self.assertFalse(
            watch["micro_prealert_candidates_top40"][0][
                "m05_telegram_watch_threshold_met"
            ]
        )
        r = inspect_original_ws3_micro_prealert_three_role_handoff_readonly(
            db_path=self.path, now_epoch=NOW,
        )
        self.assertEqual(r["status"],
                         "M04_WS3_MICRO_PREALERT_ROLE_INPUTS_PREPARED")
        self.assertEqual(r["candidate_count"], 1)
        self.assertEqual(r["three_role_input_slots_prepared"], 3)
        self.assertEqual(r["actual_three_role_ai_calls"], 0)
        self.assertEqual(r["provider_http_calls"], 0)
        self.assertEqual(r["telegram_messages_sent"], 0)
        self.assertEqual(r["new_scanner_calls"], 0)
        self.assertFalse(r["order_sent"])
        self.assertFalse(r["execution_allowed"])
        self.assertFalse(r["source_is_m05_telegram_alert"])
        c = r["candidates"][0]
        self.assertEqual(c["pair"], "uai_idr")
        self.assertGreater(c["quality_score_out_of_100"], 45)
        self.assertEqual(
            c["public_evidence"]["source_kind"],
            "ORIGINAL_V2_WS3_MICRO_PREALERT_SOURCE_UNATTESTED",
        )
        self.assertFalse(
            c["public_evidence"]["m05_telegram_watch_threshold_met"]
        )
        self.assertEqual(c["public_evidence"]["trade_activity"],
                         "PARTIAL_WS3_CAPTURE")
        self.assertEqual(c["public_evidence"]["orderbook"],
                         "PARTIAL_LOCAL_WS3_BOOK_SNAPSHOT")
        self.assertEqual(
            [x["role"] for x in c["role_slots"]],
            ["MARKET", "RISK", "STRATEGY"],
        )
        self.assertTrue(all(
            x["model_output"] is None
            and x["evidence_digest_sha256"] == c["evidence_digest_sha256"]
            for x in c["role_slots"]
        ))
        self.assertFalse(c["quality_review_observational_only"]["buy_recommended"])

    def test_micro_prealert_does_not_fabricate_missing_micro(self):
        self.row("uai_idr", -2, 100, 700000000)
        self.row("uai_idr", -1, "100.2", 700050000)
        r = inspect_original_ws3_micro_prealert_three_role_handoff_readonly(
            db_path=self.path, now_epoch=NOW,
        )
        self.assertEqual(r["status"],
                         "M04_WS3_MICRO_PREALERT_NO_CURRENT_SOURCE")
        self.assertEqual(r["candidate_count"], 0)
        self.assertEqual(r["actual_three_role_ai_calls"], 0)

    def test_stale_micro_does_not_enter_early_role_handoff(self):
        self.row("uai_idr", -2, 100, 700000000)
        self.row("uai_idr", -1, "100.2", 700050000)
        self.insert_micro_for_pair("uai_idr", captured_epoch=NOW-300)
        r = inspect_original_ws3_micro_prealert_three_role_handoff_readonly(
            db_path=self.path, now_epoch=NOW,
        )
        self.assertEqual(r["candidate_count"], 0)
        self.assertFalse(r["execution_allowed"])

    def test_prealert_does_not_promote_advanced_pump_or_manual_assets(self):
        self.row("uai_idr", -2, 100, 700000000)
        self.row("uai_idr", -1, 110, 700050000)
        self.insert_micro_for_pair("uai_idr")
        self.row("floki_idr", -2, 100, 700000000)
        self.row("floki_idr", -1, "100.2", 700050000)
        self.insert_micro_for_pair("floki_idr")
        r = inspect_original_ws3_micro_prealert_three_role_handoff_readonly(
            db_path=self.path, now_epoch=NOW,
        )
        self.assertEqual(r["candidate_count"], 0)
        self.assertFalse(r["execution_allowed"])

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


    def _micro_readiness_seed(self):
        self.row("uai_idr", -2, 100, 700000000)
        self.row("uai_idr", -1, "100.2", 700050000)
        self.insert_micro_for_pair("uai_idr")

    def test_original_cli_micro_to_role_readiness_never_pays_or_writes(self):
        self._micro_readiness_seed()
        safe = {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
        }
        credential = "secret-for-test-never-print"
        provider_source = {
            "SUMOPOD_API_KEY": credential,
            "SUMOPOD_MODEL_MARKET": "glm-5.3-flash",
            "SUMOPOD_MODEL_RISK": "glm-5.3-flash",
            "SUMOPOD_MODEL_STRATEGY": "glm-5.3-flash",
        }
        out = StringIO()
        with patch.dict(os.environ, safe, clear=True), patch(
            "indodax_ai_bot.__main__._m04_original_provider_scope",
            return_value=(provider_source, "OWNER_PRIVATE_CONFIG",
                          "M04_V2_KEY_FILE_PRESENT_LOCAL_ONLY"),
        ), patch(
            "indodax_ai_bot.__main__.DecisionJournal",
            side_effect=AssertionError("UNAUTHORIZED_JOURNAL_CREATE"),
        ) as journal, patch(
            "indodax_ai_bot.__main__.IndodaxPublicMarket",
            side_effect=AssertionError("NO_PUBLIC_REST"),
        ) as rest, patch(
            "indodax_ai_bot.sumopod_provider._post",
            side_effect=AssertionError("NO_PROVIDER_HTTP"),
        ) as provider, patch(
            "indodax_ai_bot.__main__.time.time", return_value=NOW,
        ), redirect_stdout(out):
            result_code = main([
                "--m04-ws3-micro-readiness-once", "--db", str(self.path),
            ])
        self.assertEqual(result_code, 0)
        journal.assert_not_called()
        rest.assert_not_called()
        provider.assert_not_called()
        raw = out.getvalue()
        self.assertNotIn(credential, raw)
        report = json.loads(raw)["m04_ws3_micro_role_readiness"]
        self.assertEqual(
            report["status"], "M04_WS3_READINESS_ROLES_WAITING_G1_AND_PAID_GO",
        )
        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(report["role_slots_validated"], 3)
        self.assertEqual(
            report["candidates"][0]["role_input_order"],
            ["MARKET", "RISK", "STRATEGY"],
        )
        self.assertFalse(report["candidates"][0]["candidate_buy_qualified"])
        self.assertFalse(report["g1_independent_issuer_verified"])
        self.assertFalse(report["model_dispatch_allowed"])
        self.assertEqual(report["actual_three_role_responses"], 0)
        self.assertEqual(report["paid_provider_calls"], 0)
        self.assertFalse(report["execution_allowed"])

    def test_original_readiness_rejects_tampered_digest_and_role_order(self):
        self._micro_readiness_seed()
        clean = inspect_original_ws3_micro_prealert_three_role_handoff_readonly(
            db_path=self.path, now_epoch=NOW,
        )
        for tamper in ("digest", "role", "claim_g1"):
            forged = deepcopy(clean)
            if tamper == "digest":
                forged["candidates"][0]["evidence_digest_sha256"] = "0" * 64
            elif tamper == "role":
                forged["candidates"][0]["role_slots"][1]["role"] = "MARKET"
            else:
                forged["candidates"][0]["public_evidence"][
                    "independent_g1_issuer_verified"] = True
            with self.subTest(tamper=tamper), patch(
                "indodax_ai_bot.m04_radar_three_role_handoff_readonly."
                "inspect_original_ws3_micro_prealert_three_role_handoff_readonly",
                return_value=forged,
            ):
                result = DailyScalpingOrchestrator.inspect_m04_ws3_micro_role_readiness_readonly(
                    db_path=self.path, now_epoch=NOW, provider_report={},
                )
                self.assertEqual(
                    result["status"], "M04_WS3_READINESS_PACKET_INTEGRITY_FAILED",
                )
                self.assertEqual(result["candidate_count"], 0)
                self.assertFalse(result["model_dispatch_allowed"])

    def test_original_readiness_no_micro_and_missing_db_remain_nonlive(self):
        self.seed()
        no_micro = DailyScalpingOrchestrator.inspect_m04_ws3_micro_role_readiness_readonly(
            db_path=self.path, now_epoch=NOW, provider_report={},
        )
        self.assertEqual(no_micro["status"], "M04_WS3_READINESS_NO_CURRENT_PREALERT")
        self.assertEqual(no_micro["role_slots_validated"], 0)
        self.path.unlink()
        unavailable = DailyScalpingOrchestrator.inspect_m04_ws3_micro_role_readiness_readonly(
            db_path=self.path, now_epoch=NOW, provider_report={},
        )
        self.assertEqual(unavailable["status"], "M04_WS3_READINESS_SOURCE_NOT_ACCEPTED")
        self.assertEqual(unavailable["candidate_count"], 0)
        self.assertFalse(unavailable["model_dispatch_allowed"])


if __name__ == "__main__":
    unittest.main()
