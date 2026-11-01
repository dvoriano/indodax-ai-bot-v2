"""M06 cross-source original private boundary, all mocked, no account network."""
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from indodax_ai_bot.m06_private_payloads import (
    FillRecord, OrderRecord, parse_fills, parse_open_orders,
)
from indodax_ai_bot.m06_source_consistency import (
    M06ConsistencyBlocked, inspect_cross_source_consistency,
)
from indodax_ai_bot.m06_scoped_https import (
    M06RequestBudget, M06ScopedApproval, M06ScopeDenied,
    run_scoped_private_probe,
)


def _order(*, closed=False):
    return OrderRecord(
        "btcidr-limit-6423" if closed else "6423", "btcidr", "BUY",
        "FILLED" if closed else "PARTIALLY_FILLED",
        Decimal("1"), Decimal("0.5"), "client-123",
        full_order_id=None if closed else "btcidr-limit-6423",
    )


def _fill(*, client="client-123", buyer=True, qty="0.5"):
    return FillRecord(
        "trade-1", "btcidr-limit-6423", "btcidr",
        Decimal(qty), Decimal("100000"), Decimal("100"),
        client_order_id=client, is_buyer=buyer,
        quote_quantity=Decimal("50000"), commission_asset="idr",
    )


class M06ConsistencyTests(unittest.TestCase):
    def test_numeric_open_id_resolves_by_full_order_id_without_ownership(self):
        review = inspect_cross_source_consistency(
            open_orders=(_order(),), closed_orders=(), fills=(_fill(),),
            symbol="btcidr")
        self.assertEqual(review.matched_fill_count, 1)
        self.assertEqual(review.unmatched_fill_count, 0)
        self.assertFalse(review.history_complete)
        self.assertFalse(review.bot_owned_fills_verified)
        self.assertFalse(review.execution_allowed)

    def test_official_open_order_fields_and_fill_metadata_parsing(self):
        opened=parse_open_orders([{
            "orderId":6423,"fullOrderId":"btcidr-limit-6423",
            "clientOrderId":"client-123","symbol":"BTCIDR","side":"BUY",
            "status":"NEW","origQty":"1","executedQty":"0.5",
        }],expected_symbol="btcidr")
        fills=parse_fills({"data":[{
            "tradeId":"trade-1","orderId":"btcidr-limit-6423",
            "clientOrderId":"client-123","symbol":"btcidr","qty":"0.5",
            "price":"100000","quoteQty":"50000","commission":"100",
            "commissionAsset":"idr","isBuyer":True,
        }]},expected_symbol="btcidr")
        self.assertEqual(opened[0].full_order_id,fills[0].order_id)
        self.assertTrue(fills[0].is_buyer)
        self.assertEqual(fills[0].quote_quantity,Decimal("50000"))
        self.assertFalse(fills[0].bot_owned_verified)

    def test_conflicts_fail_closed_and_unmatched_fills_stay_unverified(self):
        cases=(
            ((_order(),),(),(_fill(client="foreign"),),"CLIENT_ID_CONFLICT"),
            ((_order(),),(),(_fill(buyer=False),),"SIDE_CONFLICT"),
            ((_order(),),(),(_fill(qty="0.6"),),"QTY_EXCEEDS_SNAPSHOT"),
            ((_order(),),(_order(closed=True),),(),"SNAPSHOT_TIME_DRIFT"),
            ((OrderRecord("6423","btcidr","BUY","NEW",
                 Decimal("1"),Decimal("0"),"client-123"),),(),(),
             "OPEN_FULL_ORDER_ID_REQUIRED"),
        )
        for opens,history,fills,expected in cases:
            with self.subTest(expected=expected), self.assertRaisesRegex(
                    M06ConsistencyBlocked,expected):
                inspect_cross_source_consistency(
                    open_orders=opens,closed_orders=history,
                    fills=fills,symbol="btcidr")
        review=inspect_cross_source_consistency(
            open_orders=(),closed_orders=(),fills=(_fill(),),symbol="btcidr")
        self.assertEqual(review.unmatched_fill_count,1)
        self.assertFalse(review.bot_owned_fills_verified)

    def test_original_readonly_probe_checks_full_ids_and_never_promotes(self):
        now=int(time.time()*1000)
        approval=M06ScopedApproval(
            "M06_PRIVATE_READONLY_GO_20261031",
            now-1000,now+899000,20,"btcidr")
        payloads={
            "/api/v2/account":{"uid":17,"canTrade":False,
                "canWithdraw":False,"canWithdrawFiat":False,
                "balances":[{"asset":"IDR","free":"200000","locked":"5000"}]},
            "/api/v2/openOrders":[{
                "orderId":6423,"fullOrderId":"btcidr-limit-6423",
                "clientOrderId":"client-123","symbol":"BTCIDR","side":"BUY",
                "status":"NEW","origQty":"1","executedQty":"0.5"}],
            "/api/v2/order/histories":{"data":[]},
            "/api/v2/myTrades":{"data":[{
                "tradeId":"trade-1","orderId":"btcidr-limit-6423",
                "clientOrderId":"client-123","symbol":"btcidr",
                "qty":"0.5","price":"100000","quoteQty":"50000",
                "commission":"100","commissionAsset":"idr",
                "isBuyer":True}]},
        }
        with tempfile.TemporaryDirectory() as td:
            budget=M06RequestBudget(str(Path(td)/"budget.sqlite3"))
            seen=[]
            def fixture(request):
                seen.append(request.path)
                return payloads[request.path]
            with patch(
                "indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
                side_effect=fixture,
            ):
                result=run_scoped_private_probe(
                    approval=approval,budget=budget,api_key="fixture-key",
                    secret_key="fixture-secret")
            self.assertEqual(len(seen),4)
            self.assertEqual(result.open_orders_count,1)
            self.assertEqual(result.fills_count,1)
            self.assertFalse(result.execution_allowed)
            self.assertFalse(result.history_complete)
            self.assertFalse(result.bot_owned_fills_verified)

    def test_original_probe_rejects_open_order_missing_full_id(self):
        now=int(time.time()*1000)
        approval=M06ScopedApproval(
            "M06_PRIVATE_READONLY_GO_20261031",
            now-1000,now+899000,20,"btcidr")
        raw={
            "/api/v2/account":{"uid":17,"canTrade":False,
                "canWithdraw":False,"canWithdrawFiat":False,
                "balances":[{"asset":"IDR","free":"1000","locked":"0"}]},
            "/api/v2/openOrders":[{
                "orderId":6423,"symbol":"BTCIDR","clientOrderId":"v2",
                "side":"BUY","status":"NEW",
                "origQty":"1","executedQty":"0"}],
        }
        seen=[]
        with tempfile.TemporaryDirectory() as td:
            with patch(
                "indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
                side_effect=lambda req: (seen.append(req.path),raw[req.path])[1],
            ):
                with self.assertRaisesRegex(
                        M06ScopeDenied,"FULL_ORDER_ID_REQUIRED"):
                    run_scoped_private_probe(
                        approval=approval,
                        budget=M06RequestBudget(str(Path(td)/"budget.sqlite3")),
                        api_key="fixture-key",secret_key="fixture-secret")
        self.assertEqual(len(seen),2)


if __name__=="__main__":
    unittest.main()
