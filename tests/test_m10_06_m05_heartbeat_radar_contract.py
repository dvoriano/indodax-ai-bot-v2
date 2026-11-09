"""Original heartbeat consumer accepts radar cadence without relaxing safety."""
import json
from types import SimpleNamespace
import unittest

from indodax_ai_bot.m10_m05_heartbeat_window_readonly import (
    MARKER, assess_m05_heartbeat_window, collect_m05_heartbeat_readonly,
)

NOW = 1800000000


def row(batch=1, invocation="a" * 32, when=NOW, **updates):
    body = dict(status=MARKER, batches=batch, handled=0, ignored=0,
                outbound_sent=0, execution_allowed=False)
    body.update(updates)
    return dict(MESSAGE=json.dumps(body), _SYSTEMD_INVOCATION_ID=invocation,
                __REALTIME_TIMESTAMP=str(when * 1000000))


class RadarHeartbeatContractTests(unittest.TestCase):
    def test_first_and_nonperiodic_radar_batches_through_original_collector(self):
        records = [row(b, when=NOW-30+i) for i, b in enumerate((1, 10, 17, 20))]
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout=b"\n".join(
                json.dumps(r).encode() for r in records))
        result = collect_m05_heartbeat_readonly(now_epoch=NOW, runner=runner)
        self.assertEqual(result["sample_count"], 4)
        self.assertEqual(result["rejected_field_counts"], {})
        self.assertTrue(result["counter_progress_valid"])
        self.assertFalse(result["continuous_24h_heartbeat_verified"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "journalctl")

    def test_invalid_batch_types_still_rejected(self):
        for batch in (0, -1, True, "1", 1.5, None):
            with self.subTest(batch=batch):
                r = assess_m05_heartbeat_window([row(batch)], now_epoch=NOW)
                self.assertEqual(r["rejected_field_counts"], {"BATCHES_INVALID": 1})
                self.assertEqual(r["sample_count"], 0)

    def test_multiple_field_failures_are_counted_without_values(self):
        item = row(invocation="SECRET_INVOCATION", handled=True,
                   ignored=-1, outbound_sent="SECRET_COUNTER", execution_allowed=0)
        item["__REALTIME_TIMESTAMP"] = "SECRET_TIMESTAMP"
        r = assess_m05_heartbeat_window([item], now_epoch=NOW)
        self.assertEqual(r["rejected_record_counts"], {"HEARTBEAT_FIELDS_UNTRUSTED": 1})
        self.assertEqual(r["rejected_field_counts"], {
            "TIMESTAMP_INVALID": 1, "INVOCATION_INVALID": 1,
            "HANDLED_INVALID": 1, "IGNORED_INVALID": 1,
            "OUTBOUND_SENT_INVALID": 1, "EXECUTION_NOT_FALSE": 1})
        self.assertNotIn("SECRET", json.dumps(r))

    def test_missing_fields_remain_untrusted(self):
        r = assess_m05_heartbeat_window([{
            "MESSAGE": json.dumps({"status": MARKER})}], now_epoch=NOW)
        self.assertEqual(len(r["rejected_field_counts"]), 7)
        self.assertFalse(r["continuous_24h_heartbeat_verified"])

    def test_duplicate_nonperiodic_batch_still_blocks(self):
        r = assess_m05_heartbeat_window([row(1, when=NOW-2), row(1)], now_epoch=NOW)
        self.assertFalse(r["counter_progress_valid"])

    def test_transition_gap_is_metadata_not_restart_recovery(self):
        r = assess_m05_heartbeat_window([
            row(1, when=NOW-254), row(1, invocation="b"*32)], now_epoch=NOW)
        self.assertEqual(r["invocation_transitions"], [{
            "previous_heartbeat_epoch": NOW-254,
            "next_heartbeat_epoch": NOW, "gap_seconds": 254}])
        self.assertFalse(r["restart_recovery_proven"])
        self.assertNotIn("b"*32, json.dumps(r))

    def test_transition_output_bounded(self):
        rows = [row(1, invocation=format(i, "032x"), when=NOW-20+i)
                for i in range(12)]
        r = assess_m05_heartbeat_window(rows, now_epoch=NOW)
        self.assertEqual(len(r["invocation_transitions"]), 8)
        self.assertTrue(r["invocation_transitions_truncated"])


if __name__ == "__main__":
    unittest.main()
