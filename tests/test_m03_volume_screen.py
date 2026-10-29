"""M03 volume-floor tests: all-pair radar stays; underfloor deep excluded.

These are unit/source-fixture tests only, NOT a real 40-pair exchange proof.
"""
from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from indodax_ai_bot.__main__ import main as cli_main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.public_market import PublicDataError
from indodax_ai_bot.scanner import DailyPairScanner
from indodax_ai_bot.volume_screening import (
    apply_deep_volume_floor, public_volume_distribution,
)
from ops.inspect_m03_volume_once import main as probe_main
from test_m03a_scanner import FakeIndodaxAll


def boosted_fake(*, high=20, amount="150000000"):
    fake = FakeIndodaxAll()
    for n in range(45):
        fake.tickers[f"m{n:03d}_idr"]["vol_idr"] = (
            amount if n < high else "45000000"
        )
    return fake


class M03VolumeFloorTests(unittest.TestCase):
    def test_report_counts_real_scanner_universe_from_only_three_public_gets(self):
        fake = boosted_fake(high=21)
        scanner = DailyPairScanner(fake.client()).scan()
        report = public_volume_distribution(scanner)
        self.assertEqual(report["all_idr_pairs"], 45)
        self.assertEqual(report["eligible_initial_scanner_pairs"], 45)
        self.assertEqual(report["at_or_above_volume_idr_counts"], {
            "50000000": 21, "100000000": 21,
            "250000000": 0, "500000000": 0,
        })
        self.assertEqual(report["below_100m_but_still_in_radar"], 24)
        self.assertTrue(report["at_least_20_deep_candidates_by_volume_only"])
        self.assertFalse(report["at_least_40_deep_candidates_by_volume_only"])
        self.assertFalse(report["execution_allowed"])
        self.assertEqual(fake.calls, [
            "/api/pairs", "/api/server_time", "/api/ticker_all",
        ])

    def test_100m_floor_only_changes_deep_not_all_pair_radar(self):
        fake = boosted_fake(high=20)
        scan = DailyPairScanner(fake.client()).scan()
        deep = apply_deep_volume_floor(scan)
        self.assertEqual(len(scan.universe), 45)
        self.assertEqual(len(deep.universe), 45)
        self.assertEqual(len(deep.shortlist), 20)
        self.assertTrue(all(x.volume_idr_24h >= Decimal("100000000")
                            for x in deep.shortlist))
        self.assertIn("m044_idr", {x.pair for x in deep.universe})
        self.assertNotIn("m044_idr", {x.pair for x in deep.shortlist})
        self.assertEqual([x.pair for x in deep.watchlist],
                         ["m000_idr", "m001_idr"])
        self.assertEqual(fake.calls, [
            "/api/pairs", "/api/server_time", "/api/ticker_all",
        ])

    def test_momentum_ranked_below_floor_is_not_deep_eligible(self):
        fake = boosted_fake(high=20)
        scan = DailyPairScanner(fake.client()).scan()
        below = next(x for x in scan.universe if x.pair == "m044_idr")
        prioritized = replace(scan, universe=(
            below, *(x for x in scan.universe if x.pair != below.pair)
        ))
        deep = apply_deep_volume_floor(prioritized)
        self.assertNotIn("m044_idr", {x.pair for x in deep.shortlist})
        self.assertEqual(deep.shortlist[0].pair, "m000_idr")

    def test_40_capacity_from_all_ranked_universe_not_30_initial_shortlist(self):
        fake = boosted_fake(high=45, amount="500000000")
        scan = DailyPairScanner(fake.client()).scan()
        self.assertEqual(len(scan.shortlist), 30)
        selected = apply_deep_volume_floor(scan, max_deep_pairs=40)
        self.assertEqual(len(selected.shortlist), 40)
        self.assertEqual(len(selected.universe), 45)
        self.assertEqual(selected.shortlist[-1].pair, "m039_idr")

    def test_less_than_20_above_floor_fails_closed_before_deep_network(self):
        fake = boosted_fake(high=19)
        scan = DailyPairScanner(fake.client()).scan()
        with TemporaryDirectory() as td:
            app = DailyScalpingOrchestrator(
                Settings.from_env({}), DecisionJournal(Path(td) / "journal.sqlite3")
            )
            with patch.object(app, "_scan_with_early_momentum", return_value=scan):
                result = app.run_bulk_deep_cycle(
                    "volume-insufficient", market=fake.client(),
                    min_volume_idr=Decimal("100000000"),
                )
            self.assertEqual(result.final_decision.value, "REJECT")
            self.assertFalse(result.execution_allowed)
            self.assertIsNone(result.bulk)
            self.assertIn("BULK_SHORTLIST_BELOW_REQUESTED_LIMIT", result.reason)
            self.assertEqual(app.journal.count(), 1)

    def test_invalid_floor_and_limit_fail_closed(self):
        scan = DailyPairScanner(boosted_fake().client()).scan()
        for floor in (Decimal("-1"), Decimal("NaN"), Decimal("Infinity"), "100000000"):
            with self.subTest(floor=floor):
                with self.assertRaisesRegex(PublicDataError, "INVALID_DEEP_VOLUME_FLOOR"):
                    apply_deep_volume_floor(scan, minimum_idr=floor)
        for limit in (19, 41, True):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(PublicDataError, "INVALID_DEEP_PAIR_LIMIT"):
                    apply_deep_volume_floor(scan, max_deep_pairs=limit)

    def test_probe_only_calls_original_public_scanner_no_journal(self):
        fake = boosted_fake(high=23)
        market = fake.client()
        out = StringIO()
        with patch("ops.inspect_m03_volume_once.IndodaxPublicMarket",
                   return_value=market):
            with redirect_stdout(out):
                rc = probe_main()
        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["public_get_attempt_count_this_client"], 3)
        self.assertEqual(payload["at_or_above_volume_idr_counts"]["100000000"], 23)
        self.assertFalse(payload["g1_verified"])
        self.assertFalse(payload["execution_allowed"])

    def test_cli_volume_floor_must_be_bulk_only_and_valid(self):
        for args in (
            ["--scan-once", "--bulk-min-volume-idr", "100000000"],
            ["--bulk-deep-once", "--bulk-min-volume-idr", "-1"],
            ["--bulk-deep-once", "--bulk-min-volume-idr", "NaN"],
            ["--bulk-deep-once", "--bulk-min-volume-idr", "oops"],
        ):
            with self.subTest(args=args):
                with TemporaryDirectory() as td:
                    err = StringIO()
                    with redirect_stderr(err):
                        rc = cli_main(args + ["--db", str(Path(td) / "journal.sqlite3")])
                    self.assertEqual(rc, 2)
                    self.assertFalse(json.loads(err.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
