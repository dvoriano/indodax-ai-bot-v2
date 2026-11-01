"""New owner-approved M06 18-GET read-only window and irreversible claim.

All HTTP is replaced by synthetic response fixtures. No credential or live
exchange access; all statuses are non-executable.
"""
from dataclasses import replace
from pathlib import Path
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m06_account_coverage import (
    COVERAGE_ID, START_EPOCH_MS, SEVEN_DAYS_MS, MAX_REQUESTS, PAIRS,
    coverage_windows, read_coverage_approval, M06CoverageApproval,
)
from indodax_ai_bot.m06_scoped_https import M06ScopeDenied, M06RequestBudget
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


class M06NewPrivateCoverageTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)
        self.end_ms = START_EPOCH_MS + SEVEN_DAYS_MS + 3600000
        self.now_ms = self.end_ms + 1000
        self.approval = M06CoverageApproval(
            COVERAGE_ID, self.end_ms, self.end_ms + 900000,
            MAX_REQUESTS, START_EPOCH_MS, self.end_ms, PAIRS,
        )
        self.budget = M06RequestBudget(str(self.root / "budget.sqlite3"))

    def runner(self, account=None, history=None, fills=None):
        account = account or {
            "uid":781, "canTrade":False, "canWithdraw":False,
            "canWithdrawFiat":False,
            "balances":[{"asset":"IDR","free":"90000","locked":"100"}],
        }
        records = {
            "/api/v2/account":account,
            "/api/v2/openOrders":[],
            "/api/v2/order/histories":
                {"data": []} if history is None else history,
            "/api/v2/myTrades":
                {"data": []} if fills is None else fills,
        }
        observed = []
        def fake_read(req):
            observed.append((req.path, req.query))
            return records[req.path]
        with patch("indodax_ai_bot.m06_scoped_https._RELEASE_APPROVAL_ID", COVERAGE_ID), \
             patch("indodax_ai_bot.m06_scoped_https._NETWORK_RELEASE_GO", True), \
             patch("indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
                   side_effect=fake_read), \
             patch("indodax_ai_bot.m06_account_coverage.time.time",
                   return_value=self.end_ms / 1000 + 0.5):
            outcome = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(self.root / "decision.sqlite3"),
            ).inspect_m06_account_coverage_once(
                approval=self.approval, budget=self.budget,
                api_key="fixture-api", secret_key="fixture-secret",
            )
        return outcome, observed

    def test_18_get_one_source_owner_no_wallet_authority(self):
        outcome, seen = self.runner()
        self.assertEqual(outcome.request_count, 18)
        self.assertEqual(len(seen), 18)
        self.assertEqual([x[0] for x in seen[:2]],
                         ["/api/v2/account", "/api/v2/openOrders"])
        self.assertEqual([p for p,_ in seen].count("/api/v2/order/histories"),8)
        self.assertEqual([p for p,_ in seen].count("/api/v2/myTrades"),8)
        self.assertTrue(all("symbol=" in q for _,q in seen[2:]))
        self.assertEqual(outcome.checked_pairs,4)
        self.assertEqual(outcome.checked_windows_per_pair,2)
        self.assertEqual(outcome.status,"M06_COVERAGE_SCOPED_READONLY_REJECT")
        self.assertFalse(outcome.spendable_idr_verified)
        self.assertFalse(outcome.external_peer_coverage_verified)
        self.assertFalse(outcome.full_account_history_verified)
        self.assertFalse(outcome.execution_allowed)
        self.assertNotIn("90000", repr(outcome))

    def test_fixed_7_day_windows_are_contiguous(self):
        a,b = coverage_windows(START_EPOCH_MS,self.end_ms)
        self.assertEqual(a[0],START_EPOCH_MS)
        self.assertEqual(a[1]-a[0],SEVEN_DAYS_MS-1)
        self.assertEqual(b[0],a[1]+1)
        self.assertEqual(b[1],self.end_ms)

    def test_non_7_day_or_unbounded_scope_denied(self):
        with self.assertRaisesRegex(M06ScopeDenied,"TWO_WINDOWS"):
            coverage_windows(START_EPOCH_MS,START_EPOCH_MS+1000)
        with self.assertRaisesRegex(M06ScopeDenied,"TIME_SCOPE"):
            coverage_windows(START_EPOCH_MS,START_EPOCH_MS+15*86400*1000)
        with self.assertRaisesRegex(M06ScopeDenied,"TIME_SCOPE"):
            coverage_windows(START_EPOCH_MS-1,self.end_ms)

    def test_owner_go_secure_manifest_validates_exact_scope(self):
        path=self.root/"go.json"
        body={
            "approval_id":COVERAGE_ID, "issued_ms":self.end_ms,
            "expires_ms":self.end_ms+900000,
            "max_requests":MAX_REQUESTS, "start_ms":START_EPOCH_MS,
            "end_ms":self.end_ms, "symbols":list(PAIRS),
        }
        path.write_text(json.dumps(body))
        path.chmod(0o600)
        actual=read_coverage_approval(str(path),now_ms=self.end_ms+500)
        self.assertEqual(actual.symbols,PAIRS)
        self.assertNotIn(COVERAGE_ID,repr(actual))
        body["symbols"][0]="btcidr"
        path.write_text(json.dumps(body))
        with self.assertRaisesRegex(M06ScopeDenied,"SYMBOLS"):
            read_coverage_approval(str(path),now_ms=self.end_ms+500)

    def test_altered_limit_or_expired_go_rejected(self):
        path=self.root/"go.json"
        body={
            "approval_id":COVERAGE_ID,"issued_ms":self.end_ms,
            "expires_ms":self.end_ms+900000,
            "max_requests":MAX_REQUESTS-1,
            "start_ms":START_EPOCH_MS,"end_ms":self.end_ms,
            "symbols":list(PAIRS),
        }
        path.write_text(json.dumps(body))
        path.chmod(0o600)
        with self.assertRaisesRegex(M06ScopeDenied,"GO_EXPIRED_OR_INVALID"):
            read_coverage_approval(str(path),now_ms=self.end_ms+500)
        body["max_requests"]=MAX_REQUESTS
        path.write_text(json.dumps(body))
        with self.assertRaisesRegex(M06ScopeDenied,"GO_EXPIRED_OR_INVALID"):
            read_coverage_approval(str(path),now_ms=self.end_ms+900001)

    def test_cannot_replay_spent_budget_after_18_get(self):
        self.runner()
        with self.assertRaisesRegex(M06ScopeDenied,"ALREADY_USED"):
            self.runner()

    def test_wrong_owner_release_never_sends_GET(self):
        observed=[]
        with patch("indodax_ai_bot.m06_scoped_https._RELEASE_APPROVAL_ID",None), \
             patch("indodax_ai_bot.m06_scoped_https._NETWORK_RELEASE_GO",False), \
             patch("indodax_ai_bot.m06_scoped_https._read_json_over_verified_tls",
                   side_effect=lambda r:observed.append(r)):
            with self.assertRaisesRegex(M06ScopeDenied,"GO_ABSENT"):
                self.runner_direct()
        self.assertEqual(observed,[])

    def runner_direct(self):
        return DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.root/"direct.sqlite3"),
        ).inspect_m06_account_coverage_once(
            approval=self.approval,budget=self.budget,
            api_key="fake",secret_key="fake",
        )

    def test_trade_enabled_key_fails_after_one_GET_and_claim_burned(self):
        with self.assertRaisesRegex(M06ScopeDenied,"KEY_NOT_PROVEN_VIEW_ONLY"):
            self.runner(account={
                "uid":781,"canTrade":True,"canWithdraw":False,
                "canWithdrawFiat":False,
                "balances":[{"asset":"IDR","free":"90000","locked":"0"}],
            })
        with self.assertRaisesRegex(M06ScopeDenied,"ALREADY_USED"):
            self.runner()

    def test_full_1000_records_rejected_as_truncated(self):
        data={"data":[{
            "orderId":str(i),"clientOrderId":"c"+str(i),"symbol":"injidr",
            "side":"BUY","status":"FILLED","oriQty":"1","executedQty":"1",
        } for i in range(1000)]}
        with self.assertRaisesRegex(M06ScopeDenied,"POSSIBLY_TRUNCATED"):
            self.runner(history=data)

    def test_old_budget_claims_are_not_reissued(self):
        from indodax_ai_bot.m06_scoped_https import M06ScopedApproval
        now=int(time.time()*1000)
        self.budget.debit(
            M06ScopedApproval("M06_PRIVATE_VIEW_ONLY_ONE_SHOT_20261031",
                              now-1000,now+300000,4,"injidr"),
            api_key="historical-fixture",now_ms=now,
        )
        with self.assertRaisesRegex(M06ScopeDenied,"ALREADY_USED"):
            self.budget.claim_fresh_coverage_once(COVERAGE_ID)




class M06ReuseStoredViewOnlyCredentialsTests(unittest.TestCase):
    """V2-only secure on-disk credential reuse without TTY reprompt or GET."""

    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.home = Path(self.t.name)
        self.state = self.home / ".local" / "state" / "indodax-ai-bot-v2"
        self.state.mkdir(parents=True, mode=0o700)
        self.path = self.state / "viewonly-credentials.json"
        self.data = {"api_key":"synthetic-key", "secret_key":"synthetic-secret"}

    def stored(self, *, mode=0o600):
        self.path.write_text(json.dumps(self.data))
        self.path.chmod(mode)

    def read(self, choice=None):
        from indodax_ai_bot.m06_owner_coverage import _existing_v2_credentials
        environ = {} if choice is None else {
            "M06_V2_VIEWONLY_CREDENTIALS_FILE":choice
        }
        with patch("pathlib.Path.home", return_value=self.home), \
             patch.dict(os.environ, environ, clear=True):
            return _existing_v2_credentials()

    def test_reuses_canonical_v2_file_without_any_new_api_input(self):
        self.stored()
        self.assertEqual(self.read(), self.path)
        self.assertEqual(self.path.read_text(), json.dumps(self.data))

    def test_historical_temp_credential_file_missing_fails_closed(self):
        self.assertIsNone(self.read())

    def test_fails_closed_when_existing_file_is_world_readable(self):
        self.stored(mode=0o644)
        with self.assertRaisesRegex(ValueError,"NOT_SAFE"):
            self.read()

    def test_explicit_existing_v2_path_works_no_v1_or_other_roots(self):
        self.stored()
        self.assertEqual(self.read(str(self.path)),self.path)
        outside=self.home/"other-bot"/"credentials.json"
        outside.parent.mkdir()
        outside.write_text(json.dumps(self.data))
        outside.chmod(0o600)
        with self.assertRaisesRegex(ValueError,"NOT_SAFE"):
            self.read(str(outside))

    def test_symlinked_v2_viewonly_file_denied(self):
        real = self.state/"original.json"
        real.write_text(json.dumps(self.data))
        real.chmod(0o600)
        self.path.symlink_to(real)
        with self.assertRaisesRegex(ValueError,"NOT_SAFE"):
            self.read()

    def test_existing_invalid_secret_shape_denied(self):
        self.stored()
        self.path.write_text(json.dumps({"api_key":"test", "secret_key":""}))
        self.path.chmod(0o600)
        with self.assertRaisesRegex(ValueError,"NOT_SAFE"):
            self.read()

if __name__=="__main__":
    unittest.main()
