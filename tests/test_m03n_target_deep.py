"""M03-N target same eligible pair across actual source-bound deep minutes.

The targeting option reuses DailyScalpingOrchestrator + deep_scan and only
changes diagnostic sampling. It never buys, authorizes G1, or targets protected
coins. No new service and no extra deep API endpoints.
"""
from __future__ import annotations

from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from contextlib import redirect_stderr
import json
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from test_m03b_deep import observation, screening
from test_m03n_book_rebound import with_wall


class TargetMarket:
    def __init__(self):
        self.called=[]
    def observe(self, pair):
        self.called.append(pair)
        return with_wall(observation(pair))


class M03NTargetDeepMainpath(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.j=DecisionJournal(Path(self.tmp.name)/"journal.sqlite3")
        self.app=DailyScalpingOrchestrator(Settings.from_env({}),self.j)
        original=screening()
        self.scan=replace(original, universe=original.shortlist)

    def test_original_mainpath_can_reobserve_pair_outside_top_two(self):
        market=TargetMarket()
        with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scan:
            scan.return_value.scan.return_value=self.scan
            outcome=self.app.run_deep_cycle(
                "single-sol",market=market,target_pair="sol_idr"
            )
        self.assertEqual(market.called,["sol_idr"])
        self.assertEqual(outcome.final_decision.value,"REJECT")
        self.assertFalse(outcome.execution_allowed)
        self.assertEqual(outcome.deep["deep_examined_count"],1)
        row=outcome.deep["ranked_diagnostics"][0]
        self.assertEqual(row["pair"],"sol_idr")
        self.assertEqual(row["wall_temporal_evidence"]["status"],
                         "BID_WALL_PERSISTENCE_UNPROVEN")
        self.assertFalse(row["trade_authorized"])
        with self.j._connect() as con:
            got=con.execute(
                "SELECT pair FROM public_deep_wall_samples"
            ).fetchall()
        self.assertEqual(got,[("sol_idr",)])

    def test_without_target_preserves_existing_two_pair_deep_path(self):
        market=TargetMarket()
        with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scan:
            scan.return_value.scan.return_value=self.scan
            outcome=self.app.run_deep_cycle("original-two",market=market)
        self.assertEqual(market.called,["btc_idr","eth_idr"])
        self.assertEqual(outcome.deep["deep_examined_count"],2)
        self.assertFalse(outcome.execution_allowed)

    def test_nonexistent_pair_cannot_bypass_universe(self):
        market=TargetMarket()
        with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scan:
            scan.return_value.scan.return_value=self.scan
            outcome=self.app.run_deep_cycle(
                "unknown-pair",market=market,target_pair="qnt_idr",
            )
        self.assertEqual(market.called,[])
        self.assertIsNone(outcome.deep)
        self.assertIn("TARGET_DEEP_PAIR_NOT_ELIGIBLE",outcome.reason)
        self.assertFalse(outcome.execution_allowed)

    def test_floki_pepe_stablecoin_and_usdt_pair_are_never_main_target(self):
        for pair in ("floki_idr","pepe_idr","usdt_idr","usdc_idr",
                     "qnt_usdt","BTC/IDR", ""):
            with self.subTest(pair=pair):
                market=TargetMarket()
                with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scan:
                    scan.return_value.scan.return_value=self.scan
                    outcome=self.app.run_deep_cycle(
                        "reject-"+str(pair),market=market,target_pair=pair,
                    )
                self.assertIsNone(outcome.deep)
                self.assertEqual(market.called,[])
                self.assertFalse(outcome.execution_allowed)

    def test_cli_deep_pair_is_disallowed_for_scan_only(self):
        err=StringIO()
        with redirect_stderr(err):
            rc=main(["--scan-once","--deep-pair","sol_idr",
                     "--db",str(Path(self.tmp.name)/"cli.sqlite3")])
        self.assertEqual(rc,2)
        self.assertEqual(json.loads(err.getvalue())["error"],
                         "DEEP_PAIR_REQUIRES_DEEP_ONCE")


if __name__ == "__main__":
    unittest.main()
