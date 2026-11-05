"""Issue one time-bounded, owner-confirmed PUBLIC M03 NONLIVE policy.

Single canonical V2 source. No scanner, Telegram, private API, paid AI,
service modification or order execution. Existing V1 policy still works.
Owner declaration is not independent measurement of external IP users.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import stat
import sys
import time
from typing import Callable
from urllib.request import urlopen

from indodax_ai_bot.btc_ops_gate import _journal_gap_count, _private_json
from indodax_ai_bot.m03_egress_admission import (
    M03SharedEgressAdmission, M03EgressAdmissionError,
)

ROOT = Path.home() / "indodax-ai-bot-v2"
POLICY = Path.home() / ".config" / "indodax-ai-bot-v2" / "m03-egress-admission.json"
EXTERNAL_IP_CHECK = "https://api.ipify.org"
PILOT_SECONDS = 20 * 3600


class PilotDenied(ValueError):
    """Fixed operator-facing denial without exposing credentials or paths."""


def _owner_policy_bytes(path: Path) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                    or st.st_mode & 0o077 or not 2 <= st.st_size <= 8192):
                raise PilotDenied("M03_EXISTING_POLICY_UNTRUSTED")
            raw = os.read(fd, 8193)
        finally:
            os.close(fd)
        return raw
    except OSError:
        raise PilotDenied("M03_EXISTING_POLICY_UNAVAILABLE") from None


def _write_private_new(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _write_atomic(target: Path, data: bytes, *, epoch: int) -> None:
    temp = target.with_name(target.name + "." + str(epoch) + "." + str(os.getpid()) + ".tmp")
    if target.is_symlink():
        raise PilotDenied("M03_POLICY_SYMLINK")
    try:
        _write_private_new(temp, data)
        os.replace(temp, target)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def prepare_nonlive_pilot(
    *, root: Path, policy_path: Path, epoch: int,
    owner_confirmed: bool, ip_reader: Callable[[], str],
) -> dict:
    if not owner_confirmed:
        raise PilotDenied("M03_OWNER_CONFIRMATION_REQUIRED")
    if type(epoch) is not int or epoch <= 0:
        raise PilotDenied("M03_INVALID_CLOCK")
    if root != ROOT or root.is_symlink() or not root.is_dir():
        raise PilotDenied("M03_ONLY_CANONICAL_V2")
    directory = policy_path.parent
    st = directory.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid()
            or st.st_mode & 0o077 or directory.is_symlink()):
        raise PilotDenied("M03_POLICY_DIRECTORY_UNTRUSTED")

    old_bytes = _owner_policy_bytes(policy_path)
    try:
        old = json.loads(old_bytes)
        if (not isinstance(old, dict)
                or old.get("schema_version") not in (1, 2)
                or old.get("owner_approved_public_only") is not True
                or old.get("all_shared_ip_consumers_accounted") is not True
                or old.get("external_consumer_ids") != []
                or old.get("external_peak_requests_rolling_60s") != 0
                or not isinstance(old.get("egress_public_ip"), str)
                or not ipaddress.ip_address(old["egress_public_ip"]).is_global):
            raise ValueError("invalid previous owner declaration")
        ip = ip_reader().strip()
        if ip != old["egress_public_ip"] or not ipaddress.ip_address(ip).is_global:
            raise PilotDenied("M03_PUBLIC_EGRESS_IP_CHANGED")
    except PilotDenied:
        # Preserve the precise fixed reason for IP drift; PilotDenied is
        # a ValueError subclass and must not be swallowed as bad JSON.
        raise
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise PilotDenied("M03_EXISTING_OWNER_DECLARATION_INVALID") from None

    rt = root / "runtime"
    status, _ = _private_json(rt / "public_ws_btc.status.json", 32768)
    btc_go, _ = _private_json(rt / "btc_public_ops.go.json", 2048)
    if (btc_go.get("scope") != "BTC_PUBLIC_MONITOR_ONLY"
            or type(btc_go.get("expires_at_epoch")) is not int
            or btc_go["expires_at_epoch"] <= epoch + 600):
        raise PilotDenied("M03_BTC_GO_NOT_CURRENT")
    if (status.get("status") != "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED"
            or status.get("requested_sessions") != 10
            or status.get("attempted_sessions") != 10
            or status.get("completed_safe_sessions") != 10
            or status.get("collector_failure_code") is not None
            or type(status.get("last_completed_batch_epoch")) is not int
            or not 0 <= epoch - status["last_completed_batch_epoch"] <= 3600
            or status.get("g1_receipt_issuance_allowed") is not False
            or status.get("g2_provider_call_allowed") is not False
            or status.get("execution_allowed") is not False):
        raise PilotDenied("M03_BTC_BATCH_NOT_FRESH_CLEAN_PUBLIC")
    gaps = _journal_gap_count(rt / "public_ws_btc.sqlite3")
    if (type(status.get("unrecoverable_gap_rebaseline_count")) is not int
            or gaps != status["unrecoverable_gap_rebaseline_count"]
            or type(btc_go.get("max_gap_count")) is not int
            or not gaps <= btc_go["max_gap_count"]):
        raise PilotDenied("M03_BTC_GAP_BUDGET_UNVERIFIED")

    expires = min(epoch + PILOT_SECONDS, btc_go["expires_at_epoch"])
    policy = {
        "schema_version": 2,
        "owner_approved_public_only": True,
        "all_shared_ip_consumers_accounted": True,
        "egress_public_ip": ip,
        "external_consumer_ids": [],
        "external_peak_requests_rolling_60s": 0,
        "issued_at_epoch": epoch,
        "expires_at_epoch": expires,
        "pilot_scope": "M03_NONLIVE_PUBLIC_PILOT",
    }
    backup = policy_path.with_name(policy_path.name + ".pre-pilot-" + str(epoch) + ".bak")
    _write_private_new(backup, old_bytes)
    _write_atomic(policy_path, (json.dumps(policy, sort_keys=True) + "\n").encode(), epoch=epoch)
    allowance = M03SharedEgressAdmission(policy_path, clock=lambda: epoch).allowed_local_limit()
    return {
        "status": "M03_NONLIVE_OWNER_EGRESS_PILOT_PREPARED",
        "scope": "PUBLIC_SCANNER_NO_ORDERS",
        "local_admission_cap_rolling_60s": allowance,
        "pilot_valid_seconds_at_issue": expires - epoch,
        "btc_source_status": "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED",
        "independent_shared_ip_traffic_measured": False,
        "scanner_started": False,
        "telegram_sent": False,
        "trading_enabled": False,
    }


def _read_ip() -> str:
    with urlopen(EXTERNAL_IP_CHECK, timeout=10) as r:
        return r.read(64).decode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description="Original V2 owner NONLIVE public pilot only")
    parser.add_argument("--owner-confirm-v2-only-nonlive", action="store_true",
                        help="Owner confirms no other active REST consumers on shared egress IP")
    args = parser.parse_args()
    try:
        report = prepare_nonlive_pilot(
            root=ROOT, policy_path=POLICY, epoch=int(time.time()),
            owner_confirmed=args.owner_confirm_v2_only_nonlive,
            ip_reader=_read_ip,
        )
    except (PilotDenied, OSError, ValueError, M03EgressAdmissionError) as exc:
        code = str(exc)
        print(json.dumps({"ok": False, "reason": code
                          if code.startswith("M03_") else "M03_PILOT_PREP_FAILED"},
                         sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
