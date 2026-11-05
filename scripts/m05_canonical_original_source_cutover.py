"""One-owner original V2 M05 source switch, preserving the existing bot/timer.

The owner originally installed M05 from a frozen V2 *release checkout*.
This one-time, owner-opt-in cutover changes ONLY the already-installed M05
oneshot service to the canonical ~/indodax-ai-bot-v2 source. It never
creates another bot, timer, owner database, token file or poller.
The existing 3-second M05 timer file/cadence is kept byte-for-byte.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable

SERVICE = "indodax-v2-m05-telegram.service"
TIMER = "indodax-v2-m05-telegram.timer"
_M03_TIMER = "indodax-v2-m03-mainpath.timer"
_BTC_TIMER = "indodax-v2-public-ws-btc.timer"
_LEGACY = "indodax-ai-bot-v2-m05-release"
_MAIN = "indodax-ai-bot-v2"
_EXEC = "scripts/m05_telegram_service_tick_entry.py"
_OWNER_ID = re.compile(r"[1-9][0-9]{0,18}\Z")


class M05CutoverDenied(ValueError):
    """Stable short error code; never print private config or service text."""


def _systemctl(*args: str) -> str:
    try:
        p = subprocess.run(
            ["/usr/bin/systemctl", "--user", "--no-pager", *args],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if p.returncode or len(p.stdout) > 32768:
            raise M05CutoverDenied("M05_CUTOVER_SYSTEMD_UNAVAILABLE")
        return p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        raise M05CutoverDenied("M05_CUTOVER_SYSTEMD_UNAVAILABLE") from None


def _private(path: Path, *, max_size: int) -> None:
    try:
        st, parent = path.lstat(), path.parent.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                or st.st_uid != os.geteuid()
                or stat.S_IMODE(st.st_mode) != 0o600
                or not 0 < st.st_size <= max_size
                or not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != os.geteuid()
                or stat.S_IMODE(parent.st_mode) != 0o700):
            raise M05CutoverDenied("M05_CUTOVER_PRIVATE_FILE_UNTRUSTED")
    except OSError:
        raise M05CutoverDenied("M05_CUTOVER_PRIVATE_FILE_UNAVAILABLE") from None


def _owner_state(env: Path, home: Path) -> None:
    """Only read DB path/owner ID. Never read Telegram BotFather token."""
    _private(env, max_size=8192)
    try:
        names = {}
        for raw in env.read_text("utf-8").splitlines():
            if not raw or raw.lstrip().startswith("#") or "=" not in raw:
                continue
            key, val = raw.split("=", 1)
            if key in {"M05_TELEGRAM_OWNER_DB", "M05_TELEGRAM_OWNER_ID"}:
                names.setdefault(key, []).append(
                    val.strip().strip('"').strip("'")
                )
        if (set(names) != {"M05_TELEGRAM_OWNER_DB", "M05_TELEGRAM_OWNER_ID"}
                or any(len(items) != 1 for items in names.values())):
            raise M05CutoverDenied("M05_CUTOVER_OWNER_CONFIG_UNVERIFIED")
        raw_id = names["M05_TELEGRAM_OWNER_ID"][0]
        if _OWNER_ID.fullmatch(raw_id) is None:
            raise M05CutoverDenied("M05_CUTOVER_OWNER_CONFIG_UNVERIFIED")
        owner_id = int(raw_id)
        db = Path(names["M05_TELEGRAM_OWNER_DB"][0])
        if (not db.is_absolute() or db.is_symlink()
                or not db.is_relative_to(home)):
            raise M05CutoverDenied("M05_CUTOVER_OWNER_DB_PATH_INVALID")
        _private(db, max_size=100_000_000)
        with closing(sqlite3.connect(
            db.as_uri() + "?mode=ro", uri=True, timeout=5,
        )) as con:
            con.execute("PRAGMA query_only=ON")
            if con.execute(
                "SELECT owner_user_id, mode FROM m05_config WHERE singleton=1"
            ).fetchone() != (owner_id, "MANUAL"):
                raise M05CutoverDenied("M05_CUTOVER_OWNER_MODE_OR_ID_MISMATCH")
            for table, column, value in (
                ("m05_telegram_updates", "status", "CLAIMED"),
                ("m05_market_advisory_outbox", "delivery_state", "CLAIMED"),
                ("m05_manual_trade_previews", "send_state", "CLAIMED"),
            ):
                if con.execute(
                    f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1",
                    (value,),
                ).fetchone() is not None:
                    raise M05CutoverDenied("M05_CUTOVER_OUTSTANDING_OWNER_CLAIM")
            if con.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
                raise M05CutoverDenied("M05_CUTOVER_OWNER_DB_QUICK_CHECK_FAILED")
    except M05CutoverDenied:
        raise
    except (UnicodeError, OSError, sqlite3.Error):
        raise M05CutoverDenied("M05_CUTOVER_OWNER_DB_UNVERIFIED") from None


def _same_service_except_checkout(
    installed: str, canonical: str, home: Path,
) -> tuple[str, bytes]:
    """Preserve installed V2 service byte-for-byte except two source paths.

    The original M05 owner service can have extra VPS-only systemd hardening
    and a distinct 3-second timer. Reject any different executable, missing
    live-off settings, unsafe env replacement, or duplicate service commands,
    but do NOT demand every original harmless directive match a fresh
    template whose version may have changed since deployment.
    """
    old_abs = str(home / _LEGACY)
    new_abs = str(home / _MAIN)
    old_rel, new_rel = "%h/" + _LEGACY, "%h/" + _MAIN

    def _canonicalize(value: str) -> str:
        return value.replace(old_abs, new_abs).replace(old_rel, new_rel)

    def _fields(src: str) -> dict[str, list[str]]:
        fields: dict[str, list[str]] = {}
        section = ""
        for raw in src.splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line
                continue
            if section != "[Service]":
                continue
            key, sep, value = line.partition("=")
            if not sep:
                raise M05CutoverDenied("M05_CUTOVER_INVALID_SERVICE_LINE")
            fields.setdefault(key.strip(), []).append(value.strip())
        return fields

    current, expected = _fields(installed), _fields(canonical)
    for key in ("WorkingDirectory", "ExecStart", "EnvironmentFile", "Type"):
        if len(current.get(key, [])) != 1 or len(expected.get(key, [])) != 1:
            raise M05CutoverDenied("M05_CUTOVER_SERVICE_FIELD_NOT_UNIQUE")
    if current["Type"] != ["oneshot"]:
        raise M05CutoverDenied("M05_CUTOVER_UNEXPECTED_SERVICE_TYPE")
    if len(current.get("ExecStartPre", [])) or len(current.get("ExecStartPost", [])):
        raise M05CutoverDenied("M05_CUTOVER_ADDITIONAL_COMMANDS_UNREVIEWED")
    if len(current.get("ExecCondition", [])) or len(current.get("ExecReload", [])):
        raise M05CutoverDenied("M05_CUTOVER_ADDITIONAL_COMMANDS_UNREVIEWED")

    expected_work = expected["WorkingDirectory"][0]
    expected_exec = expected["ExecStart"][0]
    for key, expected_value in (
        ("ExecStart", expected_exec), ("WorkingDirectory", expected_work),
        ("EnvironmentFile", expected["EnvironmentFile"][0]),
    ):
        observed = _canonicalize(current[key][0])
        if observed not in (
            expected_value,
            expected_value.replace("%h", str(home)),
        ):
            raise M05CutoverDenied("M05_CUTOVER_ORIGINAL_EXEC_OR_ENV_DIFFERS")
    # Prevent a legacy unit with multiple or contradictory trading settings
    # from silently replacing one hardlock through a later Environment line.
    critical = {
        "INDODAX_TRADING_ENABLED": "NO",
        "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
        "INDODAX_EXECUTION_MODE": "DRY_RUN",
        "M05_TELEGRAM_TICK_GO": "YES",
        "M05_TELEGRAM_NETWORK_GO": "YES",
        "M05_TELEGRAM_DEDICATED_GO": "YES",
    }
    for key, value in critical.items():
        matches = [
            line for line in current.get("Environment", [])
            if key + "=" in line
        ]
        if matches != [key + "=" + value]:
            raise M05CutoverDenied("M05_CUTOVER_REQUIRED_HARDLOCK_MISSING")
    if any(
        line.startswith(("PYTHONPATH=", "PYTHONHOME=", "LD_PRELOAD="))
        for line in current.get("Environment", [])
    ):
        raise M05CutoverDenied("M05_CUTOVER_EXTRA_CODE_LOADING_ENV")
    if any(key.startswith("Exec") and key != "ExecStart" for key in current):
        raise M05CutoverDenied("M05_CUTOVER_UNKNOWN_EXEC_DIRECTIVE")

    work = current["WorkingDirectory"][0]
    exec_line = current["ExecStart"][0]
    is_old = _LEGACY in work and _LEGACY in exec_line
    is_main = _MAIN in work and _MAIN in exec_line and _LEGACY not in work + exec_line
    if not is_old and not is_main:
        raise M05CutoverDenied("M05_CUTOVER_RELEASE_SOURCE_NOT_CONFIRMED")

    if is_main:
        return "ALREADY_CANONICAL", installed.encode("utf-8")

    # Edit ONLY these exact two lines and preserve all other service settings
    # from the operator's functioning V2 release, including timeout/hardening.
    section = ""
    result: list[str] = []
    changed = set()
    for raw in installed.splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        suffix = raw[len(line):]
        if line.strip().startswith("[") and line.strip().endswith("]"):
            section = line.strip()
        if section == "[Service]":
            for key in ("WorkingDirectory", "ExecStart"):
                if line.startswith(key + "="):
                    line = _canonicalize(line)
                    changed.add(key)
        result.append(line + suffix)
    if changed != {"WorkingDirectory", "ExecStart"}:
        raise M05CutoverDenied("M05_CUTOVER_SOURCE_LINE_REWRITE_FAILED")
    return "LEGACY_RELEASE_SOURCE", "".join(result).encode("utf-8")


def _atomic_replace(target: Path, data: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".m05-canonical-", dir=target.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def switch_original_m05_source(
    *, home: Path, run: Callable[..., str] = _systemctl,
) -> dict:
    root = home / _MAIN
    if (root.is_symlink() or not root.is_dir()
            or not (root / "indodax_ai_bot" / "telegram_service_tick.py").is_file()
            or not (root / _EXEC).is_file()):
        raise M05CutoverDenied("M05_CUTOVER_CANONICAL_SOURCE_UNAVAILABLE")
    # Do not switch to an old or untested original V2 checkout.
    if ("public_reference" not in
            (root / "indodax_ai_bot" / "owner_control.py").read_text("utf-8")):
        raise M05CutoverDenied("M05_CUTOVER_SOURCE_FEATURES_NOT_SYNCED")

    unit_dir = home / ".config/systemd/user"
    unit = unit_dir / SERVICE
    template = root / "systemd/user" / SERVICE
    if (unit.is_symlink() or template.is_symlink()
            or not unit.is_file() or not template.is_file()):
        raise M05CutoverDenied("M05_CUTOVER_INSTALLED_UNIT_NOT_REGULAR")
    try:
        st = unit.stat()
        if st.st_uid != os.geteuid():
            raise M05CutoverDenied("M05_CUTOVER_UNIT_OWNERSHIP_UNVERIFIED")
        before = unit.read_bytes()
        installed = before.decode("utf-8")
        canonical = template.read_text("utf-8")
    except (OSError, UnicodeError):
        raise M05CutoverDenied("M05_CUTOVER_SERVICE_TEXT_UNAVAILABLE") from None

    source, desired = _same_service_except_checkout(installed, canonical, home)
    def prop(which: str, field: str) -> str:
        return run("show", "-P", field, which)
    if (prop(SERVICE, "FragmentPath") != str(unit)
            or prop(SERVICE, "NeedDaemonReload") != "no"
            or prop(SERVICE, "DropInPaths") not in ("",)
            or prop(SERVICE, "Restart") != "no"
            or prop(TIMER, "ActiveState") != "active"
            or prop(TIMER, "UnitFileState") != "enabled"
            or prop(_M03_TIMER, "ActiveState") != "active"
            or prop(_BTC_TIMER, "ActiveState") != "active"):
        raise M05CutoverDenied("M05_CUTOVER_UNIT_METADATA_UNVERIFIED")
    _owner_state(
        home / ".config/indodax-ai-bot-v2/m05/service.env", home,
    )
    if source == "ALREADY_CANONICAL":
        if (prop(SERVICE, "WorkingDirectory") != str(root)
                or str(root / _EXEC) not in prop(SERVICE, "ExecStart")):
            raise M05CutoverDenied("M05_CUTOVER_CANONICAL_LOAD_NOT_VERIFIED")
        return {
            "status": "M05_CANONICAL_ORIGINAL_V2_ALREADY_INSTALLED",
            "service_changed": False, "original_timer_preserved": True,
            "bot_or_owner_db_changed": False, "trading_enabled": False,
        }

    # The original 3-second timer may briefly have the oneshot running.
    # Do not reject based on this race before pausing timer; the next block
    # waits for the existing process to finish before switching the source.
    if (str(home / _LEGACY / _EXEC) not in prop(SERVICE, "ExecStart")
            or prop(SERVICE, "WorkingDirectory") != str(home / _LEGACY)):
        raise M05CutoverDenied("M05_CUTOVER_LOADED_SOURCE_DIFFERS")

    # Operator backup in existing private user systemd directory; never
    # change the timer unit, owner DB, token, old V2 release checkout, or
    # M03/BTC services.
    fd, backup_name = tempfile.mkstemp(
        prefix=".m05-service-before-canonical-", suffix=".bak",
        dir=unit_dir,
    )
    backup = Path(backup_name)
    with os.fdopen(fd, "wb") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(before)
        f.flush()
        os.fsync(f.fileno())

    stopped = False
    changed = False
    try:
        run("stop", TIMER)
        stopped = True
        # Stop timer first. A running oneshot is allowed to finish naturally.
        for _ in range(12):
            if prop(SERVICE, "ActiveState") == "inactive":
                break
            time.sleep(1)
        if prop(SERVICE, "ActiveState") != "inactive":
            raise M05CutoverDenied("M05_CUTOVER_SERVICE_BUSY_AFTER_TIMER_PAUSE")
        # Another local agent must not change the installed unit during pause.
        if unit.read_bytes() != before:
            raise M05CutoverDenied("M05_CUTOVER_CONCURRENT_UNIT_MODIFICATION")
        _atomic_replace(unit, desired)
        changed = True
        run("daemon-reload")
        if (prop(SERVICE, "WorkingDirectory") != str(root)
                or str(root / _EXEC) not in prop(SERVICE, "ExecStart")
                or prop(SERVICE, "NeedDaemonReload") != "no"
                or prop(SERVICE, "Restart") != "no"):
            raise M05CutoverDenied("M05_CUTOVER_NEW_SOURCE_NOT_LOADED")
        run("start", TIMER)
        stopped = False
        if (prop(TIMER, "ActiveState") != "active"
                or prop(TIMER, "UnitFileState") != "enabled"):
            raise M05CutoverDenied("M05_CUTOVER_TIMER_RESTORE_FAILED")
    except BaseException:
        # Restore the actual original unit and original timer. No second
        # sender/poller is ever created.
        if changed:
            _atomic_replace(unit, before)
            try:
                run("daemon-reload")
            except M05CutoverDenied:
                pass
        if stopped:
            try:
                run("start", TIMER)
            except M05CutoverDenied:
                pass
        raise

    return {
        "status": "M05_CANONICAL_ORIGINAL_V2_SOURCE_CUTOVER_PASS",
        "service_changed": True,
        "original_timer_preserved": True,
        "original_timer_active": True,
        "backed_up_original_unit": True,
        "backup_file_name": backup.name,
        "old_release_checkout_modified": False,
        "existing_bot_token_or_owner_db_modified": False,
        "m03_timer_modified": False,
        "btc_timer_modified": False,
        "live_trading_enabled": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One original M05 sender, canonical V2 source only"
    )
    parser.add_argument("--owner-approve-existing-m05-source-cutover",
                        action="store_true")
    a = parser.parse_args()
    if not a.owner_approve_existing_m05_source_cutover:
        print('{"status":"DENIED","reason":"M05_CUTOVER_OWNER_APPROVAL_REQUIRED"}')
        return 2
    try:
        report = switch_original_m05_source(home=Path.home())
        print(json.dumps(report, sort_keys=True))
        return 0
    except M05CutoverDenied as exc:
        print(json.dumps({
            "status": "DENIED", "reason": str(exc)
        }, sort_keys=True))
        return 2
    except (OSError, UnicodeError, ValueError, sqlite3.Error):
        print('{"status":"DENIED","reason":"M05_CUTOVER_LOCAL_ERROR"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
