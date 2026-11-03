"""M10-05: one SQLite quota cap for ALL cooperating V2 public REST clients.

No HTTP, services, outside projects, private exchange account, or live orders.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import sqlite3
import subprocess
import sys
import unittest

from indodax_ai_bot.public_rest_budget import (
    SharedPublicRestBudget, SharedRestBudgetError,
)


class V2SharedEgressCapTests(unittest.TestCase):
    def test_lower_cap_is_shared_by_another_v2_client_without_override(self):
        with TemporaryDirectory() as td:
            db = Path(td) / "v2-only.sqlite3"
            first = SharedPublicRestBudget(db, clock=lambda: 1800000000.0)
            second = SharedPublicRestBudget(db, clock=lambda: 1800000001.0)
            self.assertEqual(first.reserve(limit_override=3), 1)
            self.assertEqual(second.reserve(), 2)
            self.assertEqual(second.reserve(), 3)
            with self.assertRaisesRegex(
                SharedRestBudgetError, "PUBLIC_SHARED_REST_RATE_BUDGET_EXCEEDED"
            ):
                second.reserve()
            with sqlite3.connect(db) as con:
                self.assertEqual(con.execute(
                    "SELECT max_local_requests_60s "
                    "FROM rest_shared_egress_cap WHERE id=1"
                ).fetchone(), (3,))
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM rest_get_reservations"
                ).fetchone(), (3,))

    def test_lower_cap_commits_even_when_too_many_requests_already_in_window(self):
        with TemporaryDirectory() as td:
            db = Path(td) / "v2.sqlite3"
            guard = SharedPublicRestBudget(db, clock=lambda: 1800000000.0)
            for n in range(5):
                self.assertEqual(guard.reserve(), n + 1)
            # A new independent policy lowers capacity below existing usage.
            with self.assertRaisesRegex(
                SharedRestBudgetError, "PUBLIC_SHARED_REST_RATE_BUDGET_EXCEEDED"
            ):
                guard.reserve(limit_override=2)
            with sqlite3.connect(db) as con:
                self.assertEqual(con.execute(
                    "SELECT max_local_requests_60s "
                    "FROM rest_shared_egress_cap"
                ).fetchone(), (2,))
            with self.assertRaises(SharedRestBudgetError):
                guard.reserve()
            # The stale requests age out, but peer cap never rises by itself.
            later = SharedPublicRestBudget(db, clock=lambda: 1800000061.0)
            self.assertEqual(later.reserve(), 1)
            self.assertEqual(later.reserve(), 2)
            with self.assertRaises(SharedRestBudgetError):
                later.reserve()

    def test_new_policy_larger_cap_cannot_elevate_existing_v2_cap(self):
        with TemporaryDirectory() as td:
            db = Path(td) / "v2.sqlite3"
            guard = SharedPublicRestBudget(db, clock=lambda: 1800000000.0)
            self.assertEqual(guard.reserve(limit_override=2), 1)
            self.assertEqual(guard.reserve(limit_override=168), 2)
            with self.assertRaisesRegex(
                SharedRestBudgetError, "PUBLIC_SHARED_REST_RATE_BUDGET_EXCEEDED"
            ):
                guard.reserve(limit_override=50)

    def test_all_v2_process_instances_race_on_same_global_sticky_cap(self):
        with TemporaryDirectory() as td:
            db = Path(td) / "v2.sqlite3"
            SharedPublicRestBudget(db, clock=lambda: 1800000000.0).reserve(
                limit_override=4
            )
            guards = [
                SharedPublicRestBudget(db, clock=lambda: 1800000001.0)
                for _ in range(8)
            ]
            def reserve(i):
                try:
                    guards[i % len(guards)].reserve()
                    return "ALLOW"
                except SharedRestBudgetError as exc:
                    return str(exc)
            with ThreadPoolExecutor(max_workers=8) as pool:
                outcomes = list(pool.map(reserve, range(30)))
            self.assertEqual(outcomes.count("ALLOW"), 3)
            self.assertEqual(
                outcomes.count("PUBLIC_SHARED_REST_RATE_BUDGET_EXCEEDED"),
                27,
            )

    def test_second_real_v2_python_process_without_policy_cannot_bypass(self):
        with TemporaryDirectory() as td:
            db = Path(td) / "v2.sqlite3"
            guard = SharedPublicRestBudget(db, clock=lambda: 1800000000.0)
            self.assertEqual(guard.reserve(limit_override=1), 1)
            script = (
                "from indodax_ai_bot.public_rest_budget import "
                "SharedPublicRestBudget,SharedRestBudgetError; "
                "import sys; "
                "guard=SharedPublicRestBudget(sys.argv[1],"
                "clock=lambda:1800000001.0); "
                "\ntry:\n guard.reserve(); sys.exit(3)\n"
                "except SharedRestBudgetError as e:\n"
                " print(str(e)); sys.exit(0)\n"
            )
            # Keep a NEW interpreter without an admission-policy environment.
            env = dict(os.environ)
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
            result = subprocess.run(
                [sys.executable, "-B", "-c", script, str(db)],
                capture_output=True, text=True, env=env, check=False,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.strip(),
                "PUBLIC_SHARED_REST_RATE_BUDGET_EXCEEDED",
            )

    def test_invalid_overrides_denied_before_db_creation(self):
        with TemporaryDirectory() as td:
            db = Path(td) / "never.sqlite3"
            guard = SharedPublicRestBudget(db)
            for override in (0, 169, True, -1, "4", 2.0):
                with self.subTest(override=override):
                    with self.assertRaisesRegex(
                        SharedRestBudgetError, "INVALID_PUBLIC_SHARED_RATE_LIMIT"
                    ):
                        guard.reserve(limit_override=override)
            self.assertFalse(db.exists())


if __name__ == "__main__":
    unittest.main()
