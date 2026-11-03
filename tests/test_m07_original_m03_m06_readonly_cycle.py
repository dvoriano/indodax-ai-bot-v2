"""M07 source-bound one-cycle unit tests. No exchange requests."""
import unittest
from decimal import Decimal
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator, CycleResult

class MainSourceJoinTest(unittest.TestCase):
    def test_skips_private_account_when_public_cohort_empty(self):
        with TemporaryDirectory() as folder:
            bot = DailyScalpingOrchestrator(Settings(), DecisionJournal(Path(folder) / "test.sqlite3"))
            empty = CycleResult("once", None, "PUBLIC_UNVERIFIED", bulk={
                "mode": "M03G_BULK_PUBLIC_SHARED_SOURCE_ONLY",
                "shared_public_scan_context": True,
                "eligible_cohort": {"eligible_pair_ids": []},
                "provisional_market_evidence": [],
            })
            funding = dict(requested_ticket_ceiling_idr=Decimal("100000"),
                           new_floki_sell_rebuy_hold_idr=Decimal("0"),
                           new_pepe_sell_rebuy_hold_idr=Decimal("0"),
                           commitments_not_locked_idr=Decimal("0"),
                           max_spread_bps=Decimal("150"))
            with (patch.object(bot, "run_bulk_deep_cycle", return_value=empty),
                  patch.object(bot, "inspect_m06_idr_budget_readonly_once") as private):
                report = bot.run_m07_original_source_join_readonly_once(
                    "once", api_key="test", secret_key="test", funding=funding)
            private.assert_not_called()
            self.assertEqual(report["status"], "M07_M03_COHORT_NOT_ALIGNED_NO_PRIVATE_GET")
            self.assertFalse(report["execution_allowed"])

from dataclasses import replace
from indodax_ai_bot.deep_analysis import DeepCandidate
from tests.test_m07_main_idr_sourcebound_sizing_original import NOW, RULES, account
from tests.test_m03_public_quote_friction_mainpath import public_observation

class SourceJoinWithBoundCandidate(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bot = DailyScalpingOrchestrator(
            Settings(), DecisionJournal(Path(self.temp.name) / "joined.db"))
        obs = replace(public_observation(), pair_rule_fields=dict(RULES))
        with patch.object(self.bot.journal, "record_and_compare_public_wall",
                          return_value={"status": "UNATTESTED"}):
            c = self.bot._attach_public_wall_experience(
                DeepCandidate("btc_idr", 75, "PUBLIC_REVIEW", None), obs)
        self.row = c.summary()
        self.funding = {
            "requested_ticket_ceiling_idr": Decimal("100000"),
            "new_floki_sell_rebuy_hold_idr": Decimal("0"),
            "new_pepe_sell_rebuy_hold_idr": Decimal("0"),
            "commitments_not_locked_idr": Decimal("0"),
            "max_spread_bps": Decimal("150"),
        }

    def run_case(self, rows=None, private_report=None):
        evidence = CycleResult("once", None, "PUBLIC_UNVERIFIED", bulk={
            "mode": "M03G_BULK_PUBLIC_SHARED_SOURCE_ONLY",
            "shared_public_scan_context": True,
            "coverage_status": "ALL_SELECTED_ANALYZED_UNATTESTED",
            "eligible_cohort": {"status": "ALIGNED_PUBLIC_UNATTESTED",
                                "eligible_pair_ids": ["btc_idr"]},
            "provisional_market_evidence": [self.row] if rows is None else rows,
            "execution_allowed": False,
        })
        with (patch("indodax_ai_bot.orchestrator.time.time", return_value=NOW),
              patch.object(self.bot, "run_bulk_deep_cycle",
                           return_value=evidence) as scanner,
              patch.object(self.bot, "inspect_m06_idr_budget_readonly_once",
                           return_value=private_report or account()) as reader):
            result = self.bot.run_m07_original_source_join_readonly_once(
                "once", api_key="viewonly", secret_key="viewonly",
                funding=self.funding)
        return result, scanner, reader

    def run_ranked_only(self, *, fallback_row=None, cohort_status="ALIGNED_PUBLIC_UNATTESTED"):
        # Exact M03 ranked rows are from this invocation, not a new REST GET.
        # Quality watchlist is intentionally EMPTY despite an aligned cohort.
        evidence = CycleResult("once", None, "PUBLIC_UNVERIFIED", bulk={
            "mode": "M03G_BULK_PUBLIC_SHARED_SOURCE_ONLY",
            "shared_public_scan_context": True,
            "coverage_status": "PARTIAL_INVALID_PAIR_EVIDENCE",
            "eligible_cohort": {
                "status": cohort_status,
                "eligible_pair_ids": ["btc_idr"],
                "provisional_watchlist_preview": [],
                "provisional_quality_reviews": [{
                    "pair": "btc_idr",
                    "clear_of_provisional_cautions": False,
                }],
            },
            "provisional_market_evidence": [],
            "ranked_diagnostics_top10": [
                self.row if fallback_row is None else fallback_row,
            ],
        })
        with (patch("indodax_ai_bot.orchestrator.time.time", return_value=NOW),
              patch.object(self.bot, "run_bulk_deep_cycle",
                           return_value=evidence) as scanner,
              patch.object(self.bot, "inspect_m06_idr_budget_readonly_once",
                           return_value=account()) as reader):
            result = self.bot.run_m07_original_source_join_readonly_once(
                "once", api_key="viewonly", secret_key="viewonly",
                funding=self.funding)
        return result, scanner, reader

    def test_ranked_fallback_joins_but_never_promotes_to_trade_signal(self):
        result, scanner, reader = self.run_ranked_only()
        scanner.assert_called_once()
        reader.assert_called_once()
        self.assertEqual(result["status"], "M07_REAL_M03_M06_REFERENCE_JOINED_NO_ORDER")
        self.assertEqual(result["m03_quality_clean_watchlist_count"], 0)
        self.assertEqual(result["m03_ranked_diagnostic_rows"], 1)
        self.assertEqual(result["m03_candidate_selection_lane"],
                         "M03_RANKED_CAUTION_DIAGNOSTIC_ONLY")
        self.assertFalse(result["m03_selected_quality_clean"])
        self.assertFalse(result["m03_g1_independently_verified"])
        self.assertFalse(result["m05_order_specific_final_verified"])
        self.assertFalse(result["execution_allowed"])

    def test_ranked_fallback_rejects_missing_fee_or_pair_rules_before_account(self):
        row = dict(self.row)
        row["m07_same_candidate_public_preflight"] = {
            **row["m07_same_candidate_public_preflight"],
            "status": "M07_PUBLIC_RULES_OR_FEES_NOT_READY",
        }
        result, _, reader = self.run_ranked_only(fallback_row=row)
        reader.assert_not_called()
        self.assertEqual(result["status"],
                         "M07_NO_READY_PUBLIC_PAIR_RULES_OR_FEE_REFERENCE")
        self.assertEqual(
            result["m03_candidate_rejection_counts"][
                "no_valid_source_bound_public_rules_or_fee"], 1)

    def test_ranked_fallback_never_bypasses_cohort_misalignment(self):
        result, _, reader = self.run_ranked_only(
            cohort_status="REJECT_CANDLE_OR_SNAPSHOT_MISALIGNMENT")
        reader.assert_not_called()
        self.assertEqual(result["status"],
                         "M07_M03_COHORT_NOT_ALIGNED_NO_PRIVATE_GET")

    def test_ranked_fallback_stale_skips_private_account(self):
        row = dict(self.row)
        row["m07_same_candidate_public_preflight"] = {
            **row["m07_same_candidate_public_preflight"],
            "observed_epoch": NOW - 100,
        }
        result, _, reader = self.run_ranked_only(fallback_row=row)
        reader.assert_not_called()
        self.assertEqual(result["status"],
                         "M07_M03_PUBLIC_ALREADY_STALE_NO_PRIVATE_GET")

    def test_original_modules_join_and_cap_not_entire_free_balance(self):
        result, scanner, reader = self.run_case()
        scanner.assert_called_once()
        reader.assert_called_once()
        self.assertEqual(result["status"], "M07_REAL_M03_M06_REFERENCE_JOINED_NO_ORDER")
        self.assertEqual(result["candidate_pair"], "btc_idr")
        self.assertEqual(result["m06_idr_free_observed"], "920147")
        self.assertEqual(result["m07_main_sizing"]["main_idr_pool_scenario"], "920147")
        self.assertLessEqual(Decimal(result["m07_main_sizing"]["buy_max_cost_idr_reference"]),
                             Decimal("100000"))
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["m05_order_specific_final_verified"])
        self.assertFalse(result["account_all_in_fee_verified"])
        self.assertFalse(result["order_sent"])

    def test_stale_public_is_rejected_before_private_get(self):
        row = dict(self.row)
        row["m07_same_candidate_public_preflight"] = {
            **row["m07_same_candidate_public_preflight"],
            "observed_epoch": NOW - 90,
        }
        result, _, reader = self.run_case(rows=[row])
        reader.assert_not_called()
        self.assertEqual(result["status"], "M07_M03_PUBLIC_ALREADY_STALE_NO_PRIVATE_GET")

    def test_account_open_orders_and_staleness_block_sizing(self):
        for mutation in ({"all_pair_open_orders_observed": 1},
                         {"account_observed_epoch": NOW - 100}):
            with self.subTest(mutation=mutation):
                result, _, reader = self.run_case(
                    private_report={**account(), **mutation})
                reader.assert_called_once()
                self.assertEqual(result["status"], "M07_REAL_M03_M06_JOIN_BLOCKED")
                self.assertFalse(result["execution_allowed"])

    def test_no_cross_pair_and_no_false_trade_authority(self):
        for mutation in ({"pair": "eth_idr"},
                         {"trade_authorized": True},
                         {"eligible_for_same_window_comparison": False}):
            with self.subTest(mutation=mutation):
                result, _, reader = self.run_case(rows=[{**self.row, **mutation}])
                reader.assert_not_called()
                self.assertEqual(result["status"],
                                 "M07_NO_ELIGIBLE_M03_SAME_CYCLE_CANDIDATE")

    def test_new_special_proceeds_hold_only_when_explicit(self):
        self.funding.update(
            new_floki_sell_rebuy_hold_idr=Decimal("200000"),
            new_pepe_sell_rebuy_hold_idr=Decimal("10000"),
            commitments_not_locked_idr=Decimal("1000"),
        )
        result, _, _ = self.run_case()
        self.assertEqual(result["m07_main_sizing"]["main_idr_pool_scenario"],
                         "709147")
        self.assertFalse(result["execution_allowed"])

    def test_original_cli_calls_one_cycle_mainpath(self):
        from indodax_ai_bot.__main__ import main
        from io import StringIO
        from contextlib import redirect_stdout
        import json
        import os
        f = Path(self.temp.name) / "funding.json"
        f.write_text(json.dumps({k: str(v) for k, v in self.funding.items()}))
        os.chmod(f, 0o600)
        ws = Path(self.temp.name) / "ws.sqlite3"
        ws.write_bytes(b"test")
        bulk = CycleResult("once", None, "UNVERIFIED", bulk={
            "mode": "M03G_BULK_PUBLIC_SHARED_SOURCE_ONLY",
            "shared_public_scan_context": True,
            "eligible_cohort": {"status": "ALIGNED_PUBLIC_UNATTESTED",
                                "eligible_pair_ids": ["btc_idr"]},
            "provisional_market_evidence": [self.row],
        })
        out = StringIO()
        with (patch("indodax_ai_bot.orchestrator.time.time", return_value=NOW),
              patch("indodax_ai_bot.__main__._existing_v2_credentials",
                    return_value=f),
              patch("indodax_ai_bot.__main__.read_view_only_credentials",
                    return_value=("test", "test")),
              patch.object(DailyScalpingOrchestrator, "run_bulk_deep_cycle",
                           return_value=bulk) as scan,
              patch.object(DailyScalpingOrchestrator,
                           "inspect_m06_idr_budget_readonly_once",
                           return_value=account()) as view,
              redirect_stdout(out)):
            code = main(["--m07-main-readonly-cycle-once",
                         "--m07-runtime-funding-file", str(f),
                         "--ws-db", str(ws),
                         "--db", str(Path(self.temp.name) / "cli.db")])
        self.assertEqual(code, 0)
        scan.assert_called_once()
        view.assert_called_once()
        result = json.loads(out.getvalue())
        self.assertFalse(result["execution_allowed"])
        self.assertIsNone(result["final_decision"])

if __name__ == "__main__":
    unittest.main()
