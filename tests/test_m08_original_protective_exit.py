"""M08 protective SELL stop-loss / trailing / time-stop on original V2.

All positions are account-only mock evidence. No bot-owned BUY exists yet,
no trade request, no SELL reservation, no live exchange or provider calls.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m07_order_adapter import M07OrderDraft
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.policy import ScalpingPolicy
from tests.test_m08_original_exchange_buy_admission import (
    ACCOUNT_A, CID, SYMBOL, NOW, order, fills,
)

ENTRY = NOW - 300


class OriginalM08ProtectiveExitTests(unittest.TestCase):
    def setUp(self):
        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.db = Path(root.name) / "original-v2-journal.sqlite3"
        self.bot = DailyScalpingOrchestrator(Settings(),DecisionJournal(self.db))

    def record_account_buy(self):
        j=self.bot.journal
        j.record_m07_authenticated_readonly_order_status(
            account_fingerprint=ACCOUNT_A,order=order(),
            observed_ms=NOW*1000,
        )
        j.record_m07_exchange_readonly_fills(
            account_fingerprint=ACCOUNT_A,fills=fills(),
            observed_ms=NOW*1000+1,
        )

    def review(self, *,mark="24000",stop="24500",high="26000",
               trailing=None, age=300, fresh=True, pump=False,
               risk=False, bot=None):
        return (bot or self.bot).review_m08_protective_exit_reference_offline(
            account_fingerprint=ACCOUNT_A,symbol=SYMBOL,client_order_id=CID,
            reference_buy_fill_epoch=ENTRY,observed_epoch=ENTRY+age,
            observed_mark_price_idr=D(mark),stop_loss_price_idr=D(stop),
            high_watermark_price_idr=D(high),
            trailing_stop_price_idr=D(trailing) if trailing is not None else None,
            quote_fresh_unverified=fresh,
            pump_continuation_unverified=pump,
            risk_clear_unverified=risk,
        )

    def test_without_exchange_buy_cost_source_no_protective_review(self):
        x=self.review()
        self.assertEqual(x["status"],
                         "M08_PROTECTIVE_MISSING_ACCOUNT_BUY_FILLED_COST_SOURCE")
        self.assertIsNone(x["protection_review"])
        self.assertFalse(x["bot_owned_position_verified"])
        self.assertEqual(x["protective_sell_quantity_authorized"],"0")
        self.assertFalse(x["execution_allowed"])

    def test_stop_loss_allows_negative_reference_below_buy_profit_floor(self):
        self.record_account_buy()
        x=self.review(mark="24000",stop="24500",high="26000")
        self.assertEqual(x["protective_trigger_reference"],"STOP_LOSS_REVIEW")
        self.assertTrue(x["stop_loss_breached_reference"])
        self.assertEqual(x["gross_unrealized_idr_reference"],"-4200")
        self.assertLess(D(x["gross_unrealized_roi_percent_reference"]),0)
        self.assertTrue(x["protective_loss_allowed_by_policy"])
        self.assertEqual(x["protective_sell_quantity_authorized"],"0")
        self.assertFalse(x["bot_owned_position_verified"])
        self.assertFalse(x["execution_allowed"])
        self.assertFalse(x["order_sent"])

    def test_trailing_stop_exit_review_even_if_stop_loss_not_hit(self):
        self.record_account_buy()
        x=self.review(mark="25200",stop="22500",
                      high="27000",trailing="25500")
        self.assertFalse(x["stop_loss_breached_reference"])
        self.assertTrue(x["trailing_stop_breached_reference"])
        self.assertEqual(x["protective_trigger_reference"],
                         "TRAILING_STOP_REVIEW")
        self.assertFalse(x["execution_allowed"])

    def test_30_to_60_minute_is_reassessment_not_forced_sell(self):
        self.record_account_buy()
        x=self.review(mark="25400",stop="22500",high="27000",
                      trailing="25000",age=2400)
        self.assertEqual(x["protective_trigger_reference"],
                         "MONITOR_POSITION_REFERENCE_ONLY")
        self.assertEqual(x["review_urgency"],"ELEVATED")
        self.assertFalse(x["time_stop_review_reference"])

    def test_60_minute_time_stop_is_urgent_review_not_market_order(self):
        self.record_account_buy()
        x=self.review(mark="25400",stop="22500",high="27000",
                      trailing="25000",age=3600)
        self.assertEqual(x["protective_trigger_reference"],
                         "TIME_STOP_URGENT_REVIEW")
        self.assertTrue(x["time_stop_review_reference"])
        self.assertEqual(x["review_urgency"],"URGENT")
        self.assertFalse(x["execution_allowed"])

    def test_proven_pump_scenario_only_defers_time_review_under_90_min(self):
        self.record_account_buy()
        x=self.review(mark="25400",stop="22500",high="27000",
                      trailing="25000",age=4200,pump=True,risk=True)
        self.assertEqual(x["protective_trigger_reference"],
                         "MONITOR_POSITION_REFERENCE_ONLY")
        self.assertFalse(x["time_stop_review_reference"])
        extended=self.review(mark="25400",stop="22500",high="27000",
                      trailing="25000",age=5400,pump=True,risk=True)
        self.assertTrue(extended["time_stop_review_reference"])
        self.assertEqual(extended["review_urgency"],"URGENT")
        self.assertEqual(extended["protective_trigger_reference"],
                         "TIME_STOP_URGENT_REVIEW")
        self.assertFalse(extended["bot_owned_position_verified"])

    def test_stale_quote_never_becomes_trusted_sell_price_trigger(self):
        self.record_account_buy()
        x=self.review(mark="24000",stop="24500",high="26000",
                      age=6000,fresh=False)
        self.assertEqual(x["protective_trigger_reference"],
                         "STALE_QUOTE_PROTECTION_NEEDS_FRESH_SOURCE")
        self.assertTrue(x["stop_loss_breached_reference"])
        self.assertFalse(x["source_quote_fresh_unverified"])
        self.assertTrue(x["time_stop_review_reference"])
        self.assertFalse(x["execution_allowed"])

    def test_nonfinite_invalid_floor_or_clock_rejected(self):
        self.record_account_buy()
        cases=(
            {"mark":"NaN"},{"mark":"Infinity"},{"mark":"-1"},
            {"stop":"30000","high":"26000"},
            {"trailing":"29000","high":"26000"},
            {"age":-1},{"fresh":1},
        )
        for opts in cases:
            with self.subTest(opts=opts),self.assertRaises(ValueError):
                self.review(**opts)

    def test_unknown_journal_hold_survives_protective_review(self):
        self.record_account_buy()
        draft=M07OrderDraft(
            symbol="ETHIDR",side="BUY",order_type="LIMIT",
            client_order_id="m08unknownhold001",
            price=D("2000"),quantity=D("2"),
        )
        self.bot.reserve_m07_offline_intent_once(
            intent_id="m08unknownhold001",draft=draft,observed_epoch=NOW,
        )
        self.bot.simulate_m07_unknown_timeout_once(
            intent_id="m08unknownhold001",observed_epoch=NOW+1,
        )
        result=self.review()
        self.assertEqual(result["m07_unknown_holds_still_blocked"],1)
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(
            self.bot.inspect_m07_offline_intent_guard_once()["offline_unknown_holds"],1)

    def test_restart_and_parallel_reviews_never_register_sell_position(self):
        self.record_account_buy()
        restarted=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.db))
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows=list(pool.map(lambda _: self.review(bot=restarted),range(8)))
        self.assertEqual(len(rows),8)
        self.assertTrue(all(
            r["protective_trigger_reference"]=="STOP_LOSS_REVIEW"
            and r["protective_sell_quantity_authorized"]=="0"
            and not r["execution_allowed"] for r in rows
        ))
        self.assertEqual(
            restarted.inspect_m07_offline_intent_guard_once()[
                "unresolved_simulated_intents"],0)

    def test_policy_standalone_is_diagnostic_not_actual_sell(self):
        p=ScalpingPolicy()
        x=p.assess_protective_sell_reference_offline(
            account_buy_base_qty=D("4"),
            account_buy_cost_basis_idr=D("100200"),
            reference_buy_fill_epoch=ENTRY,now_epoch=ENTRY+100,
            observed_mark_price_idr=D("24000"),
            stop_loss_price_idr=D("24500"),
            high_watermark_price_idr=D("26000"),
            trailing_stop_price_idr=None,
            quote_fresh_unverified=True,
        )
        self.assertEqual(x.trigger,"STOP_LOSS_REVIEW")
        self.assertFalse(x.bot_owned_position_verified)
        self.assertEqual(x.sell_quantity_authorized,D("0"))
        self.assertFalse(x.execution_allowed)


if __name__=="__main__":
    unittest.main()
