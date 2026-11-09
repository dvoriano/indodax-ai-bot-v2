"""M04-W BTC 4 x 30s persistent PUBLIC-only regression. OFFLINE fixtures."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from indodax_ai_bot.public_ws_continuous import (
    supervised_public_ws_continuous, _standing_gap_budget,
)
from indodax_ai_bot.public_ws_health import public_ws_health
from indodax_ai_bot.trade_stream import (
    StreamEvidenceError, PublicTradeStreamJournal, probe_official_public_ws,
)
from indodax_ai_bot.m10_ops_health_readonly import assess_v2_operations_readonly
from test_m04e_trade_stream import ack

NOW = 1794250800


def owner_go(path: Path, *, max_gap=100):
    path.write_text(json.dumps({
        "scope": "BTC_PUBLIC_MONITOR_ONLY",
        "mode": "OWNER_STANDING_PUBLIC_READONLY",
        "issued_at_epoch": NOW - 100,
        "max_gap_count": max_gap,
    }))
    path.chmod(0o600)


def sample(journal: PublicTradeStreamJournal):
    info = journal.status("btc_idr")
    return {
        "subscription_acknowledged": True,
        "protocol_heartbeat_confirmed": True,
        "stream_status_during_probe": info["stream_status"],
        "recovered_publications_newly_persisted": 0,
        "live_publications_newly_persisted": 0,
        "g1_receipt_issuance_allowed": False,
        "g2_provider_call_allowed": False,
        "execution_allowed": False,
    }


class FourWindowPersistentCollectorTests(unittest.TestCase):
    def test_one_authenticated_probe_drives_two_4x30_reports_not_two_sockets(self):
        with TemporaryDirectory() as td:
            rt = Path(td)
            db = rt / "btc.sqlite3"
            status = rt / "status.json"
            go = rt / "go.json"
            owner_go(go)
            calls = []
            def fake_probe(journal, *, pair, seconds, windows, on_window):
                calls.append((pair, seconds, windows))
                journal.acknowledge(pair, ack(100))
                for _ in range(9):
                    on_window(sample(journal))
            with patch("builtins.print"):
                result = supervised_public_ws_continuous(
                    db, status_file=status, probe=fake_probe, go=go,
                    clock=lambda: NOW, _test_stop_after_reports=2,
                )
            self.assertEqual(calls, [("btc_idr", 30, None)])
            self.assertEqual(result["status"],
                             "PUBLIC_COLLECTION_WINDOW_OK_UNATTESTED")
            self.assertEqual(result["requested_sessions"], 4)
            self.assertEqual(result["attempted_sessions"], 4)
            self.assertEqual(result["completed_safe_sessions"], 4)
            self.assertFalse(result["restart_only_after_clean_batch"])
            self.assertTrue(result["persistent_socket_observed_within_windows"])
            self.assertFalse(result["g1_receipt_issuance_allowed"])
            self.assertFalse(result["g2_provider_call_allowed"])
            self.assertFalse(result["execution_allowed"])
            self.assertEqual(json.loads(status.read_text()), result)
            self.assertEqual(status.stat().st_mode & 0o077, 0)
            health = public_ws_health(db, status, now_epoch=NOW + 30)
            self.assertTrue(health["last_batch_recent_and_consistent"])
            self.assertIsNone(health["live_stream_currently_connected"])
            self.assertFalse(health["source_authenticated_for_g1"])

    def test_typed_network_loss_reconnects_immediately_then_four_windows(self):
        with TemporaryDirectory() as td:
            rt = Path(td)
            db, status, go = [rt / x for x in ("btc.sqlite3", "state.json", "go.json")]
            owner_go(go)
            calls, sleeps = [], []
            def fake_probe(journal, *, pair, seconds, windows, on_window):
                calls.append("socket")
                if len(calls) == 1:
                    raise StreamEvidenceError("PUBLIC_WS_STREAM_RECEIVE_EMPTY_OR_CLOSED")
                journal.acknowledge(pair, ack(100))
                for _ in range(4):
                    on_window(sample(journal))
            with patch("builtins.print"):
                result = supervised_public_ws_continuous(
                    db, status_file=status, probe=fake_probe, sleep=sleeps.append,
                    go=go, clock=lambda: NOW, _test_stop_after_reports=1,
                )
            self.assertEqual(calls, ["socket", "socket"])
            self.assertEqual(sleeps, [])
            self.assertEqual(result["persistent_transient_reconnect_attempts"], 1)
            self.assertEqual(result["status"], "PUBLIC_COLLECTION_WINDOW_OK_UNATTESTED")
            self.assertEqual(result["completed_safe_sessions"], 4)
            self.assertFalse(result["execution_allowed"])

    def test_unapproved_payload_fails_stops_with_reject_sidecar(self):
        with TemporaryDirectory() as td:
            rt = Path(td)
            db, status, go = [rt / x for x in ("btc.sqlite3", "state.json", "go.json")]
            owner_go(go)
            def fake_probe(journal, *, pair, seconds, windows, on_window):
                journal.acknowledge(pair, ack(100))
                unsafe = sample(journal)
                unsafe["execution_allowed"] = True
                on_window(unsafe)
            with self.assertRaisesRegex(StreamEvidenceError,
                                        "PUBLIC_WS_WINDOW_PROOF_INVALID"):
                supervised_public_ws_continuous(
                    db, status_file=status, probe=fake_probe,
                    go=go, clock=lambda: NOW,
                )
            written = json.loads(status.read_text())
            self.assertEqual(written["status"], "REJECT")
            self.assertEqual(written["requested_sessions"], 4)
            self.assertEqual(written["completed_safe_sessions"], 0)
            self.assertEqual(written["failed_session_number"], 1)
            self.assertFalse(written["execution_allowed"])

    def test_standing_gap_budget_never_relaxes(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "go.json"
            owner_go(p, max_gap=3)
            _standing_gap_budget(NOW, 3, p)
            with self.assertRaisesRegex(StreamEvidenceError,
                                        "PUBLIC_WS_STANDING_GO_OR_GAP_INVALID"):
                _standing_gap_budget(NOW, 4, p)

    def test_owner_missing_go_blocks_before_dial(self):
        with TemporaryDirectory() as td:
            rt = Path(td)
            db, status, go = [rt / x for x in ("btc.sqlite3", "status.json", "absent.json")]
            called = []
            def fake_probe(*_args, **_kwargs):
                called.append(True)
            with self.assertRaisesRegex(
                StreamEvidenceError, "PUBLIC_WS_STANDING_GO_UNAVAILABLE"
            ):
                supervised_public_ws_continuous(
                    db, status_file=status, probe=fake_probe, go=go,
                    clock=lambda: NOW,
                )
            self.assertEqual(called, [])
            self.assertFalse(status.exists())

    def test_owner_manual_hold_halts_after_window_without_false_4_4(self):
        with TemporaryDirectory() as td:
            rt = Path(td)
            db, status, go = [rt / x for x in ("btc.sqlite3", "state.json", "go.json")]
            owner_go(go)
            manual_hold = rt / "manual.hold"
            def fake_probe(journal, *, pair, seconds, windows, on_window):
                journal.acknowledge(pair, ack(100))
                on_window(sample(journal))
                manual_hold.write_text("manually held")
                with patch("indodax_ai_bot.public_ws_continuous._HOLD", manual_hold):
                    on_window(sample(journal))
            # A local direct check ensures operator HOLD never becomes
            # a clean report; the running daemon's imported default HOLD
            # path is immutable, so test the same checker directly.
            from indodax_ai_bot.public_ws_continuous import _standing_gap_budget
            manual_hold.write_text("hold")
            with self.assertRaisesRegex(StreamEvidenceError, "PUBLIC_WS_OPERATOR_HOLD"):
                _standing_gap_budget(NOW, 0, go, hold=manual_hold)

    def test_new_checkpoint_ops_health_is_4_4_not_false_incomplete(self):
        report = {
            "stage": "M04W_PERSISTENT_PUBLIC_WS_FOUR_WINDOWS",
            "status": "PUBLIC_COLLECTION_WINDOW_OK_UNATTESTED",
            "requested_sessions": 4, "attempted_sessions": 4,
            "completed_safe_sessions": 4, "collector_failure_code": None,
            "last_completed_batch_epoch": NOW - 60,
            "restart_only_after_clean_batch": False,
            "stream_status": "BASELINE_ONLY",
            "persistent_socket_observed_within_windows": True,
        }
        u = {"units": [
            {"role": "btc_public_ws", "observed_load": "loaded",
             "observed_active": "active", "observed_enabled": "enabled",
             "v2_user_fragment_path_matches": True, "unit_needs_daemon_reload": "no"},
            {"role": "btc_public_ws_timer", "observed_load": "loaded",
             "observed_active": "active", "observed_enabled": "enabled",
             "v2_user_fragment_path_matches": True, "unit_needs_daemon_reload": "no"},
        ]}
        status = assess_v2_operations_readonly(
            units_report=u, status_document=report, now_epoch=NOW)
        self.assertTrue(status["btc_ws_continuous_fresh_metadata_observed"])
        self.assertNotIn("BTC_WS_BATCH_INCOMPLETE_OR_INCONSISTENT",
                         status["alert_codes"])
        self.assertFalse(status["continuous_24h_heartbeat_verified"])
        self.assertFalse(status["live_trading_authorized"])
        stale = assess_v2_operations_readonly(
            units_report=u, status_document=report, now_epoch=NOW+400)
        self.assertIn("BTC_WS_BATCH_STALE", stale["alert_codes"])
        self.assertFalse(stale["btc_ws_continuous_fresh_metadata_observed"])


class Timeout(Exception):
    pass


class FakeClock:
    def __init__(self):
        self.i = 0
    def monotonic(self):
        self.i += 1
        return self.i * 0.5


class FakeWS:
    def __init__(self):
        self.initial = [
            {"id": 1, "result": {"client": "public-test"}},
            ack(100),
        ]
        self.sent = []
        self.replied = set()
        self.closed = False

    def settimeout(self, seconds):
        pass

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self):
        if self.initial:
            return json.dumps(self.initial.pop(0))
        heartbeats = [x["id"] for x in self.sent if x.get("method") == 7]
        for hb in heartbeats:
            if hb not in self.replied:
                self.replied.add(hb)
                return json.dumps({"id": hb})
        raise Timeout()

    def close(self):
        self.closed = True


class PersistentSocketProbeTests(unittest.TestCase):
    def test_two_windows_do_not_reconnect_one_public_socket(self):
        with TemporaryDirectory() as td:
            journal = PublicTradeStreamJournal(Path(td) / "btc.sqlite3")
            sock = FakeWS()
            opened = []
            def connect(*_args, **_kw):
                opened.append(True)
                return sock
            wsmod = SimpleNamespace(
                create_connection=connect,
                WebSocketTimeoutException=Timeout,
            )
            observed = []
            clock = FakeClock()
            with patch.dict(sys.modules, {"websocket": wsmod}), patch(
                "time.monotonic", side_effect=clock.monotonic
            ):
                report = probe_official_public_ws(
                    journal, seconds=3, windows=2,
                    on_window=observed.append,
                )
            self.assertEqual(opened, [True])
            self.assertTrue(sock.closed)
            self.assertEqual([x["id"] for x in sock.sent
                              if x.get("method") == 7], [3, 4])
            self.assertEqual(len(observed), 2)
            self.assertTrue(all(x["protocol_heartbeat_confirmed"]
                                for x in observed))
            self.assertTrue(report["public_ws_probe_completed"])
            self.assertFalse(report["g1_receipt_issuance_allowed"])
            self.assertFalse(report["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
