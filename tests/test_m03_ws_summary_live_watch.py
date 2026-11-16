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


class RawWSFrame(str):
    """Synthetic non-JSON text returned directly by ws.recv()."""


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
            value = self.frames.pop(0)
            return str(value) if isinstance(value, RawWSFrame) else json.dumps(value)
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
        override = (root / "deploy" /
                    "indodax-v2-m03-mainpath.ws3-watch.override.conf").read_text()
        self.assertIn("[Service]", override)
        self.assertIn("ExecStart=\nExecStart=/usr/bin/flock", override)
        self.assertIn("--m03-ws-summary-watch-once", override)
        self.assertIn("--m03-ws-observe-seconds 40", override)
        self.assertIn("runtime/decisions.sqlite3", override)
        self.assertNotIn("--scheduled-public-scanner-once", override)

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

    def test_nonjson_auth_phase_is_redacted_and_fail_closed(self):
        provider = FakeWS([RawWSFrame("INTERNAL_SECRET NOT_JSON")])
        with self.assertRaisesRegex(
            M03WSWatchError,
            r"^M03_WS_WATCH_JSON_INVALID:AUTH:NONJSON_TEXT$",
        ) as raised:
            collect_ws3_market_summary_once(
                ws_module=provider, monotonic=Clock(), observe_seconds=3,
            )
        self.assertNotIn("INTERNAL_SECRET", str(raised.exception))
        self.assertTrue(provider.sock.closed)

    def test_nonjson_subscribe_phase_is_redacted(self):
        provider = FakeWS([
            {"id": 1, "result": {"client": "offline-client"}},
            RawWSFrame("NOTJSON BEARER_SECRET"),
        ])
        with self.assertRaisesRegex(
            M03WSWatchError,
            r"^M03_WS_WATCH_JSON_INVALID:SUBSCRIBE:NONJSON_TEXT$",
        ) as raised:
            collect_ws3_market_summary_once(
                ws_module=provider, monotonic=Clock(), observe_seconds=3,
            )
        self.assertNotIn("BEARER_SECRET", str(raised.exception))

    def test_nonjson_observe_markup_and_incomplete_structured_fail_closed(self):
        for corrupted, expected in (
            (RawWSFrame("<html>PRIVATE_GATEWAY</html>"), "NONJSON_MARKUP"),
            (RawWSFrame('{"result": "PRIVATE_VALUE"'), "STRUCTURED_INCOMPLETE"),
            (RawWSFrame("   "), "EMPTY_TEXT"),
        ):
            with self.subTest(expected=expected):
                parts = frames(NOW, 110)
                provider = FakeWS(parts[:2] + [corrupted] + parts[2:])
                with self.assertRaisesRegex(
                    M03WSWatchError,
                    r"^M03_WS_WATCH_JSON_INVALID:OBSERVE:" + expected + "$",
                ) as raised:
                    collect_ws3_market_summary_once(
                        ws_module=provider,
                        monotonic=Clock(), observe_seconds=3,
                    )
                self.assertNotIn("PRIVATE_", str(raised.exception))
                self.assertTrue(provider.sock.closed)

    def test_malformed_structured_ws3_subcategories_no_content_leak(self):
        # No real market traffic, offline WS frame fakes only.
        # These are syntax classes, NOT a claim about actual Indodax frames.
        cases = (
            ('{"a":1}{"b":"SENSITIVE"}', "STRUCTURED_EXTRA_DATA"),
            ('{"a" 1}', "STRUCTURED_COLON"),
            ('{"a":1 "b":2}', "STRUCTURED_SEPARATOR"),
            ('{SECRET_KEY:1}', "STRUCTURED_PROPERTY"),
            ('{"a": SECRET_VALUE}', "STRUCTURED_VALUE"),
            ('{"a":"se\ncret"}', "STRUCTURED_CONTROL"),
            (r'{"a":"se\qcret"}', "STRUCTURED_ESCAPE"),
            (r'{"a":"se\uZZZZ"}', "STRUCTURED_UNICODE"),
        )
        for payload, expected in cases:
            with self.subTest(expected=expected):
                base = frames(NOW, 110)
                provider = FakeWS(base[:2] + [RawWSFrame(payload)] + base[2:])
                with self.assertRaises(M03WSWatchError) as raised:
                    collect_ws3_market_summary_once(
                        ws_module=provider,
                        monotonic=Clock(), observe_seconds=3,
                    )
                self.assertEqual(
                    str(raised.exception),
                    "M03_WS_WATCH_JSON_INVALID:OBSERVE:" + expected,
                )
                self.assertNotIn("SENSITIVE", str(raised.exception))
                self.assertNotIn("SECRET", str(raised.exception))
                self.assertTrue(provider.sock.closed)

    def test_coalesced_complete_observe_publications_same_socket(self):
        import json
        first = frames(NOW, 100)
        second = frames(NOW + 1, 110, offset=125)[2]
        frame = RawWSFrame(json.dumps(first[2]) + "\n" + json.dumps(second))
        provider = FakeWS(first[:2] + [frame])
        out = collect_ws3_market_summary_once(
            ws_module=provider, monotonic=Clock(), observe_seconds=3,
        )
        self.assertEqual(out["frames_observed"], 2)
        self.assertEqual(out["last_public_offset"], 125)
        self.assertEqual(out["coalesced_publication_frames"], 1)
        self.assertEqual(next(r for r in out["pairs"] if r["pair"] == "uai_idr")
                         ["last_idr"], "110")
        self.assertEqual(len(provider.sock.sent), 2)

    def test_coalesced_prefix_never_salvaged_from_invalid_suffix(self):
        import json
        first = frames(NOW, 100)
        valid = json.dumps(first[2])
        bad = frames(NOW+1, 110, offset=125)[2]
        bad["result"]["data"]["data"][0][2] = "SECRET_INVALID_PRICE"
        for suffix in ("trailing SECRET", "{}", json.dumps(bad),
                       valid * 8, '{"result":'):
            with self.subTest(suffix_type=len(suffix)):
                provider = FakeWS(first[:2] + [RawWSFrame(valid + suffix)])
                with self.assertRaisesRegex(
                    M03WSWatchError,
                    "^M03_WS_WATCH_JSON_INVALID:OBSERVE:STRUCTURED_EXTRA_DATA$",
                ):
                    collect_ws3_market_summary_once(
                        ws_module=provider, monotonic=Clock(), observe_seconds=3,
                    )
                self.assertTrue(provider.sock.closed)

    def test_coalesced_auth_ack_is_not_accepted(self):
        import json
        first = frames(NOW, 100)
        provider = FakeWS([RawWSFrame(json.dumps(first[0]) + json.dumps(first[1]))])
        with self.assertRaisesRegex(
            M03WSWatchError, "^M03_WS_WATCH_JSON_INVALID:AUTH:STRUCTURED_EXTRA_DATA$"
        ):
            collect_ws3_market_summary_once(
                ws_module=provider, monotonic=Clock(), observe_seconds=3,
            )

    def test_unknown_json_decoder_reason_is_always_redacted(self):
        import json
        from indodax_ai_bot.m03_ws_summary_live_watch import (
            _redacted_json_failure_code,
        )
        raw = '{"a": MALFORMED_PRIVATE}'
        synthetic = json.JSONDecodeError(
            "SOURCE_PRIVATE_EXC_DETAIL", raw, 6
        )
        result = _redacted_json_failure_code(
            raw, phase="OBSERVE", error=synthetic,
        )
        self.assertEqual(
            result, "M03_WS_WATCH_JSON_INVALID:OBSERVE:STRUCTURED_INVALID"
        )
        self.assertNotIn("PRIVATE", result)
        self.assertNotIn("MALFORMED", result)

    def test_nonjson_after_market_frame_does_not_accept_partial_watch(self):
        parts = frames(NOW, 100)
        parts.append(RawWSFrame("NOTJSON SECRET_AFTER_VALID_SUMMARY"))
        provider = FakeWS(parts)
        with self.assertRaisesRegex(
            M03WSWatchError, r"M03_WS_WATCH_JSON_INVALID:OBSERVE:NONJSON_TEXT",
        ):
            collect_ws3_market_summary_once(
                ws_module=provider, monotonic=Clock(), observe_seconds=3,
            )
        self.assertTrue(provider.sock.closed)
        self.assertFalse(
            hasattr(provider, "success_report")
        )

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


    def test_real_micro_trade_book_on_same_original_socket_and_db(self):
        import json
        from indodax_ai_bot.m03_ws_microstructure import (
            trade_channel, book_channel,
        )
        book = {
            "pair": "uaiidr",
            "bid": [{"price": "100", "idr_volume": "7000000"}],
            "ask": [{"price": "101", "idr_volume": "3000000"}],
        }
        source = frames(NOW, 100)
        source.extend([
            {"id": 3, "result": {"recoverable": True, "offset": 40}},
            {"id": 4, "result": {"recoverable": True, "offset": 50}},
            {"result": {"channel": trade_channel("uai_idr"),
                        "data": {"offset": 41, "data": [
                            ["uaiidr", NOW, 5, "buy", 100, "10000", "100"],
                            ["uaiidr", NOW, 6, "sell", 100, "2000", "20"],
                        ]}}},
            {"result": {"channel": book_channel("uai_idr"),
                        "data": {"offset": 51, "data": book}}},
        ])
        provider = FakeWS(source)
        class SlowClock:
            def __init__(self):
                self.clock = 0
            def __call__(self):
                self.clock += 0.1
                return self.clock
        view = collect_ws3_market_summary_once(
            ws_module=provider, monotonic=SlowClock(), epoch_fn=lambda: NOW,
            observe_seconds=3, micro_pairs=("uai_idr",),
        )
        self.assertEqual(len(provider.sock.sent), 4)
        self.assertEqual(provider.sock.sent[2]["params"]["channel"],
                         trade_channel("uai_idr"))
        self.assertEqual(provider.sock.sent[3]["params"]["channel"],
                         book_channel("uai_idr"))
        micro = view["microstructure"][0]
        self.assertEqual(micro["source_buy_count"], 1)
        self.assertEqual(micro["source_sell_count"], 1)
        self.assertEqual(micro["source_buy_idr"], "10000")
        self.assertEqual(micro["book"]["spread_bps"], "99.5025")
        with TemporaryDirectory() as temp:
            path = Path(temp) / "decisions.sqlite3"
            report = persist_ws3_summary_watch(
                DecisionJournal(path), view, now_epoch=NOW,
            )
            self.assertEqual(report["microstructure_pairs_saved"], 1)
            self.assertFalse(report["microstructure_full_trade_tape_verified"])
            self.assertFalse(report["g1_receipt_issuance_allowed"])
            with sqlite3.connect(path) as db:
                rows = db.execute("""
                    SELECT source_buy_count,source_sell_count,
                           spread_bps,trade_gap_observed
                    FROM m03_ws_microstructure_watch_minute
                """).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0:2], (1, 1))
            self.assertEqual(rows[0][2], "99.5025")


    def test_indexed_trade_sample_recovery_and_volume_same_original_socket(self):
        from decimal import Decimal
        from unittest.mock import patch
        from indodax_ai_bot.trade_stream import PublicTradeStreamJournal
        from indodax_ai_bot.m03_ws_microstructure import trade_channel
        from indodax_ai_bot.m05_pitiden_premium_layout import (
            build_timeframes,apply_ws3_trade_samples,format_pitiden_premium,
        )
        end=NOW//60*60+61
        trade_stamp=end//60*60-10
        class SlowClock:
            def __init__(self): self.value=0
            def __call__(self):
                self.value+=.1
                return self.value
        def trade(offset,seq,amt):
            return {"result":{"channel":trade_channel("uai_idr"),"data":{
                "offset":offset,"data":[["uaiidr",trade_stamp,seq,"buy",100.5,amt,"1"]]}}}
        with TemporaryDirectory() as tmp, patch("indodax_ai_bot.trade_stream.time.time",return_value=end):
            path=Path(tmp)/"decisions.sqlite3"
            journal=PublicTradeStreamJournal(path,index_m03_samples=True)
            path.chmod(0o600)
            first=FakeWS(frames(end,100)+[
                {"id":3,"result":{"recoverable":True,"epoch":"123","offset":40}},
                {"id":4,"result":{"recoverable":True,"offset":50}},
                trade(41,5,"100.5")])
            collect_ws3_market_summary_once(ws_module=first,monotonic=SlowClock(),
                epoch_fn=lambda:end,observe_seconds=3,micro_pairs=("uai_idr",),
                trade_journal=journal)
            replay=trade(42,6,"200.5")["result"]["data"]
            second=FakeWS(frames(end,101)+[
                {"id":3,"result":{"recoverable":True,"recovered":True,"epoch":"123",
                                  "offset":42,"publications":[replay]}},
                {"id":4,"result":{"recoverable":True,"offset":50}},
                trade(43,7,"300.5")])
            collect_ws3_market_summary_once(ws_module=second,monotonic=SlowClock(),
                epoch_fn=lambda:end,observe_seconds=3,micro_pairs=("uai_idr",),
                trade_journal=journal)
            self.assertTrue(second.sock.sent[2]["params"]["recover"])
            self.assertEqual(second.sock.sent[2]["params"]["offset"],41)
            self.assertEqual(journal.status("uai_idr")["last_publication_offset"],43)
            candidate={"pair":"uai_idr","observed_epoch":end,
                       "latest_price_idr":"101","volume_24h_idr":"500000000",
                       "reference_epoch":end-60,"observed_rise_pct":"1"}
            rows=apply_ws3_trade_samples(candidate,build_timeframes(candidate,[]),
                                         db_path=path,now_epoch=end)
            self.assertEqual(rows[0]["volume"],Decimal("601.5"))
            self.assertTrue(rows[0]["volume_sampled"])
            self.assertEqual(rows[0]["close_price"],Decimal("100.5"))
            self.assertIsNone(rows[0]["change"])
            self.assertEqual(rows[-1]["volume"],Decimal("500000000"))
            msg=format_pitiden_premium(candidate,[],source_kind="WS3",now_epoch=end,rows=rows)
            self.assertIn("~601,5",msg)
            self.assertIn("observed trades only",msg)
            journal.prune_m03_samples(end)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM m03_ws3_trade_samples").fetchone()[0],3)

    def test_continuous_checkpoint_keeps_one_socket_and_adds_liquid_trade_channel(self):
        from unittest.mock import patch
        from indodax_ai_bot.trade_stream import PublicTradeStreamJournal
        class SlowClock:
            def __init__(self): self.value=0
            def __call__(self):
                self.value+=.1
                return self.value
        incoming=frames(NOW,100)+[
            {"id":3,"result":{"recoverable":True,"epoch":"123","offset":40}},
            {"result":{"channel":"market:trade-activity-btcidr","data":{"offset":41,
              "data":[["btcidr",NOW,5,"buy",100,"10000","100"]]}}}]
        provider=FakeWS(incoming)
        seen=[]
        def checkpoint(snapshot):
            seen.append(snapshot)
            self.assertFalse(provider.sock.closed)
            raise M03WSWatchError("TEST_STOP_AFTER_CHECKPOINT")
        with TemporaryDirectory() as tmp, patch("indodax_ai_bot.trade_stream.time.time",return_value=NOW):
            journal=PublicTradeStreamJournal(Path(tmp)/"decisions.sqlite3",index_m03_samples=True)
            with self.assertRaisesRegex(M03WSWatchError,"TEST_STOP_AFTER_CHECKPOINT"):
                collect_ws3_market_summary_once(ws_module=provider,monotonic=SlowClock(),
                    epoch_fn=lambda:NOW,observe_seconds=3,trade_journal=journal,checkpoint=checkpoint)
            self.assertEqual(seen[0]["indexed_trade_pairs"],["btc_idr"])
            self.assertEqual(seen[0]["indexed_trade_channels_failed"],0)
            self.assertTrue(provider.sock.closed)
            self.assertEqual(journal.status("btc_idr")["last_publication_offset"],41)

    def test_adaptive_trade_channels_expand_above_eight_and_rotate_on_ack(self):
        from unittest.mock import patch
        from indodax_ai_bot.trade_stream import PublicTradeStreamJournal
        class SlowClock:
            def __init__(self): self.value=0
            def __call__(self):
                self.value+=.02
                return self.value
        def summary(pairs,offset):
            return {"result":{"channel":"market:summary-24h","data":{"offset":offset,
                "data":[[p+"idr",NOW,100,90,120,95,str(v),"1"] for p,v in pairs]}}}
        def ack(i): return {"id":i,"result":{"recoverable":True,"epoch":"123","offset":40}}
        pairs=[("t"+str(i),500000000+i) for i in range(12)]
        prefix=frames(NOW,100)[:2]
        with TemporaryDirectory() as tmp:
            journal=PublicTradeStreamJournal(Path(tmp)/"decisions.sqlite3",index_m03_samples=True)
            provider=FakeWS(prefix+[summary(pairs,124)]+[ack(i) for i in range(3,15)])
            seen=[]
            def stop(s):
                seen.append(s)
                raise M03WSWatchError("TEST_STOP")
            with self.assertRaisesRegex(M03WSWatchError,"TEST_STOP"):
                collect_ws3_market_summary_once(ws_module=provider,monotonic=SlowClock(),
                    epoch_fn=lambda:NOW,observe_seconds=3,trade_journal=journal,checkpoint=stop)
            self.assertEqual(len(seen[0]["indexed_trade_pairs"]),12)
            self.assertEqual(seen[0]["indexed_trade_channels_acknowledged"],12)
            self.assertEqual(len(seen[0]["dense_price_rows"]),12)
        with TemporaryDirectory() as tmp, patch("indodax_ai_bot.m03_ws_summary_live_watch._TRADE_CHANNEL_CAP",2):
            journal=PublicTradeStreamJournal(Path(tmp)/"decisions.sqlite3",index_m03_samples=True)
            provider=FakeWS(prefix+[summary([("aa",600000000),("bb",500000000)],124),
                       ack(3),ack(4),summary([("cc",900000000)],125),
                       {"id":5,"result":{}},ack(6)])
            seen=[]
            with self.assertRaisesRegex(M03WSWatchError,"TEST_STOP"):
                collect_ws3_market_summary_once(ws_module=provider,monotonic=SlowClock(),
                    epoch_fn=lambda:NOW,observe_seconds=3,trade_journal=journal,checkpoint=stop)
            self.assertEqual(seen[0]["indexed_trade_pairs"],["aa_idr","cc_idr"])
            self.assertEqual(seen[0]["indexed_trade_channels_acknowledged"],2)
            self.assertEqual(provider.sock.sent[4]["method"],2)
            self.assertEqual(provider.sock.sent[4]["params"]["channel"],"market:trade-activity-bbidr")
            self.assertEqual(provider.sock.sent[5]["params"]["channel"],"market:trade-activity-ccidr")

    def test_dense_summary_and_trade_edge_returns_enrich_original_timeframes(self):
        from decimal import Decimal
        from indodax_ai_bot.m03_ws_summary_live_watch import persist_dense_ws3_prices
        from indodax_ai_bot.trade_stream import PublicTradeStreamJournal
        from indodax_ai_bot.m05_pitiden_premium_layout import (
            build_timeframes,read_price_references,apply_ws3_trade_samples,
        )
        end=NOW//60*60+61
        b=end//60*60
        a=b-60
        with TemporaryDirectory() as tmp:
            db=Path(tmp)/"decisions.sqlite3"
            PublicTradeStreamJournal(db,index_m03_samples=True)
            db.chmod(0o600)
            persist_dense_ws3_prices(db,[("uai_idr",a,a+1,"100",1,a+2),
                  ("uai_idr",b-5,b-2,"110",2,b-1),
                  ("uai_idr",b,b,"999",3,end+10)],now_epoch=end)
            c={"pair":"uai_idr","observed_epoch":end,"latest_price_idr":"110",
               "volume_24h_idr":"500000000"}
            refs=read_price_references(c,db_path=db,now_epoch=end,source_kind="WS3")
            self.assertEqual(refs,[(a+1,Decimal("100")),(b-2,Decimal("110"))])
            rows=build_timeframes(c,refs)
            self.assertEqual(rows[0]["change"],Decimal("10"))
            with sqlite3.connect(db) as con:
                con.executemany("INSERT INTO m03_ws3_trade_samples VALUES(?,?,?,?,?,?)",[
                    ("uai_idr",a+2,1,"100","1000",a+3),
                    ("uai_idr",a+30,2,"115","1150",a+31),
                    ("uai_idr",b-2,3,"112","1120",b-1)])
            out=apply_ws3_trade_samples(c,rows,db_path=db,now_epoch=end)
            self.assertEqual(out[0]["change"],Decimal("12"))
            self.assertEqual(out[0]["observed_high"],Decimal("115"))
            self.assertEqual(out[0]["observed_low"],Decimal("100"))
            self.assertEqual(out[0]["volume"],Decimal("3270"))
            self.assertTrue(out[0]["volume_sampled"])
            self.assertTrue(out[0]["trade_return_sampled"])

    def test_m03_micro_runner_discovers_below_telegram_threshold_and_subscribes(self):
        from indodax_ai_bot.m03_ws_summary_live_watch import (
            run_m03_ws3_summary_watch_once,
        )
        from indodax_ai_bot.m03_ws_microstructure import (
            trade_channel, book_channel,
        )
        from indodax_ai_bot.m05_radar_momentum_nonlive import (
            inspect_original_m03_ws3_watch_readonly,
        )
        class SlowClock:
            def __init__(self):
                self.value = 0
            def __call__(self):
                self.value += 0.1
                return self.value
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("""
                    CREATE TABLE m03_ws_summary_watch_minute(
                        pair TEXT NOT NULL, minute_epoch INTEGER NOT NULL,
                        exchange_epoch INTEGER NOT NULL, last_idr TEXT NOT NULL,
                        rolling_24h_idr TEXT NOT NULL, public_offset INTEGER NOT NULL,
                        PRIMARY KEY(pair,minute_epoch))
                """)
                for minute, price, offset in (
                    (NOW-120, "100", 1), (NOW-60, "100.2", 2)):
                    db.execute(
                        "INSERT INTO m03_ws_summary_watch_minute "
                        "VALUES (?,?,?,?,?,?)",
                        ("uai_idr", minute, minute+40, price, "500000000", offset),
                    )
            path.chmod(0o600)
            no_alert = inspect_original_m03_ws3_watch_readonly(
                db_path=path, now_epoch=NOW,
            )
            self.assertEqual(no_alert["candidates"], [])
            incoming = frames(NOW, 100.4, offset=200)
            incoming.extend([
                {"id": 3, "result": {"recoverable": True, "offset": 40}},
                {"id": 4, "result": {"recoverable": True, "offset": 50}},
                {"result": {"channel": trade_channel("uai_idr"),
                    "data": {"offset": 41, "data": [
                        ["uaiidr", NOW, 12, "buy", "100", "25000", "250"],
                    ]}}},
                {"result": {"channel": book_channel("uai_idr"),
                    "data": {"offset": 51, "data": {
                        "pair": "uaiidr",
                        "bid": [{"price": 100, "idr_volume": "8000000"}],
                        "ask": [{"price": 101, "idr_volume": "4000000"}],
                    }}}},
            ])
            fake = FakeWS(incoming)
            output = run_m03_ws3_summary_watch_once(
                str(path), ws_module=fake, monotonic=SlowClock(),
                now_fn=lambda: NOW, observe_seconds=3, micro_enabled=True,
            )
            self.assertEqual(output["status"], "M03_WS3_SUMMARY_WATCH_ONLY_OK")
            self.assertEqual(output["microstructure_discovery_pool_count"], 1)
            self.assertEqual(output["microstructure_selected_pairs"], ["uai_idr"])
            self.assertEqual(output["microstructure_pairs_selected"], 1)
            self.assertEqual(output["microstructure_pairs_saved"], 1)
            self.assertFalse(output["microstructure_selection_is_telegram_alert"])
            self.assertEqual(fake.sock.sent[2]["params"]["channel"],
                             trade_channel("uai_idr"))
            self.assertEqual(fake.sock.sent[3]["params"]["channel"],
                             book_channel("uai_idr"))
            with sqlite3.connect(path) as db:
                row = db.execute(
                    "SELECT source_buy_count,spread_bps "
                    "FROM m03_ws_microstructure_watch_minute"
                ).fetchone()
            self.assertEqual(row, (1, "99.5025"))


    def test_missing_db_pool_bootstraps_from_live_summary_same_socket_and_sqlite(self):
        """Owner failure regression: no prior archive does NOT suppress micro."""
        from indodax_ai_bot.m03_ws_summary_live_watch import (
            run_m03_ws3_summary_watch_once,
        )
        from indodax_ai_bot.m03_ws_microstructure import (
            trade_channel, book_channel,
        )
        class SlowClock:
            def __init__(self):
                self.n = 0
            def __call__(self):
                self.n += 0.05
                return self.n
        with TemporaryDirectory() as temp:
            path = Path(temp) / "decisions.sqlite3"
            # No summary archive exists before the session.
            source = frames(NOW, 100)
            source.extend([
                {"id": 3, "result": {"recoverable": True, "offset": 17}},
                {"id": 4, "result": {"recoverable": True, "offset": 25}},
                {"id": 5, "result": {"recoverable": True, "offset": 37}},
                {"id": 6, "result": {"recoverable": True, "offset": 48}},
                {"result": {"channel": trade_channel("uai_idr"),
                    "data": {"offset": 38, "data": [
                        ["uaiidr", NOW, 20, "buy", 100, "25000", "250"],
                    ]}}},
                {"result": {"channel": book_channel("uai_idr"),
                    "data": {"offset": 49, "data": {
                        "pair": "uaiidr",
                        "bid": [{"price": 100, "idr_volume": "7000000"}],
                        "ask": [{"price": 101, "idr_volume": "5000000"}],
                    }}}},
            ])
            fake = FakeWS(source)
            report = run_m03_ws3_summary_watch_once(
                str(path), ws_module=fake, monotonic=SlowClock(),
                now_fn=lambda: NOW, observe_seconds=3, micro_enabled=True,
            )
            self.assertEqual(report["status"], "M03_WS3_SUMMARY_WATCH_ONLY_OK")
            self.assertEqual(report["microstructure_pairs_selected"], 2)
            self.assertEqual(report["microstructure_pairs_saved"], 1)
            self.assertEqual(report["microstructure_channels_requested"], 4)
            self.assertEqual(report["microstructure_channels_acknowledged"], 4)
            self.assertEqual(report["microstructure_trade_frames_received"], 1)
            self.assertEqual(report["microstructure_book_snapshots_received"], 1)
            self.assertEqual(report["microstructure_discovery_pool_count"], 2)
            self.assertEqual(report["microstructure_discovery_status"],
                             "M03_MICRO_DISCOVERY_CURRENT_WS3_SELECTED")
            self.assertEqual(report["microstructure_selection_source"],
                             "CURRENT_AUTHENTICATED_WS3_SUMMARY")
            self.assertFalse(report["microstructure_selection_is_telegram_alert"])
            self.assertFalse(report["execution_allowed"])
            channels = [x["params"]["channel"] for x in fake.sock.sent[2:]]
            self.assertCountEqual(channels, [
                trade_channel("uai_idr"), book_channel("uai_idr"),
                trade_channel("btc_idr"), book_channel("btc_idr"),
            ])
            with sqlite3.connect(path) as db:
                saved = db.execute(
                    "SELECT pair,source_buy_count,spread_bps "
                    "FROM m03_ws_microstructure_watch_minute"
                ).fetchall()
            self.assertEqual(saved, [("uai_idr", 1, "99.5025")])

    def test_live_auto_selection_rejects_manual_only_or_thin_pairs(self):
        source = frames(NOW, 100, pair="flokiidr")
        source[2]["result"]["data"]["data"][1][6] = "100000000"
        fake = FakeWS(source)
        capture = collect_ws3_market_summary_once(
            ws_module=fake, monotonic=Clock(), observe_seconds=3,
            epoch_fn=lambda: NOW, micro_autoselect_from_stream=True,
        )
        self.assertEqual(len(fake.sock.sent), 2)
        self.assertEqual(capture["microstructure_selected_pairs"], [])
        self.assertEqual(capture["microstructure_current_liquid_pool_count"], 0)
        self.assertEqual(capture["microstructure_channels_requested"], 0)
        self.assertFalse(capture["microstructure"])

    def test_auto_micro_denied_channels_remain_unverified_and_summary_alive(self):
        with self.assertRaisesRegex(
            M03WSWatchError, "M03_WS_MICRO_AUTO_SCOPE_INVALID",
        ):
            collect_ws3_market_summary_once(
                ws_module=FakeWS(frames(NOW, 100)),
                micro_pairs=("btc_idr",),
                micro_autoselect_from_stream=True,
            )
        source = frames(NOW, 100)
        source.extend(
            {"id": i, "error": {"reason": "denied"}}
            for i in (3, 4, 5, 6)
        )
        snapshot = collect_ws3_market_summary_once(
            ws_module=FakeWS(source), monotonic=Clock(),
            observe_seconds=3, epoch_fn=lambda: NOW,
            micro_autoselect_from_stream=True,
        )
        self.assertEqual(snapshot["frames_observed"], 1)
        self.assertEqual(snapshot["microstructure_channels_requested"], 4)
        self.assertEqual(snapshot["microstructure_channels_acknowledged"], 0)
        self.assertEqual(snapshot["microstructure_trade_frames_received"], 0)
        self.assertEqual(snapshot["microstructure_book_snapshots_received"], 0)
        self.assertEqual(snapshot["microstructure_malformed_pair_count"], 2)
        self.assertTrue(all(x["book"] is None for x in snapshot["microstructure"]))
        self.assertFalse(snapshot["microstructure"][0]["execution_allowed"])

    def test_no_micro_optin_preserves_original_single_summary_subscription(self):
        provider = FakeWS(frames(NOW, 100))
        view = collect_ws3_market_summary_once(
            ws_module=provider, monotonic=Clock(), observe_seconds=3,
        )
        self.assertEqual(len(provider.sock.sent), 2)
        self.assertNotIn("microstructure", view)
        with TemporaryDirectory() as temp:
            db_path = Path(temp) / "decisions.sqlite3"
            persist_ws3_summary_watch(
                DecisionJournal(db_path), view, now_epoch=NOW,
            )
            with sqlite3.connect(db_path) as db:
                exists = db.execute("""
                    SELECT count(*) FROM sqlite_master
                    WHERE type='table'
                    AND name='m03_ws_microstructure_watch_minute'
                """).fetchone()[0]
                self.assertEqual(exists, 0)

    def test_micro_denied_ack_does_not_fake_trade_or_fail_summary(self):
        source = frames(NOW, 100)
        source.extend([
            {"id": 3, "error": {"reason": "denied"}},
            {"id": 4, "error": {"reason": "denied"}},
        ])
        class SlowClock:
            def __init__(self):
                self.clock = 0
            def __call__(self):
                self.clock += 0.1
                return self.clock
        view = collect_ws3_market_summary_once(
            ws_module=FakeWS(source), monotonic=SlowClock(),
            epoch_fn=lambda: NOW, micro_pairs=("uai_idr",),
            observe_seconds=3,
        )
        self.assertEqual(view["frames_observed"], 1)
        row = view["microstructure"][0]
        self.assertTrue(row["source_malformed"])
        self.assertIsNone(row["source_buy_count"])
        self.assertIsNone(row["book"])

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
