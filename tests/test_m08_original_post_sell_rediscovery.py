"""M08-07: one original M03 scanner handoff per future settled-IDR event.

This tests the ORIGINAL CLI/orchestrator/journal with local mock ACCOUNT
orders/fills and a manually inserted future-M08-06 event fixture. None of
these fixtures attest real bot-owned order origination, IDR settlement,
wallet permissions, or actual LIVE activity. All code remains NO ORDER.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionConflictError, DecisionJournal
from indodax_ai_bot.m07_private_order_readonly import M07ReadOnlyOrder
from indodax_ai_bot.m07_readonly_fills import M07VerifiedReadOnlyFills
from indodax_ai_bot.orchestrator import CycleResult,DailyScalpingOrchestrator


ACCOUNT="a"*64
FOREIGN="b"*64
NOW=1793695000
SYMBOL="XRPIDR"
BUY_CID="m08ownedbuy001"
SELL_CID="m08ownedsell001"
EVT="m08settled001"
SETTLED_IDR="107000"
SELL_DIGEST="c"*64


def account_order(*, side="SELL",status="FILLED",symbol=SYMBOL,
                  client_id=None,qty="4"):
    cid=client_id or (SELL_CID if side=="SELL" else BUY_CID)
    return M07ReadOnlyOrder(
        symbol=symbol,client_order_id=cid,
        order_id=101 if side=="SELL" else 100,
        full_order_id="M08_SELL_101" if side=="SELL" else "M08_BUY_100",
        side=side,order_type="LIMIT",status=status,
        original_qty=Decimal(qty),
        executed_qty=Decimal("0") if status=="NEW" else Decimal(qty),
        created_ms=(NOW-600)*1000,
    )


def account_fills(order):
    sell=order.side=="SELL"
    gross=Decimal("108000") if sell else Decimal("100000")
    fee=Decimal("1000") if sell else Decimal("200")
    return M07VerifiedReadOnlyFills(
        account_symbol=order.symbol,client_order_id=order.client_order_id,
        full_order_id=order.full_order_id,side=order.side,
        executed_base=order.executed_qty,gross_quote=gross,
        total_commission_including_tax_quote=fee,
        net_quote=gross-fee if sell else gross+fee,
        count=1,sha256=SELL_DIGEST if sell else "d"*64,
    )


class OriginalM08PostSellRediscovery(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db=Path(temp.name)/"original-v2.sqlite3"
        self.journal=DecisionJournal(self.db)
        self.bot=DailyScalpingOrchestrator(Settings(),self.journal)

    def record_trade(self,*,side="SELL",status="FILLED",symbol=SYMBOL,
                     account=ACCOUNT):
        order=account_order(side=side,status=status,symbol=symbol)
        self.journal.record_m07_authenticated_readonly_order_status(
            account_fingerprint=account,order=order,
            observed_ms=NOW*1000-1000,
        )
        if status=="FILLED":
            self.journal.record_m07_exchange_readonly_fills(
                account_fingerprint=account,fills=account_fills(order),
                observed_ms=NOW*1000-500,
            )
        return order

    def future_event_fixture(self,*,event_id=EVT,symbol=SYMBOL,
                             account=ACCOUNT,net=SETTLED_IDR,
                             settled_ms=None,source=
                             "M08_RECONCILED_BOT_OWNED_SELL_IDR"):
        # Only in TEMPORARY test DB: there is no real M08-06 event
        # publisher or bot-owned submission receipt in production V2.
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO m08_position_settled_idr_events "
                "(event_id,account_fingerprint,symbol,"
                "source_buy_client_order_id,sell_client_order_id,"
                "sell_full_order_id,sell_fill_digest,"
                "bot_owned_execution_digest,idr_wallet_settlement_digest,"
                "net_settled_idr,settled_at_ms,source,status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (event_id,account,symbol,BUY_CID,SELL_CID,
                 "M08_SELL_101",SELL_DIGEST,"e"*64,"f"*64,
                 net,(NOW-5)*1000 if settled_ms is None else settled_ms,
                 source,"POSITION_SETTLED_IDR"),
            )

    def call(self,*,event_id=EVT,trace="m08freshscan1",
             bot=None,epoch=NOW,deep_limit=20):
        return (bot or self.bot).run_m08_post_sell_rediscovery_once(
            settlement_event_id=event_id,scan_trace_id=trace,
            observed_epoch=epoch,deep_limit=deep_limit,
        )

    @staticmethod
    def fake_scan(trace_id):
        return CycleResult(
            trace_id,None,"G1_PUBLIC_BULK_SOURCE_UNVERIFIED",False,
            stage_disposition="ADVISORY_ONLY_NO_OWNER_DECISION",
        )

    def test_absent_settlement_event_blocks_before_scanner(self):
        with patch.object(self.bot,"run_bulk_deep_cycle") as scanner:
            r=self.call()
        scanner.assert_not_called()
        self.assertEqual(r["status"],"M08_POSITION_SETTLED_IDR_NOT_VERIFIED")
        self.assertFalse(r["settled_event_claimed"])
        self.assertFalse(r["execution_allowed"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m08_post_sell_discovery_claims"
            ).fetchone()[0],0)

    def test_account_sell_filled_fee_and_wallet_not_a_settlement_event(self):
        self.record_trade(side="BUY")
        self.record_trade(side="SELL")
        with patch.object(self.bot,"run_bulk_deep_cycle") as scanner:
            r=self.call()
        scanner.assert_not_called()
        self.assertFalse(r["original_bulk_scanner_invoked"])

    def test_event_without_completed_buy_and_sell_is_refused(self):
        self.future_event_fixture()
        self.record_trade(side="SELL")
        with patch.object(self.bot,"run_bulk_deep_cycle") as scanner:
            r=self.call()
        scanner.assert_not_called()
        self.assertFalse(r["settled_event_claimed"])
        self.assertEqual(r["status"],"M08_POST_SELL_BUY_LOT_CONTEXT_MISSING")

    def test_future_mock_event_claims_exactly_once_then_existing_scanner(self):
        self.record_trade(side="BUY")
        self.record_trade(side="SELL")
        self.future_event_fixture()
        outcome=self.fake_scan("m08freshscan1")
        with patch.object(self.bot,"run_bulk_deep_cycle",
                          return_value=outcome) as scanner:
            r=self.call()
        scanner.assert_called_once_with(
            "m08freshscan1",market=None,
            min_volume_idr=Decimal("150000000"),deep_limit=20,
        )
        self.assertTrue(r["settled_event_claimed"])
        self.assertTrue(r["original_bulk_scanner_invoked"])
        self.assertFalse(r["previous_pair_preferred_or_blacklisted"])
        self.assertTrue(r["same_pair_reentry_requires_fresh_proposal"])
        self.assertFalse(r["prior_owner_final_reused"])
        self.assertFalse(r["new_buy_proposal_created"])
        self.assertFalse(r["new_buy_order_or_reservation_sent"])
        self.assertFalse(r["execution_allowed"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m08_post_sell_discovery_claims"
            ).fetchone()[0],1)

    def test_restart_replay_never_runs_scanner_twice(self):
        self.record_trade(side="BUY")
        self.record_trade(side="SELL")
        self.future_event_fixture()
        with patch.object(self.bot,"run_bulk_deep_cycle",
                          return_value=self.fake_scan("m08freshscan1")):
            self.call()
        restarted=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.db))
        with patch.object(restarted,"run_bulk_deep_cycle") as scanner:
            again=self.call(bot=restarted,trace="m08brandnewscan")
        scanner.assert_not_called()
        self.assertEqual(again["status"],"M08_SETTLED_EVENT_ALREADY_CLAIMED")
        self.assertFalse(again["settled_event_claimed"])

    def test_concurrent_claim_only_one_reaches_existing_scanner(self):
        self.record_trade(side="BUY")
        self.record_trade(side="SELL")
        self.future_event_fixture()
        with patch.object(
            self.bot,"run_bulk_deep_cycle",
            side_effect=lambda trace_id,**k:self.fake_scan(trace_id),
        ) as scanner,ThreadPoolExecutor(max_workers=8) as pool:
            rows=list(pool.map(
                lambda i:self.call(trace="m08scanparallel"+str(i)),
                range(8),
            ))
        self.assertEqual(sum(r["settled_event_claimed"] for r in rows),1)
        self.assertEqual(sum(r["original_bulk_scanner_invoked"] for r in rows),1)
        self.assertEqual(scanner.call_count,1)

    def test_scanner_error_consumes_event_and_does_not_auto_retry(self):
        self.record_trade(side="BUY")
        self.record_trade(side="SELL")
        self.future_event_fixture()
        with patch.object(self.bot,"run_bulk_deep_cycle",
                          side_effect=RuntimeError("PUBLIC_SCAN_CRASH")):
            with self.assertRaisesRegex(RuntimeError,"PUBLIC_SCAN_CRASH"):
                self.call()
        with patch.object(self.bot,"run_bulk_deep_cycle") as scanner:
            status=self.call(trace="m08retryblocked")
        scanner.assert_not_called()
        self.assertEqual(status["status"],"M08_SETTLED_EVENT_ALREADY_CLAIMED")

    def test_account_or_pair_mismatch_rejected_no_scan(self):
        self.record_trade(side="BUY")
        self.record_trade(side="SELL")
        self.future_event_fixture(account=FOREIGN)
        with patch.object(self.bot,"run_bulk_deep_cycle") as scanner:
            status=self.call()
        scanner.assert_not_called()
        self.assertEqual(status["status"],"M08_POST_SELL_BUY_LOT_CONTEXT_MISSING")

    def test_sell_partial_or_pending_is_not_final_settlement(self):
        self.record_trade(side="BUY")
        self.record_trade(side="SELL",status="NEW")
        self.future_event_fixture()
        with patch.object(self.bot,"run_bulk_deep_cycle") as scanner:
            status=self.call()
        scanner.assert_not_called()
        self.assertEqual(status["status"],"M08_SELL_ORDER_OR_FILLS_UNVERIFIED")

    def test_idr_proceeds_and_fee_digest_conflict_fails_closed(self):
        self.record_trade(side="BUY")
        self.record_trade(side="SELL")
        self.future_event_fixture(net="108000")
        with patch.object(self.bot,"run_bulk_deep_cycle") as scanner:
            with self.assertRaisesRegex(DecisionConflictError,
                                        "M08_POST_SELL_FILL_SETTLEMENT_CONFLICT"):
                self.call()
        scanner.assert_not_called()

    def test_unknown_local_intent_blocks_final_event_claim(self):
        self.record_trade(side="BUY")
        self.record_trade(side="SELL")
        self.future_event_fixture()
        from indodax_ai_bot.m07_order_adapter import M07OrderDraft
        d=M07OrderDraft(
            "ETHIDR","BUY","LIMIT","m08pendingunknown",
            price=Decimal("2000"),quantity=Decimal("2"),
        )
        self.bot.reserve_m07_offline_intent_once(
            intent_id="m08pendingunknown",draft=d,observed_epoch=NOW,
        )
        self.bot.simulate_m07_unknown_timeout_once(
            intent_id="m08pendingunknown",observed_epoch=NOW+1,
        )
        with patch.object(self.bot,"run_bulk_deep_cycle") as scanner:
            result=self.call()
        scanner.assert_not_called()
        self.assertEqual(result["status"],
                         "M08_POST_SELL_UNRESOLVED_ORDER_COMMITMENTS")

    def test_stale_event_no_fresh_reentry_reuse(self):
        self.record_trade(side="BUY")
        self.record_trade(side="SELL")
        self.future_event_fixture(settled_ms=(NOW-7200)*1000)
        with patch.object(self.bot,"run_bulk_deep_cycle") as scanner:
            with self.assertRaisesRegex(DecisionConflictError,
                                        "M08_POST_SELL_SETTLEMENT_SOURCE_INVALID"):
                self.call()
        scanner.assert_not_called()

    def test_original_cli_absent_event_never_sends_order_or_scans(self):
        from indodax_ai_bot.__main__ import main
        output=StringIO()
        with redirect_stdout(output),patch.object(
                DailyScalpingOrchestrator,"run_bulk_deep_cycle") as scanner:
            status=main([
                "--m08-post-sell-rediscovery-offline-once",
                "--m08-settled-event-id",EVT,
                "--db",str(self.db),
            ])
        self.assertEqual(status,0)
        scanner.assert_not_called()
        report=json.loads(output.getvalue())
        self.assertFalse(report["live_trading_authorized"])
        self.assertFalse(report["execution_allowed"])
        self.assertFalse(report["m08_post_sell_rediscovery"][
            "original_bulk_scanner_invoked"])


if __name__=="__main__":
    unittest.main()
