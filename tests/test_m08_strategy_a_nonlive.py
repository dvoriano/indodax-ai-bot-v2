"""Pure, no-network contract tests for owner Strategy A (NOT trade evidence)."""
from __future__ import annotations

from decimal import Decimal as D
import unittest

from indodax_ai_bot.m07_order_adapter import M07OrderDraft, OfficialV2OrderAdapter
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.m08_strategy_a_nonlive import (
    assess_ws3_priority_alert_offline,
    assess_maker_entry_offline,
    assess_exit_strategy_a_offline,
    assess_rebound_watch_offline,
)

NOW = 1800001020


def exit_review(*, cost="100000", qty="100", tier="100",
                bid="950", ask="960", depth="100",
                fee="0", maker_fee="0", slip="0",
                stop=False, order="NONE", age=2):
    return assess_exit_strategy_a_offline(
        remaining_bot_qty_reference=D(qty),
        remaining_cost_including_buy_fee_idr=D(cost),
        tier_sell_qty_reference=D(tier),
        bid_levels=((D(bid), D(depth)),),
        best_ask=D(ask),
        taker_sell_fee_pct=D(fee),
        maker_sell_fee_pct=D(maker_fee),
        taker_slippage_pct=D(slip),
        book_age_seconds=age,
        stop_latched=stop,
        open_sell_state=order,
    )


def rebound(*, settled=True, elapsed=1000, prices=("87", "88", "89"),
            volume="500000000", trade=True, book=True, spread="0.3"):
    observed = NOW + elapsed
    minute = observed // 60 * 60
    quotes = tuple((minute - 60 * (len(prices) - 1 - i), D(p))
                   for i, p in enumerate(prices))
    return assess_rebound_watch_offline(
        settled_idr_verified=settled, closed_epoch=NOW,
        now_epoch=observed, pre_drop_reference_price=D("100"),
        closed_1m_quotes=quotes,
        rolling_24h_idr=D(volume),
        trade_activity_verified=trade,
        orderbook_fresh_verified=book,
        spread_pct=D(spread),
    )


class StrategyAOfflineTests(unittest.TestCase):
    def test_original_v2_orchestrator_offline_routing_no_order(self):
        out = DailyScalpingOrchestrator.review_strategy_a_nonlive_once(
            review_type="POSITION_EXIT",
            remaining_bot_qty_reference=D("100"),
            remaining_cost_including_buy_fee_idr=D("100000"),
            tier_sell_qty_reference=D("100"),
            bid_levels=((D("950"), D("100")),), best_ask=D("960"),
            taker_sell_fee_pct=D("0"), maker_sell_fee_pct=D("0"),
            taker_slippage_pct=D("0"), book_age_seconds=0)
        self.assertEqual(out["status"], "STOP_TAKER_PREVIEW_ONLY")
        self.assertFalse(out["order_sent"])
        with self.assertRaises(ValueError):
            DailyScalpingOrchestrator.review_strategy_a_nonlive_once(
                review_type="LIVE_ORDER")

    def test_priority_alert_requires_both_thresholds(self):
        cases = (
            ("0.35", "600000000", True, False),
            ("1.5", "399999999", True, False),
            ("1.4999", "400000000", False, False),
            ("1.5", "400000000", False, True),
            ("2", "700000000", True, True),
        )
        for move, turnover, acceleration, expected in cases:
            with self.subTest(move=move, turnover=turnover):
                r = assess_ws3_priority_alert_offline(
                    rise_pct=D(move), rolling_24h_idr=D(turnover),
                    early_acceleration=acceleration)
                self.assertIs(r["priority_alert_eligible"], expected)
                self.assertFalse(r["execution_allowed"])
                self.assertFalse(r["order_sent"])

    def test_maker_buy_moc_and_no_taker_fallback(self):
        r = assess_maker_entry_offline(
            proposed_buy_price=D("100"), best_bid=D("100"),
            best_ask=D("101"))
        self.assertTrue(r["maker_buy_eligible"])
        self.assertEqual(r["time_in_force_reference"], "MOC")
        self.assertTrue(r["no_fallback_to_taker"])
        self.assertFalse(r["execution_allowed"])
        self.assertFalse(assess_maker_entry_offline(
            proposed_buy_price=D("101"), best_bid=D("100"),
            best_ask=D("101"))["maker_buy_eligible"])
        self.assertFalse(assess_maker_entry_offline(
            proposed_buy_price=D("100"), best_bid=D("100"),
            best_ask=D("103"))["maker_buy_eligible"])

    def test_m07_draft_moc_only_for_limit_no_dispatch(self):
        draft = M07OrderDraft(
            symbol="XRPIDR", side="BUY", order_type="LIMIT",
            client_order_id="stratA001", price=D("100"),
            quantity=D("10"), time_in_force="MOC")
        self.assertIn(("timeInForce", "MOC"), draft.parameters())
        self.assertFalse(OfficialV2OrderAdapter.contract_status()["execution_allowed"])
        with self.assertRaises(ValueError):
            M07OrderDraft(symbol="XRPIDR", side="BUY", order_type="MARKET",
                          client_order_id="badmoc", quote_order_qty=100000,
                          time_in_force="MOC").parameters()
        with self.assertRaises(ValueError):
            M07OrderDraft(symbol="XRPIDR", side="SELL", order_type="LIMIT",
                          client_order_id="badvalue",price=D("100"),
                          quantity=D("10"), time_in_force="FOK").parameters()

    def test_stop_exact_minus_five_and_gap(self):
        r = exit_review()
        self.assertEqual(r["status"], "STOP_TAKER_PREVIEW_ONLY")
        self.assertEqual(D(r["projected_full_exit_net_roi_pct"]), D("-5"))
        self.assertTrue(r["stop_latched"])
        self.assertFalse(r["execution_allowed"])
        self.assertEqual(exit_review(bid="930",ask="940")["status"],
                         "STOP_TAKER_PREVIEW_ONLY")
        self.assertEqual(exit_review(bid="950.1",ask="960")["status"],
                         "LOSS_WARNING_HOLD")

    def test_stop_triggers_after_fees_even_when_price_drop_less_than_five(self):
        r = exit_review(bid="955",ask="965",fee="0.5",slip="0.1")
        self.assertEqual(r["status"], "STOP_TAKER_PREVIEW_ONLY")

    def test_stop_latches_even_if_price_recovers(self):
        r = exit_review(bid="1100",ask="1102",stop=True)
        self.assertEqual(r["status"], "STOP_TAKER_PREVIEW_ONLY")
        self.assertTrue(r["stop_latched"])

    def test_stop_open_order_requires_cancel_reconciliation_first(self):
        for state in ("OPEN", "PARTIAL", "CANCEL_PENDING", "UNKNOWN"):
            with self.subTest(state=state):
                r = exit_review(order=state)
                self.assertEqual(r["status"], "STOP_CANCEL_RECONCILE_FIRST")
                self.assertTrue(r["required_cancel_before_new_sell"])
                self.assertFalse(r["order_sent"])

    def test_stop_incomplete_book_or_stale_fails_closed(self):
        self.assertEqual(exit_review(age=45,stop=True)["status"],
                         "STOP_LATCHED_NEEDS_FRESH_BOOK")
        self.assertEqual(exit_review(depth="20",stop=True)["status"],
                         "STOP_LATCHED_INSUFFICIENT_DEPTH")
        self.assertEqual(exit_review(depth="20")["status"],
                         "INSUFFICIENT_BID_DEPTH_RISK_ALERT_NO_ORDER")

    def test_t1_maker_and_t2_t3_taker_are_separate(self):
        # Rp500,000 cost, 100 units. Each tier needs >=Rp10,000 net.
        t1 = exit_review(cost="500000",bid="5050",ask="5120")
        self.assertEqual(t1["status"], "T1_MAKER_MOC_PREVIEW_ONLY")
        self.assertEqual(exit_review(
            cost="500000",bid="5450",ask="5460")["status"],
            "T2_TAKER_PREVIEW_ONLY")
        self.assertEqual(exit_review(
            cost="500000",bid="5600",ask="5610")["status"],
            "T3_TAKER_PREVIEW_ONLY")
        self.assertEqual(exit_review(
            cost="500000",bid="5600",ask="5610",order="OPEN")["status"],
            "T3_CANCEL_T1_AND_RECONCILE_FIRST")

    def test_min_profit_enforced_per_tier_quantity(self):
        r = exit_review(cost="500000", qty="100",tier="10",
                        bid="5100",ask="5200",depth="100")
        self.assertEqual(r["status"], "HOLD_NO_PROFIT_TIER")

    def test_taker_profit_refuses_wide_spread(self):
        r = exit_review(cost="500000",bid="5500",ask="5650")
        self.assertNotIn(r["status"], ("T2_TAKER_PREVIEW_ONLY",
                                      "T3_TAKER_PREVIEW_ONLY"))

    def test_invalid_partial_or_nonfinite_rejected(self):
        with self.assertRaises(ValueError):
            exit_review(qty="10",tier="11")
        with self.assertRaises(ValueError):
            exit_review(cost="NaN")
        with self.assertRaises(ValueError):
            assess_exit_strategy_a_offline(
                remaining_bot_qty_reference=D("10"),
                remaining_cost_including_buy_fee_idr=D("100"),
                tier_sell_qty_reference=D("10"),
                bid_levels=((D("10"), D("10")),
                            (D("11"), D("10"))),
                best_ask=D("12"), taker_sell_fee_pct=D("0"),
                maker_sell_fee_pct=D("0"), taker_slippage_pct=D("0"),
                book_age_seconds=1)

    def test_rebound_requires_settlement_cooldown_and_market_evidence(self):
        self.assertEqual(rebound(settled=False)["status"],
                         "REBOUND_BLOCKED_UNSETTLED_POSITION")
        self.assertEqual(rebound(elapsed=899)["status"],
                         "REBOUND_REENTRY_COOLDOWN")
        r = rebound()
        self.assertEqual(r["status"],
                         "REBOUND_CANDIDATE_REQUIRES_NEW_AI_OWNER_FINAL")
        self.assertTrue(r["rebound_candidate_for_review"])
        self.assertFalse(r["rebound_entry_allowed"])
        self.assertFalse(r["execution_allowed"])
        self.assertFalse(rebound(trade=False)["rebound_candidate_for_review"])
        self.assertFalse(rebound(volume="399999999")["rebound_candidate_for_review"])
        self.assertFalse(rebound(prices=("87","88","87"))["rebound_candidate_for_review"])
        self.assertFalse(rebound(prices=("95","96","97"))["rebound_candidate_for_review"])
        self.assertFalse(rebound(spread="2")["rebound_candidate_for_review"])


if __name__ == "__main__":
    unittest.main()
