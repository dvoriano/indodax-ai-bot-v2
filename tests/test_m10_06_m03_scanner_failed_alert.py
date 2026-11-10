"""M10-06 original M03 timer-versus-service outage proof, OFFLINE only."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from indodax_ai_bot.m10_ops_health_readonly import (
    assess_v2_operations_readonly, format_v2_ops_owner_message,
)
from indodax_ai_bot.m10_owner_ops_auto_alert import (
    _compact_ops_auto_message, send_one_owner_ops_alert_nonlive,
)
from indodax_ai_bot.owner_control import OwnerDecisionLedger

NOW = 1794190000


def unit(role, active, enabled, trusted=True):
    return {
        "role": role,
        "observed_load": "loaded",
        "observed_active": active,
        "observed_enabled": enabled,
        "v2_user_fragment_path_matches": trusted,
        "unit_needs_daemon_reload": "no",
    }


def units(service_state="failed", timer_active=True, trusted=True):
    return {"units": [
        unit("btc_public_ws", "inactive", "static"),
        unit("btc_public_ws_timer", "active", "enabled"),
        unit("m03_scanner_timer",
             "active" if timer_active else "inactive",
             "enabled" if timer_active else "disabled"),
        unit("m03_scanner", service_state, "static", trusted),
        unit("m05_telegram", "active", "enabled"),
        unit("m05_telegram_timer", "inactive", "disabled"),
    ]}


def btc_clean():
    return {
        "status": "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED",
        "requested_sessions": 10,
        "attempted_sessions": 10,
        "completed_safe_sessions": 10,
        "last_completed_batch_epoch": NOW - 60,
        "restart_only_after_clean_batch": True,
        "collector_failure_code": None,
        "stream_status": "DISCONNECTED",
    }


def assess(**kw):
    return assess_v2_operations_readonly(
        units_report=units(**kw), status_document=btc_clean(),
        now_epoch=NOW,
    )


class FakeApi:
    def __init__(self):
        self.sent = []

    def call(self, method, payload):
        assert method == "sendMessage"
        self.sent.append(payload)
        return {"message_id": 41}


class M10M03ServiceOutageTest(unittest.TestCase):
    def test_active_m03_timer_failed_service_detected_even_btc_10_of_10(self):
        report = assess()
        self.assertEqual(report["alert_codes"], ["M03_SCANNER_SERVICE_FAILED"])
        self.assertTrue(report["m03_timer_active_metadata_observed"])
        self.assertTrue(report["m03_scanner_service_failed_metadata_observed"])
        self.assertTrue(report["btc_ws_scheduled_fresh_metadata_observed"])
        self.assertIn("M03_SCANNER_FAILED", format_v2_ops_owner_message(report))
        compact = _compact_ops_auto_message(report)
        self.assertIn("M03 scanner service failed", compact)
        self.assertIn("SCHEDULED", compact)
        self.assertNotIn("HTTP403", compact)  # metadata cannot explain cause
        self.assertFalse(report["live_trading_authorized"])
        self.assertFalse(report["m10_06_full_acceptance"])

    def test_real_ops_snapshot_propagates_validated_btc_checkpoint_epoch(self):
        # Regression: owner alert digest must use the observed epoch, not 0.
        report = assess()
        self.assertEqual(
            report["btc_ws_last_batch"]["last_completed_batch_epoch"],
            btc_clean()["last_completed_batch_epoch"],
        )
        self.assertEqual(report["alert_codes"], ["M03_SCANNER_SERVICE_FAILED"])

    def test_idle_oneshot_between_ticks_not_a_failure(self):
        report = assess(service_state="inactive")
        self.assertEqual(report["alert_codes"], [])
        self.assertFalse(report["m03_scanner_service_failed_metadata_observed"])

    def test_paused_legacy_timer_not_reported_as_current_outage(self):
        report = assess(timer_active=False)
        self.assertNotIn("M03_SCANNER_SERVICE_FAILED", report["alert_codes"])
        self.assertFalse(report["m03_scanner_service_failed_metadata_observed"])

    def test_untrusted_service_metadata_not_promoted_to_verified_failure(self):
        report = assess(trusted=False)
        self.assertNotIn("M03_SCANNER_SERVICE_FAILED", report["alert_codes"])
        self.assertFalse(report["m03_scanner_service_failed_metadata_observed"])

    def test_original_owner_sender_accepts_new_fixed_code_once(self):
        report = assess()
        with TemporaryDirectory() as tmp:
            ledger = OwnerDecisionLedger(
                Path(tmp) / "owner.sqlite3", owner_user_id=991123,
            )
            fake = FakeApi()
            result = send_one_owner_ops_alert_nonlive(
                api=fake, ledger=ledger, report=report, now_epoch=NOW,
            )
            self.assertEqual(result["messages_sent"], 1)
            self.assertEqual(len(fake.sent), 1)
            self.assertEqual(fake.sent[0]["chat_id"], 991123)
            self.assertIn("M03 scanner service failed", fake.sent[0]["text"])
            self.assertIn("Trading OFF", fake.sent[0]["text"])
            self.assertEqual(send_one_owner_ops_alert_nonlive(
                api=fake, ledger=ledger, report=report, now_epoch=NOW + 4000,
            )["messages_sent"], 0)
            self.assertEqual(len(fake.sent), 1)


if __name__ == "__main__":
    unittest.main()
