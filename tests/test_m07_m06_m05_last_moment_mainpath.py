"""Original M05 source-specific actual owner advisory and M06/M07 late guard.

All flows are OFFLINE and tests use isolated original SQLite databases.
There is never any order, exchange private GET, or paid AI request.
"""
from __future__ import annotations
from dataclasses import replace
from decimal import Decimal as D
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m07_order_adapter import M07OrderDraft, OfficialV2OrderAdapter, M07Hardlocked
from indodax_ai_bot.m07_limit_preflight import assess_m07_limit_buy_preflight_offline
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.owner_control import OwnerDecisionLedger

MINUTE = 1800000060
NOW = MINUTE + 15
DIGEST = sha256(b"M03_SOURCE_BOUND_BTC_IDR").hexdigest()
UID = 7654321
RULES = {
    "price_precision":"1", "quantity_increment":"1",
    "trade_min_base_currency":"10000",
    "trade_min_traded_currency":"10",
    "is_market_suspended":0, "is_maintenance":0,
}


def preview():
    return assess_m07_limit_buy_preflight_offline(
        draft=M07OrderDraft("BTCIDR","BUY","LIMIT","m07-last-review",
                            price=D("100"),quantity=D("200")),
        pair_rules=RULES,
        best_bid_idr=D("99"),best_ask_idr=D("100"),
        free_idr=D("50000"),locked_idr=D("0"),
        floki_rebuy_idr=D("10000"),pepe_rebuy_idr=D("5000"),
        commitments_not_locked_idr=D("2000"),
        buy_all_in_fee_bps=D("20"),sell_all_in_fee_bps=D("40"),
        max_spread_bps=D("150"),
    )


def public():
    return {
        "stage":"M07_SAME_CANDIDATE_PUBLIC_PREFLIGHT",
        "status":"M07_PUBLIC_SOURCE_BOUND_RULES_REFERENCE_ONLY",
        "candidate_pair":"btc_idr", "observed_pair":"btc_idr",
        "closed_1m_epoch":MINUTE,"observed_epoch":MINUTE+10,
        "same_candidate_and_closed_minute":True,
        "execution_allowed":False,
    }


def account():
    return {
        "stage":"M06_ACTUAL_IDR_VIEWONLY",
        "status":"M06_IDR_BALANCE_OBSERVED_SOURCE_RESERVE_UNVERIFIED",
        "exchange_idr_free_observed":"920147",
        "exchange_idr_locked_observed":"0",
        "view_only_permissions_verified":True,"requests_completed":2,
        "all_special_sell_history_verified":False,
        "all_peer_liabilities_verified":False,
        "spendable_idr_verified":False,
        "local_special_ledger_uid_bound":False,
        "local_rebuy_receipts_linked":False,
        "source_authenticated":False,
        "floki_rebuy_local_hold_idr":None,
        "pepe_rebuy_local_hold_idr":None,
        "execution_allowed":False,
    }


def app_and_owner(folder, *, include_owner=True):
    journal=DecisionJournal(folder/"original-journal.sqlite3")
    owner=(OwnerDecisionLedger(folder/"original-owner.sqlite3",
                               owner_user_id=UID)
           if include_owner else None)
    return DailyScalpingOrchestrator(Settings(),journal,owner_control=owner),owner


def issued(owner):
    proposed=owner.issue_market_advisory_proposal(
        pair="btc_idr",digest=DIGEST,closed_1m_epoch=MINUTE,
        now_epoch=MINUTE+2,source_expires_epoch=MINUTE+89,
        setup="PULLBACK",market_status="PUBLIC",risk_status="UNVERIFIED",
    )
    assert proposed["accepted"] is True, proposed
    sent=owner.claim_market_advisory_delivery(now_epoch=MINUTE+3)
    assert sent is not None
    owner.confirm_market_advisory_delivery(
        proposal_id=sent["proposal_id"],message_id=1337)
    return proposed,sent


class M07OriginalM06M05LastMomentTests(unittest.TestCase):

    def test_fresh_m03_math_m06_viewonly_without_protected_history_blocks_buy(self):
        with TemporaryDirectory() as td:
            app,owner=app_and_owner(Path(td),include_owner=False)
            result=app.review_m07_last_moment_gate_offline(
                limit_preview=preview(), public_candidate=public(),
                account_snapshot=account(), now_epoch=NOW)
            self.assertTrue(result["limit_math_only_passed"])
            self.assertTrue(result["public_same_candidate_fresh_observed"])
            self.assertTrue(result["m06_viewonly_account_observed"])
            self.assertFalse(result["m06_general_idr_order_budget_authorized"])
            self.assertFalse(result["m05_order_specific_final_verified"])
            self.assertIn("M06_FLOKI_PEPE_PROCEEDS_NOT_COMPLETE",
                          result["missing_requirements"])
            self.assertFalse(result["execution_allowed"])
            self.assertFalse(result["order_sent"])
            self.assertEqual(app.journal.m07_offline_intent_summary()[
                "unresolved_simulated_intents"],0)

    def test_owner_button_valid_same_source_pass_remains_advisory_only(self):
        with TemporaryDirectory() as td:
            app,owner=app_and_owner(Path(td))
            proposed,sent=issued(owner)
            changed=owner.apply_callback(
                user_id=UID,chat_id=UID,callback_data=sent["pass_callback"],
                now_epoch=MINUTE+5,telegram_message_id=1337)
            self.assertEqual(changed["final_decision"],"PASS")
            record=owner.inspect_market_advisory_preflight_readonly(
                proposal_id=sent["proposal_id"],pair="btc_idr",
                source_digest=DIGEST,closed_1m_epoch=MINUTE,
                now_epoch=NOW)
            self.assertTrue(record["source_bound_owner_review_pass"])
            self.assertEqual(record["status"],"M05_OWNER_BUTTON_PASS_ADVISORY_ONLY")
            self.assertFalse(record["owner_order_execution_consent_verified"])
            result=app.review_m07_last_moment_gate_offline(
                limit_preview=preview(), public_candidate=public(),
                account_snapshot=account(),
                owner_advisory_proposal_id=sent["proposal_id"],
                source_digest=DIGEST,closed_1m_epoch=MINUTE,
                now_epoch=NOW)
            self.assertTrue(result["m05_same_source_advisory_review_pass"])
            self.assertFalse(result["m05_order_specific_final_verified"])
            self.assertFalse(result["execution_allowed"])
            with app.journal._connect() as db:
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM m07_offline_order_intents"
                ).fetchone()[0],0)
            with sqlite3.connect(owner.db_path) as db:
                self.assertEqual(db.execute(
                    "SELECT execution_allowed FROM m05_proposals"
                    " WHERE proposal_id=?",(sent["proposal_id"],)
                ).fetchone(),(0,))
            with self.assertRaises(M07Hardlocked):
                OfficialV2OrderAdapter().dispatch()

    def test_forged_other_pair_digest_and_expired_owner_denied(self):
        with TemporaryDirectory() as td:
            _,owner=app_and_owner(Path(td))
            proposed,sent=issued(owner)
            owner.apply_callback(
                user_id=UID,chat_id=UID,callback_data=sent["pass_callback"],
                now_epoch=MINUTE+5,telegram_message_id=1337)
            for pair,digest,minute,epoch in (
                ("eth_idr",DIGEST,MINUTE,NOW),
                ("btc_idr","a"*64,MINUTE,NOW),
                ("btc_idr",DIGEST,MINUTE+60,NOW),
                ("btc_idr",DIGEST,MINUTE,MINUTE+90),
            ):
                with self.subTest(pair=pair,epoch=epoch):
                    out=owner.inspect_market_advisory_preflight_readonly(
                        proposal_id=sent["proposal_id"],pair=pair,
                        source_digest=digest,closed_1m_epoch=minute,
                        now_epoch=epoch)
                    self.assertFalse(out["source_bound_owner_review_pass"])
                    self.assertFalse(out["execution_allowed"])

    def test_m05_mode_switch_and_unsent_or_pending_do_not_promote(self):
        with TemporaryDirectory() as td:
            _,owner=app_and_owner(Path(td))
            proposed=owner.issue_market_advisory_proposal(
                pair="btc_idr",digest=DIGEST,closed_1m_epoch=MINUTE,
                now_epoch=MINUTE+2,source_expires_epoch=MINUTE+89,
                setup="PULLBACK",market_status="PUBLIC",risk_status="UNVERIFIED")
            self.assertFalse(owner.inspect_market_advisory_preflight_readonly(
                proposal_id=proposed["proposal_id"],pair="btc_idr",
                source_digest=DIGEST,closed_1m_epoch=MINUTE,
                now_epoch=NOW)["source_bound_owner_review_pass"])
            owner.switch_mode(user_id=UID,chat_id=UID,mode="AUTO",
                              now_epoch=MINUTE+5)
            after=owner.inspect_market_advisory_preflight_readonly(
                proposal_id=proposed["proposal_id"],pair="btc_idr",
                source_digest=DIGEST,closed_1m_epoch=MINUTE,
                now_epoch=NOW)
            self.assertFalse(after["source_bound_owner_review_pass"])
            self.assertFalse(after["execution_allowed"])

    def test_manual_auto_manual_switch_cannot_resurrect_previously_passed_card(self):
        with TemporaryDirectory() as td:
            _,owner=app_and_owner(Path(td))
            _,sent=issued(owner)
            consent=owner.apply_callback(
                user_id=UID,chat_id=UID,callback_data=sent["pass_callback"],
                now_epoch=MINUTE+5,telegram_message_id=1337)
            self.assertEqual(consent["final_decision"],"PASS")
            owner.switch_mode(
                user_id=UID,chat_id=UID,mode="AUTO",now_epoch=MINUTE+7)
            owner.switch_mode(
                user_id=UID,chat_id=UID,mode="MANUAL",now_epoch=MINUTE+8)
            after=owner.inspect_market_advisory_preflight_readonly(
                proposal_id=sent["proposal_id"],pair="btc_idr",
                source_digest=DIGEST,closed_1m_epoch=MINUTE,
                now_epoch=NOW)
            self.assertEqual(after["status"],
                             "M05_ADVISORY_OWNER_MODE_TRANSITION")
            self.assertFalse(after["source_bound_owner_review_pass"])
            self.assertFalse(after["execution_allowed"])

    def test_fake_complete_spendable_flags_must_not_grant_authority(self):
        with TemporaryDirectory() as td:
            app,_=app_and_owner(Path(td),include_owner=False)
            forged={**account(),
                "all_special_sell_history_verified":True,
                "local_special_ledger_uid_bound":True,
                "local_rebuy_receipts_linked":True,
                "all_peer_liabilities_verified":True,
                "spendable_idr_verified":True,
                "source_authenticated":True,
            }
            result=app.review_m07_last_moment_gate_offline(
                limit_preview=preview(),public_candidate=public(),
                account_snapshot=forged,now_epoch=NOW)
            self.assertTrue(result["m06_report_claims_spendable_verified"])
            self.assertFalse(result["m06_complete_account_independently_attested"])
            self.assertFalse(result["m06_general_idr_order_budget_authorized"])
            self.assertFalse(result["execution_allowed"])

    def test_stale_public_source_and_trader_attempts_block_even_with_money(self):
        with TemporaryDirectory() as td:
            app,_=app_and_owner(Path(td),include_owner=False)
            for altered in (
                {**public(),"candidate_pair":"eth_idr"},
                {**public(),"observed_epoch":MINUTE-90},
                {**public(),"observed_epoch":MINUTE+5},
                None,
            ):
                with self.subTest(src=altered):
                    result=app.review_m07_last_moment_gate_offline(
                        limit_preview=preview(),
                        public_candidate=altered,
                        account_snapshot=account(),
                        now_epoch=MINUTE+50)
                    self.assertFalse(result["public_same_candidate_fresh_observed"])
                    self.assertFalse(result["execution_allowed"])

    def test_original_m07_unknown_or_open_reserve_blocks_new_preflight(self):
        with TemporaryDirectory() as td:
            app,_=app_and_owner(Path(td),include_owner=False)
            draft=M07OrderDraft("BTCIDR","BUY","MARKET",
                                "m07-existing",quote_order_qty=10000)
            held=app.reserve_m07_offline_intent_once(
                intent_id="m07-existing",draft=draft,
                observed_epoch=MINUTE)
            self.assertTrue(held["newly_reserved"])
            first=app.review_m07_last_moment_gate_offline(
                limit_preview=preview(),public_candidate=public(),
                account_snapshot=account(),now_epoch=NOW)
            self.assertIn("M07_LOCAL_OUTSTANDING_OR_UNKNOWN_INTENT",
                          first["missing_requirements"])
            app.simulate_m07_unknown_timeout_once(
                intent_id="m07-existing",observed_epoch=NOW)
            second=app.review_m07_last_moment_gate_offline(
                limit_preview=preview(),public_candidate=public(),
                account_snapshot=account(),now_epoch=NOW)
            self.assertTrue(second["m07_unresolved_or_unknown_local_intent"])
            self.assertFalse(second["execution_allowed"])

    def test_missing_source_and_invalid_clock_never_authorizes(self):
        with TemporaryDirectory() as td:
            app,_=app_and_owner(Path(td),include_owner=False)
            out=app.review_m07_last_moment_gate_offline(
                limit_preview=preview(),now_epoch=NOW)
            self.assertIn("M06_EXCHANGE_ACCOUNT_NOT_JOINED",
                          out["missing_requirements"])
            self.assertIn("M05_SAME_SOURCE_OWNER_REVIEW_NOT_PASS",
                          out["missing_requirements"])
            self.assertFalse(out["execution_allowed"])
            with self.assertRaises(ValueError):
                app.review_m07_last_moment_gate_offline(
                    limit_preview=preview(),now_epoch=0)


if __name__ == "__main__":
    unittest.main()
