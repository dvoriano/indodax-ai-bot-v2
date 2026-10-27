"""M04-U: both netto BUY gates plus authenticated capital/fee preflight.

Synthetic HMAC and injected roles only; never owner GO, paid Sumopod, or orders.
"""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from indodax_ai_bot.three_role_ai import ThreeRoleShadow
from test_m04a_three_role_shadow import RoleCaller, signed_role_run
from test_m04t_two_candidate_shadow import signed_two_pair_source, PAIRS, CLOCK


def strategy_with(*, net: str, roi: str, action: str = "BUY"):
    calls = []
    def caller(role, evidence):
        calls.append(role)
        response = RoleCaller()(role, evidence)
        if role == "STRATEGY":
            response["projected_net_profit_idr"] = net
            response["projected_net_roi_percent"] = roi
            response["action"] = action
        return response
    return caller, calls


class M04UStrategySafetyTests(unittest.TestCase):
    def test_net_only_met_rejects_buy(self):
        caller, calls = strategy_with(net="10000", roi="1.99")
        report = signed_role_run(caller)
        self.assertEqual(calls, ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual(report["reason"], "STRATEGY_BUY_NET_TARGET_UNMET")
        self.assertEqual(report["completed_roles"], calls)
        self.assertEqual(report["final_decision"], "REJECT")
        self.assertFalse(report["execution_allowed"])

    def test_roi_only_met_rejects_buy(self):
        caller, calls = strategy_with(net="9999.99", roi="50")
        report = signed_role_run(caller)
        self.assertEqual(report["reason"], "STRATEGY_BUY_NET_TARGET_UNMET")
        self.assertEqual(report["completed_roles"], calls)
        self.assertFalse(report["execution_allowed"])

    def test_both_thresholds_met_still_block_unverified_balance_and_fees(self):
        caller, calls = strategy_with(net="10000", roi="2")
        report = signed_role_run(caller)
        self.assertEqual(report["reason"], "STRATEGY_BUY_PREFLIGHT_UNVERIFIED")
        self.assertEqual(report["role_results"][-1]["action"], "BUY")
        self.assertEqual(report["final_decision"], "REJECT")
        self.assertFalse(report["g2_candidate_authorized"])
        self.assertFalse(report["execution_allowed"])
        self.assertFalse(report["live_trading_authorized"])
        self.assertEqual(calls, ["MARKET", "RISK", "STRATEGY"])

    def test_massive_profit_prediction_cannot_bypass_missing_capital(self):
        caller, _ = strategy_with(net="900000000", roi="999")
        report = signed_role_run(caller)
        self.assertEqual(report["reason"], "STRATEGY_BUY_PREFLIGHT_UNVERIFIED")
        self.assertFalse(report["execution_allowed"])

    def test_wait_stays_shadow_and_never_acts_on_projection(self):
        caller, calls = strategy_with(net="0", roi="0", action="WAIT")
        report = signed_role_run(caller)
        self.assertEqual(report["reason"], "M04_SHADOW_ONLY_FINAL_AND_PREFLIGHT_ABSENT")
        self.assertEqual(calls, ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual(report["role_results"][-1]["action"], "WAIT")
        self.assertEqual(report["final_decision"], "REJECT")

    def test_two_candidates_separate_buy_evaluations_both_non_executable(self):
        bulk, verifier = signed_two_pair_source()
        caller, calls = strategy_with(net="10000", roi="2")
        report = ThreeRoleShadow(
            caller=caller, clock=lambda: CLOCK, source_verifier=verifier,
        ).run("trace", bulk)
        self.assertEqual(report["candidate_count"], 2)
        self.assertEqual(calls, ["MARKET", "RISK", "STRATEGY"] * 2)
        self.assertEqual([x["pair"] for x in report["candidate_reports"]], list(PAIRS))
        self.assertEqual([x["reason"] for x in report["candidate_reports"]],
                         ["STRATEGY_BUY_PREFLIGHT_UNVERIFIED"] * 2)
        self.assertEqual(report["final_decision"], "REJECT")
        self.assertFalse(report["execution_allowed"])

    def test_risk_veto_still_stops_before_strategy(self):
        caller = RoleCaller(risk="NO_TRADE")
        report = signed_role_run(caller)
        self.assertEqual(caller.calls, ["MARKET", "RISK"])
        self.assertEqual(report["reason"], "RISK_VETO_NO_TRADE")
        self.assertFalse(report["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
