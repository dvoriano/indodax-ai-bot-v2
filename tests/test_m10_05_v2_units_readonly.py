"""Safety tests: M10-05 fixed V2 user-unit metadata, no outside services.

All unit data is synthetic. No systemd interaction or API/network calls.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.m10_v2_units_readonly import (
    _V2_UNITS, _safe_systemctl_show, inspect_named_v2_units, main,
)


def shown(unit: str, home: Path, *, active="inactive", enabled="disabled",
          loaded="loaded", reload="no", extras="") -> str:
    return (
        "LoadState=" + loaded + "\n"
        "ActiveState=" + active + "\n"
        "UnitFileState=" + enabled + "\n"
        "FragmentPath=" + str(home / ".config" / "systemd" / "user" / unit) + "\n"
        "NeedDaemonReload=" + reload + "\n" + extras
    )


class M10V2NamedUnitsTests(unittest.TestCase):
    def assert_no_ip_or_trading_claim(self, result):
        for key in (
            "active_v2_source_sha_independently_verified",
            "all_v2_rest_clients_identified",
            "all_live_v2_rest_clients_same_quota_db_verified",
            "outbound_public_ip_independently_verified",
            "all_egress_ip_consumers_verified",
            "external_rolling_60s_peak_verified",
            "m10_05_full_acceptance",
            "m03_timer_go_authorized",
        ):
            self.assertFalse(result[key], key)
        self.assertEqual(result["scope"], "KNOWN_V2_USER_UNITS_ONLY")

    def test_exact_v2_names_only_and_disabled_m03_timer(self):
        with TemporaryDirectory() as td:
            home = Path(td)
            seen = []
            def read(unit):
                seen.append(unit)
                return shown(unit, home)
            result = inspect_named_v2_units(reader=read, home=home)
            self.assertEqual(seen, [name for _, name in _V2_UNITS])
            self.assertEqual(len(result["units"]), 6)
            self.assertTrue(result["m03_timer_disabled_metadata_observed"])
            self.assertFalse(result["m03_timer_unexpected_activation_attention"])
            self.assertTrue(all(x["v2_user_fragment_path_matches"]
                                for x in result["units"]))
            self.assert_no_ip_or_trading_claim(result)

    def test_active_standing_m03_timer_is_expected_but_does_not_go(self):
        with TemporaryDirectory() as td:
            home = Path(td)
            def read(unit):
                return shown(unit, home,
                             active="active" if unit.endswith("m03-mainpath.timer")
                             else "inactive",
                             enabled="enabled" if unit.endswith("m03-mainpath.timer")
                             else "disabled")
            result = inspect_named_v2_units(reader=read, home=home)
            self.assertFalse(result["m03_timer_disabled_metadata_observed"])
            self.assertFalse(result["m03_timer_unexpected_activation_attention"])
            self.assert_no_ip_or_trading_claim(result)

    def test_missing_unit_never_proves_disabled(self):
        with TemporaryDirectory() as td:
            home = Path(td)
            def read(unit):
                return shown(unit, home, loaded="not-found") if unit.endswith(
                    "m03-mainpath.timer"
                ) else None
            result = inspect_named_v2_units(reader=read, home=home)
            self.assertFalse(result["m03_timer_disabled_metadata_observed"])
            self.assertFalse(result["m03_timer_unexpected_activation_attention"])
            self.assertEqual(result["units"][-1]["observed_load"], "not-found")
            self.assert_no_ip_or_trading_claim(result)

    def test_duplicate_and_forged_properties_do_not_promote(self):
        with TemporaryDirectory() as td:
            home = Path(td)
            def read(unit):
                return shown(unit, home, extras="ActiveState=active\n")
            result = inspect_named_v2_units(reader=read, home=home)
            self.assertFalse(result["m03_timer_disabled_metadata_observed"])
            self.assertTrue(all(x["observed_active"] == "unknown"
                                for x in result["units"]))
            self.assert_no_ip_or_trading_claim(result)

    def test_unexpected_path_and_reload_prohibit_positive_timer_claim(self):
        with TemporaryDirectory() as td:
            home = Path(td)
            def read(unit):
                if unit.endswith("m03-mainpath.timer"):
                    return shown(unit, home, reload="yes").replace(
                        str(home / ".config" / "systemd" / "user" / unit),
                        "/tmp/not-original-v2.service",
                    )
                return shown(unit, home)
            result = inspect_named_v2_units(reader=read, home=home)
            self.assertFalse(result["m03_timer_disabled_metadata_observed"])
            self.assertFalse(result["units"][-1]["v2_user_fragment_path_matches"])
            self.assertEqual(result["units"][-1]["unit_needs_daemon_reload"], "yes")
            self.assert_no_ip_or_trading_claim(result)

    def test_systemctl_command_only_queries_safe_fields_and_allowlisted_v2(self):
        with patch("indodax_ai_bot.m10_v2_units_readonly.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "LoadState=not-found\n"
            self.assertIsNotNone(_safe_systemctl_show(_V2_UNITS[0][1]))
            self.assertIsNone(_safe_systemctl_show("unrelated.service"))
            run.assert_called_once()
            args = run.call_args.args[0]
            joined = " ".join(args)
            self.assertIn("systemctl --user --no-pager show", joined)
            self.assertIn(_V2_UNITS[0][1], args)
            self.assertNotIn("Environment", joined)
            self.assertNotIn("ExecStart", joined)
            self.assertNotIn("MainPID", joined)
            self.assertFalse(run.call_args.kwargs["check"])
            self.assertFalse(run.call_args.kwargs.get("shell", False))
            self.assertLessEqual(run.call_args.kwargs["timeout"], 5)

    def test_systemctl_failure_timeout_and_output_bound_fail_closed(self):
        with patch("indodax_ai_bot.m10_v2_units_readonly.subprocess.run") as run:
            run.side_effect = subprocess.TimeoutExpired(["systemctl"], 5)
            self.assertIsNone(_safe_systemctl_show(_V2_UNITS[0][1]))
            run.side_effect = None
            run.return_value.returncode = 3
            self.assertIsNone(_safe_systemctl_show(_V2_UNITS[0][1]))
            run.return_value.returncode = 0
            run.return_value.stdout = "X" * 8193
            self.assertIsNone(_safe_systemctl_show(_V2_UNITS[0][1]))

    def test_cli_json_redacts_paths_and_unrelated_secret_text(self):
        with TemporaryDirectory() as td:
            home = Path(td)
            secret = "DO_NOT_LEAK_SECRET_VALUE"
            def read(unit):
                return shown(unit, home, extras="Environment=" + secret + "\n")
            with (patch("indodax_ai_bot.m10_v2_units_readonly.Path.home",
                        return_value=home),
                  patch("indodax_ai_bot.m10_v2_units_readonly._safe_systemctl_show",
                        side_effect=read),
                  redirect_stdout(StringIO()) as captured):
                rc = main()
            self.assertEqual(rc, 0)
            result = json.loads(captured.getvalue())
            self.assert_no_ip_or_trading_claim(result)
            self.assertNotIn(secret, captured.getvalue())
            self.assertNotIn(str(home), captured.getvalue())
            self.assertNotIn("Environment", captured.getvalue())
            self.assertNotIn("FragmentPath", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
