"""M06 four-request offline transport integration, no keys leak to fixture."""
import unittest
from indodax_ai_bot.m06_transport_boundary import OfflineM06Transport, inspect_offline_private_bundle
from indodax_ai_bot.m06_private_readonly import M06ReadOnlyBlocked


class M06TransportBoundaryTests(unittest.TestCase):
    def test_all_four_offline_reads_and_zero_authority(self):
        seen=[]
        payload={
            "/api/v2/account":{"uid":1,"balances":[
                {"asset":"IDR","free":"10000","locked":"250"}]},
            "/api/v2/openOrders":[],
            "/api/v2/order/histories":{"data":[]},
            "/api/v2/myTrades":{"data":[]},
        }
        def fixture(path,query):
            seen.append((path,query))
            return payload[path]
        result=inspect_offline_private_bundle(
            transport=OfflineM06Transport(fixture),
            api_key="test-key",secret_key="test-secret",
            timestamp_ms=1793400000000,symbol="btcidr")
        self.assertEqual(len(seen),4)
        self.assertEqual(len(result.fills),0)
        self.assertFalse(result.authenticated)
        self.assertFalse(result.history_complete)
        self.assertFalse(result.execution_allowed)
        self.assertTrue(all("test-key" not in query and "test-secret" not in query
                            for _,query in seen))

    def test_arbitrary_transport_denied(self):
        with self.assertRaisesRegex(M06ReadOnlyBlocked,"NOT_AUTHORIZED"):
            inspect_offline_private_bundle(
                transport=object(),api_key="a",secret_key="b",
                timestamp_ms=1793400000000,symbol="btcidr")


if __name__=="__main__":
    unittest.main()
