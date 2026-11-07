"""No paid provider: original M03 PUBLIC WATCH provider preview schema tests."""
from __future__ import annotations

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from tests.test_m04_original_radar_three_role_handoff_readonly import (
    NOW, candidate, snapshot,
)
from indodax_ai_bot.m04_radar_three_role_handoff_readonly import (
    prepare_original_radar_three_role_handoff_readonly,
)
from indodax_ai_bot.m04_radar_three_role_handoff_readonly import _MISSING_FACTS
from indodax_ai_bot.m04_public_watch_provider_preview_nonlive import (
    inspect_original_public_watch_provider_preview_offline,
    prepare_public_watch_provider_request_preview_offline,
    validate_public_watch_provider_response_shape_offline,
)


MODELS = {role: "glm-5.3-flash" for role in ("MARKET", "RISK", "STRATEGY")}


def handoff(xs):
    return prepare_original_radar_three_role_handoff_readonly(
        snapshot=snapshot(xs), now_epoch=NOW,
    )


def response(preview, *, stance=None):
    role = preview["role"]
    return {
        "role": role,
        "pair": preview["pair"],
        "evidence_digest_sha256": preview["evidence_digest_sha256"],
        "stance": stance or {
            "MARKET": "WATCH",
            "RISK": "NO_TRADE",
            "STRATEGY": "WAIT",
        }[role],
        "reason": "Public observation only; evidence incomplete.",
        "missing_evidence_acknowledged": list(_MISSING_FACTS),
        "g1_verified": False,
        "execution_allowed": False,
    }


class PublicWatchProviderPreviewTests(unittest.TestCase):
    def preview(self, xs, models=None):
        return prepare_public_watch_provider_request_preview_offline(
            handoff=handoff(xs), now_epoch=NOW,
            models_by_role=models,
        )

    def assert_safe(self, r):
        self.assertEqual(r["provider_http_calls"], 0)
        self.assertEqual(r["model_responses_observed"], 0)
        self.assertFalse(r["owner_paid_ai_go_authorized"])
        self.assertFalse(r["real_provider_dispatch_allowed"])
        self.assertFalse(r["independent_g1_issuer_verified"])
        self.assertFalse(r["source_is_qualified_for_trading"])
        self.assertFalse(r["execution_allowed"])
        self.assertFalse(r["order_sent"])
        self.assertEqual(r["telegram_messages_sent"], 0)
        self.assertEqual(r["new_market_scanner_invocations"], 0)

    def test_default_no_models_and_no_caller_is_prepared_but_not_sendable(self):
        r = self.preview([candidate()])
        self.assertEqual(
            r["status"], "M04_PUBLIC_WATCH_PROVIDER_CONTRACT_OFFLINE"
        )
        self.assertEqual(r["candidate_count"], 1)
        self.assertEqual(r["request_preview_count"], 3)
        self.assertFalse(r["model_ids_supplied_by_caller"])
        self.assert_safe(r)
        previews = r["request_previews"]
        self.assertEqual([p["role"] for p in previews],
                         ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual([p["depends_on_roles"] for p in previews],
                         [[], ["MARKET"], ["MARKET", "RISK"]])
        self.assertEqual(
            len({p["evidence_digest_sha256"] for p in previews}), 1
        )
        for p in previews:
            self.assertIsNone(p["request_body_preview"]["model"])
            self.assertFalse(p["send_allowed"])
            self.assertFalse(p["response_observed"])
            self.assertFalse(p["owner_paid_ai_go_authorized"])
            self.assertFalse(p["request_body_preview"]["stream"])
            self.assertNotIn("Authorization", str(p))
            body = p["request_body_preview"]
            self.assertEqual([m["role"] for m in body["messages"]],
                             ["system", "user"])
            source = json.loads(body["messages"][1]["content"])
            self.assertEqual(source["pair"], "uai_idr")
            self.assertFalse(source["independent_g1_issuer_verified"])
            self.assertFalse(source["owner_paid_ai_go_authorized"])
            self.assertEqual(source["actual_prior_role_responses"], None)
            self.assertEqual(source["source_missing"], list(_MISSING_FACTS))
            self.assertEqual(source["required_prior_roles"],
                             p["depends_on_roles"])

    def test_explicit_model_ids_prepared_without_network_authority(self):
        r = self.preview([candidate()], models=MODELS)
        self.assertTrue(r["model_ids_supplied_by_caller"])
        self.assertFalse(r["model_ids_confirmed_available_at_provider"])
        self.assertEqual(
            [p["model_id_from_caller"] for p in r["request_previews"]],
            ["glm-5.3-flash"] * 3
        )
        for p in r["request_previews"]:
            self.assertEqual(p["request_body_preview"]["model"],
                             "glm-5.3-flash")
            self.assertFalse(p["send_allowed"])
        self.assert_safe(r)

    def test_source_preview_two_candidates_is_six_bound_non_interchangeable(self):
        r = self.preview([candidate(), candidate("sol_idr")])
        self.assertEqual(r["candidate_count"], 2)
        self.assertEqual(r["request_preview_count"], 6)
        a = r["request_previews"][:3]
        z = r["request_previews"][3:]
        self.assertEqual({p["pair"] for p in a}, {"uai_idr"})
        self.assertEqual({p["pair"] for p in z}, {"sol_idr"})
        self.assertNotEqual(a[0]["evidence_digest_sha256"],
                            z[0]["evidence_digest_sha256"])
        self.assert_safe(r)

    def test_no_current_candidate_means_no_previews(self):
        r = self.preview([])
        self.assertEqual(r["status"], "M04_PUBLIC_WATCH_NO_CURRENT_CANDIDATE")
        self.assertEqual(r["request_previews"], [])
        self.assertEqual(r["candidate_count"], 0)
        self.assert_safe(r)

    def test_invalid_owner_models_are_rejected_without_default_selection(self):
        for model_map in (
            {},
            {"MARKET": "glm-5.3-flash"},
            {"MARKET": " ", "RISK": "foo", "STRATEGY": "bar"},
            {"MARKET": "glm-5.3-flash", "RISK": "glm-5.3-flash",
             "STRATEGY": "inject\nheader"},
            {"MARKET": True, "RISK": "x", "STRATEGY": "x"},
        ):
            r = self.preview([candidate()], models=model_map)
            self.assertEqual(r["status"],
                             "M04_PUBLIC_WATCH_INVALID_OWNER_MODEL_SELECTION")
            self.assert_safe(r)

    def test_stale_or_forged_go_and_g1_stop_preview(self):
        source = handoff([candidate()])
        for key in ("paid_ai_go_authorized", "g1_independent_issuer_verified",
                    "execution_allowed", "three_real_role_responses_observed"):
            changed = deepcopy(source)
            changed[key] = True
            r = prepare_public_watch_provider_request_preview_offline(
                handoff=changed, now_epoch=NOW
            )
            self.assertEqual(r["status"],
                             "M04_PUBLIC_WATCH_SOURCE_OR_RESEARCH_UNVERIFIED")
            self.assert_safe(r)
        stale = prepare_public_watch_provider_request_preview_offline(
            handoff=source, now_epoch=NOW + 100
        )
        self.assertEqual(stale["request_preview_count"], 0)
        self.assert_safe(stale)

    def test_invalid_clock_or_tampered_candidate_stops_all(self):
        h = handoff([candidate()])
        h["candidates"][0]["public_evidence"]["price_snapshot_after_idr"] = "999"
        r = prepare_public_watch_provider_request_preview_offline(
            handoff=h, now_epoch=NOW
        )
        self.assertEqual(r["request_preview_count"], 0)
        self.assert_safe(r)
        for clock in (None, True, -1, 0, "1800000045"):
            r = prepare_public_watch_provider_request_preview_offline(
                handoff=handoff([candidate()]), now_epoch=clock
            )
            self.assertEqual(r["status"], "M04_PUBLIC_WATCH_INVALID_CLOCK")
            self.assert_safe(r)

    def test_offline_shape_validator_accepts_only_exact_watch_role_schema(self):
        r = self.preview([candidate()], models=MODELS)
        for p in r["request_previews"]:
            parsed = response(p)
            v = validate_public_watch_provider_response_shape_offline(
                raw_response_json=json.dumps(parsed),
                request_preview=p,
            )
            self.assertTrue(v["response_shape_valid"])
            self.assertEqual(v["status"],
                             "M04_PUBLIC_WATCH_MODEL_SHAPE_VALIDATED_OFFLINE")
            self.assertEqual(v["role"], p["role"])
            self.assertFalse(v["actual_provider_response_verified"])
            self.assertFalse(v["execution_allowed"])
            self.assertEqual(v["provider_http_calls"], 0)

    def test_model_buy_allow_and_strategy_buy_never_validate(self):
        r = self.preview([candidate()], models=MODELS)
        for p in r["request_previews"]:
            for forbidden in ("BUY", "ALLOW", "SELL", "EXIT_REVIEW"):
                v = validate_public_watch_provider_response_shape_offline(
                    raw_response_json=json.dumps(response(
                        p, stance=forbidden
                    )), request_preview=p
                )
                self.assertFalse(v["response_shape_valid"])

    def test_response_pair_digest_role_and_proof_mismatch_rejected(self):
        p = self.preview([candidate()])["request_previews"][0]
        for key, bad in (
            ("pair", "btc_idr"),
            ("evidence_digest_sha256", "b" * 64),
            ("role", "STRATEGY"),
            ("g1_verified", True),
            ("execution_allowed", True),
            ("missing_evidence_acknowledged", []),
        ):
            raw = response(p)
            raw[key] = bad
            v = validate_public_watch_provider_response_shape_offline(
                raw_response_json=json.dumps(raw),
                request_preview=p
            )
            self.assertFalse(v["response_shape_valid"])

    def test_hallucinated_trade_price_profit_and_other_fields_rejected(self):
        p = self.preview([candidate()])["request_previews"][2]
        for name in ("entry_price_idr", "projected_net_profit_idr",
                     "projected_net_roi_percent", "target_exit_price_idr",
                     "trade_decision"):
            raw = response(p)
            raw[name] = "999999"
            v = validate_public_watch_provider_response_shape_offline(
                raw_response_json=json.dumps(raw),
                request_preview=p
            )
            self.assertFalse(v["response_shape_valid"])

    def test_duplicate_json_keys_and_invalid_response_never_pass(self):
        p = self.preview([candidate()])["request_previews"][0]
        raw = json.dumps(response(p))
        bad = raw.replace('"stance": "WATCH"',
                          '"stance": "WATCH", "stance": "AVOID"')
        self.assertNotEqual(raw, bad)
        for text in (bad, "null", "[]", '{"x":NaN}', "", " "):
            v = validate_public_watch_provider_response_shape_offline(
                raw_response_json=text, request_preview=p
            )
            self.assertFalse(v["response_shape_valid"])

    def test_explicitly_offline_dryrun_does_not_call_any_provider(self):
        h = handoff([candidate()])
        with patch(
            "indodax_ai_bot.sumopod_provider._post",
            side_effect=AssertionError("PAID PROVIDER CALLED")
        ), patch(
            "indodax_ai_bot.telegram_bot_api.TelegramBotApiClient.call",
            side_effect=AssertionError("TELEGRAM SENT")
        ), patch(
            "indodax_ai_bot.m04_public_watch_provider_preview_nonlive."
            "inspect_original_radar_three_role_handoff_readonly",
            return_value=deepcopy(h),
        ) as read:
            r = inspect_original_public_watch_provider_preview_offline(
                now_epoch=NOW,
            )
        read.assert_called_once()
        self.assertEqual(r["request_preview_count"], 3)
        self.assert_safe(r)


if __name__ == "__main__":
    unittest.main()
