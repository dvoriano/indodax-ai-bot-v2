"""Original V2 M07 LIMIT BUY offline checks, no account/source/trading claims."""
from __future__ import annotations
from dataclasses import asdict
from decimal import Decimal as D
from tempfile import TemporaryDirectory
from pathlib import Path
from io import StringIO
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch
import json
import os
import unittest

from indodax_ai_bot.policy import ScalpingPolicy
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.__main__ import main
from indodax_ai_bot.m07_order_adapter import M07OrderDraft, OfficialV2OrderAdapter, M07Hardlocked
from indodax_ai_bot.m07_limit_preflight import (
    M07PreflightBlocked, assess_m07_limit_buy_preflight_offline,
)
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


RULES = {
    "price_precision": "0.5",
    "quantity_increment": "0.1",
    "trade_min_base_currency": "10000",
    "trade_min_traded_currency": "1",
    "is_market_suspended": 0,
    "is_maintenance": 0,
}


def args(*, symbol="BTCIDR", price="100", qty="100"):
    return dict(
        draft=M07OrderDraft(symbol,"BUY","LIMIT","safe-offline",
                            price=D(price),quantity=D(qty)),
        pair_rules=dict(RULES),
        best_bid_idr=D("99.5"),best_ask_idr=D("100"),
        free_idr=D("20000"),locked_idr=D("5000"),
        floki_rebuy_idr=D("2000"),pepe_rebuy_idr=D("1000"),
        commitments_not_locked_idr=D("1000"),
        buy_all_in_fee_bps=D("20"),sell_all_in_fee_bps=D("40"),
        max_spread_bps=D("75"),
    )


def fixture():
    a=args()
    return dict(
        symbol=a["draft"].symbol,client_order_id=a["draft"].client_order_id,
        limit_price_idr=str(a["draft"].price),
        quantity_base=str(a["draft"].quantity),
        pair_rules=dict(RULES),
        **{k:str(a[k]) for k in (
            "best_bid_idr","best_ask_idr","free_idr","locked_idr",
            "floki_rebuy_idr","pepe_rebuy_idr",
            "commitments_not_locked_idr",
            "buy_all_in_fee_bps","sell_all_in_fee_bps","max_spread_bps",
        )}
    )


class M07OriginalPreflightTests(unittest.TestCase):
    def test_limit_buy_exact_steps_and_all_in_max_cost(self):
        report=assess_m07_limit_buy_preflight_offline(**args())
        self.assertTrue(report.checks_passed_unverified)
        self.assertEqual(report.status,"M07_DRAFT_RULES_MATCH_UNTRUSTED")
        self.assertEqual(report.estimated_buy_max_cost_idr,"10020.00")
        self.assertEqual(report.general_idr_scenario_budget,"16000")
        self.assertIsNotNone(report.required_future_sell_vwap_idr_unverified)
        for attr in ("public_pair_rule_provenance_verified",
                     "actual_account_fee_verified",
                     "wallet_spendable_verified",
                     "market_source_g1_verified",
                     "owner_m05_final_pass_verified","order_sent","execution_allowed"):
            self.assertFalse(getattr(report,attr))

    def test_free_idr_no_fixed_cap_growing_general_money_funds_buy(self):
        data=args()
        data["free_idr"]=D("12000")
        self.assertFalse(
            assess_m07_limit_buy_preflight_offline(**data).checks_passed_unverified
        )
        data["free_idr"]=D("30000")
        self.assertTrue(
            assess_m07_limit_buy_preflight_offline(**data).checks_passed_unverified
        )
        self.assertEqual(assess_m07_limit_buy_preflight_offline(
            **data).general_idr_scenario_budget,"26000")

    def test_reserves_floki_pepe_never_available_for_main_buy(self):
        a=args()
        a["free_idr"]=D("12500")
        out=assess_m07_limit_buy_preflight_offline(**a)
        self.assertEqual(out.general_idr_scenario_budget,"8500")
        self.assertFalse(out.general_idr_scenario_funded)
        a["floki_rebuy_idr"]=D("0")
        a["pepe_rebuy_idr"]=D("0")
        self.assertTrue(
            assess_m07_limit_buy_preflight_offline(**a).general_idr_scenario_funded)
        # No stablecoin-to-IDR baseline or historical amount cap.
        a=args()
        a["free_idr"]=D("10e6")
        self.assertEqual(
            assess_m07_limit_buy_preflight_offline(
                **a).general_idr_scenario_budget,"9996000")

    def test_step_and_increment_and_minimums_fail_closed(self):
        for delta in (
            {"draft":args(price="100.1")["draft"]},
            {"draft":args(qty="100.05")["draft"]},
            {"pair_rules":{**RULES,"trade_min_base_currency":"11000"}},
            {"pair_rules":{**RULES,"trade_min_traded_currency":"101"}},
        ):
            with self.subTest(delta=delta):
                a=args()
                a.update(delta)
                result=assess_m07_limit_buy_preflight_offline(**a)
                self.assertFalse(result.checks_passed_unverified)
                self.assertFalse(result.execution_allowed)

    def test_suspended_market_maintenance_spread_or_cash_prevents_match(self):
        for delta in (
            {"pair_rules":{**RULES,"is_market_suspended":1}},
            {"pair_rules":{**RULES,"is_maintenance":1}},
            {"best_bid_idr":D("95")},
            {"free_idr":D("9000")},
            {"commitments_not_locked_idr":D("15000")},
        ):
            with self.subTest(delta=delta):
                a=args()
                a.update(delta)
                self.assertFalse(
                    assess_m07_limit_buy_preflight_offline(
                        **a).checks_passed_unverified)

    def test_missing_invalid_or_forged_public_rules_and_fees_denied(self):
        samples=(
            {"pair_rules":{"price_precision":"0.5"}},
            {"pair_rules":{**RULES,"quantity_increment":"0"}},
            {"pair_rules":{**RULES,"price_precision":0.5}},
            {"pair_rules":{**RULES,"is_market_suspended":None}},
            {"best_ask_idr":D("99.5")},
            {"buy_all_in_fee_bps":D("-1")},
            {"sell_all_in_fee_bps":D("NaN")},
            {"max_spread_bps":D("900")},
            {"free_idr":D("NaN")},
            {"draft":M07OrderDraft(
                "PEPEIDR","BUY","LIMIT","safe-offline",D("100"),D("100"))},
            {"draft":M07OrderDraft(
                "BTCIDR","SELL","LIMIT","safe-offline",D("100"),D("100"))},
            {"draft":M07OrderDraft(
                "BTCIDR","BUY","MARKET","safe-offline",quote_order_qty=10000)},
        )
        for delta in samples:
            with self.subTest(delta=delta):
                a=args()
                a.update(delta)
                with self.assertRaises((M07PreflightBlocked, ValueError)):
                    assess_m07_limit_buy_preflight_offline(**a)

    def test_original_orchestrator_method_safety_and_hardlock(self):
        with TemporaryDirectory() as d:
            app=DailyScalpingOrchestrator(
                Settings(),DecisionJournal(Path(d)/"decisions.sqlite3"))
            result=app.assess_m07_limit_buy_preflight_offline(**args())
            self.assertTrue(result.checks_passed_unverified)
            self.assertFalse(result.execution_allowed)
            with self.assertRaises(M07Hardlocked):
                OfficialV2OrderAdapter().dispatch()
            with DecisionJournal(Path(d)/"decisions.sqlite3")._connect() as db:
                count=db.execute("SELECT COUNT(*) FROM m07_offline_order_intents").fetchone()[0]
                self.assertEqual(count,0)

    def test_original_cli_0600_fixture_and_no_exchange_request(self):
        with TemporaryDirectory() as d:
            file=Path(d)/"m07-offline-params.json"
            file.write_text(json.dumps(fixture()))
            file.chmod(0o600)
            out=StringIO()
            err=StringIO()
            with patch(
                "indodax_ai_bot.m07_private_order_readonly._https_json",
                side_effect=AssertionError("EXCHANGE_MUST_NOT_BE_CALLED"),
            ), redirect_stdout(out), redirect_stderr(err):
                rc=main(["--m07-limit-preflight-offline-once",
                         "--m07-limit-preflight-file",str(file),
                         "--db",str(Path(d)/"decisions.sqlite3")])
            self.assertEqual(rc,0,err.getvalue())
            data=json.loads(out.getvalue())
            self.assertIsNone(data["final_decision"])
            self.assertFalse(data["execution_allowed"])
            self.assertFalse(data["live_trading_authorized"])
            self.assertTrue(
                data["m07_limit_preflight"]["checks_passed_unverified"])
            self.assertFalse(
                data["m07_limit_preflight"]["execution_allowed"])
            self.assertNotIn("USDT",out.getvalue())
            self.assertNotIn("TEST_SECRET",out.getvalue())

    def test_cli_rejects_extra_schema_and_world_readable_file(self):
        with TemporaryDirectory() as d:
            file=Path(d)/"bad.json"
            data=fixture()
            data["account_authenticated"]=True
            file.write_text(json.dumps(data))
            file.chmod(0o600)
            for expected in ("SCHEMA_INVALID","INVALID_UNTRUSTED"):
                output, errors=StringIO(),StringIO()
                with redirect_stdout(output),redirect_stderr(errors):
                    rc=main(["--m07-limit-preflight-offline-once",
                             "--m07-limit-preflight-file",str(file),
                             "--db",str(Path(d)/"decisions.sqlite3")])
                self.assertEqual(rc,3,errors.getvalue())
                self.assertIn("M07_PREFLIGHT_FILE_SCHEMA_INVALID",errors.getvalue())
                break
            file.chmod(0o644)
            output,errors=StringIO(),StringIO()
            with redirect_stdout(output),redirect_stderr(errors):
                rc=main(["--m07-limit-preflight-offline-once",
                         "--m07-limit-preflight-file",str(file),
                         "--db",str(Path(d)/"decisions.sqlite3")])
            self.assertEqual(rc,3,errors.getvalue())
            self.assertFalse(bool(output.getvalue()))
            self.assertIn("execution_allowed",errors.getvalue())


if __name__ == "__main__":
    unittest.main()
