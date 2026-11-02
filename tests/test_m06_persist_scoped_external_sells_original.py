"""M06 original V2 UID-bound persistent external special SELL source tests.

Synthetic parsed View Only rows ONLY. No real private GET, live order,
new SQLite database namespace, V1 or user account identifiers.
"""
from __future__ import annotations
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.m06_private_payloads import OrderRecord, FillRecord
from indodax_ai_bot.m06_ownership_ledger import (
    M06OwnershipLedger, OwnershipConflict,
)
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.policy import ScalpingPolicy

UID=7891


def source(asset="flokiidr", *, trade="synthetic-trade-7",
           side="SELL", quote="30000", qty="10", price="3000",
           client="manual-1", oid="original-external-order-2",
           buyer=None):
    if buyer is None:
        buyer = side == "BUY"
    order=OrderRecord(
        oid,asset,side,"FILLED",D(qty),D(qty),client,False,
        "complete-external-id")
    fill=FillRecord(
        trade,oid,asset,D(qty),D(price),D("50"),
        False,client,buyer,D(quote),"idr")
    return (order,),(fill,)


class M06PersistentScopedSpecialSellTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"m06-v2-ownership.sqlite3"
        self.ledger=M06OwnershipLedger(self.path)
        self.path.chmod(0o600)
        self.key=M06OwnershipLedger.account_scope_from_exchange_uid(UID)

    def put(self, asset="flokiidr", **kw):
        orders,fills=source(asset,**kw)
        return self.ledger.observe_scoped_external_special_sells_offline(
            account_uid=UID,symbol=asset,orders=orders,fills=fills,
        )

    def test_insecure_ledger_refuses_persisting_private_sell_ids(self):
        orders,fills=source()
        self.path.chmod(0o644)
        with self.assertRaisesRegex(OwnershipConflict,"LEDGER_INSECURE"):
            self.ledger.observe_scoped_external_special_sells_offline(
                account_uid=UID,symbol="flokiidr",
                orders=orders,fills=fills)
        self.path.chmod(0o600)
        self.assertEqual(self.ledger.review_protected_rebuy(
            account_key=self.key).distinct_sell_fills,0)

    def test_missing_existing_db_never_automatically_recreated_by_import(self):
        other=Path(self.tmp.name)/"missing-special-ledger.sqlite3"
        existing=M06OwnershipLedger(other)
        other.unlink()
        orders,fills=source()
        with self.assertRaisesRegex(OwnershipConflict,"LEDGER_INSECURE"):
            existing.observe_scoped_external_special_sells_offline(
                account_uid=UID,symbol="flokiidr",
                orders=orders,fills=fills)
        self.assertFalse(other.exists())

    def test_manual_sell_is_protected_same_asset_no_bot_intent(self):
        out=self.put()
        self.assertEqual(out.floki_idr_local_protected,D("30000"))
        self.assertEqual(out.pepe_idr_local_protected,D("0"))
        self.assertEqual(out.locally_observed_external_special_sell_fills,1)
        self.assertEqual(out.distinct_sell_fills,1)
        self.assertFalse(out.exchange_proceeds_verified)
        self.assertFalse(out.main_capital_spending_allowed)
        self.assertFalse(out.execution_allowed)

    def test_pepe_sell_is_separate_from_floki_and_general_idr_unlimited(self):
        self.put()
        self.put("pepeidr",trade="pepe-trade-1",oid="pepe-order-1",
                 quote="5000",qty="10",price="500")
        review=self.ledger.review_protected_rebuy(account_key=self.key)
        self.assertEqual(review.floki_idr_local_protected,D("30000"))
        self.assertEqual(review.pepe_idr_local_protected,D("5000"))
        self.assertEqual(review.distinct_sell_fills,2)
        general=ScalpingPolicy().review_main_capital(
            free_idr=D("90000"),locked_idr=D("0"),
            floki_rebuy_idr=review.floki_idr_local_protected,
            pepe_rebuy_idr=review.pepe_idr_local_protected,
            commitments_not_locked_idr=D("0"),
        )
        self.assertEqual(general.spendable_idr,D("55000"))
        self.assertFalse(general.execution_allowed)

    def test_exact_replay_across_restart_never_double_counts(self):
        self.put()
        again=M06OwnershipLedger(self.path)
        orders,fills=source()
        report=again.observe_scoped_external_special_sells_offline(
            account_uid=UID,symbol="flokiidr",orders=orders,fills=fills)
        self.assertEqual(report.total_idr_local_protected,D("30000"))
        self.assertEqual(report.locally_observed_external_special_sell_fills,1)

    def test_cross_account_same_trade_id_conflict_and_isolation(self):
        self.put()
        orders,fills=source()
        with self.assertRaisesRegex(OwnershipConflict,"REPLAY_CONFLICT"):
            self.ledger.observe_scoped_external_special_sells_offline(
                account_uid=UID+1,symbol="flokiidr",
                orders=orders,fills=fills)
        report=self.ledger.review_protected_rebuy(account_key=self.key)
        self.assertEqual(report.total_idr_local_protected,D("30000"))
        self.assertEqual(self.ledger.review_protected_rebuy(
            account_key=M06OwnershipLedger.account_scope_from_exchange_uid(
                UID+1)).distinct_sell_fills,0)

    def test_changed_fee_quantity_or_same_id_different_pair_denied(self):
        self.put()
        orders,fills=source()
        for changed in (
            (orders,(replace(fills[0],commission=D("55")),)),
            (orders,(replace(fills[0],quantity=D("11")),)),
        ):
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(OwnershipConflict,"REPLAY_CONFLICT"):
                    self.ledger.observe_scoped_external_special_sells_offline(
                        account_uid=UID,symbol="flokiidr",
                        orders=changed[0],fills=changed[1])
        orders2,fills2=source("pepeidr")
        with self.assertRaises(OwnershipConflict):
            self.ledger.observe_scoped_external_special_sells_offline(
                account_uid=UID,symbol="pepeidr",
                orders=orders2,fills=fills2)
        self.assertEqual(self.ledger.review_protected_rebuy(
            account_key=self.key).distinct_sell_fills,1)

    def test_ambiguous_unlinked_or_missing_client_batch_fails_closed(self):
        for kwargs,changed in (
            ({},lambda o,f:(o,(replace(f[0],is_buyer=None),))),
            ({},lambda o,f:(o,(replace(f[0],order_id="missing"),))),
            ({},lambda o,f:(o,(replace(f[0],client_order_id="foreign"),))),
            ({},lambda o,f:((replace(o[0],client_order_id=None),),f)),
        ):
            with self.subTest():
                orders,fills=source(**kwargs)
                orders,fills=changed(orders,fills)
                with self.assertRaises(OwnershipConflict):
                    self.ledger.observe_scoped_external_special_sells_offline(
                        account_uid=UID,symbol="flokiidr",
                        orders=orders,fills=fills)
        self.assertEqual(self.ledger.review_protected_rebuy(
            account_key=self.key).distinct_sell_fills,0)

    def test_buys_never_release_any_special_sale_hold(self):
        self.put()
        orders,fills=source(side="BUY",trade="buy-1",oid="other-order-2")
        report=self.ledger.observe_scoped_external_special_sells_offline(
            account_uid=UID,symbol="flokiidr",orders=orders,fills=fills)
        self.assertEqual(report.floki_idr_local_protected,D("30000"))
        self.assertEqual(report.locally_observed_external_special_sell_fills,1)

    def test_overlap_existing_local_bot_sell_is_not_counted_twice(self):
        self.ledger.record_local_intent(
            account_key=self.key,order_id="original-external-order-2",
            client_id="manual-1",symbol="flokiidr",side="SELL")
        orders,fills=source()
        out=self.ledger.observe(account_key=self.key,
                                order=orders[0],fills=fills)
        self.assertEqual(
            self.ledger.review_protected_rebuy(
                account_key=self.key).floki_idr_local_protected,D("30000"))
        self.put()
        again=self.ledger.review_protected_rebuy(account_key=self.key)
        self.assertEqual(again.floki_idr_local_protected,D("30000"))
        self.assertEqual(again.distinct_sell_fills,1)
        self.assertEqual(again.locally_observed_external_special_sell_fills,1)

    def test_local_bot_source_same_gross_but_changed_fee_never_merges(self):
        self.ledger.record_local_intent(
            account_key=self.key,order_id="original-external-order-2",
            client_id="manual-1",symbol="flokiidr",side="SELL")
        orders,fills=source()
        self.ledger.observe(account_key=self.key,
                            order=orders[0],fills=fills)
        altered=(replace(fills[0],commission=D("51")),)
        with self.assertRaisesRegex(OwnershipConflict,
                                    "M06_EXTERNAL_OVERLAP_FILL_CONFLICT"):
            self.ledger.observe_scoped_external_special_sells_offline(
                account_uid=UID,symbol="flokiidr",orders=orders,
                fills=altered)
        again=self.ledger.review_protected_rebuy(account_key=self.key)
        self.assertEqual(again.distinct_sell_fills,1)
        self.assertEqual(again.locally_observed_external_special_sell_fills,0)

    def test_original_v2_orchestrator_and_secure_local_readonly_inspection(self):
        orders,fills=source()
        app=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(Path(self.tmp.name)/"decision.sqlite3"))
        result=app.store_m06_scoped_special_sell_observation_offline(
            db_path=str(self.path),account_uid=UID,
            symbol="flokiidr",orders=orders,fills=fills)
        self.assertEqual(result["floki_gross_proceeds_local_hold_idr"],"30000")
        self.assertFalse(result["all_time_sale_history_verified"])
        self.assertFalse(result["all_rebuy_consumption_verified"])
        self.assertFalse(result["execution_allowed"])
        self.path.chmod(0o600)
        observed=M06OwnershipLedger.inspect_existing_account_readonly(
            db_path=str(self.path),account_uid=UID)
        self.assertEqual(observed.locally_observed_external_special_sell_fills,1)
        self.assertEqual(observed.floki_idr_local_protected,D("30000"))
        self.assertFalse(observed.external_peer_coverage_verified)

    def test_atomic_batch_rejects_conflicting_second_trade_without_partial(self):
        orders,fills=source()
        f2=replace(fills[0],trade_id="second-trade",quantity=D("2"),
                   quote_quantity=D("6000"),price=D("3000"))
        self.ledger.observe_scoped_external_special_sells_offline(
            account_uid=UID,symbol="flokiidr",
            orders=orders,fills=(fills[0],f2))
        self.assertEqual(self.ledger.review_protected_rebuy(
            account_key=self.key).distinct_sell_fills,2)
        bad=replace(f2,commission=D("99"))
        extra=replace(fills[0],trade_id="new-trade",quantity=D("1"),
                      quote_quantity=D("3000"))
        with self.assertRaises(OwnershipConflict):
            self.ledger.observe_scoped_external_special_sells_offline(
                account_uid=UID,symbol="flokiidr",orders=orders,
                fills=(extra,bad))
        self.assertEqual(self.ledger.review_protected_rebuy(
            account_key=self.key).distinct_sell_fills,2)


if __name__=="__main__":
    unittest.main()
