"""M06: migrate V1 free-IDR protected rebuy preview; no private exchange calls.

This is a deterministic pure calculation, not authenticated spendable funds.
Do not treat a PREVIEW_ONLY return as capital authorization for BUY/SELL.
"""
from decimal import Decimal
import unittest

from indodax_ai_bot.policy import preview_main_capital_from_free_idr


class M06V1CapitalMigrationTests(unittest.TestCase):
    def preview(self, **changes):
        values = {
            "free_idr": Decimal("1000000"),
            "floki_rebuy_cash_idr": Decimal("200000"),
            "pepe_rebuy_cash_idr": Decimal("100000"),
            "other_unlocked_commitments_idr": Decimal("50000"),
        }
        values.update(changes)
        return preview_main_capital_from_free_idr(**values)

    def test_original_v1_math_is_preserved_without_adding_deposits_or_proceeds(self):
        out = self.preview()
        self.assertEqual(out.status, "PREVIEW_ONLY")
        self.assertEqual(out.preview_main_idr, Decimal("650000"))
        self.assertFalse(out.ledger_eligible)
        self.assertFalse(out.execution_allowed)
        self.assertFalse(out.private_api_called)

    def test_floki_pepe_cash_remains_reserved_for_same_asset_rebuy(self):
        result = self.preview(
            floki_rebuy_cash_idr=Decimal("500000"),
            pepe_rebuy_cash_idr=Decimal("400000"),
            other_unlocked_commitments_idr=Decimal("0"),
        )
        self.assertEqual(result.preview_main_idr, Decimal("100000"))
        self.assertFalse(result.execution_allowed)

    def test_reservations_exceeding_free_cash_reject_even_with_large_historical_profit(self):
        result = self.preview(floki_rebuy_cash_idr=Decimal("999999"),
                              pepe_rebuy_cash_idr=Decimal("100000"))
        self.assertEqual(result.status, "REJECT")
        self.assertEqual(result.preview_main_idr, Decimal("0"))

    def test_float_bool_negative_and_missing_protected_cash_fail_closed(self):
        for key, value in (
            ("free_idr", 1e6),
            ("free_idr", True),
            ("floki_rebuy_cash_idr", None),
            ("pepe_rebuy_cash_idr", "-1"),
            ("other_unlocked_commitments_idr", float("nan")),
        ):
            with self.subTest(key=key, value=value):
                out = self.preview(**{key: value})
                self.assertEqual(out.status, "REJECT")
                self.assertFalse(out.execution_allowed)

    def test_locked_exchange_idr_not_double_subtracted(self):
        # free_idr already EXCLUDES exchange locked funds.
        out = self.preview(
            free_idr="750000",
            floki_rebuy_cash_idr="100000",
            pepe_rebuy_cash_idr="0",
            other_unlocked_commitments_idr="50000",
        )
        self.assertEqual(out.preview_main_idr, Decimal("600000"))
        self.assertFalse(out.ledger_eligible)

    def test_default_zero_is_not_allowed_for_unknown_cash_buckets(self):
        with self.assertRaises(TypeError):
            preview_main_capital_from_free_idr(
                free_idr=Decimal("200000"), floki_rebuy_cash_idr=Decimal("0"),
                pepe_rebuy_cash_idr=Decimal("0"),
            )


if __name__ == "__main__":
    unittest.main()
