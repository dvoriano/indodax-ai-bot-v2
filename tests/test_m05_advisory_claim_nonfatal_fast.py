"""Original M05 longpoll must survive an uncertain CLAIMED advisory.

Fake Telegram transport and temporary original ledger only. No external IO,
real token, exchange/AI calls, writes to owner VPS, or LIVE trade.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_bot_api import TelegramBotApiClient, TelegramTransportError
from indodax_ai_bot.telegram_dedicated_inbound import dedicated_owner_fast_loop_nonlive


OWNER = 45912007
BOT = 9241907991
TOKEN = str(BOT) + ":" + "A" * 30


class M05AdvisoryClaimNonfatalFastTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="m05-claimed-nonfatal-")
        self.addCleanup(folder.cleanup)
        base = Path(folder.name)
        self.db = base / "owner.sqlite3"
        self.ledger = OwnerDecisionLedger(self.db, owner_user_id=OWNER)
        self.db.chmod(0o600)
        self.lock = str(self.db) + ".dedicated-poller.lock"
        self.calls = []
        self.polls = 0
        self.advisory_sends = 0
        self.status_sends = 0
        self.fail_advisory = False
        self.send_owner_command = True
        self.api = TelegramBotApiClient(token=TOKEN, transport=self.transport)
        now = int(time.time())
        closed = now - now % 60
        proposal = self.ledger.issue_market_advisory_proposal(
            pair="btc_idr", digest="a" * 64,
            closed_1m_epoch=closed, now_epoch=now,
            source_expires_epoch=closed + 90,
            setup="BREAKOUT_CONTINUATION_WATCH",
            market_status="M04_NOT_CONNECTED",
            risk_status="UNVERIFIED_ACCOUNT_AND_G1",
        )
        self.assertTrue(proposal["accepted"], proposal)
        self.proposal_id = proposal["proposal_id"]
        self.now = now

    def transport(self, method, payload, _timeout):
        self.calls.append(method)
        if method == "getMe":
            result = {"id": BOT, "is_bot": True, "username": "pitidenbot"}
        elif method == "getWebhookInfo":
            result = {"url": "", "pending_update_count": 0}
        elif method == "getUpdates":
            self.polls += 1
            if self.polls == 1 and self.send_owner_command:
                result = [{
                    "update_id": 42,
                    "message": {
                        "from": {"id": OWNER},
                        "chat": {"id": OWNER, "type": "private"},
                        "date": self.now, "text": "/status",
                    },
                }]
            else:
                result = []
        elif method == "sendMessage":
            if "M03/M04 public advisory" in payload.get("text", ""):
                self.advisory_sends += 1
                if self.fail_advisory:
                    raise TimeoutError("private synthetic transport error")
            else:
                self.status_sends += 1
            result = {
                "message_id": 501 + self.status_sends + self.advisory_sends,
                "chat": {"id": OWNER, "type": "private"},
            }
        else:
            raise AssertionError("UNEXPECTED_FAKE_BOT_METHOD")
        return {"ok": True, "result": result}

    def run_loop(self, max_batches=3, **kwargs):
        return dedicated_owner_fast_loop_nonlive(
            api=self.api, ledger=self.ledger,
            expected_bot_id=BOT, lock_path=self.lock,
            max_batches=max_batches, **kwargs,
        )

    def test_existing_claimed_advisory_does_not_crash_owner_or_radar(self):
        claimed = self.ledger.claim_market_advisory_delivery(
            now_epoch=self.now,
        )
        self.assertEqual(claimed["proposal_id"], self.proposal_id)
        radar_calls, heartbeats = [], []

        def radar(*, api, ledger):
            radar_calls.append(True)
            self.assertIs(api, self.api)
            self.assertIs(ledger, self.ledger)
            return {"status": "M05_RADAR_NO_CURRENT_QUALIFIED_WATCH",
                    "messages_sent": 0}

        report = self.run_loop(
            max_batches=3, auto_radar_alert=radar,
            heartbeat=heartbeats.append,
        )
        self.assertEqual(report["batches"], 3)
        self.assertEqual(report["handled"], 1)
        self.assertTrue(report["advisory_delivery_review_required"])
        self.assertEqual(report["advisory_delivery_reason"],
                         "M05_ADVISORY_DELIVERY_REVIEW_REQUIRED")
        self.assertEqual(heartbeats[0]["advisory_delivery_review_required"], True)
        self.assertEqual(self.status_sends, 1)
        self.assertEqual(self.advisory_sends, 0)
        self.assertTrue(radar_calls)
        self.assertEqual(self.ledger.market_advisory_delivery_status(
            self.proposal_id), "CLAIMED")
        self.assertEqual(report["execution_allowed"], False)
        self.assertNotIn(TOKEN, str(heartbeats))

        # Persistent CLAIMED across restart stays quarantined, no retry.
        follow = self.run_loop(max_batches=2)
        self.assertEqual(follow["batches"], 2)
        self.assertEqual(follow["advisory_delivery_reason"],
                         "M05_ADVISORY_DELIVERY_REVIEW_REQUIRED")
        self.assertEqual(self.advisory_sends, 0)
        self.assertEqual(self.ledger.market_advisory_delivery_status(
            self.proposal_id), "CLAIMED")

    def test_uncertain_send_is_not_retried_or_acked(self):
        self.fail_advisory = True
        self.send_owner_command = False
        report = self.run_loop(max_batches=3)
        self.assertTrue(report["advisory_delivery_review_required"])
        self.assertEqual(report["advisory_delivery_reason"],
                         "M05_TELEGRAM_API_TRANSPORT_FAILED")
        self.assertEqual(self.advisory_sends, 1)
        self.assertEqual(self.ledger.market_advisory_delivery_status(
            self.proposal_id), "CLAIMED")
        self.assertEqual(report["batches"], 3)
        self.assertEqual(report["execution_allowed"], False)

    def test_unrelated_sender_error_still_fails_closed(self):
        with patch(
            "indodax_ai_bot.telegram_dedicated_inbound."
            "_send_one_queued_market_advisory",
            side_effect=TelegramTransportError(
                "M05_UNRELATED_SENDER_FAILURE"
            ),
        ), self.assertRaisesRegex(
            TelegramTransportError, "M05_UNRELATED_SENDER_FAILURE"
        ):
            self.run_loop(max_batches=1)
        self.assertEqual(self.advisory_sends, 0)


if __name__ == "__main__":
    unittest.main()
