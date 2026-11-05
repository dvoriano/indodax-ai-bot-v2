"""M03L archive-only owner lease, no live IP queries/Indodax/AI/orders."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.m03_egress_admission import (
    M03EgressAdmissionError, M03SharedEgressAdmission,
)
from ops import m03l_issue_public_archive_policy_once as issuer

NOW = 1793910000
EXAMPLE_PUBLIC_IP = "43.134.68.108"
SAFE = {
    "M03L_OWNER_PACED_20D_GO": "YES",
    "INDODAX_TRADING_ENABLED": "NO",
    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
    "INDODAX_EXECUTION_MODE": "DRY_RUN",
}


class M03LArchiveOnlyLeaseTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix="v2-m03l-archive-lease-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.root = root / "indodax-ai-bot-v2"
        self.root.mkdir(mode=0o700)
        cfg = root / ".config" / "indodax-ai-bot-v2"
        cfg.mkdir(parents=True, mode=0o700)
        self.scanner = cfg / "m03-egress-admission.json"
        self.archive = cfg / "m03l-public-archive-admission.json"
        self.original = {
            "schema_version": 2,
            "owner_approved_public_only": True,
            "all_shared_ip_consumers_accounted": True,
            "egress_public_ip": EXAMPLE_PUBLIC_IP,
            "external_consumer_ids": [],
            "external_peak_requests_rolling_60s": 0,
            "issued_at_epoch": NOW - 80000,
            "expires_at_epoch": NOW - 8000,
            "pilot_scope": "M03_NONLIVE_PUBLIC_PILOT",
        }
        self.scanner.write_text(json.dumps(self.original) + "\n")
        self.scanner.chmod(0o600)

    def issue(self, *, owner=True, ip=EXAMPLE_PUBLIC_IP, epoch=NOW):
        with patch.object(issuer, "CANONICAL_ROOT", self.root), patch.object(
            issuer, "SCANNER_POLICY", self.scanner
        ), patch.object(issuer, "ARCHIVE_POLICY", self.archive):
            return issuer.issue_archive_policy_once(
                root=self.root, scanner_policy=self.scanner,
                archive_policy=self.archive, epoch=epoch,
                owner_confirmed=owner, ip_reader=lambda: ip,
            )

    def test_expired_scanner_policy_stays_expired_fresh_archive_only(self):
        original_bytes = self.scanner.read_bytes()
        with self.assertRaisesRegex(
            M03EgressAdmissionError, "ATTESTATION_EXPIRED"
        ):
            M03SharedEgressAdmission(
                self.scanner, clock=lambda: NOW
            ).allowed_local_limit()
        report = self.issue()
        self.assertEqual(
            report["status"], "M03L_PUBLIC_ARCHIVE_ONLY_LEASE_PREPARED"
        )
        self.assertEqual(report["local_get_cap_rolling_60s"], 90)
        self.assertEqual(report["lease_valid_seconds_at_issue"], 72000)
        self.assertEqual(self.scanner.read_bytes(), original_bytes)
        archive = json.loads(self.archive.read_text())
        self.assertEqual(
            archive["expires_at_epoch"] - archive["issued_at_epoch"], 72000
        )
        self.assertEqual(archive["pilot_scope"], "M03_NONLIVE_PUBLIC_PILOT")
        self.assertEqual(self.archive.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            M03SharedEgressAdmission(
                self.archive, clock=lambda: NOW
            ).allowed_local_limit(), 90
        )
        self.assertFalse(report["original_scanner_egress_policy_modified"])
        self.assertFalse(report["outside_ip_peers_independently_measured"])
        self.assertFalse(report["execution_allowed"])
        self.assertEqual(report["paid_ai_calls"], 0)

    def test_reissue_is_bounded_and_does_not_modify_scanner(self):
        before = self.scanner.read_bytes()
        self.issue()
        first = self.archive.read_bytes()
        later = self.issue(epoch=NOW + 1000)
        self.assertNotEqual(first, self.archive.read_bytes())
        self.assertEqual(before, self.scanner.read_bytes())
        self.assertEqual(later["lease_valid_seconds_at_issue"], 72000)

    def test_missing_owner_confirmation_never_reads_or_writes(self):
        with self.assertRaisesRegex(
            issuer.M03LArchivePolicyDenied, "OWNER_CONFIRMATION_REQUIRED"
        ):
            self.issue(owner=False)
        self.assertFalse(self.archive.exists())

    def test_ip_drift_never_mints_new_policy(self):
        with self.assertRaisesRegex(
            issuer.M03LArchivePolicyDenied, "EGRESS_PUBLIC_IP_CHANGED"
        ):
            self.issue(ip="8.8.8.8")
        self.assertFalse(self.archive.exists())

    def test_peers_declared_or_unknown_never_mints(self):
        for patch_data in (
            {"external_consumer_ids": ["another_consumer"],
             "external_peak_requests_rolling_60s": 3},
            {"all_shared_ip_consumers_accounted": False},
        ):
            with self.subTest(patch_data=patch_data):
                self.scanner.write_text(
                    json.dumps({**self.original, **patch_data}) + "\n"
                )
                with self.assertRaisesRegex(
                    issuer.M03LArchivePolicyDenied,
                    "PREVIOUS_OWNER_NO_PEERS_DECLARATION_INVALID"
                ):
                    self.issue()
                self.assertFalse(self.archive.exists())

    def test_tampered_existing_archive_policy_denied_no_overwrite(self):
        self.issue()
        before = self.archive.read_bytes()
        self.archive.chmod(0o644)
        with self.assertRaisesRegex(
            issuer.M03LArchivePolicyDenied,
            "EXISTING_ARCHIVE_POLICY_UNTRUSTED"
        ):
            self.issue(epoch=NOW + 1)
        self.assertEqual(self.archive.read_bytes(), before)

    def test_cli_no_flag_or_livemode_never_read_owner_source(self):
        for go_flag, env in (
            ([], SAFE),
            (["--owner-confirm-v2-only-public-backfill"],
             {**SAFE, "M03L_OWNER_PACED_20D_GO": "NO"}),
            (["--owner-confirm-v2-only-public-backfill"],
             {**SAFE, "INDODAX_TRADING_ENABLED": "YES"}),
        ):
            with self.subTest(go=go_flag, env=env), patch.dict(
                os.environ, env, clear=True
            ), patch.object(
                issuer, "issue_archive_policy_once",
                side_effect=AssertionError("NO_SOURCE_OR_IP_READ"),
            ) as mint:
                text = StringIO()
                with redirect_stdout(text):
                    code = issuer.main(go_flag)
                self.assertEqual(code, 3)
                mint.assert_not_called()
                self.assertEqual(json.loads(text.getvalue())["status"], "STOP")

    def test_private_policy_malformed_and_directory_permissions_rejected(self):
        self.scanner.write_text('{"schema_version":2,"bogus":true}')
        with self.assertRaisesRegex(
            issuer.M03LArchivePolicyDenied,
            "PREVIOUS_OWNER_EGRESS_DECLARATION_UNAVAILABLE|"
            "PREVIOUS_OWNER_NO_PEERS_DECLARATION_INVALID"
        ):
            self.issue()
        self.scanner.write_text(json.dumps(self.original))
        self.scanner.parent.chmod(0o755)
        with self.assertRaisesRegex(
            issuer.M03LArchivePolicyDenied, "DIRECTORY_UNTRUSTED"
        ):
            self.issue()


if __name__ == "__main__":
    unittest.main()
