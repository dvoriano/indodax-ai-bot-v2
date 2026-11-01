"""One separately approved M06 account GET: all transports synthetic."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m06_account_coverage import (
    ACCOUNT_DIAG_ID, read_account_diagnostic_approval,
)
from indodax_ai_bot.m06_scoped_https import M06RequestBudget, M06ScopeDenied
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


class M06OneGetDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = int(time.time() * 1000)
        self.go = self.root / "go.json"
        self.manifest = {
            "approval_id": ACCOUNT_DIAG_ID,
            "issued_ms": self.now - 1000,
            "expires_ms": self.now + 60000,
            "max_requests": 1,
            "symbol": "injidr",
        }
        self.write_go()
        self.budget = M06RequestBudget(str(self.root / "diag.sqlite3"))

    def write_go(self):
        self.go.write_text(json.dumps(self.manifest))
        self.go.chmod(0o600)

    @staticmethod
    def good_account():
        return {
            "uid": 123, "canTrade": False, "canWithdraw": False,
            "canWithdrawFiat": False,
            "balances": [
                {"asset": "IDR", "free": "50000", "locked": "200"},
            ],
        }

    def call(self, response=None):
        seen = []
        payload = self.good_account() if response is None else response

        def fake(request):
            seen.append(request.path)
            return payload

        with patch(
            "indodax_ai_bot.m06_scoped_https._RELEASE_APPROVAL_ID",
            ACCOUNT_DIAG_ID,
        ), patch(
            "indodax_ai_bot.m06_scoped_https._NETWORK_RELEASE_GO", True
        ), patch(
            "indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
            side_effect=fake,
        ):
            approval = read_account_diagnostic_approval(str(self.go))
            orchestrator = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(self.root / "decisions.sqlite3")
            )
            try:
                result = orchestrator.inspect_m06_account_diagnostic_once(
                    approval=approval, budget=self.budget,
                    api_key="fixture-only-key", secret_key="fixture-only-secret",
                )
            except M06ScopeDenied as e:
                return str(e), seen
        return result, seen

    def test_one_account_get_only_and_zero_execution_authority(self):
        result, seen = self.call()
        self.assertEqual(seen, ["/api/v2/account"])
        self.assertEqual(result.request_count, 1)
        self.assertTrue(result.view_only_permissions_verified)
        self.assertTrue(result.balances_schema_observed)
        self.assertFalse(result.spendable_idr_verified)
        self.assertFalse(result.manual_asset_ownership_verified)
        self.assertFalse(result.external_peer_coverage_verified)
        self.assertFalse(result.execution_allowed)
        self.assertNotIn("50000", repr(result))

    def test_used_budget_denies_replay_before_second_get(self):
        first, seen = self.call()
        self.assertEqual(len(seen), 1)
        second, seen = self.call()
        self.assertEqual(second, "M06_ACCOUNT_DIAG_BUDGET_ALREADY_USED")
        self.assertEqual(seen, [])

    def test_trade_permission_is_denied_even_if_account_get_succeeds(self):
        bad = self.good_account()
        bad["canTrade"] = True
        error, seen = self.call(bad)
        self.assertEqual(error, "M06_KEY_NOT_PROVEN_VIEW_ONLY")
        self.assertEqual(seen, ["/api/v2/account"])

    def test_permission_failure_does_not_allow_second_attempt(self):
        bad = self.good_account()
        bad["canWithdraw"] = True
        error, seen = self.call(bad)
        self.assertEqual(error, "M06_KEY_NOT_PROVEN_VIEW_ONLY")
        second, seen2 = self.call(self.good_account())
        self.assertEqual(second, "M06_ACCOUNT_DIAG_BUDGET_ALREADY_USED")
        self.assertFalse(seen2)

    def test_documented_200_error_code_classified_without_body(self):
        error, seen = self.call({
            "code": -1002, "msg": "private server text must never leak",
        })
        self.assertEqual(error, "M06_ACCOUNT_DIAG_API_CREDENTIAL_REJECTED")
        self.assertNotIn("private server", error)
        self.assertEqual(seen, ["/api/v2/account"])

    def test_missing_idr_balance_rejected_after_one_get(self):
        bad = self.good_account()
        bad["balances"] = []
        error, seen = self.call(bad)
        self.assertEqual(error, "M06_ACCOUNT_DIAG_BALANCE_SCHEMA_REJECTED")
        self.assertEqual(seen, ["/api/v2/account"])

    def test_manifest_tamper_cannot_access_private_api(self):
        self.manifest["max_requests"] = 2
        self.write_go()
        with self.assertRaisesRegex(M06ScopeDenied, "DIAG_GO_INVALID"):
            read_account_diagnostic_approval(str(self.go))
        self.assertFalse((self.root / "diag_other.sqlite3").exists())

    def test_go_rejects_wrong_identity_without_spending_budget(self):
        self.manifest["approval_id"] = "M06_PRIVATE_ACCOUNT_COVERAGE_20261101"
        self.write_go()
        with self.assertRaisesRegex(M06ScopeDenied, "DIAG_GO_INVALID"):
            read_account_diagnostic_approval(str(self.go))
        # Constructor creates table but no claim nor debit.
        import sqlite3
        with sqlite3.connect(self.budget.path) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM m06_go_budget"
            ).fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
