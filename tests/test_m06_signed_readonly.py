"""M06 pure signing tests: exact official HMAC, bounded GET, zero HTTP."""
import hashlib
import hmac
import unittest
from indodax_ai_bot.m06_signed_readonly import prepare_signed_get, deny_network
from indodax_ai_bot.m06_private_readonly import M06ReadOnlyBlocked


class M06SignedOfflineTests(unittest.TestCase):
    def test_signed_mytrades_exact_query_and_no_authority(self):
        p = prepare_signed_get(path="/api/v2/myTrades", api_key="offline-key",
            secret_key="offline-secret", timestamp_ms=1793400000000,
            symbol="btcidr",limit=100)
        self.assertEqual(p.query, "symbol=btcidr&limit=100&timestamp=1793400000000&recvWindow=5000")
        self.assertEqual(p.headers["Sign"],hmac.new(
            b"offline-secret",p.query.encode(),hashlib.sha256).hexdigest())
        self.assertFalse(p.execution_allowed)
        self.assertNotIn("offline-secret", repr(p))
        self.assertNotIn("offline-key", repr(p))

    def test_sign_account_and_open_orders_without_order_permission(self):
        for path in ("/api/v2/account","/api/v2/openOrders"):
            with self.subTest(path=path):
                p=prepare_signed_get(path=path,api_key="k",secret_key="s",
                    timestamp_ms=1793400000000)
                self.assertTrue(p.headers["Sign"])
                self.assertFalse(p.execution_allowed)
        with self.assertRaisesRegex(M06ReadOnlyBlocked,"PATH_DENIED"):
            prepare_signed_get(path="/api/v2/order",api_key="k",secret_key="s",
                timestamp_ms=1793400000000)

    def test_history_window_and_symbol_fail_closed(self):
        args=dict(path="/api/v2/myTrades",api_key="k",secret_key="s",
            timestamp_ms=1793400000000,symbol="btcidr")
        with self.assertRaisesRegex(ValueError,"WINDOW_INVALID"):
            prepare_signed_get(**args,start_ms=100,end_ms=100+8*86400*1000)
        with self.assertRaisesRegex(ValueError,"HISTORY_LIMIT_INVALID"):
            prepare_signed_get(**args,limit=1001)
        with self.assertRaisesRegex(ValueError,"SYMBOL_INVALID"):
            prepare_signed_get(**{**args,"symbol":"btc/idr"})
        with self.assertRaisesRegex(ValueError,"TIMESTAMP_INVALID"):
            prepare_signed_get(**{**args,"timestamp_ms":0})

    def test_transport_remains_blocked(self):
        with self.assertRaisesRegex(M06ReadOnlyBlocked,"NETWORK_BLOCKED"):
            deny_network()


if __name__ == "__main__":
    unittest.main()
