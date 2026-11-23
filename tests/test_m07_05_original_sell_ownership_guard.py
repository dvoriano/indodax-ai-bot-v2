"""M07-05 SELL quantities: no bot-owned BUY fill proof => zero executable.

Tests exercise the original orchestrator, M06 ownership ledger and original
M07 journal with offline fabricated local receipts. No HTTP / no order sends.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m06_ownership_ledger import (
    M06OwnershipLedger, OwnershipConflict,
)
from indodax_ai_bot.m06_private_payloads import OrderRecord, FillRecord
from indodax_ai_bot.m07_order_adapter import M07OrderDraft
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


UID = 987654


def sell(quantity="4", *, symbol="XRPIDR"):
    return M07OrderDraft(symbol=symbol, side="SELL", order_type="LIMIT",
                         client_order_id="m07sellguard1",
                         price=D("25000"), quantity=D(quantity))


class OriginalM07SellOwnershipGuardTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.m06db = root / "m06-existing-ownership.sqlite3"
        self.m07db = root / "m07-original-journal.sqlite3"
        self.ledger = M06OwnershipLedger(self.m06db)
        self.bot = DailyScalpingOrchestrator(
            Settings(), DecisionJournal(self.m07db),
        )
        self.key = M06OwnershipLedger.account_scope_from_exchange_uid(UID)

    def review(self, quantity="4", *, uid=UID, symbol="XRPIDR"):
        return self.bot.review_m07_sell_quantity_ownership_offline(
            local_m06_ledger_db=str(self.m06db),
            account_uid=uid, draft=sell(quantity,symbol=symbol),
        )

    def add_fill(self, *, trade="TR1", order="BUY1", client="CID1",
                 side="BUY", qty="4", account=None):
        key = self.key if account is None else account
        self.ledger.record_local_intent(
            account_key=key, order_id=order, client_id=client,
            symbol="xrp_idr", side=side,
        )
        self.ledger.observe(
            account_key=key,
            order=OrderRecord(
                order_id=order, symbol="xrp_idr", side=side,
                status="PARTIALLY_FILLED", original_qty=D("20"),
                executed_qty=D(qty), client_order_id=client,
            ),
            fills=(FillRecord(
                trade_id=trade, order_id=order, symbol="xrp_idr",
                quantity=D(qty), price=D("25000"),
                commission=D("1"), client_order_id=client,
                is_buyer=side=="BUY", quote_quantity=D(qty)*D("25000"),
                commission_asset="idr",
            ),),
        )

    def test_external_wallet_or_manual_token_balance_grants_no_sell(self):
        result = self.review()
        self.assertEqual(result["status"],
                         "M07_SELL_LOCAL_QUANTITY_EXCEEDED_OR_COMMITTED")
        self.assertEqual(result["maximum_executable_sell_base_qty"], "0")
        self.assertEqual(result["local_net_qty_unverified"], "0")
        self.assertFalse(result["exchange_bot_owned_buy_fills_verified"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["order_sent"])

    def test_local_matching_buy_math_is_not_verified_bot_ownership(self):
        self.add_fill()
        result = self.review()
        self.assertTrue(result["requested_within_local_math_only"])
        self.assertEqual(result["local_buy_qty_unverified"], "4")
        self.assertEqual(result["bot_owned_verified_base_qty"], "0")
        self.assertEqual(result["maximum_executable_sell_base_qty"], "0")
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["sell_reservation_created"])

    def test_oversell_above_local_observed_buy_is_blocked(self):
        self.add_fill(qty="4")
        result = self.review("4.00000001")
        self.assertFalse(result["requested_within_local_math_only"])
        self.assertEqual(result["status"],
                         "M07_SELL_LOCAL_QUANTITY_EXCEEDED_OR_COMMITTED")
        self.assertFalse(result["execution_allowed"])

    def test_partial_buy_only_counts_observed_partial_fill_not_intent(self):
        self.add_fill(qty="1")
        allowed_math = self.review("1")
        blocked_math = self.review("1.001")
        self.assertTrue(allowed_math["requested_within_local_math_only"])
        self.assertFalse(blocked_math["requested_within_local_math_only"])
        self.assertFalse(allowed_math["execution_allowed"])

    def test_local_sell_deduction_and_unsettled_sell_hold_conservatively(self):
        self.add_fill(qty="5")
        self.add_fill(trade="TR2", order="SELL1", client="CID2",
                      side="SELL", qty="2")
        result = self.review("3")
        self.assertEqual(result["local_buy_qty_unverified"], "5")
        self.assertEqual(result["local_sell_qty_unverified"], "2")
        self.assertEqual(result["local_net_qty_unverified"], "3")
        self.assertEqual(result["local_pending_sell_intents"], 1)
        self.assertFalse(result["requested_within_local_math_only"])
        self.assertFalse(result["execution_allowed"])

    def test_other_uid_local_buy_and_manual_special_token_excluded(self):
        self.add_fill(account="INDODAX_V2_UID:123456")
        out = self.review()
        self.assertEqual(out["local_net_qty_unverified"], "0")
        self.assertFalse(out["execution_allowed"])
        with self.assertRaisesRegex(ValueError,"M07_SYMBOL_NOT_ADMITTED"):
            self.review(symbol="FLOKIIDR")
        with self.assertRaisesRegex(ValueError,"M07_SYMBOL_NOT_ADMITTED"):
            self.review(symbol="PEPEIDR")
        with self.assertRaisesRegex(OwnershipConflict,"M07_SELL_REVIEW_INPUT_INVALID"):
            self.review(symbol="USDTIDR")

    def test_corrupted_referenced_local_fill_fails_closed(self):
        self.add_fill()
        with sqlite3.connect(self.m06db) as db:
            db.execute("UPDATE m06_observed_fills SET qty='NaN' WHERE trade_id='TR1'")
        with self.assertRaisesRegex(OwnershipConflict,
                                    "M07_SELL_LOCAL_QTY_CORRUPT"):
            self.review()

    def test_foreign_or_orphan_local_receipt_does_not_become_inventory(self):
        self.add_fill()
        with sqlite3.connect(self.m06db) as db:
            db.execute("UPDATE m06_local_fill_receipts "
                       "SET order_id='OTHER_ORDER' WHERE trade_id='TR1'")
        with self.assertRaisesRegex(OwnershipConflict,
                                    "M07_SELL_LOCAL_RECEIPT_CONFLICT"):
            self.review()

    def test_restart_and_concurrent_views_cannot_create_any_sell(self):
        self.add_fill()
        restarted = DailyScalpingOrchestrator(
            Settings(), DecisionJournal(self.m07db),
        )
        def run(_):
            return restarted.review_m07_sell_quantity_ownership_offline(
                local_m06_ledger_db=str(self.m06db),
                account_uid=UID, draft=sell(),
            )
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, range(8)))
        self.assertEqual(len(results), 8)
        self.assertTrue(all(not r["execution_allowed"]
                            and not r["sell_reservation_created"]
                            and r["maximum_executable_sell_base_qty"]=="0"
                            for r in results))
        with sqlite3.connect(self.m07db) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM m07_offline_order_intents",
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_invalid_quantity_rejected_without_other_sources(self):
        for qty in ("0","-1","NaN","Infinity","1E+30"):
            with self.subTest(qty=qty):
                with self.assertRaises(ValueError):
                    self.review(qty)

    def test_while_unknown_intent_already_exists_guard_remains_no_sell(self):
        draft = M07OrderDraft(
            symbol="ETHIDR",side="BUY",order_type="LIMIT",
            client_order_id="aunknowsellhold1",
            price=D("1000"),quantity=D("2"),
        )
        self.bot.reserve_m07_offline_intent_once(
            intent_id="aunknowsellhold1",draft=draft,
            observed_epoch=1800001000,
        )
        self.bot.simulate_m07_unknown_timeout_once(
            intent_id="aunknowsellhold1",observed_epoch=1800001001,
        )
        self.add_fill()
        report = self.review()
        self.assertEqual(report["original_journal_unknown_holds"],1)
        self.assertEqual(report["maximum_executable_sell_base_qty"],"0")
        self.assertFalse(report["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
