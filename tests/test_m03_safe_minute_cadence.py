"""M03 opt-in single systemd ticker/deep cadence, NO market network or trades.

One original orchestrator. Lightweight scans each aligned minute; original
G1 source-checked 20-deep only on five-minute boundaries. This is NOT proof
of real 24/7 runtime reliability, IP-wide rate availability or G1 provenance.
"""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.contracts import FinalDecision
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import CycleResult, DailyScalpingOrchestrator


class M03SafeMinuteCadenceTests(unittest.TestCase):
    def invoke(self, now, args=None):
        with TemporaryDirectory() as td:
            ws = Path(td) / "observed-ws.sqlite3"
            ws.write_bytes(b"PREEXISTING_FIXTURE_ONLY")
            db = Path(td) / "decisions.sqlite3"
            argv = [
                "--scheduled-public-scanner-once",
                "--ws-db", str(ws),
                "--db", str(db),
                "--bulk-min-volume-idr", "150000000",
                "--bulk-deep-limit", "20",
            ]
            if args is not None:
                argv = args + ["--db", str(db)]
            out, err = StringIO(), StringIO()
            scan = CycleResult("scan", FinalDecision.REJECT,
                               "G1_DEEP_ANALYSIS_AND_RISK_NOT_CONNECTED",
                               screening={"all_idr_pairs": 40})
            deep = CycleResult(
                "deep", FinalDecision.REJECT,
                "G1_PUBLIC_BULK_SOURCE_UNVERIFIED",
                bulk={"coverage_status": "PARTIAL_INVALID_PAIR_EVIDENCE",
                      "g1_verified": False, "execution_allowed": False},
                ai_shadow={"completed_roles": [], "execution_allowed": False},
            )
            with (
                patch("indodax_ai_bot.__main__.time.time", return_value=now),
                patch.object(DailyScalpingOrchestrator, "run_scan_cycle",
                             return_value=scan) as light,
                patch.object(DailyScalpingOrchestrator, "run_cycle",
                             return_value=deep) as scheduled,
                # The cadence fixture mocks the orchestrator and therefore
                # intentionally does not create its original journal decision.
                # Actual compact SQLite persistence is covered in the new
                # end-to-end CLI/HTTP-boundary test, not by this stub.
                patch.object(DecisionJournal, "record_m03_compact_tick"),
                patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                      side_effect=AssertionError("NO_EXCHANGE_GET")),
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                rc = main(argv)
            return rc, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue(), light, scheduled

    def test_five_minute_deep_calls_same_original_orchestrator_one_time(self):
        rc, body, err, light, scheduled = self.invoke(1800000010)
        self.assertEqual(rc, 0, err)
        light.assert_not_called()
        scheduled.assert_called_once()
        args = scheduled.call_args
        self.assertTrue(args.kwargs["public_scanner"])
        self.assertEqual(args.kwargs["deep_limit"], 20)
        self.assertEqual(args.kwargs["min_volume_idr"], Decimal("150000000"))
        self.assertIn("observed-ws.sqlite3", args.kwargs["ws_db_path"])
        self.assertEqual(body["scheduled_m03_lane"], "DEEP20_EVERY_FIVE_MINUTES")
        self.assertFalse(body["public_bulk"]["g1_verified"])
        self.assertEqual(body["ai_shadow"]["completed_roles"], [])
        self.assertFalse(body["paid_network_allowed"])
        self.assertFalse(body["live_trading_authorized"])
        self.assertEqual(body["final_decision"], "REJECT")
        self.assertFalse(body["execution_allowed"])

    def test_other_minutes_only_one_light_scan_and_no_deep(self):
        for minute in range(1,5):
            with self.subTest(minute=minute):
                rc, body, err, light, scheduled = self.invoke(
                    1800000010 + 60*minute,
                )
                self.assertEqual(rc, 0, err)
                light.assert_called_once()
                scheduled.assert_not_called()
                self.assertEqual(body["scheduled_m03_lane"],
                                 "LIGHT_ALL_IDR_EVERY_MINUTE")
                self.assertEqual(body["public_scanner"]["all_idr_pairs"], 40)
                self.assertIsNone(body["public_bulk"])
                self.assertEqual(body["final_decision"], "REJECT")
                self.assertFalse(body["execution_allowed"])

    def test_late_or_early_tick_never_scans_or_catches_up(self):
        for second in (0, 4, 21, 35, 59):
            with self.subTest(second=second):
                rc, body, err, light, scheduled = self.invoke(
                    1800000000 + second,
                )
                self.assertEqual(rc, 3, err)
                light.assert_not_called()
                scheduled.assert_called_once()
                self.assertFalse(scheduled.call_args.kwargs.get("public_scanner", False))
                self.assertEqual(body["scheduled_m03_lane"],
                                 "SKIPPED_LATE_OR_EARLY_REJECT")
                self.assertFalse(body["execution_allowed"])

    def test_no_ws_database_or_invalid_40_limit_fails_before_any_public_fetch(self):
        for args, code in (
            (["--scheduled-public-scanner-once"],
             "M03_SCHEDULED_REQUIRES_BTC_WS_DB"),
            (["--scheduled-public-scanner-once",
              "--bulk-deep-limit", "40"],
             "M03_SCHEDULED_BULK_LIMIT_MUST_BE_20"),
            (["--scheduled-public-scanner-once",
              "--ws-db", "/nonexistent/m03-ws-locked.sqlite3"],
             "M03_SCHEDULED_WS_DB_NOT_FOUND"),
        ):
            with self.subTest(code=code):
                rc, body, err, light, scheduled = self.invoke(
                    1800000010, args,
                )
                self.assertEqual(rc, 2)
                self.assertIsNone(body)
                self.assertIn(code, err)
                light.assert_not_called()
                scheduled.assert_not_called()

    def test_scheduled_mode_cannot_construct_paid_sumopod_adapter(self):
        with TemporaryDirectory() as td:
            ws = Path(td) / "ws.db"
            ws.write_bytes(b"fixture")
            out, err = StringIO(), StringIO()
            with patch("indodax_ai_bot.__main__.SumopodChatAdapter.configured_from_env",
                       side_effect=AssertionError("NO_PAID_PROVIDER_CONSTRUCTION")) as adapter:
                with redirect_stdout(out), redirect_stderr(err):
                    rc = main([
                        "--scheduled-public-scanner-once",
                        "--sumopod-shadow",
                        "--ws-db", str(ws),
                        "--db", str(Path(td) / "decisions.db"),
                    ])
            self.assertEqual(rc, 2)
            self.assertIn("SUMOPOD_SHADOW_REQUIRES_BULK_DEEP_ONCE", err.getvalue())
            adapter.assert_not_called()

    def test_systemd_timer_uses_one_nonoverlapping_two_lane_entry(self):
        root = Path(__file__).resolve().parents[1]
        service = (root / "deploy/indodax-v2-m03-mainpath.service").read_text()
        timer = (root / "deploy/indodax-v2-m03-mainpath.timer").read_text()
        self.assertIn(" --scheduled-public-scanner-once ", service)
        self.assertIn("--bulk-deep-limit 20", service)
        self.assertIn("--bulk-min-volume-idr 150000000", service)
        self.assertIn("WorkingDirectory=%h/indodax-ai-bot-v2-m03-runtime", service)
        self.assertIn("--ws-db %h/indodax-ai-bot-v2/runtime/public_ws_btc.sqlite3", service)
        self.assertIn("--db %h/indodax-ai-bot-v2/runtime/decisions.sqlite3", service)
        self.assertIn("INDODAX_TRADING_ENABLED=NO", service)
        self.assertIn("INDODAX_ALLOW_LIVE_EXECUTION=NO", service)
        self.assertIn("INDODAX_EXECUTION_MODE=DRY_RUN", service)
        self.assertIn("/usr/bin/flock -n ", service)
        self.assertIn("Restart=no", service)
        self.assertIn("OnCalendar=*-*-* *:*:10", timer)
        self.assertIn("Persistent=false", timer)
        self.assertNotIn("systemctl enable", service + timer)


if __name__ == "__main__":
    unittest.main()
