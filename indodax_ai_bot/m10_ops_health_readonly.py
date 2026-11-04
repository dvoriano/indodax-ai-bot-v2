"""M10-06 original V2-only operations alerts from snapshots, never a watchdog.

Shares the existing fixed-name V2 user-unit audit and reads only the original
public_ws_supervisor sidecar. No network, sqlite writes, extra services,
service start/stop/restart, process inventory, or hidden external guard probing.
An offline status snapshot is NOT 24/7 heartbeat or delivered alert proof.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import time
from typing import Any

from .m10_v2_units_readonly import inspect_named_v2_units


_MAX_STATUS_BYTES = 32768
_MAX_BATCH_AGE_SECONDS = 3600
_SAFE_BATCH_STATUSES = frozenset(("PUBLIC_COLLECTION_BATCH_OK_UNATTESTED", "REJECT"))
_SAFE_STREAM_STATUSES = frozenset(("DISCONNECTED", "CONTIGUOUS_OBSERVED", "BASELINE_ONLY"))


def _read_v2_ws_supervisor_status(path: Path) -> dict[str, Any] | None:
    """One fixed original V2 status JSON, owner-only regular no-follow file."""
    try:
        # Reject symlinked V2/runtime directories as well as the final file.
        # A default CLI read must not escape the V2 checkout indirectly.
        for directory in (path.parent, path.parent.parent):
            mode = os.lstat(directory).st_mode
            if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
                return None
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode)
                    or st.st_uid != os.geteuid()
                    or st.st_mode & 0o077
                    or not 2 <= st.st_size <= _MAX_STATUS_BYTES):
                return None
            raw = os.read(fd, _MAX_STATUS_BYTES + 1)
        finally:
            os.close(fd)
        doc = json.loads(raw)
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError, TypeError, UnicodeError):
        return None


def _batch_observation(document: dict[str, Any] | None, now_epoch: int) -> tuple[dict[str, Any], list[str]]:
    """Interpret the ORIGINAL supervisor keys, never infer active stream."""
    result: dict[str, Any] = {
        "last_batch_status": "UNKNOWN",
        "last_batch_age_seconds": None,
        "requested_sessions": None,
        "attempted_sessions": None,
        "completed_safe_sessions": None,
        "failure_code_observed": False,
        "restart_only_after_clean_batch_observed": False,
        "stream_disconnected_observed": False,
    }
    if not isinstance(document, dict):
        return result, ["BTC_WS_BATCH_STATUS_UNAVAILABLE"]

    status = document.get("status")
    requested = document.get("requested_sessions")
    attempted = document.get("attempted_sessions")
    completed = document.get("completed_safe_sessions")
    epoch = document.get("last_completed_batch_epoch")
    restart = document.get("restart_only_after_clean_batch")
    failure_code = document.get("collector_failure_code")
    stream = document.get("stream_status")

    if (type(status) is not str
            or status not in _SAFE_BATCH_STATUSES
            or type(requested) is not int or not 2 <= requested <= 10
            or type(attempted) is not int or not 0 <= attempted <= requested
            or type(completed) is not int or not 0 <= completed <= attempted
            or type(epoch) is not int or not 0 < epoch <= 10**11
            or type(restart) is not bool
            or (failure_code is not None and
                (type(failure_code) is not str or not 1 <= len(failure_code) <= 96))
            or type(stream) is not str or stream not in _SAFE_STREAM_STATUSES):
        return result, ["BTC_WS_BATCH_FIELDS_UNTRUSTED"]

    age = now_epoch - epoch
    result.update({
        "last_batch_status": status,
        "requested_sessions": requested,
        "attempted_sessions": attempted,
        "completed_safe_sessions": completed,
        "last_batch_age_seconds": age if 0 <= age <= 10**11 else None,
        "failure_code_observed": failure_code is not None,
        "restart_only_after_clean_batch_observed": restart,
        "stream_disconnected_observed": stream == "DISCONNECTED",
    })
    alerts: list[str] = []
    if age < 0:
        alerts.append("BTC_WS_BATCH_FUTURE_CLOCK")
    elif age > _MAX_BATCH_AGE_SECONDS:
        alerts.append("BTC_WS_BATCH_STALE")

    if status == "REJECT":
        alerts.append("BTC_WS_BATCH_REJECTED")
    if (attempted != requested or completed != requested or
            (status == "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED"
             and (failure_code is not None or restart is not True
                  or stream != "DISCONNECTED"))):
        alerts.append("BTC_WS_BATCH_INCOMPLETE_OR_INCONSISTENT")
    # A forged REJECT with restart=true must never look clean.
    if status == "REJECT" and restart:
        alerts.append("BTC_WS_REJECT_RESTART_FLAG_CONFLICT")
    return result, alerts


def assess_v2_operations_readonly(
    *, units_report: dict | None = None, status_document: dict | None = None,
    now_epoch: int | None = None, status_path: Path | None = None,
) -> dict[str, Any]:
    """Single-snapshot V2 operational warning. Zero claims of 24h proof.

    If status_document is omitted, read only default original V2 status file.
    units_report allows offline tests without systemctl or network.
    """
    current = int(time.time()) if now_epoch is None else now_epoch
    if type(current) is not int or current <= 0:
        raise ValueError("M10_OPS_INVALID_CLOCK")

    units = units_report if units_report is not None else inspect_named_v2_units()
    doc = (status_document if status_document is not None else
           _read_v2_ws_supervisor_status(status_path or
               (Path.home() / "indodax-ai-bot-v2" / "runtime" /
                "public_ws_btc.status.json")))
    batch, alerts = _batch_observation(doc, current)
    roles: dict[str, dict] = {}
    if isinstance(units, dict) and isinstance(units.get("units"), list):
        for row in units["units"]:
            if isinstance(row, dict) and isinstance(row.get("role"), str):
                if row["role"] not in roles:
                    roles[row["role"]] = row

    def trusted_unit(role: str) -> dict | None:
        row = roles.get(role)
        if (not isinstance(row, dict)
                or row.get("observed_load") != "loaded"
                or row.get("v2_user_fragment_path_matches") is not True
                or row.get("unit_needs_daemon_reload") != "no"):
            return None
        return row

    btc = trusted_unit("btc_public_ws")
    m05 = trusted_unit("m05_telegram_timer")
    m03 = trusted_unit("m03_scanner_timer")
    btc_active = bool(btc and btc.get("observed_active") == "active")
    m05_active = bool(m05 and m05.get("observed_active") == "active"
                      and m05.get("observed_enabled") in ("enabled", "enabled-runtime"))
    m03_disabled = bool(m03 and m03.get("observed_active") == "inactive"
                         and m03.get("observed_enabled") == "disabled")

    if btc is None:
        alerts.append("BTC_WS_UNIT_STATUS_UNVERIFIED")
    elif not btc_active:
        alerts.append("BTC_WS_UNIT_NOT_ACTIVE")
    if m05 is None:
        alerts.append("M05_TIMER_STATUS_UNVERIFIED")
    elif not m05_active:
        alerts.append("M05_TIMER_NOT_ACTIVE")
    if m03 is None:
        alerts.append("M03_TIMER_STATUS_UNVERIFIED")
    elif not m03_disabled:
        alerts.append("M03_TIMER_NOT_CONFIRMED_DISABLED")

    # A green-looking snapshot is NOT a heartbeat; output only warnings.
    return {
        "scope": "ONE_V2_ORIGINAL_WS_SIDECAR_AND_FIXED_USER_UNITS",
        "status": "V2_OPERATIONS_READONLY_OBSERVATION",
        "alert_codes": sorted(set(alerts)),
        "btc_ws_unit_active_metadata_observed": btc_active,
        "m05_timer_active_metadata_observed": m05_active,
        "m03_timer_disabled_metadata_observed": m03_disabled,
        "btc_ws_last_batch": batch,
        "continuous_24h_heartbeat_verified": False,
        "alerts_delivered_to_owner_verified": False,
        "actual_runtime_source_sha_verified": False,
        "systemd_guard_root_cause_verified": False,
        "service_started_or_restarted": False,
        "service_restart_authorized": False,
        "m03_timer_go_authorized": False,
        "m10_06_full_acceptance": False,
        "live_trading_authorized": False,
    }


def main() -> int:
    print(json.dumps(assess_v2_operations_readonly(),
                     sort_keys=True, separators=(",", ":")))
    return 0  # Successfully evaluated a snapshot, NOT a release gate.


if __name__ == "__main__":
    raise SystemExit(main())
