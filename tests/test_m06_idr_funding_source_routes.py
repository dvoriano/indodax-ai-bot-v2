"""Owner source-bucket policy: unlimited GENERAL IDR, exclusive FLOKI/PEPE rebuy.

Works through original M06 ledger and DailyScalpingOrchestrator. All evidence
is LOCAL OFFLINE only; not account ownership or exchange order permission.
"""
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.policy import ScalpingPolicy
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.m06_ownership_ledger import M06OwnershipLedger
from indodax_ai_bot.m06_private_payloads import (
    AccountBalances, OrderRecord, FillRecord,
)


class GeneralIDRSourcePolicyTests(unittest.TestCase):
    def setUp(self):
        self.p = ScalpingPolicy()
        self.inputs = dict(
            free_idr=D("300000"), locked_idr=D("75000"),
            floki_rebuy_idr=D("50000"), pepe_rebuy_idr=D("40000"),
            commitments_not_locked_idr=D("15000"),
        )

    def route(self, source, asset, requested, **changes):
        params = dict(self.inputs)
        params.update(changes)
        return self.p.review_idr_buy_funding_route(
            **params, funding_source=source, buy_asset=asset,
            requested_idr=D(str(requested)),
        )

    def test_all_unprotected_general_idr_is_eligible_without_principal_cap(self):
        a = self.route("MAIN", "BTC", "195000")
        self.assertEqual(a.main_available_idr, D("195000"))
        self.assertTrue(a.source_and_amount_match)
        self.assertFalse(a.source_authenticated)
        self.assertFalse(a.execution_allowed)
        self.assertFalse(a.exchange_order_authorized)
        b = self.route("MAIN", "INJ", "195001")
        self.assertFalse(b.source_and_amount_match)
        # New IDR or a settled MAIN sale grows GENERAL capacity 1:1, no
        # old fixed initial funding figure or forced swap.
        c = self.route("MAIN", "INJ", "250000",
                       free_idr=D("355000"))
        self.assertEqual(c.main_available_idr, D("250000"))
        self.assertTrue(c.source_and_amount_match)
        # Free is already exchange-net-of-locked. Locked is observation only.
        self.assertEqual(
            self.route("MAIN", "SOL", "195000",
                       locked_idr=D("290000")).main_available_idr,
            D("195000"),
        )

    def test_floki_pepe_sell_proceeds_never_pay_for_main_or_each_other(self):
        self.assertFalse(self.route("MAIN","PEPE",1000).source_and_amount_match)
        self.assertFalse(self.route("MAIN","FLOKI",1000).source_and_amount_match)
        self.assertTrue(self.route("MAIN","USDT",1000).source_and_amount_match)
        self.assertTrue(self.route("MAIN","USDC",1000).source_and_amount_match)
        # A valid MAIN funding route is not an authenticated wallet or order.
        self.assertFalse(self.route("MAIN","USDC",1000).execution_allowed)
        self.assertTrue(self.route(
            "FLOKI_REBUY","FLOKI",50000).source_and_amount_match)
        self.assertTrue(self.route(
            "PEPE_REBUY","PEPE",40000).source_and_amount_match)
        self.assertFalse(self.route(
            "FLOKI_REBUY","PEPE",30000).source_and_amount_match)
        self.assertFalse(self.route(
            "PEPE_REBUY","FLOKI",30000).source_and_amount_match)
        self.assertFalse(self.route(
            "FLOKI_REBUY","BTC",30000).source_and_amount_match)
        self.assertFalse(self.route(
            "FLOKI_REBUY","FLOKI",50001).source_and_amount_match)
        self.assertEqual(
            self.route("PEPE_REBUY","PEPE",100).selected_source_budget_idr,
            D("40000"),
        )

    def test_missing_general_balance_blocks_any_unproven_bucket_transfer(self):
        r = self.route("PEPE_REBUY", "PEPE", 20000,
                       free_idr=D("60000"))
        self.assertFalse(r.all_buckets_funded_by_free_balance)
        self.assertFalse(r.source_and_amount_match)
        self.assertEqual(r.main_available_idr, D("0"))
        self.assertFalse(r.execution_allowed)

    def test_bad_source_asset_money_or_untrusted_numeric_type_denied(self):
        for source, asset, amount, change in (
            ("MAIN", "idr", "100", {}),
            ("MAIN", "IDR", "100", {}),
            ("MAIN", "BTC", "0", {}),
            ("MAIN", "BTC", "-1", {}),
            ("MAIN", "BTC", "NaN", {}),
            ("MAIN", "BTC", "1e30", {}),
            ("OTHER", "BTC", "100", {}),
            ("PEPE_REBUY", "btc/idr", "100", {}),
            ("MAIN", "BTC", "100", {"free_idr": D("-1")}),
            ("MAIN", "BTC", "100", {"floki_rebuy_idr": D("NaN")}),
        ):
            with self.subTest(source=source, asset=asset,
                              amount=amount, change=change):
                with self.assertRaises(ValueError):
                    self.route(source, asset, amount, **change)
        with self.assertRaises(ValueError):
            self.p.review_idr_buy_funding_route(
                **self.inputs, funding_source="MAIN",
                buy_asset="BTC",requested_idr=1.0,
            )


class OriginalV2SourceRoutingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = Path(self.dir.name) / "ownership.sqlite3"
        self.ledger = M06OwnershipLedger(self.db)
        self.journal = Path(self.dir.name) / "decisions.sqlite3"
        self.app = DailyScalpingOrchestrator(
            Settings(), DecisionJournal(self.journal),
        )
        for asset, amount in (("FLOKI", "50000"), ("PEPE", "40000")):
            symbol = asset.lower() + "idr"
            oid = "local-" + asset
            cid = "v2-" + asset
            self.ledger.record_local_intent(
                account_key="acct-a", order_id=oid,
                client_id=cid, symbol=symbol, side="SELL",
            )
            order = OrderRecord(
                oid,symbol,"SELL","PARTIALLY_FILLED",D("10"),D("1"),cid,
            )
            fill = FillRecord(
                "trade-" + asset,oid,symbol,
                D("1"),D(amount),D("0"),
                client_order_id=cid,is_buyer=False,
                quote_quantity=D(amount),commission_asset="idr",
            )
            self.ledger.observe(
                account_key="acct-a",order=order,fills=(fill,),
            )

    def review(self, source, asset, requested, *,
               free="300000", account="acct-a"):
        balances = AccountBalances(
            123,(("IDR",D(free),D("75000")),),
            D(free),D("75000"),
        )
        return self.app.review_m06_idr_funding_route_offline(
            db_path=str(self.db),account_key=account,
            account=balances,funding_source=source,
            buy_asset=asset,requested_idr=D(str(requested)),
            commitments_not_locked_idr=D("10000"),
            local_shadow_commitments_idr=D("5000"),
        )

    def test_original_ledger_preserves_exact_special_proceeds_on_restart(self):
        m=self.review("MAIN","BTC","195000")
        self.assertEqual(m.main_available_idr,D("195000"))
        self.assertEqual(m.floki_rebuy_available_idr,D("50000"))
        self.assertEqual(m.pepe_rebuy_available_idr,D("40000"))
        self.assertTrue(m.source_and_amount_match)
        self.assertFalse(m.execution_allowed)
        self.assertTrue(self.review(
            "FLOKI_REBUY","FLOKI","50000").source_and_amount_match)
        self.assertFalse(self.review(
            "PEPE_REBUY","FLOKI","20000").source_and_amount_match)
        restarted=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.journal),
        )
        self.app=restarted
        after=self.review("MAIN","INJ","195000")
        self.assertEqual(m.main_available_idr,after.main_available_idr)
        self.assertFalse(after.execution_allowed)
        other=self.review("MAIN","INJ","295000",account="acct-b")
        self.assertEqual(other.main_available_idr,D("285000"))
        self.assertFalse(other.source_authenticated)
        self.assertFalse(other.execution_allowed)

    def test_new_general_idr_increases_funds_not_protected_buckets(self):
        base=self.review("MAIN","SOL", "210000")
        self.assertFalse(base.source_and_amount_match)
        grown=self.review("MAIN","SOL", "210000",free="315000")
        self.assertTrue(grown.source_and_amount_match)
        self.assertEqual(grown.main_available_idr,D("210000"))
        self.assertEqual(grown.floki_rebuy_available_idr,
                         base.floki_rebuy_available_idr)
        self.assertEqual(grown.pepe_rebuy_available_idr,
                         base.pepe_rebuy_available_idr)

    def test_no_live_rebuy_or_spend_without_exchange_provenance(self):
        p=self.review("PEPE_REBUY","PEPE","20000")
        self.assertTrue(p.source_and_amount_match)
        self.assertFalse(p.source_authenticated)
        self.assertFalse(p.exchange_order_authorized)
        self.assertFalse(p.execution_allowed)
        self.assertFalse(self.review(
            "MAIN","PEPE","20000").source_and_amount_match)


if __name__ == "__main__":
    unittest.main()
