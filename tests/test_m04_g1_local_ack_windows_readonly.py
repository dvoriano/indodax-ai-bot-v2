"""Offline local G1 ACK window audit regression; no network or service action."""
import json
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess

from scripts import m04_g1_local_ack_windows_readonly as m

END = 1795500780
PAIR = "anoa_idr"


def record(stamp, *, requested=(PAIR,), ack=(PAIR,), pid="123",
           invocation="a"*32, receipt=None):
    obj = {"status": "M03_WS3_CONTINUOUS_CHECKPOINT",
           "observed_epoch": stamp, "indexed_trade_pairs": list(requested),
           "execution_allowed": False}
    if ack is not None:
        obj["indexed_trade_pairs_acknowledged"] = list(ack)
        obj["indexed_trade_channels_acknowledged"] = len(ack)
    return {"MESSAGE": json.dumps(obj), "_PID": pid,
            "_SYSTEMD_INVOCATION_ID": invocation,
            "__REALTIME_TIMESTAMP": str(
                (stamp if receipt is None else receipt)*1000000)}


class LocalAckWindowTests(unittest.TestCase):
    def summarize(self, records):
        return m.summarize_local_ack_windows(
            records=records, pairs=[PAIR], end_epoch=END)

    def test_all_ack_is_sample_only_and_boundary_gap_visible(self):
        r = self.summarize([record(END-900), record(END-60)])
        w = r["windows"]["15m"]
        self.assertEqual(w["checkpoint_samples"], 2)
        self.assertEqual(w["max_interval_without_checkpoint_including_boundaries_seconds"], 840)
        self.assertTrue(w["pairs"][0]["all_nonempty_checkpoint_samples_ack_listed"])
        self.assertFalse(w["pairs"][0]["continuous_subscription_verified"])
        self.assertFalse(r["independent_g1_verified"])
        self.assertFalse(r["execution_allowed"])

    def test_legacy_absent_and_unacknowledged_distinguished(self):
        r = self.summarize([
            record(END-1800, ack=None),
            record(END-120, ack=()),
            record(END-60, requested=(), ack=()),
        ])
        row = r["windows"]["1h"]["pairs"][0]
        self.assertEqual(row["local_ack_unknown_legacy_checkpoints"], 1)
        self.assertEqual(row["local_ack_not_listed_checkpoints"], 2)
        self.assertEqual(row["request_not_listed_checkpoints"], 1)
        self.assertEqual(r["windows"]["15m"]["legacy_checkpoint_samples"], 0)

    def test_distinct_process_and_invocation_metadata(self):
        r = self.summarize([
            record(END-1800, pid="123"),
            record(END-900, pid="456", invocation="b"*32),
            record(END-60, pid=None, invocation=None),
        ])
        w = r["windows"]["1h"]
        self.assertEqual(w["distinct_observed_pids"], 2)
        self.assertEqual(w["distinct_observed_invocations"], 2)
        self.assertEqual(w["checkpoints_missing_pid"], 1)
        self.assertEqual(w["checkpoints_missing_invocation"], 1)
        self.assertTrue(w["multiple_process_or_invocation_ids_observed"])
        self.assertTrue(w["absence_of_transition_does_not_prove_no_reconnect"])

    def test_closed_boundaries_and_late_receipts(self):
        r = self.summarize([
            record(END-3601), record(END-3600), record(END-901),
            record(END-900), record(END-2, receipt=END),
            record(END-1), record(END),
        ])
        self.assertEqual(r["windows"]["1h"]["checkpoint_samples"], 4)
        self.assertEqual(r["windows"]["15m"]["checkpoint_samples"], 2)

    def test_missing_and_conflicting_checkpoints_fail_closed(self):
        missing = self.summarize([])
        w = missing["windows"]["1h"]
        self.assertEqual(w["checkpoint_samples"], 0)
        self.assertEqual(w["max_interval_without_checkpoint_including_boundaries_seconds"], 3600)
        self.assertFalse(w["pairs"][0]["all_nonempty_checkpoint_samples_ack_listed"])
        for rows in (
            [record(END-1, ack=("other_idr",))],
            [record(END-1), record(END-1, ack=())],
            [record(END-1, receipt=END-2)],
        ):
            self.assertEqual(self.summarize(rows)["status"],
                             "ACK_WINDOW_CHECKPOINT_CONTRACT_BLOCKED")

    def test_journal_bounded_and_explicit_original_unit(self):
        data = json.dumps(record(END-1)).encode()+b"\n"
        with patch.object(m.subprocess, "run", return_value=CompletedProcess([],0,data,b"")) as run:
            self.assertEqual(len(m._journal_read(END)), 1)
            command = run.call_args.args[0]
            self.assertIn(m._UNIT, command)
            self.assertIn(str(m._MAX_RECORDS+1), command)
            self.assertNotIn("-f", command)
        with patch.object(m, "_MAX_RECORDS", 1), patch.object(
            m.subprocess, "run", return_value=CompletedProcess([],0,data*2,b"")
        ):
            with self.assertRaisesRegex(ValueError, "JOURNAL_RECORD_CAP"):
                m._journal_read(END)


if __name__ == "__main__":
    unittest.main()
