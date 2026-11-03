"""M04 STRATEGY source-bound offline plan and all-in net arithmetic.

All scenario prices and source issuer secrets are SYNTHETIC TEST DATA.
Real Indodax price/fees/account proof, paid AI and order paths are absent.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from unittest.mock import Mock
import unittest

from indodax_ai_bot.three_role_ai import (
    ThreeRoleShadow, _strategy_source_plan,
)
from test_m04a_three_role_shadow import (
    RoleCaller, signed_role_run, source,
)
from test_m04_risk_source_veto import signed_variant
from test_m04t_two_candidate_shadow import CLOCK


def test_quote(pair="btc_idr"):
    return {
        "status": "SYNTHETIC_SOURCE_BOUND_SCENARIO_ONLY",
        "pair": pair, "closed_1m_epoch": 1800000120,
        "entry_price_idr": "100000", "quantity": "6",
        "scenario_sell_price_idr": "103500",
        "stop_loss_price_idr": "98000",
        "buy_all_in_fee_rate": "0.002",
        "sell_all_in_fee_rate": "0.002",
        "buy_slippage_bps": "10", "sell_slippage_bps": "10",
        "max_hold_minutes": 60,
        "cancel_if": "Freshness, Risk veto or 80 bps spread",
    }


def signed_quote(change=None):
    def modify(bulk):
        bulk["strategy_quote_claims_by_pair"] = {"btc_idr": test_quote()}
        if change is not None:
            change(bulk)
    return signed_variant(modify)


def model_run(bulk, verifier, custom=None):
    calls = []
    def call(role, inp):
        calls.append(role)
        raw = RoleCaller()(role, inp)
        if custom is not None:
            custom(role, inp, raw)
        return raw
    report = ThreeRoleShadow(
        caller=call, source_verifier=verifier, clock=lambda: CLOCK
    ).run("trace", bulk)
    return report, calls


class M04StrategySourceBoundPlanTests(unittest.TestCase):
    def test_missing_real_quote_keeps_all_prices_unknown_and_buy_nonexecutable(self):
        caller = RoleCaller()
        result = signed_role_run(caller)
        self.assertEqual(caller.calls, ["MARKET", "RISK", "STRATEGY"])
        plan = caller.inputs[2]["strategy_source_plan"]
        self.assertEqual(plan["status"],
                         "UNAVAILABLE_NO_VERIFIED_ENTRY_AND_COSTS")
        for field in ("entry_price_idr", "quantity", "allocated_cost_idr",
                      "estimated_net_exit_idr", "estimated_net_profit_idr",
                      "stop_loss_price_idr"):
            self.assertIsNone(plan[field])
        self.assertEqual([x["net_roi_percent"] for x in plan["net_exit_targets"]],
                         ["2", "8", "11"])
        self.assertTrue(all(x["required_exit_price_idr"] is None
                            for x in plan["net_exit_targets"]))
        self.assertEqual(result["role_results"][-1]["action"], "BUY")
        self.assertEqual(result["role_results"][-1]["effective_action"], "WAIT")
        self.assertEqual(result["reason"], "STRATEGY_BUY_PREFLIGHT_UNVERIFIED")
        self.assertFalse(result["execution_allowed"])

    def test_synthetic_quote_calculates_all_in_net_and_net_exit_target_prices(self):
        bulk, verifier = signed_quote()
        report, calls = model_run(bulk, verifier)
        self.assertEqual(calls, ["MARKET", "RISK", "STRATEGY"])
        plan = report["role_results"][-1]["strategy_source_plan"]
        self.assertEqual(plan["status"],
                         "SYNTHETIC_SCENARIO_CALCULATED_UNATTESTED")
        self.assertEqual(Decimal(plan["allocated_cost_idr"]), Decimal("601800"))
        self.assertEqual(Decimal(plan["estimated_net_exit_idr"]), Decimal("619137"))
        self.assertEqual(Decimal(plan["estimated_net_profit_idr"]), Decimal("17337"))
        self.assertGreater(Decimal(plan["estimated_net_roi_percent"]),
                           Decimal("2.8"))
        for target in plan["net_exit_targets"]:
            desired_roi = Decimal(target["net_roi_percent"])
            exit_price = Decimal(target["required_exit_price_idr"])
            gross = exit_price * Decimal("6")
            net_exit = gross * Decimal("0.997")
            actual_roi = ((net_exit - Decimal("601800"))
                          / Decimal("601800") * 100)
            self.assertAlmostEqual(actual_roi, desired_roi, places=10)
        self.assertFalse(plan["authenticated_balance_verified"])
        self.assertFalse(plan["authenticated_quote_and_fees_verified"])
        self.assertFalse(plan["execution_allowed"])
        self.assertEqual(report["role_results"][-1]["effective_action"], "WAIT")
        self.assertEqual(report["reason"], "STRATEGY_BUY_PREFLIGHT_UNVERIFIED")

    def test_caller_forging_plan_and_reply_cannot_override_original_digest(self):
        bulk, verifier = signed_quote()
        def attack(role, inp, raw):
            if role == "STRATEGY":
                inp["strategy_source_plan"]["entry_price_idr"] = "1"
                raw["strategy_source_plan"]["entry_price_idr"] = "1"
                raw["strategy_source_plan"]["net_exit_targets"][0][
                    "required_exit_price_idr"] = "1"
        result, calls = model_run(bulk, verifier, attack)
        self.assertEqual(calls, ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual(result["reason"], "M04_ROLE_INVALID_OR_UNAVAILABLE")
        self.assertEqual(result["completed_roles"], ["MARKET", "RISK"])
        self.assertFalse(result["execution_allowed"])

    def test_tampered_quote_after_signing_prevents_any_model_call(self):
        bulk, verifier = signed_quote()
        bulk["strategy_quote_claims_by_pair"]["btc_idr"][
            "entry_price_idr"] = "1"
        caller = Mock()
        report = ThreeRoleShadow(
            caller=caller, source_verifier=verifier, clock=lambda: CLOCK
        ).run("trace", bulk)
        self.assertEqual(report["reason"], "G1_ISSUER_RECEIPT_UNVERIFIED")
        caller.assert_not_called()

    def test_greedy_model_claim_above_conservative_scenario_is_rejected(self):
        bulk, verifier = signed_quote()
        def greedy(role, inp, raw):
            if role == "STRATEGY":
                raw["projected_net_profit_idr"] = "1000000"
                raw["projected_net_roi_percent"] = "50"
        report, calls = model_run(bulk, verifier, greedy)
        self.assertEqual(calls, ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual(report["reason"], "M04_ROLE_INVALID_OR_UNAVAILABLE")
        self.assertFalse(report["execution_allowed"])

    def test_invalid_or_missing_fee_scenario_returns_no_price_not_fake_net(self):
        bulk, verifier = signed_quote(
            lambda x: x["strategy_quote_claims_by_pair"]["btc_idr"].pop(
                "sell_all_in_fee_rate")
        )
        report, _ = model_run(bulk, verifier)
        plan = report["role_results"][-1]["strategy_source_plan"]
        self.assertEqual(plan["status"],
                         "UNAVAILABLE_NO_VERIFIED_ENTRY_AND_COSTS")
        self.assertIsNone(plan["estimated_net_profit_idr"])
        self.assertEqual(report["role_results"][-1]["effective_action"], "WAIT")

    def test_risk_veto_preserved_after_strategy_advisory_even_if_profitable(self):
        bulk, verifier = signed_quote()
        def veto(role, inp, raw):
            if role == "RISK":
                raw["decision"] = "NO_TRADE"
        report, calls = model_run(bulk, verifier, veto)
        self.assertEqual(calls, ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual(report["reason"], "RISK_VETO_NO_TRADE")
        self.assertEqual(report["role_results"][-1]["effective_action"], "WAIT")
        self.assertFalse(report["execution_allowed"])

    def test_other_pair_scenario_cannot_supply_this_candidate_price(self):
        seed = source(trusted=True)
        local = {
            "pair": "btc_idr",
            "closed_1m_epoch": seed["eligible_cohort"]["closed_1m_epoch"],
            "strategy_quote_claim": test_quote("eth_idr"),
        }
        plan = _strategy_source_plan(local)
        self.assertEqual(plan["status"],
                         "UNAVAILABLE_NO_VERIFIED_ENTRY_AND_COSTS")
        self.assertIsNone(plan["entry_price_idr"])


if __name__ == "__main__":
    unittest.main()
