"""M03 Cloudflare-403 diagnostic is LOCAL ONLY, never exposes proxy secrets."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from indodax_ai_bot.m03_http_access_diagnostic import (
    _proxy_names, _unit_proxy_names, _manager_proxy_names,
    diagnose_original_m03_local, inspect_local_proxy_snapshot,
)

class M03HttpLocalProxyDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.home = Path("/home/ubuntu")
        self.original = str(self.home / ".config/systemd/user/"
                            "indodax-v2-m03-mainpath.service")
        self.exec_ = str(self.home / "indodax-ai-bot-v2/.venv/bin/python")
        self.secret = "SECRET-NEVER-DISCLOSE:password@proxy.private"

    def test_local_only_exposes_variable_names_never_proxy_urls(self):
        report = inspect_local_proxy_snapshot(
            process_env={
                "HTTPS_PROXY": "http://" + self.secret,
                "no_proxy": "localhost,.private",
                "INDODAX_API_SECRET": "very-private",
            },
            urllib_proxy_schemes=["https", "no"],
            manager_output="HTTPS_PROXY=http://" + self.secret +
                           "\nGOOGLE_API_KEY=very-private\n",
            unit_output={
                "Environment": 'HTTPS_PROXY=http://' + self.secret +
                               ' INDODAX_TRADING_ENABLED=NO',
                "PassEnvironment": "HTTPS_PROXY http_proxy",
                "UnsetEnvironment": "NO_PROXY",
                "EnvironmentFiles": "",
                "FragmentPath": self.original,
                "ExecStart": "python " + self.exec_,
                "ActiveState": "failed",
            },
            home=self.home,
        )
        self.assertEqual(report["process_proxy_keys"], ["https_proxy", "no_proxy"])
        self.assertEqual(report["manager_proxy_keys"], ["https_proxy"])
        self.assertEqual(report["unit_explicit_proxy_keys"], ["https_proxy"])
        self.assertEqual(report["unit_pass_proxy_keys"], ["http_proxy", "https_proxy"])
        self.assertEqual(report["unit_unset_proxy_keys"], ["no_proxy"])
        self.assertTrue(report["unit_fragment_matches_original_v2"])
        self.assertTrue(report["unit_exec_uses_original_v2_checkout"])
        self.assertEqual(report["unit_active_state"], "failed")
        self.assertEqual(report["network_calls"], 0)
        self.assertEqual(report["indodax_requests"], 0)
        self.assertFalse(report["http_403_root_cause_verified"])
        s = json.dumps(report)
        for forbidden in (self.secret, "very-private", "proxy.private", "localhost"):
            self.assertNotIn(forbidden, s)

    def test_missing_manager_and_unit_are_unknown_not_no_proxy(self):
        report = inspect_local_proxy_snapshot(
            process_env={}, urllib_proxy_schemes=[],
            manager_output=None, unit_output=None, home=self.home,
        )
        self.assertIsNone(report["manager_proxy_keys"])
        self.assertIsNone(report["unit_explicit_proxy_keys"])
        self.assertIsNone(report["unit_environment_file_present"])
        self.assertIsNone(report["unit_fragment_matches_original_v2"])
        self.assertFalse(report["http_403_root_cause_verified"])

    def test_untrusted_unit_fragment_never_claims_original_unit(self):
        report = inspect_local_proxy_snapshot(
            process_env={}, urllib_proxy_schemes=[],
            manager_output="", unit_output={
                "FragmentPath": "/tmp/other-m03.service",
                "ExecStart": "/usr/bin/python3 -m other_bot",
                "EnvironmentFiles": "/tmp/private.env",
                "ActiveState": "not-real",
            }, home=self.home,
        )
        self.assertFalse(report["unit_fragment_matches_original_v2"])
        self.assertFalse(report["unit_exec_uses_original_v2_checkout"])
        self.assertTrue(report["unit_environment_file_present"])
        self.assertEqual(report["unit_active_state"], "UNKNOWN")
        self.assertNotIn("/tmp/private.env", json.dumps(report))

    def test_proxy_parser_only_allowlisted_variable_names(self):
        self.assertEqual(_proxy_names(["HTTP_PROXY", "SECRET_TOKEN", "No_PrOxY"]),
                         ["http_proxy", "no_proxy"])
        self.assertEqual(_unit_proxy_names('HTTPS_PROXY=token HTTP_PROXY=x'),
                         ["http_proxy", "https_proxy"])
        self.assertEqual(_manager_proxy_names(
            "HTTPS_PROXY=https://a\nSECRET=X\nNO_PROXY=localhost"),
            ["https_proxy", "no_proxy"])

    def test_one_fixed_systemd_unit_and_manager_no_network(self):
        cmd_log = []
        def fake_run(cmd, **kwargs):
            cmd_log.append(list(cmd))
            if cmd == ["systemctl", "--user", "show-environment"]:
                text = "HTTPS_PROXY=http://" + self.secret + "\n"
            else:
                self.assertEqual(cmd[:5], [
                    "systemctl", "--user", "show",
                    "indodax-v2-m03-mainpath.service", "--no-pager",
                ])
                text = (
                    "Environment=HTTP_PROXY=http://" + self.secret + "\n"
                    "FragmentPath=" + self.original + "\n"
                    "ExecStart=" + self.exec_ + " -B -m indodax_ai_bot\n"
                    "ActiveState=failed\n"
                )
            return subprocess.CompletedProcess(cmd, 0, text, "")
        with patch(
            "indodax_ai_bot.m03_http_access_diagnostic.subprocess.run",
            side_effect=fake_run,
        ), patch(
            "indodax_ai_bot.m03_http_access_diagnostic.getproxies",
            return_value={"http": "http://" + self.secret},
        ), patch(
            "indodax_ai_bot.m03_http_access_diagnostic.Path.home",
            return_value=self.home,
        ), patch.dict("os.environ", {"HTTP_PROXY": "http://" + self.secret},
                      clear=True):
            report = diagnose_original_m03_local()
        self.assertEqual(len(cmd_log), 2)
        self.assertEqual(report["process_proxy_keys"], ["http_proxy"])
        self.assertEqual(report["manager_proxy_keys"], ["https_proxy"])
        self.assertEqual(report["urllib_proxy_schemes"], ["http"])
        self.assertTrue(report["unit_fragment_matches_original_v2"])
        self.assertNotIn(self.secret, json.dumps(report))
        self.assertEqual(report["indodax_requests"], 0)

if __name__ == "__main__":
    unittest.main()
