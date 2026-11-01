"""M06 protected rebuy ledger, atomic replay safety and original V2 caller.

Pure offline fixtures, no Indodax private HTTP, no BUY/SELL and no LIVE.
"""
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.m06_ownership_ledger import (
    M06OwnershipLedger, OwnershipConflict,
)
from indodax_ai_bot.m06_private_payloads import (
    AccountBalances, FillRecord, OrderRecord,
)


def order(asset, side, order_id, client_id, executed="2"):
    return OrderRecord(
        order_id, asset.lower() + "idr", side, "PARTIALLY_FILLED",
        Decimal("10"), Decimal(executed), client_id,
    )


def fill(asset, order_id, trade_id, *, qty="1", price="100", buyer=False,
         client_id=None, quote=None):
    return FillRecord(
        trade_id, order_id, asset.lower() + "idr",
        Decimal(qty), Decimal(price), Decimal("0"),
        client_order_id=client_id, is_buyer=buyer,
        quote_quantity=Decimal(quote) if quote is not None else None,
        commission_asset="idr",
    )


class M06ProtectedRebuyOriginalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ownership.sqlite3"
        self.ledger = M06OwnershipLedger(self.path)

    def intent(self, account, asset, side, oid, cid):
        self.ledger.record_local_intent(
            account_key=account, order_id=oid, client_id=cid,
            symbol=asset.lower() + "idr", side=side,
        )

    def test_floki_sell_proceeds_are_atomic_restart_safe_and_idempotent(self):
        self.intent("account-a", "FLOKI", "SELL", "floki-sell-1", "v2-floki-1")
        record = order("FLOKI", "SELL", "floki-sell-1", "v2-floki-1")
        trades = (
            fill("FLOKI", "floki-sell-1", "trade-1",
                 client_id="v2-floki-1", quote="101"),
            fill("FLOKI", "floki-sell-1", "trade-2",
                 client_id="v2-floki-1", quote="100"),
        )
        first = self.ledger.observe(
            account_key="account-a", order=record, fills=trades,
        )
        self.assertTrue(first.account_match)
        self.assertFalse(first.bot_owned_verified)
        self.assertEqual(
            first.protected_rebuy.total_idr_local_protected, Decimal("201"),
        )
        self.assertEqual(first.protected_rebuy.distinct_sell_fills, 2)
        restart = M06OwnershipLedger(self.path)
        again = restart.observe(account_key="account-a", order=record, fills=trades)
        self.assertEqual(first, again)
        status = restart.review_protected_rebuy(account_key="account-a")
        self.assertEqual(status.floki_idr_local_protected, Decimal("201"))
        self.assertEqual(status.pepe_idr_local_protected, Decimal("0"))
        self.assertFalse(status.exchange_proceeds_verified)
        self.assertFalse(status.rebuy_spending_allowed)
        self.assertFalse(status.main_capital_spending_allowed)
        self.assertFalse(status.execution_allowed)

    def test_pepe_separate_account_no_release_on_unverified_buy(self):
        self.intent("account-a", "PEPE", "SELL", "pepe-sell-1", "v2-pepe-sell")
        self.ledger.observe(
            account_key="account-a",
            order=order("PEPE", "SELL", "pepe-sell-1", "v2-pepe-sell",
                        executed="1"),
            fills=(fill("PEPE", "pepe-sell-1", "t-pepe",
                        client_id="v2-pepe-sell"),),
        )
        self.assertEqual(
            self.ledger.review_protected_rebuy(
                account_key="account-a").total_idr_local_protected,
            Decimal("100"),
        )
        self.assertEqual(
            M06OwnershipLedger(self.path).review_protected_rebuy(
                account_key="account-b").total_idr_local_protected,
            Decimal("0"),
        )
        self.intent("account-a", "PEPE", "BUY", "pepe-buy-1", "v2-pepe-buy")
        result = self.ledger.observe(
            account_key="account-a",
            order=order("PEPE", "BUY", "pepe-buy-1", "v2-pepe-buy",
                        executed="1"),
            fills=(fill("PEPE", "pepe-buy-1", "t-pepe-buy",
                        buyer=True, client_id="v2-pepe-buy"),),
        )
        self.assertEqual(
            result.protected_rebuy.total_idr_local_protected,
            Decimal("100"),
        )  # No debit without independent exchange-confirmed rebuy settlement.
        self.assertFalse(result.protected_rebuy.execution_allowed)

    def test_foreign_identity_or_forged_verification_cannot_credit(self):
        self.intent("a", "FLOKI", "SELL", "oid", "owner-client")
        base = order("FLOKI", "SELL", "oid", "owner-client", executed="1")
        with self.assertRaisesRegex(OwnershipConflict, "FOREIGN_CLIENT_FILL"):
            self.ledger.observe(
                account_key="a", order=base, fills=(
                    fill("FLOKI", "oid", "t1", client_id="foreign"),),
            )
        with self.assertRaisesRegex(OwnershipConflict, "FILL_SIDE_MISMATCH"):
            self.ledger.observe(
                account_key="a", order=base, fills=(
                    fill("FLOKI", "oid", "t2", buyer=True),),
            )
        forged = FillRecord("t3", "oid", "flokiidr",
                            Decimal("1"), Decimal("100"), Decimal("0"),
                            bot_owned_verified=True)
        with self.assertRaisesRegex(OwnershipConflict, "FORGED_OWNERSHIP"):
            self.ledger.observe(
                account_key="a", order=base, fills=(forged,),
            )
        self.assertEqual(self.ledger.review_protected_rebuy(
            account_key="a").distinct_sell_fills, 0)

    def test_sell_qty_over_snapshot_rolls_back_entire_transaction(self):
        self.intent("a", "FLOKI", "SELL", "oid", "client")
        with self.assertRaisesRegex(OwnershipConflict, "EXCEEDS_ORDER"):
            self.ledger.observe(
                account_key="a",
                order=order("FLOKI", "SELL", "oid", "client",
                            executed="1"),
                fills=(
                    fill("FLOKI", "oid", "one", qty="1"),
                    fill("FLOKI", "oid", "two", qty="1"),
                ),
            )
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM m06_observed_fills").fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT count(*) FROM m06_special_rebuy_observations"
            ).fetchone()[0], 0)
        self.assertEqual(self.ledger.review_protected_rebuy(
            account_key="a").total_idr_local_protected, Decimal("0"))

    def test_same_trade_replay_conflict_never_changes_reserved_amount(self):
        self.intent("a", "FLOKI", "SELL", "oid", "client")
        record = order("FLOKI", "SELL", "oid", "client", executed="1")
        self.ledger.observe(
            account_key="a", order=record,
            fills=(fill("FLOKI", "oid", "trade", price="100"),),
        )
        with self.assertRaisesRegex(OwnershipConflict, "FILL_REPLAY_CONFLICT"):
            self.ledger.observe(
                account_key="a", order=record,
                fills=(fill("FLOKI", "oid", "trade", price="101"),),
            )
        self.assertEqual(self.ledger.review_protected_rebuy(
            account_key="a").total_idr_local_protected, Decimal("100"))
        with self.assertRaisesRegex(OwnershipConflict, "UNATTRIBUTED_FILL"):
            self.ledger.observe(
                account_key="b", order=record,
                fills=(fill("FLOKI", "oid", "trade-foreign"),),
            )

    def test_non_special_sell_does_not_falsely_create_rebuy_reserve(self):
        self.intent("a", "BTC", "SELL", "btc-oid", "btc-owner")
        self.ledger.observe(
            account_key="a",
            order=order("BTC", "SELL", "btc-oid", "btc-owner",
                        executed="1"),
            fills=(fill("BTC", "btc-oid", "btc-fill"),),
        )
        self.assertEqual(self.ledger.review_protected_rebuy(
            account_key="a").distinct_sell_fills, 0)

    def test_manual_asset_floors_survive_restart_without_lowering(self):
        status = self.ledger.protect_manual_asset_floor(
            account_key="account-a", asset="FLOKI",
            minimum_qty=Decimal("750000"),
        )
        self.assertEqual(status.floki_manual_qty_local_floor, Decimal("750000"))
        self.assertFalse(status.manual_holding_verified)
        self.assertFalse(status.execution_allowed)
        restart = M06OwnershipLedger(self.path)
        self.assertEqual(restart.review_protected_rebuy(
            account_key="account-a").floki_manual_qty_local_floor,
            Decimal("750000"))
        with self.assertRaisesRegex(OwnershipConflict, "REDUCTION_DENIED"):
            restart.protect_manual_asset_floor(
                account_key="account-a", asset="FLOKI",
                minimum_qty=Decimal("749999"),
            )
        increased = restart.protect_manual_asset_floor(
            account_key="account-a", asset="FLOKI",
            minimum_qty=Decimal("800000"))
        self.assertEqual(increased.floki_manual_qty_local_floor,
                         Decimal("800000"))
        pepe = restart.protect_manual_asset_floor(
            account_key="account-a", asset="PEPE",
            minimum_qty=Decimal("200000"))
        self.assertEqual(pepe.pepe_manual_qty_local_floor, Decimal("200000"))
        self.assertEqual(restart.review_protected_rebuy(
            account_key="other-account").floki_manual_qty_local_floor,
            Decimal("0"))
        self.assertFalse(pepe.manual_holding_verified)

    def test_original_orchestrator_manual_floor_and_restart(self):
        with tempfile.TemporaryDirectory() as td:
            bot = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(Path(td) / "journal.sqlite3"))
            protected = bot.protect_m06_manual_holding_floor_offline(
                db_path=str(self.path), account_key="owner-a",
                asset="FLOKI", minimum_qty=Decimal("750"),
            )
            self.assertEqual(protected.floki_manual_qty_local_floor,
                             Decimal("750"))
            restarted = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(Path(td) / "journal.sqlite3"))
            persisted = restarted.review_m06_protected_rebuy_offline(
                db_path=str(self.path), account_key="owner-a")
            self.assertEqual(persisted.floki_manual_qty_local_floor,
                             Decimal("750"))
            self.assertFalse(persisted.execution_allowed)
            with self.assertRaisesRegex(OwnershipConflict, "REDUCTION_DENIED"):
                restarted.protect_m06_manual_holding_floor_offline(
                    db_path=str(self.path), account_key="owner-a",
                    asset="FLOKI", minimum_qty=Decimal("749"),
                )

    def test_original_orchestrator_uses_same_rebuy_ledger_for_capital_reject(self):
        self.intent("v2-account", "FLOKI", "SELL", "sell-f", "v2-f")
        with tempfile.TemporaryDirectory() as td:
            decisions = DecisionJournal(Path(td) / "journal.sqlite3")
            bot = DailyScalpingOrchestrator(Settings(), decisions)
            record = order("FLOKI", "SELL", "sell-f", "v2-f",
                           executed="1")
            trade = fill("FLOKI", "sell-f", "v2-trade", client_id="v2-f")
            owned = bot.review_m06_local_intent_ownership(
                db_path=str(self.path), account_key="v2-account",
                order=record, fills=(trade,),
            )
            self.assertFalse(owned.bot_owned_verified)
            self.assertEqual(owned.protected_rebuy.floki_idr_local_protected,
                             Decimal("100"))
            restarted = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(Path(td) / "journal.sqlite3"))
            persisted = restarted.review_m06_protected_rebuy_offline(
                db_path=str(self.path), account_key="v2-account")
            self.assertEqual(persisted.total_idr_local_protected,
                             Decimal("100"))
            account = AccountBalances(
                1, (("IDR", Decimal("1000"), Decimal("150")),),
                Decimal("1000"), Decimal("150"))
            cap = restarted.reconcile_m06_capital_with_local_rebuy_offline(
                db_path=str(self.path), account_key="v2-account",
                account=account, open_orders=(),
                unlocked_commitments_idr=Decimal("0"),
                local_shadow_idr=Decimal("0"),
            )
            self.assertEqual(cap.protected_rebuy_idr, Decimal("100"))
            self.assertEqual(cap.calculated_available_idr, Decimal("0"))
            self.assertFalse(cap.source_authenticated)
            self.assertFalse(cap.execution_allowed)


if __name__ == "__main__":
    unittest.main()
