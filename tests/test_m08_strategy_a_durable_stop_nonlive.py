"""Strategy A persistent STOP shadow: original V2 journal, no live orders.

All observations are synthetic and REVIEW-ONLY. Passing these tests cannot
prove owner-specific exchange inventory, execution, fee accuracy or settlement.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionConflictError, DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.m08_strategy_a_nonlive import assess_exit_strategy_a_offline


ACCOUNT_A = "a" * 64
ACCOUNT_B = "b" * 64
SYMBOL = "XRPIDR"
CID = "strategyAownerstop001"
NOW = 1794471000


def inputs(*, price="950", ask="960", depth="100", stale=False,
           order="NONE", cost="100000"):
    return {
        "remaining_bot_qty_reference": D("100"),
        "remaining_cost_including_buy_fee_idr": D(cost),
        "tier_sell_qty_reference": D("100"),
        "bid_levels": ((D(price), D(depth)),),
        "best_ask": D(ask),
        "taker_sell_fee_pct": D("0"),
        "maker_sell_fee_pct": D("0"),
        "taker_slippage_pct": D("0"),
        "book_age_seconds": 99 if stale else 1,
        "open_sell_state": order,
    }


class OriginalStrategyADurableStopShadow(unittest.TestCase):
    def setUp(self):
        t = TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.path = Path(t.name) / "original-decisions.sqlite3"
        self.bot = DailyScalpingOrchestrator(
            Settings(), DecisionJournal(self.path))
        self.scope = dict(account_fingerprint=ACCOUNT_A, symbol=SYMBOL,
                          source_buy_client_order_id=CID)

    def assess(self, *, bot=None, scope=None, epoch=NOW, **values):
        return (bot or self.bot).review_m08_strategy_a_persisted_stop_shadow_offline_once(
            **(scope if scope is not None else self.scope),
            observed_epoch=epoch, exit_inputs=inputs(**values))

    def state(self, scope=None):
        return self.bot.journal.read_m08_strategy_a_stop_shadow_nonlive(
            **(scope if scope is not None else self.scope))

    def test_no_latch_above_minus_five_and_loss_warning_only(self):
        out = self.assess(price="950.01", ask="960")
        self.assertFalse(out["offline_stop_journal_latched"])
        self.assertEqual(out["status"], "LOSS_WARNING_HOLD")
        self.assertFalse(self.state()["latched"])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m08_strategy_a_stop_shadow"
            ).fetchone()[0], 0)

    def test_minus_five_stays_latched_after_restart_and_price_rebound(self):
        first = self.assess()
        self.assertEqual(first["status"], "STOP_TAKER_PREVIEW_ONLY")
        self.assertTrue(first["offline_stop_journal_latched"])
        self.assertFalse(first["execution_allowed"])
        restart = DailyScalpingOrchestrator(
            Settings(), DecisionJournal(self.path))
        after = self.assess(bot=restart, epoch=NOW + 60,
                            price="1100", ask="1110")
        self.assertEqual(after["status"], "STOP_TAKER_PREVIEW_ONLY")
        self.assertTrue(after["offline_stop_journal_latched"])
        self.assertEqual(after["offline_stop_journal_first_observed_epoch"],
                         NOW)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m08_strategy_a_stop_shadow"
            ).fetchone()[0], 1)

    def test_gap_minus_seven_latches_without_waiting_for_exact_price(self):
        result = self.assess(price="930", ask="940")
        self.assertTrue(result["offline_stop_journal_latched"])
        self.assertLessEqual(D(result["projected_full_exit_net_roi_pct"]),
                             D("-7"))

    def test_unknown_and_partial_open_order_require_reconcile_not_new_sell(self):
        for order in ("OPEN", "PARTIAL", "CANCEL_PENDING", "UNKNOWN"):
            with self.subTest(order=order):
                scope = dict(self.scope, source_buy_client_order_id=CID+order)
                result = self.assess(scope=scope,order=order)
                self.assertEqual(result["status"],
                                 "STOP_CANCEL_RECONCILE_FIRST")
                self.assertTrue(result["offline_stop_journal_latched"])
                self.assertFalse(result["order_sent"])
                after = self.assess(scope=scope, epoch=NOW+30,
                                    order=order,price="1080",ask="1090")
                self.assertEqual(after["status"],
                                 "STOP_CANCEL_RECONCILE_FIRST")

    def test_stale_and_insufficient_depth_cannot_create_first_latch(self):
        stale = self.assess(stale=True)
        self.assertFalse(stale["offline_stop_journal_latched"])
        shallow = self.assess(depth="10")
        self.assertFalse(shallow["offline_stop_journal_latched"])
        self.assertEqual(shallow["status"],
                         "INSUFFICIENT_BID_DEPTH_RISK_ALERT_NO_ORDER")
        self.assess()
        later = self.assess(stale=True, epoch=NOW + 10)
        self.assertEqual(later["status"], "STOP_LATCHED_NEEDS_FRESH_BOOK")
        self.assertTrue(later["offline_stop_journal_latched"])

    def test_scope_isolation_account_symbol_and_buy_id(self):
        self.assess()
        other_account = dict(self.scope, account_fingerprint=ACCOUNT_B)
        other_pair = dict(self.scope, symbol="DOGEIDR")
        other_buy = dict(self.scope, source_buy_client_order_id="newbuy002")
        for scope in (other_account, other_pair, other_buy):
            self.assertFalse(self.state(scope)["latched"])
            result = self.assess(scope=scope, price="1100",ask="1110")
            self.assertFalse(result["offline_stop_journal_latched"])
        self.assertTrue(self.state()["latched"])

    def test_cannot_forge_first_stop_from_positive_roi_flag(self):
        review = assess_exit_strategy_a_offline(
            **inputs(price="1100",ask="1110"), stop_latched=True)
        self.assertTrue(review["stop_latched"])
        with self.assertRaisesRegex(
                DecisionConflictError, "FIRST_TRIGGER_MUST_BE_NET_MINUS_FIVE"):
            self.bot.journal.latch_m08_strategy_a_stop_shadow_nonlive_once(
                **self.scope, observed_epoch=NOW, assessed_exit=review)
        self.assertFalse(self.state()["latched"])

    def test_reject_spoofed_stop_flag_and_account_scope(self):
        with self.assertRaisesRegex(ValueError,
                                    "STOP_LATCH_INPUT_INVALID"):
            self.bot.review_m08_strategy_a_persisted_stop_shadow_offline_once(
                **self.scope, observed_epoch=NOW,
                exit_inputs=dict(inputs(), stop_latched=True))
        with self.assertRaises(DecisionConflictError):
            self.assess(scope=dict(self.scope, account_fingerprint="wrong"))
        self.assertFalse(self.state()["latched"])

    def test_atomic_multiple_simultaneous_calls_one_latch_only(self):
        def claim(i):
            return self.assess(epoch=NOW+i)
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(claim, range(12)))
        self.assertEqual(len(rows), 12)
        self.assertTrue(all(r["offline_stop_journal_latched"]
                            for r in rows))
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m08_strategy_a_stop_shadow"
            ).fetchone()[0], 1)

    def test_corrupt_shadow_row_fails_closed_after_restart(self):
        self.assess()
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE m08_strategy_a_stop_shadow "
                "SET trigger_net_roi_pct='1' WHERE symbol=?", (SYMBOL,))
        with self.assertRaises(DecisionConflictError):
            self.state()
        with self.assertRaises(DecisionConflictError):
            self.assess(epoch=NOW+1,price="1000",ask="1010")

    def test_other_order_risk_and_settlement_tables_remain_untouched(self):
        self.assess()
        with sqlite3.connect(self.path) as db:
            checks = (
                ("m07_offline_order_intents", 0),
                ("m07_manual_sender_nonlive", 0),
                ("m08_offline_sell_preflight_receipts", 0),
                ("m08_position_settled_idr_events", 0),
            )
            for table, expected in checks:
                with self.subTest(table=table):
                    self.assertEqual(db.execute(
                        "SELECT COUNT(*) FROM " + table).fetchone()[0],
                        expected)
            self.assertEqual(db.execute(
                "SELECT state,execution_allowed FROM "
                "m11_offline_risk_halt WHERE singleton=1"
            ).fetchone(), ("ENGAGED", 0))


if __name__ == "__main__":
    unittest.main()
