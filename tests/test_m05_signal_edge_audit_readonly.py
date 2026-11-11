"""Synthetic offline tests for owner-private M05 post-ACK price-only audit."""
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.m05_signal_edge_audit_readonly import (
    inspect_postack_quality_readonly, post_ack_price_proxy,
)

NOW = 1794330000


def timeline():
    return {
        "status": "M03_WS3_ORIGINAL_SOURCE_CHRONOLOGICAL_QUALITY_TIMELINE",
        "owner_radar_delivery_event_source_status":
            "PER_PAIR_DELIVERY_EVENTS_AVAILABLE",
        "pairs": [{
            "pair": "uai_idr",
            "first_m04_micro_prealert_top2_replay_minute_end_wib":
                "2026-11-10 23:59:59 WIB",
            "first_m03_watch_top40_replay_minute_end_wib":
                "2026-11-11 00:00:59 WIB",
            "first_m05_top5_eligible_replay_minute_end_wib":
                "2026-11-11 00:00:59 WIB",
            "first_m04_quality_top2_replay_minute_end_wib":
                "2026-11-11 00:00:59 WIB",
        }],
    }


class PriceProxyTests(unittest.TestCase):
    def test_5m_gross_and_explicit_cost_scenario_not_fill(self):
        quotes = [(NOW + 70, Decimal("101")),
                  (NOW + 370, Decimal("102"))]
        r = post_ack_price_proxy(
            quotes, ack_epoch=NOW + 31, horizon_seconds=300,
            now_epoch=NOW + 600, total_cost_bps=Decimal("50"))
        self.assertEqual(r["status"], "OBSERVED_PRICE_ONLY_NOT_EXECUTABLE")
        self.assertEqual(r["gross_move_bps"], "99.01")
        self.assertEqual(r["assumed_cost_adjusted_bps"], "49.01")

    def test_no_data_after_ack_cannot_become_entry(self):
        r = post_ack_price_proxy(
            [(NOW + 20, Decimal("100")),
             (NOW + 370, Decimal("110"))],
            ack_epoch=NOW + 31, horizon_seconds=300,
            now_epoch=NOW + 1000)
        self.assertEqual(r["status"], "NO_FRESH_POST_ACK_ENTRY_QUOTE")
        self.assertIsNone(r["gross_move_bps"])

    def test_missing_exit_is_not_loss(self):
        r = post_ack_price_proxy(
            [(NOW + 70, Decimal("101"))],
            ack_epoch=NOW + 31, horizon_seconds=300,
            now_epoch=NOW + 1000)
        self.assertEqual(r["status"], "NO_FRESH_HORIZON_QUOTE")
        self.assertIsNone(r["gross_move_bps"])

    def test_unmatured_horizon_not_measurable(self):
        r = post_ack_price_proxy(
            [], ack_epoch=NOW + 31, horizon_seconds=900,
            now_epoch=NOW + 400)
        self.assertEqual(r["status"], "HORIZON_NOT_MATURE")

    def test_invalid_horizon_fail_closed(self):
        with self.assertRaises(ValueError):
            post_ack_price_proxy([], ack_epoch=NOW, horizon_seconds=60,
                                 now_epoch=NOW + 1000)


class OriginalOwnerJoinTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        base = Path(t.name)
        self.market = base / "decisions.sqlite3"
        self.owner = base / "owner.sqlite3"
        with sqlite3.connect(self.market) as db:
            db.execute("""
                CREATE TABLE m03_ws_summary_watch_minute(
                  pair TEXT NOT NULL, minute_epoch INTEGER NOT NULL,
                  exchange_epoch INTEGER NOT NULL, last_idr TEXT NOT NULL,
                  rolling_24h_idr TEXT NOT NULL, public_offset INTEGER NOT NULL,
                  PRIMARY KEY(pair,minute_epoch))
            """)
            for minute, epoch, price, offset in [
                (NOW, NOW + 20, "100", 1),
                (NOW + 60, NOW + 70, "101", 2),
                (NOW + 360, NOW + 370, "102", 3),
                (NOW + 960, NOW + 970, "99", 4),
            ]:
                db.execute(
                    "INSERT INTO m03_ws_summary_watch_minute VALUES(?,?,?,?,?,?)",
                    ("uai_idr", minute, epoch, price, "500000000", offset))
        with sqlite3.connect(self.owner) as db:
            db.execute("""
                CREATE TABLE m05_radar_delivery_events (
                    pair TEXT NOT NULL, claimed_epoch INTEGER NOT NULL,
                    source_observed_epoch INTEGER NOT NULL,
                    source_kind TEXT NOT NULL,
                    delivery_state TEXT NOT NULL,
                    ack_received_epoch INTEGER, telegram_message_id INTEGER
                )
            """)
            db.execute(
                "INSERT INTO m05_radar_delivery_events VALUES(?,?,?,?,?,?,?)",
                ("uai_idr", NOW + 30, NOW + 20, "WS3",
                 "ACKED", NOW + 31, 9))
        self.market.chmod(0o600)
        self.owner.chmod(0o600)

    def audit(self, **kwargs):
        with patch(
            "scripts.m05_signal_edge_audit_readonly."
            "replay_original_ws3_quality_timeline_readonly",
            return_value=timeline(),
        ):
            return inspect_postack_quality_readonly(
                start_epoch=NOW, end_epoch=NOW + 60,
                pairs=("uai_idr",), db_path=self.market,
                radar_claims_db=self.owner,
                now_epoch=NOW + 2000, **kwargs)

    def make_research_archive(self, *, first_recorded_epoch=NOW + 28,
                              first_status="ELIGIBLE_SOURCE_OBSERVATION"):
        folder = self.market.parent / "m03-ws3-evidence"
        folder.mkdir(mode=0o700, exist_ok=True)
        archive = folder / "observations.sqlite3"
        with sqlite3.connect(archive) as db:
            db.execute("""
                CREATE TABLE ws3_source_cycles(
                    minute_epoch INTEGER PRIMARY KEY,
                    recorded_local_epoch INTEGER NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE ws3_source_evidence(
                    pair TEXT NOT NULL, minute_epoch INTEGER NOT NULL,
                    source_exchange_epoch INTEGER, last_idr TEXT,
                    source_status TEXT NOT NULL,
                    PRIMARY KEY(pair,minute_epoch)
                )
            """)
            for minute, epoch, price, status, recorded in (
                (NOW, NOW + 20, "100", first_status,
                 first_recorded_epoch),
                (NOW + 60, NOW + 70, "101",
                 "ELIGIBLE_SOURCE_OBSERVATION", NOW + 80),
                (NOW + 360, NOW + 370, "102",
                 "BELOW_150M_VOLUME_FLOOR", NOW + 380),
                (NOW + 960, NOW + 970, "99",
                 "ELIGIBLE_SOURCE_OBSERVATION", NOW + 980),
            ):
                db.execute(
                    "INSERT INTO ws3_source_cycles VALUES(?,?)",
                    (minute, recorded),
                )
                db.execute(
                    "INSERT INTO ws3_source_evidence VALUES(?,?,?,?,?)",
                    ("uai_idr", minute, epoch, price, status),
                )
        archive.chmod(0o600)
        return archive

    def test_research_archive_recovers_exact_ack_after_original_retention(self):
        archive = self.make_research_archive()
        with sqlite3.connect(self.market) as db:
            db.execute("DELETE FROM m03_ws_summary_watch_minute")
        baseline = self.audit()
        self.assertEqual(baseline["status_counts"]["ACK_NOT_MATCHED_WS3_SOURCE"], 1)
        recovered = self.audit(research_evidence_db=archive)
        self.assertEqual(
            recovered["status_counts"]["MATCHED_WS3_ACK_RESEARCH_ARCHIVE"], 1
        )
        event = recovered["events"][0]
        self.assertTrue(event["source_quote_matched"])
        self.assertEqual(
            event["source_match_provenance"],
            "M03_WS3_PRIVATE_RESEARCH_ARCHIVE",
        )
        self.assertEqual(event["source_to_ack_seconds"], 11)
        self.assertFalse(event["source_replay_retained_original_quotes"])
        self.assertIsNone(event["first_micro_prealert_replay_wib"])
        self.assertEqual(event["price_proxies"]["5m"]["gross_move_bps"],
                         "99.01")
        self.assertEqual(event["price_proxies"]["15m"]["status"],
                         "OBSERVED_PRICE_ONLY_NOT_EXECUTABLE")
        self.assertTrue(recovered[
            "research_archive_cannot_reconstruct_original_m04_micro_replay"])
        self.assertEqual(recovered["exchange_requests"], 0)
        self.assertEqual(recovered["telegram_messages_sent"], 0)
        self.assertEqual(recovered["orders_sent"], 0)

    def test_research_record_after_claim_does_not_backfill_source_match(self):
        archive = self.make_research_archive(
            first_recorded_epoch=NOW + 40
        )
        with sqlite3.connect(self.market) as db:
            db.execute("DELETE FROM m03_ws_summary_watch_minute")
        r = self.audit(research_evidence_db=archive)
        self.assertEqual(r["status_counts"]["ACK_NOT_MATCHED_WS3_SOURCE"], 1)
        self.assertEqual(r["events"][0]["price_proxies"], {})
        self.assertIsNone(r["events"][0]["source_to_ack_seconds"])

    def test_below_floor_research_quote_cannot_prove_watch_source(self):
        archive = self.make_research_archive(
            first_status="BELOW_150M_VOLUME_FLOOR"
        )
        with sqlite3.connect(self.market) as db:
            db.execute("DELETE FROM m03_ws_summary_watch_minute")
        r = self.audit(research_evidence_db=archive)
        self.assertEqual(r["status_counts"]["ACK_NOT_MATCHED_WS3_SOURCE"], 1)

    def test_research_archive_insecure_mode_or_symlink_rejected(self):
        archive = self.make_research_archive()
        archive.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "RESEARCH_EVIDENCE_DB_UNTRUSTED"):
            self.audit(research_evidence_db=archive)
        archive.chmod(0o600)
        link = archive.parent / "aliased.sqlite3"
        link.symlink_to(archive)
        with self.assertRaisesRegex(ValueError, "RESEARCH_EVIDENCE_DB_UNTRUSTED"):
            self.audit(research_evidence_db=link)

    def test_research_exact_pair_epoch_required_not_just_timestamp(self):
        archive = self.make_research_archive()
        with sqlite3.connect(self.market) as db:
            db.execute("DELETE FROM m03_ws_summary_watch_minute")
        with sqlite3.connect(archive) as db:
            db.execute(
                "UPDATE ws3_source_evidence SET source_exchange_epoch=? "
                "WHERE minute_epoch=?",
                (NOW + 19, NOW),
            )
        r = self.audit(research_evidence_db=archive)
        self.assertEqual(r["status_counts"]["ACK_NOT_MATCHED_WS3_SOURCE"], 1)

    def test_owner_ack_matches_exact_real_schema_quote(self):
        r = self.audit()
        self.assertEqual(r["events_inspected"], 1)
        self.assertEqual(r["status_counts"]["MATCHED_WS3_ACK"], 1)
        event = r["events"][0]
        self.assertEqual(event["source_to_ack_seconds"], 11)
        self.assertEqual(event["claim_to_ack_seconds"], 1)
        self.assertTrue(event["source_quote_matched"])
        self.assertEqual(event["price_proxies"]["5m"]["gross_move_bps"],
                         "99.01")
        self.assertIsNone(
            event["price_proxies"]["5m"]["assumed_cost_adjusted_bps"])
        self.assertTrue(r["replay_stage_milestones_not_production_trigger_latency"])
        self.assertFalse(r["execution_allowed"])
        self.assertEqual(r["telegram_messages_sent"], 0)
        self.assertEqual(r["orders_sent"], 0)
        self.assertEqual(r["exchange_requests"], 0)

    def test_optional_cost_assumption_never_claims_real_net_pnl(self):
        r = self.audit(fee_bps="30", slippage_bps="20")
        self.assertEqual(r["events"][0]["price_proxies"]["5m"][
            "assumed_cost_adjusted_bps"], "49.01")
        self.assertTrue(r["cost_adjusted_price_move_is_not_real_net_pnl"])

    def test_one_cost_missing_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "BOTH_COST_ASSUMPTIONS"):
            self.audit(fee_bps="30")

    def test_invalid_outcome_cannot_match_source(self):
        with sqlite3.connect(self.owner) as db:
            db.execute(
                "UPDATE m05_radar_delivery_events SET source_observed_epoch=?",
                (NOW + 19,))
        r = self.audit()
        self.assertEqual(r["status_counts"]["ACK_NOT_MATCHED_WS3_SOURCE"], 1)
        self.assertEqual(r["events"][0]["price_proxies"], {})
        self.assertIsNone(r["events"][0]["source_to_ack_seconds"])

    def test_uncertain_has_no_returns_or_false_ack(self):
        with sqlite3.connect(self.owner) as db:
            db.execute(
                "UPDATE m05_radar_delivery_events "
                "SET delivery_state='UNCERTAIN', ack_received_epoch=NULL")
        r = self.audit()
        self.assertEqual(r["status_counts"]["NO_CONFIRMED_ACK"], 1)
        self.assertEqual(r["events"][0]["price_proxies"], {})
        self.assertIsNone(r["events"][0]["claim_to_ack_seconds"])

    def test_source_must_be_private_regular(self):
        self.market.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "SOURCE_NOT_TRUSTED"):
            self.audit()

    def test_scope_requires_exact_minute_and_bounded_window(self):
        with self.assertRaisesRegex(ValueError, "INVALID_POSTACK_AUDIT_SCOPE"):
            with patch(
                "scripts.m05_signal_edge_audit_readonly."
                "replay_original_ws3_quality_timeline_readonly",
                return_value=timeline(),
            ):
                inspect_postack_quality_readonly(
                    start_epoch=NOW + 1, end_epoch=NOW + 60,
                    pairs=("uai_idr",), db_path=self.market,
                    radar_claims_db=self.owner, now_epoch=NOW + 2000)


if __name__ == "__main__":
    unittest.main()
