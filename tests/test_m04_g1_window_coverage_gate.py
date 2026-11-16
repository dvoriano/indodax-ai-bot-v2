"""Original M04 G1 provenance window gate, OFFLINE synthetic receipts ONLY.

No Indodax HTTP/WS, actual provider calls, private credentials or orders.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import hmac
import unittest
from unittest.mock import Mock

from indodax_ai_bot.g1_receipt import (
    canonical_receipt_bytes, validate_g1_coverage_manifest,
)
from indodax_ai_bot.three_role_ai import ThreeRoleShadow
from test_m04a_three_role_shadow import (
    TEST_G1_SECRET, RoleCaller, signed_source_fixture,
)
from test_m04t_two_candidate_shadow import (
    signed_two_pair_source, PAIRS, CLOCK,
)


def resign_test_receipt(bulk: dict, *, pair: str = "btc_idr") -> None:
    """TEST ONLY: re-sign changed synthetic data to exercise the shape gate."""
    isolated = deepcopy(bulk)
    isolated["eligible_cohort"]["provisional_watchlist_preview"] = [pair]
    isolated["g1_attestation"] = (
        bulk["g1_attestations"][pair]
        if "g1_attestations" in bulk else bulk["g1_attestation"]
    )
    before = ThreeRoleShadow(clock=lambda: CLOCK).run("trace", isolated)
    assert before["reason"] == "G1_ISSUER_RECEIPT_UNVERIFIED", before["reason"]
    claims = deepcopy(isolated["g1_attestation"]["claims"])
    claims["source_digest_sha256"] = before["source_digest_sha256"]
    receipt = {
        "claims": claims,
        "mac_sha256": hmac.new(
            TEST_G1_SECRET, canonical_receipt_bytes(claims), sha256,
        ).hexdigest(),
    }
    if "g1_attestations" in bulk:
        bulk["g1_attestations"][pair] = receipt
    else:
        bulk["g1_attestation"] = receipt


class M04G1WindowCoverageGateTests(unittest.TestCase):
    def test_signed_synthetic_closed_candle_and_15m_1h_tape_pass_shape(self):
        bulk, verifier = signed_source_fixture()
        m = bulk["g1_coverage_manifests_by_pair"]["btc_idr"]
        self.assertTrue(validate_g1_coverage_manifest(
            m, issuer_id="synthetic-unit-issuer", pair="btc_idr",
            closed_epoch=bulk["eligible_cohort"]["closed_1m_epoch"],
        ))
        caller = RoleCaller(risk="NO_TRADE", strategy="BUY")
        out = ThreeRoleShadow(
            caller=caller, clock=lambda: CLOCK, source_verifier=verifier,
        ).run("trace", bulk)
        self.assertEqual(caller.calls, ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual(out["reason"], "RISK_VETO_NO_TRADE")
        self.assertTrue(all(
            inp["source"]["g1_coverage_manifest"] == m
            for inp in caller.inputs
        ))
        self.assertEqual(len({
            inp["evidence_digest_sha256"] for inp in caller.inputs
        }), 1)
        self.assertFalse(out["execution_allowed"])

    def test_valid_mac_without_coverage_manifest_cannot_call_any_model(self):
        bulk, verifier = signed_source_fixture()
        bulk["g1_coverage_manifests_by_pair"].pop("btc_idr")
        resign_test_receipt(bulk)
        caller = Mock()
        result = ThreeRoleShadow(
            caller=caller, clock=lambda: CLOCK, source_verifier=verifier,
        ).run("trace", bulk)
        self.assertEqual(result["reason"], "G1_WINDOW_COVERAGE_UNKNOWN")
        caller.assert_not_called()

    def test_signed_malformed_upstream_manifest_fail_closed(self):
        for change in (
            lambda m: m["tape_windows"]["1h"].update(unrecovered_gap_count=1),
            lambda m: m["tape_windows"]["15m"].update(first_offset=160),
            lambda m: m["tape_windows"]["15m"].update(publication_count=39),
            lambda m: m["tape_windows"]["1h"].update(replay_reconciled=False),
            lambda m: m["tape_windows"]["1h"].update(start_epoch=m["closed_1m_epoch"]-300),
            lambda m: m["closed_candles"]["5m"].update(end_epoch=m["closed_1m_epoch"]),
            lambda m: m.update(pair="eth_idr"),
            lambda m: m.update(issuer_id="wrong-issuer"),
            lambda m: m.update(upstream_origin_receipt_sha256="0"*64),
            lambda m: m["tape_windows"]["1h"].update(gap_count=True),
        ):
            with self.subTest(change=str(change)):
                bulk, verifier = signed_source_fixture()
                change(bulk["g1_coverage_manifests_by_pair"]["btc_idr"])
                resign_test_receipt(bulk)
                caller = Mock()
                out = ThreeRoleShadow(
                    caller=caller, clock=lambda: CLOCK,
                    source_verifier=verifier,
                ).run("trace", bulk)
                self.assertEqual(out["reason"], "G1_WINDOW_COVERAGE_INVALID")
                caller.assert_not_called()
                self.assertFalse(out["execution_allowed"])

    def test_two_candidates_bad_second_signed_coverage_blocks_only_second(self):
        bulk, verifier = signed_two_pair_source()
        bulk["g1_coverage_manifests_by_pair"][PAIRS[1]]["tape_windows"]["1h"][
            "unrecovered_gap_count"
        ] = 1
        resign_test_receipt(bulk, pair=PAIRS[1])
        caller = RoleCaller()
        out = ThreeRoleShadow(
            caller=caller, clock=lambda: CLOCK, source_verifier=verifier,
        ).run("trace", bulk)
        reports = {r["pair"]: r for r in out["candidate_reports"]}
        self.assertEqual(reports[PAIRS[1]]["reason"], "G1_WINDOW_COVERAGE_INVALID")
        self.assertEqual(reports[PAIRS[1]]["completed_roles"], [])
        self.assertEqual(caller.calls, ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual({e["source"]["pair"] for e in caller.inputs}, {PAIRS[0]})
        self.assertFalse(out["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
