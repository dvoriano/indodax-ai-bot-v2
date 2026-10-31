"""M10 capped PUBLIC BTC operations, no network, exchange account or trading."""
from __future__ import annotations

from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.btc_ops_gate import (
    AdmissionDenied, admit, main, record_exit, SCOPE,
)

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
EPOCH = 1800000000


class M10BTCOriginalCollectorOperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.go = self.root / "btc_public_ops.go.json"
        self.status = self.root / "public_ws_btc.status.json"
        self.db = self.root / "public_ws_btc.sqlite3"
        self.hold = self.root / "btc_public_ops.hold.json"
        self.state = self.root / "btc_public_ops.admission.json"

        with sqlite3.connect(self.db) as con:
            con.execute(
                "CREATE TABLE ws_stream_discontinuities("
                "id INTEGER PRIMARY KEY, pair TEXT)"
            )
            for _ in range(5):
                con.execute(
                    "INSERT INTO ws_stream_discontinuities(pair) VALUES ('btc_idr')"
                )
        self._write_private(self.go, {
            "scope": SCOPE,
            "expires_at_epoch": EPOCH + 3600,
            "max_gap_count": 5,
        })
        self._write_private(self.status, {
            "status": "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED",
            "attempted_sessions": 10,
            "completed_safe_sessions": 10,
            "collector_failure_code": None,
            "unrecoverable_gap_rebaseline_count": 5,
            "g1_receipt_issuance_allowed": False,
            "g2_provider_call_allowed": False,
            "execution_allowed": False,
        })

    @staticmethod
    def _write_private(path, value):
        path.write_text(json.dumps(value, sort_keys=True) + "\n")
        path.chmod(0o600)

    def _admit(self, *, epoch=EPOCH):
        return admit(go=self.go, status=self.status,
                     database=self.db, hold=self.hold,
                     state=self.state, epoch=epoch)

    def test_owner_go_allows_one_original_public_only_start(self):
        self.assertEqual(self._admit(), "BTC_PUBLIC_OPS_ADMITTED_UNATTESTED")
        self.assertEqual(json.loads(self.state.read_text())["last_admission_epoch"], EPOCH)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(AdmissionDenied, "START_COOLDOWN"):
            self._admit(epoch=EPOCH + 599)
        self.assertEqual(self._admit(epoch=EPOCH + 600),
                         "BTC_PUBLIC_OPS_ADMITTED_UNATTESTED")

    def test_missing_expired_bad_scope_and_unsafe_mode_go_fail_no_network(self):
        self.go.unlink()
        with self.assertRaisesRegex(AdmissionDenied, "REQUIRED_PRIVATE_FILE_MISSING"):
            self._admit()
        self.assertFalse(self.state.exists())
        for change in (
            {"scope": "TRADING_ON"},
            {"expires_at_epoch": EPOCH - 1},
            {"expires_at_epoch": EPOCH + 86401},
            {"max_gap_count": 4},
        ):
            go = {"scope": SCOPE, "expires_at_epoch": EPOCH + 3600,
                  "max_gap_count": 5}
            go.update(change)
            self._write_private(self.go, go)
            with self.assertRaises(AdmissionDenied):
                self._admit()
            self.assertFalse(self.state.exists())
        self._write_private(self.go, {
            "scope": SCOPE, "expires_at_epoch": EPOCH + 3600,
            "max_gap_count": 5,
        })
        self.go.chmod(0o644)
        with self.assertRaisesRegex(AdmissionDenied, "UNSAFE_PRIVATE_FILE"):
            self._admit()
        self.assertFalse(self.state.exists())

    def test_gap_growth_denies_until_owner_new_gap_budget(self):
        with sqlite3.connect(self.db) as con:
            con.execute(
                "INSERT INTO ws_stream_discontinuities(pair) VALUES ('btc_idr')"
            )
        with self.assertRaisesRegex(AdmissionDenied, "GAP_LIMIT_OR_MISMATCH"):
            self._admit()
        self.assertFalse(self.state.exists())

    def test_fail_stop_hook_preserves_failure_hold_and_denies_restart(self):
        self.assertEqual(
            record_exit(hold=self.hold, service_result="exit-code"),
            "BTC_PUBLIC_OPS_FAILURE_HOLD_SET",
        )
        self.assertEqual(self.hold.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(AdmissionDenied, "OPERATOR_HOLD"):
            self._admit()
        self.assertEqual(
            record_exit(hold=self.hold, service_result="exit-code"),
            "BTC_PUBLIC_OPS_HOLD_ALREADY_SET",
        )
        self.assertFalse(self.state.exists())

    def test_success_exit_does_not_hold_and_bad_exit_does(self):
        self.assertEqual(
            record_exit(hold=self.hold, service_result="success"),
            "BTC_PUBLIC_OPS_EXIT_NO_FAILURE",
        )
        self.assertEqual(
            record_exit(hold=self.hold, service_result="exec-condition"),
            "BTC_PUBLIC_OPS_EXIT_NO_FAILURE",
        )
        self.assertFalse(self.hold.exists())
        self.assertEqual(
            record_exit(hold=self.hold, service_result="signal"),
            "BTC_PUBLIC_OPS_FAILURE_HOLD_SET",
        )

    def test_failed_batch_requires_exact_new_owner_ack(self):
        failed = json.loads(self.status.read_text())
        failed["status"] = "REJECT"
        failed["collector_failure_code"] = "PUBLIC_WS_STREAM_RECEIVE_CLOSED_OR_LOST"
        self._write_private(self.status, failed)
        with self.assertRaisesRegex(AdmissionDenied, "FAILURE_NEEDS_NEW_OWNER_ACK"):
            self._admit()
        go = json.loads(self.go.read_text())
        go["acknowledged_failure_sha256"] = sha256(self.status.read_bytes()).hexdigest()
        self._write_private(self.go, go)
        self.assertEqual(self._admit(), "BTC_PUBLIC_OPS_ADMITTED_UNATTESTED")
        failed["last_completed_batch_epoch"] = EPOCH + 10
        self._write_private(self.status, failed)
        with self.assertRaisesRegex(AdmissionDenied, "FAILURE_NEEDS_NEW_OWNER_ACK"):
            self._admit(epoch=EPOCH + 700)

    def test_mutated_authority_or_untrusted_sidecar_denied(self):
        data = json.loads(self.status.read_text())
        data["execution_allowed"] = True
        self._write_private(self.status, data)
        with self.assertRaisesRegex(AdmissionDenied, "AUTHORITY_UNSAFE"):
            self._admit()
        data["execution_allowed"] = False
        self._write_private(self.status, data)
        self.status.chmod(0o644)
        with self.assertRaisesRegex(AdmissionDenied, "UNSAFE_PRIVATE_FILE"):
            self._admit()

    def test_symlink_owner_policy_rejected_without_admission_state(self):
        self.go.unlink()
        target = self.root / "other-go.json"
        self._write_private(target, {
            "scope": SCOPE, "expires_at_epoch": EPOCH + 3600,
            "max_gap_count": 5,
        })
        self.go.symlink_to(target)
        with self.assertRaisesRegex(AdmissionDenied, "UNSAFE_PRIVATE_FILE"):
            self._admit()
        self.assertFalse(self.state.exists())

    def test_cli_hook_is_sanitized_and_fails_closed(self):
        buf = StringIO()
        with redirect_stdout(buf):
            rc = main(["check", "--go", str(self.root / "absent"),
                       "--status", str(self.status),
                       "--db", str(self.db),
                       "--hold", str(self.hold),
                       "--state", str(self.state)])
        self.assertEqual(rc, 1)
        self.assertEqual(buf.getvalue().strip(), "REQUIRED_PRIVATE_FILE_MISSING")
        buf = StringIO()
        with patch.dict(os.environ, {"SERVICE_RESULT": "timeout"}):
            with redirect_stdout(buf):
                rc = main(["exit-hook", "--hold", str(self.hold)])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(),
                         "BTC_PUBLIC_OPS_FAILURE_HOLD_SET")

    def test_capped_systemd_reuses_original_cli_and_no_second_writer(self):
        original = (DEPLOY / "indodax-v2-public-ws-btc.service").read_text()
        dropin = (DEPLOY / "indodax-v2-public-ws-btc.capped-dropin.conf").read_text()
        timer = (DEPLOY / "indodax-v2-public-ws-btc.timer").read_text()

        self.assertIn("--public-ws-supervised-once", original)
        self.assertIn("--db runtime/public_ws_btc.sqlite3", original)
        self.assertIn("INDODAX_TRADING_ENABLED=NO", original)
        self.assertIn("INDODAX_ALLOW_LIVE_EXECUTION=NO", original)
        self.assertIn("INDODAX_EXECUTION_MODE=DRY_RUN", original)
        self.assertIn("Restart=no", dropin)
        self.assertIn("ExecCondition=%h/indodax-ai-bot-v2/.venv/bin/python "
                      "-B %h/indodax-ai-bot-v2-m10-ops/"
                      "indodax_ai_bot/btc_ops_gate.py check", dropin)
        self.assertIn("ExecStopPost=%h/indodax-ai-bot-v2/.venv/bin/python "
                      "-B %h/indodax-ai-bot-v2-m10-ops/"
                      "indodax_ai_bot/btc_ops_gate.py exit-hook", dropin)
        self.assertIn("Unit=indodax-v2-public-ws-btc.service", timer)
        self.assertIn("OnCalendar=*:0/15", timer)
        self.assertIn("Persistent=false", timer)
        self.assertNotIn("ExecStart=", dropin)
        self.assertNotIn("Restart=always", dropin)
        self.assertNotIn("crypto-bot-live", original + dropin + timer)


if __name__ == "__main__":
    unittest.main()
