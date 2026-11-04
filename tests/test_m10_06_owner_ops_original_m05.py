"""M10-06 actual original M05 owner command to V2 operational read-only classifier.

No Telegram API, private exchange API, paid AI, systemd mutation or order.
"""
from __future__ import annotations

from tempfile import TemporaryDirectory
from pathlib import Path
import unittest
from unittest.mock import patch

from indodax_ai_bot.owner_control import OwnerDecisionLedger
from indodax_ai_bot.telegram_owner import OfflineTelegramOwnerInterface
from indodax_ai_bot.m10_ops_health_readonly import format_v2_ops_owner_message

OWNER = 19281731
NOW = 1793758059


def snapshot():
    return {
        "scope": "ONE_V2_ORIGINAL_WS_SIDECAR_AND_FIXED_USER_UNITS",
        "status": "V2_OPERATIONS_READONLY_OBSERVATION",
        "alert_codes": [
            "BTC_WS_BATCH_INCOMPLETE_OR_INCONSISTENT",
            "BTC_WS_BATCH_REJECTED",
            "BTC_WS_BATCH_STALE",
            "BTC_WS_UNIT_NOT_ACTIVE",
        ],
        "btc_ws_unit_active_metadata_observed": False,
        "m03_timer_disabled_metadata_observed": True,
        "m05_timer_active_metadata_observed": True,
        "btc_ws_last_batch": {
            "last_batch_status": "REJECT",
            "requested_sessions": 10,
            "attempted_sessions": 5,
            "completed_safe_sessions": 4,
            "collector_failure_code": "SECRET_SHOULD_NOT_ECHO",
        },
        "continuous_24h_heartbeat_verified": False,
        "alerts_delivered_to_owner_verified": False,
        "m10_06_full_acceptance": False,
        "service_restart_authorized": False,
        "m03_timer_go_authorized": False,
    }


class OriginalM05V2OpsOwner(unittest.TestCase):
    def setUp(self):
        t = TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.ledger = OwnerDecisionLedger(
            Path(t.name) / "owner.sqlite3", owner_user_id=OWNER
        )
        self.ui = OfflineTelegramOwnerInterface(self.ledger)

    def query(self, text="/ops", *, user_id=OWNER, chat_id=OWNER,
              chat_type="private"):
        return self.ui.handle_command(
            user_id=user_id, chat_id=chat_id, chat_type=chat_type,
            text=text, now_epoch=NOW,
        )

    def assert_review_only(self, reply):
        self.assertFalse(reply["accepted"])
        self.assertFalse(reply["execution_allowed"])
        self.assertNotIn("api_secret", str(reply))
        self.assertNotIn("SECRET_SHOULD_NOT_ECHO", str(reply))

    def test_original_m05_owner_command_renders_four_actual_v2_alerts(self):
        with patch("indodax_ai_bot.m10_ops_health_readonly.assess_v2_operations_readonly",
                   return_value=snapshot()) as assess:
            result = self.query()
        assess.assert_called_once_with(now_epoch=NOW)
        message = result["text"]
        for token in ("BATCH_INCOMPLETE", "BATCH_REJECTED",
                      "BATCH_STALE", "WS_UNIT_INACTIVE",
                      "4/10", "M03 timer: DISABLED",
                      "Trading OFF", "restart/GO tidak diizinkan"):
            self.assertIn(token, message)
        self.assertNotIn("owner_user_id", str(result))
        self.assertLess(len(message), 900)
        self.assert_review_only(result)

    def test_nonowner_and_nonprivate_do_not_probe_v2_systemd(self):
        with patch("indodax_ai_bot.m10_ops_health_readonly.assess_v2_operations_readonly",
                   side_effect=AssertionError("DO_NOT_PROBE")) as assess:
            for args in [
                {"user_id": OWNER + 1},
                {"chat_id": OWNER + 1},
                {"chat_type": "group"},
                {"chat_type": "supergroup"},
            ]:
                with self.subTest(args=args):
                    result = self.query(**args)
                    self.assertFalse(result["accepted"])
            assess.assert_not_called()

    def test_unrecognized_ops_arguments_do_not_probe(self):
        with patch("indodax_ai_bot.m10_ops_health_readonly.assess_v2_operations_readonly",
                   side_effect=AssertionError("DO_NOT_PROBE")) as assess:
            for cmd in ("/ops send", "/ops restart", "/ops live",
                        "/ops now", "/ops start"):
                with self.subTest(cmd=cmd):
                    r = self.query(cmd)
                    self.assertFalse(r["accepted"])
            assess.assert_not_called()

    def test_unknown_probe_returns_fixed_unverified_without_leak(self):
        with patch("indodax_ai_bot.m10_ops_health_readonly.assess_v2_operations_readonly",
                   side_effect=ValueError("api_secret PRIVATETOKEN")):
            reply = self.query()
        self.assertIn("STATUS_UNVERIFIED", reply["text"])
        self.assertNotIn("PRIVATETOKEN", str(reply))
        self.assert_review_only(reply)

    def test_unknown_and_extra_alert_code_cannot_inject_owner_reply(self):
        base = snapshot()
        base["alert_codes"].append("SECRET_SHOULD_NOT_ECHO")
        text = format_v2_ops_owner_message(base)
        self.assertIn("ALERT_DATA_UNVERIFIED", text)
        self.assertNotIn("SECRET_SHOULD_NOT_ECHO", text)
        self.assertNotIn("PASS", text)
        self.assertIn("Trading OFF", text)

    def test_no_success_promotion_from_green_staged_snapshot(self):
        base = snapshot()
        base["alert_codes"] = []
        base["btc_ws_last_batch"]["last_batch_status"] = (
            "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED"
        )
        base["btc_ws_unit_active_metadata_observed"] = True
        txt = format_v2_ops_owner_message(base)
        self.assertIn("snapshot, bukan heartbeat 24 jam", txt)
        self.assertIn("Peringatan belum terbukti terkirim otomatis", txt)
        self.assertIn("Trading OFF", txt)
        self.assertNotIn("READY_TO_TRADE", txt)

    def test_bad_report_does_not_echo_untrusted_string(self):
        for report in (None, {}, {"status": "PRIVATE_SECRET"},
                       {"scope": "BOGUS", "status": "V2_OPERATIONS_READONLY_OBSERVATION"}):
            with self.subTest(report=report):
                txt = format_v2_ops_owner_message(report)
                self.assertIn("STATUS_UNVERIFIED", txt)
                self.assertNotIn("PRIVATE_SECRET", txt)

    def test_owner_command_case_normalized_without_replay_side_effect(self):
        with patch("indodax_ai_bot.m10_ops_health_readonly.assess_v2_operations_readonly",
                   return_value=snapshot()) as assess:
            r1 = self.query("/OPS")
            r2 = self.query("/ops")
        self.assertEqual(r1["text"], r2["text"])
        self.assertEqual(assess.call_count, 2)
        self.assert_review_only(r1)
        self.assert_review_only(r2)


if __name__ == "__main__":
    unittest.main()
