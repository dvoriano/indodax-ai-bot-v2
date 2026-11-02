"""M06 authenticated VIEW ONLY actual IDR read with source-locked local rebuy.

Two fixed signed GETs only. The read-only account balances are real source
values in production, never automatic verified spendable or live permission.
"""
from __future__ import annotations
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal as D
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import sqlite3
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.m06_ownership_ledger import M06OwnershipLedger
from indodax_ai_bot.m06_private_payloads import OrderRecord, FillRecord
from indodax_ai_bot.m06_scoped_https import M06ScopeDenied

UID = 3456
ACCOUNT = {
    "uid":UID,"canTrade":False,"canWithdraw":False,
    "canWithdrawFiat":False,
    "balances":[
        {"asset":"IDR","free":"500000","locked":"50000"},
        {"asset":"FLOKI","free":"100","locked":"0"},
        {"asset":"PEPE","free":"200","locked":"0"},
    ],
}
OPEN_ORDER={
    "orderId":120,"fullOrderId":"btcidr-limit-120",
    "clientOrderId":"account-other-client",
    "symbol":"btcidr","side":"BUY","status":"NEW",
    "origQty":"0.001","executedQty":"0",
}


def build_existing_uid_ledger(db: Path, *, bound_uid: int = UID,
                              complete_shadow: bool = True):
    ledger=M06OwnershipLedger(db)
    account=M06OwnershipLedger.account_scope_from_exchange_uid(bound_uid)
    for asset, quote in (("FLOKI","100000"),("PEPE","50000")):
        symbol=asset.lower()+"idr"
        oid="existing-"+asset
        cid="client-"+asset
        ledger.record_local_intent(
            account_key=account,order_id=oid,
            client_id=cid,symbol=symbol,side="SELL",
        )
        ledger.observe(
            account_key=account,
            order=OrderRecord(oid,symbol,"SELL","PARTIALLY_FILLED",
                              D("10"),D("1"),cid),
            fills=(FillRecord(
                "fill-"+asset,oid,symbol,D("1"),D(quote),D("0"),
                client_order_id=cid,is_buyer=False,
                quote_quantity=D(quote),commission_asset="idr",
            ),),
        )
    if complete_shadow:
        account_hash = M06OwnershipLedger._identity(account)
        with sqlite3.connect(db) as con:
            con.execute("""CREATE TABLE m06_shadow_reserve (
                account_key_hash TEXT NOT NULL,
                amount_idr TEXT NOT NULL,
                released INTEGER NOT NULL
            )""")
            con.execute(
                "INSERT INTO m06_shadow_reserve VALUES(?,?,0)",
                (account_hash,"25000"),
            )
    os.chmod(db,0o600)
    return db


def run_once(db, *, ownership_db=None, account=None, open_orders=None):
    urls=[]
    answers=[ACCOUNT if account is None else account,
             [] if open_orders is None else open_orders]
    def fake(request):
        urls.append((request.path,request.query,request.headers))
        return answers[len(urls)-1]
    with patch("indodax_ai_bot.m06_idr_live_readonly._https_json",
               side_effect=fake):
        app=DailyScalpingOrchestrator(Settings(),DecisionJournal(db))
        result=app.inspect_m06_idr_budget_readonly_once(
            api_key="OWNER_VIEW_ONLY_TEST_KEY",
            secret_key="OWNER_VIEW_ONLY_TEST_SECRET",
            local_ownership_db=(
                str(ownership_db) if ownership_db is not None else None
            ),
        )
    return result,urls


class OwnerM06ActualIDRViewOnlyTests(unittest.TestCase):
    def test_real_account_two_signed_gets_without_ledger_is_unattributed(self):
        with TemporaryDirectory() as d:
            out,requests=run_once(Path(d)/"decisions.sqlite3")
            self.assertEqual([x[0] for x in requests],
                ["/api/v2/account","/api/v2/openOrders"])
            self.assertEqual(out["exchange_idr_free_observed"],"500000")
            self.assertEqual(out["exchange_idr_locked_observed"],"50000")
            self.assertEqual(out["all_pair_open_orders_observed"],0)
            self.assertIsNone(out["general_idr_unverified_preview"])
            self.assertIsNone(out["floki_rebuy_local_hold_idr"])
            self.assertFalse(out["spendable_idr_verified"])
            self.assertFalse(out["execution_allowed"])
            self.assertIn(
                "M06_LOCAL_REBUY_LEDGER_PATH_NOT_GIVEN",
                out["missing_evidence"])
            for method,query,headers in requests:
                self.assertEqual(method[:8],"/api/v2/")
                self.assertIn("timestamp=",query)
                self.assertEqual(set(headers),{"Accept","X-APIKEY","Sign"})

    def test_same_uid_real_local_source_proceeds_and_shadow_preview(self):
        with TemporaryDirectory() as d:
            p=Path(d)
            ledger=build_existing_uid_ledger(p/"ownership.sqlite3")
            out,requests=run_once(
                p/"decisions.sqlite3",ownership_db=ledger,
                open_orders=[OPEN_ORDER],
            )
            self.assertEqual(out["requests_completed"],2)
            self.assertEqual(out["all_pair_open_orders_observed"],1)
            self.assertTrue(out["local_special_ledger_uid_bound"])
            self.assertTrue(out["local_shadow_coverage_complete"])
            self.assertTrue(out["local_rebuy_receipts_linked"])
            self.assertEqual(out["floki_rebuy_local_hold_idr"],"100000")
            self.assertEqual(out["pepe_rebuy_local_hold_idr"],"50000")
            self.assertEqual(
                out["other_local_not_locked_commitments_idr"],"25000")
            self.assertEqual(out["general_idr_unverified_preview"],"325000")
            self.assertFalse(out["spendable_idr_verified"])
            self.assertFalse(out["all_special_sell_history_verified"])
            self.assertFalse(out["all_peer_liabilities_verified"])
            self.assertFalse(out["execution_allowed"])
            self.assertFalse(out["source_authenticated"])
            self.assertEqual(len(requests),2)
            self.assertNotIn(str(UID),json.dumps(out))
            self.assertNotIn("client-FLOKI",json.dumps(out))
            self.assertNotIn("OWNER_VIEW_ONLY_TEST_SECRET",json.dumps(out))

    def test_foreign_uid_ledger_never_subtracts_wrong_account_or_claims_zero(self):
        with TemporaryDirectory() as d:
            p=Path(d)
            ledger=build_existing_uid_ledger(
                p/"other.sqlite3",bound_uid=UID+1)
            out,requests=run_once(p/"decision.sqlite3",ownership_db=ledger)
            self.assertFalse(out["local_special_ledger_uid_bound"])
            self.assertIsNone(out["general_idr_unverified_preview"])
            self.assertIn("M06_LOCAL_UID_BOUND_LEDGER_NOT_VERIFIED",
                          out["missing_evidence"])
            self.assertEqual(len(requests),2)

    def test_account_permissions_fail_before_open_orders_request(self):
        with TemporaryDirectory() as d:
            urls=[]
            with patch("indodax_ai_bot.m06_idr_live_readonly._https_json",
                       side_effect=lambda req:(urls.append(req.path),
                            {**ACCOUNT,"canTrade":True})[1]):
                with self.assertRaisesRegex(
                        M06ScopeDenied,"M06_KEY_NOT_PROVEN_VIEW_ONLY"):
                    DailyScalpingOrchestrator(
                        Settings(),DecisionJournal(Path(d)/"db.sqlite3")
                    ).inspect_m06_idr_budget_readonly_once(
                        api_key="TEST",secret_key="TESTSECRET")
            self.assertEqual(urls,["/api/v2/account"])

    def test_all_pair_non_idr_or_malformed_order_never_assumes_available(self):
        for rows in (
            [{**OPEN_ORDER,"symbol":"btc_usdt"}],
            [{**OPEN_ORDER,"fullOrderId":None}],
            [OPEN_ORDER,OPEN_ORDER],
            [OPEN_ORDER]*1000,
        ):
            with self.subTest(count=len(rows)):
                with TemporaryDirectory() as d:
                    with self.assertRaisesRegex(
                        M06ScopeDenied,"M06_IDR_OPEN_ORDERS_SCOPE_UNRESOLVED"):
                        run_once(Path(d)/"db.sqlite3",open_orders=rows)

    def test_corrupt_account_and_api_error_fail_closed(self):
        for bad in (
            {"code":-2015,"msg":"sensitive response"},
            {**ACCOUNT,"balances":[]},
            {**ACCOUNT,"canWithdraw":True},
        ):
            with self.subTest(bad=list(bad)):
                with TemporaryDirectory() as d:
                    with self.assertRaises(M06ScopeDenied):
                        run_once(Path(d)/"db.sqlite3",account=bad)

    def test_original_cli_real_path_never_prints_key_uid_or_claims_ready(self):
        with TemporaryDirectory() as d:
            p=Path(d)
            output,errors=StringIO(),StringIO()
            requests=[]
            def fake(req):
                requests.append(req.path)
                return ACCOUNT if len(requests)==1 else []
            with (
                patch("indodax_ai_bot.__main__._existing_v2_credentials",
                      return_value=Path("/synthetic/v2-only-viewonly.json")),
                patch("indodax_ai_bot.__main__.read_view_only_credentials",
                      return_value=("MY_PRIVATE_KEY","MY_PRIVATE_SECRET")),
                patch("indodax_ai_bot.m06_idr_live_readonly._https_json",
                      side_effect=fake),
            ):
                with redirect_stdout(output),redirect_stderr(errors):
                    rc=main(["--m06-private-idr-budget-once",
                             "--db",str(p/"decision.sqlite3")])
            self.assertEqual(rc,0,errors.getvalue())
            self.assertEqual(requests,
                             ["/api/v2/account","/api/v2/openOrders"])
            report=json.loads(output.getvalue())
            self.assertEqual(report["stage"],"M06_ACTUAL_IDR_VIEWONLY")
            self.assertEqual(report["exchange_idr_free_observed"],"500000")
            self.assertFalse(report["execution_allowed"])
            self.assertIsNone(report["general_idr_unverified_preview"])
            for secret in ("MY_PRIVATE_KEY","MY_PRIVATE_SECRET",str(UID)):
                self.assertNotIn(secret,output.getvalue())
            self.assertEqual(errors.getvalue(),"")


if __name__=="__main__":
    unittest.main()
