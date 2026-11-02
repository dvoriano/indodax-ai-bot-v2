"""Original V2 M03 per-candidate public quote/depth friction, no trading.

All book values are synthetic, never market prices. The genuine original
Deep callback and bulk orchestrator are traversed with local fakes only.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D
from io import StringIO
from contextlib import redirect_stdout, redirect_stderr
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.__main__ import main
from indodax_ai_bot.deep_analysis import (
    DeepCandidate, observe_public_quote_friction,
    OwnerEnteredAllInFeeSnapshot,
)
from indodax_ai_bot.contracts import MarketSnapshot, SourceReceipt
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator, CycleResult
from indodax_ai_bot.public_market import Candle, PublicObservation, IndodaxPublicMarket
from indodax_ai_bot.scanner import DailyPairScanner


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


def owner_profile(*, pair="btc_idr", epoch=1800000010,
                  buy="30", sell="50"):
    return {
        "pair": pair, "buy_all_in_bps": buy, "sell_all_in_bps": sell,
        "profile_seen_epoch": epoch, "market": "IDR",
        "order_role": "TAKER",
        "source_kind": "OWNER_ENTERED_INDODAX_PROFILE_UNATTESTED",
    }


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

    def test_official_idr_pro_all_in_totals_default_to_taker(self):
        report = observe_public_quote_friction(public_observation())
        table = report["published_idr_pro_all_in_fee_table"]
        self.assertEqual(table["market"], "IDR")
        self.assertEqual(table["mode"], "PRO")
        self.assertEqual(table["published_effective_since"], "2026-10-01")
        self.assertEqual(table["buy_maker_percent_reference_only"], "0.10555")
        self.assertEqual(table["buy_taker_percent_used"], "0.20555")
        self.assertEqual(table["sell_maker_percent_reference_only"], "0.31555")
        self.assertEqual(table["sell_taker_percent_used"], "0.41555")
        self.assertTrue(table["service_tax_cfx_already_included"])
        self.assertFalse(table["component_fee_addition_allowed"])
        self.assertFalse(table["account_specific_fee_verified"])
        self.assertTrue(table["maker_execution_not_implied"])
        self.assertEqual(
            report["selected_all_in_fee_basis"],
            "INDODAX_PRO_IDR_PUBLIC_TAKER_ALL_IN_TOTAL_UNVERIFIED_ACCOUNT",
        )
        preview = report["published_idr_pro_taker_fee_sensitivity"]
        self.assertEqual(preview["buy_total_all_in_bps"], "20.55500")
        self.assertEqual(preview["sell_total_all_in_bps"], "41.55500")
        self.assertEqual(
            preview["roundtrip_taker_fee_percent_sum_reference_only"],
            "0.62110",
        )
        self.assertEqual(
            preview["modeled_immediate_net_profit_idr_unverified"],
            "-1616.94",
        )
        self.assertFalse(preview["additional_spread_fee_deduction"])
        self.assertFalse(preview["individual_fee_components_added"])
        self.assertFalse(preview["account_specific_fee_verified"])
        self.assertFalse(preview["execution_allowed"])
        self.assertFalse(report["fees_all_in_account_verified"])
        self.assertFalse(report["net_roi_verified"])
        self.assertEqual(report["eligible_live_candidates"], 0)
        self.assertFalse(report["execution_allowed"])

    def test_official_published_taker_does_not_double_count_quote_spread(self):
        report = observe_public_quote_friction(public_observation())
        # Observed ASK100/BID99 already reduces 100k -> 99k BEFORE fees.
        # Single all-in BUY 0.20555% and SELL 0.41555% reduce proceeds:
        # -1000 -205.55 -411.3945 = -1616.9445 IDR.
        net = D(report["published_idr_pro_taker_fee_sensitivity"][
            "modeled_immediate_net_profit_idr_unverified"])
        self.assertEqual(net, D("-1616.94"))
        self.assertEqual(
            report["book_walk_immediate_exit_before_fees_idr"], "-1000.00"
        )
        self.assertEqual(report["owner_entered_fee_status"], "NOT_PROVIDED")
        self.assertIsNone(report["owner_entered_fee_sensitivity"])
        self.assertFalse(report["g1_verified"])

    def test_official_total_fee_keeps_invalid_book_fail_closed(self):
        for observed in (
            public_observation(asks=(("100", "1"),)),
            public_observation(ticker_bid="95"),
            public_observation(bids=()),
        ):
            with self.subTest(observed=observed.bid_book_levels):
                report = observe_public_quote_friction(observed)
                self.assertIsNone(
                    report["published_idr_pro_taker_fee_sensitivity"]
                )
                self.assertIsNone(report["published_idr_pro_all_in_fee_table"])
                self.assertFalse(report["execution_allowed"])

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

    def test_owner_entered_fee_snapshot_not_account_verified_and_cost_math(self):
        owner = OwnerEnteredAllInFeeSnapshot.from_mapping(owner_profile())
        report = observe_public_quote_friction(
            public_observation(), owner_fee_snapshot=owner,
        )
        self.assertEqual(report["owner_entered_fee_status"],
                         "FRESH_OWNER_ENTERED_NOT_EXCHANGE_AUTHENTICATED")
        sensitivity = report["owner_entered_fee_sensitivity"]
        self.assertEqual(sensitivity["buy_all_in_bps_owner_entered"], "30")
        self.assertEqual(
            report["selected_all_in_fee_basis"],
            "OWNER_ENTERED_IDR_TAKER_ALL_IN_TOTAL_UNVERIFIED_ACCOUNT",
        )
        self.assertEqual(
            report["published_idr_pro_taker_fee_sensitivity"][
                "modeled_immediate_net_profit_idr_unverified"],
            "-1616.94",
        )
        self.assertEqual(sensitivity["sell_all_in_bps_owner_entered"], "50")
        self.assertEqual(sensitivity[
            "modeled_immediate_net_profit_idr_unverified"], "-1795.00")
        self.assertGreater(
            D(sensitivity["policy_required_future_sell_gross_idr_unverified"]),
            D("110000"),
        )
        self.assertTrue(sensitivity[
            "future_sell_liquidity_and_fill_unknown"])
        self.assertTrue(sensitivity[
            "owner_entered_is_not_authenticated_account_fee"])
        self.assertFalse(report["fees_all_in_account_verified"])
        self.assertFalse(report["net_roi_verified"])
        self.assertEqual(report["eligible_live_candidates"], 0)
        self.assertFalse(report["execution_allowed"])

    def test_manual_profile_schema_and_staleness_are_fail_closed(self):
        for bad in (
            {**owner_profile(), "sell_all_in_bps": "NaN"},
            {**owner_profile(), "buy_all_in_bps": "-1"},
            {**owner_profile(), "buy_all_in_bps": "501"},
            {**owner_profile(), "market": "USDT"},
            {**owner_profile(), "order_role": "MAKER"},
            {**owner_profile(), "source_kind": "EXCHANGE_VERIFIED"},
            {**owner_profile(), "access_token": "NOT_ALLOWED"},
            {**owner_profile(), "profile_seen_epoch": "1800000010"},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    OwnerEnteredAllInFeeSnapshot.from_mapping(bad)
        for profile, expected in (
            (owner_profile(pair="eth_idr"), "PAIR_MISMATCH"),
            (owner_profile(epoch=1799900000), "STALE_OR_FUTURE"),
            (owner_profile(epoch=1800000100), "STALE_OR_FUTURE"),
        ):
            with self.subTest(reason=expected, profile=profile):
                rpt = observe_public_quote_friction(
                    public_observation(),
                    owner_fee_snapshot=OwnerEnteredAllInFeeSnapshot.from_mapping(profile),
                )
                self.assertEqual(rpt["owner_entered_fee_status"], expected)
                self.assertIsNone(rpt["owner_entered_fee_sensitivity"])
                self.assertFalse(rpt["execution_allowed"])

    def test_original_orchestrator_passes_owner_fee_without_trading(self):
        with TemporaryDirectory() as d:
            bot = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(Path(d)/"audit.sqlite3"),
                m03_owner_fee_snapshot=OwnerEnteredAllInFeeSnapshot.from_mapping(
                    owner_profile()
                ),
            )
            quote = public_observation()
            with patch.object(
                bot.journal, "record_and_compare_public_wall",
                return_value={"status": "UNATTESTED"},
            ):
                reviewed = bot._attach_public_wall_experience(
                    DeepCandidate("btc_idr", 75, "PUBLIC_REVIEW", None),
                    quote,
                )
            report = reviewed.summary()["public_quote_friction_diagnostic"]
            self.assertEqual(
                report["owner_entered_fee_sensitivity"][
                    "modeled_immediate_net_profit_idr_unverified"], "-1795.00"
            )
            self.assertFalse(report["g1_verified"])
            self.assertFalse(report["execution_allowed"])

    def test_original_cli_fee_file_strict_permission_and_optin(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            root.chmod(0o700)
            p = root/"fees.json"
            p.write_text(json.dumps(owner_profile()))
            p.chmod(0o600)
            db = root/"decisions.sqlite3"
            outcome = CycleResult(
                "m03-cost-stdin", None, "G1_UNQUALIFIED", False,
                bulk={"coverage_status": "PARTIAL_INVALID_PAIR_EVIDENCE",
                      "execution_allowed": False}, ai_shadow={},
            )
            with patch("indodax_ai_bot.__main__.DailyScalpingOrchestrator") as App:
                App.return_value.run_bulk_deep_cycle.return_value = outcome
                out, err = StringIO(), StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    rc = main([
                        "--bulk-deep-once", "--db", str(db),
                        "--m03-owner-fee-snapshot-file", str(p),
                    ])
                self.assertEqual(rc, 0, err.getvalue())
                fee = App.call_args.kwargs["m03_owner_fee_snapshot"]
                self.assertEqual(fee.buy_all_in_bps, D("30"))
                self.assertEqual(fee.sell_all_in_bps, D("50"))
                self.assertFalse(json.loads(out.getvalue())["execution_allowed"])
            p.chmod(0o644)
            db2 = root/"forbidden.sqlite3"
            with patch("indodax_ai_bot.__main__.DailyScalpingOrchestrator") as App:
                out, err = StringIO(), StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    rc = main([
                        "--bulk-deep-once", "--db", str(db2),
                        "--m03-owner-fee-snapshot-file", str(p),
                    ])
                self.assertEqual(rc, 2)
                self.assertIn("M03_OWNER_ENTERED_FEE_FILE_NOT_ACCEPTED",
                              err.getvalue())
                self.assertFalse(db2.exists())
                App.assert_not_called()
            p.chmod(0o600)
            db3 = root/"wrong-lane.sqlite3"
            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = main([
                    "--health", "--db", str(db3),
                    "--m03-owner-fee-snapshot-file", str(p),
                ])
            self.assertEqual(rc, 2)
            self.assertIn("M03_OWNER_FEE_REQUIRES_ORIGINAL_PUBLIC_DEEP_PATH",
                          err.getvalue())
            self.assertFalse(db3.exists())

    def test_actual_original_scanner_preserves_same_public_pair_rules(self):
        """One real original scanner caller, only 3 injected public GETs."""
        clock = 1800000070
        calls = []
        def fetch(path):
            calls.append(path)
            if path == "/api/pairs":
                return [{
                    "ticker_id": "btc_idr", "id": "btcidr",
                    "symbol": "BTCIDR", "base_currency": "idr",
                    "traded_currency": "btc", "is_market_suspended": 0,
                    "is_maintenance": 0, "price_precision": "1",
                    "quantity_increment": "0.1",
                    "trade_min_base_currency": "50000",
                    "trade_min_traded_currency": "500",
                }]
            if path == "/api/server_time":
                return {"server_time": clock}
            if path == "/api/ticker_all":
                return {"tickers": {"btc_idr": {
                    "server_time": clock, "buy": "99", "sell": "100",
                    "last": "100", "high": "110", "low": "90",
                    "vol_idr": "200000000",
                }}}
            self.fail("UNEXPECTED_PUBLIC_REQUEST: " + path)
        market = IndodaxPublicMarket(fetch=fetch, clock=lambda: clock)
        screen = DailyPairScanner(market).scan()
        self.assertEqual(calls, [
            "/api/pairs", "/api/server_time", "/api/ticker_all"
        ])
        rules = screen.scan_context.pair_rule_fields["btc_idr"]
        self.assertEqual(rules["quantity_increment"], "0.1")
        self.assertEqual(rules["price_precision"], "1")
        self.assertEqual(rules["trade_min_base_currency"], "50000")
        self.assertEqual(rules["trade_min_traded_currency"], "500")
        quote = replace(public_observation(), pair_rule_fields=rules)
        report = observe_public_quote_friction(quote)
        constraints = report["public_pair_order_constraints"]
        self.assertEqual(
            constraints["status"], "PUBLIC_RULES_CONSISTENT_RESEARCH_ONLY"
        )
        self.assertEqual(constraints["min_order_idr"], "50000")
        self.assertEqual(constraints["min_order_base_qty"], "500")
        self.assertEqual(constraints["quantity_increment_base"], "0.1")
        self.assertTrue(constraints["quoted_bid_ask_on_published_price_step"])
        self.assertTrue(constraints["illustrative_qty_exactly_on_increment"])
        self.assertFalse(constraints["exchange_order_tradability_verified"])
        self.assertFalse(constraints["actual_order_precision_and_balance_verified"])
        self.assertFalse(constraints["execution_allowed"])

    def test_original_public_observe_reuses_scan_rule_metadata_without_new_get(self):
        clock = 1800000070
        requests = []
        rows = [{
            "ticker_id": "btc_idr", "id": "btcidr", "symbol": "BTCIDR",
            "base_currency": "idr", "traded_currency": "btc",
            "price_precision": "1", "quantity_increment": "0.1",
            "trade_min_base_currency": "50000",
            "trade_min_traded_currency": "500",
            "is_market_suspended": 0, "is_maintenance": 0,
        }]
        minute = [
            {"Time": 1800000000 - i*60, "Open": "99", "High": "101",
             "Low": "98", "Close": "100", "Volume": "10"}
            for i in range(35)
        ]
        fifteen = [
            {"Time": 1799999100 - i*900, "Open": "99",
             "High": "101", "Low": "98", "Close": "100", "Volume": "50"}
            for i in range(3)
        ]
        def fetch(path):
            requests.append(path)
            if path == "/api/pairs":
                return rows
            if path == "/api/server_time":
                return {"server_time": clock}
            if path == "/api/ticker_all":
                return {"tickers": {"btc_idr": {
                    "server_time": clock, "buy": "99", "sell": "100",
                    "last": "100", "high": "110", "low": "90",
                    "vol_idr": "200000000",
                }}}
            if "/tradingview/history_v2?" in path:
                return fifteen if "tf=15" in path else minute
            if path == "/api/trades/btcidr":
                return [{"date": 1800000000, "price": "100",
                         "amount": "1", "type": "buy"}]
            if path == "/api/depth/btcidr":
                return {"buy": [["99", "2000"], ["98", "2000"]],
                        "sell": [["100", "2000"], ["101", "2000"]]}
            self.fail("UNEXPECTED_PUBLIC_GET: " + path)

        market = IndodaxPublicMarket(fetch=fetch, clock=lambda: clock)
        screen = DailyPairScanner(market).scan()
        observed = market.observe("btc_idr", scan_context=screen.scan_context)
        self.assertEqual(len(requests), 7)
        self.assertEqual(requests.count("/api/pairs"), 1)
        self.assertEqual(observed.pair_rule_fields,
                         screen.scan_context.pair_rule_fields["btc_idr"])
        report = observe_public_quote_friction(observed)
        self.assertEqual(
            report["public_pair_order_constraints"]["status"],
            "PUBLIC_RULES_CONSISTENT_RESEARCH_ONLY"
        )
        self.assertEqual(report["published_idr_pro_all_in_fee_table"][
            "buy_taker_percent_used"], "0.20555")
        self.assertFalse(report["public_pair_order_constraints"][
            "exchange_order_tradability_verified"])
        self.assertFalse(report["execution_allowed"])

    def test_public_pair_rules_missing_invalid_and_quote_steps_are_fail_closed(self):
        base = {
            "price_precision": "1", "quantity_increment": "0.1",
            "trade_min_base_currency": "50000",
            "trade_min_traded_currency": "500",
            "is_market_suspended": 0, "is_maintenance": 0,
        }
        for mod, expected in (
            ({"price_precision": None}, "INVALID_PUBLIC_PRICE_OR_QTY_RULE"),
            ({"quantity_increment": "NaN"}, "INVALID_PUBLIC_PRICE_OR_QTY_RULE"),
            ({"quantity_increment": "0"}, "INVALID_PUBLIC_PRICE_OR_QTY_RULE"),
            ({"trade_min_base_currency": "-2"}, "INVALID_PUBLIC_PRICE_OR_QTY_RULE"),
            ({"is_market_suspended": 1}, "PUBLIC_MARKET_SUSPENDED_OR_UNKNOWN"),
            ({"is_market_suspended": None}, "PUBLIC_MARKET_SUSPENDED_OR_UNKNOWN"),
            ({"price_precision": "3"}, "PUBLIC_RULE_CONSTRAINT_WARNING"),
            ({"trade_min_base_currency": "150000"}, "PUBLIC_RULE_CONSTRAINT_WARNING"),
            ({"trade_min_traded_currency": "1500"}, "PUBLIC_RULE_CONSTRAINT_WARNING"),
            ({"quantity_increment": "0.3"}, "PUBLIC_RULES_QTY_ROUND_DOWN_REQUIRED"),
        ):
            with self.subTest(mod=mod):
                quote = replace(
                    public_observation(), pair_rule_fields={**base, **mod},
                )
                report = observe_public_quote_friction(quote)
                x = report["public_pair_order_constraints"]
                self.assertEqual(x["status"], expected)
                self.assertFalse(x["execution_allowed"])
                self.assertFalse(x["exchange_order_tradability_verified"])
                self.assertFalse(report["execution_allowed"])
        missing = replace(
            public_observation(), pair_rule_fields={
                k: v for k, v in base.items() if k != "quantity_increment"
            },
        )
        x = observe_public_quote_friction(missing)["public_pair_order_constraints"]
        self.assertEqual(x["status"], "MISSING_PUBLIC_RULES_UNVERIFIED")
        self.assertFalse(x["exchange_order_tradability_verified"])

    def test_original_orchestrator_includes_pair_constraints_and_all_in_fees(self):
        with TemporaryDirectory() as d:
            bot = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(Path(d)/"journal.sqlite3"),
            )
            rules = {
                "price_precision": "1", "quantity_increment": "0.1",
                "trade_min_base_currency": "50000",
                "trade_min_traded_currency": "500",
                "is_market_suspended": 0,
            }
            quote = replace(public_observation(), pair_rule_fields=rules)
            with patch.object(bot.journal, "record_and_compare_public_wall",
                              return_value={"status": "UNATTESTED"}):
                candidate = bot._attach_public_wall_experience(
                    DeepCandidate("btc_idr", 75, "PUBLIC_REVIEW", None), quote,
                )
            result = candidate.summary()["public_quote_friction_diagnostic"]
            self.assertEqual(
                result["public_pair_order_constraints"]["status"],
                "PUBLIC_RULES_CONSISTENT_RESEARCH_ONLY"
            )
            self.assertEqual(
                result["published_idr_pro_all_in_fee_table"][
                    "buy_taker_percent_used"], "0.20555"
            )
            self.assertFalse(result["public_pair_order_constraints"][
                "exchange_order_tradability_verified"])
            self.assertFalse(result["execution_allowed"])

    def test_m07_original_candidate_conservative_sell_minimum_and_tick(self):
        from dataclasses import replace
        rules = {
            "price_precision": "1", "quantity_increment": "0.1",
            "trade_min_base_currency": "50000",
            "trade_min_traded_currency": "500",
            "is_market_suspended": 0,
        }
        obs = replace(public_observation(), pair_rule_fields=rules)
        with TemporaryDirectory() as d:
            bot = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(Path(d)/"journal.sqlite3"),
            )
            with patch.object(bot.journal, "record_and_compare_public_wall",
                              return_value={"status": "UNATTESTED"}):
                candidate = bot._attach_public_wall_experience(
                    DeepCandidate("btc_idr", 75, "PUBLIC_REVIEW", None), obs,
                )
        diag = candidate.summary()["public_quote_friction_diagnostic"]
        preview = diag["public_post_round_sell_research"]
        self.assertEqual(preview["status"],
                         "PUBLIC_POST_ROUND_SELL_RESEARCH_ONLY")
        self.assertEqual(preview["sell_qty_rounded_down_before_real_buy_fees"],
                         "1000.0")
        self.assertEqual(preview["round_down_unallocated_base_qty"], "0.0")
        self.assertEqual(preview["visible_bid_book_sell_gross_idr"], "99000.0")
        self.assertTrue(preview["sell_notional_meets_public_minimum"])
        self.assertTrue(preview["minimum_idr_met_on_buy_and_sell"])
        self.assertEqual(
            D(preview["required_future_sell_limit_price_ceiled_to_tick"])
            % D("1"), D("0")
        )
        self.assertGreater(
            D(preview["required_future_sell_limit_price_ceiled_to_tick"]),
            D("100"),
        )
        self.assertTrue(preview["fee_deducted_base_asset_unknown"])
        self.assertFalse(preview["actual_post_buy_sellable_quantity_verified"])
        self.assertFalse(preview["future_sell_book_liquidity_verified"])
        self.assertFalse(preview["exchange_order_acceptance_verified"])
        self.assertFalse(preview["owner_free_spendable_idr_verified"])
        self.assertFalse(preview["execution_allowed"])
        self.assertFalse(diag["execution_allowed"])

    def test_m07_rounded_sell_can_fail_minimum_even_when_buy_passes(self):
        from dataclasses import replace
        rules = {
            "price_precision": "1", "quantity_increment": "0.3",
            "trade_min_base_currency": "99000",
            "trade_min_traded_currency": "500",
            "is_market_suspended": 0,
        }
        obs = replace(public_observation(), pair_rule_fields=rules)
        diag = observe_public_quote_friction(obs)
        rules_check = diag["public_pair_order_constraints"]
        self.assertEqual(
            rules_check["status"], "PUBLIC_RULES_QTY_ROUND_DOWN_REQUIRED"
        )
        self.assertTrue(rules_check["illustrative_idr_meets_public_minimum"])
        preview = diag["public_post_round_sell_research"]
        self.assertEqual(
            preview["status"], "PUBLIC_POST_ROUND_SELL_BELOW_MIN_IDR"
        )
        self.assertEqual(
            preview["sell_qty_rounded_down_before_real_buy_fees"], "999.9"
        )
        self.assertEqual(preview["round_down_unallocated_base_qty"], "0.1")
        self.assertEqual(preview["visible_bid_book_sell_gross_idr"], "98990.1")
        self.assertFalse(preview["sell_notional_meets_public_minimum"])
        self.assertFalse(preview["minimum_idr_met_on_buy_and_sell"])
        self.assertFalse(preview["execution_allowed"])

    def test_m07_missing_or_suspended_rules_never_authorize_sell_draft(self):
        from dataclasses import replace
        rules = {
            "price_precision": "1", "quantity_increment": "0.1",
            "trade_min_base_currency": "50000",
            "trade_min_traded_currency": "500",
            "is_market_suspended": 0,
        }
        for altered in (
            {}, {"is_market_suspended": 1}, {"quantity_increment": "NaN"},
            {"trade_min_traded_currency": "2000"},
            {"price_precision": "3"},
        ):
            with self.subTest(altered=altered):
                obs = replace(
                    public_observation(), pair_rule_fields={**rules, **altered}
                )
                if not altered:
                    obs = replace(obs, pair_rule_fields={})
                diag = observe_public_quote_friction(obs)
                preview = diag["public_post_round_sell_research"]
                self.assertEqual(preview["status"], "PUBLIC_RULES_UNQUALIFIED")
                self.assertIsNone(
                    preview["required_future_sell_limit_price_ceiled_to_tick"]
                )
                self.assertFalse(preview["execution_allowed"])

    def test_m07_explicit_owner_fee_changes_unverified_sell_target_not_go(self):
        from dataclasses import replace
        rules = {
            "price_precision": "1", "quantity_increment": "0.1",
            "trade_min_base_currency": "50000",
            "trade_min_traded_currency": "500",
            "is_market_suspended": 0,
        }
        obs = replace(public_observation(), pair_rule_fields=rules)
        public = observe_public_quote_friction(obs)
        override = OwnerEnteredAllInFeeSnapshot.from_mapping(
            owner_profile(buy="400", sell="400")
        )
        owner = observe_public_quote_friction(
            obs, owner_fee_snapshot=override
        )
        a = D(public["public_post_round_sell_research"][
            "required_future_sell_limit_price_ceiled_to_tick"])
        b = D(owner["public_post_round_sell_research"][
            "required_future_sell_limit_price_ceiled_to_tick"])
        self.assertGreater(b, a)
        self.assertFalse(owner["fees_all_in_account_verified"])
        self.assertFalse(owner["public_post_round_sell_research"][
            "exchange_order_acceptance_verified"])
        self.assertFalse(owner["execution_allowed"])

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
            self.assertEqual(
                proof["published_idr_pro_all_in_fee_table"][
                    "buy_taker_percent_used"], "0.20555"
            )
            self.assertFalse(proof["execution_allowed"])
            self.assertFalse(cycle.execution_allowed)
            self.assertFalse(cycle.bulk["execution_allowed"])
            self.assertEqual(cycle.ai_shadow["completed_roles"], [])



class M07SameCandidatePublicBridgeTests(unittest.TestCase):
    """No balance claims: same observed M03 source, original M07 hardlock."""

    def rules(self):
        return {
            "price_precision": "1", "quantity_increment": "0.1",
            "trade_min_base_currency": "10000",
            "trade_min_traded_currency": "1",
            "is_market_suspended": 0, "is_maintenance": 0,
        }

    def report(self, obs, *, pair="btc_idr", friction=None):
        from indodax_ai_bot.m07_same_candidate_public import (
            assess_m07_same_candidate_public_boundary,
        )
        if friction is None:
            friction = observe_public_quote_friction(obs)
        return assess_m07_same_candidate_public_boundary(
            candidate_pair=pair, observation=obs,
            observed_friction=friction,
        )

    def test_real_candidate_public_pair_rules_and_fee_basis_same_source(self):
        obs = replace(public_observation(), pair_rule_fields=self.rules())
        result = self.report(obs)
        self.assertEqual(result["status"],
                         "M07_PUBLIC_SOURCE_BOUND_RULES_REFERENCE_ONLY")
        self.assertEqual(result["observed_pair"], "btc_idr")
        self.assertEqual(result["closed_1m_epoch"], 1800000060)
        self.assertTrue(result["same_candidate_and_closed_minute"])
        self.assertTrue(result["same_observation_public_rule_batch"])
        self.assertTrue(result["public_quote_price_step_match"])
        self.assertTrue(result["public_market_explicitly_active"])
        self.assertEqual(
            D(result["all_in_buy_taker_bps_public_reference_only"]),
            D("20.555"))
        self.assertEqual(
            D(result["all_in_sell_taker_bps_public_reference_only"]),
            D("41.555"))
        for label in (
            "per_account_fee_verified", "m06_actual_spendable_idr_verified",
            "m06_special_rebuy_exclusions_verified",
            "g1_independent_source_verified", "m05_fresh_owner_final_verified",
            "m07_atomic_order_intent_preflight_verified",
            "limit_buy_draft_created", "order_sent", "execution_allowed",
        ):
            self.assertFalse(result[label])

    def test_cross_pair_or_stale_fee_mixing_never_admitted(self):
        obs = replace(public_observation(), pair_rule_fields=self.rules())
        valid_friction = observe_public_quote_friction(obs)
        for pair, friction in (
            ("eth_idr", valid_friction),
            ("btc_idr", {**valid_friction, "pair":"eth_idr"}),
            ("btc_idr", {**valid_friction,
                         "closed_1m_epoch":1800000000}),
            ("btc_idr", {**valid_friction,
                         "observed_epoch":1800000100}),
            ("btc_idr", {"pair":"btc_idr"}),
        ):
            with self.subTest(pair=pair, report=friction.get("pair")):
                out = self.report(obs, pair=pair, friction=friction)
                self.assertEqual(out["status"],
                                 "M07_PUBLIC_PREFLIGHT_SOURCE_UNRESOLVED")
                self.assertFalse(out["same_candidate_and_closed_minute"])
                self.assertFalse(out["execution_allowed"])

    def test_missing_or_maintenance_rules_only_public_warnings(self):
        for rules in (
            {},
            {**self.rules(), "is_maintenance":1},
            {**self.rules(), "is_market_suspended":1},
            {**self.rules(), "price_precision":"0"},
            {**self.rules(), "price_precision":"0.3"},
        ):
            with self.subTest(rules=rules):
                obs=replace(public_observation(),pair_rule_fields=rules)
                out=self.report(obs)
                self.assertNotEqual(out["status"],
                                    "M07_PUBLIC_SOURCE_BOUND_RULES_REFERENCE_ONLY")
                self.assertFalse(out["execution_allowed"])

    def test_cross_candle_and_special_asset_exclusion_do_not_promote(self):
        from indodax_ai_bot.contracts import SourceReceipt, MarketSnapshot
        base=public_observation()
        older=replace(base, snapshot=MarketSnapshot(
            SourceReceipt("btc_idr",1800000060,1800000200,
                          "INDODAX_PUBLIC_OHLC_V2_UNATTESTED",False),
            D("99"),D("100")),
            pair_rule_fields=self.rules(),
        )
        self.assertEqual(self.report(older)["status"],
                         "M07_PUBLIC_PREFLIGHT_SOURCE_UNRESOLVED")
        restricted=replace(base, pair="floki_idr", snapshot=MarketSnapshot(
            SourceReceipt("floki_idr",1800000060,1800000070,
                          "INDODAX_PUBLIC_OHLC_V2_UNATTESTED",False),
            D("99"),D("100")),pair_rule_fields=self.rules(),
        )
        out=self.report(restricted,pair="floki_idr")
        self.assertEqual(out["status"],
                         "M07_PUBLIC_PAIR_RULES_MISSING_OR_EXCLUDED")
        self.assertFalse(out["execution_allowed"])

    def test_original_callback_preserves_one_public_observation_no_order(self):
        with TemporaryDirectory() as td:
            journal=DecisionJournal(Path(td)/"decisions.sqlite3")
            app=DailyScalpingOrchestrator(Settings(), journal)
            obs=replace(public_observation(),pair_rule_fields=self.rules())
            with patch.object(journal, "record_and_compare_public_wall",
                              return_value={"status":"UNATTESTED"}) as wall:
                result=app._attach_public_wall_experience(
                    DeepCandidate("btc_idr", 75, "PUBLIC_REVIEW", None),obs)
            wall.assert_called_once()
            self.assertEqual(result.summary()[
                "m07_same_candidate_public_preflight"]["status"],
                "M07_PUBLIC_SOURCE_BOUND_RULES_REFERENCE_ONLY")
            self.assertFalse(result.summary()["trade_authorized"])
            with journal._connect() as con:
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM m07_offline_order_intents",
                ).fetchone()[0],0)

    def test_original_bulk_mainpath_includes_m07_without_credential_or_owner(self):
        with TemporaryDirectory() as td:
            journal=DecisionJournal(Path(td)/"decisions.sqlite3")
            app=DailyScalpingOrchestrator(Settings(), journal)
            candidate=DeepCandidate("btc_idr",75,"PUBLIC_REVIEW",None)
            obs=replace(public_observation(),pair_rule_fields=self.rules())

            def fake_bulk(_market,_screening,*,limit,on_candidate):
                checked=on_candidate(candidate,obs)
                return {
                    "g1_source_admission":{"qualified":False},
                    "provisional_market_evidence":[checked.summary()],
                    "g1_verified":False,"source_trust_verified":False,
                    "trade_history_complete":False,
                    "execution_allowed":False,
                }

            with (
                patch.object(app, "_scan_with_early_momentum",
                             return_value=object()),
                patch("indodax_ai_bot.orchestrator.bulk_public_deep_scan",
                      side_effect=fake_bulk),
                patch("indodax_ai_bot.orchestrator.ThreeRoleShadow.run",
                      return_value={"status":"G1_UNQUALIFIED",
                                    "completed_roles":[],
                                    "execution_allowed":False}),
                patch.object(journal, "record_and_compare_public_wall",
                             return_value={"status":"UNATTESTED"}),
            ):
                cycle=app.run_bulk_deep_cycle("m07-public-bound-g1-off",
                                              market=object(),deep_limit=20)
            evidence=cycle.bulk["provisional_market_evidence"][0][
                "m07_same_candidate_public_preflight"]
            self.assertEqual(
                evidence["status"],"M07_PUBLIC_SOURCE_BOUND_RULES_REFERENCE_ONLY")
            self.assertFalse(evidence["m06_actual_spendable_idr_verified"])
            self.assertFalse(evidence["m05_fresh_owner_final_verified"])
            self.assertFalse(cycle.execution_allowed)
            self.assertEqual(cycle.ai_shadow["completed_roles"],[])


if __name__ == "__main__":
    unittest.main()
