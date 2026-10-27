"""M04-R transport failure observability, fail-stop and secret-safe.

Unit fixtures only: no Indodax account, WS network, paid AI, or trade.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from indodax_ai_bot.public_ws_supervisor import supervised_public_ws_batch
from indodax_ai_bot.trade_stream import (
    PUBLIC_TOKEN, PublicTradeStreamJournal, StreamEvidenceError,
    collect_official_public_ws, probe_official_public_ws,
)
from test_m04e_trade_stream import ack


class FakeSocket:
    def __init__(self, messages=(), *, send_fails=False):
        self.messages = list(messages)
        self.send_fails = send_fails
        self.closed = False

    def settimeout(self, _timeout):
        pass

    def send(self, _payload):
        if self.send_fails:
            raise RuntimeError("do-not-log-secret-or-network-body")

    def recv(self):
        if not self.messages:
            raise RuntimeError("do-not-log-secret-or-frame")
        value = self.messages.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.closed = True


AUTH = json.dumps({"id": 1, "result": {"client": "public-client"}})
SUBSCRIBE = json.dumps(ack(100))


class M04RPublicWSFailurePhaseTests(unittest.TestCase):
    def run_failed_session(self, *, socket=None, dial_fails=False, expected):
        with TemporaryDirectory() as td:
            journal = PublicTradeStreamJournal(Path(td) / "public.sqlite3")
            sock = socket
            def connect(*_args, **_kwargs):
                if dial_fails:
                    raise RuntimeError("do-not-log-secret-or-url")
                return sock
            fake_module = SimpleNamespace(
                create_connection=connect,
                WebSocketTimeoutException=type("WebSocketTimeoutException", (Exception,), {}),
            )
            with patch.dict(sys.modules, {"websocket": fake_module}):
                with self.assertRaises(StreamEvidenceError) as caught:
                    probe_official_public_ws(journal, seconds=3)
            self.assertEqual(str(caught.exception), expected)
            self.assertNotIn("do-not-log", str(caught.exception))
            self.assertNotIn(PUBLIC_TOKEN, str(caught.exception))
            self.assertFalse(journal.status("btc_idr")["g1_receipt_issuance_allowed"])
            self.assertFalse(journal.status("btc_idr")["execution_allowed"])
            if sock is not None:
                self.assertTrue(sock.closed)
            return journal.status("btc_idr")["stream_status"]

    def test_dial_exception_has_safe_specific_code(self):
        state = self.run_failed_session(dial_fails=True, expected="PUBLIC_WS_CONNECT_FAILED")
        self.assertEqual(state, "UNSUBSCRIBED")

    def test_auth_send_exception_has_safe_specific_code(self):
        self.run_failed_session(
            socket=FakeSocket(send_fails=True), expected="PUBLIC_WS_AUTH_SEND_FAILED",
        )

    def test_auth_receive_exception_has_safe_specific_code(self):
        self.run_failed_session(
            socket=FakeSocket([RuntimeError("secret=never-print")]),
            expected="PUBLIC_WS_AUTH_RECEIVE_FAILED",
        )

    def test_auth_invalid_json_classified_without_body(self):
        self.run_failed_session(
            socket=FakeSocket(["{bad-public-frame"]),
            expected="PUBLIC_WS_AUTH_DECODE_FAILED",
        )

    def test_subscribe_receive_exception_has_safe_specific_code(self):
        self.run_failed_session(
            socket=FakeSocket([AUTH, RuntimeError("bad-socket")]),
            expected="PUBLIC_WS_SUBSCRIBE_RECEIVE_FAILED",
        )

    def test_stream_receive_exception_preserves_cursor_and_disconnect(self):
        state = self.run_failed_session(
            socket=FakeSocket([AUTH, SUBSCRIBE, RuntimeError("socket-close")]),
            expected="PUBLIC_WS_STREAM_RECEIVE_FAILED",
        )
        self.assertEqual(state, "DISCONNECTED")

    def test_stream_invalid_json_classified_without_frame(self):
        state = self.run_failed_session(
            socket=FakeSocket([AUTH, SUBSCRIBE, "{broken-stream-frame"]),
            expected="PUBLIC_WS_STREAM_DECODE_FAILED",
        )
        self.assertEqual(state, "DISCONNECTED")

    def test_supervisor_sidecar_keeps_code_and_fails_closed(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            db = root / "public.sqlite3"
            sidecar = root / "last-batch.json"
            def failed(journal, **kwargs):
                def broken(*_a, **_kw):
                    raise StreamEvidenceError("PUBLIC_WS_STREAM_RECEIVE_FAILED")
                return collect_official_public_ws(
                    journal, sessions=2, session_seconds=3, pause_seconds=0,
                    probe=broken, sleep=lambda _n: None,
                )
            result = supervised_public_ws_batch(
                db, sessions=2, seconds=3, pause_seconds=0,
                collector=failed, status_file=sidecar,
            )
            self.assertEqual(result["status"], "REJECT")
            self.assertEqual(result["completed_safe_sessions"], 0)
            self.assertEqual(result["failed_session_number"], 1)
            self.assertEqual(result["collector_failure_code"],
                             "WS_SESSION_REJECTED:PUBLIC_WS_STREAM_RECEIVE_FAILED")
            self.assertFalse(result["restart_only_after_clean_batch"])
            self.assertFalse(result["g1_receipt_issuance_allowed"])
            self.assertFalse(result["g2_provider_call_allowed"])
            self.assertFalse(result["execution_allowed"])
            self.assertEqual(json.loads(sidecar.read_text()), result)


if __name__ == "__main__":
    unittest.main()
