"""Pure offline tests for original M03 as-of micro risk, never trade entry."""
from __future__ import annotations

from unittest.mock import patch
import json
import unittest

from scripts import m05_strategy_a_asof_micro_risk_readonly as m

T = 1794471720  # fixed sample claim, not real dynamic market input


def data(**change):
    row = dict(
        minute_epoch=(T // 60) * 60,
        observed_local_epoch=T - 9,
        trade_frames=9,
        source_buy_count=6, source_sell_count=3,
        source_buy_idr="4000000", source_sell_idr="2000000",
        last_trade_epoch=T-8, trade_gap_observed=0, trade_truncated=0,
        book_local_epoch=T-5, best_bid_idr="100.00",
        best_ask_idr="100.20", spread_bps="19.9800",
        bid_depth_top5_idr="2000000", ask_depth_top5_idr="1500000",
        source_malformed=0,
    )
    row.update(change)
    return tuple(row[k] for k in m.COLUMNS)


class StrategyAAsOfMicroRiskTests(unittest.TestCase):
    def test_original_missing_micro_is_unknown_not_a_false_sell(self):
        r = m.assess_asof_micro_risk_nonlive(
            claimed_epoch=T, row=None)
        self.assertEqual(r["status"], "ASOF_MICRO_NOT_RETAINED_OR_NOT_CAPTURED")
        self.assertFalse(r["buy_recommendation_allowed"])
        self.assertFalse(r["execution_allowed"])
        self.assertEqual(r["orders_sent"], 0)
        self.assertTrue(r["source_reported_side_is_not_aggressor_verified"])

    def test_valid_partial_source_is_never_a_buy_authorization(self):
        r = m.assess_asof_micro_risk_nonlive(
            claimed_epoch=T, row=data())
        self.assertEqual(r["status"],
                         "ASOF_MICRO_PARTIAL_OBSERVED_NOT_BUY_PROOF")
        self.assertTrue(r["trade_activity_evidence_present"])
        self.assertTrue(r["book_snapshot_evidence_present"])
        self.assertFalse(r["g1_verified"])
        self.assertFalse(r["three_ai_role_responses_verified"])
        self.assertEqual(r["observed_spread_bps"], "19.9800")
        self.assertFalse(r["buy_recommendation_allowed"])

    def test_source_reported_sells_greater_than_buys_review_not_aggressor(self):
        r = m.assess_asof_micro_risk_nonlive(
            claimed_epoch=T, row=data(
                source_buy_idr="10000",source_sell_idr="18000",
            ))
        self.assertEqual(r["status"], "ASOF_MICRO_RISK_REVIEW_ONLY")
        self.assertIn("SOURCE_REPORTED_SELL_SIDE_EXCEEDS_BUY_SIDE",
                      r["risk_reasons"])
        self.assertTrue(r["source_reported_side_is_not_aggressor_verified"])

    def test_reject_gap_truncated_and_malformed(self):
        for key in ("trade_gap_observed", "trade_truncated",
                    "source_malformed"):
            with self.subTest(key=key):
                r = m.assess_asof_micro_risk_nonlive(
                    claimed_epoch=T, row=data(**{key: 1}))
                self.assertIn("TRADES_MALFORMED_GAPPED_OR_TRUNCATED",
                              r["risk_reasons"])
                self.assertFalse(r["execution_allowed"])

    def test_reject_postclaim_lookahead_and_stale_capture(self):
        r = m.assess_asof_micro_risk_nonlive(
            claimed_epoch=T, row=data(observed_local_epoch=T+1))
        self.assertEqual(r["status"], "ASOF_MICRO_MALFORMED_OR_FUTURE")
        r = m.assess_asof_micro_risk_nonlive(
            claimed_epoch=T, row=data(observed_local_epoch=T-95,
                                      minute_epoch=((T-120)//60)*60))
        self.assertEqual(r["status"], "ASOF_MICRO_STALE_BEFORE_CLAIM")

    def test_reject_future_trade_or_book_and_inconsistent_spread(self):
        for key, val in (
            ("last_trade_epoch",T+1),
            ("book_local_epoch",T+1),
            ("spread_bps","0.01"),
            ("best_ask_idr","99"),
        ):
            with self.subTest(key=key):
                r = m.assess_asof_micro_risk_nonlive(
                    claimed_epoch=T, row=data(**{key:val}))
                self.assertEqual(r["status"], "ASOF_MICRO_RISK_REVIEW_ONLY")
                if key=="last_trade_epoch":
                    self.assertIn("SOURCE_TRADE_ACTIVITY_UNVERIFIED",
                                  r["risk_reasons"])
                else:
                    self.assertIn("SOURCE_BOOK_MISSING_STALE_OR_INCONSISTENT",
                                  r["risk_reasons"])

    def test_research_wide_spread_and_thin_bid_depth(self):
        r = m.assess_asof_micro_risk_nonlive(
            claimed_epoch=T, row=data(
                best_ask_idr="101.20", spread_bps="119.2843",
                bid_depth_top5_idr="50000",
            ))
        # Recomputed published spread must agree; if not, fail closed.
        self.assertEqual(r["status"], "ASOF_MICRO_RISK_REVIEW_ONLY")
        self.assertFalse(r["buy_recommendation_allowed"])

    def test_invalid_shape_and_nonfinite_amounts_block(self):
        with self.assertRaises(ValueError):
            m.assess_asof_micro_risk_nonlive(claimed_epoch=T,row=(1,2))
        r = m.assess_asof_micro_risk_nonlive(
            claimed_epoch=T,row=data(source_buy_idr="NaN"))
        self.assertIn("SOURCE_TRADE_ACTIVITY_UNVERIFIED",
                      r["risk_reasons"])

    def test_no_secret_data_or_future_price_proxies_in_output(self):
        r = m.assess_asof_micro_risk_nonlive(
            claimed_epoch=T,row=data())
        js = json.dumps(r)
        for sensitive in ("OWNER_ID", "TELEGRAM_TOKEN", "owner_db",
                          "future_price", "actual_1m_candle",
                          "secret_api_key"):
            self.assertNotIn(sensitive,js)
        self.assertFalse(r["trading_allowed"])

    def test_no_order_network_or_telegram_when_source_unavailable(self):
        with (
            patch.object(m, "_unit_pid", return_value=588864),
            patch.object(m, "_process_env", return_value={
                "owner_db": "/does-not-exist.sqlite3"}),
        ):
            r=m.inspect_original_four_ack_asof_micro_readonly()
        self.assertEqual(r["status"],
            "M05_STRATEGY_A_ASOF_MICRO_RISK_AUDIT_BLOCKED_READONLY")
        self.assertFalse(r["service_restarted"])
        self.assertEqual(r["orders_sent"],0)
        self.assertEqual(r["telegram_messages_sent"],0)


if __name__ == "__main__":
    unittest.main()
