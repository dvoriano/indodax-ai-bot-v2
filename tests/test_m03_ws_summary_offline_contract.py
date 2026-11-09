"""Documented WS summary contract is an OFFLINE parser, not REST bypass."""
import json
import unittest

from indodax_ai_bot.m03_ws_summary_offline_contract import (
    MarketSummaryContractError, normalize_documented_market_summary_offline,
)


def frame(rows=None):
    if rows is None:
        rows = [
            ["dogeidr", 1635134109, 3810, 3480, 3980, 3523,
             "112745093944.00000000", "30241791.15270789"],
            ["usdtidr", 1635134410, 14124, 14076, 14130, 14123,
             "194798674207.00000000", "13798116.12995762"],
        ]
    return {"result": {"channel": "market:summary-24h",
                       "data": {"data": rows, "offset": 2444948}}}


class OfflineMarketSummaryContractTests(unittest.TestCase):
    def test_official_examples_only_offline_not_trade_source(self):
        v = normalize_documented_market_summary_offline(frame())
        self.assertEqual(v["status"], "OFFLINE_PUBLIC_WS_MARKET_SUMMARY_UNATTESTED")
        self.assertEqual(v["pairs_parsed"], 2)
        self.assertEqual(v["samples"][0]["pair"], "doge_idr")
        self.assertEqual(v["samples"][1]["pair"], "usdt_idr")
        self.assertEqual(v["samples"][0]["last_idr"], "3810")
        self.assertEqual(v["samples"][0]["volume_24h_idr"], "112745093944.00000000")
        self.assertTrue(v["missing_best_bid_ask"])
        self.assertTrue(v["missing_trade_tape"])
        self.assertTrue(v["not_authorized_for_scanner"])
        self.assertTrue(v["not_authorized_for_g1_or_ai"])
        self.assertEqual(v["orders_sent"], 0)
        self.assertEqual(v["network_requests"], 0)

    def test_unknown_channel_rejected(self):
        doc = frame()
        doc["result"]["channel"] = "market:trade-activity-btcidr"
        with self.assertRaises(MarketSummaryContractError):
            normalize_documented_market_summary_offline(doc)

    def test_bad_row_shape_and_duplicate_pair_rejected(self):
        row = frame()["result"]["data"]["data"][0]
        for rows in ([row[:-1]], [row, row]):
            with self.assertRaises(MarketSummaryContractError):
                normalize_documented_market_summary_offline(frame(rows))

    def test_bad_numeric_nan_negative_timestamp_price_rejected(self):
        row = frame()["result"]["data"]["data"][0]
        variants = []
        for idx, new in [(1, -1), (2, "NaN"), (3, "NaN"),
                         (4, "Infinity"), (6, "-1"), (7, "NaN"),
                         (2, "SECRET HTTP BODY")]:
            copy = row.copy()
            copy[idx] = new
            variants.append(copy)
        for invalid in variants:
            with self.assertRaises(MarketSummaryContractError):
                normalize_documented_market_summary_offline(frame([invalid]))

    def test_trusted_pair_format_and_bounds(self):
        row = frame()["result"]["data"]["data"][0]
        for name in ["btc_usdt", "btc-idr", "../secretidr", "", "IDRIDR"]:
            invalid = row.copy()
            invalid[0] = name
            with self.assertRaises(MarketSummaryContractError):
                normalize_documented_market_summary_offline(frame([invalid]))
        with self.assertRaises(MarketSummaryContractError):
            normalize_documented_market_summary_offline(frame([row] * 801))

    def test_no_login_static_token_or_credentials_in_code_output(self):
        serialized = json.dumps(normalize_documented_market_summary_offline(frame()))
        for sensitive in ("token", "password", "auth", "API_KEY", "PRIVATE"):
            self.assertNotIn(sensitive.lower(), serialized.lower())


if __name__ == "__main__":
    unittest.main()
