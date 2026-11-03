"""M08-02 original V2 myTrades fill clock -> position holding-time policy.

No fake BUY-time caller parameter, no bot-owned lot, no BUY/SELL/CANCEL.
All real HTTP response boundaries mocked. Same original SQLite journal.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sqlite3
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionConflictError, DecisionJournal
from indodax_ai_bot.m07_private_order_readonly import M07ReadOnlyError
from indodax_ai_bot.m07_readonly_fills import reconcile_m07_mytrades
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from tests.test_m08_original_exchange_buy_admission import (
    ACCOUNT_A, ACCOUNT_B, CID, SYMBOL, NOW, CREATED_MS, order, fills,
)


FIRST_MS=CREATED_MS+1000
LAST_MS=CREATED_MS+60000
FIRST=FIRST_MS//1000
LAST=LAST_MS//1000


def trades(first=FIRST_MS,last=LAST_MS, qty2="3", *,
           client=CID, side=True):
    return {"data":[
        {"symbol":"xrpidr","clientOrderId":client,"orderId":"ORDER_7788",
         "isBuyer":side,"isMaker":False,"tradeId":"20002",
         "time":last,"qty":qty2,"quoteQty":"75000",
         "commission":"150","price":"25000","commissionAsset":"idr"},
        {"symbol":"xrpidr","clientOrderId":client,"orderId":"ORDER_7788",
         "isBuyer":side,"isMaker":True,"tradeId":"20001",
         "time":first,"qty":"1","quoteQty":"25000",
         "commission":"50","price":"25000","commissionAsset":"idr"},
    ]}


class OriginalM08TradeTimeTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db=Path(temp.name)/"original-v2.sqlite3"
        self.bot=DailyScalpingOrchestrator(Settings(), DecisionJournal(self.db))

    def record(self, *, account=ACCOUNT_A, payload=None):
        bound=order()
        journal=self.bot.journal
        journal.record_m07_authenticated_readonly_order_status(
            account_fingerprint=account,order=bound,observed_ms=NOW*1000,
        )
        receipt=reconcile_m07_mytrades(
            payload if payload is not None else trades(),
            order=bound,window_start_ms=CREATED_MS-60000,
            window_end_ms=CREATED_MS+120000,
        )
        record=journal.record_m07_exchange_readonly_fills(
            account_fingerprint=account,fills=receipt,
            observed_ms=NOW*1000+1,
        )
        return receipt,record

    def review(self, *, observed=FIRST+300, account=ACCOUNT_A,
               bot=None, pump=False, trail=False, fresh=False, risk=False):
        return (bot or self.bot).review_m08_holding_time_reference_offline(
            account_fingerprint=account,symbol=SYMBOL,client_order_id=CID,
            observed_epoch=observed,
            pump_continuation_unverified=pump,
            trailing_protection_unverified=trail,
            market_source_fresh_unverified=fresh,
            risk_review_clear_unverified=risk,
        )

    def test_no_buy_or_legacy_receipts_without_trade_clock_stay_unverified(self):
        missing=self.review()
        self.assertEqual(missing["status"],
            "M08_HOLDING_TIME_BUY_FILL_TIMESTAMP_UNVERIFIED")
        self.assertIsNone(missing["holding_time_reference"])
        self.assertEqual(missing["sellable_bot_owned_base_qty"],"0")
        self.bot.journal.record_m07_authenticated_readonly_order_status(
            account_fingerprint=ACCOUNT_A,order=order(),
            observed_ms=NOW*1000,
        )
        self.bot.journal.record_m07_exchange_readonly_fills(
            account_fingerprint=ACCOUNT_A,fills=fills(),
            observed_ms=NOW*1000+1,
        )
        old=self.review()
        self.assertEqual(old["trade_time_source"],"UNAVAILABLE")
        self.assertFalse(old["time_exit_order_authorized"])

    def test_real_matched_partial_trade_times_not_order_submit_time(self):
        receipt,_=self.record()
        self.assertEqual(receipt.first_trade_ms,FIRST_MS)
        self.assertEqual(receipt.last_trade_ms,LAST_MS)
        result=self.review()
        self.assertEqual(result["account_first_buy_fill_epoch_reference"],FIRST)
        self.assertEqual(result["account_last_buy_fill_epoch_reference"],LAST)
        self.assertEqual(result["elapsed_seconds_from_first_buy_fill_reference"],300)
        self.assertEqual(result["holding_phase_reference"],"FAST_SCALP_MONITOR")
        self.assertEqual(result["trade_time_source"],
                         "INDODAX_TAPIV2_MYTRADES_ACCOUNT_ONLY")
        self.assertNotEqual(result["account_first_buy_fill_epoch_reference"],
                            CREATED_MS//1000)
        self.assertFalse(result["account_buy_fill_epoch_bot_owned"])
        self.assertFalse(result["execution_allowed"])

    def test_time_windows_at_30_60_90_minutes(self):
        self.record()
        phases=((1799,"FAST_SCALP_MONITOR","NORMAL"),
                (1800,"EXIT_PRIORITY_REVIEW","ELEVATED"),
                (3600,"TIME_STOP_PROTECTIVE_REVIEW","URGENT"),
                (5400,"EXTENDED_POSITION_RISK_REVIEW","URGENT"))
        for seconds,phase,urgency in phases:
            with self.subTest(seconds=seconds):
                result=self.review(observed=FIRST+seconds)
                self.assertEqual(result["holding_phase_reference"],phase)
                self.assertEqual(result["risk_review_priority_reference"],urgency)
                self.assertEqual(result["sellable_bot_owned_base_qty"],"0")
                self.assertFalse(result["execution_allowed"])

    def test_bounded_pump_exception_requires_all_four_flags(self):
        self.record()
        exceptional=self.review(observed=FIRST+4200,
            pump=True,trail=True,fresh=True,risk=True)
        self.assertEqual(exceptional["holding_phase_reference"],
            "PUMP_TRAILING_EXCEPTION_REVIEW")
        for flags in ({"risk":False},{"fresh":False},{"trail":False},
                      {"pump":False}):
            with self.subTest(flags=flags):
                args=dict(pump=True,trail=True,fresh=True,risk=True)
                args.update(flags)
                blocked=self.review(observed=FIRST+4200,**args)
                self.assertEqual(blocked["holding_phase_reference"],
                                 "TIME_STOP_PROTECTIVE_REVIEW")
        extended=self.review(observed=FIRST+5400,
            pump=True,trail=True,fresh=True,risk=True)
        self.assertEqual(extended["risk_review_priority_reference"],"URGENT")
        self.assertFalse(extended["time_exit_order_authorized"])

    def test_trade_before_order_creation_rejected_not_saved(self):
        with self.assertRaisesRegex(M07ReadOnlyError,
                                    "M07_FILLS_TRADE_TIME_BEFORE_ORDER"):
            self.record(payload=trades(first=CREATED_MS-1000))
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m07_exchange_readonly_fill_summaries"
            ).fetchone()[0],0)

    def test_wrong_or_missing_matched_trade_time_fails_closed(self):
        for row in (trades(client="FOREIGN"),trades(side=False),
                    trades(qty2="2")):
            with self.subTest(row=row["data"][0]):
                with self.assertRaises(M07ReadOnlyError):
                    reconcile_m07_mytrades(row,order=order(),
                        window_start_ms=CREATED_MS-60000,
                        window_end_ms=CREATED_MS+120000)

    def test_foreign_account_cannot_reuse_trade_time_reference(self):
        self.record(account=ACCOUNT_B)
        observed=self.review(account=ACCOUNT_A)
        self.assertIsNone(observed["holding_time_reference"])
        self.assertEqual(observed["status"],
            "M08_HOLDING_TIME_BUY_FILL_TIMESTAMP_UNVERIFIED")

    def test_replay_identical_receipt_idempotent_new_times_conflict(self):
        original,_=self.record()
        again=self.bot.journal.record_m07_exchange_readonly_fills(
            account_fingerprint=ACCOUNT_A,fills=original,
            observed_ms=NOW*1000+2)
        self.assertTrue(again["replay"])
        tampered=replace(original,first_trade_ms=FIRST_MS+500)
        with self.assertRaisesRegex(DecisionConflictError,
                                  "M08_ACCOUNT_FILL_TIME_REPLAY_CONFLICT"):
            self.bot.journal.record_m07_exchange_readonly_fills(
                account_fingerprint=ACCOUNT_A,fills=tampered,
                observed_ms=NOW*1000+3)
        self.assertEqual(self.review()["account_first_buy_fill_epoch_reference"],FIRST)

    def test_journal_rejects_incomplete_or_future_timestamp_atomically(self):
        initial, _=self.record(account=ACCOUNT_B)
        self.bot.journal.record_m07_authenticated_readonly_order_status(
            account_fingerprint=ACCOUNT_A,order=order(),
            observed_ms=NOW*1000)
        for changed in (
            replace(initial,first_trade_ms=None),
            replace(initial,last_trade_ms=NOW*1000+10),
        ):
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(DecisionConflictError,
                                            "M08_ACCOUNT_FILL_TIME_SOURCE_CONFLICT"):
                    self.bot.journal.record_m07_exchange_readonly_fills(
                        account_fingerprint=ACCOUNT_A,fills=changed,
                        observed_ms=NOW*1000+1)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m07_exchange_readonly_fill_summaries "
                "WHERE account_fingerprint=?",(ACCOUNT_A,)
            ).fetchone()[0],0)

    def test_modified_persisted_clock_detected_at_admission(self):
        self.record()
        with sqlite3.connect(self.db) as db:
            db.execute(
                "UPDATE m08_account_buy_trade_times SET last_trade_ms=? "
                "WHERE account_fingerprint=?",(FIRST_MS-1,ACCOUNT_A))
        with self.assertRaisesRegex(DecisionConflictError,
                                    "M08_ACCOUNT_FILL_TIME_SOURCE_CONFLICT"):
            self.review()

    def test_restart_concurrent_reviews_do_not_create_owned_position(self):
        self.record()
        restarted=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.db))
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(
                lambda i:self.review(bot=restarted,observed=FIRST+1800+i),
                range(8)))
        self.assertEqual(len(results),8)
        self.assertTrue(all(
            r["elapsed_seconds_from_first_buy_fill_reference"]>=1800
            and r["sellable_bot_owned_base_qty"]=="0"
            and not r["time_exit_order_authorized"]
            for r in results))
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m08_account_buy_trade_times"
            ).fetchone()[0],1)

    def test_original_readonly_fill_reader_wires_real_times_without_extra_get(self):
        history={"data":[{
            "symbol":"xrpidr","clientOrderId":CID,
            "submitTime":CREATED_MS,"finishTime":CREATED_MS+120000,
        }]}
        exchange_order={
            "symbol":SYMBOL,"clientOrderId":CID,"orderId":7788,
            "fullOrderId":"ORDER_7788","side":"BUY","type":"LIMIT",
            "status":"FILLED","origQty":"4","executedQty":"4",
            "time":CREATED_MS,
        }
        with (patch("indodax_ai_bot.m07_private_order_readonly.time.time",
                    return_value=NOW),
              patch("indodax_ai_bot.m07_private_order_readonly._verify_view_only_uid",
                    return_value=777),
              patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                    side_effect=[history,{"data":[]},exchange_order,trades()]
                    ) as http):
            report=self.bot.inspect_m07_private_fill_fees_once(
                symbol=SYMBOL,api_key="VIEW_ONLY_TEST",
                secret_key="DUMMY_TEST",
            )
        self.assertEqual(http.call_count,4)
        self.assertEqual(report["m08_buy_position_source"][
            "account_first_buy_fill_epoch_reference"],FIRST)
        self.assertEqual(report["m08_buy_position_source"][
            "account_last_buy_fill_epoch_reference"],LAST)
        self.assertFalse(report["m08_buy_position_source"][
            "position_manager_admission"])
        self.assertFalse(report["execution_allowed"])


if __name__=="__main__":
    unittest.main()
