"""Original M06 same-token FLOKI/PEPE rebuy BUY receipts (OFFLINE fixtures).

Source-bound local observation is NEVER settlement, history completeness,
owned token quantity, order permission or MAIN available IDR evidence.
No exchange GET or HTTP, all data synthetic and owner-UID mock.
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
from indodax_ai_bot.m06_private_payloads import OrderRecord,FillRecord
from indodax_ai_bot.m06_ownership_ledger import M06OwnershipLedger,OwnershipConflict
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator

UID=78121


def records(asset="flokiidr", *, side="BUY", oid="buy-order-1",
            tid="buy-fill-1", client="client-synthetic-1",
            quote="10020", qty="2", price="5000", fee="5",
            fee_asset="idr", buyer=None):
    if buyer is None:
        buyer=(side=="BUY")
    order=OrderRecord(oid,asset,side,"FILLED",D(qty),
        D(qty),client,False,"synthetic-full-order")
    fill=FillRecord(tid,oid,asset,D(qty),D(price),D(fee),
        False,client,buyer,D(quote),fee_asset)
    return (order,),(fill,)


class M06RebuyBuyPersistOriginalV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"original-m06-ownership.sqlite3"
        self.ledger=M06OwnershipLedger(self.path)
        self.path.chmod(0o600)
        self.key=M06OwnershipLedger.account_scope_from_exchange_uid(UID)

    def put(self, asset="flokiidr", **kwargs):
        orders,fills=records(asset,**kwargs)
        return self.ledger.observe_scoped_same_token_rebuy_buy_offline(
            account_uid=UID,symbol=asset,orders=orders,fills=fills)

    def test_buy_is_uid_bound_outflow_only_and_never_releases_sell_hold(self):
        sells,fs=records(side="SELL",oid="sell-1",tid="sell-fill",
                          quote="30000",qty="3",price="10000")
        hold=self.ledger.observe_scoped_external_special_sells_offline(
            account_uid=UID,symbol="flokiidr",orders=sells,fills=fs)
        self.assertEqual(hold.floki_idr_local_protected,D("30000"))
        result=self.put()
        self.assertEqual(result.floki_idr_local_protected,D("30000"))
        self.assertEqual(result.floki_rebuy_buy_observed_outflow_idr,D("10025"))
        self.assertEqual(result.local_rebuy_buy_observation_count,1)
        self.assertFalse(result.rebuy_consumption_settled_verified)
        self.assertFalse(result.exchange_proceeds_verified)
        self.assertFalse(result.main_capital_spending_allowed)
        self.assertFalse(result.execution_allowed)

    def test_pepe_and_floki_buy_receipts_stay_in_distinct_source_buckets(self):
        self.put()
        self.put("pepeidr",oid="pepe-order",tid="pepe-buy",quote="4000",
                 qty="2",price="2000",fee="10")
        review=self.ledger.review_protected_rebuy(account_key=self.key)
        self.assertEqual(review.floki_rebuy_buy_observed_outflow_idr,D("10025"))
        self.assertEqual(review.pepe_rebuy_buy_observed_outflow_idr,D("4010"))
        self.assertEqual(review.floki_idr_local_protected,D("0"))
        self.assertEqual(review.pepe_idr_local_protected,D("0"))
        self.assertFalse(review.rebuy_consumption_settled_verified)

    def test_exact_replay_across_restart_is_idempotent(self):
        self.put()
        reread=M06OwnershipLedger(self.path)
        orders,fills=records()
        report=reread.observe_scoped_same_token_rebuy_buy_offline(
            account_uid=UID,symbol="flokiidr",orders=orders,fills=fills)
        self.assertEqual(report.floki_rebuy_buy_observed_outflow_idr,D("10025"))
        self.assertEqual(report.local_rebuy_buy_observation_count,1)

    def test_cross_account_reuse_same_buy_fill_rejected_without_leak(self):
        self.put()
        orders,fills=records()
        with self.assertRaisesRegex(OwnershipConflict,"REPLAY_CONFLICT"):
            self.ledger.observe_scoped_same_token_rebuy_buy_offline(
                account_uid=UID+1,symbol="flokiidr",orders=orders,fills=fills)
        self.assertEqual(self.ledger.review_protected_rebuy(
            account_key=M06OwnershipLedger.account_scope_from_exchange_uid(
                UID+1)).local_rebuy_buy_observation_count,0)

    def test_changed_fee_or_price_preserving_quote_is_replay_conflict(self):
        self.put()
        orders,fills=records()
        for change in (
                replace(fills[0],commission=D("6")),
                replace(fills[0],price=D("4500"))):
            with self.subTest(change=change):
                with self.assertRaisesRegex(OwnershipConflict,"REPLAY_CONFLICT"):
                    self.ledger.observe_scoped_same_token_rebuy_buy_offline(
                        account_uid=UID,symbol="flokiidr",
                        orders=orders,fills=(change,))
        self.assertEqual(self.ledger.review_protected_rebuy(
            account_key=self.key).floki_rebuy_buy_observed_outflow_idr,D("10025"))

    def test_buy_with_fee_in_token_does_not_invent_idr_fee(self):
        self.put("flokiidr",fee_asset="floki")
        report=self.ledger.review_protected_rebuy(account_key=self.key)
        self.assertEqual(report.floki_rebuy_buy_observed_outflow_idr,D("10020"))
        self.assertFalse(report.rebuy_consumption_settled_verified)

    def test_source_mismatch_unknown_side_or_wrong_pair_fails_closed(self):
        orders,fills=records()
        cases=(
            ("pepeidr",orders,fills),
            ("flokiidr",orders,(replace(fills[0],order_id="other"),)),
            ("flokiidr",orders,(replace(fills[0],client_order_id="foreign"),)),
            ("flokiidr",orders,(replace(fills[0],is_buyer=False),)),
            ("flokiidr",orders,(replace(fills[0],is_buyer=None),)),
            ("flokiidr",orders,(replace(fills[0],commission_asset="usdt"),)),
            ("flokiidr",orders,(replace(fills[0],quantity=D("3")),)),
        )
        for sym,oo,ff in cases:
            with self.subTest(symbol=sym,fill=ff):
                with self.assertRaises(OwnershipConflict):
                    self.ledger.observe_scoped_same_token_rebuy_buy_offline(
                        account_uid=UID,symbol=sym,orders=oo,fills=ff)
        self.assertEqual(self.ledger.review_protected_rebuy(
            account_key=self.key).local_rebuy_buy_observation_count,0)

    def test_all_or_nothing_when_second_replayed_buy_conflicts(self):
        self.put()
        orders,fills=records()
        newer=replace(fills[0],trade_id="new-trade")
        changed=replace(fills[0],commission=D("7"))
        # Two 2-unit fills require the signed order's executed quantity
        # to cover 4 units; otherwise it should correctly fail earlier
        # at over-execution rather than exercising atomic replay rollback.
        larger=(replace(orders[0],original_qty=D("4"),
                        executed_qty=D("4")),)
        with self.assertRaisesRegex(OwnershipConflict,"REPLAY_CONFLICT"):
            self.ledger.observe_scoped_same_token_rebuy_buy_offline(
                account_uid=UID,symbol="flokiidr",orders=larger,
                fills=(newer,changed))
        report=self.ledger.review_protected_rebuy(account_key=self.key)
        self.assertEqual(report.local_rebuy_buy_observation_count,1)
        self.assertEqual(report.floki_rebuy_buy_observed_outflow_idr,D("10025"))

    def test_trade_id_cannot_be_reused_between_sell_and_buy_directions(self):
        sells,fs=records(side="SELL",oid="sell-1",tid="collision-1")
        self.ledger.observe_scoped_external_special_sells_offline(
            account_uid=UID,symbol="flokiidr",orders=sells,fills=fs)
        orders,fills=records(tid="collision-1")
        with self.assertRaisesRegex(OwnershipConflict,"COLLIDES_WITH_SELL"):
            self.ledger.observe_scoped_same_token_rebuy_buy_offline(
                account_uid=UID,symbol="flokiidr",orders=orders,fills=fills)
        self.assertEqual(self.ledger.review_protected_rebuy(
            account_key=self.key).local_rebuy_buy_observation_count,0)

    def test_reverse_sell_collision_against_existing_buy_is_rejected(self):
        self.put()
        sells,fs=records(side="SELL",oid="sell-1",tid="buy-fill-1")
        with self.assertRaisesRegex(OwnershipConflict,"TRADE_COLLISION"):
            self.ledger.observe_scoped_external_special_sells_offline(
                account_uid=UID,symbol="flokiidr",orders=sells,fills=fs)
        report=self.ledger.review_protected_rebuy(account_key=self.key)
        self.assertEqual(report.floki_idr_local_protected,D("0"))
        self.assertEqual(report.floki_rebuy_buy_observed_outflow_idr,D("10025"))

    def test_no_implicit_new_table_on_existing_normal_m06_startup(self):
        with sqlite3.connect(self.path) as db:
            self.assertIsNone(db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name='m06_scoped_rebuy_buy_observations'",
            ).fetchone())
        self.ledger.protect_manual_asset_floor(
            account_key=self.key,asset="FLOKI",minimum_qty=D("0"))
        inspected=M06OwnershipLedger.inspect_existing_account_readonly(
            db_path=str(self.path),account_uid=UID)
        self.assertEqual(inspected.local_rebuy_buy_observation_count,0)
        with sqlite3.connect(self.path) as db:
            self.assertIsNone(db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name='m06_scoped_rebuy_buy_observations'",
            ).fetchone())
        self.put()
        with sqlite3.connect(self.path) as db:
            self.assertIsNotNone(db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name='m06_scoped_rebuy_buy_observations'",
            ).fetchone())

    def test_original_v2_orchestrator_wiring_is_non_executable(self):
        app=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(Path(self.tmp.name)/"decisions.sqlite3"))
        orders,fills=records()
        result=app.store_m06_same_token_rebuy_buy_observation_offline(
            db_path=str(self.path),account_uid=UID,
            symbol="flokiidr",orders=orders,fills=fills)
        self.assertEqual(result["floki_rebuy_buy_observed_idr"],"10025")
        self.assertFalse(result["rebuy_consumption_settled_verified"])
        self.assertFalse(result["spendable_idr_verified"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["order_sent"])
        self.assertEqual(M06OwnershipLedger.inspect_existing_account_readonly(
            db_path=str(self.path),account_uid=UID).floki_rebuy_buy_observed_outflow_idr,
            D("10025"))

    def test_original_orchestrator_denies_missing_or_insecure_file(self):
        app=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(Path(self.tmp.name)/"decisions2.sqlite3"))
        orders,fills=records()
        missing=Path(self.tmp.name)/"no-ledger.sqlite3"
        with self.assertRaisesRegex(ValueError,"EXISTING_DB_REQUIRED"):
            app.store_m06_same_token_rebuy_buy_observation_offline(
                db_path=str(missing),account_uid=UID,
                symbol="flokiidr",orders=orders,fills=fills)
        self.assertFalse(missing.exists())
        self.path.chmod(0o644)
        with self.assertRaisesRegex(ValueError,"DB_INSECURE"):
            app.store_m06_same_token_rebuy_buy_observation_offline(
                db_path=str(self.path),account_uid=UID,
                symbol="flokiidr",orders=orders,fills=fills)
        self.path.chmod(0o600)
        self.assertEqual(self.ledger.review_protected_rebuy(
            account_key=self.key).local_rebuy_buy_observation_count,0)


if __name__=="__main__":
    unittest.main()
