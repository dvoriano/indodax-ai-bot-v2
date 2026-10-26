"""M04-C: independent internal G1 receipt verifier regression, NO paid API.

Test HMAC keys and flagged fixture are synthetic. Not source attestation of
Indodax public data, and cannot enable an exchange order.
"""
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import Mock

from indodax_ai_bot.g1_receipt import G1ReceiptVerifier, canonical_receipt_bytes
from indodax_ai_bot.three_role_ai import ThreeRoleShadow
from test_m04a_three_role_shadow import (
    RoleCaller, TEST_G1_SECRET, signed_source_fixture, signed_role_run, source,
)


class M04CSourceAttestationTests(unittest.TestCase):
    def test_plain_public_source_never_calls_role_caller(self):
        mock = Mock()
        result = ThreeRoleShadow(caller=mock, clock=lambda: 1800000140).run(
            "trace", source()
        )
        self.assertEqual(result["reason"], "G1_PUBLIC_EVIDENCE_UNTRUSTED")
        mock.assert_not_called()
        self.assertFalse(result["execution_allowed"])

    def test_forged_boolean_flags_without_issuer_receipt_never_calls_provider(self):
        mock = Mock()
        result = ThreeRoleShadow(caller=mock, clock=lambda: 1800000140).run(
            "trace", source(trusted=True)
        )
        self.assertEqual(result["reason"], "G1_ISSUER_RECEIPT_UNVERIFIED")
        self.assertEqual(result["completed_roles"], [])
        mock.assert_not_called()

    def test_valid_synthetic_signed_source_executes_exact_three_test_roles_no_order(self):
        caller = RoleCaller()
        result = signed_role_run(caller)
        self.assertEqual(caller.calls, ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual(result["completed_roles"], caller.calls)
        self.assertEqual(result["final_decision"], "REJECT")
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["g2_candidate_authorized"])

    def test_modified_market_evidence_after_receipt_signing_is_rejected(self):
        bulk, verifier = signed_source_fixture()
        original = bulk["ranked_diagnostics_top10"][0]["volume_ratio"]
        bulk["ranked_diagnostics_top10"][0]["volume_ratio"] = "9999.00"
        self.assertNotEqual(original, "9999.00")
        mock = Mock()
        report = ThreeRoleShadow(caller=mock, clock=lambda: 1800000140,
                                 source_verifier=verifier).run("trace", bulk)
        self.assertEqual(report["reason"], "G1_ISSUER_RECEIPT_UNVERIFIED")
        mock.assert_not_called()

    def test_changed_trace_replay_blocked_by_receipt_pair_identity(self):
        bulk, verifier = signed_source_fixture()
        mock = Mock()
        report = ThreeRoleShadow(caller=mock, clock=lambda: 1800000140,
                                 source_verifier=verifier).run("other-trace", bulk)
        self.assertEqual(report["reason"], "G1_ISSUER_RECEIPT_UNVERIFIED")
        mock.assert_not_called()

    def test_expired_issuer_receipt_blocked_even_before_source_max_age(self):
        bulk, verifier = signed_source_fixture()
        mock = Mock()
        report = ThreeRoleShadow(caller=mock, clock=lambda: 1800000181,
                                 source_verifier=verifier).run("trace", bulk)
        self.assertEqual(report["reason"], "G1_ISSUER_RECEIPT_UNVERIFIED")
        mock.assert_not_called()

    def test_different_verification_key_or_issuer_is_rejected(self):
        bulk, _ = signed_source_fixture()
        for issuer, secret in (
            ("synthetic-unit-issuer", b"x"*32),
            ("unrecognized-issuer", TEST_G1_SECRET),
        ):
            with self.subTest(issuer=issuer, secret_match=secret == TEST_G1_SECRET):
                verifier = G1ReceiptVerifier(
                    issuer_id=issuer, verification_secret=secret
                )
                mock = Mock()
                report = ThreeRoleShadow(caller=mock, clock=lambda: 1800000140,
                                         source_verifier=verifier).run("trace", bulk)
                self.assertEqual(report["reason"], "G1_ISSUER_RECEIPT_UNVERIFIED")
                mock.assert_not_called()

    def test_attestation_cannot_upgrade_false_trade_coverage_even_if_signed(self):
        bulk, verifier = signed_source_fixture()
        bulk["trade_history_complete"] = False
        mock = Mock()
        report = ThreeRoleShadow(caller=mock, clock=lambda: 1800000140,
                                 source_verifier=verifier).run("trace", bulk)
        self.assertEqual(report["reason"], "G1_PUBLIC_EVIDENCE_UNTRUSTED")
        mock.assert_not_called()

    def test_tampered_receipt_fields_and_mac_fail_without_throwing(self):
        original, verifier = signed_source_fixture()
        mutated = []
        for key, val in (("pair", "eth_idr"), ("closed_1m_epoch", 1800000060),
                         ("issuer_id", "unknown-issuer"), ("expires_epoch", 1800000500),
                         ("source_digest_sha256", "b"*64)):
            copy = deepcopy(original)
            copy["g1_attestation"]["claims"][key] = val
            mutated.append(copy)
        copy = deepcopy(original)
        copy["g1_attestation"]["mac_sha256"] = "0"*64
        mutated.append(copy)
        copy = deepcopy(original)
        copy["g1_attestation"]["extra"] = True
        mutated.append(copy)
        for bulk in mutated:
            mock = Mock()
            with self.subTest(receipt=bulk["g1_attestation"]):
                report = ThreeRoleShadow(caller=mock, clock=lambda: 1800000140,
                                         source_verifier=verifier).run("trace", bulk)
                self.assertEqual(report["reason"], "G1_ISSUER_RECEIPT_UNVERIFIED")
                mock.assert_not_called()

    def test_receipt_schema_and_short_secret_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "INVALID_G1_VERIFIER_SECRET"):
            G1ReceiptVerifier(issuer_id="test-issuer", verification_secret=b"short")
        with self.assertRaisesRegex(ValueError, "INVALID_G1_RECEIPT_FIELDS"):
            canonical_receipt_bytes({"issuer_id": "synthetic-unit-issuer"})
        with self.assertRaisesRegex(ValueError, "INVALID_G1_VERIFIER_ISSUER"):
            G1ReceiptVerifier(issuer_id="bad issuer!", verification_secret=b"0"*32)

if __name__ == "__main__":
    unittest.main()
