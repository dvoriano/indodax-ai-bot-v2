"""M05 Strategy A owner priority preflight is secret-safe and side-effect free."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import unittest

from scripts import m05_strategy_a_priority_preflight_readonly as p


ROOT = "/home/ubuntu/indodax-ai-bot-v2"
PID = "198765"


def service(**kwargs):
    return dict({
        "LoadState": "loaded",
        "ActiveState": "active",
        "MainPID": PID,
        "Type": "simple",
        "NeedDaemonReload": "no",
        "WorkingDirectory": ROOT,
        "DropInPaths": "/home/ubuntu/.config/systemd/user/fast-longpoll.conf "
                       "/home/ubuntu/.config/systemd/user/radar-momentum.conf",
    }, **kwargs)


def flags(**kwargs):
    return dict({
        "INDODAX_TRADING_ENABLED": "NO",
        "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
        "INDODAX_EXECUTION_MODE": "DRY_RUN",
        "M05_TELEGRAM_FAST_LONGPOLL_GO": "YES",
        "M05_RADAR_MOMENTUM_ALERT_GO": "YES",
        "M05_WS3_RADAR_WATCH_GO": "YES",
    }, **kwargs)


def review(*, unit=None, fl=None, scanner=True, legacy=True,
           source=True, repo=True, pid=True):
    return p.assess_priority_preflight_nonlive(
        service=unit if unit is not None else service(),
        legacy_timer={"ActiveState": "inactive" if legacy else "active",
                      "UnitFileState": "disabled" if legacy else "enabled"},
        scanner_timer={"ActiveState": "active" if scanner else "inactive",
                       "UnitFileState": "enabled"},
        process_flags=fl if fl is not None else flags(),
        expected_workdir=ROOT, source_ok=source,
        repo_clean_main=repo, pid_stable=pid,
    )


class StrategyAPriorityPreflightTests(unittest.TestCase):
    def test_only_exact_safe_running_flags_enable_readiness(self):
        result = review()
        self.assertEqual(result["status"],
                         "M05_PRIORITY_PREFLIGHT_READY_NO_CHANGE")
        self.assertEqual(result["reasons"], [])
        self.assertTrue(result["m05_active"])
        self.assertTrue(result["m05_pid_unchanged"])
        self.assertTrue(result["m03_scanner_timer_active"])
        self.assertTrue(result["runtime_guards_verified"])
        self.assertFalse(result["priority_live_in_running_process"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["service_restarted"])
        self.assertFalse(result["telegram_sent"])
        self.assertFalse(result["order_sent"])

    def test_never_prints_secrets_or_arbitrary_env(self):
        raw = (b"BOT_TOKEN=123456789:SUPERSECRET_VALUE\0"
               b"INDODAX_TRADING_ENABLED=NO\0"
               b"INDODAX_ALLOW_LIVE_EXECUTION=NO\0"
               b"INDODAX_EXECUTION_MODE=DRY_RUN\0"
               b"M05_TELEGRAM_FAST_LONGPOLL_GO=YES\0"
               b"M05_RADAR_MOMENTUM_ALERT_GO=YES\0"
               b"M05_WS3_RADAR_WATCH_GO=YES\0"
               b"ANY_SECRET_API_KEY=abcdef\0")
        result = p.summarize_allowlisted_process_flags(raw)
        self.assertEqual(result, flags())
        serialized = json.dumps(review(fl=result))
        self.assertNotIn("SUPERSECRET", serialized)
        self.assertNotIn("abcdef", serialized)
        self.assertNotIn("BOT_TOKEN", serialized)

    def test_existing_priority_go_needs_no_second_cutover(self):
        result = review(fl=flags(M05_RADAR_PRIORITY_V2_GO="YES"))
        self.assertEqual(result["status"],
                         "M05_PRIORITY_PREFLIGHT_BLOCKED_NO_CHANGE")
        self.assertTrue(result["priority_live_in_running_process"])
        self.assertIn("PRIORITY_ALREADY_ACTIVE_NOT_A_FIRST_CUTOVER",
                      result["reasons"])

    def test_invalid_duplicate_flags_reject(self):
        for raw in (b"M05_RADAR_PRIORITY_V2_GO=YES\0"
                    b"M05_RADAR_PRIORITY_V2_GO=NO\0",
                    b"INDODAX_TRADING_ENABLED=NO\0"
                    b"INDODAX_TRADING_ENABLED=YES\0"):
            with self.subTest(raw=raw[:15]):
                with self.assertRaises(ValueError):
                    p.summarize_allowlisted_process_flags(raw)

    def test_invalid_or_missing_priority_state_blocks(self):
        for extra in ("UNKNOWN", "MAYBE"):
            with self.subTest(extra=extra):
                self.assertIn("PRIORITY_FLAG_INVALID", review(
                    fl=flags(M05_RADAR_PRIORITY_V2_GO=extra))["reasons"])

    def test_no_ready_when_trading_guard_missing_or_live(self):
        for key in (
            "INDODAX_TRADING_ENABLED",
            "INDODAX_ALLOW_LIVE_EXECUTION",
            "INDODAX_EXECUTION_MODE",
            "M05_RADAR_MOMENTUM_ALERT_GO",
            "M05_WS3_RADAR_WATCH_GO",
            "M05_TELEGRAM_FAST_LONGPOLL_GO",
        ):
            with self.subTest(key=key):
                bad = flags()
                bad[key] = "YES" if key.startswith("INDODAX_") else "NO"
                result = review(fl=bad)
                self.assertIn("RUNNING_FLAG_NOT_VERIFIED_" + key,
                              result["reasons"])
                self.assertFalse(result["execution_allowed"])

    def test_missing_longpoll_timer_or_scanner_blocks(self):
        self.assertIn("M05_LONGPOLL_DROPIN_MISSING", review(
            unit=service(DropInPaths="/tmp/other.conf"))["reasons"])
        self.assertIn("M05_LEGACY_TIMER_NOT_DISABLED",
                      review(legacy=False)["reasons"])
        self.assertIn("M03_SCANNER_TIMER_NOT_ACTIVE",
                      review(scanner=False)["reasons"])
        self.assertIn("M05_PID_NOT_STABLE",
                      review(pid=False)["reasons"])

    def test_dirty_source_or_wrong_service_workdir_blocks(self):
        self.assertIn("SOURCE_NOT_PINNED_CLEAN_MAIN",
                      review(repo=False)["reasons"])
        self.assertIn("SOURCE_NOT_PINNED_CLEAN_MAIN",
                      review(source=False)["reasons"])
        self.assertIn("M05_SERVICE_STATE_OR_PATH_NOT_READY",
                      review(unit=service(WorkingDirectory="/tmp/other")
                             )["reasons"])

    def test_main_offline_mock_runtime_never_invokes_telegram_or_order(self):
        def fake_run(args, *, cwd=None):
            if args[0] == "git" and args[1] == "branch":
                return "main"
            if args[0] == "git" and args[1] == "status":
                return ""
            raise AssertionError("unexpected command")
        def fake_show(unit, keys):
            if unit == p.M05:
                return service()
            if unit == p.M05_TIMER:
                return {"ActiveState": "inactive", "UnitFileState": "disabled"}
            if unit == p.M03_TIMER:
                return {"ActiveState": "active", "UnitFileState": "enabled"}
            raise AssertionError("unexpected service")
        blob = b"\0".join(
            (k + "=" + v).encode("ascii") for k, v in flags().items()
        )
        with (patch.object(p, "_run", side_effect=fake_run),
              patch.object(p, "_unit_show", side_effect=fake_show),
              patch.object(p.Path, "home", return_value=Path("/home/ubuntu")),
              patch.object(p.Path, "read_bytes", return_value=blob),
              patch.object(p.subprocess, "run",
                           return_value=SimpleNamespace(returncode=0))):
            result = p.inspect_original_v2_priority_preflight_readonly()
        self.assertEqual(result["status"],
                         "M05_PRIORITY_PREFLIGHT_READY_NO_CHANGE")
        self.assertFalse(result["service_restarted"])
        self.assertFalse(result["telegram_sent"])
        self.assertFalse(result["order_sent"])

    def test_unreadable_process_fails_closed_without_leaking_exception(self):
        with patch.object(p, "_run", side_effect=OSError("TOKEN=secret")):
            result = p.inspect_original_v2_priority_preflight_readonly()
        self.assertEqual(result["status"],
                         "M05_PRIORITY_PREFLIGHT_BLOCKED_NO_CHANGE")
        self.assertEqual(result["reasons"],
                         ["SOURCE_OR_RUNTIME_INSPECTION_UNAVAILABLE"])
        self.assertNotIn("secret", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
