"""Original M03 systemd EnvironmentFile metadata is secret-redacted and offline."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from indodax_ai_bot.m03_envfile_proxy_keys_readonly import (
    _parse_unit_env_files, _read_private_v2_proxy_keys,
    inspect_m03_envfile_proxy_keys, _unit_metadata,
)


class M03EnvFileProxyKeysReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "v2-owner"
        self.home.mkdir(mode=0o700)
        self.cfg = self.home / ".config" / "indodax-ai-bot-v2"
        self.cfg.mkdir(parents=True, mode=0o700)
        (self.home / ".config").chmod(0o700)
        self.cfg.chmod(0o700)
        self.file = self.cfg / "m03-owner.env"
        self.secret = "http://SHOULD-NEVER-PRINT:p@ss@private.example"
        self.file.write_text(
            "HTTPS_PROXY=" + self.secret + "\n"
            "API_TOKEN=SECRET-PRIVATE-10001\n"
            "NO_PROXY=localhost,127.0.0.1\n", encoding="utf-8"
        )
        self.file.chmod(0o600)
        self.unit = self.home / ".config" / "systemd/user" / (
            "indodax-v2-m03-mainpath.service"
        )
        self.meta = {
            "EnvironmentFiles": str(self.file) + " (ignore_errors=no)",
            "FragmentPath": str(self.unit),
            "ExecStart": str(self.home / "indodax-ai-bot-v2/.venv/bin/python")
                         + " -B -m indodax_ai_bot",
            "DropInPaths": str(self.home / ".config/systemd/user/"
                               "indodax-v2-m03-mainpath.service.d/local.conf"),
        }

    def test_owner_private_m03_envfile_only_lists_proxy_keys(self):
        report = inspect_m03_envfile_proxy_keys(
            metadata=self.meta, home=self.home
        )
        self.assertTrue(report["unit_trust_verified"])
        self.assertTrue(report["unit_dropins_present"])
        self.assertEqual(report["environment_files_declared"], 1)
        self.assertEqual(report["environment_files_inspected"], 1)
        self.assertEqual(report["environment_files_skipped"], 0)
        self.assertEqual(report["environment_proxy_keys"],
                         ["https_proxy", "no_proxy"])
        self.assertTrue(report["environment_files_all_checked"])
        self.assertTrue(report["no_network_calls"])
        self.assertFalse(report["cloudflare_403_root_cause_verified"])
        output = json.dumps(report)
        for value in (self.secret, "SECRET-PRIVATE", "private.example",
                      str(self.file), "localhost", ".service.d"):
            self.assertNotIn(value, output)

    def test_paths_outside_v2_and_symlinks_fail_without_read(self):
        outside = self.home / "another-repo.env"
        outside.write_text("HTTPS_PROXY=" + self.secret)
        outside.chmod(0o600)
        self.assertIsNone(_read_private_v2_proxy_keys(str(outside), self.home))
        link = self.cfg / "link.env"
        link.symlink_to(outside)
        self.assertIsNone(_read_private_v2_proxy_keys(str(link), self.home))
        self.assertEqual(_read_private_v2_proxy_keys(str(self.file), self.home),
                         ["https_proxy", "no_proxy"])
        report = inspect_m03_envfile_proxy_keys(
            metadata={**self.meta, "EnvironmentFiles":
                      str(link) + " (ignore_errors=no)"},
            home=self.home
        )
        self.assertEqual(report["environment_files_skipped"], 1)
        self.assertFalse(report["environment_files_all_checked"])
        self.assertEqual(report["environment_proxy_keys"], [])

    def test_world_readable_envfile_is_skipped(self):
        self.file.chmod(0o644)
        report = inspect_m03_envfile_proxy_keys(
            metadata=self.meta, home=self.home
        )
        self.assertEqual(report["environment_files_skipped"], 1)
        self.assertFalse(report["environment_files_all_checked"])
        self.assertNotIn(self.secret, json.dumps(report))

    def test_malformed_systemd_metadata_fails_closed(self):
        self.assertIsNone(_parse_unit_env_files(
            "/tmp/one.env (ignore_errors=no) malicious"
        ))
        self.assertIsNone(_parse_unit_env_files(
            "/tmp/one.env (ignore_errors=no) " * 10
        ))
        self.assertEqual(_parse_unit_env_files(""), [])
        self.assertEqual(_parse_unit_env_files(self.meta["EnvironmentFiles"]),
                         [str(self.file)])
        report = inspect_m03_envfile_proxy_keys(
            metadata={**self.meta, "FragmentPath": "/tmp/other-unit"},
            home=self.home
        )
        self.assertFalse(report["unit_trust_verified"])
        self.assertIsNone(report["environment_proxy_keys"])
        self.assertEqual(report["environment_files_inspected"], 0)

    def test_fixed_unit_metadata_only_no_network(self):
        calls = []
        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(
                cmd, 0,
                "\n".join(k + "=" + v for k, v in self.meta.items()),
                ""
            )
        with patch(
            "indodax_ai_bot.m03_envfile_proxy_keys_readonly.subprocess.run",
            side_effect=fake_run
        ):
            metadata = _unit_metadata()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:5],
                         ["systemctl", "--user", "show",
                          "indodax-v2-m03-mainpath.service", "--no-pager"])
        report = inspect_m03_envfile_proxy_keys(
            metadata=metadata, home=self.home
        )
        self.assertEqual(report["environment_proxy_keys"],
                         ["https_proxy", "no_proxy"])
        self.assertNotIn(self.secret, json.dumps(report))
        self.assertTrue(report["no_network_calls"])


if __name__ == "__main__":
    unittest.main()
