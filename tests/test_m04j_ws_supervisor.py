"""M04-J supervised PUBLIC WS batch; no service is enabled by these tests."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.public_ws_supervisor import (
    PublicWSSupervisorError, exclusive_collector_lock,
    supervised_public_ws_batch,
)
from indodax_ai_bot.trade_stream import StreamEvidenceError
from test_m04e_trade_stream import ack


def collected_report(journal, *, sessions=2, unsafe=False):
    journal.acknowledge("btc_idr", ack(100))
    journal.mark_disconnected("btc_idr")
    return {
        "status": "PUBLIC_SAMPLE_COLLECTED_UNATTESTED",
        "requested_sessions": sessions,
        "completed_safe_sessions": sessions,
        "attempted_sessions": sessions,
        "g1_receipt_issuance_allowed": False if not unsafe else True,
        "g2_provider_call_allowed": False,
        "execution_allowed": False,
    }


class M04JPublicWSSupervisorTests(unittest.TestCase):
    def test_valid_batch_atomic_status_and_no_authority(self):
        with TemporaryDirectory() as td:
            db=Path(td)/"public.sqlite3"
            status=Path(td)/"status.json"
            def fake(journal, *, pair, sessions, session_seconds, pause_seconds):
                self.assertEqual(pair,"btc_idr")
                self.assertEqual(sessions,2)
                self.assertEqual(session_seconds,3)
                return collected_report(journal)
            result=supervised_public_ws_batch(
                db, sessions=2, seconds=3, pause_seconds=0,
                collector=fake, status_file=status, now=lambda:1800001234,
            )
            self.assertEqual(result["status"],"PUBLIC_COLLECTION_BATCH_OK_UNATTESTED")
            self.assertEqual(result["last_completed_batch_epoch"],1800001234)
            self.assertTrue(result["restart_only_after_clean_batch"])
            self.assertFalse(result["g1_receipt_issuance_allowed"])
            self.assertFalse(result["g2_provider_call_allowed"])
            self.assertFalse(result["execution_allowed"])
            self.assertIsNone(result["systemd_service_active"])
            self.assertEqual(json.loads(status.read_text()),result)
            self.assertEqual(stat.S_IMODE(os.stat(status).st_mode),0o600)
            self.assertFalse(list(Path(td).glob("*.tmp")))

    def test_bad_batch_fails_stop_and_is_written_as_reject(self):
        with TemporaryDirectory() as td:
            def failed(journal, **kw):
                journal.acknowledge("btc_idr",ack(100))
                journal.mark_disconnected("btc_idr")
                return {"status":"REJECT","reason":"WS_HEARTBEAT_NOT_CONFIRMED",
                        "completed_safe_sessions":0}
            status=Path(td)/"status.json"
            result=supervised_public_ws_batch(
                Path(td)/"state.sqlite3",sessions=2,seconds=3,
                collector=failed,status_file=status,
            )
            self.assertEqual(result["status"],"REJECT")
            self.assertFalse(result["restart_only_after_clean_batch"])
            self.assertEqual(json.loads(status.read_text())["status"],"REJECT")

    def test_failed_connection_is_redacted_and_does_not_trigger_success_restart(self):
        with TemporaryDirectory() as td:
            def failed(*args, **kw):
                raise RuntimeError("do-not-copy-secret")
            result=supervised_public_ws_batch(
                Path(td)/"state.sqlite3",sessions=2,seconds=3,collector=failed,
            )
            self.assertEqual(result["status"],"REJECT")
            self.assertFalse(result["restart_only_after_clean_batch"])
            self.assertNotIn("do-not-copy-secret",str(result))

    def test_interrupted_fourth_session_code_visible_without_unsafe_restart(self):
        with TemporaryDirectory() as td:
            db=Path(td)/"state.sqlite3"
            sidecar=Path(td)/"last-batch.json"
            def failed(journal, **kw):
                journal.acknowledge("btc_idr",ack(100))
                journal.mark_disconnected("btc_idr")
                return {
                    "status":"REJECT",
                    "reason":"WS_SESSION_REJECTED:PUBLIC_WS_CONNECTION_OR_FRAME_FAILED",
                    "completed_safe_sessions":3,
                    "attempted_sessions":4,
                    "g1_receipt_issuance_allowed":False,
                    "g2_provider_call_allowed":False,
                    "execution_allowed":False,
                }
            result=supervised_public_ws_batch(
                db,sessions=10,seconds=3,collector=failed,status_file=sidecar,
            )
            self.assertEqual(result["status"],"REJECT")
            self.assertEqual(result["completed_safe_sessions"],3)
            self.assertEqual(result["attempted_sessions"],4)
            self.assertEqual(result["failed_session_number"],4)
            self.assertEqual(result["collector_failure_code"],
                             "WS_SESSION_REJECTED:PUBLIC_WS_CONNECTION_OR_FRAME_FAILED")
            self.assertFalse(result["restart_only_after_clean_batch"])
            self.assertEqual(json.loads(sidecar.read_text()),result)
            self.assertFalse(result["g1_receipt_issuance_allowed"])
            self.assertFalse(result["execution_allowed"])

    def test_failure_message_with_secret_or_newline_not_persisted(self):
        with TemporaryDirectory() as td:
            def failed(journal, **kw):
                journal.acknowledge("btc_idr",ack(100))
                journal.mark_disconnected("btc_idr")
                return {
                    "status":"REJECT",
                    "reason":"WS_FAIL secret=my-secret\\ncredential",
                    "completed_safe_sessions":0,
                    "attempted_sessions":1,
                }
            result=supervised_public_ws_batch(
                Path(td)/"state.sqlite3",sessions=2,seconds=3,
                collector=failed,status_file=Path(td)/"status.json",
            )
            self.assertEqual(result["collector_failure_code"],
                             "PUBLIC_WS_FAILURE_REASON_UNAVAILABLE")
            self.assertNotIn("my-secret",str(result))
            self.assertNotIn("credential",
                             (Path(td)/"status.json").read_text())
            self.assertFalse(result["restart_only_after_clean_batch"])

    def test_sanitized_stream_exception_stays_failed(self):
        with TemporaryDirectory() as td:
            def failed(*args,**kw):
                raise StreamEvidenceError("PUBLIC_WS_AUTH_TIMEOUT")
            result=supervised_public_ws_batch(
                Path(td)/"state.sqlite3",sessions=2,seconds=3,collector=failed,
            )
            self.assertEqual(result["status"],"REJECT")
            self.assertEqual(result["collector_failure_code"],"PUBLIC_WS_AUTH_TIMEOUT")
            self.assertFalse(result["restart_only_after_clean_batch"])

    def test_fake_g1_authorization_claim_fails_closed(self):
        with TemporaryDirectory() as td:
            result=supervised_public_ws_batch(
                Path(td)/"state.sqlite3",sessions=2,seconds=3,
                collector=lambda journal,**kw:collected_report(journal,unsafe=True),
            )
            self.assertEqual(result["status"],"REJECT")
            self.assertFalse(result["restart_only_after_clean_batch"])
            self.assertFalse(result["g1_receipt_issuance_allowed"])

    def test_lock_prevents_second_supervisor_writing_same_journal(self):
        with TemporaryDirectory() as td:
            db=Path(td)/"state.sqlite3"
            with exclusive_collector_lock(db):
                with self.assertRaisesRegex(PublicWSSupervisorError,"ALREADY_RUNNING"):
                    supervised_public_ws_batch(db,sessions=2,seconds=3)
            self.assertFalse(db.exists())
            self.assertTrue(Path(str(db)+".collector.lock").exists())

    def test_invalid_bounds_or_pair_no_journal_side_effects(self):
        with TemporaryDirectory() as td:
            db=Path(td)/"state.sqlite3"
            for extra in ({"sessions":1},{"sessions":11},{"seconds":31},
                          {"pause_seconds":-1},{"sessions":True},
                          {"seconds":False},{"pair":"eth_idr"}):
                with self.subTest(extra=extra):
                    with self.assertRaises(PublicWSSupervisorError):
                        supervised_public_ws_batch(db,**extra)
            self.assertFalse(db.exists())

    def test_cli_supervised_batch_uses_single_orchestrator_and_final_reject(self):
        safe={"status":"PUBLIC_COLLECTION_BATCH_OK_UNATTESTED",
              "restart_only_after_clean_batch":True,
              "g1_receipt_issuance_allowed":False,
              "g2_provider_call_allowed":False,
              "execution_allowed":False}
        with TemporaryDirectory() as td:
            out=StringIO()
            with patch("indodax_ai_bot.orchestrator.supervised_public_ws_batch",
                       return_value=safe) as batch:
                with redirect_stdout(out):
                    exit_code=main(["--public-ws-supervised-once",
                                    "--db",str(Path(td)/"state.sqlite3"),
                                    "--stream-seconds","3","--collector-sessions","2"])
            data=json.loads(out.getvalue())
            self.assertEqual(exit_code,0)
            self.assertEqual(data["final_decision"],"REJECT")
            self.assertFalse(data["live_trading_authorized"])
            self.assertFalse(data["execution_allowed"])
            batch.assert_called_once()

    def test_cli_rejected_supervisor_returns_nonzero(self):
        with TemporaryDirectory() as td:
            out=StringIO()
            with patch("indodax_ai_bot.orchestrator.supervised_public_ws_batch",
                       side_effect=PublicWSSupervisorError("PUBLIC_WS_SUPERVISOR_ALREADY_RUNNING")):
                with redirect_stdout(out):
                    rc=main(["--public-ws-supervised-once",
                             "--db",str(Path(td)/"state.sqlite3")])
            data=json.loads(out.getvalue())
            self.assertEqual(rc,3)
            self.assertEqual(data["public_ws"]["status"],"REJECT")
            self.assertEqual(data["final_decision"],"REJECT")
            self.assertFalse(data["public_ws"]["restart_only_after_clean_batch"])

    def test_unit_file_staged_only_restart_success_and_explicit_trading_off(self):
        unit=(Path(__file__).resolve().parents[1]/
              "deploy"/"indodax-v2-public-ws-btc.service").read_text()
        self.assertIn("Restart=on-success",unit)
        self.assertNotIn("Restart=always",unit)
        self.assertIn("INDODAX_TRADING_ENABLED=NO",unit)
        self.assertIn("INDODAX_ALLOW_LIVE_EXECUTION=NO",unit)
        self.assertIn("INDODAX_EXECUTION_MODE=DRY_RUN",unit)
        self.assertIn("--public-ws-supervised-once",unit)
        self.assertIn("WorkingDirectory=%h/indodax-ai-bot-v2",unit)
        self.assertNotIn("crypto-bot-live",unit)
        self.assertNotIn("SUMOPOD_API_KEY",unit)


if __name__ == "__main__":
    unittest.main()
