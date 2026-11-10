"""Original V2 owner MANUAL BUY/SELL Telegram preview, zero exchange orders.

Original M07 orchestrator → original M05 owner SQLite → dedicated
Telegram service → Bot API envelope → atomic one-time owner click.
Every test uses synthetic Telegram responses and NO/NO/DRY_RUN. The button
is an explicitly labeled demonstration, never a trade authorization.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from indodax_ai_bot.config import Settings
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.m07_order_adapter import (
    M07Hardlocked, M07OrderDraft, OfficialV2OrderAdapter,
)
from indodax_ai_bot.orchestrator import DailyScalpingOrchestrator
from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_bot_api import TelegramBotApiClient
from indodax_ai_bot.telegram_owner import OfflineTelegramOwnerInterface
from indodax_ai_bot.telegram_service_tick import service_tick_once

BOT = 9241907991
OWNER = 174401
ACCOUNT = "a" * 64
TOKEN = str(BOT) + ":fake_local_test_token_123456789012345678"


def draft(*, side="BUY", client="m07manual001",
          symbol="INJIDR", price="25000", qty="4"):
    return M07OrderDraft(
        symbol=symbol,side=side,order_type="LIMIT",
        client_order_id=client,
        price=Decimal(price),quantity=Decimal(qty),
    )


class ManualOwnerTradePreviewOriginalV2Tests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix="m05-m07-manual-")
        self.addCleanup(t.cleanup)
        root=Path(t.name)
        state=root/"owner"
        state.mkdir(mode=0o700)
        state.chmod(0o700)
        self.db=state/"owner.sqlite3"
        self.ledger=OwnerDecisionLedger(self.db,owner_user_id=OWNER)
        self.db.chmod(0o600)
        jdir=root/"m07"
        jdir.mkdir(mode=0o700)
        self.journal=DecisionJournal(jdir/"v2-original.sqlite3")
        self.bot=DailyScalpingOrchestrator(
            Settings(),self.journal,owner_control=self.ledger)
        secret=root/"secret"
        secret.mkdir(mode=0o700)
        secret.chmod(0o700)
        self.token=secret/"token"
        self.token.write_text(TOKEN+"\n")
        self.token.chmod(0o600)
        self.now=int(time.time())
        self.sent=[]
        self.calls=[]
        self.incoming=[]
        self.fail_send=False
        self.env={
            "INDODAX_TRADING_ENABLED":"NO",
            "INDODAX_ALLOW_LIVE_EXECUTION":"NO",
            "INDODAX_EXECUTION_MODE":"DRY_RUN",
            "M05_TELEGRAM_TICK_GO":"YES",
            "M05_TELEGRAM_NETWORK_GO":"YES",
            "M05_TELEGRAM_DEDICATED_GO":"YES",
            "M05_TELEGRAM_DEDICATED_BOT_ID":str(BOT),
            "M05_TELEGRAM_OWNER_ID":str(OWNER),
            "M05_TELEGRAM_OWNER_DB":str(self.db),
            "M05_TELEGRAM_TOKEN_FILE":str(self.token),
        }

    def queue(self, *, side="BUY",client="m07manual001",
              symbol="INJIDR",price="25000",qty="4",
              uid=OWNER,chat=OWNER):
        return self.bot.queue_m07_owner_manual_telegram_preview_offline(
            user_id=uid,chat_id=chat,account_fingerprint=ACCOUNT,
            draft=draft(side=side,client=client,symbol=symbol,
                        price=price,qty=qty),
            now_epoch=self.now,
        )

    def fake_api(self,method,payload):
        self.calls.append((method,dict(payload)))
        if method=="getMe":
            return {"id":BOT,"is_bot":True,"username":"V2SyntheticBot"}
        if method=="getWebhookInfo":
            return {"url":"","pending_update_count":0}
        if method=="getUpdates":
            return [u for u in self.incoming
                    if u["update_id"]>=payload["offset"]]
        if method=="sendMessage":
            if self.fail_send:
                raise RuntimeError("SEND_TIMEOUT")
            if "reply_markup" in payload:
                self.sent.append(payload)
            return {
                "message_id":51 if "reply_markup" in payload else 50,
                "chat":{"id":OWNER,"type":"private"},
            }
        if method in ("answerCallbackQuery","editMessageReplyMarkup"):
            return True
        raise AssertionError("UNEXPECTED_"+method)

    def tick(self):
        with patch.object(
            TelegramBotApiClient,"call",side_effect=self.fake_api,
        ):
            return service_tick_once(env=self.env)

    def callback(self,data,*,uid=OWNER,chat=OWNER,bot=BOT,
                 message=51,epoch=None,update=1):
        return {"update_id":update,"callback_query":{
            "id":"manual-preview-"+str(update),
            "from":{"id":uid},
            "data":data,
            "message":{
                "message_id":message,
                "date":self.now if epoch is None else epoch,
                "from":{"id":bot,"is_bot":True},
                "chat":{"id":chat,"type":"private"},
            },
        }}

    def row(self):
        with sqlite3.connect(self.db) as c:
            return c.execute(
                "SELECT side,client_order_id,send_state,sent_message_id,"
                "owner_choice,execution_allowed "
                "FROM m05_manual_trade_previews ORDER BY created_epoch"
            ).fetchone()

    def assert_never_order(self,result):
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["order_sent"])
        self.assertFalse(result["actual_trade_click_authorized"]
                         if "actual_trade_click_authorized" in result
                         else result["owner_order_execution_authorized"])

    def test_original_m07_to_m05_buy_card_to_real_poller_owner_click(self):
        queued=self.queue()
        self.assertTrue(queued["accepted"])
        self.assertEqual(queued["owner_mode"],"MANUAL")
        self.assertFalse(queued["source_attested"])
        self.assert_never_order(queued)
        self.assertEqual(queued["symbol"],"INJIDR")
        first=self.tick()
        self.assertEqual(first["processed"],0)
        self.assertEqual(len(self.sent),1)
        card=self.sent[0]
        self.assertIn("MANUAL BUY (BUTTON SIMULATION, NO ORDER)",card["text"])
        self.assertIn("LIMIT price per INJ: Rp25.000",card["text"])
        self.assertIn("Estimated BUY amount: Rp100.000",card["text"])
        self.assertIn("Estimated tokens received: 4 INJ",card["text"])
        self.assertNotIn("fee",card["text"].lower())
        self.assertIn("m07manual001",card["text"])
        self.assertEqual(card["chat_id"],OWNER)
        buttons=card["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([x["text"] for x in buttons],
                         ["BUY (uji, tanpa order)","BATAL"])
        self.assertLessEqual(len(buttons[0]["callback_data"]),64)
        self.assertEqual(self.row(),(
            "BUY","m07manual001","SENT",51,None,0,
        ))
        self.incoming=[self.callback(buttons[0]["callback_data"])]
        second=self.tick()
        self.assertEqual(second["processed"],1)
        self.assertFalse(second["execution_allowed"])
        self.assertEqual(self.row(),(
            "BUY","m07manual001","SENT",51,"BUY",0,
        ))
        self.assertEqual(self.ledger.telegram_next_offset(),2)
        self.assertIn("answerCallbackQuery",[m for m,_ in self.calls])
        self.assertIn("editMessageReplyMarkup",[m for m,_ in self.calls])
        self.assertEqual(self.journal.m07_offline_intent_summary()[
            "unresolved_simulated_intents"],0)
        with sqlite3.connect(self.journal._db_path) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM m08_position_settled_idr_events"
            ).fetchone()[0],0)

    def test_sell_button_is_own_explicit_side_but_cannot_sell(self):
        q=self.queue(side="SELL",client="m07manualsell001")
        self.assertTrue(q["accepted"])
        self.assert_never_order(q)
        self.tick()
        self.assertIn(
            "Estimated SELL proceeds: Rp100.000",
            self.sent[0]["text"],
        )
        self.assertIn(
            "BUY cost: no verified V2-owned BUY lot",
            self.sent[0]["text"],
        )
        self.assertIn(
            "Net P/L: unavailable without verified BUY cost",
            self.sent[0]["text"],
        )
        self.assertNotIn("fee",self.sent[0]["text"].lower())
        button=self.sent[0]["reply_markup"]["inline_keyboard"][0][0]
        self.assertEqual(button["text"],"SELL (uji, tanpa order)")
        self.incoming=[self.callback(button["callback_data"])]
        self.tick()
        self.assertEqual(self.row()[4:6],("SELL",0))
        self.assertFalse(OfficialV2OrderAdapter.contract_status()[
            "execution_allowed"])

    def test_manual_reject_button_is_not_buy_or_sell(self):
        self.queue()
        self.tick()
        reject=self.sent[0]["reply_markup"]["inline_keyboard"][0][1][
            "callback_data"]
        self.incoming=[self.callback(reject)]
        self.tick()
        self.assertEqual(self.row()[4:6],("REJECT",0))

    def test_foreign_chat_cannot_queue_and_no_message(self):
        result=self.queue(uid=OWNER+1,chat=OWNER+1)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"],"OWNER_NOT_AUTHORIZED")
        self.assertIsNone(self.row())
        result=self.queue(uid=OWNER,chat=OWNER+1)
        self.assertFalse(result["accepted"])
        self.assertIsNone(self.row())

    def test_foreign_callback_never_accepts_buy(self):
        self.queue()
        self.tick()
        action=self.sent[0]["reply_markup"]["inline_keyboard"][0][0][
            "callback_data"]
        self.incoming=[self.callback(action,uid=OWNER+1,chat=OWNER+1)]
        result=self.tick()
        self.assertEqual(result["processed"],1)
        self.assertIsNone(self.row()[4])
        self.assertFalse(result["execution_allowed"])

    def test_wrong_bot_message_envelope_denied_before_owner_ledger(self):
        self.queue()
        self.tick()
        action=self.sent[0]["reply_markup"]["inline_keyboard"][0][0][
            "callback_data"]
        self.incoming=[self.callback(action,bot=BOT+1)]
        self.tick()
        self.assertIsNone(self.row()[4])

    def test_forwarded_or_wrong_message_id_rejected(self):
        self.queue()
        self.tick()
        action=self.sent[0]["reply_markup"]["inline_keyboard"][0][0][
            "callback_data"]
        self.incoming=[self.callback(action,message=42)]
        self.tick()
        self.assertIsNone(self.row()[4])

    def test_opposite_side_forged_callback_rejected(self):
        self.queue(side="BUY")
        self.tick()
        action=self.sent[0]["reply_markup"]["inline_keyboard"][0][0][
            "callback_data"]
        wrong=action.replace(":B:",":S:")
        self.incoming=[self.callback(wrong)]
        self.tick()
        self.assertIsNone(self.row()[4])

    def test_wrong_token_and_unknown_card_denied(self):
        self.queue()
        self.tick()
        action=self.sent[0]["reply_markup"]["inline_keyboard"][0][0][
            "callback_data"]
        forged=action[:-32]+"f"*32
        if forged==action:
            forged=action[:-32]+"e"*32
        self.incoming=[self.callback(forged)]
        self.tick()
        self.assertIsNone(self.row()[4])

    def test_callback_replay_no_second_owner_choice(self):
        self.queue()
        self.tick()
        action=self.sent[0]["reply_markup"]["inline_keyboard"][0][0][
            "callback_data"]
        self.incoming=[self.callback(action,update=1)]
        self.tick()
        self.assertEqual(self.row()[4],"BUY")
        self.incoming=[self.callback(action,update=2)]
        self.tick()
        self.assertEqual(self.row()[4],"BUY")
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m05_audit "
                "WHERE event='MANUAL_PREVIEW_BUY'"
            ).fetchone()[0],1)

    def test_manual_preview_expired_cannot_authorize_owner_action(self):
        self.queue()
        self.tick()
        token=self.sent[0]["reply_markup"]["inline_keyboard"][0][0][
            "callback_data"]
        result=self.ledger.apply_manual_trade_preview_callback(
            user_id=OWNER,chat_id=OWNER,callback_data=token,
            telegram_message_id=51,now_epoch=self.now+91,
        )
        self.assertFalse(result["accepted"])
        self.assertIsNone(self.row()[4])

    def test_mode_auto_denies_new_manual_cards(self):
        switched=self.ledger.switch_mode(
            user_id=OWNER,chat_id=OWNER,mode="AUTO",
            now_epoch=self.now,
        )
        self.assertTrue(switched["accepted"])
        result=self.queue()
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"],"M05_MANUAL_TRADE_MODE_REQUIRED")
        self.assertIsNone(self.row())

    def test_manual_auto_manual_transition_invalidates_old_button(self):
        self.queue()
        self.tick()
        button=self.sent[0]["reply_markup"]["inline_keyboard"][0][0][
            "callback_data"]
        self.ledger.switch_mode(
            user_id=OWNER,chat_id=OWNER,mode="AUTO",
            now_epoch=self.now+1,
        )
        self.ledger.switch_mode(
            user_id=OWNER,chat_id=OWNER,mode="MANUAL",
            now_epoch=self.now+2,
        )
        result=self.ledger.apply_manual_trade_preview_callback(
            user_id=OWNER,chat_id=OWNER,callback_data=button,
            now_epoch=self.now+3,telegram_message_id=51,
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(self.row()[4],"REJECT")

    def test_duplicate_client_and_parallel_card_blocked(self):
        self.queue()
        a=self.queue(client="m07manual002")
        b=self.queue(client="m07manual001")
        self.assertEqual(a["reason"],"M05_MANUAL_TRADE_CARD_ALREADY_PENDING")
        self.assertEqual(b["reason"],"M05_MANUAL_TRADE_CLIENT_ID_ALREADY_USED")
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m05_manual_trade_previews"
            ).fetchone()[0],1)

    def test_concurrent_exact_preview_only_one_queued(self):
        def do(_):
            return self.queue()
        with ThreadPoolExecutor(max_workers=5) as pool:
            results=list(pool.map(do,range(5)))
        self.assertEqual(sum(x["accepted"] for x in results),1)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM m05_manual_trade_previews"
            ).fetchone()[0],1)

    def test_cancelled_or_closed_preview_does_not_reuse_client_id(self):
        self.queue()
        self.tick()
        token=self.sent[0]["reply_markup"]["inline_keyboard"][0][1][
            "callback_data"]
        self.ledger.apply_manual_trade_preview_callback(
            user_id=OWNER,chat_id=OWNER,callback_data=token,
            now_epoch=self.now,telegram_message_id=51,
        )
        r=self.queue()
        self.assertFalse(r["accepted"])
        self.assertEqual(r["reason"],"M05_MANUAL_TRADE_CLIENT_ID_ALREADY_USED")
        r=self.queue(client="m07manualnew001")
        self.assertTrue(r["accepted"])

    def test_invalid_pair_price_qty_or_type_never_queues(self):
        for changes in (
            {"symbol":"USDTIDR"},
            {"symbol":"FLOKIIDR"},
            {"price":"NaN"},
            {"price":"-1"},
            {"qty":"0"},
            {"side":"CANCEL"},
        ):
            with self.subTest(changes=changes):
                r=self.queue(**changes)
                self.assertFalse(r["accepted"])
        self.assertIsNone(self.row())

    def test_sender_uncertain_claim_blocks_replay_without_order(self):
        self.queue()
        item=self.ledger.claim_manual_trade_preview_delivery(
            now_epoch=self.now)
        self.assertIsNotNone(item)
        with self.assertRaisesRegex(
            ValueError,"M05_MANUAL_TRADE_DELIVERY_UNCERTAIN"):
            self.ledger.claim_manual_trade_preview_delivery(
                now_epoch=self.now+1)
        self.assertEqual(self.row()[2],"CLAIMED")
        self.assertFalse(self.ledger.status(
            user_id=OWNER,chat_id=OWNER)["execution_allowed"])

    def test_only_one_card_per_tick_market_first_then_manual(self):
        q=self.ledger.queue_owner_review_button_test(
            user_id=OWNER,chat_id=OWNER,now_epoch=self.now)
        self.assertTrue(q["accepted"])
        self.queue()
        first=self.tick()
        self.assertEqual(len(self.sent),1)
        self.assertEqual(first["processed"],0)
        self.assertIn("PASS (review only)",
                      [x["text"] for x in self.sent[0][
                          "reply_markup"]["inline_keyboard"][0]])
        self.assertEqual(self.row()[2],"QUEUED")
        second=self.tick()
        self.assertEqual(second["processed"],0)
        self.assertEqual(len(self.sent),2)
        self.assertEqual(self.row()[2],"SENT")

    def test_m05_buy_sell_commands_still_cannot_dispatch_orders(self):
        interface=OfflineTelegramOwnerInterface(self.ledger)
        for name in ("/buy","/sell"):
            r=interface.handle_command(
                user_id=OWNER,chat_id=OWNER,text=name,
                now_epoch=self.now,
            )
            self.assertFalse(r["execution_allowed"])
            self.assertFalse(r["accepted"])
            self.assertIn("No order sent",r["text"])

    def test_original_m07_order_adapter_stays_hardlocked_all_methods(self):
        adapter=OfficialV2OrderAdapter()
        for fn in ("create_order","get_order","cancel_order","dispatch"):
            with self.assertRaises(M07Hardlocked):
                getattr(adapter,fn)()


if __name__=="__main__":
    unittest.main()
