"""Strategy A priority M05 restart guard source and heartbeat offline contract."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest

from scripts.m05_strategy_a_priority_heartbeat_readonly import (
    inspect_heartbeat_lines,
)

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "scripts/m05_strategy_a_priority_activate_once.sh"
DROPIN = ROOT / "systemd/user/indodax-v2-m05-telegram.service.d/strategy-a-priority.conf"


def heartbeat(*, pid="151515", status="M05_RADAR_NO_CURRENT_QUALIFIED_WATCH",
              n=1, enabled=True, execution=False, record_pid=None):
    msg = dict(status="M05_FAST_LOOP_HEARTBEAT_NONLIVE",
               radar_enabled=enabled,
               execution_allowed=execution,
               radar_invocations=n,
               radar_last_status=status)
    return json.dumps({"_PID": record_pid or pid,
                       "MESSAGE": json.dumps(msg)})


class OriginalM05StrategyAPriorityRolloutTests(unittest.TestCase):
    def test_heartbeat_requires_new_pid_and_safe_callback(self):
        self.assertEqual(inspect_heartbeat_lines([
            heartbeat()], pid="151515"), "READY")
        self.assertEqual(inspect_heartbeat_lines([
            heartbeat(n=0)], pid="151515"), "WAITING")
        self.assertEqual(inspect_heartbeat_lines([
            heartbeat(record_pid="200")], pid="151515"), "WAITING")
        self.assertEqual(inspect_heartbeat_lines([
            heartbeat(enabled=False)], pid="151515"), "FAILED")
        self.assertEqual(inspect_heartbeat_lines([
            heartbeat(execution=True)], pid="151515"), "FAILED")

    def test_heartbeat_rejects_callback_failure_and_untrusted_result(self):
        for bad in ("M05_RADAR_CALLBACK_FAILED",
                    "M05_RADAR_UNTRUSTED_RESULT"):
            with self.subTest(bad=bad):
                self.assertEqual(inspect_heartbeat_lines([
                    heartbeat(status=bad)], pid="151515"), "FAILED")

    def test_heartbeat_rejects_unbounded_malformed_counters(self):
        record = json.loads(heartbeat())
        item = json.loads(record["MESSAGE"])
        for bad in (-1, "1", True):
            with self.subTest(bad=bad):
                item["radar_invocations"] = bad
                record["MESSAGE"] = json.dumps(item)
                self.assertEqual(inspect_heartbeat_lines([
                    json.dumps(record)], pid="151515"), "FAILED")

    def test_heartbeat_does_not_log_or_reflect_secret(self):
        secret = "PRIVATE_TOKEN_NEVER_ECHO_1"
        raw = [
            json.dumps({"_PID": "151515", "MESSAGE": secret}),
            heartbeat(),
        ]
        output = inspect_heartbeat_lines(raw, pid="151515")
        self.assertEqual(output, "READY")
        self.assertNotIn(secret, output)

    def test_pending_and_invalid_pid_fail_closed(self):
        self.assertEqual(inspect_heartbeat_lines([],pid="151515"),
                         "WAITING")
        self.assertEqual(inspect_heartbeat_lines([
            heartbeat()],pid="INVALID"), "FAILED")

    def test_exact_optional_dropin_no_credentials_or_trading(self):
        self.assertEqual(
            DROPIN.read_text(encoding="utf-8"),
            "[Service]\nEnvironment=M05_RADAR_PRIORITY_V2_GO=YES\n")
        self.assertNotIn("INDODAX_TRADING_ENABLED=YES",
                         DROPIN.read_text())

    def test_operator_bash_syntax(self):
        r = subprocess.run(["bash", "-n", str(SHELL)],
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_operator_go_preflight_single_service_and_rollback(self):
        script = SHELL.read_text(encoding="utf-8")
        required = (
            "M05_STRATEGY_A_PRIORITY_SINGLE_RESTART_GO",
            "EXPLICIT_SINGLE_RESTART_GO_REQUIRED",
            "trap rollback EXIT",
            "M05_STRATEGY_A_PRIORITY_ROLLBACK_STARTED",
            'rm -f -- "$DROP"',
            'systemctl --user daemon-reload',
            'systemctl --user restart "$UNIT"',
            "scripts.m05_strategy_a_priority_preflight_readonly",
            "scripts.m05_strategy_a_priority_heartbeat_readonly",
            'flock -n 9',
            "INDODAX_TRADING_ENABLED=NO",
            "INDODAX_ALLOW_LIVE_EXECUTION=NO",
            "INDODAX_EXECUTION_MODE=DRY_RUN",
            'systemctl --user is-active --quiet "$M03"',
            "M05_PRIORITY_NEW_PROCESS_GUARDS_OK",
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, script)
        for forbidden in ("getUpdates", "sendMessage",
                          "curl ", "wget ", "tradeOrder(",
                          "INDODAX_TRADING_ENABLED=YES",
                          "INDODAX_ALLOW_LIVE_EXECUTION=YES"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, script)


if __name__ == "__main__":
    unittest.main()
