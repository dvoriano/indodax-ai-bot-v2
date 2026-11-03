"""M07-04 original V2: atomic READ ONLY exchange/status shadow recovery.

All exchange responses are mocked at the original HTTPS boundary.
No BUY/SELL/CANCEL, no paid provider, and no live credential are used.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionConflictError, DecisionJournal
from indodax_ai_bot.m07_order_adapter import M07OrderDraft
from indodax_ai_bot.m07_private_order_readonly import M07ReadOnlyOrder
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


EPOCH = 1793691000
MS = EPOCH * 1000
ACCOUNT_A = "a" * 64
ACCOUNT_B = "b" * 64
CLIENT = "m07shadowonce001"


def draft(client=CLIENT):
    return M07OrderDraft(
        symbol="XRPIDR", side="BUY", order_type="LIMIT",
        client_order_id=client, price=Decimal("25000"),
        quantity=Decimal("4"),
    )


def order(*, status="NEW", executed="0", client=CLIENT,
          symbol="XRPIDR", qty="4", side="BUY", typ="LIMIT",
          exchange_id=7788, time_ms=MS):
    return M07ReadOnlyOrder(
        symbol=symbol, client_order_id=client,
        order_id=exchange_id, full_order_id="ORDER_7788",
        side=side, order_type=typ, status=status,
        original_qty=Decimal(qty), executed_qty=Decimal(executed),
        created_ms=time_ms,
    )


class OriginalM07ReadOnlyRecoveryShadow(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Path(temporary.name) / "original-v2.db"
        self.journal = DecisionJournal(self.db)
        self.bot = DailyScalpingOrchestrator(Settings(), self.journal)
        self.bot.reserve_m07_offline_intent_once(
            intent_id=CLIENT, draft=draft(), observed_epoch=EPOCH,
        )

    def record(self, value, *, ms=MS + 1000, account=ACCOUNT_A,
               journal=None):
        return (journal or self.journal).record_m07_authenticated_readonly_order_status(
            account_fingerprint=account, order=value, observed_ms=ms,
        )

    def shadow(self):
        with sqlite3.connect(self.db) as con:
            return con.execute(
                "SELECT account_fingerprint,order_status,executed_qty,"
                "observed_ms FROM m07_readonly_intent_recovery_shadow "
                "WHERE intent_id=?", (CLIENT,),
            ).fetchone()

    def test_original_reader_reuses_same_journal_and_cannot_release(self):
        raw = {
            "symbol": "XRPIDR", "clientOrderId": CLIENT,
            "orderId": 7788, "fullOrderId": "ORDER_7788",
            "side": "BUY", "type": "LIMIT", "status": "PARTIALLY_FILLED",
            "origQty": "4", "executedQty": "1", "time": MS,
        }
        with (patch(
            "indodax_ai_bot.m07_private_order_readonly._verify_view_only_uid",
            return_value=123456,
        ) as uid,
            patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                  return_value=raw) as http):
            report = self.bot.inspect_m07_private_readonly_order_once(
                symbol="XRPIDR", client_order_id=CLIENT,
                api_key="VIEW_ONLY_STUB", secret_key="STUB",
            )
        uid.assert_called_once()
        http.assert_called_once()
        self.assertEqual(report["order_status"], "PARTIALLY_FILLED")
        self.assertEqual(report["local_intent_recovery_reference_status"],
                         "M07_RECOVERY_SAME_DRAFT_REFERENCE_ONLY_NO_RELEASE")
        self.assertEqual(self.shadow()[1:3], ("PARTIALLY_FILLED", "1"))
        self.assertFalse(report["local_intent_retry_allowed"])
        self.assertFalse(report["bot_ownership_verified"])
        self.assertFalse(report["unknown_intent_released"])
        self.assertFalse(report["execution_allowed"])
        self.assertFalse(report["order_sent"])

    def test_partial_replay_cancel_after_restart_never_releases_any_slot(self):
        first = self.record(order(status="PARTIALLY_FILLED", executed="2"))
        self.assertFalse(first["replay"])
        self.assertEqual(first["recovery_reference_status"],
                         "M07_RECOVERY_SAME_DRAFT_REFERENCE_ONLY_NO_RELEASE")
        restarted = DecisionJournal(self.db)
        replay = self.record(order(status="PARTIALLY_FILLED", executed="2"),
                             ms=MS + 1500, journal=restarted)
        self.assertTrue(replay["replay"])
        terminal = self.record(order(status="CANCELLED", executed="2"),
                               ms=MS + 2000, journal=restarted)
        self.assertFalse(terminal["replay"])
        self.assertEqual(self.shadow()[1:3], ("CANCELLED", "2"))
        self.assertEqual(restarted.m07_offline_intent_summary()[
            "unresolved_simulated_intents"], 1)
        with self.assertRaisesRegex(DecisionConflictError,
                                    "M07_UNRESOLVED_PAIR_INTENT"):
            restarted.reserve_m07_offline_order_intent(
                intent_id="m07otherintent", draft=draft("m07otherintent"),
                recorded_epoch=EPOCH + 3,
            )
        self.assertEqual(self.shadow()[1:3], ("CANCELLED", "2"))

    def test_unknown_then_exchange_filled_does_not_release_or_retry(self):
        self.bot.simulate_m07_unknown_timeout_once(
            intent_id=CLIENT, observed_epoch=EPOCH + 1,
        )
        self.record(order(status="PARTIALLY_FILLED", executed="1"))
        restarted = DecisionJournal(self.db)
        self.record(order(status="FILLED", executed="4"),
                    ms=MS + 2000, journal=restarted)
        self.assertEqual(self.shadow()[1:3], ("FILLED", "4"))
        summary = restarted.m07_offline_intent_summary()
        self.assertEqual(summary["offline_unknown_holds"], 1)
        self.assertTrue(summary["additional_buy_reservations_blocked"])
        self.assertFalse(summary["execution_allowed"])
        with self.assertRaisesRegex(DecisionConflictError,
                                    "M07_UNKNOWN_BLOCKS_ALL_NEW_BUYS"):
            restarted.reserve_m07_offline_order_intent(
                intent_id="m07secondintent",
                draft=M07OrderDraft(
                    "ETHIDR","BUY","LIMIT","m07secondintent",
                    Decimal("2000"),Decimal("2"),
                ), recorded_epoch=EPOCH+4,
            )

    def test_cross_account_order_replay_does_not_rebind_intent(self):
        self.record(order())
        with self.assertRaisesRegex(DecisionConflictError,
                                    "M07_RECOVERY_ACCOUNT_OR_ORDER_CONFLICT"):
            self.record(order(), account=ACCOUNT_B, ms=MS + 2000)
        self.assertEqual(self.shadow(), (ACCOUNT_A, "NEW", "0", MS + 1000))
        with sqlite3.connect(self.db) as con:
            other = con.execute(
                "SELECT COUNT(*) FROM m07_exchange_readonly_orders "
                "WHERE account_fingerprint=?", (ACCOUNT_B,),
            ).fetchone()[0]
        self.assertEqual(other, 0)

    def test_immutable_draft_scope_and_created_time_conflict_fail_atomically(self):
        for mutation, expected in (
            ({"qty": "5"}, "M07_RECOVERY_IMMUTABLE_DRAFT_MISMATCH"),
            ({"side": "SELL"}, "M07_RECOVERY_CLIENT_ID_SCOPE_CONFLICT"),
            ({"symbol": "ETHIDR"}, "M07_RECOVERY_CLIENT_ID_SCOPE_CONFLICT"),
            ({"time_ms": MS - 120000},
             "M07_RECOVERY_ORDER_TIME_OUTSIDE_INTENT"),
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(DecisionConflictError, expected):
                    self.record(order(**mutation))
                self.assertIsNone(self.shadow())
                with sqlite3.connect(self.db) as con:
                    count = con.execute(
                        "SELECT COUNT(*) FROM m07_exchange_readonly_orders",
                    ).fetchone()[0]
                self.assertEqual(count, 0)

    def test_filled_terminal_is_immutable_and_cumulative_cannot_regress(self):
        self.record(order(status="PARTIALLY_FILLED", executed="2"))
        with self.assertRaisesRegex(DecisionConflictError,
                                    "M07_READONLY_EXECUTED_REGRESSION"):
            self.record(order(status="PARTIALLY_FILLED", executed="1"),
                        ms=MS + 2000)
        self.record(order(status="FILLED", executed="4"), ms=MS + 3000)
        with self.assertRaisesRegex(DecisionConflictError,
                                    "M07_READONLY_TERMINAL_CONFLICT"):
            self.record(order(status="CANCELLED", executed="4"),
                        ms=MS + 4000)
        self.assertEqual(self.shadow()[1:3], ("FILLED", "4"))
        self.assertEqual(self.journal.m07_offline_intent_summary()[
            "unresolved_simulated_intents"], 1)

    def test_unrelated_account_order_does_not_become_bot_owned(self):
        result = self.record(order(client="MANUAL_OTHER_ORDER"))
        self.assertEqual(result["recovery_reference_status"],
                         "M07_RECOVERY_NO_LOCAL_INTENT")
        self.assertIsNone(self.shadow())
        self.assertFalse(result["execution_allowed"])

    def test_legacy_intent_without_matching_draft_facts_is_unbound(self):
        with sqlite3.connect(self.db) as con:
            con.execute("DELETE FROM m07_offline_intent_draft_facts")
        result = self.record(order())
        self.assertEqual(result["recovery_reference_status"],
                         "M07_RECOVERY_LEGACY_OR_MARKET_DRAFT_UNBOUND")
        self.assertIsNone(self.shadow())
        self.assertEqual(self.journal.m07_offline_intent_summary()[
            "unresolved_simulated_intents"], 1)


if __name__ == "__main__":
    unittest.main()
