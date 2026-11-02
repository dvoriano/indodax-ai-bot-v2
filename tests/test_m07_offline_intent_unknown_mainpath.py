"""M07 original single journal: durable synthetic reserve, replay and UNKNOWN.

Zero exchange requests, zero private credentials, zero live orders. Tests do
not manufacture source G1 receipts, owner PASS, exchange fills or balances.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal, DecisionConflictError
from indodax_ai_bot.m07_order_adapter import M07OrderDraft, OfficialV2OrderAdapter, M07Hardlocked
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator

NOW = 1793600000


def buy(intent_id: str, symbol: str = "BTCIDR", amount: int = 10000) -> M07OrderDraft:
    return M07OrderDraft(
        symbol, "BUY", "MARKET", intent_id, quote_order_qty=amount,
    )


def app(path: Path) -> DailyScalpingOrchestrator:
    return DailyScalpingOrchestrator(Settings(), DecisionJournal(path))


class M07OriginalOfflineIntentUnknownTests(unittest.TestCase):
    def test_cli_replay_restart_original_same_db_never_creates_new_order(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "original-v2-journal.sqlite3")
            args = [
                "--m07-offline-intent-once", "--db", db,
                "--m07-intent-id", "v2-synthetic-reserve-1",
            ]
            results = []
            for _ in range(2):
                out, err = StringIO(), StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    rc = main(args)
                self.assertEqual(rc, 0, err.getvalue())
                results.append(json.loads(out.getvalue()))
            first, replay = [x["m07_offline_intent"] for x in results]
            self.assertEqual(first["status"], "OFFLINE_RESERVED")
            self.assertTrue(first["newly_reserved"])
            self.assertFalse(first["replay"])
            self.assertEqual(replay["status"], "OFFLINE_RESERVED")
            self.assertTrue(replay["replay"])
            self.assertFalse(replay["newly_reserved"])
            self.assertFalse(first["order_sent"])
            self.assertFalse(replay["order_sent"])
            self.assertFalse(results[1]["execution_allowed"])
            self.assertFalse(results[1]["live_trading_authorized"])
            status = app(Path(db)).inspect_m07_offline_intent_guard_once()
            self.assertEqual(status["unresolved_simulated_intents"], 1)
            self.assertEqual(status["offline_unknown_holds"], 0)

    def test_original_cli_unknown_lock_persists_and_refuses_new_intent(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "v2.sqlite3")
            def cli(flag: str, intent: str):
                stdout, stderr = StringIO(), StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    rc = main([
                        flag, "--db", db, "--m07-intent-id", intent,
                    ])
                return rc, (stdout.getvalue() or stderr.getvalue())
            rc, _ = cli("--m07-offline-intent-once", "v2-unknown-1")
            self.assertEqual(rc, 0)
            rc, text = cli("--m07-offline-unknown-once", "v2-unknown-1")
            self.assertEqual(rc, 0)
            hold = json.loads(text)["m07_offline_intent"]
            self.assertEqual(hold["status"], "OFFLINE_UNKNOWN_HOLD")
            self.assertTrue(hold["release_requires_authenticated_exchange_reconciliation"])
            self.assertFalse(hold["order_sent"])
            # Original CLI restarted; neither same ID nor changed ID is a
            # second submit. No fixed TTL or artificial FAILED/CANCELLED.
            rc, text = cli("--m07-offline-intent-once", "v2-unknown-1")
            self.assertEqual(rc, 0)
            prior = json.loads(text)["m07_offline_intent"]
            self.assertTrue(prior["replay"])
            self.assertFalse(prior["newly_reserved"])
            self.assertEqual(prior["status"], "OFFLINE_UNKNOWN_HOLD")
            rc, text = cli("--m07-offline-intent-once", "v2-unknown-2")
            self.assertEqual(rc, 3)
            self.assertIn("M07_UNKNOWN_BLOCKS_ALL_NEW_BUYS", text)
            guard = app(Path(db)).inspect_m07_offline_intent_guard_once()
            self.assertEqual(guard["unresolved_simulated_intents"], 1)
            self.assertEqual(guard["offline_unknown_holds"], 1)
            self.assertTrue(guard["additional_buy_reservations_blocked"])
            self.assertFalse(guard["exchange_order_state_authenticated"])

    def test_two_slots_pair_uniqueness_and_block_extra_across_restarts(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "v2.sqlite3"
            one = app(db)
            first = one.reserve_m07_offline_intent_once(
                intent_id="btc-first", draft=buy("btc-first"),
                observed_epoch=NOW,
            )
            self.assertTrue(first["newly_reserved"])
            with self.assertRaisesRegex(DecisionConflictError, "M07_UNRESOLVED_PAIR_INTENT"):
                app(db).reserve_m07_offline_intent_once(
                    intent_id="btc-second", draft=buy("btc-second"),
                    observed_epoch=NOW+1,
                )
            second = app(db).reserve_m07_offline_intent_once(
                intent_id="eth-first",
                draft=buy("eth-first", symbol="ETHIDR"),
                observed_epoch=NOW+2,
            )
            self.assertTrue(second["newly_reserved"])
            with self.assertRaisesRegex(DecisionConflictError, "M07_MAX_TWO_UNRESOLVED_INTENTS"):
                app(db).reserve_m07_offline_intent_once(
                    intent_id="sol-third",
                    draft=buy("sol-third", symbol="SOLIDR"),
                    observed_epoch=NOW+3,
                )
            self.assertEqual(app(db).inspect_m07_offline_intent_guard_once()[
                "unresolved_simulated_intents"], 2)

    def test_same_intent_mutation_and_sell_without_ownership_are_denied(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp)/"v2.sqlite3"
            a = app(db)
            a.reserve_m07_offline_intent_once(
                intent_id="same-key", draft=buy("same-key"), observed_epoch=NOW,
            )
            with self.assertRaisesRegex(DecisionConflictError, "M07_INTENT_REPLAY_CONFLICT"):
                a.reserve_m07_offline_intent_once(
                    intent_id="same-key", draft=buy("same-key", amount=11000),
                    observed_epoch=NOW+1,
                )
            with self.assertRaisesRegex(DecisionConflictError, "M07_INTENT_CLIENT_ID_MISMATCH"):
                a.reserve_m07_offline_intent_once(
                    intent_id="different-key", draft=buy("same-key"),
                    observed_epoch=NOW+2,
                )
            with self.assertRaisesRegex(DecisionConflictError, "M07_OFFLINE_SELL_OWNERSHIP_UNKNOWN"):
                a.reserve_m07_offline_intent_once(
                    intent_id="foreign-sell",
                    draft=M07OrderDraft(
                        "ETHIDR", "SELL", "MARKET", "foreign-sell",
                        quantity=Decimal("0.1"),
                    ),
                    observed_epoch=NOW+3,
                )
            self.assertEqual(app(db).inspect_m07_offline_intent_guard_once()[
                "unresolved_simulated_intents"], 1)

    def test_unknown_timestamp_monotonic_and_unreserved_denied(self):
        with TemporaryDirectory() as tmp:
            a = app(Path(tmp)/"v2.sqlite3")
            with self.assertRaisesRegex(DecisionConflictError, "M07_UNKNOWN_INTENT_NOT_RESERVED"):
                a.simulate_m07_unknown_timeout_once(
                    intent_id="none", observed_epoch=NOW,
                )
            a.reserve_m07_offline_intent_once(
                intent_id="btc", draft=buy("btc"),
                observed_epoch=NOW,
            )
            with self.assertRaisesRegex(DecisionConflictError, "M07_UNKNOWN_EPOCH_BEFORE_RESERVE"):
                a.simulate_m07_unknown_timeout_once(
                    intent_id="btc", observed_epoch=NOW-1,
                )
            a.simulate_m07_unknown_timeout_once(
                intent_id="btc", observed_epoch=NOW+2,
            )
            with self.assertRaisesRegex(DecisionConflictError, "M07_UNKNOWN_EPOCH_REGRESSION"):
                a.simulate_m07_unknown_timeout_once(
                    intent_id="btc", observed_epoch=NOW+1,
                )
            self.assertEqual(a.simulate_m07_unknown_timeout_once(
                intent_id="btc", observed_epoch=NOW+3,
            )["status"], "OFFLINE_UNKNOWN_HOLD")

    def test_concurrent_same_id_only_one_new_reservation_sqlite_transaction(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp)/"v2.sqlite3"
            a = app(db)
            def attempt(_):
                return a.reserve_m07_offline_intent_once(
                    intent_id="concurrent-1", draft=buy("concurrent-1"),
                    observed_epoch=NOW,
                )
            with ThreadPoolExecutor(max_workers=8) as pool:
                states = list(pool.map(attempt, range(8)))
            self.assertEqual(sum(x["newly_reserved"] for x in states), 1)
            self.assertEqual(sum(x["replay"] for x in states), 7)
            self.assertEqual(app(db).inspect_m07_offline_intent_guard_once()[
                "unresolved_simulated_intents"], 1)
            self.assertTrue(all(not x["order_sent"] and not x["execution_allowed"]
                                for x in states))

    def test_synthetic_cli_rejects_implicit_default_db_or_incorrect_mode(self):
        with TemporaryDirectory() as tmp:
            paths = [
                ["--m07-offline-intent-once", "--m07-intent-id", "x"],
                ["--m07-offline-intent-once", "--db", "relative.db",
                 "--m07-intent-id", "x"],
                ["--m07-offline-intent-once", "--db",
                 "/var/tmp/m07-synthetic-must-not-create.sqlite3",
                 "--m07-intent-id", "x"],
                ["--health", "--m07-intent-id", "x", "--db",
                 str(Path(tmp)/"v2.sqlite3")],
            ]
            for args in paths:
                with self.subTest(args=args):
                    out, err = StringIO(), StringIO()
                    with redirect_stdout(out), redirect_stderr(err):
                        code = main(args)
                    self.assertEqual(code, 2)
                    self.assertEqual(out.getvalue(), "")
            self.assertFalse((Path(tmp)/"v2.sqlite3").exists())
            with self.assertRaises(M07Hardlocked):
                OfficialV2OrderAdapter().create_order()

if __name__ == "__main__":
    unittest.main()
