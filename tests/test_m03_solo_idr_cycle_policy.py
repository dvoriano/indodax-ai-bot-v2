"""Offline tests for original V2 single-position IDR strategy policy."""
from decimal import Decimal
import unittest

from indodax_ai_bot.m03_solo_idr_cycle_policy import (
    CycleFacts, candidate_quality_watch, main_idr_pair_status,
    main_available_idr_review, net_edge_policy_preview,
    next_solo_idr_cycle, candidate_fingerprint, SoloIDRPolicyInvalid,
)

NOW = 1794330000


def facts(**overrides):
    base = {
        "bot_owned_open_position_count": 0,
        "bot_owned_base_quantity": Decimal("0"),
        "pending_buy": False, "pending_sell": False,
        "sell_fills_unsettled": False, "order_status_unknown": False,
        "risk_halt_new_entries": False, "all_open_orders_verified": True,
        "candidate_count": 0, "has_fresh_idr_account_snapshot": True,
        "owner_mode": "MANUAL",
    }
    base.update(overrides)
    return CycleFacts(**base)


def candidate(pair="uai_idr", phase="EARLY_ACCELERATION_WATCH"):
    return {
        "pair": pair, "observed_epoch": NOW - 20,
        "volume_24h_idr": "450000000", "observed_rise_pct": "0.75",
        "momentum_phase": phase, "rolling_24h_delta_positive": True,
        "microstructure_observation": {
            "trade_activity_observed": True,
            "orderbook_snapshot_observed": True,
            "captured_trade_count": 28,
            "source_reported_buy_count": 19,
            "source_reported_sell_count": 9,
            "spread_bps": "22.00", "bid_depth_top5_idr": "5000000",
            "ask_depth_top5_idr": "4500000",
            "trade_gap_or_truncation": False,
        },
    }


class SoloIDRPolicyTests(unittest.TestCase):
    def test_all_idr_public_watch_except_manual_floki_pepe(self):
        for pair in ("btc_idr", "uai_idr", "usdt_idr", "usdc_idr"):
            self.assertEqual(main_idr_pair_status(pair),
                             "IDR_PAIR_ELIGIBLE_FOR_PUBLIC_WATCH")
        for pair in ("floki_idr", "pepe_idr"):
            self.assertEqual(main_idr_pair_status(pair), "MANUAL_ONLY_ASSET")
        self.assertEqual(main_idr_pair_status("btc_usdt"),
                         "NOT_AN_IDR_SPOT_PAIR")

    def test_idle_scanner_continues_with_no_candidate(self):
        result = next_solo_idr_cycle(facts())
        self.assertEqual(result["phase"], "SCAN_NEW_IDR_PAIRS")
        self.assertTrue(result["scan_public_allowed"])
        self.assertFalse(result["execution_allowed"])

    def test_ai_review_at_most_two_one_bot_owned_position(self):
        result = next_solo_idr_cycle(facts(candidate_count=30))
        self.assertEqual(result["phase"], "SCAN_AND_AI_REVIEW")
        self.assertEqual(result["max_deep_candidates_per_review"], 2)
        self.assertEqual(result["max_bot_owned_positions"], 1)

    def test_confirmed_buy_turns_all_new_entries_off_and_focus_sell(self):
        result = next_solo_idr_cycle(facts(
            bot_owned_open_position_count=1,
            bot_owned_base_quantity=Decimal("25"),
            candidate_count=40,
        ))
        self.assertEqual(result["phase"], "SELL_FOCUS")
        self.assertFalse(result["scan_public_allowed"])
        self.assertFalse(result["new_buy_admitted"])

    def test_wait_for_buy_fill_not_equal_to_filled_position(self):
        result = next_solo_idr_cycle(facts(pending_buy=True))
        self.assertEqual(result["phase"], "WAIT_BUY_FILL")

    def test_pending_sell_protects_position_and_never_rebuys(self):
        result = next_solo_idr_cycle(facts(
            bot_owned_open_position_count=1,
            bot_owned_base_quantity=Decimal("12.1"), pending_sell=True,
        ))
        self.assertEqual(result["phase"], "SELL_FOCUS")
        self.assertFalse(result["scan_public_allowed"])

    def test_sell_is_not_finished_until_settlement(self):
        result = next_solo_idr_cycle(facts(sell_fills_unsettled=True))
        self.assertEqual(result["phase"], "WAIT_SELL_SETTLEMENT_IDR")
        self.assertFalse(result["scan_public_allowed"])

    def test_unknown_order_first_reconcile_not_repeat(self):
        result = next_solo_idr_cycle(facts(
            order_status_unknown=True, candidate_count=30))
        self.assertEqual(result["phase"], "UNKNOWN_ORDER_RECONCILIATION")
        self.assertFalse(result["execution_allowed"])

    def test_position_source_conflict_blocks_other_order(self):
        result = next_solo_idr_cycle(facts(
            bot_owned_open_position_count=0,
            bot_owned_base_quantity=Decimal("1")))
        self.assertEqual(result["phase"], "SOURCE_OR_POSITION_CONFLICT_RECONCILE")

    def test_missing_account_does_not_block_public_scanning(self):
        result = next_solo_idr_cycle(facts(
            candidate_count=10, has_fresh_idr_account_snapshot=False))
        self.assertEqual(result["phase"], "SCAN_WITH_CAPITAL_UNVERIFIED")
        self.assertTrue(result["scan_public_allowed"])

    def test_full_free_idr_without_special_rebuy_bucket(self):
        review = main_available_idr_review(
            exchange_free_idr="920147", unaccounted_commitments_idr="0",
            all_pair_open_orders_verified=True, account_snapshot_fresh=True,
        )
        self.assertEqual(review["computed_uncommitted_idr_preview"], "920147")
        self.assertEqual(review["manual_asset_reserve_deducted_idr"], "0")
        self.assertFalse(review["execution_allowed"])

    def test_small_positive_budget_is_preview_not_forced_trade(self):
        review = main_available_idr_review(
            exchange_free_idr="2000", unaccounted_commitments_idr="0",
            all_pair_open_orders_verified=True, account_snapshot_fresh=True,
        )
        self.assertEqual(review["computed_uncommitted_idr_preview"], "2000")
        self.assertTrue(review["exchange_minimum_and_fee_still_apply"])

    def test_pending_commitments_and_unverified_scope_reduce_capital(self):
        review = main_available_idr_review(
            exchange_free_idr="100000", unaccounted_commitments_idr="15000",
            all_pair_open_orders_verified=False, account_snapshot_fresh=True,
        )
        self.assertIsNone(review["computed_uncommitted_idr_preview"])
        self.assertFalse(review["execution_allowed"])

    def test_high_quality_watch_is_not_a_buy_or_ai_pass(self):
        result = candidate_quality_watch(candidate=candidate(), now_epoch=NOW,
                                         current_order_budget_idr="1000000")
        self.assertEqual(result["status"],
                         "PUBLIC_WATCH_QUALITY_RANKED_NOT_BUY_READY")
        self.assertGreater(result["review_score_out_of_100"], 60)
        self.assertFalse(result["real_ai_responses_observed"])
        self.assertFalse(result["execution_allowed"])
        self.assertIn("CLOSED_1M_5M_15M_NOT_VERIFIED",
                      result["missing_evidence"])

    def test_no_book_no_trade_never_fake_metrics(self):
        row = candidate()
        row["microstructure_observation"] = {}
        result = candidate_quality_watch(candidate=row, now_epoch=NOW)
        self.assertIn("SOURCE_TRADE_ACTIVITY_MISSING",
                      result["missing_evidence"])
        self.assertIn("FRESH_ORDER_BOOK_MISSING",
                      result["missing_evidence"])
        self.assertFalse(result["signals"]["book_source_observed"])

    def test_extended_pump_deprioritized_not_called_buy(self):
        result = candidate_quality_watch(
            candidate=candidate(phase="EXTENDED_MOVE_REVIEW"), now_epoch=NOW)
        self.assertIn("EXTENDED_MOVE_DO_NOT_CHASE", result["reject_reasons"])
        self.assertFalse(result["buy_recommended"])

    def test_stale_and_manual_only_reject_first(self):
        row = candidate()
        row["observed_epoch"] = NOW - 300
        self.assertEqual(candidate_quality_watch(
            candidate=row, now_epoch=NOW)["reject_reasons"],
            ["SOURCE_STALE_OR_FUTURE"])
        self.assertEqual(candidate_quality_watch(
            candidate=candidate(pair="floki_idr"), now_epoch=NOW)["pair_status"],
            "MANUAL_ONLY_ASSET")

    def test_net_requirements_use_and_not_or(self):
        for profit, roi, passed in (
            ("10000", "2", True),
            ("20000", "1.5", False),
            ("9999", "8", False),
        ):
            review = net_edge_policy_preview(
                ticket_idr="500000", forecast_net_profit_idr=profit,
                forecast_net_roi_pct=roi,
                all_in_fees_and_slippage_verified=False,
            )
            self.assertEqual(review["both_net_thresholds_met_on_scenario"], passed)
            self.assertFalse(review["buy_authorized"])

    def test_fingerprint_same_evidence_stable_new_fact_changes(self):
        one = candidate_fingerprint("uai_idr", NOW, {"price": "100"})
        two = candidate_fingerprint("uai_idr", NOW, {"price": "101"})
        self.assertNotEqual(one, two)
        self.assertEqual(one, candidate_fingerprint(
            "uai_idr", NOW, {"price": "100"}))

    def test_bad_boolean_money_and_cross_quote_are_rejected(self):
        with self.assertRaises(SoloIDRPolicyInvalid):
            main_available_idr_review(
                exchange_free_idr=True, unaccounted_commitments_idr="0",
                all_pair_open_orders_verified=True,
                account_snapshot_fresh=True,
            )
        self.assertEqual(main_idr_pair_status("btc_usdt"),
                         "NOT_AN_IDR_SPOT_PAIR")


if __name__ == "__main__":
    unittest.main()
