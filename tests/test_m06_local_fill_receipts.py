"""M06-04 immutable UID-linked local fill receipts (source evidence only).

No Indodax network, orders, genuine bot-ownership assertion or LIVE.
"""
import sqlite3
import tempfile
import time
import unittest
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m06_ownership_ledger import M06OwnershipLedger, OwnershipConflict
from indodax_ai_bot.m06_private_payloads import OrderRecord, FillRecord
from indodax_ai_bot.m06_scoped_https import M06ScopedApproval, M06RequestBudget
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


class M06LocalFillReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "original-v2.sqlite3"
        self.ledger = M06OwnershipLedger(self.path)
        self.path.chmod(0o600)
        self.scope = self.ledger.account_scope_from_exchange_uid(615)
        self.order = OrderRecord(
            "floki-order-01", "flokiidr", "SELL", "PARTIALLY_FILLED",
            D("10"), D("3"), "v2-created-client-01",
        )
        self.fill = FillRecord(
            "trade-local-01", "floki-order-01", "flokiidr",
            D("1"), D("100"), D("1"), client_order_id="v2-created-client-01",
            is_buyer=False, quote_quantity=D("100"), commission_asset="idr",
        )
        self.ledger.record_local_intent(
            account_key=self.scope, order_id=self.order.order_id,
            client_id=self.order.client_order_id, symbol=self.order.symbol,
            side=self.order.side,
        )

    def observe(self, fill=None, *, scope=None, order=None):
        return self.ledger.observe(
            account_key=self.scope if scope is None else scope,
            order=self.order if order is None else order,
            fills=(self.fill if fill is None else fill,),
        )

    def test_first_fill_persists_immutable_uid_and_rebuy_linkage(self):
        first = self.observe()
        self.assertTrue(first.account_match)
        self.assertFalse(first.bot_owned_verified)
        self.assertFalse(first.execution_allowed)
        self.assertEqual(first.protected_rebuy.total_idr_local_protected, D("100"))
        self.assertEqual(first.protected_rebuy.local_fill_receipt_count, 1)
        self.assertTrue(first.protected_rebuy.local_rebuy_receipts_linked)
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT account_hash,order_id,side,content_sha256 "
                "FROM m06_local_fill_receipts WHERE trade_id=?",
                (self.fill.trade_id,),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1:3], ("floki-order-01", "SELL"))
        self.assertEqual(len(rows[0][3]), 64)
        self.assertNotIn(self.scope, rows[0])

    def test_restarting_and_replaying_preserves_single_receipt(self):
        first = self.observe()
        reboot = M06OwnershipLedger(self.path)
        second = reboot.observe(
            account_key=self.scope, order=self.order, fills=(self.fill,),
        )
        self.assertEqual(first, second)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM m06_local_fill_receipts"
            ).fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT count(*) FROM m06_special_rebuy_observations"
            ).fetchone()[0], 1)

    def test_replay_with_changed_quote_or_fee_asset_fails_atomically(self):
        self.observe()
        for wrong in (
            replace(self.fill, quote_quantity=D("101")),
            replace(self.fill, commission_asset="floki"),
        ):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(
                OwnershipConflict, "FILL_RECEIPT_REPLAY_CONFLICT"
            ):
                self.observe(wrong)
        review = M06OwnershipLedger(self.path).review_protected_rebuy(
            account_key=self.scope,
        )
        self.assertEqual(review.total_idr_local_protected, D("100"))
        self.assertEqual(review.local_fill_receipt_count, 1)

    def test_reuse_same_trade_id_for_different_order_denied(self):
        self.observe()
        other = replace(
            self.order, order_id="floki-order-02",
            client_order_id="v2-created-client-02",
        )
        self.ledger.record_local_intent(
            account_key=self.scope, order_id=other.order_id,
            client_id=other.client_order_id, symbol=other.symbol,
            side=other.side,
        )
        reused = replace(
            self.fill, order_id=other.order_id,
            client_order_id=other.client_order_id,
        )
        with self.assertRaisesRegex(
            OwnershipConflict, "FILL_RECEIPT_REPLAY_CONFLICT"
        ):
            self.observe(reused, order=other)
        self.assertEqual(
            self.ledger.review_protected_rebuy(
                account_key=self.scope
            ).total_idr_local_protected, D("100"),
        )

    def test_legacy_fill_without_receipt_never_silently_rebound(self):
        self.observe()
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM m06_local_fill_receipts")
        local = M06OwnershipLedger(self.path).review_protected_rebuy(
            account_key=self.scope,
        )
        self.assertEqual(local.total_idr_local_protected, D("100"))
        self.assertFalse(local.local_rebuy_receipts_linked)
        with self.assertRaisesRegex(
            OwnershipConflict, "LEGACY_FILL_RECEIPT_ABSENT"
        ):
            self.observe()
        self.assertEqual(
            self.ledger.review_protected_rebuy(
                account_key=self.scope
            ).total_idr_local_protected, D("100"),
        )

    def test_foreign_uid_cannot_claim_same_intent_receipt(self):
        self.observe()
        foreign = self.ledger.account_scope_from_exchange_uid(616)
        with self.assertRaisesRegex(OwnershipConflict, "UNATTRIBUTED_FILL"):
            self.observe(scope=foreign)
        review = self.ledger.review_protected_rebuy(account_key=foreign)
        self.assertEqual(review.total_idr_local_protected, D("0"))
        self.assertFalse(review.execution_allowed)

    def test_corrupted_local_receipt_linkage_fails_closed(self):
        self.observe()
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE m06_local_fill_receipts SET side='BUY' "
                "WHERE trade_id=?", (self.fill.trade_id,),
            )
        with self.assertRaisesRegex(
            OwnershipConflict, "SPECIAL_RECEIPT_CONFLICT"
        ):
            self.ledger.review_protected_rebuy(account_key=self.scope)

    def test_original_orchestrator_four_mock_gets_observes_local_receipt_only(self):
        self.observe()
        now = int(time.time() * 1000)
        go = M06ScopedApproval(
            "M06_PRIVATE_VIEW_ONLY_RETRY_20261031",
            now - 1000, now + 899000, 4, "injidr",
        )
        source = {
            "/api/v2/account": {
                "uid": 615, "canTrade": False, "canWithdraw": False,
                "canWithdrawFiat": False,
                "balances": [
                    {"asset": "IDR", "free": "10000", "locked": "0"},
                    {"asset": "FLOKI", "free": "100", "locked": "0"},
                    {"asset": "PEPE", "free": "0", "locked": "0"},
                ],
            },
            "/api/v2/openOrders": [],
            "/api/v2/order/histories": {"data": []},
            "/api/v2/myTrades": {"data": []},
        }
        seen = []
        with patch(
            "indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
            side_effect=lambda req: (
                seen.append(req.path), source[req.path]
            )[1],
        ):
            bot = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(
                    Path(self.tmp.name) / "decision-journal.sqlite3",
                ),
            )
            result = bot.inspect_m06_private_readonly_once(
                approval=go,
                budget=M06RequestBudget(
                    str(Path(self.tmp.name) / "mock-budget.sqlite3"),
                ),
                api_key="fake-key", secret_key="fake-secret",
                local_ownership_db=str(self.path),
            )
        self.assertEqual(len(seen), 4)
        self.assertTrue(result.local_ledger_account_bound)
        self.assertTrue(result.local_rebuy_receipts_linked)
        self.assertFalse(result.protected_rebuy_verified)
        self.assertFalse(result.bot_owned_fills_verified)
        self.assertFalse(result.capital_spendable_verified)
        self.assertFalse(result.execution_allowed)


if __name__ == "__main__":
    unittest.main()
