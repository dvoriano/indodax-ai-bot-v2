"""M08 SELL net target accounting contract, non-executable until owned lots exist.

Do not confuse ticker 24h changes, individual candle returns, BUY-entry gate,
or hypothetical mark-to-market with quote-based exit net ROI vs actual fills.
"""
from __future__ import annotations

from decimal import Decimal as D
import unittest

from indodax_ai_bot.policy import ScalpingPolicy


class PositionExitTierTests(unittest.TestCase):
    def setUp(self):
        self.policy=ScalpingPolicy()
        self.default={
            "filled_buy_quantity":D("1"),
            "filled_buy_notional_idr":D("100"),
            "allocated_buy_fees_idr":D("0.20"),
            "quantity_to_sell":D("1"),
            "expected_sell_notional_idr":D("110"),
            "estimated_sell_fees_idr":D("0.20"),
            "incremental_slippage_idr":D("0.20"),
        }

    def assess(self, **changes):
        return self.policy.assess_position_exit_targets(
            **(self.default | changes)
        )

    def test_100_buy_110_sell_gross_10pct_net_t2_not_t3(self):
        result=self.assess()
        self.assertEqual(result.allocated_cost_basis_idr,D("100.20"))
        self.assertEqual(result.expected_net_proceeds_idr,D("109.60"))
        self.assertGreaterEqual(result.expected_net_roi_percent,D("8"))
        self.assertLess(result.expected_net_roi_percent,D("11"))
        self.assertEqual(result.highest_reached_tier,"T2")
        self.assertEqual(result.reached_tiers,("T1","T2"))
        self.assertFalse(result.price_change_24h_used)
        self.assertFalse(result.realized)
        self.assertFalse(result.execution_allowed)

    def test_gross_10pct_does_not_always_reach_net_t2(self):
        r=self.assess(
            allocated_buy_fees_idr=D("0.50"),
            estimated_sell_fees_idr=D("1.50"),
            incremental_slippage_idr=D("0.50"),
        )
        self.assertEqual(r.expected_net_proceeds_idr,D("108"))
        self.assertLess(r.expected_net_roi_percent,D("8"))
        self.assertEqual(r.highest_reached_tier,"T1")

    def test_exact_t2_and_t3_boundaries_are_position_based(self):
        t2=self.assess(
            allocated_buy_fees_idr=D("0"),
            expected_sell_notional_idr=D("108.20"),
            estimated_sell_fees_idr=D("0.20"),
            incremental_slippage_idr=D("0"),
        )
        self.assertEqual(t2.expected_net_roi_percent,D("8"))
        self.assertEqual(t2.highest_reached_tier,"T2")
        t3=self.assess(
            allocated_buy_fees_idr=D("0"),
            expected_sell_notional_idr=D("111.40"),
            estimated_sell_fees_idr=D("0.20"),
            incremental_slippage_idr=D("0.20"),
        )
        self.assertEqual(t3.expected_net_roi_percent,D("11"))
        self.assertEqual(t3.highest_reached_tier,"T3")

    def test_partial_sell_allocates_same_buy_cost_basis(self):
        r=self.assess(
            filled_buy_quantity=D("10"),
            filled_buy_notional_idr=D("1000"),
            allocated_buy_fees_idr=D("2"),
            quantity_to_sell=D("4"),
            expected_sell_notional_idr=D("440"),
            estimated_sell_fees_idr=D("1"),
            incremental_slippage_idr=D("1"),
        )
        self.assertEqual(r.allocated_cost_basis_idr,D("400.8"))
        self.assertEqual(r.expected_net_proceeds_idr,D("438"))
        self.assertEqual(r.highest_reached_tier,"T2")
        self.assertFalse(r.realized)

    def test_invalid_or_unknown_fills_cannot_become_sell_signal(self):
        for kwargs in (
            {"filled_buy_quantity":D("0")},
            {"quantity_to_sell":D("2")},
            {"allocated_buy_fees_idr":D("-0.01")},
            {"estimated_sell_fees_idr":D("-0.01")},
            {"incremental_slippage_idr":D("NaN")},
            {"expected_sell_notional_idr":110.0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.assess(**kwargs)

    def test_buy_rp10000_profit_floor_is_not_sell_hold_requirement(self):
        # BUY qualification is a separate gate, not a command to hold
        # a protective or tier SELL until profit reaches Rp10,000.
        self.assertFalse(self.policy.eligible_buy(D("9999"),D("8")))
        t2=self.assess()
        self.assertEqual(t2.highest_reached_tier,"T2")
        self.assertLess(t2.expected_net_profit_idr,D("10000"))
        self.assertFalse(t2.execution_allowed)



class PositionHoldingTimeTests(unittest.TestCase):
    def setUp(self):
        self.policy = ScalpingPolicy()
        self.entry = 1800000000

    def assess(self, seconds, **flags):
        return self.policy.assess_position_holding_time(
            actual_buy_fill_epoch=self.entry,
            observed_epoch=self.entry+seconds,
            **flags,
        )

    def test_scalp_entry_to_30m_fast_then_exit_priority(self):
        first = self.assess(0)
        last_fast = self.assess(1799)
        boundary = self.assess(1800)
        self.assertEqual(first.phase, "FAST_SCALP_MONITOR")
        self.assertEqual(last_fast.phase, "FAST_SCALP_MONITOR")
        self.assertEqual(boundary.phase, "EXIT_PRIORITY_REVIEW")
        self.assertEqual(boundary.risk_review_priority, "ELEVATED")
        self.assertFalse(boundary.forced_sell)
        self.assertFalse(boundary.execution_allowed)

    def test_60m_triggers_urgent_protective_review_not_forced_sell(self):
        at_60 = self.assess(3600)
        self.assertEqual(at_60.phase, "TIME_STOP_PROTECTIVE_REVIEW")
        self.assertEqual(at_60.risk_review_priority, "URGENT")
        self.assertEqual(at_60.review_interval_seconds, 60)
        self.assertFalse(at_60.forced_sell)
        self.assertFalse(at_60.execution_allowed)

    def test_verified_pump_trailing_is_bounded_exception(self):
        flags = dict(
            pump_continuation_confirmed=True,
            trailing_protection_active=True,
            market_source_fresh=True,
            risk_review_clear=True,
        )
        at_60 = self.assess(3600, **flags)
        self.assertEqual(at_60.phase, "PUMP_TRAILING_EXCEPTION_REVIEW")
        self.assertTrue(at_60.pump_exception_observed)
        self.assertFalse(at_60.execution_allowed)
        at_90 = self.assess(5400, **flags)
        self.assertEqual(at_90.phase, "EXTENDED_POSITION_RISK_REVIEW")
        self.assertEqual(at_90.risk_review_priority, "URGENT")
        self.assertFalse(at_90.forced_sell)

    def test_incomplete_trailing_or_risk_evidence_never_waives_time_review(self):
        for missing in (
            "pump_continuation_confirmed",
            "trailing_protection_active",
            "market_source_fresh",
            "risk_review_clear",
        ):
            flags = dict(
                pump_continuation_confirmed=True,
                trailing_protection_active=True,
                market_source_fresh=True,
                risk_review_clear=True,
            )
            flags[missing] = False
            with self.subTest(missing=missing):
                self.assertEqual(
                    self.assess(3660, **flags).phase,
                    "TIME_STOP_PROTECTIVE_REVIEW",
                )

    def test_actual_fill_epoch_required_and_no_fake_hold(self):
        for buy, now in ((0,10),(1800000000,1799999999),
                         (True,1800000010),("1800000000",1800000010)):
            with self.subTest(buy=buy,now=now),self.assertRaises(ValueError):
                self.policy.assess_position_holding_time(
                    actual_buy_fill_epoch=buy,observed_epoch=now
                )
        with self.assertRaises(ValueError):
            self.assess(3600,risk_review_clear="YES")

    def test_cumulative_net_t2_does_not_reset_after_one_hour(self):
        # A delayed outcome is still based on original filled entry;
        # time pressure changes EXIT urgency, not the net target basis.
        cost = self.policy.assess_position_exit_targets(
            filled_buy_quantity=D("1"), filled_buy_notional_idr=D("100"),
            allocated_buy_fees_idr=D("0.20"), quantity_to_sell=D("1"),
            expected_sell_notional_idr=D("110"),
            estimated_sell_fees_idr=D("0.20"),
            incremental_slippage_idr=D("0.20"),
        )
        self.assertEqual(cost.highest_reached_tier,"T2")
        self.assertEqual(
            self.assess(5*3600).phase,"EXTENDED_POSITION_RISK_REVIEW")
        self.assertFalse(cost.execution_allowed)




if __name__=="__main__":
    unittest.main()
