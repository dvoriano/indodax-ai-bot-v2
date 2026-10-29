"""M03 real public /api/pairs maintenance + symbol alias compatibility.

Maintenance is recorded for PUBLIC research, NEVER an order permission.
Suspended/unknown trading status, mismatched ID or foreign-quote symbols fail.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock
import unittest

from indodax_ai_bot.public_market import IndodaxPublicMarket, PublicDataError
from indodax_ai_bot.deep_analysis import bulk_public_deep_scan
from indodax_ai_bot.scanner import DailyPairScanner
from indodax_ai_bot.three_role_ai import ThreeRoleShadow
from indodax_ai_bot.volume_screening import apply_deep_volume_floor
from test_m03a_scanner import FakeIndodaxAll
from test_m03g_bulk import FakeBulkMarket, fake_screen


def btc(**overrides):
    x = {
        "ticker_id": "btc_idr",
        "id": "btcidr",
        "symbol": "BTCIDR",
        "base_currency": "idr",
        "traded_currency": "btc",
        "is_maintenance": "1",
        "is_market_suspended": "0",
    }
    return {**x, **overrides}


class M03PublicPairStatusCompatibilityTests(unittest.TestCase):
    def test_wallet_maintenance_is_public_research_caution_not_trade_permission(self):
        client = IndodaxPublicMarket(fetch=lambda p: [btc()])
        self.assertEqual(client.idr_pairs(), {"btc_idr": ("btcidr", "BTCIDR")})
        self.assertEqual(client.public_maintenance_pairs, frozenset({"btc_idr"}))

    def test_exchange_tradingview_alias_keeps_distinct_actual_ticker_identity(self):
        record = {
            "ticker_id": "actsol_idr",
            "id": "actsolidr",
            "symbol": "ACTIDR",
            "traded_currency": "actsol",
            "base_currency": "idr",
            "is_market_suspended": 0,
            "is_maintenance": 1,
        }
        client = IndodaxPublicMarket(fetch=lambda p: [record])
        self.assertEqual(client.idr_pairs(), {
            "actsol_idr": ("actsolidr", "ACTIDR")
        })
        self.assertIn("actsol_idr", client.public_maintenance_pairs)

    def test_suspended_unknown_and_wrong_pairs_cannot_be_discovered(self):
        records = [
            btc(ticker_id="susp_idr", id="suspidr",
                traded_currency="susp", symbol="SUSPIDR",
                is_market_suspended="1"),
            btc(ticker_id="status_idr", id="statusidr",
                traded_currency="status", symbol="STATUSIDR",
                is_market_suspended="unknown"),
            btc(id="badidr"),
            btc(symbol="BTCUSDT"),
            btc(traded_currency="eth"),
        ]
        client = IndodaxPublicMarket(fetch=lambda p: records)
        with self.assertRaisesRegex(PublicDataError, "NO_ACTIVE_IDR_PAIRS"):
            client.idr_pairs()

    def test_maintenance_unrecognized_value_remains_fail_closed(self):
        client = IndodaxPublicMarket(fetch=lambda p: [
            btc(is_maintenance="unexpected"),
        ])
        with self.assertRaisesRegex(PublicDataError, "NO_ACTIVE_IDR_PAIRS"):
            client.idr_pairs()

    def test_legacy_missing_flags_is_public_observation_only(self):
        client = IndodaxPublicMarket(fetch=lambda p: [
            btc(is_maintenance=None, is_market_suspended=None),
        ])
        self.assertEqual(len(client.idr_pairs()), 1)
        self.assertFalse(client.public_maintenance_pairs)

    def test_original_scanner_and_150m_deep_radar_remain_same_pipeline(self):
        fake = FakeIndodaxAll()
        for record in fake.pairs:
            record.update({
                "is_maintenance": "1",
                "is_market_suspended": "0",
            })
        fake.pairs[0]["symbol"] = "ALTIDR"
        fake.pairs[0]["traded_currency"] = "m000"
        for key, ticker in fake.tickers.items():
            ticker["vol_idr"] = "150000000"
        scanner = DailyPairScanner(fake.client()).scan()
        self.assertEqual(scanner.eligible_pairs, 45)
        self.assertEqual(scanner.summary()["maintenance_flagged_public_pairs"], 45)
        self.assertEqual(len(scanner.scan_context.maintenance_pair_ids), 45)
        self.assertTrue(all(x.public_maintenance_flag for x in scanner.universe))
        deep = apply_deep_volume_floor(scanner)
        self.assertEqual(len(deep.shortlist), 20)
        self.assertTrue(all(x.volume_idr_24h >= Decimal("150000000")
                            for x in deep.shortlist))
        self.assertFalse(scanner.summary()["trade_authorized"])
        self.assertEqual(fake.calls, [
            "/api/pairs", "/api/server_time", "/api/ticker_all",
        ])

    def test_deep_market_evidence_marks_maintenance_and_remains_untrusted(self):
        screening = fake_screen()
        context = replace(
            screening.scan_context,
            maintenance_pair_ids=frozenset({"btc_idr"}),
        )
        screening = replace(screening, scan_context=context)
        bulk = bulk_public_deep_scan(FakeBulkMarket(), screening, limit=20)
        btc_row = next(
            x for x in bulk["ranked_diagnostics_top10"]
            if x["pair"] == "btc_idr"
        )
        self.assertTrue(btc_row["public_maintenance_flag"])
        self.assertFalse(btc_row["market_tradability_verified"])
        self.assertFalse(bulk["g1_verified"])
        self.assertFalse(bulk["execution_allowed"])
        caller = Mock(side_effect=AssertionError("NO_REAL_AI_CALLS"))
        out = ThreeRoleShadow(caller=caller, clock=lambda: 1800000140).run(
            "maintenance-case", bulk,
        )
        self.assertEqual(out["final_decision"], "REJECT")
        self.assertEqual(out["completed_roles"], [])
        caller.assert_not_called()


if __name__ == "__main__":
    unittest.main()
