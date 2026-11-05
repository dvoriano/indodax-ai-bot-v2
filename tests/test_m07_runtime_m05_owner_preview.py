"""Original M07 real-source runtime opt-in queues SAME M05 private owner preview.

All HTTP in these tests is mocked at existing original source boundaries.
View Only UID/key fingerprint is pseudonymous, not bot owned. Zero order.
"""
from __future__ import annotations
from dataclasses import replace
from contextlib import redirect_stdout
from decimal import Decimal as D
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.config import Settings
from indodax_ai_bot.deep_analysis import DeepCandidate
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator, CycleResult
from indodax_ai_bot.owner_control import OwnerDecisionLedger
from tests.test_m03_public_quote_friction_mainpath import public_observation
from tests.test_m07_main_idr_sourcebound_sizing_original import (
    NOW, RULES, account,
)

OWNER = 174401


class M07RuntimeOwnerCardTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix="m07-runtime-m05-owner-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.owner_dir = root / "owner"
        self.owner_dir.mkdir(mode=0o700)
        self.owner_dir.chmod(0o700)
        self.owner_db = self.owner_dir / "owner.sqlite3"
        self.owner = OwnerDecisionLedger(
            self.owner_db, owner_user_id=OWNER,
        )
        self.owner_db.chmod(0o600)
        self.journal = DecisionJournal(root / "same-original-journal.sqlite3")
        self.app = DailyScalpingOrchestrator(
            Settings(), self.journal, owner_control=self.owner,
        )
        observed = replace(public_observation(), pair_rule_fields=dict(RULES))
        with patch.object(
            self.journal, "record_and_compare_public_wall",
            return_value={"status": "UNATTESTED"},
        ):
            candidate = self.app._attach_public_wall_experience(
                DeepCandidate("btc_idr", 75, "PUBLIC_REVIEW", None), observed,
            )
        self.row = candidate.summary()
        self.bulk = CycleResult("once", None, "PUBLIC_UNVERIFIED", bulk={
            "mode": "M03G_BULK_PUBLIC_SHARED_SOURCE_ONLY",
            "shared_public_scan_context": True,
            "coverage_status": "ALL_SELECTED_ANALYZED_UNATTESTED",
            "eligible_cohort": {
                "status": "ALIGNED_PUBLIC_UNATTESTED",
                "eligible_pair_ids": ["btc_idr"],
                "provisional_watchlist_preview": ["btc_idr"],
                "provisional_quality_reviews": [
                    {"pair": "btc_idr", "clear_of_provisional_cautions": True}
                ],
            },
            "provisional_market_evidence": [self.row],
            "ranked_diagnostics_top10": [self.row],
            "execution_allowed": False,
        })
        self.funding = dict(
            requested_ticket_ceiling_idr=D("100000"),
            new_floki_sell_rebuy_hold_idr=D("0"),
            new_pepe_sell_rebuy_hold_idr=D("0"),
            commitments_not_locked_idr=D("0"),
            max_spread_bps=D("150"),
        )
        self.snapshot = dict(
            account(), viewonly_key_uid_fingerprint="a" * 64,
        )
        self.ws = root / "btc-ws.sqlite3"
        self.ws.write_bytes(b"synthetic-placeholder")
        self.funding_file = root / "funding.json"
        self.funding_file.write_text(json.dumps({
            k: str(v) for k, v in self.funding.items(),
        }))
        self.funding_file.chmod(0o600)

    def run_once(self, *, bulk=None, acct=None, owner_user_id=OWNER):
        with patch(
            "indodax_ai_bot.orchestrator.time.time", return_value=NOW,
        ), patch.object(
            self.app, "run_bulk_deep_cycle",
            return_value=self.bulk if bulk is None else bulk,
        ) as scanner, patch.object(
            self.app, "inspect_m06_idr_budget_readonly_once",
            return_value=self.snapshot if acct is None else acct,
        ) as viewonly:
            out = self.app.run_m07_original_source_join_readonly_once(
                "real_source_once", api_key="viewonly",
                secret_key="viewonly", funding=self.funding,
                owner_preview_user_id=owner_user_id,
            )
        return out, scanner, viewonly

    def test_original_runtime_scan_to_private_card_for_clean_candidate(self):
        out, scanner, viewonly = self.run_once()
        scanner.assert_called_once()
        viewonly.assert_called_once()
        self.assertEqual(out["m06_authenticated_viewonly_get_count"], 2)
        review = out["m07_owner_preview"]
        self.assertEqual(
            review["status"], "M07_MAIN_REFERENCE_BUY_QUEUED_M05_PREVIEW_NO_ORDER",
        )
        self.assertTrue(review["owner_card_queued"])
        self.assertFalse(review["execution_allowed"])
        self.assertFalse(out["m03_g1_independently_verified"])
        self.assertFalse(out["m04_risk_three_role_executable_verified"])
        self.assertFalse(out["execution_allowed"])
        card = self.owner.claim_manual_trade_preview_delivery(now_epoch=NOW)
        self.assertIsNotNone(card)
        self.assertIn("Nominal pembelian (estimasi): Rp", card["message_text"])
        self.assertIn("Token diperoleh (estimasi)", card["message_text"])
        self.assertIn("SIMULASI TOMBOL, NO ORDER", card["message_text"])
        self.assertEqual(
            self.journal.m07_offline_intent_summary()["manual_sender_nonlive_holds"], 0,
        )

    def test_caution_only_ranked_candidate_does_not_emit_order_card(self):
        modified = dict(self.bulk.bulk)
        cohort = dict(modified["eligible_cohort"])
        cohort["provisional_watchlist_preview"] = []
        cohort["provisional_quality_reviews"] = [
            {"pair": "btc_idr", "clear_of_provisional_cautions": False}
        ]
        modified.update(
            eligible_cohort=cohort, provisional_market_evidence=[],
        )
        out, _, viewonly = self.run_once(
            bulk=CycleResult("once", None, "PUBLIC_UNVERIFIED", bulk=modified),
        )
        viewonly.assert_called_once()
        self.assertFalse(out["m07_owner_preview"]["owner_card_queued"])
        self.assertEqual(
            out["m07_owner_preview"]["status"],
            "M07_RUNTIME_M05_CAUTION_ONLY_NO_PREVIEW",
        )
        self.assertIsNone(
            self.owner.claim_manual_trade_preview_delivery(now_epoch=NOW),
        )

    def test_unknown_uid_and_key_scope_never_enqueues(self):
        acct = dict(self.snapshot)
        acct.pop("viewonly_key_uid_fingerprint")
        out, _, _ = self.run_once(acct=acct)
        self.assertEqual(
            out["m07_owner_preview"]["status"],
            "M07_RUNTIME_M05_UID_BINDING_NOT_VERIFIED",
        )
        self.assertFalse(out["m07_owner_preview"]["owner_card_queued"])

    def test_wrong_owner_and_auto_mode_block_before_any_private_get(self):
        with patch.object(self.app, "run_bulk_deep_cycle") as scan:
            with self.assertRaisesRegex(
                ValueError, "M07_RUNTIME_M05_OWNER_IDENTITY_REQUIRED",
            ):
                self.app.run_m07_original_source_join_readonly_once(
                    "wrong", api_key="a", secret_key="b",
                    funding=self.funding, owner_preview_user_id=OWNER + 1,
                )
            scan.assert_not_called()
        self.owner.switch_mode(
            user_id=OWNER, chat_id=OWNER, mode="AUTO", now_epoch=NOW,
        )
        with patch.object(self.app, "run_bulk_deep_cycle") as scan:
            with self.assertRaisesRegex(
                ValueError, "M07_RUNTIME_M05_MANUAL_MODE_REQUIRED",
            ):
                self.app.run_m07_original_source_join_readonly_once(
                    "auto", api_key="a", secret_key="b",
                    funding=self.funding, owner_preview_user_id=OWNER,
                )
            scan.assert_not_called()

    def test_original_cli_owner_optin_uses_existing_0600_ledger_and_no_order(self):
        out = StringIO()
        root = Path(self.ws.parent)
        args = [
            "--m07-main-readonly-cycle-once",
            "--m07-owner-preview-m05",
            "--m07-runtime-funding-file", str(self.funding_file),
            "--m05-shadow-db", str(self.owner_db),
            "--m05-shadow-owner-id", str(OWNER),
            "--ws-db", str(self.ws),
            "--db", str(root / "cli-journal.sqlite3"),
        ]
        with patch(
            "indodax_ai_bot.__main__._existing_v2_credentials",
            return_value=self.funding_file,
        ), patch(
            "indodax_ai_bot.__main__.read_view_only_credentials",
            return_value=("owner_key", "owner_secret"),
        ), patch(
            "indodax_ai_bot.orchestrator.time.time", return_value=NOW,
        ), patch.object(
            DailyScalpingOrchestrator, "run_bulk_deep_cycle",
            return_value=self.bulk,
        ), patch.object(
            DailyScalpingOrchestrator, "inspect_m06_idr_budget_readonly_once",
            return_value=self.snapshot,
        ) as viewonly, redirect_stdout(out):
            rc = main(args)
        self.assertEqual(rc, 0, out.getvalue())
        viewonly.assert_called_once()
        doc = json.loads(out.getvalue())["m07_original_live_source_join"]
        self.assertTrue(doc["m07_owner_preview"]["owner_card_queued"])
        self.assertFalse(doc["execution_allowed"])
        card = self.owner.claim_manual_trade_preview_delivery(now_epoch=NOW)
        self.assertIn("NO ORDER", card["message_text"])

    def test_cli_owner_flag_without_existing_owner_ledger_denied_before_source(self):
        out = StringIO()
        with patch.object(
            DailyScalpingOrchestrator, "run_bulk_deep_cycle",
            side_effect=AssertionError("MUST_NOT_SCAN"),
        ), redirect_stdout(out):
            rc = main([
                "--m07-main-readonly-cycle-once",
                "--m07-owner-preview-m05",
                "--m07-runtime-funding-file", str(self.funding_file),
                "--ws-db", str(self.ws),
                "--db", str(self.ws.parent / "fail.sqlite3"),
            ])
        self.assertNotEqual(rc, 0)
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
