"""Secret-safe ORIGINAL M03 micro 72h archive systemd preflight.

READ ONLY. Reads allowlisted systemd properties and private EnvironmentFiles.
Never prints full environment, file contents, Telegram tokens, keys, or paths.
An opt-in on a one-shot service is effective on its NEXT normal timer cycle.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys

from indodax_ai_bot.m03_ws3_evidence_archive_nonlive import DEFAULT_EVIDENCE_DB

M03 = "indodax-v2-m03-mainpath.service"
TIMER = "indodax-v2-m03-mainpath.timer"
M05 = "indodax-v2-m05-telegram.service"
MICRO_FLAG = "M03_WS3_MICRO_72H_ARCHIVE_GO"
REQUIRED_FLAGS = {
    "INDODAX_TRADING_ENABLED": "NO",
    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
    "INDODAX_EXECUTION_MODE": "DRY_RUN",
    "M03_WS3_EVIDENCE_ARCHIVE_GO": "YES",
}
ALLOWLIST = frozenset((*REQUIRED_FLAGS, MICRO_FLAG))
ENV_FILES = re.compile(r"\s*(?:\S+ \(ignore_errors=(?:yes|no)\)\s*)+")
ONE_FILE = re.compile(r"(\S+) \(ignore_errors=(?:yes|no)\)")


def summarize_allowlisted(flags: dict[str, list[str]],
                          *, enabled: bool) -> bool:
    """Only known values; conflicts and unknowns are fail-closed."""
    if type(flags) is not dict:
        return False
    if any(set(flags.get(k, [])) != {v} for k, v in REQUIRED_FLAGS.items()):
        return False
    selected = flags.get(MICRO_FLAG, [])
    return set(selected) == ({"YES"} if enabled else set())


def _unit(unit: str, *props: str) -> dict[str, str]:
    cmd = ["systemctl", "--user", "show", unit, "--no-pager"]
    for prop in props:
        cmd.extend(("-p", prop))
    data = subprocess.run(cmd, check=True, capture_output=True,
                          text=True, timeout=8)
    return dict(
        line.split("=", 1) for line in data.stdout.splitlines() if "=" in line
    )


def _secret_safe_flags(env: str, env_files: str) -> tuple[dict[str, list[str]], int]:
    values = {key: [] for key in ALLOWLIST}
    # Avoid shell parsing or executing *any* file. Validate each private
    # regular file and parse only exact allowlisted KEY=value lines.
    for setting in shlex.split(env):
        key, equal, value = setting.partition("=")
        if equal and key in values:
            values[key].append(value)
    if len(env_files) > 4096 or ENV_FILES.fullmatch(env_files) is None:
        raise ValueError("ENVFILE_FORMAT_UNVERIFIED")
    paths = ONE_FILE.findall(env_files)
    if not 1 <= len(paths) <= 4 or len(paths) != len(set(paths)):
        raise ValueError("ENVFILE_COUNT_UNVERIFIED")
    for filename in paths:
        path = Path(filename)
        try:
            info = path.lstat()
        except OSError:
            raise ValueError("ENVFILE_NOT_READABLE") from None
        if (not path.is_absolute() or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_size > 65536):
            raise ValueError("ENVFILE_UNTRUSTED") 
        raw = path.read_text(encoding="utf-8")
        for row in raw.splitlines():
            match = re.fullmatch(r"\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*", row)
            if match and match.group(1) in values:
                # Only the allowed flag is read; values are never printed.
                values[match.group(1)].append(
                    match.group(2).strip("'\""))
    return values, len(paths)


def _private_archive() -> bool:
    try:
        directory = DEFAULT_EVIDENCE_DB.parent.lstat()
        database = DEFAULT_EVIDENCE_DB.lstat()
        return (stat.S_ISDIR(directory.st_mode)
                and stat.S_ISREG(database.st_mode)
                and directory.st_uid == database.st_uid == os.geteuid()
                and stat.S_IMODE(directory.st_mode) == 0o700
                and stat.S_IMODE(database.st_mode) == 0o600
                and database.st_nlink == 1)
    except OSError:
        return False


def inspect_original_m03_micro_preflight_readonly(
    *, expect_enabled: bool = False,
) -> dict:
    checks: dict[str, object] = {
        "m03_oneshot_loaded": False,
        "m03_micro_cli_present": False,
        "m03_timer_active": False,
        "m05_active": False,
        "runtime_guards_and_price_archive_verified": False,
        "micro_flag_expected_state_verified": False,
        "archive_private": False,
        "environment_file_count": 0,
        "m03_not_restarted_by_check": True,
        "m05_not_restarted_by_check": True,
        "orders_sent_by_check": 0,
        "telegram_sent_by_check": 0,
        "execution_allowed": False,
    }
    try:
        service = _unit(M03, "LoadState", "Type", "ExecStart",
                        "Environment", "EnvironmentFiles", "NeedDaemonReload")
        timer = _unit(TIMER, "ActiveState")
        m05 = _unit(M05, "ActiveState", "MainPID")
        cmd = service.get("ExecStart", "")
        checks["m03_oneshot_loaded"] = (
            service.get("LoadState") == "loaded"
            and service.get("Type") == "oneshot"
            and service.get("NeedDaemonReload") == "no"
        )
        checks["m03_micro_cli_present"] = all(x in cmd for x in (
            "--m03-ws-summary-watch-once",
            "--m03-ws-microstructure-watch",
            "runtime/decisions.sqlite3",
        ))
        checks["m03_timer_active"] = timer.get("ActiveState") == "active"
        checks["m05_active"] = (
            m05.get("ActiveState") == "active"
            and str(m05.get("MainPID", "")).isdigit()
            and int(m05["MainPID"]) > 0
        )
        flags, count = _secret_safe_flags(
            service.get("Environment", ""),
            service.get("EnvironmentFiles", ""),
        )
        checks["environment_file_count"] = count
        checks["runtime_guards_and_price_archive_verified"] = all(
            set(flags[k]) == {v} for k, v in REQUIRED_FLAGS.items()
        )
        checks["micro_flag_expected_state_verified"] = (
            set(flags[MICRO_FLAG]) == (
                {"YES"} if expect_enabled else set()
            )
        )
        checks["archive_private"] = _private_archive()
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError,
            OverflowError):
        pass
    success = all(checks[key] is True for key in (
        "m03_oneshot_loaded", "m03_micro_cli_present", "m03_timer_active",
        "m05_active", "runtime_guards_and_price_archive_verified",
        "micro_flag_expected_state_verified", "archive_private",
    ))
    return {
        "status": (
            "M03_MICRO_72H_ENABLED_CONFIG_READY_NO_START"
            if success and expect_enabled else
            "M03_MICRO_72H_PREFLIGHT_READY_NO_CHANGE"
            if success else "M03_MICRO_72H_PREFLIGHT_BLOCKED_NO_CHANGE"
        ),
        "checks": checks,
        "expect_enabled": expect_enabled,
        "micro_research_recorded_by_check": False,
    }


def main() -> int:
    if sys.argv[1:] not in ([], ["--expect-enabled"]):
        print("M03_MICRO_72H_PREFLIGHT_USAGE_ERROR")
        return 2
    report = inspect_original_m03_micro_preflight_readonly(
        expect_enabled=sys.argv[1:] == ["--expect-enabled"]
    )
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    return (0 if report["status"] !=
            "M03_MICRO_72H_PREFLIGHT_BLOCKED_NO_CHANGE" else 2)


if __name__ == "__main__":
    sys.exit(main())
