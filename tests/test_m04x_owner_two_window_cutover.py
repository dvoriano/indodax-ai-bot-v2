"""M04-X BTC owner 4×30->2×30 cutover: full local mocks, never systemd/network."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from indodax_ai_bot.btc_ops_gate import AdmissionDenied
from scripts import m04x_owner_switch_to_two_windows as m


class TwoWindowCutoverOfflineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "indodax-ai-bot-v2"
        self.unit = base / ".config/systemd/user/indodax-v2-public-ws-btc.service"
        self.source = self.root / "deploy/indodax-v2-public-ws-btc.service"
        self.drop = base / "drop.conf"
        self.template = base / "drop-template.conf"
        for p in (self.unit, self.source, self.drop, self.template):
            p.parent.mkdir(parents=True, exist_ok=True)
        self.old = (b"# original source old 4x30\n[Service]\n" +
                    b"ExecStart=python --public-ws-supervised-continuous "
                    b"--collector-sessions 4 --stream-seconds 30\n" * 3)
        self.new = (
            b"# owner source new 2x30\n[Service]\n"
            b"ExecStart=python --public-ws-supervised-continuous "
            b"--collector-sessions 2 --stream-seconds 30 "
            b"--collector-pause-seconds 0 --public-ws-reconnect-until-recovered\n"
            b"Environment=INDODAX_TRADING_ENABLED=NO\n"
            b"Environment=INDODAX_ALLOW_LIVE_EXECUTION=NO\n"
            b"Environment=INDODAX_EXECUTION_MODE=DRY_RUN\n")
        self.unit.write_bytes(self.old)
        self.source.write_bytes(self.new)
        self.drop.write_bytes(b"same canonical dropin")
        self.template.write_bytes(self.drop.read_bytes())
        self.pid = 3868600
        self.commands = []
        self.patches = [
            patch.object(m, "ROOT", self.root),
            patch.object(m, "UNIT", self.unit),
            patch.object(m, "SOURCE_UNIT", self.source),
            patch.object(m, "DROP", self.drop),
            patch.object(m, "TEMPLATE", self.template),
            patch.object(m, "_git", side_effect=self.fake_git),
            patch.object(m, "_old_unit_bytes", return_value=self.old),
            patch.object(m, "_unit_state", side_effect=self.fake_unit_state),
            patch.object(m, "_policy_checks"),
            patch.object(m, "_command", side_effect=self.fake_command),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def fake_git(self, *args):
        if args == ("branch", "--show-current"):
            return b"main\n"
        if args == ("status", "--porcelain"):
            return b""
        if args == ("rev-parse", "HEAD"):
            return b"TESTS_OFFLINE\n"
        return b""

    def fake_unit_state(self):
        old_installed = self.unit.read_bytes() == self.old
        return ({
            "service_active": "active",
            "execstart": ("python --public-ws-supervised-continuous "
                          "--collector-sessions " +
                          ("4" if old_installed else "2")),
        }, self.pid)

    def fake_command(self, args, **kw):
        self.commands.append(args)
        if args == ["systemctl", "--user", "restart", m.UNIT_NAME]:
            self.pid += 1
        return b""

    def test_readonly_never_changes_service_or_go(self):
        out = m.preflight_readonly()
        self.assertEqual(out["mode"], "FOUR_WINDOWS_INSTALLED_RUNNING")
        self.assertEqual(out["current_pid"], 3868600)
        self.assertEqual(self.commands, [])
        self.assertEqual(self.unit.read_bytes(), self.old)

    def test_staged_install_creates_0600_backup_not_second_process(self):
        result = m.stage_two_windows()
        self.assertEqual(result["status"], "M04X_2X30_UNIT_STAGED")
        self.assertEqual(self.unit.read_bytes(), self.new)
        backups = list(self.unit.parent.glob("*.pre-m04x.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.old)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.commands, [["systemctl", "--user", "daemon-reload"]])
        self.assertEqual(self.pid, 3868600)

    def test_restart_is_separate_and_requires_original_cooldown(self):
        m.stage_two_windows()
        with patch.object(m, "diagnose_once",
                          return_value={"first_blocker": "PUBLIC_WS_START_COOLDOWN"}):
            with self.assertRaisesRegex(
                m.CutoverDenied, "M04X_ORIGINAL_COOLDOWN_OR_POLICY_BLOCKED",
            ):
                m.activate_two_windows()
        self.assertEqual(self.pid, 3868600)
        self.assertEqual(self.commands, [["systemctl", "--user", "daemon-reload"]])
        with patch.object(m, "diagnose_once",
                          return_value={"first_blocker": "NONE"}):
            result = m.activate_two_windows()
        self.assertEqual(result["status"], "M04X_2X30_NEW_PROCESS_STARTED")
        self.assertEqual(result["previous_pid"], 3868600)
        self.assertEqual(result["new_pid"], 3868601)
        self.assertFalse(result["two_window_checkpoint_verified"])
        self.assertFalse(result["trading_enabled"])
        self.assertEqual(self.commands[-1],
                         ["systemctl", "--user", "restart", m.UNIT_NAME])

    def test_owner_hold_denies_before_service_mutation(self):
        with patch.object(m, "_policy_checks",
                          side_effect=AdmissionDenied("PUBLIC_WS_OPERATOR_HOLD")):
            with self.assertRaisesRegex(
                m.CutoverDenied, "M04X_ORIGINAL_OWNER_GO_OR_BTC_UNHEALTHY",
            ):
                m.stage_two_windows()
        self.assertEqual(self.commands, [])
        self.assertEqual(self.unit.read_bytes(), self.old)


if __name__ == "__main__":
    unittest.main()
