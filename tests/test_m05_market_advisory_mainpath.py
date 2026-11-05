"""Original V2 scanner -> M04 diagnostic -> M05 owner/private Telegram review.

All market/Telegram transports in this module are synthetic test fixtures.
The durable M05 ledger, original orchestrator and dedicated poller are REAL.
No exchange/private API, paid models, orders or service modifications.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.three_role_ai import ThreeRoleShadow as RealThreeRoleShadow
from indodax_ai_bot.telegram_bot_api import (
    TelegramBotApiClient, TelegramOwnerPoller as RealTelegramOwnerPoller,
)
from indodax_ai_bot.telegram_dedicated_inbound import (
    dedicated_owner_poll_once,
)

NOW = 1800000140
CLOSED = 1800000120
OWNER = 5678001
BOT_ID = 77788001
TOKEN = str(BOT_ID) + ":" + "syntheticDedicatedBotToken1234567890"


def public_cohort(closed_epoch=CLOSED, pair="inj_idr"):
    return {
        "screening_observed_epoch": NOW,
        "coverage_status": "ALL_SELECTED_ANALYZED_UNATTESTED",
        "g1_verified": False, "source_trust_verified": False,
        "trade_history_complete": False,
        "eligible_cohort": {
            "status": "ALIGNED_PUBLIC_UNATTESTED",
            "closed_1m_epoch": closed_epoch,
            "btc_regime": "BTC_UPTREND_DIAGNOSTIC",
            "provisional_watchlist_preview": [pair],
            "ranked_same_window_diagnostics": [{"pair": pair, "score": 83}],
            "provisional_quality_reviews": [{
                "pair": pair, "review_flags": [],
                "clear_of_provisional_cautions": True,
                "scalp_setup_stage": {
                    "stage": "PULLBACK_REBOUND_WATCH",
                    "trade_authorized": False,
                },
            }],
            "source_trust_verified": False,
            "g1_verified": False, "trade_history_complete": False,
        },
        "provisional_market_evidence": [{
            "pair": pair, "closed_1m_epoch": closed_epoch,
            "score": 83, "trade_authorized": False,
            "public_best_bid_idr": "1400",
            "public_best_ask_idr": "1404",
            "public_quote_observed_epoch": NOW - 5,
            "public_quote_is_trade_price": False,
            "volume_ratio": "2.40",
            "scalp_setup_stage": {"stage": "PULLBACK_REBOUND_WATCH"},
        }],
        "g1_source_admission": {"g1_verified": False},
    }


class FakeTelegram:
    def __init__(self, updates=()):
        self.updates = list(updates)
        self.calls = []
        self.sent = None

    def __call__(self, method, payload, timeout):
        self.calls.append((method, dict(payload)))
        if method == "getMe":
            result = {"id": BOT_ID, "is_bot": True, "username": "OfficialV2TestBot"}
        elif method == "getWebhookInfo":
            result = {"url": "", "pending_update_count": 0}
        elif method == "sendMessage":
            self.sent = dict(payload)
            result = {"message_id": 55, "chat": {"id": OWNER, "type": "private"}}
        elif method == "getUpdates":
            result = [u for u in self.updates
                      if u["update_id"] >= payload["offset"]]
        elif method in ("answerCallbackQuery", "editMessageReplyMarkup"):
            result = True
        else:
            raise AssertionError("UNEXPECTED_TELEGRAM_METHOD")
        return {"ok": True, "result": result}


def fake_callback(data, *, user=OWNER, chat=OWNER, update_id=4):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": "callback-" + str(update_id),
            "from": {"id": user}, "data": data,
            "message": {
                "message_id": 55, "date": NOW,
                "from": {"id": BOT_ID, "is_bot": True},
                "chat": {"id": chat, "type": "private"},
            },
        },
    }


class OriginalV2MarketAdvisoryMainpathTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.journal = DecisionJournal(self.dir / "main.sqlite3")
        self.ledger = OwnerDecisionLedger(
            self.dir / "owner.sqlite3", owner_user_id=OWNER,
        )
        self.app = DailyScalpingOrchestrator(
            Settings.from_env({}), self.journal,
            owner_control=self.ledger,
        )
        self.lock = self.dir / "dedicated-poller.lock"

    def market_cycle(self, bulk=None, trace="market-001"):
        snapshot = public_cohort() if bulk is None else bulk
        with (
            patch.object(self.app, "_scan_with_early_momentum",
                         return_value=object()),
            patch("indodax_ai_bot.orchestrator.bulk_public_deep_scan",
                  return_value=snapshot),
            patch("indodax_ai_bot.orchestrator.time.time", return_value=NOW),
            patch("indodax_ai_bot.orchestrator.ThreeRoleShadow",
                  side_effect=lambda **kw: RealThreeRoleShadow(
                      clock=lambda: NOW, **kw)),
        ):
            return self.app.run_bulk_deep_cycle(
                trace, market=object(),
                min_volume_idr=0, deep_limit=20,
            )

    def dedicated_tick(self, fake):
        api = TelegramBotApiClient(token=TOKEN, transport=fake)
        with (
            patch(
                "indodax_ai_bot.telegram_dedicated_inbound.time.time",
                return_value=NOW + 3,
            ),
            patch("indodax_ai_bot.telegram_dedicated_inbound.TelegramOwnerPoller",
                  side_effect=lambda **kw: RealTelegramOwnerPoller(
                      clock=lambda: NOW + 3, **kw)),
        ):
            return dedicated_owner_poll_once(
                api=api, ledger=self.ledger,
                expected_bot_id=BOT_ID, lock_path=self.lock,
            )

    def test_original_bulk_to_owner_queue_without_final_or_order(self):
        out = self.market_cycle()
        self.assertIsNone(out.final_decision)
        self.assertEqual(out.ai_shadow["reason"], "G1_PUBLIC_EVIDENCE_UNTRUSTED")
        self.assertEqual(out.owner_advisory["queued"], 1)
        self.assertFalse(out.owner_advisory["execution_allowed"])
        self.assertFalse(out.execution_allowed)
        with sqlite3.connect(self.journal._db_path) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 0
            )
        status = self.ledger.status(user_id=OWNER, chat_id=OWNER)
        self.assertEqual(status["pending_shadow_proposals"], 1)

    def test_original_dedicated_outbound_then_owner_pass_and_replay(self):
        self.market_cycle()
        fake = FakeTelegram()
        tick = self.dedicated_tick(fake)
        self.assertEqual(
            tick["owner_market_advisory_delivery"]["messages_sent"], 1
        )
        self.assertEqual(
            [method for method, _ in fake.calls],
            ["getMe", "getWebhookInfo", "getUpdates", "sendMessage"],
        )
        self.assertEqual(fake.sent["chat_id"], OWNER)
        self.assertIn("G1/account/order: UNVERIFIED", fake.sent["text"])
        self.assertIn("Ini WATCH pasar saja, belum ada draft BUY/SELL", fake.sent["text"])
        self.assertNotIn("Harga dan jumlah order belum diotorisasi", fake.sent["text"])
        self.assertNotIn("Nominal pembelian (estimasi)", fake.sent["text"])
        self.assertIn("M03/M04 public advisory", fake.sent["text"])
        # Owner's live screenshot exposed literal backslash+n characters.
        # A real Telegram send must use newline characters, label WATCH
        # explicitly and show a human WIB closed-candle time.
        self.assertNotIn(chr(92) + "n", fake.sent["text"])
        self.assertIn("\nWATCH PASAR, BUKAN PERINTAH BUY\n", fake.sent["text"])
        self.assertIn("\nPair: INJ_IDR\n", fake.sent["text"])
        self.assertIn("\nCandle 1m tutup: ", fake.sent["text"])
        self.assertIn(" WIB\n", fake.sent["text"])
        self.assertIn("Bid Rp1400 | Ask Rp1404", fake.sent["text"])
        self.assertIn("Spread 28.49 bps", fake.sent["text"])
        self.assertIn("Skor scanner 83/100 | Volume 1m 2.40x", fake.sent["text"])
        self.assertIn("BUKAN harga entry", fake.sent["text"])
        self.assertIn("bukan BUY/SELL", fake.sent["text"])
        keyboard = fake.sent["reply_markup"]["inline_keyboard"][0]
        self.assertEqual(len(keyboard), 2)
        self.assertEqual(keyboard[0]["text"], "PASS (review only)")
        self.assertLessEqual(len(keyboard[0]["callback_data"].encode()), 64)
        callback = fake_callback(keyboard[0]["callback_data"])
        next_fake = FakeTelegram([callback])
        result = self.dedicated_tick(next_fake)
        self.assertEqual(result["handler"]["handled"], 1)
        self.assertEqual(
            [method for method, _ in next_fake.calls],
            ["getMe", "getWebhookInfo", "getUpdates",
             "answerCallbackQuery", "editMessageReplyMarkup", "sendMessage"],
        )
        with sqlite3.connect(self.ledger.db_path) as db:
            self.assertEqual(db.execute(
                "SELECT state FROM m05_proposals"
            ).fetchone()[0], "PASS")
            self.assertEqual(db.execute(
                "SELECT entry_price_idr,quantity,execution_allowed "
                "FROM m05_proposals"
            ).fetchone(), ("0", "0", 0))
        duplicate = self.ledger.apply_callback(
            user_id=OWNER, chat_id=OWNER,
            callback_data=keyboard[1]["callback_data"],
            now_epoch=NOW + 4,
        )
        self.assertFalse(duplicate["accepted"])
        self.assertIsNone(duplicate["final_decision"])
        self.assertEqual(
            self.ledger.status(user_id=OWNER, chat_id=OWNER)[
                "pending_shadow_proposals"], 0,
        )

    def test_owner_reject_is_stored_not_module_reject(self):
        self.market_cycle()
        fake = FakeTelegram()
        self.dedicated_tick(fake)
        rejected = FakeTelegram([
            fake_callback(fake.sent["reply_markup"]["inline_keyboard"][0][1][
                "callback_data"])
        ])
        self.dedicated_tick(rejected)
        with sqlite3.connect(self.ledger.db_path) as db:
            self.assertEqual(db.execute(
                "SELECT state FROM m05_proposals"
            ).fetchone()[0], "REJECT")

    def test_stale_market_observation_never_queues(self):
        out = self.market_cycle(public_cohort(closed_epoch=NOW-200))
        self.assertEqual(out.owner_advisory["queued"], 0)
        self.assertFalse(out.execution_allowed)
        self.assertEqual(
            self.ledger.status(user_id=OWNER, chat_id=OWNER)[
                "pending_shadow_proposals"], 0,
        )

    def test_duplicate_candle_queues_once(self):
        first = self.market_cycle(trace="first")
        second = self.market_cycle(trace="second")
        self.assertEqual(first.owner_advisory["queued"], 1)
        self.assertEqual(second.owner_advisory["queued"], 0)
        self.assertEqual(
            self.ledger.status(user_id=OWNER, chat_id=OWNER)[
                "pending_shadow_proposals"], 1,
        )

    def test_unacknowledged_market_advisory_cannot_be_decided(self):
        self.market_cycle()
        with sqlite3.connect(self.ledger.db_path) as db:
            data = db.execute(
                "SELECT proposal_id,pass_callback FROM m05_market_advisory_outbox"
            ).fetchone()
        self.assertIsNotNone(data)
        result = self.ledger.apply_callback(
            user_id=OWNER, chat_id=OWNER,
            callback_data=data[1], now_epoch=NOW + 1,
        )
        self.assertEqual(result["reason"], "M05_ADVISORY_NOT_SENT_TO_OWNER")
        self.assertFalse(result["accepted"])
        self.assertIsNone(result["final_decision"])
        self.assertEqual(
            self.ledger.market_advisory_delivery_status(data[0]), "QUEUED",
        )

    def test_expired_unsent_proposal_is_not_delivered(self):
        self.market_cycle()
        with sqlite3.connect(self.ledger.db_path) as db:
            proposal_id = db.execute(
                "SELECT proposal_id FROM m05_market_advisory_outbox"
            ).fetchone()[0]
        self.assertIsNone(self.ledger.claim_market_advisory_delivery(
            now_epoch=CLOSED + 90,
        ))
        self.assertEqual(
            self.ledger.market_advisory_delivery_status(proposal_id),
            "EXPIRED",
        )
        self.assertEqual(
            self.ledger.status(user_id=OWNER, chat_id=OWNER)[
                "pending_shadow_proposals"], 0,
        )

    def test_auto_mode_never_forges_manual_proposal(self):
        self.ledger.switch_mode(
            user_id=OWNER, chat_id=OWNER, mode="AUTO", now_epoch=NOW,
        )
        out = self.market_cycle()
        self.assertEqual(out.owner_advisory["queued"], 0)
        self.assertEqual(out.owner_advisory["diagnostics"],
                         ["M05_MANUAL_MODE_REQUIRED"])

    def test_foreign_callback_never_consumes_owner_proposal(self):
        self.market_cycle()
        fake = FakeTelegram()
        self.dedicated_tick(fake)
        keyboard = fake.sent["reply_markup"]["inline_keyboard"][0]
        bad = FakeTelegram([
            fake_callback(keyboard[0]["callback_data"], chat=OWNER + 1)
        ])
        self.dedicated_tick(bad)
        with sqlite3.connect(self.ledger.db_path) as db:
            self.assertEqual(db.execute(
                "SELECT state FROM m05_proposals"
            ).fetchone()[0], "PENDING")
        self.assertEqual(
            self.ledger.status(user_id=OWNER, chat_id=OWNER)[
                "pending_shadow_proposals"], 1,
        )


    def test_no_confirmed_entry_never_sends_owner_card_even_if_watchlist_names_pair(self):
        bad = public_cohort()
        bad["provisional_market_evidence"][0]["scalp_setup_stage"] = {
            "stage": "NO_CONFIRMED_ENTRY_SETUP", "trade_authorized": False,
        }
        bad["eligible_cohort"]["provisional_quality_reviews"][0]["scalp_setup_stage"] = {
            "stage": "NO_CONFIRMED_ENTRY_SETUP", "trade_authorized": False,
        }
        outcome = self.market_cycle(bad)
        self.assertEqual(outcome.owner_advisory["queued"], 0)
        self.assertIn("M03_NO_CONFIRMED_QUALITY_CLEAN_SETUP",
                      outcome.owner_advisory["diagnostics"])
        self.assertIsNone(self.ledger.claim_market_advisory_delivery(
            now_epoch=NOW + 3))
        self.assertFalse(outcome.execution_allowed)

    def test_quality_flags_and_stage_mismatch_fail_closed_before_telegram(self):
        for issue in ("flagged", "mismatched", "untrusted"):
            with self.subTest(issue=issue), TemporaryDirectory() as td:
                ledger = OwnerDecisionLedger(
                    Path(td) / "owner.sqlite3", owner_user_id=OWNER)
                bulk = public_cohort()
                q = bulk["eligible_cohort"]["provisional_quality_reviews"][0]
                if issue == "flagged":
                    q["review_flags"] = ["MOMENTUM_DECELERATION_CAUTION"]
                    q["clear_of_provisional_cautions"] = False
                elif issue == "mismatched":
                    q["scalp_setup_stage"]["stage"] = "EARLY_ACCELERATION_WATCH"
                else:
                    q["scalp_setup_stage"].pop("trade_authorized")
                from indodax_ai_bot.m05_market_advisory import (
                    queue_scanner_owner_advisory,
                )
                out = queue_scanner_owner_advisory(
                    bulk=bulk, ai_shadow=None, owner=ledger, now_epoch=NOW)
                self.assertEqual(out["queued"], 0)
                self.assertEqual(out["diagnostics"],
                                 ["M03_NO_CONFIRMED_QUALITY_CLEAN_SETUP"])
                self.assertFalse(out["execution_allowed"])

    def test_repeat_identical_setup_cooldown_but_changed_setup_is_allowed(self):
        from indodax_ai_bot.m05_market_advisory import queue_scanner_owner_advisory
        first = self.market_cycle()
        self.assertEqual(first.owner_advisory["queued"], 1)
        later = public_cohort(closed_epoch=CLOSED + 360)
        later["provisional_market_evidence"][0][
            "public_quote_observed_epoch"] = NOW + 355
        same = queue_scanner_owner_advisory(
            bulk=later, ai_shadow=None, owner=self.ledger,
            now_epoch=NOW + 360)
        self.assertEqual(same["queued"], 0)
        self.assertEqual(same["diagnostics"],
                         ["M05_ADVISORY_SAME_SETUP_COOLDOWN"])
        self.assertIsNone(same["owner_final_decision"])
        changed = public_cohort(closed_epoch=CLOSED + 420)
        changed["provisional_market_evidence"][0][
            "public_quote_observed_epoch"] = NOW + 415
        changed["provisional_market_evidence"][0]["scalp_setup_stage"] = {
            "stage": "BREAKOUT_CONTINUATION_WATCH", "trade_authorized": False,
        }
        changed["eligible_cohort"]["provisional_quality_reviews"][0][
            "scalp_setup_stage"] = {
                "stage": "BREAKOUT_CONTINUATION_WATCH",
                "trade_authorized": False,
            }
        fresh = queue_scanner_owner_advisory(
            bulk=changed, ai_shadow=None, owner=self.ledger,
            now_epoch=NOW + 420)
        self.assertEqual(fresh["queued"], 1)
        self.assertFalse(fresh["execution_allowed"])
        self.assertEqual(
            self.ledger.status(user_id=OWNER, chat_id=OWNER)[
                "pending_shadow_proposals"], 2,
        )


if __name__ == "__main__":
    unittest.main()
