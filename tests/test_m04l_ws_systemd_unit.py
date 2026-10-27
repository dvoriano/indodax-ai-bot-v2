"""M04-L staged public BTC user systemd unit operator inspection, no mutation."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from indodax_ai_bot.__main__ import main
from indodax_ai_bot.public_ws_unit_status import (
    inspect_public_ws_systemd_unit, UNIT_NAME,
)

REPO_TEMPLATE = (Path(__file__).resolve().parents[1] /
                 "deploy" / UNIT_NAME)


def staged(home: Path, *, body: str | None = None):
    path = home / ".config" / "systemd" / "user" / UNIT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(REPO_TEMPLATE.read_text() if body is None else body)
    return path


def response(path: Path, *, active="inactive", sub="dead", enabled="disabled",
             pid="0", load="loaded", fragment=None):
    output = "\n".join([
        "LoadState=" + load,
        "ActiveState=" + active,
        "SubState=" + sub,
        "UnitFileState=" + enabled,
        "MainPID=" + pid,
        "FragmentPath=" + (str(path) if fragment is None else fragment),
    ]) + "\n"
    return SimpleNamespace(returncode=0, stdout=output, stderr="")


class M04LUnitStatusTests(unittest.TestCase):
    def test_repo_template_safe_not_installed_and_systemctl_not_called(self):
        with TemporaryDirectory() as td:
            runner=Mock(side_effect=AssertionError("systemctl must not run"))
            data=inspect_public_ws_systemd_unit(
                home=td, template=REPO_TEMPLATE, runner=runner,
            )
            self.assertEqual(data["status"], "STAGED_NOT_INSTALLED")
            self.assertTrue(data["staged_template_present"])
            self.assertTrue(data["staged_template_safety_valid"])
            self.assertFalse(data["unit_installed"])
            self.assertFalse(data["systemd_running_confirmed"])
            self.assertIsNone(data["live_trade_stream_connected"])
            self.assertFalse(data["g1_receipt_issuance_allowed"])
            self.assertFalse(data["g2_provider_call_allowed"])
            self.assertFalse(data["execution_allowed"])
            runner.assert_not_called()

    def test_matching_installed_inactive_is_not_running(self):
        with TemporaryDirectory() as td:
            unit=staged(Path(td))
            run=Mock(return_value=response(unit))
            out=inspect_public_ws_systemd_unit(home=td,
                                               template=REPO_TEMPLATE, runner=run)
            self.assertEqual(out["status"], "USER_UNIT_INACTIVE_OR_FAILED")
            self.assertTrue(out["unit_installed"])
            self.assertTrue(out["installed_unit_matches_staged"])
            self.assertTrue(out["unit_loaded"])
            self.assertFalse(out["unit_active"])
            self.assertFalse(out["unit_enabled"])
            self.assertIsNone(out["main_pid"])
            self.assertFalse(out["systemd_running_confirmed"])
            args=run.call_args.args[0]
            self.assertEqual(args[:3], ["systemctl", "--user", "show"])
            self.assertEqual(args[3], UNIT_NAME)

    def test_matching_installed_running_is_process_only_not_live_tape(self):
        with TemporaryDirectory() as td:
            unit=staged(Path(td))
            out=inspect_public_ws_systemd_unit(
                home=td, template=REPO_TEMPLATE,
                runner=lambda *args,**kw:response(
                    unit, active="active",sub="running",enabled="enabled",pid="5010"),
            )
            self.assertEqual(out["status"], "USER_UNIT_RUNNING_OBSERVED")
            self.assertTrue(out["systemd_running_confirmed"])
            self.assertTrue(out["unit_enabled"])
            self.assertEqual(out["main_pid"],5010)
            self.assertIsNone(out["live_trade_stream_connected"])
            self.assertFalse(out["indodax_trade_tape_completeness_proven"])
            self.assertFalse(out["source_authenticated_for_g1"])
            self.assertFalse(out["execution_allowed"])

    def test_installed_modified_unit_rejected_without_systemctl(self):
        with TemporaryDirectory() as td:
            body=REPO_TEMPLATE.read_text().replace(
                "INDODAX_TRADING_ENABLED=NO",
                "INDODAX_TRADING_ENABLED=YES",
            )
            staged(Path(td),body=body)
            runner=Mock(side_effect=AssertionError("no systemctl"))
            out=inspect_public_ws_systemd_unit(
                home=td,template=REPO_TEMPLATE,runner=runner)
            self.assertEqual(out["reason"], "PUBLIC_WS_INSTALLED_UNIT_DIFFERS")
            self.assertFalse(out["installed_unit_matches_staged"])
            runner.assert_not_called()

    def test_installed_unit_symlink_not_treated_as_safe(self):
        with TemporaryDirectory() as td:
            home=Path(td)
            target=home/"external.unit"
            target.write_text(REPO_TEMPLATE.read_text())
            installed=home/".config"/"systemd"/"user"/UNIT_NAME
            installed.parent.mkdir(parents=True)
            installed.symlink_to(target)
            out=inspect_public_ws_systemd_unit(home=home,template=REPO_TEMPLATE)
            self.assertEqual(out["reason"],"PUBLIC_WS_INSTALLED_SYMLINK_NOT_ACCEPTED")
            self.assertFalse(out["systemd_running_confirmed"])

    def test_unsafe_staged_template_rejected_before_install(self):
        with TemporaryDirectory() as td:
            home=Path(td)
            template=home/"unsafe.service"
            template.write_text(REPO_TEMPLATE.read_text() +
                                "\nEnvironment=INDODAX_TRADING_ENABLED=YES\n")
            out=inspect_public_ws_systemd_unit(home=home,template=template)
            self.assertEqual(out["reason"],"PUBLIC_WS_STAGED_UNIT_UNSAFE")
            self.assertFalse(out["staged_template_safety_valid"])

    def test_absent_template_is_reject(self):
        with TemporaryDirectory() as td:
            out=inspect_public_ws_systemd_unit(
                home=td,template=Path(td)/"missing.service")
            self.assertEqual(out["reason"],"PUBLIC_WS_STAGED_UNIT_MISSING_OR_INVALID")

    def test_user_manager_unavailable_fails_without_guessing_active(self):
        with TemporaryDirectory() as td:
            unit=staged(Path(td))
            out=inspect_public_ws_systemd_unit(
                home=td,template=REPO_TEMPLATE,
                runner=lambda *args,**kw:SimpleNamespace(
                    returncode=1,stdout="",stderr="private diagnostic"),
            )
            self.assertEqual(out["reason"],"PUBLIC_WS_USER_SYSTEMD_UNAVAILABLE")
            self.assertFalse(out["unit_active"])
            self.assertNotIn("private diagnostic",str(out))

    def test_wrong_fragment_rejected_even_when_systemctl_says_active(self):
        with TemporaryDirectory() as td:
            unit=staged(Path(td))
            out=inspect_public_ws_systemd_unit(
                home=td,template=REPO_TEMPLATE,
                runner=lambda *args,**kw:response(
                    unit,active="active",sub="running",enabled="enabled",
                    pid="5001",fragment="/tmp/foreign.service"),
            )
            self.assertEqual(out["reason"],"PUBLIC_WS_UNIT_FRAGMENT_OR_LOAD_MISMATCH")
            self.assertFalse(out["systemd_running_confirmed"])

    def test_cli_unit_status_is_read_only_no_decision_journal(self):
        with TemporaryDirectory() as td:
            db=Path(td)/"should_not_exist.sqlite3"
            fake={"status":"STAGED_NOT_INSTALLED",
                  "systemd_running_confirmed":False,
                  "g1_receipt_issuance_allowed":False,
                  "g2_provider_call_allowed":False,
                  "execution_allowed":False}
            stdout=StringIO()
            with patch("indodax_ai_bot.__main__.inspect_public_ws_systemd_unit",
                       return_value=fake):
                with redirect_stdout(stdout):
                    rc=main(["--public-ws-unit-status","--db",str(db)])
            body=json.loads(stdout.getvalue())
            self.assertEqual(rc,0)
            self.assertEqual(body["final_decision"],"REJECT")
            self.assertFalse(body["live_trading_authorized"])
            self.assertFalse(body["execution_allowed"])
            self.assertFalse(db.exists())

    def test_cli_rejects_changed_unit_with_nonzero(self):
        bad={"status":"REJECT","reason":"PUBLIC_WS_INSTALLED_UNIT_DIFFERS",
             "g1_receipt_issuance_allowed":False,"execution_allowed":False}
        buf=StringIO()
        with patch("indodax_ai_bot.__main__.inspect_public_ws_systemd_unit",
                   return_value=bad):
            with redirect_stdout(buf):
                rc=main(["--public-ws-unit-status"])
        self.assertEqual(rc,3)
        self.assertEqual(json.loads(buf.getvalue())["final_decision"],"REJECT")


if __name__ == "__main__":
    unittest.main()
