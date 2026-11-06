"""M10 scheduled original public BTC collector and standing M03 timer snapshots.

All unit reports and sidecar batches are constructed offline. No systemctl,
Telegram/exchange requests, worker, clock changes or state writes.
"""
from __future__ import annotations

import unittest

from indodax_ai_bot.m10_ops_health_readonly import (
    assess_v2_operations_readonly, format_v2_ops_owner_message,
)
from indodax_ai_bot.m10_owner_ops_auto_alert import _compact_ops_auto_message


NOW = 1794000000


def unit(role, active, enabled):
    return {
        "role": role,
        "observed_load": "loaded",
        "observed_active": active,
        "observed_enabled": enabled,
        "v2_user_fragment_path_matches": True,
        "unit_needs_daemon_reload": "no",
    }


def report_units():
    return {
        "units": [
            unit("btc_public_ws", "inactive", "static"),
            unit("btc_public_ws_timer", "active", "enabled"),
            unit("m03_scanner_timer", "active", "enabled"),
            unit("m05_telegram", "active", "enabled"),
            unit("m05_telegram_timer", "inactive", "disabled"),
        ],
    }


def clean_batch(age=60):
    return {
        "status": "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED",
        "requested_sessions": 10,
        "attempted_sessions": 10,
        "completed_safe_sessions": 10,
        "last_completed_batch_epoch": NOW - age,
        "restart_only_after_clean_batch": True,
        "collector_failure_code": None,
        "stream_status": "DISCONNECTED",
    }


class M10TimerAwareOriginalTests(unittest.TestCase):
    def assess(self, units=None, batch=None):
        return assess_v2_operations_readonly(
            units_report=report_units() if units is None else units,
            status_document=clean_batch() if batch is None else batch,
            now_epoch=NOW,
        )

    def test_clean_btc_between_batches_and_standing_m03_are_not_faults(self):
        r = self.assess()
        self.assertEqual(r["alert_codes"], [])
        self.assertFalse(r["btc_ws_unit_active_metadata_observed"])
        self.assertTrue(r["btc_ws_timer_active_metadata_observed"])
        self.assertTrue(r["btc_ws_scheduled_fresh_metadata_observed"])
        self.assertTrue(r["m03_timer_active_metadata_observed"])
        self.assertFalse(r["m03_timer_disabled_metadata_observed"])
        self.assertFalse(r["continuous_24h_heartbeat_verified"])
        self.assertFalse(r["m10_06_full_acceptance"])
        self.assertFalse(r["live_trading_authorized"])
        detail = format_v2_ops_owner_message(r)
        self.assertIn("TERJADWAL, BATCH TERAKHIR SEGAR", detail)
        self.assertIn("AKTIF TERJADWAL", detail)
        self.assertNotIn("WS_UNIT_INACTIVE", detail)

    def test_disabled_btc_timer_cannot_mask_down_service(self):
        u = report_units()
        u["units"][1] = unit("btc_public_ws_timer", "inactive", "disabled")
        r = self.assess(u)
        self.assertIn("BTC_WS_UNIT_NOT_ACTIVE", r["alert_codes"])
        self.assertFalse(r["btc_ws_scheduled_fresh_metadata_observed"])

    def test_missing_timer_evidence_cannot_mask_down_service(self):
        u = report_units()
        u["units"] = [
            e for e in u["units"] if e["role"] != "btc_public_ws_timer"
        ]
        r = self.assess(u)
        self.assertIn("BTC_WS_UNIT_NOT_ACTIVE", r["alert_codes"])

    def test_stale_or_rejected_batch_never_excused_by_timer(self):
        stale = self.assess(batch=clean_batch(age=3601))
        self.assertIn("BTC_WS_BATCH_STALE", stale["alert_codes"])
        self.assertIn("BTC_WS_UNIT_NOT_ACTIVE", stale["alert_codes"])
        bad = clean_batch()
        bad["status"] = "REJECT"
        bad["completed_safe_sessions"] = 9
        bad["restart_only_after_clean_batch"] = False
        rejected = self.assess(batch=bad)
        self.assertIn("BTC_WS_BATCH_REJECTED", rejected["alert_codes"])
        self.assertIn("BTC_WS_UNIT_NOT_ACTIVE", rejected["alert_codes"])
        self.assertFalse(rejected["btc_ws_scheduled_fresh_metadata_observed"])

    def test_currently_running_btc_is_not_misclassified_as_idle(self):
        u = report_units()
        u["units"][0] = unit("btc_public_ws", "active", "static")
        r = self.assess(u)
        self.assertTrue(r["btc_ws_unit_active_metadata_observed"])
        self.assertFalse(r["btc_ws_scheduled_fresh_metadata_observed"])
        self.assertNotIn("BTC_WS_UNIT_NOT_ACTIVE", r["alert_codes"])

    def test_m03_disabled_legacy_and_conflict_classified_separately(self):
        u = report_units()
        u["units"][2] = unit("m03_scanner_timer", "inactive", "disabled")
        paused = self.assess(u)
        self.assertNotIn("M03_TIMER_NOT_CONFIRMED_DISABLED", paused["alert_codes"])
        u["units"][2] = unit("m03_scanner_timer", "inactive", "enabled")
        conflicted = self.assess(u)
        self.assertIn(
            "M03_TIMER_NOT_CONFIRMED_DISABLED", conflicted["alert_codes"]
        )

    def test_m05_crash_is_still_alerted_even_when_public_source_healthy(self):
        u = report_units()
        u["units"][3] = unit("m05_telegram", "activating", "enabled")
        r = self.assess(u)
        self.assertIn("M05_TIMER_NOT_ACTIVE", r["alert_codes"])
        self.assertNotIn("BTC_WS_UNIT_NOT_ACTIVE", r["alert_codes"])
        self.assertNotIn("M03_TIMER_NOT_CONFIRMED_DISABLED", r["alert_codes"])
        compact = _compact_ops_auto_message(r)
        self.assertIn("TERJADWAL", compact)
        self.assertIn("Receiver M05 perlu diperiksa", compact)


if __name__ == "__main__":
    unittest.main()
