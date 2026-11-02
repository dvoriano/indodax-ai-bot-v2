"""Original V2 M03 per-candidate public quote/depth friction, no trading.

All book values are synthetic, never market prices. The genuine original
Deep callback and bulk orchestrator are traversed with local fakes only.
"""
from __future__ import annotations

from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.deep_analysis import (
    DeepCandidate, observe_public_quote_friction,
)
from indodax_ai_bot.contracts import MarketSnapshot, SourceReceipt
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.public_market import Candle, PublicObservation


def public_observation(
    *, ticker_bid="99", ticker_ask="100",
    bids=(("99", "2000"), ("98", "2000")),
    asks=(("100", "2000"), ("101", "2000")),
):
    source = SourceReceipt("btc_idr", 1800000060, 1800000070,
                           "INDODAX_PUBLIC_OHLC_V2_UNATTESTED", False)
    candle = Candle(1800000000, 60, D("99"), D("100"),
                    D("98"), D("99"), D("1"))
    five = Candle(1799999700, 300, D("99"), D("100"),
                  D("98"), D("99"), D("5"))
    fifteen = Candle(1799999100, 900, D("99"), D("100"),
                     D("98"), D("99"), D("15"))
    return PublicObservation(
        pair="btc_idr",
        snapshot=MarketSnapshot(source, D(ticker_bid), D(ticker_ask)),
        candle_1m=candle,
        candle_5m=five,
        candle_15m=fifteen,
        trade_count_visible=10,
        trade_history_complete=False,
        depth_levels_visible=len(bids) + len(asks),
        ready=False,
        reason="G1_PROVENANCE_AND_TRADE_COVERAGE_UNVERIFIED",
        bid_book_levels=tuple((D(p), D(q)) for p, q in bids),
        ask_book_levels=tuple((D(p), D(q)) for p, q in asks),
    )


class M03PublicQuoteFrictionTests(unittest.TestCase):
    def test_observed_book_walk_and_separate_assumed_fee_no_double_spread(self):
        report = observe_public_quote_friction(public_observation())
        self.assertEqual(report["status"], "VISIBLE_BOOK_WALK_ILLUSTRATIVE_ONLY")
        self.assertEqual(report["illustrative_not_owned_idr"], "100000")
        self.assertEqual(report["observed_ticker_spread_bps"], "100.00")
        self.assertEqual(report["book_walk_immediate_exit_before_fees_idr"],
                         "-1000.00")
        self.assertEqual(report["illustrative_immediate_bid_proceeds_idr"],
                         "99000")
        scenario = report["fee_and_slippage_scenarios"]
        self.assertEqual(scenario["LOW_ASSUMED"][
            "hypothetical_immediate_exit_after_assumed_cost_idr"],
            "-1597.00")
        self.assertTrue(scenario["LOW_ASSUMED"][
            "spread_or_book_impact_already_included"])
        self.assertEqual(scenario["BASE_ASSUMED"][
            "additional_slippage_each_side_bps_assumed"], "25")
        self.assertFalse(report["fees_all_in_account_verified"])
        self.assertFalse(report["net_roi_verified"])
        self.assertFalse(report["g1_verified"])
        self.assertFalse(report["execution_allowed"])
        self.assertEqual(report["eligible_live_candidates"], 0)

    def test_insufficient_bid_or_ask_never_invents_roundtrip_profit(self):
        for quote, expected in (
            (public_observation(asks=(("100", "1"),)),
             "VISIBLE_ASK_DEPTH_INSUFFICIENT"),
            (public_observation(bids=(("99", "1"),)),
             "VISIBLE_BID_DEPTH_INSUFFICIENT"),
        ):
            with self.subTest(expected=expected):
                report = observe_public_quote_friction(quote)
                self.assertEqual(report["status"], expected)
                self.assertIsNone(report[
                    "book_walk_immediate_exit_before_fees_idr"])
                self.assertIsNone(report["fee_and_slippage_scenarios"])
                self.assertFalse(report["execution_allowed"])

    def test_tiny_base_quantity_is_not_mistaken_for_zero_or_covered(self):
        # 100k IDR / 1e24 IDR per coin is only 1e-19 base units.
        # A fixed absolute 1e-12 epsilon would incorrectly count this
        # meaningful quantity as zero and fabricate a total loss.
        quote = public_observation(
            ticker_bid="999999999999999999999999",
            ticker_ask="1000000000000000000000000",
            bids=(("999999999999999999999999", "1"),),
            asks=(("1000000000000000000000000", "1"),),
        )
        report = observe_public_quote_friction(quote)
        self.assertEqual(report["status"], "VISIBLE_BOOK_WALK_ILLUSTRATIVE_ONLY")
        self.assertEqual(report["illustrative_base_qty_from_asks"],
                         "1E-19")
        self.assertGreater(
            D(report["illustrative_immediate_bid_proceeds_idr"]), D("99999")
        )
        self.assertFalse(report["execution_allowed"])

    def test_stale_invalid_cross_ticker_depth_and_no_book_fail_closed(self):
        for quote, expected in (
            (public_observation(ticker_bid="95"),
             "TICKER_BOOK_QUOTE_MISMATCH"),
            (public_observation(bids=(("101", "2000"),)),
             "CROSSED_PUBLIC_BOOK"),
            (public_observation(bids=()),
             "PUBLIC_BOOK_DEPTH_UNAVAILABLE"),
            (public_observation(asks=(("100", "2000"), ("99", "5"))),
             "UNORDERED_PUBLIC_BOOK"),
            (public_observation(asks=(("100", "NaN"),)),
             "INVALID_PUBLIC_BOOK_LEVEL"),
        ):
            with self.subTest(expected=expected):
                report = observe_public_quote_friction(quote)
                self.assertEqual(report["status"], expected)
                self.assertFalse(report["execution_allowed"])
                self.assertIsNone(report["fee_and_slippage_scenarios"])

    def test_original_orchestrator_deep_callback_attaches_real_observation(self):
        with TemporaryDirectory() as d:
            journal = DecisionJournal(Path(d)/"audit.sqlite3")
            bot = DailyScalpingOrchestrator(Settings(), journal)
            candidate = DeepCandidate("btc_idr", 75, "PUBLIC_REVIEW", None)
            quote = public_observation()
            with patch.object(journal, "record_and_compare_public_wall",
                              return_value={"status": "UNATTESTED"}) as wall:
                result = bot._attach_public_wall_experience(candidate, quote)
            wall.assert_called_once()
            self.assertIsNone(candidate.public_quote_friction_diagnostic)
            self.assertEqual(result.wall_temporal_evidence["status"],
                             "UNATTESTED")
            proof = result.summary()["public_quote_friction_diagnostic"]
            self.assertEqual(proof["pair"], "btc_idr")
            self.assertEqual(proof["closed_1m_epoch"], 1800000060)
            self.assertEqual(proof["status"],
                             "VISIBLE_BOOK_WALK_ILLUSTRATIVE_ONLY")
            self.assertFalse(proof["fees_all_in_account_verified"])
            self.assertFalse(proof["execution_allowed"])

    def test_existing_bulk_mainpath_only_attaches_cost_to_public_candidate(self):
        with TemporaryDirectory() as d:
            journal = DecisionJournal(Path(d)/"journal.sqlite3")
            bot = DailyScalpingOrchestrator(Settings(), journal)
            original = DeepCandidate("btc_idr", 70, "PUBLIC_REVIEW", None)
            observed = public_observation()
            calls = []

            def fake_bulk(_market, _screening, *, limit, on_candidate):
                self.assertEqual(limit, 20)
                reviewed = on_candidate(original, observed)
                calls.append(reviewed)
                return {
                    "g1_source_admission": {"qualified": False},
                    "provisional_market_evidence": [reviewed.summary()],
                    "g1_verified": False,
                    "source_trust_verified": False,
                    "trade_history_complete": False,
                    "execution_allowed": False,
                }

            with (
                patch.object(bot, "_scan_with_early_momentum",
                             return_value=object()),
                patch("indodax_ai_bot.orchestrator.bulk_public_deep_scan",
                      side_effect=fake_bulk) as bulk,
                patch("indodax_ai_bot.orchestrator.ThreeRoleShadow.run",
                      return_value={"status": "G1_UNQUALIFIED",
                                    "completed_roles": [],
                                    "execution_allowed": False}),
                patch.object(journal, "record_and_compare_public_wall",
                             return_value={"status": "UNATTESTED"}),
            ):
                cycle = bot.run_bulk_deep_cycle(
                    "m03-public-quote-original-1", market=object(),
                    deep_limit=20,
                )
            bulk.assert_called_once()
            self.assertEqual(len(calls), 1)
            proof = cycle.bulk["provisional_market_evidence"][0][
                "public_quote_friction_diagnostic"]
            self.assertEqual(proof["status"],
                             "VISIBLE_BOOK_WALK_ILLUSTRATIVE_ONLY")
            self.assertFalse(cycle.execution_allowed)
            self.assertFalse(cycle.bulk["execution_allowed"])
            self.assertEqual(cycle.ai_shadow["completed_roles"], [])


if __name__ == "__main__":
    unittest.main()
