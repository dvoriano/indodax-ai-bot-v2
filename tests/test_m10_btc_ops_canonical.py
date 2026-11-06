"""Canonical V2 BTC operator gate: owner-scoped offline recovery contract."""
from __future__ import annotations

from hashlib import sha256
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from indodax_ai_bot.btc_ops_gate import (
    AdmissionDenied, admit, diagnose_once, main, record_exit,
)

NOW = 1793786400


class CanonicalBTCOpsGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="v2-btc-ops-gate-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.go = root / "btc_public_ops.go.json"
        self.status = root / "public_ws_btc.status.json"
        self.db = root / "public_ws_btc.sqlite3"
        self.hold = root / "btc_public_ops.hold.json"
        self.state = root / "btc_public_ops.admission.json"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE ws_stream_discontinuities(pair TEXT)")
            con.executemany(
                "INSERT INTO ws_stream_discontinuities(pair) VALUES(?)",
                [("btc_idr",)] * 5,
            )
        self.doc = {
            "status": "REJECT",
            "collector_failure_code":
                "WS_SESSION_REJECTED:PUBLIC_WS_STREAM_RECEIVE_EMPTY_OR_CLOSED",
            "requested_sessions": 10,
            "attempted_sessions": 5,
            "completed_safe_sessions": 4,
            "unrecoverable_gap_rebaseline_count": 5,
            "g1_receipt_issuance_allowed": False,
            "g2_provider_call_allowed": False,
            "execution_allowed": False,
        }
        self._write(self.status, self.doc)
        self.policy = {
            "scope": "BTC_PUBLIC_MONITOR_ONLY",
            "expires_at_epoch": NOW + 900,
            "max_gap_count": 5,
            "acknowledged_failure_sha256":
                sha256(self.status.read_bytes()).hexdigest(),
        }
        self._write(self.go, self.policy)

    @staticmethod
    def _write(path: Path, doc: dict):
        path.write_text(json.dumps(doc, sort_keys=True))
        path.chmod(0o600)

    def check(self):
        return admit(go=self.go, status=self.status, database=self.db,
                     hold=self.hold, state=self.state, epoch=NOW)

    def diagnose(self):
        return diagnose_once(
            go=self.go, status=self.status, database=self.db,
            hold=self.hold, state=self.state, epoch=NOW,
        )

    def test_readonly_diagnose_good_preconditions_never_admits(self):
        paths = [self.go, self.status, self.db]
        before = {p: p.read_bytes() for p in paths}
        with patch("indodax_ai_bot.btc_ops_gate._atomic_json",
                   side_effect=AssertionError("STATE_MUTATION_DENIED")):
            out = self.diagnose()
        self.assertEqual(out["status"],
                         "BTC_PUBLIC_OPS_READONLY_EXEC_CONDITION_DIAGNOSIS")
        self.assertEqual(out["first_blocker"], "NONE")
        self.assertTrue(out["original_gate_preconditions_passed_at_snapshot"])
        self.assertFalse(out["dynamic_admission_or_concurrent_lock_verified"])
        self.assertFalse(out["service_started_or_restarted"])
        self.assertFalse(out["live_trading_authorized"])
        self.assertFalse(self.state.exists())
        self.assertFalse(self.state.with_suffix(".json.lock").exists())
        self.assertEqual(before, {p: p.read_bytes() for p in paths})

    def test_readonly_diagnose_hold_expiry_and_ack_not_issued(self):
        self._write(self.hold, {"scope": "BTC_PUBLIC_MONITOR_ONLY"})
        self.assertEqual(self.diagnose()["first_blocker"],
                         "PUBLIC_WS_OPERATOR_HOLD")
        self.hold.unlink()
        self.policy["expires_at_epoch"] = NOW
        self._write(self.go, self.policy)
        self.assertEqual(self.diagnose()["first_blocker"],
                         "PUBLIC_WS_OWNER_GO_INVALID")
        self.policy["expires_at_epoch"] = NOW + 900
        self.policy.pop("acknowledged_failure_sha256")
        self._write(self.go, self.policy)
        self.assertEqual(self.diagnose()["first_blocker"],
                         "PUBLIC_WS_FAILURE_NEEDS_NEW_OWNER_ACK")
        self.assertFalse(self.state.exists())

    def test_readonly_diagnose_existing_cooldown_does_not_renew(self):
        self.assertEqual(self.check(), "BTC_PUBLIC_OPS_ADMITTED_UNATTESTED")
        before = self.state.read_bytes()
        with patch("indodax_ai_bot.btc_ops_gate._atomic_json",
                   side_effect=AssertionError("REWRITE_DENIED")):
            report = self.diagnose()
        self.assertEqual(report["first_blocker"], "PUBLIC_WS_START_COOLDOWN")
        self.assertEqual(self.state.read_bytes(), before)

    def test_readonly_diagnose_cli_returns_safe_blocker_without_writes(self):
        self.policy["expires_at_epoch"] = NOW - 1
        self._write(self.go, self.policy)
        args = [
            "diagnose", "--go", str(self.go), "--status", str(self.status),
            "--db", str(self.db), "--hold", str(self.hold),
            "--state", str(self.state),
        ]
        with (patch("indodax_ai_bot.btc_ops_gate.time.time",
                    return_value=NOW),
              patch("indodax_ai_bot.btc_ops_gate._atomic_json",
                    side_effect=AssertionError("MUTATION_DENIED")),
              redirect_stdout(StringIO()) as stream):
            self.assertEqual(main(args), 0)
        out = json.loads(stream.getvalue())
        self.assertEqual(out["first_blocker"], "PUBLIC_WS_OWNER_GO_INVALID")
        self.assertFalse(out["service_started_or_restarted"])
        self.assertFalse(self.state.exists())

    def test_operator_hold_blocks_even_valid_public_go(self):
        self._write(self.hold, {"scope": "BTC_PUBLIC_MONITOR_ONLY"})
        with self.assertRaisesRegex(AdmissionDenied, "PUBLIC_WS_OPERATOR_HOLD"):
            self.check()
        self.assertFalse(self.state.exists())

    def test_expired_go_denied_without_network(self):
        self.policy["expires_at_epoch"] = NOW
        self._write(self.go, self.policy)
        with self.assertRaisesRegex(AdmissionDenied, "PUBLIC_WS_OWNER_GO_INVALID"):
            self.check()
        self.assertFalse(self.state.exists())

    def test_rejected_batch_without_exact_ack_denied(self):
        self.policy.pop("acknowledged_failure_sha256")
        self._write(self.go, self.policy)
        with self.assertRaisesRegex(AdmissionDenied, "PUBLIC_WS_FAILURE_NEEDS_NEW_OWNER_ACK"):
            self.check()

    def test_gap_mismatch_denied_even_with_ack(self):
        self.doc["unrecoverable_gap_rebaseline_count"] = 4
        self._write(self.status, self.doc)
        self.policy["acknowledged_failure_sha256"] = sha256(
            self.status.read_bytes()).hexdigest()
        self._write(self.go, self.policy)
        with self.assertRaisesRegex(AdmissionDenied, "PUBLIC_WS_GAP_LIMIT_OR_MISMATCH"):
            self.check()

    def test_exact_ack_once_then_cooldown(self):
        self.assertEqual(self.check(), "BTC_PUBLIC_OPS_ADMITTED_UNATTESTED")
        with self.assertRaisesRegex(AdmissionDenied, "PUBLIC_WS_START_COOLDOWN"):
            self.check()

    def test_unexpected_failure_sets_hold_and_exec_condition_does_not(self):
        self.assertEqual(record_exit(hold=self.hold, service_result="exec-condition"),
                         "BTC_PUBLIC_OPS_EXIT_NO_FAILURE")
        self.assertFalse(self.hold.exists())
        self.assertEqual(record_exit(hold=self.hold, service_result="exit-code"),
                         "BTC_PUBLIC_OPS_FAILURE_HOLD_SET")
        self.assertTrue(self.hold.exists())


if __name__ == "__main__":
    unittest.main()
