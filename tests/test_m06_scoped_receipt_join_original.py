"""M06 original V2 source-to-UID local receipt join, scoped OFFLINE tests.

All fills are synthetic; matching local receipts is never exchange
authentication, settlement, all-time history or spending permission.
"""
from __future__ import annotations
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m06_ownership_ledger import M06OwnershipLedger, OwnershipConflict
from indodax_ai_bot.m06_private_payloads import OrderRecord,FillRecord
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator

UID=73819


def sample(symbol="flokiidr",side="BUY", *,
           trade="buy-synthetic-1",oid="buy-order-synthetic-1",
           quote="10020",fee="5",price="5000",qty="2"):
    order=OrderRecord(oid,symbol,side,"FILLED",D(qty),
                      D(qty),"test-client-one",False,"full-test-order")
    fill=FillRecord(trade,oid,symbol,D(qty),D(price),D(fee),
                    False,"test-client-one",side=="BUY",D(quote),"idr")
    return order,fill


class M06ScopedLocalReceiptJoinV2Tests(unittest.TestCase):
    def setUp(self):
        self.t=TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.p=Path(self.t.name)/"m06-ownership.sqlite3"
        self.ledger=M06OwnershipLedger(self.p)
        self.p.chmod(0o600)
        self.account_key=M06OwnershipLedger.account_scope_from_exchange_uid(UID)
        self.app=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(Path(self.t.name)/"journal.sqlite3"),
        )

    def write(self, order, fill):
        if order.side=="SELL":
            return self.ledger.observe_scoped_external_special_sells_offline(
                account_uid=UID,symbol=order.symbol,
                orders=(order,),fills=(fill,))
        return self.ledger.observe_scoped_same_token_rebuy_buy_offline(
            account_uid=UID,symbol=order.symbol,
            orders=(order,),fills=(fill,))

    def join(self, symbol, orders, fills, uid=UID):
        return self.app.review_m06_special_source_receipts_offline(
            db_path=str(self.p),account_uid=uid,
            symbol=symbol,orders=orders,fills=fills)

    def test_original_orchestrator_matches_both_local_receipt_sides(self):
        sell,sell_fill=sample(side="SELL",trade="sell-01",oid="sell-01",
            quote="30000",price="10000",qty="3")
        buy,buy_fill=sample()
        self.write(sell,sell_fill)
        before=self.write(buy,buy_fill)
        summary=self.join("flokiidr",(sell,buy),(sell_fill,buy_fill))
        self.assertEqual(summary["checked_fills"],2)
        self.assertEqual(summary["matched_local_manual_sell_receipts"],1)
        self.assertEqual(summary["matched_local_rebuy_buy_receipts"],1)
        self.assertEqual(summary["missing_local_sell_receipts"],0)
        self.assertEqual(summary["missing_local_buy_receipts"],0)
        for key in ("full_history_verified","source_authentication_verified",
                    "exchange_settlement_verified","source_funds_spendable_verified",
                    "rebuy_hold_release_allowed","execution_allowed","order_sent"):
            self.assertFalse(summary[key])
        after=self.ledger.review_protected_rebuy(account_key=self.account_key)
        self.assertEqual(before.floki_idr_local_protected,D("30000"))
        self.assertEqual(after.floki_idr_local_protected,D("30000"))
        self.assertEqual(after.floki_rebuy_buy_observed_outflow_idr,D("10025"))

    def test_pepe_scope_is_isolated_and_same_token_required(self):
        pepe,pfill=sample("pepeidr",side="SELL",trade="pepe-sell",oid="pepe-01")
        self.write(pepe,pfill)
        result=self.join("pepeidr",(pepe,),(pfill,))
        self.assertEqual(result["matched_local_manual_sell_receipts"],1)
        with self.assertRaisesRegex(OwnershipConflict,"ORDER_INVALID"):
            self.join("flokiidr",(pepe,),(pfill,))

    def test_tampered_fee_or_price_is_hard_conflict_not_missing(self):
        buy,fill=sample()
        self.write(buy,fill)
        for edited in (replace(fill,commission=D("6")),
                       replace(fill,price=D("4500")),
                       replace(fill,quote_quantity=D("10021"))):
            with self.subTest(fill=edited):
                with self.assertRaisesRegex(OwnershipConflict,"RECEIPT_CONFLICT"):
                    self.join("flokiidr",(buy,),(edited,))

    def test_existing_local_receipt_corruption_is_a_reject(self):
        buy,fill=sample()
        self.write(buy,fill)
        with sqlite3.connect(self.p) as db:
            db.execute("UPDATE m06_scoped_rebuy_buy_observations "
                       "SET receipt_sha256=? WHERE trade_id=?",
                       ("0"*64,fill.trade_id))
        with self.assertRaisesRegex(OwnershipConflict,"RECEIPT_CONFLICT"):
            self.join("flokiidr",(buy,),(fill,))

    def test_missing_local_receipt_not_proven_and_never_releases_hold(self):
        sell,sf=sample(side="SELL",trade="sell-01",oid="sell-01",
            quote="30000",price="10000",qty="3")
        self.write(sell,sf)
        buy,bf=sample()
        summary=self.join("flokiidr",(sell,buy),(sf,bf))
        self.assertEqual(summary["matched_local_manual_sell_receipts"],1)
        self.assertEqual(summary["missing_local_buy_receipts"],1)
        self.assertFalse(summary["exchange_settlement_verified"])
        self.assertFalse(summary["rebuy_hold_release_allowed"])
        self.assertEqual(self.ledger.review_protected_rebuy(
            account_key=self.account_key).floki_idr_local_protected,D("30000"))

    def test_legacy_ledger_without_optional_tables_remains_read_only(self):
        self.ledger.protect_manual_asset_floor(
            account_key=self.account_key,asset="FLOKI",minimum_qty=D("0"))
        buy,bf=sample()
        report=self.join("flokiidr",(buy,),(bf,))
        self.assertEqual(report["missing_local_buy_receipts"],1)
        with sqlite3.connect(self.p) as db:
            self.assertIsNone(db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='m06_scoped_rebuy_buy_observations'"
            ).fetchone())

    def test_foreign_account_uid_cannot_consume_local_matching_proof(self):
        buy,fill=sample()
        self.write(buy,fill)
        with self.assertRaises(OwnershipConflict):
            self.join("flokiidr",(buy,),(fill,),UID+1)

    def test_unlinked_unknown_side_duplicate_orders_fills_rejected(self):
        buy,fill=sample()
        self.write(buy,fill)
        for orders,fills in (
            ((buy,buy),(fill,)),
            ((buy,),(fill,fill)),
            ((buy,),(replace(fill,is_buyer=None),)),
            ((buy,),(replace(fill,order_id="other"),)),
            ((buy,),(replace(fill,client_order_id="unrelated"),)),
            ((buy,),(replace(fill,commission_asset=None),)),
        ):
            with self.subTest(orders=orders,fills=fills):
                with self.assertRaises(OwnershipConflict):
                    self.join("flokiidr",orders,fills)

    def test_reviewer_only_opened_ro_and_never_changes_sqlite_data(self):
        buy,fill=sample()
        self.write(buy,fill)
        before=self.p.read_bytes()
        r1=self.join("flokiidr",(buy,),(fill,))
        r2=self.join("flokiidr",(buy,),(fill,))
        self.assertEqual(r1,r2)
        self.assertEqual(before,self.p.read_bytes())
        self.assertFalse(r1["source_authentication_verified"])

    def test_no_existing_db_is_not_created_by_original_caller(self):
        buy,fill=sample()
        missing=Path(self.t.name)/"no-m06.sqlite3"
        with self.assertRaises(OwnershipConflict):
            self.app.review_m06_special_source_receipts_offline(
                db_path=str(missing),account_uid=UID,symbol="flokiidr",
                orders=(buy,),fills=(fill,))
        self.assertFalse(missing.exists())

    def test_sell_and_buy_opposite_receipt_directions_conflict(self):
        buy,fill=sample()
        self.write(buy,fill)
        mistaken=replace(buy,side="SELL")
        mistaken_fill=replace(fill,is_buyer=False)
        with self.assertRaisesRegex(OwnershipConflict,"CROSS_SIDE_COLLISION"):
            self.join("flokiidr",(mistaken,),(mistaken_fill,))


if __name__=="__main__":
    unittest.main()
