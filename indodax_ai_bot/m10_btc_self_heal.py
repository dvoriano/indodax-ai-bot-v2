"""M10 original V2 BTC PUBLIC-only transient self-heal for EXISTING timer.

Explicit local owner opt-in, exact machine-failure HOLD, exact known WS
transport failure, original standing public GO, source+journal gap agreement,
legacy canary max-two/24h or owner-selected persistent transient reconnect.
Continuous consent has no attempt-count cap; same failed-batch digest,
source/gap proof and 600s admission backoff remain. No second worker.

This only re-opens ONE already scheduled original BTC collector batch.
M03 still forbids source promotion from either legacy 10/10 or 4x30s metadata. An operator HOLD,
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
MAX_PER_24H = 2  # Legacy owner canary.
MIN_INTERVAL = 1800
CONTINUOUS_TRANSIENT_SCHEMA = 2
CONTINUOUS_TRANSIENT_MIN_INTERVAL = 600
CONTINUOUS_TRANSIENT_MODE = "ALWAYS_RECONNECT_TYPED_PUBLIC_TRANSPORT"
# Historical 24-hour REJECT freshness is not a PUBLIC reconnect admission TTL.
# The 24-hour window is still enforced on recovery ATTEMPTS, never on market evidence.
MAX_REJECT_AGE = 86400
LEASE_LIFETIME = 3600
# Actual 2026 original trade_stream._safe_ws_receive_failure classes:
# only narrow recv() connection losses. Generic "WS_SESSION_UNAVAILABLE"
# hides arbitrary exceptions and must NEVER auto-heal.
_TRANSIENT = frozenset((
    "WS_SESSION_REJECTED:PUBLIC_WS_STREAM_RECEIVE_CLOSED_OR_LOST",
    "WS_SESSION_REJECTED:PUBLIC_WS_STREAM_RECEIVE_CONNECTION_RESET",
    "WS_SESSION_REJECTED:PUBLIC_WS_STREAM_RECEIVE_CONNECTION_ABORTED",
    "WS_SESSION_REJECTED:PUBLIC_WS_STREAM_RECEIVE_BROKEN_PIPE",
    "WS_SESSION_REJECTED:PUBLIC_WS_STREAM_RECEIVE_EOF",
    "WS_SESSION_REJECTED:PUBLIC_WS_STREAM_RECEIVE_EMPTY_OR_CLOSED",
    # PUBLIC-only connect / auth transport failures (not authorization errors).
    "WS_SESSION_REJECTED:PUBLIC_WS_CONNECT_CLOSED_OR_LOST",
    "WS_SESSION_REJECTED:PUBLIC_WS_CONNECT_CONNECTION_RESET",
    "WS_SESSION_REJECTED:PUBLIC_WS_CONNECT_CONNECTION_ABORTED",
    "WS_SESSION_REJECTED:PUBLIC_WS_CONNECT_CONNECTION_REFUSED",
    "WS_SESSION_REJECTED:PUBLIC_WS_CONNECT_BROKEN_PIPE",
    "WS_SESSION_REJECTED:PUBLIC_WS_CONNECT_TIMEOUT",
    "WS_SESSION_REJECTED:PUBLIC_WS_CONNECT_HTTP_UNAVAILABLE",
    "WS_SESSION_REJECTED:PUBLIC_WS_AUTH_RECEIVE_CLOSED_OR_LOST",
    "WS_SESSION_REJECTED:PUBLIC_WS_AUTH_RECEIVE_CONNECTION_RESET",
    "WS_SESSION_REJECTED:PUBLIC_WS_AUTH_RECEIVE_CONNECTION_ABORTED",
    "WS_SESSION_REJECTED:PUBLIC_WS_AUTH_RECEIVE_BROKEN_PIPE",
    "WS_SESSION_REJECTED:PUBLIC_WS_AUTH_RECEIVE_TIMEOUT",
))
_HEX = re.compile(r"[a-f0-9]{64}\Z")
OWNER_DUPLICATE_FAILURE = "WS_SESSION_REJECTED:DUPLICATE_SEQUENCE_IN_PUBLICATION"


class AutoHealDenied(Exception):
    """Exact non-secret deny codes only."""


def _deny(code: str) -> None:
    raise AutoHealDenied(code)


def _sha(data: bytes) -> str:
    return sha256(data).hexdigest()


def _owned_dir(path: Path) -> bool:
    """Private leaf directory (0700 or stricter), never a symlink."""
    try:
        s = path.lstat()
        return (stat.S_ISDIR(s.st_mode)
                and s.st_uid == os.geteuid()
                and (stat.S_IMODE(s.st_mode) & 0o077) == 0)
    except OSError:
        return False


def _owned_safe_ancestor_dir(path: Path) -> bool:
    """An owner-controlled non-writable-by-peers ancestor (0700/0755).

    ~/.config on Ubuntu commonly has 0755 permissions. That does not
    weaken the 0700 protected child or 0600 opt-in. But group/world
    WRITE on an ancestor could allow component replacement and is denied.
    """
    try:
        s = path.lstat()
        return (stat.S_ISDIR(s.st_mode)
                and s.st_uid == os.geteuid()
                and (stat.S_IMODE(s.st_mode) & 0o022) == 0)
    except OSError:
        return False


def _consent(now: int, path: Path = CONSENT) -> dict[str, Any]:
    if not (_owned_dir(path.parent)
            and _owned_safe_ancestor_dir(path.parent.parent)):
        _deny("AUTO_HEAL_OWNER_CONFIG_DIRECTORY_UNTRUSTED")
    try:
        v, _ = _private_json(path, 2048)
    except AdmissionDenied:
        _deny("AUTO_HEAL_OWNER_OPT_IN_MISSING_OR_UNTRUSTED")
    legacy_fields = {
        "schema_version", "scope", "owner_authorized",
        "issued_at_epoch", "max_attempts_per_24h",
        "minimum_interval_seconds",
    }
    continuous_fields = {
        "schema_version", "scope", "owner_authorized",
        "issued_at_epoch", "reconnect_mode",
        "minimum_interval_seconds",
    }
    if (type(v.get("schema_version")) is not int
            or v.get("scope") != CONSENT_SCOPE
            or v.get("owner_authorized") is not True
            or type(v.get("issued_at_epoch")) is not int
            or not 0 < v["issued_at_epoch"] <= now):
        _deny("AUTO_HEAL_OWNER_OPT_IN_INVALID")
    if v["schema_version"] == 1:
        if (set(v) != legacy_fields
                or type(v.get("max_attempts_per_24h")) is not int
                or v["max_attempts_per_24h"] != MAX_PER_24H
                or type(v.get("minimum_interval_seconds")) is not int
                or v["minimum_interval_seconds"] != MIN_INTERVAL):
            _deny("AUTO_HEAL_OWNER_OPT_IN_INVALID")
    elif v["schema_version"] == CONTINUOUS_TRANSIENT_SCHEMA:
        if (set(v) != continuous_fields
                or v.get("reconnect_mode") != CONTINUOUS_TRANSIENT_MODE
                or type(v.get("minimum_interval_seconds")) is not int
                or v["minimum_interval_seconds"] !=
                CONTINUOUS_TRANSIENT_MIN_INTERVAL):
            _deny("AUTO_HEAL_OWNER_OPT_IN_INVALID")
    else:
        _deny("AUTO_HEAL_OWNER_OPT_IN_INVALID")
    return v


def _source(now: int, *, go: Path, status: Path, hold: Path,
            db: Path, max_reject_age: int | None = MAX_REJECT_AGE,
            approved_duplicate_sha256: str | None = None) -> dict[str, Any]:
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
    duplicate_owner_ack = (isinstance(approved_duplicate_sha256, str)
        and _HEX.fullmatch(approved_duplicate_sha256) is not None
        and approved_duplicate_sha256 == _sha(sbytes)
        and s.get("collector_failure_code") == OWNER_DUPLICATE_FAILURE)
    if approved_duplicate_sha256 is not None and not duplicate_owner_ack:
        _deny("AUTO_HEAL_EXACT_DUPLICATE_OWNER_ACK_MISMATCH")
    if (type(s.get("last_completed_batch_epoch")) is not int
            or not (g["issued_at_epoch"] <= s["last_completed_batch_epoch"] <= now
                    and (max_reject_age is None or
                         now - s["last_completed_batch_epoch"] <= max_reject_age))
            or s.get("status") != "REJECT"
            or s.get("pair") != "btc_idr"
            or type(s.get("requested_sessions")) is not int
            or s["requested_sessions"] not in (2, 4, 10)
            or type(s.get("attempted_sessions")) is not int
            or not 1 <= s["attempted_sessions"] <= s["requested_sessions"]
            or type(s.get("completed_safe_sessions")) is not int
            or not 0 <= s["completed_safe_sessions"] < s["attempted_sessions"]
            or s.get("failed_session_number") != s["attempted_sessions"]
            or (s.get("collector_failure_code") not in _TRANSIENT
                and not duplicate_owner_ack)
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
    approved_duplicate_sha256: str | None = None,
) -> dict[str, Any]:
    """Pure inspection, no disk writes, no private content exposed."""
    if type(now) is not int or now <= 0:
        _deny("AUTO_HEAL_CLOCK_INVALID")
    approval = _consent(now, consent)
    # Aged REJECT is never promoted to current market data. It is only
    # historic evidence for one bounded PUBLIC network reconnection attempt.
    src = _source(now, go=go, status=status, hold=hold, db=db,
                  max_reject_age=None,
                  approved_duplicate_sha256=approved_duplicate_sha256)
    history = _state(state)
    _existing_lease(lease)
    recent = [a for a in history if now - 86400 < a["epoch"] <= now]
    if (approval["schema_version"] == 1
            and len(recent) >= MAX_PER_24H):
        _deny("AUTO_HEAL_DAILY_RETRY_CAP")
    if (history and (history[-1]["epoch"] > now
                     or now - history[-1]["epoch"]
                      < approval["minimum_interval_seconds"])):
        _deny("AUTO_HEAL_RETRY_BACKOFF")
    if any(x["status_sha256"] == src["status_sha256"] for x in history):
        _deny("AUTO_HEAL_SAME_FAILED_BATCH_ALREADY_RETRIED")
    return {
        **src, "history": history,
        "status": ("AUTO_HEAL_EXACT_DUPLICATE_OWNER_RETRY_ELIGIBLE"
                   if approved_duplicate_sha256 is not None else
                   "AUTO_HEAL_TRANSIENT_PUBLIC_RECOVERY_ELIGIBLE"),
        "owner_continuous_transient_recovery": (
            approval["schema_version"] == CONTINUOUS_TRANSIENT_SCHEMA
        ),
        "retry_count_cap": (
            MAX_PER_24H if approval["schema_version"] == 1 else None
        ),
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
    approved_duplicate_sha256: str | None = None,
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
        # Runtime is a trusted owned ancestor (0700/0755), not a public
        # consent directory. The state/lease/lock files remain private 0600.
        if lock.is_symlink() or not _owned_safe_ancestor_dir(state.parent):
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
            approved_duplicate_sha256=approved_duplicate_sha256,
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
    source_allowlisted = False
    source_reason = "NONE"
    try:
        _source(epoch, go=GO, status=STATUS, hold=HOLD, db=WSDB,
                max_reject_age=None)
        source_allowlisted = True
    except AutoHealDenied as exc:
        source_reason = str(exc)
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
        "transient_public_source_allowlisted": source_allowlisted,
        "aged_rejected_batch_is_not_market_data": True,
        "source_first_blocker": source_reason,
        # Non-sensitive booleans let original VPS distinguish a normal
        # Ubuntu 0755 ~/.config from a dangerously writable/symlink path,
        # without printing private source filenames or file contents.
        "config_private_leaf_permissions_ok": _owned_dir(CONSENT.parent),
        "config_parent_ancestor_permissions_ok": (
            _owned_safe_ancestor_dir(CONSENT.parent.parent)
        ),
        "runtime_owner_ancestor_permissions_ok": (
            _owned_safe_ancestor_dir(ROOT)
        ),
        "eligible_transient_recovery_snapshot": eligible,
        "owner_continuous_transient_recovery": (
            r.get("owner_continuous_transient_recovery", False)
            if eligible else False
        ),
        "retry_count_cap": (
            r.get("retry_count_cap", MAX_PER_24H) if eligible else None
        ),
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
