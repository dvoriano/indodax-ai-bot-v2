"""M05 offline owner Final: persistent mode, TTL, digest, replay, concurrency.

ALL mock/synthetic. No Telegram connector, exchange account, provider or order.
"""
from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.contracts import (
    FinalDecision, RiskDecision, RiskVeto, SourceReceipt, TradeProposal,
)
from indodax_ai_bot.final_control import FinalOwnerControl
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator

NOW = 2000000000
HASH = "a" * 64
BAD_HASH = "b" * 64


def fake_proposal(**overrides):
    receipt = SourceReceipt(
        pair="btc_idr", closed_1m_epoch=NOW-60,
        observed_epoch=NOW-10, provenance="OFFLINE_FIXTURE_ONLY",
        verified=True,
    )
    base = TradeProposal(
        proposal_id="offline-preview-001",
        source=receipt,
        entry_idr=Decimal("100000000"),
        quantity=Decimal("0.00001"),
        estimated_net_profit_idr=Decimal("10000"),
        estimated_net_roi_percent=Decimal("2"),
        expires_epoch=NOW+45,
    )
    return replace(base, **overrides)


class M05OwnerContractOfflineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "main.sqlite3"
        self.journal = DecisionJournal(self.db)
        self.clock = [NOW]
        self.control = FinalOwnerControl(
            self.journal, owner_id="owner-fixture",
            clock=lambda: self.clock[0],
        )

    def _offer(self, proposal=None, **changes):
        args = {
            "source_digest_sha256": HASH,
            "risk": RiskVeto(RiskDecision.ALLOW, "OFFLINE_ONLY"),
            "roles_validated": True,
            "independently_verified_source": True,
            "spendable_and_preflight_verified": True,
            "fixture_only": True,
        }
        args.update(changes)
        return self.control.offer_fixture_preview(
            proposal or fake_proposal(), **args
        )

    def test_manual_default_and_forged_m04_output_still_hard_reject(self):
        self.assertEqual(self.control.get_mode(), "MANUAL")
        for mode in ("MANUAL", "AUTO"):
            data = FinalOwnerControl.mainpath_admission(
                {"status": "PASS", "g1_verified": True,
                 "source_trust_verified": True,
                 "execution_allowed": True}, mode=mode,
            )
            self.assertEqual(data["final_decision"], "REJECT")
            self.assertFalse(data["execution_allowed"])
            self.assertFalse(data["telegram_proposal_issued"])
            self.assertFalse(data["auto_execution_allowed"])

    def test_only_injected_whitelisted_owner_can_persist_auto_mode(self):
        self.assertEqual(
            self.control.change_mode(actor_id="someone-else", mode="AUTO")["reason"],
            "UNAUTHORIZED_OWNER_CONTROL",
        )
        self.assertEqual(self.control.get_mode(), "MANUAL")
        self.assertEqual(
            self.control.change_mode(actor_id="owner-fixture", mode="STOP")["reason"],
            "INVALID_OWNER_FINAL_MODE",
        )
        r = self.control.change_mode(actor_id="owner-fixture", mode="AUTO")
        self.assertEqual(r["configured_mode"], "AUTO")
        self.assertEqual(r["final_decision"], "REJECT")
        after_restart = FinalOwnerControl(
            DecisionJournal(self.db), owner_id="owner-fixture",
            clock=lambda: NOW,
        )
        self.assertEqual(after_restart.get_mode(), "AUTO")
        after_restart.change_mode(actor_id="owner-fixture", mode="MANUAL")
        self.assertEqual(self.control.get_mode(), "MANUAL")

    def test_real_proposal_issuance_hard_disabled_even_for_forged_eligible_flags(self):
        result = self._offer(fixture_only=False)
        self.assertEqual(result["reason"], "M05_REAL_PROPOSAL_ISSUANCE_DISABLED")
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(
            self.control.vote(
                actor_id="owner-fixture", proposal_id="offline-preview-001",
                source_digest_sha256=HASH, decision="PASS",
            )["reason"],
            "M05_PROPOSAL_NOT_FOUND",
        )

    def test_veto_invalid_issuer_stale_profit_and_ttl_all_reject(self):
        cases = [
            (fake_proposal(), {"risk": RiskVeto(RiskDecision.NO_TRADE, "veto")}),
            (fake_proposal(), {"independently_verified_source": False}),
            (fake_proposal(estimated_net_profit_idr=Decimal("9999")), {}),
            (fake_proposal(estimated_net_roi_percent=Decimal("1.99")), {}),
            (fake_proposal(expires_epoch=NOW+91), {}),
            (fake_proposal(source=replace(fake_proposal().source,
                                           observed_epoch=NOW-61)), {}),
            (fake_proposal(), {"source_digest_sha256": "not-sha256"}),
        ]
        for proposal, changes in cases:
            with self.subTest(changes=changes):
                r = self._offer(proposal, **changes)
                self.assertEqual(r["final_decision"], "REJECT")
                self.assertFalse(r.get("owner_vote_pending", False))

    def test_owner_vote_single_use_digest_bound_persisted_and_never_executable(self):
        r = self._offer()
        self.assertTrue(r["owner_vote_pending"])
        self.assertEqual(r["expires_epoch"], NOW+45)
        self.assertEqual(
            self.control.vote(
                actor_id="someone-else", proposal_id="offline-preview-001",
                source_digest_sha256=HASH, decision="PASS",
            )["reason"], "UNAUTHORIZED_OWNER_CONTROL",
        )
        self.assertEqual(
            self.control.vote(
                actor_id="owner-fixture", proposal_id="offline-preview-001",
                source_digest_sha256=BAD_HASH, decision="PASS",
            )["reason"], "M05_CALLBACK_BINDING_MISMATCH",
        )
        result = self.control.vote(
            actor_id="owner-fixture", proposal_id="offline-preview-001",
            source_digest_sha256=HASH, decision="PASS",
        )
        self.assertEqual(result["owner_vote_observed"], "PASS")
        self.assertEqual(result["reason"], "M05_OWNER_VOTE_RECORDED")
        self.assertEqual(result["final_decision"], "REJECT")
        self.assertFalse(result["trade_authorized"])
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(
            self.control.vote(
                actor_id="owner-fixture", proposal_id="offline-preview-001",
                source_digest_sha256=HASH, decision="REJECT",
            )["reason"], "M05_OWNER_VOTE_REPLAY_REJECTED",
        )
        self.assertEqual(self._offer()["reason"], "M05_PROPOSAL_ID_CONFLICT_OR_INVALID")
        self.assertEqual(self.journal.count(), 0)

    def test_expiry_is_consumed_permanently_and_auto_mode_does_not_change_gates(self):
        self.control.change_mode(actor_id="owner-fixture", mode="AUTO")
        self.assertTrue(self._offer()["owner_vote_pending"])
        self.clock[0] = NOW+45
        self.assertEqual(
            self.control.vote(
                actor_id="owner-fixture", proposal_id="offline-preview-001",
                source_digest_sha256=HASH, decision="PASS",
            )["reason"], "M05_PROPOSAL_EXPIRED",
        )
        self.clock[0] = NOW+1
        self.assertEqual(
            self.control.vote(
                actor_id="owner-fixture", proposal_id="offline-preview-001",
                source_digest_sha256=HASH, decision="PASS",
            )["reason"], "M05_OWNER_VOTE_REPLAY_REJECTED",
        )

    def test_competing_votes_only_one_consumes_atomic_sqlite_row(self):
        self.assertTrue(self._offer()["owner_vote_pending"])
        def vote(decision):
            another = FinalOwnerControl(
                DecisionJournal(self.db), owner_id="owner-fixture",
                clock=lambda: NOW+1,
            )
            return another.vote(
                actor_id="owner-fixture", proposal_id="offline-preview-001",
                source_digest_sha256=HASH, decision=decision,
            )["reason"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(vote, ("PASS", "REJECT")))
        self.assertEqual(results.count("M05_OWNER_VOTE_RECORDED"), 1)
        self.assertEqual(results.count("M05_OWNER_VOTE_REPLAY_REJECTED"), 1)

    def test_real_bulk_orchestrator_remains_final_reject_on_synthetic_ai_pass(self):
        app = DailyScalpingOrchestrator(
            Settings.from_env({}), self.journal,
        )
        forged_ai = {
            "status": "PASS",
            "final_decision": "PASS",
            "execution_allowed": True,
            "completed_roles": ["MARKET", "RISK", "STRATEGY"],
        }
        with patch.object(app, "_scan_with_early_momentum", return_value=object()), \\
             patch("indodax_ai_bot.orchestrator.bulk_public_deep_scan",
                   return_value={"eligible_cohort": {}}), \\
             patch("indodax_ai_bot.orchestrator.ThreeRoleShadow.run",
                   return_value=forged_ai):
            result = app.run_bulk_deep_cycle("m05-shadow", market=object())
        self.assertEqual(result.final_decision, FinalDecision.REJECT)
        self.assertEqual(result.ai_shadow, forged_ai)
        self.assertEqual(result.final_owner["final_decision"], "REJECT")
        self.assertFalse(result.final_owner["owner_approval_available"])
        self.assertFalse(result.execution_allowed)
        self.assertEqual(self.journal.count(), 1)


if __name__ == "__main__":
    unittest.main()
