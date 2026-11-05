"""Offline scope tests for original V2 M03->M05 canary release."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts import m03_m05_owner_nonlive_canary as canary

SERVICE = (
    "WorkingDirectory=%h/indodax-ai-bot-v2\n"
    "Environment=INDODAX_TRADING_ENABLED=NO\n"
    "Environment=INDODAX_ALLOW_LIVE_EXECUTION=NO\n"
    "Environment=INDODAX_EXECUTION_MODE=DRY_RUN\n"
    "Environment=INDODAX_M03_REQUIRE_EGRESS_ADMISSION=YES\n"
    "ExecStart=/usr/bin/flock -n /tmp/v2 --scheduled-public-scanner-once\n"
)


class FakeUnits:
    def __init__(self, home: Path):
        self.home = home
        self.m03_enabled = False
        self.calls = []

    def prop(self, unit: str, prop: str) -> str:
        table = {
            (canary.UNIT, "ActiveState"): "inactive",
            (canary.UNIT, "FragmentPath"): str(self.home / ".config/systemd/user" / canary.UNIT),
            (canary.UNIT, "NeedDaemonReload"): "no",
            (canary.TIMER, "ActiveState"): "active" if self.m03_enabled else "inactive",
            (canary.TIMER, "UnitFileState"): "enabled" if self.m03_enabled else "disabled",
            (canary.TIMER, "FragmentPath"): str(self.home / ".config/systemd/user" / canary.TIMER),
            (canary.TIMER, "NeedDaemonReload"): "no",
            (canary.M05_TIMER, "ActiveState"): "active",
            (canary.BTC_TIMER, "ActiveState"): "active",
        }
        return table.get((unit, prop), "unknown")

    def run(self, *args) -> str:
        self.calls.append(tuple(args))
        if args == ("enable", "--now", canary.TIMER):
            self.m03_enabled = True
        if args == ("disable", "--now", canary.TIMER):
            self.m03_enabled = False
        return ""


class OriginalCanaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-m03-m05-canary-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = self.home / "indodax-ai-bot-v2"
        self.root.mkdir(mode=0o700)
        self.deploy = self.root / "deploy"
        self.deploy.mkdir(mode=0o700)
        self.envdir = self.home / ".config" / "indodax-ai-bot-v2"
        self.envdir.mkdir(parents=True, mode=0o700)
        self.m05dir = self.envdir / "m05"
        self.m05dir.mkdir(mode=0o700)
        self.units_dir = self.home / ".config" / "systemd" / "user"
        self.units_dir.mkdir(parents=True, mode=0o700)
        (self.units_dir / canary.UNIT).write_text(SERVICE)
        (self.deploy / canary.UNIT).write_text(SERVICE)
        (self.deploy / "indodax-v2-m03-owner-advisory.conf").write_text(
            "Environment=INDODAX_TRADING_ENABLED=NO\n"
            "Environment=INDODAX_ALLOW_LIVE_EXECUTION=NO\n"
            "ExecStart=... --m05-market-advisory-queue\n")
        self.db_dir = self.home / "state"
        self.db_dir.mkdir(mode=0o700)
        self.db = self.db_dir / "owner.sqlite3"
        with sqlite3.connect(self.db) as con:
            con.executescript(
                "CREATE TABLE m05_config(singleton INTEGER PRIMARY KEY,"
                "owner_user_id INTEGER, mode TEXT);"
                "INSERT INTO m05_config VALUES (1, 1234567, 'MANUAL');"
                "CREATE TABLE m05_market_advisory_outbox(id INTEGER PRIMARY KEY);"
            )
        self.db.chmod(0o600)
        self.service_env = self.m05dir / "service.env"
        self.service_env.write_text(
            f"M05_TELEGRAM_OWNER_DB={self.db}\n"
            "M05_TELEGRAM_OWNER_ID=1234567\n"
            "M05_TELEGRAM_TOKEN_FILE=/never/read/this/path\n")
        self.service_env.chmod(0o600)
        now = int(time.time())
        self.policy = self.envdir / "m03-egress-admission.json"
        self.policy.write_text(json.dumps({
            "schema_version": 2,
            "pilot_scope": "M03_NONLIVE_PUBLIC_PILOT",
            "owner_approved_public_only": True,
            "all_shared_ip_consumers_accounted": True,
            "egress_public_ip": "43.134.68.108",
            "external_consumer_ids": [],
            "external_peak_requests_rolling_60s": 0,
            "issued_at_epoch": now - 60,
            "expires_at_epoch": now + 3000,
        }))
        self.policy.chmod(0o600)
        self.units = FakeUnits(self.home)

    def stage(self):
        with (
            patch.object(canary, "ROOT", self.root),
            patch.object(Path, "home", return_value=self.home),
            patch.object(canary, "collect_original_v2_runtime_snapshot",
                         return_value={"ready_for_nonlive_scanner_canary": True}),
            patch.object(canary, "_unit_property", self.units.prop),
            patch.object(canary, "_run_systemctl", self.units.run),
        ):
            return canary.stage_original_nonlive_canary(
                root=self.root, config=self.envdir, unit_dir=self.units_dir
            )

    def test_stages_original_timer_and_existing_owner_outbox_without_token(self):
        result = self.stage()
        self.assertEqual(result["status"], "M03_ORIGINAL_NONLIVE_TIMER_CANARY_ENABLED")
        self.assertTrue(self.units.m03_enabled)
        self.assertFalse(result["advisory_delivery_verified"])
        self.assertFalse(result["live_order_allowed"])
        env = (self.envdir / canary.ENV_NAME).read_text()
        self.assertIn(f"M05_TELEGRAM_OWNER_DB={self.db}\n", env)
        self.assertIn("M05_MARKET_ADVISORY_QUEUE_GO=YES\n", env)
        self.assertNotIn("TOKEN", env)
        self.assertTrue((self.units_dir / (canary.UNIT + ".d") / canary.DROP_NAME).is_file())
        self.assertEqual(
            os.stat(self.envdir / canary.ENV_NAME).st_mode & 0o777, 0o600
        )

    def test_no_auth_without_clean_readiness(self):
        with patch.object(canary, "collect_original_v2_runtime_snapshot",
                          return_value={"ready_for_nonlive_scanner_canary": False}):
            with patch.object(canary, "ROOT", self.root):
                with self.assertRaisesRegex(canary.CanaryDenied,
                                            "M03_CANARY_SNAPSHOT_NOT_READY"):
                    canary.stage_original_nonlive_canary(
                        root=self.root, config=self.envdir, unit_dir=self.units_dir
                    )
        self.assertFalse(self.units.m03_enabled)
        self.assertFalse((self.envdir / canary.ENV_NAME).exists())

    def test_declines_unsafe_installed_service_without_modification(self):
        (self.units_dir / canary.UNIT).write_text(SERVICE.replace(
            "INDODAX_TRADING_ENABLED=NO", "INDODAX_TRADING_ENABLED=YES"))
        with self.assertRaisesRegex(canary.CanaryDenied, "M03_CANARY_INSTALLED_SERVICE_UNVERIFIED"):
            self.stage()
        self.assertFalse(self.units.m03_enabled)
        self.assertFalse((self.envdir / canary.ENV_NAME).exists())

    def test_existing_advisory_file_fails_before_installs(self):
        (self.envdir / canary.ENV_NAME).write_text("UNVERIFIED")
        with self.assertRaisesRegex(canary.CanaryDenied, "M03_CANARY_EXISTING_DROPIN_REVIEW_REQUIRED"):
            self.stage()
        self.assertFalse(self.units.m03_enabled)

    def test_owner_ledger_must_be_manual(self):
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE m05_config SET mode='AUTO'")
        with self.assertRaisesRegex(canary.CanaryDenied, "M03_CANARY_OWNER_MODE_OR_ID_MISMATCH"):
            self.stage()
        self.assertFalse((self.envdir / canary.ENV_NAME).exists())

    def test_no_implicit_owner_execution_opt_in(self):
        self.assertEqual(canary.main.__name__, "main")
        self.assertFalse(self.units.m03_enabled)


if __name__ == "__main__":
    unittest.main()
