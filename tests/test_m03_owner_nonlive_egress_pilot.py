"""Offline original-V2 M03 NONLIVE owner lease tests, no network or services."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from indodax_ai_bot.m03_egress_admission import (
    M03SharedEgressAdmission, M03EgressAdmissionError,
)
from scripts import m03_egress_nonlive_pilot as pilot

NOW = 1793833200
IP = "43.134.68.108"


class NonliveOwnerPilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-m03-owner-pilot-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "indodax-ai-bot-v2"
        self.root.mkdir(mode=0o700)
        self.rt = self.root / "runtime"
        self.rt.mkdir(mode=0o700)
        cfg = Path(self.tmp.name) / ".config"
        cfg.mkdir(mode=0o700)
        self.policy = cfg / "m03-egress-admission.json"
        self.old = {
            "schema_version": 1,
            "owner_approved_public_only": True,
            "all_shared_ip_consumers_accounted": True,
            "egress_public_ip": IP,
            "external_consumer_ids": [],
            "external_peak_requests_rolling_60s": 0,
            "issued_at_epoch": NOW - 700,
            "expires_at_epoch": NOW - 100,
        }
        self._write(self.policy, self.old)
        self.status = {
            "status": "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED",
            "requested_sessions": 10,
            "attempted_sessions": 10,
            "completed_safe_sessions": 10,
            "collector_failure_code": None,
            "last_completed_batch_epoch": NOW - 174,
            "unrecoverable_gap_rebaseline_count": 7,
            "g1_receipt_issuance_allowed": False,
            "g2_provider_call_allowed": False,
            "execution_allowed": False,
        }
        self._write(self.rt / "public_ws_btc.status.json", self.status)
        self.btc_go = {
            "scope": "BTC_PUBLIC_MONITOR_ONLY",
            "expires_at_epoch": NOW + 65000,
            "max_gap_count": 87,
        }
        self._write(self.rt / "btc_public_ops.go.json", self.btc_go)
        with sqlite3.connect(self.rt / "public_ws_btc.sqlite3") as conn:
            conn.execute("CREATE TABLE ws_stream_discontinuities(pair TEXT)")
            conn.executemany(
                "INSERT INTO ws_stream_discontinuities(pair) VALUES(?)",
                [("btc_idr",)] * 7,
            )

    @staticmethod
    def _write(path: Path, doc: dict):
        path.write_text(json.dumps(doc, sort_keys=True) + "\n")
        path.chmod(0o600)

    def prepare(self, *, confirmed=True, ip=IP):
        with patch.object(pilot, "ROOT", self.root):
            return pilot.prepare_nonlive_pilot(
                root=self.root, policy_path=self.policy, epoch=NOW,
                owner_confirmed=confirmed, ip_reader=lambda: ip,
            )

    def test_prepares_source_bound_owner_pilot_and_keeps_old_backup(self):
        report = self.prepare()
        new = json.loads(self.policy.read_text())
        self.assertEqual(report["local_admission_cap_rolling_60s"], 90)
        self.assertEqual(report["pilot_valid_seconds_at_issue"], 65000)
        self.assertFalse(report["independent_shared_ip_traffic_measured"])
        self.assertFalse(report["scanner_started"])
        self.assertFalse(report["trading_enabled"])
        self.assertEqual(new["schema_version"], 2)
        self.assertEqual(new["pilot_scope"], "M03_NONLIVE_PUBLIC_PILOT")
        self.assertEqual(new["expires_at_epoch"], NOW + 65000)
        self.assertEqual(
            M03SharedEgressAdmission(self.policy, clock=lambda: NOW).allowed_local_limit(), 90
        )
        backup = self.policy.with_name(self.policy.name + ".pre-pilot-" + str(NOW) + ".bak")
        self.assertEqual(json.loads(backup.read_text()), self.old)

    def test_confirmation_required_with_no_policy_change(self):
        before = self.policy.read_bytes()
        with self.assertRaisesRegex(pilot.PilotDenied, "M03_OWNER_CONFIRMATION_REQUIRED"):
            self.prepare(confirmed=False)
        self.assertEqual(self.policy.read_bytes(), before)

    def test_public_ip_change_denied_without_write(self):
        before = self.policy.read_bytes()
        with self.assertRaisesRegex(pilot.PilotDenied, "M03_PUBLIC_EGRESS_IP_CHANGED"):
            self.prepare(ip="8.8.8.8")
        self.assertEqual(self.policy.read_bytes(), before)

    def test_peer_consumer_declaration_denies_pilot(self):
        self.old["external_consumer_ids"] = ["another_app"]
        self.old["external_peak_requests_rolling_60s"] = 3
        self._write(self.policy, self.old)
        with self.assertRaisesRegex(pilot.PilotDenied, "M03_EXISTING_OWNER_DECLARATION_INVALID"):
            self.prepare()

    def test_stale_btc_prevents_issuance(self):
        self.status["last_completed_batch_epoch"] = NOW - 4000
        self._write(self.rt / "public_ws_btc.status.json", self.status)
        with self.assertRaisesRegex(pilot.PilotDenied, "M03_BTC_BATCH_NOT_FRESH_CLEAN_PUBLIC"):
            self.prepare()

    def test_gap_mismatch_prevents_issuance(self):
        self.status["unrecoverable_gap_rebaseline_count"] = 6
        self._write(self.rt / "public_ws_btc.status.json", self.status)
        with self.assertRaisesRegex(pilot.PilotDenied, "M03_BTC_GAP_BUDGET_UNVERIFIED"):
            self.prepare()

    def test_legacy_v1_still_requires_15_minute_age(self):
        with self.assertRaisesRegex(M03EgressAdmissionError, "M03_EGRESS_ATTESTATION_EXPIRED"):
            M03SharedEgressAdmission(self.policy, clock=lambda: NOW).allowed_local_limit()
        self.old["issued_at_epoch"] = NOW - 60
        self.old["expires_at_epoch"] = NOW + 600
        self._write(self.policy, self.old)
        self.assertEqual(
            M03SharedEgressAdmission(self.policy, clock=lambda: NOW).allowed_local_limit(), 168
        )

    def test_v2_pilot_bad_scope_and_long_expiry_rejected(self):
        self.prepare()
        doc = json.loads(self.policy.read_text())
        doc["pilot_scope"] = "LIVE_ORDER"
        self._write(self.policy, doc)
        with self.assertRaisesRegex(M03EgressAdmissionError, "M03_EGRESS_PILOT_SCOPE_INVALID"):
            M03SharedEgressAdmission(self.policy, clock=lambda: NOW).allowed_local_limit()
        doc["pilot_scope"] = "M03_NONLIVE_PUBLIC_PILOT"
        doc["expires_at_epoch"] = NOW + 72001
        self._write(self.policy, doc)
        with self.assertRaisesRegex(M03EgressAdmissionError, "M03_EGRESS_ATTESTATION_EXPIRED"):
            M03SharedEgressAdmission(self.policy, clock=lambda: NOW).allowed_local_limit()


if __name__ == "__main__":
    unittest.main()
