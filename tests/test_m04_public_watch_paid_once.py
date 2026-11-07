"""Original V2 paid PUBLIC WATCH one-shot: all fake transport, no billing.

Real paid permission is separate; tests never read operator credential or
choose a billing budget on the operator's behalf.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_m04_original_radar_three_role_handoff_readonly import (
    NOW, candidate, snapshot,
)
from indodax_ai_bot.ai_budget import PersistentAiBudget
from indodax_ai_bot.m04_public_watch_paid_once import (
    OWNER_PAID_ONCE_CONFIRMATION, run_public_watch_paid_once,
)
from indodax_ai_bot.m04_radar_three_role_handoff_readonly import (
    _MISSING_FACTS, prepare_original_radar_three_role_handoff_readonly,
)

MODELS = {role: "glm-5.3-flash" for role in ("MARKET", "RISK", "STRATEGY")}


def handoff(xs):
    return prepare_original_radar_three_role_handoff_readonly(
        snapshot=snapshot(xs), now_epoch=NOW
    )


def fixture_transport(seen, *, corrupt_role=None, fail_at=None,
                      response_model=None):
    def fake(payload, headers, timeout):
        if fail_at and len(seen) + 1 == fail_at:
            raise TimeoutError("FAKE_PROVIDER_TIMEOUT")
        assert timeout == 8.0
        assert headers["Authorization"] == "Bearer FAKE_TEST_KEY_ONLY"
        data = json.loads(payload["messages"][1]["content"])
        role = data["role"]
        pair = data["pair"]
        digest = data["evidence_digest_sha256"]
        seen.append({
            "role": role,
            "pair": pair,
            "digest": digest,
            "prior": deepcopy(data["actual_prior_role_responses"]),
            "owner_go": data["owner_paid_ai_go_authorized"],
        })
        report = {
            "role": role, "pair": pair,
            "evidence_digest_sha256": digest,
            "stance": {
                "MARKET": "WATCH",
                "RISK": "NO_TRADE",
                "STRATEGY": "WAIT",
            }[role],
            "reason": "Fixture only, incomplete public evidence.",
            "missing_evidence_acknowledged": list(_MISSING_FACTS),
            "g1_verified": False,
            "execution_allowed": False,
        }
        if role == corrupt_role:
            report["stance"] = "BUY"
        return {
            "id": "FAKE-DO-NOT-TREAT-AS_PROVIDER_RECEIPT",
            "model": response_model or payload["model"],
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(report),
                },
            }],
            "usage": {"prompt_tokens": 100, "completion_tokens": 80},
        }
    return fake


class PublicWatchPaidOnceOwnerGatedTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = Path(self.dir.name) / "private-reservations.sqlite"

    def budget(self, daily=900_000, per=300_000):
        return PersistentAiBudget(
            self.db, daily_limit_micro_usd=daily,
            per_request_reservation_micro_usd=per,
        )

    def run_owner(self, h=None, *, budget=None, transport=None,
                  clock=None, **overrides):
        values = {
            "handoff": handoff([candidate()]) if h is None else h,
            "now_fn": clock or (lambda: NOW),
            "owner_models": MODELS,
            "api_key": "FAKE_TEST_KEY_ONLY",
            "budget": budget if budget is not None else self.budget(),
            "owner_confirmation": OWNER_PAID_ONCE_CONFIRMATION,
            "owner_pricing_reviewed": True,
            "transport": transport,
        }
        values.update(overrides)
        return run_public_watch_paid_once(**values)

    def assert_non_trade(self, r):
        self.assertFalse(r["source_g1_verified"])
        self.assertFalse(r["execution_allowed"])
        self.assertFalse(r["order_sent"])
        self.assertIsNone(r["owner_final_decision"])
        self.assertFalse(r["qualified_trade_proposal_created"])
        self.assertEqual(r["telegram_messages_sent"], 0)
        self.assertEqual(r["risk_status"], "NO_TRADE")
        self.assertEqual(r["strategy_status"], "WAIT")

    def test_one_source_three_sequential_fake_calls_with_verified_lineage(self):
        seen = []
        r = self.run_owner(transport=fixture_transport(seen))
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_TEST_TRANSPORT_THREE_ROLES_ONLY")
        self.assertTrue(r["injected_test_transport"])
        self.assertTrue(r["owner_paid_test_approved"])
        self.assertTrue(r["provider_pricing_acknowledged_by_owner"])
        self.assertFalse(r["pricing_independently_verified_by_code"])
        self.assertEqual(r["provider_http_attempts"], 3)
        self.assertEqual(r["genuine_provider_responses_verified"], 0)
        self.assertEqual(r["roles_completed"],
                         ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual([x["role"] for x in seen],
                         ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual([len(x["prior"]) for x in seen], [0, 1, 2])
        self.assertTrue(all(x["owner_go"] is True for x in seen))
        self.assertEqual({x["pair"] for x in seen}, {"uai_idr"})
        self.assertEqual(len({x["digest"] for x in seen}), 1)
        for x in r["responses"]:
            self.assertFalse(x["actual_charge_verified"])
            self.assertEqual(x["prompt_tokens_reported"], 100)
            self.assertEqual(x["completion_tokens_reported"], 80)
        self.assert_non_trade(r)

    def test_no_owner_confirmation_never_reaches_transport(self):
        seen = []
        for props in (
            {"owner_confirmation": None},
            {"owner_confirmation": "GO"},
            {"owner_pricing_reviewed": False},
            {"budget": False},
            {"api_key": ""},
            {"owner_models": {"MARKET": "glm-5.3-flash"}},
        ):
            r = self.run_owner(transport=fixture_transport(seen), **props)
            self.assertEqual(r["provider_http_attempts"], 0)
            self.assertFalse(r["owner_paid_test_approved"])
            self.assert_non_trade(r)
        self.assertEqual(seen, [])

    def test_durable_budget_three_roles_reserve_then_replay_denied(self):
        seen = []
        budget = self.budget()
        first = self.run_owner(budget=budget, transport=fixture_transport(seen))
        self.assertEqual(first["provider_http_attempts"], 3)
        second = self.run_owner(budget=budget, transport=fixture_transport(seen))
        self.assertEqual(second["status"],
                         "M04_PUBLIC_WATCH_BUDGET_OR_DUPLICATE_BLOCKED")
        self.assertEqual(second["provider_http_attempts"], 0)
        self.assertEqual(len(seen), 3)
        self.assert_non_trade(second)

    def test_operator_daily_cap_stops_after_two_test_calls(self):
        seen = []
        budget = self.budget(daily=600_000, per=300_000)
        r = self.run_owner(budget=budget, transport=fixture_transport(seen))
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_BUDGET_OR_DUPLICATE_BLOCKED")
        self.assertEqual(r["provider_http_attempts"], 2)
        self.assertEqual(r["roles_completed"], ["MARKET", "RISK"])
        self.assertEqual(len(seen), 2)
        self.assert_non_trade(r)

    def test_risk_allow_shape_fails_and_no_strategy_call(self):
        seen = []
        r = self.run_owner(transport=fixture_transport(seen, corrupt_role="RISK"))
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_PROVIDER_RESPONSE_REJECTED")
        self.assertEqual(r["roles_completed"], ["MARKET"])
        self.assertEqual(r["provider_http_attempts"], 2)
        self.assertEqual(len(seen), 2)
        self.assert_non_trade(r)

    def test_strategy_buy_shape_fails_and_no_proposal(self):
        seen = []
        r = self.run_owner(transport=fixture_transport(
            seen, corrupt_role="STRATEGY"))
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_PROVIDER_RESPONSE_REJECTED")
        self.assertEqual(r["roles_completed"], ["MARKET", "RISK"])
        self.assertEqual(r["provider_http_attempts"], 3)
        self.assert_non_trade(r)

    def test_model_mismatch_stops_on_first_response(self):
        seen = []
        r = self.run_owner(transport=fixture_transport(
            seen, response_model="unapproved-different-model"))
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_PROVIDER_RESPONSE_REJECTED")
        self.assertEqual(r["provider_http_attempts"], 1)
        self.assertEqual(r["roles_completed"], [])
        self.assert_non_trade(r)

    def test_transport_timeout_never_retries(self):
        seen = []
        r = self.run_owner(transport=fixture_transport(seen, fail_at=2))
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_PROVIDER_FAILED_NO_RETRY")
        self.assertEqual(r["provider_http_attempts"], 2)
        self.assertEqual(r["roles_completed"], ["MARKET"])
        self.assertEqual(len(seen), 1)
        self.assert_non_trade(r)

    def test_source_expiry_during_run_stops_before_next_role(self):
        seen = []
        ticks = iter([NOW, NOW, NOW+100])
        r = self.run_owner(
            transport=fixture_transport(seen), clock=lambda: next(ticks),
        )
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_SOURCE_EXPIRED_BEFORE_ROLE")
        self.assertEqual(r["provider_http_attempts"], 1)
        self.assertEqual(r["roles_completed"], ["MARKET"])
        self.assertEqual(len(seen), 1)
        self.assert_non_trade(r)

    def test_invalid_or_tampered_source_blocks_all_paid_calls(self):
        seen = []
        h = handoff([candidate()])
        h["candidates"][0]["public_evidence"]["price_snapshot_after_idr"] = "777"
        r = self.run_owner(h, transport=fixture_transport(seen))
        self.assertEqual(r["provider_http_attempts"], 0)
        self.assertEqual(r["status"],
                         "M04_PUBLIC_WATCH_SOURCE_OR_PROVIDER_PREVIEW_REJECTED")
        self.assertEqual(seen, [])
        self.assert_non_trade(r)

    def test_original_two_candidate_cohort_only_first_is_billable(self):
        seen = []
        h = handoff([candidate("uai_idr"), candidate("sol_idr")])
        r = self.run_owner(h, transport=fixture_transport(seen))
        self.assertEqual(r["provider_http_attempts"], 3)
        self.assertEqual({x["pair"] for x in seen}, {"uai_idr"})
        self.assert_non_trade(r)

    def test_no_qualifying_watch_is_zero_calls(self):
        seen = []
        r = self.run_owner(handoff([]), transport=fixture_transport(seen))
        self.assertEqual(r["status"], "M04_PUBLIC_WATCH_NO_CURRENT_CANDIDATE")
        self.assertEqual(r["provider_http_attempts"], 0)
        self.assertEqual(seen, [])
        self.assert_non_trade(r)

    def test_no_budget_cannot_call_even_if_transport_injected(self):
        seen = []
        r = self.run_owner(budget=False, transport=fixture_transport(seen))
        self.assertEqual(r["provider_http_attempts"], 0)
        self.assertFalse(r["owner_paid_test_approved"])
        self.assertEqual(seen, [])
        self.assert_non_trade(r)

    def test_no_hidden_network_when_default_or_missing_go(self):
        with patch(
            "indodax_ai_bot.m04_public_watch_paid_once._post",
            side_effect=AssertionError("UNEXPECTED_PAID_CALL")
        ):
            r = run_public_watch_paid_once(
                handoff=handoff([candidate()]),
                now_fn=lambda: NOW,
            )
        self.assertEqual(r["provider_http_attempts"], 0)
        self.assert_non_trade(r)

    def test_cli_default_is_off_even_with_local_config_connected(self):
        from scripts.m04_public_watch_paid_owner_once import main
        with patch("sys.argv", ["m04_public_watch_paid_owner_once"]), patch(
            "scripts.m04_public_watch_paid_owner_once.owner_provider_source",
            side_effect=AssertionError("MUST_NOT_OPEN_SECRET")
        ), patch(
            "scripts.m04_public_watch_paid_owner_once."
            "inspect_original_radar_three_role_handoff_readonly",
            side_effect=AssertionError("MUST_NOT_SCAN_OR_READ")
        ):
            self.assertEqual(main(), 0)

    def test_cli_missing_amounts_block_before_any_secret_or_source(self):
        from scripts.m04_public_watch_paid_owner_once import main
        with patch("sys.argv", [
            "m04_public_watch_paid_owner_once",
            "--execute-paid-once",
            "--owner-confirm", OWNER_PAID_ONCE_CONFIRMATION,
            "--owner-pricing-reviewed",
        ]), patch(
            "scripts.m04_public_watch_paid_owner_once.owner_provider_source",
            side_effect=AssertionError("MUST_NOT_OPEN_SECRET")
        ), patch(
            "scripts.m04_public_watch_paid_owner_once."
            "inspect_original_radar_three_role_handoff_readonly",
            side_effect=AssertionError("MUST_NOT_READ_ORIGINAL_SOURCE")
        ):
            self.assertEqual(main(), 2)


if __name__ == "__main__":
    unittest.main()
