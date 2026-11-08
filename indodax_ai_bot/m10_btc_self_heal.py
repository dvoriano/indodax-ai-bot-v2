"""M10 original V2 BTC PUBLIC-only transient self-heal for EXISTING timer.

Explicit local owner opt-in, exact machine-failure HOLD, exact known WS
transport failure, original standing public GO, source+journal gap agreement,
durable max-two/rolling-24h attempts and 30m backoff. No new timer, public
network request, scanner, provider, owner Telegram action or exchange order.

This only re-opens ONE already scheduled original BTC collector batch.
M03 keeps requiring a NEW clean 10/10 BTC status <=1h. An operator HOLD,
nontransport failure, corrupted journal, unknown state or absent consent
MUST continue to fail closed.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Any

from .btc_ops_gate import (
    AdmissionDenied, ROOT, SCOPE, STANDING_MODE, _journal_gap_count,
    _private_json, _read_owned, _atomic_json,
)

CONSENT_SCOPE = "BTC_PUBLIC_TRANSIENT_SELF_HEAL_ONLY"
CONSENT = Path.home() / ".config" / "indodax-ai-bot-v2" / "m10-btc-self-heal.json"
GO = ROOT / "btc_public_ops.go.json"
STATUS = ROOT / "public_ws_btc.status.json"
HOLD = ROOT / "btc_public_ops.hold.json"
WSDB = ROOT / "public_ws_btc.sqlite3"
STATE = ROOT / "btc_public_self_heal.state.json"
LEASE = ROOT / "btc_public_self_heal.lease.json"
MAX_PER_24H = 2
MIN_INTERVAL = 1800
MAX_REJECT_AGE = 86400
LEASE_LIFETIME = 3600
_TRANSIENT = frozenset((
    "WS_SESSION_UNAVAILABLE",
    "WS_SESSION_REJECTED:PUBLIC_WS_CONNECTION_OR_FRAME_FAILED",
))
_HEX = re.compile(r"[a-f0-9]{64}\Z")


class AutoHealDenied(Exception):
    """Exact non-secret deny codes only."""


def _deny(code: str) -> None:
    raise AutoHealDenied(code)


def _sha(data: bytes) -> str:
    return sha256(data).hexdigest()


def _owned_dir(path: Path) -> bool:
    try:
        s = path.lstat()
        return stat.S_ISDIR(s.st_mode) and s.st_uid == os.geteuid() and (
            stat.S_IMODE(s.st_mode) & 0o077
        ) == 0
    except OSError:
        return False


def _consent(now: int, path: Path = CONSENT) -> dict[str, Any]:
    if not (_owned_dir(path.parent)
            and _owned_dir(path.parent.parent)):
        _deny("AUTO_HEAL_OWNER_CONFIG_DIRECTORY_UNTRUSTED")
    try:
        v, _ = _private_json(path, 2048)
    except AdmissionDenied:
        _deny("AUTO_HEAL_OWNER_OPT_IN_MISSING_OR_UNTRUSTED")
    if (set(v) != {
            "schema_version", "scope", "owner_authorized",
            "issued_at_epoch", "max_attempts_per_24h",
            "minimum_interval_seconds",
        } or type(v["schema_version"]) is not int
            or v["schema_version"] != 1
            or v["scope"] != CONSENT_SCOPE
            or v["owner_authorized"] is not True
            or type(v["issued_at_epoch"]) is not int
            or not 0 < v["issued_at_epoch"] <= now
            or type(v["max_attempts_per_24h"]) is not int
            or v["max_attempts_per_24h"] != MAX_PER_24H
            or type(v["minimum_interval_seconds"]) is not int
            or v["minimum_interval_seconds"] != MIN_INTERVAL):
        _deny("AUTO_HEAL_OWNER_OPT_IN_INVALID")
    return v


def _source(now: int, *, go: Path, status: Path, hold: Path,
            db: Path) -> dict[str, Any]:
    try:
        h, hbytes = _private_json(hold, 2048)
        g, gbytes = _private_json(go, 2048)
        s, sbytes = _private_json(status, 32768)
        gaps = _journal_gap_count(db)
    except AdmissionDenied:
        _deny("AUTO_HEAL_ORIGINAL_PRIVATE_SOURCE_UNAVAILABLE")
    if h != {
        "reason": "PUBLIC_WS_SERVICE_FAILURE_HOLD",
        "scope": SCOPE,
    }:
        _deny("AUTO_HEAL_MANUAL_OR_UNKNOWN_HOLD_PRESERVED")
    if (set(g) != {
            "scope", "mode", "issued_at_epoch", "max_gap_count",
        } or g["scope"] != SCOPE or g["mode"] != STANDING_MODE
            or type(g["issued_at_epoch"]) is not int
            or not 0 < g["issued_at_epoch"] <= now
            or type(g["max_gap_count"]) is not int
            or not 0 <= g["max_gap_count"] <= 100000):
        _deny("AUTO_HEAL_STANDING_PUBLIC_GO_UNVERIFIED")
    if (type(s.get("last_completed_batch_epoch")) is not int
            or not 0 <= now - s["last_completed_batch_epoch"] <= MAX_REJECT_AGE
            or s.get("status") != "REJECT"
            or s.get("pair") != "btc_idr"
            or s.get("requested_sessions") != 10
            or s.get("attempted_sessions") != 10
            or type(s.get("completed_safe_sessions")) is not int
            or not 0 <= s["completed_safe_sessions"] < 10
            or s.get("collector_failure_code") not in _TRANSIENT
            or s.get("stream_status") != "DISCONNECTED"
            or s.get("restart_only_after_clean_batch") is not False
            or s.get("g1_receipt_issuance_allowed") is not False
            or s.get("g2_provider_call_allowed") is not False
            or s.get("execution_allowed") is not False):
        _deny("AUTO_HEAL_FAILURE_NOT_ALLOWLISTED")
    if (type(gaps) is not int or gaps < 0
            or type(s.get("unrecoverable_gap_rebaseline_count")) is not int
            or gaps != s["unrecoverable_gap_rebaseline_count"]
            or gaps > g["max_gap_count"]):
        _deny("AUTO_HEAL_GAP_OR_JOURNAL_CONFLICT")
    return {
        "go_sha256": _sha(gbytes),
        "status_sha256": _sha(sbytes),
        "hold_sha256": _sha(hbytes),
        "hold_bytes": hbytes,
        "go_bytes": gbytes,
        "status_bytes": sbytes,
    }


def _state(path: Path) -> list[dict[str, Any]]:
    if not path.exists() and not path.is_symlink():
        return []
    try:
        v, _ = _private_json(path, 8192)
    except AdmissionDenied:
        _deny("AUTO_HEAL_STATE_UNTRUSTED")
    rows = v.get("attempts")
    if (set(v) != {"schema_version", "scope", "attempts"}
            or type(v["schema_version"]) is not int
            or v["schema_version"] != 1
            or v["scope"] != CONSENT_SCOPE
            or not isinstance(rows, list) or len(rows) > 500):
        _deny("AUTO_HEAL_STATE_UNTRUSTED")
    last = 0
    for a in rows:
        if (not isinstance(a, dict)
                or set(a) != {"epoch", "status_sha256"}
                or type(a["epoch"]) is not int
                or a["epoch"] <= last
                or type(a["status_sha256"]) is not str
                or not _HEX.fullmatch(a["status_sha256"])):
            _deny("AUTO_HEAL_STATE_UNTRUSTED")
        last = a["epoch"]
    return rows


def _existing_lease(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    try:
        _private_json(path, 2048)
    except AdmissionDenied:
        _deny("AUTO_HEAL_PRIOR_LEASE_UNTRUSTED")


def ready_for_transient_recovery(
    *, now: int, consent: Path = CONSENT, go: Path = GO,
    status: Path = STATUS, hold: Path = HOLD, db: Path = WSDB,
    state: Path = STATE, lease: Path = LEASE,
) -> dict[str, Any]:
    """Pure inspection, no disk writes, no private content exposed."""
    if type(now) is not int or now <= 0:
        _deny("AUTO_HEAL_CLOCK_INVALID")
    _consent(now, consent)
    src = _source(now, go=go, status=status, hold=hold, db=db)
    history = _state(state)
    _existing_lease(lease)
    recent = [a for a in history if now - 86400 < a["epoch"] <= now]
    if len(recent) >= MAX_PER_24H:
        _deny("AUTO_HEAL_DAILY_RETRY_CAP")
    if (history and (history[-1]["epoch"] > now
                     or now - history[-1]["epoch"] < MIN_INTERVAL)):
        _deny("AUTO_HEAL_RETRY_BACKOFF")
    if any(x["status_sha256"] == src["status_sha256"] for x in history):
        _deny("AUTO_HEAL_SAME_FAILED_BATCH_ALREADY_RETRIED")
    return {
        **src, "history": history,
        "status": "AUTO_HEAL_TRANSIENT_PUBLIC_RECOVERY_ELIGIBLE",
        "next_attempt_count_24h": len(recent) + 1,
    }


def validated_standing_failure_lease(
    *, now: int, policy_bytes: bytes, status_bytes: bytes,
    lease: Path = LEASE, state: Path = STATE, consent: Path = CONSENT,
) -> bool:
    """Use only a receipt created by our explicit owner opt-in machine-HOLD
    release, bound to exactly one failed batch and standing GO. Never G1.
    """
    if type(now) is not int or not isinstance(policy_bytes, bytes) or not isinstance(status_bytes, bytes):
        return False
    try:
        _consent(now, consent)
        data, _ = _private_json(lease, 2048)
        if (set(data) != {
                "schema_version", "scope", "issued_at_epoch",
                "expires_at_epoch", "go_sha256", "status_sha256",
            } or type(data["schema_version"]) is not int
                or data["schema_version"] != 1
                or data["scope"] != CONSENT_SCOPE
                or type(data["issued_at_epoch"]) is not int
                or type(data["expires_at_epoch"]) is not int
                or not 0 <= now - data["issued_at_epoch"] <= LEASE_LIFETIME
                or data["expires_at_epoch"] != data["issued_at_epoch"] + LEASE_LIFETIME
                or now >= data["expires_at_epoch"]
                or data["go_sha256"] != _sha(policy_bytes)
                or data["status_sha256"] != _sha(status_bytes)):
            return False
        attempts = _state(state)
        return any(
            x["epoch"] == data["issued_at_epoch"]
            and x["status_sha256"] == data["status_sha256"]
            for x in attempts
        )
    except (AdmissionDenied, AutoHealDenied, OSError, ValueError, TypeError):
        return False


def maybe_auto_heal_original_gate(
    *, now: int, go: Path = GO, status: Path = STATUS,
    hold: Path = HOLD, db: Path = WSDB, consent: Path = CONSENT,
    state: Path = STATE, lease: Path = LEASE,
) -> bool:
    """Only the existing ExecCondition 'check' invokes this when opted in.

    If any precondition fails, leave original HOLD unchanged. No retry
    network and no service start here. Caller proceeds to normal admit().
    """
    if type(now) is not int or now <= 0:
        return False
    if not consent.is_file() and not consent.is_symlink():
        return False
    # The original per-service admission lock already guards all starts
    # in systemd. We use a second exclusive lock here to avoid parallel
    # manual command collisions and double release of the same HOLD.
    import fcntl
    lock = state.with_name(state.name + ".lock")
    fd = -1
    try:
        if lock.is_symlink() or not _owned_dir(state.parent):
            return False
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os,"O_NOFOLLOW",0), 0o600)
        stat_fd = os.fstat(fd)
        if (not stat.S_ISREG(stat_fd.st_mode)
                or stat_fd.st_uid != os.geteuid()
                or stat_fd.st_nlink != 1
                or stat.S_IMODE(stat_fd.st_mode) != 0o600):
            return False
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = ready_for_transient_recovery(
            now=now, consent=consent, go=go, status=status,
            hold=hold, db=db, state=state, lease=lease,
        )
        tag = str(now) + "." + str(os.getpid())
        archive = hold.with_name(hold.name + ".auto-recovered." + tag + ".bak")
        if archive.exists() or archive.is_symlink():
            return False
        keep = [
            a for a in info["history"] if now - 86400 < a["epoch"] <= now
        ]
        attempts = keep + [{"epoch": now, "status_sha256": info["status_sha256"]}]
        receipt = {
            "schema_version": 1, "scope": CONSENT_SCOPE,
            "issued_at_epoch": now,
            "expires_at_epoch": now + LEASE_LIFETIME,
            "go_sha256": info["go_sha256"],
            "status_sha256": info["status_sha256"],
        }
        # No auto-release if an operator/source file changed during review.
        if (_read_owned(go, 2048) != info["go_bytes"]
                or _read_owned(status, 32768) != info["status_bytes"]
                or _read_owned(hold, 2048) != info["hold_bytes"]
                or _journal_gap_count(db) != json.loads(info["status_bytes"])[
                    "unrecoverable_gap_rebaseline_count"]):
            return False
        # Reserve retry attempt + bound one-batch receipt BEFORE HOLD moves.
        # A crash before final move leaves HOLD engaged and never silently
        # reattempts the same failed batch: deliberately fail-closed.
        _atomic_json(state, {
            "schema_version": 1, "scope": CONSENT_SCOPE, "attempts": attempts,
        })
        _atomic_json(lease, receipt)
        if (_read_owned(go, 2048) != info["go_bytes"]
                or _read_owned(status, 32768) != info["status_bytes"]
                or _read_owned(hold, 2048) != info["hold_bytes"]):
            return False
        os.replace(hold, archive)
        return True
    except (AutoHealDenied, AdmissionDenied, OSError, ValueError, TypeError,
            BlockingIOError):
        return False
    finally:
        if fd >= 0:
            os.close(fd)


def diagnose_self_heal_readonly(*, now: int | None = None) -> dict[str, Any]:
    """Source-based offline diagnostic, never manipulates HOLD/GO/WS/timers."""
    epoch = int(time.time()) if now is None else now
    try:
        r = ready_for_transient_recovery(now=epoch)
        reason = r["status"]
        eligible = True
        attempts = r["next_attempt_count_24h"]
    except AutoHealDenied as exc:
        reason = str(exc)
        eligible = False
        attempts = 0
    return {
        "status": "M10_PUBLIC_BTC_SELF_HEAL_READONLY",
        "first_blocker": "NONE" if eligible else reason,
        "eligible_transient_recovery_snapshot": eligible,
        "next_attempt_count_24h": attempts,
        "owner_intentional_hold_preserved": True,
        "service_started_or_restarted": False,
        "new_scanner_or_timer_created": False,
        "hold_or_policy_modified": False,
        "independent_g1_issuer_verified": False,
        "paid_ai_calls": 0,
        "order_sent": False,
        "trading_enabled": False,
    }
