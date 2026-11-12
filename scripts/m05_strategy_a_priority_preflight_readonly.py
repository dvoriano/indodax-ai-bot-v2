"""Original V2 M05 Strategy A priority-rollout PRE-FLIGHT, read-only.

No network, API client, Telegram send, systemd edits/restarts, DB mutation
or exchange orders. Read ONLY allowlisted service metadata and select keys
from the original M05 process environment. NEVER print env bytes or tokens.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys


M05 = "indodax-v2-m05-telegram.service"
M05_TIMER = "indodax-v2-m05-telegram.timer"
M03_TIMER = "indodax-v2-m03-mainpath.timer"
PR283_SOURCE = "35de691b5304d1182f63b375bc7a6c67fcfc85d8"
PR284_SOURCE = "e30c6d961cfa02d1c408e5a88d944af22cb94006"
REQUIRED_FLAGS = {
    "INDODAX_TRADING_ENABLED": "NO",
    "INDODAX_ALLOW_LIVE_EXECUTION": "NO",
    "INDODAX_EXECUTION_MODE": "DRY_RUN",
    "M05_TELEGRAM_FAST_LONGPOLL_GO": "YES",
    "M05_RADAR_MOMENTUM_ALERT_GO": "YES",
    "M05_WS3_RADAR_WATCH_GO": "YES",
}
PRIORITY_FLAG = "M05_RADAR_PRIORITY_V2_GO"
SCANNED_FLAGS = (*REQUIRED_FLAGS, PRIORITY_FLAG)


def summarize_allowlisted_process_flags(raw: bytes) -> dict[str, str]:
    """Never include unrecognized environment values or secret credentials."""
    if type(raw) is not bytes or len(raw) > 1024 * 1024:
        raise ValueError("M05_PRIORITY_ENV_UNAVAILABLE")
    found: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        key, delimiter, value = entry.partition(b"=")
        if not delimiter:
            continue
        for permitted in SCANNED_FLAGS:
            if key != permitted.encode("ascii"):
                continue
            # Duplicate or unexpected flag values fail closed.
            if permitted in found:
                raise ValueError("M05_PRIORITY_DUPLICATE_ENV_FLAG")
            found[permitted] = (
                value.decode("ascii") if value in (b"YES", b"NO", b"DRY_RUN")
                else "UNEXPECTED"
            )
    return found


def assess_priority_preflight_nonlive(
        *, service: dict[str, str], legacy_timer: dict[str, str],
        scanner_timer: dict[str, str], process_flags: dict[str, str],
        expected_workdir: str, source_ok: bool, repo_clean_main: bool,
        pid_stable: bool) -> dict:
    """Pure diagnostics only; never claims an M05 restart is authorized."""
    reasons = []
    if not source_ok or not repo_clean_main:
        reasons.append("SOURCE_NOT_PINNED_CLEAN_MAIN")
    if (service.get("ActiveState") != "active"
            or service.get("Type") != "simple"
            or service.get("WorkingDirectory") != expected_workdir
            or service.get("NeedDaemonReload") != "no"
            or service.get("LoadState") != "loaded"):
        reasons.append("M05_SERVICE_STATE_OR_PATH_NOT_READY")
    if (not str(service.get("MainPID", "")).isascii()
            or not str(service.get("MainPID", "")).isdecimal()
            or int(service.get("MainPID", "0")) <= 0
            or not pid_stable):
        reasons.append("M05_PID_NOT_STABLE")
    if "fast-longpoll.conf" not in service.get("DropInPaths", ""):
        reasons.append("M05_LONGPOLL_DROPIN_MISSING")
    if (legacy_timer.get("ActiveState") != "inactive"
            or legacy_timer.get("UnitFileState") != "disabled"):
        reasons.append("M05_LEGACY_TIMER_NOT_DISABLED")
    if scanner_timer.get("ActiveState") != "active":
        reasons.append("M03_SCANNER_TIMER_NOT_ACTIVE")
    for key, expected in REQUIRED_FLAGS.items():
        if process_flags.get(key) != expected:
            reasons.append("RUNNING_FLAG_NOT_VERIFIED_" + key)
    priority = process_flags.get(PRIORITY_FLAG)
    if priority == "YES":
        reasons.append("PRIORITY_ALREADY_ACTIVE_NOT_A_FIRST_CUTOVER")
    elif priority not in (None, "NO"):
        reasons.append("PRIORITY_FLAG_INVALID")
    return {
        "status": ("M05_PRIORITY_PREFLIGHT_READY_NO_CHANGE" if not reasons
                   else "M05_PRIORITY_PREFLIGHT_BLOCKED_NO_CHANGE"),
        "reasons": reasons,
        "priority_live_in_running_process": priority == "YES",
        "strategy_a_source_present": bool(source_ok),
        "m03_scanner_timer_active": scanner_timer.get("ActiveState") == "active",
        "m05_active": service.get("ActiveState") == "active",
        "m05_pid_unchanged": bool(pid_stable),
        "runtime_guards_verified": all(
            process_flags.get(key) == val
            for key, val in REQUIRED_FLAGS.items()
            if key.startswith("INDODAX_")),
        "token_or_env_contents_printed": False,
        "service_restarted": False,
        "telegram_sent": False,
        "order_sent": False,
        "execution_allowed": False,
    }


def _run(args: list[str], *, cwd: Path | None = None) -> str:
    # Fixed command vectors only, no shell and no user-supplied arguments.
    result = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True,
        timeout=10, check=True)
    return result.stdout.strip()


def _unit_show(unit: str, keys: tuple[str, ...]) -> dict[str, str]:
    if unit not in (M05, M05_TIMER, M03_TIMER):
        raise ValueError("M05_PRIORITY_UNEXPECTED_UNIT")
    output = _run([
        "systemctl", "--user", "show", unit,
        "--no-pager",
    ] + [arg for key in keys for arg in ("-p", key)])
    result = {}
    for line in output.splitlines():
        name, sep, value = line.partition("=")
        if sep and name in keys and name not in result:
            result[name] = value
    return result


def inspect_original_v2_priority_preflight_readonly() -> dict:
    root = Path.home() / "indodax-ai-bot-v2"
    m05_fields = ("LoadState", "ActiveState", "Type", "WorkingDirectory",
                  "NeedDaemonReload", "MainPID", "DropInPaths")
    timer_fields = ("ActiveState", "UnitFileState")
    try:
        branch = _run(["git", "branch", "--show-current"], cwd=root)
        clean = _run(["git", "status", "--porcelain"], cwd=root) == ""
        source_ok = all(
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", ancestor, "HEAD"],
                cwd=root, capture_output=True, timeout=8, check=False
            ).returncode == 0
            for ancestor in (PR283_SOURCE, PR284_SOURCE)
        )
        repo_ok = branch == "main" and clean
        unit = _unit_show(M05, m05_fields)
        old_timer = _unit_show(M05_TIMER, timer_fields)
        scanner = _unit_show(M03_TIMER, timer_fields)
        pid = unit.get("MainPID", "")
        if re.fullmatch(r"[1-9][0-9]{0,9}", pid) is None:
            raise ValueError("M05_PRIORITY_PID_NOT_VERIFIED")
        env = summarize_allowlisted_process_flags(
            (Path("/proc") / pid / "environ").read_bytes()
        )
        stable = _unit_show(M05, ("MainPID",)).get("MainPID") == pid
        return assess_priority_preflight_nonlive(
            service=unit, legacy_timer=old_timer, scanner_timer=scanner,
            process_flags=env, expected_workdir=str(root),
            source_ok=source_ok, repo_clean_main=repo_ok, pid_stable=stable,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        # Never print exception messages, raw systemd environment, credentials
        # or account-specific filenames from private sources.
        return {
            "status": "M05_PRIORITY_PREFLIGHT_BLOCKED_NO_CHANGE",
            "reasons": ["SOURCE_OR_RUNTIME_INSPECTION_UNAVAILABLE"],
            "token_or_env_contents_printed": False,
            "service_restarted": False,
            "telegram_sent": False,
            "order_sent": False,
            "execution_allowed": False,
        }


def main() -> int:
    verdict = inspect_original_v2_priority_preflight_readonly()
    print(json.dumps(verdict, sort_keys=True, separators=(",", ":")))
    return 0 if verdict["status"] == "M05_PRIORITY_PREFLIGHT_READY_NO_CHANGE" else 2


if __name__ == "__main__":
    sys.exit(main())
