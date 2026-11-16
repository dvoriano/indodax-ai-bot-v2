"""M09-04: same ORIGINAL V2 journal/CLI, isolated PAPER vs real IDR.

Never calls Indodax/private GET, paid AI, signed BUY/SELL/CANCEL, M05
owner PASS/REJECT or M08 real-settled event producer. Hypothetical prices
and fees are explicitly PAPER-only, including net PnL and WIB grouping.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout,redirect_stderr
from datetime import datetime,timezone
from decimal import Decimal as D
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal,DecisionConflictError
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


BUY_EPOCH=1793694000
SELL_EPOCH=BUY_EPOCH+1800
PID="paper_m09_001"


class OriginalM09VirtualPaper(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db=Path(tmp.name)/"original-v2.sqlite3"
        self.journal=DecisionJournal(self.db)
        self.app=DailyScalpingOrchestrator(Settings(),self.journal)

    def buy(self,*,app=None,**changes):
        args={
            "paper_id":PID,"symbol":"XRPIDR",
            "buy_qty":D("4"),"buy_price_idr":D("25000"),
            "buy_all_in_fee_idr":D("200"),
            "buy_epoch":BUY_EPOCH,
        }
        args.update(changes)
        return (app or self.app).run_m09_original_paper_cycle_offline_once(
            action="BUY",details=args)

    def sell(self,*,app=None,**changes):
        args={
            "paper_id":PID,"paper_exit_id":"pexit_m09_001",
            "sell_qty":D("4"),"sell_price_idr":D("28000"),
            "sell_all_in_fee_idr":D("500"),
            "incremental_slippage_idr":D("100"),
            "sell_epoch":SELL_EPOCH,
        }
        args.update(changes)
        return (app or self.app).run_m09_original_paper_cycle_offline_once(
            action="SELL",details=args)

    def summary(self,day=None,app=None):
        return (app or self.app).run_m09_original_paper_cycle_offline_once(
            action="SUMMARY",details={"day_wib":day},
        )

    def check_isolation(self,r):
        self.assertFalse(r["execution_allowed"])
        self.assertFalse(r["order_sent"])
        self.assertFalse(r["paper_can_be_spendable_capital"])
        self.assertFalse(r["paper_may_publish_position_settled_idr"])
        self.assertIsNone(r["owner_order_final"])
        self.assertEqual(r["m08_original_settlement_source"],
                         "M08_NO_AUTHENTICATED_BOT_OWNED_SETTLEMENT_ISSUER")
        paper=r["paper"]
        self.assertEqual(paper["source"],"M09_PAPER_SCENARIO_ONLY")
        self.assertFalse(paper["paper_is_exchange_confirmed_fill"])
        self.assertFalse(paper["paper_is_bot_owned_real_lot"])
        self.assertIsNone(paper["real_exchange_wallet_idr_delta"])
        self.assertIsNone(paper["real_exchange_realized_pnl_idr"])
        self.assertFalse(paper["spendable_idr_authorized"])
        self.assertFalse(paper["authenticated_exchange_settlement_verified"])
        self.assertFalse(paper["m08_position_settled_idr_created"])

    def test_one_full_buy_sell_pnl_net_fees_and_slippage_once(self):
        buy=self.buy()
        self.assertEqual(buy["paper"]["paper_buy_cost_idr"],"100200")
        sell=self.sell()
        self.assertEqual(sell["paper"]["paper_exit_net_pnl_idr"],"11200")
        self.assertEqual(sell["paper"]["paper_remaining_qty"],"0")
        self.assertEqual(sell["paper"]["status"],"M09_PAPER_CLOSED")
        summary=self.summary()
        self.assertEqual(summary["paper"]["paper_net_pnl_idr_reference"],"11200")
        self.assertEqual(summary["paper"]["paper_fully_closed_positions"],1)
        self.assertEqual(summary["paper"]["paper_positive_exit_count"],1)
        for r in (buy,sell,summary):
            self.check_isolation(r)

    def test_no_second_paper_buy_until_final_exit_across_restart(self):
        self.buy()
        self.sell(sell_qty=D("1"))
        restarted=DailyScalpingOrchestrator(Settings(),DecisionJournal(self.db))
        with self.assertRaisesRegex(DecisionConflictError,
                                    "M09_PAPER_POSITION_ALREADY_OPEN"):
            self.buy(app=restarted,paper_id="paper_next",symbol="ETHIDR",
                     buy_epoch=SELL_EPOCH+1)
        final=self.sell(app=restarted,paper_exit_id="pexit_final",
                        sell_qty=D("3"),sell_epoch=SELL_EPOCH+2)
        self.assertEqual(final["paper"]["paper_remaining_qty"],"0")
        with self.assertRaisesRegex(DecisionConflictError,
                                    "M09_PAPER_BUY_PRECEDES_LAST_EXIT"):
            self.buy(app=restarted,paper_id="paper_next",symbol="ETHIDR",
                     buy_epoch=SELL_EPOCH+1)
        new=self.buy(app=restarted,paper_id="paper_next",symbol="ETHIDR",
                     buy_epoch=SELL_EPOCH+3)
        self.assertEqual(new["paper"]["status"],"M09_SIMULATED_BUY_OPEN")
        self.assertTrue(self.buy(app=restarted)["paper"]["replay"])
        self.assertEqual(self.summary()["paper"]["paper_open_or_partial_positions"],1)
        self.check_isolation(new)

    def test_competing_paper_buys_accept_only_one(self):
        def one(i):
            try:
                return self.buy(paper_id="paper_concurrent_"+str(i))
            except DecisionConflictError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=5) as pool:
            results=list(pool.map(one,range(5)))
        accepted=[r for r in results if isinstance(r,dict)]
        refused=[r for r in results if isinstance(r,str)]
        self.assertEqual(len(accepted),1)
        self.assertEqual(refused,["M09_PAPER_POSITION_ALREADY_OPEN"]*4)
        self.assertEqual(self.summary()["paper"]["paper_open_or_partial_positions"],1)
        self.check_isolation(accepted[0])

    def test_paper_review_connects_original_position_policy_after_partial(self):
        self.buy()
        self.sell(sell_qty=D("1"))
        restarted=DailyScalpingOrchestrator(Settings(),DecisionJournal(self.db))
        values=dict(paper_id=PID,observed_epoch=SELL_EPOCH+1,
                    book_observed_epoch=SELL_EPOCH+1,tier_sell_qty=D("3"),
                    bid_levels=((D("29900"),D("3")),),best_ask=D("30000"),
                    taker_sell_fee_pct=D("0.3"),maker_sell_fee_pct=D("0.2"),
                    taker_slippage_pct=D("0.1"))
        def review(**changes):
            return restarted.run_m09_original_paper_cycle_offline_once(
                action="REVIEW",details={**values,**changes})
        outcome=review()
        self.assertEqual(outcome["paper"]["paper_remaining_qty"],"3")
        self.assertEqual(D(outcome["paper"]["paper_remaining_cost_idr"]),D("75150"))
        self.assertEqual(outcome["paper"]["paper_holding_phase"],"EXIT_PRIORITY_REVIEW")
        self.assertEqual(outcome["paper"]["paper_strategy_a_reference"]["status"],
                         "T3_TAKER_PREVIEW_ONLY")
        self.assertFalse(outcome["paper"]["paper_sell_fill_recorded"])
        self.check_isolation(outcome)
        stop=review(bid_levels=((D("23000"),D("3")),),best_ask=D("23100"))
        self.assertEqual(stop["paper"]["paper_strategy_a_reference"]["status"],
                         "STOP_TAKER_PREVIEW_ONLY")
        self.check_isolation(stop)
        stale=review(book_observed_epoch=SELL_EPOCH-40)
        self.assertEqual(stale["paper"]["paper_strategy_a_reference"]["status"],
                         "STALE_BOOK_RISK_ALERT_NO_ORDER")
        self.assertEqual(self.summary()["paper"]["paper_exit_count"],1)
        self.sell(paper_exit_id="pexit_finish",sell_qty=D("3"),
                  sell_epoch=SELL_EPOCH+1)
        with self.assertRaisesRegex(DecisionConflictError,"M09_PAPER_POSITION_CLOSED"):
            review()

    def test_observed_market_mark_uses_original_book_without_sell_or_mutation(self):
        self.buy()
        source=self.db.parent/"original-m03.sqlite3"
        now=BUY_EPOCH+121
        minute=now-now%60
        with sqlite3.connect(source) as con:
            con.execute("""CREATE TABLE m03_ws_microstructure_watch_minute (
              pair,minute_epoch,observed_local_epoch,trade_frames,
              source_buy_count,source_sell_count,source_buy_idr,source_sell_idr,
              last_trade_epoch,trade_offset,trade_gap_observed,trade_truncated,
              book_offset,book_local_epoch,best_bid_idr,best_ask_idr,
              spread_bps,bid_depth_top5_idr,ask_depth_top5_idr,
              book_imbalance_top5_ratio,source_malformed)""")
            spread=str(D("100")/D("30050")*10000)
            con.execute("INSERT INTO m03_ws_microstructure_watch_minute VALUES ("+
                        ",".join(["?"]*21)+")",
                        ("xrp_idr",minute,now,0,None,None,None,None,None,None,
                         0,0,12,now,"30000","30100",spread,"5000000",
                         "6000000",None,0))
        source.chmod(0o600)
        original=source.read_bytes()
        args=dict(paper_id=PID,sell_fee_pct=D("0.3"),slippage_pct=D("0.1"),
                  source_db=source,now_epoch=now)
        result=self.app.run_m09_original_paper_cycle_offline_once(
            action="OBSERVE",details=args)
        self.assertEqual(result["paper"]["paper_exit_reference"],
                         "T3_NET_MARK_REVIEW_NO_FILL")
        self.assertEqual(D(result["paper"]["paper_net_mark_profit_idr"]),D("19320"))
        self.assertFalse(result["paper"]["paper_sell_fill_recorded"])
        self.assertFalse(result["paper"]["paper_fill_depth_verified"])
        self.check_isolation(result)
        stale=self.app.run_m09_original_paper_cycle_offline_once(
            action="OBSERVE",details={**args,"now_epoch":now+31})
        self.assertEqual(stale["paper"]["status"],
                         "M09_PAPER_WAIT_FOR_FRESH_OBSERVED_BOOK")
        self.assertIsNone(stale["paper"]["paper_net_mark_profit_idr"])
        self.assertEqual(original,source.read_bytes())
        self.assertEqual(self.summary()["paper"]["paper_exit_count"],0)

    def test_discover_reuses_original_radar_without_paper_fill(self):
        now=BUY_EPOCH+121
        candidate={"pair":"xrp_idr","observed_epoch":now,
                   "volume_24h_idr":"500000000","observed_rise_pct":"0.35",
                   "microstructure_observation":{
                       "orderbook_snapshot_observed":True,
                       "trade_activity_observed":True,"source_malformed":False,
                       "trade_source_gap_or_truncation":False,
                       "orderbook_locally_observed_epoch":now,
                       "best_bid_idr":"25000","best_ask_idr":"25100"}}
        source=self.db.parent/"original-m03.sqlite3"
        with patch("indodax_ai_bot.m05_radar_momentum_nonlive."
                   "inspect_original_m03_ws3_watch_readonly",
                   return_value={"micro_prealert_candidates_top40":[candidate]}) as read:
            result=self.app.run_m09_original_paper_cycle_offline_once(
                action="DISCOVER",details={"source_db":source,"now_epoch":now})
            self.assertEqual(result["paper"]["status"],
                             "M09_PAPER_MAKER_ENTRY_REFERENCE_NO_FILL")
            self.assertFalse(result["paper"]["paper_buy_fill_recorded"])
            self.check_isolation(result)
            self.buy()
            blocked=self.app.run_m09_original_paper_cycle_offline_once(
                action="DISCOVER",details={"source_db":source,"now_epoch":now})
            self.assertEqual(blocked["paper"]["status"],
                             "M09_PAPER_MONITOR_EXISTING_POSITION")
            self.assertEqual(read.call_count,1)

    def test_loss_accepted_on_paper_protection_does_not_stop_or_buy(self):
        self.buy()
        loss=self.sell(sell_price_idr=D("24000"),
                       sell_all_in_fee_idr=D("150"),
                       incremental_slippage_idr=D("50"))
        self.assertEqual(loss["paper"]["paper_exit_net_pnl_idr"],"-4400")
        self.assertEqual(self.summary()["paper"]["paper_negative_exit_count"],1)
        self.check_isolation(loss)

    def test_partial_sells_prorate_buy_fees_once_and_finish_closed(self):
        self.buy()
        one=self.sell(sell_qty=D("1"),sell_price_idr=D("26000"),
                      sell_all_in_fee_idr=D("40"),
                      incremental_slippage_idr=D("10"))
        self.assertEqual(one["paper"]["paper_exit_net_pnl_idr"],"900")
        self.assertEqual(one["paper"]["paper_remaining_qty"],"3")
        two=self.sell(paper_exit_id="pexit_m09_002",sell_qty=D("3"),
                      sell_price_idr=D("24000"),
                      sell_all_in_fee_idr=D("120"),
                      incremental_slippage_idr=D("30"),sell_epoch=SELL_EPOCH+10)
        self.assertEqual(two["paper"]["paper_exit_net_pnl_idr"],"-3300")
        self.assertEqual(two["paper"]["paper_position_total_net_pnl_idr"],"-2400")
        self.assertEqual(two["paper"]["status"],"M09_PAPER_CLOSED")
        self.assertEqual(self.summary()["paper"]["paper_exit_count"],2)
        self.check_isolation(two)

    def test_repeat_exact_buy_and_sell_are_atomic_replays_after_restart(self):
        self.buy()
        first=self.sell()
        restart=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.db))
        rb=self.buy(app=restart)
        rs=self.sell(app=restart)
        self.assertTrue(rb["paper"]["replay"])
        self.assertTrue(rs["paper"]["replay"])
        self.assertEqual(first["paper"]["paper_exit_net_pnl_idr"],
                         rs["paper"]["paper_exit_net_pnl_idr"])
        self.assertEqual(self.summary(app=restart)["paper"]["paper_exit_count"],1)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM m09_paper_simulated_buys").fetchone()[0],1)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM m09_paper_simulated_sells").fetchone()[0],1)

    def test_changed_buy_or_sell_replay_cannot_overwrite_receipt(self):
        self.buy()
        self.sell()
        for fn,args in (
            (self.buy,{"buy_qty":D("5")}),
            (self.buy,{"symbol":"ETHIDR"}),
            (self.sell,{"sell_price_idr":D("27000")}),
            (self.sell,{"sell_all_in_fee_idr":D("200")}),
            (self.sell,{"paper_id":"paper_foreign"}),
        ):
            with self.subTest(args=args),self.assertRaises(DecisionConflictError):
                fn(**args)
        self.assertEqual(self.summary()["paper"]["paper_exit_count"],1)

    def test_oversell_after_partial_is_blocked_across_restart(self):
        self.buy()
        self.sell(sell_qty=D("3"))
        restart=DailyScalpingOrchestrator(
            Settings(),DecisionJournal(self.db))
        with self.assertRaisesRegex(DecisionConflictError,
                                    "M09_PAPER_OVERSELL_OR_TIME_CONFLICT"):
            self.sell(app=restart,paper_exit_id="pexit_m09_002",
                      sell_qty=D("2"),sell_epoch=SELL_EPOCH+1)
        self.assertEqual(self.summary()["paper"]["paper_exit_count"],1)

    def test_concurrent_partial_requests_cannot_sell_more_than_paper_buy(self):
        self.buy()
        def one(i):
            try:
                return self.sell(paper_exit_id="pexit_concurrent_"+str(i),
                                 sell_qty=D("1"),sell_epoch=SELL_EPOCH)
            except DecisionConflictError as e:
                return str(e)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(one,range(8)))
        allowed=[r for r in results if isinstance(r,dict)]
        refused=[r for r in results if isinstance(r,str)]
        self.assertEqual(len(allowed),4)
        self.assertEqual(len(refused),4)
        self.assertTrue(all("M09_PAPER_OVERSELL" in r for r in refused))
        self.assertEqual(self.summary()["paper"]["paper_fully_closed_positions"],1)

    def test_wib_day_cutoff_and_reset_is_not_position_reset(self):
        self.buy()
        before=int(datetime(2026,11,3,16,59,30,
                            tzinfo=timezone.utc).timestamp())
        self.sell(sell_qty=D("1"),paper_exit_id="pexit_wib_first",
                  sell_epoch=before)
        next_day=self.sell(sell_qty=D("3"),paper_exit_id="pexit_wib_next",
                           sell_epoch=before+120)
        nov3=self.summary("2026-11-03")
        nov4=self.summary("2026-11-04")
        self.assertEqual(nov3["paper"]["paper_exit_count"],1)
        self.assertEqual(nov4["paper"]["paper_exit_count"],1)
        self.assertEqual(nov4["paper"]["paper_fully_closed_positions"],1)
        self.assertEqual(self.summary()["paper"]["paper_exit_count"],2)
        self.check_isolation(next_day)

    def test_missing_buy_invalid_identity_fee_nan_slippage_and_time_fail(self):
        with self.assertRaisesRegex(DecisionConflictError,
                                    "M09_PAPER_BUY_RECEIPT_MISSING"):
            self.sell()
        for changes in (
            {"paper_id":"real_order_123"},
            {"buy_qty":D("NaN")},
            {"buy_qty":D("0")},
            {"buy_all_in_fee_idr":D("-1")},
            {"symbol":"FLOKIIDR"},
            {"buy_epoch":0},
        ):
            with self.subTest(changes=changes),self.assertRaises(DecisionConflictError):
                self.buy(**changes)
        self.buy()
        for changes in (
            {"sell_qty":D("0")},
            {"sell_qty":D("5")},
            {"sell_price_idr":D("NaN")},
            {"sell_all_in_fee_idr":D("-1")},
            {"incremental_slippage_idr":D("Infinity")},
            {"sell_price_idr":D("1"),"sell_all_in_fee_idr":D("10")},
            {"sell_epoch":BUY_EPOCH-1},
        ):
            with self.subTest(changes=changes),self.assertRaises(DecisionConflictError):
                self.sell(**changes)
        self.assertEqual(self.summary()["paper"]["paper_exit_count"],0)

    def test_invalid_wib_day_and_corrupted_receipt_fail_closed(self):
        for date in ("2026-02-30","2026-13-01","bad",0):
            with self.subTest(date=date),self.assertRaises(DecisionConflictError):
                self.summary(date)
        self.buy()
        self.sell()
        with sqlite3.connect(self.db) as con:
            con.execute(
                "UPDATE m09_paper_simulated_sells SET sell_qty='9999' "
                "WHERE paper_exit_id='pexit_m09_001'"
            )
        with self.assertRaisesRegex(DecisionConflictError,
                                    "M09_PAPER_RECEIPT_OVERSELL_CORRUPT"):
            self.summary()

    def test_paper_does_not_change_any_real_order_position_or_settlement_tables(self):
        with sqlite3.connect(self.db) as con:
            baseline={
                table:con.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]
                for table in (
                    "m07_offline_order_intents",
                    "m07_exchange_readonly_orders",
                    "m07_exchange_readonly_fill_summaries",
                    "m08_position_settled_idr_events",
                    "m08_post_sell_discovery_claims",
                    "m08_offline_sell_preflight_receipts",
                )
            }
        self.buy()
        self.sell()
        self.summary()
        with sqlite3.connect(self.db) as con:
            for table,n in baseline.items():
                with self.subTest(table=table):
                    self.assertEqual(con.execute(
                        "SELECT COUNT(*) FROM "+table).fetchone()[0],n)
        self.assertFalse(self.app.journal.review_m09_original_m08_position_and_idr_readonly()[
            "wallet_settled_idr_verified"])

    def test_original_cli_buy_sell_summary_offline_owner_only_file(self):
        from indodax_ai_bot.__main__ import main
        path=self.db.parent/"paper-input.json"
        def cli(data):
            path.write_text(json.dumps(data))
            path.chmod(0o600)
            out=StringIO()
            with redirect_stdout(out):
                code=main([
                    "--m09-paper-offline-once",
                    "--m09-paper-file",str(path),
                    "--db",str(self.db),
                ])
            self.assertEqual(code,0)
            return json.loads(out.getvalue())
        buy=cli({"action":"BUY","paper_id":PID,"symbol":"XRPIDR",
                 "buy_qty":"4","buy_price_idr":"25000",
                 "buy_all_in_fee_idr":"200","buy_epoch":BUY_EPOCH})
        sell=cli({"action":"SELL","paper_id":PID,
                  "paper_exit_id":"pexit_cli_001",
                  "sell_qty":"4","sell_price_idr":"28000",
                  "sell_all_in_fee_idr":"500",
                  "incremental_slippage_idr":"100",
                  "sell_epoch":SELL_EPOCH})
        summary=cli({"action":"SUMMARY","day_wib":None})
        self.assertEqual(sell["m09_paper"]["paper"]["paper_exit_net_pnl_idr"],
                         "11200")
        self.assertEqual(summary["m09_paper"]["paper"][
            "paper_net_pnl_idr_reference"],"11200")
        for reply in (buy,sell,summary):
            self.assertIsNone(reply["final_decision"])
            self.assertFalse(reply["execution_allowed"])
            self.assertFalse(reply["live_trading_authorized"])
            self.assertFalse(reply["m09_paper"]["order_sent"])

    def test_original_cli_rejects_extra_api_secret_without_leaking(self):
        from indodax_ai_bot.__main__ import main
        path=self.db.parent/"paper-secret.json"
        path.write_text(json.dumps({
            "action":"BUY","paper_id":PID,"symbol":"XRPIDR",
            "buy_qty":"4","buy_price_idr":"25000",
            "buy_all_in_fee_idr":"200","buy_epoch":BUY_EPOCH,
            "api_secret":"PRIVATE_MUST_NOT_LEAK",
        }))
        path.chmod(0o600)
        out=StringIO()
        with redirect_stderr(out):
            code=main([
                "--m09-paper-offline-once","--m09-paper-file",str(path),
                "--db",str(self.db),
            ])
        self.assertEqual(code,3)
        self.assertNotIn("PRIVATE_MUST_NOT_LEAK",out.getvalue())
        self.assertFalse(json.loads(out.getvalue())["execution_allowed"])

    def test_original_cli_denies_live_mode_and_non_tmp_journal(self):
        from indodax_ai_bot.__main__ import main
        with redirect_stderr(StringIO()):
            self.assertEqual(main([
                "--m09-paper-offline-once",
                "--m09-paper-file","runtime/paper.json",
                "--db",str(self.db),
            ]),2)
        with patch("indodax_ai_bot.__main__.Settings.from_env",
                   return_value=Settings(
                       trading_enabled=True,allow_live_execution=True,
                       execution_mode="LIVE")):
            with redirect_stderr(StringIO()):
                self.assertEqual(main([
                    "--m09-paper-offline-once",
                    "--m09-paper-file",str(self.db.parent/"input.json"),
                    "--db",str(self.db),
                ]),2)

    def test_no_private_get_provider_owner_or_signed_order_transport(self):
        with (
            patch("indodax_ai_bot.m07_private_order_readonly._https_json",
                  side_effect=AssertionError("PRIVATE_DENIED")) as private,
            patch("indodax_ai_bot.m07_order_adapter.OfficialV2OrderAdapter.create_order",
                  side_effect=AssertionError("ORDER_DENIED")) as order,
            patch.object(self.app,"run_bulk_deep_cycle",
                         side_effect=AssertionError("PUBLIC_SCAN_DENIED")) as scan,
        ):
            self.buy()
            self.sell()
        private.assert_not_called()
        order.assert_not_called()
        scan.assert_not_called()


if __name__=="__main__":
    unittest.main()
