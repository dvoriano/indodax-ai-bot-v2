"""M08-01: same M07 journal BUY fee cost -> 2/8/11% net tier preview.

No bot-owned exchange BUY exists in this project. All journal data fixtures
are ACCOUNT ONLY, all SELL economics are explicit offline scenarios.
No HTTP, BUY, SELL, CANCEL or executable position is created.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.m07_order_adapter import M07OrderDraft
from tests.test_m08_original_exchange_buy_admission import (
    ACCOUNT_A, ACCOUNT_B, CID, SYMBOL, NOW, order, fills,
)


class OriginalM08NetTierEvidenceTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db=Path(tmp.name)/"original-v2-decisions.sqlite3"
        self.bot=DailyScalpingOrchestrator(Settings(),DecisionJournal(self.db))

    def record(self, *, account=ACCOUNT_A, status="FILLED",
               qty="4", with_fees=True):
        j=self.bot.journal
        j.record_m07_authenticated_readonly_order_status(
            account_fingerprint=account,
            order=order(status=status,executed=qty),
            observed_ms=NOW*1000,
        )
        if with_fees:
            j.record_m07_exchange_readonly_fills(
                account_fingerprint=account,
                fills=fills(),observed_ms=NOW*1000+1,
            )

    def review(self, *, sell_gross="103000", sell_fee="500", slippage="100",
               qty="4", account=ACCOUNT_A, bot=None):
        return (bot or self.bot).review_m08_net_tier_exit_reference_offline(
            account_fingerprint=account,symbol=SYMBOL,client_order_id=CID,
            sell_base_qty_reference=D(qty),
            expected_sell_gross_idr_reference=D(sell_gross),
            estimated_sell_all_in_fee_idr_reference=D(sell_fee),
            incremental_execution_slippage_idr_reference=D(slippage),
        )

    def test_without_account_buy_receipts_never_mints_position(self):
        r=self.review()
        self.assertEqual(r["status"],"M08_NET_TIER_BUY_COST_SOURCE_UNAVAILABLE")
        self.assertIsNone(r["net_exit_reference"])
        self.assertEqual(r["sellable_bot_owned_base_qty"],"0")
        self.assertFalse(r["execution_allowed"])

    def test_complete_account_buy_economics_t1_net_only(self):
        self.record()
        r=self.review()
        self.assertEqual(r["buy_source_status"],
                         "M08_ACCOUNT_BUY_COST_BASIS_ONLY_NOT_BOT_OWNED")
        self.assertEqual(r["allocated_buy_cost_including_tax_idr_reference"],
                         "100200")
        self.assertEqual(r["expected_sell_net_proceeds_idr_reference"],"102400")
        self.assertEqual(r["expected_sell_net_profit_idr_reference"],"2200")
        self.assertEqual(r["reached_net_tiers_reference"],["T1"])
        self.assertEqual(r["highest_reached_net_tier_reference"],"T1")
        self.assertFalse(r["position_change_24h_used"])
        self.assertFalse(r["sell_fee_and_slippage_exchange_verified"])
        self.assertEqual(r["sellable_bot_owned_base_qty"],"0")
        self.assertFalse(r["execution_allowed"])
        self.assertFalse(r["realized_pnl_recorded"])

    def test_t2_and_t3_absolute_net_cost_basis_not_reset_each_candle(self):
        self.record()
        t2=self.review(sell_gross="110000")
        t3=self.review(sell_gross="112000")
        self.assertEqual(t2["reached_net_tiers_reference"],["T1","T2"])
        self.assertEqual(t3["reached_net_tiers_reference"],["T1","T2","T3"])
        self.assertEqual(t2["allocated_buy_cost_including_tax_idr_reference"],
                         t3["allocated_buy_cost_including_tax_idr_reference"])
        self.assertEqual(t2["sellable_bot_owned_base_qty"],"0")
        self.assertEqual(t3["sellable_bot_owned_base_qty"],"0")

    def test_partial_exit_prorates_buy_cost_and_fees_once(self):
        self.record()
        r=self.review(qty="1",sell_gross="26000",
                      sell_fee="100",slippage="50")
        self.assertEqual(r["allocated_buy_cost_including_tax_idr_reference"],
                         "25050.00")
        self.assertEqual(r["expected_sell_net_profit_idr_reference"],"800.00")
        self.assertEqual(r["reached_net_tiers_reference"],["T1"])
        self.assertFalse(r["tier_order_or_qty_reserved"])

    def test_protective_loss_remains_reviewable_without_profit_floor(self):
        self.record()
        r=self.review(sell_gross="97000")
        self.assertEqual(r["reached_net_tiers_reference"],[])
        self.assertIsNone(r["highest_reached_net_tier_reference"])
        self.assertLess(D(r["expected_sell_net_profit_idr_reference"]),0)
        self.assertFalse(r["execution_allowed"])

    def test_fee_and_slippage_counted_once_no_double_tax(self):
        self.record()
        high_cost=self.review(sell_gross="104000",sell_fee="300",
                              slippage="100")
        low_cost=self.review(sell_gross="104000",sell_fee="600",
                             slippage="200")
        self.assertEqual(high_cost["expected_sell_net_proceeds_idr_reference"],
                         "103600")
        self.assertEqual(low_cost["expected_sell_net_proceeds_idr_reference"],
                         "103200")
        self.assertEqual(
            D(high_cost["expected_sell_net_profit_idr_reference"])
            -D(low_cost["expected_sell_net_profit_idr_reference"]),D("400"))

    def test_partial_filled_account_order_without_complete_receipts_denied(self):
        self.record(status="PARTIALLY_FILLED",qty="1",with_fees=False)
        r=self.review()
        self.assertEqual(r["status"],"M08_NET_TIER_BUY_COST_SOURCE_UNAVAILABLE")
        self.assertEqual(r["buy_source_status"],
                         "M08_BUY_FILL_INCOMPLETE_OR_UNSETTLED")

    def test_other_account_cannot_borrow_cost_basis(self):
        self.record(account=ACCOUNT_B)
        r=self.review(account=ACCOUNT_A)
        self.assertEqual(r["status"],"M08_NET_TIER_BUY_COST_SOURCE_UNAVAILABLE")
        self.assertFalse(r["execution_allowed"])

    def test_invalid_quantity_fee_or_slippage_fails_closed(self):
        self.record()
        cases=(
            {"qty":"4.00000001"},{"qty":"0"},{"qty":"-1"},
            {"qty":"NaN"},{"qty":"Infinity"},
            {"sell_gross":"-1"},{"sell_fee":"-1"},
            {"slippage":"NaN"},{"sell_gross":"Infinity"},
        )
        for opts in cases:
            with self.subTest(opts=opts),self.assertRaises(ValueError):
                self.review(**opts)

    def test_synthetic_client_match_still_no_bot_owned_fill(self):
        draft=M07OrderDraft(
            symbol=SYMBOL,side="BUY",order_type="LIMIT",
            client_order_id=CID,price=D("25000"),quantity=D("4"),
        )
        self.bot.reserve_m07_offline_intent_once(
            intent_id=CID,draft=draft,observed_epoch=order().created_ms//1000,
        )
        self.record()
        r=self.review()
        self.assertFalse(r["bot_owned_position_verified"])
        self.assertFalse(r["tier_order_or_qty_reserved"])
        self.assertEqual(r["sellable_bot_owned_base_qty"],"0")

    def test_unknown_hold_does_not_promote_tier_to_sell(self):
        self.record()
        draft=M07OrderDraft(
            symbol="ETHIDR",side="BUY",order_type="LIMIT",
            client_order_id="m08tierunknownintent001",
            price=D("2000"),quantity=D("2"),
        )
        self.bot.reserve_m07_offline_intent_once(
            intent_id="m08tierunknownintent001",
            draft=draft,observed_epoch=NOW,
        )
        self.bot.simulate_m07_unknown_timeout_once(
            intent_id="m08tierunknownintent001",observed_epoch=NOW+1,
        )
        r=self.review(sell_gross="120000")
        self.assertEqual(r["original_m07_unknown_holds"],1)
        self.assertEqual(r["highest_reached_net_tier_reference"],"T3")
        self.assertFalse(r["execution_allowed"])

    def test_restart_and_parallel_queries_remain_no_order(self):
        self.record()
        restarted=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.db))
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows=list(pool.map(lambda _:self.review(bot=restarted),range(8)))
        self.assertEqual(len(rows),8)
        self.assertTrue(all(
            r["highest_reached_net_tier_reference"]=="T1"
            and not r["execution_allowed"]
            and not r["tier_order_or_qty_reserved"]
            for r in rows
        ))


if __name__=="__main__":
    unittest.main()
