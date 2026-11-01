"""M06 private UID -> existing V2 rebuy ledger: offline, reject-only.

No Indodax calls. No real keys. No new live DB, executor, or service.
"""
import os
import contextlib
import io
import json
import sqlite3
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.m06_ownership_ledger import (
    M06OwnershipLedger, OwnershipConflict, ProtectedRebuyReview,
)
from indodax_ai_bot.m06_private_payloads import OrderRecord, FillRecord
from indodax_ai_bot.m06_account_review import reconcile_readonly_private_snapshot
from indodax_ai_bot.m06_private_payloads import AccountBalances
from indodax_ai_bot.m06_scoped_https import (
    M06RequestBudget, M06ScopedApproval,
)


class M06UIDBoundReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.path = self.dir / "existing.sqlite3"
        self.uid = 421
        self.account_key = M06OwnershipLedger.account_scope_from_exchange_uid(
            self.uid,
        )
        self.ledger = M06OwnershipLedger(self.path)
        self.ledger.record_local_intent(
            account_key=self.account_key,
            order_id="flokiidr-limit-1", client_id="v2-own-1",
            symbol="flokiidr", side="SELL",
        )
        self.ledger.observe(
            account_key=self.account_key,
            order=OrderRecord(
                "flokiidr-limit-1", "flokiidr", "SELL", "FILLED",
                Decimal("3"), Decimal("3"), "v2-own-1",
            ),
            fills=(FillRecord(
                "trade-uid-1", "flokiidr-limit-1", "flokiidr",
                Decimal("3"), Decimal("100"), Decimal("0"),
                client_order_id="v2-own-1", is_buyer=False,
                quote_quantity=Decimal("300"),
            ),),
        )
        self.ledger.protect_manual_asset_floor(
            account_key=self.account_key, asset="FLOKI",
            minimum_qty=Decimal("750"),
        )
        self.path.chmod(0o600)

    def test_private_readonly_binding_recovers_only_matching_uid(self):
        correct = M06OwnershipLedger.inspect_existing_account_readonly(
            db_path=str(self.path), account_uid=self.uid,
        )
        self.assertEqual(correct.total_idr_local_protected, Decimal("300"))
        self.assertEqual(correct.floki_manual_qty_local_floor, Decimal("750"))
        self.assertFalse(correct.exchange_proceeds_verified)
        self.assertFalse(correct.execution_allowed)
        with self.assertRaisesRegex(OwnershipConflict, "ACCOUNT_UNBOUND"):
            M06OwnershipLedger.inspect_existing_account_readonly(
                db_path=str(self.path), account_uid=422,
            )
        restart = M06OwnershipLedger.inspect_existing_account_readonly(
            db_path=str(self.path), account_uid=self.uid,
        )
        self.assertEqual(restart, correct)

    def test_missing_or_broad_permission_db_is_denied_not_created(self):
        missing = self.dir / "absent.sqlite3"
        with self.assertRaisesRegex(OwnershipConflict, "UNAVAILABLE"):
            M06OwnershipLedger.inspect_existing_account_readonly(
                db_path=str(missing), account_uid=self.uid,
            )
        self.assertFalse(missing.exists())
        self.path.chmod(0o644)
        with self.assertRaisesRegex(OwnershipConflict, "INSECURE"):
            M06OwnershipLedger.inspect_existing_account_readonly(
                db_path=str(self.path), account_uid=self.uid,
            )
        self.path.chmod(0o600)
        link = self.dir / "symlink.sqlite3"
        link.symlink_to(self.path)
        with self.assertRaisesRegex(OwnershipConflict, "INSECURE"):
            M06OwnershipLedger.inspect_existing_account_readonly(
                db_path=str(link), account_uid=self.uid,
            )
        with self.assertRaisesRegex(OwnershipConflict, "UID_INVALID"):
            M06OwnershipLedger.inspect_existing_account_readonly(
                db_path=str(self.path), account_uid=True,
            )

    def test_local_evidence_can_never_grant_verified_capital(self):
        bound = M06OwnershipLedger.inspect_existing_account_readonly(
            db_path=str(self.path), account_uid=self.uid,
        )
        account = AccountBalances(
            self.uid,
            (("IDR", Decimal("1000"), Decimal("0")),
             ("FLOKI", Decimal("1000"), Decimal("0"))),
            Decimal("1000"), Decimal("0"),
        )
        review = reconcile_readonly_private_snapshot(
            account=account, open_orders=(), order_history=(), fills=(),
            symbol="injidr", local_rebuy=bound,
        )
        self.assertTrue(review.local_ledger_account_bound)
        self.assertTrue(review.local_rebuy_hold_present)
        self.assertTrue(review.local_manual_floor_present)
        self.assertFalse(review.spendable_capital_verified)
        self.assertFalse(review.protected_rebuy_ledger_verified)
        self.assertFalse(review.execution_allowed)
        forged = ProtectedRebuyReview(
            Decimal("300"), Decimal("0"), Decimal("300"), 1,
            exchange_proceeds_verified=True,
        )
        with self.assertRaisesRegex(ValueError, "AUTHORITY_FORGED"):
            reconcile_readonly_private_snapshot(
                account=account, open_orders=(), order_history=(),
                fills=(), symbol="injidr", local_rebuy=forged,
            )

    def test_original_orchestrator_four_reads_do_not_write_local_ledger(self):
        now = int(time.time() * 1000)
        approval = M06ScopedApproval(
            "M06_PRIVATE_READONLY_GO_20261031", now-1000,
            now+899000, 20, "injidr",
        )
        source = {
            "/api/v2/account": {
                "uid": self.uid, "canTrade": False,
                "canWithdraw": False, "canWithdrawFiat": False,
                "balances": [
                    {"asset":"IDR","free":"1000","locked":"0"},
                    {"asset":"FLOKI","free":"1000","locked":"0"},
                ],
            },
            "/api/v2/openOrders": [],
            "/api/v2/order/histories": {"data":[]},
            "/api/v2/myTrades": {"data":[]},
        }
        with sqlite3.connect(self.path) as db:
            before = db.execute(
                "SELECT count(*) FROM m06_special_rebuy_observations",
            ).fetchone()[0]
        with patch(
            "indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
            side_effect=lambda req: source[req.path],
        ):
            bot = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(self.dir / "journal.sqlite3"))
            result = bot.inspect_m06_private_readonly_once(
                approval=approval,
                budget=M06RequestBudget(str(self.dir / "budget.sqlite3")),
                api_key="fixture-key", secret_key="fixture-secret",
                local_ownership_db=str(self.path),
            )
        self.assertEqual(result.request_count_this_run, 4)
        self.assertTrue(result.local_ledger_account_bound)
        self.assertTrue(result.local_rebuy_hold_present)
        self.assertTrue(result.local_manual_floor_present)
        self.assertFalse(result.capital_spendable_verified)
        self.assertFalse(result.cross_bot_ownership_verified)
        self.assertFalse(result.bot_owned_fills_verified)
        self.assertFalse(result.execution_allowed)
        with sqlite3.connect(self.path) as db:
            after = db.execute(
                "SELECT count(*) FROM m06_special_rebuy_observations",
            ).fetchone()[0]
        self.assertEqual(before, after)

    def test_original_cli_reuses_same_ledger_without_raw_account_output(self):
        now = int(time.time() * 1000)
        go = self.dir / "go.json"
        go.write_text(json.dumps({
            "approval_id": "M06_PRIVATE_READONLY_GO_20261031",
            "issued_ms": now - 1000,
            "expires_ms": now + 899000,
            "max_requests": 20,
            "symbol": "injidr",
        }))
        go.chmod(0o600)
        creds = self.dir / "fixture-creds.json"
        creds.write_text(json.dumps({
            "api_key": "fixture-key",
            "secret_key": "fixture-secret",
        }))
        creds.chmod(0o600)
        payload = {
            "/api/v2/account": {
                "uid": self.uid, "canTrade": False,
                "canWithdraw": False, "canWithdrawFiat": False,
                "balances": [
                    {"asset": "IDR", "free": "12345678", "locked": "0"},
                    {"asset": "FLOKI", "free": "1000", "locked": "0"},
                ],
            },
            "/api/v2/openOrders": [],
            "/api/v2/order/histories": {"data": []},
            "/api/v2/myTrades": {"data": []},
        }
        stdout = io.StringIO()
        with patch.dict(os.environ, {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
        }), patch(
            "indodax_ai_bot.__main__._m06_fixed_budget_db",
            return_value=str(self.dir / "budget-cli.sqlite3"),
        ), patch(
            "indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
            side_effect=lambda req: payload[req.path],
        ), contextlib.redirect_stdout(stdout):
            rc = main([
                "--db", str(self.path),
                "--m06-private-readonly-once",
                "--m06-go-file", str(go),
                "--m06-credentials-file", str(creds),
            ])
        self.assertEqual(rc, 0)
        out = json.loads(stdout.getvalue())
        self.assertTrue(out["local_ledger_account_bound"])
        self.assertTrue(out["local_rebuy_hold_present"])
        self.assertTrue(out["local_manual_floor_present"])
        self.assertFalse(out["execution_allowed"])
        self.assertFalse(out["capital_spendable_verified"])
        self.assertNotIn("12345678", stdout.getvalue())
        self.assertNotIn("fixture-secret", stdout.getvalue())
        self.assertNotIn(str(self.uid), stdout.getvalue())

    def test_absent_local_ledger_report_does_not_promote(self):
        now = int(time.time() * 1000)
        approval = M06ScopedApproval(
            "M06_PRIVATE_READONLY_GO_20261031", now-1000,
            now+899000, 20, "injidr",
        )
        source = {
            "/api/v2/account": {
                "uid": self.uid, "canTrade": False,
                "canWithdraw": False, "canWithdrawFiat": False,
                "balances": [{"asset":"IDR","free":"1000","locked":"0"}],
            },
            "/api/v2/openOrders": [],
            "/api/v2/order/histories": {"data":[]},
            "/api/v2/myTrades": {"data":[]},
        }
        missing = self.dir / "no-ledger.sqlite3"
        with patch(
            "indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
            side_effect=lambda req: source[req.path],
        ):
            bot = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(self.dir / "journal.sqlite3"))
            result = bot.inspect_m06_private_readonly_once(
                approval=approval,
                budget=M06RequestBudget(str(self.dir / "budget.sqlite3")),
                api_key="fixture-key", secret_key="fixture-secret",
                local_ownership_db=str(missing),
            )
        self.assertFalse(missing.exists())
        self.assertFalse(result.local_ledger_account_bound)
        self.assertFalse(result.local_rebuy_hold_present)
        self.assertFalse(result.execution_allowed)


if __name__ == "__main__":
    unittest.main()
