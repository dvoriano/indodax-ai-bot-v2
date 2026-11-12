"""No-network, no-order regression for real original M05 WS3 ACK research gate."""
from __future__ import annotations

import contextlib
from io import StringIO
import json
import sqlite3
import unittest
from unittest.mock import patch

from indodax_ai_bot import m05_ws3_asof_quality_nonlive as m
from scripts import m05_ws3_asof_quality_readonly as cli

T = 1794471762
S = T - 20


def price(**updates):
    result = dict(
        minute_epoch=S//60*60, source_exchange_epoch=S,
        last_idr="100.20", rolling_24h_idr="500000000",
        public_offset=1234, source_status="ELIGIBLE_SOURCE_OBSERVATION",
        recorded_local_epoch=T-12,
    )
    result.update(updates)
    return tuple(result[k] for k in m.SOURCE_COLUMNS)


def micro(**updates):
    result = dict(
        minute_epoch=(T-7)//60*60, recorded_local_epoch=T-7,
        trade_frames=1, source_buy_count=1, source_sell_count=0,
        source_buy_idr="60000", source_sell_idr="0",
        last_trade_epoch=T-9, trade_offset=140,
        trade_gap_observed=0, trade_truncated=0,
        book_local_epoch=T-8, book_offset=150,
        best_bid_idr="100", best_ask_idr="100.2",
        spread_bps="19.9800", bid_depth_top5_idr="2000000",
        ask_depth_top5_idr="3000000", source_malformed=0,
        source_complete=0, aggressor_verified=0, g1_verified=0,
    )
    result.update(updates)
    return tuple(result[k] for k in m.MICRO_COLUMNS)


def assess(*, source=S, p=None, r=None):
    return m.classify_ws3_preclaim_quality_nonlive(
        pair="nbtnew_idr", claim_epoch=T, source_epoch=source,
        price_row=price() if p is None else p,
        micro_row=micro() if r is None else r,
    )


class M05WS3AsOfPureQualityTests(unittest.TestCase):
    def test_full_fresh_source_is_observed_not_ai_or_buy(self):
        out = assess()
        self.assertEqual(out["evidence_tier"], "TRADE_BOOK_OBSERVED")
        self.assertEqual(out["micro_vs_alert_source"],
                         "AFTER_ALERT_SOURCE_BEFORE_CLAIM")
        self.assertTrue(out["fresh_trade_observed"])
        self.assertTrue(out["fresh_book_observed"])
        self.assertFalse(out["original_m05_sender_consumed_micro_verified"])
        self.assertTrue(out["risk_veto"])
        self.assertFalse(out["g1_verified"])
        self.assertFalse(out["m04_actual_role_responses_verified"])
        self.assertFalse(out["buy_recommendation_allowed"])
        self.assertFalse(out["execution_allowed"])
        self.assertEqual(out["final_decision"], "REJECT")
        self.assertEqual(out["orders_sent"], 0)
        self.assertEqual(out["provider_calls_by_audit"], 0)

    def test_pre_source_micro_is_distinguished(self):
        out = assess(r=micro(
            minute_epoch=(T-25)//60*60,
            recorded_local_epoch=T-25,
            last_trade_epoch=T-27, book_local_epoch=T-26,
        ))
        self.assertEqual(out["evidence_tier"], "TRADE_BOOK_OBSERVED")
        self.assertEqual(out["micro_vs_alert_source"],
                         "AT_OR_BEFORE_ALERT_SOURCE")

    def test_price_only_missing_micro_is_unknown_not_zero_trades(self):
        out = assess(r=None)  # helper default is full; explicit manual case
        out = m.classify_ws3_preclaim_quality_nonlive(
            pair="nbtnew_idr", claim_epoch=T, source_epoch=S,
            price_row=price(), micro_row=None,
        )
        self.assertEqual(out["evidence_tier"], "PRICE_ONLY")
        self.assertEqual(out["micro_status"], "MICRO_UNKNOWN")
        self.assertIn("MICRO_NOT_CAPTURED_OR_NOT_RETAINED",
                      out["research_cautions"])

    def test_book_only_is_not_false_sell_or_buy(self):
        out = assess(r=micro(
            trade_frames=0, source_buy_count=None,
            source_sell_count=None, source_buy_idr=None,
            source_sell_idr=None, last_trade_epoch=None,
            trade_offset=None,
        ))
        self.assertEqual(out["evidence_tier"], "BOOK_ONLY")
        self.assertEqual(out["micro_status"], "FRESH_BOOK_TRADE_UNKNOWN")
        self.assertFalse(out["fresh_trade_observed"])

    def test_trade_only_book_missing(self):
        out = assess(r=micro(book_local_epoch=None, book_offset=None))
        self.assertEqual(out["evidence_tier"], "TRADE_ONLY")
        self.assertFalse(out["fresh_book_observed"])

    def test_missing_price_keeps_source_unverified_even_with_micro(self):
        out = m.classify_ws3_preclaim_quality_nonlive(
            pair="nbtnew_idr", claim_epoch=T, source_epoch=S,
            price_row=None, micro_row=micro(),
        )
        self.assertEqual(out["evidence_tier"], "SOURCE_UNVERIFIED")
        self.assertFalse(out["fresh_trade_observed"])

    def test_source_stale_151_seconds_is_not_promoted(self):
        earlier = T - 151
        out = assess(source=earlier, p=price(
            minute_epoch=earlier//60*60,
            source_exchange_epoch=earlier,
            recorded_local_epoch=earlier+10,
        ))
        self.assertEqual(out["evidence_tier"], "SOURCE_UNVERIFIED")

    def test_source_150_seconds_is_accepted_only_when_row_matches_epoch(self):
        earlier = T - 150
        out = assess(source=earlier, p=price(
            minute_epoch=earlier//60*60,
            source_exchange_epoch=earlier,
            recorded_local_epoch=earlier+10,
        ))
        self.assertEqual(out["evidence_tier"], "TRADE_BOOK_OBSERVED")
        out = assess(p=price(source_exchange_epoch=S+1))
        self.assertEqual(out["evidence_tier"], "SOURCE_UNVERIFIED")

    def test_future_source_cycle_is_rejected(self):
        out = assess(p=price(recorded_local_epoch=T+1))
        self.assertEqual(out["evidence_tier"], "SOURCE_UNVERIFIED")

    def test_price_below_original_150m_floor_is_rejected(self):
        out = assess(p=price(
            source_status="BELOW_150M_VOLUME_FLOOR",
            rolling_24h_idr="149999999",
        ))
        self.assertEqual(out["evidence_tier"], "SOURCE_UNVERIFIED")

    def test_stale_micro_capture_does_not_credit_old_data(self):
        old = T - 100
        out = assess(r=micro(
            minute_epoch=old//60*60, recorded_local_epoch=old,
            last_trade_epoch=old-1, book_local_epoch=old-1,
        ))
        self.assertEqual(out["micro_status"], "MICRO_STALE_AT_CLAIM")
        self.assertEqual(out["evidence_tier"], "PRICE_ONLY")

    def test_micro_after_claim_never_admitted(self):
        out = assess(r=micro(
            minute_epoch=(T+1)//60*60,
            recorded_local_epoch=T+1,
            last_trade_epoch=T,
            book_local_epoch=T,
        ))
        self.assertEqual(out["micro_status"], "MICRO_BAD_TIMELINE_OR_FUTURE")
        self.assertEqual(out["evidence_tier"], "PRICE_ONLY")

    def test_malformed_gapped_truncated_and_fake_authority_fail_closed(self):
        for fld in (
            "trade_gap_observed", "trade_truncated", "source_malformed",
            "source_complete", "aggressor_verified", "g1_verified"
        ):
            with self.subTest(field=fld):
                out = assess(r=micro(**{fld: 1}))
                self.assertEqual(out["evidence_tier"], "PRICE_ONLY")
                self.assertTrue(out["risk_veto"])

    def test_inconsistent_book_spread_is_not_accepted(self):
        out = assess(r=micro(spread_bps="999"))
        self.assertEqual(out["evidence_tier"], "TRADE_ONLY")
        self.assertFalse(out["fresh_book_observed"])

    def test_trade_timestamp_from_future_is_not_accepted(self):
        out = assess(r=micro(last_trade_epoch=T+1))
        self.assertEqual(out["evidence_tier"], "BOOK_ONLY")
        self.assertTrue(out["fresh_book_observed"])

    def test_source_reported_sells_only_add_caution_not_aggressor(self):
        out = assess(r=micro(
            source_buy_idr="10000", source_sell_idr="30000",
        ))
        self.assertEqual(out["evidence_tier"], "TRADE_BOOK_OBSERVED")
        self.assertIn("SOURCE_REPORTED_SELL_SIDE_HIGH_NOT_AGGRESSOR_PROOF",
                      out["research_cautions"])
        self.assertTrue(out["source_side_is_not_aggressor_proof"])

    def test_nan_source_price_and_depth_are_not_promoted(self):
        self.assertEqual(
            assess(p=price(last_idr="NaN"))["evidence_tier"],
            "SOURCE_UNVERIFIED",
        )
        self.assertEqual(
            assess(r=micro(bid_depth_top5_idr="Infinity"))["evidence_tier"],
            "TRADE_ONLY",
        )

    def test_malformed_source_and_claim_input_rejected(self):
        with self.assertRaises(ValueError):
            assess(source=T+1)
        with self.assertRaises(ValueError):
            assess(p=(1, 2))
        with self.assertRaises(ValueError):
            assess(r=(1, 2))
        with self.assertRaises(ValueError):
            m.classify_ws3_preclaim_quality_nonlive(
                pair="btc_usdt", claim_epoch=T, source_epoch=S,
                price_row=price(), micro_row=micro(),
            )


class M05WS3AsOfSQLiteTests(unittest.TestCase):
    def setUp(self):
        self.owner = sqlite3.connect(":memory:")
        self.archive = sqlite3.connect(":memory:")
        self.addCleanup(self.owner.close)
        self.addCleanup(self.archive.close)
        self.owner.execute("""
            CREATE TABLE m05_radar_delivery_events (
              pair TEXT, claimed_epoch INTEGER, source_observed_epoch INTEGER,
              source_kind TEXT, delivery_state TEXT, ack_received_epoch INTEGER
            )
        """)
        self.archive.execute("""
            CREATE TABLE ws3_source_evidence (
              pair TEXT, minute_epoch INTEGER, source_exchange_epoch INTEGER,
              last_idr TEXT, rolling_24h_idr TEXT, public_offset INTEGER,
              source_status TEXT
            )
        """)
        self.archive.execute("""
            CREATE TABLE ws3_source_cycles (
              minute_epoch INTEGER, recorded_local_epoch INTEGER
            )
        """)
        cols = ", ".join(
            field + " " + (
                "TEXT" if field in (
                    "source_buy_idr", "source_sell_idr", "best_bid_idr",
                    "best_ask_idr", "spread_bps", "bid_depth_top5_idr",
                    "ask_depth_top5_idr"
                ) else "INTEGER"
            ) for field in m.MICRO_COLUMNS
        )
        self.archive.execute(
            "CREATE TABLE ws3_micro_research_evidence "
            "(pair TEXT, " + cols + ")"
        )
        self.owner.execute(
            "INSERT INTO m05_radar_delivery_events VALUES (?,?,?,?,?,?)",
            ("nbtnew_idr", T, S, "WS3", "ACKED", T+1),
        )
        self.archive.execute(
            "INSERT INTO ws3_source_evidence VALUES (?,?,?,?,?,?,?)",
            ("nbtnew_idr", *price()[:6]),
        )
        self.archive.execute(
            "INSERT INTO ws3_source_cycles VALUES (?,?)",
            (price()[0], price()[-1]),
        )
        self.archive.execute(
            "INSERT INTO ws3_micro_research_evidence VALUES ("
            + ",".join("?" for _ in range(1 + len(m.MICRO_COLUMNS))) + ")",
            ("nbtnew_idr", *micro()),
        )
        self.owner.commit()
        self.archive.commit()

    def review(self):
        self.owner.execute("PRAGMA query_only=ON")
        self.archive.execute("PRAGMA query_only=ON")
        return m.audit_ws3_ack_cohort_nonlive(
            self.owner, self.archive, start_epoch=T-60, end_epoch=T+2,
        )

    def test_original_schema_join_pinned_ack_and_micro_readonly(self):
        res = self.review()
        self.assertEqual(res["status"], "M05_WS3_ASOF_QUALITY_REVIEW_READONLY")
        self.assertEqual(res["ws3_acked_reviewed"], 1)
        self.assertEqual(res["evidence_tier_counts"]["TRADE_BOOK_OBSERVED"], 1)
        self.assertTrue(res["events"][0]["risk_veto"])
        self.assertEqual(res["events"][0]["bot_api_ack_delay_sec"], 1)
        self.assertFalse(res["m04_actual_role_responses_verified"])
        self.assertEqual(res["orders_sent"], 0)

    def test_price_cycle_recorded_after_claim_fails_closed(self):
        self.archive.execute(
            "UPDATE ws3_source_cycles SET recorded_local_epoch=?",
            (T+10,),
        )
        out = self.review()
        self.assertEqual(out["evidence_tier_counts"]["SOURCE_UNVERIFIED"], 1)

    def test_micro_captured_after_claim_is_not_lookahead(self):
        self.archive.execute(
            "UPDATE ws3_micro_research_evidence SET recorded_local_epoch=?",
            (T+10,),
        )
        out = self.review()
        self.assertEqual(out["evidence_tier_counts"]["PRICE_ONLY"], 1)

    def test_no_source_micro_or_ack_table_is_not_positive(self):
        self.archive.execute("DROP TABLE ws3_source_evidence")
        self.archive.execute("DROP TABLE ws3_micro_research_evidence")
        out = self.review()
        self.assertEqual(out["evidence_tier_counts"]["SOURCE_UNVERIFIED"], 1)
        self.assertFalse(out["source_archive_available"])
        self.assertFalse(out["micro_archive_available"])

    def test_empty_cohort_is_no_ack_not_failure(self):
        self.owner.execute("DELETE FROM m05_radar_delivery_events")
        out = self.review()
        self.assertEqual(out["status"], "M05_WS3_NO_ACK_IN_WINDOW_READONLY")
        self.assertEqual(out["ws3_acked_reviewed"], 0)

    def test_pending_and_rest_rows_are_not_treated_as_ack(self):
        self.owner.execute(
            "INSERT INTO m05_radar_delivery_events VALUES (?,?,?,?,?,?)",
            ("test_idr", T, S, "REST", "ACKED", T+1),
        )
        self.owner.execute(
            "INSERT INTO m05_radar_delivery_events VALUES (?,?,?,?,?,?)",
            ("more_idr", T, S, "WS3", "CLAIMED", None),
        )
        out = self.review()
        self.assertEqual(out["ws3_acked_reviewed"], 1)
        self.assertEqual(out["rest_not_reconstructed"], 1)
        self.assertEqual(out["ws3_not_acked"], 1)

    def test_event_cap_and_ack_timeline_safety(self):
        for i in range(m.MAX_ACK_EVENTS + 1):
            self.owner.execute(
                "INSERT INTO m05_radar_delivery_events VALUES (?,?,?,?,?,?)",
                (f"x{i}_idr", T, S, "WS3", "CLAIMED", None),
            )
        with self.assertRaises(ValueError):
            self.review()

    def test_bad_ack_timestamp_blocked(self):
        self.owner.execute(
            "UPDATE m05_radar_delivery_events SET ack_received_epoch=?",
            (T-1,),
        )
        with self.assertRaises(ValueError):
            self.review()


class M05WS3AsOfOriginalWrapperSafetyTests(unittest.TestCase):
    def test_missing_owner_path_blocks_without_service_change(self):
        with (
            patch.object(cli, "_unit_pid", return_value=588864),
            patch.object(cli, "_process_env",
                         return_value={"owner_db": "/nonexistent/db.sqlite3"}),
        ):
            res = cli.inspect_original_m05_ws3_asof_quality_readonly(
                start_epoch=T-30, end_epoch=T,
            )
        self.assertEqual(
            res["status"], "M05_WS3_ASOF_QUALITY_AUDIT_BLOCKED_READONLY"
        )
        self.assertEqual(res["orders_sent"], 0)
        self.assertFalse(res["service_restarted"])

    def test_bad_cli_date_fails_closed_without_pid_or_db(self):
        with patch.object(cli, "_unit_pid") as unit, contextlib.redirect_stdout(StringIO()) as stream:
            code = cli.main(["--start-wib", "bad-date"])
        self.assertEqual(code, 2)
        self.assertIn("WINDOW_DATE_FORMAT_INVALID", stream.getvalue())
        unit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
