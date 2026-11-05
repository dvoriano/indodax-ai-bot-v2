"""Stage EXISTING original V2 M03 timer to the SAME V2 M05 owner outbox.

Explicit owner action, PUBLIC-only NONLIVE pilot. No new scanner, poller,
private API, paid AI, exchange order, or fake G1. Does not touch M05 units.
Refuses unknown existing overrides and private configuration; no secrets logged.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import time

from indodax_ai_bot.m03_egress_admission import M03SharedEgressAdmission
from scripts.m03_m05_owner_vps_snapshot import collect_original_v2_runtime_snapshot

UNIT = "indodax-v2-m03-mainpath.service"
TIMER = "indodax-v2-m03-mainpath.timer"
M05_TIMER = "indodax-v2-m05-telegram.timer"
BTC_TIMER = "indodax-v2-public-ws-btc.timer"
ROOT = Path.home() / "indodax-ai-bot-v2"
HOME_CONFIG = Path.home() / ".config" / "indodax-ai-bot-v2"
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
ENV_NAME = "m03-m05-advisory.env"
DROP_NAME = "90-owner-advisory.conf"
_ID = re.compile(r"[1-9][0-9]{0,18}\Z")


class CanaryDenied(ValueError):
    """Stable local denial; never print operator paths, credentials or values."""


def _private_file(path: Path, max_bytes: int) -> bytes:
    try:
        st = path.lstat()
        parent = path.parent.lstat()
        if (not path.is_absolute() or not stat.S_ISREG(st.st_mode)
                or st.st_uid != os.geteuid() or st.st_nlink != 1
                or stat.S_IMODE(st.st_mode) != 0o600
                or not 0 < st.st_size <= max_bytes
                or not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != os.geteuid()
                or stat.S_IMODE(parent.st_mode) != 0o700):
            raise CanaryDenied("M03_CANARY_UNTRUSTED_PRIVATE_CONFIG")
        with path.open("rb") as f:
            return f.read(max_bytes + 1)
    except OSError:
        raise CanaryDenied("M03_CANARY_PRIVATE_CONFIG_UNAVAILABLE") from None


def _owner_binding(raw: bytes) -> tuple[Path, int]:
    # Parse only allowlisted NON-SECRET config values, never copy token path.
    result: dict[str, str] = {}
    try:
        for line in raw.decode("utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in ("M05_TELEGRAM_OWNER_DB", "M05_TELEGRAM_OWNER_ID"):
                if key in result or len(value) > 1024:
                    raise CanaryDenied("M03_CANARY_OWNER_CONFIG_INVALID")
                result[key] = value.strip().strip('"').strip("'")
        if set(result) != {"M05_TELEGRAM_OWNER_DB", "M05_TELEGRAM_OWNER_ID"}:
            raise CanaryDenied("M03_CANARY_OWNER_CONFIG_INVALID")
        db = Path(result["M05_TELEGRAM_OWNER_DB"])
        identity = result["M05_TELEGRAM_OWNER_ID"]
        if (_ID.fullmatch(identity) is None or not db.is_absolute()
                or not db.is_relative_to(Path.home())
                or any(x in ("..", ".") for x in db.parts)):
            raise CanaryDenied("M03_CANARY_OWNER_CONFIG_INVALID")
        return db, int(identity)
    except UnicodeError:
        raise CanaryDenied("M03_CANARY_OWNER_CONFIG_INVALID") from None


def _check_owner_ledger(db: Path, user_id: int) -> None:
    _private_file(db, 100_000_000)
    try:
        with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=3) as con:
            con.execute("PRAGMA query_only=ON")
            owner = con.execute(
                "SELECT owner_user_id,mode FROM m05_config WHERE singleton=1"
            ).fetchone()
            if owner != (user_id, "MANUAL"):
                raise CanaryDenied("M03_CANARY_OWNER_MODE_OR_ID_MISMATCH")
            con.execute("SELECT 1 FROM m05_market_advisory_outbox LIMIT 1")
    except sqlite3.Error:
        raise CanaryDenied("M03_CANARY_OWNER_LEDGER_UNAVAILABLE") from None


def _run_systemctl(*args: str) -> str:
    cmd = ["/usr/bin/systemctl", "--user", "--no-pager", *args]
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, check=False,
        )
        if p.returncode != 0 or len(p.stdout) > 8192:
            raise CanaryDenied("M03_CANARY_SYSTEMD_OPERATION_FAILED")
        return p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        raise CanaryDenied("M03_CANARY_SYSTEMD_OPERATION_FAILED") from None


def _unit_property(unit: str, prop: str) -> str:
    return _run_systemctl("show", "-P", prop, unit)


def _validate_original_service(installed: str, template: str) -> None:
    """Keep installed V2 service; refuse unknown extra executable overrides."""
    required = (
        "WorkingDirectory=%h/indodax-ai-bot-v2",
        "Environment=INDODAX_TRADING_ENABLED=NO",
        "Environment=INDODAX_ALLOW_LIVE_EXECUTION=NO",
        "Environment=INDODAX_EXECUTION_MODE=DRY_RUN",
        "Environment=INDODAX_M03_REQUIRE_EGRESS_ADMISSION=YES",
    )
    if any(item not in installed for item in required):
        raise CanaryDenied("M03_CANARY_INSTALLED_SERVICE_UNVERIFIED")
    commands = lambda s: [
        x.strip() for x in s.splitlines()
        if x.strip().startswith("ExecStart=")
    ]
    if (commands(installed) != commands(template)
            or len(commands(template)) != 1):
        raise CanaryDenied("M03_CANARY_INSTALLED_EXEC_DIFFERS")


def _write_exclusive(path: Path, value: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(value)
        f.flush()
        os.fsync(f.fileno())


def stage_original_nonlive_canary(
    *, root: Path = ROOT, config: Path = HOME_CONFIG,
    unit_dir: Path = UNIT_DIR,
) -> dict:
    if root != ROOT or root.is_symlink() or not root.is_dir():
        raise CanaryDenied("M03_CANARY_ONLY_ORIGINAL_V2")
    snapshot = collect_original_v2_runtime_snapshot()
    if snapshot.get("ready_for_nonlive_scanner_canary") is not True:
        raise CanaryDenied("M03_CANARY_SNAPSHOT_NOT_READY")
    policy = json.loads(_private_file(config / "m03-egress-admission.json", 8192))
    if (policy.get("schema_version") != 2
            or policy.get("pilot_scope") != "M03_NONLIVE_PUBLIC_PILOT"
            or policy.get("external_consumer_ids") != []
            or policy.get("external_peak_requests_rolling_60s") != 0
            or type(policy.get("expires_at_epoch")) is not int
            or policy["expires_at_epoch"] < int(time.time()) + 600):
        raise CanaryDenied("M03_CANARY_NONLIVE_PILOT_NOT_CURRENT")
    if M03SharedEgressAdmission(config / "m03-egress-admission.json").allowed_local_limit() != 90:
        raise CanaryDenied("M03_CANARY_PUBLIC_LIMIT_UNVERIFIED")
    if (_unit_property(TIMER, "ActiveState") != "inactive"
            or _unit_property(TIMER, "UnitFileState") != "disabled"
            or _unit_property(UNIT, "ActiveState") != "inactive"
            or _unit_property(M05_TIMER, "ActiveState") != "active"
            or _unit_property(BTC_TIMER, "ActiveState") != "active"):
        raise CanaryDenied("M03_CANARY_UNEXPECTED_UNIT_STATE")
    if (_unit_property(UNIT, "FragmentPath") != str(unit_dir / UNIT)
            or _unit_property(UNIT, "NeedDaemonReload") != "no"
            or _unit_property(TIMER, "FragmentPath") != str(unit_dir / TIMER)
            or _unit_property(TIMER, "NeedDaemonReload") != "no"):
        raise CanaryDenied("M03_CANARY_UNIT_PATH_UNVERIFIED")

    base = unit_dir / UNIT
    try:
        service_text = base.read_text("utf-8")
        template_text = (root / "deploy" / UNIT).read_text("utf-8")
    except (OSError, UnicodeError):
        raise CanaryDenied("M03_CANARY_ORIGINAL_SERVICE_UNAVAILABLE") from None
    if base.is_symlink() or (root / "deploy" / UNIT).is_symlink():
        raise CanaryDenied("M03_CANARY_SERVICE_SYMLINK")
    _validate_original_service(service_text, template_text)

    db, owner_id = _owner_binding(_private_file(config / "m05" / "service.env", 8192))
    _check_owner_ledger(db, owner_id)

    # Owner DB path/ID are non-secret but avoid displaying either value.
    # No token value, token path, Telegram API call or other bot is touched.
    advisory_env = (
        "M05_MARKET_ADVISORY_QUEUE_GO=YES\n"
        f"M05_TELEGRAM_OWNER_DB={db}\n"
        f"M05_TELEGRAM_OWNER_ID={owner_id}\n"
    ).encode()
    drop_source = root / "deploy" / "indodax-v2-m03-owner-advisory.conf"
    if drop_source.is_symlink():
        raise CanaryDenied("M03_CANARY_ADVISORY_SOURCE_SYMLINK")
    try:
        drop_bytes = drop_source.read_bytes()
    except OSError:
        raise CanaryDenied("M03_CANARY_ADVISORY_SOURCE_UNAVAILABLE") from None
    if (b"--m05-market-advisory-queue" not in drop_bytes
            or b"INDODAX_TRADING_ENABLED=NO" not in drop_bytes
            or b"INDODAX_ALLOW_LIVE_EXECUTION=NO" not in drop_bytes):
        raise CanaryDenied("M03_CANARY_SOURCE_GUARD_MISSING")

    installed_env = config / ENV_NAME
    drop_dir = unit_dir / (UNIT + ".d")
    installed_drop = drop_dir / DROP_NAME
    if (installed_env.exists() or installed_env.is_symlink()
            or installed_drop.exists() or installed_drop.is_symlink()):
        raise CanaryDenied("M03_CANARY_EXISTING_DROPIN_REVIEW_REQUIRED")
    if drop_dir.exists() and (drop_dir.is_symlink() or not drop_dir.is_dir()
                              or list(drop_dir.iterdir())):
        raise CanaryDenied("M03_CANARY_UNKNOWN_DROPINS")
    if not unit_dir.is_dir() or unit_dir.is_symlink() or not config.is_dir():
        raise CanaryDenied("M03_CANARY_UNIT_DIR_UNTRUSTED")
    # Staging only the EXISTING M03 service and M05 owner outbox.
    drop_dir.mkdir(mode=0o700, exist_ok=True)
    env_staged = False
    drop_staged = False
    enabled = False
    try:
        _write_exclusive(installed_env, advisory_env)
        env_staged = True
        _write_exclusive(installed_drop, drop_bytes)
        drop_staged = True
        _run_systemctl("daemon-reload")
        if (_unit_property(UNIT, "NeedDaemonReload") != "no"
                or _unit_property(UNIT, "ActiveState") != "inactive"):
            raise CanaryDenied("M03_CANARY_RELOAD_OR_SERVICE_UNEXPECTED")
        _run_systemctl("enable", "--now", TIMER)
        enabled = True
        if (_unit_property(TIMER, "ActiveState") != "active"
                or _unit_property(TIMER, "UnitFileState") != "enabled"):
            raise CanaryDenied("M03_CANARY_TIMER_NOT_ACTIVE")
    except Exception:
        # Never strand an active scanner without the exact source/env.
        try:
            _run_systemctl("disable", "--now", TIMER)
        except CanaryDenied:
            pass
        if drop_staged:
            installed_drop.unlink(missing_ok=True)
        if env_staged:
            installed_env.unlink(missing_ok=True)
        try:
            _run_systemctl("daemon-reload")
        except CanaryDenied:
            pass
        raise
    return {
        "status": "M03_ORIGINAL_NONLIVE_TIMER_CANARY_ENABLED",
        "m03_original_timer_active": enabled,
        "original_m05_owner_ledger_reused": True,
        "m05_telegram_timer_modified": False,
        "btc_timer_modified": False,
        "public_only": True,
        "paid_ai_started": False,
        "advisory_delivery_verified": False,
        "live_order_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Existing V2 M03->M05 owner canary")
    parser.add_argument("--enable-original-owner-nonlive", action="store_true")
    args = parser.parse_args()
    if not args.enable_original_owner_nonlive:
        print("M03_CANARY_OWNER_NONLIVE_OPTIN_REQUIRED", file=sys.stderr)
        return 2
    try:
        result = stage_original_nonlive_canary()
    except (CanaryDenied, OSError, ValueError, sqlite3.Error) as exc:
        code = str(exc)
        if not code.startswith("M03_"):
            code = "M03_CANARY_LOCAL_ERROR"
        print(json.dumps({"status": "DENIED", "reason": code}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
