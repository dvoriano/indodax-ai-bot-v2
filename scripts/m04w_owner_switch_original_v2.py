"""One-time owner-only original V2 BTC PUBLIC 10x30 -> persistent 4x30 unit switch.

Preflight is read-only. Activation backs up EXACT old canonical installed user
unit to owner-private 0600 and atomically installs the new source. It never
stops/starts/restarts a service, changes a timer, policy, HOLD, DB, scanner,
Telegram, account, paid adapter or BUY/SELL. The existing timer starts naturally.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

from indodax_ai_bot.btc_ops_gate import AdmissionDenied, _policy_checks
from scripts.m10_btc_owner_bounded_recovery import (
    DROP, TEMPLATE, _units, assess_original_btc_systemd,
    BtcRecoveryDenied,
)

ROOT = Path.home() / "indodax-ai-bot-v2"
UNIT = Path.home() / ".config/systemd/user/indodax-v2-public-ws-btc.service"
NEW_TEMPLATE = ROOT / "deploy/indodax-v2-public-ws-btc.service"
PIN_OLD = "725e4552713e36cab9e95cca4c5bbe7cce76d7f9"
PATH_IN_REPO = "deploy/indodax-v2-public-ws-btc.service"
TOKEN = "OWNER_SWITCH_ORIGINAL_V2_BTC_PUBLIC_4X30_PERSISTENT"


class OwnerSwitchDenied(ValueError):
    pass


def _deny(code: str):
    raise OwnerSwitchDenied(code)


def _git(*args: str) -> str:
    try:
        p = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=10, check=False,
        )
        if p.returncode != 0 or len(p.stdout) > 32768:
            _deny("M04W_ORIGINAL_GIT_UNVERIFIED")
        return p.stdout.decode("utf-8").strip()
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        _deny("M04W_ORIGINAL_GIT_UNVERIFIED")


def _read_old_template() -> bytes:
    try:
        p = subprocess.run(
            ["git", "-C", str(ROOT), "show", PIN_OLD + ":" + PATH_IN_REPO],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=10, check=False,
        )
        if p.returncode or not 100 <= len(p.stdout) <= 8192:
            _deny("M04W_OLD_CANONICAL_TEMPLATE_UNAVAILABLE")
        return p.stdout
    except (OSError, subprocess.TimeoutExpired):
        _deny("M04W_OLD_CANONICAL_TEMPLATE_UNAVAILABLE")


def preflight_readonly() -> tuple[str, bytes]:
    if (_git("branch", "--show-current") != "main"
            or _git("status", "--porcelain")
            or not _git("rev-parse", "HEAD")):
        _deny("M04W_ORIGINAL_MAIN_NOT_CLEAN")
    _git("merge-base", "--is-ancestor", PIN_OLD, "HEAD")
    if (NEW_TEMPLATE.is_symlink() or UNIT.is_symlink()
            or DROP.is_symlink() or TEMPLATE.is_symlink()
            or not UNIT.is_file() or not DROP.is_file()
            or DROP.read_bytes() != TEMPLATE.read_bytes()
            or not NEW_TEMPLATE.is_file()):
        _deny("M04W_ORIGINAL_FILES_UNVERIFIED")
    try:
        st = UNIT.stat()
        if (st.st_uid != os.geteuid() or not stat.S_ISREG(st.st_mode)
                or st.st_size > 8192
                or not stat.S_ISDIR(UNIT.parent.lstat().st_mode)
                or UNIT.parent.stat().st_uid != os.geteuid()):
            _deny("M04W_UNIT_OWNER_UNTRUSTED")
        new = NEW_TEMPLATE.read_bytes()
        old = _read_old_template()
        if (new.count(b"--public-ws-supervised-continuous") != 1
                or new.count(b"--collector-sessions 4") != 1
                or new.count(b"--stream-seconds 30") != 1
                or new.count(b"--collector-pause-seconds 0") != 1
                or b"INDODAX_TRADING_ENABLED=NO" not in new
                or b"INDODAX_ALLOW_LIVE_EXECUTION=NO" not in new
                or b"INDODAX_EXECUTION_MODE=DRY_RUN" not in new):
            _deny("M04W_NEW_PUBLIC_UNIT_INVALID")
        actual = UNIT.read_bytes()
        if actual == new:
            mode = "ALREADY_INSTALLED"
        elif actual == old:
            mode = "EXACT_OLD_INSTALLED"
        else:
            _deny("M04W_INSTALLED_UNIT_NOT_CANONICAL")
        values = _units()
        assess_original_btc_systemd(values=values)
        # The original standing scope/offset/gap must agree before changing
        # the unit's future ExecStart. This does not admit another session.
        rt = ROOT / "runtime"
        _policy_checks(
            rt / "btc_public_ops.go.json",
            rt / "public_ws_btc.status.json",
            rt / "public_ws_btc.sqlite3",
            rt / "btc_public_ops.hold.json",
            int(time.time()),
        )
        if mode == "EXACT_OLD_INSTALLED":
            if ("--public-ws-supervised-once" not in values["execstart"]
                    or "--public-ws-supervised-continuous" in values["execstart"]):
                _deny("M04W_OLD_RUNNING_ENTRYPOINT_UNVERIFIED")
        elif "--public-ws-supervised-continuous" not in values["execstart"]:
            _deny("M04W_NEW_LOADED_ENTRYPOINT_UNVERIFIED")
    except (AdmissionDenied, BtcRecoveryDenied):
        _deny("M04W_ORIGINAL_GATE_OR_SYSTEMD_NOT_READY")
    except (OSError, UnicodeError):
        _deny("M04W_ORIGINAL_FILES_UNVERIFIED")
    return mode, new


def switch_original_unit() -> dict:
    mode, new = preflight_readonly()
    if mode == "ALREADY_INSTALLED":
        return {"status": "M04W_ALREADY_INSTALLED", "restart_or_order": False,
                "trading_enabled": False}
    before = UNIT.read_bytes()
    tag = str(int(time.time())) + "." + str(os.getpid())
    backup = UNIT.with_name(UNIT.name + ".pre-m04w." + tag + ".bak")
    stage = UNIT.with_name(UNIT.name + ".m04w." + tag + ".tmp")
    if (backup.exists() or backup.is_symlink()
            or stage.exists() or stage.is_symlink()):
        _deny("M04W_PRIVATE_BACKUP_CONFLICT")
    try:
        # Verify original still inactive and not edited during preflight.
        if UNIT.read_bytes() != before or _units()["service_active"] != "inactive":
            _deny("M04W_ORIGINAL_CHANGED_DURING_INSTALL")
        for path, raw, mode_bits in (
            (backup, before, 0o600),
            (stage, new, stat.S_IMODE(UNIT.stat().st_mode)),
        ):
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                         | getattr(os, "O_NOFOLLOW", 0), mode_bits)
            with os.fdopen(fd, "wb") as out:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(path, mode_bits)
        if UNIT.read_bytes() != before or _units()["service_active"] != "inactive":
            _deny("M04W_ORIGINAL_CHANGED_DURING_INSTALL")
        os.replace(stage, UNIT)
        p = subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=12, check=False,
        )
        if p.returncode:
            _deny("M04W_DAEMON_RELOAD_FAILED")
        loaded = _units()
        if (loaded.get("needs_reload") != "no"
                or "--public-ws-supervised-continuous" not in
                    loaded.get("execstart", "")
                or "--collector-sessions 4" not in loaded.get("execstart", "")
                or loaded.get("restart") != "no"
                or loaded.get("timer_active") != "active"
                or loaded.get("m05_active") != "active"):
            _deny("M04W_POST_INSTALL_SYSTEMD_UNVERIFIED")
        return {
            "status": "M04W_ORIGINAL_FOUR_WINDOW_UNIT_INSTALLED",
            "private_backup_created": True,
            "service_started_or_restarted": False,
            "timer_changed": False,
            "m03_or_telegram_changed": False,
            "live_market_data_verified": False,
            "trading_enabled": False,
            "order_sent": False,
        }
    except (OSError, subprocess.TimeoutExpired):
        _deny("M04W_INSTALL_UNAVAILABLE")
    finally:
        stage.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Original V2 PUBLIC BTC owner unit migration")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--diagnose-readonly", action="store_true")
    group.add_argument("--owner-switch-to-four-windows", action="store_true")
    parser.add_argument("--owner-confirm")
    args = parser.parse_args()
    if args.owner_switch_to_four_windows and args.owner_confirm != TOKEN:
        print(json.dumps({"status": "DENIED", "reason": "M04W_OWNER_CONFIRM_REQUIRED"}))
        return 2
    try:
        if args.diagnose_readonly:
            mode, _ = preflight_readonly()
            report = {"status": "M04W_ORIGINAL_UNIT_PREFLIGHT_READONLY",
                      "mode": mode, "service_or_timer_modified": False,
                      "order_sent": False}
        else:
            report = switch_original_unit()
        print(json.dumps(report, sort_keys=True))
        return 0
    except OwnerSwitchDenied as exc:
        code = str(exc)
        print(json.dumps({"status": "DENIED",
                          "reason": code if re.fullmatch(r"M04W_[A-Z0-9_]{1,80}", code)
                                    else "M04W_PREFLIGHT_UNAVAILABLE"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
