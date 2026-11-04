"""M07 MANUAL original Telegram click → sender shape → same V2 journal.

All transport observations are synthetic. A recorded owner preview press,
mock POST ACK/FILLED and fake fee/fill cannot become live execution,
authenticated bot origin, sellable lot, or settled IDR.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sqlite3
import time
import unittest

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal, DecisionConflictError
from indodax_ai_bot.m07_order_adapter import (
    M07OrderDraft, OfficialV2OrderAdapter, M07Hardlocked,
)
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_bot_api import (
    TelegramBotApiClient, TelegramOwnerPoller,
)

OWNER=174401
BOT=9241907991
ACCOUNT="a"*64
TOKEN=str(BOT)+":fake_m07_manual_sender_token_123456789012345"


def draft(side="BUY", client="m07manualsender01", symbol="INJIDR",
          price="25000", qty="4"):
    return M07OrderDraft(
        symbol=symbol, side=side, order_type="LIMIT",
        client_order_id=client, price=Decimal(price),
        quantity=Decimal(qty),
    )


def fake_response(d, *, status="NEW", qty="0", order_id=1234, **changes):
    return dict({
        "symbol":d.symbol, "clientOrderId":d.client_order_id,
        "side":d.side,"type":d.order_type,
        "orderId":order_id,"status":status,"executedQty":qty,
    },**changes)


class OriginalManualSenderNonliveTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory(prefix="m07-manual-bound-")
        self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)
        self.owner=OwnerDecisionLedger(root/"owner.sqlite3",
                                       owner_user_id=OWNER)
        self.journal=DecisionJournal(root/"original-v2.sqlite3")
        self.app=DailyScalpingOrchestrator(
            Settings(),self.journal,owner_control=self.owner)
        self.now=int(time.time())
        self.sent=[]

    def prepare_owner(self, *, d=None, click=True,
                      reject=False, bot_id=BOT, user=OWNER):
        if d is None: d=draft()
        q=self.app.queue_m07_owner_manual_telegram_preview_offline(
            user_id=OWNER,chat_id=OWNER,
            account_fingerprint=ACCOUNT,draft=d,
            now_epoch=self.now,
        )
        self.assertTrue(q["accepted"],q)
        card=self.owner.claim_manual_trade_preview_delivery(
            now_epoch=self.now,
        )
        self.assertIsNotNone(card)
        self.owner.confirm_manual_trade_preview_delivery(
            preview_id=q["preview_id"],message_id=55,
        )
        if click:
            action=(card["reject_callback"] if reject else
                    card["confirm_callback"])
            def fake_transport(method,payload,timeout):
                raise AssertionError("NO_NETWORK_CALL_{}".format(method))
            api=TelegramBotApiClient(token=TOKEN,transport=fake_transport)
            poller=TelegramOwnerPoller(
                api=api,ledger=self.owner,
                expected_bot_id=BOT,clock=lambda:self.now,
            )
            outcome=poller._dispatch({
                "update_id":1,"callback_query":{
                    "id":"owned-preview-callback",
                    "from":{"id":user},"data":action,
                    "message":{
                        "message_id":55,"date":self.now,
                        "from":{"id":bot_id,"is_bot":True},
                        "chat":{"id":OWNER,"type":"private"},
                    },
                },
            },self.now)
            self.sent.append(outcome)
        return q["preview_id"],d

    def stage(self, preview=None, d=None, **options):
        if preview is None:
            preview,d=self.prepare_owner(d=d)
        if d is None: d=draft()
        return self.app.review_m07_manual_owner_sender_nonlive_once(
            owner_user_id=OWNER,preview_id=preview,
            account_fingerprint=ACCOUNT,draft=d,
            now_epoch=options.pop("now_epoch",self.now),
            **options,
        )

    def counts(self):
        with sqlite3.connect(self.journal._db_path) as db:
            return {
                "sender":db.execute(
                    "SELECT COUNT(*) FROM m07_manual_sender_nonlive"
                ).fetchone()[0],
                "orders":db.execute(
                    "SELECT COUNT(*) FROM m07_exchange_readonly_orders"
                ).fetchone()[0],
                "intents":db.execute(
                    "SELECT COUNT(*) FROM m07_offline_order_intents"
                ).fetchone()[0],
                "settled":db.execute(
                    "SELECT COUNT(*) FROM m08_position_settled_idr_events"
                ).fetchone()[0],
            }

    def deny(self, r):
        self.assertFalse(r["order_sent"])
        self.assertFalse(r["execution_allowed"])
        self.assertFalse(r["live_trading_authorized"])
        self.assertFalse(r["real_sender_issuer_verified"])
        self.assertIsNone(r["owner_final_decision"])

    def test_original_bot_telegram_callback_to_m07_atomic_sender_shadow(self):
        result=self.stage()
        self.assertEqual(self.sent[0][0],
                         "OWNER_MANUAL_PREVIEW_BUY_RECORDED")
        self.deny(result)
        s=result["staged"]
        self.assertEqual(s["status"],"OFFLINE_CLICK_BOUND_NO_SUBMIT")
        self.assertFalse(s["post_order_issuer_verified"])
        self.assertFalse(s["owner_final_execution_authenticated"])
        self.assertFalse(s["retry_authorized"])
        self.assertEqual(self.counts(),{
            "sender":1,"orders":0,"intents":0,"settled":0,
        })

    def test_unclicked_owner_card_does_not_stage_order(self):
        preview,d=self.prepare_owner(click=False)
        r=self.stage(preview,d)
        self.assertEqual(r["status"],"M07_MANUAL_OWNER_CLICK_NOT_ELIGIBLE")
        self.assertFalse(r["execution_allowed"])
        self.assertEqual(self.counts()["sender"],0)

    def test_foreign_bot_envelope_cannot_stage(self):
        preview,d=self.prepare_owner(bot_id=BOT+1)
        self.assertEqual(self.sent[0][0],
                         "REJECT_CALLBACK_MESSAGE_NOT_VERIFIED")
        r=self.stage(preview,d)
        self.assertEqual(r["status"],"M07_MANUAL_OWNER_CLICK_NOT_ELIGIBLE")
        self.assertEqual(self.counts()["sender"],0)

    def test_other_telegram_user_cannot_stage(self):
        preview,d=self.prepare_owner(user=OWNER+1)
        self.assertEqual(self.sent[0][0],"REJECT_CALLBACK_NOT_OWNER")
        self.assertEqual(self.stage(preview,d)["status"],
                         "M07_MANUAL_OWNER_CLICK_NOT_ELIGIBLE")

    def test_reject_click_is_not_buy_order_authority(self):
        preview,d=self.prepare_owner(reject=True)
        self.assertEqual(self.sent[0][0],
                         "OWNER_MANUAL_PREVIEW_REJECT_RECORDED")
        self.assertEqual(self.stage(preview,d)["status"],
                         "M07_MANUAL_OWNER_CLICK_NOT_ELIGIBLE")
        self.assertEqual(self.counts()["sender"],0)

    def test_wrong_draft_identity_is_not_source_bound(self):
        preview,d=self.prepare_owner()
        for bad in (
            draft(client="wrongclient"),
            draft(symbol="BTCIDR"),
            draft(price="26000"),
            draft(qty="5"),
            draft(side="SELL"),
        ):
            with self.subTest(bad=bad):
                r=self.stage(preview,bad)
                self.assertEqual(r["status"],
                                 "M07_MANUAL_OWNER_CLICK_NOT_ELIGIBLE")
        self.assertEqual(self.counts()["sender"],0)

    def test_wrong_account_uid_or_fingerprint_refused(self):
        preview,d=self.prepare_owner()
        for change in (
            {"owner_user_id":OWNER+1},
            {"account_fingerprint":"b"*64},
        ):
            with self.subTest(change=change):
                r=self.app.review_m07_manual_owner_sender_nonlive_once(
                    owner_user_id=change.get("owner_user_id",OWNER),
                    preview_id=preview,
                    account_fingerprint=change.get(
                        "account_fingerprint",ACCOUNT),
                    draft=d,now_epoch=self.now,
                )
                self.assertFalse(r["execution_allowed"])
                self.assertEqual(r["status"],
                                 "M07_MANUAL_OWNER_CLICK_NOT_ELIGIBLE")

    def test_expired_or_auto_mode_button_not_admitted(self):
        preview,d=self.prepare_owner()
        self.assertEqual(
            self.stage(preview,d,now_epoch=self.now+91)["status"],
            "M07_MANUAL_OWNER_CLICK_NOT_ELIGIBLE",
        )
        self.owner.switch_mode(
            user_id=OWNER,chat_id=OWNER,mode="AUTO",
            now_epoch=self.now+1,
        )
        r=self.stage(preview,d,now_epoch=self.now+2)
        self.assertEqual(r["status"],"M07_MANUAL_OWNER_CLICK_NOT_ELIGIBLE")
        self.assertEqual(self.counts()["sender"],0)

    def test_same_click_identical_client_id_replay_cannot_resubmit(self):
        preview,d=self.prepare_owner()
        first=self.stage(preview,d)
        second=self.stage(preview,d)
        self.assertFalse(first["staged"]["replay"])
        self.assertTrue(second["staged"]["replay"])
        self.assertEqual(self.counts()["sender"],1)
        self.deny(second)

    def test_fake_post_new_ack_not_real_order_or_execution(self):
        preview,d=self.prepare_owner()
        r=self.stage(preview,d,mock_result=fake_response(d),
                     mock_result_provided=True)
        self.deny(r)
        self.assertEqual(r["sender_simulation"]["status"],
                         "OFFLINE_MOCK_RESPONSE_HOLD_NO_ISSUER")
        self.assertFalse(r["sender_simulation"]["exchange_response_authenticated"])
        self.assertIsNone(r["sender_simulation"]["real_exchange_order_id"])
        self.assertEqual(self.counts()["settled"],0)

    def test_fake_post_filled_cannot_mint_owned_position(self):
        preview,d=self.prepare_owner()
        r=self.stage(
            preview,d,mock_result=fake_response(
                d,status="FILLED",qty="4"),
            mock_result_provided=True,
        )
        self.deny(r)
        self.assertFalse(r["sender_simulation"]["bot_owned_lot_verified"])
        self.assertFalse(r["sender_simulation"]["m08_settlement_issuer_allowed"])
        self.assertEqual(self.counts()["orders"],0)
        self.assertEqual(self.counts()["settled"],0)

    def test_fake_partial_cannot_claim_partial_fill(self):
        preview,d=self.prepare_owner()
        r=self.stage(
            preview,d,mock_result=fake_response(
                d,status="PARTIALLY_FILLED",qty="1.5"),
            mock_result_provided=True,
        )
        self.assertEqual(r["sender_simulation"]["status"],
                         "OFFLINE_MOCK_RESPONSE_HOLD_NO_ISSUER")
        self.assertFalse(r["sender_simulation"]["bot_owned_lot_verified"])

    def test_timeout_creates_permanent_unknown_and_no_auto_retry(self):
        preview,d=self.prepare_owner()
        r=self.stage(preview,d,mock_result=None,
                     mock_result_provided=True)
        self.assertEqual(r["sender_simulation"]["status"],
                         "OFFLINE_UNKNOWN_HOLD_NO_RETRY")
        self.assertFalse(r["sender_simulation"]["retry_authorized"])
        resumed=DecisionJournal(self.journal._db_path)
        self.assertEqual(
            resumed.m07_offline_intent_summary()[
                "manual_sender_nonlive_unknown_holds"],1,
        )
        with self.assertRaisesRegex(
            DecisionConflictError,"M07_MANUAL_GLOBAL_UNKNOWN_HOLD"):
            resumed.reserve_m07_offline_order_intent(
                intent_id="freshbtc",draft=M07OrderDraft(
                    "BTCIDR","BUY","MARKET","freshbtc",
                    quote_order_qty=10000),
                recorded_epoch=self.now+1,
            )

    def test_different_mock_after_timeout_cannot_clear_unknown(self):
        preview,d=self.prepare_owner()
        self.stage(preview,d,mock_result=None,mock_result_provided=True)
        with self.assertRaisesRegex(
            DecisionConflictError,"ALREADY_TERMINAL_HOLD"):
            self.stage(preview,d,mock_result=fake_response(d),
                       mock_result_provided=True)
        self.assertEqual(
            self.journal.m07_offline_intent_summary()[
                "manual_sender_nonlive_unknown_holds"],1,
        )

    def test_conflicting_mock_ack_cannot_rewrite_first_result(self):
        preview,d=self.prepare_owner()
        self.stage(preview,d,mock_result=fake_response(d),
                   mock_result_provided=True)
        with self.assertRaisesRegex(
            DecisionConflictError,"ALREADY_TERMINAL_HOLD"):
            self.stage(
                preview,d,mock_result=fake_response(d,order_id=999),
                mock_result_provided=True,
            )

    def test_identical_mock_ack_replay_inert(self):
        preview,d=self.prepare_owner()
        self.stage(preview,d,mock_result=fake_response(d),
                   mock_result_provided=True)
        next_result=self.stage(
            preview,d,mock_result=fake_response(d),
            mock_result_provided=True,
        )
        self.assertTrue(next_result["sender_simulation"]["replay"])
        self.assertEqual(self.counts()["sender"],1)

    def test_mock_id_side_qty_status_and_extra_field_refused(self):
        d=draft()
        bad=[
            fake_response(d,clientOrderId="foreign"),
            fake_response(d,side="SELL"),
            fake_response(d,status="PARTIALLY_FILLED",qty="0"),
            fake_response(d,status="FILLED",qty="1"),
            fake_response(d,status="NEW",qty="1"),
            fake_response(d,status="NOT_REAL"),
            dict(fake_response(d),fee="99999"),
        ]
        for mock in bad:
            with self.subTest(mock=mock):
                with self.assertRaisesRegex(
                    ValueError,"M07_SENDER_MOCK_RESULT"):
                    OfficialV2OrderAdapter.inspect_nonlive_post_result_shape(
                        draft=d,response=mock,
                        submitted_at_ms=self.now*1000,
                    )

    def test_manual_sell_without_verified_v2_owned_lot_rejected(self):
        d=draft(side="SELL",client="m07manualsell01")
        preview,d=self.prepare_owner(d=d)
        with self.assertRaisesRegex(
            DecisionConflictError,
            "M07_MANUAL_SELL_REQUIRES_VERIFIED_BOT_OWNED_LOT"):
            self.stage(preview,d)
        self.assertEqual(self.counts()["sender"],0)

    def test_cross_namespace_pair_collision_held(self):
        preview,d=self.prepare_owner()
        self.stage(preview,d)
        with self.assertRaisesRegex(
            DecisionConflictError,"M07_UNRESOLVED_PAIR_INTENT"):
            self.journal.reserve_m07_offline_order_intent(
                intent_id="other_inj",draft=M07OrderDraft(
                    "INJIDR","BUY","MARKET","other_inj",
                    quote_order_qty=10000),
                recorded_epoch=self.now,
            )
        self.assertEqual(self.counts()["intents"],0)

    def test_global_two_slots_across_old_and_manual(self):
        preview,d=self.prepare_owner()
        self.stage(preview,d)
        self.journal.reserve_m07_offline_order_intent(
            intent_id="otherbtc",draft=M07OrderDraft(
                "BTCIDR","BUY","MARKET","otherbtc",
                quote_order_qty=10000),
            recorded_epoch=self.now,
        )
        with self.assertRaisesRegex(
            DecisionConflictError,"M07_MAX_TWO_UNRESOLVED_INTENTS"):
            self.journal.reserve_m07_offline_order_intent(
                intent_id="thirdxrp",draft=M07OrderDraft(
                    "XRPIDR","BUY","MARKET","thirdxrp",
                    quote_order_qty=10000),
                recorded_epoch=self.now,
            )

    def test_concurrent_replay_has_one_atomic_manual_hold(self):
        preview,d=self.prepare_owner()
        def one(_):
            return self.stage(preview,d)["staged"]
        with ThreadPoolExecutor(max_workers=5) as pool:
            results=list(pool.map(one,range(5)))
        self.assertEqual(sum(r["replay"] is False for r in results),1)
        self.assertEqual(self.counts()["sender"],1)

    def test_original_trade_http_methods_remain_hardlocked(self):
        adapter=OfficialV2OrderAdapter()
        for m in ("create_order","get_order","cancel_order","dispatch"):
            with self.subTest(m=m):
                with self.assertRaises(M07Hardlocked):
                    getattr(adapter,m)()
        self.assertFalse(adapter.contract_status()["network_allowed"])

    def test_no_market_ai_sender_network_or_new_m08_event(self):
        with (
            patch("indodax_ai_bot.orchestrator.IndodaxPublicMarket") as public,
            patch("indodax_ai_bot.orchestrator.SumopodChatAdapter",
                  create=True) as ai,
        ):
            r=self.stage()
        public.assert_not_called()
        ai.assert_not_called()
        self.deny(r)
        self.assertEqual(self.counts()["settled"],0)


if __name__=="__main__":
    unittest.main()
