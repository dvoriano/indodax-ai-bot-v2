"""Explicit owner-only bounded recovery for the ORIGINAL V2 BTC public timer.

One source, one existing BTC collector, 2h maximum public-only pilot.
Requires an exact REJECT sidecar, owner-current failure-HOLD acknowledgement,
and canonical installed V2 BTC systemd source. This does NOT run a collector
or modify scanner/M05. The existing BTC timer may perform the NEXT batch;
on failure the original ExecStopPost must restore fail-stop HOLD.

Never accepts arbitrary HOLD, bypasses missing/failed source provenance,
alters G1, issues paid AI calls, or enables BUY/SELL.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

from indodax_ai_bot.btc_ops_gate import (
    AdmissionDenied, _journal_gap_count, _private_json, _read_owned,
)

ROOT = Path.home() / "indodax-ai-bot-v2"
RT = ROOT / "runtime"
UNIT = "indodax-v2-public-ws-btc.service"
TIMER = "indodax-v2-public-ws-btc.timer"
M05 = "indodax-v2-m05-telegram.service"
UNITDIR = Path.home() / ".config/systemd/user"
DROP = UNITDIR / (UNIT + ".d") / "90-capped-ops.conf"
TEMPLATE = ROOT / "deploy" / "indodax-v2-public-ws-btc.capped-dropin.conf"
GO = RT / "btc_public_ops.go.json"
STATUS = RT / "public_ws_btc.status.json"
HOLD = RT / "btc_public_ops.hold.json"
WSDB = RT / "public_ws_btc.sqlite3"
PILOT_SECONDS = 7200
SAFE = re.compile(r"M10_BTC_[A-Z0-9_]{1,90}\Z")


class BtcRecoveryDenied(ValueError):
    """Stable nonsecret code. Never echo raw systemd, paths, IPs or status."""


def deny(code: str) -> None:
    raise BtcRecoveryDenied(code)


def _trusted_dir(path: Path) -> bool:
    try:
        st = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid()


def assess_exact_failed_btc(
    *, old_go: dict, status: dict, status_bytes: bytes, hold: dict,
    gaps: int, epoch: int,
) -> dict:
    """Strict no-network source review. The old owner GO is NOT reused."""
    if (type(epoch) is not int or epoch <= 0
            or type(old_go) is not dict or type(status) is not dict
            or type(hold) is not dict or type(status_bytes) is not bytes):
        deny("M10_BTC_SOURCE_UNTRUSTED")
    if (set(old_go) - {
            "scope", "expires_at_epoch", "max_gap_count",
            "acknowledged_failure_sha256"
        } or old_go.get("scope") != "BTC_PUBLIC_MONITOR_ONLY"
            or type(old_go.get("expires_at_epoch")) is not int
            or old_go["expires_at_epoch"] > epoch
            or type(old_go.get("max_gap_count")) is not int
            or not 0 <= old_go["max_gap_count"] <= 100000):
        deny("M10_BTC_PREVIOUS_GO_UNVERIFIED")
    # Only a machine-issued fail-stop HOLD; any deliberate/manual operator
    # hold with unknown reason remains untouched for separate review.
    if hold != {
        "reason": "PUBLIC_WS_SERVICE_FAILURE_HOLD",
        "scope": "BTC_PUBLIC_MONITOR_ONLY",
    }:
        deny("M10_BTC_HOLD_OWNER_REVIEW_REQUIRED")
    if (status.get("status") != "REJECT"
            or status.get("pair") != "btc_idr"
            or status.get("requested_sessions") != 10
            or status.get("attempted_sessions") != 10
            or type(status.get("completed_safe_sessions")) is not int
            or not 0 <= status["completed_safe_sessions"] < 10
            or not isinstance(status.get("collector_failure_code"), str)
            or not re.fullmatch(
                r"[A-Z][A-Z0-9_]{0,90}(?::[A-Z][A-Z0-9_]{0,90})?",
                status["collector_failure_code"]
            )
            or status.get("g1_receipt_issuance_allowed") is not False
            or status.get("g2_provider_call_allowed") is not False
            or status.get("execution_allowed") is not False
            or status.get("restart_only_after_clean_batch") is not False):
        deny("M10_BTC_PREVIOUS_BATCH_UNTRUSTED")
    age_epoch = status.get("last_completed_batch_epoch")
    if (type(age_epoch) is not int
            or not 0 <= epoch - age_epoch <= 72 * 3600):
        deny("M10_BTC_PREVIOUS_BATCH_OUT_OF_WINDOW")
    if (type(gaps) is not int or gaps < 0
            or type(status.get("unrecoverable_gap_rebaseline_count")) is not int
            or status["unrecoverable_gap_rebaseline_count"] != gaps
            or gaps > old_go["max_gap_count"]):
        deny("M10_BTC_GAP_BUDGET_MISMATCH")
    return {
        "scope": "BTC_PUBLIC_MONITOR_ONLY",
        "expires_at_epoch": epoch + PILOT_SECONDS,
        "max_gap_count": old_go["max_gap_count"],
        # Matches the original btc_ops_gate._policy_checks exact REJECT
        # acknowledgement. This is NOT evidence of a clean 10/10 batch.
        "acknowledged_failure_sha256": sha256(status_bytes).hexdigest(),
    }


def _show(unit: str, field: str) -> str:
    try:
        p = subprocess.run(
            ["systemctl", "--user", "show", "-P", field, unit],
            capture_output=True, text=True, timeout=8,
            check=False,
        )
        if p.returncode != 0 or len(p.stdout) > 8192:
            deny("M10_BTC_SYSTEMD_UNAVAILABLE")
        return p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        deny("M10_BTC_SYSTEMD_UNAVAILABLE")


def assess_original_btc_systemd(*, values: dict[str, str]) -> None:
    """Only exact expected V2 user units/collector. No runtime mutations."""
    if (values.get("fragment") != str(UNITDIR / UNIT)
            or values.get("workdir") != str(ROOT)
            or values.get("load") != "loaded"
            or values.get("service_active") != "inactive"
            or values.get("restart") != "no"
            or values.get("needs_reload") != "no"
            or values.get("timer_active") != "active"
            or values.get("timer_enabled") not in ("enabled", "enabled-runtime")
            or values.get("m05_active") != "active"):
        deny("M10_BTC_ORIGINAL_UNIT_NOT_READY")
    # Zero permission to follow any historic outside-V2 script as
    # ExecCondition, ExecStart or post-exit handler.
    installed = values.get("dropins", "").split()
    if installed != [str(DROP)]:
        deny("M10_BTC_UNREVIEWED_DROPINS")
    cli = values.get("execstart", "")
    checker = values.get("condition", "")
    exit_hook = values.get("exit_hook", "")
    expected_python = str(ROOT / ".venv/bin/python")
    gate = str(ROOT / "indodax_ai_bot/btc_ops_gate.py")
    legacy = ("--public-ws-supervised-once" in cli
              and "--public-ws-supervised-continuous" not in cli
              and "--collector-sessions 10" in cli)
    persistent = ("--public-ws-supervised-continuous" in cli
                  and "--public-ws-supervised-once" not in cli
                  and "--collector-sessions 4" in cli
                  and "--stream-seconds 30" in cli
                  and "--collector-pause-seconds 0" in cli
                  and "--public-ws-reconnect-until-recovered" in cli)
    if (expected_python not in cli
            or not (legacy or persistent)
            or "--pair btc_idr" not in cli
            or expected_python not in checker
            or gate + " check" not in checker
            or checker.count("btc_ops_gate.py") != 1
            or expected_python not in exit_hook
            or gate + " exit-hook" not in exit_hook
            or exit_hook.count("btc_ops_gate.py") != 1):
        deny("M10_BTC_ORIGINAL_EXEC_BOUNDARY_UNVERIFIED")


def _units() -> dict[str, str]:
    return {
        "fragment": _show(UNIT, "FragmentPath"),
        "workdir": _show(UNIT, "WorkingDirectory"),
        "load": _show(UNIT, "LoadState"),
        "service_active": _show(UNIT, "ActiveState"),
        "restart": _show(UNIT, "Restart"),
        "needs_reload": _show(UNIT, "NeedDaemonReload"),
        "timer_active": _show(TIMER, "ActiveState"),
        "timer_enabled": _show(TIMER, "UnitFileState"),
        "m05_active": _show(M05, "ActiveState"),
        "dropins": _show(UNIT, "DropInPaths"),
        "execstart": _show(UNIT, "ExecStart"),
        "condition": _show(UNIT, "ExecCondition"),
        "exit_hook": _show(UNIT, "ExecStopPost"),
    }


def _create_private_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _replace_private(path: Path, data: bytes, tag: str) -> None:
    temp = path.with_name(path.name + "." + tag + ".tmp")
    try:
        _create_private_exclusive(temp, data)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def read_original_private_sources() -> tuple[
    dict, bytes, dict, bytes, dict, bytes, int,
]:
    """Owner-private fail-closed reader with fixed diagnostic categories.

    This returns only data to the already authorized SAME original recovery
    routine. Exceptions are sanitized before any user-visible output.
    Neither file permissions nor active user services are modified.
    """
    try:
        old_go, go_bytes = _private_json(GO, 2048)
    except AdmissionDenied:
        deny("M10_BTC_PRIVATE_GO_UNAVAILABLE")
    try:
        status, status_bytes = _private_json(STATUS, 32768)
    except AdmissionDenied:
        deny("M10_BTC_PRIVATE_STATUS_UNAVAILABLE")
    try:
        hold, hold_bytes = _private_json(HOLD, 2048)
    except AdmissionDenied:
        deny("M10_BTC_PRIVATE_HOLD_UNAVAILABLE")
    try:
        gap_count = _journal_gap_count(WSDB)
    except AdmissionDenied:
        deny("M10_BTC_PRIVATE_WS_JOURNAL_UNAVAILABLE")
    return old_go, go_bytes, status, status_bytes, hold, hold_bytes, gap_count


def recover_public_btc_once(*, approve: bool, epoch: int | None = None,
                            dry_run: bool = False) -> dict:
    if not approve and not dry_run:
        deny("M10_BTC_EXPLICIT_OWNER_APPROVAL_REQUIRED")
    now = int(time.time()) if epoch is None else epoch
    if (not _trusted_dir(ROOT) or not _trusted_dir(RT)
            or not _trusted_dir(UNITDIR)
            or not _trusted_dir(DROP.parent)
            or DROP.is_symlink() or TEMPLATE.is_symlink()
            or not DROP.is_file() or not TEMPLATE.is_file()
            or DROP.read_bytes() != TEMPLATE.read_bytes()):
        deny("M10_BTC_ORIGINAL_SOURCE_UNTRUSTED")
    # No service/timer mutations, no alternate code imports.
    assess_original_btc_systemd(values=_units())
    # Classify the FIRST exact failed owner-private source instead of
    # a generic unavailable code. No raw paths, payloads, access tokens or
    # exception strings. All checks remain READ ONLY until owner approval.
    old_go, go_bytes, status, status_bytes, hold, hold_bytes, gap_count = (
        read_original_private_sources()
    )
    new_go = assess_exact_failed_btc(
        old_go=old_go, status=status, status_bytes=status_bytes,
        hold=hold, gaps=gap_count, epoch=now,
    )
    if dry_run:
        return {
            "status": "M10_BTC_PUBLIC_RECOVERY_READY_OWNER_ACTION_REQUIRED",
            "exact_old_failed_batch_bound": True,
            "machine_failure_hold_only": True,
            "original_v2_units_verified": True,
            "public_pilot_max_seconds": PILOT_SECONDS,
            "go_or_hold_modified": False,
            "collector_started": False,
            "m05_modified": False,
            "m03_policy_renewed": False,
            "execution_allowed": False,
        }
    # A fresh, explicit owner action is the only mechanism that can renew
    # the PUBLIC GO and archive the *known machine-failure* HOLD.
    tag = str(now) + "." + str(os.getpid())
    go_backup = GO.with_name(GO.name + ".pre-recovery." + tag + ".bak")
    hold_archive = HOLD.with_name(HOLD.name + ".ack-recovery." + tag + ".bak")
    if go_backup.exists() or hold_archive.exists():
        deny("M10_BTC_RECOVERY_BACKUP_CONFLICT")
    # Back up expired owner policy privately before any replacement.
    try:
        _create_private_exclusive(go_backup, go_bytes)
        # Ensure source status and rejected sidecar cannot change
        # unnoticed between explicit acknowledgement and releasing HOLD.
        if (_read_owned(STATUS, 32768) != status_bytes
                or _read_owned(GO, 2048) != go_bytes
                or _read_owned(HOLD, 2048) != hold_bytes):
            deny("M10_BTC_RECOVERY_SOURCE_CHANGED")
        new_bytes = (json.dumps(
            new_go, sort_keys=True, separators=(",", ":")
        ) + "\n").encode()
        _replace_private(GO, new_bytes, tag)
        try:
            # The HOLD is always the last file released. Until this atomic
            # move all original systemd starts remain denied.
            if (_read_owned(STATUS, 32768) != status_bytes
                    or _read_owned(HOLD, 2048) != hold_bytes
                    or _show(UNIT, "ActiveState") != "inactive"):
                deny("M10_BTC_RECOVERY_SOURCE_CHANGED")
            os.replace(HOLD, hold_archive)
        except Exception:
            _replace_private(GO, go_bytes, tag + ".rollback")
            raise
    except OSError:
        deny("M10_BTC_RECOVERY_PRIVATE_IO_FAILED")
    return {
        "status": "M10_BTC_PUBLIC_OWNER_PILOT_STAGED_UNATTESTED",
        "scope": "ORIGINAL_V2_BTC_PUBLIC_ONLY",
        "exact_rejected_sidecar_acknowledged": True,
        "machine_failure_hold_archived": True,
        "previous_go_backed_up": True,
        "new_public_go_seconds": PILOT_SECONDS,
        "original_btc_timer_left_active": True,
        "collector_started_or_restarted": False,
        "m05_service_modified": False,
        "m03_egress_policy_renewed": False,
        "new_clean_10_of_10_batch_verified": False,
        "three_ai_provider_called": False,
        "order_sent": False,
        "trading_enabled": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Original V2 BTC public recovery")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--readiness-only", action="store_true")
    g.add_argument("--owner-approve-exact-failed-btc-batch", action="store_true")
    args = parser.parse_args()
    try:
        report = recover_public_btc_once(
            approve=args.owner_approve_exact_failed_btc_batch,
            dry_run=args.readiness_only,
        )
    except (BtcRecoveryDenied, OSError, ValueError) as exc:
        code = str(exc)
        print(json.dumps({"status": "DENIED", "reason":
                          code if SAFE.fullmatch(code)
                          else "M10_BTC_RECOVERY_LOCAL_UNAVAILABLE"},
                         sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
