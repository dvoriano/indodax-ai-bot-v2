"""M04 original public WATCH three-role FIXTURE-only sequential runner tests."""
from __future__ import annotations

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from tests.test_m04_original_radar_three_role_handoff_readonly import (
    NOW, candidate, snapshot,
)
from tests.test_m04_public_watch_provider_preview_nonlive import response
from indodax_ai_bot.m04_radar_three_role_handoff_readonly import (
    prepare_original_radar_three_role_handoff_readonly,
)
from indodax_ai_bot.m04_public_watch_provider_preview_nonlive import (
    prepare_public_watch_provider_request_preview_offline,
)
from indodax_ai_bot.m04_public_watch_role_sequence_nonlive import (
    inspect_original_public_watch_role_sequence_offline,
    simulate_public_watch_role_sequence_offline,
)


def handoff(candidates):
    return prepare_original_radar_three_role_handoff_readonly(
        snapshot=snapshot(candidates), now_epoch=NOW,
    )


def simulated(candidates):
    h = handoff(candidates)
    preview = prepare_public_watch_provider_request_preview_offline(
        handoff=h, now_epoch=NOW
    )
    payloads = {}
    for p in preview["request_previews"]:
        payloads.setdefault(p["pair"], {})[p["role"]] = json.dumps(
            response(p), sort_keys=True,
        )
    return h, payloads


class PublicWatchRoleSequenceNonliveTests(unittest.TestCase):
    def run_fixture(self, candidates):
        h, fixtures = simulated(candidates)
        return simulate_public_watch_role_sequence_offline(
            handoff=h, now_epoch=NOW,
            fixture_response_json_by_pair=fixtures,
        )

    def assert_never_live(self, result):
        self.assertTrue(result["simulated_fixture_only"])
        self.assertEqual(result["actual_model_responses_observed"], 0)
        self.assertEqual(result["provider_http_calls"], 0)
        self.assertFalse(result["owner_paid_ai_go_authorized"])
        self.assertFalse(result["provider_dispatch_allowed"])
        self.assertFalse(result["independent_g1_issuer_verified"])
        self.assertFalse(result["account_and_all_in_fees_verified"])
        self.assertEqual(result["telegram_messages_sent"], 0)
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["order_sent"])
        self.assertIsNone(result["owner_final_decision"])

    def test_single_candidate_three_ordered_roles_same_digest(self):
        r = self.run_fixture([candidate()])
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_SEQUENCE_FIXTURE_VALIDATED_ONLY")
        self.assertEqual(r["candidate_count"], 1)
        self.assertEqual(r["simulated_response_shapes_validated"], 3)
        self.assert_never_live(r)
        c = r["candidate_reports"][0]
        self.assertEqual(c["pair"], "uai_idr")
        self.assertFalse(c["model_origin_verified"])
        self.assertFalse(c["trade_proposal_created"])
        roles = c["role_fixture_reports"]
        self.assertEqual([r["role"] for r in roles],
                         ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual([r["stance"] for r in roles],
                         ["WATCH", "NO_TRADE", "WAIT"])
        self.assertEqual([r["prior_fixture_role_count"] for r in roles],
                         [0, 1, 2])
        self.assertEqual(len({r["test_request_context_sha256"]
                              for r in roles}), 3)
        self.assertEqual(len({r["test_fixture_response_sha256"]
                              for r in roles}), 3)

    def test_two_candidates_six_roles_and_no_cross_source(self):
        r = self.run_fixture([candidate(), candidate("sol_idr")])
        self.assertEqual(r["candidate_count"], 2)
        self.assertEqual(r["simulated_response_shapes_validated"], 6)
        self.assertEqual([c["pair"] for c in r["candidate_reports"]],
                         ["uai_idr", "sol_idr"])
        self.assertNotEqual(
            r["candidate_reports"][0]["evidence_digest_sha256"],
            r["candidate_reports"][1]["evidence_digest_sha256"],
        )
        for c in r["candidate_reports"]:
            self.assertTrue(c["complete_role_order_verified_in_test"])
            self.assertTrue(c["all_prior_role_contexts_same_pair_and_digest"])
        self.assert_never_live(r)

    def test_no_fixture_provided_is_not_real_ai_response(self):
        h = handoff([candidate()])
        r = simulate_public_watch_role_sequence_offline(
            handoff=h, now_epoch=NOW,
        )
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_SEQUENCE_TEST_RESPONSES_NOT_PROVIDED")
        self.assertEqual(r["candidate_count"], 0)
        self.assert_never_live(r)

    def test_no_current_watch_requires_no_fixtures(self):
        r = self.run_fixture([])
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_SEQUENCE_NO_CURRENT_CANDIDATE")
        self.assert_never_live(r)
        r = simulate_public_watch_role_sequence_offline(
            handoff=handoff([]), now_epoch=NOW,
            fixture_response_json_by_pair={"eth_idr": {}},
        )
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_SEQUENCE_FIXTURE_COHORT_MISMATCH")
        self.assert_never_live(r)

    def test_missing_or_extra_role_or_pair_rejected(self):
        h, fix = simulated([candidate()])
        for altered in (
            {},
            {"uai_idr": {"MARKET": fix["uai_idr"]["MARKET"]}},
            {"uai_idr": {**fix["uai_idr"], "BUY": "{}"}},
            {"sol_idr": fix["uai_idr"]},
        ):
            r = simulate_public_watch_role_sequence_offline(
                handoff=h, now_epoch=NOW,
                fixture_response_json_by_pair=altered,
            )
            self.assertEqual(r["candidate_count"], 0)
            self.assert_never_live(r)

    def test_missing_market_result_rejects_later_role_chain(self):
        h, f = simulated([candidate()])
        f["uai_idr"]["MARKET"] = "{}"
        r = simulate_public_watch_role_sequence_offline(
            handoff=h, now_epoch=NOW, fixture_response_json_by_pair=f,
        )
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_SEQUENCE_FIXTURE_RESPONSE_REJECTED")
        self.assertEqual(r["candidate_reports"], [])
        self.assert_never_live(r)

    def test_risk_allow_or_strategy_buy_rejected_and_never_promoted(self):
        for role, stance in (("RISK", "ALLOW"), ("STRATEGY", "BUY")):
            h, f = simulated([candidate()])
            parsed = json.loads(f["uai_idr"][role])
            parsed["stance"] = stance
            f["uai_idr"][role] = json.dumps(parsed)
            r = simulate_public_watch_role_sequence_offline(
                handoff=h, now_epoch=NOW,
                fixture_response_json_by_pair=f,
            )
            self.assertEqual(r["status"],
                             "M04_PUBLIC_WATCH_SEQUENCE_FIXTURE_RESPONSE_REJECTED")
            self.assert_never_live(r)

    def test_cross_pair_model_result_rejected_with_all_rollback(self):
        h, f = simulated([candidate(), candidate("sol_idr")])
        bad = json.loads(f["sol_idr"]["MARKET"])
        bad["evidence_digest_sha256"] = (
            json.loads(f["uai_idr"]["MARKET"])["evidence_digest_sha256"]
        )
        f["sol_idr"]["MARKET"] = json.dumps(bad)
        r = simulate_public_watch_role_sequence_offline(
            handoff=h, now_epoch=NOW,
            fixture_response_json_by_pair=f,
        )
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_SEQUENCE_FIXTURE_RESPONSE_REJECTED")
        self.assertEqual(r["candidate_reports"], [])
        self.assertEqual(r["simulated_response_shapes_validated"], 0)
        self.assert_never_live(r)

    def test_invalid_g1_and_execution_flags_in_response_fail(self):
        for key in ("g1_verified", "execution_allowed"):
            h, f = simulated([candidate()])
            data = json.loads(f["uai_idr"]["STRATEGY"])
            data[key] = True
            f["uai_idr"]["STRATEGY"] = json.dumps(data)
            r = simulate_public_watch_role_sequence_offline(
                handoff=h, now_epoch=NOW,
                fixture_response_json_by_pair=f,
            )
            self.assertEqual(r["status"],
                             "M04_PUBLIC_WATCH_SEQUENCE_FIXTURE_RESPONSE_REJECTED")
            self.assert_never_live(r)

    def test_source_snapshot_tamper_and_expiry_rejected(self):
        h, f = simulated([candidate()])
        mutated = deepcopy(h)
        mutated["candidates"][0]["public_evidence"]["price_snapshot_after_idr"] = "9999"
        r = simulate_public_watch_role_sequence_offline(
            handoff=mutated, now_epoch=NOW,
            fixture_response_json_by_pair=f,
        )
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_SEQUENCE_SOURCE_UNVERIFIED")
        self.assert_never_live(r)
        r = simulate_public_watch_role_sequence_offline(
            handoff=h, now_epoch=NOW + 110,
            fixture_response_json_by_pair=f,
        )
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_SEQUENCE_SOURCE_UNVERIFIED")
        self.assert_never_live(r)

    def test_no_network_telegram_or_private_reader_calls(self):
        h, f = simulated([candidate()])
        with patch(
            "indodax_ai_bot.sumopod_provider._post",
            side_effect=AssertionError("PROVIDER NETWORK CALLED")
        ), patch(
            "indodax_ai_bot.telegram_bot_api.TelegramBotApiClient.call",
            side_effect=AssertionError("TELEGRAM NETWORK CALLED")
        ):
            r = simulate_public_watch_role_sequence_offline(
                handoff=h, now_epoch=NOW,
                fixture_response_json_by_pair=f,
            )
        self.assertEqual(r["simulated_response_shapes_validated"], 3)
        self.assert_never_live(r)

    def test_original_inspector_called_once_without_fixtures_or_ai(self):
        h = handoff([candidate()])
        with patch(
            "indodax_ai_bot.m04_public_watch_role_sequence_nonlive."
            "inspect_original_radar_three_role_handoff_readonly",
            return_value=deepcopy(h)
        ) as read:
            r = inspect_original_public_watch_role_sequence_offline(
                now_epoch=NOW
            )
        read.assert_called_once()
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_SEQUENCE_TEST_RESPONSES_NOT_PROVIDED")
        self.assert_never_live(r)

    def test_invalid_clock_and_handoff_never_create_response(self):
        for clock in (None, True, 0, -1, "1800000045"):
            r = simulate_public_watch_role_sequence_offline(
                handoff=handoff([candidate()]), now_epoch=clock,
            )
            self.assertEqual(r["candidate_count"], 0)
            self.assert_never_live(r)
        r = simulate_public_watch_role_sequence_offline(
            handoff={}, now_epoch=NOW,
        )
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_SEQUENCE_SOURCE_UNVERIFIED")
        self.assert_never_live(r)


if __name__ == "__main__":
    unittest.main()
