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

from .m10_v2_units_readonly import (
    inspect_named_v2_units, classify_m05_receiver_metadata,
)


_MAX_STATUS_BYTES = 32768
_MAX_BATCH_AGE_SECONDS = 3600
_MAX_PERSISTENT_WINDOW_AGE_SECONDS = 360
_SAFE_BATCH_STATUSES = frozenset((
    "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED",
    "PUBLIC_COLLECTION_WINDOW_OK_UNATTESTED",
    "REJECT",
))
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
        "stream_live_observed": False,
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
        "stream_live_observed": stream in ("BASELINE_ONLY", "CONTIGUOUS_OBSERVED"),
    })
    alerts: list[str] = []
    if age < 0:
        alerts.append("BTC_WS_BATCH_FUTURE_CLOCK")
    elif age > (_MAX_PERSISTENT_WINDOW_AGE_SECONDS
                if status == "PUBLIC_COLLECTION_WINDOW_OK_UNATTESTED"
                else _MAX_BATCH_AGE_SECONDS):
        alerts.append("BTC_WS_BATCH_STALE")

    if status == "REJECT":
        alerts.append("BTC_WS_BATCH_REJECTED")
    live_window = status == "PUBLIC_COLLECTION_WINDOW_OK_UNATTESTED"
    if (attempted != requested or completed != requested
            or (live_window and (
                document.get("stage") != "M04W_PERSISTENT_PUBLIC_WS_FOUR_WINDOWS"
                or requested != 4 or failure_code is not None or restart is not False
                or stream not in ("BASELINE_ONLY", "CONTIGUOUS_OBSERVED")
                or document.get("persistent_socket_observed_within_windows") is not True))
            or (status == "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED"
                and (requested not in (4, 10) or failure_code is not None
                     or restart is not True or stream != "DISCONNECTED"))):
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
    btc_timer = trusted_unit("btc_public_ws_timer")
    m05 = trusted_unit("m05_telegram_timer")
    receiver = classify_m05_receiver_metadata(units)
    m03 = trusted_unit("m03_scanner_timer")
    m03_service = trusted_unit("m03_scanner")
    btc_active = bool(btc and btc.get("observed_active") == "active")
    btc_timer_active = bool(
        btc_timer and btc_timer.get("observed_active") == "active"
        and btc_timer.get("observed_enabled") in ("enabled", "enabled-runtime")
    )
    # Migration: historic 10/10 finite batches remain inspectable; one
    # persistent service instead checkpoints 4x30s while the socket stays
    # open. An active unit + fresh checkpoint is metadata, NOT live ping.
    fresh_btc_batch = bool(
        batch["last_batch_status"] == "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED"
        and batch["requested_sessions"] in (4, 10)
        and batch["attempted_sessions"] == batch["requested_sessions"]
        and batch["completed_safe_sessions"] == batch["requested_sessions"]
        and batch["failure_code_observed"] is False
        and batch["restart_only_after_clean_batch_observed"] is True
        and batch["stream_disconnected_observed"] is True
        and type(batch["last_batch_age_seconds"]) is int
        and 0 <= batch["last_batch_age_seconds"] <= _MAX_BATCH_AGE_SECONDS
    )
    btc_scheduled_fresh = bool(
        btc is not None and not btc_active and btc_timer_active
        and fresh_btc_batch
    )
    btc_continuous_fresh = bool(
        btc_active
        and batch["last_batch_status"] == "PUBLIC_COLLECTION_WINDOW_OK_UNATTESTED"
        and batch["requested_sessions"] == 4
        and batch["attempted_sessions"] == 4
        and batch["completed_safe_sessions"] == 4
        and batch["failure_code_observed"] is False
        and batch["restart_only_after_clean_batch_observed"] is False
        and batch["stream_live_observed"] is True
        and type(batch["last_batch_age_seconds"]) is int
        and 0 <= batch["last_batch_age_seconds"] <= _MAX_PERSISTENT_WINDOW_AGE_SECONDS
        and "BTC_WS_BATCH_INCOMPLETE_OR_INCONSISTENT" not in alerts
    )
    m05_active = receiver["m05_timer_active_enabled_metadata_only"]
    m05_receiver_active = receiver["m05_receiver_metadata_active"]
    m03_disabled = bool(m03 and m03.get("observed_active") == "inactive"
                         and m03.get("observed_enabled") == "disabled")
    m03_scheduled = bool(
        m03 and m03.get("observed_active") == "active"
        and m03.get("observed_enabled") in ("enabled", "enabled-runtime")
    )

    if btc is None:
        alerts.append("BTC_WS_UNIT_STATUS_UNVERIFIED")
    elif not (btc_active or btc_scheduled_fresh):
        alerts.append("BTC_WS_UNIT_NOT_ACTIVE")
    if not m05_receiver_active:
        if m05 is None:
            alerts.append("M05_TIMER_STATUS_UNVERIFIED")
        else:
            alerts.append("M05_TIMER_NOT_ACTIVE")
    if m03 is None:
        alerts.append("M03_TIMER_STATUS_UNVERIFIED")
    elif not (m03_disabled or m03_scheduled):
        alerts.append("M03_TIMER_NOT_CONFIRMED_DISABLED")
    # A live *timer* is not proof the triggered oneshot scanner succeeded.
    # Inactive is normal between successful ticks; only a trusted explicit
    # systemd FAILED service state is a concrete failure snapshot.
    # Never infer HTTP403/root cause or freshness from this metadata alone.
    m03_service_failed = bool(
        m03_scheduled and m03_service is not None
        and m03_service.get("observed_active") == "failed"
    )
    if m03_service_failed:
        alerts.append("M03_SCANNER_SERVICE_FAILED")

    # A green-looking snapshot is NOT a heartbeat; output only warnings.
    return {
        "scope": "ONE_V2_ORIGINAL_WS_SIDECAR_AND_FIXED_USER_UNITS",
        "status": "V2_OPERATIONS_READONLY_OBSERVATION",
        "alert_codes": sorted(set(alerts)),
        "btc_ws_unit_active_metadata_observed": btc_active,
        "btc_ws_timer_active_metadata_observed": btc_timer_active,
        "btc_ws_scheduled_fresh_metadata_observed": btc_scheduled_fresh,
        "btc_ws_continuous_fresh_metadata_observed": btc_continuous_fresh,
        "m05_timer_active_metadata_observed": m05_active,
        "m05_receiver_metadata": receiver,
        "m03_timer_disabled_metadata_observed": m03_disabled,
        "m03_timer_active_metadata_observed": m03_scheduled,
        "m03_scanner_service_failed_metadata_observed": m03_service_failed,
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



# Fixed status labels only. Never expose raw network exception, path or
# untrusted report key/value to the owner's Telegram reply.
_M05_OPS_ALERT_LABELS = {
    "BTC_WS_BATCH_INCOMPLETE_OR_INCONSISTENT": "BATCH_INCOMPLETE",
    "BTC_WS_BATCH_REJECTED": "BATCH_REJECTED",
    "BTC_WS_BATCH_STALE": "BATCH_STALE",
    "BTC_WS_BATCH_FUTURE_CLOCK": "BATCH_CLOCK_INVALID",
    "BTC_WS_BATCH_STATUS_UNAVAILABLE": "BATCH_UNAVAILABLE",
    "BTC_WS_BATCH_FIELDS_UNTRUSTED": "BATCH_UNTRUSTED",
    "BTC_WS_REJECT_RESTART_FLAG_CONFLICT": "RESTART_FLAG_CONFLICT",
    "BTC_WS_UNIT_NOT_ACTIVE": "WS_UNIT_INACTIVE",
    "BTC_WS_UNIT_STATUS_UNVERIFIED": "WS_UNIT_UNVERIFIED",
    "M05_TIMER_NOT_ACTIVE": "M05_TIMER_INACTIVE",
    "M05_TIMER_STATUS_UNVERIFIED": "M05_TIMER_UNVERIFIED",
    "M03_TIMER_NOT_CONFIRMED_DISABLED": "M03_TIMER_REVIEW",
    "M03_TIMER_STATUS_UNVERIFIED": "M03_TIMER_UNVERIFIED",
    "M03_SCANNER_SERVICE_FAILED": "M03_SCANNER_FAILED",
}


def format_v2_ops_owner_message(report: dict | None) -> str:
    """Owner-only M05 preview, never a sent/delivered alert or trading consent.

    All text is fixed or derived from strictly bounded integer/session data.
    Untrusted strings in the original sidecar/units are never echoed.
    """
    prefix = "M10-06 V2 operasional READ-ONLY (snapshot, bukan heartbeat 24 jam)."
    if (not isinstance(report, dict)
            or report.get("status") != "V2_OPERATIONS_READONLY_OBSERVATION"
            or report.get("scope") != "ONE_V2_ORIGINAL_WS_SIDECAR_AND_FIXED_USER_UNITS"):
        return prefix + "\nSTATUS_UNVERIFIED. Tidak ada GO. Trading OFF."

    alerts = report.get("alert_codes")
    if not isinstance(alerts, list) or len(alerts) > 32:
        return prefix + "\nALERT_DATA_UNVERIFIED. Tidak ada GO. Trading OFF."
    labels = []
    for item in alerts:
        if type(item) is not str or item not in _M05_OPS_ALERT_LABELS:
            return prefix + "\nALERT_DATA_UNVERIFIED. Tidak ada GO. Trading OFF."
        label = _M05_OPS_ALERT_LABELS[item]
        if label not in labels:
            labels.append(label)
    labels.sort()

    state = (
        "AKTIF 4x30s (checkpoint metadata)"
        if report.get("btc_ws_continuous_fresh_metadata_observed") is True
        else "AKTIF (metadata saja)"
        if report.get("btc_ws_unit_active_metadata_observed") is True
        else "TERJADWAL, BATCH TERAKHIR SEGAR (snapshot)"
        if report.get("btc_ws_scheduled_fresh_metadata_observed") is True
        else "TIDAK AKTIF/UNVERIFIED"
    )
    timer = (
        "AKTIF TERJADWAL (metadata saja)"
        if report.get("m03_timer_active_metadata_observed") is True
        else "DISABLED (metadata saja)"
        if report.get("m03_timer_disabled_metadata_observed") is True
        else "BELUM TERVERIFIKASI"
    )
    original = report.get("btc_ws_last_batch")
    original = original if isinstance(original, dict) else {}
    status = original.get("last_batch_status")
    if status not in ("REJECT", "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED",
                      "PUBLIC_COLLECTION_WINDOW_OK_UNATTESTED"):
        status = "UNKNOWN"
    count = original.get("completed_safe_sessions")
    requested = original.get("requested_sessions")
    if (type(count) is not int or type(requested) is not int
            or not 0 <= count <= requested <= 10):
        sessions = "UNKNOWN"
    else:
        sessions = str(count) + "/" + str(requested)
    warnings = ", ".join(labels) if labels else "TIDAK TERDETEKSI (snapshot saja)"
    message = (
        prefix + "\nBTC WS: " + state + "; batch terakhir: " + status +
        "; sesi aman: " + sessions + "." +
        "\nM03 timer: " + timer + "." +
        "\nPeringatan: " + warnings + "." +
        "\nPeringatan belum terbukti terkirim otomatis; restart/GO tidak diizinkan. Trading OFF."
    )
    return message if len(message) <= 900 else (
        prefix + "\nALERT_DATA_UNVERIFIED. Tidak ada GO. Trading OFF."
    )


def main() -> int:
    print(json.dumps(assess_v2_operations_readonly(),
                     sort_keys=True, separators=(",", ":")))
    return 0  # Successfully evaluated a snapshot, NOT a release gate.


if __name__ == "__main__":
    raise SystemExit(main())
