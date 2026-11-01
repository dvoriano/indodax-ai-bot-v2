"""M06 single automatic account diagnosis, not a new approval layer.

All tests synthetic. No Indodax HTTP, no live trade, no Telegram owner prompt.
"""
from decimal import Decimal as D
from pathlib import Path
import tempfile
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m06_account_review import (
    observe_m06_account_preview, reconcile_readonly_private_snapshot,
)
from indodax_ai_bot.m06_ownership_ledger import ProtectedRebuyReview
from indodax_ai_bot.m06_private_payloads import AccountBalances, OrderRecord
from indodax_ai_bot.m06_scoped_https import M06RequestBudget, M06ScopedApproval
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


class M06SingleAccountDiagnosticTests(unittest.TestCase):
    def account(self):
        return AccountBalances(
            771, (("IDR", D("9000"), D("2000")),), D("9000"), D("2000"),
        )

    def protected(self):
        return ProtectedRebuyReview(
            D("750"), D("250"), D("1000"), 2,
            local_shadow_commitments_idr=D("1500"),
            local_shadow_coverage_complete=True,
        )

    def test_free_already_excludes_locked_and_local_holds_apply_once(self):
        p = observe_m06_account_preview(
            account=self.account(), open_orders=(),
            local_rebuy=self.protected(),
        )
        self.assertEqual(p.observed_free_idr, D("9000"))
        self.assertEqual(p.observed_locked_idr, D("2000"))
        self.assertEqual(p.known_local_protected_idr, D("1000"))
        self.assertEqual(p.known_local_pending_idr, D("1500"))
        self.assertEqual(p.candidate_budget_upper_bound_idr, D("6500"))
        self.assertFalse(p.spendable_verified)
        self.assertFalse(p.execution_allowed)
        self.assertFalse(p.external_obligations_complete)

    def test_missing_ledger_is_not_trusted_as_proof_of_zero_liability(self):
        p = observe_m06_account_preview(
            account=self.account(), open_orders=(), local_rebuy=None,
        )
        self.assertEqual(p.candidate_budget_upper_bound_idr, D("9000"))
        self.assertFalse(p.local_ledger_present)
        self.assertFalse(p.external_obligations_complete)
        self.assertFalse(p.spendable_verified)
        self.assertNotIn("9000", repr(p))

    def test_holds_above_free_clip_upper_bound_to_zero(self):
        hold = replace(
            self.protected(), local_shadow_commitments_idr=D("15000"),
        )
        p = observe_m06_account_preview(
            account=self.account(), open_orders=(), local_rebuy=hold,
        )
        self.assertEqual(p.candidate_budget_upper_bound_idr, D("0"))
        self.assertFalse(p.execution_allowed)

    def test_open_buy_is_observed_not_automatically_authorized(self):
        order = OrderRecord(
            "local-pending-order", "injidr", "BUY", "NEW",
            D("8"), D("0"),
        )
        p = observe_m06_account_preview(
            account=self.account(), open_orders=(order,),
            local_rebuy=self.protected(),
        )
        self.assertEqual(p.pending_buy_orders, 1)
        self.assertEqual(p.candidate_budget_upper_bound_idr, D("6500"))
        self.assertFalse(p.spendable_verified)
        self.assertFalse(p.execution_allowed)

    def test_original_reconciler_one_summary_stays_reject(self):
        review = reconcile_readonly_private_snapshot(
            account=self.account(), open_orders=(),
            order_history=(), fills=(), symbol="injidr",
            local_rebuy=self.protected(),
        )
        self.assertEqual(review.account_preview.candidate_budget_upper_bound_idr, D("6500"))
        self.assertFalse(review.spendable_capital_verified)
        self.assertFalse(review.execution_allowed)
        self.assertEqual(review.status, "REJECT")
        self.assertNotIn("6500", repr(review))

    def test_original_orchestrator_four_mock_gets_no_extra_gate(self):
        with tempfile.TemporaryDirectory() as dirname:
            now = int(time.time() * 1000)
            data = {
                "/api/v2/account": {
                    "uid": 771, "canTrade": False,
                    "canWithdraw": False, "canWithdrawFiat": False,
                    "balances": [{"asset": "IDR", "free": "9000", "locked": "2000"}],
                },
                "/api/v2/openOrders": [],
                "/api/v2/order/histories": {"data": []},
                "/api/v2/myTrades": {"data": []},
            }
            seen = []
            with patch(
                "indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
                side_effect=lambda request: (
                    seen.append(request.path), data[request.path]
                )[1],
            ):
                outcome = DailyScalpingOrchestrator(
                    Settings(), DecisionJournal(Path(dirname) / "decisions.db"),
                ).inspect_m06_private_readonly_once(
                    approval=M06ScopedApproval(
                        "M06_PRIVATE_VIEW_ONLY_RETRY_20261031",
                        now - 1000, now + 899000, 4, "injidr",
                    ),
                    budget=M06RequestBudget(str(Path(dirname) / "budget.db")),
                    api_key="fake-key", secret_key="fake-secret",
                    local_ownership_db=str(Path(dirname) / "missing-ownership.db"),
                )
            self.assertEqual(len(seen), 4)
            self.assertEqual(outcome.account_preview.candidate_budget_upper_bound_idr, D("9000"))
            self.assertFalse(outcome.account_preview.local_ledger_present)
            self.assertFalse(outcome.capital_spendable_verified)
            self.assertFalse(outcome.execution_allowed)
            self.assertNotIn("9000", repr(outcome))


if __name__ == "__main__":
    unittest.main()
