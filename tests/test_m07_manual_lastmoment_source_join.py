"""Original M05 Telegram owner press -> M03/M06 recheck -> SAME M07 hold.

All market/account/Telegram inputs are mocked or synthetic. The output never
qualifies a LIVE order, independent G1 source, paid AI Risk or real IDR spend.
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
from indodax_ai_bot.m07_order_adapter import M07OrderDraft, OfficialV2OrderAdapter
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_bot_api import TelegramBotApiClient, TelegramOwnerPoller
from tests.test_m03_public_quote_friction_mainpath import public_observation
from tests.test_m07_main_idr_sourcebound_sizing_original import NOW, RULES, account


OWNER = 174401
BOT_ID = 9876543210
ACCOUNT_FP = "a" * 64
CLIENT_ID = "m07_manual_fresh_001"


class OriginalM07OwnerLastMomentSourceTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix="m07-owner-recheck-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.owner = OwnerDecisionLedger(
            root / "owner.sqlite3", owner_user_id=OWNER)
        self.journal = DecisionJournal(root / "decision.sqlite3")
        self.app = DailyScalpingOrchestrator(
            Settings(), self.journal, owner_control=self.owner)
        obs = replace(public_observation(), pair_rule_fields=dict(RULES))
        with patch.object(
            self.journal, "record_and_compare_public_wall",
            return_value={"status": "UNATTESTED"},
        ):
            candidate = self.app._attach_public_wall_experience(
                DeepCandidate("btc_idr", 75, "PUBLIC_REVIEW", None), obs)
        self.public = candidate.summary()["m07_same_candidate_public_preflight"]
        self.account = dict(account(), viewonly_key_uid_fingerprint=ACCOUNT_FP)
        self.funding = {
            "requested_ticket_ceiling_idr": D("100000"),
            "new_floki_sell_rebuy_hold_idr": D("0"),
            "new_pepe_sell_rebuy_hold_idr": D("0"),
            "commitments_not_locked_idr": D("0"),
            "max_spread_bps": D("150"),
        }
        self.preview_id = None
        self.draft = None

    def prepare(self, *, click=True):
        result = self.app.queue_m07_same_source_main_buy_preview_nonlive(
            user_id=OWNER, chat_id=OWNER,
            account_fingerprint=ACCOUNT_FP,
            client_order_id=CLIENT_ID,
            public_candidate=self.public,
            account_snapshot=self.account, funding=self.funding,
            now_epoch=NOW,
        )
        self.assertTrue(result["owner_card_queued"], result)
        self.preview_id = result["preview_id"]
        self.draft = M07OrderDraft(
            symbol="BTCIDR", side="BUY", order_type="LIMIT",
            client_order_id=CLIENT_ID,
            price=D(result["buy_limit_price_idr_reference"]),
            quantity=D(result["base_quantity_reference"]),
        )
        card = self.owner.claim_manual_trade_preview_delivery(now_epoch=NOW)
        self.assertIsNotNone(card)
        self.owner.confirm_manual_trade_preview_delivery(
            preview_id=self.preview_id, message_id=88)
        if click:
            api = TelegramBotApiClient(
                token=str(BOT_ID) + ":" + "A" * 30,
                transport=lambda *args: (_ for _ in ()).throw(
                    AssertionError("TELEGRAM_HTTP_NOT_ALLOWED")),
            )
            poller = TelegramOwnerPoller(
                api=api, ledger=self.owner, expected_bot_id=BOT_ID,
                clock=lambda: NOW,
            )
            outcome = poller._dispatch({
                "update_id": 1,
                "callback_query": {
                    "id": "source-bound-manual-click",
                    "from": {"id": OWNER},
                    "data": card["confirm_callback"],
                    "message": {
                        "message_id": 88, "date": NOW,
                        "from": {"id": BOT_ID, "is_bot": True},
                        "chat": {"id": OWNER, "type": "private"},
                    },
                },
            }, NOW)
            self.assertEqual(outcome[0], "OWNER_MANUAL_PREVIEW_BUY_RECORDED")
        return result

    def review(self, **changes):
        inputs = {
            "owner_user_id": OWNER,
            "preview_id": self.preview_id,
            "account_fingerprint": ACCOUNT_FP,
            "draft": self.draft,
            "public_candidate": self.public,
            "account_snapshot": self.account,
            "funding": self.funding,
            "owner_main_source_classified": True,
            "now_epoch": NOW + 1,
        }
        inputs.update(changes)
        return self.app.review_m07_manual_owner_last_moment_nonlive_once(
            **inputs)

    def holds(self):
        with sqlite3.connect(self.journal._db_path) as db:
            return db.execute(
                "SELECT COUNT(*) FROM m07_manual_sender_nonlive").fetchone()[0]

    def assert_blocked_without_new_hold(self, result):
        self.assertFalse(result["m07_manual_sender_hold_staged"], result)
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["order_sent"])
        self.assertEqual(self.holds(), 0)

    def test_real_original_bot_callback_rechecks_and_stages_one_nonlive_hold(self):
        self.prepare()
        with patch(
            "indodax_ai_bot.m07_private_order_readonly._https_json",
            side_effect=AssertionError("NO_PRIVATE_GET"),
        ) as private, patch(
            "indodax_ai_bot.sumopod_provider._post",
            side_effect=AssertionError("NO_PAID_AI"),
        ) as paid:
            result = self.review()
        self.assertEqual(
            result["status"],
            "M07_MANUAL_LASTMOMENT_REFERENCE_HOLD_NONLIVE_ONLY")
        self.assertTrue(result["owner_button_source_bound"])
        self.assertTrue(result["m03_m06_reference_rechecked"])
        self.assertTrue(result["m07_manual_sender_hold_staged"])
        self.assertEqual(result["sender_status"], "OFFLINE_CLICK_BOUND_NO_SUBMIT")
        for field in (
            "g1_issuer_verified", "ai_risk_verified",
            "account_spendable_verified", "owner_order_final_authenticated",
            "order_sent", "execution_allowed",
        ):
            self.assertFalse(result[field])
        self.assertEqual(self.holds(), 1)
        self.assertTrue(result["m08_original_buy_admission_checked"])
        self.assertEqual(
            result["m08_account_buy_source_status"],
            "M08_EXCHANGE_BUY_NOT_OBSERVED",
        )
        self.assertEqual(result["m08_verified_bot_owned_lots"], 0)
        self.assertEqual(result["m08_sellable_bot_owned_base_qty"], "0")
        self.assertFalse(result["m08_position_manager_admitted"])
        self.assertFalse(OfficialV2OrderAdapter.contract_status()["network_allowed"])
        private.assert_not_called()
        paid.assert_not_called()

    def test_unclicked_or_switched_owner_cannot_pass_recheck(self):
        self.prepare(click=False)
        self.assert_blocked_without_new_hold(self.review())
        self.owner.switch_mode(
            user_id=OWNER, chat_id=OWNER,
            mode="AUTO", now_epoch=NOW + 1)
        self.assert_blocked_without_new_hold(self.review())
        self.assertEqual(
            self.review()["status"], "M07_MANUAL_LASTMOMENT_OWNER_PRESS_DENIED")

    def test_changed_account_scope_or_new_source_fingerprint_blocks(self):
        self.prepare()
        wrong = dict(self.account, viewonly_key_uid_fingerprint="b" * 64)
        self.assert_blocked_without_new_hold(
            self.review(account_snapshot=wrong))
        self.assertEqual(
            self.review(account_snapshot=wrong)["status"],
            "M07_MANUAL_LASTMOMENT_SOURCE_IDENTITY_DENIED")
        wrong_pair = dict(self.public, candidate_pair="eth_idr")
        self.assert_blocked_without_new_hold(
            self.review(public_candidate=wrong_pair))

    def test_stale_market_and_account_block_at_last_moment(self):
        self.prepare()
        self.assert_blocked_without_new_hold(
            self.review(now_epoch=NOW + 35))
        older = dict(self.account, account_observed_epoch=NOW - 100)
        self.assert_blocked_without_new_hold(
            self.review(account_snapshot=older))

    def test_changed_ask_or_reduced_funds_cannot_reuse_old_card(self):
        self.prepare()
        changed_quote = dict(
            self.public, public_best_ask_idr_reference="101")
        self.assert_blocked_without_new_hold(
            self.review(public_candidate=changed_quote))
        funded_less = dict(
            self.account, exchange_idr_free_observed="60000")
        result = self.review(account_snapshot=funded_less)
        self.assertEqual(result["status"], "M07_MANUAL_LASTMOMENT_DRAFT_CHANGED")
        self.assert_blocked_without_new_hold(result)

    def test_foreign_draft_and_missing_classification_never_stage(self):
        self.prepare()
        swapped = M07OrderDraft(
            symbol="BTCIDR", side="BUY", order_type="LIMIT",
            client_order_id="m07_another_client", price=self.draft.price,
            quantity=self.draft.quantity,
        )
        self.assert_blocked_without_new_hold(self.review(draft=swapped))
        self.assert_blocked_without_new_hold(
            self.review(owner_main_source_classified=False))

    def test_existing_unknown_or_click_hold_blocks_replay_and_new_intent(self):
        self.prepare()
        first = self.review()
        self.assertTrue(first["m07_manual_sender_hold_staged"])
        again = self.review()
        self.assertEqual(
            again["status"], "M07_MANUAL_LASTMOMENT_OUTSTANDING_HOLD")
        self.assertEqual(self.holds(), 1)
        reopened = DecisionJournal(self.journal._db_path)
        self.assertEqual(
            reopened.m07_offline_intent_summary()["manual_sender_nonlive_holds"], 1)
        self.assertFalse(again["execution_allowed"])


    def test_original_poller_click_refreshes_same_pair_and_stages_hold(self):
        from indodax_ai_bot.deep_analysis import DeepScan
        from indodax_ai_bot.scanner import ScannerResult, ScreenedPair
        # Keep the test self-contained: test_m03g_bulk imports its OWN
        # test fixtures as top-level modules, which only works under
        # unittest discover -s tests, not the operator's
        # python -m unittest tests.test_m07_manual_lastmoment_source_join.
        rows = tuple(
            ScreenedPair(
                "btc_idr" if i == 0 else f"token{i:03d}_idr",
                D("30000000"), D("100"), D("101"),
                D("50"), D("5"), NOW - 1,
            )
            for i in range(20)
        )
        screen = ScannerResult(NOW, 398, 300, 98, rows, rows[:2])
        self.prepare(click=False)
        with sqlite3.connect(self.owner.db_path) as db:
            token = db.execute(
                "SELECT confirm_callback FROM m05_manual_trade_previews"
            ).fetchone()[0]
        candidate = DeepCandidate(
            "btc_idr", 75, "PUBLIC_EVIDENCE_UNATTESTED", None,
            m07_same_candidate_public_preflight=self.public,
        )
        same = {
            "status": "ALIGNED_PUBLIC_UNATTESTED",
            "provisional_watchlist_preview": ["btc_idr"],
            "provisional_quality_reviews": [
                {"pair": "btc_idr",
                 "clear_of_provisional_cautions": True}
            ],
        }
        deep = DeepScan(40, 20, (candidate,), 1, same_window=same)
        calls = []
        update = {
            "update_id": 4,
            "callback_query": {
                "id": "m07-click-id-4", "from": {"id": OWNER},
                "data": token,
                "message": {
                    "message_id": 88, "date": NOW,
                    "from": {"id": BOT_ID, "is_bot": True},
                    "chat": {"id": OWNER, "type": "private"},
                },
            },
        }
        def telegram(method, payload, timeout):
            calls.append(method)
            if method == "getUpdates":
                result = [update] if payload["offset"] <= 4 else []
            elif method in ("answerCallbackQuery", "editMessageReplyMarkup"):
                result = True
            elif method == "sendMessage":
                result = {"message_id": 89,
                          "chat": {"id": OWNER, "type": "private"}}
            else:
                raise AssertionError("NO_UNEXPECTED_TELEGRAM_METHOD")
            return {"ok": True, "result": result}
        api = TelegramBotApiClient(
            token=str(BOT_ID) + ":" + "A" * 30, transport=telegram,
        )
        nonlive = []
        def on_click(preview_id, epoch):
            nonlive.append(preview_id)
            return self.app.recheck_m07_clicked_manual_buy_from_sources_nonlive_once(
                owner_user_id=OWNER, preview_id=preview_id,
                api_key="TEST_VIEWONLY", secret_key="TEST_VIEWONLY",
                funding=self.funding,
            )
        poller = TelegramOwnerPoller(
            api=api, ledger=self.owner, clock=lambda: NOW + 1,
            expected_bot_id=BOT_ID,
            on_manual_buy_preview_nonlive=on_click,
        )
        with (
            patch.object(self.app, "_scan_with_early_momentum",
                         return_value=screen) as scanned,
            patch("indodax_ai_bot.orchestrator.deep_scan",
                  return_value=deep) as market,
            patch.object(self.app, "inspect_m06_idr_budget_readonly_once",
                         return_value=self.account) as funds,
            patch("indodax_ai_bot.orchestrator.time.time",
                  return_value=NOW + 1),
        ):
            first = poller.poll_once()
            second = poller.poll_once()
        self.assertEqual(first["handled"], 1)
        self.assertEqual(second["handled"], 0)
        self.assertEqual(nonlive, [self.preview_id])
        self.assertEqual(self.holds(), 1)
        self.assertEqual(scanned.call_count, 1)
        self.assertEqual(market.call_count, 1)
        self.assertEqual(funds.call_count, 1)
        self.assertIn("answerCallbackQuery", calls)
        self.assertEqual(self.owner.telegram_next_offset(), 5)
        self.assertFalse(first["execution_allowed"])
        self.assertFalse(OfficialV2OrderAdapter.contract_status()["network_allowed"])

    def test_no_click_no_refresh_and_reject_cannot_start_m07(self):
        self.prepare(click=False)
        self.assertIsNone(self.owner.load_clicked_manual_buy_draft_nonlive(
            preview_id=self.preview_id, owner_user_id=OWNER,
            observed_epoch=NOW))
        self.assertIsNone(self.owner.load_clicked_manual_buy_draft_nonlive(
            preview_id=self.preview_id, owner_user_id=OWNER + 1,
            observed_epoch=NOW))
        with patch.object(
            self.app, "_scan_with_early_momentum",
            side_effect=AssertionError("SHOULD_NOT_SCAN"),
        ) as scanned:
            outcome = self.app.recheck_m07_clicked_manual_buy_from_sources_nonlive_once(
                owner_user_id=OWNER, preview_id=self.preview_id,
                api_key="test", secret_key="test", funding=self.funding,
            )
        self.assertEqual(outcome["status"],
                         "M07_CLICK_NOT_BOUND_TO_OWNER_DRAFT")
        scanned.assert_not_called()
        self.assertEqual(self.holds(), 0)

    def test_dedicated_owner_press_cannot_upgrade_synthetic_source_to_live(self):
        self.prepare(click=True)
        press = self.owner.load_clicked_manual_buy_draft_nonlive(
            preview_id=self.preview_id, owner_user_id=OWNER,
            observed_epoch=NOW + 1)
        self.assertIsNotNone(press)
        self.assertEqual(press["draft"], self.draft)
        self.assertFalse(press["order_sent"])
        self.assertIsNone(self.owner.load_clicked_manual_buy_draft_nonlive(
            preview_id=self.preview_id, owner_user_id=OWNER,
            observed_epoch=NOW + 91))
        self.owner.switch_mode(
            user_id=OWNER, chat_id=OWNER, mode="AUTO",
            now_epoch=NOW + 2,
        )
        self.assertIsNone(self.owner.load_clicked_manual_buy_draft_nonlive(
            preview_id=self.preview_id, owner_user_id=OWNER,
            observed_epoch=NOW + 3))

    def record_account_only_buy_for_clicked_draft(self):
        from indodax_ai_bot.m07_private_order_readonly import M07ReadOnlyOrder
        from indodax_ai_bot.m07_readonly_fills import M07VerifiedReadOnlyFills
        from hashlib import sha256
        gross = self.draft.price * self.draft.quantity
        account_order = M07ReadOnlyOrder(
            symbol=self.draft.symbol,
            client_order_id=self.draft.client_order_id,
            order_id=7734, full_order_id="TEST_ACCOUNT_ONLY_7734",
            side="BUY", order_type="LIMIT", status="FILLED",
            original_qty=self.draft.quantity,
            executed_qty=self.draft.quantity,
            created_ms=(NOW - 120) * 1000,
        )
        account_fills = M07VerifiedReadOnlyFills(
            account_symbol=self.draft.symbol,
            client_order_id=self.draft.client_order_id,
            full_order_id="TEST_ACCOUNT_ONLY_7734",
            side="BUY", executed_base=self.draft.quantity,
            gross_quote=gross, total_commission_including_tax_quote=D("100"),
            net_quote=gross + D("100"),
            count=1,
            sha256=sha256(b"account-only-not-bot-owned").hexdigest(),
        )
        self.journal.record_m07_authenticated_readonly_order_status(
            account_fingerprint=ACCOUNT_FP, order=account_order,
            observed_ms=NOW * 1000,
        )
        self.journal.record_m07_exchange_readonly_fills(
            account_fingerprint=ACCOUNT_FP, fills=account_fills,
            observed_ms=NOW * 1000 + 1,
        )
        return gross

    def test_m08_same_journal_account_fill_is_cost_reference_never_bot_lot(self):
        self.prepare()
        gross = self.record_account_only_buy_for_clicked_draft()
        result = self.review()
        self.assertEqual(
            result["status"],
            "M07_MANUAL_LASTMOMENT_REFERENCE_HOLD_NONLIVE_ONLY",
        )
        self.assertEqual(
            result["m08_account_buy_source_status"],
            "M08_ACCOUNT_BUY_COST_BASIS_ONLY_NOT_BOT_OWNED",
        )
        self.assertTrue(result["m08_account_fill_cost_basis_reference_only"])
        self.assertEqual(result["m08_sellable_bot_owned_base_qty"], "0")
        self.assertEqual(result["m08_verified_bot_owned_lots"], 0)
        self.assertEqual(self.holds(), 1)
        account = self.app.review_m08_buy_position_admission_readonly(
            account_fingerprint=ACCOUNT_FP,
            symbol=self.draft.symbol,
            client_order_id=self.draft.client_order_id,
        )
        self.assertEqual(
            D(account["account_buy_cost_basis_idr_reference"]),
            gross + D("100"),
        )
        self.assertFalse(account["bot_owned_buy_fill_proven"])
        self.assertFalse(account["position_manager_admission"])
        self.assertFalse(account["execution_allowed"])
        restarted = DecisionJournal(self.journal._db_path)
        self.assertEqual(
            restarted.m07_offline_intent_summary()[
                "manual_sender_nonlive_holds"], 1,
        )

    def test_m08_corrupt_account_fee_source_blocks_before_m07_hold(self):
        self.prepare()
        self.record_account_only_buy_for_clicked_draft()
        with sqlite3.connect(self.journal._db_path) as db:
            db.execute(
                "UPDATE m07_exchange_readonly_fill_summaries "
                "SET net_quote_idr='1' WHERE client_order_id=?",
                (self.draft.client_order_id,),
            )
        denied = self.review()
        self.assertEqual(
            denied["status"],
            "M07_MANUAL_LASTMOMENT_M08_SOURCE_CONFLICT",
        )
        self.assertTrue(denied["owner_button_source_bound"])
        self.assert_blocked_without_new_hold(denied)
        self.assertEqual(self.journal.m07_offline_intent_summary()[
            "manual_sender_nonlive_holds"], 0)

    def test_m08_foreign_account_fills_do_not_count_as_owner_position(self):
        self.prepare()
        self.record_account_only_buy_for_clicked_draft()
        result = self.review(account_fingerprint="b" * 64)
        self.assertEqual(
            result["status"], "M07_MANUAL_LASTMOMENT_OWNER_PRESS_DENIED",
        )
        self.assert_blocked_without_new_hold(result)



if __name__ == "__main__":
    unittest.main()
