"""M06 special-sale observation is a scoped fact, NEVER an account budget.

Use original signed GET private response parsers with synthetic rows only.
No historical SELL absence outside a scoped window is ever inferred.
"""
from __future__ import annotations
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m06_private_payloads import (
    OrderRecord, FillRecord,
)
from indodax_ai_bot.m06_special_sale_window import (
    observe_m06_special_sale_window, M06SpecialSaleWindowRejected,
)
from indodax_ai_bot.m06_account_coverage import (
    COVERAGE_ID, START_EPOCH_MS, SEVEN_DAYS_MS, MAX_REQUESTS,
    PAIRS, M06CoverageApproval,
)
from indodax_ai_bot.m06_scoped_https import M06RequestBudget
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


def order(asset="flokiidr", *, side="SELL"):
    return OrderRecord("ord-1",asset,side,"FILLED",
                       D("10"),D("10"),"owner-manual",
                       False,"fullorder-1")


def fill(asset="flokiidr", *, trade="fill-1", qty="10", price="100",
         quote=None, buyer=False, oid="ord-1"):
    return FillRecord(
        trade,oid,asset,D(qty),D(price),D("1"),
        False,"owner-manual",buyer,
        None if quote is None else D(quote),"idr",
    )


class M06SpecialSaleWindowUnitTests(unittest.TestCase):
    def test_floki_manual_sell_gross_idr_counted_without_local_bot_intent(self):
        result=observe_m06_special_sale_window(
            symbol="flokiidr",orders=(order(),),fills=(fill(),))
        self.assertEqual(result.asset,"FLOKI")
        self.assertEqual(result.scoped_sell_fill_count,1)
        self.assertEqual(result.scoped_sell_gross_idr,D("1000"))
        self.assertTrue(result.exact_window_sale_proceeds_observed)
        self.assertFalse(result.all_time_special_sale_history_verified)
        self.assertFalse(result.rebuy_liability_after_prior_rebuys_verified)
        self.assertFalse(result.main_spendable_idr_verified)
        self.assertFalse(result.execution_allowed)
        self.assertNotIn("owner-manual",repr(result))

    def test_pepe_multiple_partial_sell_fills_and_high_quote_conservative(self):
        result=observe_m06_special_sale_window(
            symbol="pepeidr",
            orders=(order("pepeidr"),),
            fills=(
                fill("pepeidr",trade="fill-1",qty="3",price="100",quote="360"),
                fill("pepeidr",trade="fill-2",qty="7",price="100",quote="700"),
            ))
        self.assertEqual(result.scoped_sell_fill_count,2)
        self.assertEqual(result.scoped_sell_gross_idr,D("1060"))

    def test_buy_trades_never_become_special_sale_liability(self):
        result=observe_m06_special_sale_window(
            symbol="flokiidr",orders=(order(side="BUY"),),
            fills=(fill(buyer=True),))
        self.assertEqual(result.scoped_sell_fill_count,0)
        self.assertEqual(result.scoped_sell_gross_idr,D(0))
        self.assertFalse(result.all_time_special_sale_history_verified)

    def test_orphan_or_unknown_direction_never_implies_zero_full_history(self):
        result=observe_m06_special_sale_window(
            symbol="flokiidr",orders=(order(),),
            fills=(
                fill(trade="fill-a",oid="not-in-this-window"),
                fill(trade="fill-b",buyer=None),
            ))
        self.assertEqual(result.scoped_unlinked_fill_count,1)
        self.assertEqual(result.scoped_unknown_side_fill_count,1)
        self.assertFalse(result.exact_window_sale_proceeds_observed)
        self.assertTrue(result.scope_has_unresolved_fills)
        self.assertFalse(result.main_spendable_idr_verified)
        self.assertEqual(result.scoped_sell_gross_idr,D(0))

    def test_bad_pair_duplicate_or_conflicting_side_denied(self):
        for param in (
            {"symbol":"btcidr"},
            {"orders":(order(),order())},
            {"fills":(fill(),fill())},
            {"fills":(fill(buyer=True),)},
            {"fills":(fill(quote="-5"),)},
            {"fills":(fill(qty="NaN"),)},
        ):
            with self.subTest(case=param):
                args={"symbol":"flokiidr","orders":(order(),),
                      "fills":(fill(),)}
                args.update(param)
                with self.assertRaises(M06SpecialSaleWindowRejected):
                    observe_m06_special_sale_window(**args)

    def test_no_fills_is_only_empty_scoped_sample_never_lifetime_zero(self):
        for asset in ("flokiidr","pepeidr"):
            result=observe_m06_special_sale_window(
                symbol=asset,orders=(),fills=())
            self.assertTrue(result.exact_window_sale_proceeds_observed)
            self.assertEqual(result.scoped_sell_gross_idr,D(0))
            self.assertFalse(result.all_time_special_sale_history_verified)
            self.assertFalse(result.main_spendable_idr_verified)


class M06OriginalCoverageSpecialSaleJoinTests(unittest.TestCase):
    def test_original_18_get_no_extra_request_and_bounded_sell_totals(self):
        with TemporaryDirectory() as td:
            folder=Path(td)
            end_ms=START_EPOCH_MS+SEVEN_DAYS_MS+3600000
            approval=M06CoverageApproval(
                COVERAGE_ID,end_ms,end_ms+900000,
                MAX_REQUESTS,START_EPOCH_MS,end_ms,PAIRS)
            seen=[]
            def fake_read(req):
                seen.append((req.path,req.query))
                if req.path=="/api/v2/account":
                    return {"uid":781,"canTrade":False,"canWithdraw":False,
                            "canWithdrawFiat":False,
                            "balances":[{"asset":"IDR","free":"920147","locked":"0"}]}
                if req.path=="/api/v2/openOrders":
                    return []
                pair=next((p for p in ("flokiidr","pepeidr")
                           if "symbol="+p in req.query),None)
                if pair is None:
                    return {"data":[]}
                # For the same window record is replayed across both 7-day
                # responses, dedup identical fill/order within one pair.
                if req.path=="/api/v2/order/histories":
                    return {"data":[{
                        "orderId":pair+"-ord-7",
                        "clientOrderId":"manual-owner-order",
                        "symbol":pair,"side":"SELL","status":"FILLED",
                        "oriQty":"10","executedQty":"10",
                    }]}
                if req.path=="/api/v2/myTrades":
                    return {"data":[{
                        "tradeId":pair+"-fill-99",
                        "orderId":pair+"-ord-7",
                        "clientOrderId":"manual-owner-order",
                        "symbol":pair,"isBuyer":False,
                        "qty":"10","price":"100",
                        "quoteQty":"1010" if pair=="pepeidr" else "1000",
                        "commission":"2","commissionAsset":"idr",
                    }]}
                raise AssertionError("NO_OTHER_PATHS_ALLOWED")
            with (
                patch("indodax_ai_bot.m06_scoped_https._RELEASE_APPROVAL_ID",
                      COVERAGE_ID),
                patch("indodax_ai_bot.m06_scoped_https._NETWORK_RELEASE_GO",
                      True),
                patch("indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
                      side_effect=fake_read),
                patch("indodax_ai_bot.m06_account_coverage.time.time",
                      return_value=end_ms/1000+0.5),
            ):
                result=DailyScalpingOrchestrator(
                    Settings(),DecisionJournal(folder/"decisions.sqlite3")
                ).inspect_m06_account_coverage_once(
                    approval=approval,
                    budget=M06RequestBudget(str(folder/"budget.sqlite3")),
                    api_key="synthetic-key",secret_key="synthetic-secret",
                )
            self.assertEqual(result.request_count,18)
            self.assertEqual(len(seen),18)
            self.assertEqual(
                dict(result.scoped_special_sell_gross_idr),
                {"FLOKI":"1000","PEPE":"1010"})
            self.assertEqual(
                dict(result.scoped_special_sell_fill_count),
                {"FLOKI":1,"PEPE":1})
            self.assertEqual(
                dict(result.scoped_special_unresolved_fills),
                {"FLOKI":0,"PEPE":0})
            gap=result.scoped_special_history_gaps
            self.assertEqual(
                gap["status"],"M06_SPECIAL_SALE_HISTORY_GAPS_UNRESOLVED")
            self.assertEqual(gap["queried_consecutive_windows"],2)
            self.assertEqual(gap["scoped_sell_gross_idr_observed"],
                             {"FLOKI":"1000","PEPE":"1010"})
            self.assertTrue(gap["special_sale_before_first_window_unverified"])
            self.assertTrue(gap["special_sale_after_last_window_unverified"])
            self.assertTrue(gap["rebuy_consumption_provenance_unverified"])
            self.assertFalse(gap["balance_source_funds_verified"])
            self.assertIsNone(gap["safe_general_idr_from_this_report"])
            self.assertFalse(result.full_account_history_verified)
            self.assertFalse(result.spendable_idr_verified)
            self.assertFalse(result.execution_allowed)
            self.assertNotIn("synthetic-key",repr(result))


if __name__=="__main__":
    unittest.main()
