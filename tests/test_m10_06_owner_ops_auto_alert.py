"""M10-06 original M05 opt-in owner alert: OFFLINE, no network or orders."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.m10_owner_ops_auto_alert import (
    send_one_owner_ops_alert_nonlive,
)
from indodax_ai_bot.telegram_bot_api import TelegramTransportError


OWNER = 19281731
NOW = 1793926800


def report(codes=None, epoch=1793920000):
    return {
        "status": "V2_OPERATIONS_READONLY_OBSERVATION",
        "scope": "ONE_V2_ORIGINAL_WS_SIDECAR_AND_FIXED_USER_UNITS",
        "alert_codes": codes if codes is not None else [
            "BTC_WS_BATCH_REJECTED", "BTC_WS_UNIT_NOT_ACTIVE",
        ],
        "btc_ws_last_batch": {
            "last_batch_status": "REJECT",
            "last_completed_batch_epoch": epoch,
            "requested_sessions": 10,
            "completed_safe_sessions": 9,
        },
        "btc_ws_unit_active_metadata_observed": False,
        "m03_timer_disabled_metadata_observed": False,
        "live_trading_authorized": False,
        "m10_06_full_acceptance": False,
    }


class FakeApi:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def call(self, method, payload):
        assert method == "sendMessage"
        self.sent.append(payload)
        if self.fail:
            raise TelegramTransportError("NETWORK_UNCERTAIN")
        return {"message_id": 42}


class OwnerOpsAutoAlertOffline(unittest.TestCase):
    def setUp(self):
        t = TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.ledger = OwnerDecisionLedger(
            Path(t.name) / "owner.sqlite3", owner_user_id=OWNER,
        )

    def send(self, api, snap=None, now=NOW):
        return send_one_owner_ops_alert_nonlive(
            api=api, ledger=self.ledger,
            report=report() if snap is None else snap,
            now_epoch=now,
        )

    def test_first_warning_owner_only_and_readonly(self):
        api = FakeApi()
        result = self.send(api)
        self.assertEqual(result["messages_sent"], 1)
        self.assertEqual(len(api.sent), 1)
        self.assertEqual(api.sent[0]["chat_id"], OWNER)
        self.assertIn("Trading OFF", api.sent[0]["text"])
        self.assertIn("ALERT OTOMATIS M10-06", api.sent[0]["text"])

    def test_automatic_alert_is_compact_and_details_remain_on_ops(self):
        api = FakeApi()
        snapshot = report([
            "BTC_WS_BATCH_STALE",
            "M03_TIMER_NOT_CONFIRMED_DISABLED",
        ])
        self.assertEqual(self.send(api, snapshot)["messages_sent"], 1)
        message = api.sent[0]["text"]
        self.assertIn("Data BTC lama", message)
        self.assertIn("Timer M03 perlu review", message)
        self.assertIn("Detail: /ops", message)
        self.assertIn("Trading OFF", message)
        self.assertIn("snapshot, bukan uptime 24 jam", message)
        self.assertNotIn("Penerimaan owner belum terverifikasi", message)
        self.assertLessEqual(len(message.splitlines()), 6)
        self.assertLess(len(message), 275)

    def test_no_repeat_on_restart_even_after_hour(self):
        self.assertEqual(self.send(FakeApi())["messages_sent"], 1)
        api = FakeApi()
        self.assertEqual(self.send(api, now=NOW + 7200)["messages_sent"], 0)
        self.assertEqual(api.sent, [])

    def test_changed_warning_throttled_then_sent(self):
        self.send(FakeApi())
        changed = report(["BTC_WS_BATCH_STALE"])
        self.assertEqual(self.send(FakeApi(), changed, NOW + 10)["messages_sent"], 0)
        self.assertEqual(self.send(FakeApi(), changed, NOW + 3601)["messages_sent"], 1)

    def test_no_alert_on_clean_or_untrusted_snapshot(self):
        api = FakeApi()
        self.assertEqual(self.send(api, report([]))["messages_sent"], 0)
        corrupted = report()
        corrupted["live_trading_authorized"] = True
        self.assertEqual(self.send(api, corrupted)["messages_sent"], 0)
        corrupted = report(["SECRET_NOT_WHITELISTED"])
        self.assertEqual(self.send(api, corrupted)["messages_sent"], 0)
        self.assertEqual(api.sent, [])

    def test_transport_uncertain_no_retry(self):
        api = FakeApi(fail=True)
        result = self.send(api)
        self.assertEqual(result["status"], "M10_ALERT_DELIVERY_UNCERTAIN_NO_RETRY")
        self.assertEqual(self.send(FakeApi(), now=NOW + 3601)["messages_sent"], 0)


if __name__ == "__main__":
    unittest.main()
