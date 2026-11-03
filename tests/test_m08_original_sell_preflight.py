"""M08-05 original V2: one non-executing tier/protection/SELL state preflight.

All signed status and myTrades order views are mocked fixtures in the
existing original journal. This never submits, cancels, retries, releases an
UNKNOWN, creates a bot-owned lot, or mints a sellable position.
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
from indodax_ai_bot.journal import DecisionConflictError,DecisionJournal
from indodax_ai_bot.m06_ownership_ledger import M06OwnershipLedger
from indodax_ai_bot.m07_order_adapter import M07OrderDraft
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from tests.test_m08_original_exchange_buy_admission import (
    ACCOUNT_A,ACCOUNT_B,CID,SYMBOL,NOW,CREATED_MS,order,
)
from tests.test_m08_original_trade_time import (
    FIRST,trades,
)
from indodax_ai_bot.m07_readonly_fills import reconcile_m07_mytrades


def sell(client="m08preflight001",qty="4",price="28000"):
    return M07OrderDraft(
        symbol=SYMBOL, side="SELL",order_type="LIMIT",
        client_order_id=client,quantity=D(qty),price=D(price),
    )


class OriginalM08SellPreflightTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)
        self.db=root/"original-decisions.sqlite3"
        self.m06=root/"original-m06.sqlite3"
        self.bot=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.db))
        M06OwnershipLedger(self.m06)
        self.uid=987654
        self.observed=FIRST+1200

    def record_buy(self, *,account=ACCOUNT_A):
        obj=order()
        self.bot.journal.record_m07_authenticated_readonly_order_status(
            account_fingerprint=account,order=obj,observed_ms=NOW*1000,
        )
        receipt=reconcile_m07_mytrades(
            trades(),order=obj,
            window_start_ms=CREATED_MS-60000,
            window_end_ms=CREATED_MS+120000,
        )
        self.bot.journal.record_m07_exchange_readonly_fills(
            account_fingerprint=account,
            fills=receipt,observed_ms=NOW*1000+1,
        )

    def record_sell_status(self, *, status="NEW",executed="0",uid=ACCOUNT_A):
        obj=replace(
            order(),side="SELL",status=status,original_qty=D("3"),
            executed_qty=D(executed),client_order_id="account_sell_001",
            full_order_id="ACCOUNT_SELL_1",order_id=9991,
        )
        self.bot.journal.record_m07_authenticated_readonly_order_status(
            account_fingerprint=uid,order=obj,
            observed_ms=NOW*1000+1000 if status=="NEW"
                else NOW*1000+2000,
        )

    def review(self, *,draft=None,bot=None,account=ACCOUNT_A,
               qty="4",mark="28000",stop="22000",high="28000",
               trail="26000",gross="112000",fee="500",slippage="100",
               observed=None, fresh=True):
        return (bot or self.bot).review_m08_sell_preflight_offline_once(
            sell_draft=draft if draft is not None else sell(qty=qty),
            source_buy_client_order_id=CID,
            account_fingerprint=account,account_uid=self.uid,
            local_m06_ledger_db=str(self.m06),
            observed_epoch=self.observed if observed is None else observed,
            expected_sell_notional_idr=D(gross),
            estimated_sell_fees_idr=D(fee),
            incremental_slippage_idr=D(slippage),
            observed_mark_price_idr=D(mark),
            stop_loss_price_idr=D(stop),
            high_watermark_price_idr=D(high),
            trailing_stop_price_idr=D(trail) if trail is not None else None,
            quote_fresh_unverified=fresh,
        )

    def test_no_buy_or_fee_source_still_fails_closed_without_order(self):
        out=self.review()
        self.assertEqual(out["status"],"M08_SELL_PREFLIGHT_BUY_SOURCE_UNVERIFIED")
        self.assertFalse(out["bot_owned_buy_fill_verified"])
        self.assertEqual(out["maximum_executable_sell_base_qty"],"0")
        self.assertTrue(out["preflight_only_not_order_intent"])
        self.assertFalse(out["execution_allowed"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m07_offline_order_intents"
            ).fetchone()[0],0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m08_offline_sell_preflight_receipts"
            ).fetchone()[0],1)

    def test_t3_net_tier_references_do_not_create_sell(self):
        self.record_buy()
        out=self.review()
        self.assertEqual(out["highest_net_tier_reference"],"T3")
        self.assertEqual(out["exit_hint_reference"],"NET_TIER_REVIEW_ONLY")
        self.assertEqual(out["status"],"M08_SELL_PREFLIGHT_BOT_OWNED_LOT_UNVERIFIED")
        self.assertEqual(out["verified_bot_owned_base_qty"],"0")
        self.assertFalse(out["owner_order_specific_final_verified"])
        self.assertFalse(out["sell_reservation_created"])
        self.assertFalse(out["order_sent"])

    def test_protective_loss_overrides_missing_profit_tier(self):
        self.record_buy()
        out=self.review(mark="24000",stop="24500",high="27000",
                        trail="25500",gross="96000")
        self.assertIsNone(out["highest_net_tier_reference"])
        self.assertEqual(out["protective_trigger_reference"],
                         "STOP_LOSS_REVIEW")
        self.assertEqual(out["exit_hint_reference"],
                         "PROTECTIVE_RISK_REVIEW_ONLY")
        self.assertEqual(out["maximum_executable_sell_base_qty"],"0")

    def test_60_minute_time_stop_with_no_tier_remains_no_order(self):
        self.record_buy()
        out=self.review(mark="25000",stop="21000",high="27000",
                        trail="24500",gross="100000",
                        observed=FIRST+3600)
        self.assertEqual(out["protective_trigger_reference"],
                         "TIME_STOP_URGENT_REVIEW")
        self.assertEqual(out["exit_hint_reference"],
                         "PROTECTIVE_RISK_REVIEW_ONLY")
        self.assertFalse(out["retry_allowed"])

    def test_stale_mark_prevents_tier_authorization_even_when_t3(self):
        self.record_buy()
        out=self.review(fresh=False)
        self.assertEqual(out["highest_net_tier_reference"],"T3")
        self.assertEqual(out["exit_hint_reference"],
                         "RECHECK_STALE_REFERENCE_NO_ORDER")
        self.assertFalse(out["execution_allowed"])

    def test_account_partial_sell_and_cancel_do_not_release(self):
        self.record_buy()
        self.record_sell_status(status="PARTIALLY_FILLED",executed="1")
        out=self.review()
        self.assertEqual(out["status"],
                         "M08_SELL_PREFLIGHT_PARTIAL_OR_PENDING_SELL")
        self.assertEqual(out["exchange_partial_sell_count_reference"],1)
        self.assertEqual(out["exchange_pending_sell_base_reference"],"2")
        self.assertEqual(out["maximum_executable_sell_base_qty"],"0")
        self.record_sell_status(status="CANCELLED",executed="1")
        another=self.review(draft=sell(client="m08preflight002"))
        self.assertEqual(another["exchange_cancelled_sell_count_reference"],1)
        self.assertEqual(another["exchange_pending_sell_base_reference"],"0")
        self.assertFalse(another["cancelled_order_qty_recredited"])
        self.assertFalse(another["execution_allowed"])

    def test_account_filled_sell_cannot_prove_bot_owned_or_settlement(self):
        self.record_buy()
        self.record_sell_status(status="FILLED",executed="3")
        r=self.review()
        self.assertEqual(r["exchange_filled_sell_count_reference"],1)
        self.assertEqual(r["exchange_sell_viewonly_status"],
                         "M08_SELL_FILLED_NOT_OWNERSHIP")
        self.assertFalse(r["realized_pnl_recorded"])
        self.assertFalse(r["unknown_order_released"])

    def test_unknown_blocks_future_preflight_across_restart(self):
        self.record_buy()
        draft=M07OrderDraft(
            symbol="ETHIDR",side="BUY",order_type="LIMIT",
            client_order_id="m08unknown001",price=D("2000"),quantity=D("2"))
        self.bot.reserve_m07_offline_intent_once(
            intent_id="m08unknown001",draft=draft,
            observed_epoch=NOW,
        )
        self.bot.simulate_m07_unknown_timeout_once(
            intent_id="m08unknown001",observed_epoch=NOW+1)
        restarted=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.db))
        r=self.review(bot=restarted)
        self.assertEqual(r["status"],"M08_SELL_PREFLIGHT_UNKNOWN_HOLD_BLOCKED")
        self.assertEqual(r["original_m07_unknown_holds"],1)
        self.assertFalse(r["unknown_order_released"])
        self.assertFalse(r["retry_allowed"])
        self.assertFalse(r["execution_allowed"])

    def test_same_client_and_scenario_replay_without_duplicate_order(self):
        self.record_buy()
        one=self.review()
        restarted=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.db))
        two=self.review(bot=restarted)
        self.assertFalse(one["preflight_review_replay"])
        self.assertTrue(two["preflight_review_replay"])
        self.assertFalse(two["sell_reservation_created"])
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM m08_offline_sell_preflight_receipts"
            ).fetchone()[0],1)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM m07_offline_order_intents"
            ).fetchone()[0],0)

    def test_same_sell_client_modified_qty_or_scenario_conflicts(self):
        self.record_buy()
        self.review()
        for changed in (
            {"draft":sell(qty="3")},
            {"fee":"700"},
            {"mark":"27000"},
            {"observed":FIRST+1800},
        ):
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(DecisionConflictError,
                     "M08_SELL_PREFLIGHT_REPLAY_OR_DUPLICATE_CONFLICT"):
                    self.review(**changed)

    def test_same_client_other_account_cannot_rebind(self):
        self.record_buy()
        self.review()
        with self.assertRaisesRegex(DecisionConflictError,
                     "M08_SELL_PREFLIGHT_REPLAY_OR_DUPLICATE_CONFLICT"):
            self.review(account=ACCOUNT_B)

    def test_exchange_changed_after_read_before_atomic_write_is_refused(self):
        self.record_buy()
        journal=self.bot.journal
        original=journal.record_m08_sell_preflight_denied_once
        def interpose(**kwargs):
            self.record_sell_status(status="NEW")
            return original(**kwargs)
        with patch.object(journal,"record_m08_sell_preflight_denied_once",
                          side_effect=interpose):
            with self.assertRaisesRegex(
                    DecisionConflictError,
                    "M08_SELL_PREFLIGHT_EXCHANGE_STATE_CHANGED"):
                self.review()
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m08_offline_sell_preflight_receipts"
            ).fetchone()[0],0)

    def test_missing_uid_ledger_or_invalid_draft_fails_closed(self):
        self.record_buy()
        with self.assertRaisesRegex(ValueError,"M08_SELL_PREFLIGHT_REVIEW_INPUT_INVALID"):
            self.review(draft=M07OrderDraft(
                symbol=SYMBOL,side="BUY",order_type="LIMIT",
                client_order_id="m08buydraft",price=D("25000"),quantity=D("4")))
        self.m06.unlink()
        with self.assertRaises(Exception):
            self.review()

    def test_concurrent_idempotent_review_only_one_receipt(self):
        self.record_buy()
        restarted=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.db))
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:self.review(bot=restarted),range(8)))
        self.assertEqual(sum(r["preflight_review_replay"] is False
                             for r in results),1)
        self.assertEqual(sum(r["preflight_review_replay"] is True
                             for r in results),7)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m08_offline_sell_preflight_receipts"
            ).fetchone()[0],1)
        self.assertTrue(all(not r["order_sent"] for r in results))


if __name__=="__main__":
    unittest.main()
