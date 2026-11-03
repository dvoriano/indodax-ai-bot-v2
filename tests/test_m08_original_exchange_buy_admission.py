"""M08 original V2: actual-account BUY economics are NOT bot-owned inventory.

The real private reader is exercised with only HTTPS responses mocked.
No source attestation, order submission, trade or service is created.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sqlite3
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionConflictError, DecisionJournal
from indodax_ai_bot.m07_order_adapter import M07OrderDraft
from indodax_ai_bot.m07_private_order_readonly import M07ReadOnlyOrder
from indodax_ai_bot.m07_readonly_fills import M07VerifiedReadOnlyFills
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


NOW = 1793691000
CREATED_MS = (NOW - 3600) * 1000
ACCOUNT_A = "a" * 64
ACCOUNT_B = "b" * 64
CID = "originalm08test01"
SYMBOL = "XRPIDR"


def order(side="BUY", status="FILLED", executed="4", cid=CID):
    return M07ReadOnlyOrder(
        symbol=SYMBOL, client_order_id=cid, order_id=7788,
        full_order_id="ORDER_7788", side=side, order_type="LIMIT",
        status=status, original_qty=D("4"),
        executed_qty=D(executed), created_ms=CREATED_MS,
    )


def fills(side="BUY", qty="4", gross="100000", fee="200", cost=None):
    g=D(gross)
    f=D(fee)
    return M07VerifiedReadOnlyFills(
        account_symbol=SYMBOL, client_order_id=CID,
        full_order_id="ORDER_7788", side=side,
        executed_base=D(qty), gross_quote=g,
        total_commission_including_tax_quote=f,
        net_quote=(D(cost) if cost is not None
                   else g + f if side == "BUY" else g - f),
        count=1, sha256=sha256(b"m08-test-fill").hexdigest(),
    )


class M08OriginalPositionAdmission(unittest.TestCase):
    def setUp(self):
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.db = Path(td.name) / "original-decision-journal.sqlite3"
        self.bot = DailyScalpingOrchestrator(Settings(),DecisionJournal(self.db))

    def record(self, *, account=ACCOUNT_A, value=None, receipts=True):
        self.bot.journal.record_m07_authenticated_readonly_order_status(
            account_fingerprint=account,order=value or order(),
            observed_ms=NOW*1000,
        )
        if receipts:
            self.bot.journal.record_m07_exchange_readonly_fills(
                account_fingerprint=account, fills=fills(),
                observed_ms=NOW*1000+1,
            )

    def review(self, account=ACCOUNT_A):
        return self.bot.review_m08_buy_position_admission_readonly(
            account_fingerprint=account,symbol=SYMBOL,
            client_order_id=CID,
        )

    def test_no_order_and_no_fills_never_create_position(self):
        result=self.review()
        self.assertEqual(result["status"],"M08_EXCHANGE_BUY_NOT_OBSERVED")
        self.assertEqual(result["verified_bot_owned_lots"],0)
        self.assertFalse(result["execution_allowed"])

    def test_account_only_buy_fill_cost_including_tax_not_bot_position(self):
        self.record()
        result=self.review()
        self.assertEqual(result["status"],
                         "M08_ACCOUNT_BUY_COST_BASIS_ONLY_NOT_BOT_OWNED")
        self.assertEqual(result["account_buy_qty_reference"],"4")
        self.assertEqual(result["account_buy_gross_idr_reference"],"100000")
        self.assertEqual(
            result["account_buy_fees_including_tax_idr_reference"],"200")
        self.assertEqual(result["account_buy_cost_basis_idr_reference"],
                         "100200")
        self.assertTrue(result["account_buy_fills_and_fees_reconciled"])
        self.assertFalse(result["bot_owned_buy_fill_proven"])
        self.assertFalse(result["position_manager_admission"])
        self.assertEqual(result["sellable_bot_owned_base_qty"],"0")
        self.assertFalse(result["m08_sell_target_authorized"])
        self.assertFalse(result["execution_allowed"])

    def test_filled_buy_without_complete_fee_history_refused(self):
        self.record(receipts=False)
        x=self.review()
        self.assertEqual(x["status"],"M08_BUY_FILL_FEE_RECEIPTS_MISSING")
        self.assertFalse(x["account_buy_fills_and_fees_reconciled"])
        self.assertEqual(x["verified_bot_owned_base_qty"],"0")

    def test_partial_buy_does_not_mint_inventory(self):
        self.record(value=order(status="PARTIALLY_FILLED",executed="1"),
                    receipts=False)
        x=self.review()
        self.assertEqual(x["status"],"M08_BUY_FILL_INCOMPLETE_OR_UNSETTLED")
        self.assertEqual(x["sellable_bot_owned_base_qty"],"0")

    def test_other_account_cannot_borrow_account_buy_fills(self):
        self.record(account=ACCOUNT_B)
        x=self.review(ACCOUNT_A)
        self.assertEqual(x["status"],"M08_EXCHANGE_BUY_NOT_OBSERVED")
        self.assertFalse(x["position_manager_admission"])

    def test_recorded_sell_or_historical_conversion_cannot_count_as_buy(self):
        self.bot.journal.record_m07_authenticated_readonly_order_status(
            account_fingerprint=ACCOUNT_A,
            order=order(side="SELL"),observed_ms=NOW*1000,
        )
        x=self.review()
        self.assertEqual(x["status"],"M08_EXCHANGE_ORDER_NOT_BUY")
        self.assertEqual(x["verified_bot_owned_base_qty"],"0")

    def test_matching_synthetic_intent_still_not_bot_submission_proof(self):
        draft=M07OrderDraft(
            symbol=SYMBOL,side="BUY",order_type="LIMIT",
            client_order_id=CID,price=D("25000"),quantity=D("4"),
        )
        self.bot.reserve_m07_offline_intent_once(
            intent_id=CID,draft=draft,
            observed_epoch=CREATED_MS//1000,
        )
        self.record()
        x=self.review()
        self.assertTrue(x["local_synthetic_intent_correlation_only"])
        self.assertEqual(x["verified_bot_owned_lots"],0)
        self.assertFalse(x["independent_bot_order_submit_attested"])
        self.assertFalse(x["position_manager_admission"])

    def test_incorrect_qty_or_cost_basis_corruption_fails_closed(self):
        self.record()
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "UPDATE m07_exchange_readonly_fill_summaries "
                "SET net_quote_idr='100100' WHERE client_order_id=?",(CID,),
            )
        with self.assertRaisesRegex(DecisionConflictError,
                                    "M08_BUY_COST_OR_QUANTITY_CONFLICT"):
            self.review()
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "UPDATE m07_exchange_readonly_fill_summaries "
                "SET net_quote_idr='100200',qty_base='5' "
                "WHERE client_order_id=?",(CID,),
            )
        with self.assertRaisesRegex(DecisionConflictError,
                                    "M08_BUY_COST_OR_QUANTITY_CONFLICT"):
            self.review()

    def test_invalid_source_digest_or_fee_provenance_fails_closed(self):
        self.record()
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "UPDATE m07_exchange_readonly_fill_summaries "
                "SET fill_digest='arbitrary' WHERE client_order_id=?",(CID,),
            )
        with self.assertRaisesRegex(DecisionConflictError,
                                    "M08_BUY_FILL_RECEIPT_IDENTITY_CONFLICT"):
            self.review()

    def test_restart_and_eight_concurrent_account_reviews_no_ownership(self):
        self.record()
        restarted=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.db))
        def inspect(_):
            return restarted.review_m08_buy_position_admission_readonly(
                account_fingerprint=ACCOUNT_A,symbol=SYMBOL,
                client_order_id=CID,
            )
        with ThreadPoolExecutor(max_workers=8) as workers:
            results=list(workers.map(inspect,range(8)))
        self.assertTrue(all(
            r["account_buy_cost_basis_idr_reference"]=="100200"
            and not r["position_manager_admission"]
            and r["sellable_bot_owned_base_qty"]=="0"
            for r in results
        ))
        with sqlite3.connect(self.db) as db:
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM m07_exchange_readonly_fill_summaries"
                ).fetchone()[0],1,
            )

    def test_original_private_fill_reader_wires_position_gate_with_no_new_get(self):
        # This exercises the SAME original orchestrator → M07 View Only
        # four-read boundary (account permission is mocked separately) →
        # journal → M08 position admission; no additional provider/API call.
        history={"data":[{
            "symbol":"xrpidr","clientOrderId":CID,
            "submitTime":CREATED_MS,"finishTime":CREATED_MS+120000,
        }]}
        exchange_order={
            "symbol":SYMBOL,"clientOrderId":CID,
            "orderId":7788,"fullOrderId":"ORDER_7788",
            "side":"BUY","type":"LIMIT","status":"FILLED",
            "origQty":"4","executedQty":"4","time":CREATED_MS,
        }
        trades={"data":[{
            "symbol":"xrpidr","clientOrderId":CID,
            "orderId":"ORDER_7788","isBuyer":True,"isMaker":False,
            "tradeId":"123456789","time":CREATED_MS+1000,
            "qty":"4","quoteQty":"100000","commission":"200",
            "price":"25000","commissionAsset":"idr",
        }]}
        with (patch("indodax_ai_bot.m07_private_order_readonly.time.time",
                    return_value=NOW),
              patch("indodax_ai_bot.m07_private_order_readonly._verify_view_only_uid",
                    return_value=777) as uid,
              patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                    side_effect=[history,{"data":[]},exchange_order,trades]
                    ) as http):
            report=self.bot.inspect_m07_private_fill_fees_once(
                symbol=SYMBOL,api_key="VIEW_ONLY_KEY_TEST",
                secret_key="DUMMY_SIGNING_SECRET_TEST",
            )
        uid.assert_called_once()
        self.assertEqual(http.call_count,4)
        m08=report["m08_buy_position_source"]
        self.assertEqual(m08["status"],
                         "M08_ACCOUNT_BUY_COST_BASIS_ONLY_NOT_BOT_OWNED")
        self.assertTrue(m08["account_buy_fills_and_fees_reconciled"])
        self.assertEqual(m08["account_buy_cost_basis_idr_reference"],"100200")
        self.assertFalse(m08["position_manager_admission"])
        self.assertFalse(report["bot_ownership_verified"])
        self.assertFalse(report["execution_allowed"])


if __name__=="__main__":
    unittest.main()
