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


if __name__ == "__main__":
    unittest.main()
