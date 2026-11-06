"""M10 operator-gated, bounded scheduling of the EXISTING BTC public WS collector.

This is an ExecCondition/ExecStopPost guard for the original systemd service.
It does not collect any data, contact an exchange, open an account, emit a G1
receipt, invoke AI, or place orders. The existing supervised CLI and SQLite
collector lock remain the sole public-data writer.
"""
from __future__ import annotations

import argparse
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time

SCOPE = "BTC_PUBLIC_MONITOR_ONLY"
MIN_START_INTERVAL = 600
MAX_GO_LIFETIME = 86400
ROOT = Path.home() / "indodax-ai-bot-v2" / "runtime"
SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,90}$")


class AdmissionDenied(Exception):
    """A stable safe denial, not an exception string or network log."""


def _read_owned(path: Path, limit: int) -> bytes:
    try:
        st = path.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or st.st_mode & 0o077 or not st.st_mode & stat.S_IRUSR
                or st.st_size < 2 or st.st_size > limit):
            raise AdmissionDenied("UNSAFE_PRIVATE_FILE")
        raw = path.read_bytes()
        if len(raw) != st.st_size:
            raise AdmissionDenied("CHANGING_PRIVATE_FILE")
        return raw
    except FileNotFoundError:
        raise AdmissionDenied("REQUIRED_PRIVATE_FILE_MISSING") from None
    except OSError:
        raise AdmissionDenied("PRIVATE_FILE_UNREADABLE") from None


def _private_json(path: Path, limit: int) -> tuple[dict, bytes]:
    raw = _read_owned(path, limit)
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError()
    except (UnicodeError, ValueError, TypeError):
        raise AdmissionDenied("PRIVATE_JSON_INVALID") from None
    return body, raw


def _journal_gap_count(database: Path) -> int:
    if database.is_symlink() or not database.is_file():
        raise AdmissionDenied("PUBLIC_WS_JOURNAL_UNAVAILABLE")
    try:
        uri = database.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2) as con:
            con.execute("PRAGMA query_only=ON")
            row = con.execute(
                "SELECT COUNT(*) FROM ws_stream_discontinuities WHERE pair=?",
                ("btc_idr",),
            ).fetchone()
            return row[0]
    except (sqlite3.Error, OSError):
        raise AdmissionDenied("PUBLIC_WS_JOURNAL_INVALID") from None


def _policy_checks(go_path: Path, status_path: Path, database: Path,
                   hold_path: Path, epoch: int) -> None:
    if hold_path.exists() or hold_path.is_symlink():
        raise AdmissionDenied("PUBLIC_WS_OPERATOR_HOLD")

    policy, _ = _private_json(go_path, 2048)
    if set(policy) - {"scope", "expires_at_epoch", "max_gap_count",
                      "acknowledged_failure_sha256"}:
        raise AdmissionDenied("PUBLIC_WS_GO_UNKNOWN_FIELDS")
    expiration = policy.get("expires_at_epoch")
    maximum = policy.get("max_gap_count")
    if (policy.get("scope") != SCOPE
            or type(expiration) is not int
            or not epoch < expiration <= epoch + MAX_GO_LIFETIME
            or type(maximum) is not int or not 0 <= maximum <= 100000):
        raise AdmissionDenied("PUBLIC_WS_OWNER_GO_INVALID")

    sidecar, sidecar_bytes = _private_json(status_path, 32768)
    if (sidecar.get("g1_receipt_issuance_allowed") is not False
            or sidecar.get("g2_provider_call_allowed") is not False
            or sidecar.get("execution_allowed") is not False):
        raise AdmissionDenied("PUBLIC_WS_AUTHORITY_UNSAFE")
    if (type(sidecar.get("unrecoverable_gap_rebaseline_count")) is not int
            or sidecar["unrecoverable_gap_rebaseline_count"] > maximum
            or _journal_gap_count(database) !=
               sidecar["unrecoverable_gap_rebaseline_count"]):
        raise AdmissionDenied("PUBLIC_WS_GAP_LIMIT_OR_MISMATCH")

    if sidecar.get("status") == "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED":
        if (sidecar.get("collector_failure_code") is not None
                or sidecar.get("attempted_sessions") != 10
                or sidecar.get("completed_safe_sessions") != 10):
            raise AdmissionDenied("PUBLIC_WS_PREVIOUS_BATCH_UNSAFE")
    elif sidecar.get("status") == "REJECT":
        digest = policy.get("acknowledged_failure_sha256")
        if (not isinstance(digest, str)
                or not re.fullmatch(r"[a-f0-9]{64}", digest)
                or digest != sha256(sidecar_bytes).hexdigest()):
            raise AdmissionDenied("PUBLIC_WS_FAILURE_NEEDS_NEW_OWNER_ACK")
    else:
        raise AdmissionDenied("PUBLIC_WS_PREVIOUS_STATUS_UNKNOWN")


def _atomic_json(path: Path, value: dict) -> None:
    if path.is_symlink():
        raise AdmissionDenied("PUBLIC_WS_STATE_SYMLINK_DENIED")
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    tmp = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def admit(*, go: Path, status: Path, database: Path, hold: Path,
          state: Path, epoch: int | None = None) -> str:
    """Fail-closed authorization and durable 600s cooldown for systemd starts."""
    now = int(time.time()) if epoch is None else epoch
    _policy_checks(go, status, database, hold, now)
    lock = state.with_suffix(state.suffix + ".lock")
    if state.is_symlink() or lock.is_symlink():
        raise AdmissionDenied("PUBLIC_WS_STATE_SYMLINK_DENIED")
    try:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+b") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _policy_checks(go, status, database, hold, now)
            if state.exists():
                prior, _ = _private_json(state, 512)
                ts = prior.get("last_admission_epoch")
                if type(ts) is not int or ts > now or now - ts < MIN_START_INTERVAL:
                    raise AdmissionDenied("PUBLIC_WS_START_COOLDOWN")
            _atomic_json(state, {"last_admission_epoch": now,
                                 "scope": SCOPE})
    except BlockingIOError:
        raise AdmissionDenied("PUBLIC_WS_ADMISSION_ALREADY_RUNNING") from None
    except OSError:
        raise AdmissionDenied("PUBLIC_WS_ADMISSION_STATE_UNAVAILABLE") from None
    return "BTC_PUBLIC_OPS_ADMITTED_UNATTESTED"


def diagnose_once(*, go: Path, status: Path, database: Path, hold: Path,
                  state: Path, epoch: int | None = None) -> dict:
    """Read the SAME original ExecCondition gates without admitting/starting.

    Never touches admission locks, writes GO/hold/cooldown, opens networks,
    or authorizes a service. A snapshot cannot prove concurrent availability.
    """
    now = int(time.time()) if epoch is None else epoch
    reason = "NONE"
    try:
        _policy_checks(go, status, database, hold, now)
        lock = state.with_suffix(state.suffix + ".lock")
        if state.is_symlink() or lock.is_symlink():
            raise AdmissionDenied("PUBLIC_WS_STATE_SYMLINK_DENIED")
        if state.exists():
            prior, _ = _private_json(state, 512)
            ts = prior.get("last_admission_epoch")
            if type(ts) is not int or ts > now or now - ts < MIN_START_INTERVAL:
                raise AdmissionDenied("PUBLIC_WS_START_COOLDOWN")
    except AdmissionDenied as exc:
        code = str(exc)
        reason = code if SAFE_CODE.fullmatch(code) else "PUBLIC_WS_OPERATION_DENIED"
    return {
        "status": "BTC_PUBLIC_OPS_READONLY_EXEC_CONDITION_DIAGNOSIS",
        "first_blocker": reason,
        "original_gate_preconditions_passed_at_snapshot": reason == "NONE",
        "dynamic_admission_or_concurrent_lock_verified": False,
        "go_created_or_renewed": False,
        "original_hold_modified": False,
        "cooldown_state_modified": False,
        "service_started_or_restarted": False,
        "private_api_or_exchange_call_sent": False,
        "paid_ai_calls": 0,
        "order_sent": False,
        "live_trading_authorized": False,
    }


def record_exit(*, hold: Path, service_result: str) -> str:
    """Persist a fail-stop latch on actual service failure; never leak logs."""
    if service_result in ("success", "exec-condition"):
        return "BTC_PUBLIC_OPS_EXIT_NO_FAILURE"
    if hold.exists() or hold.is_symlink():
        return "BTC_PUBLIC_OPS_HOLD_ALREADY_SET"
    _atomic_json(hold, {"reason": "PUBLIC_WS_SERVICE_FAILURE_HOLD",
                        "scope": SCOPE})
    return "BTC_PUBLIC_OPS_FAILURE_HOLD_SET"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PUBLIC BTC original systemd gate only")
    p.add_argument("action", choices=("check", "exit-hook", "diagnose"))
    p.add_argument("--go", type=Path, default=ROOT / "btc_public_ops.go.json")
    p.add_argument("--status", type=Path, default=ROOT / "public_ws_btc.status.json")
    p.add_argument("--db", type=Path, default=ROOT / "public_ws_btc.sqlite3")
    p.add_argument("--hold", type=Path, default=ROOT / "btc_public_ops.hold.json")
    p.add_argument("--state", type=Path, default=ROOT / "btc_public_ops.admission.json")
    args = p.parse_args(argv)
    try:
        if args.action == "diagnose":
            print(json.dumps(diagnose_once(
                go=args.go, status=args.status, database=args.db,
                hold=args.hold, state=args.state,
            ), sort_keys=True))
            return 0
        if args.action == "check":
            outcome = admit(go=args.go, status=args.status, database=args.db,
                            hold=args.hold, state=args.state)
        else:
            outcome = record_exit(
                hold=args.hold,
                service_result=os.environ.get("SERVICE_RESULT", "unknown"),
            )
        print(outcome)
        return 0
    except AdmissionDenied as exc:
        code = str(exc)
        print(code if SAFE_CODE.fullmatch(code) else "PUBLIC_WS_OPERATION_DENIED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
