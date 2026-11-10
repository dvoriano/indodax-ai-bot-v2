"""Original M03 WS3 24h summary WATCH research, strictly offline fixtures."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from indodax_ai_bot import __main__ as cli
from indodax_ai_bot.m03_ws_summary_offline_contract import (
    MarketSummaryContractError,
    normalize_documented_market_summary_offline,
    review_documented_market_summary_sequence_offline,
)
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator


def row(pair, stamp, last, vol):
    return [pair, stamp, last, "0.5", "200", "1", vol, "10000"]


def summary(rows, offset):
    return {"result": {"channel": "market:summary-24h",
                       "data": {"data": rows, "offset": offset}}}


def two_frames(now):
    return [
        summary([
            row("dogeidr", now - 65, "100", "160000000"),
            row("pepeidr", now - 65, "1", "180000000"),
            row("ethidr", now - 65, "100", "140000000"),
            row("btcidr", now - 65, "100", "180000000"),
        ], 1010),
        summary([
            row("dogeidr", now - 5, "103", "200000000"),
            row("pepeidr", now - 5, "1.1", "190000000"),
            row("ethidr", now - 5, "102", "145000000"),
            row("btcidr", now - 5, "99", "179000000"),
        ], 1017),
    ]


class M03WSOfflineWatchMainpathTests(unittest.TestCase):
    def test_original_orchestrator_ranks_positive_watch_and_excludes_reserved(self):
        now = 1794000090
        result = DailyScalpingOrchestrator.review_m03_ws_summary_fixture_watch_offline(
            two_frames(now), now_epoch=now,
        )
        self.assertEqual(result["status"],
                         "OFFLINE_WS_SUMMARY_M03_WATCH_RESEARCH_UNATTESTED")
        self.assertEqual(result["qualified_watch_candidate_count"], 1)
        self.assertEqual([x["pair"] for x in result["candidate_research_rows"]],
                         ["doge_idr"])
        self.assertEqual(result["candidate_research_rows"][0][
            "price_change_percent_between_two_samples"], "3")
        self.assertEqual(result["candidate_research_rows"][0][
            "change_in_rolling_24h_turnover_idr"], "40000000")
        self.assertEqual(result["skipped_pair_counts"]["excluded_pair"], 1)
        self.assertEqual(result["skipped_pair_counts"][
            "below_rolling_turnover_floor"], 1)
        self.assertEqual(result["skipped_pair_counts"][
            "nonpositive_observed_price_change"], 1)
        self.assertFalse(result["g1_receipt_issuance_allowed"])
        self.assertFalse(result["g2_provider_call_allowed"])
        self.assertFalse(result["execution_allowed"])
        self.assertTrue(result["rolling_24h_turnover_delta_is_not_minute_traded_volume"])
        self.assertEqual(result["network_requests"], 0)
        self.assertEqual(result["sqlite_writes"], 0)
        self.assertEqual(result["orders_sent"], 0)

    def test_offset_equal_rewind_duplicate_and_non_summary_fail(self):
        now = 1794000090
        for new_offset in [1010, 1009]:
            frames = two_frames(now)
            frames[1]["result"]["data"]["offset"] = new_offset
            with self.assertRaisesRegex(
                MarketSummaryContractError,
                "WS_SUMMARY_REPLAY_OR_OFFSET_REWIND",
            ):
                review_documented_market_summary_sequence_offline(
                    frames, now_epoch=now,
                )
        invalid = two_frames(now)
        invalid[1]["result"]["channel"] = "market:trade-activity-btcidr"
        with self.assertRaisesRegex(
            MarketSummaryContractError, "WS_SUMMARY_CHANNEL_INVALID",
        ):
            review_documented_market_summary_sequence_offline(
                invalid, now_epoch=now,
            )

    def test_stale_future_gap_or_no_new_pair_never_becomes_watch(self):
        now = 1794000090
        for after in [now - 500, now + 1, now - 95]:
            frames = two_frames(now)
            frames[1]["result"]["data"]["data"][0][1] = after
            report = DailyScalpingOrchestrator.review_m03_ws_summary_fixture_watch_offline(
                frames, now_epoch=now,
            )
            self.assertEqual(report["qualified_watch_candidate_count"], 0)
        frames = two_frames(now)
        frames[0]["result"]["data"]["data"][0][1] = now - 600
        report = DailyScalpingOrchestrator.review_m03_ws_summary_fixture_watch_offline(
            frames, now_epoch=now,
        )
        self.assertEqual(report["qualified_watch_candidate_count"], 0)
        frames = two_frames(now)
        frames[0]["result"]["data"]["data"][0][0] = "ltcidr"
        report = DailyScalpingOrchestrator.review_m03_ws_summary_fixture_watch_offline(
            frames, now_epoch=now,
        )
        self.assertEqual(report["qualified_watch_candidate_count"], 0)

    def test_wrong_options_and_huge_decimal_exponent_fail_closed(self):
        now = 1794000090
        for option in [{"now_epoch": True}, {"now_epoch": 0},
                       {"now_epoch": now, "max_candidates": 41}]:
            with self.assertRaises(MarketSummaryContractError):
                review_documented_market_summary_sequence_offline(
                    two_frames(now), **option,
                )
        rows = two_frames(now)
        rows[1]["result"]["data"]["data"][0][2] = "1e100000000"
        with self.assertRaisesRegex(
            MarketSummaryContractError, "WS_SUMMARY_NUMBER_INVALID",
        ):
            normalize_documented_market_summary_offline(rows[1])

    def test_original_cli_two_frame_fixture_no_io_or_journal(self):
        now = int(time.time())
        with TemporaryDirectory(prefix="v2-m03ws-", dir="/tmp") as td:
            fixture = Path(td) / "summary.json"
            fixture.write_text(json.dumps({"frames": two_frames(now)}))
            fixture.chmod(0o600)
            env = {
                "INDODAX_TRADING_ENABLED": "NO",
                "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
                "INDODAX_EXECUTION_MODE": "DRY_RUN",
            }
            with patch.dict(os.environ, env), patch.object(
                cli, "DecisionJournal", side_effect=AssertionError("NO_DB")
            ), patch.object(
                cli, "IndodaxPublicMarket", side_effect=AssertionError("NO_REST")
            ), patch(
                "indodax_ai_bot.public_market.urlopen",
                side_effect=AssertionError("NO_HTTP"),
            ), patch("sys.stdout", new_callable=io.StringIO) as out, patch(
                "sys.stderr", new_callable=io.StringIO,
            ) as err:
                code = cli.main([
                    "--m03-ws-summary-replay-offline-once",
                    "--m03-ws-summary-replay-file", str(fixture),
                ])
            self.assertEqual(code, 0, err.getvalue())
            self.assertEqual(err.getvalue(), "")
            result = json.loads(out.getvalue())
            self.assertEqual(result["m03_ws_summary_offline_review"][
                "qualified_watch_candidate_count"], 1)
            self.assertIsNone(result["final_decision"])
            self.assertFalse(result["execution_allowed"])
            self.assertFalse(result["live_trading_authorized"])
            self.assertEqual(set(Path(td).iterdir()), {fixture})

    def test_original_cli_private_file_permission_and_live_mode_denied(self):
        now = int(time.time())
        with TemporaryDirectory(prefix="v2-m03ws-", dir="/tmp") as td:
            fixture = Path(td) / "summary.json"
            fixture.write_text(json.dumps({"frames": two_frames(now)}))
            env = {
                "INDODAX_TRADING_ENABLED": "NO",
                "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
                "INDODAX_EXECUTION_MODE": "DRY_RUN",
            }
            args = [
                "--m03-ws-summary-replay-offline-once",
                "--m03-ws-summary-replay-file", str(fixture),
            ]
            fixture.chmod(0o644)
            with patch.dict(os.environ, env), patch(
                "sys.stderr", new_callable=io.StringIO,
            ) as err, patch("sys.stdout", new_callable=io.StringIO):
                code = cli.main(args)
            self.assertEqual(code, 2)
            self.assertIn("M03_WS_REPLAY_FIXTURE_UNTRUSTED", err.getvalue())
            fixture.chmod(0o600)
            env["INDODAX_TRADING_ENABLED"] = "YES"
            with patch.dict(os.environ, env), patch(
                "sys.stderr", new_callable=io.StringIO
            ) as err, patch("sys.stdout", new_callable=io.StringIO):
                code = cli.main(args)
            self.assertEqual(code, 2)
            self.assertNotIn("candidate_research_rows", err.getvalue())


if __name__ == "__main__":
    unittest.main()
