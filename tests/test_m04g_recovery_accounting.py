"""M04-G: separate PUBLIC WebSocket replay publications from fresh live frames.

All frames synthetic. No paid AI, private Indodax API, signed source or order.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from indodax_ai_bot.trade_stream import (
    PublicTradeStreamJournal, StreamEvidenceError, probe_official_public_ws,
)
from test_m04e_trade_stream import ack, pub, replay_pub
from test_m04f_ws_heartbeat import FakeClock, FakeSocket, Timeout


def offline_session(journal, *, acknowledgement, live=None, heartbeat=True):
    fake_ws = FakeSocket(heartbeat=heartbeat)
    fake_ws.messages = [
        {"id": 1, "result": {"client": "test-only-public-client"}},
        acknowledgement,
    ]
    if heartbeat:
        fake_ws.messages.append({"id": 3})
    fake_ws.messages.extend(live or [])
    mock_websocket = SimpleNamespace(
        create_connection=lambda *args, **kwargs: fake_ws,
        WebSocketTimeoutException=Timeout,
    )
    clock = FakeClock()
    with patch.dict(sys.modules, {"websocket": mock_websocket}):
        with patch("time.monotonic", side_effect=clock.monotonic):
            result = probe_official_public_ws(journal, pair="btc_idr", seconds=3)
    return result, fake_ws


class M04GRecoveryAccountingTests(unittest.TestCase):
    def test_first_live_session_has_zero_recovery_and_one_new_publication(self):
        with TemporaryDirectory() as td:
            journal = PublicTradeStreamJournal(Path(td)/"state.sqlite3")
            data, sock = offline_session(journal, acknowledgement=ack(100),
                                         live=[pub(101)])
            self.assertFalse(data["resume_requested"])
            self.assertIsNone(data["resume_requested_offset"])
            self.assertEqual(data["recovery_diagnostic"], "INITIAL_SUBSCRIPTION")
            self.assertEqual(data["server_replayed_publication_frames"], 0)
            self.assertEqual(data["recovered_publications_newly_persisted"], 0)
            self.assertEqual(data["live_publications_newly_persisted"], 1)
            self.assertEqual(data["persisted_publications"], 1)
            self.assertEqual(data["server_ack_offset"], 100)
            self.assertTrue(data["subscription_acknowledged"])
            self.assertEqual(data["stream_status"], "DISCONNECTED")
            self.assertFalse(data["g1_receipt_issuance_allowed"])
            self.assertTrue(sock.closed)

    def test_restart_counts_inclusive_replay_separately_from_new_live_data(self):
        with TemporaryDirectory() as td:
            path = Path(td)/"state.sqlite3"
            journal = PublicTradeStreamJournal(path)
            first, _ = offline_session(journal, acknowledgement=ack(100),
                                       live=[pub(101)])
            self.assertEqual(first["persisted_publications"], 1)
            restarted = PublicTradeStreamJournal(path)
            second, sock = offline_session(
                restarted,
                acknowledgement=ack(
                    103, publications=[replay_pub(101), replay_pub(102), replay_pub(103)]),
                live=[pub(104)],
            )
            self.assertEqual(second["resume_requested_offset"], 101)
            self.assertTrue(second["resume_requested"])
            self.assertEqual(second["server_replayed_publication_frames"], 3)
            self.assertEqual(second["recovered_publications_newly_persisted"], 2)
            self.assertEqual(second["live_publications_newly_persisted"], 1)
            self.assertEqual(second["received_publications_in_this_probe"], 1)
            self.assertEqual(second["publications_persisted_before_subscription"], 1)
            self.assertEqual(second["publications_persisted_after_subscription"], 3)
            self.assertEqual(second["persisted_publications"], 4)
            self.assertEqual(second["last_publication_offset"], 104)
            self.assertEqual(second["recovery_diagnostic"],
                             "REPLAY_NEW_PUBLICATIONS_PERSISTED")
            self.assertEqual(second["stream_status_during_probe"], "CONTIGUOUS_OBSERVED")
            self.assertEqual(second["stream_status"], "DISCONNECTED")
            self.assertTrue(second["protocol_heartbeat_confirmed"])
            self.assertFalse(second["g2_provider_call_allowed"])
            self.assertFalse(second["execution_allowed"])
            self.assertEqual(sock.sent[1]["params"]["recover"], True)
            self.assertEqual(sock.sent[1]["params"]["offset"], 101)

    def test_resume_without_new_publications_distinguished_from_trade_delivery(self):
        with TemporaryDirectory() as td:
            journal = PublicTradeStreamJournal(Path(td)/"state.sqlite3")
            offline_session(journal, acknowledgement=ack(100), live=[pub(101)])
            second, _ = offline_session(journal, acknowledgement=ack(101), live=[])
            self.assertTrue(second["resume_requested"])
            self.assertEqual(second["recovered_publications_newly_persisted"], 0)
            self.assertEqual(second["live_publications_newly_persisted"], 0)
            self.assertEqual(second["recovery_diagnostic"], "RESUME_NO_NEW_PUBLICATIONS")
            self.assertEqual(second["ws_activity_diagnostic"], "QUIET_CHANNEL_PROTOCOL_ALIVE")
            self.assertEqual(second["persisted_publications"], 1)
            self.assertFalse(second["indodax_trade_tape_completeness_proven"])

    def test_resume_with_boundary_only_is_not_a_new_trade(self):
        with TemporaryDirectory() as td:
            journal = PublicTradeStreamJournal(Path(td)/"state.sqlite3")
            offline_session(journal, acknowledgement=ack(100), live=[pub(101)])
            second, _ = offline_session(journal, acknowledgement=ack(
                101, publications=[replay_pub(101)]))
            self.assertEqual(second["server_replayed_publication_frames"], 1)
            self.assertEqual(second["recovered_publications_newly_persisted"], 0)
            self.assertEqual(second["recovery_diagnostic"], "REPLAY_BOUNDARY_ONLY")
            self.assertEqual(second["persisted_publications"], 1)
            self.assertFalse(second["source_authenticated_for_g1"])

    def test_recover_gap_never_claims_contiguous_source_or_issuer(self):
        # M04-P/R: once a GAP has been durably recorded, subscription
        # must request a *fresh discontinuous* baseline. Server replay in
        # that fresh ACK cannot be treated as a valid resumed history.
        with TemporaryDirectory() as td:
            journal = PublicTradeStreamJournal(Path(td)/"state.sqlite3")
            journal.acknowledge("btc_idr", ack(100))
            journal.ingest("btc_idr", pub(104))
            self.assertEqual(journal.status("btc_idr")["stream_status"], "GAP")
            self.assertNotIn("recover", journal.subscription("btc_idr")["params"])
            with self.assertRaisesRegex(
                StreamEvidenceError, "^WS_FRESH_BASELINE_CANNOT_REPLAY$"
            ):
                offline_session(
                    journal,
                    acknowledgement=ack(104, publications=[replay_pub(101)]),
                )
            state = journal.status("btc_idr")
            self.assertEqual(state["stream_status"], "GAP")
            self.assertEqual(state["last_publication_offset"], 100)
            self.assertEqual(state["persisted_publications"], 0)
            self.assertEqual(state["unrecoverable_gap_rebaseline_count"], 0)
            self.assertFalse(state["observed_stream_continuity"])
            self.assertFalse(state["source_authenticated_for_g1"])
            self.assertFalse(state["g1_receipt_issuance_allowed"])
            self.assertFalse(state["g2_provider_call_allowed"])
            self.assertFalse(state["execution_allowed"])

    def test_recovery_counts_do_not_depend_on_server_offset_distance_alone(self):
        # Replay 101(boundary),102,104 skips 103. M04-P/R intentionally
        # FAILS the entire probe, keeps 102 as the last proven row and
        # persists GAP; it never returns a normal "recovery" report.
        with TemporaryDirectory() as td:
            journal = PublicTradeStreamJournal(Path(td)/"state.sqlite3")
            offline_session(journal, acknowledgement=ack(100), live=[pub(101)])
            with self.assertRaisesRegex(
                StreamEvidenceError, "^WS_RECOVERY_GAP_UNRESOLVED$"
            ):
                offline_session(journal, acknowledgement=ack(
                    104, publications=[
                        replay_pub(101), replay_pub(102), replay_pub(104),
                    ]))
            state = journal.status("btc_idr")
            self.assertEqual(state["stream_status"], "GAP")
            self.assertEqual(state["last_publication_offset"], 102)
            self.assertEqual(state["persisted_publications"], 2)
            self.assertEqual(state["unrecoverable_gap_rebaseline_count"], 0)
            self.assertFalse(state["observed_stream_continuity"])
            self.assertFalse(state["source_authenticated_for_g1"])
            self.assertFalse(state["indodax_trade_tape_completeness_proven"])
            self.assertFalse(state["g1_receipt_issuance_allowed"])
            self.assertFalse(state["g2_provider_call_allowed"])
            self.assertFalse(state["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
