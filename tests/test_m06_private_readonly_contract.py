"""M06 official private v2 surface never opens network or accepts authority."""
import unittest
from decimal import Decimal
from indodax_ai_bot.m06_private_readonly import (
    IndodaxV2PrivateReadOnly, M06ReadOnlyBlocked, READ_ONLY_PATHS,
)


class M06PrivateReadOnlyContractTests(unittest.TestCase):
    def test_get_only_allowlist_and_no_network(self):
        client = IndodaxV2PrivateReadOnly()
        self.assertEqual(len(READ_ONLY_PATHS), 4)
        for path in READ_ONLY_PATHS:
            with self.subTest(path=path), self.assertRaisesRegex(
                    M06ReadOnlyBlocked, "NETWORK_BLOCKED"):
                client.request(method="GET", path=path)
        for method, path in (("POST", "/api/v2/order"),
                             ("DELETE", "/api/v2/order"),
                             ("POST", "/api/v2/account"),
                             ("GET", "/api/v2/order")):
            with self.subTest(method=method, path=path), self.assertRaisesRegex(
                    M06ReadOnlyBlocked, "METHOD_OR_PATH_DENIED"):
                client.request(method=method, path=path)

    def test_go_boolean_cannot_open_network(self):
        with self.assertRaisesRegex(M06ReadOnlyBlocked, "GO_NOT_IMPLEMENTED"):
            IndodaxV2PrivateReadOnly(network_go=True)

    def test_fixture_never_promotes_source_trust(self):
        samples = {
            "/api/v2/account": {},
            "/api/v2/openOrders": [],
            "/api/v2/order/histories": [],
            "/api/v2/myTrades": [],
        }
        for path, payload in samples.items():
            with self.subTest(path=path):
                result = IndodaxV2PrivateReadOnly.inspect_fixture(path, payload)
                self.assertTrue(result.response_shape_valid)
                self.assertFalse(result.authenticated)
                self.assertFalse(result.freshness_verified)
                self.assertFalse(result.complete_history)
                self.assertFalse(result.bot_owned_fills_verified)
                self.assertFalse(result.execution_allowed)

    def test_untrusted_balance_math_only(self):
        result = IndodaxV2PrivateReadOnly.inspect_idr_balances(
            free=Decimal("100"), locked=Decimal("200"))
        self.assertEqual(result["free_idr_unverified"], Decimal("100"))
        self.assertFalse(result["authenticated"])
        self.assertFalse(result["execution_allowed"])
        with self.assertRaises(ValueError):
            IndodaxV2PrivateReadOnly.inspect_idr_balances(
                free=Decimal("NaN"), locked=Decimal("0"))


if __name__ == "__main__":
    unittest.main()
