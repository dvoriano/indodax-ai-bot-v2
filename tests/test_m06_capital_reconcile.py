"""M06 capital reconciliation safety: no authenticated wallet claims."""
import unittest
from decimal import Decimal as D
from indodax_ai_bot.m06_capital_reconcile import reconcile_unverified_capital
from indodax_ai_bot.m06_private_payloads import AccountBalances, OrderRecord


class M06CapitalReconcileTests(unittest.TestCase):
    def setUp(self):
        self.account=AccountBalances(1,(("IDR",D("100000"),D("90000")),),
                                      D("100000"),D("90000"))

    def test_complete_empty_open_orders_reserves_independent_holds_once(self):
        result=reconcile_unverified_capital(
            account=self.account,open_orders=(),rebuy_idr=D("20000"),
            unlocked_commitments_idr=D("10000"),local_shadow_idr=D("5000"),
            all_pairs_open_orders_complete=True)
        self.assertEqual(result.calculated_available_idr,D("65000"))
        self.assertFalse(result.source_authenticated)
        self.assertFalse(result.execution_allowed)

    def test_unpriced_buy_or_incomplete_scope_zeroes_preview(self):
        order=OrderRecord("1","btcidr","BUY","NEW",D("2"),D("0"))
        for orders,complete in (((),False),((order,),True)):
            with self.subTest(orders=orders,complete=complete):
                result=reconcile_unverified_capital(
                    account=self.account,open_orders=orders,rebuy_idr=D("0"),
                    unlocked_commitments_idr=D("0"),local_shadow_idr=D("0"),
                    all_pairs_open_orders_complete=complete)
                self.assertEqual(result.calculated_available_idr,D("0"))
                self.assertTrue(result.missing_open_order_notional)

    def test_overcommitted_never_negative(self):
        result=reconcile_unverified_capital(
            account=self.account,open_orders=(),rebuy_idr=D("120000"),
            unlocked_commitments_idr=D("0"),local_shadow_idr=D("0"),
            all_pairs_open_orders_complete=True)
        self.assertEqual(result.calculated_available_idr,D("0"))

    def test_invalid_negative_and_wrong_type_denied(self):
        for value in (D("-1"),D("NaN"),1.0):
            with self.subTest(value=str(value)),self.assertRaises(ValueError):
                reconcile_unverified_capital(
                    account=self.account,open_orders=(),rebuy_idr=value,
                    unlocked_commitments_idr=D("0"),local_shadow_idr=D("0"),
                    all_pairs_open_orders_complete=True)


if __name__=="__main__":
    unittest.main()
