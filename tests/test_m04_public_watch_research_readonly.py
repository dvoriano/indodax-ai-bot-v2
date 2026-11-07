"""Strict offline original M04 PUBLIC WATCH deterministic role-research contract.

No real MARKET/RISK/STRATEGY model calls; no money, orders, Telegram,
new scanner or second M03 database. Fake handoff fixture is never G1.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from tests.test_m04_original_radar_three_role_handoff_readonly import (
    NOW, candidate, snapshot,
)
from indodax_ai_bot.m04_radar_three_role_handoff_readonly import (
    prepare_original_radar_three_role_handoff_readonly,
)
from indodax_ai_bot.m04_public_watch_research_readonly import (
    inspect_original_public_watch_research_readonly,
    prepare_public_watch_research_readonly,
)


def handoff(xs):
    return prepare_original_radar_three_role_handoff_readonly(
        snapshot=snapshot(xs), now_epoch=NOW,
    )


class PublicWatchResearchReadonlyContractTests(unittest.TestCase):
    def research(self, xs, *, now=NOW):
        return prepare_public_watch_research_readonly(
            handoff=handoff(xs), now_epoch=now
        )

    def assert_offline(self, r):
        self.assertEqual(r["provider_http_calls"], 0)
        self.assertEqual(r["telegram_messages_sent"], 0)
        self.assertEqual(r["actual_market_ai_responses"], 0)
        self.assertEqual(r["actual_risk_ai_responses"], 0)
        self.assertEqual(r["actual_strategy_ai_responses"], 0)
        self.assertFalse(r["three_real_role_responses_observed"])
        self.assertFalse(r["independent_g1_issuer_verified"])
        self.assertFalse(r["paid_ai_go_authorized"])
        self.assertFalse(r["execution_allowed"])
        self.assertFalse(r["recommendation_to_buy"])
        self.assertFalse(r["order_sent"])
        self.assertIsNone(r["owner_final_decision"])

    def test_one_public_rising_price_uses_same_digest_three_non_model_views(self):
        r = self.research([candidate()])
        self.assertEqual(r["status"], "M04_PUBLIC_WATCH_RESEARCH_OFFLINE_ONLY")
        self.assertEqual(r["candidate_count"], 1)
        self.assertEqual(r["original_m03_inspections"], 0)
        self.assert_offline(r)
        x = r["candidates"][0]
        self.assertEqual(x["pair"], "uai_idr")
        self.assertEqual(x["source_status"], "PUBLIC_WATCH_UNATTESTED")
        self.assertEqual(x["sample_age_seconds"], 15)
        self.assertEqual(x["actual_ai_role_calls"], 0)
        self.assertEqual(x["research_selection_rank"], 1)
        self.assertEqual(
            [a["role"] for a in x["role_research_views"]],
            ["MARKET", "RISK", "STRATEGY"]
        )
        self.assertEqual(
            {a["evidence_digest_sha256"] for a in x["role_research_views"]},
            {x["evidence_digest_sha256"]}
        )
        self.assertTrue(all(
            a["model_invoked"] is False and
            a["kind"] == "DETERMINISTIC_SOURCE_INSPECTION_NOT_AN_AI_RESPONSE"
            for a in x["role_research_views"]
        ))
        self.assertEqual(
            x["role_research_views"][0]["observational_label"],
            "RISING_QUOTE_WATCH"
        )
        self.assertEqual(
            x["role_research_views"][1]["status"],
            "PUBLIC_RESEARCH_ONLY_NO_TRADE"
        )
        strategy = x["role_research_views"][2]
        self.assertEqual(strategy["status"],
                         "WAIT_NO_VERIFIED_ENTRY_FEES_AND_CAPITAL")
        self.assertIsNone(strategy["net_profit_idr"])
        self.assertIsNone(strategy["net_roi_percent"])
        self.assertEqual(strategy["minimum_targets_relation"], "AND")
        self.assertEqual(strategy["minimum_net_profit_idr"], "10000")
        self.assertEqual(strategy["minimum_net_roi_percent"], "2")
        self.assertFalse(x["trade_proposal_created"])

    def test_empty_watch_is_empty_not_fake_model_output(self):
        r = self.research([])
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_RESEARCH_NO_CURRENT_CANDIDATE")
        self.assertEqual(r["candidate_count"], 0)
        self.assertEqual(r["candidates"], [])
        self.assert_offline(r)

    def test_two_independent_candidate_digests_and_three_views_each(self):
        r = self.research([
            candidate("eth_idr", price_watch=False, volume_watch=True,
                      after="99", before="100"),
            candidate("sol_idr", price_watch=False, volume_watch=True,
                      after="101", before="100"),
            candidate("uai_idr", price_watch=True, volume_watch=False)
        ])
        self.assertEqual(r["candidate_count"], 2)
        self.assertEqual([x["pair"] for x in r["candidates"]],
                         ["uai_idr", "sol_idr"])
        self.assertNotEqual(r["candidates"][0]["evidence_digest_sha256"],
                            r["candidates"][1]["evidence_digest_sha256"])
        self.assertEqual([len(x["role_research_views"])
                          for x in r["candidates"]], [3, 3])
        self.assert_offline(r)

    def test_falling_quote_with_rolling_delta_is_not_buy_pressure(self):
        r = self.research([
            candidate("eth_idr", price_watch=False, volume_watch=True,
                      after="99", before="100"),
        ])
        view = r["candidates"][0]["role_research_views"]
        self.assertEqual(view[0]["observational_label"],
                         "ROLLING_VOLUME_DELTA_WITH_FALLING_QUOTE")
        self.assertIn(
            "PRICE_NOT_RISING_NO_BUY_PRESSURE_INFERENCE",
            view[1]["caution_codes"]
        )
        self.assertEqual(view[2]["status"],
                         "WAIT_NO_VERIFIED_ENTRY_FEES_AND_CAPITAL")
        self.assert_offline(r)

    def test_combined_price_and_ticker_delta_is_only_observation(self):
        c = candidate(
            "btc_idr", price_watch=True, volume_watch=True
        )
        r = self.research([c])
        self.assertEqual(
            r["candidates"][0]["role_research_views"][0][
                "observational_label"],
            "RISING_QUOTE_AND_ROLLING_24H_DELTA"
        )
        self.assertFalse(
            r["candidates"][0]["role_research_views"][0][
                "rolling_24h_delta_is_1m_executed_volume"]
        )
        self.assert_offline(r)

    def test_source_gap_and_wide_spread_become_cautions(self):
        c = candidate()
        c["missing_intermediate_minute_samples"] = True
        c["spread_bps"] = "81"
        from indodax_ai_bot.m05_radar_momentum_nonlive import (
            classify_public_watch_pre_ai_nonlive
        )
        c["pre_ai_review"] = classify_public_watch_pre_ai_nonlive(c)
        r = self.research([c])
        cautions = r["candidates"][0]["role_research_views"][1]["caution_codes"]
        self.assertIn("MISSING_INTERMEDIATE_SOURCE_MINUTE", cautions)
        self.assertIn("OBSERVED_SPREAD_OVER_60_BPS", cautions)
        self.assert_offline(r)

    def test_expired_packet_rejected_after_snapshot(self):
        r = prepare_public_watch_research_readonly(
            handoff=handoff([candidate()]), now_epoch=NOW + 95
        )
        self.assertEqual(r["status"], "M04_PUBLIC_WATCH_RESEARCH_SOURCE_REJECTED")
        self.assertEqual(r["candidate_count"], 0)
        self.assert_offline(r)

    def test_changed_price_without_digest_change_rejected(self):
        h = handoff([candidate()])
        h["candidates"][0]["public_evidence"]["price_snapshot_after_idr"] = "999999"
        r = prepare_public_watch_research_readonly(handoff=h, now_epoch=NOW)
        self.assertEqual(r["status"], "M04_PUBLIC_WATCH_RESEARCH_SOURCE_REJECTED")
        self.assert_offline(r)

    def test_forged_g1_or_paid_go_promotion_rejected(self):
        for key in ("g1_independent_issuer_verified",
                    "paid_ai_go_authorized",
                    "three_real_role_responses_observed",
                    "execution_allowed"):
            h = handoff([candidate()])
            h[key] = True
            r = prepare_public_watch_research_readonly(
                handoff=h, now_epoch=NOW
            )
            self.assertEqual(
                r["status"], "M04_PUBLIC_WATCH_RESEARCH_HANDOFF_UNVERIFIED"
            )
            self.assert_offline(r)

    def test_untrusted_ai_model_output_in_role_slot_rejected(self):
        h = handoff([candidate()])
        h["candidates"][0]["role_slots"][0]["model_output"] = {
            "role": "MARKET", "decision": "BUY"
        }
        r = prepare_public_watch_research_readonly(
            handoff=h, now_epoch=NOW
        )
        self.assertEqual(r["status"], "M04_PUBLIC_WATCH_RESEARCH_SOURCE_REJECTED")
        self.assert_offline(r)

    def test_cannot_strip_required_missing_fields(self):
        h = handoff([candidate()])
        h["candidates"][0]["source_missing"] = [
            "independent_g1_issuer_receipt",
        ]
        r = prepare_public_watch_research_readonly(
            handoff=h, now_epoch=NOW
        )
        self.assertEqual(r["status"], "M04_PUBLIC_WATCH_RESEARCH_SOURCE_REJECTED")
        self.assert_offline(r)

    def test_cannot_forge_net_profit_or_trade_consent(self):
        for key, value in [
            ("required_net_profit_idr", "0"),
            ("required_net_roi_percent", "0"),
            ("required_threshold_relation", "OR"),
            ("execution_allowed", True),
            ("owner_order_specific_consent_verified", True),
        ]:
            h = handoff([candidate()])
            h["candidates"][0][key] = value
            r = prepare_public_watch_research_readonly(
                handoff=h, now_epoch=NOW
            )
            self.assertEqual(r["status"],
                             "M04_PUBLIC_WATCH_RESEARCH_SOURCE_REJECTED")
            self.assert_offline(r)

    def test_duplicate_pair_or_wrong_selected_rank_rejected(self):
        a = candidate()
        b = candidate("sol_idr")
        h = handoff([a, b])
        h["candidates"][1]["pair"] = "uai_idr"
        r = prepare_public_watch_research_readonly(
            handoff=h, now_epoch=NOW
        )
        self.assertEqual(r["status"], "M04_PUBLIC_WATCH_RESEARCH_SOURCE_REJECTED")
        h = handoff([a, b])
        h["candidates"][1]["handoff_selection_rank"] = 1
        r = prepare_public_watch_research_readonly(
            handoff=h, now_epoch=NOW
        )
        self.assertEqual(r["status"], "M04_PUBLIC_WATCH_RESEARCH_SOURCE_REJECTED")

    def test_numeric_nan_or_forged_candle_flag_rejected(self):
        for key, val in (
            ("spread_snapshot_bps", "NaN"),
            ("volume_24h_snapshot_idr", "-2"),
            ("source_is_actual_closed_candle", True),
            ("rolling_delta_is_closed_1m_volume", True),
        ):
            h = handoff([candidate()])
            h["candidates"][0]["public_evidence"][key] = val
            # Recomputed local SHA is still NOT issuer/G1 authority.
            packet = h["candidates"][0]["public_evidence"]
            digest = sha256(json.dumps(
                packet, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True, allow_nan=False,
            ).encode("ascii")).hexdigest()
            h["candidates"][0]["evidence_digest_sha256"] = digest
            for slot in h["candidates"][0]["role_slots"]:
                slot["evidence_digest_sha256"] = digest
            r = prepare_public_watch_research_readonly(
                handoff=h, now_epoch=NOW
            )
            self.assertEqual(r["status"], "M04_PUBLIC_WATCH_RESEARCH_SOURCE_REJECTED")
            self.assert_offline(r)

    def test_invalid_clock_and_malformed_handoff_fail_closed(self):
        valid = handoff([candidate()])
        for clock in (None, True, 0, -1, "1800000045"):
            r = prepare_public_watch_research_readonly(
                handoff=valid, now_epoch=clock
            )
            self.assertEqual(r["candidate_count"], 0)
            self.assert_offline(r)
        r = prepare_public_watch_research_readonly(
            handoff={"status": "M04_RADAR_HANDOFF_PUBLIC_PACKETS_PREPARED"},
            now_epoch=NOW
        )
        self.assertEqual(r["status"], "M04_PUBLIC_WATCH_RESEARCH_HANDOFF_UNVERIFIED")

    def test_one_original_inspector_and_zero_telegram_or_paid_provider(self):
        h = handoff([candidate()])
        with patch(
            "indodax_ai_bot.m04_public_watch_research_readonly."
            "inspect_original_radar_three_role_handoff_readonly",
            return_value=deepcopy(h)
        ) as read, patch(
            "indodax_ai_bot.sumopod_provider._post",
            side_effect=AssertionError("PAID PROVIDER CALLED")
        ), patch(
            "indodax_ai_bot.telegram_bot_api.TelegramBotApiClient.call",
            side_effect=AssertionError("TELEGRAM CALLED")
        ):
            report = inspect_original_public_watch_research_readonly(
                now_epoch=NOW
            )
        self.assertEqual(report["candidate_count"], 1)
        self.assertEqual(report["original_m03_inspections"], 1)
        read.assert_called_once()
        self.assert_offline(report)

    def test_observed_time_offset_from_minute_bucket_is_allowed(self):
        # minute bucket span remains 180s, real observed reference timestamps
        # may differ from those minute starts by a few seconds.
        c = candidate()
        c["reference_epoch"] -= 20
        from indodax_ai_bot.m05_radar_momentum_nonlive import (
            classify_public_watch_pre_ai_nonlive
        )
        c["pre_ai_review"] = classify_public_watch_pre_ai_nonlive(c)
        r = self.research([c])
        self.assertEqual(r["candidate_count"], 1)
        self.assert_offline(r)


if __name__ == "__main__":
    unittest.main()
