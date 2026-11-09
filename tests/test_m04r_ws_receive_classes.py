"""M04-R actual receive-failure class diagnostics, same V2 collector and sidecar.

No real WebSocket/network, paid AI, signed account, live trading, DB resets
or source trust promotion. Never expose raw socket exception strings.
"""
from __future__ import annotations

import json
import ssl
import sys
from pathlib import Path
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
    def __init__(self, messages):
        self.messages=list(messages)
        self.closed=False

    def settimeout(self, _n):
        pass

    def send(self, _payload):
        pass

    def recv(self):
        if not self.messages:
            raise RuntimeError("private-contact-token-never-log")
        x=self.messages.pop(0)
        if isinstance(x, BaseException):
            raise x
        return x

    def close(self):
        self.closed=True


AUTH=json.dumps({"id":1,"result":{"client":"test-only-client"}})
SUBSCRIBE=json.dumps(ack(100))
class ClosedException(Exception): pass
class ProtocolException(Exception): pass
class PayloadException(Exception): pass
class TimeoutException(Exception): pass


def module_for(sock):
    return SimpleNamespace(
        create_connection=lambda *_a, **_k:sock,
        WebSocketConnectionClosedException=ClosedException,
        WebSocketProtocolException=ProtocolException,
        WebSocketPayloadException=PayloadException,
        WebSocketTimeoutException=TimeoutException,
    )


class M04RReceiveFailureClassTests(unittest.TestCase):
    def check_code(self, exception, expected):
        with TemporaryDirectory() as td:
            journal=PublicTradeStreamJournal(Path(td)/"btc.sqlite3")
            sock=FakeSocket([AUTH,SUBSCRIBE,exception])
            with patch.dict(sys.modules, {"websocket":module_for(sock)}):
                with self.assertRaises(StreamEvidenceError) as caught:
                    probe_official_public_ws(journal,seconds=3)
            self.assertEqual(str(caught.exception),expected)
            self.assertNotIn("private-",str(caught.exception))
            self.assertNotIn(PUBLIC_TOKEN,str(caught.exception))
            self.assertTrue(sock.closed)
            state=journal.status("btc_idr")
            self.assertEqual(state["stream_status"],"DISCONNECTED")
            self.assertFalse(state["indodax_trade_tape_completeness_proven"])
            self.assertFalse(state["g1_receipt_issuance_allowed"])
            self.assertFalse(state["g2_provider_call_allowed"])
            self.assertFalse(state["execution_allowed"])

    def test_closed_or_lost_connection_distinct_not_assumed_server_close(self):
        self.check_code(
            ClosedException("private-peer-or-network-body"),
            "PUBLIC_WS_STREAM_RECEIVE_CLOSED_OR_LOST",
        )

    def test_connection_reset_and_abort_distinguished(self):
        self.check_code(
            ConnectionResetError("private-reset-error"),
            "PUBLIC_WS_STREAM_RECEIVE_CONNECTION_RESET",
        )
        self.check_code(
            ConnectionAbortedError("private-aborted-error"),
            "PUBLIC_WS_STREAM_RECEIVE_CONNECTION_ABORTED",
        )

    def test_tls_broken_pipe_eof_oserror_classified(self):
        for exc,code in (
            (ssl.SSLError("private-cert-issue"),"TLS_ERROR"),
            (BrokenPipeError("private-pipe"),"BROKEN_PIPE"),
            (EOFError("private-end"),"EOF"),
            (OSError("private-system-error"),"OS_ERROR"),
        ):
            with self.subTest(code=code):
                self.check_code(exc,"PUBLIC_WS_STREAM_RECEIVE_"+code)

    def test_ws_protocol_payload_errors_not_misdiagnosed_as_transport(self):
        self.check_code(
            ProtocolException("private-frame-body"),
            "PUBLIC_WS_STREAM_RECEIVE_PROTOCOL_ERROR",
        )
        self.check_code(
            PayloadException("private-payload-body"),
            "PUBLIC_WS_STREAM_RECEIVE_PAYLOAD_ERROR",
        )

    def test_unknown_exception_preserves_existing_generic_safe_code(self):
        self.check_code(
            RuntimeError("private-unknown-body"),
            "PUBLIC_WS_STREAM_RECEIVE_FAILED",
        )

    def test_empty_return_is_not_valid_trade_or_decode(self):
        self.check_code("", "PUBLIC_WS_STREAM_RECEIVE_EMPTY_OR_CLOSED")
        self.check_code(None, "PUBLIC_WS_STREAM_RECEIVE_EMPTY_OR_CLOSED")

    def test_subtype_survives_supervisor_and_failstops_after_one_fast_retry(self):
        with TemporaryDirectory() as td:
            root=Path(td)
            db=root/"public.sqlite3"
            sidecar=root/"batch.json"
            sockets=[FakeSocket([AUTH,SUBSCRIBE,ClosedException("private-no-logging")])
                     for _ in range(2)]
            called=[]
            def connect(*_args,**_kwargs):
                called.append(1)
                return sockets[len(called)-1]
            fake=module_for(sockets[0])
            fake.create_connection=connect
            def collector(journal, **kwargs):
                return collect_official_public_ws(
                    journal, pair=kwargs["pair"],
                    sessions=kwargs["sessions"],
                    session_seconds=kwargs["session_seconds"],
                    pause_seconds=0,
                    probe=probe_official_public_ws,
                    sleep=lambda _:None,
                )
            with patch.dict(sys.modules,{"websocket":fake}):
                result=supervised_public_ws_batch(
                    db,sessions=2,seconds=3,pause_seconds=0,
                    collector=collector,status_file=sidecar,
                )
            self.assertEqual(len(called),2)
            self.assertTrue(all(sock.closed for sock in sockets))
            self.assertEqual(result["immediate_transient_reconnect_attempts"],1)
            self.assertEqual(result["status"],"REJECT")
            self.assertEqual(result["attempted_sessions"],1)
            self.assertEqual(result["completed_safe_sessions"],0)
            self.assertEqual(result["failed_session_number"],1)
            self.assertEqual(
                result["collector_failure_code"],
                "WS_SESSION_REJECTED:PUBLIC_WS_STREAM_RECEIVE_CLOSED_OR_LOST",
            )
            self.assertFalse(result["restart_only_after_clean_batch"])
            self.assertEqual(json.loads(sidecar.read_text()),result)
            self.assertEqual(
                PublicTradeStreamJournal(db).status("btc_idr")["stream_status"],
                "DISCONNECTED",
            )
            self.assertFalse(result["g1_receipt_issuance_allowed"])
            self.assertFalse(result["g2_provider_call_allowed"])
            self.assertFalse(result["execution_allowed"])


if __name__=="__main__":
    unittest.main()
