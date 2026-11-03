"""M03 shared REST reservation and opt-in 40-pair original mainpath tests.

Entirely synthetic. No Indodax network, private account, paid AI, or orders.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import subprocess
import sys
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.public_market import IndodaxPublicMarket, PublicDataError
from indodax_ai_bot.public_rest_budget import (
    SharedPublicRestBudget, SharedRestBudgetError,
)
from indodax_ai_bot.scanner import DailyPairScanner
from test_m03_volume_screen import boosted_fake
from test_m03g_bulk import FakeBulkMarket
from unittest.mock import patch


class M03SharedRestAndFortyTests(unittest.TestCase):
    def test_shared_process_instances_fail_closed_under_concurrent_reservations(self):
        with TemporaryDirectory() as td:
            db = Path(td) / "rate.sqlite3"
            guards = [SharedPublicRestBudget(db, limit=10, clock=lambda: 1800000000.0)
                      for _ in range(4)]
            def call(i):
                try:
                    guards[i % 4].reserve()
                    return "ALLOW"
                except SharedRestBudgetError as exc:
                    return str(exc)
            with ThreadPoolExecutor(max_workers=8) as pool:
                responses = list(pool.map(call, range(20)))
            self.assertEqual(responses.count("ALLOW"), 10)
            self.assertEqual(
                responses.count("PUBLIC_SHARED_REST_RATE_BUDGET_EXCEEDED"), 10,
            )
            with self.assertRaisesRegex(
                SharedRestBudgetError, "PUBLIC_SHARED_REST_RATE_BUDGET_EXCEEDED"
            ):
                SharedPublicRestBudget(db, limit=10, clock=lambda: 1800000001.0).reserve()
            self.assertEqual(
                SharedPublicRestBudget(db, limit=10, clock=lambda: 1800000061.0).reserve(),
                1,
            )

    def test_second_real_python_process_shares_the_same_quota_file(self):
        with TemporaryDirectory() as td:
            db = Path(td) / "shared.sqlite3"
            SharedPublicRestBudget(db, limit=1, clock=lambda: 1800000000.0).reserve()
            code = (
                "from indodax_ai_bot.public_rest_budget import "
                "SharedPublicRestBudget,SharedRestBudgetError; "
                "import sys; "
                "guard=SharedPublicRestBudget(sys.argv[1], limit=1, "
                "clock=lambda:1800000001.0); "
                "\ntry:\n guard.reserve(); sys.exit(3)\n"
                "except SharedRestBudgetError as e:\n"
                " print(str(e)); sys.exit(0)\n"
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
            other = subprocess.run(
                [sys.executable, "-B", "-c", code, str(db)],
                env=env, capture_output=True, text=True, timeout=10, check=False,
            )
            self.assertEqual(other.returncode, 0, other.stderr)
            self.assertEqual(other.stdout.strip(),
                             "PUBLIC_SHARED_REST_RATE_BUDGET_EXCEEDED")

    def test_default_http_transport_constructs_shared_guard(self):
        self.assertIsInstance(
            IndodaxPublicMarket()._rest_budget, SharedPublicRestBudget,
        )
        self.assertIsNone(IndodaxPublicMarket(fetch=lambda _p: {})._rest_budget)

    def test_failed_transport_reservation_is_not_refunded(self):
        with TemporaryDirectory() as td:
            guard = SharedPublicRestBudget(Path(td) / "quota.sqlite3",
                                           limit=1, clock=lambda: 1800000000.0)
            def failed(_path):
                raise TimeoutError("simulated transport timeout")
            market = IndodaxPublicMarket(
                fetch=failed, clock=lambda: 1800000000.0, rest_budget=guard,
            )
            with self.assertRaisesRegex(PublicDataError, "PUBLIC_REQUEST_FAILED"):
                market._get("/api/pairs")
            with self.assertRaisesRegex(
                PublicDataError, "PUBLIC_SHARED_REST_RATE_BUDGET_EXCEEDED"
            ):
                market._get("/api/pairs")
            self.assertEqual(market.public_get_attempt_count, 2)

    def test_unavailable_db_and_invalid_budget_fail_closed(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "not-a-directory"
            path.write_text("blocked")
            with self.assertRaisesRegex(
                SharedRestBudgetError, "PUBLIC_SHARED_REST_RATE_DB_UNAVAILABLE"
            ):
                SharedPublicRestBudget(path / "quota.sqlite3").reserve()
            for bad in (0, 169, True, -1):
                with self.subTest(bad=bad):
                    with self.assertRaisesRegex(
                        SharedRestBudgetError, "INVALID_PUBLIC_SHARED_RATE_LIMIT"
                    ):
                        SharedPublicRestBudget(Path(td) / "test.sqlite3", limit=bad)

    def test_40_pair_opt_in_uses_existing_45_pair_universe_and_no_order(self):
        fake = boosted_fake(high=45, amount="500000000")
        market = FakeBulkMarket()
        # Keep the original 30-second source gate strict. Earlier fixture
        # accidentally mixed scanner epoch 1800000065 with market epoch
        # 1800000130 (65s apart), correctly aborting before pair one.
        fake.now = market.now
        for ticker in fake.tickers.values():
            ticker["server_time"] = fake.now - 1
        screened = DailyPairScanner(fake.client()).scan()
        self.assertEqual(screened.observed_epoch, market.now)
        self.assertEqual(len(screened.shortlist), 30)
        with TemporaryDirectory() as td:
            app = DailyScalpingOrchestrator(
                Settings.from_env({}),
                DecisionJournal(Path(td) / "journal.sqlite3"),
            )
            with (patch.object(app, "_scan_with_early_momentum", return_value=screened),
                  patch.object(app, "_attach_public_wall_experience",
                               side_effect=lambda candidate, obs: candidate)):
                result = app.run_bulk_deep_cycle(
                    "forty-public-only", market=market,
                    min_volume_idr=Decimal("150000000"), deep_limit=40,
                )
            self.assertIsNone(result.final_decision)
            self.assertFalse(result.execution_allowed)
            self.assertIsNotNone(result.bulk)
            self.assertEqual(result.bulk["attempted_count"], 40)
            self.assertEqual(result.bulk["analyzed_count"], 40)
            self.assertEqual(result.bulk["estimated_requests_including_discovery"], 167)
            self.assertEqual(len(market.calls), 41)
            self.assertFalse(result.bulk["g1_verified"])
            self.assertFalse(result.bulk["execution_allowed"])

    def test_cli_rejects_invalid_limits_without_market_network(self):
        invalid = (
            ["--bulk-deep-once", "--bulk-deep-limit", "19"],
            ["--bulk-deep-once", "--bulk-deep-limit", "41"],
            ["--scan-once", "--bulk-deep-limit", "40"],
        )
        with TemporaryDirectory() as td:
            for args in invalid:
                with self.subTest(args=args):
                    stderr = StringIO()
                    with redirect_stderr(stderr):
                        code = main(args + ["--db", str(Path(td) / "safe.sqlite3")])
                    self.assertEqual(code, 2)
                    self.assertFalse(json.loads(stderr.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
