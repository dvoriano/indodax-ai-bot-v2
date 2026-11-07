"""M04 original M03 public radar -> three-role source packet, OFFLINE only.

Fake public candidates. No Telegram, Sumopod API, paid AI, Indodax orders
or owner SQLite writes. This deliberately never forges G1/candle authority.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from indodax_ai_bot.m05_radar_momentum_nonlive import (
    classify_public_watch_pre_ai_nonlive,
)
from indodax_ai_bot.m04_radar_three_role_handoff_readonly import (
    inspect_original_radar_three_role_handoff_readonly,
    prepare_original_radar_three_role_handoff_readonly,
)

NOW = 1800000045
OBSERVED = NOW - 15


def candidate(pair="uai_idr", *, observed=OBSERVED, volume_watch=False,
              price_watch=True, after="108", before="100"):
    rise = (int(after) / int(before) - 1) * 100
    c = {
        "pair": pair,
        "observed_epoch": observed,
        "reference_epoch": observed - 180,
        "reference_interval_seconds": 180,
        "sample_age_seconds": NOW - observed,
        "latest_price_idr": after,
        "reference_price_idr": before,
        "observed_rise_pct": format(rise, ".2f"),
        "price_watch": price_watch,
        "volume_watch": volume_watch,
        "volume_24h_idr": "500000000",
        "volume_24h_net_change_idr": "30000000" if volume_watch else None,
        "volume_24h_net_change_pct": "6.38" if volume_watch else None,
        "volume_comparison_seconds": 180 if volume_watch else None,
        "volume_reference_epoch": observed - 180 if volume_watch else None,
        "rolling_24h_turnover_not_1m_traded_volume": True,
        "spread_bps": "32",
        "missing_intermediate_minute_samples": False,
        "casebook_early_watch_observed": False,
        "no_g1_or_ai_or_order_authority": True,
        "watch_type": (
            "PRICE_AND_ROLLING_TURNOVER_WATCH"
            if price_watch and volume_watch else
            "PRICE_MOMENTUM_WATCH" if price_watch else
            "ROLLING_TURNOVER_CHANGE_WATCH"
        ),
    }
    c["pre_ai_review"] = classify_public_watch_pre_ai_nonlive(c)
    return c


def snapshot(candidates=None):
    return {
        "status": "M03_ORIGINAL_PUBLIC_RADAR_OBSERVED_UNATTESTED",
        "candidates": [] if candidates is None else candidates,
        "source_authenticated_for_g1": False,
        "three_ai_role_responses_observed": False,
        "owner_order_specific_final_verified": False,
        "execution_allowed": False,
    }


class M04OriginalRadarHandoffReadonlyTests(unittest.TestCase):
    def prepare(self, xs):
        return prepare_original_radar_three_role_handoff_readonly(
            snapshot=snapshot(xs), now_epoch=NOW,
        )

    def assert_nonlive(self, r):
        self.assertFalse(r["g1_independent_issuer_verified"])
        self.assertFalse(r["three_real_role_responses_observed"])
        self.assertFalse(r["paid_ai_go_authorized"])
        self.assertFalse(r["execution_allowed"])
        self.assertFalse(r["order_sent"])
        self.assertIsNone(r["owner_final_decision"])
        self.assertEqual(r["provider_http_calls"], 0)
        self.assertEqual(r["telegram_messages_sent"], 0)
        self.assertEqual(r["new_scanner_calls"], 0)

    def test_original_single_candidate_prepares_three_shared_digest_slots(self):
        x = self.prepare([candidate()])
        self.assertEqual(x["status"], "M04_RADAR_HANDOFF_PUBLIC_PACKETS_PREPARED")
        self.assertEqual(x["candidate_count"], 1)
        self.assertEqual(x["three_role_input_slots_prepared"], 3)
        self.assert_nonlive(x)
        row = x["candidates"][0]
        self.assertEqual(row["pair"], "uai_idr")
        self.assertEqual(row["required_threshold_relation"], "AND")
        self.assertEqual(row["required_net_profit_idr"], "10000")
        self.assertEqual(row["required_net_roi_percent"], "2")
        self.assertEqual(row["deterministic_pre_ai_risk_status"],
                         "NO_TRADE_SOURCE_AND_ACCOUNT_UNVERIFIED")
        self.assertFalse(row["public_evidence"]["source_is_actual_closed_candle"])
        self.assertFalse(row["public_evidence"]["rolling_delta_is_closed_1m_volume"])
        self.assertEqual(row["public_evidence"]["news_status"], "NEWS_UNAVAILABLE")
        self.assertIn("complete_public_trade_tape", row["source_missing"])
        self.assertIn("independent_g1_issuer_receipt", row["source_missing"])
        roles = row["role_slots"]
        self.assertEqual([role["role"] for role in roles],
                         ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual(
            {role["evidence_digest_sha256"] for role in roles},
            {row["evidence_digest_sha256"]}
        )
        self.assertEqual({role["model_output"] for role in roles}, {None})
        canonical = json.dumps(row["public_evidence"], sort_keys=True,
                               separators=(",", ":"), ensure_ascii=True
                               ).encode("ascii")
        self.assertEqual(row["evidence_digest_sha256"],
                         sha256(canonical).hexdigest())

    def test_no_candidate_means_no_fake_ai_or_buy(self):
        r = self.prepare([])
        self.assertEqual(r["status"], "M04_RADAR_HANDOFF_NO_CURRENT_WATCH")
        self.assertEqual(r["candidate_count"], 0)
        self.assertEqual(r["candidates"], [])
        self.assert_nonlive(r)

    def test_two_candidates_are_separate_digests_without_cross_contamination(self):
        c1 = candidate("uai_idr")
        c2 = candidate("eth_idr", volume_watch=True, price_watch=False,
                       after="99", before="100")
        result = self.prepare([c1, c2, candidate("sol_idr")])
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["three_role_input_slots_prepared"], 6)
        self.assertNotEqual(result["candidates"][0]["evidence_digest_sha256"],
                            result["candidates"][1]["evidence_digest_sha256"])
        self.assertEqual(result["candidates"][1]["public_evidence"]["watch_type"],
                         "ROLLING_TURNOVER_CHANGE_WATCH")
        self.assertEqual(result["candidates"][1]["public_evidence"][
            "volume_24h_rolling_net_delta_idr"], "30000000")
        self.assertEqual(result["candidates"][1]["public_evidence"][
            "observed_rise_pct"], "-1.00")
        self.assert_nonlive(result)

    def test_one_original_read_only_inspector_call_and_no_provider_call(self):
        with patch(
            "indodax_ai_bot.m04_radar_three_role_handoff_readonly."
            "inspect_original_m03_radar_readonly",
            return_value=snapshot([candidate()])
        ) as reader, patch(
            "indodax_ai_bot.sumopod_provider._post",
            side_effect=AssertionError("PAID AI CALLED")
        ):
            r = inspect_original_radar_three_role_handoff_readonly(
                now_epoch=NOW,
            )
        self.assertEqual(r["candidate_count"], 1)
        reader.assert_called_once()
        self.assert_nonlive(r)

    def test_forged_pre_ai_digest_is_denied(self):
        c = candidate()
        c["pre_ai_review"]["review_priority"] = "FORGED"
        r = self.prepare([c])
        self.assertEqual(r["status"], "M04_RADAR_INVALID_CANDIDATE_PACKET")
        self.assert_nonlive(r)

    def test_source_stale_and_missing_flags_are_denied(self):
        stale = candidate(observed=NOW - 100)
        self.assertEqual(self.prepare([stale])["candidate_count"], 0)
        gap = candidate()
        gap["rolling_24h_turnover_not_1m_traded_volume"] = False
        self.assertEqual(self.prepare([gap])["candidate_count"], 0)
        unknown = candidate()
        unknown["no_g1_or_ai_or_order_authority"] = False
        self.assertEqual(self.prepare([unknown])["candidate_count"], 0)

    def test_price_and_turnover_or_invalid_types_are_denied(self):
        false_price = candidate()
        false_price["latest_price_idr"] = "150"
        self.assertEqual(self.prepare([false_price])["candidate_count"], 0)
        bad_spread = candidate()
        bad_spread["spread_bps"] = "NaN"
        self.assertEqual(self.prepare([bad_spread])["candidate_count"], 0)
        conflict = candidate()
        conflict["watch_type"] = "ROLLING_TURNOVER_CHANGE_WATCH"
        self.assertEqual(self.prepare([conflict])["candidate_count"], 0)

    def test_duplicate_pair_fails_whole_handoff(self):
        c = candidate()
        r = self.prepare([c, deepcopy(c)])
        self.assertEqual(r["status"], "M04_RADAR_DUPLICATE_PAIR")
        self.assert_nonlive(r)

    def test_untrusted_snapshot_cannot_promote_g1_or_model_calls(self):
        fake = snapshot([candidate()])
        fake["source_authenticated_for_g1"] = True
        result = prepare_original_radar_three_role_handoff_readonly(
            snapshot=fake, now_epoch=NOW,
        )
        self.assertEqual(result["status"], "M04_RADAR_ORIGINAL_SOURCE_UNVERIFIED")
        self.assert_nonlive(result)

    def test_future_clock_and_bad_limit_fail_closed(self):
        candidate_only = snapshot([candidate()])
        for now, maxn in [(True, 2), (0, 2), (NOW, 0), (NOW, 3)]:
            r = prepare_original_radar_three_role_handoff_readonly(
                snapshot=candidate_only, now_epoch=now,
                max_candidates=maxn,
            )
            self.assertEqual(r["status"], "M04_RADAR_HANDOFF_INVALID_SCOPE")
            self.assert_nonlive(r)


if __name__ == "__main__":
    unittest.main()
