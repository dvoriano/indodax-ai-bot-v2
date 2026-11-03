"""M09-02 fault matrix on ORIGINAL V2 CLI, scanner, M04, M05 and M07.

All public HTTP uses deterministic local fixture or a real V2 SQLite
rate-budget limiter; no remote HTTP, credentials, paid AI, private GET,
order submissions or trading permission. M05 shadow button tests are
CONSENT ONLY and do not authorize a trade.
"""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal as D
from io import StringIO
from pathlib import Path
import json
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m07_order_adapter import M07OrderDraft
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator, CycleResult
from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.public_market import IndodaxPublicMarket, PublicDataError
from indodax_ai_bot.public_rest_budget import SharedPublicRestBudget
from tests.test_m09_01_original_shadow_mainpath import NOW,public_bulk

RULES={
    "price_precision":"1","quantity_increment":"0.1",
    "trade_min_base_currency":"10000",
    "trade_min_traded_currency":"1",
    "is_market_suspended":0,"is_maintenance":0,
}


def draft(price="100",quantity="1000",client="m09fault001"):
    return M07OrderDraft(
        symbol="BTCIDR",side="BUY",order_type="LIMIT",
        client_order_id=client,price=D(price),quantity=D(quantity),
    )


def public_candidate(at=NOW-5):
    return {
        "stage":"M07_SAME_CANDIDATE_PUBLIC_PREFLIGHT",
        "status":"M07_PUBLIC_SOURCE_BOUND_RULES_REFERENCE_ONLY",
        "candidate_pair":"btc_idr","observed_pair":"btc_idr",
        "same_candidate_and_closed_minute":True,
        "execution_allowed":False,"observed_epoch":at,
    }


def account_snapshot():
    return {
        "stage":"M06_ACTUAL_IDR_VIEWONLY",
        "status":"M06_IDR_BALANCE_OBSERVED_SOURCE_RESERVE_UNVERIFIED",
        "view_only_permissions_verified":True,"requests_completed":2,
        "execution_allowed":False,
        "all_peer_liabilities_verified":True,
        "spendable_idr_verified":True,"source_authenticated":True,
    }


class OriginalM09FaultChainTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/"decisions.sqlite3"
        self.app=DailyScalpingOrchestrator(Settings(),DecisionJournal(self.db))

    def run_source(self,*,cycle=None,bulk=None,owner=None):
        app=owner or self.app
        if cycle is not None:
            with patch.object(app,"run_bulk_deep_cycle",
                              return_value=cycle):
                return app.run_m09_original_shadow_mainpath_once("m09faultsrc")
        fixed=public_bulk() if bulk is None else bulk
        with (
            patch.object(app,"_scan_with_early_momentum",
                         return_value=object()),
            patch("indodax_ai_bot.orchestrator.apply_deep_volume_floor",
                  side_effect=lambda observed,**kw:observed),
            patch("indodax_ai_bot.orchestrator.bulk_public_deep_scan",
                  return_value=fixed),
            patch("indodax_ai_bot.orchestrator.time.time",
                  return_value=NOW),
        ):
            return app.run_m09_original_shadow_mainpath_once("m09faultsrc")

    def review(self,**changes):
        values=dict(
            draft=draft(),pair_rules=dict(RULES),
            best_bid_idr=D("100"),best_ask_idr=D("101"),
            free_idr=D("920147"),locked_idr=D("0"),
            floki_rebuy_idr=D("0"),pepe_rebuy_idr=D("0"),
            commitments_not_locked_idr=D("0"),
            buy_all_in_fee_bps=D("25"),sell_all_in_fee_bps=D("25"),
            max_spread_bps=D("150"),
            public_candidate=public_candidate(),
            account_snapshot=account_snapshot(),
            now_epoch=NOW,
        )
        values.update(changes)
        return self.app.review_m09_original_fault_preflight_offline(**values)

    def test_stale_or_missing_signed_g1_is_not_a_final_decision(self):
        r=self.run_source()
        self.assertTrue(r["m09_original_public_candle_time_fresh_reference"])
        self.assertTrue(r["m09_unattested_source_never_clears_risk"])
        self.assertTrue(r["m09_risk_veto_or_source_unavailable"])
        self.assertFalse(r["independent_g1_issuer_verified"])
        self.assertIsNone(r["owner_final_decision"])
        bulk=public_bulk()
        bulk["screening_observed_epoch"]=NOW-250
        bulk["eligible_cohort"]["closed_1m_epoch"]=NOW-310
        stale=self.run_source(bulk=bulk)
        self.assertFalse(stale["m09_original_public_candle_time_fresh_reference"])
        self.assertEqual(stale["m04_completed_role_count"],0)
        self.assertFalse(stale["execution_allowed"])

    def test_public_fake_g1_or_order_authority_rejected(self):
        for key in ("g1_verified","execution_allowed","trade_authorized"):
            bulk=public_bulk()
            bulk[key]=True
            with self.subTest(key=key),self.assertRaisesRegex(
                    ValueError,"M09_PUBLIC_CANNOT_PROMOTE_G1_OR_EXECUTION"):
                self.run_source(bulk=bulk)

    def test_public_fake_signed_g1_issuer_rejected(self):
        bulk=public_bulk()
        bulk["g1_source_admission"]["g1_receipt_issuance_allowed"]=True
        with self.assertRaisesRegex(
                ValueError,"M09_PUBLIC_G1_ISSUER_NOT_ATTESTED"):
            self.run_source(bulk=bulk)

    def test_risk_veto_and_rejecting_three_roles_cannot_authorize(self):
        result=CycleResult(
            "m09faultsrc",None,"G1_PUBLIC_BULK_SOURCE_UNVERIFIED",False,
            bulk=public_bulk(),
            ai_shadow={"stage":"M04A_THREE_ROLE_SHADOW",
                       "status":"REJECT",
                       "reason":"RISK_VETO_SCOPED_SYNTHETIC",
                       "completed_roles":["MARKET","RISK"],
                       "execution_allowed":False},
            stage_disposition="ADVISORY_ONLY_NO_OWNER_DECISION",
        )
        r=self.run_source(cycle=result)
        self.assertTrue(r["m09_risk_veto_or_source_unavailable"])
        self.assertEqual(r["m04_completed_role_count"],2)
        self.assertFalse(r["m04_paid_ai_provider_called"])
        self.assertFalse(r["execution_allowed"])

    def test_ai_cannot_create_final_trade_permission(self):
        for bad in (
            {"execution_allowed":True},{"final_decision":"PASS"},
            {"live_trading_authorized":True},
        ):
            ai={"status":"REJECT","reason":"RISK_VETO",
                "completed_roles":[],"execution_allowed":False,**bad}
            out=CycleResult(
                "m09faultsrc",None,"G1_PUBLIC_BULK_SOURCE_UNVERIFIED",False,
                bulk=public_bulk(),ai_shadow=ai,
                stage_disposition="ADVISORY_ONLY_NO_OWNER_DECISION")
            with self.subTest(bad=bad),self.assertRaisesRegex(
                    ValueError,"M09_RISK_SHADOW_CANNOT_AUTHORIZE_FINAL"):
                self.run_source(cycle=out)

    def test_manual_auto_manual_owner_mode_not_executable(self):
        ledger=OwnerDecisionLedger(
            self.db.parent/"m05-ledger.sqlite3",owner_user_id=70001)
        app=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.db),owner_control=ledger)
        base=self.run_source(owner=app)
        self.assertEqual(base["m09_m05_observed_mode_reference"],"MANUAL")
        switched=ledger.switch_mode(
            user_id=70001,chat_id=70001,mode="AUTO",now_epoch=NOW)
        self.assertTrue(switched["accepted"])
        auto=self.run_source(owner=app)
        self.assertEqual(auto["m09_m05_observed_mode_reference"],"AUTO")
        self.assertTrue(auto["m09_auto_mode_cannot_issue_order_final"])
        self.assertIsNone(auto["owner_final_decision"])
        ledger.switch_mode(
            user_id=70001,chat_id=70001,mode="MANUAL",
            now_epoch=NOW+1)
        manual=self.run_source(owner=app)
        self.assertEqual(manual["m09_m05_observed_mode_reference"],"MANUAL")
        self.assertFalse(manual["execution_allowed"])

    def test_owner_callback_one_use_and_mode_transition_no_order(self):
        ledger=OwnerDecisionLedger(
            self.db.parent/"owner-buttons.sqlite3",owner_user_id=90001)
        proposal=ledger.issue_offline_shadow_proposal(
            pair="btc_idr",digest="a"*64,
            closed_1m_epoch=NOW-30,
            entry_price_idr="100",quantity="1000",
            now_epoch=NOW,source_expires_epoch=NOW+65,
        )
        self.assertTrue(proposal["accepted"])
        first=ledger.apply_callback(
            user_id=90001,chat_id=90001,
            callback_data=proposal["callbacks"]["PASS"],
            now_epoch=NOW+1,
        )
        self.assertTrue(first["accepted"])
        self.assertFalse(first["execution_allowed"])
        repeat=ledger.apply_callback(
            user_id=90001,chat_id=90001,
            callback_data=proposal["callbacks"]["PASS"],
            now_epoch=NOW+2,
        )
        self.assertFalse(repeat["accepted"])
        self.assertEqual(repeat["reason"],"CALLBACK_ALREADY_CONSUMED")
        self.assertFalse(repeat["execution_allowed"])
        ledger.switch_mode(
            user_id=90001,chat_id=90001,mode="AUTO",now_epoch=NOW+3)
        source=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.db),owner_control=ledger)
        out=self.run_source(owner=source)
        self.assertEqual(out["m09_m05_observed_mode_reference"],"AUTO")
        self.assertIsNone(out["m05_order_specific_final_decision"])
        self.assertFalse(out["execution_allowed"])

    def test_quote_precision_fee_and_budget_preview_never_authorizes(self):
        good=self.review()
        self.assertTrue(good["m07_limit_math_reference_passed"])
        self.assertTrue(good["m07_last_moment_source_bound"])
        self.assertFalse(good["m05_order_specific_final_verified"])
        self.assertFalse(good["execution_allowed"])
        cases=(
            {"draft":draft(price="100.1")},
            {"draft":draft(quantity="1000.03")},
            {"free_idr":D("5000")},
            {"free_idr":D("5000"),"locked_idr":D("920000")},
            {"best_ask_idr":D("130")},
            {"commitments_not_locked_idr":D("900000")},
        )
        # Indodax free_idr is already exclusive of locked_idr. Never
        # subtract locked a second time; this remains only hypothetical
        # sizing and can NEVER authorize a trade.
        locked_reference=self.review(locked_idr=D("920000"))
        self.assertTrue(locked_reference["m07_limit_math_reference_passed"])
        self.assertFalse(locked_reference["execution_allowed"])
        for change in cases:
            with self.subTest(change=change):
                out=self.review(**change)
                self.assertFalse(out["m07_limit_math_reference_passed"])
                self.assertFalse(out["execution_allowed"])

    def test_invalid_fee_nan_and_incomplete_rules_fail_closed(self):
        for change in (
            {"buy_all_in_fee_bps":D("NaN")},
            {"sell_all_in_fee_bps":D("1000")},
            {"pair_rules":{}},
            {"best_bid_idr":D("NaN")},
            {"draft":draft(price="-1")},
        ):
            with self.subTest(change=change):
                out=self.review(**change)
                self.assertEqual(out["status"],"M09_LIMIT_PREVIEW_FAIL_CLOSED")
                self.assertFalse(out["m07_limit_math_reference_passed"])
                self.assertFalse(out["order_sent"])

    def test_stale_pair_crossing_and_fake_spendable_block(self):
        stale=self.review(public_candidate=public_candidate(NOW-95))
        self.assertFalse(stale["m07_last_moment_source_bound"])
        self.assertFalse(stale["execution_allowed"])
        foreign=self.review(public_candidate={
            **public_candidate(),"candidate_pair":"eth_idr"})
        self.assertFalse(foreign["m07_last_moment_source_bound"])
        claimed=self.review()
        self.assertFalse(claimed["m07_fresh_spendable_capital_verified"])
        self.assertIn(
            "G1_RISK_ROLE_SOURCE_NOT_ATTESTED",
            claimed["m07_missing_requirements"])
        self.assertIn(
            "M07_ORDER_EXECUTION_HARDLOCKED",
            claimed["m07_missing_requirements"])

    def test_unknown_hold_blocks_even_valid_budget_preview(self):
        d=M07OrderDraft(
            symbol="ETHIDR",side="BUY",order_type="LIMIT",
            client_order_id="m09faultunknown",price=D("2000"),
            quantity=D("2"))
        self.app.reserve_m07_offline_intent_once(
            intent_id="m09faultunknown",draft=d,observed_epoch=NOW)
        self.app.simulate_m07_unknown_timeout_once(
            intent_id="m09faultunknown",observed_epoch=NOW+1)
        r=self.review()
        self.assertTrue(r["m07_unresolved_or_unknown_hold"])
        self.assertFalse(r["m05_order_specific_final_verified"])
        self.assertFalse(r["execution_allowed"])

    def test_public_rate_quota_real_sqlite_reservation_blocks_whole_chain(self):
        calls=[]
        def local_fetch(path):
            calls.append(path)
            if path=="/api/pairs":
                return [{
                    "ticker_id":"btc_idr","id":"btcidr","symbol":"BTCIDR",
                    "base_currency":"idr","traded_currency":"btc",
                    "is_market_suspended":0,"is_maintenance":0,
                }]
            raise AssertionError("NO_EXTRA_GET_AFTER_RATE_BUDGET")
        market=IndodaxPublicMarket(
            fetch=local_fetch,clock=lambda: NOW,
            rest_budget=SharedPublicRestBudget(
                path=self.db.parent/"rate.sqlite3",
                limit=1,clock=lambda:NOW,
            ),
        )
        with patch("indodax_ai_bot.orchestrator.time.time",
                   return_value=NOW):
            report=self.app.run_m09_original_shadow_mainpath_once(
                "m09rate",market=market,
            )
        self.assertEqual(calls,["/api/pairs"])
        self.assertTrue(report["m09_public_failure_or_rate_blocked"])
        self.assertIn("PUBLIC_SHARED_REST_RATE_BUDGET_EXCEEDED",
                      report["m03_scan_reason"])
        self.assertFalse(report["m09_public_rate_budget_ip_wide_verified"])
        self.assertFalse(report["m04_paid_ai_provider_called"])
        self.assertFalse(report["order_sent"])

    def test_original_cli_m09_source_rate_failure_returns_no_authority(self):
        unavailable=CycleResult(
            "m09cli",None,
            "PUBLIC_BULK_BLOCKED:PUBLIC_SHARED_REST_RATE_BUDGET_EXCEEDED",
            False,stage_disposition="ADVISORY_ONLY_NO_OWNER_DECISION")
        out=StringIO()
        with patch.object(DailyScalpingOrchestrator,
                          "run_bulk_deep_cycle",return_value=unavailable):
            with redirect_stdout(out):
                code=main(["--m09-shadow-mainpath-once",
                           "--db",str(self.db)])
        self.assertEqual(code,0)
        payload=json.loads(out.getvalue())
        self.assertTrue(payload["m09_original_shadow"][
            "m09_public_failure_or_rate_blocked"])
        self.assertFalse(payload["m09_original_shadow"]["execution_allowed"])
        self.assertIsNone(payload["final_decision"])

    def test_no_private_paid_provider_or_order_transport(self):
        with (
            patch.object(self.app,"run_bulk_deep_cycle",
                         return_value=CycleResult(
                             "m09",None,"G1_UNVERIFIED",False,
                             stage_disposition="ADVISORY_ONLY_NO_OWNER_DECISION",
                         )),
            patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                  side_effect=AssertionError("PRIVATE_DENIED")) as private,
            patch("indodax_ai_bot.m07_order_adapter.OfficialV2OrderAdapter.dispatch",
                  side_effect=AssertionError("ORDER_DENIED")) as orders,
        ):
            out=self.app.run_m09_original_shadow_mainpath_once("m09noorder")
        private.assert_not_called()
        orders.assert_not_called()
        self.assertFalse(out["order_sent"])
        self.assertFalse(out["execution_allowed"])


if __name__=="__main__":
    unittest.main()
