"""Standing PUBLIC-only M03/BTC owner consent: no expiry, still fail closed.

No internet, systemd, Telegram, private exchange credentials, AI or orders.
"""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from indodax_ai_bot.btc_ops_gate import (
    AdmissionDenied, STANDING_MODE, _policy_checks,
)
from indodax_ai_bot.m03_egress_admission import (
    M03EgressAdmissionError, M03SharedEgressAdmission,
)
from scripts import m03_egress_nonlive_pilot as owner

NOW = 1794000000
IP = "43.134.68.108"


class StandingPublicOnlyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="original-v2-standing-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "indodax-ai-bot-v2"
        self.root.mkdir(mode=0o700)
        self.rt = self.root / "runtime"
        self.rt.mkdir(mode=0o700)
        config = Path(tmp.name) / ".config"
        config.mkdir(mode=0o700)
        self.policy_path = config / "m03-egress-admission.json"
        self.go_path = self.rt / "btc_public_ops.go.json"
        self.status_path = self.rt / "public_ws_btc.status.json"
        self.hold_path = self.rt / "btc_public_ops.hold.json"
        self.db_path = self.rt / "public_ws_btc.sqlite3"
        self.policy = {
            "schema_version": 2, "owner_approved_public_only": True,
            "all_shared_ip_consumers_accounted": True,
            "egress_public_ip": IP, "external_consumer_ids": [],
            "external_peak_requests_rolling_60s": 0,
            "issued_at_epoch": NOW - 10000,
            "expires_at_epoch": NOW - 8000,
            "pilot_scope": "M03_NONLIVE_PUBLIC_PILOT",
        }
        self.go = {
            "scope": "BTC_PUBLIC_MONITOR_ONLY",
            "expires_at_epoch": NOW - 20000, "max_gap_count": 87,
        }
        self.status = {
            "status": "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED",
            "requested_sessions": 10, "attempted_sessions": 10,
            "completed_safe_sessions": 10,
            "collector_failure_code": None,
            "restart_only_after_clean_batch": True,
            "last_completed_batch_epoch": NOW - 22000,
            "unrecoverable_gap_rebaseline_count": 8,
            "g1_receipt_issuance_allowed": False,
            "g2_provider_call_allowed": False,
            "execution_allowed": False,
        }
        self._write(self.policy_path, self.policy)
        self._write(self.go_path, self.go)
        self._write(self.status_path, self.status)
        with sqlite3.connect(self.db_path) as db:
            db.execute("CREATE TABLE ws_stream_discontinuities(pair TEXT)")
            db.executemany(
                "INSERT INTO ws_stream_discontinuities(pair) VALUES('btc_idr')",
                [()] * 8,
            )
        self._root_patch = patch.object(owner, "ROOT", self.root)
        self._root_patch.start()
        self.addCleanup(self._root_patch.stop)

    @staticmethod
    def _write(path, value):
        path.write_text(json.dumps(value, sort_keys=True) + "\n")
        path.chmod(0o600)

    def issue(self, **kwargs):
        d = dict(root=self.root, policy_path=self.policy_path,
                 epoch=NOW, owner_confirmed=True, ip_reader=lambda: IP)
        d.update(kwargs)
        return owner.prepare_standing_public(**d)

    def btc(self, now):
        _policy_checks(self.go_path, self.status_path, self.db_path,
                       self.hold_path, now)

    def m03(self, now):
        return M03SharedEgressAdmission(
            self.policy_path, clock=lambda: now,
            btc_runtime_dir=self.rt,
        ).allowed_local_limit()

    def test_standing_issuance_from_expired_legacy_preserves_caps_and_backups(self):
        original_go = self.go_path.read_bytes()
        original_policy = self.policy_path.read_bytes()
        result = self.issue()
        self.assertEqual(result["status"], "M03_BTC_STANDING_PUBLIC_ONLY_INSTALLED")
        self.assertIsNone(result["consent_expiration_epoch"])
        self.assertEqual(result["m03_local_public_rest_cap_60s"], 90)
        self.assertTrue(result["btc_requires_new_clean_batch_for_m03"])
        self.assertFalse(result["service_started_or_restarted"])
        self.assertFalse(result["order_sent"])
        self.assertEqual(json.loads(self.go_path.read_text()),
                         {"scope": "BTC_PUBLIC_MONITOR_ONLY",
                          "mode": STANDING_MODE, "issued_at_epoch": NOW,
                          "max_gap_count": 87})
        self.assertEqual(json.loads(self.policy_path.read_text())["schema_version"], 3)
        go_backups = list(self.rt.glob("btc_public_ops.go.json.pre-standing.*.bak"))
        policy_backups = list(self.policy_path.parent.glob(
            "m03-egress-admission.json.pre-standing.*.bak"))
        self.assertEqual(len(go_backups), 1)
        self.assertEqual(len(policy_backups), 1)
        self.assertEqual(go_backups[0].read_bytes(), original_go)
        self.assertEqual(policy_backups[0].read_bytes(), original_policy)
        self.assertEqual(go_backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.go_path.stat().st_mode & 0o777, 0o600)

    def test_no_expiry_btc_allows_future_clean_batches_without_renewal(self):
        self.issue()
        for gap in (0, 1000, 40000 * 86400):
            with self.subTest(gap=gap):
                self.btc(NOW + gap)

    def test_m03_waits_for_fresh_btc_batch_then_no_consent_expiry(self):
        self.issue()
        with self.assertRaisesRegex(
            M03EgressAdmissionError, "M03_STANDING_BTC_SOURCE_UNHEALTHY"
        ):
            self.m03(NOW)
        for after in (NOW + 1, NOW + 365 * 86400):
            self.status["last_completed_batch_epoch"] = after - 120
            self._write(self.status_path, self.status)
            self.assertEqual(self.m03(after), 90)

    def test_m03_rejects_forged_permanent_scope(self):
        self.issue()
        data = json.loads(self.policy_path.read_text())
        data["pilot_scope"] = "M03_TRADE_UNLIMITED"
        self._write(self.policy_path, data)
        with self.assertRaisesRegex(
            M03EgressAdmissionError, "M03_EGRESS_STANDING_SCOPE_INVALID"
        ):
            self.m03(NOW)

    def test_btc_reject_and_hold_still_block_permanent_go(self):
        self.issue()
        self._write(self.hold_path, {"scope": "BTC_PUBLIC_MONITOR_ONLY"})
        with self.assertRaisesRegex(AdmissionDenied, "PUBLIC_WS_OPERATOR_HOLD"):
            self.btc(NOW)
        self.hold_path.unlink()
        self.status["status"] = "REJECT"
        self.status["completed_safe_sessions"] = 9
        self._write(self.status_path, self.status)
        with self.assertRaisesRegex(
            AdmissionDenied, "PUBLIC_WS_FAILURE_NEEDS_NEW_OWNER_ACK"
        ):
            self.btc(NOW)

    def test_gap_mismatch_blocks_btc_and_m03(self):
        self.issue()
        self.status["last_completed_batch_epoch"] = NOW - 60
        self.status["unrecoverable_gap_rebaseline_count"] = 7
        self._write(self.status_path, self.status)
        with self.assertRaisesRegex(AdmissionDenied, "PUBLIC_WS_GAP_LIMIT_OR_MISMATCH"):
            self.btc(NOW)
        with self.assertRaisesRegex(
            M03EgressAdmissionError, "M03_STANDING_BTC_SOURCE_UNHEALTHY"
        ):
            self.m03(NOW)

    def test_no_automatic_migration_without_owner_confirmation(self):
        previous = (self.go_path.read_bytes(), self.policy_path.read_bytes())
        with self.assertRaisesRegex(
            owner.PilotDenied, "M03_STANDING_EXPLICIT_OWNER_CONFIRMATION_REQUIRED"
        ):
            self.issue(owner_confirmed=False)
        self.assertEqual(previous, (
            self.go_path.read_bytes(), self.policy_path.read_bytes()))

    def test_wrong_public_ip_rejected_without_writes(self):
        old_go = self.go_path.read_bytes()
        with self.assertRaisesRegex(owner.PilotDenied, "M03_PUBLIC_EGRESS_IP_CHANGED"):
            self.issue(ip_reader=lambda: "8.8.8.8")
        self.assertEqual(self.go_path.read_bytes(), old_go)

    def test_undeclared_rest_peer_rejected(self):
        self.policy["external_consumer_ids"] = ["unverified-peer"]
        self._write(self.policy_path, self.policy)
        with self.assertRaisesRegex(
            owner.PilotDenied, "M03_EXISTING_OWNER_DECLARATION_INVALID"
        ):
            self.issue()

    def test_untrusted_status_rejected_without_mutation(self):
        self.status["g2_provider_call_allowed"] = True
        self._write(self.status_path, self.status)
        previous = self.go_path.read_bytes()
        with self.assertRaisesRegex(
            owner.PilotDenied, "M03_STANDING_BTC_PRECONDITIONS_NOT_MET"
        ):
            self.issue()
        self.assertEqual(self.go_path.read_bytes(), previous)

    def test_second_activation_refuses_standing_source(self):
        self.issue()
        with self.assertRaisesRegex(
            owner.PilotDenied, "M03_EXISTING_OWNER_DECLARATION_INVALID"
        ):
            self.issue()

    def test_legacy_expired_remains_fail_closed_without_standing_owner_go(self):
        with self.assertRaisesRegex(AdmissionDenied, "PUBLIC_WS_OWNER_GO_INVALID"):
            self.btc(NOW)
        with self.assertRaisesRegex(
            M03EgressAdmissionError, "M03_EGRESS_ATTESTATION_EXPIRED"
        ):
            self.m03(NOW)

    def test_pilot_no_expiry_field_cannot_fake_schema3(self):
        self.issue()
        x = json.loads(self.policy_path.read_text())
        x["expires_at_epoch"] = NOW + 100000
        self._write(self.policy_path, x)
        with self.assertRaisesRegex(
            M03EgressAdmissionError, "M03_EGRESS_POLICY_INVALID"
        ):
            self.m03(NOW)


if __name__ == "__main__":
    unittest.main()
