"""Offline evidence for M06 owner GO, TLS-only probes, quota and key veto.

All HTTP calls are patched: this suite MUST NOT access Indodax.
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from indodax_ai_bot.m06_private_readonly import M06ReadOnlyBlocked
from indodax_ai_bot.m06_scoped_https import (
    M06RequestBudget, M06ScopedApproval, M06ScopeDenied,
    enforce_view_only_permissions, read_approval, read_view_only_credentials,
    run_scoped_private_probe, _read_json_over_verified_tls,
)
from indodax_ai_bot.m06_signed_readonly import prepare_signed_get


class M06PrivateGOTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.now = int(time.time() * 1000)
        self.approval = M06ScopedApproval(
            "M06_PRIVATE_READONLY_GO_20261031",
            self.now - 1000, self.now + 899000, 20, "injidr")
        self.db = str(self.base / "m06-go-budget.sqlite3")

    def _private_file(self, name, payload):
        p = self.base / name
        p.write_text(json.dumps(payload), encoding="utf-8")
        p.chmod(0o600)
        return str(p)

    def test_manifest_and_credential_permissions_enforced(self):
        manifest = {
            "approval_id": self.approval.approval_id,
            "issued_ms": self.approval.issued_ms,
            "expires_ms": self.approval.expires_ms,
            "max_requests": self.approval.max_requests,
            "symbol": self.approval.symbol,
        }
        path = self._private_file("go.json", manifest)
        self.assertEqual(read_approval(path, now_ms=self.now), self.approval)
        credentials = self._private_file(
            "view.json", {"api_key":"fixture-key","secret_key":"fixture-secret"})
        self.assertEqual(read_view_only_credentials(credentials),
                         ("fixture-key","fixture-secret"))
        os.chmod(credentials, 0o644)
        with self.assertRaisesRegex(M06ScopeDenied, "OWNER_OR_MODE"):
            read_view_only_credentials(credentials)
        os.chmod(credentials, 0o600)
        os.symlink(credentials, self.base / "credential-link")
        with self.assertRaises(M06ScopeDenied):
            read_view_only_credentials(str(self.base / "credential-link"))
        manifest["expires_ms"] = self.approval.issued_ms + 900001
        oversized = self._private_file("bad-go.json", manifest)
        with self.assertRaisesRegex(M06ScopeDenied, "EXPIRED_OR_OUT_OF_SCOPE"):
            read_approval(oversized, now_ms=self.now)

    def test_budget_is_persistent_and_nonrenewable(self):
        budget = M06RequestBudget(self.db)
        for i in range(20):
            self.assertEqual(
                M06RequestBudget(self.db).debit(
                    self.approval, api_key="fixture-key", now_ms=self.now), i + 1)
        with self.assertRaisesRegex(M06ScopeDenied, "QUOTA_EXHAUSTED"):
            budget.debit(self.approval, api_key="fixture-key", now_ms=self.now)
        with self.assertRaisesRegex(M06ScopeDenied, "CHANGE_OR_RENEWAL"):
            budget.debit(self.approval, api_key="another-key", now_ms=self.now)
        with self.assertRaisesRegex(M06ScopeDenied, "EXPIRED"):
            budget.debit(self.approval, api_key="fixture-key",
                         now_ms=self.approval.expires_ms)

    def test_reject_trade_or_withdraw_key_and_absent_flags(self):
        good = {"canTrade":False, "canWithdraw":False,
                "canWithdrawFiat":False}
        enforce_view_only_permissions(good)
        for bad in ({"canTrade":True, **{
                "canWithdraw":False,"canWithdrawFiat":False}},
                {"canTrade":False,"canWithdraw":True,"canWithdrawFiat":False},
                {"canTrade":False,"canWithdraw":False,"canWithdrawFiat":True},
                {"canTrade":False,"canWithdraw":False},
                {"canTrade":0,"canWithdraw":False,"canWithdrawFiat":False}):
            with self.subTest(bad=bad), self.assertRaisesRegex(
                    M06ScopeDenied, "NOT_PROVEN_VIEW_ONLY"):
                enforce_view_only_permissions(bad)

    def test_four_gets_scope_account_first_and_zero_trade_authority(self):
        seen = []
        fixtures = {
            "/api/v2/account":{
                "uid": 123, "canTrade":False,
                "canWithdraw":False, "canWithdrawFiat":False,
                "balances":[{"asset":"IDR","free":"100000","locked":"5000"}],
            },
            "/api/v2/openOrders": [],
            "/api/v2/order/histories":{"data":[]},
            "/api/v2/myTrades":{"data":[]},
        }
        def fixture(request):
            seen.append((request.path, request.query, request.headers))
            return fixtures[request.path]
        with patch("indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
                   side_effect=fixture):
            result = run_scoped_private_probe(
                approval=self.approval, budget=M06RequestBudget(self.db),
                api_key="fixture-key", secret_key="fixture-secret")
        self.assertEqual([row[0] for row in seen], [
            "/api/v2/account", "/api/v2/openOrders",
            "/api/v2/order/histories", "/api/v2/myTrades"])
        self.assertEqual(result.request_count_this_run, 4)
        self.assertFalse(result.execution_allowed)
        self.assertFalse(result.history_complete)
        self.assertFalse(result.cross_bot_ownership_verified)
        self.assertTrue(result.view_only_permissions_verified)
        self.assertNotIn("fixture-secret",repr(result))
        self.assertTrue(all(
            "fixture-key" not in query and "fixture-secret" not in query
            for _, query, _ in seen))
        with M06RequestBudget(self.db)._connect() as conn:
            consumed = conn.execute(
                "SELECT consumed FROM m06_go_budget WHERE singleton=1").fetchone()[0]
        self.assertEqual(consumed,4)

    def test_permission_failure_stops_after_account_and_debits_one(self):
        seen = []
        def fixture(request):
            seen.append(request.path)
            return {"canTrade":True, "canWithdraw":False,
                    "canWithdrawFiat":False}
        with patch("indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
                   side_effect=fixture):
            with self.assertRaisesRegex(M06ScopeDenied,"NOT_PROVEN_VIEW_ONLY"):
                run_scoped_private_probe(
                    approval=self.approval, budget=M06RequestBudget(self.db),
                    api_key="fixture-key", secret_key="fixture-secret")
        self.assertEqual(seen, ["/api/v2/account"])
        with M06RequestBudget(self.db)._connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT consumed FROM m06_go_budget WHERE singleton=1"
            ).fetchone()[0], 1)

    def test_expired_owner_go_cannot_be_renewed_by_local_manifest(self):
        # Even a syntactically valid signed request cannot reach HTTPS
        # from this unapproved source SHA. A new GO requires a new release.
        prepared=prepare_signed_get(
            path="/api/v2/account",api_key="fixture-key",
            secret_key="fixture-secret",timestamp_ms=self.now)
        with patch("indodax_ai_bot.m06_scoped_https.http.client.HTTPSConnection"
                   ) as https:
            with self.assertRaisesRegex(M06ScopeDenied,
                                        "REQUIRES_NEW_RELEASE"):
                _read_json_over_verified_tls(prepared)
            https.assert_not_called()

    def test_original_four_reads_reconcile_rebuy_and_ownership_as_blocked(self):
        now = self.now
        fixtures = {
            "/api/v2/account": {
                "uid": 123, "canTrade": False, "canWithdraw": False,
                "canWithdrawFiat": False,
                "balances": [
                    {"asset": "IDR", "free": "750000", "locked": "100000"},
                    {"asset": "FLOKI", "free": "1000000", "locked": "0"},
                    {"asset": "PEPE", "free": "250000", "locked": "0"},
                ],
            },
            "/api/v2/openOrders": [
                {"orderId": 46, "fullOrderId": "injidr-limit-46",
                 "clientOrderId": "other-owner-46", "symbol": "INJIDR",
                 "side": "BUY", "status": "NEW", "origQty": "10",
                 "executedQty": "0"},
            ],
            "/api/v2/order/histories": {"data": []},
            "/api/v2/myTrades": {"data": []},
        }
        with patch("indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
                   side_effect=lambda req: fixtures[req.path]):
            result = run_scoped_private_probe(
                approval=self.approval,
                budget=M06RequestBudget(self.db),
                api_key="fixture-key", secret_key="fixture-secret")
        self.assertEqual(result.status, "M06_VIEW_ONLY_RECONCILED_REJECT")
        self.assertEqual(result.pending_buy_orders, 1)
        self.assertEqual(result.open_orders_count, 1)
        self.assertFalse(result.capital_spendable_verified)
        self.assertFalse(result.protected_rebuy_verified)
        self.assertFalse(result.special_asset_ownership_verified)
        self.assertFalse(result.cross_bot_ownership_verified)
        self.assertFalse(result.execution_allowed)
        self.assertIn("UNVERIFIED_REBUY", result.reconciliation_status)

    def test_conflicting_private_fill_returns_safe_fixed_error(self):
        fixtures = {
            "/api/v2/account": {
                "uid": 1, "canTrade": False, "canWithdraw": False,
                "canWithdrawFiat": False,
                "balances": [{"asset": "IDR", "free": "100", "locked": "0"}],
            },
            "/api/v2/openOrders": [{
                "orderId": 46, "fullOrderId": "injidr-limit-46",
                "symbol": "INJIDR", "side": "BUY", "status": "NEW",
                "clientOrderId": "v2-46", "origQty": "1", "executedQty": "0.5",
            }],
            "/api/v2/order/histories": {"data": []},
            "/api/v2/myTrades": {"data": [{
                "tradeId": "t-1", "orderId": "injidr-limit-46",
                "clientOrderId": "v2-46", "symbol": "injidr",
                "qty": "0.6", "price": "100", "commission": "0",
                "isBuyer": True,
            }]},
        }
        with patch("indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
                   side_effect=lambda req: fixtures[req.path]):
            with self.assertRaisesRegex(
                M06ScopeDenied, "PRIVATE_DATA_RECONCILIATION_REJECT"
            ):
                run_scoped_private_probe(
                    approval=self.approval,
                    budget=M06RequestBudget(self.db),
                    api_key="fixture-key", secret_key="fixture-secret")

    def test_fresh_one_shot_go_is_exact_four_gets_and_inj_idr(self):
        self.assertEqual(
            self.approval.symbol, "injidr")
        from indodax_ai_bot.m06_scoped_https import read_approval
        base = {
            "approval_id": "M06_PRIVATE_VIEW_ONLY_ONE_SHOT_20261031",
            "issued_ms": self.now - 1000,
            "expires_ms": self.now + 899000,
            "max_requests": 4,
            "symbol": "injidr",
        }
        path = self._private_file("new-approval.json", base)
        accepted = read_approval(path, now_ms=self.now)
        self.assertEqual(accepted.max_requests, 4)
        for changed in ({"max_requests": 20}, {"symbol": "btcidr"},
                        {"expires_ms": self.now + 901000}):
            payload = {**base, **changed}
            name = "bad-" + str(len(str(changed))) + "-" + str(list(changed))
            path = self._private_file(name + ".json", payload)
            with self.assertRaises(M06ScopeDenied):
                read_approval(path, now_ms=self.now)

    def test_live_release_rejects_old_approval_before_debit(self):
        with patch("indodax_ai_bot.m06_scoped_https._NETWORK_RELEASE_GO", True):
            with patch("indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls") as network:
                with self.assertRaisesRegex(
                        M06ScopeDenied, "NETWORK_RELEASE_SCOPE_DENIED"):
                    run_scoped_private_probe(
                        approval=self.approval,
                        budget=M06RequestBudget(self.db),
                        api_key="fixture-key", secret_key="fixture-secret")
                network.assert_not_called()
        with M06RequestBudget(self.db)._connect() as db:
            self.assertIsNone(db.execute(
                "SELECT consumed FROM m06_go_budget WHERE singleton=1"
            ).fetchone())

    def test_activated_direct_https_without_debit_is_blocked(self):
        prepared = prepare_signed_get(
            path="/api/v2/account",
            api_key="fixture-key", secret_key="fixture-secret",
            timestamp_ms=self.now,
        )
        with patch("indodax_ai_bot.m06_scoped_https._NETWORK_RELEASE_GO", True):
            with patch("indodax_ai_bot.m06_scoped_https.http.client.HTTPSConnection") as https:
                with self.assertRaisesRegex(
                        M06ScopeDenied, "WITHOUT_ATOMIC_BUDGET"):
                    _read_json_over_verified_tls(prepared)
                https.assert_not_called()

    def test_one_shot_account_first_four_gets_exhausts_budget_forever(self):
        approval = M06ScopedApproval(
            "M06_PRIVATE_VIEW_ONLY_ONE_SHOT_20261031",
            self.now - 1000, self.now + 899000, 4, "injidr",
        )
        rows = {
            "/api/v2/account": {
                "uid": 987, "canTrade": False, "canWithdraw": False,
                "canWithdrawFiat": False,
                "balances": [{"asset": "IDR", "free": "0", "locked": "0"}],
            },
            "/api/v2/openOrders": [],
            "/api/v2/order/histories": {"data": []},
            "/api/v2/myTrades": {"data": []},
        }
        seen = []
        with patch("indodax_ai_bot.m06_scoped_https._NETWORK_RELEASE_GO", True):
            with patch("indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
                       side_effect=lambda request: (
                           seen.append(request.path), rows[request.path]
                       )[1]):
                review = run_scoped_private_probe(
                    approval=approval,
                    budget=M06RequestBudget(self.db),
                    api_key="fixture-key", secret_key="fixture-secret",
                )
                self.assertFalse(review.execution_allowed)
                self.assertFalse(review.history_complete)
                self.assertEqual(len(seen), 4)
                with self.assertRaisesRegex(
                        M06ScopeDenied, "QUOTA_EXHAUSTED"):
                    run_scoped_private_probe(
                        approval=approval,
                        budget=M06RequestBudget(self.db),
                        api_key="fixture-key", secret_key="fixture-secret",
                    )
                self.assertEqual(len(seen), 4)

    def test_unapproved_route_cannot_hit_https(self):
        with self.assertRaisesRegex(M06ScopeDenied, "ROUTE_DENIED"):
            _read_json_over_verified_tls(object())
        with self.assertRaisesRegex(M06ReadOnlyBlocked,"PATH_DENIED"):
            prepare_signed_get(
                path="/api/v2/order",api_key="fixture-key",
                secret_key="fixture-secret",timestamp_ms=self.now)


if __name__ == "__main__":
    unittest.main()
