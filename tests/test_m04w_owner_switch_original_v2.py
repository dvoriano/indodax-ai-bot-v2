"""M04-W original owner BTC user-unit switch offline source/backup tests."""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import m04w_owner_switch_original_v2 as deploy


class OwnerM04WOriginalUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "indodax-ai-bot-v2"
        self.unit = base / ".config/systemd/user/indodax-v2-public-ws-btc.service"
        self.new_template = self.root / "deploy/indodax-v2-public-ws-btc.service"
        self.drop = base / ".config/systemd/user/indodax-v2-public-ws-btc.service.d/90-capped-ops.conf"
        self.drop_template = self.root / "deploy/indodax-v2-public-ws-btc.capped-dropin.conf"
        for path in (self.unit, self.new_template, self.drop, self.drop_template):
            path.parent.mkdir(parents=True, exist_ok=True)
        self.old = (
            b"[Service]\n"
            b"ExecStart=python -B -m indodax_ai_bot --public-ws-supervised-once "
            b"--collector-sessions 10 --stream-seconds 30 "
            b"--collector-pause-seconds 1\n"
        )
        self.new = (
            b"[Service]\n"
            b"ExecStart=python -B -m indodax_ai_bot --public-ws-supervised-continuous "
            b"--collector-sessions 4 --stream-seconds 30 "
            b"--collector-pause-seconds 0 --public-ws-reconnect-until-recovered\n"
            b"Environment=INDODAX_TRADING_ENABLED=NO\n"
            b"Environment=INDODAX_ALLOW_LIVE_EXECUTION=NO\n"
            b"Environment=INDODAX_EXECUTION_MODE=DRY_RUN\n"
        )
        self.unit.write_bytes(self.old)
        self.new_template.write_bytes(self.new)
        self.drop.write_bytes(b"unchanged V2 dropin\n")
        self.drop_template.write_bytes(self.drop.read_bytes())
        self.patches = [
            patch.object(deploy, "ROOT", self.root),
            patch.object(deploy, "UNIT", self.unit),
            patch.object(deploy, "NEW_TEMPLATE", self.new_template),
            patch.object(deploy, "DROP", self.drop),
            patch.object(deploy, "TEMPLATE", self.drop_template),
            patch.object(deploy, "_read_old_template", return_value=self.old),
            patch.object(deploy, "_git", side_effect=self.fake_git),
            patch.object(deploy, "assess_original_btc_systemd"),
            patch.object(deploy, "_policy_checks"),
            patch.object(deploy, "_units", side_effect=self.fake_units),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def fake_git(*args):
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "HEAD"):
            return "FAKE_NONLIVE_SHA"
        return ""

    def fake_units(self):
        is_new = self.unit.read_bytes() == self.new
        return {
            "service_active": "inactive",
            "needs_reload": "no",
            "timer_active": "active",
            "m05_active": "active",
            "restart": "no",
            "execstart": ("--public-ws-supervised-continuous --collector-sessions 4"
                          if is_new else
                          "--public-ws-supervised-once --collector-sessions 10"),
        }

    def test_readonly_preflight_never_mutates_original_unit(self):
        before = self.unit.read_bytes()
        mode, new = deploy.preflight_readonly()
        self.assertEqual(mode, "EXACT_OLD_INSTALLED")
        self.assertEqual(new, self.new)
        self.assertEqual(self.unit.read_bytes(), before)
        self.assertEqual(list(self.unit.parent.glob("*.pre-m04w.*.bak")), [])

    def test_unit_switch_exact_old_backup_atomic_and_no_service_restart(self):
        def command(args, **kw):
            self.assertEqual(args, ["systemctl", "--user", "daemon-reload"])
            return SimpleNamespace(returncode=0)
        with patch.object(deploy.subprocess, "run", side_effect=command):
            report = deploy.switch_original_unit()
        self.assertEqual(report["status"],
                         "M04W_ORIGINAL_FOUR_WINDOW_UNIT_INSTALLED")
        self.assertEqual(self.unit.read_bytes(), self.new)
        backups = list(self.unit.parent.glob("*.pre-m04w.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.old)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertFalse(report["service_started_or_restarted"])
        self.assertFalse(report["order_sent"])

    def test_changed_installed_unit_denied_before_mutation(self):
        self.unit.write_text("unreviewed unit")
        with self.assertRaisesRegex(deploy.OwnerSwitchDenied,
                                    "M04W_INSTALLED_UNIT_NOT_CANONICAL"):
            deploy.preflight_readonly()
        self.assertEqual(list(self.unit.parent.glob("*.pre-m04w.*.bak")), [])

    def test_changed_dropin_denied_before_mutation(self):
        self.drop.write_text("Restart=always")
        with self.assertRaisesRegex(deploy.OwnerSwitchDenied,
                                    "M04W_ORIGINAL_FILES_UNVERIFIED"):
            deploy.preflight_readonly()

    def test_already_installed_is_idempotent(self):
        self.unit.write_bytes(self.new)
        out = deploy.switch_original_unit()
        self.assertEqual(out["status"], "M04W_ALREADY_INSTALLED")
        self.assertEqual(list(self.unit.parent.glob("*.pre-m04w.*.bak")), [])


if __name__ == "__main__":
    unittest.main()
