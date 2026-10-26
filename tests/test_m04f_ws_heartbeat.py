"""M04-F official inclusive replay and public protocol heartbeat, all synthetic.

No Indodax private API, model calls, execution, source issuer or orders.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace
import json
import sys
import unittest

from indodax_ai_bot.trade_stream import (
    PublicTradeStreamJournal, StreamEvidenceError, probe_official_public_ws,
)
from test_m04e_trade_stream import ack, pub, replay_pub


class FakeClock:
    def __init__(self):
        self.ticks = 0

    def monotonic(self):
        self.ticks += 1
        return self.ticks * 0.5


class Timeout(Exception):
    pass


class FakeSocket:
    def __init__(self, *, trade=False, heartbeat=True):
        self.messages = [
            {"id": 1, "result": {"client": "fake-client"}},
            ack(),
        ]
        if heartbeat:
            self.messages.append({"id": 3})
        if trade:
            self.messages.append(pub(101))
        self.sent = []
        self.closed = False

    def settimeout(self, seconds):
        self.timeout = seconds

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self):
        if self.messages:
            return json.dumps(self.messages.pop(0))
        raise Timeout()

    def close(self):
        self.closed = True


class M04FWSProtocolTests(unittest.TestCase):
    def test_recovery_can_repeat_requested_unstored_initial_baseline(self):
        with TemporaryDirectory() as td:
            j = PublicTradeStreamJournal(Path(td) / "ws.sqlite3")
            j.acknowledge("btc_idr", ack(100))
            j.mark_disconnected("btc_idr")
            out = j.acknowledge("btc_idr", ack(101, publications=[
                replay_pub(100), replay_pub(101),
            ]))
            self.assertEqual(out["last_publication_offset"], 101)
            self.assertEqual(out["persisted_publications"], 1)
            self.assertTrue(out["observed_stream_continuity"])
            self.assertFalse(out["indodax_trade_tape_completeness_proven"])

    def test_replayed_committed_boundary_must_have_identical_contents(self):
        with TemporaryDirectory() as td:
            j = PublicTradeStreamJournal(Path(td) / "ws.sqlite3")
            j.acknowledge("btc_idr", ack(100))
            j.ingest("btc_idr", pub(101))
            j.mark_disconnected("btc_idr")
            changed = replay_pub(101)
            changed["data"][0][5] = "9999"
            with self.assertRaisesRegex(StreamEvidenceError, "WS_RESUME_BOUNDARY_COLLISION"):
                j.acknowledge("btc_idr", ack(102, publications=[
                    changed, replay_pub(102),
                ]))
            self.assertEqual(j.status("btc_idr")["last_publication_offset"], 101)
            self.assertEqual(j.read_count("btc_idr"), 1)

    def test_disconnected_stale_frame_cannot_advance_until_new_ack(self):
        with TemporaryDirectory() as td:
            j = PublicTradeStreamJournal(Path(td) / "ws.sqlite3")
            j.acknowledge("btc_idr", ack(100))
            j.mark_disconnected("btc_idr")
            with self.assertRaisesRegex(StreamEvidenceError, "WS_STREAM_GAP_OR_EPOCH_CHANGED"):
                j.ingest("btc_idr", pub(101))
            self.assertEqual(j.status("btc_idr")["last_publication_offset"], 100)
            status = j.acknowledge("btc_idr", ack(100, publications=[]))
            self.assertEqual(status["stream_status"], "BASELINE_ONLY")
            status = j.ingest("btc_idr", pub(101))
            self.assertEqual(status["last_publication_offset"], 101)

    def test_gap_without_replay_never_claims_healthy_on_reconnect(self):
        with TemporaryDirectory() as td:
            j = PublicTradeStreamJournal(Path(td) / "ws.sqlite3")
            j.acknowledge("btc_idr", ack(100))
            j.ingest("btc_idr", pub(103))
            self.assertEqual(j.status("btc_idr")["stream_status"], "GAP")
            again = j.acknowledge("btc_idr", ack(103, publications=[]))
            self.assertEqual(again["stream_status"], "GAP")
            self.assertFalse(again["observed_stream_continuity"])
            self.assertFalse(again["g1_receipt_issuance_allowed"])

    def _probe_fake(self, *, trade=False, heartbeat=True):
        sock = FakeSocket(trade=trade, heartbeat=heartbeat)
        clock = FakeClock()
        with TemporaryDirectory() as td:
            journal = PublicTradeStreamJournal(Path(td) / "ws.sqlite3")
            fake = SimpleNamespace(
                create_connection=lambda *args, **kwargs: sock,
                WebSocketTimeoutException=Timeout,
            )
            with patch.dict(sys.modules, {"websocket": fake}):
                with patch("time.monotonic", side_effect=clock.monotonic):
                    outcome = probe_official_public_ws(journal, seconds=3)
            state = journal.status("btc_idr")
        return sock, outcome, state

    def test_quiet_channel_heartbeat_ack_proves_protocol_alive_not_trade_tape(self):
        sock, data, state = self._probe_fake()
        self.assertTrue(data["public_ws_probe_completed"])
        self.assertTrue(data["subscription_acknowledged"])
        self.assertTrue(data["protocol_heartbeat_confirmed"])
        self.assertEqual(data["received_publications_in_this_probe"], 0)
        self.assertEqual(data["ws_activity_diagnostic"], "QUIET_CHANNEL_PROTOCOL_ALIVE")
        self.assertEqual(data["stream_status_during_probe"], "BASELINE_ONLY")
        self.assertEqual(data["stream_status"], "DISCONNECTED")
        self.assertTrue(any(item.get("method") == 7 and item.get("id") == 3
                            for item in sock.sent))
        self.assertTrue(sock.closed)
        self.assertEqual(state["stream_status"], "DISCONNECTED")
        self.assertFalse(data["g1_receipt_issuance_allowed"])
        self.assertFalse(data["g2_provider_call_allowed"])
        self.assertFalse(data["execution_allowed"])

    def test_market_publication_detected_without_promoting_g1_or_ai(self):
        sock, data, state = self._probe_fake(trade=True)
        self.assertEqual(data["received_publications_in_this_probe"], 1)
        self.assertEqual(data["persisted_publications"], 1)
        self.assertEqual(data["ws_activity_diagnostic"], "PUBLIC_TRADE_FRAMES_RECEIVED")
        self.assertFalse(data["source_authenticated_for_g1"])
        self.assertFalse(data["indodax_trade_tape_completeness_proven"])
        self.assertEqual(state["last_publication_offset"], 101)

    def test_no_trade_without_pong_is_explicitly_unconfirmed(self):
        sock, data, state = self._probe_fake(heartbeat=False)
        self.assertEqual(data["received_publications_in_this_probe"], 0)
        self.assertFalse(data["protocol_heartbeat_confirmed"])
        self.assertEqual(data["ws_activity_diagnostic"],
                         "NO_TRADE_FRAMES_HEARTBEAT_UNCONFIRMED")
        self.assertFalse(state["g1_receipt_issuance_allowed"])


if __name__ == "__main__":
    unittest.main()
