"""Offline original-V2 M05 24h heartbeat journal audits: no systemd changes."""
import json
from types import SimpleNamespace
import unittest

from indodax_ai_bot.m10_m05_heartbeat_window_readonly import (
    MARKER, WINDOW_SECONDS, assess_m05_heartbeat_window,
    collect_m05_heartbeat_readonly,
)

NOW = 1800000000
START = NOW - WINDOW_SECONDS
INV = "a" * 32
INV2 = "b" * 32


def row(when, n, invocation=INV, **overrides):
    body = {
        "status": MARKER, "batches": n * 10,
        "handled": n, "ignored": 0, "outbound_sent": 0,
        "execution_allowed": False,
    }
    body.update(overrides)
    return {
        "__REALTIME_TIMESTAMP": str(when * 1000000),
        "_SYSTEMD_INVOCATION_ID": invocation,
        "MESSAGE": json.dumps(body),
    }


def full_day():
    return [row(t, i + 1) for i, t in enumerate(
        range(START + 30, NOW, 60)
    )]


class OriginalM05HeartbeatAudit(unittest.TestCase):
    def test_complete_day_is_heartbeat_only_not_full_acceptance(self):
        result = assess_m05_heartbeat_window(full_day(), now_epoch=NOW)
        self.assertTrue(result["continuous_24h_heartbeat_verified"])
        self.assertEqual(result["observed_process_invocation_count"], 1)
        self.assertEqual(result["maximum_observed_gap_seconds"], 60)
        self.assertEqual(result["sample_count"], 1440)
        self.assertFalse(result["m10_06_full_acceptance"])
        self.assertFalse(result["btc_ws_24h_uptime_verified"])
        self.assertFalse(result["restart_recovery_proven"])

    def test_started_late_does_not_qualify(self):
        result = assess_m05_heartbeat_window(
            full_day()[80:], now_epoch=NOW,
        )
        self.assertFalse(result["continuous_24h_heartbeat_verified"])
        self.assertGreater(result["maximum_observed_gap_seconds"], 180)

    def test_large_gap_blocks_continuity(self):
        events = full_day()
        events = events[:400] + events[430:]
        result = assess_m05_heartbeat_window(events, now_epoch=NOW)
        self.assertFalse(result["continuous_24h_heartbeat_verified"])
        self.assertGreater(result["maximum_observed_gap_seconds"], 180)

    def test_new_invocation_requires_separate_recovery_evidence(self):
        events = full_day()
        for index in range(750, len(events)):
            events[index]["_SYSTEMD_INVOCATION_ID"] = INV2
        result = assess_m05_heartbeat_window(events, now_epoch=NOW)
        self.assertEqual(result["observed_process_invocation_count"], 2)
        self.assertFalse(result["continuous_24h_heartbeat_verified"])
        self.assertFalse(result["restart_recovery_proven"])

    def test_untrusted_heartbeat_is_not_clean(self):
        events = full_day()
        event = dict(events[3])
        body = json.loads(event["MESSAGE"])
        body["execution_allowed"] = True
        event["MESSAGE"] = json.dumps(body)
        events[3] = event
        result = assess_m05_heartbeat_window(events, now_epoch=NOW)
        self.assertTrue(result["invalid_or_untrusted_records"])
        self.assertFalse(result["continuous_24h_heartbeat_verified"])

    def test_duplicate_or_reset_counter_denied(self):
        events = full_day()
        events[200]["MESSAGE"] = events[199]["MESSAGE"]
        result = assess_m05_heartbeat_window(events, now_epoch=NOW)
        self.assertFalse(result["counter_progress_valid"])
        self.assertFalse(result["continuous_24h_heartbeat_verified"])

    def test_bounded_journal_runner_only_original_unit(self):
        records = full_day()
        data = b"\n".join(json.dumps(e).encode() for e in records) + b"\n"
        commands = []

        def runner(args, **kwargs):
            commands.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout=data)

        result = collect_m05_heartbeat_readonly(
            now_epoch=NOW, runner=runner,
        )
        self.assertTrue(result["journal_read_successful"])
        self.assertTrue(result["continuous_24h_heartbeat_verified"])
        args, opts = commands[0]
        self.assertIn("indodax-v2-m05-telegram.service", args)
        self.assertIn("--user", args)
        self.assertIn("--output=json", args)
        self.assertNotIn("systemctl", args)
        self.assertEqual(opts["timeout"], 18)

    def test_unavailable_journal_fails_closed(self):
        def runner(args, **kwargs):
            return SimpleNamespace(returncode=1, stdout=b"bad")
        result = collect_m05_heartbeat_readonly(
            now_epoch=NOW, runner=runner,
        )
        self.assertFalse(result["journal_read_successful"])
        self.assertFalse(result["continuous_24h_heartbeat_verified"])
        self.assertFalse(result["m10_06_full_acceptance"])


if __name__ == "__main__":
    unittest.main()
