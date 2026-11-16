"""Offline fixtures for original M05 owner priority ACK research, NO orders."""
from __future__ import annotations

from decimal import Decimal
import json
import unittest

from scripts.m05_strategy_a_priority_receipts_readonly import (
    process_start_epoch_from_proc,
    assess_ack_source_asof_offline,
    summarize_priority_receipts_offline,
)

CLAIM = 1794471200
SOURCE = CLAIM - 18
PAIR = "uai_idr"


def rows(*, latest="102", turnover="500000000", recorded=None,
         ref="100", age=120, absent=False):
    if absent:
        return []
    return [
        (SOURCE // 60 * 60, SOURCE, latest, turnover,
         "ELIGIBLE_SOURCE_OBSERVATION",
         CLAIM - 3 if recorded is None else recorded),
        (SOURCE // 60 * 60 - age, SOURCE - age, ref,
         "499000000", "ELIGIBLE_SOURCE_OBSERVATION",
         CLAIM - 80),
    ]


def event(*, source_kind="WS3", delivery="ACKED", claim=CLAIM,
          source=SOURCE, ack=None):
    return (PAIR, claim, source, source_kind, delivery,
            claim + 3 if delivery == "ACKED" and ack is None else ack)


class OriginalM05PriorityAckResearchTests(unittest.TestCase):
    def test_asof_reconstruction_meets_both_owner_thresholds(self):
        r = assess_ack_source_asof_offline(
            source_kind="WS3", source_epoch=SOURCE,
            claim_epoch=CLAIM, rows=rows())
        self.assertEqual(r["source_verdict"],
                         "ASOF_ARCHIVE_PRIORITY_THRESHOLDS_OBSERVED")
        self.assertTrue(r["priority_threshold_reconstructed"])
        self.assertEqual(r["observed_rise_pct"], "2.0000")
        self.assertEqual(r["rolling_24h_idr"], "500000000")

    def test_below_price_or_turnover_threshold_is_review_not_false_trade(self):
        for kw in ({"latest": "101.49"}, {"turnover": "399999999"}):
            with self.subTest(kw=kw):
                r = assess_ack_source_asof_offline(
                    source_kind="WS3", source_epoch=SOURCE,
                    claim_epoch=CLAIM, rows=rows(**kw))
                self.assertEqual(r["source_verdict"],
                                 "ASOF_ARCHIVE_THRESHOLD_MISMATCH_REVIEW")
                self.assertFalse(r["priority_threshold_reconstructed"])

    def test_exact_boundary_is_accepted(self):
        r = assess_ack_source_asof_offline(
            source_kind="WS3", source_epoch=SOURCE,
            claim_epoch=CLAIM, rows=rows(latest="101.5",
                                        turnover="400000000"))
        self.assertTrue(r["priority_threshold_reconstructed"])

    def test_missing_reference_or_after_claim_source_unverified(self):
        missing = assess_ack_source_asof_offline(
            source_kind="WS3", source_epoch=SOURCE,
            claim_epoch=CLAIM, rows=rows(absent=True))
        self.assertEqual(missing["source_verdict"],
                         "EXACT_ASOF_WS3_SOURCE_UNAVAILABLE")
        future = assess_ack_source_asof_offline(
            source_kind="WS3", source_epoch=SOURCE,
            claim_epoch=CLAIM, rows=rows(recorded=CLAIM+1))
        self.assertEqual(future["source_verdict"],
                         "EXACT_ASOF_WS3_SOURCE_UNAVAILABLE")
        ref_missing = assess_ack_source_asof_offline(
            source_kind="WS3", source_epoch=SOURCE,
            claim_epoch=CLAIM, rows=rows()[:1])
        self.assertEqual(ref_missing["source_verdict"],
                         "ASOF_REFERENCE_MINUTE_UNAVAILABLE")

    def test_rest_alert_never_promoted_to_ws3_verified(self):
        r = assess_ack_source_asof_offline(
            source_kind="REST", source_epoch=SOURCE,
            claim_epoch=CLAIM, rows=rows())
        self.assertEqual(r["source_verdict"],
                         "REST_SOURCE_NOT_RECONSTRUCTED")
        self.assertFalse(r["priority_threshold_reconstructed"])

    def test_claim_is_not_ack_and_unknown_is_not_failure(self):
        events = [
            event(delivery="CLAIMED", ack=None),
            (PAIR, CLAIM + 15, SOURCE + 15, "WS3", "UNCERTAIN", None),
        ]
        r = summarize_priority_receipts_offline(
            pid_start=CLAIM-100, now_epoch=CLAIM+100,
            events=events, source_reader=lambda *args: rows())
        self.assertEqual(r["status"], "M05_PRIORITY_NO_NEW_ACK_YET_READONLY")
        self.assertEqual(r["acked"], 0)
        self.assertEqual(r["claimed_unconfirmed"], 1)
        self.assertEqual(r["delivery_uncertain"], 1)
        self.assertEqual(r["asof_threshold_reconstructed_count"], 0)
        self.assertFalse(r["execution_allowed"])

    def test_natural_botapi_ack_with_asof_matching_source(self):
        r = summarize_priority_receipts_offline(
            pid_start=CLAIM-100, now_epoch=CLAIM+100,
            events=[event()], source_reader=lambda *args: rows())
        self.assertEqual(r["status"], "M05_PRIORITY_ACK_EVENTS_OBSERVED_READONLY")
        self.assertEqual(r["acked"], 1)
        self.assertEqual(r["asof_threshold_reconstructed_count"], 1)
        self.assertTrue(r["ack_is_bot_api_only_not_owner_device_receipt"])
        self.assertTrue(r["asof_reconstruction_not_original_m05_rank_receipt"])
        self.assertFalse(r["execution_allowed"])
        self.assertEqual(r["telegram_messages_sent_by_audit"], 0)
        self.assertEqual(r["orders_sent"], 0)

    def test_independent_claim_not_misclassified_as_momentum_mismatch(self):
        def reader(*args):
            raise AssertionError("independent route must not apply legacy threshold")
        r=summarize_priority_receipts_offline(pid_start=CLAIM-100,
            now_epoch=CLAIM+100,events=[event()+("INDEPENDENT_FOUR_GREEN",4)],
            source_reader=reader)
        self.assertEqual(r["independent_four_green_acked"],1)
        self.assertEqual(r["archive_mismatch_needs_review"],0)
        self.assertEqual(r["events"][0]["green_at_claim"],4)
        self.assertTrue(r["events"][0]["local_green_claim_not_independent_attestation"])
        with self.assertRaises(ValueError):
            summarize_priority_receipts_offline(pid_start=CLAIM-100,
                now_epoch=CLAIM+100,events=[event()+("INDEPENDENT_FOUR_GREEN",3)],
                source_reader=reader)

    def test_source_missing_and_no_alert_are_inconclusive_not_zero_profit(self):
        missing = summarize_priority_receipts_offline(
            pid_start=CLAIM-100, now_epoch=CLAIM+100,
            events=[event()], source_reader=lambda *args: [])
        self.assertEqual(missing["acked"], 1)
        self.assertEqual(missing["missing_or_unverified_provenance"], 1)
        self.assertEqual(missing["asof_threshold_reconstructed_count"], 0)
        empty = summarize_priority_receipts_offline(
            pid_start=CLAIM-100, now_epoch=CLAIM+100,
            events=[], source_reader=lambda *args: [])
        self.assertEqual(empty["status"], "M05_PRIORITY_NO_NEW_ACK_YET_READONLY")
        self.assertEqual(empty["event_count"], 0)

    def test_old_process_event_cannot_be_misattributed(self):
        with self.assertRaises(ValueError):
            summarize_priority_receipts_offline(
                pid_start=CLAIM+1, now_epoch=CLAIM+100,
                events=[event()], source_reader=lambda *args: rows())

    def test_no_private_owner_id_message_id_token_emitted(self):
        r = summarize_priority_receipts_offline(
            pid_start=CLAIM-100, now_epoch=CLAIM+100,
            events=[event()], source_reader=lambda *args: rows())
        raw = json.dumps(r)
        for word in ("telegram_message_id", "owner_user_id",
                     "BOT_TOKEN", "private_key", "api_key", "owner_db"):
            self.assertNotIn(word, raw)

    def test_linux_proc_stat_start_time_parser(self):
        # Fields after ") " are original Linux fields 3 onward; start is #22.
        fields = ["S"] + ["0"] * 18 + ["12000"] + ["0"] * 20
        stat = "1234 (M05 worker) " + " ".join(fields)
        self.assertEqual(process_start_epoch_from_proc(
            stat_text=stat, uptime_seconds=130.0,
            now_epoch=10000, tick_hz=100), 9990)
        with self.assertRaises(ValueError):
            process_start_epoch_from_proc(
                stat_text="bad", uptime_seconds=130.0,
                now_epoch=10000, tick_hz=100)


if __name__ == "__main__":
    unittest.main()
