"""M06 original V2 UID-bound shadow commitments; fully offline, no orders.

Evidence of local holds is never authenticated exchange capital, and even a
complete local table does NOT prove cross-bot/all-pair obligations.
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m06_capital_reconcile import reconcile_unverified_capital
from indodax_ai_bot.m06_ownership_ledger import M06OwnershipLedger, OwnershipConflict
from indodax_ai_bot.m06_private_payloads import AccountBalances
from indodax_ai_bot.m06_shadow_account import AccountObservation, M06ShadowCapital
from indodax_ai_bot.m06_account_review import reconcile_readonly_private_snapshot
from indodax_ai_bot.m06_scoped_https import M06RequestBudget, M06ScopedApproval
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


class M06UIDShadowCommitmentsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "same-v2-journal.sqlite3"
        self.uid = 531
        self.scope = M06OwnershipLedger.account_scope_from_exchange_uid(
            self.uid,
        )
        self.ownership = M06OwnershipLedger(self.path)
        self.path.chmod(0o600)
        self.ownership.protect_manual_asset_floor(
            account_key=self.scope, asset="FLOKI",
            minimum_qty=D("750"),
        )
        self.account = AccountBalances(
            self.uid,
            (("IDR", D("1000"), D("200")),
             ("FLOKI", D("1000"), D("0"))),
            D("1000"), D("200"),
        )
        self.observation = AccountObservation(
            observed_epoch=1000,
            free_idr=D("1000"),
            locked_idr=D("200"),
            floki_rebuy_idr=D("100"),
            pepe_rebuy_idr=D("0"),
            other_unlocked_commitments_idr=D("50"),
            account_key=self.scope,
        )

    def _reserve(self, trace, amount):
        return M06ShadowCapital(self.path).reserve_shadow(
            observation=self.observation, trace_id=trace,
            requested_idr=D(amount), now_epoch=1000,
        )

    def _read(self, uid=None):
        return M06OwnershipLedger.inspect_existing_account_readonly(
            db_path=str(self.path),
            account_uid=self.uid if uid is None else uid,
        )

    def test_absent_shadow_table_is_unknown_not_verified_zero(self):
        snapshot = self._read()
        self.assertEqual(snapshot.local_shadow_commitments_idr, D("0"))
        self.assertFalse(snapshot.local_shadow_coverage_complete)
        account_review = reconcile_readonly_private_snapshot(
            account=self.account, open_orders=(), order_history=(), fills=(),
            symbol="injidr", local_rebuy=snapshot,
        )
        self.assertFalse(account_review.local_shadow_coverage_complete)
        self.assertFalse(account_review.spendable_capital_verified)
        self.assertFalse(account_review.execution_allowed)

    def test_same_sqlite_snapshot_counts_only_active_uid_reservations(self):
        first = self._reserve("first", "200")
        self.assertFalse(first.replayed)
        again = self._reserve("first", "200")
        self.assertTrue(again.replayed)
        self._reserve("second", "100")
        observed = self._read()
        self.assertTrue(observed.local_shadow_coverage_complete)
        self.assertEqual(observed.local_shadow_commitments_idr, D("300"))
        self.assertEqual(observed.floki_manual_qty_local_floor, D("750"))
        # Existing local reservations subtract from a calculator's *preview*.
        raw = reconcile_unverified_capital(
            account=self.account, open_orders=(), rebuy_idr=D("100"),
            unlocked_commitments_idr=D("50"),
            local_shadow_idr=observed.local_shadow_commitments_idr,
            all_pairs_open_orders_complete=True,
        )
        self.assertEqual(raw.calculated_available_idr, D("550"))
        self.assertFalse(raw.source_authenticated)
        self.assertFalse(raw.execution_allowed)
        with patch(
            "indodax_ai_bot.m06_account_review.reconcile_unverified_capital",
            wraps=reconcile_unverified_capital,
        ) as calculation:
            review = reconcile_readonly_private_snapshot(
                account=self.account, open_orders=(), order_history=(),
                fills=(), symbol="injidr", local_rebuy=observed,
            )
        self.assertEqual(calculation.call_args.kwargs["local_shadow_idr"],
                         D("300"))
        self.assertTrue(review.local_shadow_reservations_present)
        self.assertTrue(review.local_shadow_coverage_complete)
        self.assertFalse(review.spendable_capital_verified)
        self.assertFalse(review.execution_allowed)

        M06ShadowCapital(self.path).release_shadow(trace_id="first")
        after = self._read()
        self.assertEqual(after.local_shadow_commitments_idr, D("100"))
        self.assertEqual(self._read(), after)

    def test_other_uid_never_inherits_local_reservations(self):
        key = M06OwnershipLedger.account_scope_from_exchange_uid(532)
        self.ownership.protect_manual_asset_floor(
            account_key=key, asset="PEPE", minimum_qty=D("70"),
        )
        self._reserve("held-first-user", "200")
        foreign = self._read(532)
        self.assertTrue(foreign.local_shadow_coverage_complete)
        self.assertEqual(foreign.local_shadow_commitments_idr, D("0"))
        self.assertEqual(foreign.floki_manual_qty_local_floor, D("0"))
        self.assertEqual(foreign.pepe_manual_qty_local_floor, D("70"))

    def test_corrupted_shadow_liability_is_denied_not_ignored(self):
        self._reserve("held", "50")
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE m06_shadow_reserve SET amount_idr=? WHERE trace_id=?",
                ("-100", "held"),
            )
        with self.assertRaisesRegex(OwnershipConflict, "SHADOW_LEDGER_CORRUPT"):
            self._read()

    def test_concurrent_reservations_are_atomic_not_double_spend(self):
        M06ShadowCapital(self.path)
        def submit(i):
            try:
                self._reserve("simultaneous-" + str(i), "400")
                return "RESERVED"
            except ValueError as err:
                self.assertIn("INSUFFICIENT_FREE_IDR", str(err))
                return "REJECT"
        with ThreadPoolExecutor(max_workers=3) as workers:
            outcomes = list(workers.map(submit, range(3)))
        self.assertEqual(outcomes.count("RESERVED"), 2)
        self.assertEqual(outcomes.count("REJECT"), 1)
        self.assertEqual(self._read().local_shadow_commitments_idr, D("800"))

    def test_original_orchestrator_rejects_unverified_shadow_after_four_mock_gets(self):
        self._reserve("original-view", "200")
        now = int(time.time() * 1000)
        approval = M06ScopedApproval(
            "M06_PRIVATE_VIEW_ONLY_RETRY_20261031",
            now - 1000, now + 899000, 4, "injidr",
        )
        payloads = {
            "/api/v2/account": {
                "uid": self.uid,
                "canTrade": False,
                "canWithdraw": False,
                "canWithdrawFiat": False,
                "balances": [
                    {"asset": "IDR", "free": "1000", "locked": "200"},
                    {"asset": "FLOKI", "free": "1000", "locked": "0"},
                ],
            },
            "/api/v2/openOrders": [],
            "/api/v2/order/histories": {"data": []},
            "/api/v2/myTrades": {"data": []},
        }
        visited = []
        with patch(
            "indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
            side_effect=lambda req: (
                visited.append(req.path), payloads[req.path]
            )[1],
        ):
            bot = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(
                    Path(self.temp.name) / "original-journal.sqlite3",
                ),
            )
            result = bot.inspect_m06_private_readonly_once(
                approval=approval,
                budget=M06RequestBudget(
                    str(Path(self.temp.name) / "test-budget.sqlite3"),
                ),
                api_key="fake-only", secret_key="fake-secret",
                local_ownership_db=str(self.path),
            )
        self.assertEqual(len(visited), 4)
        self.assertTrue(result.view_only_permissions_verified)
        self.assertTrue(result.local_ledger_account_bound)
        self.assertTrue(result.local_shadow_coverage_complete)
        self.assertTrue(result.local_shadow_reservations_present)
        self.assertFalse(result.protected_rebuy_verified)
        self.assertFalse(result.capital_spendable_verified)
        self.assertFalse(result.cross_bot_ownership_verified)
        self.assertFalse(result.execution_allowed)


if __name__ == "__main__":
    unittest.main()
