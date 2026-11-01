"""M04-A: structural 3-role shadow contract, never an executable bot/paid AI test."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from io import StringIO
from contextlib import redirect_stdout
import json
import hmac
from hashlib import sha256
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.deep_analysis import bulk_public_deep_scan
from indodax_ai_bot.three_role_ai import ROLES, ThreeRoleShadow
from indodax_ai_bot.g1_receipt import G1ReceiptVerifier, canonical_receipt_bytes
from test_m03g_bulk import FakeBulkMarket, fake_screen


def source(*, trusted=False, preview=True):
    result = bulk_public_deep_scan(FakeBulkMarket(), fake_screen())
    if preview:
        result["eligible_cohort"]["provisional_watchlist_preview"] = ["btc_idr"]
        for item in result["eligible_cohort"]["provisional_quality_reviews"]:
            if item["pair"] == "btc_idr":
                item["review_flags"] = []
                item["clear_of_provisional_cautions"] = True
    if trusted:
        # Synthetic TEST-ONLY source trust. Public production never sets these.
        for key in ("g1_verified", "source_trust_verified", "trade_history_complete"):
            result[key] = True
            result["eligible_cohort"][key] = True
        # Test-only SOURCE-BOUND claims exercise role sequencing. They are
        # not private exchange/issuer attestations or production readiness.
        epoch = result["eligible_cohort"]["closed_1m_epoch"]
        result["risk_source_claims_by_pair"] = {
            "btc_idr": {
                name: {
                    "pair": "btc_idr", "closed_1m_epoch": epoch,
                    "status": "SOURCE_BOUND_CLAIM_ONLY",
                }
                for name in ("listing", "spendable_balance", "fee_slippage",
                             "owned_position", "market_liquidity")
            }
        }
    return result


TEST_G1_SECRET = b"synthetic-test-only-internal-issuer-secret-00001"


def signed_source_fixture():
    """A private synthetic issuer key. Never used on production public data."""
    bulk = source(trusted=True)
    current = ThreeRoleShadow(clock=lambda: 1800000140).run("trace", bulk)
    if current["reason"] != "G1_ISSUER_RECEIPT_UNVERIFIED":
        raise AssertionError("SOURCE_BOUND_RECEIPT_GATE_NOT_REACHED")
    digest = current["source_digest_sha256"]
    claims = {
        "schema_version": 1,
        "issuer_id": "synthetic-unit-issuer",
        "trace_id": "trace",
        "pair": "btc_idr",
        "closed_1m_epoch": 1800000120,
        "source_digest_sha256": digest,
        "expires_epoch": 1800000180,
    }
    signed = hmac.new(TEST_G1_SECRET, canonical_receipt_bytes(claims), sha256).hexdigest()
    bulk["g1_attestation"] = {"claims": claims, "mac_sha256": signed}
    verifier = G1ReceiptVerifier(
        issuer_id="synthetic-unit-issuer",
        verification_secret=TEST_G1_SECRET,
    )
    return bulk, verifier


def signed_role_run(caller):
    bulk, verifier = signed_source_fixture()
    return ThreeRoleShadow(caller=caller, clock=lambda: 1800000140,
                           source_verifier=verifier).run("trace", bulk)


class RoleCaller:
    def __init__(self, risk="ALLOW", strategy="BUY", corrupt_role=None, corrupt_hash=False):
        self.calls = []
        self.inputs = []
        self.risk = risk
        self.strategy = strategy
        self.corrupt_role = corrupt_role
        self.corrupt_hash = corrupt_hash

    def __call__(self, role, evidence):
        self.calls.append(role)
        self.inputs.append(deepcopy(evidence))
        payload = {
            "role": self.corrupt_role if self.corrupt_role == role else role,
            "pair": evidence["source"]["pair"],
            "evidence_digest_sha256": (
                "0"*64 if self.corrupt_hash else evidence["evidence_digest_sha256"]),
            "reason": "synthetic role contract result",
        }
        if role == "MARKET":
            payload["bias"] = "MIXED"
            payload["evidence_status"] = deepcopy(evidence["market_fact_catalog"])
        elif role == "RISK":
            payload["decision"] = self.risk
            payload["risk_source_report"] = deepcopy(evidence["risk_source_report"])
        else:
            payload.update({
                "action": self.strategy,
                "projected_net_profit_idr": "15000",
                "projected_net_roi_percent": "2.50",
                "strategy_source_plan": deepcopy(evidence["strategy_source_plan"]),
            })
        return payload


class M04AShadowTests(unittest.TestCase):
    def test_real_public_untrusted_source_never_calls_provider(self):
        caller = Mock(side_effect=AssertionError("must not reach provider"))
        report = ThreeRoleShadow(caller=caller, clock=lambda:1800000140).run(
            "trace", source())
        self.assertEqual(report["status"], "REJECT")
        self.assertEqual(report["reason"], "G1_PUBLIC_EVIDENCE_UNTRUSTED")
        self.assertEqual(report["completed_roles"], [])
        self.assertEqual(len(report["source_digest_sha256"]),64)
        self.assertFalse(report["g1_verified"])
        self.assertFalse(report["g2_candidate_authorized"])
        self.assertFalse(report["execution_allowed"])
        caller.assert_not_called()

    def test_empty_preview_never_calls_provider(self):
        caller = Mock()
        report = ThreeRoleShadow(caller=caller,clock=lambda:1800000140).run(
            "trace", source(preview=False))
        self.assertEqual(report["reason"], "NO_PROVISIONAL_CANDIDATE")
        caller.assert_not_called()

    def test_quality_caution_cannot_be_bypassed_by_injected_preview(self):
        data = source(preview=False)
        data["eligible_cohort"]["provisional_watchlist_preview"]=["btc_idr"]
        caller=Mock()
        report=ThreeRoleShadow(caller=caller,clock=lambda:1800000140).run("trace",data)
        self.assertEqual(report["reason"],"PREVIEW_HAS_UNRESOLVED_QUALITY_CAUTION")
        caller.assert_not_called()

    def test_synthetic_verified_fixture_has_exact_three_ordered_roles_but_still_no_order(self):
        caller=RoleCaller()
        result=signed_role_run(caller)
        self.assertEqual(caller.calls,list(ROLES))
        self.assertEqual(result["completed_roles"],list(ROLES))
        self.assertEqual([x["role"] for x in result["role_results"]],list(ROLES))
        self.assertIsNone(result["final_decision"])
        self.assertEqual(result["reason"],"STRATEGY_BUY_PREFLIGHT_UNVERIFIED")
        self.assertFalse(result["provider_connected"])
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(len({x["evidence_digest_sha256"] for x in caller.inputs}),1)
        self.assertEqual(len({x["source"]["closed_1m_epoch"] for x in caller.inputs}),1)
        self.assertTrue(all(x["role_order"]==list(ROLES) for x in caller.inputs))

    def test_risk_no_trade_still_collects_strategy_advice_without_order(self):
        for decision in ("NO_TRADE","EXIT_REVIEW"):
            with self.subTest(decision=decision):
                caller=RoleCaller(risk=decision)
                out=signed_role_run(caller)
                self.assertEqual(caller.calls,["MARKET","RISK","STRATEGY"])
                self.assertEqual(out["completed_roles"],list(ROLES))
                self.assertEqual(out["reason"],"RISK_VETO_"+decision)
                self.assertEqual(out["role_results"][1]["decision"], decision)
                self.assertEqual(out["role_results"][2]["action"],"BUY")
                self.assertEqual(out["role_results"][2]["effective_action"],"WAIT")
                self.assertIsNone(out["final_decision"])
                self.assertFalse(out["risk_veto_bypass_allowed"])
                self.assertFalse(out["g2_candidate_authorized"])
                self.assertFalse(out["execution_allowed"])

    def test_role_hash_mismatch_rejected_not_retried_or_promoted(self):
        caller=RoleCaller(corrupt_hash=True)
        out=signed_role_run(caller)
        self.assertEqual(out["reason"],"M04_ROLE_INVALID_OR_UNAVAILABLE")
        self.assertEqual(caller.calls,["MARKET"])
        self.assertEqual(out["completed_roles"],[])
        self.assertFalse(out["g2_candidate_authorized"])

    def test_invalid_risk_decision_rejected_before_strategy(self):
        caller=RoleCaller(risk="OVERRIDE")
        # Test the RISK response contract using the TEST-ONLY signed
        # issuer fixture. Unsigned trust flags must never reach any role.
        out=signed_role_run(caller)
        self.assertEqual(out["reason"],"M04_ROLE_INVALID_OR_UNAVAILABLE")
        self.assertEqual(caller.calls,["MARKET","RISK"])
        self.assertEqual(out["completed_roles"],["MARKET"])

    def test_negative_strategy_net_pnl_rejected(self):
        def provider(role,inp):
            raw=RoleCaller()(role,inp)
            if role=="STRATEGY":
                raw["projected_net_profit_idr"]="-1"
            return raw
        out=signed_role_run(provider)
        self.assertEqual(out["reason"],"M04_ROLE_INVALID_OR_UNAVAILABLE")
        self.assertEqual(out["completed_roles"],["MARKET","RISK"])

    def test_stale_or_missing_root_source_trust_never_invokes_models(self):
        caller=Mock()
        for data, now, reason in [
            (source(trusted=True),1800000200,"EXPIRED_G1_SOURCE"),
            (source(trusted=False),1800000140,"G1_PUBLIC_EVIDENCE_UNTRUSTED"),
        ]:
            with self.subTest(reason=reason):
                report=ThreeRoleShadow(caller=caller,clock=lambda:now).run(
                    "trace",data)
                self.assertEqual(report["reason"],reason)
        caller.assert_not_called()

    def test_cli_mainpath_public_market_always_blocks_real_role_provider(self):
        # Integration reuses CLI -> orchestrator -> bulk -> shadow, no fourth AI.
        from unittest.mock import patch
        from pathlib import Path
        with TemporaryDirectory() as td:
            output=StringIO()
            with patch("indodax_ai_bot.orchestrator.DailyPairScanner") as scan:
                scan.return_value.scan.return_value = fake_screen()
                with patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket",
                           return_value=FakeBulkMarket()):
                    with redirect_stdout(output):
                        code=main(["--bulk-deep-once","--db",str(Path(td)/"safe.sqlite3")])
        data=json.loads(output.getvalue())
        self.assertEqual(code,0)
        self.assertIsNone(data["final_decision"])
        self.assertFalse(data["execution_allowed"])
        self.assertEqual(data["ai_shadow"]["status"],"REJECT")
        self.assertFalse(data["ai_shadow"]["provider_connected"])
        self.assertFalse(data["ai_shadow"]["live_trading_authorized"])
        self.assertFalse(data["ai_shadow"]["g2_candidate_authorized"])


if __name__ == "__main__":
    unittest.main()
