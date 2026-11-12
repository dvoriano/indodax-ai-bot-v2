"""Original M03 user-timer 72h micro opt-in, secret-safe nonlive tests."""
from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import m03_micro_72h_owner_preflight_readonly as gate

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/m03_micro_72h_owner_enable_once.sh"
DROP = ROOT / ("systemd/user/indodax-v2-m03-mainpath.service.d/"
               "zzz-micro-72h-evidence.conf")

FLAGS = {
    "INDODAX_TRADING_ENABLED": ["NO"],
    "INDODAX_ALLOW_LIVE_EXECUTION": ["NO"],
    "INDODAX_EXECUTION_MODE": ["DRY_RUN"],
    "M03_WS3_EVIDENCE_ARCHIVE_GO": ["YES"],
    "M03_WS3_MICRO_72H_ARCHIVE_GO": [],
}


class OriginalM03Micro72hOptinTests(unittest.TestCase):
    def test_flags_off_by_default_on_explicit_enabled_only(self):
        self.assertTrue(gate.summarize_allowlisted(FLAGS, enabled=False))
        self.assertFalse(gate.summarize_allowlisted(FLAGS, enabled=True))
        newer = {k:list(v) for k,v in FLAGS.items()}
        newer[gate.MICRO_FLAG] = ["YES"]
        self.assertTrue(gate.summarize_allowlisted(newer, enabled=True))
        self.assertFalse(gate.summarize_allowlisted(newer, enabled=False))

    def test_reject_conflicting_or_live_execution_values(self):
        for key, value in (
            ("INDODAX_TRADING_ENABLED", ["YES"]),
            ("INDODAX_ALLOW_LIVE_EXECUTION", ["YES"]),
            ("INDODAX_EXECUTION_MODE", ["LIVE"]),
            ("M03_WS3_EVIDENCE_ARCHIVE_GO", ["NO"]),
            (gate.MICRO_FLAG, ["NO"]),
            (gate.MICRO_FLAG, ["NO", "YES"]),
        ):
            with self.subTest(key=key):
                current={k:list(v) for k,v in FLAGS.items()}
                current[key] = value
                self.assertFalse(gate.summarize_allowlisted(
                    current, enabled=False))

    def test_reads_only_allowlisted_values_from_one_private_envfile(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/"private.env"
            file.write_text("INDODAX_TRADING_ENABLED=NO\n"
                            "INDODAX_ALLOW_LIVE_EXECUTION=NO\n"
                            "INDODAX_EXECUTION_MODE=DRY_RUN\n"
                            "SECRET_OWNER_TOKEN=DO_NOT_LEAK\n")
            file.chmod(0o600)
            env="M03_WS3_EVIDENCE_ARCHIVE_GO=YES"
            flags,count=gate._secret_safe_flags(
                env,f"{file} (ignore_errors=no)")
            self.assertEqual(count,1)
            self.assertEqual(flags["INDODAX_TRADING_ENABLED"],["NO"])
            self.assertFalse(gate.MICRO_FLAG in flags and flags[gate.MICRO_FLAG])
            self.assertNotIn("SECRET_OWNER_TOKEN", json.dumps(flags))
            self.assertTrue(gate.summarize_allowlisted(flags,enabled=False))

    def test_untrusted_envfiles_or_symlinks_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp)
            file=parent/"unsafe.env"
            file.write_text("INDODAX_TRADING_ENABLED=NO\n")
            file.chmod(0o644)
            for target in (file,parent/"link.env"):
                if target.name == "link.env":
                    target.symlink_to(file)
                with self.subTest(target=target.name):
                    with self.assertRaises(ValueError):
                        gate._secret_safe_flags(
                            "",f"{target} (ignore_errors=no)")
            for input_value in ("", "unexpected", f"{file} (ignore_errors=maybe)"):
                with self.subTest(input=input_value):
                    with self.assertRaises(ValueError):
                        gate._secret_safe_flags("",input_value)

    def test_original_safe_preflight_reports_no_service_mutation(self):
        svc={
            "LoadState":"loaded","Type":"oneshot","NeedDaemonReload":"no",
            "ExecStart":(
                "--m03-ws-summary-watch-once "
                "--m03-ws-microstructure-watch runtime/decisions.sqlite3"),
            "Environment":"M03_WS3_EVIDENCE_ARCHIVE_GO=YES",
            "EnvironmentFiles":"/private/env (ignore_errors=no)",
        }
        def unit(u,*props):
            return (svc if u==gate.M03 else
                {"ActiveState":"active"} if u==gate.TIMER else
                {"ActiveState":"active","MainPID":"588864"})
        with (
            patch.object(gate,"_unit",side_effect=unit),
            patch.object(gate,"_private_archive",return_value=True),
            patch.object(gate,"_secret_safe_flags",
                         return_value=(FLAGS,1)),
        ):
            report=gate.inspect_original_m03_micro_preflight_readonly()
        self.assertEqual(report["status"],
                         "M03_MICRO_72H_PREFLIGHT_READY_NO_CHANGE")
        self.assertTrue(report["checks"]["m05_not_restarted_by_check"])
        self.assertEqual(report["checks"]["orders_sent_by_check"],0)

    def test_original_enabled_config_requires_real_micro_flag(self):
        svc={
            "LoadState":"loaded","Type":"oneshot","NeedDaemonReload":"no",
            "ExecStart":(
                "--m03-ws-summary-watch-once "
                "--m03-ws-microstructure-watch runtime/decisions.sqlite3"),
            "Environment":"M03_WS3_EVIDENCE_ARCHIVE_GO=YES",
            "EnvironmentFiles":"/private/env (ignore_errors=no)",
        }
        def unit(u,*props):
            return (svc if u==gate.M03 else
                {"ActiveState":"active"} if u==gate.TIMER else
                {"ActiveState":"active","MainPID":"588864"})
        enabled={k:list(v) for k,v in FLAGS.items()}
        enabled[gate.MICRO_FLAG]=["YES"]
        with (
            patch.object(gate,"_unit",side_effect=unit),
            patch.object(gate,"_private_archive",return_value=True),
            patch.object(gate,"_secret_safe_flags",
                         return_value=(enabled,1)),
        ):
            report=gate.inspect_original_m03_micro_preflight_readonly(
                expect_enabled=True)
        self.assertEqual(report["status"],
                         "M03_MICRO_72H_ENABLED_CONFIG_READY_NO_START")
        self.assertFalse(report["micro_research_recorded_by_check"])

    def test_wrong_micro_cli_and_absent_m03_timer_block(self):
        with patch.object(gate,"_unit",side_effect=OSError(
                "BOT_TOKEN=SECRET_NEVER_PRINT")):
            report=gate.inspect_original_m03_micro_preflight_readonly()
        self.assertEqual(report["status"],
                         "M03_MICRO_72H_PREFLIGHT_BLOCKED_NO_CHANGE")
        self.assertNotIn("BOT_TOKEN",json.dumps(report))

    def test_exact_reviewed_dropin_only_one_research_flag(self):
        self.assertEqual(DROP.read_text(),
            "[Service]\nEnvironment=M03_WS3_MICRO_72H_ARCHIVE_GO=YES\n")
        self.assertNotIn("INDODAX_TRADING_ENABLED=YES",DROP.read_text())

    def test_operator_syntax_no_start_stop_restart_orders_or_telegram(self):
        run=subprocess.run(["bash","-n",str(SCRIPT)],
                           capture_output=True,text=True,timeout=8)
        self.assertEqual(run.returncode,0,run.stderr)
        src=SCRIPT.read_text()
        for wanted in (
            "M03_MICRO_72H_OWNER_ENABLE_GO",
            "scripts.m03_micro_72h_owner_preflight_readonly",
            "M03_MICRO_72H_ROLLBACK_STARTED",
            "trap rollback EXIT",
            "systemctl --user daemon-reload",
            "M03_MICRO_72H_CONFIG_ENABLED_NEXT_NATURAL_TICK",
            'systemctl --user is-active --quiet "$M05"',
            "INDODAX_TRADING_ENABLED=NO",
            "INDODAX_ALLOW_LIVE_EXECUTION=NO",
            "INDODAX_EXECUTION_MODE=DRY_RUN",
        ):
            with self.subTest(wanted=wanted):
                self.assertIn(wanted,src)
        for forbidden in (
            'systemctl --user restart', 'systemctl --user start',
            'systemctl --user stop','curl ','wget ','sendMessage',
            'INDODAX_TRADING_ENABLED=YES', 'INDODAX_ALLOW_LIVE_EXECUTION=YES',
        ):
            self.assertNotIn(forbidden,src)


if __name__=="__main__":
    unittest.main()
