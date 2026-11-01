"""M06-02..05 one original V2 account evidence route; never grants LIVE.

All exchange responses are mocked; no private account request, Telegram
approval, alternate bot/ledger, or runtime service touches.
"""
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m06_account_review import reconcile_readonly_private_snapshot
from indodax_ai_bot.m06_ownership_ledger import (
    M06OwnershipLedger, OwnershipConflict,
)
from indodax_ai_bot.m06_private_payloads import AccountBalances, FillRecord, OrderRecord
from indodax_ai_bot.m06_scoped_https import M06RequestBudget, M06ScopedApproval
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


class M06AccountEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)
        self.path = self.root / "v2-ledger.sqlite3"
        self.uid = 521
        self.key = M06OwnershipLedger.account_scope_from_exchange_uid(self.uid)
        self.ledger = M06OwnershipLedger(self.path)
        self.order = OrderRecord(
            "v2-exchange-order", "injidr", "BUY", "FILLED",
            D("2"), D("2"), "v2-client-order",
        )
        self.fill = FillRecord(
            "v2-exchange-fill", self.order.order_id, "injidr",
            D("2"), D("150"), D("1"),
            client_order_id=self.order.client_order_id,
            is_buyer=True, quote_quantity=D("300"), commission_asset="idr",
        )
        self.ledger.record_local_intent(
            account_key=self.key, order_id=self.order.order_id,
            client_id=self.order.client_order_id, symbol="injidr", side="BUY",
        )
        self.path.chmod(0o600)

    def observe(self):
        self.ledger.observe(
            account_key=self.key, order=self.order, fills=(self.fill,),
        )

    def match(self, fills=None, orders=None, uid=None):
        return self.ledger.match_exchange_window_to_local_receipts_readonly(
            db_path=str(self.path), account_uid=self.uid if uid is None else uid,
            orders=(self.order,) if orders is None else orders,
            fills=(self.fill,) if fills is None else fills,
            symbol="injidr",
        )

    def test_local_intent_not_enough_without_fill_receipt(self):
        outcome = self.match()
        self.assertEqual(outcome.checked_fills, 1)
        self.assertEqual(outcome.matched_local_receipts, 0)
        self.assertEqual(outcome.unlinked_fills, 1)
        self.assertFalse(outcome.bot_owned_verified)

    def test_immutable_local_fill_matches_exchange_shaped_window(self):
        self.observe()
        result = self.match()
        self.assertEqual(result.checked_fills, 1)
        self.assertEqual(result.matched_local_receipts, 1)
        self.assertEqual(result.unlinked_fills, 0)
        self.assertFalse(result.account_wide_history_complete)
        self.assertFalse(result.execution_allowed)
        self.assertNotIn("v2-exchange-fill", repr(result))
        self.assertEqual(self.match(), result)

    def test_mutated_fee_cannot_relabel_existing_fill(self):
        self.observe()
        amended = replace(self.fill, commission=D("9"))
        with self.assertRaisesRegex(OwnershipConflict, "RECEIPT_CONFLICT"):
            self.match(fills=(amended,))

    def test_other_account_uid_does_not_claim_local_receipts(self):
        self.observe()
        with self.assertRaisesRegex(OwnershipConflict, "ACCOUNT_UNBOUND"):
            self.match(uid=522)

    def test_missing_client_provenance_is_not_silently_backfilled(self):
        self.observe()
        altered = replace(self.fill, client_order_id=None)
        report = self.match(fills=(altered,))
        self.assertEqual(report.matched_local_receipts, 0)
        self.assertEqual(report.unlinked_fills, 1)

    def test_open_receipt_conflict_or_duplicate_is_denied(self):
        self.observe()
        with self.assertRaisesRegex(OwnershipConflict, "DUPLICATE_FILL"):
            self.match(fills=(self.fill, self.fill))
        other = replace(self.order, order_id="v2-exchange-order-other")
        with self.assertRaisesRegex(OwnershipConflict, "DUPLICATE_ORDER"):
            self.match(orders=(self.order, self.order))
        report = self.match(orders=(other,))
        self.assertEqual(report.unlinked_fills, 1)

    def test_one_reconciliation_reports_every_unverified_dependency(self):
        self.observe()
        local = M06OwnershipLedger.inspect_existing_account_readonly(
            db_path=str(self.path), account_uid=self.uid,
        )
        review = reconcile_readonly_private_snapshot(
            account=AccountBalances(
                self.uid, (("IDR", D("9000"), D("2000")),
                           ("PEPE", D("100"), D("0"))),
                D("9000"), D("2000"),
            ),
            open_orders=(), order_history=(self.order,), fills=(self.fill,),
            symbol="injidr", local_rebuy=local,
        )
        self.assertIn("M06_EXTERNAL_ACCOUNT_COMMITMENTS_UNPROVEN",
                      review.unresolved_requirements)
        self.assertIn("M06_FULL_ACCOUNT_HISTORY_UNPROVEN",
                      review.unresolved_requirements)
        self.assertIn("M06_SPECIAL_WALLET_OWNERSHIP_UNPROVEN",
                      review.unresolved_requirements)
        self.assertEqual(review.status, "REJECT")
        self.assertFalse(review.spendable_capital_verified)

    def test_original_four_mock_gets_crosslinks_and_remains_reject(self):
        self.observe()
        now = int(time.time() * 1000)
        source = {
            "/api/v2/account": {
                "uid": self.uid, "canTrade": False,
                "canWithdraw": False, "canWithdrawFiat": False,
                "balances": [{"asset": "IDR", "free": "9000", "locked": "2000"}],
            },
            "/api/v2/openOrders": [],
            "/api/v2/order/histories": {
                "data": [{"orderId":self.order.order_id,"symbol":"injidr",
                          "side":"BUY","status":"FILLED","oriQty":"2",
                          "executedQty":"2","clientOrderId":"v2-client-order"}],
            },
            "/api/v2/myTrades": {
                "data": [{"tradeId":self.fill.trade_id,
                          "orderId":self.order.order_id,"symbol":"injidr",
                          "qty":"2","price":"150","commission":"1",
                          "clientOrderId":"v2-client-order",
                          "isBuyer":True,"quoteQty":"300",
                          "commissionAsset":"IDR"}],
            },
        }
        seen = []
        with patch(
            "indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
            side_effect=lambda request: (
                seen.append(request.path), source[request.path]
            )[1],
        ):
            result = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(self.root / "decisions.sqlite3"),
            ).inspect_m06_private_readonly_once(
                approval=M06ScopedApproval(
                    "M06_PRIVATE_VIEW_ONLY_RETRY_20261031",
                    now - 1000, now + 899000, 4, "injidr",
                ),
                budget=M06RequestBudget(str(self.root / "budget.sqlite3")),
                api_key="fake-key", secret_key="fake-secret",
                local_ownership_db=str(self.path),
            )
        self.assertEqual(len(seen), 4)
        self.assertEqual(result.local_exchange_receipts_linked, 1)
        self.assertEqual(result.local_exchange_receipts_unlinked, 0)
        self.assertFalse(result.bot_owned_fills_verified)
        self.assertFalse(result.cross_bot_ownership_verified)
        self.assertFalse(result.capital_spendable_verified)
        self.assertFalse(result.execution_allowed)
        self.assertIn("M06_EXTERNAL_ACCOUNT_COMMITMENTS_UNPROVEN",
                      result.unresolved_requirements)
        self.assertNotIn("v2-exchange-order", repr(result))


if __name__ == "__main__":
    unittest.main()
