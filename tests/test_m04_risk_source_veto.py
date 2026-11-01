"""M04 RISK source-bound veto, synthetic fixtures only, zero real order authority.

The source-claim examples below are NOT private exchange proofs; all role
calls are injected local test callers, no owner Final exists, and all
results remain non-executable.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import hmac
from unittest.mock import Mock
import unittest

from indodax_ai_bot.g1_receipt import G1ReceiptVerifier, canonical_receipt_bytes
from indodax_ai_bot.three_role_ai import ThreeRoleShadow, _risk_source_report
from test_m04a_three_role_shadow import (
    RoleCaller, TEST_G1_SECRET, signed_role_run, source,
)
from test_m04t_two_candidate_shadow import signed_two_pair_source, CLOCK, PAIRS


def signed_variant(change):
    """Sign a modified synthetic fixture with the UNIT-TEST key only."""
    bulk = source(trusted=True)
    change(bulk)
    preliminary = ThreeRoleShadow(clock=lambda: CLOCK).run("trace", bulk)
    if preliminary["reason"] != "G1_ISSUER_RECEIPT_UNVERIFIED":
        raise AssertionError("SIGNED_CASE_GATE_CHANGED:" + preliminary["reason"])
    claims = {
        "schema_version": 1, "issuer_id": "synthetic-unit-issuer",
        "trace_id": "trace", "pair": "btc_idr",
        "closed_1m_epoch": 1800000120,
        "source_digest_sha256": preliminary["source_digest_sha256"],
        "expires_epoch": 1800000180,
    }
    bulk["g1_attestation"] = {
        "claims": claims,
        "mac_sha256": hmac.new(
            TEST_G1_SECRET, canonical_receipt_bytes(claims), sha256
        ).hexdigest(),
    }
    verifier = G1ReceiptVerifier(
        issuer_id="synthetic-unit-issuer",
        verification_secret=TEST_G1_SECRET,
    )
    return bulk, verifier


def market_row(bulk, pair="btc_idr"):
    selected = bulk.get("provisional_market_evidence") or []
    targets = [x for x in selected if x["pair"] == pair]
    if targets:
        return targets[0]
    return next(
        x for x in bulk["ranked_diagnostics_top10"] if x["pair"] == pair
    )


class M04RiskSourceBoundVetoTests(unittest.TestCase):
    def test_synthetic_signed_claims_allow_only_nonexecutable_shadow_path(self):
        caller = RoleCaller()
        result = signed_role_run(caller)
        self.assertEqual(caller.calls, ["MARKET", "RISK", "STRATEGY"])
        risk = result["role_results"][1]
        self.assertEqual(risk["decision"], "ALLOW")
        self.assertEqual(risk["risk_source_report"]["blockers"], [])
        self.assertFalse(risk["risk_source_report"]["execution_allowed"])
        self.assertFalse(risk["risk_source_report"][
            "independent_exchange_account_proof_verified"])
        self.assertEqual(
            caller.inputs[1]["risk_source_report"],
            risk["risk_source_report"],
        )
        self.assertIsNone(result["final_decision"])
        self.assertFalse(result["execution_allowed"])

    def test_missing_balance_claim_forces_no_trade_even_if_model_allows(self):
        def change(bulk):
            del bulk["risk_source_claims_by_pair"]["btc_idr"]["spendable_balance"]
        bulk, verifier = signed_variant(change)
        caller = RoleCaller(risk="ALLOW")
        report = ThreeRoleShadow(
            caller=caller, source_verifier=verifier, clock=lambda: CLOCK,
        ).run("trace", bulk)
        self.assertEqual(caller.calls, ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual(report["reason"], "RISK_VETO_NO_TRADE")
        risk = report["role_results"][1]
        self.assertEqual(risk["model_decision"], "ALLOW")
        self.assertEqual(risk["decision"], "NO_TRADE")
        self.assertIn(
            "MISSING_SPENDABLE_BALANCE_EVIDENCE",
            risk["risk_source_report"]["blockers"],
        )
        self.assertFalse(report["execution_allowed"])
        self.assertEqual(report["role_results"][-1]["effective_action"], "WAIT")
        self.assertIsNone(report["final_decision"])

    def test_wide_spread_warning_reaches_strategy_advisory(self):
        bulk, verifier = signed_variant(
            lambda data: market_row(data).update(spread_bps="120")
        )
        caller = RoleCaller(risk="ALLOW")
        report = ThreeRoleShadow(
            caller=caller, source_verifier=verifier, clock=lambda: CLOCK
        ).run("trace", bulk)
        self.assertEqual(caller.calls, ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual(report["reason"], "RISK_VETO_NO_TRADE")
        self.assertIn(
            "SPREAD_MISSING_OR_TOO_WIDE",
            report["role_results"][1]["risk_source_report"]["blockers"],
        )

    def test_anomalous_pair_forces_risk_veto(self):
        bulk, verifier = signed_variant(
            lambda data: market_row(data).update(anomaly_flags=["FAKE_PUMP"])
        )
        caller = RoleCaller()
        report = ThreeRoleShadow(
            caller=caller, source_verifier=verifier, clock=lambda: CLOCK
        ).run("trace", bulk)
        self.assertEqual(caller.calls, ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual(report["reason"], "RISK_VETO_NO_TRADE")
        self.assertIn(
            "ANOMALY_OR_MAINTENANCE_REVIEW",
            report["role_results"][1]["risk_source_report"]["blockers"],
        )

    def test_forged_risk_report_is_rejected_without_strategy(self):
        bulk, verifier = signed_variant(lambda _: None)
        calls = []
        def caller(role, evidence):
            calls.append(role)
            response = RoleCaller()(role, evidence)
            if role == "RISK":
                evidence["risk_source_report"]["blockers"] = []
                response["risk_source_report"]["blockers"].append("FAKE")
            return response
        result = ThreeRoleShadow(
            caller=caller, source_verifier=verifier, clock=lambda: CLOCK
        ).run("trace", bulk)
        self.assertEqual(calls, ["MARKET", "RISK"])
        self.assertEqual(result["reason"], "M04_ROLE_INVALID_OR_UNAVAILABLE")
        self.assertEqual(result["completed_roles"], ["MARKET"])
        self.assertFalse(result["execution_allowed"])

    def test_later_tampering_of_signed_account_claim_blocks_all_roles(self):
        bulk, verifier = signed_variant(lambda _: None)
        bulk["risk_source_claims_by_pair"]["btc_idr"]["listing"]["status"] = "FAKE"
        caller = Mock()
        result = ThreeRoleShadow(
            caller=caller, source_verifier=verifier, clock=lambda: CLOCK,
        ).run("trace", bulk)
        self.assertEqual(result["reason"], "G1_ISSUER_RECEIPT_UNVERIFIED")
        caller.assert_not_called()

    def test_two_candidate_synthetic_risk_claims_are_isolated(self):
        bulk, verifier = signed_two_pair_source()
        caller = RoleCaller()
        out = ThreeRoleShadow(
            caller=caller, source_verifier=verifier, clock=lambda: CLOCK,
        ).run("trace", bulk)
        self.assertEqual(out["candidate_count"], 2)
        self.assertEqual(caller.calls, ["MARKET", "RISK", "STRATEGY"] * 2)
        self.assertEqual(
            [caller.inputs[i]["risk_source_report"]["pair"] for i in (1, 4)],
            list(PAIRS),
        )
        self.assertIsNone(out["final_decision"])
        self.assertFalse(out["execution_allowed"])

    def test_plain_public_source_is_still_blocked_before_market(self):
        caller = Mock()
        result = ThreeRoleShadow(
            caller=caller, clock=lambda: CLOCK,
        ).run("trace", source())
        self.assertEqual(result["reason"], "G1_PUBLIC_EVIDENCE_UNTRUSTED")
        caller.assert_not_called()


if __name__ == "__main__":
    unittest.main()
