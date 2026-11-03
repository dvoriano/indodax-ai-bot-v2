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
            self.assertEqual(report["status"], "M07_NO_ELIGIBLE_M03_SAME_CYCLE_CANDIDATE")
            self.assertFalse(report["execution_allowed"])

if __name__ == "__main__":
    unittest.main()
