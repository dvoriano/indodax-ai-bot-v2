"""Original M05 WS3 ACK -> M04 three uncalled roles / strict veto tests."""
from __future__ import annotations

from copy import deepcopy
import json
import re
import unittest

from indodax_ai_bot.m04_m05_ack_risk_handoff_nonlive import (
    prepare_m04_m05_ack_research_role_inputs_nonlive,
)
from indodax_ai_bot.m05_ws3_asof_quality_nonlive import (
    classify_ws3_preclaim_quality_nonlive,
)
from indodax_ai_bot.three_role_ai import ROLES
from tests.test_m05_ws3_asof_quality_readonly import T, S, price, micro


def quality(*, p=True, m=True, micro_changes=None):
    return classify_ws3_preclaim_quality_nonlive(
        pair="nbtnew_idr", claim_epoch=T, source_epoch=S,
        price_row=price() if p else None,
        micro_row=micro(**(micro_changes or {})) if m else None,
    )


class OriginalM04M05ACKHandoffTests(unittest.TestCase):
    def prepare(self, q=None):
        return prepare_m04_m05_ack_research_role_inputs_nonlive(
            quality=quality() if q is None else q,
        )

    def test_original_role_order_and_identical_source_digest(self):
        report = self.prepare()
        self.assertEqual(report["stage"], "M04_ORIGINAL_M05_ACK_ASOF_REVIEW_ONLY")
        self.assertEqual(report["role_slots_prepared"], 3)
        self.assertEqual([slot["role"] for slot in report["role_slots"]],
                         list(ROLES))
        digests = {slot["source_digest_sha256"]
                   for slot in report["role_slots"]}
        self.assertEqual(digests, {report["evidence_digest_sha256"]})
        self.assertTrue(re.fullmatch(r"[a-f0-9]{64}",
                                       report["evidence_digest_sha256"]))
        for i, slot in enumerate(report["role_slots"]):
            self.assertEqual(slot["required_prior_roles"], list(ROLES[:i]))
            self.assertIsNone(slot["actual_model_response"])
            self.assertIn("NOT_CALLED", slot["dispatch"])
        self.assertTrue(report["deterministic_risk_veto"]["veto"])
        self.assertFalse(report["deterministic_risk_veto"][
            "risk_review_is_real_ai_response"])
        self.assertEqual(report["deterministic_strategy"]["stance"],
                         "WATCH_NO_TRADE")
        self.assertFalse(report["deterministic_strategy"][
            "strategy_review_is_real_ai_response"])
        self.assertFalse(report["actual_three_model_responses_observed"])
        self.assertFalse(report["execution_allowed"])
        self.assertEqual(report["final_decision"], "REJECT")
        self.assertEqual(report["provider_http_attempts"], 0)
        self.assertEqual(report["orders_sent"], 0)
        self.assertEqual(report["telegram_messages_sent"], 0)

    def test_source_absent_and_micro_absent_are_not_buy_opportunities(self):
        for q, code in (
            (quality(p=False), "ASOF_PRICE_SOURCE_NOT_VERIFIED"),
            (quality(m=False), "MICRO_TRADE_AND_BOOK_UNKNOWN"),
        ):
            with self.subTest(code=code):
                report = self.prepare(q)
                self.assertIn(code, report["deterministic_risk_veto"]["reasons"])
                self.assertFalse(report["execution_allowed"])
                self.assertEqual(report["role_slots_prepared"], 3)

    def test_book_only_and_trade_only_are_partial(self):
        unknown_trade = quality(micro_changes={
            "trade_frames": 0, "source_buy_count": None,
            "source_sell_count": None, "source_buy_idr": None,
            "source_sell_idr": None, "last_trade_epoch": None,
            "trade_offset": None,
        })
        unknown_book = quality(micro_changes={
            "book_local_epoch": None, "book_offset": None,
        })
        self.assertIn("MICRO_TRADE_UNKNOWN",
                      self.prepare(unknown_trade)["deterministic_risk_veto"][
                          "reasons"])
        self.assertIn("MICRO_ORDERBOOK_UNKNOWN",
                      self.prepare(unknown_book)["deterministic_risk_veto"][
                          "reasons"])

    def test_micro_after_original_source_not_credited_at_source_epoch(self):
        report = self.prepare()
        self.assertEqual(report["market_public_observations"][
            "micro_relation_to_original_alert_source"],
            "AFTER_ALERT_SOURCE_BEFORE_CLAIM")
        self.assertIn("MICRO_NOT_AVAILABLE_AT_ALERT_SOURCE_EPOCH",
                      report["deterministic_risk_veto"]["reasons"])
        self.assertFalse(report["market_public_observations"][
            "original_sender_consumption_verified"])

    def test_spread_and_reported_sells_cautions_are_only_review(self):
        q = quality(micro_changes={
            "source_buy_idr": "10000", "source_sell_idr": "40000",
            "spread_bps": "999", "best_ask_idr": "101.410",
        })
        # Inconsistent book spread must not pass the original M05 source
        # validator just because its printed spread is large.
        self.assertEqual(q["evidence_tier"], "TRADE_ONLY")
        q = quality(micro_changes={
            "source_buy_idr": "10000", "source_sell_idr": "40000",
        })
        report = self.prepare(q)
        self.assertIn("SOURCE_REPORTED_SELL_SIDE_HIGH_NOT_AGGRESSOR_PROOF",
                      report["deterministic_risk_veto"]["reasons"])
        wide = quality(micro_changes={
            "best_ask_idr": "101.20", "spread_bps": "119.2843",
        })
        self.assertEqual(wide["evidence_tier"], "TRADE_BOOK_OBSERVED")
        self.assertIn("OBSERVED_SPREAD_OVER_50BPS_RESEARCH",
                      self.prepare(wide)["deterministic_risk_veto"]["reasons"])
        self.assertFalse(report["market_public_observations"][
            "source_side_is_verified_taker_aggressor"])

    def test_any_original_authority_promotion_is_rejected(self):
        q = quality()
        flips = {
            "g1_verified": True,
            "m04_actual_role_responses_verified": True,
            "original_m05_sender_consumed_micro_verified": True,
            "source_side_is_not_aggressor_proof": False,
            "watch_publication_policy_changed": True,
            "risk_veto": False,
            "execution_allowed": True,
            "buy_recommendation_allowed": True,
            "final_decision": "PASS",
            "orders_sent": 1,
            "telegram_messages_sent_by_audit": 1,
            "provider_calls_by_audit": 1,
        }
        for field, value in flips.items():
            with self.subTest(field=field):
                bad = deepcopy(q)
                bad[field] = value
                with self.assertRaises(ValueError):
                    self.prepare(bad)

    def test_tier_mismatch_and_future_or_fake_micro_are_blocked(self):
        q = quality()
        for field, value in (
            ("evidence_tier", "PRICE_ONLY"),
            ("fresh_book_observed", False),
            ("fresh_trade_observed", False),
            ("source_age_sec", 151),
            ("micro_age_sec", 91),
            ("micro_vs_alert_source", "UNKNOWN"),
            ("micro_age_sec", -1),
            ("price_status", "PRICE_SOURCE_NOT_RECONSTRUCTED"),
        ):
            with self.subTest(field=field):
                bad = deepcopy(q)
                bad[field] = value
                with self.assertRaises(ValueError):
                    self.prepare(bad)

    def test_missing_cautions_bad_values_and_non_ascii_pairs_fail_closed(self):
        q = quality()
        for field, value in (
            ("research_cautions", []),
            ("research_cautions", ["SECRETS/URL"]),
            ("research_cautions", q["research_cautions"] * 2),
            ("pair", "éth_idr"),
            ("pair", "btc_usdt"),
            ("claim_wib", "not-a-date"),
            ("evidence_tier", ["PRICE_ONLY"]),
        ):
            with self.subTest(field=field):
                bad = deepcopy(q)
                bad[field] = value
                with self.assertRaises(ValueError):
                    self.prepare(bad)

    def test_source_digest_changes_with_source_observation(self):
        original = self.prepare()
        changed = quality()
        changed["source_age_sec"] += 1
        new = self.prepare(changed)
        self.assertNotEqual(original["evidence_digest_sha256"],
                            new["evidence_digest_sha256"])
        self.assertNotIn("private", json.dumps(original).lower())
        self.assertFalse(new["paid_ai_owner_go_authorized"])

    def test_m04_review_never_promotes_closed_candles_or_net_profit(self):
        report = self.prepare()
        m = report["market_public_observations"]
        self.assertFalse(m["closed_candles_verified"])
        self.assertFalse(m["full_15m_and_1h_trade_tape_verified"])
        self.assertFalse(m["rolling_24h_delta_is_executed_1m_volume"])
        self.assertFalse(report["deterministic_strategy"][
            "net_profit_10000_idr_verified"])
        self.assertFalse(report["deterministic_strategy"][
            "net_roi_2_percent_verified"])
        self.assertFalse(report["deterministic_strategy"][
            "risk_veto_overridden"])


if __name__ == "__main__":
    unittest.main()
