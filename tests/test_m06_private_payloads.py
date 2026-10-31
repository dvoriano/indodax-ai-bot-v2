"""M06 strict fixture acceptance against documented Trade API V2 response keys."""
import unittest
from decimal import Decimal
from indodax_ai_bot.m06_private_payloads import (
    parse_account, parse_orders, parse_fills, M06PayloadError,
)


class M06PrivatePayloadTests(unittest.TestCase):
    def test_account_available_and_locked_are_distinct(self):
        out=parse_account({"uid":1776329213,"canTrade":False,
            "balances":[{"asset":"IDR","free":"100000","locked":"25000"},
                        {"asset":"FLOKI","free":"1000","locked":"50"}]})
        self.assertEqual(out.free_idr,Decimal("100000"))
        self.assertEqual(out.locked_idr,Decimal("25000"))
        self.assertFalse(out.exchange_authenticated)
        self.assertFalse(out.execution_allowed)

    def test_history_official_ori_qty_and_unowned_order(self):
        rows={"data":[{"orderId":"btcidr-limit-31695","clientOrderId":"manual-1",
            "symbol":"btcidr","side":"BUY","status":"CANCELLED",
            "oriQty":"0.1","executedQty":"0.02"}]}
        out=parse_orders(rows,expected_symbol="btcidr")
        self.assertEqual(out[0].original_qty,Decimal("0.1"))
        self.assertFalse(out[0].bot_owned_verified)

    def test_mytrades_official_tradeid_and_unowned_fill(self):
        rows={"data":[{"tradeId":"72057594037936570",
            "orderId":"btcidr-limit-3568","symbol":"btcidr",
            "price":"1564455","qty":"0.1","commission":"468"}]}
        out=parse_fills(rows,expected_symbol="btcidr")
        self.assertEqual(out[0].trade_id,"72057594037936570")
        self.assertFalse(out[0].bot_owned_verified)

    def test_error_duplicates_negative_and_overfilled_fail_closed(self):
        invalid=[
            lambda:parse_account({"code":-1002,"msg":"Invalid credentials"}),
            lambda:parse_account({"uid":1,"balances":[
                {"asset":"IDR","free":"1","locked":"0"},
                {"asset":"IDR","free":"1","locked":"0"}]}),
            lambda:parse_account({"uid":1,"balances":[
                {"asset":"IDR","free":"NaN","locked":"0"}]}),
            lambda:parse_orders({"data":[{"orderId":"x","symbol":"btcidr",
                "side":"BUY","status":"FILLED","oriQty":"1",
                "executedQty":"2"}]},expected_symbol="btcidr"),
            lambda:parse_fills({"data":[{"tradeId":"x","orderId":"y",
                "symbol":"ethidr","price":"1","qty":"1","commission":"0"}]},
                expected_symbol="btcidr"),
            lambda:parse_fills({"data":[]},expected_symbol="notpair"),
        ]
        for fn in invalid:
            with self.subTest(case=str(fn)),self.assertRaises(M06PayloadError):
                fn()


if __name__=="__main__":
    unittest.main()
