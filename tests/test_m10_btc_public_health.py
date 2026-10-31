"""M10 read-only PUBLIC BTC operational reporting tests, no network."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from indodax_ai_bot.btc_ops_health import health, main, SERVICE, TIMER

NOW = 1793413700


class M10BTCPublicHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.service_active = "inactive"
        self.service_result = "success"
        self.restart = "no"
        self.timer_active = "active"
        self.timer_enabled = "enabled"
        self.requests = []

        with sqlite3.connect(self.root / "public_ws_btc.sqlite3") as con:
            con.execute("CREATE TABLE ws_trade_publications(pair TEXT)")
            con.execute("CREATE TABLE ws_stream_discontinuities(pair TEXT)")
            for _ in range(5):
                con.execute("INSERT INTO ws_stream_discontinuities VALUES ('btc_idr')")
            for _ in range(15):
                con.execute("INSERT INTO ws_trade_publications VALUES ('btc_idr')")

        self._write("btc_public_ops.go.json", {
            "scope": "BTC_PUBLIC_MONITOR_ONLY",
            "expires_at_epoch": NOW + 7200,
            "max_gap_count": 5,
        })
        self._write("public_ws_btc.status.json", {
            "status": "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED",
            "collector_failure_code": None,
            "attempted_sessions": 10,
            "completed_safe_sessions": 10,
            "unrecoverable_gap_rebaseline_count": 5,
            "last_completed_batch_epoch": NOW - 600,
            "g1_receipt_issuance_allowed": False,
            "g2_provider_call_allowed": False,
            "execution_allowed": False,
        })

    def _write(self, name, val):
        p = self.root / name
        p.write_text(json.dumps(val) + "\n")
        p.chmod(0o600)

    def _fake_systemctl(self, args, **kwargs):
        self.requests.append(tuple(args))
        self.assertEqual(args[:3], ["systemctl", "--user", "show"])
        name = args[3]
        assert name in (SERVICE, TIMER)
        if name == SERVICE:
            kv = {
                "LoadState": "loaded", "ActiveState": self.service_active,
                "Result": self.service_result, "Restart": self.restart,
                "MainPID": "0", "NRestarts": "0", "UnitFileState": "disabled",
            }
        else:
            kv = {
                "LoadState": "loaded", "ActiveState": self.timer_active,
                "Result": "success", "Restart": "no",
                "MainPID": "0", "NRestarts": "0", "UnitFileState": self.timer_enabled,
            }
        return SimpleNamespace(
            returncode=0,
            stdout="".join(f"{k}={v}\n" for k,v in kv.items()),
            stderr="",
        )

    def _health(self, now=NOW):
        return health(root=self.root, now_epoch=now,
                      runner=self._fake_systemctl)

    def test_healthy_recent_batch_reports_only_allowlisted_public_metadata(self):
        out = self._health()
        self.assertEqual(out["status"], "HEALTHY_LAST_BATCH_UNATTESTED")
        self.assertEqual(out["reason"], "M10_RECENT_PUBLIC_BATCH_CONSISTENT")
        self.assertEqual(out["last_batch_age_seconds"], 600)
        self.assertEqual(out["go_remaining_seconds"], 7200)
        self.assertEqual(out["historical_gaps"], 5)
        self.assertEqual(out["publications"], 15)
        self.assertTrue(out["timer_active"])
        self.assertFalse(out["collector_active"])
        self.assertFalse(out["notification_sent"])
        self.assertFalse(out["execution_allowed"])
        self.assertFalse(out["source_authenticated_for_g1"])
        self.assertEqual(len(self.requests), 2)
        self.assertNotIn("token", str(out).lower())
        self.assertNotIn("order", str(out).lower())

    def test_running_process_is_not_fabricated_continuous_trade_tape(self):
        self.service_active = "activating"
        out = self._health()
        self.assertEqual(out["status"], "COLLECTING_UNATTESTED")
        self.assertEqual(out["reason"], "M10_BOUNDED_BATCH_IN_PROGRESS")
        self.assertFalse(out["full_trade_tape_proven"])
        self.assertFalse(out["execution_allowed"])

    def test_timer_stopped_or_restart_on_success_is_alert(self):
        self.timer_active = "inactive"
        out = self._health()
        self.assertEqual(out["status"], "ALERT_REVIEW")
        self.assertEqual(out["reason"], "M10_TIMER_OR_RESTART_UNSAFE")
        self.timer_active = "active"
        self.restart = "on-success"
        out = self._health()
        self.assertEqual(out["status"], "ALERT_REVIEW")
        self.assertEqual(out["reason"], "M10_TIMER_OR_RESTART_UNSAFE")

    def test_expired_and_corrupt_owner_go_fail_closed(self):
        out = self._health(now=NOW + 7200)
        self.assertEqual(out["reason"], "M10_PUBLIC_GO_EXPIRED")
        self._write("btc_public_ops.go.json", {
            "scope": "LIVE_ORDERS", "expires_at_epoch": NOW + 7200,
            "max_gap_count": 5,
        })
        self.assertEqual(self._health()["reason"], "M10_PUBLIC_GO_SCOPE_UNSAFE")

    def test_missing_owner_go_or_private_permission_no_secret_exposure(self):
        path = self.root / "btc_public_ops.go.json"
        path.unlink()
        self.assertEqual(self._health()["status"], "ALERT_REVIEW")
        self.assertNotIn("token", str(self._health()).lower())
        self._write("btc_public_ops.go.json", {
            "scope": "BTC_PUBLIC_MONITOR_ONLY",
            "expires_at_epoch": NOW + 7200,
            "max_gap_count": 5,
        })
        path.chmod(0o644)
        self.assertEqual(self._health()["reason"], "UNSAFE_PRIVATE_FILE")

    def test_sixth_gap_blocks_even_when_last_sidecar_is_clean(self):
        with sqlite3.connect(self.root / "public_ws_btc.sqlite3") as con:
            con.execute("INSERT INTO ws_stream_discontinuities VALUES ('btc_idr')")
        out = self._health()
        self.assertEqual(out["status"], "ALERT_REVIEW")
        self.assertEqual(out["reason"], "M10_PUBLIC_GAP_BUDGET_EXCEEDED")

    def test_gap_rebaseline_mismatch_stale_and_failed_batch_alert(self):
        sidecar = self.root / "public_ws_btc.status.json"
        x = json.loads(sidecar.read_text())
        x["unrecoverable_gap_rebaseline_count"] = 4
        self._write("public_ws_btc.status.json", x)
        self.assertEqual(self._health()["reason"], "M10_PUBLIC_SIDECAR_UNSAFE")
        x["unrecoverable_gap_rebaseline_count"] = 5
        x["last_completed_batch_epoch"] = NOW - 1900
        self._write("public_ws_btc.status.json", x)
        self.assertEqual(self._health()["reason"], "M10_PUBLIC_LAST_BATCH_STALE")
        x["status"] = "REJECT"
        self._write("public_ws_btc.status.json", x)
        self.assertEqual(self._health()["reason"], "M10_PUBLIC_SIDECAR_UNSAFE")

    def test_service_error_or_operator_hold_cannot_be_healthy(self):
        self.service_result = "exit-code"
        self.assertEqual(self._health()["reason"], "M10_SERVICE_FAILURE")
        self.service_result = "success"
        self._write("btc_public_ops.hold.json", {
            "reason": "PUBLIC_WS_SERVICE_FAILURE_HOLD",
        })
        self.assertEqual(self._health()["reason"], "M10_OPERATOR_HOLD")

    def test_cli_is_explicitly_read_only_and_fails_closed(self):
        out = StringIO()
        with patch("indodax_ai_bot.btc_ops_health.health",
                   return_value={"status": "ALERT_REVIEW", "execution_allowed": False}):
            with redirect_stdout(out):
                rc = main(["--root", str(self.root)])
        self.assertEqual(rc, 3)
        self.assertEqual(json.loads(out.getvalue())["status"], "ALERT_REVIEW")
        self.assertFalse(json.loads(out.getvalue())["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
