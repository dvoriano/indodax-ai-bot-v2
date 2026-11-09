"""Original M05 heartbeat diagnosis: reason codes only, strictly offline."""
import json
import unittest

from indodax_ai_bot.m10_m05_heartbeat_window_readonly import (
    MARKER, WINDOW_SECONDS, assess_m05_heartbeat_window,
)

NOW = 1800000000
START = NOW - WINDOW_SECONDS


def record(t, batches, invocation="a" * 32, **updates):
    body = {
        "status": MARKER, "batches": batches,
        "handled": batches, "ignored": 0, "outbound_sent": 0,
        "execution_allowed": False,
    }
    body.update(updates)
    return {
        "__REALTIME_TIMESTAMP": str(t * 1000000),
        "_SYSTEMD_INVOCATION_ID": invocation,
        "MESSAGE": json.dumps(body),
    }


class M10HeartbeatDiagnosticTests(unittest.TestCase):
    def test_rejection_categories_no_raw_content(self):
        rows = [
            record(START + 20, 10),
            dict(record(START + 40, 20), MESSAGE="SECRET_BOT_TOKEN_CANNOT_BE_JSON"),
            record(START + 60, 30, status="UNRELATED_STATUS"),
            record(START + 80, 40, execution_allowed=True),
            dict(record(START + 100, 50), MESSAGE="x" * 3000),
        ]
        result = assess_m05_heartbeat_window(rows, now_epoch=NOW)
        self.assertEqual(result["rejected_record_counts"], {
            "HEARTBEAT_FIELDS_UNTRUSTED": 1,
            "MATCHED_NON_HEARTBEAT": 1,
            "MESSAGE_NOT_BOUNDED": 1,
            "MESSAGE_NOT_JSON": 1,
        })
        self.assertTrue(result["invalid_or_untrusted_records"])
        self.assertEqual(result["sample_count"], 1)
        self.assertFalse(result["continuous_24h_heartbeat_verified"])
        self.assertNotIn("SECRET_BOT_TOKEN", json.dumps(result))

    def test_window_start_gap_is_not_a_transport_gap(self):
        events = [record(t, (i + 1) * 10) for i, t in enumerate(
            range(START + 240, NOW, 60))]
        r = assess_m05_heartbeat_window(events, now_epoch=NOW)
        self.assertEqual(r["maximum_observed_gap_seconds"], 240)
        self.assertEqual(r["maximum_gap_kind"], "WINDOW_START")
        self.assertEqual(r["maximum_gap_start_epoch"], START)
        self.assertEqual(r["maximum_gap_end_epoch"], START + 240)
        self.assertFalse(r["continuous_24h_heartbeat_verified"])

    def test_window_end_gap_is_not_a_transport_gap(self):
        events = [record(t, (i + 1) * 10) for i, t in enumerate(
            range(START + 30, NOW - 450, 60))]
        r = assess_m05_heartbeat_window(events, now_epoch=NOW)
        self.assertEqual(r["maximum_gap_kind"], "WINDOW_END")
        self.assertGreater(r["maximum_observed_gap_seconds"], 180)
        self.assertEqual(r["maximum_gap_end_epoch"], NOW)

    def test_between_heartbeats_gap_observed(self):
        rows = [
            record(START + 30, 10),
            record(START + 90, 20),
            record(NOW - 30, 30),
        ]
        r = assess_m05_heartbeat_window(rows, now_epoch=NOW)
        self.assertEqual(r["maximum_gap_kind"], "BETWEEN_HEARTBEATS")
        self.assertEqual(r["maximum_gap_start_epoch"], START + 90)
        self.assertEqual(r["maximum_gap_end_epoch"], NOW - 30)

    def test_four_process_segments_do_not_expose_ids(self):
        rows = [record(START + 20 + i * 60, 10, chr(97+i)*32)
                for i in range(4)]
        r = assess_m05_heartbeat_window(rows, now_epoch=NOW)
        self.assertEqual(r["observed_process_invocation_count"], 4)
        self.assertEqual(len(r["invocation_segments"]), 4)
        self.assertFalse(r["invocation_segments_truncated"])
        self.assertEqual([x["sample_count"] for x in r["invocation_segments"]],
                         [1, 1, 1, 1])
        self.assertFalse(r["restart_recovery_proven"])
        self.assertFalse(r["continuous_24h_heartbeat_verified"])
        for marker in ("a"*32, "b"*32, "c"*32, "d"*32):
            self.assertNotIn(marker, json.dumps(r))


if __name__ == "__main__":
    unittest.main()
