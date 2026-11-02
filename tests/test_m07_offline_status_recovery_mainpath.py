"""Original V2 M07 untrusted status timeline (NO private HTTP, fills or LIVE).

All observations are explicitly synthetic. A FILLED fixture must never
unlock reservations, create an owned lot, or authorize another BUY/SELL.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal, DecisionConflictError
from indodax_ai_bot.m07_order_adapter import (
    M07OfflineStatusObservation, M07OrderDraft, OfficialV2OrderAdapter,
    M07Hardlocked,
)
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


def app(db: Path) -> DailyScalpingOrchestrator:
    return DailyScalpingOrchestrator(Settings(), DecisionJournal(db))


def buy(client_id: str, symbol: str = "BTCIDR") -> M07OrderDraft:
    return M07OrderDraft(
        symbol, "BUY", "MARKET", client_id, quote_order_qty=10000,
    )


def obs(intent: str, oid: str, state: str, base: str, quote: str,
        now: int, symbol: str = "BTCIDR") -> M07OfflineStatusObservation:
    return M07OfflineStatusObservation(
        intent_id=intent, client_order_id=intent, symbol=symbol,
        observation_id=oid, status=state,
        cumulative_base=Decimal(base),
        cumulative_quote=Decimal(quote), observed_epoch=now,
    )


def cli(args: list[str], now: int) -> tuple[int, dict]:
    stdout, stderr = StringIO(), StringIO()
    with patch("indodax_ai_bot.__main__.time.time", return_value=now):
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(args)
    return result, json.loads(stdout.getvalue() or stderr.getvalue())


class M07OriginalStatusTimelineTests(unittest.TestCase):
    def test_real_original_cli_reserve_status_replay_restart_no_execution(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "v2.sqlite3")
            ident = "cli-observation-1"
            rc, reserved = cli([
                "--m07-offline-intent-once", "--db", db,
                "--m07-intent-id", ident,
            ], 1793601000)
            self.assertEqual(rc, 0)
            self.assertEqual(reserved["m07_offline_intent"]["status"],
                             "OFFLINE_RESERVED")
            status_args = [
                "--m07-offline-status-once", "--db", db,
                "--m07-intent-id", ident,
                "--m07-observation-id", "source-event-1",
                "--m07-observed-status", "PARTIALLY_FILLED",
                "--m07-cumulative-base", "0.000001",
                "--m07-cumulative-quote", "1000",
            ]
            rc, first = cli(status_args, 1793601001)
            self.assertEqual(rc, 0)
            report = first["m07_offline_intent"]
            self.assertEqual(report["simulated_order_status"], "PARTIALLY_FILLED")
            self.assertEqual(report["intent_state"], "OFFLINE_RESERVED")
            self.assertFalse(report["exchange_order_state_authenticated"])
            self.assertFalse(report["bot_owned_fills_verified"])
            self.assertFalse(report["execution_allowed"])
            rc, again = cli(status_args, 1793601001)
            self.assertEqual(rc, 0)
            self.assertTrue(again["m07_offline_intent"]["replay"])
            self.assertFalse(again["m07_offline_intent"]["retry_permitted"])
            state = app(Path(db)).inspect_m07_offline_intent_guard_once()
            self.assertEqual(state["unresolved_simulated_intents"], 1)
            with self.assertRaises(M07Hardlocked):
                OfficialV2OrderAdapter().dispatch()

    def test_partial_to_filled_never_unlocks_or_credits_owned_quantity(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "journal.sqlite3"
            one = app(db)
            one.reserve_m07_offline_intent_once(
                intent_id="btc", draft=buy("btc"), observed_epoch=100,
            )
            part = one.observe_m07_offline_status_once(
                obs("btc", "part", "PARTIALLY_FILLED", "0.001", "1000", 101),
            )
            self.assertFalse(part["release_permitted"])
            filled = app(db).observe_m07_offline_status_once(
                obs("btc", "fill", "FILLED", "0.01", "10000", 102),
            )
            self.assertEqual(filled["simulated_order_status"], "FILLED")
            self.assertFalse(filled["bot_owned_fills_verified"])
            self.assertFalse(filled["release_permitted"])
            with self.assertRaisesRegex(DecisionConflictError,
                                        "M07_UNRESOLVED_PAIR_INTENT"):
                app(db).reserve_m07_offline_intent_once(
                    intent_id="other", draft=buy("other"),
                    observed_epoch=103,
                )
            with self.assertRaisesRegex(DecisionConflictError,
                                        "M07_OBSERVATION_TERMINAL_IS_IMMUTABLE"):
                app(db).observe_m07_offline_status_once(
                    obs("btc", "post-fill", "CANCELED",
                        "0.01", "10000", 103),
                )

    def test_unknown_then_cancel_and_fill_do_not_release_global_buy_hold(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "journal.sqlite3"
            a = app(db)
            a.reserve_m07_offline_intent_once(
                intent_id="btc", draft=buy("btc"), observed_epoch=100,
            )
            hold = a.observe_m07_offline_status_once(
                obs("btc", "timeout", "UNKNOWN", "0", "0", 101),
            )
            self.assertEqual(hold["intent_state"], "OFFLINE_UNKNOWN_HOLD")
            final = app(db).observe_m07_offline_status_once(
                obs("btc", "terminal", "CANCELED", "0", "0", 102),
            )
            self.assertEqual(final["intent_state"], "OFFLINE_UNKNOWN_HOLD")
            self.assertFalse(final["release_permitted"])
            with self.assertRaisesRegex(DecisionConflictError,
                                        "M07_UNKNOWN_BLOCKS_ALL_NEW_BUYS"):
                app(db).reserve_m07_offline_intent_once(
                    intent_id="eth", draft=buy("eth", "ETHIDR"),
                    observed_epoch=103,
                )
            summary = app(db).inspect_m07_offline_intent_guard_once()
            self.assertEqual(summary["offline_unknown_holds"], 1)

    def test_cumulative_regression_and_status_regression_fail_closed(self):
        with TemporaryDirectory() as tmp:
            a = app(Path(tmp) / "v2.sqlite3")
            a.reserve_m07_offline_intent_once(
                intent_id="btc", draft=buy("btc"), observed_epoch=100,
            )
            a.observe_m07_offline_status_once(
                obs("btc", "part", "PARTIALLY_FILLED", "0.1", "5000", 101),
            )
            for candidate, code in [
                (obs("btc", "smaller", "PARTIALLY_FILLED",
                     "0.09", "5001", 102), "M07_OBSERVATION_CUMULATIVE_REGRESSION"),
                (obs("btc", "wrong-quote", "PARTIALLY_FILLED",
                     "0.11", "4999", 102), "M07_OBSERVATION_CUMULATIVE_REGRESSION"),
                (obs("btc", "new", "NEW", "0", "0", 102),
                 "M07_OBSERVATION_CUMULATIVE_REGRESSION"),
                (obs("btc", "stale", "CANCELED", "0.1", "5000", 99),
                 "M07_OBSERVATION_STALE"),
            ]:
                with self.subTest(candidate=candidate.observation_id):
                    with self.assertRaisesRegex(DecisionConflictError, code):
                        a.observe_m07_offline_status_once(candidate)

    def test_cross_order_identity_duplicate_and_changed_payload_denied(self):
        with TemporaryDirectory() as tmp:
            a = app(Path(tmp) / "journal.sqlite3")
            a.reserve_m07_offline_intent_once(
                intent_id="btc", draft=buy("btc"), observed_epoch=100,
            )
            a.reserve_m07_offline_intent_once(
                intent_id="eth", draft=buy("eth", "ETHIDR"), observed_epoch=100,
            )
            a.observe_m07_offline_status_once(
                obs("btc", "event", "NEW", "0", "0", 101),
            )
            with self.assertRaisesRegex(DecisionConflictError,
                                        "M07_OBSERVATION_ORDER_MISMATCH"):
                a.observe_m07_offline_status_once(
                    obs("btc", "foreign", "NEW", "0", "0", 102, "ETHIDR"),
                )
            with self.assertRaisesRegex(DecisionConflictError,
                                        "M07_OBSERVATION_REPLAY_CONFLICT"):
                a.observe_m07_offline_status_once(
                    obs("btc", "event", "CANCELED", "0", "0", 103),
                )
            with self.assertRaisesRegex(DecisionConflictError,
                                        "M07_OBSERVATION_REPLAY_CONFLICT"):
                a.observe_m07_offline_status_once(
                    obs("eth", "event", "NEW", "0", "0", 103, "ETHIDR"),
                )

    def test_concurrent_identical_event_atomic_single_record(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "v2.sqlite3"
            a = app(db)
            a.reserve_m07_offline_intent_once(
                intent_id="btc", draft=buy("btc"), observed_epoch=100,
            )
            event = obs("btc", "same-event", "PARTIALLY_FILLED",
                        "0.001", "100", 101)
            with ThreadPoolExecutor(max_workers=8) as pool:
                responses = list(pool.map(
                    lambda _: a.observe_m07_offline_status_once(event),
                    range(8),
                ))
            self.assertEqual(sum(not x["replay"] for x in responses), 1)
            self.assertEqual(sum(x["replay"] for x in responses), 7)
            self.assertTrue(all(not x["execution_allowed"] for x in responses))

    def test_invalid_cumulative_and_cli_args_refused_before_journal(self):
        for status, base, quote in [
            ("PARTIALLY_FILLED", "0", "0"),
            ("FILLED", "0.1", "0"),
            ("NEW", "0.01", "1"),
            ("REJECTED", "0.01", "1"),
            ("UNKNOWN", "-0.1", "0"),
            ("FILLED", "NaN", "1"),
            ("UNKNOWN", "1e-99999", "0"),
            ("UNKNOWN", "1." + "0" * 100, "0"),
        ]:
            with self.subTest(status=status, base=base):
                with self.assertRaises(ValueError):
                    obs("btc", "x", status, base, quote, 100).validated_record()
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp)/"invalid.sqlite3")
            cases = [
                ["--m07-offline-status-once", "--db", db,
                 "--m07-intent-id", "btc"],
                ["--m07-offline-status-once", "--db", db,
                 "--m07-intent-id", "btc",
                 "--m07-observation-id", "event",
                 "--m07-observed-status", "FILLED",
                 "--m07-cumulative-base", "-1",
                 "--m07-cumulative-quote", "1"],
                ["--m07-offline-intent-once", "--db", db,
                 "--m07-intent-id", "btc",
                 "--m07-observation-id", "event"],
            ]
            for args in cases:
                rc, result = cli(args, 100)
                self.assertEqual(rc, 2, result)
            self.assertFalse(Path(db).exists())


if __name__ == "__main__":
    unittest.main()
