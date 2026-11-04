"""M03 original --once opt-in mainpath, WS read-only and staged minute timer.

No public internet or private Indodax, Sumopod, executable orders, or
systemd changes occur in these tests. Strict G1 Option A remains REJECT.
"""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.contracts import FinalDecision
from indodax_ai_bot.orchestrator import CycleResult, DailyScalpingOrchestrator
from indodax_ai_bot.trade_stream import PublicTradeStreamJournal, StreamEvidenceError
from test_m03g_bulk import FakeBulkMarket, fake_screen


class M03MainpathOptInTests(unittest.TestCase):
    def _cli(self, args):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(args)
        return rc, (json.loads(out.getvalue()) if out.getvalue() else None), err.getvalue()

    def test_default_once_is_original_no_network_m01_safe_stub(self):
        with TemporaryDirectory() as td:
            with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                       side_effect=AssertionError("DEFAULT_ONCE_MUST_NOT_FETCH")):
                rc, body, err = self._cli([
                    "--once", "--db", str(Path(td) / "decisions.sqlite3"),
                ])
        self.assertEqual(rc, 0, err)
        self.assertEqual(body["reason"], "G1_SOURCE_NOT_CONNECTED")
        self.assertEqual(body["final_decision"], "REJECT")
        self.assertFalse(body["execution_allowed"])
        self.assertNotIn("public_bulk", body)

    def test_opt_in_once_reuses_original_scanner_bulk_and_m04_source_gate(self):
        with TemporaryDirectory() as td:
            db = Path(td) / "decisions.sqlite3"
            ws = Path(td) / "existing-ws.sqlite3"
            PublicTradeStreamJournal(ws)
            source = fake_screen()
            eligible = tuple(
                replace(item, volume_idr_24h=Decimal("500000000"),
                        last_idr=Decimal("100"))
                for item in source.shortlist
            )
            source = replace(source, universe=eligible, shortlist=eligible,
                             watchlist=eligible[:2])
            with (patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner,
                  patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                        return_value=FakeBulkMarket()),
                  patch("indodax_ai_bot.sumopod_provider._post",
                        side_effect=AssertionError("PAID_NETWORK_FORBIDDEN")) as post):
                scanner.return_value.scan.return_value = source
                rc, body, err = self._cli([
                    "--once", "--public-scanner-mainpath",
                    "--ws-db", str(ws), "--db", str(db),
                ])
            self.assertEqual(rc, 0, err)
            self.assertEqual(scanner.call_count, 1)
            post.assert_not_called()
            self.assertEqual(body["mainpath_mode"],
                             "M03_PUBLIC_SCANNER_TO_M04_OWNER_ADVISORY")
            self.assertEqual(body["public_24h_volume_floor_idr"], "150000000")
            self.assertTrue(body["btc_ws_existing_journal_readonly"])
            self.assertEqual(body["public_bulk"]["requested_pairs"][0], "btc_idr")
            self.assertEqual(body["public_bulk"]["attempted_count"], 20)
            self.assertIn("btc_ws_closed_minute_sample", body["public_bulk"])
            self.assertIn("btc_ws_source_quality",
                          body["public_bulk"]["g1_source_admission"])
            self.assertFalse(body["public_bulk"]["g1_verified"])
            self.assertEqual(body["ai_shadow"]["completed_roles"], [])
            self.assertFalse(body["sumopod_shadow"]["configured"])
            self.assertIsNone(body["final_decision"])
            self.assertFalse(body["execution_allowed"])
            with sqlite3.connect(db) as con:
                recorded = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
                samples = con.execute(
                    "SELECT COUNT(*) FROM public_ticker_minute_samples"
                ).fetchone()[0]
            self.assertEqual(recorded, 0)
            self.assertEqual(samples, 20)

    def test_optin_requires_existing_distinct_ws_and_once_before_any_fetch(self):
        with TemporaryDirectory() as td:
            db = Path(td) / "decisions.sqlite3"
            missing = Path(td) / "missing.sqlite3"
            invalid = [
                (["--scan-once", "--public-scanner-mainpath",
                  "--ws-db", str(missing)], "M03_MAINPATH_REQUIRES_ONCE"),

                (["--once", "--public-scanner-mainpath",
                  "--ws-db", str(db)], "M03_MAINPATH_WS_DB_MUST_BE_SEPARATE"),
            ]
            for args, reason in invalid:
                with self.subTest(reason=reason):
                    with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                               side_effect=AssertionError("MUST_NOT_FETCH")):
                        rc, body, err = self._cli(args + ["--db", str(db)])
                    self.assertEqual(rc, 2)
                    self.assertIsNone(body)
                    self.assertIn(reason, err)
            self.assertFalse(db.exists())
            self.assertFalse(missing.exists())

    def test_explicit_40_limit_routes_through_same_orchestrator_and_clamps_floor(self):
        with TemporaryDirectory() as td:
            db = Path(td) / "decisions.sqlite3"
            ws = Path(td) / "ws.sqlite3"
            PublicTradeStreamJournal(ws)
            fake = CycleResult(
                "synthetic", FinalDecision.REJECT, "G1_SOURCE_UNVERIFIED",
                bulk={"coverage_status": "PARTIAL_INVALID_PAIR_EVIDENCE"},
                ai_shadow={"completed_roles": []},
            )
            with patch.object(DailyScalpingOrchestrator,
                              "run_bulk_deep_cycle", return_value=fake) as bulk:
                rc, body, err = self._cli([
                    "--once", "--public-scanner-mainpath",
                    "--bulk-deep-limit", "40",
                    "--bulk-min-volume-idr", "100000000",
                    "--ws-db", str(ws), "--db", str(db),
                ])
            self.assertEqual(rc, 0, err)
            self.assertEqual(bulk.call_count, 1)
            kw = bulk.call_args.kwargs
            self.assertEqual(kw["deep_limit"], 40)
            self.assertEqual(kw["ws_db_path"], str(ws))
            self.assertEqual(kw["min_volume_idr"], Decimal("150000000"))
            self.assertEqual(body["public_24h_volume_floor_idr"], "150000000")
            self.assertEqual(body["final_decision"], "REJECT")
            self.assertFalse(body["execution_allowed"])

    def test_readonly_ws_accessor_cannot_create_or_write_collector_db(self):
        with TemporaryDirectory() as td:
            missing = Path(td) / "missing-ws.sqlite3"
            with self.assertRaisesRegex(StreamEvidenceError,
                                        "PUBLIC_WS_SQLITE_NOT_FOUND"):
                PublicTradeStreamJournal.readonly(missing)
            self.assertFalse(missing.exists())
            db = Path(td) / "original-ws.sqlite3"
            PublicTradeStreamJournal(db)
            ro = PublicTradeStreamJournal.readonly(db)
            with ro._connect() as con:
                self.assertEqual(con.execute("PRAGMA query_only").fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    con.execute(
                        "INSERT INTO ws_stream_state "
                        "(pair,epoch,last_offset,status) VALUES "
                        "('btc_idr','123',0,'GAP')"
                    )
            with sqlite3.connect(db) as con:
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM ws_stream_state"
                ).fetchone()[0], 0)

    def test_staged_minute_timer_is_disabled_and_never_an_executor(self):
        root = Path(__file__).resolve().parents[1]
        service = (root / "deploy/indodax-v2-m03-mainpath.service").read_text()
        timer = (root / "deploy/indodax-v2-m03-mainpath.timer").read_text()
        self.assertIn("--scheduled-public-scanner-once", service)
        self.assertNotIn("--once --public-scanner-mainpath", service)
        self.assertIn("--bulk-deep-limit 20", service)
        self.assertIn("--bulk-min-volume-idr 150000000", service)
        self.assertIn("WorkingDirectory=%h/indodax-ai-bot-v2\n", service)
        self.assertNotIn("indodax-ai-bot-v2-m03-runtime", service)
        self.assertIn("ConditionPathExists=%h/indodax-ai-bot-v2/indodax_ai_bot/__main__.py", service)
        self.assertIn("--ws-db %h/indodax-ai-bot-v2/runtime/public_ws_btc.sqlite3", service)
        self.assertIn("--db %h/indodax-ai-bot-v2/runtime/decisions.sqlite3", service)
        self.assertNotIn("--ws-db runtime/public_ws_btc.sqlite3", service)
        self.assertNotIn("--db runtime/decisions.sqlite3", service)
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
