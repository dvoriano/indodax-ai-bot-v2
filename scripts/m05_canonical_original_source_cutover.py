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
) -> str:
    """Require exact original V2 service except its release source path."""
    old_abs = str(home / _LEGACY)
    new_abs = str(home / _MAIN)
    old_rel, new_rel = "%h/" + _LEGACY, "%h/" + _MAIN

    # An existing canonical unit is an idempotent no-op.
    if installed in (canonical, canonical.replace("%h", str(home))):
        return "ALREADY_CANONICAL"
    if installed.count(_EXEC) != 1 or installed.count("ExecStart=") != 1:
        raise M05CutoverDenied("M05_CUTOVER_UNKNOWN_EXEC")
    if (_LEGACY not in installed
            or ("WorkingDirectory=" + old_rel not in installed
                and "WorkingDirectory=" + old_abs not in installed)):
        raise M05CutoverDenied("M05_CUTOVER_RELEASE_SOURCE_NOT_CONFIRMED")
    normalized = installed.replace(old_abs, new_abs).replace(old_rel, new_rel)
    if normalized not in (canonical, canonical.replace("%h", str(home))):
        raise M05CutoverDenied("M05_CUTOVER_UNREVIEWED_UNIT_DIFFERENCE")
    return "LEGACY_RELEASE_SOURCE"


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
        desired = template.read_bytes()
        canonical = desired.decode("utf-8")
    except (OSError, UnicodeError):
        raise M05CutoverDenied("M05_CUTOVER_SERVICE_TEXT_UNAVAILABLE") from None

    source = _same_service_except_checkout(installed, canonical, home)
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

    if prop(SERVICE, "ActiveState") != "inactive":
        raise M05CutoverDenied("M05_CUTOVER_SERVICE_NOT_IDLE")
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
