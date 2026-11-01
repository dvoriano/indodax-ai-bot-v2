"""M06-05 shared-account coexistence diagnostics on original V2 only.

Local evidence is not proof that other bots/processes have surrendered an
Indodax wallet. All results remain REJECT, with no private network or orders.
"""
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m06_account_review import reconcile_readonly_private_snapshot
from indodax_ai_bot.m06_ownership_ledger import M06OwnershipLedger, ProtectedRebuyReview
from indodax_ai_bot.m06_private_payloads import AccountBalances, FillRecord, OrderRecord
from indodax_ai_bot.m06_scoped_https import M06RequestBudget, M06ScopedApproval
from indodax_ai_bot.m06_shadow_account import AccountObservation, M06ShadowCapital
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


class M06LocalCoexistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "v2-original-ledger.sqlite3"
        self.ledger = M06OwnershipLedger(self.path)
        self.path.chmod(0o600)
        self.uid = 501
        self.scope = self.ledger.account_scope_from_exchange_uid(self.uid)
        self.ledger.record_local_intent(
            account_key=self.scope, order_id="original-501",
            client_id="v2-client-501", symbol="injidr", side="BUY",
        )

    def read(self, uid=None):
        return self.ledger.inspect_existing_account_readonly(
            db_path=str(self.path),
            account_uid=self.uid if uid is None else uid,
        )

    def test_single_uid_never_claims_external_peer_coverage(self):
        local = self.read()
        self.assertFalse(local.local_other_account_records_present)
        self.assertFalse(local.local_orphan_fill_records_present)
        self.assertFalse(local.external_peer_coverage_verified)
        review = reconcile_readonly_private_snapshot(
            account=AccountBalances(
                self.uid, (("IDR", D("1000"), D("0")),), D("1000"), D("0"),
            ), open_orders=(), order_history=(), fills=(),
            symbol="injidr", local_rebuy=local,
        )
        self.assertFalse(review.cross_bot_ownership_verified)
        self.assertFalse(review.spendable_capital_verified)
        self.assertFalse(review.execution_allowed)
        self.assertFalse(review.external_peer_coverage_verified)

    def test_local_other_account_intent_detected_in_same_sqlite(self):
        foreign = self.ledger.account_scope_from_exchange_uid(502)
        self.ledger.record_local_intent(
            account_key=foreign, order_id="foreign-502",
            client_id="other-502", symbol="btcidr", side="BUY",
        )
        first = self.read()
        second = self.read()
        self.assertTrue(first.local_other_account_records_present)
        self.assertEqual(second, first)
        self.assertFalse(first.external_peer_coverage_verified)

    def test_foreign_shadow_liability_is_not_ignored(self):
        foreign = self.ledger.account_scope_from_exchange_uid(502)
        M06ShadowCapital(self.path).reserve_shadow(
            observation=AccountObservation(
                observed_epoch=1000,
                free_idr=D("1000"), locked_idr=D("0"),
                floki_rebuy_idr=D("0"), pepe_rebuy_idr=D("0"),
                other_unlocked_commitments_idr=D("0"),
                account_key=foreign,
            ),
            trace_id="foreign-501-reserve", requested_idr=D("300"),
            now_epoch=1000,
        )
        review = self.read()
        self.assertTrue(review.local_other_account_records_present)
        self.assertEqual(review.local_shadow_commitments_idr, D("0"))
        self.assertTrue(review.local_shadow_coverage_complete)
        self.assertFalse(review.external_peer_coverage_verified)

    def test_legacy_orphan_fill_detected_without_rebinding(self):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO m06_observed_fills VALUES(?,?,?,?,?,?)",
                ("orphan-fill", "outside-order", "btcidr",
                 "1", "250", "1"),
            )
        review = self.read()
        self.assertTrue(review.local_orphan_fill_records_present)
        self.assertFalse(review.local_other_account_records_present)
        self.assertFalse(review.external_peer_coverage_verified)
        self.assertFalse(review.execution_allowed)

    def test_other_uid_fill_receipt_visible_only_as_conflict_hint(self):
        other_uid = self.ledger.account_scope_from_exchange_uid(502)
        self.ledger.record_local_intent(
            account_key=other_uid, order_id="other-sell",
            client_id="other-owner", symbol="pepeidr", side="SELL",
        )
        self.ledger.observe(
            account_key=other_uid,
            order=OrderRecord(
                "other-sell", "pepeidr", "SELL", "FILLED",
                D("2"), D("2"), "other-owner",
            ),
            fills=(FillRecord(
                "other-fill", "other-sell", "pepeidr",
                D("2"), D("100"), D("1"),
                client_order_id="other-owner", is_buyer=False,
            ),),
        )
        local = self.read()
        self.assertTrue(local.local_other_account_records_present)
        self.assertFalse(local.local_orphan_fill_records_present)
        self.assertFalse(local.external_peer_coverage_verified)
        self.assertEqual(local.total_idr_local_protected, D("0"))

    def test_forged_external_coverage_is_refused_by_original_reconciler(self):
        local = self.read()
        forged = replace(local, external_peer_coverage_verified=True)
        with self.assertRaisesRegex(ValueError, "AUTHORITY_FORGED"):
            reconcile_readonly_private_snapshot(
                account=AccountBalances(
                    self.uid, (("IDR", D("1000"), D("0")),),
                    D("1000"), D("0"),
                ),
                open_orders=(), order_history=(), fills=(),
                symbol="injidr", local_rebuy=forged,
            )

    def test_original_orchestrator_four_mock_gets_reject_coexistence(self):
        other_uid = self.ledger.account_scope_from_exchange_uid(502)
        self.ledger.record_local_intent(
            account_key=other_uid, order_id="unknown-party",
            client_id="unknown-client", symbol="ethidr", side="BUY",
        )
        now = int(time.time() * 1000)
        approval = M06ScopedApproval(
            "M06_PRIVATE_VIEW_ONLY_RETRY_20261031",
            now - 1000, now + 899000, 4, "injidr",
        )
        source = {
            "/api/v2/account": {
                "uid": self.uid, "canTrade": False,
                "canWithdraw": False, "canWithdrawFiat": False,
                "balances": [
                    {"asset": "IDR", "free": "1900", "locked": "0"},
                ],
            },
            "/api/v2/openOrders": [],
            "/api/v2/order/histories": {"data": []},
            "/api/v2/myTrades": {"data": []},
        }
        seen = []
        with patch(
            "indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
            side_effect=lambda req: (
                seen.append(req.path), source[req.path]
            )[1],
        ):
            bot = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(
                    Path(self.temp.name) / "test-decisions.sqlite3",
                ),
            )
            outcome = bot.inspect_m06_private_readonly_once(
                approval=approval,
                budget=M06RequestBudget(
                    str(Path(self.temp.name) / "test-budget.sqlite3"),
                ),
                api_key="fixture-key", secret_key="fixture-secret",
                local_ownership_db=str(self.path),
            )
        self.assertEqual(len(seen), 4)
        self.assertTrue(outcome.local_ledger_account_bound)
        self.assertTrue(outcome.local_other_account_records_present)
        self.assertFalse(outcome.external_peer_coverage_verified)
        self.assertFalse(outcome.cross_bot_ownership_verified)
        self.assertFalse(outcome.bot_owned_fills_verified)
        self.assertFalse(outcome.capital_spendable_verified)
        self.assertFalse(outcome.execution_allowed)


if __name__ == "__main__":
    unittest.main()
