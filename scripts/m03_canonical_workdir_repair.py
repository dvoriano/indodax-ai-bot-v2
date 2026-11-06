"""Bounded owner-authorized correction for ONE original V2 M03 service.

Fix ONLY the currently observed alternate-V2 WorkingDirectory via a new,
reversible drop-in, and harden owned canonical M03 SQLite files from 0644 to
0600. Do not read alternate-release files, modify original existing unit,
override M03 ExecStart, restart M05, start/stop/restart M03 or timers, touch
external projects, call market/provider APIs, or enable an order.

Usage (owner VPS, after original clean main pin):
 python -B -m scripts.m03_canonical_workdir_repair --owner-repair-m03-source
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

UNIT = "indodax-v2-m03-mainpath.service"
TIMER = "indodax-v2-m03-mainpath.timer"
M05 = "indodax-v2-m05-telegram.service"
M05_TIMER = "indodax-v2-m05-telegram.timer"
HOME = Path.home()
ROOT = HOME / "indodax-ai-bot-v2"
UNIT_DIR = HOME / ".config" / "systemd" / "user"
DROP_DIR = UNIT_DIR / (UNIT + ".d")
OWNER_DROP = DROP_DIR / "90-owner-advisory.conf"
REPAIR_DROP = DROP_DIR / "95-canonical-v2-workdir.conf"
DB = ROOT / "runtime" / "decisions.sqlite3"
DROP_CONTENT = b"[Service]\nWorkingDirectory=%h/indodax-ai-bot-v2\n"


class M03SourceRepairDenied(ValueError):
    """Stable nonsecret error code only."""


def _deny(code: str) -> None:
    raise M03SourceRepairDenied(code)


def _systemd(*args: str) -> str:
    try:
        p = subprocess.run(
            ["/usr/bin/systemctl", "--user", "--no-pager", *args],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if p.returncode != 0 or len(p.stdout) > 8192:
            _deny("M03_CANONICAL_SYSTEMD_UNAVAILABLE")
        return p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        _deny("M03_CANONICAL_SYSTEMD_UNAVAILABLE")


def _prop(unit: str, key: str) -> str:
    return _systemd("show", "-P", key, unit)


def _trusted_directory(path: Path) -> bool:
    try:
        st = path.lstat()
    except OSError:
        return False
    return (stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid()
            and not stat.S_ISLNK(st.st_mode))


def _trusted_private_candidate(path: Path) -> int:
    """Existing owned regular file; never follow symlinks/hardlinks."""
    try:
        st = path.lstat()
    except OSError:
        _deny("M03_CANONICAL_DB_FILE_UNAVAILABLE")
    mode = stat.S_IMODE(st.st_mode)
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
            or st.st_nlink != 1 or mode not in (0o600, 0o644)):
        _deny("M03_CANONICAL_DB_FILE_UNTRUSTED")
    return mode


def classify_m03_original_source_binding(*, working_directory: str,
                                         execstart: str,
                                         dropins: str,
                                         fragment: str,
                                         root: Path = ROOT,
                                         unit_dir: Path = UNIT_DIR) -> str:
    """Pure strict fixed-scope preflight: only canonical V2 or alternate V2."""
    if fragment != str(unit_dir / UNIT):
        _deny("M03_CANONICAL_FOREIGN_SERVICE_FRAGMENT")
    source = str(root)
    is_alt = (working_directory.startswith(source + "-")
              and Path(working_directory).parent == root.parent
              and working_directory != source)
    if not is_alt and working_directory != source:
        _deny("M03_CANONICAL_UNKNOWN_WORKDIR")
    expected = (
        "/usr/bin/flock", str(root / ".venv/bin/python"),
        "--scheduled-public-scanner-once",
        "--bulk-deep-limit 20", "--m05-market-advisory-queue",
        str(root / "runtime/decisions.sqlite3"),
        str(root / "runtime/public_ws_btc.sqlite3"),
    )
    if any(part not in execstart for part in expected):
        _deny("M03_CANONICAL_EXECSTART_MISMATCH")
    installed = dropins.split()
    if installed != [str(unit_dir / (UNIT + ".d") / "90-owner-advisory.conf")]:
        _deny("M03_CANONICAL_UNKNOWN_OR_CONFLICTING_DROPIN")
    return "ALT_V2_SCOPE_CORRECTION" if is_alt else "ALREADY_CANONICAL"


def repair_original_m03_source() -> dict:
    if (not _trusted_directory(ROOT)
            or not _trusted_directory(ROOT / "runtime")
            or not _trusted_directory(UNIT_DIR)
            or not _trusted_directory(DROP_DIR)):
        _deny("M03_CANONICAL_LOCAL_PATH_UNTRUSTED")
    if not ROOT.joinpath("indodax_ai_bot", "__main__.py").is_file():
        _deny("M03_CANONICAL_MAIN_SOURCE_MISSING")
    if (not _trusted_directory(DROP_DIR) or OWNER_DROP.is_symlink()
            or not OWNER_DROP.is_file() or REPAIR_DROP.exists()
            or REPAIR_DROP.is_symlink()):
        _deny("M03_CANONICAL_EXISTING_DROPIN_CONFLICT")
    if (_prop(UNIT, "LoadState") != "loaded"
            or _prop(UNIT, "Type") != "oneshot"
            or _prop(UNIT, "ActiveState") != "inactive"
            or _prop(UNIT, "NeedDaemonReload") != "no"
            or _prop(TIMER, "ActiveState") != "active"
            or _prop(TIMER, "UnitFileState") != "enabled"
            or _prop(M05, "ActiveState") != "active"
            or _prop(M05_TIMER, "ActiveState") != "inactive"
            or _prop(M05_TIMER, "UnitFileState") != "disabled"):
        _deny("M03_CANONICAL_ORIGINAL_UNITS_NOT_READY")
    binding = classify_m03_original_source_binding(
        working_directory=_prop(UNIT, "WorkingDirectory"),
        execstart=_prop(UNIT, "ExecStart"),
        dropins=_prop(UNIT, "DropInPaths"),
        fragment=_prop(UNIT, "FragmentPath"),
    )
    modes = {DB: _trusted_private_candidate(DB)}
    for suffix in ("-wal", "-shm"):
        path = Path(str(DB) + suffix)
        if path.exists() or path.is_symlink():
            modes[path] = _trusted_private_candidate(path)
    # File mode tightening never broadens access; do not roll it back to 0644.
    for path, mode in modes.items():
        if mode != 0o600:
            os.chmod(path, 0o600, follow_symlinks=False)
        if _trusted_private_candidate(path) != 0o600:
            _deny("M03_CANONICAL_DB_PERMISSIONS_NOT_PRIVATE")

    changed = False
    if binding == "ALT_V2_SCOPE_CORRECTION":
        try:
            fd = os.open(
                REPAIR_DROP, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                getattr(os, "O_NOFOLLOW", 0), 0o600,
            )
            changed = True
            with os.fdopen(fd, "wb") as out:
                out.write(DROP_CONTENT)
                out.flush()
                os.fsync(out.fileno())
            _systemd("daemon-reload")
            if (_prop(UNIT, "WorkingDirectory") != str(ROOT)
                    or _prop(UNIT, "NeedDaemonReload") != "no"
                    or _prop(TIMER, "ActiveState") != "active"
                    or _prop(TIMER, "UnitFileState") != "enabled"
                    or _prop(M05, "ActiveState") != "active"):
                _deny("M03_CANONICAL_RELOADED_METADATA_MISMATCH")
        except Exception:
            if changed:
                try:
                    REPAIR_DROP.unlink()
                    _systemd("daemon-reload")
                except (OSError, M03SourceRepairDenied):
                    _deny("M03_CANONICAL_ROLLBACK_REQUIRES_OWNER_REVIEW")
            raise

    count = None
    try:
        with sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                          "AND name='m03_compact_tick'").fetchone():
                count = db.execute("SELECT COUNT(*) FROM m03_compact_tick").fetchone()[0]
    except (OSError, sqlite3.Error):
        pass
    return {
        "status": "M03_CANONICAL_WORKDIR_REPAIRED" if changed
                  else "M03_CANONICAL_ALREADY_INSTALLED",
        "original_v2_workdir_metadata_verified": (
            _prop(UNIT, "WorkingDirectory") == str(ROOT)
        ),
        "original_m03_timer_still_active": (
            _prop(TIMER, "ActiveState") == "active"
        ),
        "same_original_m05_receiver_still_active": (
            _prop(M05, "ActiveState") == "active"
        ),
        "original_m03_database_private_0600": (
            _trusted_private_candidate(DB) == 0o600
        ),
        "original_m03_historical_deep20_count": count,
        "original_m03_recent_tick_verified": False,
        "m10_07_full_acceptance": False,
        "service_restarted": False,
        "timer_stopped": False,
        "m05_service_modified": False,
        "paid_ai_called": False,
        "exchange_orders_sent": False,
        "trading_enabled": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Original V2 M03 source fix")
    parser.add_argument("--owner-repair-m03-source", action="store_true")
    args = parser.parse_args()
    if not args.owner_repair_m03_source:
        print("M03_CANONICAL_EXPLICIT_OWNER_ACTION_REQUIRED", file=sys.stderr)
        return 2
    try:
        report = repair_original_m03_source()
    except (M03SourceRepairDenied, OSError, ValueError) as exc:
        code = str(exc)
        if not code.startswith("M03_CANONICAL_"):
            code = "M03_CANONICAL_LOCAL_OPERATION_FAILED"
        print(json.dumps({"status": "DENIED", "reason": code}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
