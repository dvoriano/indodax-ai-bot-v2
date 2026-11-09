"""M04-S immediate PUBLIC BTC reconnect, offline only.

A typed transport loss gets exactly one new socket in the SAME bounded
collector invocation, with no extra scheduling delay. Untrusted or repeat
failures never spin or authorize a market/trading receipt.
"""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.public_ws_supervisor import supervised_public_ws_batch
from indodax_ai_bot.trade_stream import (
    PublicTradeStreamJournal, StreamEvidenceError, collect_official_public_ws,
)
from test_m04e_trade_stream import ack


def fresh_completed_session(journal, *, pair):
    # No invented trades. Existing journal alone validates the new ACK/offset.
    acknowledged = journal.acknowledge(pair, ack(100))
    journal.mark_disconnected(pair)
    return {
        **journal.status(pair),
        "stream_status_during_probe": acknowledged["stream_status"],
        "subscription_acknowledged": True,
        "protocol_heartbeat_confirmed": True,
        "resume_requested": False,
        "resume_requested_offset": None,
        "recovery_diagnostic": "OFFLINE_VALID_ACK_ONLY",
        "server_replayed_publication_frames": 0,
        "recovered_publications_newly_persisted": 0,
        "live_publications_newly_persisted": 0,
    }


class M04SImmediateReconnectTests(unittest.TestCase):
    def test_empty_close_immediately_fresh_reconnect_no_timer_wait(self):
        with TemporaryDirectory() as td:
            journal = PublicTradeStreamJournal(Path(td) / "btc.sqlite3")
            calls, sleeps = [], []

            def probe(j, *, pair, seconds):
                calls.append((pair, seconds))
                if len(calls) == 1:
                    j.acknowledge(pair, ack(100))
                    j.mark_disconnected(pair)
                    raise StreamEvidenceError(
                        "PUBLIC_WS_STREAM_RECEIVE_EMPTY_OR_CLOSED"
                    )
                return fresh_completed_session(j, pair=pair)

            result = collect_official_public_ws(
                journal, sessions=2, session_seconds=3, pause_seconds=2,
                probe=probe, sleep=sleeps.append,
            )
            self.assertEqual(len(calls), 3)
            self.assertEqual(sleeps, [2])  # NONE before the immediate retry
            self.assertEqual(result["attempted_sessions"], 2)
            self.assertEqual(result["completed_safe_sessions"], 2)
            self.assertEqual(result["immediate_transient_reconnect_attempts"], 1)
            self.assertEqual(result["status"], "PUBLIC_SAMPLE_COLLECTED_UNATTESTED")
            self.assertEqual(result["total_live_publications_newly_persisted"], 0)
            self.assertFalse(result["full_stream_continuity_proven"])
            self.assertFalse(result["source_authenticated_for_g1"])
            self.assertFalse(result["g1_receipt_issuance_allowed"])
            self.assertFalse(result["g2_provider_call_allowed"])
            self.assertFalse(result["execution_allowed"])
            self.assertEqual(journal.status("btc_idr")["stream_status"],
                             "DISCONNECTED")

    def test_repeat_close_stops_after_one_immediate_reconnect(self):
        with TemporaryDirectory() as td:
            journal = PublicTradeStreamJournal(Path(td) / "btc.sqlite3")
            calls = []

            def probe(j, *, pair, seconds):
                calls.append(True)
                raise StreamEvidenceError(
                    "PUBLIC_WS_STREAM_RECEIVE_CLOSED_OR_LOST"
                )

            result = collect_official_public_ws(
                journal, sessions=10, session_seconds=3, pause_seconds=0,
                probe=probe, sleep=lambda _: self.fail("unexpected delay"),
            )
            self.assertEqual(len(calls), 2)
            self.assertEqual(result["attempted_sessions"], 1)
            self.assertEqual(result["completed_safe_sessions"], 0)
            self.assertEqual(result["immediate_transient_reconnect_attempts"], 1)
            self.assertEqual(
                result["reason"],
                "WS_SESSION_REJECTED:PUBLIC_WS_STREAM_RECEIVE_CLOSED_OR_LOST",
            )
            self.assertEqual(result["status"], "REJECT")
            self.assertFalse(result["execution_allowed"])

    def test_unauthorized_rate_limit_tls_protocol_and_unknown_never_retry(self):
        codes = (
            "PUBLIC_WS_CONNECT_FAILED",  # 401/403/429/unknown handshake
            "PUBLIC_WS_CONNECT_TLS_ERROR",
            "PUBLIC_WS_STREAM_RECEIVE_TLS_ERROR",
            "PUBLIC_WS_STREAM_RECEIVE_PROTOCOL_ERROR",
            "PUBLIC_WS_STREAM_RECEIVE_PAYLOAD_ERROR",
            "PUBLIC_WS_STREAM_RECEIVE_FAILED",
            "PUBLIC_WS_AUTH_REJECTED",
            "PUBLIC_WS_SUBSCRIBE_TIMEOUT",
        )
        for code in codes:
            with self.subTest(code=code), TemporaryDirectory() as td:
                journal = PublicTradeStreamJournal(Path(td) / "btc.sqlite3")
                called = []
                def probe(*_args, **_kw):
                    called.append(True)
                    raise StreamEvidenceError(code)

                result = collect_official_public_ws(
                    journal, sessions=2, session_seconds=3,
                    probe=probe, sleep=lambda _: None,
                )
                self.assertEqual(len(called), 1)
                self.assertEqual(result["immediate_transient_reconnect_attempts"], 0)
                self.assertEqual(result["status"], "REJECT")
                self.assertEqual(result["reason"], "WS_SESSION_REJECTED:" + code)
                self.assertFalse(result["execution_allowed"])

    def test_second_transient_in_same_batch_does_not_start_third_socket(self):
        with TemporaryDirectory() as td:
            journal = PublicTradeStreamJournal(Path(td) / "btc.sqlite3")
            calls = []

            def probe(j, *, pair, seconds):
                calls.append(True)
                if len(calls) in (1, 3):
                    raise StreamEvidenceError(
                        "PUBLIC_WS_CONNECT_CONNECTION_RESET"
                    )
                if len(calls) > 3:
                    self.fail("third reconnect is forbidden")
                return fresh_completed_session(j, pair=pair)

            result = collect_official_public_ws(
                journal, sessions=3, session_seconds=3, pause_seconds=0,
                probe=probe, sleep=lambda _: None,
            )
            self.assertEqual(len(calls), 3)
            self.assertEqual(result["attempted_sessions"], 2)
            self.assertEqual(result["completed_safe_sessions"], 1)
            self.assertEqual(result["immediate_transient_reconnect_attempts"], 1)
            self.assertEqual(result["status"], "REJECT")
            self.assertFalse(result["full_stream_continuity_proven"])

    def test_supervisor_sidecar_records_one_reconnect_without_false_g1(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            sidecar = root / "btc.status.json"
            calls = []
            def probe(j, *, pair, seconds):
                calls.append(True)
                if len(calls) == 1:
                    raise StreamEvidenceError(
                        "PUBLIC_WS_CONNECT_CONNECTION_RESET"
                    )
                return fresh_completed_session(j, pair=pair)

            def collector(j, *, pair, sessions, session_seconds, pause_seconds):
                return collect_official_public_ws(
                    j, pair=pair, sessions=sessions,
                    session_seconds=session_seconds,
                    pause_seconds=pause_seconds,
                    probe=probe, sleep=lambda _: None,
                )

            result = supervised_public_ws_batch(
                root / "btc.sqlite3", sessions=2, seconds=3,
                pause_seconds=0, collector=collector,
                status_file=sidecar, now=lambda: 1800001234,
            )
            self.assertEqual(result["status"],
                             "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED")
            self.assertEqual(result["immediate_transient_reconnect_attempts"], 1)
            self.assertEqual(result["attempted_sessions"], 2)
            self.assertEqual(result["completed_safe_sessions"], 2)
            self.assertFalse(result["full_stream_continuity_proven"])
            self.assertFalse(result["g1_receipt_issuance_allowed"])
            self.assertFalse(result["g2_provider_call_allowed"])
            self.assertFalse(result["execution_allowed"])
            self.assertEqual(json.loads(sidecar.read_text()), result)


if __name__ == "__main__":
    unittest.main()
