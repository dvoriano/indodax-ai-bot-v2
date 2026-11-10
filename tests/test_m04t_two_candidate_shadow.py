"""M04-T: at most 2 candidate source-bound three-role contracts.

Test-only HMAC issuer and RoleCaller. Never proves trusted Indodax sources
or enables real providers, money, or a Final PASS.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import hmac
from unittest.mock import Mock
import unittest

from indodax_ai_bot.g1_receipt import G1ReceiptVerifier, canonical_receipt_bytes
from indodax_ai_bot.three_role_ai import ThreeRoleShadow
from test_m04a_three_role_shadow import RoleCaller, TEST_G1_SECRET, source, synthetic_issuer_coverage_manifest


PAIRS = ("btc_idr", "token000_idr")
CLOCK = 1800000140


def two_pair_source(*, trusted=True):
    bulk = source(trusted=trusted)
    cohort = bulk["eligible_cohort"]
    cohort["provisional_watchlist_preview"] = list(PAIRS)
    if trusted:
        epoch = cohort["closed_1m_epoch"]
        bulk["risk_source_claims_by_pair"][PAIRS[1]] = {
            name: {
                "pair": PAIRS[1], "closed_1m_epoch": epoch,
                "status": "SOURCE_BOUND_CLAIM_ONLY",
            }
            for name in ("listing", "spendable_balance", "fee_slippage",
                         "owned_position", "market_liquidity")
        }
        bulk["g1_coverage_manifests_by_pair"][PAIRS[1]] = (
            synthetic_issuer_coverage_manifest(PAIRS[1], epoch)
        )
    for item in cohort["provisional_quality_reviews"]:
        if item["pair"] in PAIRS:
            item["clear_of_provisional_cautions"] = True
            item["review_flags"] = []
    return bulk


def signed_two_pair_source():
    bulk = two_pair_source()
    receipts = {}
    for pair in PAIRS:
        single = deepcopy(bulk)
        single["eligible_cohort"]["provisional_watchlist_preview"] = [pair]
        phase = ThreeRoleShadow(clock=lambda: CLOCK).run("trace", single)
        if phase["reason"] != "G1_ISSUER_RECEIPT_UNVERIFIED":
            raise AssertionError("TEST_FIXTURE_DID_NOT_PASS_PUBLIC_G1_GATE:" + phase["reason"])
        digest = phase["source_digest_sha256"]
        claims = {
            "schema_version": 1,
            "issuer_id": "synthetic-unit-issuer",
            "trace_id": "trace",
            "pair": pair,
            "closed_1m_epoch": 1800000120,
            "source_digest_sha256": digest,
            "expires_epoch": 1800000180,
        }
        receipts[pair] = {
            "claims": claims,
            "mac_sha256": hmac.new(
                TEST_G1_SECRET, canonical_receipt_bytes(claims), sha256
            ).hexdigest(),
        }
    bulk["g1_attestations"] = receipts
    verifier = G1ReceiptVerifier(
        issuer_id="synthetic-unit-issuer",
        verification_secret=TEST_G1_SECRET,
    )
    return bulk, verifier


class M04TTwoCandidateShadowTests(unittest.TestCase):
    def test_public_two_preview_never_reaches_role_caller(self):
        caller = Mock()
        result = ThreeRoleShadow(caller=caller, clock=lambda: CLOCK).run(
            "trace", two_pair_source(trusted=False),
        )
        self.assertEqual(result["reason"], "G1_PUBLIC_EVIDENCE_UNTRUSTED")
        self.assertEqual(result["completed_roles"], [])
        caller.assert_not_called()

    def test_trusted_flags_without_two_receipts_never_call_models(self):
        caller = Mock()
        result = ThreeRoleShadow(caller=caller, clock=lambda: CLOCK).run(
            "trace", two_pair_source(),
        )
        self.assertEqual(result["reason"], "G1_ISSUER_RECEIPT_UNVERIFIED")
        caller.assert_not_called()

    def test_two_signed_test_candidates_each_receive_exact_three_roles(self):
        bulk, verifier = signed_two_pair_source()
        caller = RoleCaller()
        result = ThreeRoleShadow(
            caller=caller, clock=lambda: CLOCK, source_verifier=verifier,
        ).run("trace", bulk)
        self.assertEqual(result["reason"], "M04_TWO_CANDIDATE_SHADOW_ONLY")
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(len(result["candidate_reports"]), 2)
        self.assertEqual(result["completed_role_calls"], 6)
        self.assertEqual(caller.calls, ["MARKET", "RISK", "STRATEGY"] * 2)
        self.assertEqual(
            [p["pair"] for p in result["candidate_reports"]], list(PAIRS)
        )
        for pair, report in zip(PAIRS, result["candidate_reports"]):
            self.assertEqual(report["completed_roles"], ["MARKET", "RISK", "STRATEGY"])
            self.assertEqual(report["evidence_digest_sha256"], result["source_digests_by_pair"][pair])
        first, second = caller.inputs[:3], caller.inputs[3:]
        self.assertEqual({e["source"]["pair"] for e in first}, {PAIRS[0]})
        self.assertEqual({e["source"]["pair"] for e in second}, {PAIRS[1]})
        self.assertEqual(len({e["evidence_digest_sha256"] for e in first}), 1)
        self.assertEqual(len({e["evidence_digest_sha256"] for e in second}), 1)
        self.assertNotEqual(first[0]["evidence_digest_sha256"],
                            second[0]["evidence_digest_sha256"])
        self.assertTrue(all(e["prior_role_results"] == [] for e in (first[0], second[0])))
        self.assertIsNone(result["final_decision"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["provider_connected"])

    def test_invalid_second_signature_blocks_first_model_call(self):
        bulk, verifier = signed_two_pair_source()
        bulk["g1_attestations"][PAIRS[1]]["mac_sha256"] = "0" * 64
        caller = Mock()
        result = ThreeRoleShadow(
            caller=caller, clock=lambda: CLOCK, source_verifier=verifier,
        ).run("trace", bulk)
        self.assertEqual(result["candidate_pair"], PAIRS[1])
        self.assertEqual(result["reason"], "G1_ISSUER_RECEIPT_UNVERIFIED")
        caller.assert_not_called()

    def test_second_candidate_quality_failure_blocks_all_model_calls(self):
        bulk, verifier = signed_two_pair_source()
        for row in bulk["eligible_cohort"]["provisional_quality_reviews"]:
            if row["pair"] == PAIRS[1]:
                row["clear_of_provisional_cautions"] = False
                row["review_flags"] = ["BOOK_SELL_DOMINANT"]
        caller = Mock()
        result = ThreeRoleShadow(
            caller=caller, clock=lambda: CLOCK, source_verifier=verifier,
        ).run("trace", bulk)
        # The shared quality-review evidence is part of BOTH pair digests.
        # Mutating one review after signing invalidates the first issuer
        # receipt as well, so the entire batch fails before any caller.
        self.assertEqual(result["reason"], "G1_ISSUER_RECEIPT_UNVERIFIED")
        caller.assert_not_called()

    def test_risk_no_trade_keeps_strategy_advisory_on_each_pair(self):
        bulk, verifier = signed_two_pair_source()
        caller = RoleCaller(risk="NO_TRADE")
        result = ThreeRoleShadow(
            caller=caller, clock=lambda: CLOCK, source_verifier=verifier,
        ).run("trace", bulk)
        self.assertEqual(caller.calls, ["MARKET", "RISK", "STRATEGY"] * 2)
        self.assertEqual(result["completed_role_calls"], 6)
        self.assertTrue(all(
            r["reason"] == "RISK_VETO_NO_TRADE"
            and r["completed_roles"] == ["MARKET", "RISK", "STRATEGY"]
            and r["role_results"][-1]["effective_action"] == "WAIT"
            for r in result["candidate_reports"]
        ))
        self.assertIsNone(result["final_decision"])
        self.assertFalse(result["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
