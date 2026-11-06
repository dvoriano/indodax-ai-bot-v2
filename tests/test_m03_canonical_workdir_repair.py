"""Offline original M03 canonical source cutover guard tests. No systemctl."""
import os
from pathlib import Path
import tempfile
import unittest

from scripts.m03_canonical_workdir_repair import (
    M03SourceRepairDenied, UNIT,
    _trusted_private_candidate, classify_m03_original_source_binding,
)


class OriginalM03CanonicalSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = self.home / "indodax-ai-bot-v2"
        self.unit_dir = self.home / ".config/systemd/user"
        self.drop = self.unit_dir / (UNIT + ".d") / "90-owner-advisory.conf"
        self.exec = (
            "/usr/bin/flock -n /run/user/1000/indodax-v2-m03-mainpath.lock "
            + str(self.root / ".venv/bin/python") +
            " -B -m indodax_ai_bot --scheduled-public-scanner-once "
            "--bulk-deep-limit 20 --m05-market-advisory-queue "
            "--ws-db " + str(self.root / "runtime/public_ws_btc.sqlite3") +
            " --db " + str(self.root / "runtime/decisions.sqlite3")
        )

    def classify(self, workdir=None, **overrides):
        kwargs = dict(
            working_directory=(
                self.home / "indodax-ai-bot-v2-m05-release"
                if workdir is None else workdir
            ),
            execstart=self.exec,
            dropins=str(self.drop),
            fragment=str(self.unit_dir / UNIT),
            root=self.root, unit_dir=self.unit_dir,
        )
        kwargs.update(overrides)
        return classify_m03_original_source_binding(**kwargs)

    def test_alternate_v2_release_is_only_allowed_correction(self):
        self.assertEqual(self.classify(), "ALT_V2_SCOPE_CORRECTION")

    def test_canonical_is_recognized_without_duplicate(self):
        self.assertEqual(self.classify(self.root), "ALREADY_CANONICAL")

    def test_other_checkout_refused_without_reading_it(self):
        with self.assertRaises(M03SourceRepairDenied):
            self.classify(self.home / "crypto-bot")

    def test_subfolder_not_treated_as_canonical(self):
        with self.assertRaises(M03SourceRepairDenied):
            self.classify(self.root / "scripts")

    def test_installed_service_must_be_exact_original_unit(self):
        with self.assertRaises(M03SourceRepairDenied):
            self.classify(fragment=str(self.unit_dir / "other.service"))

    def test_unknown_dropins_fail_closed(self):
        with self.assertRaises(M03SourceRepairDenied):
            self.classify(dropins=str(self.drop) + " /tmp/evil.conf")

    def test_original_cli_execstart_requires_scanner_and_advisory(self):
        with self.assertRaises(M03SourceRepairDenied):
            self.classify(execstart=self.exec.replace(
                "--scheduled-public-scanner-once", "--once"
            ))
        with self.assertRaises(M03SourceRepairDenied):
            self.classify(execstart=self.exec.replace(
                "--m05-market-advisory-queue", ""
            ))

    def test_owned_regular_0644_eligible_for_hardening(self):
        path = self.home / "decisions.sqlite3"
        path.write_bytes(b"dummy")
        os.chmod(path, 0o644)
        self.assertEqual(_trusted_private_candidate(path), 0o644)
        os.chmod(path, 0o600)
        self.assertEqual(_trusted_private_candidate(path), 0o600)

    def test_symlink_refused(self):
        path = self.home / "decisions.sqlite3"
        path.write_bytes(b"dummy")
        os.chmod(path, 0o600)
        alias = self.home / "alias.sqlite3"
        alias.symlink_to(path)
        with self.assertRaises(M03SourceRepairDenied):
            _trusted_private_candidate(alias)

    def test_hardlink_refused(self):
        path = self.home / "decisions.sqlite3"
        path.write_bytes(b"dummy")
        os.chmod(path, 0o600)
        alias = self.home / "alias.sqlite3"
        os.link(path, alias)
        with self.assertRaises(M03SourceRepairDenied):
            _trusted_private_candidate(path)


if __name__ == "__main__":
    unittest.main()
