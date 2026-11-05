"""Original M03/M06/M07 same-cycle sized reference → SAME M05 owner card.

No new scanner, poller, credentials, provider, private request or order.
All account and market data are synthetic and explicitly UNATTESTED.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.deep_analysis import DeepCandidate
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.owner_control import OwnerDecisionLedger
from tests.test_m03_public_quote_friction_mainpath import public_observation
from tests.test_m07_main_idr_sourcebound_sizing_original import (
    NOW, RULES, account,
)


OWNER = 174401
FINGERPRINT = "a" * 64


class M07SameSourceToM05ManualPreviewTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix="m07-m05-original-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.owner = OwnerDecisionLedger(root / "owner.sqlite3", owner_user_id=OWNER)
        self.journal = DecisionJournal(root / "original.sqlite3")
        self.bot = DailyScalpingOrchestrator(
            Settings(), self.journal, owner_control=self.owner,
        )
        obs = replace(public_observation(), pair_rule_fields=dict(RULES))
        with patch.object(
            self.journal, "record_and_compare_public_wall",
            return_value={"status": "UNATTESTED"},
        ):
            selected = self.bot._attach_public_wall_experience(
                DeepCandidate("btc_idr", 75, "PUBLIC_REVIEW", None), obs,
            )
        self.public = selected.summary()["m07_same_candidate_public_preflight"]
        self.account = account()
        self.funding = {
            "requested_ticket_ceiling_idr": D("100000"),
            "new_floki_sell_rebuy_hold_idr": D("0"),
            "new_pepe_sell_rebuy_hold_idr": D("0"),
            "commitments_not_locked_idr": D("0"),
            "max_spread_bps": D("150"),
        }

    def queue(self, *, client="m07sizedm05_001", owner=OWNER,
              now_epoch=NOW, **overrides):
        kw = {
            "user_id": owner,
            "chat_id": owner,
            "account_fingerprint": FINGERPRINT,
            "client_order_id": client,
            "public_candidate": self.public,
            "account_snapshot": self.account,
            "funding": self.funding,
            "now_epoch": now_epoch,
        }
        kw.update(overrides)
        return self.bot.queue_m07_same_source_main_buy_preview_nonlive(**kw)

    def test_real_original_sizing_to_owner_telegram_card_includes_all_in_idr(self):
        with patch(
            "indodax_ai_bot.m07_private_order_readonly._https_json",
            side_effect=AssertionError("PRIVATE_GET_MUST_NOT_RUN"),
        ) as private, patch(
            "indodax_ai_bot.sumopod_provider._post",
            side_effect=AssertionError("PAID_AI_MUST_NOT_RUN"),
        ) as paid:
            report = self.queue()
        paid.assert_not_called()
        private.assert_not_called()
        self.assertEqual(
            report["status"], "M07_MAIN_REFERENCE_BUY_QUEUED_M05_PREVIEW_NO_ORDER",
        )
        self.assertTrue(report["owner_card_queued"])
        self.assertTrue(report["owner_card_preview_only"])
        self.assertEqual(report["pair"], "btc_idr")
        self.assertFalse(report["g1_issuer_verified"])
        self.assertFalse(report["ai_risk_verified"])
        self.assertFalse(report["live_account_spendable_verified"])
        self.assertFalse(report["execution_allowed"])
        self.assertFalse(report["order_sent"])
        cost = D(report["buy_max_cost_idr_reference"])
        qty = D(report["base_quantity_reference"])
        price = D(report["buy_limit_price_idr_reference"])
        self.assertEqual(price, D(self.public["public_best_ask_idr_reference"]))
        self.assertLessEqual(cost, self.funding["requested_ticket_ceiling_idr"])
        self.assertGreaterEqual(cost, qty * price)
        card = self.owner.claim_manual_trade_preview_delivery(
            now_epoch=NOW,
        )
        self.assertIsNotNone(card)
        self.assertIn("M05 MANUAL BUY (SIMULASI TOMBOL, NO ORDER)", card["message_text"])
        self.assertIn("BTCIDR", card["message_text"])
        self.assertIn("Nominal pembelian (estimasi): Rp", card["message_text"])
        self.assertIn("Token diperoleh (estimasi): ", card["message_text"])
        self.assertNotIn("fee", card["message_text"].lower())
        self.assertNotIn("pajak", card["message_text"].lower())
        self.assertIn("Trading OFF", card["message_text"])
        self.assertIn("BUY", card["message_text"])
        self.assertEqual(
            self.journal.m07_offline_intent_summary()[
                "manual_sender_nonlive_holds"], 0,
        )
        with sqlite3.connect(self.journal._db_path) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m08_position_settled_idr_events",
            ).fetchone()[0], 0)

    def test_repeat_same_client_id_and_pending_card_never_duplicate(self):
        a = self.queue()
        self.assertTrue(a["owner_card_queued"])
        b = self.queue()
        self.assertFalse(b["owner_card_queued"])
        self.assertEqual(b["owner_rejection_reason"],
                         "M05_MANUAL_TRADE_CLIENT_ID_ALREADY_USED")
        c = self.queue(client="m07secondpreview")
        self.assertFalse(c["owner_card_queued"])
        self.assertEqual(c["owner_rejection_reason"],
                         "M05_MANUAL_TRADE_CARD_ALREADY_PENDING")

    def test_closed_source_and_stale_account_cannot_enqueue(self):
        for extra in (
            {"public_candidate": {**self.public, "observed_epoch": NOW - 40}},
            {"account_snapshot": {**self.account,
                                  "account_observed_epoch": NOW - 50}},
            {"account_snapshot": {**self.account,
                                  "all_pair_open_orders_observed": 1}},
            {"account_snapshot": {**self.account,
                                  "view_only_permissions_verified": False}},
            {"public_candidate": {**self.public,
                                  "status": "M07_PUBLIC_SOURCE_RULES_INCOMPLETE"}},
        ):
            with self.subTest(extra=extra):
                result = self.queue(**extra)
                self.assertFalse(result["owner_card_queued"])
                self.assertFalse(result["execution_allowed"])
        self.assertIsNone(
            self.owner.claim_manual_trade_preview_delivery(now_epoch=NOW),
        )

    def test_budget_fee_spread_and_precision_blocks_card(self):
        for funding in (
            {**self.funding, "requested_ticket_ceiling_idr": D("1")},
            {**self.funding, "requested_ticket_ceiling_idr": D("NaN")},
            {**self.funding, "max_spread_bps": D("0")},
            {**self.funding, "new_floki_sell_rebuy_hold_idr": D("999999")},
            {**self.funding, "max_spread_bps": "150"},
        ):
            with self.subTest(funding=funding):
                try:
                    result = self.queue(funding=funding)
                except (ValueError, ArithmeticError):
                    continue
                self.assertFalse(result["owner_card_queued"])
        self.assertIsNone(
            self.owner.claim_manual_trade_preview_delivery(now_epoch=NOW),
        )

    def test_wrong_owner_or_auto_mode_refuses_manual_card(self):
        bad = self.queue(owner=OWNER + 1)
        self.assertFalse(bad["owner_card_queued"])
        self.assertEqual(bad["owner_rejection_reason"], "OWNER_NOT_AUTHORIZED")
        self.owner.switch_mode(
            user_id=OWNER, chat_id=OWNER, mode="AUTO", now_epoch=NOW,
        )
        auto = self.queue(client="m07autonotallowed")
        self.assertFalse(auto["owner_card_queued"])
        self.assertEqual(auto["owner_rejection_reason"],
                         "M05_MANUAL_TRADE_MODE_REQUIRED")
        self.assertIsNone(
            self.owner.claim_manual_trade_preview_delivery(now_epoch=NOW),
        )

    def test_invalid_identity_and_forged_reference_does_not_queue(self):
        for extra in (
            {"public_candidate": {**self.public,
                                  "candidate_pair": "eth_idr"}},
            {"public_candidate": {**self.public,
                                  "public_best_ask_idr_reference": "NaN"}},
            {"account_fingerprint": "INVALID"},
            {"client_order_id": "bad id"},
            {"owner_main_source_classified": False},
        ):
            with self.subTest(extra=extra):
                try:
                    out = self.queue(**extra)
                except (ValueError, ArithmeticError):
                    continue
                self.assertFalse(out["owner_card_queued"])
        self.assertIsNone(
            self.owner.claim_manual_trade_preview_delivery(now_epoch=NOW),
        )


if __name__ == "__main__":
    unittest.main()
