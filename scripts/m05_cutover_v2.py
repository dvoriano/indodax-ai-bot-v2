"""One-time original V2 M05 owner poller source/SQLite cutover. NO TRADING.

Must be run explicitly by the VPS operator, never imported by systemd/CI.
Only original V2 repo, installed M05 unit and its existing owner DB are touched.
No API request, paid AI, order, token print, new bot or M03 timer activation.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone

os.umask(0o077)
HOME = Path.home()
REPO = "dvoriano/indodax-ai-bot-v2"
RELEASE = HOME / "indodax-ai-bot-v2-m05-release"
OLD = "2e8e43357d73bdd8cce76de2ad208b15faf9f9a4"
NEW = "a8ccb02ab40ea4ca1aeca3a803ddfc6e37177890"
SERVICE = "indodax-v2-m05-telegram.service"
TIMER = "indodax-v2-m05-telegram.timer"
ENVFILE = HOME / ".config/indodax-ai-bot-v2/m05/service.env"

phase = "PREFLIGHT"
paused = False
upgraded = False
migration_started = False
resume_attempted = False
lock_fd = None
backup = None
db_path = None
before = None


def call(*args: str, timeout: int = 30) -> str:
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode:
        raise RuntimeError("M05_COMMAND_FAILED_" + Path(args[0]).name.upper())
    return result.stdout.strip()


def property_of(unit: str, key: str) -> str:
    return call("systemctl", "--user", "show", unit, "-p", key, "--value")


def require(ok: bool, code: str):
    if not ok:
        raise RuntimeError(code)


def secure_file(p: Path):
    info = p.lstat()
    require(
        not p.is_symlink() and stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == 0o600,
        "M05_UNSAFE_PRIVATE_FILE",
    )


def read_only(p: Path):
    conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.execute("PRAGMA query_only=ON")
    return conn


def snapshot(p: Path):
    conn = read_only(p)
    try:
        require(conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
                "M05_DB_INTEGRITY_NOT_OK")
        return {
            "owner": conn.execute(
                "SELECT owner_user_id,mode FROM m05_config WHERE singleton=1"
            ).fetchone(),
            "cursor": conn.execute(
                "SELECT next_offset FROM m05_telegram_cursor WHERE singleton=1"
            ).fetchone(),
            "updates": conn.execute(
                "SELECT COUNT(*),MAX(update_id),"
                "COALESCE(SUM(status='CLAIMED'),0) "
                "FROM m05_telegram_updates"
            ).fetchone(),
            "proposals": conn.execute(
                "SELECT COUNT(*) FROM m05_proposals"
            ).fetchone(),
            "audit": conn.execute(
                "SELECT COUNT(*) FROM m05_audit"
            ).fetchone(),
            "cycle": conn.execute(
                "SELECT COUNT(*) FROM m05_cycle_observations"
            ).fetchone(),
            "outbox": bool(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='m05_market_advisory_outbox'"
            ).fetchone()),
        }
    finally:
        conn.close()


def backup_database(src: Path, dst: Path):
    source = read_only(src)
    target = sqlite3.connect(dst, timeout=10)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    dst.chmod(0o600)


def wait_idle():
    for _ in range(15):
        if property_of(SERVICE, "ActiveState") == "inactive":
            return
        time.sleep(1)
    raise RuntimeError("M05_SERVICE_NOT_IDLE")


def unlock():
    global lock_fd
    if lock_fd is not None:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        lock_fd = None


try:
    require(RELEASE.is_dir() and not RELEASE.is_symlink(),
            "M05_RELEASE_MISSING")
    require(call("git", "-C", str(RELEASE), "rev-parse", "--show-toplevel")
            == str(RELEASE), "M05_RELEASE_NOT_ORIGINAL")
    require(call("git", "-C", str(RELEASE), "rev-parse", "HEAD")
            == OLD, "M05_RELEASE_SHA_CHANGED")
    require(not call("git", "-C", str(RELEASE), "status", "--porcelain"),
            "M05_RELEASE_DIRTY")
    origin = call("git", "-C", str(RELEASE), "remote", "get-url", "origin")
    require(origin in {
        "git@github.com:" + REPO + ".git",
        "https://github.com/" + REPO + ".git",
        "ssh://git@github.com/" + REPO + ".git",
    }, "M05_REPOSITORY_ORIGIN_UNEXPECTED")
    require(property_of(SERVICE, "LoadState") == "loaded",
            "M05_SERVICE_NOT_LOADED")
    require(property_of(SERVICE, "WorkingDirectory") == str(RELEASE),
            "M05_SERVICE_RELEASE_MISMATCH")
    require(str(RELEASE / "scripts/m05_telegram_service_tick_entry.py")
            in property_of(SERVICE, "ExecStart"),
            "M05_SERVICE_ENTRYPOINT_MISMATCH")
    require(property_of(SERVICE, "DropInPaths") == "",
            "M05_UNKNOWN_SERVICE_OVERRIDE")
    require(property_of(SERVICE, "ActiveState") == "inactive"
            and property_of(SERVICE, "Result") == "success",
            "M05_SERVICE_NOT_IDLE_SUCCESS")
    require(property_of(TIMER, "ActiveState") == "active"
            and property_of(TIMER, "UnitFileState") == "enabled",
            "M05_TIMER_NOT_HEALTHY")
    require(property_of("indodax-v2-m03-mainpath.timer", "ActiveState")
            == "inactive" and property_of(
                "indodax-v2-m03-mainpath.timer", "UnitFileState"
            ) == "disabled", "M03_MUST_REMAIN_DISABLED")
    service_env = property_of(SERVICE, "Environment")
    for required in (
        "INDODAX_TRADING_ENABLED=NO",
        "INDODAX_ALLOW_LIVE_EXECUTION=NO",
        "INDODAX_EXECUTION_MODE=DRY_RUN",
    ):
        require(required in service_env, "M05_TRADING_SAFETY_NOT_CONFIRMED")

    secure_file(ENVFILE)
    metadata = {}
    for line in ENVFILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            if k.strip() in ("M05_TELEGRAM_OWNER_DB", "M05_TELEGRAM_OWNER_ID"):
                metadata[k.strip()] = v.strip().strip("\"'")
    owner_id = int(metadata["M05_TELEGRAM_OWNER_ID"])
    db_path = Path(metadata["M05_TELEGRAM_OWNER_DB"])
    require(db_path == HOME /
            ".local/state/indodax-ai-bot-v2/m05-telegram/owner.sqlite3",
            "M05_OWNER_DB_PATH_CHANGED")
    parent = db_path.parent.stat()
    require(parent.st_uid == os.getuid()
            and stat.S_IMODE(parent.st_mode) == 0o700,
            "M05_OWNER_DIRECTORY_UNSAFE")
    secure_file(db_path)
    before = snapshot(db_path)
    require(before["owner"] is not None
            and before["owner"][0] == owner_id
            and before["updates"][2] == 0
            and not before["outbox"], "M05_PRE_UPGRADE_STATE_CHANGED")

    # Fetch first. A network outage cannot pause an otherwise healthy poller.
    phase = "FETCH_BEFORE_PAUSE"
    call("git", "-C", str(RELEASE), "fetch", "--no-tags",
         "origin", "main", timeout=30)
    call("git", "-C", str(RELEASE), "cat-file", "-e", NEW + "^{commit}")
    call("git", "-C", str(RELEASE), "merge-base",
         "--is-ancestor", NEW, "FETCH_HEAD")
    require(call("git", "-C", str(RELEASE), "rev-parse", "HEAD")
            == OLD, "M05_RELEASE_SHA_CHANGED_AFTER_FETCH")

    phase = "PAUSE_ORIGINAL_TIMER"
    call("systemctl", "--user", "stop", TIMER)
    paused = True
    require(property_of(TIMER, "ActiveState") == "inactive",
            "M05_TIMER_PAUSE_NOT_CONFIRMED")
    wait_idle()

    # Same original Telegram poller lock, not a second lock namespace.
    lock_path = Path(str(db_path) + ".dedicated-poller.lock")
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
                      | getattr(os, "O_NOFOLLOW", 0), 0o600)
    info = os.fstat(lock_fd)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
            "M05_POLL_LOCK_UNSAFE")
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    before = snapshot(db_path)
    require(before["owner"][0] == owner_id and before["updates"][2] == 0
            and not before["outbox"], "M05_PRE_BACKUP_STATE_CHANGED")

    phase = "PRIVATE_ONLINE_SQLITE_BACKUP"
    backup_dir = db_path.parent / "cutover-backups"
    backup_dir.mkdir(mode=0o700, exist_ok=True)
    di = backup_dir.lstat()
    require(not backup_dir.is_symlink() and di.st_uid == os.getuid()
            and stat.S_IMODE(di.st_mode) == 0o700,
            "M05_BACKUP_DIRECTORY_UNSAFE")
    backup = backup_dir / (
        "owner-pre-m05-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + ".sqlite3"
    )
    require(not backup.exists(), "M05_BACKUP_ALREADY_EXISTS")
    backup_database(db_path, backup)
    secure_file(backup)
    require(snapshot(backup) == before, "M05_BACKUP_COMPARE_FAILED")

    phase = "UPGRADE_EXISTING_RELEASE"
    upgraded = True
    call("git", "-C", str(RELEASE), "checkout", "--detach", NEW)
    require(call("git", "-C", str(RELEASE), "rev-parse", "HEAD")
            == NEW and not call(
                "git", "-C", str(RELEASE), "status", "--porcelain"
            ), "M05_SOURCE_UPGRADE_NOT_VERIFIED")

    phase = "MIGRATE_EXISTING_OWNER_SCHEMA"
    sys.path.insert(0, str(RELEASE))
    from indodax_ai_bot.owner_control import OwnerDecisionLedger
    migration_started = True
    OwnerDecisionLedger(db_path, owner_user_id=owner_id)
    after = snapshot(db_path)
    require(after["outbox"] and
            all(after[k] == before[k] for k in (
                "owner", "cursor", "updates", "proposals", "audit", "cycle"
            )), "M05_OWNER_STATE_CHANGED_ON_MIGRATION")

    phase = "RESUME_ORIGINAL_TIMER"
    unlock()
    resume_attempted = True
    call("systemctl", "--user", "start", TIMER)
    require(property_of(TIMER, "ActiveState") == "active"
            and property_of(TIMER, "UnitFileState") == "enabled",
            "M05_TIMER_RESUME_NOT_VERIFIED")
    print("M05_CUTOVER_SOURCE_SHA=" + NEW)
    print("M05_DB_SCHEMA_MIGRATED=True")
    print("M05_OWNER_MODE_CURSOR_RECEIPTS_PRESERVED=True")
    print("M05_BACKUP_FILE=" + str(backup))
    print("M05_TIMER_ENABLED_ACTIVE=True")
    print("M05_TRADING_ENABLED=False")
    print("M05_PRODUCTION_CUTOVER_PASS")
except Exception as e:
    print("M05_CUTOVER_FAILED_PHASE=" + phase)
    print("M05_CUTOVER_FAILURE_TYPE=" + type(e).__name__)
    if paused and not resume_attempted:
        try:
            wait_idle()
            if upgraded:
                call("git", "-C", str(RELEASE), "checkout", "--force",
                     "--detach", OLD)
            if migration_started:
                require(backup is not None and backup.is_file(),
                        "M05_BACKUP_REQUIRED_FOR_ROLLBACK")
                backup_database(backup, db_path)
            require(call("git", "-C", str(RELEASE), "rev-parse", "HEAD")
                    == OLD and snapshot(db_path) == before,
                    "M05_ROLLBACK_NOT_VERIFIED")
            unlock()
            call("systemctl", "--user", "start", TIMER)
            require(property_of(TIMER, "ActiveState") == "active",
                    "M05_ROLLBACK_TIMER_NOT_ACTIVE")
            print("M05_CUTOVER_ROLLBACK_ORIGINAL_TIMER_RESTORED")
        except Exception:
            print("M05_ROLLBACK_REQUIRES_OPERATOR_REVIEW_TIMER_STOPPED")
    elif resume_attempted:
        print("M05_POST_RESUME_OPERATOR_REVIEW_REQUIRED_NO_DB_ROLLBACK")
    print("M05_PRODUCTION_CUTOVER_NOT_PASS")
    raise SystemExit(2)
