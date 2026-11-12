"""Offline synthetic quality/safety tests for M05 WS3 as-of peer comparison."""
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest

from scripts.m05_ws3_asof_control_price_audit_readonly import (
    compare_asof_ws3_controls_readonly,
    select_asof_controls,
)
from decimal import Decimal

T = 1794330000  # Fixed minute-aligned synthetic epoch.


class OriginalAsOfControlsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        parent = Path(folder.name)
        self.owner = parent / "owner.sqlite3"
        directory = parent / "m03-ws3-evidence"
        directory.mkdir(mode=0o700)
        self.research = directory / "observations.sqlite3"
        with sqlite3.connect(self.owner) as db:
            db.execute(
                "CREATE TABLE m05_radar_delivery_events ("
                "pair TEXT,claimed_epoch INTEGER,"
                "source_observed_epoch INTEGER,source_kind TEXT,"
                "delivery_state TEXT,ack_received_epoch INTEGER)"
            )
            db.execute(
                "INSERT INTO m05_radar_delivery_events VALUES (?,?,?,?,?,?)",
                ("uai_idr", T + 50, T + 20, "WS3", "ACKED", T + 52),
            )
            # Future control crossover must NOT be used at source selection.
            db.execute(
                "INSERT INTO m05_radar_delivery_events VALUES (?,?,?,?,?,?)",
                ("xrp_idr", T + 600, T + 550, "WS3", "CLAIMED", None),
            )
        self.owner.chmod(0o600)
        with sqlite3.connect(self.research) as db:
            db.execute(
                "CREATE TABLE ws3_source_cycles ("
                "minute_epoch INTEGER PRIMARY KEY,"
                "recorded_local_epoch INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE TABLE ws3_source_evidence ("
                "pair TEXT,minute_epoch INTEGER,"
                "source_exchange_epoch INTEGER,last_idr TEXT,"
                "rolling_24h_idr TEXT,source_status TEXT,"
                "PRIMARY KEY(pair,minute_epoch))"
            )
            observations = (
                ("uai_idr", T, 20, 100, 500000000, 35),
                ("xrp_idr", T, 25, 200, 450000000, 35),
                ("ada_idr", T, 22, 300, 1200000000, 35),
                ("uai_idr", T + 60, 66, 100, 500000000, 88),
                ("xrp_idr", T + 60, 70, 200, 450000000, 88),
                ("uai_idr", T + 360, 365, 110, 500000000, 390),
                ("xrp_idr", T + 360, 367, 202, 450000000, 390),
                ("uai_idr", T + 960, 965, 115, 500000000, 988),
                ("xrp_idr", T + 960, 967, 201, 450000000, 988),
            )
            for minute in sorted({x[1] for x in observations}):
                # Correct recorded epoch of a source cycle once per minute.
                recorded = next(x[5] for x in observations if x[1] == minute)
                db.execute(
                    "INSERT INTO ws3_source_cycles VALUES (?,?)",
                    (minute, T + recorded),
                )
            for pair, minute, offset, price, turnover, recorded in observations:
                db.execute(
                    "INSERT INTO ws3_source_evidence VALUES (?,?,?,?,?,?)",
                    (pair, minute, T + offset, str(price),
                     str(turnover), "ELIGIBLE_SOURCE_OBSERVATION"),
                )
        self.research.chmod(0o600)

    def audit(self):
        return compare_asof_ws3_controls_readonly(
            start_epoch=T, end_epoch=T,
            pairs=("uai_idr",), owner_db=self.owner,
            research_db=self.research, now_epoch=T + 2000,
        )

    def test_future_alert_does_not_disqualify_valid_asof_control(self):
        r = self.audit()
        self.assertEqual(r["status"], "M05_WS3_ASOF_CONTROL_PRICE_AUDIT_READONLY")
        self.assertEqual(r["events"], 1)
        self.assertEqual(r["matched_alerts"], 1)
        self.assertEqual(r["alerts_with_controls"], 1)
        self.assertEqual(r["control_selections"], 1)
        self.assertEqual(r["five_min_paired_events"], 1)
        self.assertEqual(r["fifteen_min_paired_events"], 1)
        event = r["cohort"][0]
        self.assertEqual([x["pair"] for x in event["controls"]], ["xrp_idr"])
        self.assertTrue(event["controls"][0]["became_alert_within_next_15m"])
        self.assertEqual(event["paired_outcomes"]["5m"]["alert_gross_bps"],
                         "1000.00")
        self.assertEqual(event["paired_outcomes"]["5m"][
            "control_mean_gross_bps"], "100.00")
        self.assertEqual(event["paired_outcomes"]["5m"]["difference_bps"],
                         "900.00")
        self.assertTrue(r["control_matching_uses_asof_claim_data_only"])
        self.assertTrue(r["same_control_pairs_may_repeat_not_independent"])
        self.assertEqual(r["telegram_messages_sent"], 0)
        self.assertEqual(r["exchange_requests"], 0)
        self.assertEqual(r["paid_provider_calls"], 0)
        self.assertEqual(r["orders_sent"], 0)
        self.assertFalse(r["execution_allowed"])

    def test_prior_claim_excludes_control(self):
        with sqlite3.connect(self.owner) as db:
            db.execute(
                "UPDATE m05_radar_delivery_events SET "
                "claimed_epoch=? WHERE pair='xrp_idr'", (T - 20,),
            )
        r = self.audit()
        self.assertEqual(r["matched_alerts"], 1)
        self.assertEqual(r["control_selections"], 0)
        self.assertEqual(r["five_min_paired_events"], 0)

    def test_archive_written_after_alert_cannot_select_peer(self):
        with sqlite3.connect(self.research) as db:
            db.execute(
                "UPDATE ws3_source_cycles "
                "SET recorded_local_epoch=? WHERE minute_epoch=?",
                (T + 55, T),
            )
        r = self.audit()
        self.assertEqual(r["matched_alerts"], 0)
        self.assertEqual(r["alerts_with_controls"], 0)

    def test_missing_peer_horizon_does_not_invent_zero_return(self):
        with sqlite3.connect(self.research) as db:
            db.execute(
                "DELETE FROM ws3_source_evidence "
                "WHERE pair='xrp_idr' AND minute_epoch=?", (T + 360,),
            )
        r = self.audit()
        self.assertEqual(r["five_min_control_price_outcomes"], 0)
        self.assertEqual(r["five_min_paired_events"], 0)
        self.assertEqual(r["cohort"][0]["paired_outcomes"]["5m"]["status"],
                         "NO_PAIRED_FRESH_OUTCOME")

    def test_research_requires_owner_private_regular_and_0700_parent(self):
        self.research.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "SOURCE_UNTRUSTED"):
            self.audit()
        self.research.chmod(0o600)
        self.research.parent.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "RESEARCH_PARENT_UNTRUSTED"):
            self.audit()

    def test_scope_bounds_are_strict(self):
        with self.assertRaisesRegex(ValueError, "INVALID_CONTROL_AUDIT_SCOPE"):
            compare_asof_ws3_controls_readonly(
                start_epoch=T + 1, end_epoch=T,
                pairs=("uai_idr",), owner_db=self.owner,
                research_db=self.research, now_epoch=T + 2000,
            )


if __name__ == "__main__":
    unittest.main()
