"""M07 real signed READ ONLY myTrades source: original V2 CLI and journal."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.journal import DecisionJournal, DecisionConflictError
from indodax_ai_bot.config import Settings
from indodax_ai_bot.m07_order_adapter import M07OrderDraft, OfficialV2OrderAdapter, M07Hardlocked
from indodax_ai_bot.m07_private_order_readonly import M07ReadOnlyError
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator

T=1793635200000
DAY=86400000
START=T-9*DAY
END=START+120000
CID="viewonly-usdt-51"
FULL="usdtidr-limit-909"
ACCOUNT={"canTrade":False,"canWithdraw":False,"canWithdrawFiat":False,"uid":33344,"balances":[]}


def order_history():
    return {"data":[{
        "symbol":"usdtidr","clientOrderId":CID,
        "submitTime":START,"finishTime":END,
        "side":"SELL","type":"LIMIT","status":"FILLED",
    }]}


def order_detail():
    return {
        "symbol":"USDTIDR","clientOrderId":CID,
        "orderId":909,"fullOrderId":FULL,
        "side":"SELL","type":"LIMIT","status":"FILLED",
        "origQty":"51","executedQty":"51","time":START,
    }


def one_fill(ident="9911", qty="20", quote="340000", fee="1300",
             order_id=FULL, client_id=CID, asset="idr"):
    return {
        "tradeId":ident,"orderId":order_id,"clientOrderId":client_id,
        "symbol":"usdtidr","price":"17000","qty":qty,
        "quoteQty":quote,"commission":fee,
        "commissionAsset":asset,
        "isBuyer":False,"isMaker":False,"time":START+1000,
    }


def two_fills():
    return {"data":[
        one_fill(), one_fill(ident="9912",qty="31",quote="527000",fee="2000"),
    ]}


def offline_app(db):
    return DailyScalpingOrchestrator(Settings(),DecisionJournal(db))


def read(db, raw_trades, *, time_ms=T, history=None, record=None):
    calls=[]
    expected=[ACCOUNT,{"data":[]},history or order_history(),
              record or order_detail(),raw_trades]
    def fake(request):
        calls.append((request.path,request.query))
        return expected[len(calls)-1]
    with (
        patch("indodax_ai_bot.m07_private_order_readonly._https_json",
              side_effect=fake),
        patch("indodax_ai_bot.m07_private_order_readonly.time.time",
              return_value=time_ms/1000),
    ):
        result=offline_app(db).inspect_m07_private_fill_fees_once(
            symbol="USDTIDR",api_key="TEST_VIEW_ONLY_KEY",
            secret_key="TEST_VIEW_ONLY_SECRET",
        )
    return result,calls


class M07ViewOnlyFillFeesTests(unittest.TestCase):
    def test_original_cli_five_gets_51_usdt_idr_net_and_no_buy_sell(self):
        with TemporaryDirectory() as tmp:
            db=Path(tmp)/"v2.sqlite3"
            calls=[]
            results=[ACCOUNT,{"data":[]},order_history(),
                     order_detail(),two_fills()]
            def fake(req):
                calls.append((req.path,req.query))
                return results[len(calls)-1]
            with (
                patch("indodax_ai_bot.__main__._existing_v2_credentials",
                      return_value=Path("/synthetic/v2-viewonly.json")),
                patch("indodax_ai_bot.__main__.read_view_only_credentials",
                      return_value=("TEST_KEY","TEST_SECRET")),
                patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                      side_effect=fake),
                patch("indodax_ai_bot.m07_private_order_readonly.time.time",
                      return_value=T/1000),
            ):
                out,err=StringIO(),StringIO()
                with redirect_stdout(out),redirect_stderr(err):
                    rc=main([
                        "--m07-private-fill-fees-once",
                        "--m07-private-symbol","USDTIDR",
                        "--db",str(db),
                    ])
            self.assertEqual(rc,0,err.getvalue())
            self.assertEqual([x[0] for x in calls],[
                "/api/v2/account","/api/v2/order/histories",
                "/api/v2/order/histories","/api/v2/order",
                "/api/v2/myTrades",
            ])
            self.assertIn("symbol=usdtidr",calls[4][1])
            self.assertIn("startTime=",calls[4][1])
            self.assertIn("limit=1000",calls[4][1])
            report=json.loads(out.getvalue())["m07_private_readonly_order"]
            self.assertEqual(report["executed_base_qty_reconciled"],"51")
            self.assertEqual(report["gross_quote_idr"],"867000")
            self.assertEqual(report["commission_including_tax_idr"],"3300")
            self.assertEqual(report["net_quote_idr"],"863700")
            self.assertEqual(report["matched_fill_count"],2)
            self.assertEqual(report["requests_completed"],5)
            self.assertTrue(report["fills_and_fee_history_reconciled"])
            self.assertFalse(report["bot_ownership_verified"])
            self.assertFalse(report["spendable_idr_verified"])
            self.assertFalse(report["execution_allowed"])
            self.assertFalse(report["unknown_intent_released"])
            self.assertNotIn("TEST_SECRET",out.getvalue())
            self.assertNotIn(CID,out.getvalue())
            with DecisionJournal(db)._connect() as con:
                r=con.execute(
                    "SELECT qty_base,gross_quote_idr,commission_including_tax_idr,"
                    "net_quote_idr,fill_count FROM m07_exchange_readonly_fill_summaries"
                ).fetchone()
                self.assertEqual(r,("51","867000","3300","863700",2))
            with self.assertRaises(M07Hardlocked):
                OfficialV2OrderAdapter().dispatch()

    def test_restart_identical_receipts_replay_but_no_position_release(self):
        with TemporaryDirectory() as tmp:
            db=Path(tmp)/"v2.sqlite3"
            one,calls=read(db,two_fills())
            two,calls2=read(db,two_fills(),time_ms=T+1000)
            self.assertFalse(one["read_only_fill_journal_replay"])
            self.assertTrue(two["read_only_fill_journal_replay"])
            self.assertTrue(two["observation_replayed"])
            self.assertFalse(two["execution_allowed"])
            self.assertEqual(len(calls2),5)

    def test_missing_fill_and_duplicate_id_fail_before_receipt_credit(self):
        cases=[
            ({"data":[one_fill()]}, "M07_FILLS_EXECUTED_QUANTITY_MISMATCH"),
            ({"data":[one_fill(),one_fill()]}, "M07_FILLS_TRADE_ID_DUPLICATED"),
            ({"data":[]}, "M07_FILLS_EMPTY_OR_TRUNCATED"),
            ({"data":[one_fill(asset="usdt"),
                      one_fill(ident="9912",qty="31",
                               quote="527000",fee="2000")]},
             "M07_FILLS_NON_IDR_FEE_OR_AMOUNT_INVALID"),
            ({"data":[one_fill(order_id="foreign-id")]},
             "M07_FILLS_ORDER_IDENTITY_MISMATCH"),
            ({"data":[one_fill()]*1000}, "M07_FILLS_EMPTY_OR_TRUNCATED"),
        ]
        for raw,expected in cases:
            with self.subTest(expected=expected):
                with TemporaryDirectory() as tmp:
                    db=Path(tmp)/"v2.sqlite3"
                    with self.assertRaisesRegex(M07ReadOnlyError,expected):
                        read(db,raw)
                    with DecisionJournal(db)._connect() as con:
                        count=con.execute(
                            "SELECT COUNT(*) FROM m07_exchange_readonly_fill_summaries"
                        ).fetchone()[0]
                        self.assertEqual(count,0)

    def test_other_orders_in_same_history_do_not_contaminate_51_usdt(self):
        with TemporaryDirectory() as tmp:
            foreign=one_fill(
                ident="10433",qty="1",quote="17000",fee="50",
                order_id="usdtidr-limit-other",client_id="other",
            )
            raw=two_fills()
            raw["data"].append(foreign)
            result,calls=read(Path(tmp)/"journal.sqlite3",raw)
            self.assertEqual(result["matched_fill_count"],2)
            self.assertEqual(result["net_quote_idr"],"863700")
            self.assertEqual(len(calls),5)

    def test_source_change_on_replay_does_not_overwrite_audited_fills(self):
        with TemporaryDirectory() as tmp:
            db=Path(tmp)/"v2.sqlite3"
            read(db,two_fills())
            changed=two_fills()
            changed["data"][0]["commission"]="1200"
            with self.assertRaisesRegex(DecisionConflictError,
                                        "M07_READONLY_FILLS_REPLAY_CONFLICT"):
                read(db,changed,time_ms=T+1000)

    def test_synthetic_unknown_remains_blocked_after_verified_account_fills(self):
        with TemporaryDirectory() as tmp:
            db=Path(tmp)/"v2.sqlite3"
            a=offline_app(db)
            a.reserve_m07_offline_intent_once(
                intent_id="btc-unknown",
                draft=M07OrderDraft(
                    "BTCIDR","BUY","MARKET","btc-unknown",
                    quote_order_qty=10000,
                ),observed_epoch=START//1000-100,
            )
            a.simulate_m07_unknown_timeout_once(
                intent_id="btc-unknown",observed_epoch=START//1000-99,
            )
            result,_=read(db,two_fills())
            self.assertTrue(result["fills_and_fee_history_reconciled"])
            self.assertFalse(result["unknown_intent_released"])
            self.assertEqual(
                offline_app(db).inspect_m07_offline_intent_guard_once()[
                    "offline_unknown_holds"],1,
            )

    def test_missing_finish_time_fails_before_mytrades(self):
        with TemporaryDirectory() as tmp:
            db=Path(tmp)/"v2.sqlite3"
            bad=order_history()
            del bad["data"][0]["finishTime"]
            seen=[]
            payloads=[ACCOUNT,{"data":[]},bad,order_detail()]
            def answer(request):
                seen.append(request.path)
                return payloads[len(seen)-1]
            with (
                patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                      side_effect=answer),
                patch("indodax_ai_bot.m07_private_order_readonly.time.time",
                      return_value=T/1000),
            ):
                with self.assertRaisesRegex(M07ReadOnlyError,
                                            "M07_FILLS_HISTORY_FINISH_UNVERIFIED"):
                    offline_app(db).inspect_m07_private_fill_fees_once(
                        symbol="USDTIDR",api_key="TEST",
                        secret_key="TEST_SECRET",
                    )
            self.assertEqual(len(seen),4)

    def test_bad_cli_override_never_reads_private_key(self):
        with TemporaryDirectory() as tmp:
            db=Path(tmp)/"bad.sqlite3"
            with patch("indodax_ai_bot.__main__._existing_v2_credentials") as get:
                out,err=StringIO(),StringIO()
                with redirect_stdout(out),redirect_stderr(err):
                    rc=main([
                        "--m07-private-fill-fees-once",
                        "--m07-private-symbol","USDTIDR",
                        "--m07-private-client-order-id","bogus",
                        "--db",str(db),
                    ])
            self.assertEqual(rc,2)
            get.assert_not_called()
            self.assertFalse(db.exists())


if __name__=="__main__":
    unittest.main()
