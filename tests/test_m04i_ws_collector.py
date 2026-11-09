"""M04-I bounded PUBLIC WS collection: no keys, AI, issuer or order.

Mocked session probes exercise restart recovery against the SAME SQLite journal.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.trade_stream import (
    PublicTradeStreamJournal, StreamEvidenceError, collect_official_public_ws,
)
from test_m04e_trade_stream import ack, pub, replay_pub


def report(journal, *, live=0, recovered=0, heartbeat=True, during="CONTIGUOUS_OBSERVED"):
    state = journal.status("btc_idr")
    return {
        **state,
        "stream_status_during_probe": during,
        "subscription_acknowledged": True,
        "protocol_heartbeat_confirmed": heartbeat,
        "resume_requested": True,
        "resume_requested_offset": 101,
        "recovery_diagnostic": "REPLAY_NEW_PUBLICATIONS_PERSISTED" if recovered
                               else "RESUME_NO_NEW_PUBLICATIONS",
        "server_replayed_publication_frames": recovered,
        "recovered_publications_newly_persisted": recovered,
        "live_publications_newly_persisted": live,
    }


class M04IBoundedCollectorTests(unittest.TestCase):
    def test_two_sessions_one_journal_recovery_and_live_counts(self):
        with TemporaryDirectory() as td:
            j = PublicTradeStreamJournal(Path(td) / "state.sqlite3")
            seen = []
            def probe(journal, *, pair, seconds):
                self.assertEqual(pair, "btc_idr")
                self.assertEqual(seconds, 3)
                seen.append(journal)
                if len(seen) == 1:
                    journal.acknowledge(pair, ack(100))
                    journal.ingest(pair, pub(101))
                    journal.mark_disconnected(pair)
                    return report(journal, live=1)
                self.assertTrue(journal.subscription(pair)["params"]["recover"])
                journal.acknowledge(pair, ack(102, publications=[replay_pub(102)]))
                journal.ingest(pair, pub(103))
                journal.mark_disconnected(pair)
                return report(journal, recovered=1, live=1)
            sleeps=[]
            result=collect_official_public_ws(
                j, sessions=2, session_seconds=3, pause_seconds=1,
                probe=probe, sleep=lambda x:sleeps.append(x))
            self.assertEqual(result["status"],"PUBLIC_SAMPLE_COLLECTED_UNATTESTED")
            self.assertEqual(result["completed_safe_sessions"],2)
            self.assertEqual(result["attempted_sessions"],2)
            self.assertEqual(result["total_recovered_publications_newly_persisted"],1)
            self.assertEqual(result["total_live_publications_newly_persisted"],2)
            self.assertEqual(result["last_publication_offset"],103)
            self.assertEqual(result["persisted_publications"],3)
            self.assertEqual(len(result["session_reports"]),2)
            self.assertEqual(sleeps,[1])
            self.assertIs(seen[0],seen[1])
            self.assertFalse(result["g1_receipt_issuance_allowed"])
            self.assertFalse(result["g2_provider_call_allowed"])
            self.assertFalse(result["execution_allowed"])

    def test_missing_heartbeat_stops_before_reconnect(self):
        with TemporaryDirectory() as td:
            j=PublicTradeStreamJournal(Path(td)/"state.sqlite3")
            called=[]
            def probe(journal, *, pair, seconds):
                called.append(True)
                return report(journal,heartbeat=False)
            result=collect_official_public_ws(
                j,sessions=4,session_seconds=3,probe=probe,sleep=lambda _:None)
            self.assertEqual(len(called),1)
            self.assertEqual(result["reason"],"WS_HEARTBEAT_NOT_CONFIRMED")
            self.assertEqual(result["completed_safe_sessions"],0)
            self.assertEqual(result["attempted_sessions"],1)
            self.assertEqual(result["status"],"REJECT")

    def test_gap_status_stops_and_never_promotes_caller(self):
        with TemporaryDirectory() as td:
            j=PublicTradeStreamJournal(Path(td)/"state.sqlite3")
            def probe(journal, *, pair, seconds):
                return report(journal,during="GAP")
            result=collect_official_public_ws(j,sessions=2,session_seconds=3,
                                               probe=probe,sleep=lambda _:None)
            self.assertEqual(result["reason"],"WS_CONTINUITY_OR_DISCONNECT_REJECTED")
            self.assertFalse(result["full_closed_minute_coverage_proven"])
            self.assertEqual(result["completed_safe_sessions"],0)
            self.assertFalse(result["execution_allowed"])

    def test_probe_error_reports_no_extra_successful_sessions(self):
        with TemporaryDirectory() as td:
            j=PublicTradeStreamJournal(Path(td)/"state.sqlite3")
            call=[]
            def probe(journal, *, pair, seconds):
                call.append(1)
                if len(call)==1:
                    journal.acknowledge(pair, ack(100))
                    journal.mark_disconnected(pair)
                    return report(journal)
                raise StreamEvidenceError("PUBLIC_WS_CONNECTION_OR_FRAME_FAILED")
            result=collect_official_public_ws(
                j,sessions=4,session_seconds=3,probe=probe,sleep=lambda _:None)
            self.assertEqual(result["attempted_sessions"],2)
            self.assertEqual(result["completed_safe_sessions"],1)
            self.assertEqual(result["reason"],
                             "WS_SESSION_REJECTED:PUBLIC_WS_CONNECTION_OR_FRAME_FAILED")
            self.assertEqual(result["status"],"REJECT")

    def test_typed_close_immediately_resubscribes_same_journal_once(self):
        with TemporaryDirectory() as td:
            j = PublicTradeStreamJournal(Path(td) / "btc.sqlite3")
            calls, sleeps = [], []
            def probe(journal, *, pair, seconds):
                calls.append(journal)
                if len(calls) == 1:
                    journal.acknowledge(pair, ack(100))
                    journal.mark_disconnected(pair)
                    raise StreamEvidenceError(
                        "PUBLIC_WS_STREAM_RECEIVE_CLOSED_OR_LOST")
                self.assertTrue(journal.subscription(pair)["params"]["recover"])
                if len(calls) == 2:
                    journal.acknowledge(pair, ack(101,
                        publications=[replay_pub(101)]))
                    journal.ingest(pair, pub(102))
                    journal.mark_disconnected(pair)
                    return report(journal, live=1, recovered=1)
                journal.acknowledge(pair, ack(102))
                journal.mark_disconnected(pair)
                return report(journal)
            result = collect_official_public_ws(
                j, sessions=2, session_seconds=3, pause_seconds=1,
                probe=probe, sleep=lambda s: sleeps.append(s))
            self.assertEqual(len(calls), 3)
            self.assertTrue(all(x is j for x in calls))
            self.assertEqual(sleeps, [1])  # No sleep before reconnect.
            self.assertEqual(result["immediate_transport_reconnect_attempts"], 1)
            self.assertEqual(result["completed_safe_sessions"], 2)
            self.assertEqual(result["attempted_sessions"], 2)
            self.assertEqual(result["status"], "PUBLIC_SAMPLE_COLLECTED_UNATTESTED")
            self.assertEqual(result["total_live_publications_newly_persisted"], 1)
            self.assertEqual(result["total_recovered_publications_newly_persisted"], 1)
            self.assertFalse(result["source_authenticated_for_g1"])
            self.assertFalse(result["execution_allowed"])

    def test_immediate_retry_failure_is_reject_and_no_repeat_loop(self):
        with TemporaryDirectory() as td:
            j = PublicTradeStreamJournal(Path(td) / "btc.sqlite3")
            calls, sleeps = [], []
            def probe(journal, *, pair, seconds):
                calls.append(True)
                raise StreamEvidenceError("PUBLIC_WS_STREAM_RECEIVE_EOF")
            result = collect_official_public_ws(
                j, sessions=10, session_seconds=3, probe=probe,
                sleep=lambda s: sleeps.append(s))
            self.assertEqual(len(calls), 2)
            self.assertEqual(sleeps, [])
            self.assertEqual(result["completed_safe_sessions"], 0)
            self.assertEqual(result["attempted_sessions"], 1)
            self.assertEqual(result["immediate_transport_reconnect_attempts"], 1)
            self.assertEqual(result["reason"],
                "WS_SESSION_REJECTED:PUBLIC_WS_STREAM_RECEIVE_EOF")
            self.assertEqual(result["status"], "REJECT")
            self.assertFalse(result["execution_allowed"])

    def test_denials_protocol_and_unknown_never_reconnect(self):
        for code in ("PUBLIC_WS_CONNECT_FAILED",
                     "PUBLIC_WS_STREAM_RECEIVE_PROTOCOL_ERROR",
                     "PUBLIC_WS_STREAM_RECEIVE_TLS_ERROR",
                     "PUBLIC_WS_AUTH_REJECTED",
                     "PUBLIC_WS_STREAM_RECEIVE_FAILED"):
            with self.subTest(code=code), TemporaryDirectory() as td:
                j = PublicTradeStreamJournal(Path(td) / "btc.sqlite3")
                calls = []
                def probe(_journal, *, pair, seconds):
                    calls.append(True)
                    raise StreamEvidenceError(code)
                result = collect_official_public_ws(
                    j, sessions=2, session_seconds=3,
                    probe=probe, sleep=lambda _:None)
                self.assertEqual(len(calls), 1)
                self.assertEqual(result["immediate_transport_reconnect_attempts"], 0)
                self.assertEqual(result["status"], "REJECT")

    def test_invalid_collector_config_cannot_start_any_session(self):
        with TemporaryDirectory() as td:
            j=PublicTradeStreamJournal(Path(td)/"state.sqlite3")
            for kw in ({"sessions":1},{"sessions":11},{"session_seconds":31},
                       {"pause_seconds":-1},{"sessions":True},
                       {"session_seconds":False}):
                with self.subTest(kw=kw):
                    with self.assertRaisesRegex(StreamEvidenceError,
                                                "INVALID_PUBLIC_WS_COLLECTOR_BOUNDS"):
                        collect_official_public_ws(j, probe=lambda *_a,**_k:None, **kw)
            self.assertEqual(j.status("btc_idr")["stream_status"],"UNSUBSCRIBED")

    def test_cli_single_mainpath_mock_collection_still_final_reject(self):
        fake = {"status":"PUBLIC_SAMPLE_COLLECTED_UNATTESTED",
                "reason":"PUBLIC_WS_SAMPLE_IS_NOT_CERTIFIED_TAPE",
                "completed_safe_sessions":2,"g1_receipt_issuance_allowed":False,
                "g2_provider_call_allowed":False,"execution_allowed":False}
        with TemporaryDirectory() as td:
            stdout=StringIO()
            with patch("indodax_ai_bot.orchestrator.collect_official_public_ws",
                       return_value=fake) as collector:
                with redirect_stdout(stdout):
                    rc=main(["--public-ws-collect","--db",str(Path(td)/"journal.sqlite3"),
                             "--pair","btc_idr","--stream-seconds","3",
                             "--collector-sessions","2","--collector-pause-seconds","0"])
            obj=json.loads(stdout.getvalue())
            self.assertEqual(rc,0)
            self.assertEqual(obj["final_decision"],"REJECT")
            self.assertFalse(obj["execution_allowed"])
            self.assertFalse(obj["live_trading_authorized"])
            self.assertEqual(obj["public_ws"]["completed_safe_sessions"],2)
            collector.assert_called_once()

    def test_cli_failed_public_collector_reports_reject_and_nonzero(self):
        fake={"status":"REJECT","reason":"WS_HEARTBEAT_NOT_CONFIRMED",
              "g1_receipt_issuance_allowed":False,
              "g2_provider_call_allowed":False,"execution_allowed":False}
        with TemporaryDirectory() as td:
            stdout=StringIO()
            with patch("indodax_ai_bot.orchestrator.collect_official_public_ws",
                       return_value=fake):
                with redirect_stdout(stdout):
                    rc=main(["--public-ws-collect","--db",str(Path(td)/"journal.sqlite3"),
                             "--stream-seconds","3"])
            obj=json.loads(stdout.getvalue())
            self.assertEqual(rc,3)
            self.assertEqual(obj["final_decision"],"REJECT")
            self.assertFalse(obj["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
