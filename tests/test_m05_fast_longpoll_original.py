"""Fast M05 Telegram longpoll against ORIGINAL owner ledger and poller.

No real Telegram, token, paid AI, exchange HTTP, systemd or LIVE order.
Tests run both `python -m unittest discover -s tests` and `tests.*`.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from indodax_ai_bot.config import ConfigurationError
from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_bot_api import (
    TelegramBotApiClient, TelegramOwnerPoller, TelegramTransportError,
)
from indodax_ai_bot.telegram_dedicated_inbound import (
    dedicated_owner_fast_loop_nonlive, dedicated_poller_lock,
)
from indodax_ai_bot.telegram_service_tick import service_fast_loop_nonlive

OWNER = 45912007
BOT = 9241907991
TOKEN = str(BOT) + ":" + "A" * 30
NOW = int(time.time())


class M05OriginalFastLongpollTests(unittest.TestCase):
    def setUp(self):
        self.loop_clock = 0.0
        self.poll_elapsed = 2.0
        clock = patch(
            "indodax_ai_bot.telegram_dedicated_inbound.monotonic",
            side_effect=lambda: self.loop_clock,
        )
        clock.start()
        self.addCleanup(clock.stop)
        t = tempfile.TemporaryDirectory(prefix="original-m05-fast-")
        self.addCleanup(t.cleanup)
        root = Path(t.name)
        self.state = root / "state"
        self.state.mkdir(mode=0o700)
        self.state.chmod(0o700)
        self.secrets = root / "secrets"
        self.secrets.mkdir(mode=0o700)
        self.secrets.chmod(0o700)
        self.db = self.state / "owner.sqlite3"
        self.owner = OwnerDecisionLedger(self.db, owner_user_id=OWNER)
        self.db.chmod(0o600)
        self.key_file = self.secrets / "token"
        self.key_file.write_text(TOKEN + "\n", encoding="ascii")
        self.key_file.chmod(0o600)
        self.lock = str(self.db) + ".dedicated-poller.lock"
        self.env = {
            "INDODAX_TRADING_ENABLED": "NO",
            "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
            "INDODAX_EXECUTION_MODE": "DRY_RUN",
            "M05_TELEGRAM_TICK_GO": "YES",
            "M05_TELEGRAM_NETWORK_GO": "YES",
            "M05_TELEGRAM_DEDICATED_GO": "YES",
            "M05_TELEGRAM_FAST_LONGPOLL_GO": "YES",
            "M05_TELEGRAM_DEDICATED_BOT_ID": str(BOT),
            "M05_TELEGRAM_OWNER_ID": str(OWNER),
            "M05_TELEGRAM_OWNER_DB": str(self.db),
            "M05_TELEGRAM_TOKEN_FILE": str(self.key_file),
        }
        self.calls = []
        self.sent = []
        self.mode_button = None
        self.bot_api = TelegramBotApiClient(
            token=TOKEN, transport=self.transport,
            timeout_seconds=9,
        )

    def data(self, method, payload):
        self.calls.append((method, dict(payload)))
        if method == "getMe":
            return {"id": BOT, "is_bot": True, "username": "pitidenbot"}
        if method == "getWebhookInfo":
            return {"url": "", "pending_update_count": 0}
        if method == "getUpdates":
            self.loop_clock += self.poll_elapsed
            self.assertEqual(payload["timeout"], 2)
            offset = payload["offset"]
            if offset <= 1:
                return [{
                    "update_id": 1,
                    "message": {
                        "from": {"id": OWNER},
                        "chat": {"id": OWNER, "type": "private"},
                        "date": NOW, "text": "/kontrol",
                    },
                }]
            if offset == 2 and self.mode_button is not None:
                return [{
                    "update_id": 2,
                    "callback_query": {
                        "id": "fast-choice-auto",
                        "from": {"id": OWNER},
                        "data": self.mode_button,
                        "message": {
                            "message_id": 501,
                            "date": NOW,
                            "from": {"id": BOT, "is_bot": True},
                            "chat": {"id": OWNER, "type": "private"},
                        },
                    },
                }]
            if offset == 3:
                return [{
                    "update_id": 3,
                    "message": {
                        "from": {"id": OWNER},
                        "chat": {"id": OWNER, "type": "private"},
                        "date": NOW, "text": "/status",
                    },
                }]
            return []
        if method == "sendMessage":
            self.sent.append(dict(payload))
            if "reply_markup" in payload:
                self.mode_button = payload["reply_markup"][
                    "inline_keyboard"][0][1]["callback_data"]
                return {"message_id": 501,
                        "chat": {"id": OWNER, "type": "private"}}
            return {"message_id": 501 + len(self.sent),
                    "chat": {"id": OWNER, "type": "private"}}
        if method in ("answerCallbackQuery", "editMessageReplyMarkup"):
            return True
        raise AssertionError("UNKNOWN_FAKE_BOT_METHOD")

    def transport(self, method, payload, _timeout):
        return {"ok": True, "result": self.data(method, payload)}

    def test_existing_owner_poller_wakes_on_update_then_reply_same_batch(self):
        report = dedicated_owner_fast_loop_nonlive(
            api=self.bot_api, ledger=self.owner,
            expected_bot_id=BOT, lock_path=self.lock,
            max_batches=3, wait_seconds=2,
        )
        self.assertEqual(report["status"], "M05_FAST_LOOP_EXITED_NONLIVE")
        self.assertEqual(report["batches"], 3)
        self.assertEqual(report["handled"], 3)
        self.assertEqual(report["next_offset"], 4)
        self.assertFalse(report["execution_allowed"])
        self.assertFalse(report["order_sent"])
        self.assertEqual(self.owner.status(
            user_id=OWNER, chat_id=OWNER)["owner_mode"], "AUTO")
        self.assertEqual([
            method for method, _ in self.calls
            if method == "getUpdates"], ["getUpdates"] * 3)
        self.assertTrue(all(
            payload["timeout"] == 2 for method, payload in self.calls
            if method == "getUpdates"
        ))
        self.assertEqual([
            method for method, _ in self.calls
            if method == "getMe"], ["getMe"])
        self.assertIn("answerCallbackQuery",
                      [method for method, _ in self.calls])
        self.assertEqual(len(self.sent), 3)
        self.assertNotIn(TOKEN, str(report))
        self.assertTrue(all("order" not in method.lower()
                            for method, _ in self.calls))

    def test_fast_loop_reuses_cursor_after_restart_no_double_reply(self):
        first = dedicated_owner_fast_loop_nonlive(
            api=self.bot_api, ledger=self.owner,
            expected_bot_id=BOT, lock_path=self.lock,
            max_batches=3,
        )
        self.assertEqual(first["handled"], 3)
        self.calls.clear()
        self.sent.clear()
        second = dedicated_owner_fast_loop_nonlive(
            api=self.bot_api,
            ledger=OwnerDecisionLedger(self.db, owner_user_id=OWNER),
            expected_bot_id=BOT, lock_path=self.lock,
            max_batches=2,
        )
        self.assertEqual(second["handled"], 0)
        self.assertEqual(second["next_offset"], 4)
        self.assertEqual(self.sent, [])

    def test_only_one_same_bot_consumer_holds_original_lock(self):
        with dedicated_poller_lock(self.lock):
            with self.assertRaisesRegex(
                TelegramTransportError, "ALREADY_RUNNING"
            ):
                dedicated_owner_fast_loop_nonlive(
                    api=self.bot_api, ledger=self.owner,
                    expected_bot_id=BOT, lock_path=self.lock,
                    max_batches=1,
                )
        self.assertEqual(self.calls, [])

    def test_fast_go_and_trading_off_required_before_network(self):
        with self.assertRaisesRegex(
            ConfigurationError, "M05_FAST_LONGPOLL_GO_REQUIRED"
        ):
            service_fast_loop_nonlive(
                env={**self.env, "M05_TELEGRAM_FAST_LONGPOLL_GO": "NO"},
                max_batches=1,
            )
        for change in (
            {"INDODAX_TRADING_ENABLED": "YES"},
            {"INDODAX_EXECUTION_MODE": "LIVE"},
            {"M05_TELEGRAM_NETWORK_GO": "NO"},
        ):
            with self.subTest(change=change), self.assertRaises(
                    ConfigurationError):
                service_fast_loop_nonlive(
                    env={**self.env, **change}, max_batches=1,
                )
        self.assertEqual(self.calls, [])

    def test_service_fast_entry_uses_existing_secure_source_and_worker(self):
        with patch.object(
            TelegramBotApiClient, "call",
            side_effect=lambda method, payload: self.data(method, payload),
        ):
            report = service_fast_loop_nonlive(
                env=self.env, max_batches=3,
            )
        self.assertEqual(report["handled"], 3)
        self.assertEqual(self.owner.telegram_next_offset(), 4)
        self.assertFalse(report["execution_allowed"])
        self.assertEqual([
            method for method, payload in self.calls if method == "getUpdates"
        ], ["getUpdates"] * 3)
        self.assertNotIn(TOKEN, json.dumps(report))

    def test_bad_wait_and_batch_limit_fail_without_network(self):
        for wait in (0, 6, True, "2"):
            with self.subTest(wait=wait), self.assertRaises(ValueError):
                dedicated_owner_fast_loop_nonlive(
                    api=self.bot_api, ledger=self.owner,
                    expected_bot_id=BOT, lock_path=self.lock,
                    wait_seconds=wait, max_batches=1,
                )
        for count in (0, -1, True, 201):
            with self.subTest(count=count), self.assertRaises(ValueError):
                dedicated_owner_fast_loop_nonlive(
                    api=self.bot_api, ledger=self.owner,
                    expected_bot_id=BOT, lock_path=self.lock,
                    max_batches=count,
                )
        self.assertEqual(self.calls, [])
        poller = TelegramOwnerPoller(api=self.bot_api, ledger=self.owner)
        with self.assertRaisesRegex(ValueError, "INVALID_LONGPOLL_WAIT"):
            poller.poll_once(wait_seconds=-1)

    def test_webhook_conflict_blocks_before_getupdates(self):
        def hooked(method, payload, timeout):
            self.calls.append((method, payload))
            if method == "getMe":
                row = {"id": BOT, "is_bot": True, "username": "pitidenbot"}
            elif method == "getWebhookInfo":
                row = {"url": "https://webhook.example.invalid/connected",
                       "pending_update_count": 0}
            else:
                raise AssertionError("NO_GETUPDATES_WITH_WEBHOOK")
            return {"ok": True, "result": row}
        api = TelegramBotApiClient(token=TOKEN, transport=hooked)
        with self.assertRaisesRegex(
                TelegramTransportError, "WEBHOOK_PRESENT"
        ):
            dedicated_owner_fast_loop_nonlive(
                api=api, ledger=self.owner,
                expected_bot_id=BOT, lock_path=self.lock, max_batches=1,
            )
        self.assertEqual([m for m, _ in self.calls],
                         ["getMe", "getWebhookInfo"])

    def test_stop_and_periodic_identity_rechecks(self):
        stopped = dedicated_owner_fast_loop_nonlive(
            api=self.bot_api, ledger=self.owner,
            expected_bot_id=BOT, lock_path=self.lock,
            stop_requested=lambda: True, max_batches=3,
        )
        self.assertEqual(stopped["batches"], 0)
        self.assertEqual([m for m, _ in self.calls],
                         ["getMe", "getWebhookInfo"])
        self.calls.clear()
        # A no-updates source tests 16 iterations without any fake owner
        # commands. Periodic preflight after the 15th cycle must occur.
        def empty(method, payload, timeout):
            self.calls.append((method, payload))
            if method == "getMe":
                out = {"id": BOT, "is_bot": True, "username": "pitidenbot"}
            elif method == "getWebhookInfo":
                out = {"url": "", "pending_update_count": 0}
            elif method == "getUpdates":
                self.assertEqual(payload["timeout"], 2)
                out = []
            else:
                raise AssertionError("UNEXPECTED_IDLE_API")
            return {"ok": True, "result": out}
        empty_api = TelegramBotApiClient(token=TOKEN, transport=empty)
        report = dedicated_owner_fast_loop_nonlive(
            api=empty_api, ledger=self.owner,
            expected_bot_id=BOT, lock_path=self.lock,
            max_batches=16,
        )
        self.assertEqual(report["handled"], 0)
        self.assertEqual(
            [m for m, _ in self.calls].count("getMe"), 2,
        )
        self.assertEqual(
            [m for m, _ in self.calls].count("getUpdates"), 16,
        )


    def test_readonly_identity_preflight_transport_retries_before_getupdates(self):
        called = []
        failure_budget = {"getMe": 1, "getWebhookInfo": 1}

        def flaky(method, payload, _timeout):
            called.append(method)
            if method in failure_budget and failure_budget[method]:
                failure_budget[method] -= 1
                raise TimeoutError("synthetic private transport failure")
            if method == "getMe":
                return {"ok": True, "result": {
                    "id": BOT, "is_bot": True, "username": "pitidenbot",
                }}
            if method == "getWebhookInfo":
                return {"ok": True, "result": {
                    "url": "", "pending_update_count": 0,
                }}
            if method == "getUpdates":
                return {"ok": True, "result": []}
            raise AssertionError("READONLY_PREFLIGHT_MUST_NOT_SEND")

        api = TelegramBotApiClient(token=TOKEN, transport=flaky)
        with patch("indodax_ai_bot.telegram_dedicated_inbound.time.sleep") as sleep:
            out = dedicated_owner_fast_loop_nonlive(
                api=api, ledger=self.owner, expected_bot_id=BOT,
                lock_path=self.lock, max_batches=1,
            )
        self.assertEqual(out["readonly_preflight_transport_failures"], 2)
        self.assertEqual(out["batches"], 1)
        self.assertEqual(called.count("getMe"), 3)
        self.assertEqual(called.count("getWebhookInfo"), 2)
        self.assertEqual(called.count("getUpdates"), 1)
        self.assertEqual([x.args[0] for x in sleep.call_args_list], [1, 2])
        self.assertFalse(out["execution_allowed"])
        self.assertNotIn("sendMessage", called)

    def test_readonly_preflight_three_transport_failures_abort_without_poll(self):
        called = []

        def unreachable(method, payload, _timeout):
            called.append(method)
            if method != "getMe":
                raise AssertionError("PREFLIGHT_CANNOT_POLL_OR_SEND")
            raise TimeoutError("synthetic transport secret")

        api = TelegramBotApiClient(token=TOKEN, transport=unreachable)
        offset = self.owner.telegram_next_offset()
        with patch("indodax_ai_bot.telegram_dedicated_inbound.time.sleep") as sleep:
            report = dedicated_owner_fast_loop_nonlive(
                api=api, ledger=self.owner, expected_bot_id=BOT,
                lock_path=self.lock, max_batches=1,
                stop_requested=lambda: len(called) >= 3,
            )
        self.assertEqual(called, ["getMe"] * 3)
        self.assertEqual([x.args[0] for x in sleep.call_args_list], [1, 2, 5])
        self.assertEqual(report["status"], "M05_FAST_LOOP_STOPPED_NONLIVE")
        self.assertEqual(report["batches"], 0)
        self.assertEqual(self.owner.telegram_next_offset(), offset)
        self.assertFalse(report["execution_allowed"])

    def test_invalid_bot_identity_never_retried(self):
        called = []

        def mismatch(method, payload, _timeout):
            called.append(method)
            if method == "getMe":
                return {"ok": True, "result": {
                    "id": BOT + 1, "is_bot": True, "username": "pitidenbot",
                }}
            raise AssertionError("IDENTITY_FAILURE_MUST_ABORT")

        api = TelegramBotApiClient(token=TOKEN, transport=mismatch)
        with patch("indodax_ai_bot.telegram_dedicated_inbound.time.sleep") as sleep:
            with self.assertRaisesRegex(
                TelegramTransportError, "BOT_ID_MISMATCH",
            ):
                dedicated_owner_fast_loop_nonlive(
                    api=api, ledger=self.owner, expected_bot_id=BOT,
                    lock_path=self.lock, max_batches=1,
                )
        self.assertEqual(called, ["getMe"])
        sleep.assert_not_called()

    def test_getupdates_transport_transient_retry_does_not_send_or_ack(self):
        calls = []

        def transient(method, payload, _timeout):
            calls.append(method)
            if method == "getMe":
                return {"ok": True, "result": {
                    "id": BOT, "is_bot": True, "username": "pitidenbot",
                }}
            if method == "getWebhookInfo":
                return {"ok": True, "result": {
                    "url": "", "pending_update_count": 0,
                }}
            if method == "getUpdates":
                if calls.count("getUpdates") < 3:
                    raise TimeoutError("transient simulated network outage")
                return {"ok": True, "result": []}
            raise AssertionError("UNEXPECTED_EFFECT_METHOD")

        api = TelegramBotApiClient(token=TOKEN, transport=transient)
        offset = self.owner.telegram_next_offset()
        with patch(
            "indodax_ai_bot.telegram_dedicated_inbound.time.sleep"
        ) as sleeping:
            report = dedicated_owner_fast_loop_nonlive(
                api=api, ledger=self.owner, expected_bot_id=BOT,
                lock_path=self.lock, max_batches=1,
            )
        self.assertEqual(report["batches"], 1)
        self.assertEqual(report["handled"], 0)
        self.assertEqual(report["getupdates_transport_failures"], 2)
        self.assertEqual(report["getupdates_consecutive_failures"], 0)
        self.assertEqual(calls.count("getUpdates"), 3)
        self.assertEqual([x.args[0] for x in sleeping.call_args_list], [1, 2])
        self.assertEqual(self.owner.telegram_next_offset(), offset)
        self.assertNotIn("sendMessage", calls)
        self.assertNotIn("answerCallbackQuery", calls)
        self.assertFalse(report["execution_allowed"])

    def test_getupdates_exhaustion_fails_closed_after_three_reads(self):
        calls = []

        def down(method, payload, _timeout):
            calls.append(method)
            if method == "getMe":
                result = {"id": BOT, "is_bot": True, "username": "pitidenbot"}
            elif method == "getWebhookInfo":
                result = {"url": "", "pending_update_count": 0}
            elif method == "getUpdates":
                raise TimeoutError("simulated read failure")
            else:
                raise AssertionError("UNEXPECTED_EFFECT_METHOD")
            return {"ok": True, "result": result}

        api = TelegramBotApiClient(token=TOKEN, transport=down)
        offset = self.owner.telegram_next_offset()
        with patch("indodax_ai_bot.telegram_dedicated_inbound.time.sleep") as sleeper:
            report = dedicated_owner_fast_loop_nonlive(
                api=api, ledger=self.owner, expected_bot_id=BOT,
                lock_path=self.lock, max_batches=1,
                stop_requested=lambda: calls.count("getUpdates") >= 3,
            )
        self.assertEqual(calls.count("getUpdates"), 3)
        self.assertEqual([x.args[0] for x in sleeper.call_args_list], [1, 2, 4])
        self.assertEqual(report["getupdates_transport_failures"], 3)
        self.assertEqual(report["batches"], 0)
        self.assertEqual(self.owner.telegram_next_offset(), offset)
        self.assertNotIn("sendMessage", calls)

    def test_outage_longer_than_three_getupdates_reads_recovers_same_poller(self):
        from types import SimpleNamespace
        calls = []
        def flaky(method, payload, _timeout):
            calls.append(method)
            if method == "getMe":
                result = {"id": BOT, "is_bot": True, "username": "pitidenbot"}
            elif method == "getWebhookInfo":
                result = {"url": "", "pending_update_count": 0}
            elif method == "getUpdates":
                if calls.count("getUpdates") <= 5:
                    raise TimeoutError("private simulated outage")
                result = []
            else:
                raise AssertionError("NO_EFFECTS_DURING_NETWORK_OUTAGE")
            return {"ok": True, "result": result}
        api = TelegramBotApiClient(token=TOKEN, transport=flaky)
        offset = self.owner.telegram_next_offset()
        with patch("indodax_ai_bot.telegram_dedicated_inbound.time.sleep") as pause:
            result = dedicated_owner_fast_loop_nonlive(
                api=api, ledger=self.owner, expected_bot_id=BOT,
                lock_path=self.lock, max_batches=1,
            )
        self.assertEqual(result["batches"], 1)
        self.assertEqual(result["getupdates_transport_failures"], 5)
        self.assertEqual(result["getupdates_consecutive_failures"], 0)
        self.assertEqual(calls.count("getUpdates"), 6)
        self.assertEqual([x.args[0] for x in pause.call_args_list],
                         [1, 2, 4, 8, 16])
        self.assertEqual(self.owner.telegram_next_offset(), offset)
        self.assertNotIn("sendMessage", calls)
        self.assertFalse(result["execution_allowed"])

    def test_unavailable_readonly_preflight_recovers_without_polling_while_down(self):
        calls = []
        def flaky(method, payload, _timeout):
            calls.append(method)
            if method == "getMe":
                if calls.count("getMe") <= 3:
                    raise TimeoutError("private synthetic transport outage")
                result = {"id": BOT, "is_bot": True, "username": "pitidenbot"}
            elif method == "getWebhookInfo":
                result = {"url": "", "pending_update_count": 0}
            elif method == "getUpdates":
                result = []
            else:
                raise AssertionError("NO_EFFECTS_DURING_PREFLIGHT_OUTAGE")
            return {"ok": True, "result": result}
        api = TelegramBotApiClient(token=TOKEN, transport=flaky)
        with patch("indodax_ai_bot.telegram_dedicated_inbound.time.sleep") as pause:
            result = dedicated_owner_fast_loop_nonlive(
                api=api, ledger=self.owner, expected_bot_id=BOT,
                lock_path=self.lock, max_batches=1,
            )
        self.assertEqual(result["batches"], 1)
        self.assertEqual(result["readonly_preflight_transport_failures"], 3)
        self.assertEqual(calls.count("getMe"), 4)
        self.assertEqual(calls.count("getUpdates"), 1)
        self.assertEqual([x.args[0] for x in pause.call_args_list], [1, 2, 5])
        self.assertNotIn("sendMessage", calls)
        self.assertFalse(result["execution_allowed"])

    def test_failed_owner_send_is_never_replayed_as_getupdates_retry(self):
        calls = []

        def failed_send(method, payload, _timeout):
            calls.append(method)
            if method == "sendMessage":
                raise TimeoutError("uncertain synthetic send outcome")
            return {"ok": True, "result": self.data(method, payload)}

        api = TelegramBotApiClient(token=TOKEN, transport=failed_send)
        with patch("indodax_ai_bot.telegram_dedicated_inbound.time.sleep") as sleeper:
            with self.assertRaisesRegex(
                TelegramTransportError, "M05_TELEGRAM_API_TRANSPORT_FAILED",
            ):
                dedicated_owner_fast_loop_nonlive(
                    api=api, ledger=self.owner, expected_bot_id=BOT,
                    lock_path=self.lock, max_batches=2,
                )
        self.assertEqual(calls.count("getUpdates"), 1)
        self.assertEqual(calls.count("sendMessage"), 1)
        self.assertEqual(sleeper.call_count, 0)

    def test_radar_starts_on_first_completed_poll_not_tenth(self):
        calls = []
        events = []

        def radar(*, api, ledger):
            self.assertIs(api, self.bot_api)
            self.assertIs(ledger, self.owner)
            calls.append(True)
            return {"status": "M05_RADAR_NO_CURRENT_QUALIFIED_WATCH",
                    "messages_sent": 0}

        report = dedicated_owner_fast_loop_nonlive(
            api=self.bot_api, ledger=self.owner,
            expected_bot_id=BOT, lock_path=self.lock,
            max_batches=1, auto_radar_alert=radar,
            heartbeat=events.append,
        )
        self.assertEqual(report["radar_invocations"], 1)
        self.assertEqual(calls, [True])
        self.assertEqual(events[0]["batches"], 1)
        from indodax_ai_bot.m10_m05_heartbeat_window_readonly import (
            assess_m05_heartbeat_window,
        )
        audit = assess_m05_heartbeat_window([{
            "MESSAGE": json.dumps(events[0]),
            "__REALTIME_TIMESTAMP": str(NOW * 1000000),
            "_SYSTEMD_INVOCATION_ID": "a" * 32,
        }], now_epoch=NOW)
        self.assertEqual(audit["sample_count"], 1)
        self.assertEqual(audit["rejected_field_counts"], {})
        self.assertFalse(audit["invalid_or_untrusted_records"])
        self.assertFalse(audit["continuous_24h_heartbeat_verified"])
        self.assertFalse(report["order_sent"])
        self.assertFalse(report["execution_allowed"])

    def test_radar_ack_accounted_for_in_original_existing_heartbeat(self):
        events = []
        radar_calls = []

        def radar(*, api, ledger):
            self.assertIs(api, self.bot_api)
            self.assertIs(ledger, self.owner)
            radar_calls.append(True)
            first = len(radar_calls) == 1
            return {
                "status": ("M05_RADAR_OWNER_PRIVATE_NOTICE_SENT" if first
                           else "M05_RADAR_NO_CURRENT_QUALIFIED_WATCH"),
                "messages_sent": 1 if first else 0,
                "execution_allowed": False,
            }

        result = dedicated_owner_fast_loop_nonlive(
            api=self.bot_api, ledger=self.owner,
            expected_bot_id=BOT, lock_path=self.lock,
            max_batches=30, heartbeat=events.append,
            auto_radar_alert=radar,
        )
        self.assertEqual(len(radar_calls), 2)
        self.assertEqual(len(events), 5)
        self.assertEqual(events[0]["batches"], 1)
        self.assertEqual(events[0]["radar_send_acks"], 1)
        self.assertEqual(result["radar_send_acks"], 1)
        self.assertEqual(result["radar_invocations"], 2)
        self.assertEqual(result["radar_delivery_uncertain"], 0)
        self.assertTrue(result["radar_enabled"])
        self.assertEqual(
            events[0]["radar_last_status"],
            "M05_RADAR_OWNER_PRIVATE_NOTICE_SENT",
        )
        self.assertEqual(events[-1]["radar_last_status"],
                         "M05_RADAR_NO_CURRENT_QUALIFIED_WATCH")
        self.assertEqual(events[-1]["radar_send_acks"], 1)
        self.assertEqual(events[-1]["execution_allowed"], False)
        self.assertFalse(result["order_sent"])
        self.assertNotIn(TOKEN, json.dumps(events))

    def test_optional_radar_failure_does_not_terminate_original_receiver(self):
        events = []

        def broken_radar(**_):
            raise RuntimeError("synthetic secret must never leak")

        result = dedicated_owner_fast_loop_nonlive(
            api=self.bot_api, ledger=self.owner,
            expected_bot_id=BOT, lock_path=self.lock,
            max_batches=30, heartbeat=events.append,
            auto_radar_alert=broken_radar,
        )
        self.assertEqual(result["batches"], 30)
        self.assertEqual(result["radar_invocations"], 2)
        self.assertEqual(result["radar_delivery_uncertain"], 2)
        self.assertEqual(result["radar_send_acks"], 0)
        self.assertEqual(
            events[-1]["radar_last_status"], "M05_RADAR_CALLBACK_FAILED"
        )
        self.assertNotIn("synthetic secret", json.dumps(events))
        self.assertFalse(result["order_sent"])
        self.assertFalse(result["execution_allowed"])

    def test_m10_optional_alert_crash_does_not_block_original_radar(self):
        events = []
        called = []

        def broken_ops(*, api, ledger):
            self.assertIs(api, self.bot_api)
            self.assertIs(ledger, self.owner)
            called.append("ops")
            raise RuntimeError("synthetic confidential exception")

        def working_radar(*, api, ledger):
            self.assertIs(api, self.bot_api)
            self.assertIs(ledger, self.owner)
            called.append("radar")
            first = called.count("radar") == 1
            return {
                "status": ("M05_RADAR_OWNER_PRIVATE_NOTICE_SENT" if first
                           else "M05_RADAR_NO_CURRENT_QUALIFIED_WATCH"),
                "messages_sent": 1 if first else 0,
                "execution_allowed": False,
            }

        report = dedicated_owner_fast_loop_nonlive(
            api=self.bot_api, ledger=self.owner,
            expected_bot_id=BOT, lock_path=self.lock,
            max_batches=31, heartbeat=events.append,
            auto_ops_alert=broken_ops,
            auto_radar_alert=working_radar,
        )
        self.assertEqual(called, ["radar", "radar", "ops", "radar"])
        self.assertEqual(report["batches"], 31)
        self.assertEqual(report["ops_alert_failures"], 1)
        self.assertEqual(report["radar_invocations"], 3)
        self.assertEqual(report["radar_send_acks"], 1)
        self.assertEqual(events[-1]["ops_alert_failures"], 1)
        self.assertEqual(
            events[0]["radar_last_status"],
            "M05_RADAR_OWNER_PRIVATE_NOTICE_SENT",
        )
        self.assertEqual(events[-1]["radar_last_status"],
                         "M05_RADAR_NO_CURRENT_QUALIFIED_WATCH")
        self.assertNotIn("synthetic confidential exception", str(events))
        self.assertFalse(report["execution_allowed"])
        self.assertFalse(report["order_sent"])

    def test_fast_optin_dropin_does_not_modify_legacy_timer(self):
        root = Path(__file__).resolve().parents[1]
        original = (root / "systemd/user"
                    / "indodax-v2-m05-telegram.service").read_text()
        timer = (root / "systemd/user"
                 / "indodax-v2-m05-telegram.timer").read_text()
        optin = (root / "systemd/user"
                 / "indodax-v2-m05-telegram.service.d"
                 / "fast-longpoll.conf").read_text()
        self.assertIn("Type=oneshot", original)
        self.assertIn("OnUnitInactiveSec=3s", timer)
        self.assertIn("Type=simple", optin)
        self.assertIn("M05_TELEGRAM_FAST_LONGPOLL_GO=YES", optin)
        self.assertIn("Restart=on-failure", optin)
        self.assertIn("[Install]", optin)
        self.assertIn("WantedBy=default.target", optin)
        self.assertIn("is-active --quiet indodax-v2-m05-telegram.timer",
                      optin)
        self.assertNotIn(TOKEN, original + timer + optin)

    def test_radar_slow_polls_use_elapsed_time_not_thirty_batches(self):
        self.poll_elapsed = 45
        checked = []
        events = []

        def radar(**_):
            checked.append(self.loop_clock)
            return {"status": "M05_RADAR_NO_CURRENT_QUALIFIED_WATCH",
                    "messages_sent": 0}

        report = dedicated_owner_fast_loop_nonlive(
            api=self.bot_api, ledger=self.owner,
            expected_bot_id=BOT, lock_path=self.lock,
            max_batches=3, auto_radar_alert=radar, heartbeat=events.append,
        )
        self.assertEqual(checked, [45, 90, 135])
        self.assertEqual([e["batches"] for e in events], [1, 2, 3])
        self.assertEqual(report["radar_invocations"], 3)
        self.assertFalse(report["execution_allowed"])

    def test_many_immediate_updates_do_not_accelerate_radar(self):
        self.poll_elapsed = 0
        checked = []

        def radar(**_):
            checked.append(self.loop_clock)
            return {"status": "M05_RADAR_NO_CURRENT_QUALIFIED_WATCH",
                    "messages_sent": 0}

        dedicated_owner_fast_loop_nonlive(
            api=self.bot_api, ledger=self.owner,
            expected_bot_id=BOT, lock_path=self.lock,
            max_batches=100, auto_radar_alert=radar,
        )
        self.assertEqual(checked, [0])

    def test_slow_failed_callback_waits_from_completion_without_catchup(self):
        self.poll_elapsed = 20
        checked = []

        def radar(**_):
            checked.append(self.loop_clock)
            self.loop_clock += 180
            raise RuntimeError("do not log this synthetic token")

        events = []
        report = dedicated_owner_fast_loop_nonlive(
            api=self.bot_api, ledger=self.owner,
            expected_bot_id=BOT, lock_path=self.lock,
            max_batches=4, auto_radar_alert=radar, heartbeat=events.append,
        )
        self.assertEqual(checked, [20, 240])
        self.assertEqual(report["batches"], 4)
        self.assertEqual(report["radar_delivery_uncertain"], 2)
        self.assertEqual(report["radar_send_acks"], 0)
        self.assertNotIn("synthetic token", json.dumps(events))

    def test_stop_before_first_poll_prevents_radar_callback(self):
        with patch("indodax_ai_bot.telegram_dedicated_inbound."
                   "_send_one_queued_market_advisory") as outbox:
            radar = unittest.mock.Mock()
            report = dedicated_owner_fast_loop_nonlive(
                api=self.bot_api, ledger=self.owner,
                expected_bot_id=BOT, lock_path=self.lock,
                stop_requested=lambda: True, auto_radar_alert=radar,
            )
        radar.assert_not_called()
        outbox.assert_not_called()
        self.assertEqual(report["radar_invocations"], 0)

    def test_original_service_radar_optin_off_makes_no_callback(self):
        with patch.object(TelegramBotApiClient, "call", side_effect=self.data), \
                patch("indodax_ai_bot.m05_radar_momentum_nonlive."
                      "send_one_owner_radar_momentum_nonlive") as radar:
            report = service_fast_loop_nonlive(env=self.env, max_batches=31)
        radar.assert_not_called()
        self.assertFalse(report["radar_enabled"])
        self.assertEqual(report["radar_invocations"], 0)

    def test_original_service_first_poll_real_sender_and_restart_dedup(self):
        # Real original M03-shaped SQLite -> real radar -> original owner
        # ledger -> fake Bot API ACK. Only network and source path/time are
        # substituted; no duplicate sender or invented LIVE acceptance.
        from indodax_ai_bot.m05_radar_momentum_nonlive import (
            send_one_owner_radar_momentum_nonlive,
        )
        source = self.state / "decisions.sqlite3"
        with sqlite3.connect(source) as db:
            db.executescript("""
                CREATE TABLE public_ticker_minute_samples (
                    pair TEXT, minute_epoch INTEGER, observed_epoch INTEGER,
                    ticker_epoch INTEGER, last_idr TEXT,
                    volume_24h_idr TEXT, spread_bps TEXT,
                    PRIMARY KEY(pair, minute_epoch));
                CREATE TABLE public_surge_cases (
                    pair TEXT, minute_epoch INTEGER, cohort TEXT);
            """)
            for i in range(6):
                db.execute("INSERT INTO public_ticker_minute_samples "
                           "VALUES (?,?,?,?,?,?,?)", (
                               "uai_idr", NOW // 60 * 60 - i * 60,
                               NOW - i * 60, NOW - i * 60,
                               str(120 - i * 4), "500000000", "20"))
        source.chmod(0o600)
        events = []

        def original_sender(*, api, ledger):
            return send_one_owner_radar_momentum_nonlive(
                api=api, ledger=ledger, db_path=source, now_epoch=NOW,
            )

        with patch.object(TelegramBotApiClient, "call", side_effect=self.data), \
                patch("indodax_ai_bot.m05_radar_momentum_nonlive."
                      "send_one_owner_radar_momentum_nonlive",
                      side_effect=original_sender):
            first = service_fast_loop_nonlive(
                env={**self.env, "M05_RADAR_MOMENTUM_ALERT_GO": "YES"},
                max_batches=1, heartbeat=events.append,
            )
            restarted = service_fast_loop_nonlive(
                env={**self.env, "M05_RADAR_MOMENTUM_ALERT_GO": "YES"},
                max_batches=1, heartbeat=events.append,
            )
        radar_messages = [m for m in self.sent if "RADAR" in m["text"]]
        self.assertEqual(len(radar_messages), 1)
        self.assertEqual(radar_messages[0]["chat_id"], OWNER)
        self.assertNotIn("reply_markup", radar_messages[0])
        self.assertEqual(first["radar_send_acks"], 1)
        self.assertEqual(restarted["radar_send_acks"], 0)
        self.assertEqual(events[0]["batches"], 1)
        self.assertEqual(events[0]["radar_send_acks"], 1)
        self.assertFalse(first["execution_allowed"])
        self.assertFalse(restarted["execution_allowed"])
        self.assertNotIn(TOKEN, json.dumps(events))


if __name__ == "__main__":
    unittest.main()


