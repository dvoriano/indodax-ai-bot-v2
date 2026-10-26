"""M03-C: atomic/public deep rotation tests. No order/private API or model calls."""
from __future__ import annotations

import json
from contextlib import redirect_stdout
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionConflictError, DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.scanner import ScreenedPair, ScannerResult
from test_m03b_deep import observation


PAIRS = ("btc_idr", "eth_idr", "sol_idr", "xrp_idr", "inj_idr")


def candidates(epoch=1800000130):
    rows = tuple(ScreenedPair(p, Decimal("200000000"), Decimal("100"), Decimal("101"),
                             Decimal("100"), Decimal("10"), epoch - 1) for p in PAIRS)
    return ScannerResult(epoch, 398, 310, 88, rows, rows[:2])


class PublicEvidenceFixture:
    def __init__(self):
        self.seen = []

    def observe(self, pair):
        self.seen.append(pair)
        return observation(pair)


class M03CRotatingWindowTests(unittest.TestCase):
    def test_cursor_persists_restart_wraps_and_covers_five_pairs(self):
        with TemporaryDirectory() as temp:
            db = Path(temp) / "journal.sqlite3"
            observed = []
            for i in range(3):
                journal = DecisionJournal(db)  # restart with same DB
                window = journal.reserve_deep_window(
                    f"t-{i}", PAIRS, observed_epoch=1000 + 12*i
                )
                observed.extend(window.pair_ids)
            self.assertEqual(observed, list(PAIRS) + ["btc_idr"])
            last = DecisionJournal(db).reserve_deep_window(
                "t-3", PAIRS, observed_epoch=1036
            )
            self.assertEqual(last.pair_ids, ("eth_idr", "sol_idr"))
            self.assertEqual(last.starting_index, 1)
            self.assertEqual(last.next_index, 3)

    def test_replayed_trace_returns_same_batch_without_advancing(self):
        with TemporaryDirectory() as temp:
            journal = DecisionJournal(Path(temp) / "journal.sqlite3")
            w = journal.reserve_deep_window("same", PAIRS, observed_epoch=1000)
            same = journal.reserve_deep_window("same", PAIRS, observed_epoch=1000)
            self.assertEqual(same.pair_ids, w.pair_ids)
            self.assertTrue(same.replayed)
            with self.assertRaisesRegex(DecisionConflictError, "DEEP_TRACE_UNIVERSE_CHANGED"):
                journal.reserve_deep_window("same", PAIRS[::-1], observed_epoch=1000)
            second = journal.reserve_deep_window("next", PAIRS, observed_epoch=1012)
            self.assertEqual(second.pair_ids, ("sol_idr", "xrp_idr"))

    def test_minimum_spacing_uses_exchange_observed_epoch(self):
        with TemporaryDirectory() as temp:
            journal = DecisionJournal(Path(temp) / "journal.sqlite3")
            journal.reserve_deep_window("first", PAIRS, observed_epoch=1000)
            with self.assertRaisesRegex(ValueError, "DEEP_SCAN_COOLDOWN"):
                journal.reserve_deep_window("too-soon", PAIRS, observed_epoch=1011)
            with self.assertRaisesRegex(ValueError, "DEEP_SCAN_COOLDOWN"):
                journal.reserve_deep_window("backwards", PAIRS, observed_epoch=990)
            later = journal.reserve_deep_window("allowed", PAIRS, observed_epoch=1012)
            self.assertEqual(later.starting_index, 2)

    def test_concurrent_reservations_same_second_only_one_commits(self):
        with TemporaryDirectory() as temp:
            journal = DecisionJournal(Path(temp) / "journal.sqlite3")
            def reserve(i):
                try:
                    journal.reserve_deep_window(f"concurrent-{i}", PAIRS, observed_epoch=1000)
                    return "OK"
                except ValueError as exc:
                    return str(exc)
            with ThreadPoolExecutor(max_workers=2) as pool:
                outputs = list(pool.map(reserve, (1, 2)))
            self.assertEqual(outputs.count("OK"), 1)
            self.assertEqual(outputs.count("DEEP_SCAN_COOLDOWN"), 1)

    def test_invalid_budget_universe_or_epoch_fails_closed(self):
        with TemporaryDirectory() as temp:
            journal = DecisionJournal(Path(temp) / "journal.sqlite3")
            for universe in ((), ("btc_idr", "btc_idr"), ("btc_usdt",)):
                with self.assertRaises(ValueError):
                    journal.reserve_deep_window("bad", universe, observed_epoch=1000)
            with self.assertRaises(ValueError):
                journal.reserve_deep_window("bad", PAIRS, observed_epoch=True)
            with self.assertRaises(ValueError):
                journal.reserve_deep_window("bad", PAIRS, observed_epoch=1000, batch_size=3)

    def test_mainpath_rotates_two_pairs_not_stale_cross_cycle_ranking(self):
        with TemporaryDirectory() as temp:
            market = PublicEvidenceFixture()
            app = DailyScalpingOrchestrator(
                Settings.from_env({}), DecisionJournal(Path(temp) / "journal.sqlite3")
            )
            for i, wanted in enumerate((list(PAIRS[:2]), list(PAIRS[2:4]), ["inj_idr", "btc_idr"])):
                with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as discovery:
                    discovery.return_value.scan.return_value = candidates(1800000130 + i*12)
                    result = app.run_rolling_deep_cycle(f"cycle-{i}", market=market)
                self.assertEqual(result.final_decision.value, "REJECT")
                self.assertFalse(result.execution_allowed)
                self.assertEqual(result.rolling["reserved_pairs"], wanted)
                self.assertEqual(result.rolling["deep_examined_count"], 2)
                self.assertFalse(result.rolling["full_universe_deep_verified"])
                self.assertFalse(result.rolling["cross_cycle_ranking_allowed"])
                self.assertFalse(result.rolling["trade_authorized"])
                self.assertEqual(result.rolling["same_window"]["status"],
                                 "ALIGNED_PUBLIC_UNATTESTED")
                self.assertFalse(result.rolling["same_window"]["g2_candidate_authorized"])
            # When BTC is absent from the selected pair batch, a fresh BTC
            # observation is requested strictly for common-window context.
            self.assertEqual(market.seen,
                             ["btc_idr", "eth_idr", "sol_idr", "xrp_idr",
                              "btc_idr", "inj_idr", "btc_idr"])
            self.assertEqual(app.journal.count(), 3)

    def test_cli_rolling_public_keeps_live_disabled(self):
        with TemporaryDirectory() as temp:
            output = StringIO()
            with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket", return_value=PublicEvidenceFixture()):
                with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as discovery:
                    discovery.return_value.scan.return_value = candidates()
                    with redirect_stdout(output):
                        code = main([
                            "--rolling-deep-once", "--db", str(Path(temp) / "safe.sqlite3"),
                        ])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["rolling_probe_status"], "PUBLIC_ROLLING_UNATTESTED")
            self.assertEqual(result["final_decision"], "REJECT")
            self.assertFalse(result["execution_allowed"])
            self.assertFalse(result["live_trading_authorized"])


if __name__ == "__main__":
    unittest.main()
