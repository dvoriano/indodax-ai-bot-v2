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
from indodax_ai_bot.three_role_ai import ThreeRoleShadow, _candidate_decision_loop
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


def signed_two_pair_source(*, quote_claims=None):
    bulk = two_pair_source()
    if quote_claims is not None:
        bulk["strategy_quote_claims_by_pair"] = deepcopy(quote_claims)
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

    def test_invalid_individual_receipt_does_not_poison_valid_peer(self):
        for bad in PAIRS:
            with self.subTest(bad=bad):
                bulk, verifier = signed_two_pair_source()
                bulk["g1_attestations"][bad]["mac_sha256"] = "0" * 64
                caller = RoleCaller()
                result = ThreeRoleShadow(
                    caller=caller, clock=lambda: CLOCK, source_verifier=verifier,
                ).run("trace", bulk)
                good = next(p for p in PAIRS if p != bad)
                self.assertEqual(caller.calls, ["MARKET", "RISK", "STRATEGY"])
                self.assertEqual({e["source"]["pair"] for e in caller.inputs}, {good})
                reports = {r["pair"]: r for r in result["candidate_reports"]}
                self.assertEqual(reports[bad]["reason"], "G1_ISSUER_RECEIPT_UNVERIFIED")
                self.assertEqual(reports[bad]["completed_roles"], [])
                self.assertEqual(set(result["source_digests_by_pair"]), {good})
                self.assertFalse(result["execution_allowed"])

    def test_missing_individual_receipt_does_not_poison_valid_peer(self):
        bulk, verifier = signed_two_pair_source()
        del bulk["g1_attestations"][PAIRS[1]]
        caller = RoleCaller()
        result = ThreeRoleShadow(
            caller=caller, clock=lambda: CLOCK, source_verifier=verifier,
        ).run("trace", bulk)
        self.assertEqual(caller.calls, ["MARKET", "RISK", "STRATEGY"])
        self.assertEqual(result["candidate_reports"][1]["reason"],
                         "G1_ISSUER_RECEIPT_UNVERIFIED")
        self.assertFalse(result["execution_allowed"])

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


    def test_two_buy_opinions_without_bound_quote_stay_rescan(self):
        bulk, verifier = signed_two_pair_source()
        result = ThreeRoleShadow(
            caller=RoleCaller(), clock=lambda: CLOCK, source_verifier=verifier,
        ).run("trace", bulk)
        decision = result["candidate_decision_loop"]
        self.assertEqual(decision["status"], "RESCAN_FRESH_EVIDENCE")
        self.assertEqual(decision["qualified_scenario_count"], 0)
        self.assertIsNone(decision["selected_pair"])
        self.assertFalse(decision["execution_allowed"])

    def test_one_bound_scenario_and_one_risk_veto_select_review_only(self):
        from test_m04_strategy_source_plan import test_quote
        bulk, verifier = signed_two_pair_source(
            quote_claims={p: test_quote(p) for p in PAIRS})
        def caller(role, evidence):
            risk = "NO_TRADE" if evidence["source"]["pair"] == PAIRS[1] else "ALLOW"
            return RoleCaller(risk=risk)(role, evidence)
        result = ThreeRoleShadow(
            caller=caller, clock=lambda: CLOCK, source_verifier=verifier,
        ).run("trace", bulk)
        decision = result["candidate_decision_loop"]
        self.assertEqual(decision["status"], "SELECTED_SYNTHETIC_REVIEW_ONLY")
        self.assertEqual(decision["selected_pair"], PAIRS[0])
        self.assertEqual(decision["qualified_scenario_count"], 1)
        self.assertEqual(decision["candidates"][1]["disposition"], "RISK_REJECT")
        self.assertIsNone(decision["final_decision"])
        self.assertFalse(decision["paper_fill_authorized"])
        self.assertFalse(result["execution_allowed"])

    def test_decision_matrix_comparison_and_staleness(self):
        def report(pair, net="15000", roi="2.5", spread="20", stop="98000"):
            return {
                "pair": pair, "evidence_digest_sha256": sha256(pair.encode()).hexdigest(),
                "reason": "STRATEGY_BUY_PREFLIGHT_UNVERIFIED",
                "completed_roles": ["MARKET", "RISK", "STRATEGY"],
                "role_results": [
                    {"role": "MARKET", "bias": "BULLISH", "evidence_status": {
                        "spread_bps": {"status": "PUBLIC_DIAGNOSTIC", "value": spread}}},
                    {"role": "RISK", "decision": "ALLOW",
                     "risk_source_report": {"blockers": []}},
                    {"role": "STRATEGY", "action": "BUY",
                     "projected_net_profit_idr": net, "projected_net_roi_percent": roi,
                     "strategy_source_plan": {
                         "pair": pair, "status": "SYNTHETIC_SCENARIO_CALCULATED_UNATTESTED",
                         "allocated_cost_idr": "600000", "entry_price_idr": "100000",
                         "stop_loss_price_idr": stop,
                         "estimated_net_profit_idr": "30000",
                         "estimated_net_roi_percent": "5"}},
                ],
            }
        def decide(rows, now=CLOCK):
            decision = _candidate_decision_loop(
                reports=rows, source_epoch=CLOCK, now_epoch=now)
            self.assertIsNone(decision["final_decision"])
            self.assertFalse(decision["execution_allowed"])
            self.assertFalse(decision["paper_fill_authorized"])
            return decision
        a = report(PAIRS[0])
        b = report(PAIRS[1], net="18000", roi="3", spread="10")
        self.assertEqual(decide([])["status"], "RESCAN_FRESH_EVIDENCE")
        self.assertEqual(decide([a])["selected_pair"], PAIRS[0])
        self.assertEqual(decide([a,b])["selected_pair"], PAIRS[1])
        self.assertEqual(decide([b,a])["selected_pair"], PAIRS[1])
        self.assertEqual(decide([a,report(PAIRS[1])])["status"],
                         "RESCAN_INCOMPARABLE_SCENARIOS")
        self.assertEqual(decide([a,report(PAIRS[1], net="18000", roi="3",
                                         spread="40")])["status"],
                         "RESCAN_INCOMPARABLE_SCENARIOS")
        self.assertEqual(decide([a,b], CLOCK+61)["status"], "RESCAN_STALE_EVIDENCE")
        self.assertEqual(decide([a,a])["status"], "RESCAN_INVALID_COHORT")
        veto = deepcopy(a)
        veto["reason"] = "RISK_VETO_NO_TRADE"
        self.assertEqual(decide([veto,b])["selected_pair"], PAIRS[1])
        veto2 = deepcopy(b)
        veto2["reason"] = "RISK_VETO_NO_TRADE"
        self.assertEqual(decide([veto,veto2])["qualified_scenario_count"], 0)
        unavailable = deepcopy(a)
        unavailable["reason"] = "M04_ROLE_INVALID_OR_UNAVAILABLE"
        unavailable["completed_roles"] = []
        self.assertEqual(decide([unavailable,b])["selected_pair"], PAIRS[1])


    def test_original_journal_dedupes_same_source_after_restart(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from indodax_ai_bot.journal import DecisionJournal
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "original.sqlite3"
            bulk, verifier = signed_two_pair_source()
            caller = RoleCaller(risk="NO_TRADE")
            def run(data, trace, injected):
                return ThreeRoleShadow(
                    caller=injected, clock=lambda: CLOCK, source_verifier=verifier,
                    review_claim=DecisionJournal(path).claim_m04_candidate_evidence_once,
                ).run(trace, data)
            first = run(bulk, "trace", caller)
            self.assertEqual(first["completed_role_calls"], 6)
            # A new trace and valid new signatures cannot replay the same
            # immutable facts. No provider invocation after restart.
            replay = deepcopy(bulk)
            for pair in PAIRS:
                one = deepcopy(replay)
                one["eligible_cohort"]["provisional_watchlist_preview"] = [pair]
                check = ThreeRoleShadow(clock=lambda: CLOCK).run("trace2", one)
                claims = replay["g1_attestations"][pair]["claims"]
                claims["trace_id"] = "trace2"
                claims["source_digest_sha256"] = check["source_digest_sha256"]
                replay["g1_attestations"][pair]["mac_sha256"] = hmac.new(
                    TEST_G1_SECRET, canonical_receipt_bytes(claims), sha256
                ).hexdigest()
            blocked = Mock()
            second = run(replay, "trace2", blocked)
            blocked.assert_not_called()
            self.assertEqual(second["completed_role_calls"], 0)
            self.assertEqual([r["reason"] for r in second["candidate_reports"]],
                             ["M04_REVIEW_EVIDENCE_ALREADY_CLAIMED"] * 2)
            self.assertFalse(second["execution_allowed"])

    def test_claim_failure_never_reaches_provider(self):
        bulk, verifier = signed_two_pair_source()
        caller = Mock()
        claim = Mock(side_effect=OSError("offline"))
        result = ThreeRoleShadow(
            caller=caller, clock=lambda: CLOCK, source_verifier=verifier,
            review_claim=claim,
        ).run("trace", bulk)
        self.assertEqual(claim.call_count, 2)
        caller.assert_not_called()
        self.assertEqual([r["reason"] for r in result["candidate_reports"]],
                         ["M04_REVIEW_CLAIM_UNAVAILABLE"] * 2)
        self.assertFalse(result["execution_allowed"])



    def test_historical_review_reuses_archive_and_never_invents_missing_outcome(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import sqlite3
        import json
        from decimal import Decimal
        from indodax_ai_bot.journal import DecisionJournal
        from indodax_ai_bot.calibration_replay import evaluate_m04_review_history_once
        with TemporaryDirectory() as tmp:
            journal = DecisionJournal(Path(tmp)/"original.sqlite3")
            archive=Path(tmp)/"observations.sqlite3"
            bulk, verifier=signed_two_pair_source()
            caller=RoleCaller(risk="NO_TRADE")
            result=ThreeRoleShadow(caller=caller,clock=lambda:CLOCK,
                source_verifier=verifier,
                review_claim=journal.claim_m04_candidate_evidence_once).run("trace",bulk)
            source_epoch=bulk["screening_observed_epoch"]
            instant=CLOCK
            self.assertEqual(journal.record_m04_history_reviews(ai_shadow=result,
                source_epoch=source_epoch,decision_epoch=instant),2)
            self.assertEqual(journal.record_m04_history_reviews(ai_shadow=result,
                source_epoch=source_epoch,decision_epoch=instant),0)
            self.assertEqual(evaluate_m04_review_history_once(journal,
                now_epoch=instant+3660,evidence_db=archive)["status"],
                "M04_HISTORY_SOURCE_UNAVAILABLE")
            with sqlite3.connect(archive) as db:
                db.execute("CREATE TABLE ws3_source_evidence(pair TEXT,minute_epoch INTEGER,"
                    "source_exchange_epoch INTEGER,last_idr TEXT,public_offset INTEGER,source_status TEXT)")
                db.execute("CREATE TABLE ws3_source_cycles(minute_epoch INTEGER PRIMARY KEY,"
                           "recorded_local_epoch INTEGER)")
                def sample(t,price,local=None):
                    local=t if local is None else local
                    minute=local-local%60
                    db.execute("INSERT OR IGNORE INTO ws3_source_cycles VALUES(?,?)",(minute,local))
                    db.execute("INSERT INTO ws3_source_evidence VALUES(?,?,?,?,?,?)",
                               (PAIRS[0],minute,t,price,1,"ELIGIBLE_SOURCE_OBSERVATION"))
                sample(instant-60,"1")  # pre-decision: never anchor
                sample(instant,"100")
                sample(instant+300,"103")
                sample(instant+900,"110",instant+1020)  # too late for 15m
                sample(instant+1800,"99")
                sample(instant+3600,"104")
            archive.chmod(0o600)
            before=evaluate_m04_review_history_once(journal,now_epoch=instant+359,
                                                     evidence_db=archive)
            self.assertEqual(before["outcomes_recorded"],0)
            first=evaluate_m04_review_history_once(journal,now_epoch=instant+360,
                                                   evidence_db=archive)
            self.assertEqual(first["outcomes_recorded"],2)
            self.assertEqual(first["cohorts"]["RISK_REJECT:5"],
                {"observed":1,"unknown":1,"net_proxy_50bps_ge_2pct":1})
            done=evaluate_m04_review_history_once(journal,now_epoch=instant+3660,
                                                   evidence_db=archive)
            self.assertEqual(done["outcomes_recorded"],6)
            self.assertEqual(done["cohorts"]["RISK_REJECT:15"]["unknown"],2)
            self.assertEqual(done["cohorts"]["RISK_REJECT:60"]["observed"],1)
            self.assertFalse(done["strategy_auto_updated"])
            with journal._connect() as db:
                raw=db.execute("SELECT outcome_json FROM m04_history_outcomes "
                               "WHERE pair=? AND horizon_minutes=5",(PAIRS[0],)).fetchone()[0]
                item=json.loads(raw)
                self.assertEqual(Decimal(item["gross_move_bps"]),Decimal("300"))
                self.assertEqual(Decimal(item["net_proxy_bps"]["50"]),Decimal("250"))
                self.assertFalse(item["actual_fill_or_profit"])
            replay=evaluate_m04_review_history_once(journal,now_epoch=instant+3661,
                                                     evidence_db=archive)
            self.assertEqual(replay["outcomes_recorded"],0)
            self.assertEqual(replay["cohorts"],done["cohorts"])


if __name__ == "__main__":
    unittest.main()
