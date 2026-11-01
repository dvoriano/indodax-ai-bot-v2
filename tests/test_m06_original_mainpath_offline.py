"""One original V2 CLI -> orchestrator -> account review -> journal."""
import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.m06_shadow_account import AccountObservation
from indodax_ai_bot.m06_ownership_ledger import M06OwnershipLedger
from indodax_ai_bot.m06_private_payloads import OrderRecord, FillRecord


class M06OriginalMainpathTests(unittest.TestCase):
    def test_cli_runs_original_caller_and_journals_reject(self):
        with tempfile.TemporaryDirectory() as td:
            path=str(Path(td)/"decisions.sqlite3")
            output=io.StringIO()
            with patch.dict(os.environ, {
                "INDODAX_TRADING_ENABLED":"NO",
                "INDODAX_ALLOW_LIVE_EXECUTION":"NO",
                "INDODAX_EXECUTION_MODE":"DRY_RUN",
            }), contextlib.redirect_stdout(output):
                code=main(["--db",path,"--m06-account-review-once"])
            self.assertEqual(code,0)
            report=json.loads(output.getvalue().strip())
            self.assertEqual(report["reason"],"M06_PRIVATE_ACCOUNT_GO_ABSENT")
            self.assertFalse(report["execution_allowed"])
            self.assertFalse(report["account_authenticated"])
            with sqlite3.connect(path) as db:
                row=db.execute("SELECT final_decision, reason FROM decisions WHERE trace_id=?",
                     ("M06_ACCOUNT_READONLY_NO_GO",)).fetchone()
            self.assertEqual(row,("REJECT","M06_PRIVATE_ACCOUNT_GO_ABSENT"))

    def test_original_orchestrator_capital_with_incomplete_orders_stays_zero(self):
        from indodax_ai_bot.m06_private_payloads import AccountBalances
        with tempfile.TemporaryDirectory() as td:
            bot=DailyScalpingOrchestrator(Settings(),DecisionJournal(
                Path(td)/"decisions.sqlite3"))
            account=AccountBalances(1,(("IDR",Decimal("100000"),Decimal("0")),),
                                    Decimal("100000"),Decimal("0"))
            result=bot.reconcile_m06_capital_offline(
                account=account,open_orders=(),rebuy_idr=Decimal("10000"),
                unlocked_commitments_idr=Decimal("0"),
                local_shadow_idr=Decimal("0"))
            self.assertEqual(result.calculated_available_idr,Decimal("0"))
            self.assertFalse(result.execution_allowed)

    def test_original_orchestrator_ownership_restart_remains_unverified(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"ownership.sqlite3"
            M06OwnershipLedger(path).record_local_intent(
                account_key="account",order_id="id1",client_id="v2-1",
                symbol="btcidr",side="BUY")
            order=OrderRecord("id1","btcidr","BUY","FILLED",
                Decimal("1"),Decimal("1"),"v2-1")
            fill=FillRecord("t1","id1","btcidr",
                Decimal("1"),Decimal("100"),Decimal("1"))
            for _ in range(2):
                bot=DailyScalpingOrchestrator(Settings(),DecisionJournal(
                    Path(td)/"decisions.sqlite3"))
                review=bot.review_m06_local_intent_ownership(
                    db_path=str(path),account_key="account",
                    order=order,fills=(fill,))
                self.assertTrue(review.account_match)
                self.assertFalse(review.bot_owned_verified)
                self.assertFalse(review.execution_allowed)

    def test_original_orchestrator_parses_official_v2_fixtures_without_trusting_them(self):
        with tempfile.TemporaryDirectory() as td:
            bot=DailyScalpingOrchestrator(Settings(),DecisionJournal(
                Path(td)/"decisions.sqlite3"))
            out=bot.inspect_m06_offline_private_fixtures(
                account_payload={"uid":123,"balances":[
                    {"asset":"IDR","free":"100000","locked":"10000"}]},
                open_orders_payload=[{"orderId":12,"symbol":"BTCIDR",
                    "side":"BUY","status":"NEW","origQty":"1","executedQty":"0"}],
                orders_payload={"data":[{"orderId":"btcidr-limit-12",
                    "symbol":"btcidr","side":"BUY","status":"FILLED",
                    "oriQty":"1","executedQty":"1"}]},
                fills_payload={"data":[{"tradeId":"1234","orderId":"btcidr-limit-12",
                    "symbol":"btcidr","qty":"1","price":"10000","commission":"30"}]},
                symbol="btcidr",now_epoch=1000)
            self.assertEqual(out["status"],"REJECT")
            self.assertEqual(out["fills_seen"],1)
            self.assertFalse(out["bot_ownership_verified"])
            self.assertFalse(out["account_authenticated"])
            self.assertFalse(out["execution_allowed"])

    def test_original_orchestrator_untrusted_balance_does_not_grant_authority(self):
        with tempfile.TemporaryDirectory() as td:
            orchestrator=DailyScalpingOrchestrator(Settings(),DecisionJournal(
                Path(td)/"decisions.sqlite3"))
            observation=AccountObservation(observed_epoch=1000,
                free_idr=Decimal("100000"),locked_idr=Decimal("10000"),
                floki_rebuy_idr=Decimal("10000"),pepe_rebuy_idr=Decimal("5000"),
                other_unlocked_commitments_idr=Decimal("10000"),account_key="offline-only")
            out=orchestrator.review_m06_account(observation=observation,now_epoch=1000)
            self.assertEqual(out.shadow_spendable_idr,Decimal("75000"))
            self.assertEqual(out.status,"REJECT")
            self.assertFalse(out.account_authenticated)
            self.assertFalse(out.ownership_verified)
            self.assertFalse(out.execution_allowed)
            with self.assertRaisesRegex(ValueError,"UNTRUSTED_AUTH"):
                orchestrator.review_m06_account(
                    observation=AccountObservation(**{**observation.__dict__,
                        "exchange_authenticated":True}), now_epoch=1000)


if __name__ == "__main__":
    unittest.main()
