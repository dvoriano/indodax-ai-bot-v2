"""M04 MARKET facts must be copied from one immutable source, not hallucinated.

All issuer receipts and model-shaped callers below are synthetic/offline.
This does not qualify upstream G1 or permit paid network calls or orders.
"""
from __future__ import annotations

from copy import deepcopy
from unittest.mock import Mock
import unittest

from indodax_ai_bot.three_role_ai import ThreeRoleShadow, _market_fact_catalog
from test_m04a_three_role_shadow import (
    RoleCaller, signed_role_run, signed_source_fixture, source,
)


class M04MarketGroundedEvidenceTests(unittest.TestCase):
    def test_market_echoes_actual_public_partial_and_missing_facts(self):
        caller = RoleCaller()
        report = signed_role_run(caller)
        self.assertEqual(caller.calls, ["MARKET", "RISK", "STRATEGY"])
        catalog = caller.inputs[0]["market_fact_catalog"]
        self.assertEqual(catalog["btc_regime"]["status"], "PUBLIC_DIAGNOSTIC")
        self.assertEqual(catalog["buy_notional_sample_idr"]["status"],
                         "PARTIAL_PUBLIC_SAMPLE")
        self.assertEqual(catalog["sell_notional_sample_idr"]["status"],
                         "PARTIAL_PUBLIC_SAMPLE")
        self.assertEqual(catalog["listing_status"], {
            "status": "UNAVAILABLE", "source_ref": None, "value": None,
        })
        self.assertEqual(catalog["volume_ratio"]["source_ref"],
                         "source.market_evidence.volume_ratio")
        self.assertEqual(catalog["anomalies"]["status"], "PUBLIC_DIAGNOSTIC")
        self.assertEqual(report["role_results"][0]["evidence_status"], catalog)
        self.assertEqual(
            caller.inputs[1]["prior_role_results"][0]["evidence_status"],
            catalog,
        )
        self.assertEqual(
            caller.inputs[2]["prior_role_results"][0]["evidence_status"],
            catalog,
        )
        self.assertEqual(report["reason"], "STRATEGY_BUY_PREFLIGHT_UNVERIFIED")
        self.assertFalse(report["execution_allowed"])
        self.assertFalse(report["provider_connected"])

    def test_forged_market_price_status_reference_and_extra_fields_rejected(self):
        def injected(role, inp, attack):
            value = RoleCaller()(role, inp)
            if role == "MARKET":
                attack(value)
            return value

        attacks = {
            "invented_value": lambda r: r["evidence_status"]["spread_bps"].update(
                value="999999"),
            "upgraded_sample": lambda r: r["evidence_status"][
                "buy_notional_sample_idr"].update(status="PUBLIC_DIAGNOSTIC"),
            "fake_listing": lambda r: r["evidence_status"]["listing_status"].update(
                status="PUBLIC_DIAGNOSTIC",
                source_ref="source.market_evidence.listing_status",
                value="TRADABLE"),
            "missing_evidence": lambda r: r.pop("evidence_status"),
            "missing_category": lambda r: r["evidence_status"].pop("anomalies"),
            "invented_extra": lambda r: r.update(entry_price_idr="12345"),
        }
        for name, attack in attacks.items():
            calls = []
            def caller(role, inp):
                calls.append(role)
                return injected(role, inp, attack)
            with self.subTest(name=name):
                report = signed_role_run(caller)
                self.assertEqual(calls, ["MARKET"])
                self.assertEqual(report["reason"], "M04_ROLE_INVALID_OR_UNAVAILABLE")
                self.assertEqual(report["completed_roles"], [])
                self.assertFalse(report["execution_allowed"])

    def test_model_cannot_change_risk_catalog_or_prior_role_results(self):
        caller = RoleCaller()
        inputs = []

        def mutated(role, inp):
            inputs.append(deepcopy(inp))
            response = caller(role, inp)
            if role == "MARKET":
                inp["market_fact_catalog"]["spread_bps"]["value"] = "FORGED"
                inp["source"]["market_evidence"]["spread_bps"] = "FORGED"
                inp["source"]["market_evidence"]["anomaly_flags"].append("FAKE")
            return response

        report = signed_role_run(mutated)
        self.assertEqual(caller.calls, ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual(
            [i["market_fact_catalog"] for i in inputs],
            [inputs[0]["market_fact_catalog"]] * 3,
        )
        self.assertEqual(inputs[1]["prior_role_results"][0]["evidence_status"],
                         inputs[0]["market_fact_catalog"])
        self.assertEqual(report["reason"], "STRATEGY_BUY_PREFLIGHT_UNVERIFIED")

    def test_caller_cannot_forge_both_input_catalog_and_its_reply(self):
        """A forged echoed catalog must not redefine the source of truth."""
        calls = []

        def attacker(role, inp):
            calls.append(role)
            response = RoleCaller()(role, inp)
            if role == "MARKET":
                forged = inp["market_fact_catalog"]["spread_bps"]
                forged["value"] = "9000000"
                response["evidence_status"]["spread_bps"]["value"] = "9000000"
                inp["source"]["market_evidence"]["spread_bps"] = "9000000"
                listing = inp["market_fact_catalog"]["listing_status"]
                listing.update({
                    "status": "PUBLIC_DIAGNOSTIC",
                    "source_ref": "source.forged_listings",
                    "value": "TRADABLE",
                })
                response["evidence_status"]["listing_status"].update(listing)
            return response

        result = signed_role_run(attacker)
        self.assertEqual(calls, ["MARKET"])
        self.assertEqual(result["reason"], "M04_ROLE_INVALID_OR_UNAVAILABLE")
        self.assertEqual(result["completed_roles"], [])
        self.assertFalse(result["execution_allowed"])

    def test_nonfinite_and_unavailable_facts_not_reported_as_observed(self):
        caller = RoleCaller()
        signed_role_run(caller)
        orig = deepcopy(caller.inputs[0]["source"])
        orig["market_evidence"]["volume_ratio"] = "NaN"
        orig["market_evidence"]["spread_bps"] = "Infinity"
        orig["market_evidence"]["book_imbalance"] = None
        catalog = _market_fact_catalog(orig)
        for name in ("volume_ratio", "spread_bps", "book_imbalance"):
            self.assertEqual(catalog[name],
                             {"status": "UNAVAILABLE", "source_ref": None,
                              "value": None})

    def test_risk_no_trade_veto_keeps_strategy_advisory_and_blocks_order(self):
        caller = RoleCaller(risk="NO_TRADE")
        report = signed_role_run(caller)
        self.assertEqual(caller.calls, ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual(report["completed_roles"], ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual(report["reason"], "RISK_VETO_NO_TRADE")
        self.assertEqual(report["role_results"][-1]["effective_action"], "WAIT")
        self.assertFalse(report["execution_allowed"])

    def test_public_unsigned_source_does_not_even_call_market(self):
        caller = Mock()
        report = ThreeRoleShadow(caller=caller, clock=lambda: 1800000140).run(
            "trace", source()
        )
        self.assertEqual(report["reason"], "G1_PUBLIC_EVIDENCE_UNTRUSTED")
        self.assertEqual(report["completed_roles"], [])
        self.assertFalse(report["g1_verified"])
        caller.assert_not_called()


if __name__ == "__main__":
    unittest.main()
