"""Offline real original V2 Telegram service cutover tests, no network."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from indodax_ai_bot.owner_control import OwnerDecisionLedger
from scripts import m05_canonical_original_source_cutover as app

CANONICAL = """[Unit]
Description=Indodax V2 M05 dedicated Telegram owner one-shot (NO TRADING)
[Service]
Type=oneshot
WorkingDirectory=%h/indodax-ai-bot-v2
Restart=no
Environment=INDODAX_TRADING_ENABLED=NO
Environment=INDODAX_ALLOW_LIVE_EXECUTION=NO
Environment=INDODAX_EXECUTION_MODE=DRY_RUN
Environment=M05_TELEGRAM_TICK_GO=YES
Environment=M05_TELEGRAM_NETWORK_GO=YES
Environment=M05_TELEGRAM_DEDICATED_GO=YES
EnvironmentFile=%h/.config/indodax-ai-bot-v2/m05/service.env
ExecStart=%h/indodax-ai-bot-v2/.venv/bin/python -I -B %h/indodax-ai-bot-v2/scripts/m05_telegram_service_tick_entry.py
"""


class FakeSystemd:
    def __init__(self, home: Path):
        self.home = home
        self.unit_path = home / ".config/systemd/user" / app.SERVICE
        self.loaded = self.unit_path.read_text()
        self.timer_active = True
        self.enabled = True
        self.daemon_reloads = 0
        self.stop_count = 0
        self.start_count = 0
        self.fail_reload = False
        self.fail_start = False

    def __call__(self, *args):
        if args[:2] == ("show", "-P"):
            _, _, field, unit = args
            if unit == app.SERVICE:
                if field == "FragmentPath":
                    return str(self.unit_path)
                if field == "NeedDaemonReload":
                    return "no" if self.unit_path.read_text() == self.loaded else "yes"
                if field == "DropInPaths":
                    return ""
                if field == "Restart":
                    return "no"
                if field == "ActiveState":
                    return "inactive"
                text = self.loaded.replace("%h", str(self.home))
                if field == "WorkingDirectory":
                    for line in text.splitlines():
                        if line.startswith("WorkingDirectory="):
                            return line.split("=", 1)[1]
                if field == "ExecStart":
                    for line in text.splitlines():
                        if line.startswith("ExecStart="):
                            return "argv[]=" + line.split("=", 1)[1]
            if unit == app.TIMER:
                if field == "ActiveState":
                    return "active" if self.timer_active else "inactive"
                if field == "UnitFileState":
                    return "enabled" if self.enabled else "disabled"
            if unit in (app._M03_TIMER, app._BTC_TIMER) and field == "ActiveState":
                return "active"
            return "unexpected_property"
        if args == ("stop", app.TIMER):
            self.stop_count += 1
            self.timer_active = False
            return ""
        if args == ("start", app.TIMER):
            self.start_count += 1
            if self.fail_start:
                self.fail_start = False
                raise app.M05CutoverDenied("M05_CUTOVER_SYSTEMD_UNAVAILABLE")
            self.timer_active = True
            return ""
        if args == ("daemon-reload",):
            self.daemon_reloads += 1
            if self.fail_reload:
                self.fail_reload = False
                raise app.M05CutoverDenied("M05_CUTOVER_SYSTEMD_UNAVAILABLE")
            self.loaded = self.unit_path.read_text()
            return ""
        raise AssertionError("UNKNOWN_SYSTEMD_ACTION " + repr(args))


class CanonicalOwnerM05CutoverTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="v2-m05-canonical-cutover-")
        self.addCleanup(t.cleanup)
        self.home = Path(t.name)
        self.root = self.home / "indodax-ai-bot-v2"
        source = self.root / "indodax_ai_bot"
        source.mkdir(parents=True, mode=0o700)
        (source / "telegram_service_tick.py").write_text("ORIGINAL V2 TICK\n")
        (source / "owner_control.py").write_text("public_reference\n")
        scripts = self.root / "scripts"
        scripts.mkdir(mode=0o700)
        (scripts / "m05_telegram_service_tick_entry.py").write_text("V2\n")
        deploy = self.root / "systemd/user"
        deploy.mkdir(parents=True, mode=0o700)
        (deploy / app.SERVICE).write_text(CANONICAL)
        self.units = self.home / ".config/systemd/user"
        self.units.mkdir(parents=True, mode=0o700)
        self.legacy = CANONICAL.replace(
            "%h/indodax-ai-bot-v2", "%h/indodax-ai-bot-v2-m05-release",
        )
        self.unit = self.units / app.SERVICE
        self.unit.write_text(self.legacy)
        self.unit.chmod(0o600)
        self.env_dir = self.home / ".config/indodax-ai-bot-v2/m05"
        self.env_dir.mkdir(parents=True, mode=0o700)
        self.env_dir.chmod(0o700)
        state = self.home / ".local/state/indodax-ai-bot-v2/m05-telegram"
        state.mkdir(parents=True, mode=0o700)
        state.chmod(0o700)
        self.db = state / "owner.sqlite3"
        OwnerDecisionLedger(self.db, owner_user_id=1234567)
        self.db.chmod(0o600)
        self.env = self.env_dir / "service.env"
        self.env.write_text(
            f"M05_TELEGRAM_OWNER_DB={self.db}\n"
            "M05_TELEGRAM_OWNER_ID=1234567\n"
            "M05_TELEGRAM_TOKEN_FILE=/never/touch/a/private/token\n"
        )
        self.env.chmod(0o600)
        self.fake = FakeSystemd(self.home)

    def go(self):
        return app.switch_original_m05_source(home=self.home, run=self.fake)

    def test_switch_one_original_installed_service_preserves_owner_ledger(self):
        before = self.db.read_bytes()
        result = self.go()
        self.assertEqual(result["status"],
                         "M05_CANONICAL_ORIGINAL_V2_SOURCE_CUTOVER_PASS")
        self.assertTrue(result["service_changed"])
        self.assertFalse(result["live_trading_enabled"])
        self.assertEqual(self.unit.read_text(), CANONICAL)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(self.fake.stop_count, 1)
        self.assertEqual(self.fake.start_count, 1)
        self.assertTrue(self.fake.timer_active)
        self.assertTrue(self.fake.enabled)
        backup = self.units / result["backup_file_name"]
        self.assertEqual(backup.read_text(), self.legacy)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_extra_original_v2_hardening_preserved_byte_for_byte(self):
        # Actual owner service may contain VPS-specific hardening/timeout
        # directives not present in the current repository template.
        # Only source paths are changed; leave all other directives intact.
        hardened = self.legacy + (
            "ProtectSystem=strict\n"
            "TimeoutStartSec=85s\n"
            "PrivateTmp=true\n"
        )
        self.unit.write_text(hardened)
        self.fake.loaded = hardened
        result = self.go()
        self.assertTrue(result["service_changed"])
        self.assertEqual(
            self.unit.read_text(),
            hardened.replace(
                "%h/indodax-ai-bot-v2-m05-release",
                "%h/indodax-ai-bot-v2",
            ),
        )
        self.assertEqual(self.fake.stop_count, 1)
        self.assertTrue(self.fake.timer_active)

    def test_conflicting_live_enable_env_blocks_unsafe_source_switch(self):
        unsafe = self.legacy.replace(
            "Environment=INDODAX_TRADING_ENABLED=NO",
            "Environment=INDODAX_TRADING_ENABLED=YES",
        )
        self.unit.write_text(unsafe)
        self.fake.loaded = unsafe
        with self.assertRaisesRegex(
            app.M05CutoverDenied, "M05_CUTOVER_REQUIRED_HARDLOCK_MISSING"
        ):
            self.go()
        self.assertEqual(self.fake.stop_count, 0)
        self.assertEqual(self.unit.read_text(), unsafe)

    def test_changed_original_exec_command_blocks_source_switch(self):
        unsafe = self.legacy.replace(" -I -B ", " -B ")
        self.unit.write_text(unsafe)
        self.fake.loaded = unsafe
        with self.assertRaisesRegex(
            app.M05CutoverDenied, "M05_CUTOVER_ORIGINAL_EXEC_OR_ENV_DIFFERS"
        ):
            self.go()
        self.assertEqual(self.fake.stop_count, 0)

    def test_idempotent_second_run_keeps_same_original_timer(self):
        self.go()
        before = [x.name for x in self.units.iterdir()]
        result = self.go()
        self.assertEqual(result["status"],
                         "M05_CANONICAL_ORIGINAL_V2_ALREADY_INSTALLED")
        self.assertFalse(result["service_changed"])
        self.assertEqual(self.fake.stop_count, 1)
        self.assertEqual([x.name for x in self.units.iterdir()], before)

    def test_unreviewed_exec_change_refused_without_pause_or_backup(self):
        self.unit.write_text(self.legacy.replace(
            "ExecStart=", "ExecStartPre=/bin/false\nExecStart="))
        self.fake.loaded = self.unit.read_text()
        with self.assertRaisesRegex(
            app.M05CutoverDenied, "M05_CUTOVER_UNREVIEWED_UNIT_DIFFERENCE"
        ):
            self.go()
        self.assertTrue(self.fake.timer_active)
        self.assertEqual(self.fake.stop_count, 0)

    def test_preflight_missing_manual_preview_refused_no_timer_change(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DROP TABLE m05_manual_trade_previews")
        with self.assertRaisesRegex(
            app.M05CutoverDenied, "M05_CUTOVER_OWNER_DB_UNVERIFIED"
        ):
            self.go()
        self.assertTrue(self.fake.timer_active)
        self.assertEqual(self.unit.read_text(), self.legacy)

    def test_m05_owner_auto_mode_refused_without_pausing_timer(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE m05_config SET mode='AUTO'")
        with self.assertRaisesRegex(
            app.M05CutoverDenied, "M05_CUTOVER_OWNER_MODE_OR_ID_MISMATCH"
        ):
            self.go()
        self.assertTrue(self.fake.timer_active)
        self.assertEqual(self.fake.stop_count, 0)

    def test_outstanding_claimed_update_blocks_source_change(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO m05_telegram_updates("
                "update_id,callback_query_id,status,event_epoch) "
                "VALUES(99,NULL,'CLAIMED',1793820000)"
            )
        with self.assertRaisesRegex(
            app.M05CutoverDenied, "M05_CUTOVER_OUTSTANDING_OWNER_CLAIM"
        ):
            self.go()
        self.assertTrue(self.fake.timer_active)

    def test_daemon_reload_failure_restores_installed_service_and_timer(self):
        self.fake.fail_reload = True
        with self.assertRaisesRegex(
            app.M05CutoverDenied, "M05_CUTOVER_SYSTEMD_UNAVAILABLE"
        ):
            self.go()
        self.assertEqual(self.unit.read_text(), self.legacy)
        self.assertEqual(self.fake.stop_count, 1)
        self.assertEqual(self.fake.start_count, 1)
        self.assertTrue(self.fake.timer_active)

    def test_timer_reenable_failure_rolls_back_service_source(self):
        self.fake.fail_start = True
        with self.assertRaisesRegex(
            app.M05CutoverDenied, "M05_CUTOVER_SYSTEMD_UNAVAILABLE"
        ):
            self.go()
        self.assertEqual(self.unit.read_text(), self.legacy)
        self.assertEqual(self.fake.stop_count, 1)
        self.assertEqual(self.fake.start_count, 2)
        self.assertTrue(self.fake.timer_active)

    def test_current_service_with_unknown_loaded_exec_is_not_assumed_safe(self):
        self.unit.write_text(CANONICAL)
        self.fake.loaded = self.legacy
        with self.assertRaisesRegex(
            app.M05CutoverDenied, "M05_CUTOVER_UNIT_METADATA_UNVERIFIED"
        ):
            self.go()
        self.assertEqual(self.fake.stop_count, 0)


if __name__ == "__main__":
    unittest.main()
