"""M10-06 V2-only read-only ops alert regression, 0 REST/order/service actions."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from indodax_ai_bot.m10_ops_health_readonly import (
    _batch_observation, _read_v2_ws_supervisor_status,
    assess_v2_operations_readonly, main,
)


NOW = 1793781000


def safe_doc(**changes):
    record = {
        "status": "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED",
        "requested_sessions": 10,
        "attempted_sessions": 10,
        "completed_safe_sessions": 10,
        "last_completed_batch_epoch": NOW - 60,
        "restart_only_after_clean_batch": True,
        "collector_failure_code": None,
        "stream_status": "DISCONNECTED",
    }
    record.update(changes)
    return record


def units(**overrides):
    rows = [
        {"role": "btc_public_ws", "observed_load": "loaded",
         "observed_active": "active", "observed_enabled": "enabled",
         "v2_user_fragment_path_matches": True,
         "unit_needs_daemon_reload": "no"},
        {"role": "m05_telegram_timer", "observed_load": "loaded",
         "observed_active": "active", "observed_enabled": "enabled",
         "v2_user_fragment_path_matches": True,
         "unit_needs_daemon_reload": "no"},
        {"role": "m03_scanner_timer", "observed_load": "loaded",
         "observed_active": "inactive", "observed_enabled": "disabled",
         "v2_user_fragment_path_matches": True,
         "unit_needs_daemon_reload": "no"},
    ]
    for role, change in overrides.items():
        for row in rows:
            if row["role"] == role:
                row.update(change)
    return {"units": rows}


class M10OpsReadonlyTests(unittest.TestCase):
    def assert_not_promotion(self, report):
        for key in (
            "continuous_24h_heartbeat_verified",
            "alerts_delivered_to_owner_verified",
            "actual_runtime_source_sha_verified",
            "systemd_guard_root_cause_verified",
            "service_started_or_restarted",
            "service_restart_authorized",
            "m03_timer_go_authorized",
            "m10_06_full_acceptance",
            "live_trading_authorized",
        ):
            self.assertFalse(report[key], key)
        self.assertEqual(report["status"], "V2_OPERATIONS_READONLY_OBSERVATION")

    def test_clean_staged_snapshot_is_never_a_24h_or_go_claim(self):
        r = assess_v2_operations_readonly(
            units_report=units(), status_document=safe_doc(), now_epoch=NOW)
        self.assertEqual(r["alert_codes"], [])
        self.assertTrue(r["btc_ws_unit_active_metadata_observed"])
        self.assertTrue(r["m05_timer_active_metadata_observed"])
        self.assertTrue(r["m03_timer_disabled_metadata_observed"])
        self.assertEqual(r["btc_ws_last_batch"]["completed_safe_sessions"], 10)
        self.assert_not_promotion(r)

    def test_original_v2_rejected_four_of_ten_and_inactive_alerts(self):
        bad = safe_doc(status="REJECT", attempted_sessions=5,
                       completed_safe_sessions=4,
                       collector_failure_code=(
                           "WS_SESSION_REJECTED:PUBLIC_WS_STREAM_RECEIVE_EMPTY_OR_CLOSED"
                       ), restart_only_after_clean_batch=False,
                       last_completed_batch_epoch=NOW-86400)
        r = assess_v2_operations_readonly(
            units_report=units(btc_public_ws={"observed_active": "inactive",
                                              "observed_enabled": "disabled"}),
            status_document=bad, now_epoch=NOW)
        self.assertEqual(r["alert_codes"], sorted([
            "BTC_WS_BATCH_REJECTED",
            "BTC_WS_BATCH_INCOMPLETE_OR_INCONSISTENT",
            "BTC_WS_BATCH_STALE",
            "BTC_WS_UNIT_NOT_ACTIVE",
        ]))
        self.assertEqual(r["btc_ws_last_batch"]["attempted_sessions"], 5)
        self.assertEqual(r["btc_ws_last_batch"]["completed_safe_sessions"], 4)
        self.assertFalse(r["btc_ws_last_batch"]["restart_only_after_clean_batch_observed"])
        self.assertTrue(r["btc_ws_last_batch"]["failure_code_observed"])
        self.assert_not_promotion(r)

    def test_reject_with_restart_true_is_not_clean(self):
        r = assess_v2_operations_readonly(
            units_report=units(), status_document=safe_doc(
                status="REJECT", restart_only_after_clean_batch=True),
            now_epoch=NOW)
        self.assertIn("BTC_WS_BATCH_REJECTED", r["alert_codes"])
        self.assertIn("BTC_WS_REJECT_RESTART_FLAG_CONFLICT", r["alert_codes"])
        self.assert_not_promotion(r)

    def test_clean_status_with_failure_code_is_inconsistent(self):
        r = assess_v2_operations_readonly(
            units_report=units(), status_document=safe_doc(
                collector_failure_code="WS_FAILED"), now_epoch=NOW)
        self.assertIn("BTC_WS_BATCH_INCOMPLETE_OR_INCONSISTENT", r["alert_codes"])
        self.assert_not_promotion(r)

    def test_missing_unparseable_and_unknown_fields_fail_closed(self):
        for item, expected in [
            ({}, "BTC_WS_BATCH_FIELDS_UNTRUSTED"),
            ({"status": ["REJECT"]}, "BTC_WS_BATCH_FIELDS_UNTRUSTED"),
            (safe_doc(status={"REJECT": 1}), "BTC_WS_BATCH_FIELDS_UNTRUSTED"),
            (safe_doc(stream_status=[]), "BTC_WS_BATCH_FIELDS_UNTRUSTED"),
            (safe_doc(completed_safe_sessions=True), "BTC_WS_BATCH_FIELDS_UNTRUSTED"),
            (safe_doc(requested_sessions=20), "BTC_WS_BATCH_FIELDS_UNTRUSTED"),
            (safe_doc(last_completed_batch_epoch="bad"), "BTC_WS_BATCH_FIELDS_UNTRUSTED"),
            (safe_doc(restart_only_after_clean_batch="true"), "BTC_WS_BATCH_FIELDS_UNTRUSTED"),
            (safe_doc(collector_failure_code="SECRET" * 50), "BTC_WS_BATCH_FIELDS_UNTRUSTED"),
        ]:
            with self.subTest(payload=item):
                r = assess_v2_operations_readonly(
                    units_report=units(), status_document=item, now_epoch=NOW)
                self.assertIn(expected, r["alert_codes"])
                self.assertEqual(r["btc_ws_last_batch"]["last_batch_status"], "UNKNOWN")
                self.assert_not_promotion(r)

    def test_future_status_and_stale_threshold(self):
        future = assess_v2_operations_readonly(
            units_report=units(), status_document=safe_doc(
                last_completed_batch_epoch=NOW+1), now_epoch=NOW)
        self.assertIn("BTC_WS_BATCH_FUTURE_CLOCK", future["alert_codes"])
        stale = assess_v2_operations_readonly(
            units_report=units(), status_document=safe_doc(
                last_completed_batch_epoch=NOW-3601), now_epoch=NOW)
        self.assertIn("BTC_WS_BATCH_STALE", stale["alert_codes"])
        recent = assess_v2_operations_readonly(
            units_report=units(), status_document=safe_doc(
                last_completed_batch_epoch=NOW-3600), now_epoch=NOW)
        self.assertNotIn("BTC_WS_BATCH_STALE", recent["alert_codes"])

    def test_longpoll_service_active_and_old_timer_disabled_no_false_alert(self):
        present = units()
        present["units"].append({
            "role": "m05_telegram", "observed_load": "loaded",
            "observed_active": "active", "observed_enabled": "enabled",
            "v2_user_fragment_path_matches": True,
            "unit_needs_daemon_reload": "no",
        })
        for row in present["units"]:
            if row["role"] == "m05_telegram_timer":
                row["observed_active"] = "inactive"
                row["observed_enabled"] = "disabled"
        result = assess_v2_operations_readonly(
            units_report=present, status_document=safe_doc(),
            now_epoch=NOW,
        )
        self.assertNotIn("M05_TIMER_NOT_ACTIVE", result["alert_codes"])
        self.assertFalse(result["m05_timer_active_metadata_observed"])
        self.assertTrue(result["m05_receiver_metadata"]["m05_receiver_metadata_active"])
        self.assertFalse(result["m05_receiver_metadata"]["m05_receiver_type_simple_verified"])
        self.assert_not_promotion(result)

    def test_dual_m05_receiver_metadata_is_not_accepted(self):
        present = units()
        present["units"].append({
            "role": "m05_telegram", "observed_load": "loaded",
            "observed_active": "active", "observed_enabled": "enabled",
            "v2_user_fragment_path_matches": True,
            "unit_needs_daemon_reload": "no",
        })
        result = assess_v2_operations_readonly(
            units_report=present, status_document=safe_doc(),
            now_epoch=NOW,
        )
        self.assertIn("M05_TIMER_NOT_ACTIVE", result["alert_codes"])
        self.assertFalse(result["m05_receiver_metadata"]["m05_receiver_metadata_active"])
        self.assert_not_promotion(result)

    def test_unobserved_m03_or_m05_is_not_silent_success(self):
        r = assess_v2_operations_readonly(
            units_report=units(
                m03_scanner_timer={"observed_active": "active",
                                   "observed_enabled": "enabled"},
                m05_telegram_timer={"observed_active": "inactive"}),
            status_document=safe_doc(), now_epoch=NOW)
        # Standing PUBLIC M03 active+enabled is explicitly expected;
        # M05 inactive remains a real operational warning.
        self.assertNotIn("M03_TIMER_NOT_CONFIRMED_DISABLED", r["alert_codes"])
        self.assertIn("M05_TIMER_NOT_ACTIVE", r["alert_codes"])
        self.assertTrue(r["m03_timer_active_metadata_observed"])
        self.assertFalse(r["m03_timer_disabled_metadata_observed"])
        self.assert_not_promotion(r)
        conflict = assess_v2_operations_readonly(
            units_report=units(
                m03_scanner_timer={"observed_active": "active",
                                   "observed_enabled": "disabled"}),
            status_document=safe_doc(), now_epoch=NOW)
        self.assertIn(
            "M03_TIMER_NOT_CONFIRMED_DISABLED", conflict["alert_codes"]
        )
        self.assert_not_promotion(conflict)

    def test_unknown_fragment_and_unknown_unit_lists_block_positive_metadata(self):
        r = assess_v2_operations_readonly(
            units_report=units(
                btc_public_ws={"v2_user_fragment_path_matches": False},
                m03_scanner_timer={"unit_needs_daemon_reload": "yes"}),
            status_document=safe_doc(), now_epoch=NOW)
        self.assertIn("BTC_WS_UNIT_STATUS_UNVERIFIED", r["alert_codes"])
        self.assertIn("M03_TIMER_STATUS_UNVERIFIED", r["alert_codes"])
        self.assert_not_promotion(r)
        r2 = assess_v2_operations_readonly(
            units_report={"units": None}, status_document=safe_doc(),
            now_epoch=NOW)
        self.assertIn("M05_TIMER_STATUS_UNVERIFIED", r2["alert_codes"])

    def test_missing_status_default_read_is_fail_closed_no_network(self):
        with TemporaryDirectory() as td:
            target = Path(td) / "absent.json"
            r = assess_v2_operations_readonly(
                units_report=units(), status_path=target, now_epoch=NOW)
            self.assertIn("BTC_WS_BATCH_STATUS_UNAVAILABLE", r["alert_codes"])
            self.assert_not_promotion(r)

    def test_status_file_security_symlink_oversize_group_permission(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            good = root / "status.json"
            good.write_text(json.dumps(safe_doc()))
            good.chmod(0o600)
            self.assertEqual(_read_v2_ws_supervisor_status(good)["status"],
                             "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED")
            link = root / "link.json"
            link.symlink_to(good)
            self.assertIsNone(_read_v2_ws_supervisor_status(link))
            good.chmod(0o644)
            self.assertIsNone(_read_v2_ws_supervisor_status(good))
            good.chmod(0o600)
            good.write_text("X" * 32769)
            self.assertIsNone(_read_v2_ws_supervisor_status(good))

    def test_invalid_clock_never_coerces_bool(self):
        for v in (True, 0, -1, "1700000000"):
            with self.subTest(v=v), self.assertRaises(ValueError):
                assess_v2_operations_readonly(
                    units_report=units(), status_document=safe_doc(),
                    now_epoch=v)

    def test_cli_produces_redacted_json_and_no_promotion(self):
        secret = "REALLY_PRIVATE_SUPERVISOR_DETAIL"
        with (
            patch("indodax_ai_bot.m10_ops_health_readonly.inspect_named_v2_units",
                  return_value=units()),
            patch("indodax_ai_bot.m10_ops_health_readonly._read_v2_ws_supervisor_status",
                  return_value={
                      **safe_doc(status="REJECT", attempted_sessions=5,
                                 completed_safe_sessions=4,
                                 collector_failure_code=secret,
                                 restart_only_after_clean_batch=False),
                      "api_secret": secret,
                  }),
            patch("indodax_ai_bot.m10_ops_health_readonly.time.time",
                  return_value=NOW),
            redirect_stdout(StringIO()) as output,
        ):
            self.assertEqual(main(), 0)
        result = json.loads(output.getvalue())
        self.assertIn("BTC_WS_BATCH_REJECTED", result["alert_codes"])
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn("api_secret", output.getvalue())
        self.assert_not_promotion(result)


if __name__ == "__main__":
    unittest.main()
