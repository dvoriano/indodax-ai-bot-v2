"""Owner-scoped original V2 BTC 4x30 -> 2x30 PUBLIC persistent cutover.

Stage: guarded 0600 backup + atomic original systemd unit replacement, no
process restart. Activate: separately gated original-unit restart AFTER existing
600-second admission cooldown passes; timer and original M03/M05 stay enabled.
Never alter owner GO/HOLD, database, exchange permissions or bypass HTTP403.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from indodax_ai_bot.btc_ops_gate import (
    AdmissionDenied, _policy_checks, diagnose_once,
)
from scripts.m10_btc_owner_bounded_recovery import (
    DROP, TEMPLATE, _units, assess_original_btc_systemd, BtcRecoveryDenied,
)

ROOT = Path.home() / "indodax-ai-bot-v2"
UNIT = Path.home() / ".config/systemd/user/indodax-v2-public-ws-btc.service"
SOURCE_UNIT = ROOT / "deploy/indodax-v2-public-ws-btc.service"
OLD_SHA = "809387414e6c76682d74c297124b0ef5ca9e58a5"
SOURCE_PATH = "deploy/indodax-v2-public-ws-btc.service"
UNIT_NAME = "indodax-v2-public-ws-btc.service"
TOKEN = "OWNER_APPROVE_ORIGINAL_V2_BTC_2X30_CUTOVER"


class CutoverDenied(ValueError):
    pass


def _deny(code: str) -> None:
    raise CutoverDenied(code)


def _command(cmd: list[str], *, timeout: int = 12) -> bytes:
    try:
        out = subprocess.run(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=timeout,
                             check=False)
        if out.returncode or len(out.stdout) > 16384:
            _deny("M04X_COMMAND_FAILED")
        return out.stdout
    except (OSError, subprocess.TimeoutExpired):
        _deny("M04X_COMMAND_UNAVAILABLE")


def _git(*args: str) -> bytes:
    return _command(["git", "-C", str(ROOT), *args])


def _old_unit_bytes() -> bytes:
    raw = _git("show", OLD_SHA + ":" + SOURCE_PATH)
    if (not 100 <= len(raw) <= 8192
            or raw.count(b"--collector-sessions 4") != 1
            or raw.count(b"--public-ws-supervised-continuous") != 1):
        _deny("M04X_CANONICAL_4X30_UNIT_UNAVAILABLE")
    return raw


def _new_unit_bytes() -> bytes:
    if SOURCE_UNIT.is_symlink() or not SOURCE_UNIT.is_file():
        _deny("M04X_NEW_UNIT_UNAVAILABLE")
    raw = SOURCE_UNIT.read_bytes()
    if (not 100 <= len(raw) <= 8192
            or raw.count(b"--collector-sessions 2") != 1
            or raw.count(b"--collector-sessions 4")
            or raw.count(b"--stream-seconds 30") != 1
            or raw.count(b"--collector-pause-seconds 0") != 1
            or raw.count(b"--public-ws-supervised-continuous") != 1
            or raw.count(b"--public-ws-reconnect-until-recovered") != 1
            or b"INDODAX_TRADING_ENABLED=NO" not in raw
            or b"INDODAX_ALLOW_LIVE_EXECUTION=NO" not in raw
            or b"INDODAX_EXECUTION_MODE=DRY_RUN" not in raw):
        _deny("M04X_NEW_UNIT_INVALID")
    return raw


def _unit_state() -> tuple[dict[str, str], int]:
    values = _units()
    # The existing M10 inspector rejects service_active=active because it
    # was written for recovery. Test every OTHER original boundary unchanged.
    try:
        assess_original_btc_systemd(
            values={**values, "service_active": "inactive"},
        )
    except BtcRecoveryDenied:
        _deny("M04X_ORIGINAL_SYSTEMD_NOT_CANONICAL")
    if values["service_active"] != "active":
        _deny("M04X_ORIGINAL_COLLECTOR_NOT_ACTIVE")
    try:
        raw = _command(["systemctl", "--user", "show", "-P",
                        "MainPID", UNIT_NAME])
        pid = int(raw.decode("ascii").strip())
    except (UnicodeError, ValueError):
        _deny("M04X_ORIGINAL_PID_UNVERIFIED")
    if pid <= 0:
        _deny("M04X_ORIGINAL_PID_UNVERIFIED")
    return values, pid


def preflight_readonly() -> dict:
    if (_git("branch", "--show-current").strip() != b"main"
            or _git("status", "--porcelain").strip()
            or not _git("rev-parse", "HEAD").strip()):
        _deny("M04X_ORIGINAL_MAIN_NOT_CLEAN")
    _git("merge-base", "--is-ancestor", OLD_SHA, "HEAD")
    if (UNIT.is_symlink() or not UNIT.is_file()
            or UNIT.parent.is_symlink()
            or DROP.is_symlink() or TEMPLATE.is_symlink()
            or not DROP.is_file() or not TEMPLATE.is_file()
            or DROP.read_bytes() != TEMPLATE.read_bytes()):
        _deny("M04X_ORIGINAL_FILES_UNVERIFIED")
    st = UNIT.stat()
    if (st.st_uid != os.geteuid() or st.st_size > 8192
            or not stat.S_ISREG(st.st_mode)
            or st.st_mode & 0o022):
        _deny("M04X_ORIGINAL_UNIT_OWNER_UNTRUSTED")
    old, new = _old_unit_bytes(), _new_unit_bytes()
    installed = UNIT.read_bytes()
    if installed == old:
        mode = "FOUR_WINDOWS_INSTALLED_RUNNING"
    elif installed == new:
        mode = "TWO_WINDOWS_UNIT_STAGED"
    else:
        _deny("M04X_ORIGINAL_UNIT_CONTENT_CHANGED")
    values, pid = _unit_state()
    installed_count = "4" if mode == "FOUR_WINDOWS_INSTALLED_RUNNING" else "2"
    if ("--public-ws-supervised-continuous" not in values["execstart"]
            or "--collector-sessions " + installed_count
               not in values["execstart"]):
        _deny("M04X_ORIGINAL_EXEC_ENTRYPOINT_CHANGED")
    rt = ROOT / "runtime"
    try:
        _policy_checks(rt / "btc_public_ops.go.json",
                       rt / "public_ws_btc.status.json",
                       rt / "public_ws_btc.sqlite3",
                       rt / "btc_public_ops.hold.json",
                       int(time.time()))
    except AdmissionDenied:
        _deny("M04X_ORIGINAL_OWNER_GO_OR_BTC_UNHEALTHY")
    return {"mode": mode, "current_pid": pid,
            "original_unit_verified": True,
            "service_restarted": False, "orders_sent": 0}


def _exclusive_private_file(path: Path, content: bytes, perms: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0), perms)
    with os.fdopen(fd, "wb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(path, perms)


def stage_two_windows() -> dict:
    snap = preflight_readonly()
    if snap["mode"] == "TWO_WINDOWS_UNIT_STAGED":
        return {"status": "M04X_2X30_UNIT_ALREADY_STAGED",
                "service_restarted": False, "orders_sent": 0}
    old, new = UNIT.read_bytes(), _new_unit_bytes()
    suffix = str(int(time.time())) + "." + str(os.getpid())
    backup = UNIT.with_name(UNIT.name + ".pre-m04x." + suffix + ".bak")
    staged = UNIT.with_name(UNIT.name + ".m04x." + suffix + ".tmp")
    if backup.exists() or backup.is_symlink() or staged.exists() or staged.is_symlink():
        _deny("M04X_BACKUP_CONFLICT")
    try:
        if (UNIT.read_bytes() != old
                or _unit_state()[1] != snap["current_pid"]):
            _deny("M04X_COLLECTOR_CHANGED_DURING_STAGE")
        _exclusive_private_file(backup, old, 0o600)
        _exclusive_private_file(staged, new, stat.S_IMODE(UNIT.stat().st_mode))
        if (UNIT.read_bytes() != old
                or _unit_state()[1] != snap["current_pid"]):
            _deny("M04X_COLLECTOR_CHANGED_DURING_STAGE")
        os.replace(staged, UNIT)
        _command(["systemctl", "--user", "daemon-reload"])
        after = preflight_readonly()
        if (after["mode"] != "TWO_WINDOWS_UNIT_STAGED"
                or after["current_pid"] != snap["current_pid"]):
            _deny("M04X_POST_STAGE_UNVERIFIED")
        return {"status": "M04X_2X30_UNIT_STAGED",
                "original_pid_still_running": True,
                "old_4x30_unit_private_backup": True,
                "service_restarted": False, "orders_sent": 0}
    except OSError:
        _deny("M04X_STAGE_UNAVAILABLE")
    finally:
        staged.unlink(missing_ok=True)


def activate_two_windows() -> dict:
    snap = preflight_readonly()
    if snap["mode"] != "TWO_WINDOWS_UNIT_STAGED":
        _deny("M04X_STAGE_2X30_UNIT_FIRST")
    rt = ROOT / "runtime"
    diagnosis = diagnose_once(
        go=rt / "btc_public_ops.go.json",
        status=rt / "public_ws_btc.status.json",
        database=rt / "public_ws_btc.sqlite3",
        hold=rt / "btc_public_ops.hold.json",
        state=rt / "btc_public_ops.admission.json",
    )
    if diagnosis["first_blocker"] != "NONE":
        _deny("M04X_ORIGINAL_COOLDOWN_OR_POLICY_BLOCKED")
    # Exactly one original service restart, guarded by the original
    # ExecCondition. No timer/GO/HOLD/db or other service modifications.
    _command(["systemctl", "--user", "restart", UNIT_NAME], timeout=25)
    values, next_pid = _unit_state()
    if ("--collector-sessions 2" not in values["execstart"]
            or next_pid == snap["current_pid"]):
        _deny("M04X_NEW_PROCESS_NOT_ATTESTED")
    return {"status": "M04X_2X30_NEW_PROCESS_STARTED",
            "previous_pid": snap["current_pid"],
            "new_pid": next_pid,
            "two_window_checkpoint_verified": False,
            "original_timer_modified": False,
            "m03_m05_modified": False,
            "trading_enabled": False, "orders_sent": 0}


def main() -> int:
    p = argparse.ArgumentParser(description="Owner original V2 BTC 4x30→2x30")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--diagnose-readonly", action="store_true")
    g.add_argument("--owner-stage-2x30", action="store_true")
    g.add_argument("--owner-activate-2x30", action="store_true")
    p.add_argument("--owner-confirm")
    args = p.parse_args()
    if ((args.owner_stage_2x30 or args.owner_activate_2x30)
            and args.owner_confirm != TOKEN):
        print(json.dumps({"status": "DENIED", "reason": "M04X_OWNER_CONFIRM_REQUIRED"}))
        return 2
    try:
        result = (preflight_readonly() if args.diagnose_readonly
                  else stage_two_windows() if args.owner_stage_2x30
                  else activate_two_windows())
        print(json.dumps(result, sort_keys=True))
        return 0
    except (CutoverDenied, BtcRecoveryDenied, AdmissionDenied,
            OSError, ValueError) as exc:
        code = str(exc)
        print(json.dumps({"status": "DENIED",
                          "reason": code if re.fullmatch(r"M04X_[A-Z0-9_]{1,100}", code)
                                    else "M04X_CUTOVER_UNAVAILABLE"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
