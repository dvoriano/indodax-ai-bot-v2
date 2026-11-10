"""Official PUBLIC WS3 trade activity and order book contracts, offline."""
import unittest

from indodax_ai_bot.m03_ws_microstructure import (
    WS3MicrostructureInvalid, parse_trade_activity_frame,
    parse_orderbook_frame, trade_channel, book_channel,
)


def trade_frame(offset=99):
    return {"result": {
        "channel": "market:trade-activity-uaiidr",
        "data": {"offset": offset, "data": [
            ["uaiidr", 1794330001, 21, "buy", 100, "10000", "100"],
            ["uaiidr", 1794330002, 22, "sell", 101, "5050", "50"],
        ]},
    }}


def book_frame(offset=91):
    return {"result": {
        "channel": "market:order-book-uaiidr",
        "data": {"offset": offset, "data": {
            "pair": "uaiidr",
            "bid": [{"price": "100", "idr_volume": "5000000",
                     "uai_volume": "50000"},
                    {"price": "99", "idr_volume": "2000000"}],
            "ask": [{"price": "101", "idr_volume": "3000000",
                     "uai_volume": "29702.97"},
                    {"price": "102", "idr_volume": "1000000"}],
        }},
    }}


class WS3MicroContractTests(unittest.TestCase):
    def test_real_documented_trade_shape_and_reported_side(self):
        r = parse_trade_activity_frame(trade_frame(), pair="uai_idr")
        self.assertEqual(r["offset"], 99)
        self.assertEqual([x["source_side"] for x in r["trades"]],
                         ["buy", "sell"])
        self.assertEqual(r["trades"][0]["source_reported_idr_amount"], "10000")
        self.assertFalse(r["aggressor_side_verified"])
        self.assertFalse(r["complete_trade_tape_verified"])
        self.assertFalse(r["orders_sent"])

    def test_real_documented_book_spread_and_top_five_depth(self):
        r = parse_orderbook_frame(book_frame(), pair="uai_idr")
        self.assertEqual(r["best_bid_idr"], "100")
        self.assertEqual(r["best_ask_idr"], "101")
        self.assertEqual(r["bid_depth_top5_idr"], "7000000")
        self.assertEqual(r["ask_depth_top5_idr"], "4000000")
        self.assertEqual(r["book_imbalance_top5_ratio"], "1.7500")
        self.assertFalse(r["exchange_timestamp_available"])
        self.assertFalse(r["execution_allowed"])

    def test_channels_are_official_and_pair_scoped(self):
        self.assertEqual(trade_channel("btc_idr"),
                         "market:trade-activity-btcidr")
        self.assertEqual(book_channel("btc_idr"),
                         "market:order-book-btcidr")
        for invalid in ("uaiusdt", "floki_idr/abc", "", "UAI_IDR"):
            with self.assertRaises(WS3MicrostructureInvalid):
                trade_channel(invalid)

    def test_rejects_wrong_pair_source_or_malformed_trade(self):
        x = trade_frame()
        x["result"]["data"]["data"][0][0] = "btcidr"
        with self.assertRaises(WS3MicrostructureInvalid):
            parse_trade_activity_frame(x, pair="uai_idr")
        x = trade_frame()
        x["result"]["data"]["data"][0][3] = "BUYER_TAKER"
        with self.assertRaises(WS3MicrostructureInvalid):
            parse_trade_activity_frame(x, pair="uai_idr")
        x = trade_frame()
        x["result"]["data"]["offset"] = True
        with self.assertRaises(WS3MicrostructureInvalid):
            parse_trade_activity_frame(x, pair="uai_idr")

    def test_malformed_or_crossed_book_is_rejected(self):
        x = book_frame()
        x["result"]["data"]["data"]["bid"][0]["price"] = "102"
        with self.assertRaises(WS3MicrostructureInvalid):
            parse_orderbook_frame(x, pair="uai_idr")
        x = book_frame()
        x["result"]["data"]["data"]["pair"] = "btcidr"
        with self.assertRaises(WS3MicrostructureInvalid):
            parse_orderbook_frame(x, pair="uai_idr")
        x = book_frame()
        x["result"]["data"]["data"]["ask"][0]["idr_volume"] = "NaN"
        with self.assertRaises(WS3MicrostructureInvalid):
            parse_orderbook_frame(x, pair="uai_idr")


if __name__ == "__main__":
    unittest.main()
