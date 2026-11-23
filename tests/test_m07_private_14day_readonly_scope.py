"""Original V2 M07 bounded 14-day View Only audit across 2 official windows."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m07_order_adapter import (
    M07OrderDraft, M07Hardlocked, OfficialV2OrderAdapter,
)
from indodax_ai_bot.m07_private_order_readonly import (
    M07ReadOnlyError, read_m07_14day_order_status_once,
    normalize_order_status,
)
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator

NOW_MS = 1793635200000
DAY_MS = 86400 * 1000
C = "usdtidr-owner-existing-1"
KEY, SECRET = "FAKE_VIEW_KEY", "FAKE_VIEW_SECRET"
ACCOUNT = {
    "canTrade": False, "canWithdraw": False, "canWithdrawFiat": False,
    "uid": 12345, "balances": [],
}


def hist(submit_ms):
    return {"data": [
        {"symbol":"usdtidr","clientOrderId":C,
         "submitTime":submit_ms,"side":"SELL","status":"FILLED",
         "orderId":"usdtidr-limit-31"}
    ]}


def final_status(*, created_ms=NOW_MS-9*DAY_MS):
    return {
        "symbol":"USDTIDR","clientOrderId":C,
        "orderId":31,"fullOrderId":"usdtidr-limit-31",
        "side":"SELL","type":"LIMIT","status":"FILLED",
        "origQty":"9","executedQty":"9","time":created_ms,
    }


class M07FourGetReadonlyLookbackTests(unittest.TestCase):
    def test_original_cli_four_viewonly_gets_two_windows_and_excluded_asset(self):
        with TemporaryDirectory() as tmp:
            calls=[]
            def answer(req):
                calls.append((req.path,req.query))
                return [ACCOUNT, {"data":[]},
                        hist(NOW_MS-9*DAY_MS), final_status()][len(calls)-1]
            with (
                patch("indodax_ai_bot.__main__._existing_v2_credentials",
                      return_value=Path("/synthetic/0600.json")),
                patch("indodax_ai_bot.__main__.read_view_only_credentials",
                      return_value=(KEY,SECRET)),
                patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                      side_effect=answer),
                patch("indodax_ai_bot.m07_private_order_readonly.time.time",
                      return_value=NOW_MS/1000),
            ):
                out,err=StringIO(),StringIO()
                with redirect_stdout(out),redirect_stderr(err):
                    rc=main([
                        "--m07-private-14day-order-once",
                        "--m07-private-symbol","USDTIDR",
                        "--db",str(Path(tmp)/"v2.sqlite3"),
                    ])
            self.assertEqual(rc,0,err.getvalue())
            self.assertEqual([x[0] for x in calls],[
                "/api/v2/account","/api/v2/order/histories",
                "/api/v2/order/histories","/api/v2/order",
            ])
            for req in calls[1:3]:
                self.assertIn("symbol=usdtidr",req[1])
                self.assertIn("startTime=",req[1])
                self.assertIn("endTime=",req[1])
                self.assertIn("limit=10",req[1])
            self.assertIn("origClientOrderId="+C,calls[3][1])
            result=json.loads(out.getvalue())["m07_private_readonly_order"]
            self.assertEqual(result["requests_completed"],4)
            self.assertEqual(result["history_window_last_days"],14)
            self.assertTrue(result["excluded_from_main_trading_if_special_asset"])
            self.assertEqual(result["order_status"],"FILLED")
            self.assertFalse(result["bot_ownership_verified"])
            self.assertFalse(result["unknown_intent_released"])
            self.assertFalse(result["execution_allowed"])
            self.assertNotIn(KEY,out.getvalue())
            self.assertNotIn(C,out.getvalue())
            offline = M07OrderDraft("USDTIDR","BUY","MARKET","client1",
                                    quote_order_qty=10000).parameters()
            self.assertIn(("symbol","USDTIDR"),offline)
            # Shape acceptance never bypasses the original hardlocked adapter.
            with self.assertRaises(M07Hardlocked):
                OfficialV2OrderAdapter().dispatch()
            with self.assertRaises(M07Hardlocked):
                OfficialV2OrderAdapter().create_order()
            with self.assertRaises(M07Hardlocked):
                OfficialV2OrderAdapter().cancel_order()

    def test_no_history_either_window_is_unresolved_after_only_three_gets(self):
        with TemporaryDirectory() as tmp:
            req=[]
            def answer(request):
                req.append(request.path)
                return ACCOUNT if len(req)==1 else {"data":[]}
            db=Path(tmp)/"v2.sqlite3"
            with (
                patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                      side_effect=answer),
                patch("indodax_ai_bot.m07_private_order_readonly.time.time",
                      return_value=NOW_MS/1000),
            ):
                app=DailyScalpingOrchestrator(Settings(),DecisionJournal(db))
                with self.assertRaisesRegex(M07ReadOnlyError,
                                            "M07_HISTORY_14D_EMPTY_UNRESOLVED"):
                    app.inspect_m07_private_14day_order_once(
                        symbol="USDTIDR",api_key=KEY,secret_key=SECRET)
            self.assertEqual(req,[
                "/api/v2/account","/api/v2/order/histories",
                "/api/v2/order/histories",
            ])
            with DecisionJournal(db)._connect() as conn:
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM m07_exchange_readonly_orders"
                ).fetchone()[0],0)

    def test_first_history_nonempty_still_check_bounded_second_window(self):
        with TemporaryDirectory() as tmp:
            req=[]
            def answer(request):
                req.append(request.path)
                return [
                    ACCOUNT,hist(NOW_MS-2*DAY_MS),
                    hist(NOW_MS-10*DAY_MS),
                    final_status(created_ms=NOW_MS-2*DAY_MS)
                ][len(req)-1]
            with (
                patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                      side_effect=answer),
                patch("indodax_ai_bot.m07_private_order_readonly.time.time",
                      return_value=NOW_MS/1000),
            ):
                result=read_m07_14day_order_status_once(
                    journal=DecisionJournal(Path(tmp)/"v2.sqlite3"),
                    symbol="USDTIDR",api_key=KEY,secret_key=SECRET)
            self.assertEqual(result["requests_completed"],4)
            self.assertEqual(len(req),4)
            self.assertFalse(result["history_is_full_account_coverage"])

    def test_history_get_order_clock_mismatch_cannot_be_stored(self):
        with TemporaryDirectory() as tmp:
            db=Path(tmp)/"v2.sqlite3"
            received=[]
            def answer(request):
                received.append(request.path)
                return [ACCOUNT,{"data":[]},
                        hist(NOW_MS-9*DAY_MS),
                        final_status(created_ms=NOW_MS-10*DAY_MS)
                       ][len(received)-1]
            with (
                patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                      side_effect=answer),
                patch("indodax_ai_bot.m07_private_order_readonly.time.time",
                      return_value=NOW_MS/1000),
            ):
                with self.assertRaisesRegex(M07ReadOnlyError,
                                            "M07_HISTORY_ORDER_TIME_MISMATCH"):
                    read_m07_14day_order_status_once(
                        journal=DecisionJournal(db),
                        symbol="USDTIDR",api_key=KEY,secret_key=SECRET)
            self.assertEqual(len(received),4)
            with DecisionJournal(db)._connect() as con:
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM m07_exchange_readonly_orders"
                ).fetchone()[0],0)

    def test_history_out_of_window_fails_before_fourth_get(self):
        with TemporaryDirectory() as tmp:
            req=[]
            def answer(request):
                req.append(request.path)
                return [
                    ACCOUNT,
                    hist(NOW_MS-9*DAY_MS),
                ][len(req)-1]
            with (
                patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                      side_effect=answer),
                patch("indodax_ai_bot.m07_private_order_readonly.time.time",
                      return_value=NOW_MS/1000),
            ):
                with self.assertRaisesRegex(M07ReadOnlyError,
                                            "M07_HISTORY_OUTSIDE_WINDOW"):
                    read_m07_14day_order_status_once(
                        journal=DecisionJournal(Path(tmp)/"v2.sqlite3"),
                        symbol="USDTIDR",api_key=KEY,secret_key=SECRET)
            self.assertEqual(len(req),2)

    def test_missing_viewonly_permissions_stops_before_history(self):
        with TemporaryDirectory() as tmp:
            req=[]
            def answer(request):
                req.append(request.path)
                return {**ACCOUNT,"canTrade":True}
            with patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                       side_effect=answer):
                with self.assertRaisesRegex(M07ReadOnlyError,
                                            "M07_KEY_NOT_PROVEN_VIEW_ONLY"):
                    read_m07_14day_order_status_once(
                        journal=DecisionJournal(Path(tmp)/"v2.sqlite3"),
                        symbol="USDTIDR",api_key=KEY,secret_key=SECRET)
            self.assertEqual(req,["/api/v2/account"])

    def test_readonly_query_accepts_pepe_floki_but_never_order_draft(self):
        request=OfficialV2OrderAdapter().prepare_status_viewonly_offline(
            symbol="FLOKIIDR",orig_client_order_id="audit-existing-1",
            api_key=KEY,secret_key=SECRET,timestamp_ms=NOW_MS)
        self.assertEqual(request.method,"GET")
        self.assertIn("symbol=FLOKIIDR",request.query)
        self.assertFalse(request.network_allowed)
        self.assertFalse(request.execution_allowed)
        for symbol in ("PEPEIDR","FLOKIIDR"):
            with self.subTest(symbol=symbol):
                with self.assertRaisesRegex(ValueError,"M07_SYMBOL_NOT_ADMITTED"):
                    M07OrderDraft(symbol,"BUY","MARKET","real1",
                                 quote_order_qty=10000).parameters()

    def test_invalid_override_blocked_before_credentials_or_network(self):
        with TemporaryDirectory() as tmp:
            with patch("indodax_ai_bot.__main__._existing_v2_credentials") as creds:
                out,err=StringIO(),StringIO()
                with redirect_stdout(out),redirect_stderr(err):
                    code=main([
                        "--m07-private-14day-order-once",
                        "--m07-private-symbol","USDTIDR",
                        "--m07-private-client-order-id","bogus",
                        "--db",str(Path(tmp)/"dummy.sqlite3"),
                    ])
            self.assertEqual(code,2)
            creds.assert_not_called()
            self.assertFalse((Path(tmp)/"dummy.sqlite3").exists())


if __name__=="__main__":
    unittest.main()
