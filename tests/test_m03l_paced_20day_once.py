"""Owner-paced one-command 20d M03-L research archive: scoped NONLIVE tests.

All HTTP is fake/injected, no shared-IP quota mutation, Sumopod or orders.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ops.m03l_paced_20day_once import (
    TARGET_1M_ROWS, REQUIRED_HEADROOM, M03LPacedDenied,
    existing_contiguous_rows, run_original_batch, run_paced, main,
)

SAFE_OWNER_ENV = {
    "INDODAX_TRADING_ENABLED": "NO",
    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
    "INDODAX_EXECUTION_MODE": "DRY_RUN",
    "INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "YES",
    "M03L_OWNER_PACED_20D_GO": "YES",
}


class M03LOneCommandArchive20d(unittest.TestCase):
    def run_fake(self, *, initial: int, quota, fail_batch=None,
                 max_wait_checks=45):
        state = {"count": initial}
        calls = []
        sleeps = []
        headroom_calls = [0]

        def actual_count():
            return state["count"]

        def current_headroom():
            headroom_calls[0] += 1
            return quota(headroom_calls[0])

        def fake_batch(*, db, windows, expected_count, env):
            calls.append((windows, expected_count))
            self.assertEqual(env["INDODAX_EXECUTION_MODE"], "DRY_RUN")
            self.assertEqual(env["M03L_OWNER_PACED_20D_GO"], "YES")
            self.assertEqual(str(db), "runtime/m03l_public_ohlc.sqlite3")
            if fail_batch == len(calls):
                state["count"] += 360  # one valid window committed
                return {
                    "accepted": False, "windows_committed": 1,
                    "source_failure_code": "RESEARCH_HISTORY_1M_INCOMPLETE",
                    "final_decision": "REJECT", "execution_allowed": False,
                }
            state["count"] = expected_count
            return {
                "accepted": True, "windows_committed": windows,
                "source_failure_code": "NONE", "final_decision": "REJECT",
                "execution_allowed": False,
            }

        with patch.dict(os.environ, SAFE_OWNER_ENV, clear=True):
            with redirect_stdout(StringIO()) as output:
                try:
                    report = run_paced(
                        count_rows=actual_count, headroom=current_headroom,
                        batch=fake_batch, sleep=sleeps.append,
                        max_wait_checks=max_wait_checks,
                    )
                except M03LPacedDenied as exc:
                    report = str(exc)
        return report, state["count"], calls, sleeps, output.getvalue(), headroom_calls[0]

    def test_one_session_15_batches_5_days_to_20_days(self):
        report, rows, calls, sleeps, output, checks = self.run_fake(
            initial=7200, quota=lambda n: (90, 3, 87),
        )
        self.assertEqual(rows, TARGET_1M_ROWS)
        self.assertEqual(report["archived_days"], 20)
        self.assertEqual(report["batches_completed_this_run"], 15)
        self.assertEqual(report["status"],
                         "M03L_OWNER_PUBLIC_ARCHIVE_20D_TARGET_REACHED_UNATTESTED")
        self.assertEqual(len(calls), 15)
        self.assertTrue(all(w == 4 for w, _ in calls))
        self.assertEqual(calls[0], (4, 8640))
        self.assertEqual(calls[-1], (4, TARGET_1M_ROWS))
        self.assertEqual(sleeps, [65] * 14)
        self.assertFalse(report["g1_verified"])
        self.assertEqual(report["paid_ai_calls"], 0)
        self.assertFalse(report["order_sent"])
        self.assertFalse(report["execution_allowed"])
        self.assertEqual(report["final_decision"], "REJECT")
        self.assertEqual(
            sum(json.loads(line)["max_public_get"]
                for line in output.splitlines()
                if json.loads(line)["status"] == "ORIGINAL_M03L_ONE_BATCH_START"),
            180,
        )

    def test_wait_quota_but_no_spending_while_only_three_left(self):
        report, rows, calls, sleeps, output, checks = self.run_fake(
            initial=TARGET_1M_ROWS - 360,
            quota=lambda n: (90, 87, 3) if n == 1 else (90, 4, 86),
        )
        self.assertEqual(rows, TARGET_1M_ROWS)
        self.assertEqual(calls, [(1, TARGET_1M_ROWS)])
        self.assertEqual(sleeps, [20])
        self.assertEqual(checks, 2)
        self.assertIn("WAIT_SHARED_REST_CAPACITY", output)
        self.assertEqual(report["archived_days"], 20)

    def test_busy_quota_stops_without_one_exchange_get_or_db_write(self):
        report, rows, calls, sleeps, output, checks = self.run_fake(
            initial=7200, quota=lambda n: (90, 87, 3),
            max_wait_checks=2,
        )
        self.assertEqual(report, "M03L_SHARED_REST_CAPACITY_STILL_BUSY")
        self.assertEqual(rows, 7200)
        self.assertEqual(calls, [])
        self.assertEqual(sleeps, [20])
        self.assertEqual(checks, 2)
        self.assertEqual(REQUIRED_HEADROOM, 24)

    def test_source_partial_failure_keeps_committed_rows_without_replay(self):
        report, rows, calls, sleeps, output, checks = self.run_fake(
            initial=7200, quota=lambda n: (90, 0, 90), fail_batch=1,
        )
        self.assertEqual(report, "M03L_BATCH_STOP_INSPECT_SOURCE_OR_QUOTA")
        self.assertEqual(rows, 7560)
        self.assertEqual(calls, [(4, 8640)])
        self.assertEqual(sleeps, [])
        self.assertIn("RESEARCH_HISTORY_1M_INCOMPLETE", output)

    def test_original_cli_wrapper_only_original_public_archive_and_reject(self):
        result = {
            "public_history_archive": {
                "status": "PUBLIC_ARCHIVE_BATCH_ACCEPTED_UNATTESTED",
                "windows_committed_this_call": 4, "source_failure": None,
                "g1_verified": False,
                "archive_summary": {"stored_1m_rows": 8640},
            },
            "final_decision": "REJECT", "execution_allowed": False,
            "live_trading_authorized": False,
        }
        fake = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(result), stderr="",
        )
        with patch("ops.m03l_paced_20day_once.subprocess.run",
                   return_value=fake) as runner:
            report = run_original_batch(
                db=Path("runtime/m03l_public_ohlc.sqlite3"),
                windows=4, expected_count=8640, env=SAFE_OWNER_ENV,
            )
        self.assertTrue(report["accepted"])
        self.assertEqual(report["http_calls_bounded_upper"], 12)
        args, kwargs = runner.call_args
        self.assertEqual(args[0][2:4], ["-m", "indodax_ai_bot"])
        self.assertEqual(
            args[0][4:],
            ["--public-history-archive-once", "--history-minutes", "360",
             "--history-windows", "4", "--pair", "btc_idr",
             "--db", "runtime/m03l_public_ohlc.sqlite3"],
        )
        self.assertEqual(kwargs["env"]["INDODAX_TRADING_ENABLED"], "NO")
        self.assertNotIn("--sumopod-shadow", args[0])

    def test_untrusted_success_claim_or_broker_permission_rejected(self):
        for mutation in (
            {"final_decision": "PASS"},
            {"execution_allowed": True},
            {"live_trading_authorized": True},
        ):
            data = {
                "public_history_archive": {
                    "status": "PUBLIC_ARCHIVE_BATCH_ACCEPTED_UNATTESTED",
                    "windows_committed_this_call": 4,
                    "archive_summary": {"stored_1m_rows": 8640},
                    "source_failure": None, "g1_verified": False,
                },
                "final_decision": "REJECT", "execution_allowed": False,
                "live_trading_authorized": False,
                **mutation,
            }
            with self.subTest(mutation=mutation), patch(
                "ops.m03l_paced_20day_once.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=0,
                    stdout=json.dumps(data), stderr="",
                ),
            ):
                r = run_original_batch(
                    db=Path("runtime/m03l_public_ohlc.sqlite3"),
                    windows=4, expected_count=8640, env=SAFE_OWNER_ENV,
                )
                self.assertFalse(r["accepted"])

    def test_go_missing_or_live_mode_blocks_before_quota_and_network(self):
        for changed in (
            {"M03L_OWNER_PACED_20D_GO": "NO"},
            {"INDODAX_TRADING_ENABLED": "YES"},
            {"INDODAX_ALLOW_LIVE_EXECUTION": "YES"},
            {"INDODAX_EXECUTION_MODE": "LIVE"},
            {"INDODAX_M03_REQUIRE_EGRESS_ADMISSION": "NO"},
        ):
            with self.subTest(changed=changed), patch.dict(
                    os.environ, {**SAFE_OWNER_ENV, **changed}, clear=True):
                with self.assertRaisesRegex(
                    M03LPacedDenied, "OWNER_GO_OR_NONLIVE_SAFETY_MISSING"):
                    run_paced(
                        count_rows=lambda: self.fail("NO_ARCHIVE_READ"),
                        headroom=lambda: self.fail("NO_QUOTA_READ"),
                        batch=lambda **kw: self.fail("NO_NETWORK"),
                    )

    def test_existing_sqlite_archive_continuity_and_gap_are_readonly(self):
        with TemporaryDirectory() as t:
            db = Path(t) / "archive.sqlite3"
            with sqlite3.connect(db) as con:
                con.execute(
                    "CREATE TABLE public_ohlc_1m("
                    "pair TEXT, start_epoch INTEGER, PRIMARY KEY(pair,start_epoch))"
                )
                first = 1800000000 - 1800000000 % 60
                con.executemany(
                    "INSERT INTO public_ohlc_1m VALUES ('btc_idr',?)",
                    ((first + i * 60,) for i in range(4320)),
                )
            self.assertEqual(existing_contiguous_rows(db), 4320)
            with sqlite3.connect(db) as con:
                con.execute(
                    "UPDATE public_ohlc_1m SET start_epoch=? "
                    "WHERE pair='btc_idr' AND start_epoch=?",
                    (first + 4321*60, first + 4319*60),
                )
            with self.assertRaisesRegex(
                    M03LPacedDenied,
                    "M03L_ARCHIVE_NOT_CONTIGUOUS_OR_BOUND_INVALID"):
                existing_contiguous_rows(db)

    def test_no_explicit_cli_go_never_touches_local_archive(self):
        with patch.dict(os.environ, SAFE_OWNER_ENV, clear=True):
            out = StringIO()
            with redirect_stdout(out):
                rc = main([])
        self.assertEqual(rc, 3)
        self.assertIn("M03L_EXPLICIT_OWNER_CLI_GO_REQUIRED",
                      out.getvalue())
        self.assertIn('"execution_allowed": false', out.getvalue())


if __name__ == "__main__":
    unittest.main()
