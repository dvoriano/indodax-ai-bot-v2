"""M04-S: inject a real G1 receipt verifier into EXISTING bulk mainpath.

Production CLI deliberately has no verifier or paid provider. Synthetic
unit fixtures prove wiring only, not actual Indodax attestation.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.three_role_ai import ThreeRoleShadow as RealShadow
from test_m04a_three_role_shadow import RoleCaller, source
from test_m04t_two_candidate_shadow import signed_two_pair_source, CLOCK
from test_m03g_bulk import FakeBulkMarket, fake_screen


class M04SG1VerifierMainpathTests(unittest.TestCase):
    def run_mainpath(self, *, evidence, caller, verifier=None):
        with TemporaryDirectory() as td:
            app = DailyScalpingOrchestrator(
                Settings(), DecisionJournal(Path(td) / "decisions.sqlite3"),
                ai_role_caller=caller, source_verifier=verifier,
            )
            with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scanner:
                scanner.return_value.scan.return_value = fake_screen()
                with patch("indodax_ai_bot.orchestrator.bulk_public_deep_scan",
                           return_value=evidence):
                    # The clock is fixed only to exercise signed unit fixtures
                    # created at their synthetic epoch; production clock unpatched.
                    with patch(
                        "indodax_ai_bot.orchestrator.ThreeRoleShadow",
                        side_effect=lambda **kw: RealShadow(
                            clock=lambda: CLOCK, **kw
                        ),
                    ):
                        out = app.run_bulk_deep_cycle(
                            "trace", market=FakeBulkMarket(),
                        )
        return out

    def test_public_source_with_test_verifier_still_cannot_call_any_role(self):
        _, verifier = signed_two_pair_source()
        caller = Mock()
        out = self.run_mainpath(
            evidence=source(trusted=False), caller=caller, verifier=verifier,
        )
        caller.assert_not_called()
        self.assertEqual(out.ai_shadow["reason"], "G1_PUBLIC_EVIDENCE_UNTRUSTED")
        self.assertIsNone(out.final_decision)
        self.assertFalse(out.execution_allowed)

    def test_signed_test_source_with_no_injected_verifier_cannot_call_roles(self):
        evidence, _ = signed_two_pair_source()
        caller = Mock()
        out = self.run_mainpath(evidence=evidence, caller=caller)
        caller.assert_not_called()
        self.assertEqual(out.ai_shadow["reason"], "G1_ISSUER_RECEIPT_UNVERIFIED")
        self.assertEqual(out.ai_shadow["completed_roles"], [])
        self.assertFalse(out.execution_allowed)

    def test_synthetic_signed_receipts_are_wired_to_two_candidate_shadow(self):
        evidence, verifier = signed_two_pair_source()
        caller = RoleCaller()
        out = self.run_mainpath(
            evidence=evidence, caller=caller, verifier=verifier,
        )
        self.assertEqual(caller.calls, ["MARKET", "RISK", "STRATEGY"] * 2)
        self.assertEqual(out.ai_shadow["candidate_count"], 2)
        self.assertEqual(out.ai_shadow["completed_role_calls"], 6)
        self.assertEqual(out.ai_shadow["reason"], "M04_TWO_CANDIDATE_SHADOW_ONLY")
        self.assertIsNone(out.final_decision)
        self.assertFalse(out.execution_allowed)
        self.assertFalse(out.ai_shadow["provider_connected"])

    def test_invalid_verifier_dependency_rejected_before_any_source_fetch(self):
        with TemporaryDirectory() as td:
            with self.assertRaisesRegex(TypeError, "INVALID_G1_SOURCE_VERIFIER"):
                DailyScalpingOrchestrator(
                    Settings(), DecisionJournal(Path(td) / "decisions.sqlite3"),
                    ai_role_caller=Mock(), source_verifier={"verified": True},
                )


if __name__ == "__main__":
    unittest.main()
