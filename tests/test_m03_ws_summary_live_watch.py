"""M03 official WS3 summary live WATCH: offline socket/database regression tests."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m03_ws_summary_live_watch import (
    M03WSWatchError, collect_ws3_market_summary_once,
    persist_ws3_summary_watch,
)

NOW = 1794330000


class FakeTimeout(Exception):
    pass


class Clock:
    def __init__(self):
        self.tick = 0
    def __call__(self):
        self.tick += 1
        return self.tick * 0.5


class Socket:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []
        self.closed = False
        self.timeout = None
    def settimeout(self, timeout):
        self.timeout = timeout
    def send(self, data):
        import json
        self.sent.append(json.loads(data))
    def recv(self):
        if self.frames:
            import json
            return json.dumps(self.frames.pop(0))
        raise FakeTimeout
    def close(self):
        self.closed = True


class FakeWS:
    WebSocketTimeoutException = FakeTimeout
    def __init__(self, frames):
        self.sock = Socket(frames)
    def create_connection(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs
        return self.sock


def frames(stamp, price, *, offset=124, pair="uaiidr"):
    return [
        {"id": 1, "result": {"client": "synthetic-offline-client"}},
        {"id": 2, "result": {"recoverable": True,
                              "epoch": "1", "offset": 123}},
        {"result": {
            "channel": "market:summary-24h",
            "data": {"offset": offset, "data": [
                [pair, stamp, price, 90, 200, 90, "250000000", "1500"],
                ["btcidr", stamp, 100, 90, 120, 95, "700000000", "2"],
            ]},
        }},
    ]


class M03WSLiveWatchTests(unittest.TestCase):
    def capture(self, provided):
        provider = FakeWS(provided)
        value = collect_ws3_market_summary_once(
            ws_module=provider, monotonic=Clock(), observe_seconds=3,
        )
        self.assertTrue(provider.sock.closed)
        self.assertEqual(provider.sock.sent[1]["params"]["channel"],
                         "market:summary-24h")
        self.assertEqual(provider.sock.timeout, 1)
        self.assertEqual(value["frames_observed"], 1)
        return value

    def test_40_second_timer_watch_is_bounded_and_source_only(self):
        provider = FakeWS(frames(NOW, 110))
        result = collect_ws3_market_summary_once(
            ws_module=provider, monotonic=Clock(), observe_seconds=40,
        )
        self.assertEqual(result["requested_observation_seconds"], 40)
        self.assertEqual(result["frames_observed"], 1)
        self.assertTrue(provider.sock.closed)
        for invalid in (0, 2, 46, True, None):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    M03WSWatchError, "M03_WS_WATCH_WINDOW_INVALID",
                ):
                    collect_ws3_market_summary_once(
                        ws_module=FakeWS(frames(NOW, 100)),
                        monotonic=Clock(), observe_seconds=invalid,
                    )

    def test_staged_unit_reuses_original_m03_service_and_persistent_v2_db(self):
        root = Path(__file__).resolve().parents[1]
        staged = (root / "deploy" /
                  "indodax-v2-m03-mainpath.ws3-watch.service").read_text()
        timer = (root / "deploy" /
                 "indodax-v2-m03-mainpath.timer").read_text()
        self.assertIn("--m03-ws-summary-watch-once", staged)
        self.assertIn("--m03-ws-observe-seconds 40", staged)
        self.assertIn("runtime/decisions.sqlite3", staged)
        self.assertIn("indodax-v2-m03-mainpath.lock", staged)
        self.assertIn("Type=oneshot", staged)
        self.assertIn("Restart=no", staged)
        self.assertIn("TimeoutStartSec=70s", staged)
        self.assertNotIn("ExecStartPre=", staged)
        self.assertNotIn("--scheduled-public-scanner-once", staged)
        self.assertNotIn("https://indodax.com/api", staged)
        self.assertIn("OnCalendar=", timer)

    def test_official_public_summary_is_watch_only_and_archived_separately(self):
        before = self.capture(frames(NOW, 100))
        after = self.capture(frames(NOW + 60, 110, offset=125))
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "decisions.sqlite3"
            journal = DecisionJournal(path)
            first = persist_ws3_summary_watch(
                journal, before, now_epoch=NOW,
            )
            self.assertEqual(first["watch_candidate_count"], 0)
            second = persist_ws3_summary_watch(
                journal, after, now_epoch=NOW + 60,
            )
            self.assertEqual(second["status"], "M03_WS3_SUMMARY_WATCH_ONLY_OK")
            self.assertEqual(second["watch_candidates_top30"][0]["pair"],
                             "uai_idr")
            self.assertEqual(second["watch_candidates_top30"][0][
                "price_change_between_samples_pct"], "10.0000")
            self.assertFalse(second["original_rest_all_idr_scanner_recovered"])
            self.assertFalse(second["g1_receipt_issuance_allowed"])
            self.assertFalse(second["execution_allowed"])
            self.assertFalse(second["best_bid_ask_spread_depth_verified"])
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM m03_ws_summary_watch_minute"
                ).fetchone()[0], 4)
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM public_ticker_minute_samples"
                ).fetchone()[0], 0)
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM decisions"
                ).fetchone()[0], 0)

    def test_latest_same_second_quote_uses_source_offset_per_pair(self):
        incoming = frames(NOW, 100, offset=124)
        second = {
            "result": {
                "channel": "market:summary-24h",
                "data": {
                    "offset": 125,
                    "data": [["uaiidr", NOW, 110, 90, 200, 90,
                              "250000000", "1500"]],
                },
            },
        }
        provider = FakeWS(incoming + [second])
        snapshot = collect_ws3_market_summary_once(
            ws_module=provider, monotonic=Clock(), observe_seconds=3,
        )
        self.assertEqual(snapshot["frames_observed"], 2)
        by_pair = {x["pair"]: x for x in snapshot["pairs"]}
        self.assertEqual(by_pair["uai_idr"]["last_idr"], "110")
        self.assertEqual(by_pair["uai_idr"]["source_public_offset"], 125)
        self.assertEqual(by_pair["btc_idr"]["source_public_offset"], 124)
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "decisions.sqlite3"
            persist_ws3_summary_watch(
                DecisionJournal(path), snapshot, now_epoch=NOW,
            )
            with sqlite3.connect(path) as db:
                rows = dict(db.execute(
                    "SELECT pair,public_offset FROM "
                    "m03_ws_summary_watch_minute"
                ).fetchall())
            self.assertEqual(rows["uai_idr"], 125)
            self.assertEqual(rows["btc_idr"], 124)

    def test_duplicate_same_minute_cannot_reemit_candidate_or_rewrite_quote(self):
        before = self.capture(frames(NOW, 100, offset=124))
        after = self.capture(frames(NOW + 60, 110, offset=125))
        replay = self.capture(frames(NOW + 60, 130, offset=126))
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "decisions.sqlite3"
            journal = DecisionJournal(path)
            persist_ws3_summary_watch(journal, before, now_epoch=NOW)
            first = persist_ws3_summary_watch(
                journal, after, now_epoch=NOW+60,
            )
            self.assertEqual(first["watch_candidate_count"], 1)
            self.assertEqual(first["prior_source_samples_compared"], 2)
            duplicate = persist_ws3_summary_watch(
                journal, replay, now_epoch=NOW+60,
            )
            self.assertEqual(duplicate["watch_candidate_count"], 0)
            self.assertEqual(duplicate["newly_stored_minute_samples"], 0)
            self.assertEqual(
                duplicate["candidate_baseline_status"],
                "WARMUP_NO_PRIOR_COMPARABLE_PAIR",
            )
            with sqlite3.connect(path) as db:
                saved = db.execute(
                    "SELECT last_idr FROM m03_ws_summary_watch_minute "
                    "WHERE pair='uai_idr' ORDER BY minute_epoch DESC LIMIT 1"
                ).fetchone()
            self.assertEqual(saved[0], "110")

    def test_malformed_idr_row_is_not_silently_discarded(self):
        incoming = frames(NOW, 100)
        incoming[2]["result"]["data"]["data"][0] = [
            "uaiidr", NOW, 100, 90, 200, 90, "250000000",
        ]
        with self.assertRaisesRegex(
            M03WSWatchError, "M03_WS_WATCH_SOURCE_SCHEMA_INVALID",
        ):
            self.capture(incoming)

    def test_first_scan_explains_baseline_warmup_without_claiming_rest_recovered(self):
        data = self.capture(frames(NOW, 100))
        with TemporaryDirectory() as temporary:
            result = persist_ws3_summary_watch(
                DecisionJournal(Path(temporary)/"decisions.sqlite3"),
                data, now_epoch=NOW,
            )
            self.assertEqual(
                result["candidate_baseline_status"],
                "WARMUP_NO_PRIOR_COMPARABLE_PAIR",
            )
            self.assertEqual(result["original_rest_http_status"],
                             "NOT_CHECKED_BY_WS3")
            self.assertEqual(result["watch_candidate_count"], 0)
            self.assertFalse(result["original_rest_all_idr_scanner_recovered"])

    def test_stale_source_does_not_enter_watch_database(self):
        view = self.capture(frames(NOW-3600, 110))
        with TemporaryDirectory() as temporary:
            journal = DecisionJournal(Path(temporary) / "decisions.sqlite3")
            with self.assertRaisesRegex(
                M03WSWatchError, "M03_WS_WATCH_NO_FRESH_LIQUID_IDR_PAIR",
            ):
                persist_ws3_summary_watch(journal, view, now_epoch=NOW)

    def test_malformed_official_frame_rejected_without_body_leak(self):
        parts = frames(NOW, 110)
        parts[2]["result"]["data"]["data"][0][2] = "SECRET_500"
        with self.assertRaisesRegex(
            M03WSWatchError, "M03_WS_WATCH_SOURCE_SCHEMA_INVALID",
        ) as raised:
            self.capture(parts)
        self.assertNotIn("SECRET_500", str(raised.exception))

    def test_subscription_denial_is_not_a_successful_watch(self):
        parts = frames(NOW, 110)
        parts[1] = {"id": 2, "error": {"reason": "PRIVATE_TOKEN"}}
        with self.assertRaisesRegex(
            M03WSWatchError, "M03_WS_WATCH_SUBSCRIBE_REJECTED",
        ):
            self.capture(parts)

    def test_missing_liquidity_and_excluded_main_pair_not_in_watch(self):
        parts = frames(NOW, 100, pair="flokiidr")
        view = self.capture(parts)
        with TemporaryDirectory() as temporary:
            journal = DecisionJournal(Path(temporary) / "decisions.sqlite3")
            out = persist_ws3_summary_watch(journal, view, now_epoch=NOW)
            self.assertEqual(out["fresh_liquid_main_idr_pairs"], 1)
            self.assertEqual(out["watch_candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
