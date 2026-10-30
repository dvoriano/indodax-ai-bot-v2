"""M04: immutable per-candidate source-bound shadow inputs (synthetic only).

These tests do not attest Indodax source, qualify G1, call a paid provider,
or authorize an order. The existing original orchestrator is reused.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from unittest.mock import Mock
import unittest

from indodax_ai_bot.three_role_ai import ROLES, ThreeRoleShadow
from test_m04a_three_role_shadow import (
    RoleCaller, signed_source_fixture, source,
)


class M04ImmutableCandidateSnapshotTests(unittest.TestCase):
    def test_market_risk_strategy_receive_identical_canonical_source(self):
        bulk, verifier = signed_source_fixture()
        caller = RoleCaller()
        result = ThreeRoleShadow(
            caller=caller, source_verifier=verifier,
            clock=lambda: 1800000140,
        ).run("trace", bulk)

        self.assertEqual(caller.calls, list(ROLES))
        self.assertEqual(result["reason"], "STRATEGY_BUY_PREFLIGHT_UNVERIFIED")
        original = caller.inputs[0]["source"]
        canonical = json.dumps(
            original, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode("utf-8")
        digest = sha256(canonical).hexdigest()
        self.assertEqual(digest, result["source_digest_sha256"])
        self.assertIn("btc_idr", original["public_timeframe_evidence"])
        self.assertIsNotNone(
            original["public_timeframe_evidence"]["btc_idr"]["timeframe_diagnostic"]
        )
        for inp in caller.inputs:
            self.assertEqual(inp["source"], original)
            self.assertEqual(inp["market_evidence"], original["market_evidence"])
            self.assertEqual(inp["evidence_digest_sha256"], digest)
            self.assertEqual(inp["snapshot_contract"], {
                "schema_version": 1,
                "pair": "btc_idr",
                "closed_1m_epoch": 1800000120,
                "evidence_digest_sha256": digest,
            })
            self.assertFalse(inp["live_execution_enabled"])
        self.assertFalse(result["execution_allowed"])

    def test_market_caller_cannot_mutate_risk_or_strategy_source(self):
        bulk, verifier = signed_source_fixture()
        observed = []
        delegate = RoleCaller()

        def caller(role, inp):
            observed.append(deepcopy(inp))
            response = delegate(role, inp)
            if role == "MARKET":
                # A hypothetical provider can mutate only its own copy.
                inp["source"]["btc_regime"] = "FORGED"
                inp["source"]["market_evidence"]["score"] = -999
                inp["market_evidence"]["score"] = -999
                inp["snapshot_contract"]["evidence_digest_sha256"] = "0" * 64
                inp["prior_role_results"].append({"role": "FORGED"})
            return response

        out = ThreeRoleShadow(
            caller=caller, source_verifier=verifier,
            clock=lambda: 1800000140,
        ).run("trace", bulk)
        self.assertEqual([x["role_order"] for x in observed],
                         [list(ROLES)] * 3)
        self.assertEqual([x["source"] for x in observed],
                         [observed[0]["source"]] * 3)
        self.assertEqual(
            [x["snapshot_contract"] for x in observed],
            [observed[0]["snapshot_contract"]] * 3,
        )
        self.assertEqual([len(x["prior_role_results"]) for x in observed],
                         [0, 1, 2])
        self.assertEqual(out["reason"], "STRATEGY_BUY_PREFLIGHT_UNVERIFIED")
        self.assertFalse(out["execution_allowed"])

    def test_tampered_public_5m_context_invalidates_previously_signed_digest(self):
        bulk, verifier = signed_source_fixture()
        coverage = bulk["g1_source_admission"]["pair_coverage"]
        btc = next(x for x in coverage if x["pair"] == "btc_idr")
        btc["timeframe_diagnostic"]["closed_5m_start_epoch"] += 300
        mock = Mock(side_effect=AssertionError("model must remain blocked"))
        out = ThreeRoleShadow(
            caller=mock, source_verifier=verifier,
            clock=lambda: 1800000140,
        ).run("trace", bulk)
        self.assertEqual(out["reason"], "G1_ISSUER_RECEIPT_UNVERIFIED")
        self.assertFalse(out["g2_candidate_authorized"])
        mock.assert_not_called()

    def test_mismatched_public_pair_minute_fails_before_role_caller(self):
        bulk, verifier = signed_source_fixture()
        coverage = bulk["g1_source_admission"]["pair_coverage"]
        btc = next(x for x in coverage if x["pair"] == "btc_idr")
        btc["closed_1m_epoch"] += 60
        mock = Mock()
        out = ThreeRoleShadow(
            caller=mock, source_verifier=verifier,
            clock=lambda: 1800000140,
        ).run("trace", bulk)
        self.assertEqual(out["reason"], "G1_SOURCE_TIMING_EPOCH_MISMATCH")
        self.assertFalse(out["execution_allowed"])
        mock.assert_not_called()

    def test_public_untrusted_candidate_never_uses_snapshot_to_call_models(self):
        mock = Mock()
        out = ThreeRoleShadow(
            caller=mock, clock=lambda: 1800000140,
        ).run("trace", source())
        self.assertEqual(out["reason"], "G1_PUBLIC_EVIDENCE_UNTRUSTED")
        self.assertEqual(out["completed_roles"], [])
        self.assertFalse(out["g1_verified"])
        mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
