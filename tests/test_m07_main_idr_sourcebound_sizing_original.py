"""M07-02 original orchestrator M03 public + M06 RO MAIN IDR sizing proof.

All snapshots are LOCAL synthetic shapes. No external requests and no orders.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.deep_analysis import DeepCandidate
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from tests.test_m03_public_quote_friction_mainpath import public_observation


NOW = 1800000080
RULES = {
    "price_precision": "1", "quantity_increment": "0.1",
    "trade_min_base_currency": "10000",
    "trade_min_traded_currency": "1",
    "is_market_suspended": 0, "is_maintenance": 0,
}


def account():
    return {
        "status": "M06_IDR_BALANCE_OBSERVED_SOURCE_RESERVE_UNVERIFIED",
        "view_only_permissions_verified": True,
        "requests_completed": 2,
        "account_observed_epoch": NOW - 5,
        "exchange_idr_free_observed": "920147",
        "exchange_idr_locked_observed": "0",
        "all_pair_open_orders_observed": 0,
        "all_special_sell_history_verified": False,
        "local_special_ledger_uid_bound": False,
        "all_peer_liabilities_verified": False,
        "spendable_idr_verified": False,
        "source_authenticated": False,
        "execution_allowed": False,
        "order_sent": False,
    }


def inputs(public):
    return {
        "public_candidate": public,
        "account_snapshot": account(),
        "owner_main_source_classified": True,
        "requested_ticket_ceiling_idr": D("100000"),
        "new_floki_sell_rebuy_hold_idr": D("0"),
        "new_pepe_sell_rebuy_hold_idr": D("0"),
        "commitments_not_locked_idr": D("0"),
        "max_spread_bps": D("150"),
        "now_epoch": NOW,
    }


class OwnerClassifiedMainSizingOriginalTests(unittest.TestCase):
    def build(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        journal = DecisionJournal(Path(folder.name) / "m07-original.sqlite3")
        bot = DailyScalpingOrchestrator(Settings(), journal)
        obs = replace(public_observation(), pair_rule_fields=dict(RULES))
        with patch.object(journal, "record_and_compare_public_wall",
                          return_value={"status":"UNATTESTED"}):
            candidate = bot._attach_public_wall_experience(
                DeepCandidate("btc_idr", 75, "PUBLIC_REVIEW", None), obs)
        public = candidate.summary()["m07_same_candidate_public_preflight"]
        self.assertEqual(
            public["status"], "M07_PUBLIC_SOURCE_BOUND_RULES_REFERENCE_ONLY")
        self.assertEqual(public["public_best_ask_idr_reference"], "100")
        self.assertEqual(public["public_pair_rules_reference"]["price_precision"],"1")
        return bot, public

    def test_owner_main_920147_is_full_funding_pool_not_whole_ticket(self):
        bot, public = self.build()
        result = bot.review_m07_owner_main_idr_sizing_offline(**inputs(public))
        self.assertEqual(result["main_idr_pool_scenario"], "920147")
        self.assertEqual(result["requested_ticket_ceiling_idr"], "100000")
        self.assertEqual(result["status"], "M07_MAIN_SIZING_RULES_MATCH_REFERENCE_ONLY")
        self.assertTrue(result["price_step_and_minimum_reference_met"])
        self.assertTrue(result["spread_ceiling_reference_met"])
        self.assertEqual(D(result["sized_qty_base_reference"]) % D("0.1"), D("0"))
        self.assertLessEqual(D(result["buy_max_cost_idr_reference"]), D("100000"))
        self.assertFalse(result["slippage_and_depth_executable_verified"])
        self.assertFalse(result["per_account_all_in_fee_verified"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["order_sent"])
        self.assertEqual(bot.journal.m07_offline_intent_summary()[
            "unresolved_simulated_intents"], 0)

    def test_no_initial_principal_cap_and_no_invented_historical_holds(self):
        bot, public = self.build()
        v = inputs(public)
        v["requested_ticket_ceiling_idr"] = D("2000000")
        result = bot.review_m07_owner_main_idr_sizing_offline(**v)
        self.assertEqual(result["main_idr_pool_scenario"], "920147")
        self.assertLessEqual(D(result["buy_max_cost_idr_reference"]), D("920147"))
        self.assertTrue(result["owner_main_source_classification_accepted_for_preview"])
        self.assertFalse(result["execution_allowed"])

    def test_new_special_sale_holds_and_not_locked_commitments_reduce_budget(self):
        bot, public = self.build()
        v = inputs(public)
        v.update(
            requested_ticket_ceiling_idr=D("2000000"),
            new_floki_sell_rebuy_hold_idr=D("200000"),
            new_pepe_sell_rebuy_hold_idr=D("10000"),
            commitments_not_locked_idr=D("1000"),
        )
        result = bot.review_m07_owner_main_idr_sizing_offline(**v)
        self.assertEqual(result["main_idr_pool_scenario"], "709147")
        self.assertLessEqual(D(result["buy_max_cost_idr_reference"]), D("709147"))
        self.assertFalse(result["execution_allowed"])

    def test_owner_classification_required_without_historical_special_audit(self):
        bot, public = self.build()
        v = inputs(public)
        v["owner_main_source_classified"] = False
        result = bot.review_m07_owner_main_idr_sizing_offline(**v)
        self.assertEqual(
            result["status"], "M07_MAIN_SIZING_OWNER_SOURCE_CLASSIFICATION_REQUIRED")
        self.assertFalse(result["execution_allowed"])

    def test_stale_quote_account_open_orders_and_fake_permission_are_blocked(self):
        bot, public = self.build()
        base = inputs(public)
        variants = [
            dict(public_candidate={**public, "observed_epoch":NOW-40}),
            dict(public_candidate={**public, "candidate_pair":"eth_idr"}),
            dict(public_candidate={**public, "execution_allowed":True}),
            dict(account_snapshot={**account(), "account_observed_epoch":NOW-50}),
            dict(account_snapshot={**account(), "all_pair_open_orders_observed":1}),
            dict(account_snapshot={**account(), "order_sent":True}),
        ]
        for changes in variants:
            with self.subTest(changes=changes):
                out = bot.review_m07_owner_main_idr_sizing_offline(
                    **{**base, **changes})
                self.assertIsNone(out["sized_qty_base_reference"])
                self.assertFalse(out["execution_allowed"])

    def test_invalid_quote_precision_or_fee_does_not_allocate_budget(self):
        bot, public = self.build()
        bad = [
            {**public, "public_best_ask_idr_reference":"NaN"},
            {**public, "public_best_ask_idr_reference":"100.33"},
            {**public, "all_in_buy_taker_bps_public_reference_only":"9999"},
            {**public, "public_pair_rules_reference":{}},
        ]
        for candidate in bad:
            with self.subTest(candidate=candidate):
                out = bot.review_m07_owner_main_idr_sizing_offline(
                    **{**inputs(public), "public_candidate":candidate})
                self.assertNotEqual(
                    out["status"], "M07_MAIN_SIZING_RULES_MATCH_REFERENCE_ONLY")
                self.assertFalse(out["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
