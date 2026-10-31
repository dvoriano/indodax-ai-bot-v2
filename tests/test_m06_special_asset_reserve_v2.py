"""M06 V2 policy: token allocation only; never authenticate account ownership."""
from decimal import Decimal as D
import unittest
from indodax_ai_bot.policy import ScalpingPolicy


class SpecialAssetReservePolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = ScalpingPolicy()

    def test_floki_max_25pct_and_75pct_reserved(self):
        report = self.policy.review_special_asset_pool(
            asset="FLOKI", total_quantity=D("1000"),
            manual_quantity=D("500"), bot_owned_quantity=D("300"))
        self.assertEqual(report.maximum_trading_quantity, D("250.00"))
        self.assertEqual(report.minimum_reserved_quantity, D("750.00"))
        self.assertEqual(report.manually_excluded_quantity, D("500"))
        self.assertFalse(report.wallet_authenticated)
        self.assertFalse(report.execution_allowed)

    def test_pepe_max_30pct_and_70pct_reserved(self):
        report = self.policy.review_special_asset_pool(
            asset="PEPE", total_quantity=D("1000"),
            manual_quantity=D("500"), bot_owned_quantity=D("200"))
        self.assertEqual(report.maximum_trading_quantity, D("200"))
        self.assertEqual(report.minimum_reserved_quantity, D("700.00"))
        self.assertFalse(report.execution_allowed)

    def test_manual_half_must_remain_outside_bot_even_if_token_total_is_large(self):
        with self.assertRaisesRegex(ValueError, "M06_MANUAL_HALF_NOT_RESERVED"):
            self.policy.review_special_asset_pool(
                asset="PEPE", total_quantity=D("1000"),
                manual_quantity=D("400"), bot_owned_quantity=D("300"))

    def test_manual_and_bot_owned_cannot_overlap(self):
        with self.assertRaisesRegex(ValueError, "M06_SPECIAL_ASSET_OWNERSHIP_OVERLAP"):
            self.policy.review_special_asset_pool(
                asset="FLOKI", total_quantity=D("1"),
                manual_quantity=D("0.8"), bot_owned_quantity=D("0.5"))

    def test_no_float_nan_negative_or_unknown_wallet_values(self):
        for bad in (D("-1"), D("NaN"), 1.0, True, None):
            with self.subTest(bad=str(bad)):
                with self.assertRaises(ValueError):
                    self.policy.review_special_asset_pool(
                        asset="PEPE", total_quantity=D("100"),
                        manual_quantity=D("0"), bot_owned_quantity=bad)

    def test_only_owner_selected_special_assets_allowed(self):
        with self.assertRaisesRegex(ValueError, "M06_SPECIAL_ASSET_UNSUPPORTED"):
            self.policy.review_special_asset_pool(
                asset="BTC", total_quantity=D("100"),
                manual_quantity=D("50"), bot_owned_quantity=D("50"))



    def test_free_idr_not_double_subtract_locked_idr(self):
        # Indodax free is already net of exchange locked funds.
        out = self.policy.review_main_capital(
            free_idr=D("120000"), locked_idr=D("50000"),
            floki_rebuy_idr=D("30000"), pepe_rebuy_idr=D("10000"),
            commitments_not_locked_idr=D("20000"))
        self.assertEqual(out.spendable_idr, D("60000"))
        self.assertEqual(out.locked_idr_observed, D("50000"))
        self.assertEqual(out.reserved_rebuy_idr, D("40000"))
        self.assertFalse(out.overcommitted)
        self.assertFalse(out.source_authenticated)
        self.assertFalse(out.execution_allowed)

    def test_idr_rebuy_reserve_and_unlocked_commitments_cannot_go_negative(self):
        out = self.policy.review_main_capital(
            free_idr=D("30000"), locked_idr=D("90000"),
            floki_rebuy_idr=D("25000"), pepe_rebuy_idr=D("15000"),
            commitments_not_locked_idr=D("5000"))
        self.assertEqual(out.spendable_idr, D("0"))
        self.assertTrue(out.overcommitted)
        self.assertFalse(out.execution_allowed)

    def test_invalid_free_locked_commitments_and_nondecimals_rejected(self):
        good = dict(
            free_idr=D("100000"), locked_idr=D("0"),
            floki_rebuy_idr=D("0"), pepe_rebuy_idr=D("0"),
            commitments_not_locked_idr=D("0"))
        for field in good:
            for invalid in (D("-1"), D("NaN"), D("Infinity"), None,
                            1, True, 1.0, D("1e30")):
                with self.subTest(field=field, invalid=str(invalid)):
                    case = dict(good)
                    case[field] = invalid
                    with self.assertRaisesRegex(ValueError, "M06_INVALID_CAPITAL_DECIMAL"):
                        self.policy.review_main_capital(**case)

    def test_zero_free_idr_never_implies_authorized_account(self):
        out = self.policy.review_main_capital(
            free_idr=D("0"), locked_idr=D("0"),
            floki_rebuy_idr=D("0"), pepe_rebuy_idr=D("0"),
            commitments_not_locked_idr=D("0"))
        self.assertEqual(out.spendable_idr, D("0"))
        self.assertFalse(out.source_authenticated)
        self.assertFalse(out.execution_allowed)


if __name__ == "__main__":
    unittest.main()
