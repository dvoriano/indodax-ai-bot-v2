"""Original V2 opt-in bounded micro research archive, offline only."""
from __future__ import annotations

from pathlib import Path
import os
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

from indodax_ai_bot.m03_ws3_micro_research_archive_nonlive import (
    archive_ws3_micro_research_nonlive, RETENTION_SECONDS,
)
from indodax_ai_bot.m03_ws_summary_live_watch import (
    run_m03_ws3_summary_watch_once,
)

NOW = 1794330040


def micro(pair="uai_idr"):
    return {
        "pair": pair, "trade_frames": 3,
        "source_buy_count": 2, "source_sell_count": 1,
        "source_buy_idr": "100000", "source_sell_idr": "50000",
        "last_trade_epoch": NOW-5, "trade_offset": 44,
        "trade_gap_observed": False, "trade_truncated": False,
        "book": {
            "pair": pair, "best_bid_idr": "100", "best_ask_idr": "101",
            "spread_bps": "99.5025", "bid_depth_top5_idr": "1800000",
            "ask_depth_top5_idr": "1700000",
            "book_imbalance_top5_ratio": "1.0588",
        },
        "book_local_epoch": NOW-4, "book_offset": 45,
        "source_malformed": False, "source_coverage_complete": False,
        "aggressor_side_verified": False, "g1_verified": False,
        "execution_allowed": False,
    }


def snapshot(rows=None):
    return {
        "status": "M03_WS3_PUBLIC_SUMMARY_OBSERVED",
        "frames_observed": 2, "pairs": [],
        "microstructure": [micro()] if rows is None else rows,
        "microstructure_selection_source": "PRIOR_M03_MINUTE_ARCHIVE",
        "microstructure_selected_pairs": ["uai_idr"],
        "microstructure_current_liquid_pool_count": 2,
        "microstructure_channels_requested": 2,
        "microstructure_channels_acknowledged": 2,
        "microstructure_trade_frames_received": 3,
        "microstructure_book_snapshots_received": 1,
        "microstructure_malformed_pair_count": 0,
    }


class M03WSMicroResearch72hTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.db = self.base / "private" / "observations.sqlite3"

    def record(self, rows=None, *, now=NOW):
        return archive_ws3_micro_research_nonlive(
            snapshot(rows), now_epoch=now, evidence_db=self.db)

    def test_original_two_pair_cap_private_permissions_and_source_flags(self):
        result = self.record()
        self.assertEqual(result["status"],
                         "M03_WS3_MICRO_RESEARCH_ARCHIVE_RECORDED")
        self.assertEqual(result["source_micro_pairs_selected"], 1)
        self.assertEqual(result["source_micro_evidence_pairs_attempted"], 1)
        self.assertEqual(result["extra_exchange_requests"], 0)
        self.assertEqual(result["orders_sent"], 0)
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(stat.S_IMODE(self.db.lstat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(self.db.parent.lstat().st_mode),0o700)
        with sqlite3.connect(self.db) as db:
            rows=db.execute(
                "SELECT pair,minute_epoch,source_buy_count,source_sell_count,"
                "best_bid_idr,best_ask_idr,source_complete,aggressor_verified,"
                "g1_verified FROM ws3_micro_research_evidence"
            ).fetchall()
        self.assertEqual(rows, [
            ("uai_idr",(NOW//60)*60,2,1,"100","101",0,0,0)
        ])

    def test_same_minute_first_observation_immutable(self):
        self.record()
        changed = micro()
        changed["source_buy_idr"] = "999999"
        self.record([changed])
        with sqlite3.connect(self.db) as db:
            value=db.execute(
                "SELECT source_buy_idr,COUNT(*) FROM "
                "ws3_micro_research_evidence").fetchone()
        self.assertEqual(value,("100000",1))

    def test_trading_authority_forgery_and_future_source_rejected(self):
        for patchrow in (
            {"g1_verified":True}, {"execution_allowed":True},
            {"aggressor_side_verified":True},
            {"source_coverage_complete":True},
            {"book_local_epoch":NOW+10},
            {"last_trade_epoch":NOW+1},
            {"source_buy_idr":"NaN"},
            {"trade_gap_observed":1},
        ):
            with self.subTest(patchrow=patchrow):
                row=micro()
                row.update(patchrow)
                with self.assertRaises(ValueError):
                    self.record([row])

    def test_missing_book_imbalance_is_unknown_not_fabricated(self):
        row=micro()
        row["book"]["book_imbalance_top5_ratio"]=None
        self.record([row])
        with sqlite3.connect(self.db) as db:
            value=db.execute(
                "SELECT book_imbalance_top5_ratio "
                "FROM ws3_micro_research_evidence").fetchone()[0]
        self.assertIsNone(value)

    def test_empty_capture_is_not_zero_trades(self):
        row=micro()
        row.update(trade_frames=0,source_buy_count=None,
                   source_sell_count=None,source_buy_idr=None,
                   source_sell_idr=None,last_trade_epoch=None,
                   trade_offset=None,book=None,book_offset=None,
                   book_local_epoch=None)
        r=self.record([row])
        self.assertEqual(r["source_micro_evidence_pairs_attempted"],0)
        with sqlite3.connect(self.db) as db:
            count=db.execute(
                "SELECT COUNT(*) FROM ws3_micro_research_evidence"
            ).fetchone()[0]
        self.assertEqual(count,0)

    def test_duplicate_invalid_and_excess_pair_inputs_fail_closed(self):
        with self.assertRaises(ValueError):
            self.record([micro(),micro()])
        with self.assertRaises(ValueError):
            self.record([micro("a_idr"),micro("b_idr"),micro("c_idr")])
        with self.assertRaises(ValueError):
            self.record([micro("bad/coin")])

    def test_retention_72h_and_highwater_cap(self):
        self.record()
        later=NOW+RETENTION_SECONDS+120
        row=micro("new_idr")
        row["last_trade_epoch"]=later-3
        row["book_local_epoch"]=later-5
        self.record([row],now=later)
        with sqlite3.connect(self.db) as db:
            count=db.execute(
                "SELECT COUNT(*),MAX(pair) FROM ws3_micro_research_evidence"
            ).fetchone()
        self.assertEqual(count,(1,"new_idr"))
        with patch("indodax_ai_bot.m03_ws3_micro_research_archive_nonlive."
                   "MAX_MICRO_TOTAL_ROWS",1):
            latest=micro("other_idr")
            latest["last_trade_epoch"]=later+60-3
            latest["book_local_epoch"]=later+60-4
            self.record([latest],now=later+60)
        with sqlite3.connect(self.db) as db:
            count=db.execute(
                "SELECT COUNT(*) FROM ws3_micro_research_evidence"
            ).fetchone()[0]
        self.assertEqual(count,1)

    def test_reject_insecure_existing_sqlite(self):
        self.db.parent.mkdir(mode=0o700)
        self.db.write_text("")
        self.db.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "EVIDENCE_DB_UNTRUSTED"):
            self.record()

    def test_optional_archive_off_by_default_and_no_new_collector(self):
        obs=snapshot()
        discover={"pairs":["uai_idr"],"status":"SELECTED",
                  "fresh_liquid_pair_count":1}
        with (
            patch("indodax_ai_bot.m03_ws_summary_live_watch."
                  "collect_ws3_market_summary_once",return_value=obs) as collector,
            patch("indodax_ai_bot.m03_ws_summary_live_watch."
                  "persist_ws3_summary_watch",return_value={"status":"WATCH_OK"}),
            patch("indodax_ai_bot.m05_radar_momentum_nonlive."
                  "inspect_original_m03_ws3_micro_discovery_readonly",
                  return_value=discover),
            patch.dict(os.environ,{
                "M03_WS3_EVIDENCE_ARCHIVE_GO":"YES",
                "M03_WS3_MICRO_72H_ARCHIVE_GO":"NO",
            }),
            patch("indodax_ai_bot.m03_ws3_evidence_archive_nonlive."
                  "archive_ws3_evidence_nonlive",
                  return_value={"status":"SOURCE_RECORDED"}),
            patch("indodax_ai_bot.m03_ws3_micro_research_archive_nonlive."
                  "archive_ws3_micro_research_nonlive") as micro_write,
        ):
            result=run_m03_ws3_summary_watch_once(
                str(self.base/"decisions.sqlite3"),
                now_fn=lambda:NOW,micro_enabled=True)
        collector.assert_called_once()
        micro_write.assert_not_called()
        self.assertNotIn("research_micro_archive",result)

    def test_both_optins_activate_same_snapshot_local_archive_only(self):
        obs=snapshot()
        discover={"pairs":["uai_idr"],"status":"SELECTED",
                  "fresh_liquid_pair_count":1}
        with (
            patch("indodax_ai_bot.m03_ws_summary_live_watch."
                  "collect_ws3_market_summary_once",return_value=obs) as collector,
            patch("indodax_ai_bot.m03_ws_summary_live_watch."
                  "persist_ws3_summary_watch",return_value={"status":"WATCH_OK"}),
            patch("indodax_ai_bot.m05_radar_momentum_nonlive."
                  "inspect_original_m03_ws3_micro_discovery_readonly",
                  return_value=discover),
            patch.dict(os.environ,{
                "M03_WS3_EVIDENCE_ARCHIVE_GO":"YES",
                "M03_WS3_MICRO_72H_ARCHIVE_GO":"YES",
            }),
            patch("indodax_ai_bot.m03_ws3_evidence_archive_nonlive."
                  "archive_ws3_evidence_nonlive",
                  return_value={"status":"SOURCE_RECORDED"}),
            patch("indodax_ai_bot.m03_ws3_micro_research_archive_nonlive."
                  "archive_ws3_micro_research_nonlive",
                  return_value={"status":"MICRO_RECORDED"}) as micro_write,
        ):
            result=run_m03_ws3_summary_watch_once(
                str(self.base/"decisions.sqlite3"),
                now_fn=lambda:NOW,micro_enabled=True)
        collector.assert_called_once()
        micro_write.assert_called_once()
        self.assertIs(micro_write.call_args.args[0],obs)
        self.assertEqual(result["research_micro_archive"]["status"],
                         "MICRO_RECORDED")

    def test_shadow_failure_does_not_stop_existing_m03_watch(self):
        obs=snapshot()
        discover={"pairs":["uai_idr"],"status":"SELECTED",
                  "fresh_liquid_pair_count":1}
        with (
            patch("indodax_ai_bot.m03_ws_summary_live_watch."
                  "collect_ws3_market_summary_once",return_value=obs),
            patch("indodax_ai_bot.m03_ws_summary_live_watch."
                  "persist_ws3_summary_watch",return_value={
                      "status":"WATCH_OK","watch_candidate_count":1}),
            patch("indodax_ai_bot.m05_radar_momentum_nonlive."
                  "inspect_original_m03_ws3_micro_discovery_readonly",
                  return_value=discover),
            patch.dict(os.environ,{
                "M03_WS3_EVIDENCE_ARCHIVE_GO":"YES",
                "M03_WS3_MICRO_72H_ARCHIVE_GO":"YES",
            }),
            patch("indodax_ai_bot.m03_ws3_evidence_archive_nonlive."
                  "archive_ws3_evidence_nonlive",
                  return_value={"status":"SOURCE_RECORDED"}),
            patch("indodax_ai_bot.m03_ws3_micro_research_archive_nonlive."
                  "archive_ws3_micro_research_nonlive",
                  side_effect=OSError("inaccessible research DB")),
        ):
            result=run_m03_ws3_summary_watch_once(
                str(self.base/"decisions.sqlite3"),
                now_fn=lambda:NOW,micro_enabled=True)
        self.assertEqual(result["watch_candidate_count"],1)
        self.assertEqual(result["research_micro_archive"]["status"],
                         "M03_WS3_MICRO_RESEARCH_ARCHIVE_UNAVAILABLE")
        self.assertEqual(result["research_micro_archive"]["orders_sent"],0)


if __name__ == "__main__":
    unittest.main()
